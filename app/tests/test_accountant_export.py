import csv
import io
import json
import uuid
import zipfile
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session
from server import main as m
from test_time_clock import setup_clock


def test_accountant_export_separates_invoice_and_cash_dates_and_redacts_drafts(setup_clock):
 clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech']
 with Session(m.engine) as db:
  j=db.scalar(select(m.Job).where(m.Job.job_number==job));owner_id=owner.get('/api/auth/me').json()['id']
  old=m.InvoiceRecord(job_id=j.id,number='OLD'+uuid.uuid4().hex[:8],status='Issued',amount=110,issued_on=date(2025,12,31));draft=m.InvoiceRecord(job_id=j.id,number='DRAFT'+uuid.uuid4().hex[:8],status='Draft',amount=999,issued_on=date(2026,1,1));db.add_all([old,draft]);db.flush()
  payment=m.InvoicePayment(invoice_id=old.id,amount=110,paid_on=date(2026,1,5),reference='PAY'+uuid.uuid4().hex);db.add(payment);db.flush();pid=payment.id;old_number=old.number;draft_number=draft.number
  db.add(m.PaymentReversal(payment_id=payment.id,reference='REV'+uuid.uuid4().hex,amount=10,reason='Test correction',reversed_on=date(2026,1,6),created_by=owner_id));db.commit()
 assert tech.get('/api/admin/accountant-export?year=2026').status_code==403
 result=owner.get('/api/admin/accountant-export.zip?year=2026');assert result.status_code==200,result.text
 with zipfile.ZipFile(io.BytesIO(result.content)) as z:
  rows=lambda name:list(csv.DictReader(io.StringIO(z.read(name).decode('utf-8-sig'))))
  assert any(r['record_id']==pid and r['invoice_number']==old_number for r in rows('customer_payments.csv'))
  assert not any(r['invoice_number'] in (old_number,draft_number) for r in rows('sales_invoices.csv'))
  assert any(r['original_payment_id']==pid for r in rows('payment_reversals.csv'))
  assert 'not a complete general ledger' in z.read('READ-ME-FIRST.txt').decode()
 assert owner.get('/api/admin/accountant-export?year=1800').status_code==422


def test_unknown_tax_undated_records_and_csv_formula_safety(setup_clock):
 clients,orders,now,tech_id,job=setup_clock;owner=clients['owner']
 with Session(m.engine) as db:
  j=db.scalar(select(m.Job).where(m.Job.job_number==job));c=db.get(m.Customer,j.customer_id);c.name='=DO_NOT_EXECUTE()'
  undated=m.InvoiceRecord(job_id=j.id,number='UND'+uuid.uuid4().hex[:8],status='Issued',amount=20)
  dated=m.InvoiceRecord(job_id=j.id,number='DATE'+uuid.uuid4().hex[:8],status='Issued',amount=100,issued_on=date(2026,1,2));db.add_all([undated,dated]);db.commit();und=undated.number;num=dated.number
 preview=owner.get('/api/admin/accountant-export?year=2026');assert preview.status_code==200,preview.text
 assert any('no itemized tax breakdown' in x for x in preview.json()['warnings'])
 result=owner.get('/api/admin/accountant-export.zip?year=2026')
 with zipfile.ZipFile(io.BytesIO(result.content)) as z:
  rows=lambda name:list(csv.DictReader(io.StringIO(z.read(name).decode('utf-8-sig'))))
  row=next(r for r in rows('sales_invoices.csv') if r['invoice_number']==num)
  assert row['sales_tax']=='' and row['tax_breakdown_known']=='False'
  assert row['customer'].startswith("'=DO_NOT_EXECUTE")
  assert any(r['invoice_number']==und for r in rows('undated_invoices_review.csv'))
