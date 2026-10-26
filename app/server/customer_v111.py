"""Scoped customer links with authenticated internal previews by default."""
import hashlib,json,os,secrets,uuid
from datetime import date,datetime,timedelta,timezone
from typing import Literal
from fastapi import Depends,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from sqlalchemy import String,Text,ForeignKey,DateTime,Numeric,select
from sqlalchemy.orm import Mapped,mapped_column,Session
from . import main as m,call_pricing as p
from .billing_v111 import now,utc,settings
from .quote_template import presentation as quote_presentation

class CustomerGrant(m.Base):
 __tablename__='customer_grants_v111'
 id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
 token_hash:Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
 kind:Mapped[str]=mapped_column(String(20),nullable=False)
 customer_id:Mapped[str]=mapped_column(ForeignKey('customers.id',ondelete='RESTRICT'),nullable=False)
 proposal_id:Mapped[str|None]=mapped_column(ForeignKey('proposals.id',ondelete='RESTRICT'))
 proposal_revision:Mapped[int|None]=mapped_column()
 expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
 revoked:Mapped[bool]=mapped_column(default=False,nullable=False)
 accepted_json:Mapped[str]=mapped_column(Text,default='',nullable=False)

class CustomerRequest(m.Base):
 __tablename__='customer_requests_v111'
 id:Mapped[str]=mapped_column(String(36),primary_key=True)
 customer_id:Mapped[str]=mapped_column(ForeignKey('customers.id',ondelete='RESTRICT'),nullable=False,index=True)
 location_id:Mapped[str]=mapped_column(ForeignKey('locations.id',ondelete='RESTRICT'),nullable=False)
 name:Mapped[str]=mapped_column(String(200),nullable=False)
 description:Mapped[str]=mapped_column(Text,nullable=False)
 status:Mapped[str]=mapped_column(String(20),default='New',nullable=False)
 work_order_id:Mapped[str|None]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'))
 fingerprint:Mapped[str]=mapped_column(String(64),nullable=False)
 created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)

class PMVisitTemplate(m.Base):
 __tablename__='pm_visit_templates_v111'
 id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
 name:Mapped[str]=mapped_column(String(200),unique=True,nullable=False)
 interval_days:Mapped[int]=mapped_column(nullable=False)
 items:Mapped[str]=mapped_column(Text,nullable=False)
 estimated_hours:Mapped[str]=mapped_column(String(30),nullable=False)
 revision:Mapped[int]=mapped_column(default=1,nullable=False)

class GrantIn(BaseModel):
 kind:Literal['Quote','Service request']
 proposal_id:str|None=None
 customer_id:str|None=None
 expires_days:int=Field(default=7,ge=1,le=30)

class TokenIn(BaseModel):
 token:str=Field(min_length=30,max_length=100)

class CustomerAccept(TokenIn):
 name:str=Field(min_length=2,max_length=200)
 selected_option:int=Field(ge=0,le=4)
 consent:bool

class RequestIn(TokenIn):
 request_id:uuid.UUID
 location_id:str
 name:str=Field(min_length=2,max_length=200)
 description:str=Field(min_length=5,max_length=5000)

class ConvertIn(BaseModel):
 job_number:str

class TemplateIn(BaseModel):
 name:str=Field(min_length=3,max_length=200)
 interval_days:int=Field(ge=7,le=730)
 items:list[str]=Field(min_length=1,max_length=50)
 estimated_hours:str=Field(default='1',max_length=30)

class ApplyIn(BaseModel):
 plan_id:str
 template_id:str
 expected_interval_days:int=Field(ge=1)

class RunPMIn(BaseModel):
 through_date:date


def resolve_grant(db,token,lock=False):
 q=select(CustomerGrant).where(CustomerGrant.token_hash==hashlib.sha256(token.encode()).hexdigest())
 if lock:q=q.with_for_update()
 r=db.scalar(q)
 if not r or r.revoked or utc(r.expires_at)<now():raise HTTPException(404,'Link expired or unavailable')
 return r


def grant_view(db,r):
 customer=db.get(m.Customer,r.customer_id)
 if r.kind=='Service request':return {'kind':r.kind,'customer':customer.name,'locations':[{'id':s.id,'name':s.name,'address':s.address} for s in db.scalars(select(m.Location).where(m.Location.customer_id==r.customer_id))]}
 from .proposals_pm_v15 import QuoteDetail
 q=db.get(m.Proposal,r.proposal_id);detail=db.get(QuoteDetail,q.id)
 if not r.accepted_json and (detail.revision!=r.proposal_revision or q.status!='Draft'):raise HTTPException(409,'Quote changed; request a new approval link')
 if r.accepted_json:return {'kind':'Quote','accepted':True,'quote':json.loads(r.accepted_json)}
 options=[{'name':o['name'],'description':o.get('description',''),'price':str(o['price'])} for o in json.loads(detail.options_json)]
 from decimal import Decimal,ROUND_HALF_UP
 config=quote_presentation(db,q.id)
 for o in options:
  amount=Decimal(o['price']);deposit=None
  if config.get('deposit_percent') is not None:deposit=(amount*Decimal(str(config['deposit_percent']))/100).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
  elif config.get('deposit_amount') is not None:deposit=Decimal(str(config['deposit_amount']))
  if deposit is not None:o.update({'deposit':str(deposit),'balance':str(amount-deposit)})
 return {'kind':'Quote','accepted':False,'quote':{'number':q.number,'title':q.title,'scope':q.scope,'customer':customer.name,'inclusions':detail.inclusions,'exclusions':detail.exclusions,'warranty':detail.warranty,'payment_terms':detail.payment_terms,'project_timeline':detail.project_timeline,'options':options,'presentation':quote_presentation(db,q.id)}}


def accept(db,data):
 from .proposals_pm_v15 import QuoteDetail,OptionIn,add_revision,snapshot
 r=resolve_grant(db,data.token);q=db.scalar(select(m.Proposal).where(m.Proposal.id==r.proposal_id).with_for_update());r=resolve_grant(db,data.token,lock=True)
 if r.kind!='Quote' or not q:raise HTTPException(422,'This link does not approve quotes')
 if r.accepted_json:return {'accepted':True,'already_accepted':True}
 if not data.consent or not data.name.strip():raise HTTPException(422,'Enter your name and confirm authorization')
 detail=db.get(QuoteDetail,q.id)
 if q.status!='Draft' or detail.revision!=r.proposal_revision:raise HTTPException(409,'Quote changed; request a fresh link')
 options=[OptionIn(**o) for o in json.loads(detail.options_json)]
 if data.selected_option>=len(options):raise HTTPException(422,'Choose an offered option')
 p.validate_quote_margin(db,q,options);public=grant_view(db,r)['quote'];q.status='Approved';q.total=options[data.selected_option].price;detail.selected_option=data.selected_option;detail.acceptance_name=data.name.strip();detail.acceptance_reference='Scoped link authorization '+r.id;detail.accepted_at=now();detail.revision+=1
 # This is possession-of-link consent, not independent identity verification.
 detail.accepted_snapshot=p.dump(snapshot(db,q,detail));public.update({'accepted_by':data.name.strip(),'selected_option':data.selected_option,'accepted_at':now().isoformat()});r.accepted_json=p.dump(public);add_revision(db,q,detail);m.log(db,'ACCEPT_SCOPED_CUSTOMER_QUOTE','proposal',q.id,r.id);db.commit();return {'accepted':True,'already_accepted':False}


def request_service(db,data):
 r=resolve_grant(db,data.token,lock=True)
 if r.kind!='Service request':raise HTTPException(422,'This link cannot request service')
 site=db.get(m.Location,data.location_id)
 if not site or site.customer_id!=r.customer_id:raise HTTPException(422,'Choose a service site for this customer')
 fingerprint=hashlib.sha256(p.dump([r.customer_id,data.location_id,data.name,data.description]).encode()).hexdigest();old=db.get(CustomerRequest,str(data.request_id))
 if old:
  if old.fingerprint!=fingerprint:raise HTTPException(409,'Request identifier was reused for different content')
  return {'id':old.id,'status':old.status}
 if not data.name.strip() or not data.description.strip():raise HTTPException(422,'Enter your name and service request')
 row=CustomerRequest(id=str(data.request_id),customer_id=r.customer_id,location_id=site.id,name=data.name.strip(),description=data.description.strip(),fingerprint=fingerprint);db.add(row);db.commit();return {'id':row.id,'status':'New'}


def pm_attention(db):
 from .proposals_pm_v15 import PMAgreement,PMCoverage
 today=date.today();due=[];renewals=[]
 for a in db.scalars(select(PMAgreement).where(PMAgreement.status=='Active')):
  if a.end_date<=today+timedelta(days=a.renewal_notice_days):renewals.append({'id':a.id,'number':a.number,'customer':db.get(m.Customer,a.customer_id).name,'end_date':str(a.end_date),'expired':a.end_date<today})
  for coverage in db.scalars(select(PMCoverage).where(PMCoverage.agreement_id==a.id)):
   plan=db.get(m.PMPlan,coverage.plan_id);asset=db.get(m.Equipment,coverage.equipment_id)
   if plan.active and plan.next_due<=today:due.append({'plan_id':plan.id,'agreement':a.number,'asset_tag':asset.asset_tag,'location':db.get(m.Location,asset.location_id).name,'next_due':str(plan.next_due),'overdue_days':(today-plan.next_due).days})
 return {'due_visits':due,'renewals':renewals}


def attach_customer_workflows(app):
 m.Base.metadata.create_all(m.engine)
 @app.get('/service-office')
 def service_page(user=Depends(p.owner)):return FileResponse(m.ROOT/'service-office.html')
 @app.get('/service-office.js')
 def service_script(user=Depends(p.owner)):return FileResponse(m.ROOT/'service-office.js',media_type='application/javascript')
 @app.get('/customer-preview')
 def preview(user=Depends(p.owner)):return FileResponse(m.ROOT/'customer-preview.html')
 @app.get('/customer-preview.js')
 def script(user=Depends(p.owner)):return FileResponse(m.ROOT/'customer-preview.js',media_type='application/javascript')
 @app.post('/api/admin/customer-links')
 def create(data:GrantIn,user=Depends(p.owner)):
  from .proposals_pm_v15 import QuoteDetail
  with Session(m.engine) as db:
   customer=data.customer_id;revision=None
   if data.kind=='Quote':
    quote=db.get(m.Proposal,data.proposal_id);detail=db.get(QuoteDetail,data.proposal_id)
    if not quote or not detail or quote.status!='Draft':raise HTTPException(422,'Choose an editable draft quote')
    customer=db.get(m.Job,quote.job_id).customer_id;revision=detail.revision
   if not customer or not db.get(m.Customer,customer):raise HTTPException(422,'Choose a customer')
   raw=secrets.token_urlsafe(32);r=CustomerGrant(token_hash=hashlib.sha256(raw.encode()).hexdigest(),kind=data.kind,customer_id=customer,proposal_id=data.proposal_id if data.kind=='Quote' else None,proposal_revision=revision,expires_at=now()+timedelta(days=data.expires_days));db.add(r);db.flush();m.log(db,'CREATE_SCOPED_CUSTOMER_LINK','customer',customer,r.id);db.commit()
   internal=settings(db)['values']['test_mode'] or os.getenv('LMS_CUSTOMER_PORTAL_ENABLED')!='1';base='/customer-preview' if internal else os.getenv('LMS_PORTAL_PUBLIC_URL','').rstrip('/')
   if not base:raise HTTPException(503,'Configure the separately scoped customer portal URL first')
   return {'id':r.id,'url':base+'#'+raw,'internal_preview':internal,'expires_at':utc(r.expires_at).isoformat()}
 @app.post('/api/admin/customer-links/revoke/{grant_id}')
 def revoke(grant_id:str,user=Depends(p.owner)):
  with Session(m.engine) as db:
   r=db.get(CustomerGrant,grant_id)
   if not r:raise HTTPException(404,'Link not found')
   r.revoked=True;db.commit();return {'revoked':True}
 @app.post('/api/admin/customer-preview/resolve')
 def staff_resolve(data:TokenIn,user=Depends(p.owner)):
  with Session(m.engine) as db:return grant_view(db,resolve_grant(db,data.token))
 @app.post('/api/admin/customer-preview/accept')
 def staff_accept(data:CustomerAccept,user=Depends(p.owner)):
  with Session(m.engine) as db:return accept(db,data)
 @app.post('/api/admin/customer-preview/request')
 def staff_request(data:RequestIn,user=Depends(p.owner)):
  with Session(m.engine) as db:return request_service(db,data)
 @app.get('/api/admin/customer-requests')
 def requests(user=Depends(p.owner)):
  with Session(m.engine) as db:return [{'id':r.id,'customer':db.get(m.Customer,r.customer_id).name,'customer_id':r.customer_id,'location_id':r.location_id,'location':db.get(m.Location,r.location_id).name,'name':r.name,'description':r.description,'status':r.status,'work_order_id':r.work_order_id,'jobs':[{'job_number':j.job_number,'description':j.description} for j in db.scalars(select(m.Job).where(m.Job.customer_id==r.customer_id,m.Job.location_id==r.location_id))]} for r in db.scalars(select(CustomerRequest).order_by(CustomerRequest.created_at.desc()))]
 @app.post('/api/admin/customer-requests/{request_id}/convert')
 def convert(request_id:str,data:ConvertIn,user=Depends(p.owner)):
  with Session(m.engine) as db:
   r=db.scalar(select(CustomerRequest).where(CustomerRequest.id==request_id).with_for_update())
   if not r:raise HTTPException(404,'Request not found')
   if r.work_order_id:return {'id':r.work_order_id,'already_created':True}
   job=db.scalar(select(m.Job).where(m.Job.job_number==data.job_number))
   if not job or job.customer_id!=r.customer_id or job.location_id!=r.location_id:raise HTTPException(422,'Select a verified job for this customer and service site')
   w=m.WorkOrder(job_id=job.id,summary=r.description,priority='Normal',status='Open');db.add(w);db.flush();r.work_order_id=w.id;r.status='Scheduled';m.log(db,'CONVERT_CUSTOMER_REQUEST','work_order',w.id,r.id);db.commit();return {'id':w.id,'already_created':False}
 @app.get('/api/admin/pm/attention')
 def attention(user=Depends(p.owner)):
  with Session(m.engine) as db:return pm_attention(db)
 @app.get('/api/admin/pm/visit-templates')
 def templates(user=Depends(p.owner)):
  with Session(m.engine) as db:return [{'id':r.id,'name':r.name,'interval_days':r.interval_days,'items':json.loads(r.items),'estimated_hours':r.estimated_hours,'revision':r.revision} for r in db.scalars(select(PMVisitTemplate))]
 @app.post('/api/admin/pm/visit-templates',status_code=201)
 def add_template(data:TemplateIn,user=Depends(p.owner)):
  from decimal import Decimal
  try:hours=Decimal(data.estimated_hours)
  except Exception:raise HTTPException(422,'Enter estimated hours')
  if not hours.is_finite() or hours<0 or hours>1000 or any(not i.strip() or len(i)>500 for i in data.items):raise HTTPException(422,'Check template hours and checklist items')
  with Session(m.engine) as db:
   m.lock_time_user(db,user['id'])
   if db.scalar(select(PMVisitTemplate).where(PMVisitTemplate.name==data.name.strip())):raise HTTPException(409,'Template name already exists')
   r=PMVisitTemplate(name=data.name.strip(),interval_days=data.interval_days,items=p.dump(data.items),estimated_hours=str(hours));db.add(r);db.flush();db.commit();return {'id':r.id}
 @app.post('/api/admin/pm/apply-template')
 def apply_template(data:ApplyIn,user=Depends(p.owner)):
  with Session(m.engine) as db:
   plan=db.scalar(select(m.PMPlan).where(m.PMPlan.id==data.plan_id).with_for_update());t=db.get(PMVisitTemplate,data.template_id)
   if not plan or not t:raise HTTPException(404,'Plan or template not found')
   if plan.interval_days!=data.expected_interval_days:raise HTTPException(409,'Plan changed; reload')
   plan.interval_days=t.interval_days;plan.checklist=t.items;m.log(db,'APPLY_PM_VISIT_TEMPLATE','pm_plan',plan.id,t.id);db.commit();return {'saved':True,'note':'Future visit definitions updated; existing inspection records retained'}
 @app.post('/api/admin/pm/generate-due')
 def generate(data:RunPMIn,user=Depends(p.owner)):
  from .proposals_pm_v15 import PMAgreement,schedule_agreement
  if data.through_date>date.today()+timedelta(days=366):raise HTTPException(422,'Use a horizon within one year')
  with Session(m.engine) as db:
   results=[]
   for a in db.scalars(select(PMAgreement).where(PMAgreement.status=='Active').order_by(PMAgreement.id).with_for_update()):results.append({'agreement':a.number,**schedule_agreement(db,a,data.through_date)})
   db.commit();return {'results':results}
