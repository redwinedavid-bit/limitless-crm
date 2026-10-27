"""Reopen unsent, unpaid invoices without losing their original record."""
import json,uuid
from datetime import timedelta,date
from decimal import Decimal
import pytest
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from test_time_clock import setup_clock
from server import main as m,billing_v111 as b
from server.call_pricing import CallPricingExport
from server.purchasing_v13 import POInvoiceCost


def issued(owner,job):
 r=owner.post('/api/invoices',json={'job_number':job,'number':'REOPEN-'+uuid.uuid4().hex,
     'lines':[{'description':'Labor','quantity':1,'unit_price':100}]})
 assert r.status_code==201,r.text
 inv=r.json()
 assert owner.post('/api/invoices/'+inv['id']+'/issue').status_code==200
 return inv


def request(owner,inv,**values):
 meta=owner.get('/api/admin/invoices/'+inv['id']+'/details').json()
 return owner.post('/api/admin/invoices/'+inv['id']+'/reopen',
    json={'expected_revision':meta['revision'],'reason':'Include missing purchase orders',**values})


def test_reopen_preserves_number_original_pdf_and_reissue(setup_clock):
 clients,orders,now,tid,job=setup_clock;o=clients['owner'];inv=issued(o,job);iid=inv['id']
 cfg=o.get('/api/admin/billing/settings').json()
 assert o.put('/api/admin/billing/settings',json={**cfg['values'],'test_mode':True,'expected_revision':cfg['revision']}).status_code==200
 preview=o.post('/api/admin/invoices/'+iid+'/email',json={'request_id':str(uuid.uuid4()),
     'recipient':'test@example.com','subject':'Original preview','message':'Original invoice'}).json()
 path='/api/admin/invoices/'+iid
 meta=o.get(path+'/details').json()
 assert meta['reopen_blockers']==[]
 r=request(o,inv);assert r.status_code==200,r.text
 assert r.json()['number']==inv['number'] and r.json()['status']=='Draft'
 assert request(o,inv).status_code==409
 history=o.get(path+'/details').json()['reopenings']
 assert len(history)==1 and history[0]['original_amount']==100
 old_pdf=o.get(history[0]['pdf_url']);assert old_pdf.status_code==200 and old_pdf.content.startswith(b'%PDF')
 assert clients['tech'].get(history[0]['pdf_url']).status_code==403
 assert o.get(history[0]['pdf_url'].replace(iid,str(uuid.uuid4()))).status_code==404
 with Session(m.engine) as db:
  row=db.get(m.InvoiceRecord,iid);assert row.issued_on is None and row.number==inv['number']
  assert db.get(b.InvoiceSnapshot,iid) is None
  assert db.get(b.MailOutbox,preview['id']).status=='Superseded'
  archive=db.get(b.InvoiceReopening,r.json()['archive_id'])
  assert json.loads(archive.original_json)['invoice']['status']=='Issued'
  assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.entity_id==iid,m.AuditEvent.event=='REOPEN_INVOICE'))==1
 assert o.post('/api/admin/mail-outbox/'+preview['id']+'/process',json={}).json()['status']=='Superseded'
 assert o.put('/api/invoices/'+iid+'/lines',json={'lines':[{'description':'Materials and labor','quantity':1,'unit_price':200}]}).status_code==200
 assert o.post('/api/invoices/'+iid+'/issue').status_code==200
 assert o.post(path+'/reopen',json={'expected_revision':meta['revision'],'reason':'Stale browser must not reopen again'}).status_code==409
 assert o.get(history[0]['pdf_url']).content==old_pdf.content
 cfg2=o.get('/api/admin/billing/settings').json()
 o.put('/api/admin/billing/settings',json={**cfg['values'],'expected_revision':cfg2['revision']})


@pytest.mark.parametrize('activity',['payment','credit','Queued','Sending','Sent','Unconfirmed','legacy_sent'])
def test_reopen_rejects_financial_and_delivery_activity(setup_clock,activity):
 clients,orders,now,tid,job=setup_clock;o=clients['owner'];inv=issued(o,job);iid=inv['id']
 if activity=='payment':
  assert o.post('/api/invoices/'+iid+'/payments',json={'amount':10,'paid_on':str(date.today()),'reference':uuid.uuid4().hex}).status_code==201
 elif activity=='credit':
  assert o.post('/api/invoices/'+iid+'/credits',json={'amount':10,'credited_on':str(date.today()),'number':uuid.uuid4().hex,'reason':'Test invoice credit'}).status_code==201
 else:
  uid=o.get('/api/auth/me').json()['id']
  with Session(m.engine) as db:
   if activity=='legacy_sent':
    db.add(m.InvoiceEmailEvent(invoice_id=iid,recipient='test@example.com',subject='Sent',sent_by=uid,gmail_message_id='provider-id'))
   else:
    db.add(b.MailOutbox(invoice_id=iid,request_id=str(uuid.uuid4()),recipient='test@example.com',
       subject='Invoice',message='Invoice',pdf=b'%PDF',fingerprint='test',status=activity,created_by=uid))
   db.commit()
 assert request(o,inv).status_code==409
 with Session(m.engine) as db:
  assert db.get(m.InvoiceRecord,iid).status!='Draft'
  assert not db.scalar(select(b.InvoiceReopening.id).where(b.InvoiceReopening.invoice_id==iid))


def test_reopen_requires_owner_reason_and_current_revision(setup_clock):
 clients,orders,now,tid,job=setup_clock;o=clients['owner'];inv=issued(o,job)
 path='/api/admin/invoices/'+inv['id']+'/reopen'
 assert clients['tech'].post(path,json={'expected_revision':0,'reason':'Technician attempt'}).status_code==403
 assert request(o,inv,reason='          ').status_code==422
 assert request(o,inv,expected_revision=99).status_code==409


def test_reopened_call_refresh_adds_po_once_to_same_invoice(setup_clock):
 clients,orders,now,tid,job=setup_clock;o=clients['owner'];t=clients['tech'];wo=orders[0]
 o.put('/api/labor-rates',json={'technician_id':tid,'hourly_cost':30,'mileage_rate':.5})
 entry=t.post('/api/work-orders/'+wo+'/time',json={'started_at':now[0].isoformat(),'ended_at':(now[0]+timedelta(hours=1)).isoformat()}).json()
 assert o.post('/api/time/'+entry['id']+'/approve',json={}).status_code==201
 t.put('/api/work-orders/'+wo+'/mileage',json={'start_odometer':100,'end_odometer':100,'expected_revision':0})
 data={'mode':'actual','parts':[],'labor_sell_per_hour':100,'mileage_sell_per_mile':1}
 base='/api/call-pricing/'+wo
 saved=o.put(base+'/actual',json=data);assert saved.status_code==200,saved.text
 number='REOPEN-CALL-'+uuid.uuid4().hex
 body={'expected_revision':1,'number':number,'taxable':False}
 exported=o.post(base+'/invoice/export',json=body);assert exported.status_code==200,exported.text
 iid=exported.json()['id'];assert o.post('/api/invoices/'+iid+'/issue').status_code==200
 with Session(m.engine) as db:
  jid=db.scalar(select(m.Job.id).where(m.Job.job_number==job))
  po=m.PurchaseOrder(job_id=jid,po_number='REOPEN-PO-'+uuid.uuid4().hex,vendor='Supplier',amount=100,received_value=0,status='Issued',source='crm_v13')
  db.add(po);db.commit();pid=po.id
 assert request(o,{'id':iid}).status_code==200
 saved=o.put(base+'/actual',json={**data,'expected_revision':1});assert saved.status_code==200,saved.text
 assert Decimal(saved.json()['pricing']['po_material_cost'])==100
 body['expected_revision']=2
 refreshed=o.post(base+'/invoice/export',json=body);assert refreshed.status_code==200,refreshed.text
 assert refreshed.json()['id']==iid
 assert o.post(base+'/invoice/export',json=body).json()['id']==iid
 assert o.post('/api/invoices/'+iid+'/issue').status_code==200
 with Session(m.engine) as db:
  assert db.get(m.InvoiceRecord,iid).number==number
  assert db.get(POInvoiceCost,pid).invoice_id==iid
  assert db.scalar(select(func.count()).select_from(POInvoiceCost).where(POInvoiceCost.po_id==pid))==1
