"""Prevent existing Terraform configuration from reverting CI-managed app images.
Run against the EXISTING remote service/main.tf.json, never the bundled template.
Only edits the configuration and makes a backup. Does not apply Terraform.
"""
import json
import sys
from pathlib import Path

p = Path(sys.argv[1]).expanduser().resolve()
config = json.loads(p.read_text())
service = config['resource']['google_cloud_run_v2_service']['crm']
if service['name'] != 'lms-crm-test':
    raise SystemExit('Unexpected service; no changes made.')
lifecycle = service.setdefault('lifecycle', {})
ignored = lifecycle.setdefault('ignore_changes', [])
if not isinstance(ignored, list):
    raise SystemExit('Existing ignore_changes is not a list; review it manually.')
field = 'template[0].containers[0].image'
if field not in ignored:
    backup = p.with_name(p.name + '.before-ci')
    if backup.exists():
        raise SystemExit('Backup already exists; review it before changing configuration.')
    backup.write_bytes(p.read_bytes())
    ignored.append(field)
    p.write_text(json.dumps(config, indent=2) + '\n')
print('Image ownership configured. Run terraform validate and plan in this existing service directory; do not apply unrelated changes.')
