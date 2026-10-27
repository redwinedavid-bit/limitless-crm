"""Job-linked purchasing and shared employee cost profiles."""
import uuid
import pytest
from sqlalchemy.orm import Session
from server import main as m
from test_time_clock import setup_clock


@pytest.fixture(autouse=True)
def restore_test_buyer_designators():
    # Existing purchasing tests also use the three unique designators.
    with Session(m.engine) as db:
        saved={u.id:u.po_prefix for u in db.query(m.User).filter(m.User.po_prefix!=None)}
    yield
    with Session(m.engine) as db:
        for u in db.query(m.User).filter(m.User.po_prefix!=None):u.po_prefix=None
        db.flush()
        for uid,prefix in saved.items():db.get(m.User,uid).po_prefix=prefix
        db.commit()


def test_employee_cost_profiles_include_admins_and_preserve_approvals(setup_clock):
    clients,orders,now,tech_id,job=setup_clock
    owner,tech=clients['owner'],clients['tech']
    profiles=owner.get('/api/admin/labor-burden').json()
    owner_id=owner.get('/api/auth/me').json()['id']
    assert {owner_id,tech_id}.issubset({p['technician_id'] for p in profiles})
    assert tech.get('/api/admin/labor-burden').status_code==403
    assert tech.get('/employee-costs').status_code==403
    assert owner.get('/employee-costs').status_code==200
    body={'base_wage':30,'payroll_percent':10,'workers_comp_percent':5,'benefits_per_hour':2,'mileage_rate':.6,'overtime_multiplier':1.5,'expected_revision':0}
    for uid in (owner_id,tech_id):
        assert owner.put('/api/admin/labor-burden/'+uid,json=body).status_code==200
    assert tech.put('/api/admin/labor-burden/'+tech_id,json={**body,'expected_revision':1}).status_code==403
    assert owner.put('/api/admin/labor-burden/'+tech_id,json=body).status_code==409
    path='/api/work-orders/'+orders[0]+'/mileage'
    assert tech.put(path,json={'start_odometer':100,'end_odometer':110,'expected_revision':0}).status_code==200
    assert owner.post(path+'/'+tech_id+'/approve',json={'expected_revision':1}).json()['mileage_cost']==6
    assert owner.put('/api/admin/labor-burden/'+tech_id,json={**body,'base_wage':40,'mileage_rate':1,'expected_revision':1}).status_code==200
    assert owner.get('/api/jobs/'+job+'/field-costs').json()['approved_call_mileage_cost']==6
    rates=owner.get('/api/labor-rates').json()
    assert next(r for r in rates if r['technician_id']==tech_id)['hourly_cost']==40


def test_call_po_uses_assigned_job_and_retries_once(setup_clock):
    clients,orders,now,tech_id,job=setup_clock
    owner,tech=clients['owner'],clients['tech']
    with Session(m.engine) as db:
        # Only one account can hold each buyer designator.
        for u in db.query(m.User).filter(m.User.po_prefix=='DB'):u.po_prefix=None
        db.flush();db.get(m.User,tech_id).po_prefix='DB';db.commit()
    path='/api/calls/'+orders[0]+'/purchase-orders'
    data={'request_id':str(uuid.uuid4()),'vendor':'Test supplier','description':'Motor for assigned call','amount':'75.00'}
    a=tech.post(path,json=data)
    assert a.status_code==201,a.text
    assert a.json()['number'].startswith('DB') and a.json()['job_number']==job
    assert tech.post(path,json=data).json()['number']==a.json()['number']
    assert tech.post(path,json={**data,'amount':'76.00'}).status_code==409
    assert tech.post(path,json={**data,'request_id':str(uuid.uuid4()),'job_number':'LMS999999'}).status_code==422
    unassigned=owner.post('/api/work-orders',json={'job_number':job,'summary':'Unassigned'}).json()['id']
    assert tech.post('/api/calls/'+unassigned+'/purchase-orders',json={**data,'request_id':str(uuid.uuid4())}).status_code==403
    assert clients['other'].post(path,json={**data,'request_id':str(uuid.uuid4())}).status_code==403
    financial=owner.get('/api/jobs/'+job+'/financial-overview').json()
    assert financial['open_new_po_commitment']==0 and financial['new_po_actual']==75


def test_proposal_dashboard_tracks_out_and_links_accepted_job_once(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner']
    ids=[]
    for i in range(2):
        q=owner.post('/api/proposals',json={'job_number':job,'number':'Q'+uuid.uuid4().hex[:10],'title':'Replacement','scope':'Test scope','total':100}).json()
        ids.append(q['id'])
    endpoint='/api/admin/proposal-pipeline/'+ids[0]
    assert clients['tech'].get('/api/admin/proposal-pipeline').status_code==403
    assert owner.put(endpoint,json={'stage':'Sent','reference':'Sent by office email','expected_revision':0}).status_code==200
    data=owner.get('/api/admin/proposal-pipeline').json()
    assert next(q for q in data['proposals'] if q['id']==ids[0])['stage']=='Sent'
    assert owner.put(endpoint,json={'stage':'Declined','reference':'Customer declined','expected_revision':0}).status_code==409
    assert owner.put(endpoint,json={'stage':'Declined','reference':'Customer declined','expected_revision':1}).status_code==200
    for id in ids:assert owner.post('/api/proposals/'+id+'/approve').status_code==200
    data=owner.get('/api/admin/proposal-pipeline').json()
    accepted=[j for j in data['accepted_jobs'] if j['job_number']==job]
    assert len(accepted)==1 and accepted[0]['url'].endswith('job='+job)
    assert owner.put(endpoint,json={'stage':'Sent','reference':'Invalid overwrite','expected_revision':2}).status_code==409
    assert len([j for j in owner.get('/api/jobs').json() if j['id']==job])==1


def test_employee_purchasing_access_requires_admin_and_unique_designator(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];other=clients['other'].get('/api/auth/me').json()['id']
    with Session(m.engine) as db:
        for u in db.query(m.User).filter(m.User.po_prefix=='AR'):u.po_prefix=None
        db.commit()
    path='/api/admin/employees/'+tech_id+'/po-designator'
    assert clients['tech'].put(path,json={'prefix':'AR'}).status_code==403
    assert owner.put(path,json={'prefix':'AR'}).status_code==200
    assert owner.put('/api/admin/employees/'+other+'/po-designator',json={'prefix':'AR'}).status_code==409
    assert owner.put(path,json={'prefix':'DB'}).status_code==409

