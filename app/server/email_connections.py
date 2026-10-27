"""Owner-managed Gmail sending connections. Tokens never leave the server."""
import hashlib,json,os,secrets
from datetime import datetime,timedelta,timezone
from fastapi import Cookie,Depends,HTTPException,Request
from fastapi.responses import FileResponse,RedirectResponse
from sqlalchemy import String,Text,DateTime,ForeignKey,select
from sqlalchemy.orm import Mapped,mapped_column,Session
from cryptography.fernet import Fernet,InvalidToken
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import credentials,id_token
from google_auth_oauthlib.flow import Flow
from . import main as m
from .call_pricing import owner

INVOICE_SENDER='accounting@limitlessmechanical.net'
QUOTE_SENDERS=('redwine.david@limitlessmechanical.net','redwine.adrian@limitlessmechanical.net')
SENDERS=(INVOICE_SENDER,*QUOTE_SENDERS)
SCOPES=('openid','https://www.googleapis.com/auth/userinfo.email','https://www.googleapis.com/auth/gmail.send')
COOKIE='lms_email_connect'
def now():return datetime.now(timezone.utc)
def utc(value):return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
def digest(value):return hashlib.sha256(value.encode()).hexdigest()

class EmailConnection(m.Base):
 __tablename__='email_connections'
 sender:Mapped[str]=mapped_column(String(250),primary_key=True)
 google_subject:Mapped[str]=mapped_column(String(250),nullable=False)
 encrypted_token:Mapped[str]=mapped_column(Text,nullable=False)
 connected_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
 connected_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)
 active:Mapped[bool]=mapped_column(default=True,nullable=False)

class EmailOAuthState(m.Base):
 __tablename__='email_oauth_states'
 state_hash:Mapped[str]=mapped_column(String(64),primary_key=True)
 browser_hash:Mapped[str]=mapped_column(String(64),nullable=False)
 sender:Mapped[str]=mapped_column(String(250),nullable=False)
 user_id:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
 encrypted_verifier:Mapped[str]=mapped_column(Text,nullable=False)
 expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
 consumed:Mapped[bool]=mapped_column(default=False,nullable=False)

def cipher():
 try:return Fernet(os.environ.get('LMS_EMAIL_TOKEN_KEY','').encode())
 except (ValueError,TypeError):raise HTTPException(503,'Email connections need their secure server setup completed')

def redirect_uri():
 origin=os.environ.get('APP_PUBLIC_URL','').rstrip('/')
 if not origin.startswith('https://'):raise HTTPException(503,'The public HTTPS address must be configured before connecting email')
 return origin+'/api/admin/email/oauth/callback'

def setup_missing():
 missing=[name for name in ('LMS_GMAIL_CLIENT_ID','LMS_GMAIL_CLIENT_SECRET','LMS_EMAIL_TOKEN_KEY') if not os.environ.get(name)]
 try:cipher();redirect_uri()
 except HTTPException:
  if not missing:missing.append('valid secure email configuration')
 return missing

def flow(verifier,state=None):
 if setup_missing():raise HTTPException(503,'Complete the secure Google email setup before connecting a mailbox')
 config={'web':{'client_id':os.environ['LMS_GMAIL_CLIENT_ID'],'client_secret':os.environ['LMS_GMAIL_CLIENT_SECRET'],
     'auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token'}}
 return Flow.from_client_config(config,scopes=SCOPES,state=state,code_verifier=verifier,redirect_uri=redirect_uri())

def connected(db,sender):
 row=db.get(EmailConnection,sender)
 return bool(sender in SENDERS and row and row.active and not setup_missing())

def access_token(sender):
 if sender not in SENDERS:raise RuntimeError('Sender is not allowed')
 with Session(m.engine) as db:
  row=db.get(EmailConnection,sender)
  if not row or not row.active:raise RuntimeError('Connect this sending account first')
  try:token=json.loads(cipher().decrypt(row.encrypted_token.encode()))
  except (InvalidToken,ValueError,HTTPException):raise RuntimeError('Email connection needs to be restored') from None
  creds=credentials.Credentials(token=None,refresh_token=token['refresh_token'],token_uri='https://oauth2.googleapis.com/token',
      client_id=os.environ.get('LMS_GMAIL_CLIENT_ID'),client_secret=os.environ.get('LMS_GMAIL_CLIENT_SECRET'),scopes=SCOPES)
  try:creds.refresh(GoogleRequest())
  except Exception:raise RuntimeError('Email authorization expired; reconnect this account') from None
  return creds.token

def verify_identity(creds,sender):
 claims=id_token.verify_oauth2_token(creds.id_token,GoogleRequest(),os.environ['LMS_GMAIL_CLIENT_ID'])
 if claims.get('email','').lower()!=sender or claims.get('email_verified') is not True or not claims.get('sub'):
  raise ValueError('Wrong Google account')
 granted=set(creds.granted_scopes or creds.scopes or [])
 if 'https://www.googleapis.com/auth/gmail.send' not in granted or not creds.refresh_token:
  raise ValueError('Sending permission or offline access was not granted')
 return claims

def attach_email_connections(app):
 m.Base.metadata.create_all(m.engine)
 @app.get('/email-center')
 def page(user=Depends(owner)):return FileResponse(m.ROOT/'email-center.html')
 @app.get('/email-center.js')
 def script(user=Depends(owner)):return FileResponse(m.ROOT/'email-center.js',media_type='application/javascript')
 @app.get('/api/admin/email/connections')
 def status(user=Depends(owner)):
  from .billing_v111 import settings
  with Session(m.engine) as db:
   rows=[]
   for sender in SENDERS:
    row=db.get(EmailConnection,sender)
    rows.append({'sender':sender,'purpose':'Invoices' if sender==INVOICE_SENDER else 'Quotes',
       'connected':connected(db,sender),'connected_at':utc(row.connected_at).isoformat() if row and row.active else None})
   cfg=settings(db)['values']
   return {'accounts':rows,'setup_required':bool(setup_missing()),'preview_mode':cfg['test_mode'],
      'server_sending_enabled':os.environ.get('LMS_ALLOW_EXTERNAL_EMAIL')=='1',
      'remittance_address_ready':bool(cfg['remit_address'].strip()),'redirect_uri':redirect_uri() if os.environ.get('APP_PUBLIC_URL','').startswith('https://') else None}
 @app.post('/api/admin/email/connections/{sender}/connect')
 def begin(sender:str,user=Depends(owner)):
  if sender not in SENDERS:raise HTTPException(422,'Choose one of the approved sending accounts')
  state=secrets.token_urlsafe(40);browser=secrets.token_urlsafe(40);verifier=secrets.token_urlsafe(64)
  f=flow(verifier,state)
  url,_=f.authorization_url(access_type='offline',prompt='consent select_account',login_hint=sender,state=state)
  with Session(m.engine) as db:
   db.add(EmailOAuthState(state_hash=digest(state),browser_hash=digest(browser),sender=sender,user_id=user['id'],
      encrypted_verifier=cipher().encrypt(verifier.encode()).decode(),expires_at=now()+timedelta(minutes=10)))
   db.commit()
  from fastapi.responses import JSONResponse
  response=JSONResponse({'authorization_url':url})
  response.set_cookie(COOKIE,browser,max_age=600,secure=True,httponly=True,samesite='lax',path='/api/admin/email/oauth')
  return response
 @app.get('/api/admin/email/oauth/callback')
 def callback(request:Request,state:str='',code:str='',error:str='',lms_email_connect:str|None=Cookie(default=None)):
  # The regular CRM cookie is SameSite=Strict. A short-lived, browser-bound state cookie
  # authenticates this cross-site callback without weakening the CRM login cookie.
  with Session(m.engine) as db:
   row=db.scalar(select(EmailOAuthState).where(EmailOAuthState.state_hash==digest(state)).with_for_update())
   if not row or row.consumed or utc(row.expires_at)<now() or not lms_email_connect or not secrets.compare_digest(row.browser_hash,digest(lms_email_connect)):
    raise HTTPException(400,'Email connection expired or belongs to another browser. Start again from Email accounts.')
   user=db.get(m.User,row.user_id)
   if not user or not user.active or user.role!='owner':raise HTTPException(403,'Owner access is required')
   row.consumed=True;sender=row.sender;user_id=user.id;verifier=cipher().decrypt(row.encrypted_verifier.encode()).decode();row.encrypted_verifier=''
   db.commit()
   if error or not code:
    response=RedirectResponse('/email-center?connection=cancelled',status_code=303)
   else:
    try:
     f=flow(verifier,state);f.fetch_token(code=code);creds=f.credentials;claims=verify_identity(creds,sender)
    except Exception:
     response=RedirectResponse('/email-center?connection=failed',status_code=303)
    else:
     user=db.get(m.User,user_id)
     if not user or not user.active or user.role!='owner':raise HTTPException(403,'Owner access is required')
     connection=db.get(EmailConnection,sender)
     if not connection:connection=EmailConnection(sender=sender);db.add(connection)
     connection.google_subject=claims['sub'];connection.encrypted_token=cipher().encrypt(json.dumps({'refresh_token':creds.refresh_token}).encode()).decode()
     connection.connected_by=user_id;connection.connected_at=now();connection.active=True
     m.log(db,'CONNECT_EMAIL','email_connection',sender,'Google sending authorization verified');db.commit()
     response=RedirectResponse('/email-center?connection=connected',status_code=303)
  response.delete_cookie(COOKIE,path='/api/admin/email/oauth');response.headers['Referrer-Policy']='no-referrer';response.headers['Cache-Control']='no-store'
  return response
 @app.post('/api/admin/email/connections/{sender}/disconnect')
 def disconnect(sender:str,user=Depends(owner)):
  if sender not in SENDERS:raise HTTPException(422,'Unknown sending account')
  with Session(m.engine) as db:
   row=db.get(EmailConnection,sender)
   if row:row.active=False;row.encrypted_token='';m.log(db,'DISCONNECT_EMAIL','email_connection',sender);db.commit()
  return {'disconnected':True}

