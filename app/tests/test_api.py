from fastapi.testclient import TestClient
from server.main import app,Base,engine
from server.security import User,password_hash
import uuid

def test_purchase_order_always_has_one_real_job():
    client=TestClient(app)
    assert client.get('/api/health').json()['status']=='ok'
    suffix=uuid.uuid4().hex[:8]
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        db.add(User(username='owner'+suffix,display_name='Test Owner',role='owner',password_hash=password_hash('test-password-1234')))
        db.add(User(username='tech'+suffix,display_name='Test Tech',role='technician',password_hash=password_hash('test-password-1234')))
        db.commit()
    assert client.get('/api/snapshot').status_code==401
    assert client.post('/api/auth/login',json={'username':'tech'+suffix,'password':'test-password-1234'}).status_code==200
    assert client.get('/api/snapshot').status_code==403
    assert client.post('/api/customers',json={'name':'Denied'}).status_code==403
    assert client.post('/api/auth/logout').status_code==200
    assert client.post('/api/auth/login',json={'username':'owner'+suffix,'password':'test-password-1234'}).status_code==200
    c=client.post('/api/customers',json={'name':'Test Customer '+suffix})
    assert c.status_code==201
    assert client.post('/api/purchase-orders',json={'po_number':'PO'+suffix,'job_id':'missing','vendor':'URI','amount':'40'}).status_code==410
    j=client.post('/api/jobs',json={'job_number':'LMS'+str(int(suffix,16)),'customer_id':c.json()['id'],'description':'Test job'})
    assert j.status_code==201
    snap=client.get('/api/snapshot').json()
    assert any(x['id']==j.json()['id'] for x in snap['jobs'])
    from sqlalchemy.orm import Session
    from sqlalchemy import select
    from server.main import Job
    with Session(engine) as db: dbid=db.scalar(select(Job.id).where(Job.job_number==j.json()['id']))
    # The legacy endpoint cannot bypass automatic numbering.
    assert client.post('/api/purchase-orders',json={'po_number':'PO'+suffix,'job_id':dbid,'vendor':'URI','amount':'40','status':'Issued'}).status_code==410
    # No buyer designator is assigned to this generic test owner, so purchasing is blocked.
    assert client.post('/api/purchasing/pos',json={'job_number':j.json()['id'],'vendor':'URI','amount':'40'}).status_code==403
