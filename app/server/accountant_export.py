"""Owner-only source-record CSV export, not a tax return or accounting ledger."""
import csv
import io
import json
import zipfile
from datetime import date
from decimal import Decimal
from fastapi import Depends,HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import main as m,call_pricing as p,profit_tracking as profit


def year_bounds(year):
 if not 2000<=year<=2100:raise HTTPException(422,'Choose a year from 2000 to 2100')
 return date(year,1,1),date(year+1,1,1)


def export_data(db,year):
 from .billing_v111 import CheckReceipt,CheckAllocation,CheckLifecycle,WaveOpening
 start,end=year_bounds(year);files={};warnings=[];relevant_pos=set()
 def in_year(value):return value is not None and start<=value<end
 def job_info(job_id):
  j=db.get(m.Job,job_id);c=db.get(m.Customer,j.customer_id);site=db.get(m.Location,j.location_id) if j.location_id else None
  return {'job_id':j.id,'job_number':j.job_number,'customer_id':c.id,'customer':c.name,'service_site':site.name if site else '', 'service_address':site.address if site else '', 'billing_address':c.billing_address}
 def invoice_info(invoice_id):
  inv=db.get(m.InvoiceRecord,invoice_id)
  return {'invoice_id':inv.id,'invoice_number':inv.number,'invoice_issued_on':inv.issued_on.isoformat() if inv.issued_on else '',**job_info(inv.job_id)}
 issued=[];undated=[]
 for inv in db.scalars(select(m.InvoiceRecord).where(m.InvoiceRecord.status!='Draft').order_by(m.InvoiceRecord.number)):
  if not inv.issued_on:undated.append({'invoice_id':inv.id,'invoice_number':inv.number,'amount_including_tax':str(inv.amount),**job_info(inv.job_id),'review':'No issue date; cannot assign a tax year'});continue
  if not in_year(inv.issued_on):continue
  lines=m.invoice_lines(db,inv);tax=sum((x.tax_amount for x in lines),Decimal('0')) if lines else None
  if tax is None:warnings.append('Invoice '+inv.number+' has no itemized tax breakdown; tax was left blank, not assumed zero.')
  ledger=m.invoice_ledger(db,inv,end-date.resolution)
  issued.append({**invoice_info(inv.id),'due_on':str(inv.due_on or ''),'wave_opening_balance_only':bool(db.get(WaveOpening,inv.id)),'document_total_including_tax':str(inv.amount),'subtotal_excluding_tax':str(sum((x.subtotal for x in lines),Decimal('0'))) if lines else '', 'sales_tax':str(tax) if tax is not None else '', 'tax_breakdown_known':bool(lines),'credits_through_year_end':str(ledger['credits']),'collected_through_year_end':str(ledger['collected']),'balance_through_year_end':str(ledger['balance'])})
 files['sales_invoices.csv']=[r for r in issued if not r['wave_opening_balance_only']];files['wave_opening_balances_REFERENCE_ONLY.csv']=[r for r in issued if r['wave_opening_balance_only']];files['undated_invoices_review.csv']=undated
 payments=[];reversals=[];refunds=[];credits=[]
 for row in db.scalars(select(m.InvoicePayment).order_by(m.InvoicePayment.paid_on)):
  if in_year(row.paid_on):payments.append({'record_id':row.id,'paid_on':str(row.paid_on),'amount_received':str(row.amount),'reference':row.reference,'check_allocation_REFERENCE_ONLY':bool(db.scalar(select(CheckAllocation).where(CheckAllocation.payment_id==row.id))),**invoice_info(row.invoice_id)})
 for row in db.scalars(select(m.PaymentReversal).order_by(m.PaymentReversal.reversed_on)):
  if in_year(row.reversed_on):
   payment=db.get(m.InvoicePayment,row.payment_id);reversals.append({'record_id':row.id,'reversed_on':str(row.reversed_on),'amount':str(row.amount),'original_payment_id':payment.id,'check_allocation_REFERENCE_ONLY':bool(db.scalar(select(CheckAllocation).where(CheckAllocation.payment_id==payment.id))),'reference':row.reference,'reason':row.reason,**invoice_info(payment.invoice_id)})
 for row in db.scalars(select(m.InvoiceRefund).order_by(m.InvoiceRefund.refunded_on)):
  if in_year(row.refunded_on):refunds.append({'record_id':row.id,'refunded_on':str(row.refunded_on),'amount':str(row.amount),'reference':row.reference,'reason':row.reason,**invoice_info(row.invoice_id)})
 for row in db.scalars(select(m.InvoiceCredit).order_by(m.InvoiceCredit.credited_on)):
  if in_year(row.credited_on):credits.append({'record_id':row.id,'credited_on':str(row.credited_on),'number':row.number,'amount_including_any_tax':str(row.amount),'tax_component':'','reason':row.reason,**invoice_info(row.invoice_id)})
 files.update({'customer_payments.csv':payments,'payment_reversals.csv':reversals,'customer_refunds.csv':refunds,'invoice_credits.csv':credits})
 def purchase_info(po_id):
  po=db.get(m.PurchaseOrder,po_id);relevant_pos.add(po.id)
  return {'po_id':po.id,'po_number':po.po_number,'vendor':po.vendor,**job_info(po.job_id)}
 bills=[];vendor_credits=[];receipts=[]
 for row in db.scalars(select(m.POVendorInvoice).order_by(m.POVendorInvoice.invoice_date)):
  if in_year(row.invoice_date):bills.append({'record_id':row.id,'invoice_date':str(row.invoice_date),'reference':row.reference,'bill_amount':str(row.amount),'actual_paid_on':'','actual_paid_amount':'','expense_category':'ACCOUNTANT_REVIEW','purchase_tax_component':'','matched_receipt_value_reference_only':str(row.matched_receipt_value),'variance_reason':row.variance_reason,**purchase_info(row.po_id)})
 for row in db.scalars(select(m.POVendorCredit).order_by(m.POVendorCredit.credited_on)):
  if in_year(row.credited_on):vendor_credits.append({'record_id':row.id,'credited_on':str(row.credited_on),'reference':row.reference,'amount':str(row.amount),'reason':row.reason,**purchase_info(row.po_id)})
 for row in db.scalars(select(m.POReceipt).order_by(m.POReceipt.received_on)):
  if in_year(row.received_on):receipts.append({'record_id':row.id,'received_on':str(row.received_on),'reference':row.reference,'received_value_not_cash_payment':str(row.amount),'note':row.note,**purchase_info(row.po_id)})
 files.update({'vendor_bills.csv':bills,'vendor_credits.csv':vendor_credits,'purchasing_receipts_REFERENCE_ONLY.csv':receipts})
 mileage=[];labor=[]
 for row in db.scalars(select(m.CallMileage)):
  w=db.get(m.WorkOrder,row.work_order_id);basis=profit.utc(w.created_at).date()
  if in_year(basis):mileage.append({'record_id':w.id+':'+row.technician_id,'call_id':w.id,'call':w.summary,'technician':db.get(m.User,row.technician_id).display_name,'call_created_on_REFERENCE_ONLY':str(basis),'actual_trip_date_VERIFY':'','start_odometer':str(row.start_odometer),'end_odometer':str(row.end_odometer) if row.end_odometer is not None else '', 'miles':str(row.miles),'approved_vehicle_cost_ESTIMATE_ONLY':str(row.approved_cost) if row.approved_cost is not None else '',**job_info(w.job_id)})
 for approval,entry in db.execute(select(m.TimeApproval,m.FieldTimeEntry).join(m.FieldTimeEntry,m.TimeApproval.time_entry_id==m.FieldTimeEntry.id)):
  when=profit.utc(entry.started_at).astimezone(__import__('zoneinfo').ZoneInfo('America/New_York')).date()
  if in_year(when):
   w=db.get(m.WorkOrder,entry.work_order_id);labor.append({'approval_id':approval.id,'time_entry_id':entry.id,'work_started_on':str(when),'technician':db.get(m.User,entry.technician_id).display_name,'approved_hours':str(entry.hours),'regular_hours':str(approval.regular_hours),'overtime_hours':str(approval.overtime_hours),'internal_loaded_labor_cost_NOT_PAYROLL_PAID':str(approval.labor_cost),'legacy_miles_REFERENCE_ONLY':str(entry.mileage),**job_info(w.job_id)})
 files['mileage_REFERENCE_ONLY.csv']=mileage;files['approved_labor_costs_REFERENCE_ONLY.csv']=labor
 attachments=[]
 for a in db.scalars(select(m.POAttachment)):
  if a.po_id in relevant_pos or in_year(profit.utc(a.uploaded_at).date()):attachments.append({'attachment_id':a.id,'filename':a.filename,'kind':a.kind,'media_type':a.mime_type,'bytes':a.size_bytes,'download_path':'/api/purchasing/attachments/'+a.id+'/download',**purchase_info(a.po_id)})
 files['receipt_documents_index.csv']=attachments
 if undated:warnings.append(str(len(undated))+' undated issued invoices are in the review file and are not assigned to this year.')
 if credits:warnings.append('Credit memo amounts do not contain a separate tax allocation. Reconcile tax components before using them for tax reporting.')
 warnings.extend(['These are CRM source records, not a complete general ledger or tax return. No accounting method or deduction eligibility is assumed.',
  'Do not add sales invoice totals to customer payments as a single income total. Payment, credit, refund and reversal dates can fall in different years.',
  'Vendor bills and received PO values do not establish cash payments. Vendor payment dates, purchase tax components and expense categories are left for verification.',
  'Receiving values, mileage costs and loaded labor costs are operational references, not additional deductions or evidence of payroll/vehicle cash payments. Do not add them again to vendor bills or payroll.',
  'The CRM does not include all bank/card transactions, rent, insurance, payroll paid, depreciation, owner transactions or other expenses unless recorded elsewhere. Reconcile with bank records, Wave and your accountant.',
  'Mileage year selection uses call creation dates because a verified trip date is not stored on the odometer record; confirm actual trip dates. This is not a complete tax mileage log.',
  'The package covers only records entered/imported into this CRM. It is not a Wave import format and no automatic Wave sync is configured.'])
 checks=[];returns=[]
 for c in db.scalars(select(CheckReceipt)):
  if in_year(c.received_on):checks.append({'check_id':c.id,'customer_id':c.customer_id,'check_number':c.check_number,'received_on':str(c.received_on),'amount_received':str(c.amount),'status_now':c.status,'deposited_on':str(c.deposited_on or '')})
 for event in db.scalars(select(CheckLifecycle).where(CheckLifecycle.action=='Returned')):
  if in_year(event.action_on):
   c=db.get(CheckReceipt,event.check_id);returns.append({'event_id':event.id,'check_id':c.id,'returned_on':str(event.action_on),'amount':str(c.amount),'reason':event.reason})
 files['customer_check_receipts.csv']=checks;files['returned_checks.csv']=returns
 warnings.append('Check receipt totals include unapplied amounts. Invoice payments marked check_allocation_REFERENCE_ONLY and their reversals are allocations of those same checks, not additional cash movements. Deposits move funds to the bank; they are not another receipt. Wave opening balances are outstanding receivable references, not new sales.')
 total=lambda rows,key:sum((Decimal(r[key]) for r in rows),Decimal('0'))
 legacy_payments=[r for r in payments if not r['check_allocation_REFERENCE_ONLY']];legacy_reversals=[r for r in reversals if not r['check_allocation_REFERENCE_ONLY']]
 summary={'recorded_check_receipts':str(total(checks,'amount_received')),'returned_checks':str(total(returns,'amount')),'recorded_customer_payments':str(total(payments,'amount_received')),'recorded_payment_reversals':str(total(reversals,'amount')),'recorded_customer_refunds':str(total(refunds,'amount')),'net_recorded_customer_cash_movements_including_any_tax':str(total(legacy_payments,'amount_received')+total(checks,'amount_received')-total(legacy_reversals,'amount')-total(returns,'amount')-total(refunds,'amount'))}
 return {'year':year,'generated_at':profit.stamp().isoformat(),'files':files,'counts':{name:len(rows) for name,rows in files.items()},'summary':summary,'warnings':warnings}


def make_zip(db,data,include_receipts):
 from .purchasing_v13 import ATTACHMENT_ROOT
 from . import private_storage
 buffer=io.BytesIO();file_rows=data['files'];total_bytes=0
 with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
  for name,rows in file_rows.items():
   out=io.StringIO();fields=list(dict.fromkeys(key for row in rows for key in row)) or ['no_records_in_selected_period'];writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader()
   for row in rows:
    values={key:("'"+str(value) if str(value).lstrip().startswith(('=','+','-','@')) else value) for key,value in row.items()};writer.writerow(values)
   z.writestr(name,out.getvalue().encode('utf-8-sig'))
  if include_receipts:
   for item in file_rows['receipt_documents_index.csv']:
    if total_bytes+item['bytes']>50*1024*1024:raise HTTPException(413,'Receipt files exceed the 50 MB export limit; use the index and download separately')
    attachment=db.get(m.POAttachment,item['attachment_id']);payload=private_storage.read(ATTACHMENT_ROOT,attachment.storage_key);total_bytes+=len(payload)
    if total_bytes>50*1024*1024:raise HTTPException(413,'Receipt files exceed the export limit')
    extension={'application/pdf':'.pdf','image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp','image/heic':'.heic'}.get(attachment.mime_type,'.bin')
    z.writestr('receipt_documents/'+attachment.id+extension,payload)
  z.writestr('manifest.json',json.dumps({k:v for k,v in data.items() if k!='files'},indent=2))
  z.writestr('READ-ME-FIRST.txt','Limitless Mechanical accountant source export — '+str(data['year'])+'\n\n'+'\n\n'.join(data['warnings'])+'\n\nReceipts included: '+str(include_receipts)+'\nStable record IDs allow deduplication. Keep original supporting documents and verify the export with your accountant.\n')
 return buffer.getvalue()


def attach_accountant_export(app):
 @app.get('/api/admin/accountant-export')
 def preview(year:int,user=Depends(p.owner)):
  with Session(m.engine) as db:
   data=export_data(db,year);return {key:value for key,value in data.items() if key!='files'}
 @app.get('/api/admin/accountant-export.zip')
 def download(year:int,include_receipts:bool=False,user=Depends(p.owner)):
  with Session(m.engine) as db:
   data=export_data(db,year);payload=make_zip(db,data,include_receipts);m.log(db,'EXPORT_ACCOUNTANT_SOURCE_RECORDS','accounting',str(year),p.dump({'include_receipts':include_receipts,'counts':data['counts']}));db.commit()
   return StreamingResponse(io.BytesIO(payload),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="Limitless-accountant-source-{year}.zip"','Cache-Control':'private, no-store'})
