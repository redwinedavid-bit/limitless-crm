"""Internal pilot protections: real provider traffic is always mocked."""
import io,uuid,json
from datetime import date,timedelta
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session
from sqlalchemy import select
from fastapi.testclient import TestClient
from PIL import Image
from test_time_clock import setup_clock
from server import main as m,billing_v111 as b,field_v111 as f

@pytest.fixture
def pilot(setup_clock):
 clients,orders,now,tid,job=setup_clock;o=clients['owner']
 old=o.get('/api/admin/billing/settings').json()
 cfg=o.put('/api/admin/billing/settings',json={**old['values'],'test_mode':True,'expected_revision':old['revision']});assert cfg.status_code==200
 yield setup_clock
 cfg=o.get('/api/admin/billing/settings').json();o.put('/api/admin/billing/settings',json={**old['values'],'expected_revision':cfg['revision']})

def invoice(o,job,amount='100.00'):
 r=o.post('/api/invoices',json={'job_number':job,'number':'PILOT-'+uuid.uuid4().hex[:12],'amount':amount});assert r.status_code==201,r.text
 inv=r.json();assert o.post('/api/invoices/'+inv['id']+'/issue').status_code==200;return inv

def mail(inv):return {'request_id':str(uuid.uuid4()),'recipient':'test@example.com','subject':'Internal invoice','message':'Test attachment','confirm_resend':False}

def test_preview_outbox_idempotent_and_financial_access(pilot,monkeypatch):
 clients,orders,now,tid,job=pilot;o=clients['owner'];inv=invoice(o,job)
 def forbidden(*a,**kw):raise AssertionError('Provider must not be called')
 monkeypatch.setattr(m,'gmail_send',forbidden)
 body=mail(inv);path='/api/admin/invoices/'+inv['id']+'/email'
 r=o.post(path,json=body);assert r.status_code==200,r.text;assert r.json()['status']=='Preview'
 assert o.post(path,json=body).json()['id']==r.json()['id']
 assert o.post(path,json={**body,'request_id':str(uuid.uuid4())}).json()['id']==r.json()['id']
 assert o.post(path,json={**body,'message':'Changed'}).status_code==409
 assert o.post('/api/admin/mail-outbox/'+r.json()['id']+'/process',json={}).json()['status']=='Preview'
 assert clients['tech'].get('/billing-desk').status_code==403
 assert clients['tech'].get('/api/admin/mail-outbox').status_code==403
 assert o.get('/api/admin/mail-outbox/'+r.json()['id']+'/preview').content.startswith(b'%PDF')

def test_uncertain_send_requires_manual_review(pilot,monkeypatch):
 clients,orders,now,tid,job=pilot;o=clients['owner'];cfg=o.get('/api/admin/billing/settings').json()
 assert o.put('/api/admin/billing/settings',json={**cfg['values'],'test_mode':False,'remit_address':'Office test address','expected_revision':cfg['revision']}).status_code==200
 monkeypatch.setenv('LMS_ALLOW_EXTERNAL_EMAIL','1');monkeypatch.setenv('LMS_GMAIL_SENDER',b.SENDER);monkeypatch.setattr(m,'gmail_configured',lambda:True)
 attempts=[]
 def uncertain(*args,**kw):attempts.append(1);raise TimeoutError('Uncertain provider response')
 monkeypatch.setattr(m,'gmail_send',uncertain)
 inv=invoice(o,job);path='/api/admin/invoices/'+inv['id']+'/email';body=mail(inv);r=o.post(path,json=body).json()
 process='/api/admin/mail-outbox/'+r['id']+'/process';assert o.post(process,json={}).json()['status']=='Unconfirmed'
 assert o.post(process,json={}).json()['status']=='Unconfirmed';assert len(attempts)==1
 assert o.post(path,json={**body,'request_id':str(uuid.uuid4()),'confirm_resend':True}).status_code==409
 resolve='/api/admin/mail-outbox/'+r['id']+'/resolve'
 assert o.post(resolve,json={'action':'Confirmed not sent','reason':'Verified test mailbox sent folder'}).status_code==200
 assert o.post(path,json={**body,'request_id':str(uuid.uuid4())}).json()['status']=='Queued'

def test_check_allocations_partial_unapplied_return_and_export(pilot):
 clients,orders,now,tid,job=pilot;o=clients['owner'];inv1=invoice(o,job);inv2=invoice(o,job)
 with Session(m.engine) as db:cid=db.scalar(select(m.Job).where(m.Job.job_number==job)).customer_id
 r=o.post('/api/admin/checks',json={'customer_id':cid,'check_number':uuid.uuid4().hex,'amount':'250','received_on':str(date.today())});assert r.status_code==201,r.text
 check=r.json();path='/api/admin/checks/'+check['id'];body={'request_id':str(uuid.uuid4()),'invoice_id':inv1['id'],'amount':'100'}
 a=o.post(path+'/allocate',json=body);assert a.status_code==200,a.text;assert Decimal(a.json()['unapplied'])==150
 assert o.post(path+'/allocate',json=body).json()['revision']==a.json()['revision']
 assert o.post(path+'/allocate',json={**body,'request_id':str(uuid.uuid4()),'invoice_id':inv2['id'],'amount':'101'}).status_code==422
 a=o.post(path+'/allocate',json={**body,'request_id':str(uuid.uuid4()),'invoice_id':inv2['id']});assert a.status_code==200;assert Decimal(a.json()['unapplied'])==50
 returned=o.post(path+'/state',json={'status':'Returned','action_on':str(date.today()),'expected_revision':a.json()['revision'],'reason':'Bank returned the check'});assert returned.status_code==200,returned.text
 assert returned.json()['unapplied']=='0.00'
 with Session(m.engine) as db:
  assert m.invoice_ledger(db,db.get(m.InvoiceRecord,inv1['id']))['balance']==100
  from server.accountant_export import export_data
  data=export_data(db,date.today().year);assert next(x for x in data['files']['returned_checks.csv'] if x['check_id']==check['id'])['amount']=='250.00'
 assert o.post(path+'/allocate',json={**body,'request_id':str(uuid.uuid4())}).status_code==409

def test_commercial_details_frozen_and_sequence(pilot):
 clients,orders,now,tid,job=pilot;o=clients['owner'];nums=[o.post('/api/admin/invoices/next-number').json()['number'] for _ in range(2)];assert nums[0]!=nums[1]
 inv=o.post('/api/invoices',json={'job_number':job,'number':nums[0],'amount':'100'}).json();path='/api/admin/invoices/'+inv['id']+'/details'
 assert o.put(path,json={'customer_po':'PO-123','service_from':str(date.today()),'expected_revision':0}).status_code==200
 assert o.post('/api/invoices/'+inv['id']+'/issue').status_code==200
 assert o.put(path,json={'customer_po':'Changed','expected_revision':1}).status_code==409
 with Session(m.engine) as db:assert json.loads(db.get(b.InvoiceSnapshot,inv['id']).json)['commercial']['customer_po']=='PO-123'

def test_finish_call_and_waiting_states(pilot):
 clients,orders,now,tid,job=pilot;o=clients['owner'];t=clients['tech'];wo=orders[0];path='/api/calls/'+wo
 assert o.patch('/api/work-orders/'+wo+'/status',json={'status':'Waiting for Parts'}).status_code==200
 assert o.patch('/api/work-orders/'+wo+'/status',json={'status':'Completed'}).status_code==409
 body={'parts_confirmed':True,'no_parts':True,'no_equipment_reason':'Equipment record pending','no_mileage_reason':'No vehicle travel required'}
 assert t.post(path+'/finish',json=body).status_code==422
 assert t.post(path+'/notes',json={'client_request_id':str(uuid.uuid4()),'kind':'service_description','audience':'customer','text':'Inspected and verified operation'}).status_code==201
 r=t.post(path+'/finish',json=body);assert r.status_code==200,r.text;assert r.json()['status']=='Completed'
 assert t.post(path+'/finish',json=body).status_code==409

def test_offline_time_idempotency_overlap_and_permission(pilot,monkeypatch):
 clients,orders,now,tid,job=pilot;t=clients['tech'];path='/api/calls/'+orders[0]+'/offline-time';monkeypatch.setattr(f,'clock_now',lambda:now[0])
 body={'request_id':str(uuid.uuid4()),'started_at':(now[0]-timedelta(hours=2)).isoformat(),'ended_at':(now[0]-timedelta(hours=1)).isoformat()}
 r=t.post(path,json=body);assert r.status_code==201,r.text
 assert t.post(path,json=body).json()['id']==r.json()['id']
 assert t.post(path,json={**body,'request_id':str(uuid.uuid4())}).status_code==409
 assert clients['other'].post(path,json={**body,'request_id':str(uuid.uuid4())}).status_code==403
 assert t.post(path,json={**body,'request_id':str(uuid.uuid4()),'ended_at':(now[0]+timedelta(hours=1)).isoformat()}).status_code==422
 with Session(m.engine) as db:assert db.get(m.TimeApproval,r.json()['id']) is None

def test_photo_offline_idempotency_and_equipment_document_scope(pilot,tmp_path,monkeypatch):
 from server import field_v14
 monkeypatch.setattr(field_v14,'UPLOAD_DIR',tmp_path)
 clients,orders,now,tid,job=pilot;t=clients['tech'];o=clients['owner'];wo=orders[0];buf=io.BytesIO();Image.new('RGB',(8,8),'teal').save(buf,format='PNG');data={'request_id':str(uuid.uuid4()),'caption':'Test equipment'};files={'file':('photo.png',buf.getvalue(),'image/png')};path='/api/calls/'+wo+'/offline-photo'
 r=t.post(path,data=data,files=files);assert r.status_code==201,r.text;assert t.post(path,data=data,files=files).json()['id']==r.json()['id']
 assert clients['other'].post(path,data=data,files=files).status_code==403
 assert len(list(tmp_path.iterdir()))==1


def test_request_link_customer_scope_revoke_and_internal_preview(pilot):
 clients,orders,now,tid,job=pilot;o=clients['owner']
 with Session(m.engine) as db:cid=db.scalar(select(m.Job).where(m.Job.job_number==job)).customer_id
 site=o.post('/api/admin/customers/'+cid+'/sites',json={'name':'Named site','address':'100 Test Rd'});assert site.status_code==201,site.text
 r=o.post('/api/admin/customer-links',json={'kind':'Service request','customer_id':cid});assert r.status_code==200,r.text;g=r.json();assert g['internal_preview'] and g['url'].startswith('/customer-preview#');token=g['url'].split('#')[1]
 view=o.post('/api/admin/customer-preview/resolve',json={'token':token});assert view.status_code==200;assert view.json()['locations'][0]['id']==site.json()['id']
 body={'token':token,'request_id':str(uuid.uuid4()),'location_id':site.json()['id'],'name':'Test Requester','description':'Cooling system needs service'}
 a=o.post('/api/admin/customer-preview/request',json=body);assert a.status_code==200,a.text;assert o.post('/api/admin/customer-preview/request',json=body).json()['id']==a.json()['id']
 assert o.post('/api/admin/customer-preview/request',json={**body,'location_id':'other-site'}).status_code==422
 assert o.post('/api/admin/customer-links/revoke/'+g['id']).status_code==200
 assert o.post('/api/admin/customer-preview/resolve',json={'token':token}).status_code==404
 from server.customer_portal import app
 assert TestClient(app).post('/api/customer/resolve',json={'token':token}).status_code==404


def test_wave_import_validation_and_no_new_sales(pilot):
 clients,orders,now,tid,job=pilot;o=clients['owner'];row={'source_reference':'wave-'+uuid.uuid4().hex,'invoice_number':'WAVE-'+uuid.uuid4().hex[:12],'job_number':job,'outstanding_amount':'75.00','issued_on':str(date.today()),'due_on':str(date.today())};path='/api/admin/wave-opening-balances'
 assert o.post(path,json={'rows':[row]}).json()=={'valid_rows':1,'errors':[],'committed':False}
 assert o.post(path,json={'rows':[row,{**row,'source_reference':uuid.uuid4().hex}],'commit':True}).json()['errors']
 assert o.post(path,json={'rows':[row],'commit':True}).json()['committed']
 assert o.post(path,json={'rows':[row],'commit':True}).json()['errors']
 with Session(m.engine) as db:
  from server.accountant_export import export_data
  files=export_data(db,date.today().year)['files'];assert any(x['invoice_number']==row['invoice_number'] for x in files['wave_opening_balances_REFERENCE_ONLY.csv']);assert not any(x['invoice_number']==row['invoice_number'] for x in files['sales_invoices.csv'])

def test_new_office_screens_admin_only_and_templates(pilot):
 clients,orders,now,tid,job=pilot;o=clients['owner']
 for path in ['/service-office','/billing-desk','/customer-preview']:
  assert o.get(path).status_code==200;assert clients['tech'].get(path).status_code==403
 body={'name':'Cooling inspection '+uuid.uuid4().hex,'interval_days':90,'items':['Inspect connections','Record temperatures'],'estimated_hours':'1.5'}
 r=o.post('/api/admin/pm/visit-templates',json=body);assert r.status_code==201,r.text
 assert o.post('/api/admin/pm/visit-templates',json=body).status_code==409
 assert o.post('/api/admin/pm/visit-templates',json={**body,'name':uuid.uuid4().hex,'estimated_hours':'NaN'}).status_code==422
 assert o.get('/api/admin/pm/attention').status_code==200

def test_scoped_quote_approval_hides_costs_and_rejects_stale_link(pilot):
 from test_v15 import quote_body
 clients,orders,now,tid,job=pilot;o=clients['owner'];tag=uuid.uuid4().hex[:10];body=quote_body(job,tag);body['options']=[{'name':'Repair','description':'Customer repair scope','price':200,'estimated_cost':100}]
 q=o.post('/api/estimates/advanced',json=body);assert q.status_code==201,q.text;q=q.json()
 g=o.post('/api/admin/customer-links',json={'kind':'Quote','proposal_id':q['id']}).json();token=g['url'].split('#')[1]
 view=o.post('/api/admin/customer-preview/resolve',json={'token':token});assert view.status_code==200 and 'estimated_cost' not in view.text
 edit={k:v for k,v in body.items() if k not in ('job_number','number')};edit['expected_revision']=1;edit['scope']='Revised customer scope'
 assert o.put('/api/estimates/advanced/'+q['id'],json=edit).status_code==200
 assert o.post('/api/admin/customer-preview/accept',json={'token':token,'name':'Test Customer','selected_option':0,'consent':True}).status_code==409
 fresh=o.post('/api/admin/customer-links',json={'kind':'Quote','proposal_id':q['id']}).json()['url'].split('#')[1];accept={'token':fresh,'name':'Test Customer','selected_option':0,'consent':True}
 result=o.post('/api/admin/customer-preview/accept',json=accept);assert result.status_code==200,result.text
 assert o.post('/api/admin/customer-preview/accept',json=accept).json()['already_accepted']
 assert 'estimated_cost' not in o.post('/api/admin/customer-preview/resolve',json={'token':fresh}).text


def test_recovered_server_clockout_uses_captured_time(pilot):
 clients,orders,now,tid,job=pilot;t=clients['tech'];r=t.post('/api/time/clock/start',json={'work_order_id':orders[0]});assert r.status_code==201
 clock=r.json()['active'];captured=now[0]+timedelta(hours=1);now[0]+=timedelta(hours=2)
 stop=t.post('/api/time/clock/stop',json={'clock_id':clock['id'],'captured_stop_at':captured.isoformat()});assert stop.status_code==200,stop.text;assert stop.json()['entry']['hours']==1

def test_paid_invoice_reminders_are_not_delivered(pilot,monkeypatch):
 clients,orders,now,tid,job=pilot;o=clients['owner'];cfg=o.get('/api/admin/billing/settings').json()
 assert o.put('/api/admin/billing/settings',json={**cfg['values'],'test_mode':False,'remit_address':'Office address','expected_revision':cfg['revision']}).status_code==200
 inv=invoice(o,job);body=mail(inv)
 with Session(m.engine) as db:
  who={'id':db.scalar(select(m.User.id).where(m.User.role=='owner'))};r=b.enqueue(db,db.get(m.InvoiceRecord,inv['id']),who,uuid.uuid4(),body['recipient'],body['subject'],body['message'],purpose='Reminder7');db.commit();rid=r.id
 assert o.post('/api/invoices/'+inv['id']+'/payments',json={'amount':100,'paid_on':str(date.today()),'reference':uuid.uuid4().hex}).status_code==201
 monkeypatch.setenv('LMS_ALLOW_EXTERNAL_EMAIL','1');monkeypatch.setenv('LMS_GMAIL_SENDER',b.SENDER);monkeypatch.setattr(m,'gmail_configured',lambda:True)
 calls=[];monkeypatch.setattr(m,'gmail_send',lambda *a,**kw:calls.append(1))
 result=o.post('/api/admin/mail-outbox/'+rid+'/process',json={});assert result.json()['status']=='Cancelled';assert not calls

def test_equipment_manual_private_and_limited_to_linked_calls(pilot,tmp_path,monkeypatch):
 from server import field_v14
 monkeypatch.setattr(field_v14,'UPLOAD_DIR',tmp_path)
 clients,orders,now,tid,job=pilot;o=clients['owner'];t=clients['tech']
 with Session(m.engine) as db:cid=db.scalar(select(m.Job).where(m.Job.job_number==job)).customer_id
 site=o.post('/api/admin/customers/'+cid+'/sites',json={'name':'Manual site','address':'10 Test Rd'}).json()
 asset=o.post('/api/equipment',json={'location_id':site['id'],'asset_tag':'Unit-1','model':'Test'});assert asset.status_code==201,asset.text;asset=asset.json()
 assert o.post('/api/work-orders/'+orders[0]+'/equipment/'+asset['id']).status_code==201
 payload=b'%PDF-1.4\n% Test manual fixture\n';upload=o.post('/api/admin/equipment/'+asset['id']+'/documents',data={'title':'Wiring manual'},files={'file':('manual.pdf',payload,'application/pdf')});assert upload.status_code==201,upload.text
 path='/api/calls/'+orders[0]+'/equipment-documents/'+upload.json()['id'];assert t.get(path).content==payload
 assert clients['other'].get(path).status_code==403
 assert t.get('/api/calls/'+orders[1]+'/equipment-documents/'+upload.json()['id']).status_code==404
