import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'release.py'
spec = importlib.util.spec_from_file_location('release', SCRIPT)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
IMAGE = r.PREFIX + 'a' * 64
BASE = {'metadata': {'annotations': {'run.googleapis.com/iap-enabled': 'true'}},
        'spec': {'template': {'metadata': {'annotations': {'run.googleapis.com/network-interfaces': 'private-network'}},
                 'spec': {'serviceAccountName': 'runtime', 'containers': [{'image': 'old-image', 'env': [{'name': 'DATABASE_URL', 'valueFrom': {'secretKeyRef': {'name': 'private-db', 'key': '1'}}}]}]}}},
        'status': {'latestReadyRevisionName': 'old', 'latestCreatedRevisionName': 'old', 'url': 'https://example.invalid'}}


def ready():
    data = copy.deepcopy(BASE)
    data['spec']['template']['spec']['containers'][0]['image'] = IMAGE
    data['status'].update(latestReadyRevisionName='new', latestCreatedRevisionName='new')
    return data


class ReleaseTests(unittest.TestCase):
    def execute(self, snapshots, branch='release', image=IMAGE):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'image.txt'
            file.write_text(image)
            with patch.object(r.sys, 'argv', ['release.py', str(file), branch]), patch.object(r, 'service', side_effect=snapshots), patch.object(r, 'run') as run:
                try:
                    r.main()
                except SystemExit as e:
                    return run.call_args_list, str(e)
                return run.call_args_list, None

    def test_ready_image_promoted_only_after_preserving_runtime(self):
        new = ready(); final = ready(); final['status']['traffic'] = [{'revisionName': 'new', 'percent': 100}]
        calls, error = self.execute([BASE, new, new, final])
        self.assertIsNone(error)
        self.assertIn('--no-traffic', calls[0].args)
        self.assertIn('--to-revisions=new=100', calls[1].args)
        self.assertEqual(len(calls), 2)

    def test_other_branches_and_wrong_repository_do_not_deploy(self):
        for kwargs in [{'branch': 'main'}, {'image': 'other.example/image@sha256:' + 'a'*64}]:
            calls, error = self.execute([], **kwargs)
            self.assertTrue(error); self.assertEqual(calls, [])

    def test_not_ready_does_not_promote(self):
        new = ready();new['status']['latestReadyRevisionName'] = 'old'
        calls, error = self.execute([BASE,new])
        self.assertIn('not ready',error);self.assertEqual(len(calls),1)

    def test_changed_env_or_iap_does_not_promote(self):
        for field in ('env','iap'):
            new = ready()
            if field=='env':new['spec']['template']['spec']['containers'][0]['env']=[]
            else:new['metadata']['annotations']['run.googleapis.com/iap-enabled']='false'
            calls,error=self.execute([BASE,new])
            self.assertIn('configuration changed',error);self.assertEqual(len(calls),1)

    def test_newer_revision_does_not_promote(self):
        newer=ready();newer['status']['latestCreatedRevisionName']='other-release'
        calls,error=self.execute([BASE,ready(),newer])
        self.assertIn('Another release',error);self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()
