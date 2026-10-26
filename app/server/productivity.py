"""Call workspace, defaults, reusable repair bundles, drafts and billing checklist."""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import String, Text, ForeignKey, DateTime, select, func
from sqlalchemy.orm import Mapped, mapped_column, Session
from . import main as m
from . import call_pricing as pricing
from .time_clock import as_utc, mileage_payload, active_payload

class PricingDefaults(m.Base):
    __tablename__='pricing_defaults'
    scope:Mapped[str]=mapped_column(String(50),primary_key=True)
    customer_id:Mapped[str|None]=mapped_column(ForeignKey('customers.id',ondelete='RESTRICT'),nullable=True)
    revision:Mapped[int]=mapped_column(nullable=False,default=1)
    values_json:Mapped[str]=mapped_column(Text,nullable=False)

class FavoritePart(m.Base):
    __tablename__='favorite_parts'
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),primary_key=True)
    part_id:Mapped[str]=mapped_column(ForeignKey('catalog_parts.id',ondelete='RESTRICT'),primary_key=True)

class RepairBundle(m.Base):
    __tablename__='repair_bundles'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    name:Mapped[str]=mapped_column(String(150),nullable=False)
    name_key:Mapped[str]=mapped_column(String(150),nullable=False,unique=True)
    parts_json:Mapped[str]=mapped_column(Text,nullable=False)
    labor_hours:Mapped[Decimal]=mapped_column(m.Numeric(8,3),nullable=False,default=0)
    active:Mapped[bool]=mapped_column(nullable=False,default=True)
    revision:Mapped[int]=mapped_column(nullable=False,default=1)

class PersonalDraft(m.Base):
    __tablename__='personal_drafts'
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),primary_key=True)
    work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),primary_key=True)
    kind:Mapped[str]=mapped_column(String(25),primary_key=True)
    revision:Mapped[int]=mapped_column(nullable=False,default=1)
    body_json:Mapped[str]=mapped_column(Text,nullable=False)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)

class CallChecklist(m.Base):
    __tablename__='call_checklists'
    work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),primary_key=True)
    parts_pricing_revision:Mapped[int]=mapped_column(nullable=False)
    reviewed_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    reviewed_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)

class QuickJobCounter(m.Base):
    __tablename__='quick_job_counter'
    key:Mapped[str]=mapped_column(String(20),primary_key=True)
    last_number:Mapped[str]=mapped_column(String(40),nullable=False)

class QuickCallReceipt(m.Base):
    __tablename__='quick_call_receipts'
    request_id:Mapped[str]=mapped_column(String(36),primary_key=True)
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    fingerprint:Mapped[str]=mapped_column(String(64),nullable=False)
    work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=False)

class DefaultsIn(BaseModel):
    labor_sell_per_hour:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
    mileage_sell_per_mile:Decimal|None=Field(default=None,ge=0,max_digits=10,decimal_places=2)
    labor_cost_per_hour:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
    mileage_cost_per_mile:Decimal|None=Field(default=None,ge=0,max_digits=10,decimal_places=3)
    service_charge:Decimal|None=Field(default=None,ge=0,max_digits=14,decimal_places=2)
    target_margin:int|None=Field(default=None,ge=35,le=95)
    expected_revision:int=Field(default=0,ge=0)

class FavoriteIn(BaseModel):
    favorite:bool

class BundlePartIn(BaseModel):
    part_id:str
    quantity:Decimal=Field(gt=0,le=1000000,decimal_places=3)

class BundleIn(BaseModel):
    name:str=Field(min_length=1,max_length=150)
    parts:list[BundlePartIn]=Field(min_length=1,max_length=100)
    labor_hours:Decimal=Field(default=Decimal('0'),ge=0,le=10000,decimal_places=3)
    active:bool=True
    expected_revision:int=Field(default=0,ge=0)

class DraftIn(BaseModel):
    body:dict
    expected_revision:int=Field(ge=0)

class ChecklistIn(BaseModel):
    expected_pricing_revision:int=Field(ge=1)

class NewCallCustomer(BaseModel):
    name:str=Field(min_length=1,max_length=250)
    billing_address:str=Field(default='',max_length=2000)
    contact:str=Field(default='',max_length=250)
    phone:str=Field(default='',max_length=60)
    email:str=Field(default='',max_length=250)
    @model_validator(mode='after')
    def valid_name(self):
        if not self.name.strip():raise ValueError('Enter the customer name')
        if self.email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',self.email):raise ValueError('Enter a valid billing email')
        return self

class NewCallSite(BaseModel):
    name:str=Field(min_length=2,max_length=250)
    address:str=Field(min_length=3,max_length=2000)
    @model_validator(mode='after')
    def valid_site(self):
        if len(self.name.strip())<2 or len(self.address.strip())<3:raise ValueError('Enter a site name and service address')
        return self

class QuickCallIn(BaseModel):
    request_id:uuid.UUID
    customer_id:str|None=None
    new_customer:NewCallCustomer|None=None
    new_site:NewCallSite|None=None
    location_id:str|None=None
    job_number:str|None=None
    summary:str=Field(min_length=3,max_length=2000)
    priority:Literal['Low','Normal','High','Emergency']='Normal'
    technician_id:str|None=None
    scheduled_start:datetime|None=None
    scheduled_end:datetime|None=None
    @model_validator(mode='after')
    def schedule(self):
        if bool(self.customer_id)==bool(self.new_customer):raise ValueError('Choose an existing customer or enter a new customer')
        if self.location_id and self.new_site:raise ValueError('Choose an existing site or enter a new site')
        if self.new_customer and (self.location_id or self.job_number):raise ValueError('A new customer cannot use another customer\'s site or job')
        if self.job_number and self.new_site:raise ValueError('An existing job must use its existing site')
        if self.technician_id or self.scheduled_start or self.scheduled_end:
            if not all((self.technician_id,self.scheduled_start,self.scheduled_end)):
                raise ValueError('Select a technician and both schedule times')
            if any(not v.tzinfo or v.utcoffset() is None for v in (self.scheduled_start,self.scheduled_end)):
                raise ValueError('Schedule requires a timezone offset')
            if self.scheduled_end<=self.scheduled_start:raise ValueError('End must follow start')
        if not self.summary.strip():raise ValueError('Enter a call description')
        return self


def access(db,wo_id,user):
    if user['role']!='technician' and user['role']!='owner' and 'manage_jobs' not in m.ROLE_SCOPES.get(user['role'],set()):
        raise HTTPException(403,'Call workspace access required')
    return m.permitted_wo(db,wo_id,user)


def defaults_for(db,customer_id=None):
    result={key:'0' for key in ('labor_sell_per_hour','mileage_sell_per_mile','labor_cost_per_hour','mileage_cost_per_mile','service_charge')}
    result['target_margin']=35
    global_row=db.get(PricingDefaults,'global')
    if global_row:result.update(json.loads(global_row.values_json))
    specific=db.get(PricingDefaults,customer_id) if customer_id else None
    if specific:result.update(json.loads(specific.values_json))
    row=specific if customer_id else global_row
    return {'values':result,'overrides':json.loads(row.values_json) if row else {},'revision':row.revision if row else 0,'customer_id':customer_id}


def bundle_payload(db,row):
    parts=[]
    for item in json.loads(row.parts_json):
        p=db.get(pricing.CatalogPart,item['part_id'])
        parts.append({'part_id':p.id,'catalog_revision':p.revision,'description':p.description,'sku':p.sku,
                      'unit_cost':str(p.unit_cost),'unit_price':str(p.unit_price),'quantity':item['quantity'],'active':p.active})
    return {'id':row.id,'name':row.name,'parts':parts,'labor_hours':str(row.labor_hours),'active':row.active,'revision':row.revision}


def draft_access(db,wo_id,kind,user):
    access(db,wo_id,user)
    if kind not in ('note','field-note') and user['role']!='owner':raise HTTPException(403,'Pricing drafts are admin-only')


def call_readiness(db,w):
    recorded=pricing.recorded_costs(db,w.id)
    blockers=list(recorded['blockers'])
    if w.status!='Completed':blockers.append('Call is not marked completed')
    row=db.scalar(select(pricing.CallPricing).where(pricing.CallPricing.work_order_id==w.id,pricing.CallPricing.mode=='actual'))
    checklist=db.get(CallChecklist,w.id)
    if not row:blockers.append('Enter actual parts / pricing')
    else:
        snapshot=json.loads(row.snapshot_json)
        if snapshot['recorded']['token']!=recorded['token']:blockers.append('Refresh pricing after recorded costs changed')
        if not snapshot['ready_to_export']:blockers.append('Resolve pricing warnings or margin shortfall')
        if not checklist or checklist.parts_pricing_revision!=row.revision:blockers.append('Review parts used, or confirm no parts were used')
    link=db.get(pricing.CallPricingExport,(w.id,'invoice'))
    inv=db.get(m.InvoiceRecord,link.record_id) if link else None
    if inv and inv.status!='Draft':stage='Invoiced'
    elif blockers:stage='Needs review'
    elif inv:stage='Draft invoice ready'
    else:stage='Ready to invoice'
    return {'work_order_id':w.id,'job_number':db.get(m.Job,w.job_id).job_number,'summary':w.summary,
            'status':w.status,'stage':stage,'blockers':list(dict.fromkeys(blockers)),
            'ready':not blockers,'parts_reviewed':bool(row and checklist and checklist.parts_pricing_revision==row.revision),
            'pricing_revision':row.revision if row else 0,'invoice_id':inv.id if inv else None,
            'invoice_number':inv.number if inv else None,'invoice_status':inv.status if inv else None}


def attach_productivity(app):
    m.Base.metadata.create_all(m.engine)
    with m.engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO quick_job_counter (key,last_number) VALUES ('jobs','0') ON CONFLICT(key) DO NOTHING")

    @app.get('/calls')
    def calls_page(user=Depends(m.current_user)):
        if user['role']!='owner' and 'manage_jobs' not in m.ROLE_SCOPES.get(user['role'],set()):raise HTTPException(403,'Office call access required')
        return FileResponse(m.ROOT/'calls.html',headers={'Cache-Control':'private, no-cache'})
    @app.get('/calls.js')
    def calls_script(user=Depends(m.current_user)):return FileResponse(m.ROOT/'calls.js',media_type='application/javascript',headers={'Cache-Control':'private, no-cache'})
    @app.get('/calls/{wo_id}')
    def call_page(wo_id:str,user=Depends(m.current_user)):
        with Session(m.engine) as db:access(db,wo_id,user)
        return FileResponse(m.ROOT/'call-workspace.html')
    @app.get('/call-workspace.js')
    def workspace_script(user=Depends(m.current_user)):return FileResponse(m.ROOT/'call-workspace.js',media_type='application/javascript')
    @app.get('/drafts.js')
    def draft_script(user=Depends(m.current_user)):return FileResponse(m.ROOT/'drafts.js',media_type='application/javascript')

    @app.get('/api/admin/pricing-defaults')
    def get_defaults(customer_id:str|None=None,user=Depends(pricing.owner)):
        with Session(m.engine) as db:
            if customer_id and not db.get(m.Customer,customer_id):raise HTTPException(404,'Customer not found')
            return defaults_for(db,customer_id)
    @app.get('/api/admin/calls/{wo_id}/pricing-defaults')
    def call_defaults(wo_id:str,user=Depends(pricing.owner)):
        with Session(m.engine) as db:
            w=pricing.call(db,wo_id);return defaults_for(db,db.get(m.Job,w.job_id).customer_id)

    @app.put('/api/admin/pricing-defaults')
    def save_defaults(data:DefaultsIn,customer_id:str|None=None,user=Depends(pricing.owner)):
        with Session(m.engine) as db:
            m.lock_time_user(db,user['id'])
            if customer_id and not db.get(m.Customer,customer_id):raise HTTPException(404,'Customer not found')
            key=customer_id or 'global';row=db.get(PricingDefaults,key)
            if data.expected_revision!=(row.revision if row else 0):raise HTTPException(409,'Defaults changed; reload before saving')
            values=data.model_dump(mode='json',exclude={'expected_revision'},exclude_none=True)
            if row:row.revision+=1
            else:row=PricingDefaults(scope=key,customer_id=customer_id);db.add(row)
            row.values_json=pricing.dump(values);db.flush();m.log(db,'SAVE_PRICING_DEFAULTS','pricing_defaults',key,row.values_json);db.commit()
            return defaults_for(db,customer_id)

    @app.get('/api/admin/favorite-parts')
    def favorites(user=Depends(pricing.owner)):
        with Session(m.engine) as db:return list(db.scalars(select(FavoritePart.part_id).where(FavoritePart.user_id==user['id'])))
    @app.put('/api/parts-catalog/{part_id}/favorite')
    def favorite(part_id:str,data:FavoriteIn,user=Depends(pricing.owner)):
        with Session(m.engine) as db:
            m.lock_time_user(db,user['id'])
            if not db.get(pricing.CatalogPart,part_id):raise HTTPException(404,'Part not found')
            row=db.get(FavoritePart,(user['id'],part_id))
            if data.favorite and not row:db.add(FavoritePart(user_id=user['id'],part_id=part_id))
            elif not data.favorite and row:db.delete(row)
            db.commit();return {'favorite':data.favorite}

    @app.get('/api/admin/repair-bundles')
    def bundles(user=Depends(pricing.owner)):
        with Session(m.engine) as db:return [bundle_payload(db,row) for row in db.scalars(select(RepairBundle).order_by(RepairBundle.name))]
    def write_bundle(db,data,bundle_id=None):
        row=db.get(RepairBundle,bundle_id) if bundle_id else None
        if bundle_id and not row:raise HTTPException(404,'Bundle not found')
        if data.expected_revision!=(row.revision if row else 0):raise HTTPException(409,'Bundle changed; reload before saving')
        name=data.name.strip()
        if not name:raise HTTPException(422,'Enter a bundle name')
        same=db.scalar(select(RepairBundle).where(RepairBundle.name_key==name.casefold()))
        if same and same.id!=bundle_id:raise HTTPException(409,'Bundle name already exists')
        for item in data.parts:
            part=db.get(pricing.CatalogPart,item.part_id)
            if not part or (data.active and not part.active):raise HTTPException(422,'Bundle parts must be active catalog items')
        if not row:row=RepairBundle();db.add(row)
        else:row.revision+=1
        row.name=name;row.name_key=name.casefold();row.parts_json=pricing.dump([item.model_dump(mode='json') for item in data.parts]);row.labor_hours=data.labor_hours;row.active=data.active
        db.flush();return row
    @app.post('/api/admin/repair-bundles',status_code=201)
    def create_bundle(data:BundleIn,user=Depends(pricing.owner)):
        with Session(m.engine) as db:
            m.lock_time_user(db,user['id']);row=write_bundle(db,data);m.log(db,'CREATE_REPAIR_BUNDLE','repair_bundle',row.id);db.commit();return bundle_payload(db,row)
    @app.put('/api/admin/repair-bundles/{bundle_id}')
    def edit_bundle(bundle_id:str,data:BundleIn,user=Depends(pricing.owner)):
        with Session(m.engine) as db:
            m.lock_time_user(db,user['id']);row=write_bundle(db,data,bundle_id);m.log(db,'EDIT_REPAIR_BUNDLE','repair_bundle',row.id);db.commit();return bundle_payload(db,row)

    @app.get('/api/drafts/{wo_id}/{kind}')
    def get_draft(wo_id:str,kind:Literal['note','field-note','pricing-estimate','pricing-actual'],user=Depends(m.current_user)):
        with Session(m.engine) as db:
            draft_access(db,wo_id,kind,user);row=db.get(PersonalDraft,(user['id'],wo_id,kind))
            return {'revision':row.revision,'body':json.loads(row.body_json),'updated_at':as_utc(row.updated_at).isoformat()} if row else {'revision':0,'body':{}}
    @app.put('/api/drafts/{wo_id}/{kind}')
    def save_draft(wo_id:str,kind:Literal['note','field-note','pricing-estimate','pricing-actual'],data:DraftIn,user=Depends(m.current_user)):
        encoded=pricing.dump(data.body)
        if len(encoded.encode())>65536:raise HTTPException(413,'Draft exceeds 64 KB')
        if kind=='note' and (set(data.body)-{'text'} or not isinstance(data.body.get('text',''),str) or len(data.body.get('text',''))>10000):raise HTTPException(422,'Invalid note draft')
        if kind=='field-note' and (set(data.body)-{'text','kind','audience'} or not isinstance(data.body.get('text',''),str) or len(data.body.get('text',''))>10000):raise HTTPException(422,'Invalid field note draft')
        with Session(m.engine) as db:
            draft_access(db,wo_id,kind,user);m.lock_time_user(db,user['id'])
            row=db.get(PersonalDraft,(user['id'],wo_id,kind))
            if data.expected_revision!=(row.revision if row else 0):raise HTTPException(409,'Draft changed in another window; reload to avoid overwriting it')
            if row:row.revision+=1
            else:row=PersonalDraft(user_id=user['id'],work_order_id=wo_id,kind=kind);db.add(row)
            row.body_json=encoded;row.updated_at=pricing.now();db.flush();db.commit()
            return {'revision':row.revision,'body':json.loads(row.body_json),'updated_at':as_utc(row.updated_at).isoformat()}

    @app.get('/api/calls/options')
    def call_options(user=Depends(m.require('manage_jobs'))):
        with Session(m.engine) as db:
            return {'can_create_customer':'manage_customers' in m.ROLE_SCOPES.get(user['role'],set()),
                    'can_create_site':user['role']=='owner',
                    'customers':[{'id':c.id,'name':c.name,'billing_address':c.billing_address} for c in db.scalars(select(m.Customer).order_by(m.Customer.name))],
                    'locations':[{'id':l.id,'customer_id':l.customer_id,'name':l.name,'address':l.address} for l in db.scalars(select(m.Location))],
                    'jobs':[{'job_number':j.job_number,'customer_id':j.customer_id,'location_id':j.location_id,'description':j.description} for j in db.scalars(select(m.Job))],
                    'technicians':[{'id':u.id,'name':u.display_name} for u in db.scalars(select(m.User).where(m.User.role=='technician',m.User.active==True))]}

    @app.post('/api/calls/quick',status_code=201)
    def quick_call(data:QuickCallIn,user=Depends(m.require('manage_jobs'))):
        body=data.model_dump_json();fingerprint=hashlib.sha256(body.encode()).hexdigest()
        with Session(m.engine) as db:
            counter=db.scalar(select(QuickJobCounter).where(QuickJobCounter.key=='jobs').with_for_update())
            receipt=db.get(QuickCallReceipt,str(data.request_id))
            if receipt:
                if receipt.user_id!=user['id'] or receipt.fingerprint!=fingerprint:raise HTTPException(409,'Creation request changed; start a new call request')
                return m.work_order_payload(db.get(m.WorkOrder,receipt.work_order_id),db)
            if data.new_customer:
                if 'manage_customers' not in m.ROLE_SCOPES.get(user['role'],set()):raise HTTPException(403,'Customer management access required')
                values={k:v.strip() for k,v in data.new_customer.model_dump().items()}
                if db.scalar(select(m.Customer.id).where(func.lower(func.trim(m.Customer.name))==values['name'].lower())):
                    raise HTTPException(409,'A customer with this name already exists. Select that customer above.')
                customer=m.Customer(**values);db.add(customer);db.flush()
                m.log(db,'CREATE_CUSTOMER','customer',customer.id,'Created with service call')
            else:customer=db.get(m.Customer,data.customer_id)
            if not customer:raise HTTPException(422,'Select an existing customer')
            location=db.get(m.Location,data.location_id) if data.location_id else None
            if data.location_id and (not location or location.customer_id!=customer.id):raise HTTPException(422,'Location must belong to the selected customer')
            if data.new_site:
                if user['role']!='owner':raise HTTPException(403,'Owner access required to add a service site')
                if db.scalar(select(m.Location.id).where(m.Location.customer_id==customer.id,func.lower(func.trim(m.Location.name))==data.new_site.name.strip().lower())):
                    raise HTTPException(409,'This customer already has a site with that name. Select the existing site.')
                location=m.Location(customer_id=customer.id,name=data.new_site.name.strip(),address=data.new_site.address.strip())
                db.add(location);db.flush();m.log(db,'CREATE_SERVICE_SITE','location',location.id,'Created with service call')
            location_id=location.id if location else None
            if data.job_number:
                job=db.scalar(select(m.Job).where(m.Job.job_number==data.job_number))
                if not job or job.customer_id!=customer.id or job.location_id!=location_id:raise HTTPException(422,'Existing job must match customer and location')
            else:
                numbers=[int(n[3:]) for n in db.scalars(select(m.Job.job_number)) if re.fullmatch(r'LMS\d+',n)]
                number=max([int(counter.last_number),*numbers])+1;counter.last_number=str(number)
                job=m.Job(job_number='LMS'+str(number),customer_id=customer.id,location_id=location_id,description=data.summary)
                db.add(job);db.flush()
            w=m.WorkOrder(job_id=job.id,summary=data.summary.strip(),priority=data.priority)
            db.add(w);db.flush()
            if data.technician_id:
                m.lock_time_user(db,data.technician_id)
                tech=db.get(m.User,data.technician_id)
                if not tech or tech.role!='technician' or not tech.active:raise HTTPException(422,'Select an active technician')
                overlap=db.scalar(select(m.DispatchAssignment.id).where(m.DispatchAssignment.technician_id==tech.id,m.DispatchAssignment.status!='Cancelled',m.DispatchAssignment.scheduled_start<data.scheduled_end,m.DispatchAssignment.scheduled_end>data.scheduled_start))
                if overlap:raise HTTPException(409,'Technician has an overlapping assignment')
                db.add(m.DispatchAssignment(work_order_id=w.id,technician_id=tech.id,scheduled_start=data.scheduled_start,scheduled_end=data.scheduled_end))
                w.status='Scheduled'
            db.add(QuickCallReceipt(request_id=str(data.request_id),user_id=user['id'],fingerprint=fingerprint,work_order_id=w.id))
            m.log(db,'QUICK_CREATE_CALL','work_order',w.id,job.job_number);db.commit();return m.work_order_payload(w,db)

    @app.get('/api/calls/{wo_id}/workspace')
    def workspace(wo_id:str,user=Depends(m.current_user)):
        with Session(m.engine) as db:
            w=access(db,wo_id,user);job=db.get(m.Job,w.job_id);customer=db.get(m.Customer,job.customer_id);location=db.get(m.Location,job.location_id) if job.location_id else None
            times=list(db.scalars(select(m.FieldTimeEntry).where(m.FieldTimeEntry.work_order_id==w.id).order_by(m.FieldTimeEntry.started_at)))
            if user['role']=='technician':times=[t for t in times if t.technician_id==user['id']]
            miles=list(db.scalars(select(m.CallMileage).where(m.CallMileage.work_order_id==w.id)))
            if user['role']=='technician':miles=[r for r in miles if r.technician_id==user['id']]
            assets=db.scalars(select(m.Equipment).join(m.WorkOrderEquipment,m.Equipment.id==m.WorkOrderEquipment.equipment_id).where(m.WorkOrderEquipment.work_order_id==w.id))
            notes=db.scalars(select(m.ServiceNote).where(m.ServiceNote.work_order_id==w.id).order_by(m.ServiceNote.created_at.desc()))
            active=db.get(m.ActiveTimeClock,user['id']) if user['role']=='technician' else None
            result={'call':m.work_order_payload(w,db),'role':user['role'],'customer':{'id':customer.id,'name':customer.name,'contact':customer.contact,'phone':customer.phone},
                    'location':{'name':location.name,'address':location.address} if location else None,
                    'equipment':[{'id':a.id,'asset_tag':a.asset_tag,'manufacturer':a.manufacturer,'model':a.model,'serial':a.serial} for a in assets],
                    'notes':[{'id':n.id,'author':db.get(m.User,n.technician_id).display_name,'note':n.note,'created_at':as_utc(n.created_at).isoformat()} for n in notes],
                    'time':[m.time_entry_payload(db,t) for t in times],
                    'mileage':[mileage_payload(db,r) for r in miles],
                    'active_clock':active_payload(db,active),'server_now':pricing.now().isoformat()}
            if location:
                from .operations_v110 import SiteMetadata
                meta=db.get(SiteMetadata,location.id)
                if meta:result['location'].update({'contact':meta.contact,'phone':meta.phone})
            if user['role']=='owner':result['readiness']=call_readiness(db,w)
            return result

    @app.get('/api/admin/billing-queue')
    def billing_queue(user=Depends(pricing.owner)):
        with Session(m.engine) as db:
            return [call_readiness(db,w) for w in db.scalars(select(m.WorkOrder).where(m.WorkOrder.status=='Completed').order_by(m.WorkOrder.created_at)) if not (db.get(pricing.CallPricingExport,(w.id,'invoice')) and db.get(m.InvoiceRecord,db.get(pricing.CallPricingExport,(w.id,'invoice')).record_id).status!='Draft')]
    @app.put('/api/admin/calls/{wo_id}/parts-review')
    def parts_review(wo_id:str,data:ChecklistIn,user=Depends(pricing.owner)):
        with Session(m.engine) as db:
            w=pricing.call(db,wo_id,lock=True);row=db.scalar(select(pricing.CallPricing).where(pricing.CallPricing.work_order_id==w.id,pricing.CallPricing.mode=='actual'))
            if not row or row.revision!=data.expected_pricing_revision:raise HTTPException(409,'Save the current actual pricing before confirming parts')
            checklist=db.get(CallChecklist,w.id)
            if not checklist:checklist=CallChecklist(work_order_id=w.id);db.add(checklist)
            checklist.parts_pricing_revision=row.revision;checklist.reviewed_by=user['id'];checklist.reviewed_at=pricing.now()
            m.log(db,'CONFIRM_CALL_PARTS','work_order',w.id,str(row.revision));db.commit();return call_readiness(db,w)
