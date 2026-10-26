"""Owner-only operational metrics, with explicit coverage and reconciliation limits."""
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import main as m, call_pricing as pricing
from .productivity import call_readiness
from .call_field_notes import usage_rows
from .time_corrections import pending


def build_metrics(db,start,end):
    start_dt=datetime.combine(start,time.min,tzinfo=timezone.utc)
    end_dt=datetime.combine(end+timedelta(days=1),time.min,tzinfo=timezone.utc)
    calls=list(db.scalars(select(m.WorkOrder).where(m.WorkOrder.created_at>=start_dt,m.WorkOrder.created_at<end_dt).order_by(m.WorkOrder.created_at.desc())))
    rows=[];labor={};parts={};matched_revenue=Decimal('0');matched_cost=Decimal('0');receivable=Decimal('0');billed=Decimal('0');pending_time=0;unapproved_mileage=0
    for w in calls:
        job=db.get(m.Job,w.job_id);customer=db.get(m.Customer,job.customer_id);readiness=call_readiness(db,w)
        recorded=pricing.recorded_costs(db,w.id)
        actual=db.scalar(select(pricing.CallPricing).where(pricing.CallPricing.work_order_id==w.id,pricing.CallPricing.mode=='actual'))
        estimated=db.scalar(select(pricing.CallPricing).where(pricing.CallPricing.work_order_id==w.id,pricing.CallPricing.mode=='estimate'))
        a=json.loads(actual.snapshot_json) if actual else None;e=json.loads(estimated.snapshot_json) if estimated else None
        known=bool(a and a['ready_to_export'] and not recorded['blockers'] and a['recorded']['token']==recorded['token'])
        link=db.get(pricing.CallPricingExport,(w.id,'invoice'));inv=db.get(m.InvoiceRecord,link.record_id) if link else None
        issued=bool(inv and inv.status!='Draft')
        if issued:
            a=json.loads(link.snapshot_json)
            known=bool(not a.get('blockers') and not recorded['blockers'] and a['recorded']['token']==recorded['token'])
        net=None;adjusted=False;gross_margin=None;gross_profit=None
        if issued:
            ledger=m.invoice_ledger(db,inv);receivable+=ledger['balance']
            adjusted=bool(ledger['credits'] or ledger['refunds'] or ledger['reversals'])
            net=sum((line.subtotal for line in m.invoice_lines(db,inv)),Decimal('0'));billed+=net
            if known and not adjusted and net>0:
                cost=Decimal(a['total_direct_cost']);gross_profit=net-cost;gross_margin=pricing.money(gross_profit/net*100)
                matched_revenue+=net;matched_cost+=cost
        entries=list(db.scalars(select(m.FieldTimeEntry).where(m.FieldTimeEntry.work_order_id==w.id)))
        for entry in entries:
            name=db.get(m.User,entry.technician_id).display_name
            person=labor.setdefault(entry.technician_id,{'technician':name,'recorded_hours':Decimal('0'),'approved_hours':Decimal('0'),'approved_cost':Decimal('0'),'calls':set()})
            person['recorded_hours']+=entry.hours;person['calls'].add(w.id)
            approved=db.scalar(select(m.TimeApproval).where(m.TimeApproval.time_entry_id==entry.id))
            if approved:person['approved_hours']+=entry.hours;person['approved_cost']+=approved.labor_cost
            else:pending_time+=1
        unapproved_mileage+=sum(1 for row in db.scalars(select(m.CallMileage).where(m.CallMileage.work_order_id==w.id)) if row.approved_cost is None)
        for used in usage_rows(db,w.id):
            key=(used.part_id or used.description,used.unit)
            part=parts.setdefault(key,{'description':used.description,'unit':used.unit,'quantity':Decimal('0'),'calls':set()})
            part['quantity']+=used.quantity;part['calls'].add(w.id)
        rows.append({'id':w.id,'job_number':job.job_number,'customer_id':customer.id,'customer':customer.name,'summary':w.summary,'status':w.status,
                     'billing_stage':readiness['stage'],'blockers':readiness['blockers'],'recorded_hours':recorded['labor_hours'],
                     'approved_labor_cost':recorded['labor_cost'],'miles':recorded['miles'],'approved_vehicle_cost':recorded['mileage_cost'],
                     'estimated_hours':e['labor_hours'] if e else None,'hours_variance':str(Decimal(recorded['labor_hours'])-Decimal(e['labor_hours'])) if e else None,
                     'estimated_cost':e['total_direct_cost'] if e else None,'actual_cost':a['total_direct_cost'] if known else None,
                     'parts_cost':a['parts_cost'] if known else None,'cost_complete':known,
                     'billed_pretax':str(net) if net is not None else None,'invoice_number':inv.number if inv else None,
                     'gross_profit':str(pricing.money(gross_profit)) if gross_profit is not None else None,
                     'gross_margin_percent':str(gross_margin) if gross_margin is not None else None,
                     'adjustments_need_reconciliation':adjusted})
    weighted=(matched_revenue-matched_cost)/matched_revenue*100 if matched_revenue else None
    return {'start':start.isoformat(),'end':end.isoformat(),
            'basis':'Calls created in this UTC date range. Revenue/cost metrics cover linked call-pricing invoices only; unlinked/manual invoices and job-wide PO spending are excluded to avoid double-counting.',
            'summary':{'calls':len(rows),'completed':sum(r['status']=='Completed' for r in rows),'active':sum(r['status']!='Completed' for r in rows),
                       'ready_to_bill':sum(r['billing_stage'] in ('Ready to invoice','Draft invoice ready') for r in rows),
                       'unbilled_completed':sum(r['status']=='Completed' and r['billing_stage']!='Invoiced' for r in rows),
                       'pending_time_entries':pending_time,'unapproved_mileage_records':unapproved_mileage,
                       'pending_corrections':sum(len(pending(db,wo_id=w.id)) for w in calls),
                       'cost_complete_calls':sum(r['cost_complete'] for r in rows),'billed_pretax':str(pricing.money(billed)),
                       'receivable_including_tax':str(pricing.money(receivable)),
                       'matched_revenue':str(pricing.money(matched_revenue)),'matched_cost':str(pricing.money(matched_cost)),
                       'matched_gross_profit':str(pricing.money(matched_revenue-matched_cost)),
                       'weighted_gross_margin_percent':str(pricing.money(weighted)) if weighted is not None else None,
                       'margin_coverage_calls':sum(r['gross_margin_percent'] is not None for r in rows),
                       'under_35_calls':sum(Decimal(r['gross_margin_percent'])<35 for r in rows if r['gross_margin_percent'] is not None)},
            'calls_detail':rows,
            'technicians':[{'technician':r['technician'],'recorded_hours':str(r['recorded_hours']),'approved_hours':str(r['approved_hours']),'approved_cost':str(pricing.money(r['approved_cost'])),'calls':len(r['calls'])} for r in labor.values()],
            'parts_used':[{'description':r['description'],'unit':r['unit'],'quantity':str(r['quantity']),'calls':len(r['calls'])} for r in parts.values()],
            'limits':'Margins use owner-entered parts/other costs and approved time/mileage. Missing costs are not treated as zero. Credits, refunds or payment reversals require reconciliation before a margin is shown. Gross margin is before overhead and income tax.'}


def attach_admin_metrics(app):
    @app.get('/admin-metrics')
    def metrics_page(user=Depends(pricing.owner)):return FileResponse(m.ROOT/'admin-metrics.html')
    @app.get('/admin-metrics.js')
    def metrics_script(user=Depends(pricing.owner)):return FileResponse(m.ROOT/'admin-metrics.js',media_type='application/javascript')
    @app.get('/api/admin/metrics')
    def metrics(start:date|None=None,end:date|None=None,user=Depends(pricing.owner)):
        end=end or pricing.now().date();start=start or end-timedelta(days=29)
        if end<start or (end-start).days>3660:raise HTTPException(422,'Select an ordered date range of at most ten years')
        with Session(m.engine) as db:return build_metrics(db,start,end)
