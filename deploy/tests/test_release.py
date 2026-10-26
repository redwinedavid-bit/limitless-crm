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


def revision(state='True', observed=1, image=IMAGE):
    return {'metadata': {'name': 'new', 'generation': 1},
            'spec': {'containers': [{'image': image}]},
            'status': {'observedGeneration': observed,
                       'conditions': [{'type': 'Ready', 'status': state, 'message': 'Container status'}]}}


class ReleaseTests(unittest.TestCase):
    def execute(self, snapshots, branch='release', image=IMAGE, revisions=None, clock=None):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'image.txt'
            file.write_text(image)
            with patch.object(r.sys, 'argv', ['release.py', str(file), branch]), patch.object(r, 'service', side_effect=snapshots), patch.object(r, 'run') as run, patch.object(r, 'revision_details', side_effect=revisions, return_value=revision()), patch.object(r.time, 'sleep'), patch.object(r.time, 'monotonic', side_effect=clock, return_value=0):
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
        calls, error = self.execute([BASE,new], revisions=[revision('False')])
        self.assertIn('not ready',error);self.assertEqual(len(calls),1)

    def test_stale_service_summary_uses_exact_revision_readiness(self):
        new = ready(); new['status']['latestReadyRevisionName'] = 'old'
        final = ready(); final['status']['traffic'] = [{'revisionName': 'new', 'percent': 100}]
        calls, error = self.execute([BASE, new, new, final])
        self.assertIsNone(error); self.assertEqual(len(calls), 2)

    def test_waits_for_revision_and_observed_generation(self):
        final = ready(); final['status']['traffic'] = [{'revisionName': 'new', 'percent': 100}]
        calls, error = self.execute([BASE, ready(), ready(), final],
                                    revisions=[revision('Unknown'), revision(observed=0), revision()])
        self.assertIsNone(error); self.assertEqual(len(calls), 2)

    def test_timeout_does_not_promote(self):
        calls, error = self.execute([BASE, ready()], revisions=[revision('Unknown')], clock=[0, 181])
        self.assertIn('Timed out', error); self.assertEqual(len(calls), 1)

    def test_wrong_revision_image_does_not_promote(self):
        calls, error = self.execute([BASE, ready()], revisions=[revision(image='wrong-image')])
        self.assertIn('does not match', error); self.assertEqual(len(calls), 1)

    def test_configuration_changed_while_waiting_does_not_promote(self):
        changed = ready(); changed['spec']['template']['spec']['containers'][0]['env'] = []
        calls, error = self.execute([BASE, ready(), changed])
        self.assertIn('configuration changed', error); self.assertEqual(len(calls), 1)

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
