"""Invoice delivery outbox, commercial billing, check allocation and internal test mode."""
import hashlib,io,json,re,uuid
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from typing import Literal
from fastapi import Depends,HTTPException
from fastapi.responses import FileResponse,StreamingResponse
from pydantic import BaseModel,Field,model_validator
from sqlalchemy import String,Text,ForeignKey,DateTime,Numeric,LargeBinary,UniqueConstraint,select
from sqlalchemy.orm import Mapped,mapped_column,Session
from . import main as m,call_pricing as p

SENDER='accounting@limitlessmechanical.net'
def now():return datetime.now(timezone.utc)
def utc(d):return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
def email(value):
 value=value.strip().lower()
 if not re.fullmatch(r'[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+',value):raise HTTPException(422,'Enter a single valid email address')
 return value

class BillingSettings(m.Base):
 __tablename__='billing_settings_v111'
 key:Mapped[str]=mapped_column(String(20),primary_key=True)
 revision:Mapped[int]=mapped_column(default=1,nullable=False)
 json:Mapped[str]=mapped_column(Text,nullable=False)

class CommercialInvoice(m.Base):
 __tablename__='commercial_invoice_details_v111'
 invoice_id:Mapped[str]=mapped_column(ForeignKey('crm_invoices.id',ondelete='RESTRICT'),primary_key=True)
 revision:Mapped[int]=mapped_column(default=1,nullable=False)
 json:Mapped[str]=mapped_column(Text,nullable=False)

class InvoiceSnapshot(m.Base):
 __tablename__='invoice_snapshots_v111'
 invoice_id:Mapped[str]=mapped_column(ForeignKey('crm_invoices.id',ondelete='RESTRICT'),primary_key=True)
 json:Mapped[str]=mapped_column(Text,nullable=False)

class CustomerContact(m.Base):
 __tablename__='customer_contacts_v111'
 id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
 customer_id:Mapped[str]=mapped_column(ForeignKey('customers.id',ondelete='RESTRICT'),index=True,nullable=False)
 location_id:Mapped[str|None]=mapped_column(ForeignKey('locations.id',ondelete='RESTRICT'))
 name:Mapped[str]=mapped_column(String(200),nullable=False)
 email:Mapped[str]=mapped_column(String(250),nullable=False)
 purpose:Mapped[str]=mapped_column(String(20),nullable=False)
 active:Mapped[bool]=mapped_column(default=True,nullable=False)

class InvoiceSequence(m.Base):
 __tablename__='invoice_sequence_v111'
 key:Mapped[str]=mapped_column(String(20),primary_key=True)
 last_number:Mapped[int]=mapped_column(default=0,nullable=False)

class MailOutbox(m.Base):
 __tablename__='invoice_outbox_v111'
 id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
 invoice_id:Mapped[str]=mapped_column(ForeignKey('crm_invoices.id',ondelete='RESTRICT'),index=True,nullable=False)
 request_id:Mapped[str]=mapped_column(String(100),unique=True,nullable=False)
 recipient:Mapped[str]=mapped_column(String(250),nullable=False)
 subject:Mapped[str]=mapped_column(String(250),nullable=False)
 message:Mapped[str]=mapped_column(Text,nullable=False)
 pdf:Mapped[bytes]=mapped_column(LargeBinary,nullable=False)
 fingerprint:Mapped[str]=mapped_column(String(64),nullable=False)
 status:Mapped[str]=mapped_column(String(20),default='Queued',nullable=False)
 purpose:Mapped[str]=mapped_column(String(50),default='Invoice',nullable=False)
 provider_id:Mapped[str]=mapped_column(String(150),default='',nullable=False)
 error:Mapped[str]=mapped_column(Text,default='',nullable=False)
 created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)
 attempted_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
 created_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)

class CheckReceipt(m.Base):
 __tablename__='check_receipts_v111'
 id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
 customer_id:Mapped[str]=mapped_column(ForeignKey('customers.id',ondelete='RESTRICT'),nullable=False,index=True)
 check_number:Mapped[str]=mapped_column(String(80),nullable=False)
 amount:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
 received_on:Mapped[date]=mapped_column(m.Date,nullable=False)
 deposited_on:Mapped[date|None]=mapped_column(m.Date)
 status:Mapped[str]=mapped_column(String(20),default='Received',nullable=False)
 revision:Mapped[int]=mapped_column(default=1,nullable=False)
 note:Mapped[str]=mapped_column(Text,default='',nullable=False)
 __table_args__=(UniqueConstraint('customer_id','check_number',name='uq_customer_check_number'),)

class CheckLifecycle(m.Base):
 __tablename__='check_lifecycle_v111'
 id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
 check_id:Mapped[str]=mapped_column(ForeignKey('check_receipts_v111.id',ondelete='RESTRICT'),nullable=False)
 action:Mapped[str]=mapped_column(String(20),nullable=False)
 action_on:Mapped[date]=mapped_column(m.Date,nullable=False)
 reason:Mapped[str]=mapped_column(Text,nullable=False)

class CheckAllocation(m.Base):
 __tablename__='check_allocations_v111'
 request_id:Mapped[str]=mapped_column(String(36),primary_key=True)
 check_id:Mapped[str]=mapped_column(ForeignKey('check_receipts_v111.id',ondelete='RESTRICT'),nullable=False,index=True)
 payment_id:Mapped[str]=mapped_column(ForeignKey('crm_invoice_payments.id',ondelete='RESTRICT'),unique=True,nullable=False)

class CollectionPause(m.Base):
 __tablename__='collection_pause_v111'
 invoice_id:Mapped[str]=mapped_column(ForeignKey('crm_invoices.id',ondelete='RESTRICT'),primary_key=True)
 until:Mapped[date]=mapped_column(m.Date,nullable=False)
 reason:Mapped[str]=mapped_column(Text,nullable=False)

class WaveOpening(m.Base):
 __tablename__='wave_opening_balances_v111'
 invoice_id:Mapped[str]=mapped_column(ForeignKey('crm_invoices.id',ondelete='RESTRICT'),primary_key=True)
 source_reference:Mapped[str]=mapped_column(String(100),unique=True,nullable=False)

class SettingsIn(BaseModel):
 test_mode:bool=True
 remit_to:str=Field(default='Limitless Mechanical Services, Inc.',min_length=3,max_length=200)
 remit_address:str=Field(default='',max_length=1500)
 payment_terms:str=Field(default='Net 30',min_length=2,max_length=500)
 invoice_prefix:str=Field(default='LMS-INV-',min_length=1,max_length=20,pattern=r'^[A-Za-z0-9-]+$')
 reminders_enabled:bool=False
 reminder_days:list[int]=Field(default_factory=lambda:[7,21,35],max_length=5)
 expected_revision:int=Field(default=0,ge=0)
 @model_validator(mode='after')
 def days(self):
  if any(d<1 or d>365 for d in self.reminder_days) or len(set(self.reminder_days))!=len(self.reminder_days):raise ValueError('Use distinct reminder days from 1 to 365')
  return self

class InvoiceMetaIn(BaseModel):
 include_service_fee:bool|None=None
 separate_material_labor:bool=True
 taxable:bool|None=None
 tax_rate:Decimal=Field(default=Decimal('0'),ge=0,le=100,decimal_places=3)
 job_summary:str=Field(default='',max_length=5000)
 customer_po:str=Field(default='',max_length=150)
 service_from:date|None=None
 service_to:date|None=None
 note:str=Field(default='',max_length=2000)
 expected_revision:int=Field(default=0,ge=0)
 @model_validator(mode='after')
 def ordered(self):
  if self.service_from and self.service_to and self.service_to<self.service_from:raise ValueError('Service dates must be ordered')
  return self

class ContactIn(BaseModel):
 customer_id:str
 location_id:str|None=None
 name:str=Field(min_length=2,max_length=200)
 email:str
 purpose:Literal['Billing','Scheduling','Site']='Billing'

class SendIn(BaseModel):
 request_id:uuid.UUID
 recipient:str
 subject:str=Field(min_length=1,max_length=250)
 message:str=Field(min_length=1,max_length=5000)
 confirm_resend:bool=False

class CheckIn(BaseModel):
 customer_id:str
 check_number:str=Field(min_length=1,max_length=80)
 amount:Decimal=Field(gt=0,max_digits=14,decimal_places=2)
 received_on:date
 note:str=Field(default='',max_length=2000)

class AllocateIn(BaseModel):
 request_id:uuid.UUID
 invoice_id:str
 amount:Decimal=Field(gt=0,max_digits=14,decimal_places=2)

class CheckStateIn(BaseModel):
 status:Literal['Deposited','Returned']
 action_on:date
 expected_revision:int=Field(ge=1)
 reason:str=Field(min_length=3,max_length=2000)

class ResolveMailIn(BaseModel):
 action:Literal['Confirmed sent','Confirmed not sent']
 reason:str=Field(min_length=10,max_length=2000)
 provider_id:str=Field(default='',max_length=150)

class PauseIn(BaseModel):
 until:date
 reason:str=Field(min_length=3,max_length=2000)

class OpeningIn(BaseModel):
 commit:bool=False
 rows:list[dict]=Field(max_length=500)


def settings(db):
 row=db.get(BillingSettings,'company');v=SettingsIn().model_dump(mode='json',exclude={'expected_revision'})
 if row:v.update(json.loads(row.json))
 return {'revision':row.revision if row else 0,'values':v,'invoice_sender':SENDER}


def metadata(db,inv):
 row=db.get(CommercialInvoice,inv.id);v=json.loads(row.json) if row else {}
 link=db.scalar(select(p.CallPricingExport).where(p.CallPricingExport.kind=='invoice',p.CallPricingExport.record_id==inv.id))
 v.setdefault('job_summary',db.get(m.WorkOrder,link.work_order_id).summary if link else db.get(m.Job,inv.job_id).description)
 lines=m.invoice_lines(db,inv)
 v.setdefault('taxable',any(line.tax_amount>0 for line in lines))
 v.setdefault('tax_rate',str(next((line.tax_rate for line in lines if line.tax_rate>0),Decimal('0'))))
 return {'revision':row.revision if row else 0,'values':v}


def freeze_invoice(db,inv):
 row=db.get(InvoiceSnapshot,inv.id)
 if row:return json.loads(row.json)
 job=db.get(m.Job,inv.job_id);c=db.get(m.Customer,job.customer_id);site=db.get(m.Location,job.location_id) if job.location_id else None;cfg=settings(db)['values']
 snapshot={'customer':{'name':c.name,'billing_address':c.billing_address},'site':{'name':site.name,'address':site.address} if site else None,'commercial':{**metadata(db,inv)['values'],'remit_to':cfg['remit_to'],'remit_address':cfg['remit_address'],'payment_terms':cfg['payment_terms'],'reply_to':SENDER}}
 if inv.status!='Draft':db.add(InvoiceSnapshot(invoice_id=inv.id,json=p.dump(snapshot)));db.flush()
 return snapshot

def next_number(db):
 # A seeded singleton serializes independent admin requests on PostgreSQL.
 row=db.scalar(select(InvoiceSequence).where(InvoiceSequence.key=='invoice').with_for_update());prefix=settings(db)['values']['invoice_prefix']
 while True:
  row.last_number+=1;number=prefix+str(row.last_number).zfill(5)
  if not db.scalar(select(m.InvoiceRecord.id).where(m.InvoiceRecord.number==number)):return number


def mail_payload(row):
 status=row.status
 if status=='Sending' and row.attempted_at and now()-utc(row.attempted_at)>timedelta(minutes=5):status='Unconfirmed'
 return {'id':row.id,'invoice_id':row.invoice_id,'recipient':row.recipient,'subject':row.subject,'status':status,'purpose':row.purpose,'error':row.error,'provider_id':row.provider_id,'created_at':utc(row.created_at).isoformat()}


def enqueue(db,inv,user,request_id,recipient,subject,message,confirm_resend=False,purpose='Invoice'):
 recipient=email(recipient);request_id=str(request_id)
 if inv.status=='Draft':raise HTTPException(409,'Issue the invoice before preparing its email')
 prior=db.scalar(select(MailOutbox).where(MailOutbox.request_id==request_id))
 fingerprint=hashlib.sha256(p.dump([inv.id,recipient,subject,message,purpose]).encode()).hexdigest()
 if prior:
  if prior.fingerprint!=fingerprint:raise HTTPException(409,'This email request identifier was used for different content')
  return prior
 same=list(db.scalars(select(MailOutbox).where(MailOutbox.invoice_id==inv.id,MailOutbox.recipient==recipient,MailOutbox.purpose==purpose).order_by(MailOutbox.created_at.desc())))
 if any(mail_payload(r)['status']=='Unconfirmed' or r.status=='Sending' for r in same):raise HTTPException(409,'An earlier send is unconfirmed; inspect Sent Mail and resolve it before retrying')
 if same and same[0].status=='Queued':return same[0]
 if same and same[0].status=='Preview' and settings(db)['values']['test_mode']:return same[0]
 if same and same[0].status=='Preview' and not confirm_resend:raise HTTPException(409,'A test preview exists; explicitly confirm a new live send')
 if same and same[0].status=='Sent' and not confirm_resend:raise HTTPException(409,'Already sent; explicitly confirm a resend')
 row=MailOutbox(invoice_id=inv.id,request_id=request_id,recipient=recipient,subject=subject,message=message,pdf=m._invoice_pdf(db,inv),fingerprint=fingerprint,status='Preview' if settings(db)['values']['test_mode'] else 'Queued',purpose=purpose,created_by=user['id'])
 db.add(row);db.flush();m.log(db,'PREPARE_INVOICE_EMAIL','invoice',inv.id,row.id);return row


def process_mail(mail_id):
 import os
 with Session(m.engine) as db:
  row=db.scalar(select(MailOutbox).where(MailOutbox.id==mail_id).with_for_update())
  if not row:raise HTTPException(404,'Email not found')
  if row.status!='Queued':return mail_payload(row)
  if settings(db)['values']['test_mode'] or os.getenv('LMS_ALLOW_EXTERNAL_EMAIL')!='1':
   row.status='Preview';db.commit();return mail_payload(row)
  if not settings(db)['values']['remit_address'] or not m.gmail_configured() or os.getenv('LMS_GMAIL_SENDER','').lower()!=SENDER:
   row.status='Failed';row.error='Set the remittance address and authorize accounting@limitlessmechanical.net before sending';db.commit();return mail_payload(row)
  inv=db.scalar(select(m.InvoiceRecord).where(m.InvoiceRecord.id==row.invoice_id).with_for_update())
  if not freeze_invoice(db,inv)['commercial'].get('remit_address'):
   row.status='Failed';row.error='This issued invoice has no remittance address. Review the document before preparing a corrected invoice.';db.commit();return mail_payload(row)
  if row.purpose.startswith('Reminder') and (m.invoice_ledger(db,inv)['balance']==0 or paused(db,inv.id)):
   row.status='Cancelled';row.error='Invoice paid or collection follow-up paused';db.commit();return mail_payload(row)
  row.status='Sending';row.attempted_at=now();db.commit()
  # Commit intent BEFORE calling Gmail. An interrupted send is never automatically retried.
  try:
   provider=m.gmail_send(row.recipient,row.subject,row.message,row.pdf,'Invoice-'+inv.number+'.pdf',message_id='<lms-'+row.id+'@limitlessmechanical.net>')
  except Exception:
   row.status='Unconfirmed';row.error='Provider result uncertain. Check accounting Sent Mail; do not blindly retry.';db.commit();return mail_payload(row)
  row.status='Sent';row.provider_id=provider;row.error=''
  db.add(m.InvoiceEmailEvent(invoice_id=row.invoice_id,recipient=row.recipient,subject=row.subject,sent_by=row.created_by,gmail_message_id=provider))
  m.log(db,'INVOICE_EMAIL_ACCEPTED_BY_PROVIDER','invoice',row.invoice_id,provider);db.commit();return mail_payload(row)


def check_payload(db,row):
 allocations=[];allocated=Decimal('0')
 for a in db.scalars(select(CheckAllocation).where(CheckAllocation.check_id==row.id)):
  payment=db.get(m.InvoicePayment,a.payment_id);inv=db.get(m.InvoiceRecord,payment.invoice_id);reversed_amount=sum((x.amount for x in db.scalars(select(m.PaymentReversal).where(m.PaymentReversal.payment_id==payment.id))),Decimal('0'));allocated+=max(Decimal('0'),payment.amount-reversed_amount)
  allocations.append({'payment_id':payment.id,'invoice_id':inv.id,'invoice_number':inv.number,'amount':str(payment.amount)})
 return {'id':row.id,'customer_id':row.customer_id,'customer':db.get(m.Customer,row.customer_id).name,'check_number':row.check_number,'amount':str(row.amount),'received_on':str(row.received_on),'deposited_on':str(row.deposited_on or ''),'status':row.status,'revision':row.revision,'allocated':str(allocated),'unapplied':str(row.amount-allocated) if row.status!='Returned' else '0.00','allocations':allocations,'note':row.note}


def paused(db,invoice_id):
 r=db.get(CollectionPause,invoice_id);return bool(r and r.until>=date.today())


def prepare_reminders(db,user):
 cfg=settings(db)['values'];prepared=[]
 if not cfg['reminders_enabled']:return prepared
 for inv in db.scalars(select(m.InvoiceRecord).where(m.InvoiceRecord.status!='Draft',m.InvoiceRecord.due_on!=None)):
  balance=m.invoice_ledger(db,inv)['balance']
  if not balance or paused(db,inv.id):continue
  overdue=(date.today()-inv.due_on).days
  steps=[d for d in cfg['reminder_days'] if overdue>=d]
  if not steps:continue
  day=max(steps);request=f'reminder:{inv.id}:{day}'
  if db.scalar(select(MailOutbox.id).where(MailOutbox.request_id==request)):continue
  customer=db.get(m.Customer,db.get(m.Job,inv.job_id).customer_id)
  contacts=list(db.scalars(select(CustomerContact).where(CustomerContact.customer_id==customer.id,CustomerContact.purpose=='Billing',CustomerContact.active==True)))
  recipients=sorted(set(c.email for c in contacts)) or ([customer.email] if customer.email else [])
  for recipient in recipients:
   key=request+':'+hashlib.sha256(recipient.encode()).hexdigest()[:12]
   if db.scalar(select(MailOutbox.id).where(MailOutbox.request_id==key)):continue
   row=enqueue(db,inv,user,key,recipient,'Invoice '+inv.number+' — payment reminder',f'Our records show ${balance:,.2f} outstanding on invoice {inv.number}. Please contact {SENDER} if payment has already been sent.',purpose='Reminder '+str(day));prepared.append(row.id)
 return prepared


def attach_billing(app):
 m.Base.metadata.create_all(m.engine)
 with m.engine.begin() as c:c.exec_driver_sql("INSERT INTO invoice_sequence_v111 (key,last_number) VALUES ('invoice',0) ON CONFLICT(key) DO NOTHING")
 @app.get('/billing-desk')
 def page(user=Depends(p.owner)):return FileResponse(m.ROOT/'billing-desk.html')
 @app.get('/billing-desk.js')
 def script(user=Depends(p.owner)):return FileResponse(m.ROOT/'billing-desk.js',media_type='application/javascript')
 @app.get('/api/admin/billing/settings')
 def get_settings(user=Depends(p.owner)):
  with Session(m.engine) as db:return settings(db)
 @app.put('/api/admin/billing/settings')
 def save_settings(data:SettingsIn,user=Depends(p.owner)):
  with Session(m.engine) as db:
   m.lock_time_user(db,user['id']);r=db.get(BillingSettings,'company')
   if data.expected_revision!=(r.revision if r else 0):raise HTTPException(409,'Settings changed; reload')
   if not r:r=BillingSettings(key='company');db.add(r)
   else:r.revision+=1
   r.json=data.model_dump_json(exclude={'expected_revision'});m.log(db,'SAVE_BILLING_SETTINGS','company','billing',r.json);db.commit();return settings(db)
 @app.post('/api/admin/invoices/next-number')
 def number(user=Depends(p.owner)):
  with Session(m.engine) as db:n=next_number(db);db.commit();return {'number':n}
 @app.get('/api/admin/invoices/{invoice_id}/details')
 def get_meta(invoice_id:str,user=Depends(p.owner)):
  with Session(m.engine) as db:
   inv=db.get(m.InvoiceRecord,invoice_id)
   if not inv:raise HTTPException(404,'Invoice not found')
   return metadata(db,inv)
 @app.put('/api/admin/invoices/{invoice_id}/details')
 def save_meta(invoice_id:str,data:InvoiceMetaIn,user=Depends(p.owner)):
  with Session(m.engine) as db:
   inv=db.scalar(select(m.InvoiceRecord).where(m.InvoiceRecord.id==invoice_id).with_for_update())
   if not inv:raise HTTPException(404,'Invoice not found')
   if inv.status!='Draft':raise HTTPException(409,'Issued invoice details are locked')
   r=db.get(CommercialInvoice,invoice_id)
   if data.expected_revision!=(r.revision if r else 0):raise HTTPException(409,'Invoice details changed; reload')
   if not r:r=CommercialInvoice(invoice_id=invoice_id,json='{}');db.add(r)
   else:r.revision+=1
   values=json.loads(r.json) if r.json else {}
   values.update(data.model_dump(mode='json',exclude={'expected_revision'},exclude_unset=True))
   if (data.include_service_fee or data.taxable is not None) and not m.invoice_lines(db,inv) and inv.amount>0:
    db.add(m.InvoiceLine(invoice_id=inv.id,position=1,description='Services & Materials',quantity=1,unit_price=inv.amount,tax_rate=0,subtotal=inv.amount,tax_amount=0));db.flush()
   if data.include_service_fee is not None:
    fees=[line for line in m.invoice_lines(db,inv) if line.description=='Service Fee']
    if data.include_service_fee and not fees:
     current=m.invoice_lines(db,inv);rate=data.tax_rate if data.taxable else Decimal('0')
     if data.taxable is None:rate=next((line.tax_rate for line in current if line.tax_rate>0),Decimal('0'))
     item=m.InvoiceLineInput(description='Service Fee',quantity=1,unit_price=100,tax_rate=rate)
     net,tax=m.line_calculation(item)
     db.add(m.InvoiceLine(invoice_id=inv.id,position=max((line.position for line in current),default=0)+1,description='Service Fee',quantity=1,unit_price=100,tax_rate=rate,subtotal=net,tax_amount=tax));inv.amount+=net+tax
    elif not data.include_service_fee:
     for line in fees:inv.amount-=line.subtotal+line.tax_amount;db.delete(line)
    db.flush()
    p.validate_invoice_margin(db,inv)
   if data.taxable is not None:
    rate=data.tax_rate if data.taxable else Decimal('0')
    if data.taxable and rate<=0:raise HTTPException(422,'Enter a tax rate or uncheck Taxable')
    lines=m.invoice_lines(db,inv)
    if not lines and data.taxable:raise HTTPException(422,'Add invoice lines before applying tax')
    if lines:
     # Update tax fields in place: preserve mileage links and the reviewed pre-tax subtotal.
     net=tax=Decimal('0')
     for line in lines:
      item=m.InvoiceLineInput(description=line.description,quantity=line.quantity,unit_price=line.unit_price,tax_rate=rate)
      line.subtotal,line.tax_amount=m.line_calculation(item);line.tax_rate=rate;net+=line.subtotal;tax+=line.tax_amount
     inv.amount=net+tax
    values['tax_rate']=str(rate)
   r.json=p.dump(values);m.log(db,'SAVE_INVOICE_OPTIONS','invoice',inv.id,p.dump(values));db.commit();return metadata(db,inv)
 @app.get('/api/admin/customer-contacts')
 def contacts(user=Depends(p.owner)):
  with Session(m.engine) as db:return [{'id':r.id,'customer_id':r.customer_id,'location_id':r.location_id,'name':r.name,'email':r.email,'purpose':r.purpose} for r in db.scalars(select(CustomerContact).where(CustomerContact.active==True))]
 @app.post('/api/admin/customer-contacts',status_code=201)
 def add_contact(data:ContactIn,user=Depends(p.owner)):
  with Session(m.engine) as db:
   if not db.get(m.Customer,data.customer_id):raise HTTPException(422,'Customer not found')
   if data.location_id and (not db.get(m.Location,data.location_id) or db.get(m.Location,data.location_id).customer_id!=data.customer_id):raise HTTPException(422,'Site belongs to a different customer')
   r=CustomerContact(**data.model_dump(exclude={'email'}),email=email(data.email));db.add(r);db.flush();db.commit();return {'id':r.id}
 @app.get('/api/admin/mail-outbox')
 def outbox(user=Depends(p.owner)):
  with Session(m.engine) as db:return [mail_payload(r) for r in db.scalars(select(MailOutbox).order_by(MailOutbox.created_at.desc()))]
 @app.post('/api/admin/invoices/{invoice_id}/email')
 def queue(invoice_id:str,data:SendIn,user=Depends(p.owner)):
  with Session(m.engine) as db:
   inv=db.scalar(select(m.InvoiceRecord).where(m.InvoiceRecord.id==invoice_id).with_for_update())
   if not inv:raise HTTPException(404,'Invoice not found')
   r=enqueue(db,inv,user,data.request_id,data.recipient,data.subject,data.message,data.confirm_resend);db.commit();return mail_payload(r)
 @app.get('/api/admin/mail-outbox/{mail_id}/preview')
 def preview_mail(mail_id:str,user=Depends(p.owner)):
  with Session(m.engine) as db:
   r=db.get(MailOutbox,mail_id)
   if not r:raise HTTPException(404,'Email not found')
   return StreamingResponse(io.BytesIO(r.pdf),media_type='application/pdf',headers={'Cache-Control':'private, no-store'})
 @app.post('/api/admin/mail-outbox/{mail_id}/process')
 def send_mail(mail_id:str,user=Depends(p.owner)):return process_mail(mail_id)
 @app.post('/api/admin/mail-outbox/{mail_id}/resolve')
 def resolve(mail_id:str,data:ResolveMailIn,user=Depends(p.owner)):
  with Session(m.engine) as db:
   r=db.scalar(select(MailOutbox).where(MailOutbox.id==mail_id).with_for_update())
   if not r or mail_payload(r)['status']!='Unconfirmed':raise HTTPException(409,'Only an unconfirmed send can be resolved here')
   r.status='Sent' if data.action=='Confirmed sent' else 'Failed';r.provider_id=data.provider_id;r.error='Manual review: '+data.reason;m.log(db,'RESOLVE_UNCONFIRMED_EMAIL','invoice',r.invoice_id,data.model_dump_json());db.commit();return mail_payload(r)
 @app.post('/api/admin/collections/prepare')
 def reminders(user=Depends(p.owner)):
  with Session(m.engine) as db:ids=prepare_reminders(db,user);db.commit();return {'prepared':ids,'test_mode':settings(db)['values']['test_mode']}
 @app.put('/api/admin/invoices/{invoice_id}/collection-pause')
 def pause(invoice_id:str,data:PauseIn,user=Depends(p.owner)):
  with Session(m.engine) as db:
   if not db.get(m.InvoiceRecord,invoice_id):raise HTTPException(404,'Invoice not found')
   r=db.get(CollectionPause,invoice_id)
   if not r:r=CollectionPause(invoice_id=invoice_id);db.add(r)
   r.until=data.until;r.reason=data.reason;db.commit();return {'paused_until':str(r.until)}
 @app.get('/api/admin/checks')
 def checks(user=Depends(p.owner)):
  with Session(m.engine) as db:return [check_payload(db,r) for r in db.scalars(select(CheckReceipt).order_by(CheckReceipt.received_on.desc()))]
 @app.post('/api/admin/checks',status_code=201)
 def receive(data:CheckIn,user=Depends(p.owner)):
  with Session(m.engine) as db:
   customer=db.scalar(select(m.Customer).where(m.Customer.id==data.customer_id).with_for_update())
   if not customer:raise HTTPException(422,'Customer not found')
   number=data.check_number.strip().upper()
   if not number:raise HTTPException(422,'Enter a check number')
   if db.scalar(select(CheckReceipt).where(CheckReceipt.customer_id==data.customer_id,CheckReceipt.check_number==number)):raise HTTPException(409,'This customer check is already recorded')
   r=CheckReceipt(**data.model_dump(exclude={'check_number'}),check_number=number);db.add(r);db.flush();m.log(db,'RECEIVE_CHECK','customer',customer.id,str(r.amount));db.commit();return check_payload(db,r)
 @app.post('/api/admin/checks/{check_id}/allocate')
 def allocate(check_id:str,data:AllocateIn,user=Depends(p.owner)):
  with Session(m.engine) as db:
   r=db.scalar(select(CheckReceipt).where(CheckReceipt.id==check_id).with_for_update())
   if not r:raise HTTPException(404,'Check not found')
   existing=db.get(CheckAllocation,str(data.request_id))
   if existing:
    payment=db.get(m.InvoicePayment,existing.payment_id)
    if existing.check_id!=check_id or payment.invoice_id!=data.invoice_id or payment.amount!=data.amount:raise HTTPException(409,'Allocation request reused for different values')
    return check_payload(db,r)
   if r.status=='Returned':raise HTTPException(409,'Returned checks cannot be allocated')
   inv=db.scalar(select(m.InvoiceRecord).where(m.InvoiceRecord.id==data.invoice_id).with_for_update())
   if not inv or inv.status=='Draft' or db.get(m.Job,inv.job_id).customer_id!=r.customer_id:raise HTTPException(422,'Choose an issued invoice for this customer')
   available=Decimal(check_payload(db,r)['unapplied'])
   if data.amount>available or data.amount>m.invoice_ledger(db,inv)['balance']:raise HTTPException(422,'Allocation exceeds the check remainder or invoice balance')
   payment=m.InvoicePayment(invoice_id=inv.id,amount=data.amount,paid_on=r.received_on,reference='CHK:'+r.id+':'+str(data.request_id));db.add(payment);db.flush();db.add(CheckAllocation(request_id=str(data.request_id),check_id=r.id,payment_id=payment.id));m.refresh_invoice_status(inv,m.invoice_ledger(db,inv));r.revision+=1;m.log(db,'ALLOCATE_CUSTOMER_CHECK','invoice',inv.id,str(data.amount));db.commit();return check_payload(db,r)
 @app.post('/api/admin/checks/{check_id}/state')
 def check_state(check_id:str,data:CheckStateIn,user=Depends(p.owner)):
  with Session(m.engine) as db:
   r=db.scalar(select(CheckReceipt).where(CheckReceipt.id==check_id).with_for_update())
   if not r or r.revision!=data.expected_revision:raise HTTPException(409,'Check changed; reload')
   if r.status=='Returned':raise HTTPException(409,'This check was already returned')
   if data.action_on<r.received_on:raise HTTPException(422,'Action date precedes receipt')
   if data.status=='Deposited':r.deposited_on=data.action_on
   else:
    for a in db.scalars(select(CheckAllocation).where(CheckAllocation.check_id==r.id)):
     payment=db.get(m.InvoicePayment,a.payment_id);inv=db.scalar(select(m.InvoiceRecord).where(m.InvoiceRecord.id==payment.invoice_id).with_for_update());already=sum((x.amount for x in db.scalars(select(m.PaymentReversal).where(m.PaymentReversal.payment_id==payment.id))),Decimal('0'))
     if payment.amount>already:db.add(m.PaymentReversal(payment_id=payment.id,reference='RETURN:'+r.id+':'+payment.id,amount=payment.amount-already,reason=data.reason,reversed_on=data.action_on,created_by=user['id']));db.flush();m.refresh_invoice_status(inv,m.invoice_ledger(db,inv))
   db.add(CheckLifecycle(check_id=r.id,action=data.status,action_on=data.action_on,reason=data.reason));r.status=data.status;r.revision+=1;r.note+='\n'+data.reason;m.log(db,'UPDATE_CHECK_STATE','customer',r.customer_id,data.model_dump_json());db.commit();return check_payload(db,r)
 @app.post('/api/admin/wave-opening-balances')
 def opening(data:OpeningIn,user=Depends(p.owner)):
  # Explicit mapping template: no guessed Wave columns, no customer/job auto-creation.
  with Session(m.engine) as db:
   m.lock_time_user(db,user['id']);validated=[];errors=[];seen=set();numbers=set()
   for index,row in enumerate(data.rows,1):
    try:
     ref=str(row['source_reference']).strip();number=str(row['invoice_number']).strip();job=db.scalar(select(m.Job).where(m.Job.job_number==row['job_number']));amount=Decimal(str(row['outstanding_amount']));issued=date.fromisoformat(row['issued_on']);due=date.fromisoformat(row['due_on'])
     if not ref or len(ref)>100 or not number or len(number)>70 or not job or not amount.is_finite() or amount<=0 or amount!=p.money(amount) or due<issued:raise ValueError('Invalid job, dates, reference or positive outstanding amount')
     if ref in seen or number in numbers or db.scalar(select(WaveOpening).where(WaveOpening.source_reference==ref)) or db.scalar(select(m.InvoiceRecord.id).where(m.InvoiceRecord.number==number)):raise ValueError('Duplicate source reference or invoice number')
     seen.add(ref);numbers.add(number);validated.append((ref,number,job.id,amount,issued,due))
    except Exception as exc:errors.append({'row':index,'error':str(exc)[:150]})
   if errors or not data.commit:return {'valid_rows':len(validated),'errors':errors,'committed':False}
   for ref,number,job_id,amount,issued,due in validated:
    inv=m.InvoiceRecord(job_id=job_id,number=number,amount=amount,status='Issued',issued_on=issued,due_on=due);db.add(inv);db.flush();db.add(WaveOpening(invoice_id=inv.id,source_reference=ref));details=CommercialInvoice(invoice_id=inv.id,json=p.dump({'note':'Wave opening outstanding balance only; not the original invoice total. Use the original Wave document for history.','opening_balance_only':True}));db.add(details)
   m.log(db,'IMPORT_WAVE_OPENING_BALANCES','accounting','wave',str(len(validated)));db.commit();return {'valid_rows':len(validated),'errors':[],'committed':True}

