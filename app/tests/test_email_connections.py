"""Mailbox identity, OAuth state isolation, encrypted tokens and quote delivery guards."""
import json,uuid
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit,parse_qs
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select,delete
from sqlalchemy.orm import Session
from test_time_clock import setup_clock
from server import main as m,email_connections as e,quote_mail as q,billing_v111 as b,invoice_mail as transport

@pytest.fixture
def email_setup(setup_clock,monkeypatch):
 monkeypatch.setenv('LMS_EMAIL_TOKEN_KEY',Fernet.generate_key().decode())
 monkeypatch.setenv('LMS_GMAIL_CLIENT_ID','test-client.apps.googleusercontent.com')
 monkeypatch.setenv('LMS_GMAIL_CLIENT_SECRET','test-client-secret')
 monkeypatch.setenv('APP_PUBLIC_URL','https://testserver')
 clients,orders,now,tid,job=setup_clock
 with Session(m.engine) as db:
  db.execute(delete(e.EmailOAuthState));db.execute(delete(e.EmailConnection));db.commit()
 cfg=clients['owner'].get('/api/admin/billing/settings').json()
 clients['owner'].put('/api/admin/billing/settings',json={**cfg['values'],'test_mode':True,'expected_revision':cfg['revision']})
 yield clients,job,monkeypatch
 latest=clients['owner'].get('/api/admin/billing/settings').json()
 clients['owner'].put('/api/admin/billing/settings',json={**cfg['values'],'expected_revision':latest['revision']})
 with Session(m.engine) as db:
  db.execute(delete(e.EmailOAuthState));db.execute(delete(e.EmailConnection));db.commit()

def connect_start(owner,sender=e.INVOICE_SENDER):
 r=owner.post('/api/admin/email/connections/'+sender+'/connect',json={});assert r.status_code==200,r.text
 state=parse_qs(urlsplit(r.json()['authorization_url']).query)['state'][0]
 cookie=r.cookies.get(e.COOKIE)
 return state,cookie

def callback(owner,state,cookie,**params):
 return owner.get('/api/admin/email/oauth/callback',params={'state':state,'code':'one-use-code',**params},
      headers={'Cookie':e.COOKIE+'='+cookie},follow_redirects=False)

def test_only_approved_accounts_and_owner_can_connect(email_setup):
 clients,job,mp=email_setup;o=clients['owner'];t=clients['tech']
 r=o.get('/api/admin/email/connections');assert r.status_code==200
 assert [x['sender'] for x in r.json()['accounts']]==list(e.SENDERS)
 assert not any(x['connected'] for x in r.json()['accounts'])
 assert not r.json()['setup_required'] and 'secret' not in r.text and 'token' not in r.text
 assert t.get('/api/admin/email/connections').status_code==403
 assert t.post('/api/admin/email/connections/'+e.INVOICE_SENDER+'/connect',json={}).status_code==403
 assert o.post('/api/admin/email/connections/outsider@example.com/connect',json={}).status_code==422
 mp.delenv('LMS_EMAIL_TOKEN_KEY');assert o.get('/api/admin/email/connections').json()['setup_required']
 assert o.post('/api/admin/email/connections/'+e.INVOICE_SENDER+'/connect',json={}).status_code==503

def test_oauth_browser_binding_pkce_and_expiry(email_setup):
 clients,job,mp=email_setup;o=clients['owner'];state,cookie=connect_start(o)
 with Session(m.engine) as db:
  row=db.get(e.EmailOAuthState,e.digest(state));assert row and row.state_hash!=state
  assert row.browser_hash!=cookie and e.cipher().decrypt(row.encrypted_verifier.encode())
 assert callback(o,state,'wrong-browser').status_code==400
 with Session(m.engine) as db:
  row=db.get(e.EmailOAuthState,e.digest(state));row.expires_at=e.now()-timedelta(seconds=1);db.commit()
 assert callback(o,state,cookie).status_code==400

def test_oauth_connect_encrypts_offline_token_and_cannot_replay(email_setup):
 clients,job,mp=email_setup;o=clients['owner'];sender=e.QUOTE_SENDERS[0];state,cookie=connect_start(o,sender)
 creds=SimpleNamespace(refresh_token='private-refresh-token',id_token='private-id-token',granted_scopes=e.SCOPES,scopes=e.SCOPES)
 fake=SimpleNamespace(credentials=creds,fetch_token=lambda **kw:None)
 mp.setattr(e,'flow',lambda *a,**kw:fake)
 mp.setattr(e.id_token,'verify_oauth2_token',lambda token,request,audience:{'email':sender,'email_verified':True,'sub':'google-subject'})
 r=callback(o,state,cookie);assert r.status_code==303 and r.headers['location']=='/email-center?connection=connected'
 assert callback(o,state,cookie).status_code==400
 with Session(m.engine) as db:
  row=db.get(e.EmailConnection,sender);assert row.active and 'private-refresh-token' not in row.encrypted_token
  assert json.loads(e.cipher().decrypt(row.encrypted_token.encode()))['refresh_token']=='private-refresh-token'
  assert db.get(e.EmailOAuthState,e.digest(state)).encrypted_verifier==''
 assert 'private' not in o.get('/api/admin/email/connections').text
 assert transport.configured(sender)
 assert o.post('/api/admin/email/connections/'+sender+'/disconnect',json={}).status_code==200
 assert not transport.configured(sender)
 with Session(m.engine) as db:assert db.get(e.EmailConnection,sender).encrypted_token==''

@pytest.mark.parametrize('claims',[{'email':'wrong@example.com','email_verified':True,'sub':'x'},
 {'email':e.INVOICE_SENDER,'email_verified':False,'sub':'x'}])
def test_wrong_google_identity_is_not_connected(email_setup,claims):
 clients,job,mp=email_setup;o=clients['owner'];state,cookie=connect_start(o)
 creds=SimpleNamespace(refresh_token='private-token',id_token='id',granted_scopes=e.SCOPES,scopes=e.SCOPES)
 mp.setattr(e,'flow',lambda *a,**kw:SimpleNamespace(credentials=creds,fetch_token=lambda **kw:None))
 mp.setattr(e.id_token,'verify_oauth2_token',lambda *a,**kw:claims)
 r=callback(o,state,cookie);assert r.headers['location']=='/email-center?connection=failed'
 with Session(m.engine) as db:assert db.get(e.EmailConnection,e.INVOICE_SENDER) is None

def quote_body(owner,job,sender=e.QUOTE_SENDERS[0]):
 quote=owner.post('/api/proposals',json={'job_number':job,'number':'MAIL-'+uuid.uuid4().hex,'title':'Repair quote','scope':'Customer-facing work','total':100}).json()
 data=next(x for x in owner.get('/api/admin/quote-mail/quotes').json() if x['id']==quote['id'])
 return quote,{'request_id':str(uuid.uuid4()),'sender':sender,'recipient':'customer@example.com',
   'subject':'Quote for repair','message':'Please review the attached quote.','expected_source_hash':data['source_hash']}

def test_quote_preview_and_sender_restrictions(email_setup):
 clients,job,mp=email_setup;o=clients['owner'];mp.setattr(q.q,'proposal_pdf',lambda *a:b'%PDF-test')
 mp.setattr(m,'gmail_send',lambda *a,**kw:pytest.fail('A preview must never send'))
 quote,body=quote_body(o,job);path='/api/admin/quote-mail/'+quote['id']+'/prepare'
 assert o.post(path,json={**body,'sender':e.INVOICE_SENDER}).status_code==422
 assert clients['tech'].post(path,json=body).status_code==403
 assert o.post(path,json={**body,'expected_source_hash':'0'*64}).status_code==409
 r=o.post(path,json=body);assert r.status_code==200,r.text
 row=r.json();assert row['status']=='Preview' and row['sender']==e.QUOTE_SENDERS[0]
 assert o.post(path,json=body).json()['id']==row['id']
 assert o.post(path,json={**body,'message':'Different message'}).status_code==409
 assert o.get(row['preview_url']).content==b'%PDF-test'
 assert clients['tech'].get(row['preview_url']).status_code==403
 assert o.post('/api/admin/quote-mail/'+row['id']+'/process',json={}).json()['status']=='Preview'

@pytest.mark.parametrize('sender',e.QUOTE_SENDERS)
def test_live_quote_sends_from_selected_mailbox_once(email_setup,sender):
 clients,job,mp=email_setup;o=clients['owner'];mp.setattr(q.q,'proposal_pdf',lambda *a:b'%PDF-test')
 cfg=o.get('/api/admin/billing/settings').json();o.put('/api/admin/billing/settings',json={**cfg['values'],'test_mode':False,'expected_revision':cfg['revision']})
 mp.setenv('LMS_ALLOW_EXTERNAL_EMAIL','1');mp.setattr(transport,'configured',lambda value=sender:True)
 calls=[];mp.setattr(m,'gmail_send',lambda *a,**kw:calls.append((a,kw)) or 'provider-confirmed')
 quote,body=quote_body(o,job,sender);path='/api/admin/quote-mail/'+quote['id']+'/prepare'
 row=o.post(path,json=body).json();assert row['status']=='Queued'
 process='/api/admin/quote-mail/'+row['id']+'/process'
 assert o.post(process,json={}).json()['status']=='Sent'
 assert o.post(process,json={}).json()['status']=='Sent' and len(calls)==1
 assert calls[0][1]['sender']==sender and calls[0][0][0]=='customer@example.com'
 assert o.post(path,json={**body,'request_id':str(uuid.uuid4())}).status_code==409

def test_stale_quote_cancelled_and_uncertain_send_not_retried(email_setup):
 clients,job,mp=email_setup;o=clients['owner'];mp.setattr(q.q,'proposal_pdf',lambda *a:b'%PDF-test')
 cfg=o.get('/api/admin/billing/settings').json();o.put('/api/admin/billing/settings',json={**cfg['values'],'test_mode':False,'expected_revision':cfg['revision']})
 mp.setenv('LMS_ALLOW_EXTERNAL_EMAIL','1');mp.setattr(transport,'configured',lambda *a:True)
 quote,body=quote_body(o,job);row=o.post('/api/admin/quote-mail/'+quote['id']+'/prepare',json=body).json()
 with Session(m.engine) as db:db.get(m.Proposal,quote['id']).total=200;db.commit()
 assert o.post('/api/admin/quote-mail/'+row['id']+'/process',json={}).json()['status']=='Cancelled'
 quote,body=quote_body(o,job);path='/api/admin/quote-mail/'+quote['id']+'/prepare';row=o.post(path,json=body).json();calls=[]
 def uncertain(*a,**kw):calls.append(1);raise TimeoutError()
 mp.setattr(m,'gmail_send',uncertain);process='/api/admin/quote-mail/'+row['id']+'/process'
 assert o.post(process,json={}).json()['status']=='Unconfirmed'
 assert o.post(process,json={}).json()['status']=='Unconfirmed' and len(calls)==1
 assert o.post(path,json={**body,'request_id':str(uuid.uuid4()),'confirm_resend':True}).status_code==409
 assert o.post('/api/admin/quote-mail/'+row['id']+'/resolve',json={'action':'Confirmed not sent','reason':'Verified the sender Sent Mail folder'}).status_code==200
 assert o.post(path,json={**body,'request_id':str(uuid.uuid4())}).json()['status']=='Queued'

def test_transport_uses_selected_account_without_reading_inbox(email_setup):
 clients,job,mp=email_setup;sender=e.QUOTE_SENDERS[1]
 with Session(m.engine) as db:
  db.add(e.EmailConnection(sender=sender,google_subject='test-subject',encrypted_token='encrypted',connected_by=clients['owner'].get('/api/auth/me').json()['id']));db.commit()
 mp.setattr(e,'access_token',lambda value:'fake-access-token' if value==sender else pytest.fail('Wrong account'))
 sent=[];mp.setattr(transport,'_post',lambda url,body,headers:sent.append((url,body)) or {'id':'gmail-id'})
 assert transport.gmail_send('customer@example.com','Subject','Body',b'%PDF','Quote.pdf',sender=sender)=='gmail-id'
 import base64
 from email import message_from_bytes
 mime=message_from_bytes(base64.urlsafe_b64decode(json.loads(sent[0][1])['raw']+'==='))
 assert mime['From']==sender and mime['Reply-To']==sender and mime['To']=='customer@example.com' and mime['Bcc'] is None
 assert sent[0][0].endswith('/users/me/messages/send')

