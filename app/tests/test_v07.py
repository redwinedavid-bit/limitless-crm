import uuid
from datetime import datetime,timezone,timedelta,date
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from server.main import app,engine
from server.security import User,password_hash

def test_v07_workflows():
    tag=uuid.uuid4().hex[:8];pw='test-password-123'
    with Session(engine) as db:
        db.add_all([User(username='o'+tag,display_name='Owner',role='owner',password_hash=password_hash(pw)),User(username='t'+tag,display_name='Tech',role='technician',password_hash=password_hash(pw))]);db.commit()
        tech=db.query(User).filter_by(username='t'+tag).one().id
    c=TestClient(app)
    def login(who):
        c.post('/api/auth/logout');assert c.post('/api/auth/login',json={'username':who+tag,'password':pw}).status_code==200
    login('o')
    customer=c.post('/api/customers',json={'name':'Test '+tag}).json()
    job='LMS'+str(int(tag,16))
    assert c.post('/api/jobs',json={'job_number':job,'customer_id':customer['id'],'location_name':'Shop'}).status_code==201
    location=[x for x in c.get('/api/locations').json() if x['customer_id']==customer['id']][0]
    asset=c.post('/api/equipment',json={'location_id':location['id'],'asset_tag':'RTU1'}).json()
    w=c.post('/api/work-orders',json={'job_number':job,'summary':'Test equipment'}).json()
    now=datetime(2026,11,12,13,tzinfo=timezone.utc)
    assert c.post(f"/api/work-orders/{w['id']}/assignments",json={'technician_id':tech,'scheduled_start':now.isoformat(),'scheduled_end':(now+timedelta(hours=5)).isoformat()}).status_code==201
    assert c.put('/api/labor-rates',json={'technician_id':tech,'hourly_cost':40,'overtime_multiplier':1.5,'mileage_rate':0.7}).status_code==200
    p=c.post('/api/pm/plans',json={'equipment_id':asset['id'],'interval_days':90,'next_due':'2026-11-12','checklist':['Filter','Belt']})
    assert p.status_code==201,p.text
    plan=p.json()['id']
    r=c.post(f'/api/pm/plans/{plan}/work-order',json={'job_number':job});assert r.status_code==201,r.text
    assert c.post(f'/api/pm/plans/{plan}/work-order',json={'job_number':job}).status_code==409
    report=c.get(f"/api/work-orders/{w['id']}/report");assert report.status_code==200 and 'LIMITLESS MECHANICAL' in report.text
    login('t')
    tr=c.post(f"/api/work-orders/{w['id']}/time",json={'started_at':now.isoformat(),'ended_at':(now+timedelta(hours=3)).isoformat(),'mileage':10});assert tr.status_code==201,tr.text
    entry=tr.json()['id']
    assert c.post(f'/api/time/{entry}/approve',json={'overtime_hours':0}).status_code==403
    assert c.get(f'/api/work-orders/{r.json()["work_order_id"]}/report').status_code==403
    login('o')
    ap=c.post(f'/api/time/{entry}/approve',json={'overtime_hours':1});assert ap.status_code==201,ap.text
    assert ap.json()['labor_cost']==140.0 and ap.json()['mileage_cost']==7.0
    assert c.post(f'/api/time/{entry}/approve',json={'overtime_hours':1}).status_code==409
    costs=c.get(f'/api/jobs/{job}/field-costs').json();assert costs['approved_field_labor_and_mileage']==147.0
