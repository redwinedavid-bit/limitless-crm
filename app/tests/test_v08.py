import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from server.main import app,engine
from server.security import User,password_hash

def test_proposal_invoice_payment_lifecycle():
    token=uuid.uuid4().hex[:10]
    with Session(engine) as db:
        db.add(User(username='billing'+token,display_name='Billing Owner',role='owner',password_hash=password_hash('example-test-pass')));db.commit()
    c=TestClient(app)
    assert c.get('/api/proposals').status_code==401
    assert c.post('/api/auth/login',json={'username':'billing'+token,'password':'example-test-pass'}).status_code==200
    cu=c.post('/api/customers',json={'name':'Billing '+token}).json()
    job='LMS'+str(int(token,16))
    assert c.post('/api/jobs',json={'job_number':job,'customer_id':cu['id']}).status_code==201
    p=c.post('/api/proposals',json={'job_number':job,'number':'PROP-'+token,'title':'Replace unit','total':'2500.00'});assert p.status_code==201,p.text
    p=p.json();assert p['status']=='Draft'
    inv={'job_number':job,'number':'INV-'+token,'proposal_id':p['id'],'amount':'2500.00'}
    assert c.post('/api/invoices',json=inv).status_code==422
    assert c.post('/api/proposals/'+p['id']+'/approve').status_code==200
    assert c.post('/api/proposals/'+p['id']+'/approve').status_code==409
    assert c.post('/api/invoices',json={**inv,'amount':'2200.00'}).status_code==422
    i=c.post('/api/invoices',json=inv);assert i.status_code==201,i.text
    i=i.json();assert c.post('/api/invoices',json={**inv,'number':'INV2-'+token}).status_code==409
    assert c.post('/api/invoices/'+i['id']+'/payments',json={'amount':100,'reference':'txn-'+token,'paid_on':'2026-10-23'}).status_code==409
    assert c.post('/api/invoices/'+i['id']+'/issue').status_code==200
    x=c.post('/api/invoices/'+i['id']+'/payments',json={'amount':1000,'reference':'txn-'+token,'paid_on':'2026-10-23'});assert x.status_code==201,x.text
    assert x.json()['balance']==1500
    assert c.post('/api/invoices/'+i['id']+'/payments',json={'amount':1000,'reference':'txn-'+token,'paid_on':'2026-10-23'}).status_code==409
    assert c.post('/api/invoices/'+i['id']+'/payments',json={'amount':1501,'reference':'txnb-'+token,'paid_on':'2026-10-23'}).status_code==422
    x=c.post('/api/invoices/'+i['id']+'/payments',json={'amount':1500,'reference':'txnc-'+token,'paid_on':'2026-10-23'});assert x.status_code==201 and x.json()['status']=='Paid'
    f=c.get('/api/jobs/'+job+'/financial-overview');assert f.status_code==200
    assert f.json()['billed']==2500 and f.json()['collected']==2500 and f.json()['receivable']==0
    assert c.get('/financials').status_code==200
