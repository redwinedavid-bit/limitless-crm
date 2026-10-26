import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from server.main import app, engine
from server.security import User, password_hash


def test_dispatch_assignment_permissions_conflicts_and_notes():
    marker=uuid.uuid4().hex[:10]
    owner='owner_'+marker
    tech='tech_'+marker
    outsider='other_'+marker
    password='a-unique-test-password'
    with Session(engine) as db:
        db.add_all([User(username=owner,display_name='Owner',role='owner',password_hash=password_hash(password)),User(username=tech,display_name='Assigned Tech',role='technician',password_hash=password_hash(password)),User(username=outsider,display_name='Other Tech',role='technician',password_hash=password_hash(password))]);db.commit()
        tech_id=db.query(User).filter_by(username=tech).one().id
    client=TestClient(app)
    def login(username):
        client.post('/api/auth/logout')
        assert client.post('/api/auth/login',json={'username':username,'password':password}).status_code==200
    login(owner)
    customer=client.post('/api/customers',json={'name':'Customer '+marker}).json()
    job='LMS'+str(int(marker,16))
    assert client.post('/api/jobs',json={'job_number':job,'customer_id':customer['id'],'description':'Service work'}).status_code==201
    first=client.post('/api/work-orders',json={'job_number':job,'summary':'System one inspection'}).json()
    second=client.post('/api/work-orders',json={'job_number':job,'summary':'System two inspection'}).json()
    start=datetime(2026,11,3,13,tzinfo=timezone.utc)
    def assign(wo, beginning, ending):
        return client.post('/api/work-orders/'+wo+'/assignments',json={'technician_id':tech_id,'scheduled_start':beginning.isoformat(),'scheduled_end':ending.isoformat()})
    assert assign(first['id'],start,start+timedelta(hours=2)).status_code==201
    assert assign(second['id'],start+timedelta(hours=1),start+timedelta(hours=3)).status_code==409
    assert assign(second['id'],start+timedelta(hours=2),start+timedelta(hours=3)).status_code==201
    login(outsider)
    assert client.get('/api/work-orders').json()==[]
    assert client.get('/api/work-orders/'+first['id']+'/notes').status_code==403
    assert client.post('/api/work-orders/'+first['id']+'/notes',json={'note':'Unauthorized'}).status_code==403
    assert client.patch('/api/work-orders/'+first['id']+'/status',json={'status':'Completed'}).status_code==403
    login(tech)
    assert len(client.get('/api/work-orders').json())==2
    assert client.get('/api/dispatch').status_code==403
    assert client.post('/api/work-orders/'+first['id']+'/notes',json={'note':'Checked compressor operation'}).status_code==201
    assert client.patch('/api/work-orders/'+first['id']+'/status',json={'status':'In Progress'}).status_code==200
    assert len(client.get('/api/work-orders/'+first['id']+'/notes').json())==1
