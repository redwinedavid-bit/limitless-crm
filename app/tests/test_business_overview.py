"""Period/date logic, invoice balances, check deduplication and PO cost transitions."""
import uuid
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from sqlalchemy import select,delete
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
import pytest
from test_time_clock import setup_clock
from server import main as m,business_overview as overview
from server.billing_v111 import CheckReceipt,CheckAllocation,CheckLifecycle,WaveOpening

@pytest.fixture
def overview_setup(setup_clock,monkeypatch):
 clients,orders,now,tid,job=setup_clock
 monkeypatch.setattr(overview,'today',lambda:date(2044,7,20))
 with Session(m.engine) as db:
  j=db.scalar(select(m.Job).where(m.Job.job_number==job));jid=j.id;cid=j.customer_id;uid=db.scalar(select(m.User.id).where(m.User.username.like('owner%')).limit(1))
 yield clients,orders,tid,job,jid,cid,uid
 # Financial fixtures use one reporting year; remove their records between tests.
 from server.billing_v111 import InvoiceSnapshot,CommercialInvoice,MailOutbox
 with Session(m.engine) as db:
  ids=list(db.scalars(select(m.InvoiceRecord.id).where(m.InvoiceRecord.job_id==jid)))
  payments=list(db.scalars(select(m.InvoicePayment.id).where(m.InvoicePayment.invoice_id.in_(ids))))
  checks=list(db.scalars(select(CheckReceipt.id).where(CheckReceipt.customer_id==cid)))
  db.execute(delete(CheckAllocation).where(CheckAllocation.check_id.in_(checks)))
  db.execute(delete(CheckLifecycle).where(CheckLifecycle.check_id.in_(checks)))
  db.execute(delete(CheckReceipt).where(CheckReceipt.id.in_(checks)))
  db.execute(delete(m.PaymentReversal).where(m.PaymentReversal.payment_id.in_(payments)))
  db.execute(delete(m.InvoicePayment).where(m.InvoicePayment.id.in_(payments)))
  db.execute(delete(m.MileageInvoiceLink).where(m.MileageInvoiceLink.invoice_id.in_(ids)))
  for model in [m.InvoiceRefund,m.InvoiceCredit,m.InvoiceLine,InvoiceSnapshot,CommercialInvoice,MailOutbox,m.InvoiceEmailEvent,WaveOpening]:db.execute(delete(model).where(model.invoice_id.in_(ids)))
  from server.call_pricing import CallPricingExport
  db.execute(delete(CallPricingExport).where(CallPricingExport.record_id.in_(ids)))
  db.execute(delete(m.MileageInvoiceLink).where(m.MileageInvoiceLink.invoice_id.in_(ids)))
  db.execute(delete(m.InvoiceRecord).where(m.InvoiceRecord.id.in_(ids)))
  pos=list(db.scalars(select(m.PurchaseOrder.id).where(m.PurchaseOrder.job_id==jid)))
  for model in [m.POReceipt,m.POVendorInvoice,m.POReturn,m.POVendorCredit]:db.execute(delete(model).where(model.po_id.in_(pos)))
  db.execute(delete(m.PurchaseOrder).where(m.PurchaseOrder.id.in_(pos)))
  entries=list(db.scalars(select(m.FieldTimeEntry.id).where(m.FieldTimeEntry.work_order_id.in_(orders))))
  db.execute(delete(m.TimeApproval).where(m.TimeApproval.time_entry_id.in_(entries)))
  db.execute(delete(m.CallMileage).where(m.CallMileage.work_order_id.in_(orders)))
  db.commit()

def invoice(db,jid,issued,due=None,amount=100):
 i=m.InvoiceRecord(job_id=jid,number='OV-'+uuid.uuid4().hex[:12],amount=amount,status='Issued',issued_on=issued,due_on=due);db.add(i);db.flush();return i

def report(**kwargs):
 with Session(m.engine) as db:return overview.build(db,**kwargs)

def test_periods_monday_weeks_leap_year_and_navigation():
 meta,start,end=overview.bounds('week',date(2026,10,25));assert start==date(2026,10,19) and end==date(2026,10,25)
 meta,start,end=overview.bounds('month',date(2024,2,29));assert end==date(2024,2,29) and meta['next_anchor']=='2024-03-29'
 assert overview.bounds('year',date(2024,2,29))[0]['previous_anchor']=='2023-02-28'
 assert overview.bounds('day',date(2026,10,24))[1:]==(date(2026,10,24),date(2026,10,24))
 from fastapi import HTTPException
 for args in [('bad',None,None,None),('custom',None,date(2026,5,2),date(2026,5,1))]:
  with pytest.raises(HTTPException):overview.bounds(*args)

def test_overview_permissions_and_page(overview_setup):
 clients,*_=overview_setup
 for path in ['/business-overview','/business-overview.js','/api/admin/business-overview']:
  assert clients['owner'].get(path).status_code==200
  assert clients['tech'].get(path).status_code==403
  assert TestClient(m.app).get(path).status_code==401
 assert clients['owner'].get('/api/admin/business-overview?period=custom').status_code==422

def test_issue_dates_wave_exclusion_and_historical_receivables(overview_setup):
 clients,orders,tid,job,jid,cid,uid=overview_setup
 with Session(m.engine) as db:
  i=invoice(db,jid,date(2044,7,2),date(2044,7,10),200);iid=i.id
  future=invoice(db,jid,date(2044,7,21),date(2044,7,22),700)
  opening=invoice(db,jid,date(2044,7,1),date(2044,8,1),300);db.add(WaveOpening(invoice_id=opening.id,source_reference=uuid.uuid4().hex))
  undated=invoice(db,jid,None,None,50)
  db.add(m.InvoicePayment(invoice_id=i.id,amount=50,paid_on=date(2044,7,15),reference=uuid.uuid4().hex));db.commit()
 r=report(period='month',anchor=date(2044,7,20));assert r['summary']['issued_total_including_tax']=='200.00'
 assert r['summary']['issued_count']==1 and len(r['series'])==20
 assert next(x for x in r['receivables']['overdue'] if x['id']==iid)['balance']=='150.00'
 assert r['receivables']['due_next_30_total']=='300.00'
 historical=report(period='custom',start=date(2044,7,1),end=date(2044,7,12))
 assert next(x for x in historical['receivables']['overdue'] if x['id']==iid)['balance']=='200.00'
 assert historical['summary']['net_payments_received_including_tax']=='0.00'

def test_checks_count_once_unapplied_allocations_deposit_returns(overview_setup):
 clients,orders,tid,job,jid,cid,uid=overview_setup
 with Session(m.engine) as db:
  i=invoice(db,jid,date(2044,7,1),date(2044,7,5),500)
  check=CheckReceipt(customer_id=cid,check_number=uuid.uuid4().hex,amount=350,received_on=date(2044,7,2),deposited_on=date(2044,7,3),status='Returned');db.add(check);db.flush()
  p=m.InvoicePayment(invoice_id=i.id,amount=100,paid_on=date(2044,7,2),reference=uuid.uuid4().hex);other=m.InvoicePayment(invoice_id=i.id,amount=50,paid_on=date(2044,7,3),reference=uuid.uuid4().hex);db.add_all([p,other]);db.flush()
  db.add(CheckAllocation(request_id=str(uuid.uuid4()),check_id=check.id,payment_id=p.id))
  db.add(CheckLifecycle(check_id=check.id,action='Returned',action_on=date(2044,7,10),reason='Test returned check'))
  db.add(m.PaymentReversal(payment_id=p.id,amount=100,reversed_on=date(2044,7,10),reference=uuid.uuid4().hex,reason='Return',created_by=uid))
  db.add(m.PaymentReversal(payment_id=other.id,amount=20,reversed_on=date(2044,7,11),reference=uuid.uuid4().hex,reason='Correction',created_by=uid))
  db.add(m.InvoiceRefund(invoice_id=i.id,amount=10,refunded_on=date(2044,7,12),reference=uuid.uuid4().hex,reason='Refund',created_by=uid));db.commit()
 before=report(period='custom',start=date(2044,7,1),end=date(2044,7,9));assert before['summary']['net_payments_received_including_tax']=='400.00'
 full=report(period='month',anchor=date(2044,7,20));assert full['summary']['net_payments_received_including_tax']=='20.00'
 assert next(x for x in full['series'] if x['date']=='2044-07-10')['collected']=='-350.00'

def test_receiving_matching_returns_and_credits_do_not_double_count(overview_setup):
 clients,orders,tid,job,jid,cid,uid=overview_setup
 with Session(m.engine) as db:
  po=m.PurchaseOrder(po_number='QA'+uuid.uuid4().hex[:12],job_id=jid,vendor='Supplier',amount=100,received_value=100,status='Received',source='manual',issue_date=date(2044,7,1));db.add(po);db.flush()
  base={'po_id':po.id,'recorded_by':uid,'reference':uuid.uuid4().hex}
  db.add(m.POReceipt(**base,amount=100,received_on=date(2044,6,30)))
  db.add(m.POVendorInvoice(**base,vendor_key=uuid.uuid4().hex,amount=105,matched_receipt_value=100,invoice_date=date(2044,7,2),variance_reason='Freight'))
  db.add(m.POReturn(**base,amount=20,returned_on=date(2044,7,3),reason='Return billed items'))
  db.add(m.POVendorCredit(**base,vendor_key=uuid.uuid4().hex,amount=20,credited_on=date(2044,7,4),reason='Credit'))
  db.commit()
 r=report(period='month',anchor=date(2044,7,20));assert r['summary']['recorded_job_cost']=='-15.00'
 series={x['date']:x for x in r['series']};assert series['2044-07-02']['cost']=='5.00';assert series['2044-07-03']['cost']=='0.00';assert series['2044-07-04']['cost']=='-20.00'
 whole=report(period='custom',start=date(2044,6,30),end=date(2044,7,20));assert whole['summary']['recorded_job_cost']=='85.00'

def test_work_start_local_date_and_odometer_approval_date(overview_setup):
 clients,orders,tid,job,jid,cid,uid=overview_setup
 with Session(m.engine) as db:
  # 00:30 UTC July 2 is the evening of July 1 in Kentucky.
  entry=m.FieldTimeEntry(work_order_id=orders[0],technician_id=tid,started_at=datetime(2044,7,2,0,30,tzinfo=timezone.utc),ended_at=datetime(2044,7,2,2,30,tzinfo=timezone.utc),hours=2,mileage=0);db.add(entry);db.flush()
  db.add(m.TimeApproval(time_entry_id=entry.id,approved_by=uid,regular_hours=2,overtime_hours=0,labor_cost=80,mileage_cost=0))
  db.add(m.CallMileage(work_order_id=orders[0],technician_id=tid,start_odometer=100,end_odometer=120,miles=20,updated_at=datetime(2044,7,3,16,tzinfo=timezone.utc),approved_cost=10,approved_by=uid,approved_at=datetime(2044,7,3,16,tzinfo=timezone.utc)));db.commit()
 r=report(period='month',anchor=date(2044,7,20));series={x['date']:x for x in r['series']}
 assert series['2044-07-01']['cost']=='80.00';assert series['2044-07-03']['cost']=='10.00'
 assert r['summary']['cost_breakdown']['approved_labor']=='80.00' and r['summary']['cost_breakdown']['approved_vehicle']=='10.00'

def test_year_month_buckets_and_future_period_empty(overview_setup):
 with Session(m.engine) as db:
  r=overview.build(db,'year',date(2044,7,20));assert len(r['series'])==7 and r['bucket']=='month'
  future=overview.build(db,'month',date(2045,1,1));assert future['series']==[] and future['summary']['issued_count']==0

def test_margin_coverage_matches_costed_calls_and_excludes_adjustments(overview_setup,setup_clock):
 from test_profit_tracking import prepare_actual
 clients,orders,tid,job,jid,cid,uid=overview_setup;owner=clients['owner'];wo=orders[0]
 prepare_actual(setup_clock,wo,price=100,cost=50)
 exported=owner.post('/api/call-pricing/'+wo+'/invoice/export',json={'number':'MARGIN-'+uuid.uuid4().hex[:8],'expected_revision':1});assert exported.status_code==200,exported.text
 iid=exported.json()['id'];assert owner.post('/api/invoices/'+iid+'/issue').status_code==200
 with Session(m.engine) as db:
  inv=db.get(m.InvoiceRecord,iid);inv.issued_on=date(2044,7,2);invoice(db,jid,date(2044,7,3),amount=100);db.commit()
 r=report(period='month',anchor=date(2044,7,20));s=r['summary'];assert s['matched_call_margin_percent']=='50.00' and s['matched_call_count']==1 and s['unmatched_issued_count']==1
 assert s['issued_total_including_tax']=='200.00' and s['matched_revenue_pretax']=='100.00'
 with Session(m.engine) as db:
  db.add(m.InvoiceCredit(invoice_id=iid,number='CM'+uuid.uuid4().hex[:10],amount=10,credited_on=date(2044,7,3),reason='Adjustment',created_by=uid));db.commit()
 r=report(period='month',anchor=date(2044,7,20));assert r['summary']['matched_call_margin_percent'] is None and r['summary']['excluded_linked_calls']==1
