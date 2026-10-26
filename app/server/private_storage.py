"""Private local development files or explicitly configured private GCS objects.
Never creates buckets, IAM bindings, public ACLs or signed/public URLs.
"""
import io
import os
import re
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import StreamingResponse


def durable_configured():return os.getenv('LMS_DURABLE_UPLOADS')=='1' and bool(os.getenv('LMS_PRIVATE_UPLOAD_BUCKET','').strip())

def bucket():
    from google.cloud import storage
    b=storage.Client().bucket(os.environ['LMS_PRIVATE_UPLOAD_BUCKET']);b.reload(timeout=20)
    config=b.iam_configuration
    if not config.uniform_bucket_level_access_enabled or config.public_access_prevention!='enforced':
        raise HTTPException(503,'Private uploads require uniform bucket access and enforced public access prevention')
    return b


def safe_key(key):
    if not re.fullmatch(r'(?:gcs/(?:field|purchasing)/)?[a-f0-9]{32}',key):raise HTTPException(404,'File not found')
    return key


def put(root,key,data,media_type,namespace='field'):
    safe_key(key)
    if os.getenv('LMS_PRIVATE_UPLOAD_BUCKET'):
        if not durable_configured():raise HTTPException(503,'Private durable uploads are not enabled')
        key=f'gcs/{namespace}/{key}'
        try:bucket().blob(key).upload_from_string(data,content_type=media_type,if_generation_match=0,timeout=45)
        except HTTPException:raise
        except Exception:raise HTTPException(503,'Private storage is unavailable; the file was not saved')
    else:
        from . import main as m
        if m.RUNTIME['mode'] in ('staging','production'):raise HTTPException(503,'Configure private durable upload storage first')
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (root/key).open('xb') as f:f.write(data)
    return key


def read(root,key):
    safe_key(key)
    if key.startswith('gcs/'):
        if not durable_configured():raise HTTPException(503,'Private storage is not configured')
        try:return bucket().blob(key).download_as_bytes(timeout=30)
        except HTTPException:raise
        except Exception:raise HTTPException(503,'Private file could not be loaded')
    try:return (root/key).read_bytes()
    except OSError:raise HTTPException(404,'File not found')


def remove(root,key):
    safe_key(key)
    if key.startswith('gcs/'):
        blob=bucket().blob(key);blob.reload(timeout=20);blob.delete(if_generation_match=blob.generation,timeout=20)
    else:(root/key).unlink(missing_ok=True)


def response(root,key,media_type,filename):
    # All callers authorize the database record before reading its bytes.
    name=filename.replace('"','').replace('\r','').replace('\n','').encode('ascii','ignore').decode() or 'private-file'
    return StreamingResponse(io.BytesIO(read(root,key)),media_type=media_type,headers={'Content-Disposition':f'inline; filename="{name}"','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
