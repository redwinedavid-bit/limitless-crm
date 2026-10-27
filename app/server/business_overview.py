"""Admin period overview. Operational date-based flows, not a general ledger P&L."""
import calendar
from collections import defaultdict
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import Depends,HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import main as m,call_pricing as pricing
from .billing_v111 import CheckReceipt,CheckAllocation,CheckLifecycle,WaveOpening
ZONE=ZoneInfo('America/New_York');ZERO=Decimal('0')
def today():return datetime.now(ZONE).date()
def local_day(value):return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(ZONE).date()
def money(value):return str(m.cents(value))
def month_shift(day,offset):
 index=day.year*12+day.month-1+offset;year,month=divmod(index,12);month+=1
 return date(year,month,min(day.day,calendar.monthrange(year,month)[1]))
def bounds(period,anchor=None,start=None,end=None):
 anchor=anchor or today()
 if period=='day':start=end=anchor;previous=anchor-timedelta(days=1);following=anchor+timedelta(days=1)
 elif period=='week':start=anchor-timedelta(days=anchor.weekday());end=start+timedelta(days=6);previous=anchor-timedelta(days=7);following=anchor+timedelta(days=7)
 elif period=='month':start=anchor.replace(day=1);end=start.replace(day=calendar.monthrange(start.year,start.month)[1]);previous=month_shift(anchor,-1);following=month_shift(anchor,1)
 elif period=='year':start=date(anchor.year,1,1);end=date(anchor.year,12,31);previous=month_shift(anchor,-12);following=month_shift(anchor,12)
 elif period=='custom':
  if not start or not end:raise HTTPException(422,'Select both custom dates')
  previous=following=None
 else:raise HTTPException(422,'Choose day, week, month, year or custom')
 if end<start or (end-start).days>3660 or start.year<2000 or end.year>2100:raise HTTPException(422,'Use ordered dates from 2000 to 2100, up to ten years')
 return {'period':period,'anchor':str(anchor),'start':str(start),'end':str(end),'previous_anchor':str(previous) if previous else None,'next_anchor':str(following) if following else None},start,end

def purchasing_events(db):
 """Recompute each PO's net actual at each event date, then emit its change.
 Bills replace matched receipts. Returns do not also reduce matched billed cost;
 vendor credits do that. This preserves the PO ledger's max-zero behavior.
 """
 result=[];by_po=defaultdict(lambda:defaultdict(lambda:defaultdict(lambda:ZERO)))
 pos=list(db.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.source!='historical')))
 ids=[po.id for po in pos]
 for po in pos:
  if po.issue_date:by_po[po.id][po.issue_date]['issued']+=po.amount
 for audit in db.scalars(select(m.AuditEvent).where(m.AuditEvent.entity_id.in_(ids),m.AuditEvent.event.in_(('CANCEL','CLOSE')))):
  by_po[audit.entity_id][local_day(audit.created_at)]['closed']+=1
 if not ids:return result
 for model,dayfield,kind in [(m.POReceipt,'received_on','received'),(m.POReturn,'returned_on','returned'),(m.POVendorInvoice,'invoice_date','invoiced'),(m.POVendorCredit,'credited_on','credits')]:
  for row in db.scalars(select(model).where(model.po_id.in_(ids))):
   day=getattr(row,dayfield);by_po[row.po_id][day][kind]+=row.amount
   if kind=='invoiced':by_po[row.po_id][day]['matched']+=row.matched_receipt_value
 for events in by_po.values():
  totals=defaultdict(lambda:ZERO);old=ZERO
  for day in sorted(events):
   for k,v in events[day].items():totals[k]+=v
   actual=totals['invoiced']-totals['credits']+max(ZERO,max(ZERO,totals['received']-totals['returned'])-totals['matched'])
   if totals['issued'] and not totals['closed']:actual+=max(ZERO,totals['issued']-totals['received'])
   delta=actual-old;old=actual
   if delta:result.append((day,delta,'purchasing'))
 return result

def build(db,period='month',anchor=None,start=None,end=None):
 meta,start,end=bounds(period,anchor,start,end);asof=min(end,today());effective_end=asof;events=[];details=[]
 def within(day):return start<=day<=effective_end
 def add(day,key,value):
  if within(day):events.append((day,key,Decimal(value)))
 opening=set(db.scalars(select(WaveOpening.invoice_id)));allocated=set(db.scalars(select(CheckAllocation.payment_id)))
 invoices=list(db.scalars(select(m.InvoiceRecord).where(m.InvoiceRecord.status!='Draft')))
 issued=ZERO;issued_count=0;unknown_tax=0;undated=0;net_collections=ZERO;overdue=[];upcoming=[];outstanding=ZERO;credits=ZERO
 for inv in invoices:
  if not inv.issued_on:undated+=1;continue
  if inv.id not in opening and within(inv.issued_on):
   issued+=inv.amount;issued_count+=1;add(inv.issued_on,'billed',inv.amount)
   if not m.invoice_lines(db,inv):unknown_tax+=1
   details.append({'kind':'Invoice issued','date':str(inv.issued_on),'reference':inv.number,'amount':money(inv.amount),'url':'/api/invoices/'+inv.id+'/document'})
  if inv.issued_on>asof:continue
  ledger=m.invoice_ledger(db,inv,asof);balance=ledger['balance'];outstanding+=balance
  if balance<=0:continue
  job=db.get(m.Job,inv.job_id);customer=db.get(m.Customer,job.customer_id)
  row={'id':inv.id,'number':inv.number,'customer':customer.name,'job_number':job.job_number,'due_on':str(inv.due_on) if inv.due_on else None,'balance':money(balance),'url':'/api/invoices/'+inv.id+'/document'}
  if inv.due_on and inv.due_on<asof:overdue.append(row)
  elif inv.due_on and asof<=inv.due_on<=asof+timedelta(days=30):upcoming.append(row)
 for row in db.scalars(select(m.InvoiceCredit)):
  inv=db.get(m.InvoiceRecord,row.invoice_id)
  if inv.id not in opening and inv.status!='Draft' and within(row.credited_on):credits+=row.amount
 # Checks are cash receipts even when unapplied. Their invoice allocations are not new money.
 for row in db.scalars(select(CheckReceipt)):
  if within(row.received_on):add(row.received_on,'collected',row.amount);net_collections+=row.amount
 for row in db.scalars(select(CheckLifecycle).where(CheckLifecycle.action=='Returned')):
  if within(row.action_on):amount=db.get(CheckReceipt,row.check_id).amount;add(row.action_on,'collected',-amount);net_collections-=amount
 for row in db.scalars(select(m.InvoicePayment)):
  if row.id not in allocated and within(row.paid_on):add(row.paid_on,'collected',row.amount);net_collections+=row.amount
 for row in db.scalars(select(m.PaymentReversal)):
  if row.payment_id not in allocated and within(row.reversed_on):add(row.reversed_on,'collected',-row.amount);net_collections-=row.amount
 for row in db.scalars(select(m.InvoiceRefund)):
  if within(row.refunded_on):add(row.refunded_on,'collected',-row.amount);net_collections-=row.amount
 # Costs follow the relevant recorded event, rather than call creation dates.
 for day,amount,kind in purchasing_events(db):add(day,'cost',amount)
 labor=vehicle=ZERO
 for approval,entry in db.execute(select(m.TimeApproval,m.FieldTimeEntry).join(m.FieldTimeEntry,m.TimeApproval.time_entry_id==m.FieldTimeEntry.id)):
  day=local_day(entry.started_at)
  if within(day):labor+=approval.labor_cost;vehicle+=approval.mileage_cost;add(day,'cost',approval.labor_cost+approval.mileage_cost)
 for row in db.scalars(select(m.CallMileage).where(m.CallMileage.approved_cost!=None,m.CallMileage.approved_at!=None)):
  day=local_day(row.approved_at)
  if within(day):vehicle+=row.approved_cost;add(day,'cost',row.approved_cost)
 cost=sum((v for d,k,v in events if k=='cost'),ZERO);purchasing=cost-labor-vehicle
 bucket='month' if period=='year' or (end-start).days>90 else 'day'
 series={};cursor=start
 while cursor<=effective_end:
  key=cursor.strftime('%Y-%m') if bucket=='month' else str(cursor)
  series.setdefault(key,{'date':key,'billed':ZERO,'collected':ZERO,'cost':ZERO})
  cursor=(cursor.replace(day=1)+timedelta(days=32)).replace(day=1) if bucket=='month' else cursor+timedelta(days=1)
 for day,kind,value in events:
  key=day.strftime('%Y-%m') if bucket=='month' else str(day)
  if key in series:series[key][kind]+=value
 # Gross margin only matches reviewed call snapshots to their issued invoice.
 from .profit_tracking import call_financial
 matched_revenue=matched_cost=ZERO;matched_count=excluded=0
 for link in db.scalars(select(pricing.CallPricingExport).where(pricing.CallPricingExport.kind=='invoice')):
  inv=db.get(m.InvoiceRecord,link.record_id)
  if not inv or inv.status=='Draft' or not inv.issued_on or not within(inv.issued_on):continue
  w=db.get(m.WorkOrder,link.work_order_id);r=call_financial(db,w)
  if r['gross_margin_percent'] is None:excluded+=1;continue
  matched_revenue+=Decimal(r['billed_before_adjustments']);matched_cost+=Decimal(r['direct_cost']);matched_count+=1
 margin=(matched_revenue-matched_cost)/matched_revenue*100 if matched_revenue else None
 calls=[w for w in db.scalars(select(m.WorkOrder)) if within(local_day(w.created_at))]
 po_created=sum(within(p.issue_date) for p in db.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.source!='historical')) if p.issue_date)
 return {'range':meta,'timezone':str(ZONE),'as_of':str(asof),'bucket':bucket,'partial_period':start<=today()<end,'series':[{**r,**{k:money(r[k]) for k in ('billed','collected','cost')}} for r in series.values()],
  'summary':{'issued_total_including_tax':money(issued),'issued_count':issued_count,'credit_memos_including_tax':money(credits),'net_payments_received_including_tax':money(net_collections),'recorded_job_cost':money(cost),'cost_breakdown':{'purchasing':money(purchasing),'approved_labor':money(labor),'approved_vehicle':money(vehicle)},'calls_created':len(calls),'new_pos':po_created,'matched_call_margin_percent':money(margin) if margin is not None else None,'matched_call_count':matched_count,'excluded_linked_calls':excluded,'unmatched_issued_count':issued_count-matched_count-excluded,'matched_revenue_pretax':money(matched_revenue),'matched_cost':money(matched_cost),'matched_gross_profit':money(matched_revenue-matched_cost)},
  'receivables':{'as_of':str(asof),'outstanding':money(outstanding),'overdue_total':money(sum((Decimal(r['balance']) for r in overdue),ZERO)),'overdue':sorted(overdue,key=lambda r:r['due_on']),'due_next_30_total':money(sum((Decimal(r['balance']) for r in upcoming),ZERO)),'due_next_30':sorted(upcoming,key=lambda r:r['due_on'])},
  'issued_details':sorted(details,key=lambda r:r['date'],reverse=True),'coverage':{'undated_invoices':undated,'invoices_without_itemized_tax':unknown_tax,'wave_openings_excluded_from_new_revenue':True},
  'basis':{'revenue':'Invoices issued on their issue dates, including sales tax; Wave opening balances excluded. Credit memos are shown separately, not silently deducted from issued totals.','payments':'Full check receipts plus other payments, less check returns, non-check reversals and refunds; allocations and deposits are not additional receipts. Includes any sales tax.','cost':'PO amounts on issue dates with later receipt/bill/return/credit adjustments, approved labor and legacy mileage on work start date, and automatically applied odometer costs on save date. Negative costs reflect credits/adjustments.','margin':'Current reviewed call-pricing costs matched to invoices issued in the selected period, before sales tax and overhead. Incomplete or adjusted calls are excluded; manual/unlinked invoices do not establish cost coverage. Later adjustments can make historical margin coverage incomplete.','limits':'Operational overview, not company net profit, taxable income or a complete cash-expense ledger. Unrecorded costs, business overhead, non-PO expenses and unapproved field costs are not included. Historical spreadsheet costs have no reliable dated posting and are excluded from this period chart.'}}

def attach_overview(app):
 @app.get('/business-overview')
 def page(user=Depends(pricing.owner)):return FileResponse(m.ROOT/'business-overview.html')
 @app.get('/business-overview.js')
 def script(user=Depends(pricing.owner)):return FileResponse(m.ROOT/'business-overview.js',media_type='application/javascript')
 @app.get('/api/admin/business-overview')
 def overview(period:str='month',anchor:date|None=None,start:date|None=None,end:date|None=None,user=Depends(pricing.owner)):
  with Session(m.engine) as db:return build(db,period,anchor,start,end)

