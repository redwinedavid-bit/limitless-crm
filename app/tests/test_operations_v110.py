import io
import json
import uuid
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session
from server import main as m,operations_v110 as ops,profit_tracking as profit,private_storage
from server.field_v14 import WorkPhoto
from server.call_field_notes import CallPartUsed
from test_time_clock import setup_clock
from test_profit_tracking import customer_id,prepare_actual
from test_call_pricing import quote


def linked_asset(setup):
 clients,orders,now,tech_id,job=setup;owner=clients['owner'];cid=customer_id(owner,orders[0])
 with Session(m.engine) as db:
  loc=m.Location(customer_id=cid,name='Test site');db.add(loc);db.flush();asset=m.Equipment(location_id=loc.id,asset_tag='RTU-'+uuid.uuid4().hex[:6]);db.add(asset);db.commit();asset_id=asset.id
 assert owner.post('/api/work-orders/'+orders[0]+'/equipment/'+asset_id).status_code==201
 return asset_id


def test_customer_multiple_named_sites_shared_billing_and_site_invoice(setup_clock):
 clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];cid=customer_id(owner,orders[0]);base='/api/admin/customers/'+cid
 assert tech.get(base+'/sites').status_code==403
 assert owner.put(base+'/billing',json={'billing_address':'100 Central Billing Road','contact':'Accounts Payable'}).status_code==200
 assert owner.put(base+'/billing',json={'billing_address':'wrong stale address'}).status_code==409
 sites=[]
 for name,address in [('Berea Station','200 Berea Service Road'),('Station 3','300 Third Station Road')]:
  r=owner.post(base+'/sites',json={'name':name,'address':address});assert r.status_code==201,r.text;sites.append(r.json()['id'])
 assert owner.post(base+'/sites',json={'name':'berea station','address':'different'}).status_code==409
 data=owner.get(base+'/sites').json();assert len(data['sites'])==2 and data['customer']['billing_address']=='100 Central Billing Road'
 jobs=[]
 for i,site in enumerate(sites):
  number='LMS'+str(int(uuid.uuid4().hex[:10],16));r=owner.post('/api/jobs',json={'job_number':number,'customer_id':cid,'location_id':site,'description':'Site repair'});assert r.status_code==201,r.text;jobs.append(number)
  invoice=owner.post('/api/invoices',json={'job_number':number,'number':'SITE'+uuid.uuid4().hex[:8],'amount':100}).json()
  html=owner.get('/api/invoices/'+invoice['id']+'/document').text
  assert '100 Central Billing Road' in html and ['200 Berea Service Road','300 Third Station Road'][i] in html
  assert ['Berea Station','Station 3'][i] in html
  assert owner.get('/api/invoices/'+invoice['id']+'/pdf').content.startswith(b'%PDF')
 with Session(m.engine) as db:assert db.get(m.Customer,cid).billing_address=='100 Central Billing Road'


def test_recommendation_conversion_private_scope_retry_and_margin_guard(setup_clock):
 clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];wo=orders[0]
 n=tech.post('/api/calls/'+wo+'/notes',json={'client_request_id':str(uuid.uuid4()),'kind':'recommendation','audience':'internal','text':'Internal supplier dispute do not disclose'}).json()
 path='/api/admin/calls/'+wo+'/service-quote';body={'request_id':str(uuid.uuid4()),'number':'REC'+uuid.uuid4().hex[:7],'title':'Replace failed component','source_note_id':n['id'],'estimated_cost':'100'}
 assert tech.post(path,json=body).status_code==403
 assert owner.post(path,json=body).status_code==422
 body['customer_scope']='Replace the failed component and verify operation.'
 result=owner.post(path,json=body);assert result.status_code==201,result.text
 assert owner.post(path,json=body).json()['id']==result.json()['id']
 public=owner.get('/api/estimates/advanced/'+result.json()['id']).json()
 assert public['total']==153.85 and 'supplier dispute' not in public['scope']
 assert Decimal(public['options'][0]['estimated_cost'])==0
 edit={k:public[k] for k in ['title','scope','inclusions','exclusions','warranty','payment_terms','project_timeline','options']};edit['expected_revision']=public['revision'];edit['options'][0]['price']='100'
 assert owner.put('/api/estimates/advanced/'+public['id'],json=edit).status_code==409


def test_additional_work_needs_confirmed_customer_approval_and_tech_redaction(setup_clock):
 clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];wo=orders[0]
 body={'request_id':str(uuid.uuid4()),'number':'ADD'+uuid.uuid4().hex[:7],'title':'Additional condenser repair','customer_scope':'Replace contactor and damaged wiring.','estimated_cost':65}
 created=owner.post('/api/admin/calls/'+wo+'/service-quote',json=body);assert created.status_code==201,created.text;pid=created.json()['id']
 assert owner.post('/api/proposals/'+pid+'/approve').status_code==409
 approval={'expected_revision':1,'accepted_by':'Site manager','reference':'Phone call 10:30 AM','method':'Phone'}
 assert owner.post('/api/admin/additional-work/'+pid+'/accept',json=approval).status_code==422
 assert owner.post('/api/admin/additional-work/'+pid+'/accept',json={**approval,'customer_authorization_confirmed':True}).status_code==200
 assert owner.post('/api/admin/additional-work/'+pid+'/accept',json={**approval,'customer_authorization_confirmed':True}).status_code==409
 visible=tech.get('/api/calls/'+wo+'/additional-work').json()[0]
 assert visible['status']=='Approved' and 'price' not in visible and 'pricing' not in visible
 assert owner.get('/api/invoices').status_code==200


def test_checklists_and_warranties_are_call_scoped_and_revision_checked(setup_clock):
 clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];other=clients['other'];wo=orders[0];asset=linked_asset(setup_clock)
 path='/api/calls/'+wo+'/checklists';answers={str(i):'Pass' for i in range(4)};body={'equipment_id':asset,'template':'heat_pump','answers':answers,'note':'Internal test observation'}
 assert other.get(path).status_code==403
 assert tech.put(path,json=body).status_code==200
 assert tech.put(path,json=body).status_code==409
 assert tech.put(path,json={**body,'equipment_id':'wrong'}).status_code==422
 assert 'Internal test observation' not in tech.get('/api/calls/'+wo+'/customer-summary').text
 warranty={'equipment_id':asset,'provider':'Manufacturer Example','starts_on':'2026-01-01','expires_on':'2027-01-01','coverage':'Parts only; labor excluded'}
 assert tech.post('/api/admin/warranties',json=warranty).status_code==403
 created=owner.post('/api/admin/warranties',json=warranty);assert created.status_code==201,created.text
 assert owner.put('/api/admin/warranties/'+created.json()['id'],json=warranty).status_code==409
 assert tech.get('/api/calls/'+wo+'/warranties').json()[0]['coverage']=='Parts only; labor excluded'
 assert other.get('/api/calls/'+wo+'/warranties').status_code==403


def test_receipt_matching_exact_total_replaces_cost_and_blocks_double_allocation(setup_clock):
 clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];wo=orders[0]
 prepare_actual(setup_clock,wo,price=100,cost=50)
 used=tech.post('/api/calls/'+wo+'/parts-used',json={'client_request_id':str(uuid.uuid4()),'description':'Small parts','quantity':3,'unit':'each'}).json()
 with Session(m.engine) as db:
  jobrow=db.scalar(select(m.Job).where(m.Job.job_number==job));owner_id=owner.get('/api/auth/me').json()['id'];po=m.PurchaseOrder(po_number='DR'+uuid.uuid4().hex[:8],job_id=jobrow.id,vendor='Supply House',description='Parts',amount=10,received_value=10,status='Received',source='manual');db.add(po);db.flush();receipt=m.POReceipt(po_id=po.id,reference='R1',amount=10,received_on=now[0].date(),recorded_by=owner_id);db.add(receipt);db.commit();rid=receipt.id
 path='/api/admin/calls/'+wo+'/parts/'+used['id']+'/receipt';body={'receipt_id':rid,'amount':'10.00','note':'Verified receipt total'}
 assert owner.put(path,json=body).status_code==200
 assert owner.put(path,json=body).status_code==409
 assert owner.put(path,json={**body,'expected_revision':1,'amount':11}).status_code==422
 priced=owner.put('/api/call-pricing/'+wo+'/actual',json=quote(mode='actual',parts=[{'usage_id':used['id'],'description':'Small parts','quantity':3,'unit_cost':1,'unit_price':10}],labor_sell_per_hour=100,expected_revision=1))
 assert priced.status_code==200,priced.text
 snapshot=priced.json()['pricing'];assert snapshot['parts_cost']=='10.00' and snapshot['total_direct_cost']=='60.00'
 assert snapshot['parts'][0]['cost_basis'].startswith('Allocated purchase receipts')
 matched=owner.get('/api/admin/calls/'+wo+'/reconciliation').json();assert matched['parts'][0]['status']=='Matched'
 used2=tech.post('/api/calls/'+wo+'/parts-used',json={'client_request_id':str(uuid.uuid4()),'description':'Other parts','quantity':1,'unit':'each'}).json()
 assert owner.put('/api/admin/calls/'+wo+'/parts/'+used2['id']+'/receipt',json={'receipt_id':rid,'amount':1,'note':'Try reuse'}).status_code==422


def test_payroll_export_uses_reviewed_hours_and_flags_unapproved_time(setup_clock):
 clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];wo=orders[0]
 entry=tech.post('/api/work-orders/'+wo+'/time',json={'started_at':now[0].isoformat(),'ended_at':(now[0]+timedelta(hours=3)).isoformat()}).json()
 week='2026-10-19';path='/api/admin/payroll?week_start='+week
 assert tech.get(path).status_code==403
 assert owner.get(path).json()['blockers']
 assert owner.get('/api/admin/payroll.csv?week_start='+week).status_code==409
 assert owner.put('/api/labor-rates',json={'technician_id':tech_id,'hourly_cost':30}).status_code==200
 assert owner.post('/api/time/'+entry['id']+'/approve',json={'overtime_hours':1}).status_code==201
 preview=owner.get(path).json();row=next(r for r in preview['rows'] if r['entry_id']==entry['id']);assert row['regular_hours']=='2.00' and row['overtime_hours']=='1.00'
 # Other tests may have unapproved entries in this shared database: verify the reviewed row,
 # then choose an isolated week for an independent export check below.
 assert owner.get('/api/admin/payroll?week_start=2026-10-20').status_code==422


def test_private_photo_upload_download_and_gcs_fail_closed(setup_clock,tmp_path,monkeypatch):
 clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];other=clients['other'];wo=orders[0]
 from server import field_v14
 monkeypatch.setattr(field_v14,'UPLOAD_DIR',tmp_path)
 output=io.BytesIO();Image.new('RGB',(8,8),'teal').save(output,format='PNG')
 uploaded=tech.post('/api/field/work-orders/'+wo+'/photos',files={'file':('nameplate.png',output.getvalue(),'image/png')},data={'caption':'Nameplate'})
 assert uploaded.status_code==201,uploaded.text
 path=uploaded.json()['download_url'];assert other.get(path).status_code==403
 result=tech.get(path);assert result.status_code==200 and result.content==output.getvalue()
 assert result.headers['cache-control']=='private, no-store'
 monkeypatch.setenv('LMS_PRIVATE_UPLOAD_BUCKET','configured-bucket');monkeypatch.delenv('LMS_DURABLE_UPLOADS',raising=False)
 blocked=tech.post('/api/field/work-orders/'+wo+'/photos',files={'file':('x.png',output.getvalue(),'image/png')})
 assert blocked.status_code==503
 assert not private_storage.durable_configured()


def test_notifications_are_private_and_snooze_does_not_resolve_work(setup_clock,monkeypatch):
 clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];cid=customer_id(owner,orders[0])
 f=owner.post('/api/admin/customers/'+cid+'/follow-ups',json={'text':'Review quote with site manager','due_on':'2020-01-01'});assert f.status_code==201
 assert tech.get('/api/admin/notifications').status_code==403
 items=owner.get('/api/admin/notifications').json()['items'];item=next(i for i in items if i['key'].startswith('follow:'+f.json()['id']))
 assert owner.put('/api/admin/notifications/'+item['key']+'/snooze',json={'hours':24}).status_code==200
 assert item['key'] not in [i['key'] for i in owner.get('/api/admin/notifications').json()['items']]
 assert owner.get('/api/admin/customers/'+cid+'/history').json()['follow_ups'][0]['status']=='Open'


def test_gcs_storage_adapter_requires_private_bucket_and_never_uses_public_urls(monkeypatch,tmp_path):
 from fastapi import HTTPException
 import pytest
 from types import SimpleNamespace
 from google.cloud import storage
 objects={};writes=[]
 class Blob:
  def __init__(self,key):self.key=key
  def upload_from_string(self,data,**kw):
   assert kw['if_generation_match']==0
   writes.append(self.key);objects[self.key]=data
  def download_as_bytes(self,**kw):return objects[self.key]
 class Bucket:
  iam_configuration=SimpleNamespace(uniform_bucket_level_access_enabled=True,public_access_prevention='inherited')
  def reload(self,**kw):pass
  def blob(self,key):return Blob(key)
 b=Bucket();monkeypatch.setattr(storage,'Client',lambda:SimpleNamespace(bucket=lambda name:b))
 monkeypatch.setenv('LMS_DURABLE_UPLOADS','1');monkeypatch.setenv('LMS_PRIVATE_UPLOAD_BUCKET','test-private-bucket')
 with pytest.raises(HTTPException) as exc:private_storage.put(tmp_path,uuid.uuid4().hex,b'private','image/png')
 assert exc.value.status_code==503 and writes==[]
 b.iam_configuration.public_access_prevention='enforced'
 key=private_storage.put(tmp_path,uuid.uuid4().hex,b'private','image/png')
 assert key.startswith('gcs/field/') and private_storage.read(tmp_path,key)==b'private'
 assert list(tmp_path.iterdir())==[]


def test_payroll_is_downloadable_after_review_in_an_isolated_week(setup_clock):
 clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];wo=orders[0]
 # A unique distant week avoids unrelated fixture time in this shared database.
 start=datetime(2031,3,3,12,tzinfo=timezone.utc)
 entry=tech.post('/api/work-orders/'+wo+'/time',json={'started_at':start.isoformat(),'ended_at':(start+timedelta(hours=2)).isoformat()})
 assert entry.status_code==201,entry.text
 owner.put('/api/labor-rates',json={'technician_id':tech_id,'hourly_cost':30})
 assert owner.post('/api/time/'+entry.json()['id']+'/approve',json={}).status_code==201
 result=owner.get('/api/admin/payroll.csv?week_start=2031-03-03')
 assert result.status_code==200,result.text
 assert 'regular_hours' in result.text and job in result.text and '2.00' in result.text


def test_three_individual_logins_admin_finances_technician_assignment_and_disable(setup_clock):
 from fastapi.testclient import TestClient
 clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];suffix=uuid.uuid4().hex[:8];password='individual-test-password-123'
 assert clients['tech'].get('/api/admin/team').status_code==403
 new={}
 for role in ('admin','technician'):
  body={'username':role+suffix,'display_name':role.title()+' Tester','role':role,'google_email':role+suffix+'@example.com','password':password}
  result=owner.post('/api/admin/team',json=body);assert result.status_code==201,result.text
  assert 'password' not in result.text
  assert owner.post('/api/admin/team',json=body).status_code==409
  client=TestClient(m.app);assert client.post('/api/auth/login',json={'username':body['username'],'password':password}).status_code==200
  new[role]=(client,result.json()['id'])
 admin,aid=new['admin'];technician,tid=new['technician']
 assert admin.get('/api/parts-catalog').status_code==200 and admin.get('/api/admin/profitability').status_code==200
 assert technician.get('/api/parts-catalog').status_code==403 and technician.get('/api/admin/team').status_code==403
 assert technician.get('/api/calls/'+orders[0]+'/workspace').status_code==403
 assert admin.post('/api/work-orders/'+orders[0]+'/assignments',json={'technician_id':tid,'scheduled_start':now[0].isoformat(),'scheduled_end':(now[0]+timedelta(hours=8)).isoformat()}).status_code==201
 assert technician.get('/api/calls/'+orders[0]+'/workspace').status_code==200
 assert owner.put('/api/admin/team/'+tid+'/active',json={'active':False,'expected_revision':1}).status_code==200
 assert technician.get('/api/auth/me').status_code==401
 assert owner.put('/api/admin/team/'+aid+'/active',json={'active':False,'expected_revision':0}).status_code==409
