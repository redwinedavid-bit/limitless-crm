import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from server.main import app, engine
from server.security import User, password_hash
import server.main as main

def test_pdf_and_configured_email_send(monkeypatch):
    unique=uuid.uuid4().hex[:10]
    client=TestClient(app)
    with Session(engine) as db:
        db.add(User(username='mail'+unique,display_name='Invoice Sender',role='owner',password_hash=password_hash('testingpass123')))
        db.commit()
    assert client.get('/api/billing/email/status').status_code==401
    assert client.post('/api/auth/login',json={'username':'mail'+unique,'password':'testingpass123'}).status_code==200
    cust=client.post('/api/customers',json={'name':'Test Customer '+unique,'billing_address':'Somewhere, KY'}).json()
    job='LMS'+str(int(unique,16))
    assert client.post('/api/jobs',json={'job_number':job,'customer_id':cust['id'],'description':'Coil service'}).status_code==201
    inv=client.post('/api/invoices',json={'job_number':job,'number':'MAIL-INV-'+unique,'amount':'650.00'}).json()
    pdf=client.get('/api/invoices/'+inv['id']+'/pdf')
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF') and len(pdf.content)>1000
    monkeypatch.setattr(main,'gmail_configured',lambda:False)
    data={'recipient':'payables@example.com','subject':'Invoice','message':'See attached invoice'}
    assert client.post('/api/invoices/'+inv['id']+'/send-email',json=data).status_code==409
    from server.billing_v111 import BillingSettings
    monkeypatch.setenv('LMS_ALLOW_EXTERNAL_EMAIL','1')
    monkeypatch.setenv('LMS_GMAIL_SENDER','accounting@limitlessmechanical.net')
    old=client.get('/api/admin/billing/settings').json()
    assert client.put('/api/admin/billing/settings',json={**old['values'],'test_mode':False,'remit_address':'123 Office Rd','expected_revision':old['revision']}).status_code==200
    monkeypatch.setattr(main,'gmail_configured',lambda:True)
    assert client.post('/api/invoices/'+inv['id']+'/send-email',json=data).status_code==409
    assert client.post('/api/invoices/'+inv['id']+'/issue').status_code==200
    assert client.post('/api/invoices/'+inv['id']+'/send-email',json={**data,'recipient':'a@example.com\nbcc:evil@example.com'}).status_code==422
    observed=[]
    monkeypatch.setattr(main,'gmail_send',lambda recipient,subject,message,pdf,filename,**kwargs:observed.append((recipient,pdf,filename)) or 'gmail-test-'+unique)
    sent=client.post('/api/invoices/'+inv['id']+'/send-email',json=data)
    assert sent.status_code==200 and sent.json()['status']=='sent'
    assert observed[0][1].startswith(b'%PDF')
    history=client.get('/api/invoices/'+inv['id']+'/email-history').json()
    assert len(history)==1 and history[0]['recipient']=='payables@example.com'

    cfg=client.get('/api/admin/billing/settings').json()
    assert client.put('/api/admin/billing/settings',json={**old['values'],'expected_revision':cfg['revision']}).status_code==200
