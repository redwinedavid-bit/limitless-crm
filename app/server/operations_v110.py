"""Private testing: field shortcuts and owner review tools; no external sending."""
import csv
import hashlib
import io
import json
import uuid
from datetime import date,datetime,time,timedelta,timezone
from decimal import Decimal,ROUND_HALF_UP
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import Depends,HTTPException
from fastapi.responses import FileResponse,StreamingResponse
from pydantic import BaseModel,Field,model_validator
from sqlalchemy import String,Text,ForeignKey,DateTime,Numeric,select
from sqlalchemy.orm import Mapped,mapped_column,Session
from . import main as m,call_pricing as p,profit_tracking as profit
from .productivity import access,call_readiness

CHECKLISTS={
 'service':('General service',['Confirm customer concern','Inspect serviced equipment','Verify operation after service','Document findings and recommendations']),
 'heat_pump':('Heat pump / cooling',['Inspect filters and coil condition','Check electrical connections and operating readings','Check drains and safeties','Verify heating/cooling operation']),
 'gas_furnace':('Gas furnace',['Inspect venting and combustion-air path','Inspect ignition and flame operation','Record applicable combustion / safety checks','Verify heating operation']),
 'refrigeration':('Refrigeration',['Record box temperature and customer concern','Inspect evaporator/condenser condition','Record operating readings and defrost checks','Verify temperature response']),
 'maintenance':('Preventive maintenance',['Complete equipment-specific maintenance scope','Record condition and deficiencies','Verify drains, safeties and operation','Document repairs recommended'])}

class Checklist(m.Base):
 __tablename__='service_checklists_v110'
 work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),primary_key=True)
 equipment_id:Mapped[str]=mapped_column(ForeignKey('equipment.id',ondelete='RESTRICT'),primary_key=True)
 template:Mapped[str]=mapped_column(String(30),nullable=False)
 revision:Mapped[int]=mapped_column(nullable=False,default=1)
 snapshot_json:Mapped[str]=mapped_column(Text,nullable=False)
 updated_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)

class Warranty(m.Base):
 __tablename__='equipment_warranties_v110'
 id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
 equipment_id:Mapped[str]=mapped_column(ForeignKey('equipment.id',ondelete='RESTRICT'),nullable=False,index=True)
 kind:Mapped[str]=mapped_column(String(30),nullable=False)
 provider:Mapped[str]=mapped_column(String(200),nullable=False)
 starts_on:Mapped[date]=mapped_column(m.Date,nullable=False)
 expires_on:Mapped[date]=mapped_column(m.Date,nullable=False)
 coverage:Mapped[str]=mapped_column(Text,nullable=False)
 reference:Mapped[str]=mapped_column(String(200),nullable=False,default='')
 active:Mapped[bool]=mapped_column(nullable=False,default=True)
 revision:Mapped[int]=mapped_column(nullable=False,default=1)

class GuardedQuote(m.Base):
 __tablename__='guarded_service_quotes_v110'
 proposal_id:Mapped[str]=mapped_column(ForeignKey('proposals.id',ondelete='RESTRICT'),primary_key=True)
 work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
 kind:Mapped[str]=mapped_column(String(25),nullable=False)
 request_id:Mapped[str]=mapped_column(String(36),nullable=False,unique=True)
 fingerprint:Mapped[str]=mapped_column(String(64),nullable=False)
 snapshot_json:Mapped[str]=mapped_column(Text,nullable=False)

class ReceiptAllocation(m.Base):
 __tablename__='call_receipt_allocations_v110'
 usage_id:Mapped[str]=mapped_column(ForeignKey('call_parts_used.id',ondelete='RESTRICT'),primary_key=True)
 receipt_id:Mapped[str]=mapped_column(ForeignKey('po_receipts.id',ondelete='RESTRICT'),primary_key=True)
 amount:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
 revision:Mapped[int]=mapped_column(nullable=False,default=1)
 note:Mapped[str]=mapped_column(Text,nullable=False)

class InboxState(m.Base):
 __tablename__='admin_inbox_state_v110'
 user_id:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),primary_key=True)
 item_key:Mapped[str]=mapped_column(String(200),primary_key=True)
 snoozed_until:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)

class SiteMetadata(m.Base):
 __tablename__='service_site_metadata_v110'
 location_id:Mapped[str]=mapped_column(ForeignKey('locations.id',ondelete='RESTRICT'),primary_key=True)
 revision:Mapped[int]=mapped_column(nullable=False,default=1)
 contact:Mapped[str]=mapped_column(String(200),nullable=False,default='')
 phone:Mapped[str]=mapped_column(String(60),nullable=False,default='')

class BillingRevision(m.Base):
 __tablename__='customer_billing_revisions_v110'
 customer_id:Mapped[str]=mapped_column(ForeignKey('customers.id',ondelete='RESTRICT'),primary_key=True)
 revision:Mapped[int]=mapped_column(nullable=False,default=1)

class SiteIn(BaseModel):
 name:str=Field(min_length=2,max_length=250)
 address:str=Field(min_length=3,max_length=2000)
 contact:str=Field(default='',max_length=200)
 phone:str=Field(default='',max_length=60)
 expected_revision:int=Field(default=0,ge=0)
 @model_validator(mode='after')
 def not_blank(self):
  if not self.name.strip() or not self.address.strip():raise ValueError('Enter a site name and service address')
  return self

class BillingIn(BaseModel):
 billing_address:str=Field(min_length=3,max_length=2000)
 contact:str=Field(default='',max_length=250)
 phone:str=Field(default='',max_length=60)
 email:str=Field(default='',max_length=250)
 expected_revision:int=Field(default=0,ge=0)

class TeamProfile(m.Base):
 __tablename__='team_profiles_v110'
 user_id:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),primary_key=True)
 google_email:Mapped[str]=mapped_column(String(250),nullable=False,unique=True)
 revision:Mapped[int]=mapped_column(nullable=False,default=1)

class TeamIn(BaseModel):
 username:str=Field(min_length=3,max_length=120,pattern=r'^[A-Za-z0-9_.@-]+$')
 display_name:str=Field(min_length=2,max_length=160)
 role:Literal['admin','technician']
 google_email:str=Field(min_length=5,max_length=250,pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
 password:str=Field(min_length=12,max_length=128)

class TeamActiveIn(BaseModel):
 active:bool
 expected_revision:int=Field(default=0,ge=0)

class ChecklistIn(BaseModel):
 equipment_id:str
 template:Literal['service','heat_pump','gas_furnace','refrigeration','maintenance']='service'
 answers:dict[str,Literal['Pass','Needs attention','Not applicable','Not checked']]
 note:str=Field(default='',max_length=2000)
 expected_revision:int=Field(default=0,ge=0)

class WarrantyIn(BaseModel):
 equipment_id:str
 kind:Literal['Manufacturer','Labor','Repair']='Manufacturer'
 provider:str=Field(min_length=2,max_length=200)
 starts_on:date
 expires_on:date
 coverage:str=Field(min_length=3,max_length=3000)
 reference:str=Field(default='',max_length=200)
 active:bool=True
 expected_revision:int=Field(default=0,ge=0)
 @model_validator(mode='after')
 def ordered(self):
  if self.expires_on<self.starts_on:raise ValueError('Warranty expiry must be on or after its start')
  if not self.provider.strip() or not self.coverage.strip():raise ValueError('Enter provider and coverage')
  return self

class ServiceQuoteIn(BaseModel):
 request_id:uuid.UUID
 number:str=Field(min_length=2,max_length=70)
 title:str=Field(min_length=3,max_length=250)
 customer_scope:str=Field(default='',max_length=10000)
 estimated_cost:Decimal=Field(ge=0,max_digits=14,decimal_places=2)
 target_margin:int=Field(default=35,ge=35,le=95)
 selling_price:Decimal|None=Field(default=None,gt=0,max_digits=14,decimal_places=2)
 exception_reason:str=Field(default='',max_length=2000)
 source_note_id:str|None=None

class ApprovalIn(BaseModel):
 expected_revision:int=Field(ge=1)
 accepted_by:str=Field(min_length=2,max_length=200)
 method:Literal['In person','Phone','Email']='In person'
 reference:str=Field(min_length=3,max_length=250)
 customer_authorization_confirmed:bool=False

class AllocationIn(BaseModel):
 receipt_id:str
 amount:Decimal=Field(ge=0,max_digits=14,decimal_places=2)
 note:str=Field(min_length=3,max_length=2000)
 expected_revision:int=Field(default=0,ge=0)

class SnoozeIn(BaseModel):
 hours:int=Field(default=24,ge=1,le=168)


def warranty_payload(row):
 today=profit.stamp().date();status='Inactive' if not row.active else 'Upcoming' if today<row.starts_on else 'Expired' if today>row.expires_on else 'Active'
 return {'id':row.id,'equipment_id':row.equipment_id,'asset_tag':None,'kind':row.kind,'provider':row.provider,'starts_on':row.starts_on.isoformat(),'expires_on':row.expires_on.isoformat(),'coverage':row.coverage,'reference':row.reference,'active':row.active,'status':status,'revision':row.revision}


def warranties_for(db,wo_id):
 assets=list(db.scalars(select(m.WorkOrderEquipment.equipment_id).where(m.WorkOrderEquipment.work_order_id==wo_id)))
 result=[]
 for row in db.scalars(select(Warranty).where(Warranty.equipment_id.in_(assets)).order_by(Warranty.expires_on)):
  data=warranty_payload(row);data['asset_tag']=db.get(m.Equipment,row.equipment_id).asset_tag;result.append(data)
 return result


def validate_quote(db,quote,options):
 guard=db.get(GuardedQuote,quote.id)
 if not guard:return
 s=json.loads(guard.snapshot_json)
 if any(o.price<Decimal(s['approved_floor']) for o in options):raise HTTPException(409,'Price falls below the reviewed service quote floor; create a newly reviewed quote')


def allocation_details(db,usage_id):
 from .purchasing_v13 import details
 allocations=list(db.scalars(select(ReceiptAllocation).where(ReceiptAllocation.usage_id==usage_id)));total=Decimal('0');errors=[];evidence=[]
 for row in allocations:
  if row.amount==0:continue
  receipt=db.get(m.POReceipt,row.receipt_id);po=db.get(m.PurchaseOrder,receipt.po_id)
  receipt_total=sum((x.amount for x in db.scalars(select(ReceiptAllocation).where(ReceiptAllocation.receipt_id==receipt.id))),Decimal('0'))
  po_total=sum((x.amount for x in db.scalars(select(ReceiptAllocation).join(m.POReceipt,ReceiptAllocation.receipt_id==m.POReceipt.id).where(m.POReceipt.po_id==po.id))),Decimal('0'))
  actual=details(db,po)['actual']
  if receipt_total>receipt.amount or po_total>actual:errors.append('Purchasing credits/returns changed the available receipt cost; review allocations')
  total+=row.amount;evidence.append([row.receipt_id,str(row.amount),row.revision,str(receipt.amount),str(actual),str(po_total)])
 return {'amount':total if allocations and total else None,'blockers':list(dict.fromkeys(errors)),'evidence':evidence}


def reconciliation(db,wo_id):
 from .call_field_notes import usage_rows
 w=p.call(db,wo_id);actual=db.scalar(select(p.CallPricing).where(p.CallPricing.work_order_id==wo_id,p.CallPricing.mode=='actual'))
 snapshot=json.loads(actual.snapshot_json) if actual else {};saved={line.get('usage_id'):line for line in snapshot.get('parts',[]) if line.get('usage_id')}
 result=[]
 for used in usage_rows(db,wo_id):
  info=allocation_details(db,used.id);line=saved.get(used.id);priced=Decimal(line['extended_cost']) if line and Decimal(line['quantity'])==used.quantity else None
  allocations=[]
  for a in db.scalars(select(ReceiptAllocation).where(ReceiptAllocation.usage_id==used.id)):
   receipt=db.get(m.POReceipt,a.receipt_id);po=db.get(m.PurchaseOrder,receipt.po_id)
   allocations.append({'receipt_id':receipt.id,'po_number':po.po_number,'reference':receipt.reference,'amount':str(a.amount),'revision':a.revision,'note':a.note})
  status='Needs receipt match' if info['amount'] is None else 'Purchasing changed' if info['blockers'] else 'Save actual pricing with receipt costs' if priced!=info['amount'] else 'Matched'
  result.append({'usage_id':used.id,'description':used.description,'quantity':str(used.quantity),'priced_cost':str(priced) if priced is not None else None,'receipt_cost':str(info['amount']) if info['amount'] is not None else None,'variance':str(p.money(info['amount']-priced)) if priced is not None and info['amount'] is not None else None,'status':status,'blockers':info['blockers'],'allocations':allocations})
 receipts=[]
 for r in db.scalars(select(m.POReceipt).join(m.PurchaseOrder,m.POReceipt.po_id==m.PurchaseOrder.id).where(m.PurchaseOrder.job_id==w.job_id,m.PurchaseOrder.source!='historical')):
  po=db.get(m.PurchaseOrder,r.po_id);allocated=sum((x.amount for x in db.scalars(select(ReceiptAllocation).where(ReceiptAllocation.receipt_id==r.id))),Decimal('0'))
  receipts.append({'id':r.id,'po_number':po.po_number,'reference':r.reference,'amount':str(r.amount),'allocated':str(allocated),'remaining':str(r.amount-allocated)})
 return {'parts':result,'receipts':receipts,'basis':'Receipt allocations replace the direct cost of their reported part line when actual pricing is recalculated. They are not an additional expense. PO totals remain job-scoped and are not added again to per-call profit.'}


def payroll(db,week_start):
 if week_start.weekday()!=0:raise HTTPException(422,'Select a Monday as the payroll week start')
 zone=ZoneInfo('America/New_York');start=datetime.combine(week_start,time.min,zone).astimezone(timezone.utc);end=datetime.combine(week_start+timedelta(days=7),time.min,zone).astimezone(timezone.utc)
 rows=[];blockers=[];warnings=[]
 from .time_corrections import pending
 for e in db.scalars(select(m.FieldTimeEntry).where(m.FieldTimeEntry.started_at<end,m.FieldTimeEntry.ended_at>start).order_by(m.FieldTimeEntry.started_at)):
  a=db.scalar(select(m.TimeApproval).where(m.TimeApproval.time_entry_id==e.id));w=db.get(m.WorkOrder,e.work_order_id);job=db.get(m.Job,w.job_id);tech=db.get(m.User,e.technician_id)
  if not a or pending(db,entry_id=e.id):blockers.append({'entry_id':e.id,'technician':tech.display_name,'reason':'Approve time / review pending correction before exporting'});continue
  begin=profit.utc(e.started_at);finish=profit.utc(e.ended_at);clip_start=max(begin,start);clip_end=min(finish,end);duration=Decimal(str((finish-begin).total_seconds()))
  if duration<=0:blockers.append({'entry_id':e.id,'reason':'Time entry has no positive duration'});continue
  portion=Decimal(str((clip_end-clip_start).total_seconds()))/duration
  hours=p.money(e.hours*portion)
  # Overtime is allocated to the final approved portion of this entry; no automatic legal classification.
  ot_seconds=duration*a.overtime_hours/e.hours if e.hours else Decimal('0');ot_start=finish-timedelta(seconds=float(ot_seconds));overlap=max(0,(clip_end-max(clip_start,ot_start)).total_seconds())
  ot=min(hours,p.money(a.overtime_hours*Decimal(str(overlap))/ot_seconds)) if ot_seconds else Decimal('0');regular=hours-ot
  if clip_start!=begin or clip_end!=finish:warnings.append({'entry_id':e.id,'reason':'Entry spans payroll weeks; confirm allocation. Approved overtime is assigned to the end of the entry.'})
  if e.hours>24:warnings.append({'entry_id':e.id,'reason':'Entry exceeds 24 hours; confirm the reviewed time.'})
  rows.append({'entry_id':e.id,'technician_id':e.technician_id,'technician':tech.display_name,'job_number':job.job_number,'call':w.summary,'start_local':clip_start.astimezone(zone).isoformat(),'end_local':clip_end.astimezone(zone).isoformat(),'hours':str(hours),'regular_hours':str(regular),'overtime_hours':str(ot)})
 from .time_corrections import TimeCorrection
 for correction in db.scalars(select(TimeCorrection).where(TimeCorrection.status=='Pending',TimeCorrection.started_at<end,TimeCorrection.ended_at>start)):
  if not any(b['entry_id']==correction.entry_id for b in blockers):blockers.append({'entry_id':correction.entry_id or correction.id,'reason':'A proposed time correction affects this payroll week; review it before exporting'})
 for active in db.scalars(select(m.ActiveTimeClock).where(m.ActiveTimeClock.started_at<end)):
  if profit.utc(active.started_at)<end and profit.stamp()>start:blockers.append({'entry_id':active.id,'reason':'A running clock may contain time in this payroll week; stop and review it first'})
 return {'week_start':week_start.isoformat(),'week_end':(week_start+timedelta(days=6)).isoformat(),'timezone':'America/New_York','rows':rows,'blockers':blockers,'warnings':warnings,'total_hours':str(sum((Decimal(r['hours']) for r in rows),Decimal('0'))),'regular_hours':str(sum((Decimal(r['regular_hours']) for r in rows),Decimal('0'))),'overtime_hours':str(sum((Decimal(r['overtime_hours']) for r in rows),Decimal('0'))),'basis':'Approved hours only; no breaks deducted. Regular/overtime classifications come from admin approval. Cross-week entries allocate approved overtime to the end of the entry and require explicit warning review. Export contains hours, not net pay or tax withholding.'}


def inbox(db,user):
 items=[];today=profit.stamp().date()
 def add(key,title,url,detail=''):
  state=db.get(InboxState,(user['id'],key))
  if not state or profit.utc(state.snoozed_until)<=profit.stamp():items.append({'key':key,'title':title,'detail':detail,'url':url})
 for f in db.scalars(select(profit.CustomerFollowUp).where(profit.CustomerFollowUp.status.in_(['Open','Scheduled']),profit.CustomerFollowUp.due_on<=today)):
  add('follow:'+f.id+':'+str(f.revision),'Follow-up due: '+db.get(m.Customer,f.customer_id).name,'/profitability?customer='+f.customer_id,f.text[:250])
 for e in db.scalars(select(m.FieldTimeEntry).outerjoin(m.TimeApproval,m.TimeApproval.time_entry_id==m.FieldTimeEntry.id).where(m.TimeApproval.id==None)):
  add('time:'+e.id,'Time awaiting review','/time-review',db.get(m.User,e.technician_id).display_name)
 from .time_corrections import TimeCorrection
 for correction in db.scalars(select(TimeCorrection).where(TimeCorrection.status=='Pending')):
  add('correction:'+correction.id,'Time correction awaiting review','/time-review',correction.reason[:250])
 for guard in db.scalars(select(GuardedQuote).where(GuardedQuote.kind=='Additional work')):
  q=db.get(m.Proposal,guard.proposal_id)
  if q.status=='Approved' and not db.scalar(select(m.InvoiceRecord.id).where(m.InvoiceRecord.proposal_id==q.id,m.InvoiceRecord.status!='Draft')):
   add('additional:'+q.id,'Approved additional work needs billing review','/operations',q.number+' · '+q.title)
 for c in db.scalars(select(m.WorkOrder).where(m.WorkOrder.status=='Completed')):
  ready=call_readiness(db,c)
  if ready['stage']!='Invoiced':add('billing:'+c.id,'Completed call awaiting billing','/calls/'+c.id,c.summary)
 for system in profit.portfolio(db)['flagged_systems']:
  add('system:'+system['equipment_id']+':'+str(system['service_count'])+':'+str(system['callback_count']),'System needs attention: '+system['asset_tag'],'/profitability?customer='+system['customer_id'],'; '.join(system['reasons']))
 return {'items':items,'count':len(items),'basis':'Private owner inbox. Conditions refresh when this inbox is opened or refreshed; no customer email, SMS or push is sent.'}


def attach_operations(app):
 m.Base.metadata.create_all(m.engine)
 @app.get('/operations')
 def page(user=Depends(p.owner)):return FileResponse(m.ROOT/'operations.html')
 @app.get('/operations.js')
 def script(user=Depends(p.owner)):return FileResponse(m.ROOT/'operations.js',media_type='application/javascript')
 @app.get('/field-shortcuts.js')
 def field_script(user=Depends(m.current_user)):return FileResponse(m.ROOT/'field-shortcuts.js',media_type='application/javascript')
 @app.get('/api/admin/team')
 def team(user=Depends(m.require('manage_users'))):
  with Session(m.engine) as db:
   result=[]
   for u in db.scalars(select(m.User).order_by(m.User.display_name)):
    meta=db.get(TeamProfile,u.id);result.append({'id':u.id,'username':u.username,'display_name':u.display_name,'role':'admin' if u.role=='owner' else u.role,'active':u.active,'google_email':meta.google_email if meta else '', 'revision':meta.revision if meta else 0,'is_you':u.id==user['id']})
   return result
 @app.post('/api/admin/team',status_code=201)
 def create_team_member(data:TeamIn,user=Depends(m.require('manage_users'))):
  from .security import password_hash
  with Session(m.engine) as db:
   m.lock_time_user(db,user['id']);username=data.username.lower();email=data.google_email.lower()
   if not data.display_name.strip():raise HTTPException(422,'Enter an employee display name')
   if db.scalar(select(m.User.id).where(m.User.username==username)) or db.scalar(select(TeamProfile.user_id).where(TeamProfile.google_email==email)):
    raise HTTPException(409,'This username or Google email is already registered')
   member=m.User(username=username,display_name=data.display_name.strip(),role='owner' if data.role=='admin' else 'technician',password_hash=password_hash(data.password),active=True)
   db.add(member);db.flush();db.add(TeamProfile(user_id=member.id,google_email=email));m.log(db,'CREATE_TEAM_ACCOUNT','user',member.id,p.dump({'role':data.role,'google_email':email}));db.commit()
   return {'id':member.id,'username':member.username,'role':data.role,'google_email':email,'note':'App account created. Google IAP and OAuth test-user access must be granted separately to this exact Google email before cloud sign-in.'}
 @app.put('/api/admin/team/{user_id}/active')
 def set_team_active(user_id:str,data:TeamActiveIn,user=Depends(m.require('manage_users'))):
  with Session(m.engine) as db:
   db.scalars(select(m.User).where(m.User.role=='owner').order_by(m.User.id).with_for_update()).all()
   member=db.get(m.User,user_id)
   if not member:raise HTTPException(404,'Employee not found')
   if user_id==user['id'] or member.username=='david':raise HTTPException(409,'This account cannot be disabled from the team screen')
   meta=db.get(TeamProfile,user_id)
   if data.expected_revision!=(meta.revision if meta else 0):raise HTTPException(409,'Account changed; reload')
   if not meta:raise HTTPException(409,'Use the existing account-management workflow for legacy accounts')
   if not data.active and db.get(m.ActiveTimeClock,user_id):raise HTTPException(409,'Stop and review this technician’s running clock before disabling access')
   member.active=data.active;meta.revision+=1
   if not data.active:
    from .security import UserSession
    # Revocation is immediate; existing sessions must not survive offboarding.
    for token in db.scalars(select(UserSession).where(UserSession.user_id==user_id)):db.delete(token)
   m.log(db,'SET_TEAM_ACCOUNT_ACTIVE','user',user_id,str(data.active));db.commit();return {'active':member.active,'revision':meta.revision}
 @app.get('/api/admin/customers/{customer_id}/sites')
 def customer_sites(customer_id:str,user=Depends(p.owner)):
  with Session(m.engine) as db:
   c=db.get(m.Customer,customer_id)
   if not c:raise HTTPException(404,'Customer not found')
   rows=[]
   for site in db.scalars(select(m.Location).where(m.Location.customer_id==customer_id).order_by(m.Location.name)):
    meta=db.get(SiteMetadata,site.id);rows.append({'id':site.id,'name':site.name,'address':site.address,'contact':meta.contact if meta else '', 'phone':meta.phone if meta else '', 'revision':meta.revision if meta else 0})
   revision=db.get(BillingRevision,customer_id)
   return {'customer':{'id':c.id,'name':c.name,'billing_address':c.billing_address,'contact':c.contact,'phone':c.phone,'email':c.email,'revision':revision.revision if revision else 0},'sites':rows,'basis':'Each named service site has its own address, equipment and service history. All sites bill to this customer billing address.'}
 def write_site(db,customer_id,data,site=None):
  c=db.scalar(select(m.Customer).where(m.Customer.id==customer_id).with_for_update())
  if not c:raise HTTPException(404,'Customer not found')
  meta=db.get(SiteMetadata,site.id) if site else None
  if data.expected_revision!=(meta.revision if meta else 0):raise HTTPException(409,'Site changed; reload')
  existing=next((x for x in db.scalars(select(m.Location).where(m.Location.customer_id==customer_id)) if x.name.strip().casefold()==data.name.strip().casefold() and (not site or x.id!=site.id)),None)
  if existing:raise HTTPException(409,'This customer already has a site with that name')
  if not site:site=m.Location(customer_id=customer_id);db.add(site)
  site.name=data.name.strip();site.address=data.address.strip();db.flush()
  if not meta:meta=SiteMetadata(location_id=site.id);db.add(meta)
  else:meta.revision+=1
  meta.contact=data.contact.strip();meta.phone=data.phone.strip();db.flush();m.log(db,'SAVE_CUSTOMER_SERVICE_SITE','location',site.id,data.model_dump_json());db.commit();return {'id':site.id,'revision':meta.revision}
 @app.post('/api/admin/customers/{customer_id}/sites',status_code=201)
 def create_site(customer_id:str,data:SiteIn,user=Depends(p.owner)):
  with Session(m.engine) as db:return write_site(db,customer_id,data)
 @app.put('/api/admin/customers/{customer_id}/sites/{site_id}')
 def update_site(customer_id:str,site_id:str,data:SiteIn,user=Depends(p.owner)):
  with Session(m.engine) as db:
   site=db.get(m.Location,site_id)
   if not site or site.customer_id!=customer_id:raise HTTPException(404,'Service site not found for this customer')
   return write_site(db,customer_id,data,site)
 @app.put('/api/admin/customers/{customer_id}/billing')
 def update_billing(customer_id:str,data:BillingIn,user=Depends(p.owner)):
  with Session(m.engine) as db:
   c=db.scalar(select(m.Customer).where(m.Customer.id==customer_id).with_for_update())
   if not c:raise HTTPException(404,'Customer not found')
   row=db.get(BillingRevision,customer_id)
   if data.expected_revision!=(row.revision if row else 0):raise HTTPException(409,'Billing details changed; reload')
   if not data.billing_address.strip():raise HTTPException(422,'Enter the shared billing address')
   c.billing_address=data.billing_address.strip();c.contact=data.contact.strip();c.phone=data.phone.strip();c.email=data.email.strip()
   if not row:row=BillingRevision(customer_id=customer_id);db.add(row)
   else:row.revision+=1
   db.flush();m.log(db,'SAVE_SHARED_BILLING_ADDRESS','customer',customer_id,data.model_dump_json());db.commit();return {'saved':True,'revision':row.revision}
 @app.get('/api/calls/{wo_id}/checklists')
 def list_checklists(wo_id:str,user=Depends(m.current_user)):
  with Session(m.engine) as db:
   access(db,wo_id,user)
   return {'templates':{key:{'name':v[0],'items':v[1]} for key,v in CHECKLISTS.items()},'saved':[{'equipment_id':r.equipment_id,'template':r.template,'revision':r.revision,'snapshot':json.loads(r.snapshot_json)} for r in db.scalars(select(Checklist).where(Checklist.work_order_id==wo_id))]}
 @app.put('/api/calls/{wo_id}/checklists')
 def save_checklist(wo_id:str,data:ChecklistIn,user=Depends(m.current_user)):
  from .field_v14 import ensure_editor,ensure_open
  with Session(m.engine) as db:
   access(db,wo_id,user);p.call(db,wo_id,lock=True);ensure_editor(user);ensure_open(db,wo_id)
   if not db.get(m.WorkOrderEquipment,(wo_id,data.equipment_id)):raise HTTPException(422,'Link this system to the call first')
   names=CHECKLISTS[data.template][1]
   if set(data.answers)!=set(str(i) for i in range(len(names))):raise HTTPException(422,'Answer each checklist item explicitly')
   row=db.get(Checklist,(wo_id,data.equipment_id))
   if data.expected_revision!=(row.revision if row else 0):raise HTTPException(409,'Checklist changed; reload')
   if not row:row=Checklist(work_order_id=wo_id,equipment_id=data.equipment_id);db.add(row)
   else:row.revision+=1
   row.template=data.template;row.updated_by=user['id'];row.snapshot_json=p.dump({'items':names,'answers':data.answers,'note':data.note,'updated_at':profit.stamp().isoformat()})
   m.log(db,'SAVE_SERVICE_CHECKLIST','work_order',wo_id,data.equipment_id);db.commit();return {'revision':row.revision}
 @app.get('/api/calls/{wo_id}/warranties')
 def call_warranties(wo_id:str,user=Depends(m.current_user)):
  with Session(m.engine) as db:access(db,wo_id,user);return warranties_for(db,wo_id)
 @app.get('/api/admin/warranties')
 def all_warranties(user=Depends(p.owner)):
  with Session(m.engine) as db:
   result=[]
   for r in db.scalars(select(Warranty)):
    data=warranty_payload(r);data['asset_tag']=db.get(m.Equipment,r.equipment_id).asset_tag;result.append(data)
   return result
 def write_warranty(db,data,row=None):
  if not db.get(m.Equipment,data.equipment_id):raise HTTPException(422,'System not found')
  if data.expected_revision!=(row.revision if row else 0):raise HTTPException(409,'Warranty changed; reload')
  if row and row.equipment_id!=data.equipment_id:raise HTTPException(422,'Warranty system cannot be changed')
  if not row:row=Warranty();db.add(row)
  else:row.revision+=1
  for key,value in data.model_dump(exclude={'expected_revision'}).items():setattr(row,key,value)
  db.flush();m.log(db,'SAVE_WARRANTY','equipment',row.equipment_id,row.id);db.commit();return warranty_payload(row)
 @app.post('/api/admin/warranties',status_code=201)
 def create_warranty(data:WarrantyIn,user=Depends(p.owner)):
  with Session(m.engine) as db:return write_warranty(db,data)
 @app.put('/api/admin/warranties/{warranty_id}')
 def edit_warranty(warranty_id:str,data:WarrantyIn,user=Depends(p.owner)):
  with Session(m.engine) as db:
   row=db.scalar(select(Warranty).where(Warranty.id==warranty_id).with_for_update())
   if not row:raise HTTPException(404,'Warranty not found')
   return write_warranty(db,data,row)
 @app.get('/api/calls/{wo_id}/additional-work')
 def additional_work(wo_id:str,user=Depends(m.current_user)):
  from .proposals_pm_v15 import QuoteDetail
  with Session(m.engine) as db:
   access(db,wo_id,user);result=[]
   for guard in db.scalars(select(GuardedQuote).where(GuardedQuote.work_order_id==wo_id,GuardedQuote.kind=='Additional work')):
    quote=db.get(m.Proposal,guard.proposal_id);detail=db.get(QuoteDetail,quote.id);row={'id':quote.id,'title':quote.title,'scope':quote.scope,'status':quote.status,'accepted_by':detail.acceptance_name,'accepted_at':detail.accepted_at.isoformat() if detail.accepted_at else None}
    if user['role']=='owner':row.update({'number':quote.number,'price':str(quote.total),'revision':detail.revision,'pricing':json.loads(guard.snapshot_json)})
    result.append(row)
   return result
 @app.post('/api/admin/calls/{wo_id}/service-quote',status_code=201)
 def service_quote(wo_id:str,data:ServiceQuoteIn,user=Depends(p.owner)):
  from .proposals_pm_v15 import QuoteDetail,QuoteIn,OptionIn,_create_quote
  from .call_field_notes import CallNote
  with Session(m.engine) as db:
   w=p.call(db,wo_id,lock=True);request_id=str(data.request_id);fingerprint=hashlib.sha256(data.model_dump_json().encode()).hexdigest()
   existing=db.scalar(select(GuardedQuote).where(GuardedQuote.request_id==request_id))
   if existing:
    if existing.fingerprint!=fingerprint or existing.work_order_id!=wo_id:raise HTTPException(409,'Request identifier was already used for different quote data')
    q=db.get(m.Proposal,existing.proposal_id);return {'id':q.id,'number':q.number,'status':q.status,'already_created':True}
   scope=data.customer_scope.strip();kind='Additional work';source_type='additional_work';source_id=request_id
   if data.source_note_id:
    note=db.get(CallNote,data.source_note_id)
    if not note or note.work_order_id!=wo_id or note.kind not in ('recommendation','follow_up'):raise HTTPException(422,'Select a recommendation on this call')
    if note.audience=='internal' and not scope:raise HTTPException(422,'Write a customer-facing scope; internal notes cannot be copied automatically')
    scope=scope or note.text;kind='Recommendation';source_type='call_note';source_id=note.id
    duplicate=db.scalar(select(QuoteDetail).where(QuoteDetail.source_type==source_type,QuoteDetail.source_id==source_id))
    if duplicate:raise HTTPException(409,'A draft quote already exists for this recommendation')
   if len(scope)<3:raise HTTPException(422,'Enter the customer-facing work scope')
   floor=p.minimum_price(data.estimated_cost,Decimal(data.target_margin));price=data.selling_price or floor
   if price<=0:raise HTTPException(422,'Enter a positive quote price')
   exception=price<floor
   if exception and len(data.exception_reason.strip())<10:raise HTTPException(422,'A below-target quote needs an explicit admin exception reason of at least 10 characters')
   job=db.get(m.Job,w.job_id)
   result=_create_quote(db,user,QuoteIn(job_number=job.job_number,number=data.number,title=data.title,scope=scope,options=[OptionIn(name=kind,description=scope,price=price)]),source_type=source_type,source_id=source_id,commit=False)
   snapshot={'estimated_cost':str(data.estimated_cost),'target_margin':data.target_margin,'minimum_price':str(floor),'approved_floor':str(price if exception else floor),'exception_reason':data.exception_reason.strip() if exception else '', 'reviewed_by':user['id'],'reviewed_at':profit.stamp().isoformat()}
   db.add(GuardedQuote(proposal_id=result['id'],work_order_id=wo_id,kind=kind,request_id=request_id,fingerprint=fingerprint,snapshot_json=p.dump(snapshot)))
   m.log(db,'CREATE_REVIEWED_SERVICE_QUOTE','work_order',wo_id,p.dump({'kind':kind,'pricing':snapshot}));db.commit()
   return {'id':result['id'],'number':data.number,'status':'Draft','already_created':False}
 @app.post('/api/admin/additional-work/{proposal_id}/accept')
 def accept_additional(proposal_id:str,data:ApprovalIn,user=Depends(p.owner)):
  from .proposals_pm_v15 import QuoteDetail,snapshot,add_revision,OptionIn
  if not data.customer_authorization_confirmed:raise HTTPException(422,'Confirm that you obtained the customer authorization')
  if not data.accepted_by.strip() or not data.reference.strip():raise HTTPException(422,'Enter customer name and approval reference')
  with Session(m.engine) as db:
   guard=db.get(GuardedQuote,proposal_id)
   if not guard or guard.kind!='Additional work':raise HTTPException(404,'Additional work request not found')
   quote=db.scalar(select(m.Proposal).where(m.Proposal.id==proposal_id).with_for_update());detail=db.get(QuoteDetail,quote.id)
   if quote.status!='Draft' or detail.revision!=data.expected_revision:raise HTTPException(409,'Approval request changed or was already accepted; reload')
   options=[OptionIn(**x) for x in json.loads(detail.options_json)];validate_quote(db,quote,options)
   quote.status='Approved';quote.total=options[0].price;detail.selected_option=0;detail.acceptance_name=data.accepted_by.strip();detail.acceptance_reference=data.method+': '+data.reference.strip();detail.accepted_at=profit.stamp();detail.recorded_by=user['id'];detail.revision+=1
   detail.accepted_snapshot=p.dump(snapshot(db,quote,detail));add_revision(db,quote,detail);m.log(db,'RECORD_ADDITIONAL_WORK_APPROVAL','proposal',quote.id,detail.acceptance_reference);db.commit();return {'id':quote.id,'status':quote.status}
 @app.get('/api/admin/calls/{wo_id}/reconciliation')
 def get_reconciliation(wo_id:str,user=Depends(p.owner)):
  with Session(m.engine) as db:return reconciliation(db,wo_id)
 @app.put('/api/admin/calls/{wo_id}/parts/{usage_id}/receipt')
 def allocate(wo_id:str,usage_id:str,data:AllocationIn,user=Depends(p.owner)):
  from .call_field_notes import CallPartUsed
  from .purchasing_v13 import details
  with Session(m.engine) as db:
   w=p.call(db,wo_id,lock=True);used=db.get(CallPartUsed,usage_id);receipt=db.get(m.POReceipt,data.receipt_id)
   if not used or not used.active or used.work_order_id!=wo_id or not receipt:raise HTTPException(422,'Select a reported part and valid receipt')
   po=db.scalar(select(m.PurchaseOrder).where(m.PurchaseOrder.id==receipt.po_id).with_for_update())
   if po.job_id!=w.job_id or po.source=='historical':raise HTTPException(422,'Receipt must belong to this exact job and a current PO')
   export=db.get(p.CallPricingExport,(wo_id,'invoice'))
   if export and db.get(m.InvoiceRecord,export.record_id).status!='Draft':raise HTTPException(409,'Issued call acquisition costs are locked; use a reviewed adjustment')
   row=db.get(ReceiptAllocation,(usage_id,receipt.id))
   if data.expected_revision!=(row.revision if row else 0):raise HTTPException(409,'Allocation changed; reload')
   receipt_alloc=sum((r.amount for r in db.scalars(select(ReceiptAllocation).where(ReceiptAllocation.receipt_id==receipt.id))),Decimal('0'))-(row.amount if row else 0)+data.amount
   po_alloc=sum((r.amount for r in db.scalars(select(ReceiptAllocation).join(m.POReceipt,ReceiptAllocation.receipt_id==m.POReceipt.id).where(m.POReceipt.po_id==po.id))),Decimal('0'))-(row.amount if row else 0)+data.amount
   if receipt_alloc>receipt.amount or po_alloc>details(db,po)['actual']:raise HTTPException(422,'Allocation exceeds the remaining received/actual purchase cost')
   if not row:row=ReceiptAllocation(usage_id=usage_id,receipt_id=receipt.id);db.add(row)
   else:row.revision+=1
   row.amount=data.amount;row.note=data.note;db.flush();m.log(db,'ALLOCATE_RECEIPT_COST','work_order',wo_id,data.model_dump_json());db.commit();return {'revision':row.revision,'saved':True}
 @app.get('/api/admin/payroll')
 def payroll_preview(week_start:date,user=Depends(p.owner)):
  with Session(m.engine) as db:return payroll(db,week_start)
 @app.get('/api/admin/payroll.csv')
 def payroll_export(week_start:date,reviewed_warnings:bool=False,user=Depends(p.owner)):
  with Session(m.engine) as db:
   result=payroll(db,week_start)
   if result['blockers']:raise HTTPException(409,'Resolve unapproved/running time and pending corrections before exporting')
   if result['warnings'] and not reviewed_warnings:raise HTTPException(409,'Review and acknowledge payroll allocation warnings before exporting')
   out=io.StringIO();fields=['entry_id','technician','job_number','call','start_local','end_local','hours','regular_hours','overtime_hours'];writer=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore');writer.writeheader()
   for row in result['rows']:
    safe={key:("'"+str(value) if str(value).lstrip().startswith(('=','+','-','@')) else value) for key,value in row.items()};writer.writerow(safe)
   m.log(db,'EXPORT_PAYROLL_HOURS','payroll',week_start.isoformat(),result['total_hours']+' hours');db.commit()
   return StreamingResponse(io.BytesIO(out.getvalue().encode('utf-8-sig')),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="Limitless-hours-{week_start.isoformat()}.csv"'})
 @app.get('/api/admin/notifications')
 def notifications(user=Depends(p.owner)):
  with Session(m.engine) as db:return inbox(db,user)
 @app.put('/api/admin/notifications/{item_key}/snooze')
 def snooze(item_key:str,data:SnoozeIn,user=Depends(p.owner)):
  if len(item_key)>200:raise HTTPException(422,'Notification identifier is too long')
  with Session(m.engine) as db:
   m.lock_time_user(db,user['id'])
   if item_key not in {r['key'] for r in inbox(db,user)['items']}:raise HTTPException(404,'Active notification not found')
   row=db.get(InboxState,(user['id'],item_key))
   if not row:row=InboxState(user_id=user['id'],item_key=item_key);db.add(row)
   row.snoozed_until=profit.stamp()+timedelta(hours=data.hours);db.commit();return {'snoozed':True}
