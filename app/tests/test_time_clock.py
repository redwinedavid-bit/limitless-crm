"""Regression coverage for call time, mileage, access and cost accounting."""
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from server import main as m, time_clock as clock
from server.security import User, password_hash

@pytest.fixture
def setup_clock(monkeypatch):
    suffix=uuid.uuid4().hex[:10];password='test-clock-password-123';hashed=password_hash(password)
    with Session(m.engine) as db:
        users=[User(username=r+suffix,display_name=r.title(),role='owner' if r=='owner' else 'technician',password_hash=hashed) for r in ('owner','tech','other')]
        db.add_all(users);db.commit();tech_id=users[1].id
    clients={}
    for role in ('owner','tech','other'):
        c=TestClient(m.app)
        assert c.post('/api/auth/login',json={'username':role+suffix,'password':password}).status_code==200
        clients[role]=c
    owner=clients['owner'];customer=owner.post('/api/customers',json={'name':'Clock Test '+suffix}).json();job='LMS'+str(int(suffix,16))
    assert owner.post('/api/jobs',json={'job_number':job,'customer_id':customer['id'],'description':'Clock testing'}).status_code==201
    now=[datetime(2026,10,24,12,tzinfo=timezone.utc)];orders=[]
    for name in ('Assigned one','Assigned two'):
        w=owner.post('/api/work-orders',json={'job_number':job,'summary':name}).json();orders.append(w['id'])
        assert owner.post('/api/work-orders/'+w['id']+'/assignments',json={'technician_id':tech_id,'scheduled_start':(now[0]+timedelta(days=len(orders)-1)).isoformat(),'scheduled_end':(now[0]+timedelta(days=len(orders)-1,hours=8)).isoformat()}).status_code==201
    monkeypatch.setattr(clock,'clock_now',lambda:now[0])
    return clients,orders,now,tech_id,job

def test_clock_persistence_and_retry(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;tech=clients['tech'];owner=clients['owner']
    started=tech.post('/api/time/clock/start',json={'work_order_id':orders[0]})
    assert started.status_code==201,started.text
    active=started.json()['active'];reloaded=TestClient(m.app);reloaded.cookies.update(tech.cookies)
    assert reloaded.get('/api/time/clock').json()['active']['id']==active['id']
    now[0]+=timedelta(hours=2,minutes=30)
    result=tech.post('/api/time/clock/stop',json={'clock_id':active['id']})
    assert result.status_code==200,result.text
    assert result.json()['entry']['hours']==2.5 and result.json()['entry']['mileage']==0
    assert result.json()['active'] is None
    retry=tech.post('/api/time/clock/stop',json={'clock_id':active['id']})
    assert retry.json()['entry']['id']==result.json()['entry']['id']
    entries=tech.get('/api/work-orders/'+orders[0]+'/time').json()
    assert len(entries)==1 and not entries[0]['approved']
    fresh=tech.post('/api/time/clock/start',json={'work_order_id':orders[1]}).json()['active']
    stale=tech.post('/api/time/clock/stop',json={'clock_id':active['id']})
    assert stale.json()['active']['id']==fresh['id']
    assert owner.put('/api/labor-rates',json={'technician_id':tech_id,'hourly_cost':20,'mileage_rate':.5}).status_code==200
    assert owner.post('/api/time/'+entries[0]['id']+'/approve',json={}).status_code==201
    assert owner.get('/api/jobs/'+job+'/field-costs').json()['approved_hours']==2.5

def test_access_overlap_and_one_active_clock(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;tech=clients['tech'];other=clients['other'];owner=clients['owner'];payload={'work_order_id':orders[0]}
    assert TestClient(m.app).get('/api/time/clock').status_code==401
    assert owner.post('/api/time/clock/start',json=payload).status_code==403
    assert other.post('/api/time/clock/start',json=payload).status_code==403
    active=tech.post('/api/time/clock/start',json=payload).json()['active']
    assert tech.post('/api/time/clock/start',json={'work_order_id':orders[1]}).status_code==409
    assert other.get('/api/time/clock').json()['active'] is None
    assert other.post('/api/time/clock/stop',json={'clock_id':active['id']}).status_code==409
    assert tech.get('/api/time/entries').status_code==403
    now[0]+=timedelta(hours=1)
    overlap={'started_at':(now[0]-timedelta(minutes=30)).isoformat(),'ended_at':now[0].isoformat()}
    assert tech.post('/api/work-orders/'+orders[1]+'/time',json=overlap).status_code==409
    before={'started_at':(now[0]-timedelta(hours=3)).isoformat(),'ended_at':(now[0]-timedelta(hours=2)).isoformat()}
    assert tech.post('/api/work-orders/'+orders[1]+'/time',json=before).status_code==201
    saved=tech.post('/api/time/clock/stop',json={'clock_id':active['id']})
    assert saved.status_code==200 and saved.json()['entry']['hours']==1
    assert tech.post('/api/work-orders/'+orders[1]+'/time',json=overlap).status_code==409

def test_mileage_replaces_total_and_costs_once(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;tech=clients['tech'];owner=clients['owner'];other=clients['other'];path='/api/work-orders/'+orders[0]+'/mileage'
    assert other.put(path,json={'start_odometer':100,'end_odometer':120,'expected_revision':0}).status_code==403
    assert tech.put(path,json={'start_odometer':100,'end_odometer':99,'expected_revision':0}).status_code==422
    saved=tech.put(path,json={'start_odometer':100,'end_odometer':120,'expected_revision':0})
    assert saved.status_code==200,saved.text
    assert saved.json()['miles']==20 and saved.json()['revision']==1
    assert tech.put(path,json={'start_odometer':100,'end_odometer':130,'expected_revision':0}).status_code==409
    assert tech.get('/api/time/mileage').status_code==403
    assert owner.put('/api/labor-rates',json={'technician_id':tech_id,'hourly_cost':20,'mileage_rate':.5}).status_code==200
    approve=path+'/'+tech_id+'/approve'
    assert other.post(approve,json={'expected_revision':1}).status_code==403
    assert owner.post(approve,json={'expected_revision':1}).json()['mileage_cost']==10
    assert owner.post(approve,json={'expected_revision':1}).status_code==409
    assert owner.get('/api/jobs/'+job+'/field-costs').json()['approved_field_labor_and_mileage']==10
    assert owner.get('/api/jobs/'+job+'/costs').json()['approved_field_cost']==10
    assert owner.get('/api/jobs/'+job+'/financial-overview').json()['historical_and_approved_actual_cost']==10
    replaced=tech.put(path,json={'start_odometer':100,'end_odometer':125,'expected_revision':1})
    assert replaced.json()['miles']==25 and not replaced.json()['approved']
    assert owner.get('/api/jobs/'+job+'/costs').json()['approved_field_cost']==0
    assert owner.post(approve,json={'expected_revision':1}).status_code==409
    assert owner.post(approve,json={'expected_revision':2}).json()['mileage_cost']==12.5
    assert owner.get('/api/jobs/'+job+'/costs').json()['approved_field_cost']==12.5
    assert len(tech.get(path).json())==1

def test_legacy_mileage_and_future_overlap(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;tech=clients['tech']
    with Session(m.engine) as db:
        db.add(m.FieldTimeEntry(work_order_id=orders[0],technician_id=tech_id,started_at=now[0]-timedelta(hours=2),ended_at=now[0]-timedelta(hours=1),hours=1,mileage=10,note='Existing v1.6 entry'));db.commit()
    assert tech.get('/api/work-orders/'+orders[0]+'/time').json()[0]['hours']==1
    assert tech.put('/api/work-orders/'+orders[0]+'/mileage',json={'start_odometer':100,'end_odometer':110,'expected_revision':0}).status_code==409
    future={'started_at':now[0].isoformat(),'ended_at':(now[0]+timedelta(hours=1)).isoformat()}
    assert tech.post('/api/work-orders/'+orders[0]+'/time',json=future).status_code==201
    assert tech.post('/api/time/clock/start',json={'work_order_id':orders[0]}).status_code==409
    assert tech.put('/api/work-orders/'+orders[1]+'/mileage',json={'start_odometer':100,'end_odometer':110,'expected_revision':0}).status_code==200
    assert tech.post('/api/work-orders/'+orders[1]+'/time',json={**future,'mileage':10}).status_code==409

def test_long_clock_can_stop_after_reassignment(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;tech=clients['tech']
    active=tech.post('/api/time/clock/start',json={'work_order_id':orders[0]}).json()['active']
    with Session(m.engine) as db:
        for a in db.scalars(select(m.DispatchAssignment).where(m.DispatchAssignment.work_order_id==orders[0],m.DispatchAssignment.technician_id==tech_id)):db.delete(a)
        db.commit()
    now[0]+=timedelta(hours=25)
    stopped=tech.post('/api/time/clock/stop',json={'clock_id':active['id']})
    assert stopped.status_code==200,stopped.text
    assert stopped.json()['entry']['hours']==25 and stopped.json()['entry']['needs_review']
    assert tech.get('/api/time/clock').json()['active'] is None


def test_start_reading_saved_before_finish_and_invoice_uses_calculated_miles(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;tech=clients['tech'];owner=clients['owner']
    path='/api/work-orders/'+orders[0]+'/mileage'
    start=tech.put(path,json={'start_odometer':120000,'expected_revision':0})
    assert start.status_code==200,start.text
    assert not start.json()['complete'] and start.json()['miles']==0
    owner.put('/api/labor-rates',json={'technician_id':tech_id,'hourly_cost':20,'mileage_rate':.5})
    approve=path+'/'+tech_id+'/approve'
    assert owner.post(approve,json={'expected_revision':1}).status_code==422
    end=tech.put(path,json={'start_odometer':120000,'end_odometer':120025,'expected_revision':1})
    assert end.status_code==200 and end.json()['miles']==25
    inv=owner.post('/api/invoices',json={'job_number':job,'number':'MILES'+uuid.uuid4().hex,'lines':[{'description':'Service labor','quantity':1,'unit_price':100}]}).json()
    endpoint='/api/invoices/'+inv['id']+'/call-mileage'
    charge={'work_order_id':orders[0],'unit_price':'1.25','tax_rate':8}
    assert owner.post(endpoint,json=charge).status_code==422  # approval required
    assert owner.post(approve,json={'expected_revision':2}).status_code==200
    assert tech.post(endpoint,json=charge).status_code==403
    billed=owner.post(endpoint,json=charge)
    assert billed.status_code==200,billed.text
    assert billed.json()['amount']==133.75
    line=billed.json()['lines'][-1]
    assert line['quantity']==25 and line['unit_price']==1.25 and line['tax']==2.5
    assert tech.get(path).json()[0]['invoiced']
    assert owner.post(endpoint,json=charge).status_code==409
    assert tech.put(path,json={'start_odometer':120000,'end_odometer':120030,'expected_revision':2}).status_code==409
    assert owner.put('/api/invoices/'+inv['id']+'/lines',json={'lines':[{'description':'Replacement','quantity':1,'unit_price':1}]}).status_code==409
    removed=owner.delete(endpoint+'/'+orders[0])
    assert removed.status_code==200 and removed.json()['amount']==100
    assert not tech.get(path).json()[0]['invoiced']
    assert owner.post(endpoint,json=charge).status_code==200
    assert owner.post('/api/invoices/'+inv['id']+'/issue').status_code==200
    assert owner.delete(endpoint+'/'+orders[0]).status_code==409
    assert owner.post(endpoint,json=charge).status_code==409
