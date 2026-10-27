import uuid
from decimal import Decimal
from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from server import main as m
from server.security import User, password_hash
from test_time_clock import setup_clock


def quote(**changes):
    return {'mode':'estimate','labor_hours':'0','labor_cost_per_hour':'0','labor_sell_per_hour':'0',
            'miles':'0','mileage_cost_per_mile':'0','mileage_sell_per_mile':'0','target_margin':'35',
            'parts':[{'description':'Sample part','quantity':'1','unit_cost':'100','unit_price':'100'}],**changes}


def test_owner_only_and_margin_slider_math(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];base='/api/call-pricing/'+orders[0]
    assert tech.get('/call-pricing').status_code==403
    assert tech.get('/api/parts-catalog').status_code==403
    assert tech.post(base+'/preview',json=quote()).status_code==403
    assert TestClient(m.app).get('/api/parts-catalog').status_code==401
    # Financial permissions alone are insufficient: only the owner/admin can use pricing.
    username='office'+uuid.uuid4().hex
    with Session(m.engine) as db:
        db.add(User(username=username,display_name='Office employee',role='office',password_hash=password_hash('test-password-123')));db.commit()
    staff=TestClient(m.app);staff.post('/api/auth/login',json={'username':username,'password':'test-password-123'})
    assert staff.get('/api/parts-catalog').status_code==403
    assert staff.get('/call-pricing').status_code==403
    for target,expected in [('35','153.85'),('36','156.25'),('37','158.74'),('40','166.67'),('45','181.82'),('35','153.85')]:
        result=owner.post(base+'/preview',json=quote(target_margin=target))
        assert result.status_code==200,result.text
        p=result.json()
        assert p['selling_subtotal']==expected and p['minimum_selling_subtotal']==expected
        assert p['ready_to_export'] and Decimal(p['gross_margin_percent'])>=Decimal(target)
        assert p['lines'][-1]['description']=='Service call charge'
    assert owner.post(base+'/preview',json=quote(target_margin='34')).status_code==422
    assert owner.post(base+'/preview',json=quote(target_margin='35.5')).status_code==422
    manual=owner.post(base+'/preview',json=quote(auto_target=False)).json()
    assert manual['below_target'] and manual['shortfall']=='53.85' and not manual['ready_to_export']
    missing=owner.post(base+'/preview',json=quote(labor_hours='1',labor_cost_per_hour='0')).json()
    assert not missing['ready_to_export'] and 'internal hourly labor cost' in ' '.join(missing['blockers'])


def test_catalog_revisions_snapshot_and_quote_guard(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];base='/api/call-pricing/'+orders[0]
    part={'sku':' filter-'+uuid.uuid4().hex[:8]+' ','description':'Filter','unit':'each','supplier':'Sample Supply','unit_cost':'65'}
    created=owner.post('/api/parts-catalog',json=part)
    assert created.status_code==201,created.text
    p=created.json();assert p['unit_price']=='100.00' and p['minimum_price_35']=='100.00'
    assert owner.post('/api/parts-catalog',json={**part,'sku':p['sku'].lower()}).status_code==409
    data=quote(parts=[{'part_id':p['id'],'catalog_revision':1,'quantity':2,'unit_price':'100'}])
    saved=owner.put(base+'/estimate',json=data)
    assert saved.status_code==200,saved.text
    assert saved.json()['pricing']['total_direct_cost']=='130.00'
    changed=owner.put('/api/parts-catalog/'+p['id'],json={**part,'unit_cost':'80','expected_revision':1})
    assert changed.status_code==200
    assert owner.put('/api/parts-catalog/'+p['id'],json={**part,'expected_revision':1}).status_code==409
    assert owner.get(base+'/estimate').json()['pricing']['parts'][0]['unit_cost']=='65.00'
    assert owner.post(base+'/preview',json=data).status_code==409
    exported=owner.post(base+'/proposal/export',json={'number':'PROP'+uuid.uuid4().hex[:8],'expected_revision':1})
    assert exported.status_code==200,exported.text
    proposal_id=exported.json()['id']
    public=owner.get('/api/estimates/advanced/'+proposal_id).json()
    assert public['total']==200 and public['options'][0]['estimated_cost']=='0'
    edited={k:public[k] for k in ['title','scope','inclusions','exclusions','warranty','payment_terms','project_timeline','options']}
    edited['expected_revision']=public['revision'];edited['options'][0]['price']='100'
    assert owner.put('/api/estimates/advanced/'+proposal_id,json=edited).status_code==409
    assert owner.put(base+'/estimate',json=data).status_code==409
    archived=owner.put('/api/parts-catalog/'+p['id'],json={**part,'unit_cost':'80','active':False,'expected_revision':2})
    assert archived.status_code==200
    assert owner.post(base+'/preview',json={**data,'parts':[{**data['parts'][0],'catalog_revision':3}]}).status_code==422


def test_actual_costs_invoice_and_duplicate_mileage_protection(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];base='/api/call-pricing/'+orders[0]
    data=quote(mode='actual',parts=[{'description':'Capacitor','quantity':1,'unit_cost':40,'unit_price':100}],labor_hours=999,labor_cost_per_hour=999,labor_sell_per_hour=100,miles=999,mileage_cost_per_mile=999,mileage_sell_per_mile=1)
    assert not owner.post(base+'/preview',json=data).json()['ready_to_export']
    time=tech.post('/api/work-orders/'+orders[0]+'/time',json={'started_at':now[0].isoformat(),'ended_at':(now[0]+timedelta(hours=2)).isoformat()}).json()
    owner.put('/api/labor-rates',json={'technician_id':tech_id,'hourly_cost':30,'mileage_rate':'.5'})
    owner.post('/api/time/'+time['id']+'/approve',json={})
    path='/api/work-orders/'+orders[0]+'/mileage'
    tech.put(path,json={'start_odometer':10000,'end_odometer':10010,'expected_revision':0})
    assert owner.post(base+'/preview',json=data).json()['ready_to_export']  # mileage applies without approval
    saved=owner.put(base+'/actual',json=data)
    assert saved.status_code==200,saved.text
    pricing=saved.json()['pricing']
    assert pricing['labor_hours']=='2.00' and pricing['miles']=='10.00'
    assert pricing['total_direct_cost']=='105.00' and pricing['selling_subtotal']=='310.00'
    number='INV'+uuid.uuid4().hex[:8];export={'number':number,'expected_revision':1,'tax_rate':10}
    r=owner.post(base+'/invoice/export',json=export)
    assert r.status_code==200,r.text
    invoice_id=r.json()['id']
    assert owner.post(base+'/invoice/export',json=export).json()['id']==invoice_id
    invoices=owner.get('/api/invoices').json();inv=next(i for i in invoices if i['id']==invoice_id)
    assert inv['amount']==341
    assert [x['subtotal'] for x in inv['lines'] if x['description']=='Materials']==[110]
    assert not any('mileage' in x['description'].lower() for x in inv['lines'])
    assert tech.get(path).json()[0]['invoiced']
    assert owner.post('/api/invoices/'+invoice_id+'/call-mileage',json={'work_order_id':orders[0],'unit_price':1}).status_code==409
    assert owner.post('/api/invoices/'+invoice_id+'/issue').status_code==200
    assert owner.put(base+'/actual',json={**data,'expected_revision':1,'target_margin':40}).status_code==200
    assert owner.post(base+'/invoice/export',json={**export,'expected_revision':2}).status_code==409


def test_discount_bypass_and_cost_change_require_review(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];base='/api/call-pricing/'+orders[0]
    entry=tech.post('/api/work-orders/'+orders[0]+'/time',json={'started_at':now[0].isoformat(),'ended_at':(now[0]+timedelta(hours=1)).isoformat()}).json()
    owner.put('/api/labor-rates',json={'technician_id':tech_id,'hourly_cost':65,'mileage_rate':0})
    owner.post('/api/time/'+entry['id']+'/approve',json={})
    path='/api/work-orders/'+orders[0]+'/mileage';tech.put(path,json={'start_odometer':10,'end_odometer':10,'expected_revision':0});owner.post(path+'/'+tech_id+'/approve',json={'expected_revision':1})
    data=quote(mode='actual',parts=[],labor_sell_per_hour=100)
    saved=owner.put(base+'/actual',json=data)
    assert saved.status_code==200
    number='DISC'+uuid.uuid4().hex[:8]
    exported=owner.post(base+'/invoice/export',json={'number':number,'expected_revision':1})
    assert exported.status_code==200,exported.text
    inv_id=exported.json()['id']
    assert owner.put('/api/invoices/'+inv_id+'/lines',json={'lines':[{'description':'Discounted call','quantity':1,'unit_price':90}]}).status_code==200
    assert owner.post('/api/invoices/'+inv_id+'/issue').status_code==409
    # Refresh the same draft; do not create a second invoice for this call.
    owner.put(base+'/actual',json={**data,'expected_revision':1,'labor_sell_per_hour':110})
    refreshed=owner.post(base+'/invoice/export',json={'number':number,'expected_revision':2})
    assert refreshed.status_code==200 and refreshed.json()['id']==inv_id
    more=tech.post('/api/work-orders/'+orders[0]+'/time',json={'started_at':(now[0]+timedelta(hours=2)).isoformat(),'ended_at':(now[0]+timedelta(hours=3)).isoformat()}).json()
    assert owner.post('/api/invoices/'+inv_id+'/issue').status_code==409
    owner.post('/api/time/'+more['id']+'/approve',json={})
    assert owner.post('/api/invoices/'+inv_id+'/issue').status_code==409
    assert owner.put(base+'/actual',json={**data,'expected_revision':2,'labor_sell_per_hour':110}).status_code==200
    assert owner.post(base+'/invoice/export',json={'number':number,'expected_revision':3}).status_code==200
    assert owner.post('/api/invoices/'+inv_id+'/issue').status_code==200

