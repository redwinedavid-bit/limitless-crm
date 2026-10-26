"""Profit controls, callback attribution, system flags and lifetime history boundaries."""
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session
from server import main as m, profit_tracking as profit, call_pricing as pricing
from server.call_field_notes import CallNote
from test_time_clock import setup_clock
from test_call_pricing import quote


def customer_id(owner,wo):return owner.get('/api/calls/'+wo+'/workspace').json()['customer']['id']

def prepare_actual(setup,wo,index=0,price=100,cost=65):
    clients,orders,now,tech_id,job=setup;owner=clients['owner'];tech=clients['tech']
    start=now[0]+timedelta(days=index)
    e=tech.post('/api/work-orders/'+wo+'/time',json={'started_at':start.isoformat(),'ended_at':(start+timedelta(hours=1)).isoformat()})
    assert e.status_code==201,e.text
    assert owner.put('/api/labor-rates',json={'technician_id':tech_id,'hourly_cost':cost}).status_code==200
    a=owner.post('/api/time/'+e.json()['id']+'/approve',json={});assert a.status_code==201,a.text
    path='/api/work-orders/'+wo+'/mileage'
    assert tech.put(path,json={'start_odometer':100,'end_odometer':100,'expected_revision':0}).status_code==200
    assert owner.post(path+'/'+tech_id+'/approve',json={'expected_revision':1}).status_code==200
    data=quote(mode='actual',parts=[],labor_sell_per_hour=price,auto_target=False)
    result=owner.put('/api/call-pricing/'+wo+'/actual',json=data);assert result.status_code==200,result.text
    return result.json(),e.json()


def test_loaded_labor_snapshots_overtime_and_corrections(setup_clock,monkeypatch):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech']
    body={'base_wage':'30','payroll_percent':'10','workers_comp_percent':'5','benefits_per_hour':'5','overtime_multiplier':'1.5'}
    assert tech.get('/api/admin/labor-burden').status_code==403
    assert owner.put('/api/admin/labor-burden/'+tech_id,json=body).status_code==200
    assert owner.put('/api/admin/labor-burden/'+tech_id,json=body).status_code==409
    entry=tech.post('/api/work-orders/'+orders[0]+'/time',json={'started_at':now[0].isoformat(),'ended_at':(now[0]+timedelta(hours=2)).isoformat()}).json()
    approved=owner.post('/api/time/'+entry['id']+'/approve',json={'overtime_hours':1})
    assert approved.status_code==201,approved.text
    # 30 regular + 45 OT, 15% wage burden, two hours of benefits.
    assert approved.json()['labor_cost']==96.25
    approval_id=approved.json()['id']
    with Session(m.engine) as db:
        snapshot=json.loads(db.get(profit.LaborCostSnapshot,approval_id).details_json)
        assert snapshot['profile_revision']==1 and snapshot['total']=='96.25'
    assert owner.put('/api/admin/labor-burden/'+tech_id,json={**body,'base_wage':40,'expected_revision':1}).status_code==200
    with Session(m.engine) as db:assert db.get(m.TimeApproval,approval_id).labor_cost==Decimal('96.25')
    from server import time_corrections
    monkeypatch.setattr(time_corrections,'clock_now',lambda:now[0]+timedelta(hours=5))
    requested=tech.post('/api/time/corrections',json={'entry_id':entry['id'],'started_at':now[0].isoformat(),'ended_at':(now[0]+timedelta(hours=1)).isoformat(),'reason':'Accidental extra hour'}).json()
    reviewed=owner.post('/api/time/corrections/'+requested['id']+'/review',json={'decision':'approve','note':'Verified clock error'})
    assert reviewed.status_code==200,reviewed.text
    with Session(m.engine) as db:
        assert db.get(m.TimeApproval,approval_id) is None
        assert json.loads(db.get(profit.LaborCostSnapshot,approval_id).details_json)['total']=='96.25'


def test_minimum_charge_acquisition_overhead_and_historical_prices(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];wo=orders[0];cid=customer_id(owner,wo)
    policy='/api/admin/profit-policy?customer_id='+cid
    assert owner.put(policy,json={'minimum_call_price':'250','overhead_per_hour':'20','expected_revision':0}).status_code==200
    assert owner.put(policy,json={'expected_revision':0}).status_code==409
    assert clients['tech'].get(policy).status_code==403
    data=quote(parts=[],labor_hours=2,labor_cost_per_hour=30,labor_sell_per_hour=40,acquisition_freight=10,nonrecoverable_purchase_tax=5)
    saved=owner.put('/api/call-pricing/'+wo+'/estimate',json=data)
    assert saved.status_code==200,saved.text
    result=saved.json()['pricing']
    assert result['total_direct_cost']=='75.00' and result['selling_subtotal']=='250.00'
    assert result['allocated_overhead']=='40.00' and result['profit_after_allocated_overhead']=='135.00'
    assert owner.put(policy,json={'minimum_call_price':300,'overhead_per_hour':30,'expected_revision':1}).status_code==200
    old=owner.get('/api/call-pricing/'+wo+'/estimate').json()['pricing']
    assert old['selling_subtotal']=='250.00' and old['allocated_overhead']=='40.00'
    below=owner.post('/api/call-pricing/'+wo+'/preview',json={**data,'auto_target':False,'margin_override_reason':'Customer accommodation approved'})
    assert below.status_code==200 and not below.json()['ready_to_export']  # minimum cannot be overridden


def test_margin_exception_is_audited_and_exact_price_scoped(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];wo=orders[0];base='/api/call-pricing/'+wo
    body=quote(auto_target=False,margin_override_reason='Approved goodwill discount')
    assert clients['tech'].put(base+'/estimate',json=body).status_code==403
    assert owner.put(base+'/estimate',json={**body,'margin_override_reason':'short'}).status_code==422
    saved=owner.put(base+'/estimate',json=body)
    assert saved.status_code==200,saved.text
    result=saved.json()['pricing'];assert result['below_target'] and result['ready_to_export']
    assert result['margin_override']['approved']
    assert pricing.allowed_floor(result)==Decimal('100.00')
    proposal=owner.post(base+'/proposal/export',json={'number':'EX'+uuid.uuid4().hex[:8],'expected_revision':1})
    assert proposal.status_code==200,proposal.text
    with Session(m.engine) as db:
        assert pricing.allowed_floor(json.loads(db.get(pricing.CallPricingExport,(wo,'proposal')).snapshot_json))==Decimal('100.00')
    # Changing the price cannot reuse an old exception in a fresh saved revision.
    no_exception=owner.put(base+'/estimate',json=quote(auto_target=False,expected_revision=1)).json()
    assert not no_exception['pricing']['ready_to_export']
    assert owner.post(base+'/proposal/export',json={'number':proposal.json()['number'],'expected_revision':2}).status_code==409


def test_invoice_exception_cannot_cover_a_later_discount(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];wo=orders[0]
    prepare_actual(setup_clock,wo,price=50,cost=65)
    saved=owner.put('/api/call-pricing/'+wo+'/actual',json=quote(mode='actual',parts=[],labor_sell_per_hour=50,auto_target=False,expected_revision=1,margin_override_reason='Warranty concession approved'))
    assert saved.status_code==200,saved.text
    exported=owner.post('/api/call-pricing/'+wo+'/invoice/export',json={'number':'W'+uuid.uuid4().hex[:9],'expected_revision':2})
    assert exported.status_code==200,exported.text
    inv=exported.json()['id']
    assert owner.put('/api/invoices/'+inv+'/lines',json={'lines':[{'description':'Discounted service','quantity':1,'unit_price':40}]}).status_code==200
    assert owner.post('/api/invoices/'+inv+'/issue').status_code==409
    assert owner.put('/api/invoices/'+inv+'/lines',json={'lines':[{'description':'Approved service','quantity':1,'unit_price':50}]}).status_code==200
    assert owner.post('/api/invoices/'+inv+'/issue').status_code==200


def test_callback_costs_reduce_original_family_profit_without_double_counting(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];cid=customer_id(owner,orders[0])
    prepare_actual(setup_clock,orders[0],price=100,cost=65)
    exported=owner.post('/api/call-pricing/'+orders[0]+'/invoice/export',json={'number':'F'+uuid.uuid4().hex[:9],'expected_revision':1}).json()
    assert owner.post('/api/invoices/'+exported['id']+'/issue').status_code==200
    prepare_actual(setup_clock,orders[1],index=1,price=0,cost=20)
    classified=owner.put('/api/admin/calls/'+orders[1]+'/category',json={'job_type':'Warranty','original_call_id':orders[0],'reason':'Return to correct original repair'})
    assert classified.status_code==200,classified.text
    history=owner.get('/api/admin/customers/'+cid+'/history')
    assert history.status_code==200,history.text
    families=history.json()['profitability']['families'];assert len(families)==1
    f=families[0];assert f['callback_count']==1 and f['callback_cost']=='20.00'
    assert f['cost']=='85.00' and f['gross_profit']=='15.00' and f['margin_percent']=='15.00'
    assert history.json()['profitability']['by_customer'][0]['gross_profit']=='15.00'
    assert owner.put('/api/admin/calls/'+orders[0]+'/category',json={'original_call_id':orders[1],'reason':'Try to make a cycle'}).status_code==422
    assert clients['tech'].get('/api/admin/customers/'+cid+'/history').status_code==403
    assert clients['tech'].get('/profitability').status_code==403
    assert clients['tech'].get('/api/admin/profitability').status_code==403


def test_system_flags_distinct_calls_maintenance_exclusion_window_and_thresholds(setup_clock,monkeypatch):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];cid=customer_id(owner,orders[0])
    monkeypatch.setattr(profit,'stamp',lambda:now[0]+timedelta(days=10))
    with Session(m.engine) as db:
        loc=m.Location(customer_id=cid,name='Test plant');db.add(loc);db.commit();location={'id':loc.id}
    asset=owner.post('/api/equipment',json={'location_id':location['id'],'asset_tag':'RTU-1'}).json()
    asset2=owner.post('/api/equipment',json={'location_id':location['id'],'asset_tag':'RTU-2'}).json()
    third=owner.post('/api/work-orders',json={'job_number':job,'summary':'Third service call'}).json()['id']
    for i,wo in enumerate([*orders,third]):
        with Session(m.engine) as db:db.get(m.WorkOrder,wo).created_at=now[0]+timedelta(days=i);db.commit()
        assert owner.post('/api/work-orders/'+wo+'/equipment/'+asset['id']).status_code==201
    planned=owner.post('/api/work-orders',json={'job_number':job,'summary':'Generated PM visit'}).json()['id']
    assert owner.post('/api/work-orders/'+planned+'/equipment/'+asset['id']).status_code==201
    with Session(m.engine) as db:
        db.get(m.WorkOrder,planned).created_at=now[0]+timedelta(days=3)
        plan=m.PMPlan(equipment_id=asset['id'],interval_days=30,next_due=now[0].date(),checklist='Inspect and clean');db.add(plan);db.flush()
        db.add(m.PMVisit(plan_id=plan.id,work_order_id=planned,scheduled_due=now[0].date()));db.commit()
    path='/api/admin/equipment/'+asset['id']+'/health'
    health=owner.get(path).json();assert health['flagged'] and health['service_count']==3
    assert not owner.get('/api/admin/equipment/'+asset2['id']+'/health').json()['flagged']
    assert clients['tech'].get(path).status_code==403
    assert owner.put('/api/admin/calls/'+third+'/category',json={'job_type':'Maintenance'}).status_code==200
    health=owner.get(path).json();assert not health['flagged'] and health['excluded_maintenance_count']==2
    assert owner.put('/api/admin/calls/'+orders[1]+'/category',json={'job_type':'Warranty','original_call_id':orders[0],'reason':'Repeat failure after repair'}).status_code==200
    policy='/api/admin/profit-policy?customer_id='+cid
    assert owner.put(policy,json={'repeat_callbacks':1,'repeat_service_calls':5,'repeat_window_days':90}).status_code==200
    health=owner.get(path).json();assert health['flagged'] and health['callback_count']==1 and 'callbacks' in health['reasons'][0]
    # More notes and time entries do not create extra service visits.
    assert len(health['visits'])==2
    monkeypatch.setattr(profit,'stamp',lambda:now[0]+timedelta(days=200))
    assert not owner.get(path).json()['flagged']
    assert owner.put(policy,json={'repeat_window_days':0,'expected_revision':1}).status_code==422


def test_customer_recommendations_tracking_billing_and_payment_history(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];cid=customer_id(owner,orders[0])
    n=tech.post('/api/calls/'+orders[0]+'/notes',json={'client_request_id':str(uuid.uuid4()),'kind':'recommendation','audience':'internal','text':'Monitor intermittent contactor chatter'}).json()
    endpoint='/api/admin/customers/'+cid
    assert owner.get(endpoint+'/history').json()['summary']['open_follow_ups']==1
    body={'text':n['text'],'source_note_id':n['id'],'work_order_id':orders[0],'due_on':'2026-11-01'}
    follow=owner.post(endpoint+'/follow-ups',json=body)
    assert follow.status_code==201,follow.text
    assert owner.post(endpoint+'/follow-ups',json=body).status_code==409
    assert owner.get(endpoint+'/history').json()['summary']['open_follow_ups']==1
    assert owner.put(endpoint+'/follow-ups/'+follow.json()['id'],json={**body,'status':'Resolved','expected_revision':1}).status_code==200
    assert owner.get(endpoint+'/history').json()['summary']['open_follow_ups']==0
    assert tech.get('/api/calls/'+orders[0]+'/customer-summary').json()['notes']==[]
    prepare_actual(setup_clock,orders[0],price=100,cost=50)
    export=owner.post('/api/call-pricing/'+orders[0]+'/invoice/export',json={'number':'H'+uuid.uuid4().hex[:9],'expected_revision':1,'tax_rate':10}).json()
    assert owner.post('/api/invoices/'+export['id']+'/issue').status_code==200
    with Session(m.engine) as db:
        invoice=db.get(m.InvoiceRecord,export['id']);invoice.issued_on=now[0].date();invoice.due_on=now[0].date()+timedelta(days=30)
        db.add(m.InvoicePayment(invoice_id=invoice.id,amount=110,paid_on=now[0].date()+timedelta(days=12),reference=uuid.uuid4().hex));db.commit()
    h=owner.get(endpoint+'/history').json();assert h['summary']['net_billed_including_tax']=='110.00'
    assert h['summary']['collected_including_tax']=='110.00' and h['summary']['outstanding_including_tax']=='0.00'
    assert h['summary']['average_days_to_full_payment']==12
    assert next(f for f in h['profitability']['families'] if f['original_call_id']==orders[0])['revenue']=='100.00'


def test_missing_callback_costs_and_invoice_adjustments_do_not_invent_profit(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];cid=customer_id(owner,orders[0])
    prepare_actual(setup_clock,orders[0],price=100,cost=50)
    exported=owner.post('/api/call-pricing/'+orders[0]+'/invoice/export',json={'number':'R'+uuid.uuid4().hex[:9],'expected_revision':1}).json()
    assert owner.post('/api/invoices/'+exported['id']+'/issue').status_code==200
    assert owner.put('/api/admin/calls/'+orders[1]+'/category',json={'job_type':'Warranty','original_call_id':orders[0],'reason':'Unreviewed warranty call'}).status_code==200
    family=owner.get('/api/admin/customers/'+cid+'/history').json()['profitability']['families'][0]
    assert family['callback_count']==1 and family['cost'] is None and family['gross_profit'] is None
    prepare_actual(setup_clock,orders[1],index=1,price=0,cost=20)
    with Session(m.engine) as db:
        db.add(m.InvoiceCredit(invoice_id=exported['id'],number='CR'+uuid.uuid4().hex[:9],amount=10,reason='Customer accommodation',credited_on=now[0].date(),created_by=owner.get('/api/auth/me').json()['id']));db.commit()
    h=owner.get('/api/admin/customers/'+cid+'/history').json()
    assert h['summary']['net_billed_including_tax']=='90.00'
    assert h['profitability']['families'][0]['gross_profit'] is None
    call=next(c for c in h['profitability']['calls'] if c['id']==orders[0]);assert call['adjustments_need_reconciliation']


def test_serviced_system_selector_is_assignment_and_customer_scoped(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];other=clients['other'];cid=customer_id(owner,orders[0])
    with Session(m.engine) as db:
        own=m.Location(customer_id=cid,name='Own site');foreign=m.Customer(name='Other customer '+uuid.uuid4().hex);db.add_all([own,foreign]);db.flush()
        elsewhere=m.Location(customer_id=foreign.id,name='Other site');db.add(elsewhere);db.flush()
        asset=m.Equipment(location_id=own.id,asset_tag='AHU-1');wrong=m.Equipment(location_id=elsewhere.id,asset_tag='AHU-1');db.add_all([asset,wrong]);db.commit();asset_id=asset.id;wrong_id=wrong.id
    path='/api/calls/'+orders[0]+'/service-systems'
    assert other.get(path).status_code==403
    assets=tech.get(path).json();assert asset_id in [a['id'] for a in assets] and wrong_id not in [a['id'] for a in assets]
    assert 'unit_cost' not in json.dumps(assets)
    assert tech.post(path+'/'+wrong_id).status_code==422
    assert tech.post(path+'/'+asset_id).status_code==200
    assert tech.post(path+'/'+asset_id).status_code==200  # retries do not inflate visit counts
    assert next(a for a in tech.get(path).json() if a['id']==asset_id)['linked']


def test_reported_parts_cost_review_and_billing_queue(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];wo=orders[0]
    prepare_actual(setup_clock,wo,price=100,cost=50)
    part=tech.post('/api/calls/'+wo+'/parts-used',json={'client_request_id':str(uuid.uuid4()),'description':'Unlisted repair part','quantity':2,'unit':'each'}).json()
    base='/api/call-pricing/'+wo
    missing=owner.post(base+'/preview',json=quote(mode='actual',parts=[],labor_sell_per_hour=100)).json()
    assert not missing['ready_to_export']
    data=quote(mode='actual',parts=[{'usage_id':part['id'],'description':'Unlisted repair part','quantity':2,'unit_cost':10,'unit_price':20}],labor_sell_per_hour=100,expected_revision=1)
    saved=owner.put(base+'/actual',json=data);assert saved.status_code==200,saved.text
    assert saved.json()['pricing']['total_direct_cost']=='70.00'
    assert tech.post('/api/calls/'+wo+'/notes',json={'client_request_id':str(uuid.uuid4()),'kind':'service_description','audience':'customer','text':'Completed service work'}).status_code==201
    assert tech.post('/api/calls/'+wo+'/finish',json={'parts_confirmed':True,'no_parts':False,'no_equipment_reason':'No equipment record created yet','no_mileage_reason':'No travel on this call'}).status_code==200
    assert owner.put('/api/admin/calls/'+wo+'/parts-review',json={'expected_pricing_revision':2}).status_code==200
    queue=owner.get('/api/admin/billing-queue').json()
    assert next(r for r in queue if r['work_order_id']==wo)['ready']
    assert clients['tech'].get('/api/admin/billing-queue').status_code==403


def test_running_clock_reduces_provisional_profit_without_changing_saved_quote(setup_clock,monkeypatch):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];wo=orders[0]
    monkeypatch.setattr(profit,'stamp',lambda:now[0])
    assert owner.put('/api/labor-rates',json={'technician_id':tech_id,'hourly_cost':30}).status_code==200
    data=quote(parts=[],labor_hours=1,labor_cost_per_hour=30,labor_sell_per_hour=100)
    assert owner.put('/api/call-pricing/'+wo+'/estimate',json=data).status_code==200
    assert tech.post('/api/time/clock/start',json={'work_order_id':wo}).status_code==201
    now[0]+=timedelta(hours=2)
    projected=owner.get('/api/admin/calls/'+wo+'/profitability').json()
    assert projected['projected_profit']=='40.00' and 'Provisional' in projected['projection_basis']
    assert projected['gross_profit'] is None and not projected['cost_complete']
    saved=owner.get('/api/call-pricing/'+wo+'/estimate').json()['pricing']
    assert saved['gross_profit']=='70.00' and saved['selling_subtotal']=='100.00'
