"""Owner-only profitability policies, cost snapshots and longitudinal customer records."""
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import String, Text, ForeignKey, DateTime, select
from sqlalchemy.orm import Mapped, mapped_column, Session
from . import main as m, call_pricing as p

ZERO=Decimal('0')

def dec(value):return Decimal(str(value or 0))
def amount(value):return str(p.money(value))
def stamp():return datetime.now(timezone.utc)

def utc(value):return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class ProfitPolicy(m.Base):
    __tablename__='profit_policies'
    scope:Mapped[str]=mapped_column(String(50),primary_key=True)
    revision:Mapped[int]=mapped_column(nullable=False,default=1)
    values_json:Mapped[str]=mapped_column(Text,nullable=False)

class LaborBurden(m.Base):
    __tablename__='labor_burden_profiles'
    technician_id:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),primary_key=True)
    revision:Mapped[int]=mapped_column(nullable=False,default=1)
    values_json:Mapped[str]=mapped_column(Text,nullable=False)

class LaborCostSnapshot(m.Base):
    __tablename__='labor_cost_snapshots'
    approval_id:Mapped[str]=mapped_column(String(36),primary_key=True) # Retained even when a corrected approval is replaced.
    details_json:Mapped[str]=mapped_column(Text,nullable=False)

class CallCategory(m.Base):
    __tablename__='call_profit_categories'
    work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),primary_key=True)
    original_call_id:Mapped[str|None]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=True,index=True)
    job_type:Mapped[str]=mapped_column(String(50),nullable=False,default='Service')
    reason:Mapped[str]=mapped_column(Text,nullable=False,default='')
    revision:Mapped[int]=mapped_column(nullable=False,default=1)

class CustomerFollowUp(m.Base):
    __tablename__='customer_follow_ups'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    customer_id:Mapped[str]=mapped_column(ForeignKey('customers.id',ondelete='RESTRICT'),nullable=False,index=True)
    work_order_id:Mapped[str|None]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=True)
    source_note_id:Mapped[str|None]=mapped_column(ForeignKey('call_notes.id',ondelete='RESTRICT'),nullable=True,unique=True)
    text:Mapped[str]=mapped_column(Text,nullable=False)
    due_on:Mapped[date|None]=mapped_column(m.Date,nullable=True)
    status:Mapped[str]=mapped_column(String(20),nullable=False,default='Open')
    revision:Mapped[int]=mapped_column(nullable=False,default=1)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=stamp)
    updated_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)

class PolicyIn(BaseModel):
    minimum_call_price:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
    overhead_per_hour:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
    repeat_window_days:int|None=Field(default=None,ge=7,le=730)
    repeat_service_calls:int|None=Field(default=None,ge=2,le=100)
    repeat_callbacks:int|None=Field(default=None,ge=1,le=100)
    expected_revision:int=Field(default=0,ge=0)

class BurdenIn(BaseModel):
    base_wage:Decimal=Field(gt=0,max_digits=10,decimal_places=2)
    payroll_percent:Decimal=Field(default=ZERO,ge=0,le=100,decimal_places=3)
    workers_comp_percent:Decimal=Field(default=ZERO,ge=0,le=100,decimal_places=3)
    benefits_per_hour:Decimal=Field(default=ZERO,ge=0,max_digits=10,decimal_places=2)
    overtime_multiplier:Decimal=Field(default=Decimal('1.5'),ge=1,le=4,decimal_places=2)
    mileage_rate:Decimal=Field(default=ZERO,ge=0,max_digits=8,decimal_places=3)
    expected_revision:int=Field(default=0,ge=0)

class CategoryIn(BaseModel):
    job_type:Literal['Service','Maintenance','Installation','Emergency','Warranty']='Service'
    original_call_id:str|None=None
    reason:str=Field(default='',max_length=2000)
    expected_revision:int=Field(default=0,ge=0)
    @model_validator(mode='after')
    def reason_required(self):
        if self.original_call_id and len(self.reason.strip())<5:raise ValueError('Explain the callback relationship')
        return self

class FollowUpIn(BaseModel):
    text:str=Field(min_length=3,max_length=5000)
    due_on:date|None=None
    status:Literal['Open','Scheduled','Resolved','Declined']='Open'
    work_order_id:str|None=None
    source_note_id:str|None=None
    expected_revision:int=Field(default=0,ge=0)


def customer_for(db,w):return db.get(m.Job,w.job_id).customer_id

def policy_for(db,customer_id=None):
    result={'minimum_call_price':'0','overhead_per_hour':'0','repeat_window_days':'90','repeat_service_calls':'3','repeat_callbacks':'2'};revisions={}
    for key in ('global',customer_id):
        if not key:continue
        row=db.get(ProfitPolicy,key)
        if row:result.update(json.loads(row.values_json));revisions[key]=row.revision
    selected=db.get(ProfitPolicy,customer_id or 'global')
    return {'values':result,'revision':selected.revision if selected else 0,'overrides':json.loads(selected.values_json) if selected else {},'sources':revisions}


def burden_cost(db,entry,rate,regular,overtime):
    row=db.get(LaborBurden,entry.technician_id)
    if not row:return None
    v=json.loads(row.values_json);base=dec(v['base_wage'])
    # The legacy cost-rate form must not silently replace a structured wage profile.
    if rate.hourly_cost!=base or rate.overtime_multiplier!=dec(v['overtime_multiplier']):
        raise HTTPException(409,'Labor rate changed outside the burden profile. Review the fully loaded labor settings first.')
    wage=regular*base+overtime*base*rate.overtime_multiplier
    payroll=wage*dec(v['payroll_percent'])/100;comp=wage*dec(v['workers_comp_percent'])/100
    benefits=entry.hours*dec(v['benefits_per_hour']);total=p.money(wage+payroll+comp+benefits)
    return {'profile_revision':row.revision,'profile':v,'wage_cost':amount(wage),'payroll_cost':amount(payroll),
            'workers_comp_cost':amount(comp),'benefits_cost':amount(benefits),'total':str(total)}


def category_payload(row,wo):
    return {'work_order_id':wo,'revision':row.revision if row else 0,'job_type':row.job_type if row else 'Service',
            'original_call_id':row.original_call_id if row else None,'reason':row.reason if row else ''}


def live_projection(db,w,estimate,actual,complete):
    if complete and actual:
        return actual['gross_profit'],'Reviewed actual pricing; before invoice adjustments'
    if not estimate or estimate.get('blockers'):return None,'Save a complete estimate to project call profitability'
    from .call_field_notes import usage_rows
    labor=ZERO;vehicle=ZERO;unknown=False;active_found=False
    entries=list(db.scalars(select(m.FieldTimeEntry).where(m.FieldTimeEntry.work_order_id==w.id)))
    def hourly(rate,technician):
        if not rate:return None
        profile=db.get(LaborBurden,technician)
        if not profile:return rate.hourly_cost
        v=json.loads(profile.values_json)
        if rate.hourly_cost!=dec(v['base_wage']):return None
        return rate.hourly_cost*(1+(dec(v['payroll_percent'])+dec(v['workers_comp_percent']))/100)+dec(v['benefits_per_hour'])
    for entry in entries:
        approval=db.scalar(select(m.TimeApproval).where(m.TimeApproval.time_entry_id==entry.id))
        rate=db.scalar(select(m.LaborRate).where(m.LaborRate.technician_id==entry.technician_id))
        if approval:labor+=approval.labor_cost;vehicle+=approval.mileage_cost
        else:
            cost=hourly(rate,entry.technician_id)
            if cost is None:unknown=True
            else:labor+=entry.hours*cost;vehicle+=entry.mileage*rate.mileage_rate
    for clock in db.scalars(select(m.ActiveTimeClock).where(m.ActiveTimeClock.work_order_id==w.id)):
        active_found=True;rate=db.scalar(select(m.LaborRate).where(m.LaborRate.technician_id==clock.technician_id));cost=hourly(rate,clock.technician_id)
        if cost is None:unknown=True
        else:labor+=max(ZERO,dec((stamp()-utc(clock.started_at)).total_seconds())/3600)*cost
    for mileage in db.scalars(select(m.CallMileage).where(m.CallMileage.work_order_id==w.id)):
        if mileage.approved_cost is not None:vehicle+=mileage.approved_cost
        else:
            rate=db.scalar(select(m.LaborRate).where(m.LaborRate.technician_id==mileage.technician_id))
            if not rate:unknown=True
            else:vehicle+=mileage.miles*rate.mileage_rate
    reported_parts=ZERO
    for used in usage_rows(db,w.id):
        part=db.get(p.CatalogPart,used.part_id) if used.part_id else None
        if part:reported_parts+=used.quantity*part.unit_cost
        else:
            saved=next((line for line in (actual or {}).get('parts',[]) if line.get('usage_id')==used.id and dec(line['quantity'])==used.quantity),None)
            if saved:reported_parts+=dec(saved['extended_cost'])
            else:unknown=True
    if unknown:return None,'Projection needs a labor rate or an unlisted part cost; no zero-cost assumptions were made'
    cost=max(dec(estimate['labor_cost']),labor)+max(dec(estimate['mileage_cost']),vehicle)+max(dec(estimate['parts_cost']),reported_parts)+dec(estimate.get('other_cost'))+dec(estimate.get('acquisition_freight'))+dec(estimate.get('nonrecoverable_purchase_tax'))
    label='Provisional: saved quote price; greater of estimated or incurred labor, vehicle and parts costs. Unapproved/running time assumes regular hours; overtime requires review.'
    if not entries and not active_found and not reported_parts:label='Saved estimate; no field labor or parts incurred yet'
    return amount(dec(estimate['selling_subtotal'])-cost),label


def call_financial(db,w):
    recorded=p.recorded_costs(db,w.id);category=db.get(CallCategory,w.id)
    rows={r.mode:r for r in db.scalars(select(p.CallPricing).where(p.CallPricing.work_order_id==w.id))}
    estimate=json.loads(rows['estimate'].snapshot_json) if 'estimate' in rows else None
    actual=json.loads(rows['actual'].snapshot_json) if 'actual' in rows else None
    link=db.get(p.CallPricingExport,(w.id,'invoice'));inv=db.get(m.InvoiceRecord,link.record_id) if link else None
    issued=bool(inv and inv.status!='Draft')
    if issued:actual=json.loads(link.snapshot_json)  # billed snapshots survive later draft edits
    blockers=list(recorded['blockers'])
    if actual:
        blockers.extend(x for x in actual.get('blockers',[]) if x!='Enter a positive customer selling price')
        if actual['recorded']['token']!=recorded['token']:blockers.append('Recorded costs changed; refresh actual pricing')
    else:blockers.append('Actual parts and other costs have not been reviewed')
    complete=not blockers
    direct=dec(actual['total_direct_cost']) if complete else None
    ledger=m.invoice_ledger(db,inv) if issued else None
    adjusted=bool(ledger and (ledger['credits'] or ledger['refunds'] or ledger['reversals']))
    revenue=sum((l.subtotal for l in m.invoice_lines(db,inv)),ZERO) if issued else ZERO
    if adjusted:blockers.append('Invoice adjustments require reconciliation')
    gross=revenue-direct if issued and complete and not adjusted else None
    overhead=dec(actual.get('allocated_overhead',0)) if complete else None
    if not issued and (dec(recorded['labor_cost']) or dec(recorded['mileage_cost'])):blockers.append('Recorded costs are not yet linked to an issued invoice')
    from .call_field_notes import usage_rows
    if usage_rows(db,w.id) and not actual:blockers.append('Reported parts are not accounted for in actual pricing')
    job=db.get(m.Job,w.job_id);c=db.get(m.Customer,job.customer_id)
    equipments=[db.get(m.Equipment,r.equipment_id).asset_tag for r in db.scalars(select(m.WorkOrderEquipment).where(m.WorkOrderEquipment.work_order_id==w.id))]
    projected_profit,projection_basis=live_projection(db,w,estimate,actual,complete)
    return {'id':w.id,'customer_id':c.id,'customer':c.name,'job_number':job.job_number,'summary':w.summary,'status':w.status,
        'created_at':utc(w.created_at).isoformat(),'equipment':equipments,**category_payload(category,w.id),
        'issued':issued,'invoice_id':inv.id if inv else None,'billed_before_adjustments':amount(revenue),
        'cost_complete':complete,'direct_cost':amount(direct) if direct is not None else None,
        'approved_cost_so_far':amount(dec(recorded['labor_cost'])+dec(recorded['mileage_cost'])),
        'gross_profit':amount(gross) if gross is not None else None,
        'gross_margin_percent':amount(gross/revenue*100) if gross is not None and revenue>0 else None,
        'allocated_overhead':amount(overhead) if overhead is not None else None,
        'profit_after_allocated_overhead':amount(gross-overhead) if gross is not None else None,
        'estimated_cost':estimate['total_direct_cost'] if estimate else None,
        'cost_variance':amount(direct-dec(estimate['total_direct_cost'])) if direct is not None and estimate else None,
        'hours_variance':str(dec(recorded['labor_hours'])-dec(estimate['labor_hours'])) if estimate else None,
        'parts_cost_variance':amount(dec(actual['parts_cost'])-dec(estimate['parts_cost'])) if complete and estimate else None,
        'projected_profit':projected_profit,
        'projection_basis':projection_basis,
        'recorded_hours':recorded['labor_hours'],'alerts':list(dict.fromkeys(blockers)),
        'margin_override':actual.get('margin_override') if actual else None,
        'adjustments_need_reconciliation':adjusted}


def portfolio(db,customer_id=None):
    query=select(m.WorkOrder).join(m.Job,m.WorkOrder.job_id==m.Job.id)
    if customer_id:query=query.where(m.Job.customer_id==customer_id)
    rows=[call_financial(db,w) for w in db.scalars(query.order_by(m.WorkOrder.created_at.desc()))]
    families=[]
    for root in rows:
        if root['original_call_id']:continue
        members=[root]+[r for r in rows if r['original_call_id']==root['id']]
        complete=all(r['cost_complete'] and not r['adjustments_need_reconciliation'] for r in members)
        revenue=sum((dec(r['billed_before_adjustments']) for r in members),ZERO)
        cost=sum((dec(r['direct_cost']) for r in members),ZERO) if complete else None
        callback_cost=sum((dec(r['direct_cost']) for r in members[1:]),ZERO) if complete else None
        overhead=sum((dec(r['allocated_overhead']) for r in members),ZERO) if complete else None
        families.append({'original_call_id':root['id'],'customer_id':root['customer_id'],'customer':root['customer'],
            'job_type':root['job_type'],'equipment':root['equipment'],'callback_count':len(members)-1,
            'revenue':amount(revenue),'cost':amount(cost) if cost is not None else None,
            'callback_cost':amount(callback_cost) if callback_cost is not None else None,
            'gross_profit':amount(revenue-cost) if cost is not None and root['issued'] else None,
            'margin_percent':amount((revenue-cost)/revenue*100) if cost is not None and root['issued'] and revenue>0 else None,
            'profit_after_allocated_overhead':amount(revenue-cost-overhead) if cost is not None and root['issued'] else None,
            'cost_complete':complete,'call_ids':[r['id'] for r in members]})
    def groups(field):
        buckets={}
        for r in families:
            if field=='customer':names=[(r['customer_id'],r['customer'])]
            elif field=='equipment':
                assets=list(db.scalars(select(m.Equipment).join(m.WorkOrderEquipment,m.WorkOrderEquipment.equipment_id==m.Equipment.id).where(m.WorkOrderEquipment.work_order_id==r['original_call_id'])))
                names=[(a.id,db.get(m.Location,a.location_id).name+' / '+a.asset_tag) for a in assets]
            else:names=[(r[field],r[field])]
            for key,name in names or [('unassigned','Unassigned')]:
                bucket=buckets.setdefault(key,{'id':key,'name':name,'families':0,'covered_families':0,'revenue':ZERO,'cost':ZERO,'profit_after_allocated_overhead':ZERO})
                bucket['families']+=1
                if r['gross_profit'] is not None:
                    bucket['covered_families']+=1;bucket['revenue']+=dec(r['revenue']);bucket['cost']+=dec(r['cost']);bucket['profit_after_allocated_overhead']+=dec(r['profit_after_allocated_overhead'])
        result=[]
        for b in buckets.values():
            revenue=b['revenue'];cost=b['cost'];result.append({**b,'revenue':amount(revenue),'cost':amount(cost),
                'profit_after_allocated_overhead':amount(b['profit_after_allocated_overhead']) if b['covered_families'] else None,
                'gross_profit':amount(revenue-cost) if b['covered_families'] else None,
                'margin_percent':amount((revenue-cost)/revenue*100) if revenue else None})
        return result
    flagged=[]
    asset_query=select(m.Equipment).join(m.Location,m.Equipment.location_id==m.Location.id)
    if customer_id:asset_query=asset_query.where(m.Location.customer_id==customer_id)
    for asset in db.scalars(asset_query):
        health=equipment_health(db,asset,db.get(m.Location,asset.location_id).customer_id)
        if health['flagged']:flagged.append(health)
    return {'flagged_systems':flagged,'calls':rows,'families':families,'by_customer':groups('customer'),'by_job_type':groups('job_type'),'by_equipment':groups('equipment'),
        'basis':'All recorded calls. Family profitability includes the original call plus linked callbacks, counted once. Only reviewed costs and unadjusted issued invoices contribute to realized margins. Equipment groups show associated calls; a call with multiple assets appears in each asset group and those groups must not be summed. Overhead is an allocation, not audited net profit.'}


def equipment_health(db,asset,customer_id):
    from datetime import timedelta
    policy=policy_for(db,customer_id)['values'];days=int(policy['repeat_window_days']);start=stamp()-timedelta(days=days)
    linked=list(db.scalars(select(m.WorkOrder).join(m.WorkOrderEquipment,m.WorkOrderEquipment.work_order_id==m.WorkOrder.id).where(m.WorkOrderEquipment.equipment_id==asset.id,m.WorkOrder.created_at>=start,m.WorkOrder.created_at<=stamp()).order_by(m.WorkOrder.created_at.desc())))
    service=[];callbacks=[];maintenance=[]
    for w in linked:
        category=db.get(CallCategory,w.id)
        item={'id':w.id,'summary':w.summary,'status':w.status,'created_at':utc(w.created_at).isoformat(),'original_call_id':category.original_call_id if category else None}
        # Only explicitly classified planned maintenance is excluded, never a callback.
        planned=bool(db.scalar(select(m.PMVisit.id).where(m.PMVisit.work_order_id==w.id))) or bool(category and category.job_type=='Maintenance')
        if planned and not (category and category.original_call_id):maintenance.append(item);continue
        service.append(item)
        if category and category.original_call_id:callbacks.append(item)
    reasons=[]
    if len(service)>=int(policy['repeat_service_calls']):reasons.append(f"{len(service)} service calls within {days} days")
    if len(callbacks)>=int(policy['repeat_callbacks']):reasons.append(f"{len(callbacks)} callbacks within {days} days")
    return {'equipment_id':asset.id,'asset_tag':asset.asset_tag,'customer_id':customer_id,'location_id':asset.location_id,
        'customer':db.get(m.Customer,customer_id).name,'location':db.get(m.Location,asset.location_id).name,
        'flagged':bool(reasons),'reasons':reasons,'window_days':days,'service_count':len(service),'callback_count':len(callbacks),
        'excluded_maintenance_count':len(maintenance),'service_threshold':int(policy['repeat_service_calls']),'callback_threshold':int(policy['repeat_callbacks']),
        'visits':service,'basis':'Rolling calendar window uses call creation dates in UTC; distinct linked calls, not note or time-entry counts. Generated PM visits and classified planned maintenance are excluded. Flags update automatically as visits age out.'}


def follow_up_payload(r):return {'id':r.id,'text':r.text,'due_on':r.due_on.isoformat() if r.due_on else None,'status':r.status,'revision':r.revision,'work_order_id':r.work_order_id,'source_note_id':r.source_note_id}


def customer_history(db,customer):
    jobs=list(db.scalars(select(m.Job).where(m.Job.customer_id==customer.id)));ids=[j.id for j in jobs]
    calls=list(db.scalars(select(m.WorkOrder).where(m.WorkOrder.job_id.in_(ids)))) if ids else []
    call_ids=[w.id for w in calls];proposals=list(db.scalars(select(m.Proposal).where(m.Proposal.job_id.in_(ids)))) if ids else []
    invoices=list(db.scalars(select(m.InvoiceRecord).where(m.InvoiceRecord.job_id.in_(ids)))) if ids else []
    locations=list(db.scalars(select(m.Location).where(m.Location.customer_id==customer.id)))
    assets=list(db.scalars(select(m.Equipment).where(m.Equipment.location_id.in_([l.id for l in locations])))) if locations else []
    from .call_field_notes import CallNote
    notes=list(db.scalars(select(CallNote).where(CallNote.work_order_id.in_(call_ids)).order_by(CallNote.created_at.desc()))) if call_ids else []
    follows=list(db.scalars(select(CustomerFollowUp).where(CustomerFollowUp.customer_id==customer.id)))
    tracked={r.source_note_id:r for r in follows if r.source_note_id};today=stamp().date();billed=ZERO;collected=ZERO;balance=ZERO;overdue=ZERO;aging={'current':ZERO,'1_30':ZERO,'31_60':ZERO,'61_90':ZERO,'over_90':ZERO};invoice_rows=[];payment_days=[]
    for inv in invoices:
        ledger=m.invoice_ledger(db,inv);issued=inv.status!='Draft'
        days=max(0,(today-inv.due_on).days) if inv.due_on else 0
        if issued:
            billed+=ledger['net_billed'];collected+=ledger['collected'];balance+=ledger['balance']
            if days:overdue+=ledger['balance']
            key='current' if days==0 else '1_30' if days<=30 else '31_60' if days<=60 else '61_90' if days<=90 else 'over_90'
            aging[key]+=ledger['balance']
            payments=list(db.scalars(select(m.InvoicePayment).where(m.InvoicePayment.invoice_id==inv.id)))
            clean=not any(ledger[k] for k in ('credits','refunds','reversals'))
            if clean and inv.issued_on and payments and ledger['balance']==0 and ledger['collected']>=inv.amount:
                payment_days.append(max(0,(max(x.paid_on for x in payments)-inv.issued_on).days))
        invoice_rows.append({'id':inv.id,'number':inv.number,'status':inv.status,'issued_on':inv.issued_on.isoformat() if inv.issued_on else None,
            'due_on':inv.due_on.isoformat() if inv.due_on else None,'net_billed_with_tax':amount(ledger['net_billed']),
            'balance':amount(ledger['balance']),'days_overdue':days if issued and ledger['balance']>0 else 0,
            'adjustments_need_reconciliation':bool(ledger['credits'] or ledger['refunds'] or ledger['reversals'])})
    accepted=sum(x.status in ('Approved','Accepted') for x in proposals);decided=sum(x.status in ('Approved','Accepted','Rejected','Declined') for x in proposals)
    recurring=[]
    for a in assets:
        linked=list(db.scalars(select(m.WorkOrderEquipment.work_order_id).where(m.WorkOrderEquipment.equipment_id==a.id)))
        plans=list(db.scalars(select(m.PMPlan).where(m.PMPlan.equipment_id==a.id,m.PMPlan.active==True)))
        health=equipment_health(db,a,customer.id)
        recurring.append({'health':health,'id':a.id,'asset_tag':a.asset_tag,'manufacturer':a.manufacturer,'model':a.model,'serial':a.serial,
            'location_id':a.location_id,'call_ids':linked,'service_count':len(linked),
            'maintenance':[{'next_due':x.next_due.isoformat(),'interval_days':x.interval_days,'overdue':x.next_due<today} for x in plans]})
    return {'customer':{'id':customer.id,'name':customer.name,'phone':customer.phone,'email':customer.email,'contact':customer.contact},
        'summary':{'calls':len(calls),'first_call':min((utc(w.created_at).isoformat() for w in calls),default=None),
            'last_call':max((utc(w.created_at).isoformat() for w in calls),default=None),'accepted_quotes':accepted,
            'decided_quotes':decided,'quote_acceptance_percent':amount(dec(accepted)/decided*100) if decided else None,
            'net_billed_including_tax':amount(billed),'collected_including_tax':amount(collected),'outstanding_including_tax':amount(balance),
            'overdue_including_tax':amount(overdue),'average_days_to_full_payment':round(sum(payment_days)/len(payment_days),1) if payment_days else None,
            'payment_sample_invoices':len(payment_days),'open_follow_ups':sum(r.status in ('Open','Scheduled') for r in follows)+sum(n.kind in ('recommendation','follow_up') and n.id not in tracked for n in notes)},
        'aging':{k:amount(v) for k,v in aging.items()},'equipment':recurring,
        'locations':[{'id':l.id,'name':l.name,'address':l.address} for l in locations],
        'quotes':[{'id':q.id,'number':q.number,'title':q.title,'status':q.status,'total':amount(q.total),'created_at':utc(q.created_at).isoformat()} for q in proposals],
        'invoices':invoice_rows,'follow_ups':[follow_up_payload(r) for r in follows],
        'recommendations':[{'id':n.id,'work_order_id':n.work_order_id,'kind':n.kind,'audience':n.audience,'text':n.text,
            'created_at':utc(n.created_at).isoformat(),'tracking_id':tracked[n.id].id if n.id in tracked else None} for n in notes if n.kind in ('recommendation','follow_up')],
        'service_notes':[{'id':n.id,'work_order_id':n.work_order_id,'kind':n.kind,'audience':n.audience,'text':n.text,'created_at':utc(n.created_at).isoformat()} for n in notes],
        'legacy_service_notes':[{'work_order_id':n.work_order_id,'text':n.note,'created_at':utc(n.created_at).isoformat()} for n in db.scalars(select(m.ServiceNote).where(m.ServiceNote.work_order_id.in_(call_ids)).order_by(m.ServiceNote.created_at.desc()))] if call_ids else [],
        'profitability':portfolio(db,customer.id),
        'basis':'Lifetime activity recorded in this CRM. Billing, receipts and aging include tax and invoice adjustments; profitability excludes sales tax. Average payment days covers fully paid, unadjusted invoices only. Missing imported history is not inferred.'}


def attach_profit_tracking(app):
    m.Base.metadata.create_all(m.engine)
    @app.get('/profitability')
    def page(user=Depends(p.owner)):return FileResponse(m.ROOT/'profitability.html')
    @app.get('/profitability.js')
    def script(user=Depends(p.owner)):return FileResponse(m.ROOT/'profitability.js',media_type='application/javascript')
    @app.get('/api/admin/profitability')
    def report(user=Depends(p.owner)):
        with Session(m.engine) as db:return portfolio(db)
    @app.get('/api/admin/profit-policy')
    def policy(customer_id:str|None=None,user=Depends(p.owner)):
        with Session(m.engine) as db:
            if customer_id and not db.get(m.Customer,customer_id):raise HTTPException(404,'Customer not found')
            return policy_for(db,customer_id)
    @app.put('/api/admin/profit-policy')
    def save_policy(data:PolicyIn,customer_id:str|None=None,user=Depends(p.owner)):
        with Session(m.engine) as db:
            m.lock_time_user(db,user['id'])
            if customer_id and not db.get(m.Customer,customer_id):raise HTTPException(404,'Customer not found')
            key=customer_id or 'global';row=db.scalar(select(ProfitPolicy).where(ProfitPolicy.scope==key).with_for_update())
            if data.expected_revision!=(row.revision if row else 0):raise HTTPException(409,'Policy changed; reload before saving')
            values={k:str(v) for k,v in data.model_dump().items() if k!='expected_revision' and v is not None}
            if not row:row=ProfitPolicy(scope=key,values_json=p.dump(values));db.add(row)
            else:row.values_json=p.dump(values);row.revision+=1
            m.log(db,'SAVE_PROFIT_POLICY','customer' if customer_id else 'company',key,p.dump(values));db.commit();return policy_for(db,customer_id)
    @app.get('/api/admin/labor-burden')
    def labor_profiles(user=Depends(p.owner)):
        with Session(m.engine) as db:
            result=[]
            for tech in db.scalars(select(m.User).where(m.User.role=='technician')):
                row=db.get(LaborBurden,tech.id);v=json.loads(row.values_json) if row else None
                result.append({'technician_id':tech.id,'name':tech.display_name,'revision':row.revision if row else 0,'values':v,
                    'loaded_regular_hourly_cost':amount(dec(v['base_wage'])*(1+(dec(v['payroll_percent'])+dec(v['workers_comp_percent']))/100)+dec(v['benefits_per_hour'])) if v else None})
            return result
    @app.put('/api/admin/labor-burden/{technician_id}')
    def save_burden(technician_id:str,data:BurdenIn,user=Depends(p.owner)):
        with Session(m.engine) as db:
            tech=db.get(m.User,technician_id)
            if not tech or tech.role!='technician':raise HTTPException(422,'Select a technician')
            m.lock_time_user(db,technician_id)
            row=db.get(LaborBurden,technician_id)
            if data.expected_revision!=(row.revision if row else 0):raise HTTPException(409,'Labor profile changed; reload before saving')
            v={k:str(v) for k,v in data.model_dump().items() if k!='expected_revision'}
            if not row:row=LaborBurden(technician_id=technician_id,values_json=p.dump(v));db.add(row)
            else:row.values_json=p.dump(v);row.revision+=1
            rate=db.scalar(select(m.LaborRate).where(m.LaborRate.technician_id==technician_id))
            if not rate:rate=m.LaborRate(technician_id=technician_id);db.add(rate)
            rate.hourly_cost=data.base_wage;rate.overtime_multiplier=data.overtime_multiplier;rate.mileage_rate=data.mileage_rate
            m.log(db,'SAVE_LABOR_BURDEN','technician',technician_id,p.dump(v));db.commit();return {'saved':True,'revision':row.revision}
    @app.get('/api/admin/calls/{wo_id}/profitability')
    def call_report(wo_id:str,user=Depends(p.owner)):
        with Session(m.engine) as db:return call_financial(db,p.call(db,wo_id))
    @app.get('/api/admin/calls/{wo_id}/category')
    def get_category(wo_id:str,user=Depends(p.owner)):
        with Session(m.engine) as db:p.call(db,wo_id);return category_payload(db.get(CallCategory,wo_id),wo_id)
    @app.put('/api/admin/calls/{wo_id}/category')
    def save_category(wo_id:str,data:CategoryIn,user=Depends(p.owner)):
        with Session(m.engine) as db:
            # Serialize root/child relationship updates across owners as well as calls.
            db.scalars(select(m.User).where(m.User.role=='owner').order_by(m.User.id).with_for_update()).all()
            w=p.call(db,wo_id,lock=True);row=db.get(CallCategory,wo_id)
            if data.expected_revision!=(row.revision if row else 0):raise HTTPException(409,'Call classification changed; reload')
            if data.original_call_id:
                original=p.call(db,data.original_call_id);parent=db.get(CallCategory,original.id)
                if original.id==wo_id or customer_for(db,original)!=customer_for(db,w):raise HTTPException(422,'A callback must link to a different call for this customer')
                if utc(original.created_at)>utc(w.created_at):raise HTTPException(422,'Original call must precede its callback')
                if (parent and parent.original_call_id) or db.scalar(select(CallCategory.work_order_id).where(CallCategory.original_call_id==wo_id)):
                    raise HTTPException(422,'Link callbacks directly to the original call; chains are not allowed')
            if not row:row=CallCategory(work_order_id=wo_id);db.add(row)
            else:row.revision+=1
            row.job_type=data.job_type;row.original_call_id=data.original_call_id;row.reason=data.reason.strip()
            m.log(db,'CLASSIFY_CALL','work_order',wo_id,data.model_dump_json());db.commit();return category_payload(row,wo_id)
    @app.get('/api/calls/{wo_id}/service-systems')
    def available_systems(wo_id:str,user=Depends(m.current_user)):
        from .productivity import access
        with Session(m.engine) as db:
            w=access(db,wo_id,user);job=db.get(m.Job,w.job_id)
            query=select(m.Equipment).join(m.Location,m.Equipment.location_id==m.Location.id).where(m.Location.customer_id==job.customer_id)
            if job.location_id:query=query.where(m.Equipment.location_id==job.location_id)
            linked=set(db.scalars(select(m.WorkOrderEquipment.equipment_id).where(m.WorkOrderEquipment.work_order_id==wo_id)))
            return [{'id':a.id,'asset_tag':a.asset_tag,'manufacturer':a.manufacturer,'model':a.model,'location':db.get(m.Location,a.location_id).name,'linked':a.id in linked} for a in db.scalars(query)]
    @app.post('/api/calls/{wo_id}/service-systems/{equipment_id}')
    def link_system(wo_id:str,equipment_id:str,user=Depends(m.current_user)):
        from .productivity import access
        with Session(m.engine) as db:
            w=access(db,wo_id,user);p.call(db,wo_id,lock=True);job=db.get(m.Job,w.job_id);asset=db.get(m.Equipment,equipment_id)
            if not asset:raise HTTPException(404,'System not found')
            location=db.get(m.Location,asset.location_id)
            if location.customer_id!=job.customer_id or (job.location_id and job.location_id!=location.id):raise HTTPException(422,'System belongs to a different customer or site')
            if db.get(m.WorkOrderEquipment,(wo_id,equipment_id)):return {'linked':True}
            m.field_report_open(db,wo_id)
            export=db.get(p.CallPricingExport,(wo_id,'invoice'))
            if export and db.get(m.InvoiceRecord,export.record_id).status!='Draft':raise HTTPException(409,'Issued call system links are locked')
            db.add(m.WorkOrderEquipment(work_order_id=wo_id,equipment_id=equipment_id));m.log(db,'LINK_SERVICED_SYSTEM','equipment',equipment_id,wo_id);db.commit();return {'linked':True}
    @app.get('/api/admin/equipment/{equipment_id}/health')
    def system_health(equipment_id:str,user=Depends(p.owner)):
        with Session(m.engine) as db:
            asset=db.get(m.Equipment,equipment_id)
            if not asset:raise HTTPException(404,'System not found')
            return equipment_health(db,asset,db.get(m.Location,asset.location_id).customer_id)
    @app.get('/api/admin/customers/{customer_id}/history')
    def history(customer_id:str,user=Depends(p.owner)):
        with Session(m.engine) as db:
            customer=db.get(m.Customer,customer_id)
            if not customer:raise HTTPException(404,'Customer not found')
            return customer_history(db,customer)
    def save_follow(db,customer_id,data,user,row=None):
        if not db.get(m.Customer,customer_id):raise HTTPException(404,'Customer not found')
        if not data.text.strip():raise HTTPException(422,'Enter a follow-up description')
        if data.expected_revision!=(row.revision if row else 0):raise HTTPException(409,'Follow-up changed; reload')
        if data.work_order_id and customer_for(db,p.call(db,data.work_order_id))!=customer_id:raise HTTPException(422,'Call belongs to a different customer')
        if data.source_note_id:
            from .call_field_notes import CallNote
            note=db.get(CallNote,data.source_note_id)
            if not note or note.kind not in ('recommendation','follow_up') or customer_for(db,p.call(db,note.work_order_id))!=customer_id or data.work_order_id!=note.work_order_id:
                raise HTTPException(422,'Select a recommendation from this customer and call')
            duplicate=db.scalar(select(CustomerFollowUp).where(CustomerFollowUp.source_note_id==data.source_note_id))
            if duplicate and (not row or duplicate.id!=row.id):raise HTTPException(409,'Recommendation is already tracked')
        if row and (row.source_note_id!=data.source_note_id or row.work_order_id!=data.work_order_id):raise HTTPException(422,'Follow-up source cannot be changed')
        if not row:row=CustomerFollowUp(customer_id=customer_id,updated_by=user['id']);db.add(row)
        else:row.revision+=1
        row.text=data.text.strip();row.work_order_id=data.work_order_id;row.source_note_id=data.source_note_id;row.due_on=data.due_on;row.status=data.status;row.updated_at=stamp();row.updated_by=user['id']
        db.flush();m.log(db,'SAVE_CUSTOMER_FOLLOW_UP','customer',customer_id,data.model_dump_json());db.commit();return follow_up_payload(row)
    @app.post('/api/admin/customers/{customer_id}/follow-ups',status_code=201)
    def create_follow(customer_id:str,data:FollowUpIn,user=Depends(p.owner)):
        with Session(m.engine) as db:
            customer=db.scalar(select(m.Customer).where(m.Customer.id==customer_id).with_for_update())
            if not customer:raise HTTPException(404,'Customer not found')
            return save_follow(db,customer_id,data,user)
    @app.put('/api/admin/customers/{customer_id}/follow-ups/{follow_id}')
    def edit_follow(customer_id:str,follow_id:str,data:FollowUpIn,user=Depends(p.owner)):
        with Session(m.engine) as db:
            row=db.scalar(select(CustomerFollowUp).where(CustomerFollowUp.id==follow_id,CustomerFollowUp.customer_id==customer_id).with_for_update())
            if not row:raise HTTPException(404,'Follow-up not found')
            return save_follow(db,customer_id,data,user,row)
