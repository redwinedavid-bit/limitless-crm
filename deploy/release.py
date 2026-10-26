"""Image-only release of the existing CRM. Never creates a service or changes IAM."""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT = 'limitless-crm-512612'
REGION = 'us-central1'
SERVICE = 'lms-crm-test'
PREFIX = f'us-central1-docker.pkg.dev/{PROJECT}/lms-test-images/crm@sha256:'


def run(*args, read=False):
    command = ['gcloud', *args, f'--project={PROJECT}', f'--region={REGION}']
    if read:
        return json.loads(subprocess.check_output(command + ['--format=json'], text=True))
    subprocess.run(command + ['--quiet'], check=True)


def service():
    return run('run', 'services', 'describe', SERVICE, read=True)


def revision_details(name):
    return run('run', 'revisions', 'describe', name, read=True)


def wait_for_revision(name, image, timeout=180):
    """Check the exact revision, independently of the service readiness summary."""
    deadline = time.monotonic() + timeout
    while True:
        data = revision_details(name)
        metadata = data.get('metadata', {})
        containers = data.get('spec', {}).get('containers', [])
        if metadata.get('name') != name or len(containers) != 1 or containers[0].get('image') != image:
            raise SystemExit('Revision identity or image does not match this release. No traffic promotion performed.')
        status = data.get('status', {})
        condition = next((c for c in status.get('conditions', []) if c.get('type') == 'Ready'), {})
        observed = status.get('observedGeneration')
        generation = metadata.get('generation')
        reconciled = generation is not None and observed is not None and str(generation) == str(observed)
        state = str(condition.get('status', 'Unknown')).lower()
        if reconciled and state == 'true':
            print(f'Revision {name} is ready.', flush=True)
            return
        detail = condition.get('message') or condition.get('reason') or 'Readiness pending'
        if reconciled and state == 'false':
            raise SystemExit(f'Revision {name} is not ready: {detail}. Previous traffic is retained.')
        if time.monotonic() >= deadline:
            raise SystemExit(f'Timed out waiting for revision {name}: {detail}. Previous traffic is retained.')
        print(f'Waiting for revision {name}: {detail}', flush=True)
        time.sleep(5)


def preserved_config(data):
    # Compare the full runtime spec (env/secret references, identity, resources,
    # mounts, etc.) except the deliberately changed image.
    spec = json.loads(json.dumps(data['spec']['template']['spec']))
    for container in spec.get('containers', []):
        container.pop('image', None)
    annotations = data.get('metadata', {}).get('annotations', {})
    template_annotations = data['spec']['template'].get('metadata', {}).get('annotations', {})
    return {
        'runtime': spec,
        'security': {k: v for k, v in annotations.items() if k.startswith('run.googleapis.com/') and any(x in k for x in ('iap', 'ingress', 'invoker'))},
        'network_scaling': {k: v for k, v in template_annotations.items() if any(x in k for x in ('network', 'vpc', 'minScale', 'maxScale'))},
    }


def main():
    if len(sys.argv) != 3 or sys.argv[2] != 'release':
        raise SystemExit('Deployment is restricted to the release branch.')
    image = Path(sys.argv[1]).read_text().strip()
    if not re.fullmatch(re.escape(PREFIX) + r'[a-f0-9]{64}', image):
        raise SystemExit('Expected the CRM repository image pinned by SHA256 digest.')
    before = service()  # Fails if the service is absent; never deploys a replacement stack.
    containers = before['spec']['template']['spec'].get('containers', [])
    if len(containers) != 1:
        raise SystemExit('Expected one CRM container. Review deployment configuration.')
    old_revision = before.get('status', {}).get('latestReadyRevisionName')
    print(f'Previous ready revision: {old_revision}', flush=True)
    run('run', 'services', 'update', SERVICE, f'--image={image}', '--no-traffic')
    after = service()
    if preserved_config(before) != preserved_config(after):
        raise SystemExit('Runtime configuration changed unexpectedly. New revision has not been promoted.')
    revision = after.get('status', {}).get('latestCreatedRevisionName')
    if not revision:
        raise SystemExit('Cloud Run did not return a revision name. Previous traffic is retained.')
    if after['spec']['template']['spec']['containers'][0]['image'] != image:
        raise SystemExit('Image changed during release. Stop and review concurrent deployments.')
    wait_for_revision(revision, image)
    # Do not approve overlapping releases; verify again immediately before promotion.
    current = service()
    if current['status']['latestCreatedRevisionName'] != revision:
        raise SystemExit('Another release is in progress. No traffic promotion performed.')
    if preserved_config(before) != preserved_config(current):
        raise SystemExit('Runtime configuration changed while waiting. No traffic promotion performed.')
    run('run', 'services', 'update-traffic', SERVICE, f'--to-revisions={revision}=100')
    final = service()
    serving = final.get('status', {}).get('traffic', [])
    if not any(t.get('revisionName') == revision and t.get('percent') == 100 for t in serving):
        raise SystemExit('Traffic could not be verified. Inspect the service before retrying.')
    print(f'Released {revision}. Verify login and a mock service call at {final["status"]["url"]}', flush=True)


if __name__ == '__main__':
    main()
