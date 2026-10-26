import uuid
from datetime import datetime,timezone,timedelta
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from server.main import app,engine
from server.security import User,password_hash

def test_equipment_and_field_time():
    mark=uuid.uuid4().hex[:9];pw='strong-test-password-123'
    with Session(engine) as db:
        db.add_all([User(username='owner'+mark,display_name='Owner',role='owner',password_hash=password_hash(pw)),User(username='tech'+mark,display_name='Tech',role='technician',password_hash=password_hash(pw)),User(username='outsider'+mark,display_name='Outsider',role='technician',password_hash=password_hash(pw))]);db.commit()
        tech=db.query(User).filter_by(username='tech'+mark).one().id
    c=TestClient(app)
    def login(who):
        c.post('/api/auth/logout');assert c.post('/api/auth/login',json={'username':who+mark,'password':pw}).status_code==200
    login('owner')
    cust=c.post('/api/customers',json={'name':'HVAC '+mark}).json()
    jobnum='LMS'+str(int(mark,16))
    c.post('/api/jobs',json={'job_number':jobnum,'customer_id':cust['id'],'location_name':'Building A','description':'Inspect RTU'})
    loc=[l for l in c.get('/api/locations').json() if l['customer_id']==cust['id']][0]
    equipment={'location_id':loc['id'],'asset_tag':'RTU-01','manufacturer':'Carrier','model':'123','serial':'SN123'}
    a=c.post('/api/equipment',json=equipment);assert a.status_code==201,a.text
    assert c.post('/api/equipment',json=equipment).status_code==409
    w=c.post('/api/work-orders',json={'job_number':jobnum,'summary':'Inspection'}).json()
    assert c.post('/api/work-orders/'+w['id']+'/equipment/'+a.json()['id']).status_code==201
    assert len(c.get('/api/work-orders/'+w['id']+'/equipment').json())==1
    start=datetime(2026,11,10,13,tzinfo=timezone.utc)
    c.post('/api/work-orders/'+w['id']+'/assignments',json={'technician_id':tech,'scheduled_start':start.isoformat(),'scheduled_end':(start+timedelta(hours=8)).isoformat()})
    payload={'started_at':start.isoformat(),'ended_at':(start+timedelta(hours=3)).isoformat(),'mileage':15}
    login('outsider');assert c.post('/api/work-orders/'+w['id']+'/time',json=payload).status_code==403
    login('tech');r=c.post('/api/work-orders/'+w['id']+'/time',json=payload);assert r.status_code==201,r.text
    assert r.json()['hours']==3.0
    assert c.post('/api/work-orders/'+w['id']+'/time',json=payload).status_code==409
    assert c.post('/api/work-orders/'+w['id']+'/time',json={**payload,'ended_at':start.isoformat()}).status_code==422
    assert len(c.get('/api/work-orders/'+w['id']+'/time').json())==1
