"""v1.6 runtime safety validation; no cloud credentials are bundled."""
from __future__ import annotations
import os
from urllib.parse import urlsplit

HOSTED_ENVS = {'staging', 'production'}


def validate_settings(env=None):
    e = dict(os.environ if env is None else env)
    mode = e.get('APP_ENV', 'local').lower().strip()
    if mode not in {'local', 'test', 'staging', 'production'}:
        raise RuntimeError('APP_ENV must be local, test, staging, or production')
    if mode in HOSTED_ENVS:
        url = e.get('DATABASE_URL', '')
        if not url.startswith('postgresql+psycopg://'):
            raise RuntimeError('Hosted CRM requires PostgreSQL (postgresql+psycopg DATABASE_URL)')
        public_url = e.get('APP_PUBLIC_URL', '')
        parsed = urlsplit(public_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/'):
            raise RuntimeError('Hosted CRM requires APP_PUBLIC_URL=https://<host>')
        if e.get('COOKIE_SECURE') != '1':
            raise RuntimeError('Hosted CRM requires COOKIE_SECURE=1')
        hosts = [h.strip().lower() for h in e.get('ALLOWED_HOSTS', '').split(',') if h.strip()]
        if not hosts or '*' in hosts or parsed.hostname.lower() not in hosts:
            raise RuntimeError('ALLOWED_HOSTS must explicitly include the APP_PUBLIC_URL host')
        if e.get('LMS_GMAIL_SENDER') and not e.get('LMS_GMAIL_ACCESS_TOKEN'):
            # Credentials are configured later, and never required for staging.
            pass
        if mode == 'production' and e.get('PRODUCTION_APPROVED') != 'YES':
            raise RuntimeError('Production must remain locked until security review and restore drill')
    return {'mode': mode, 'hosts': [x.strip() for x in e.get('ALLOWED_HOSTS', '').split(',') if x.strip()],
            'public_url': e.get('APP_PUBLIC_URL', '')}
