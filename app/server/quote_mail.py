"""Reviewed quote attachments, explicit sends and durable delivery history."""
import hashlib,io,json,os,uuid
from datetime import datetime,timedelta,timezone
from fastapi import Depends,HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,Field
from sqlalchemy import String,Text,DateTime,ForeignKey,LargeBinary,select
from sqlalchemy.orm import Mapped,mapped_column,Session
from . import main as m,billing_v111 as b,proposals_pm_v15 as q
from .call_pricing import owner,dump
from .email_connections import QUOTE_SENDERS

class QuoteMail(m.Base):
 __tablename__='quote_mail_outbox'
 id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
 proposal_id:Mapped[str]=mapped_column(ForeignKey('proposals.id',ondelete='RESTRICT'),index=True,nullable=False)
 request_id:Mapped[str]=mapped_column(String(36),unique=True,nullable=False)
 sender:Mapped[str]=mapped_column(String(250),nullable=False)
 recipient:Mapped[str]=mapped_column(String(250),nullable=False)
 subject:Mapped[str]=mapped_column(String(250),nullable=False)
 message:Mapped[str]=mapped_column(Text,nullable=False)
 pdf:Mapped[bytes]=mapped_column(LargeBinary,nullable=False)
 source_hash:Mapped[str]=mapped_column(String(64),nullable=False)
 fingerprint:Mapped[str]=mapped_column(String(64),nullable=False)
 status:Mapped[str]=mapped_column(String(20),nullable=False)
 provider_id:Mapped[str]=mapped_column(String(150),default='',nullable=False)
 error:Mapped[str]=mapped_column(Text,default='',nullable=False)
 created_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
 created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=b.now,nullable=False)
 attempted_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))

class QuoteSendIn(BaseModel):
 request_id:uuid.UUID
 sender:str=Field(max_length=250)
 recipient:str=Field(max_length=250)
 subject:str=Field(min_length=1,max_length=250)
 message:str=Field(min_length=1,max_length=5000)
 confirm_resend:bool=False
 expected_source_hash:str=Field(min_length=64,max_length=64)

def source_hash(db,quote):return hashlib.sha256(dump(q.quote_out(db,quote)).encode()).hexdigest()
def payload(row):
 status=row.status
 if status=='Sending' and row.attempted_at and b.now()-b.utc(row.attempted_at)>timedelta(minutes=5):status='Unconfirmed'
 return {'id':row.id,'proposal_id':row.proposal_id,'sender':row.sender,'recipient':row.recipient,'subject':row.subject,
    'status':status,'provider_id':row.provider_id,'error':row.error,'created_at':b.utc(row.created_at).isoformat(),
    'preview_url':'/api/admin/quote-mail/'+row.id+'/preview'}

def process(mail_id):
 from .invoice_mail import configured
 with Session(m.engine) as db:
  row=db.scalar(select(QuoteMail).where(QuoteMail.id==mail_id).with_for_update())
  if not row:raise HTTPException(404,'Quote email not found')
  if row.status!='Queued':return payload(row)
  if b.settings(db)['values']['test_mode'] or os.getenv('LMS_ALLOW_EXTERNAL_EMAIL')!='1':
   row.status='Preview';db.commit();return payload(row)
  if row.sender not in QUOTE_SENDERS or not configured(row.sender):
   row.status='Failed';row.error='Connect this quote sending account before preparing another email.';db.commit();return payload(row)
  quote=db.scalar(select(m.Proposal).where(m.Proposal.id==row.proposal_id).with_for_update())
  if not quote or source_hash(db,quote)!=row.source_hash:
   row.status='Cancelled';row.error='Quote changed after preparation. Review it and prepare a fresh copy.';db.commit();return payload(row)
  row.status='Sending';row.attempted_at=b.now();db.commit()
  try:
   provider=m.gmail_send(row.recipient,row.subject,row.message,row.pdf,'Quote-'+quote.number+'.pdf',
       sender=row.sender,message_id='<lms-quote-'+row.id+'@limitlessmechanical.net>')
   if not provider:raise RuntimeError('Missing provider confirmation')
  except Exception:
   row.status='Unconfirmed';row.error='Sending result uncertain. Check this sender’s Sent Mail before recording a result.';db.commit();return payload(row)
  row.status='Sent';row.provider_id=provider;row.error=''
  m.log(db,'SEND_QUOTE_EMAIL','proposal',quote.id,dump({'sender':row.sender,'recipient':row.recipient,'provider_id':provider}))
  db.commit();return payload(row)

def attach_quote_mail(app):
 m.Base.metadata.create_all(m.engine)
 @app.get('/api/admin/quote-mail/quotes')
 def quotes(user=Depends(owner)):
  with Session(m.engine) as db:
   return [{'id':quote.id,'number':quote.number,'title':quote.title,'status':quote.status,
      'customer':db.get(m.Customer,db.get(m.Job,quote.job_id).customer_id).name,
      'source_hash':source_hash(db,quote),'pdf_url':'/api/proposals/'+quote.id+'/pdf'}
     for quote in db.scalars(select(m.Proposal).order_by(m.Proposal.number))]
 @app.get('/api/admin/quote-mail')
 def history(user=Depends(owner)):
  with Session(m.engine) as db:return [payload(row) for row in db.scalars(select(QuoteMail).order_by(QuoteMail.created_at.desc()))]
 @app.post('/api/admin/quote-mail/{proposal_id}/prepare')
 def prepare(proposal_id:str,data:QuoteSendIn,user=Depends(owner)):
  if data.sender not in QUOTE_SENDERS:raise HTTPException(422,'Quotes must use David’s or Adrian’s approved email account')
  recipient=b.email(data.recipient);request_id=str(data.request_id)
  fingerprint=hashlib.sha256(dump([proposal_id,data.sender,recipient,data.subject,data.message,data.expected_source_hash]).encode()).hexdigest()
  with Session(m.engine) as db:
   quote=db.scalar(select(m.Proposal).where(m.Proposal.id==proposal_id).with_for_update())
   if not quote:raise HTTPException(404,'Quote not found')
   prior=db.scalar(select(QuoteMail).where(QuoteMail.request_id==request_id))
   if prior:
    if prior.fingerprint!=fingerprint:raise HTTPException(409,'This email request was already used with different content')
    return payload(prior)
   current=source_hash(db,quote)
   if current!=data.expected_source_hash:raise HTTPException(409,'Quote changed. Reload and review its current PDF before preparing email.')
   same=list(db.scalars(select(QuoteMail).where(QuoteMail.proposal_id==proposal_id,QuoteMail.recipient==recipient).order_by(QuoteMail.created_at.desc())))
   if any(payload(row)['status'] in ('Sending','Unconfirmed') for row in same):raise HTTPException(409,'Resolve the earlier uncertain delivery before sending another copy')
   if any(row.status=='Queued' for row in same):raise HTTPException(409,'A copy is already queued. Process it before preparing another.')
   if any(row.status=='Sent' for row in same) and not data.confirm_resend:raise HTTPException(409,'Already sent. Explicitly confirm another copy.')
   for old in same:
    if old.status=='Preview':old.status='Superseded'
   row=QuoteMail(proposal_id=quote.id,request_id=request_id,sender=data.sender,recipient=recipient,subject=data.subject,
      message=data.message,pdf=q.proposal_pdf(db,quote,db.get(q.QuoteDetail,quote.id)),source_hash=current,
      fingerprint=fingerprint,status='Preview' if b.settings(db)['values']['test_mode'] else 'Queued',created_by=user['id'])
   db.add(row);db.flush();m.log(db,'PREPARE_QUOTE_EMAIL','proposal',quote.id,row.id);db.commit();return payload(row)
 @app.get('/api/admin/quote-mail/{mail_id}/preview')
 def preview(mail_id:str,user=Depends(owner)):
  with Session(m.engine) as db:
   row=db.get(QuoteMail,mail_id)
   if not row:raise HTTPException(404,'Quote email not found')
   return StreamingResponse(io.BytesIO(row.pdf),media_type='application/pdf',headers={'Cache-Control':'private, no-store'})
 @app.post('/api/admin/quote-mail/{mail_id}/process')
 def send(mail_id:str,user=Depends(owner)):return process(mail_id)
 @app.post('/api/admin/quote-mail/{mail_id}/resolve')
 def resolve(mail_id:str,data:b.ResolveMailIn,user=Depends(owner)):
  with Session(m.engine) as db:
   row=db.scalar(select(QuoteMail).where(QuoteMail.id==mail_id).with_for_update())
   if not row or payload(row)['status']!='Unconfirmed':raise HTTPException(409,'Only an uncertain send can be reviewed')
   row.status='Sent' if data.action=='Confirmed sent' else 'Failed';row.provider_id=data.provider_id;row.error='Manual review: '+data.reason
   m.log(db,'RESOLVE_QUOTE_EMAIL','proposal',row.proposal_id,data.model_dump_json());db.commit();return payload(row)
 @app.post('/api/admin/quote-mail/{mail_id}/cancel')
 def cancel(mail_id:str,user=Depends(owner)):
  with Session(m.engine) as db:
   row=db.scalar(select(QuoteMail).where(QuoteMail.id==mail_id).with_for_update())
   if not row or row.status not in ('Preview','Queued','Failed'):raise HTTPException(409,'Only unsent email can be cancelled')
   row.status='Cancelled';m.log(db,'CANCEL_QUOTE_EMAIL','proposal',row.proposal_id,row.id);db.commit();return payload(row)

