"""Owner-only price book and per-call gross-margin planning. Costs exclude sales tax."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import Literal
from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import ForeignKey, String, Text, DateTime, Numeric, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column, Session
from . import main as m

CENT=Decimal('.01')

def money(value):return Decimal(str(value)).quantize(CENT,rounding=ROUND_HALF_UP)
def minimum_price(cost,target):return (cost/(1-target/100)).quantize(CENT,rounding=ROUND_CEILING)
def now():return datetime.now(timezone.utc)
def dump(value):return json.dumps(value,sort_keys=True,separators=(',',':'))

class CatalogPart(m.Base):
    __tablename__='catalog_parts'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    sku:Mapped[str]=mapped_column(String(80),nullable=False,unique=True)
    description:Mapped[str]=mapped_column(String(500),nullable=False)
    unit:Mapped[str]=mapped_column(String(30),nullable=False)
    supplier:Mapped[str]=mapped_column(String(250),nullable=False,default='')
    unit_cost:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    unit_price:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    active:Mapped[bool]=mapped_column(nullable=False,default=True)
    revision:Mapped[int]=mapped_column(nullable=False,default=1)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=now)

class CallPricing(m.Base):
    __tablename__='call_pricing'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=False)
    mode:Mapped[str]=mapped_column(String(10),nullable=False)
    revision:Mapped[int]=mapped_column(nullable=False,default=1)
    input_json:Mapped[str]=mapped_column(Text,nullable=False)
    snapshot_json:Mapped[str]=mapped_column(Text,nullable=False)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=now)
    __table_args__=(UniqueConstraint('work_order_id','mode',name='uq_call_pricing_mode'),)

class CallPricingExport(m.Base):
    __tablename__='call_pricing_exports'
    work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),primary_key=True)
    kind:Mapped[str]=mapped_column(String(15),primary_key=True)
    record_id:Mapped[str]=mapped_column(String(36),nullable=False)
    pricing_revision:Mapped[int]=mapped_column(nullable=False)
    snapshot_json:Mapped[str]=mapped_column(Text,nullable=False)

class PartIn(BaseModel):
    sku:str=Field(min_length=1,max_length=80)
    description:str=Field(min_length=1,max_length=500)
    unit:str=Field(default='each',min_length=1,max_length=30)
    supplier:str=Field(default='',max_length=250)
    unit_cost:Decimal=Field(ge=0,max_digits=14,decimal_places=2)
    unit_price:Decimal|None=Field(default=None,ge=0,max_digits=14,decimal_places=2)
    active:bool=True
    expected_revision:int=Field(default=0,ge=0)

class PricingPartIn(BaseModel):
    part_id:str|None=None
    usage_id:str|None=None
    catalog_revision:int|None=Field(default=None,ge=1)
    description:str=Field(default='',max_length=500)
    unit_cost:Decimal|None=Field(default=None,ge=0,max_digits=14,decimal_places=2)
    unit_price:Decimal|None=Field(default=None,ge=0,max_digits=14,decimal_places=2)
    quantity:Decimal=Field(gt=0,le=1000000,decimal_places=3)
    @model_validator(mode='after')
    def required_cost(self):
        if self.part_id and self.catalog_revision is None:raise ValueError('Catalog revision is required')
        if not self.part_id and (not self.description.strip() or self.unit_cost is None):raise ValueError('Enter a description and cost for an additional part')
        return self

class PricingIn(BaseModel):
    overtime_sell_per_hour:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
    mode:Literal['estimate','actual']='estimate'
    parts:list[PricingPartIn]=Field(default_factory=list,max_length=100)
    labor_hours:Decimal=Field(default=Decimal('0'),ge=0,le=100000,decimal_places=3)
    labor_cost_per_hour:Decimal=Field(default=Decimal('0'),ge=0,max_digits=12,decimal_places=2)
    labor_sell_per_hour:Decimal=Field(ge=0,max_digits=12,decimal_places=2)
    miles:Decimal=Field(default=Decimal('0'),ge=0,le=1000000,decimal_places=2)
    mileage_cost_per_mile:Decimal=Field(default=Decimal('0'),ge=0,max_digits=10,decimal_places=3)
    mileage_sell_per_mile:Decimal=Field(ge=0,max_digits=10,decimal_places=2)
    other_cost:Decimal=Field(default=Decimal('0'),ge=0,max_digits=14,decimal_places=2)
    other_charge:Decimal=Field(default=Decimal('0'),ge=0,max_digits=14,decimal_places=2)
    service_charge:Decimal=Field(default=Decimal('0'),ge=0,max_digits=14,decimal_places=2)
    acquisition_freight:Decimal=Field(default=Decimal('0'),ge=0,max_digits=14,decimal_places=2)
    nonrecoverable_purchase_tax:Decimal=Field(default=Decimal('0'),ge=0,max_digits=14,decimal_places=2)
    margin_override_reason:str=Field(default='',max_length=2000)
    target_margin:Decimal=Field(default=Decimal('35'),ge=35,le=95,decimal_places=0)
    auto_target:bool=True
    expected_revision:int=Field(default=0,ge=0)

class ExportIn(BaseModel):
    expected_revision:int=Field(ge=1)
    number:str=Field(min_length=2,max_length=70)
    tax_rate:Decimal=Field(default=Decimal('0'),ge=0,le=100,max_digits=7,decimal_places=3)


def owner(user=Depends(m.current_user)):
    if user['role']!='owner':raise HTTPException(403,'Owner/admin access is required for parts, costs and pricing')
    return user


def part_payload(part):
    return {'id':part.id,'sku':part.sku,'description':part.description,'unit':part.unit,
            'supplier':part.supplier,'unit_cost':str(part.unit_cost),'unit_price':str(part.unit_price),
            'minimum_price_35':str(minimum_price(part.unit_cost,Decimal('35'))),
            'active':part.active,'revision':part.revision,'updated_at':part.updated_at.isoformat()}


def call(db,wo_id,lock=False):
    query=select(m.WorkOrder).where(m.WorkOrder.id==wo_id)
    if lock:query=query.with_for_update()
    row=db.scalar(query)
    if not row:raise HTTPException(404,'Call not found')
    return row


def recorded_costs(db,wo_id):
    entries=list(db.scalars(select(m.FieldTimeEntry).where(m.FieldTimeEntry.work_order_id==wo_id).order_by(m.FieldTimeEntry.id)))
    mileage=list(db.scalars(select(m.CallMileage).where(m.CallMileage.work_order_id==wo_id).order_by(m.CallMileage.technician_id)))
    blockers=[];hours=Decimal('0');labor=Decimal('0');miles=Decimal('0');vehicle=Decimal('0');evidence=[];overtime=Decimal('0')
    if db.scalar(select(m.ActiveTimeClock.technician_id).where(m.ActiveTimeClock.work_order_id==wo_id)):
        blockers.append('A call clock is still running')
    if not entries:blockers.append('No completed call time has been recorded')
    from .time_corrections import pending
    if pending(db,wo_id=wo_id):blockers.append('Time correction needs admin review')
    for entry in entries:
        approval=db.scalar(select(m.TimeApproval).where(m.TimeApproval.time_entry_id==entry.id))
        hours+=entry.hours
        if not approval:blockers.append('Call time needs office approval')
        else:
            labor+=approval.labor_cost;vehicle+=approval.mileage_cost;overtime+=approval.overtime_hours
        # Legacy mileage remains a cost but cannot also be charged as new call mileage.
        miles+=entry.mileage
        evidence.append([entry.id,str(entry.hours),str(entry.mileage),approval.id if approval else None,
                         str(approval.labor_cost) if approval else None,str(approval.mileage_cost) if approval else None,
                         str(approval.overtime_hours) if approval else None])
    for row in mileage:
        if row.end_odometer is None:blockers.append('Ending odometer is missing')
        if row.approved_cost is None:blockers.append('Call mileage needs office approval')
        miles+=row.miles
        if row.approved_cost is not None:vehicle+=row.approved_cost
        evidence.append([row.technician_id,row.revision,str(row.miles),str(row.approved_cost)])
    if not mileage and not any(entry.mileage>0 for entry in entries):
        blockers.append('Record call odometer readings, including matching readings for zero mileage')
    # Missing odometer readings must not make an unfinished call appear more profitable.
    from .call_field_notes import usage_rows
    for row in usage_rows(db,wo_id):
        evidence.append(['part',row.id,row.revision,row.part_id,str(row.quantity),row.description])
        from .operations_v110 import allocation_details
        evidence.append(['receipts',row.id,allocation_details(db,row.id)['evidence']])
    token=hashlib.sha256(dump(evidence).encode()).hexdigest()
    return {'labor_hours':str(hours),'overtime_hours':str(overtime),'labor_cost':str(money(labor)),'miles':str(miles),
            'mileage_cost':str(money(vehicle)),'odometer_recorded':bool(mileage),
            'blockers':list(dict.fromkeys(blockers)),'token':token}


def calculate(db,wo_id,data):
    w=call(db,wo_id);job=db.get(m.Job,w.job_id)
    costs=Decimal('0');price=Decimal('0');lines=[];parts=[];blockers=[]
    def add_line(description,quantity,unit_price):
        nonlocal price
        if quantity<=0:return
        subtotal=money(quantity*unit_price);price+=subtotal
        lines.append({'description':description,'quantity':str(quantity),'unit_price':str(unit_price),'subtotal':str(subtotal)})
    from .call_field_notes import usage_rows
    usages={row.id:row for row in usage_rows(db,wo_id)}
    if data.mode=='actual':
        supplied=[line.usage_id for line in data.parts if line.usage_id]
        if len(supplied)!=len(set(supplied)) or set(supplied)!=set(usages):blockers.append('Load all technician-reported parts into actual pricing')
        for line in data.parts:
            if line.usage_id and (line.usage_id not in usages or line.quantity!=usages[line.usage_id].quantity or line.part_id!=usages[line.usage_id].part_id):
                blockers.append('Reported part quantities changed; reload reported parts')
    for requested in data.parts:
        if requested.part_id:
            p=db.get(CatalogPart,requested.part_id)
            if not p or not p.active:raise HTTPException(422,'Part is missing or archived; refresh the catalog')
            if p.revision!=requested.catalog_revision:raise HTTPException(409,'Catalog cost changed; refresh catalog costs before calculating')
            description=p.description;unit_cost=p.unit_cost
            unit_price=requested.unit_price if requested.unit_price is not None else p.unit_price
            sku=p.sku;unit=p.unit
        else:
            description=requested.description.strip();unit_cost=requested.unit_cost
            unit_price=requested.unit_price if requested.unit_price is not None else minimum_price(unit_cost,data.target_margin)
            sku='';unit='each'
        extended_cost=money(unit_cost*requested.quantity)
        cost_basis='Catalog / declared cost'
        if data.mode=='actual' and requested.usage_id:
            from .operations_v110 import allocation_details
            allocation=allocation_details(db,requested.usage_id);blockers.extend(allocation['blockers'])
            if allocation['amount'] is not None:
                extended_cost=allocation['amount'];unit_cost=money(extended_cost/requested.quantity);cost_basis='Allocated purchase receipts (extended total governs)'
        costs+=extended_cost
        parts.append({'part_id':requested.part_id,'usage_id':requested.usage_id,'catalog_revision':requested.catalog_revision,
                      'sku':sku,'description':description,'unit':unit,'quantity':str(requested.quantity),
                      'cost_basis':cost_basis,'unit_cost':str(unit_cost),'unit_price':str(unit_price),'extended_cost':str(extended_cost)})
        add_line(description,requested.quantity,unit_price)
    parts_cost=costs
    recorded=None
    if data.mode=='actual':
        recorded=recorded_costs(db,wo_id);blockers.extend(recorded['blockers'])
        hours=Decimal(recorded['labor_hours']);labor=Decimal(recorded['labor_cost'])
        miles=Decimal(recorded['miles']);vehicle=Decimal(recorded['mileage_cost'])
    else:
        hours=data.labor_hours;labor=money(hours*data.labor_cost_per_hour)
        miles=data.miles;vehicle=money(miles*data.mileage_cost_per_mile)
        if hours>0 and data.labor_cost_per_hour==0:blockers.append('Enter an internal hourly labor cost')
        if miles>0 and data.mileage_cost_per_mile==0:blockers.append('Enter an internal vehicle cost per mile')
    acquisition=data.acquisition_freight+data.nonrecoverable_purchase_tax
    costs+=labor+vehicle+data.other_cost+acquisition
    from .profit_tracking import policy_for
    policy=policy_for(db,job.customer_id)
    minimum_call=Decimal(policy['values']['minimum_call_price'])
    overhead=money(hours*Decimal(policy['values']['overhead_per_hour']))
    overtime_hours=Decimal(recorded['overtime_hours']) if recorded and data.overtime_sell_per_hour is not None else Decimal('0')
    add_line('Call labor (including travel time)',hours-overtime_hours,data.labor_sell_per_hour)
    if overtime_hours:add_line('Overtime labor (including travel time)',overtime_hours,data.overtime_sell_per_hour)
    add_line('Call mileage',miles,data.mileage_sell_per_mile)
    if data.other_charge:add_line('Additional service materials / expenses',Decimal('1'),data.other_charge)
    if data.service_charge:add_line('Service call charge',Decimal('1'),data.service_charge)
    if len(lines)>150:raise HTTPException(422,'Too many invoice lines')
    costs=money(costs);price=money(price);margin_floor=minimum_price(costs,data.target_margin);floor=max(margin_floor,minimum_call)
    adjustment=max(Decimal('0'),floor-price) if data.auto_target else Decimal('0')
    if adjustment:
        if lines and lines[-1]['description']=='Service call charge':
            lines[-1]['unit_price']=str(Decimal(lines[-1]['unit_price'])+adjustment)
            lines[-1]['subtotal']=lines[-1]['unit_price']
            price+=adjustment
        else:add_line('Service call charge',Decimal('1'),adjustment)
        price=money(price)
    if max(costs,price,floor)>Decimal('999999999999.99'):raise HTTPException(422,'Call pricing exceeds the supported amount')
    if price<=0:blockers.append('Enter a positive customer selling price')
    margin=(price-costs)/price*100 if price>0 else None
    below=price<floor
    override_reason=data.margin_override_reason.strip()
    if override_reason and len(override_reason)<10:raise HTTPException(422,'Provide at least 10 characters explaining the margin exception')
    override=bool(below and override_reason and price>0 and price>=minimum_call)
    return {'work_order_id':wo_id,'job_number':job.job_number,'summary':w.summary,'mode':data.mode,
            'target_margin':str(data.target_margin),'auto_target':data.auto_target,'target_adjustment':str(adjustment),'parts':parts,'lines':lines,
            'acquisition_freight':str(data.acquisition_freight),'nonrecoverable_purchase_tax':str(data.nonrecoverable_purchase_tax),
            'allocated_overhead':str(overhead),'profit_after_allocated_overhead':str(money(price-costs-overhead)),
            'minimum_call_price':str(minimum_call),'policy_sources':policy['sources'],
            'margin_override':{'reason':override_reason,'approved':False} if override else None,
            'parts_cost':str(parts_cost),'labor_hours':str(hours),'labor_cost':str(labor),
            'miles':str(miles),'mileage_cost':str(vehicle),'other_cost':str(data.other_cost),
            'total_direct_cost':str(costs),'selling_subtotal':str(price),'gross_profit':str(money(price-costs)),
            'gross_margin_percent':str(money(margin)) if margin is not None else None,
            'minimum_selling_subtotal':str(floor),'shortfall':str(max(Decimal('0'),floor-price)),
            'below_target':below,'blockers':blockers,'ready_to_export':not blockers and (not below or override),
            'recorded':recorded,'basis':'Gross margin before overhead; sales tax excluded. Parts and other costs are entered by the owner.'}


def pricing_payload(row):
    return {'revision':row.revision,'input':json.loads(row.input_json),'pricing':json.loads(row.snapshot_json)}


def allowed_floor(snapshot):
    override=snapshot.get('margin_override')
    if override and override.get('approved'):
        return max(Decimal(snapshot.get('minimum_call_price','0')),Decimal(snapshot['selling_subtotal']))
    return Decimal(snapshot['minimum_selling_subtotal'])

def export_floor(snapshot):
    if snapshot['blockers']:raise HTTPException(409,'Resolve missing cost information before exporting: '+ '; '.join(snapshot['blockers']))
    if Decimal(snapshot['selling_subtotal'])<allowed_floor(snapshot):
        raise HTTPException(409,'Selling price is below the margin target; minimum pre-tax price is $'+snapshot['minimum_selling_subtotal'])


def validate_invoice_margin(db,inv):
    if inv.proposal_id:
        from .operations_v110 import GuardedQuote
        guard=db.get(GuardedQuote,inv.proposal_id)
        if guard and sum((line.subtotal for line in m.invoice_lines(db,inv)),Decimal('0'))<Decimal(json.loads(guard.snapshot_json)['approved_floor']):
            raise HTTPException(409,'Invoice price is below the reviewed additional-work / repair quote floor')
    linked=db.scalar(select(CallPricingExport).where(CallPricingExport.kind=='invoice',CallPricingExport.record_id==inv.id))
    if not linked:return
    snapshot=json.loads(linked.snapshot_json)
    net=sum((line.subtotal for line in m.invoice_lines(db,inv)),Decimal('0'))
    if net<allowed_floor(snapshot):
        raise HTTPException(409,'Invoice discount falls below the call margin target; revise the call pricing first')
    current=recorded_costs(db,linked.work_order_id)
    if current['blockers'] or current['token']!=snapshot['recorded']['token']:
        raise HTTPException(409,'Recorded call costs changed; save actual pricing and refresh this draft from Call pricing')


def validate_quote_margin(db,quote,options):
    from .operations_v110 import validate_quote
    validate_quote(db,quote,options)
    link=db.scalar(select(CallPricingExport).where(CallPricingExport.kind=='proposal',CallPricingExport.record_id==quote.id))
    if not link:return
    snapshot=json.loads(link.snapshot_json)
    floor=allowed_floor(snapshot)
    if any(option.price<floor for option in options):
        raise HTTPException(409,'Proposal option is below the call pricing target; revise Call pricing first')


def attach_call_pricing(app):
    m.Base.metadata.create_all(m.engine)
    @app.get('/call-pricing')
    def pricing_page(user=Depends(owner)):return FileResponse(m.ROOT/'call-pricing.html')
    @app.get('/call-pricing.js')
    def pricing_script(user=Depends(owner)):return FileResponse(m.ROOT/'call-pricing.js',media_type='application/javascript')

    @app.get('/api/parts-catalog')
    def catalog(user=Depends(owner)):
        with Session(m.engine) as db:return [part_payload(p) for p in db.scalars(select(CatalogPart).order_by(CatalogPart.sku))]

    @app.post('/api/parts-catalog',status_code=201)
    def create_part(data:PartIn,user=Depends(owner)):
        with Session(m.engine) as db:
            m.lock_time_user(db,user['id'])
            sku=data.sku.strip().upper()
            if not sku or not data.description.strip() or not data.unit.strip():raise HTTPException(422,'SKU and description cannot be blank')
            if db.scalar(select(CatalogPart.id).where(CatalogPart.sku==sku)):raise HTTPException(409,'Part code already exists')
            p=CatalogPart(sku=sku,description=data.description.strip(),unit=data.unit.strip(),supplier=data.supplier.strip(),
                unit_cost=data.unit_cost,unit_price=data.unit_price if data.unit_price is not None else minimum_price(data.unit_cost,Decimal('35')),active=data.active)
            db.add(p);db.flush();m.log(db,'CREATE_CATALOG_PART','catalog_part',p.id,sku);db.commit();return part_payload(p)

    @app.put('/api/parts-catalog/{part_id}')
    def edit_part(part_id:str,data:PartIn,user=Depends(owner)):
        with Session(m.engine) as db:
            m.lock_time_user(db,user['id'])
            p=db.scalar(select(CatalogPart).where(CatalogPart.id==part_id).with_for_update())
            if not p:raise HTTPException(404,'Part not found')
            if p.revision!=data.expected_revision:raise HTTPException(409,'Part changed; reload before saving')
            sku=data.sku.strip().upper()
            if not sku or not data.description.strip() or not data.unit.strip():raise HTTPException(422,'Enter part code, description and unit')
            if db.scalar(select(CatalogPart.id).where(CatalogPart.sku==sku,CatalogPart.id!=p.id)):raise HTTPException(409,'Part code already exists')
            before=part_payload(p)
            p.sku=sku;p.description=data.description.strip();p.unit=data.unit.strip();p.supplier=data.supplier.strip();p.active=data.active
            p.unit_cost=data.unit_cost;p.unit_price=data.unit_price if data.unit_price is not None else minimum_price(data.unit_cost,Decimal('35'))
            p.revision+=1;p.updated_at=now();db.flush()
            m.log(db,'EDIT_CATALOG_PART','catalog_part',p.id,dump({'before':before,'after':part_payload(p)}));db.commit();return part_payload(p)

    @app.get('/api/call-pricing/{wo_id}/recorded')
    def recorded(wo_id:str,user=Depends(owner)):
        with Session(m.engine) as db:call(db,wo_id);return recorded_costs(db,wo_id)

    @app.post('/api/call-pricing/{wo_id}/preview')
    def preview(wo_id:str,data:PricingIn,user=Depends(owner)):
        with Session(m.engine) as db:return calculate(db,wo_id,data)

    @app.get('/api/call-pricing/{wo_id}/{mode}')
    def get_pricing(wo_id:str,mode:Literal['estimate','actual'],user=Depends(owner)):
        with Session(m.engine) as db:
            call(db,wo_id)
            row=db.scalar(select(CallPricing).where(CallPricing.work_order_id==wo_id,CallPricing.mode==mode))
            return pricing_payload(row) if row else {'revision':0,'input':None,'pricing':None}

    @app.put('/api/call-pricing/{wo_id}/{mode}')
    def save_pricing(wo_id:str,mode:Literal['estimate','actual'],data:PricingIn,user=Depends(owner)):
        if data.mode!=mode:raise HTTPException(422,'Pricing mode mismatch')
        with Session(m.engine) as db:
            call(db,wo_id,lock=True)
            row=db.scalar(select(CallPricing).where(CallPricing.work_order_id==wo_id,CallPricing.mode==mode))
            if data.expected_revision!=(row.revision if row else 0):raise HTTPException(409,'Call pricing changed; reload before saving')
            snapshot=calculate(db,wo_id,data)
            if snapshot.get('margin_override'):
                if snapshot['blockers']:raise HTTPException(409,'Resolve missing costs before approving a margin exception')
                snapshot['margin_override'].update({'approved':True,'approved_by':user['id'],'approved_at':now().isoformat()})
                m.log(db,'APPROVE_MARGIN_EXCEPTION','work_order',wo_id,dump({'mode':mode,'reason':data.margin_override_reason,'price':snapshot['selling_subtotal'],'target':snapshot['target_margin']}))
            if not row:row=CallPricing(work_order_id=wo_id,mode=mode);db.add(row)
            else:row.revision+=1
            row.input_json=data.model_dump_json();row.snapshot_json=dump(snapshot);row.updated_at=now()
            db.flush();m.log(db,'SAVE_CALL_PRICING','work_order',wo_id,f'{mode}; revision {row.revision}');db.commit();return pricing_payload(row)

    @app.post('/api/call-pricing/{wo_id}/{kind}/export')
    def export_pricing(wo_id:str,kind:Literal['proposal','invoice'],data:ExportIn,user=Depends(owner)):
        mode='estimate' if kind=='proposal' else 'actual'
        with Session(m.engine) as db:
            w=call(db,wo_id,lock=True)
            row=db.scalar(select(CallPricing).where(CallPricing.work_order_id==wo_id,CallPricing.mode==mode))
            if not row or row.revision!=data.expected_revision:raise HTTPException(409,'Save and reload the current pricing revision before exporting')
            snapshot=json.loads(row.snapshot_json);export_floor(snapshot)
            if kind=='invoice':
                tech_ids=sorted(set(db.scalars(select(m.FieldTimeEntry.technician_id).where(m.FieldTimeEntry.work_order_id==wo_id))) | set(db.scalars(select(m.CallMileage.technician_id).where(m.CallMileage.work_order_id==wo_id))))
                for technician_id in tech_ids:m.lock_time_user(db,technician_id)
                current=recorded_costs(db,wo_id)
                if current['blockers'] or current['token']!=snapshot['recorded']['token']:
                    raise HTTPException(409,'Call costs changed; recalculate and save actual pricing before invoicing')
            existing=db.get(CallPricingExport,(wo_id,kind))
            if existing:
                model=m.InvoiceRecord if kind=='invoice' else m.Proposal
                record=db.scalar(select(model).where(model.id==existing.record_id).with_for_update())
                if existing.pricing_revision==row.revision:
                    return {'kind':kind,'id':record.id,'number':record.number,'status':record.status,'already_exported':True}
                if record.status!='Draft':raise HTTPException(409,'Export is already approved or issued; use the existing billing adjustment workflow')
                if record.number!=data.number:raise HTTPException(409,'Use the existing draft number to refresh its pricing')
            else:record=None
            if kind=='proposal':
                from .proposals_pm_v15 import QuoteDetail, add_revision
                if record is None:
                    if db.scalar(select(m.Proposal.id).where(m.Proposal.number==data.number)):raise HTTPException(409,'Proposal number exists')
                    record=m.Proposal(job_id=w.job_id,number=data.number,title=w.summary[:250],scope='Service call: '+w.summary,total=Decimal(snapshot['selling_subtotal']))
                    db.add(record);db.flush()
                    detail=QuoteDetail(proposal_id=record.id,options_json='[]')
                    db.add(detail)
                    from .quote_template import PresentationIn,save_presentation
                    save_presentation(db,record.id,PresentationIn(),[])
                else:
                    detail=db.get(QuoteDetail,record.id);detail.revision+=1
                    record.total=Decimal(snapshot['selling_subtotal'])
                detail.options_json=dump([{'name':'Service call','description':w.summary,'price':snapshot['selling_subtotal'],'estimated_cost':'0'}])
                db.flush();add_revision(db,record,detail)
            else:
                if record is None:
                    if db.scalar(select(m.InvoiceRecord.id).where(m.InvoiceRecord.number==data.number)):raise HTTPException(409,'Invoice number exists')
                    record=m.InvoiceRecord(job_id=w.job_id,number=data.number,amount=Decimal('0'))
                    db.add(record);db.flush()
                else:
                    for link in db.scalars(select(m.MileageInvoiceLink).where(m.MileageInvoiceLink.invoice_id==record.id)):db.delete(link)
                    db.flush()
                odometer=list(db.scalars(select(m.CallMileage).where(m.CallMileage.work_order_id==wo_id)))
                for item in odometer:
                    m.lock_time_user(db,item.technician_id);db.refresh(item)
                    link=db.get(m.MileageInvoiceLink,(wo_id,item.technician_id))
                    if link:raise HTTPException(409,'Call mileage has already been invoiced separately; review before creating a full-call invoice')
                items=[m.InvoiceLineInput(description=line['description'],quantity=line['quantity'],unit_price=line['unit_price'],tax_rate=data.tax_rate) for line in snapshot['lines']]
                net,tax=m.put_invoice_lines(db,record,items);record.amount=net+tax
                db.flush()
                mileage_line=next((line for line in m.invoice_lines(db,record) if line.description=='Call mileage'),None)
                if mileage_line:
                    for item in odometer:
                        if item.miles>0:db.add(m.MileageInvoiceLink(work_order_id=wo_id,technician_id=item.technician_id,invoice_id=record.id,invoice_line_id=mileage_line.id))
            if not existing:
                existing=CallPricingExport(work_order_id=wo_id,kind=kind,record_id=record.id,pricing_revision=row.revision,snapshot_json=dump(snapshot));db.add(existing)
            else:existing.pricing_revision=row.revision;existing.snapshot_json=dump(snapshot)
            m.log(db,'EXPORT_CALL_PRICING',kind,record.id,f'{wo_id}; {mode} revision {row.revision}')
            db.commit()
            return {'kind':kind,'id':record.id,'number':record.number,'status':record.status,'already_exported':False}
