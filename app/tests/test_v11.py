import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import server.main as main
from server.main import app,engine
from server.security import User,password_hash

def test_statement_and_explicit_invoice_resend(monkeypatch):
    key=uuid.uuid4().hex[:10]
    client=TestClient(app)
    with Session(engine) as db:
        db.add(User(username='statement'+key,display_name='Statement Test',role='owner',password_hash=password_hash('testpassword123')))
        db.commit()
    assert client.get('/api/billing/customers/unknown/statement.pdf').status_code==401
    assert client.post('/api/auth/login',json={'username':'statement'+key,'password':'testpassword123'}).status_code==200
    customer=client.post('/api/customers',json={'name':'Statement Customer '+key,'billing_address':'123 Test Rd'}).json()
    job='LMS'+str(int(key,16))
    assert client.post('/api/jobs',json={'job_number':job,'customer_id':customer['id'],'description':'Service work'}).status_code==201
    from server.billing_v111 import BillingSettings
    monkeypatch.setenv('LMS_ALLOW_EXTERNAL_EMAIL','1')
    monkeypatch.setenv('LMS_GMAIL_SENDER','accounting@limitlessmechanical.net')
    old=client.get('/api/admin/billing/settings').json()
    assert client.put('/api/admin/billing/settings',json={**old['values'],'test_mode':False,'remit_address':'123 Office Rd','expected_revision':old['revision']}).status_code==200
    inv=client.post('/api/invoices',json={'job_number':job,'number':'STMT-'+key,'amount':'325.00'}).json()
    assert client.post('/api/invoices/'+inv['id']+'/issue').status_code==200
    assert client.post('/api/invoices/'+inv['id']+'/payments',json={'amount':'125.00','paid_on':'2026-10-23','reference':'STMT-PAY-'+key}).status_code==201
    pdf=client.get('/api/billing/customers/'+customer['id']+'/statement.pdf')
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF') and len(pdf.content)>1000
    sent=[]
    monkeypatch.setattr(main,'gmail_configured',lambda:True)
    monkeypatch.setattr(main,'gmail_send',lambda *args,**kwargs: sent.append(args) or 'gmail-'+key+str(len(sent)))
    body={'recipient':'billing@example.com','subject':'Test invoice','message':'Invoice attached'}
    assert client.post('/api/invoices/'+inv['id']+'/send-email',json=body).status_code==200
    assert client.post('/api/invoices/'+inv['id']+'/send-email',json=body).status_code==409
    assert len(sent)==1
    assert client.post('/api/invoices/'+inv['id']+'/send-email',json={**body,'confirm_resend':True}).status_code==200
    assert len(sent)==2
    history=client.get('/api/invoices/'+inv['id']+'/email-history').json()
    assert len(history)==2

    cfg=client.get('/api/admin/billing/settings').json()
    assert client.put('/api/admin/billing/settings',json={**old['values'],'expected_revision':cfg['revision']}).status_code==200
