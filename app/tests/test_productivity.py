import json
import uuid
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from server import main as m, call_pricing as pricing, time_corrections as corrections
from server.field_v14 import _snapshot
from server.productivity import QuickCallReceipt
from test_time_clock import setup_clock
from test_call_pricing import quote


def catalog_part(owner,cost='65'):
    r=owner.post('/api/parts-catalog',json={'sku':'PART'+uuid.uuid4().hex[:8],'description':'Sample contactor','unit':'each','unit_cost':cost})
    assert r.status_code==201,r.text
    return r.json()


def test_customer_defaults_favorites_and_bundles(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech']
    customer_id=owner.get('/api/calls/'+orders[0]+'/workspace').json()['customer']['id']
    assert tech.get('/api/admin/pricing-defaults').status_code==403
    g=owner.put('/api/admin/pricing-defaults',json={'labor_sell_per_hour':'100','mileage_sell_per_mile':'1.25','target_margin':40,'expected_revision':0})
    assert g.status_code==200,g.text
    endpoint='/api/admin/pricing-defaults?customer_id='+customer_id
    assert owner.put(endpoint,json={'labor_sell_per_hour':'120','expected_revision':0}).status_code==200
    values=owner.get('/api/admin/calls/'+orders[0]+'/pricing-defaults').json()['values']
    assert values['labor_sell_per_hour']=='120' and values['target_margin']==40
    assert owner.put(endpoint,json={'target_margin':35.5,'expected_revision':1}).status_code==422
    assert owner.put(endpoint,json={'expected_revision':1}).status_code==200
    assert owner.get(endpoint).json()['values']['labor_sell_per_hour']=='100'
    assert owner.put(endpoint,json={'expected_revision':1}).status_code==409
    p=catalog_part(owner)
    assert tech.put('/api/parts-catalog/'+p['id']+'/favorite',json={'favorite':True}).status_code==403
    assert owner.put('/api/parts-catalog/'+p['id']+'/favorite',json={'favorite':True}).status_code==200
    assert p['id'] in owner.get('/api/admin/favorite-parts').json()
    data={'name':'Contactor repair '+uuid.uuid4().hex[:6],'parts':[{'part_id':p['id'],'quantity':2}],'labor_hours':1.5}
    bundle=owner.post('/api/admin/repair-bundles',json=data)
    assert bundle.status_code==201,bundle.text
    assert bundle.json()['parts'][0]['unit_cost']=='65.00'
    assert tech.get('/api/admin/repair-bundles').status_code==403
    assert owner.put('/api/admin/repair-bundles/'+bundle.json()['id'],json={**data,'expected_revision':0}).status_code==409
    assert owner.put('/api/admin/repair-bundles/'+bundle.json()['id'],json={**data,'expected_revision':1,'active':False}).status_code==200


def test_private_autosave_conflicts_and_no_publication(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;tech=clients['tech'];other=clients['other'];owner=clients['owner']
    path='/api/drafts/'+orders[0]+'/field-note'
    body={'text':'Unsubmitted diagnosis','kind':'recommendation','audience':'internal'}
    assert tech.put(path,json={'body':body,'expected_revision':0}).status_code==200
    assert tech.get(path).json()['body']==body
    assert owner.get(path).json()['body']=={}  # each user's draft is private
    assert other.get(path).status_code==403
    assert tech.put(path,json={'body':{'text':'stale overwrite'},'expected_revision':0}).status_code==409
    assert tech.get('/api/calls/'+orders[0]+'/notes').json()==[]
    assert tech.get('/api/calls/'+orders[0]+'/customer-summary').json()['notes']==[]
    assert tech.put('/api/drafts/'+orders[0]+'/pricing-actual',json={'body':{},'expected_revision':0}).status_code==403
    assert owner.put('/api/drafts/'+orders[0]+'/pricing-actual',json={'body':{'labor_sell_per_hour':'','parts':[{'quantity':''}]},'expected_revision':0}).status_code==200
    assert not any(i['job_number']==job for i in owner.get('/api/invoices').json())
    assert tech.put(path,json={'body':{'text':'x'*10001},'expected_revision':1}).status_code==422


def test_reported_parts_and_customer_internal_note_separation(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];other=clients['other'];wo=orders[0]
    p=catalog_part(owner)
    public=tech.get('/api/field/parts-catalog')
    assert public.status_code==200
    item=next(x for x in public.json() if x['id']==p['id'])
    assert set(item)=={'id','sku','description','unit'}
    part={'client_request_id':str(uuid.uuid4()),'part_id':p['id'],'quantity':2,'internal_note':'Private supplier discussion'}
    used=tech.post('/api/calls/'+wo+'/parts-used',json=part)
    assert used.status_code==201,used.text
    assert tech.post('/api/calls/'+wo+'/parts-used',json=part).json()['id']==used.json()['id']
    assert tech.post('/api/calls/'+wo+'/parts-used',json={**part,'quantity':3}).status_code==409
    assert other.get('/api/calls/'+wo+'/parts-used').status_code==403
    for kind in ('service_description','recommendation','follow_up'):
        for audience in ('customer','internal'):
            note={'client_request_id':str(uuid.uuid4()),'kind':kind,'audience':audience,'text':audience+' '+kind+' unique text'}
            r=tech.post('/api/calls/'+wo+'/notes',json=note);assert r.status_code==201,r.text
            assert tech.post('/api/calls/'+wo+'/notes',json=note).json()['id']==r.json()['id']
    summary=tech.get('/api/calls/'+wo+'/customer-summary').text
    assert 'customer recommendation unique text' in summary and 'internal recommendation unique text' not in summary
    assert 'Private supplier discussion' not in summary and 'Sample contactor' in summary
    html=tech.get('/api/work-orders/'+wo+'/report').text
    assert 'customer service_description unique text' in html and 'internal follow_up unique text' not in html
    with Session(m.engine) as db:
        snapshot=_snapshot(db,db.get(m.WorkOrder,wo))
        assert 'internal recommendation unique text' not in json.dumps(snapshot)
        assert 'customer recommendation unique text' in json.dumps(snapshot)
    records=owner.get('/api/admin/calls/'+wo+'/reported-parts').json()
    assert records[0]['usage_id']==used.json()['id'] and records[0]['unit_cost']=='65.00'
    assert tech.get('/api/admin/calls/'+wo+'/reported-parts').status_code==403
    workspace=tech.get('/api/calls/'+wo+'/workspace')
    assert workspace.status_code==200,workspace.text
    assert 'readiness' not in workspace.json() and 'unit_cost' not in workspace.text


def test_quick_call_retry_location_validation_and_assignment(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech']
    options=owner.get('/api/calls/options');assert options.status_code==200
    customer_id=owner.get('/api/calls/'+orders[0]+'/workspace').json()['customer']['id']
    data={'request_id':str(uuid.uuid4()),'customer_id':customer_id,'summary':'New service call','technician_id':tech_id,
          'scheduled_start':(now[0]+timedelta(days=4)).isoformat(),'scheduled_end':(now[0]+timedelta(days=4,hours=1)).isoformat()}
    assert tech.post('/api/calls/quick',json=data).status_code==403
    created=owner.post('/api/calls/quick',json=data)
    assert created.status_code==201,created.text
    assert created.json()['status']=='Scheduled'
    assert owner.post('/api/calls/quick',json=data).json()['id']==created.json()['id']
    assert owner.post('/api/calls/quick',json={**data,'summary':'Changed request'}).status_code==409
    assert tech.get('/api/calls/'+created.json()['id']+'/workspace').status_code==200
    assert owner.post('/api/calls/quick',json={**data,'request_id':str(uuid.uuid4()),'location_id':'missing'}).status_code==422
    overlap=owner.post('/api/calls/quick',json={**data,'request_id':str(uuid.uuid4())})
    assert overlap.status_code==409
    with Session(m.engine) as db:assert len(db.scalars(select(QuickCallReceipt).where(QuickCallReceipt.work_order_id==created.json()['id'])).all())==1


def test_time_correction_review_preserves_original_and_resets_cost_approval(setup_clock,monkeypatch):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];wo=orders[0]
    monkeypatch.setattr(corrections,'clock_now',lambda:now[0]+timedelta(hours=10))
    entry=tech.post('/api/work-orders/'+wo+'/time',json={'started_at':now[0].isoformat(),'ended_at':(now[0]+timedelta(hours=4)).isoformat()}).json()
    owner.put('/api/labor-rates',json={'technician_id':tech_id,'hourly_cost':50})
    assert owner.post('/api/time/'+entry['id']+'/approve',json={}).status_code==201
    request={'entry_id':entry['id'],'started_at':now[0].isoformat(),'ended_at':(now[0]+timedelta(hours=2)).isoformat(),'reason':'Forgot to clock out'}
    correction=tech.post('/api/time/corrections',json=request)
    assert correction.status_code==201,correction.text
    assert 'labor_cost' not in correction.text
    assert tech.post('/api/time/corrections',json=request).status_code==409
    assert tech.post('/api/time/corrections/'+correction.json()['id']+'/review',json={'decision':'approve','note':'Reviewed'}).status_code==403
    assert owner.post('/api/time/'+entry['id']+'/approve',json={}).status_code==409
    reviewed=owner.post('/api/time/corrections/'+correction.json()['id']+'/review',json={'decision':'approve','note':'Confirmed with technician'})
    assert reviewed.status_code==200,reviewed.text
    saved=tech.get('/api/work-orders/'+wo+'/time').json()[0]
    assert saved['hours']==2 and not saved['approved']
    admin=owner.get('/api/time/corrections').json()
    record=next(x for x in admin if x['id']==correction.json()['id'])
    assert record['original']['hours']=='4.00' and record['original']['approval']['labor_cost']=='200.00'
    assert tech.get('/api/time/corrections').json()[0]['original']['approval']=={'previously_approved':True}
    assert owner.post('/api/time/corrections/'+correction.json()['id']+'/review',json={'decision':'approve','note':'Again'}).status_code==409


def test_forgotten_running_clock_correction(setup_clock,monkeypatch):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];wo=orders[0]
    monkeypatch.setattr(corrections,'clock_now',lambda:now[0])
    started=tech.post('/api/time/clock/start',json={'work_order_id':wo}).json()['active']
    assert tech.patch('/api/work-orders/'+wo+'/status',json={'status':'Completed'}).status_code==409
    original_start=now[0];now[0]+=timedelta(hours=5)
    request=tech.post('/api/time/corrections',json={'clock_id':started['id'],'started_at':original_start.isoformat(),'ended_at':(original_start+timedelta(hours=3)).isoformat(),'reason':'Forgot clock out after service'})
    assert request.status_code==201,request.text
    review=owner.post('/api/time/corrections/'+request.json()['id']+'/review',json={'decision':'approve','note':'Confirmed the finish time'})
    assert review.status_code==200,review.text
    assert tech.get('/api/time/clock').json()['active'] is None
    assert tech.get('/api/work-orders/'+wo+'/time').json()[0]['hours']==3
    assert tech.post('/api/time/clock/stop',json={'clock_id':started['id']}).status_code==200
