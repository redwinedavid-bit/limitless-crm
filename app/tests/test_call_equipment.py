"""Call-scoped equipment creation retains site identity and assignment access."""
import uuid
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from fastapi import HTTPException
from server import main as m
from test_time_clock import setup_clock


def sites(setup_clock):
    clients,orders,now,tech_id,job_number=setup_clock
    with Session(m.engine) as db:
        job=db.scalar(select(m.Job).where(m.Job.job_number==job_number))
        a=m.Location(customer_id=job.customer_id,name='School A',address='100 Service Lane')
        b=m.Location(customer_id=job.customer_id,name='School B',address='200 Service Lane')
        customer=m.Customer(name='Other customer '+uuid.uuid4().hex)
        db.add_all([a,b,customer]);db.flush()
        other=m.Location(customer_id=customer.id,name='Other site',address='300 Elsewhere')
        db.add(other);db.flush();ids=(a.id,b.id,other.id);job.location_id=a.id;db.commit()
    return ids


def test_create_and_link_for_assigned_technician(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;a,b,other=sites(setup_clock)
    tech=clients['tech'];base='/api/calls/'+orders[0]
    locations=tech.get(base+'/equipment-locations').json()
    assert locations['location_id']==a and [l['id'] for l in locations['locations']]==[a]
    payload={'asset_tag':' RTU-1 ','equipment_type':'Packaged unit','manufacturer':'Carrier','model':'MODEL','serial':'SERIAL','refrigerant':'R-410A','notes':'Gym roof'}
    assert clients['other'].post(base+'/service-systems',json=payload).status_code==403
    assert clients['other'].get(base+'/equipment-locations').status_code==403
    assert tech.post(base+'/service-systems',json={**payload,'location_id':b}).status_code==422
    assert tech.post(base+'/service-systems',json={**payload,'location_id':other}).status_code==422
    r=tech.post(base+'/service-systems',json=payload);assert r.status_code==201,r.text
    item=r.json();assert item['linked'] and item['location_id']==a and item['asset_tag']=='RTU-1'
    assert item['serial']=='SERIAL' and item['notes']=='Gym roof'
    rows=tech.get(base+'/service-systems').json()
    assert any(e['id']==item['id'] and e['linked'] for e in rows)
    assert tech.post(base+'/service-systems',json={**payload,'asset_tag':'rtu-1'}).status_code==409
    assert tech.post(base+'/service-systems',json={**payload,'asset_tag':'   '}).status_code==422
    assert tech.post(base+'/service-systems',json={**payload,'serial':'x'*121}).status_code==422
    # Catalog persists beyond this visit, without creating duplicate equipment.
    equipment=clients['owner'].get('/api/equipment',params={'location_id':a}).json()
    assert [e['id'] for e in equipment]==[item['id']]


def test_location_choice_and_atomic_rollback(setup_clock,monkeypatch):
    clients,orders,now,tech_id,job_number=setup_clock;a,b,other=sites(setup_clock)
    tech=clients['tech'];base='/api/calls/'+orders[0]
    with Session(m.engine) as db:
        job=db.scalar(select(m.Job).where(m.Job.job_number==job_number));job.location_id=None;db.commit()
    assert len(tech.get(base+'/equipment-locations').json()['locations'])==2
    payload={'asset_tag':'FCU-1','location_id':b}
    assert tech.post(base+'/service-systems',json={'asset_tag':'FCU-1'}).status_code==422
    original=m.log
    def fail(db,action,*args):
        if action=='CREATE_CALL_EQUIPMENT':raise RuntimeError('Injected audit failure')
        return original(db,action,*args)
    monkeypatch.setattr(m,'log',fail)
    with pytest.raises(RuntimeError):tech.post(base+'/service-systems',json=payload)
    with Session(m.engine) as db:
        assert db.scalar(select(m.Equipment.id).where(m.Equipment.location_id==b)) is None
        assert db.scalar(select(func.count()).select_from(m.WorkOrderEquipment).where(m.WorkOrderEquipment.work_order_id==orders[0]))==0
    monkeypatch.setattr(m,'log',original)
    assert tech.post(base+'/service-systems',json=payload).status_code==201
    def locked(db,wo):raise HTTPException(409,'Report locked')
    monkeypatch.setattr(m,'field_report_open',locked)
    assert tech.post(base+'/service-systems',json={**payload,'asset_tag':'FCU-2'}).status_code==409
    with Session(m.engine) as db:assert db.scalar(select(m.Equipment.id).where(m.Equipment.asset_tag=='FCU-2')) is None
