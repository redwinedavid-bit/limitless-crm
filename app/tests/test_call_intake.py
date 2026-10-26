"""One-screen intake creates a complete call or leaves no partial records."""
import uuid
from datetime import timedelta
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from server import main as m
from test_time_clock import setup_clock


def payload():
    return {'request_id':str(uuid.uuid4()),'new_customer':{'name':'Intake '+uuid.uuid4().hex,'billing_address':'100 Billing Lane','email':'test@example.com'},'new_site':{'name':'Main office','address':'200 Service Lane'},'summary':'Office AC not cooling'}


def test_new_customer_site_call_assignment_and_replay(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];data=payload()
    data.update(technician_id=tech_id,scheduled_start=(now[0]+timedelta(days=8)).isoformat(),scheduled_end=(now[0]+timedelta(days=8,hours=2)).isoformat())
    r=owner.post('/api/calls/quick',json=data)
    assert r.status_code==201,r.text
    assert r.json()['status']=='Scheduled'
    w=owner.get('/api/calls/'+r.json()['id']+'/workspace').json()
    assert w['customer']['name']==data['new_customer']['name']
    assert w['location']['address']=='200 Service Lane'
    assert clients['tech'].get('/api/calls/'+r.json()['id']+'/workspace').status_code==200
    assert clients['other'].get('/api/calls/'+r.json()['id']+'/workspace').status_code==403
    assert owner.post('/api/calls/quick',json=data).json()['id']==r.json()['id']
    with Session(m.engine) as db:
        assert db.scalar(select(func.count()).select_from(m.Customer).where(m.Customer.name==data['new_customer']['name']))==1
        customer=db.scalar(select(m.Customer).where(m.Customer.name==data['new_customer']['name']))
        assert customer.billing_address=='100 Billing Lane'
        assert len(list(db.scalars(select(m.Location).where(m.Location.customer_id==customer.id))))==1
    assert owner.post('/api/calls/quick',json={**data,'request_id':str(uuid.uuid4())}).status_code==409


def test_failed_assignment_rolls_back_customer_site_and_job(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];data=payload()
    data.update(technician_id=tech_id,scheduled_start=now[0].isoformat(),scheduled_end=(now[0]+timedelta(hours=1)).isoformat())
    with Session(m.engine) as db:before=db.scalar(select(func.count()).select_from(m.Job))
    assert owner.post('/api/calls/quick',json=data).status_code==409
    with Session(m.engine) as db:
        assert db.scalar(select(m.Customer.id).where(m.Customer.name==data['new_customer']['name'])) is None
        assert db.scalar(select(func.count()).select_from(m.Job))==before
    data.update(scheduled_start=(now[0]+timedelta(days=9)).isoformat(),scheduled_end=(now[0]+timedelta(days=9,hours=1)).isoformat())
    assert owner.post('/api/calls/quick',json=data).status_code==201


def test_existing_customer_new_site_and_scope_checks(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];data=payload()
    cid=owner.get('/api/calls/'+orders[0]+'/workspace').json()['customer']['id']
    data.pop('new_customer');data['customer_id']=cid
    assert clients['tech'].post('/api/calls/quick',json=data).status_code==403
    r=owner.post('/api/calls/quick',json=data);assert r.status_code==201,r.text
    assert owner.post('/api/calls/quick',json={**data,'request_id':str(uuid.uuid4())}).status_code==409
    opts=owner.get('/api/calls/options').json()
    assert opts['can_create_customer'] and opts['can_create_site']
    site=next(s for s in opts['locations'] if s['customer_id']==cid and s['name']=='Main office')
    assert owner.post('/api/calls/quick',json={**payload(),'customer_id':cid}).status_code==422
    assert owner.post('/api/calls/quick',json={**data,'location_id':site['id']}).status_code==422
    assert owner.post('/api/calls/quick',json={**data,'job_number':job}).status_code==422
    assert owner.post('/api/calls/quick',json={**payload(),'new_customer':{'name':'  '}}).status_code==422
    with Session(m.engine) as db:
        u=db.get(m.User,tech_id);u.role='office';db.commit()
    assert clients['tech'].get('/api/calls/options').json()['can_create_site'] is False
    assert clients['tech'].post('/api/calls/quick',json={**data,'request_id':str(uuid.uuid4())}).status_code==403


def test_office_lookup_routes_populate_forms_without_technician_access(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner']
    customers=owner.get('/api/customers');jobs=owner.get('/api/jobs')
    assert customers.status_code==200 and jobs.status_code==200
    row=next(j for j in jobs.json() if j['id']==job)
    assert any(c['id']==row['customerId'] for c in customers.json())
    assert clients['tech'].get('/api/customers').status_code==403
    assert clients['tech'].get('/api/jobs').status_code==403


def test_call_page_uses_versioned_script_and_revalidates_cache(setup_clock):
    import re
    owner = setup_clock[0]['owner']
    page = owner.get('/calls')
    assert page.status_code == 200
    assert page.headers['cache-control'] == 'private, no-cache'
    script_url = re.search(r'<script src="(/calls\.js\?v=[^"]+)"', page.text).group(1)
    script = owner.get(script_url)
    assert script.status_code == 200
    assert script.headers['cache-control'] == 'private, no-cache'
    assert 'No calls to show' in script.text
    # The script and page must agree about every directly referenced element.
    ids = set(re.findall(r'\bid="([^"]+)"', page.text))
    referenced = set(re.findall(r"el\('([^']+)'\)", script.text))
    assert referenced <= ids, referenced - ids

