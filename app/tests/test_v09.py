import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from server.main import app, engine
from server.security import User, password_hash

def test_customer_billing_and_invoice_document():
    suffix=uuid.uuid4().hex[:12]
    client=TestClient(app)
    assert client.get('/api/billing/summary').status_code==401
    with Session(engine) as db:
        db.add(User(username='owner'+suffix,display_name='Billing Test',role='owner',password_hash=password_hash('testingpass123')))
        db.commit()
    assert client.post('/api/auth/login',json={'username':'owner'+suffix,'password':'testingpass123'}).status_code==200
    customer=client.post('/api/customers',json={'name':'Customer '+suffix,'billing_address':'123 Main St'}).json()
    job='LMS'+str(int(suffix,16))
    assert client.post('/api/jobs',json={'job_number':job,'customer_id':customer['id'],'description':'HVAC Repair'}).status_code==201
    inv=client.post('/api/invoices',json={'job_number':job,'number':'BILL-'+suffix,'amount':'500.00','due_on':'2026-01-01'}).json()
    assert client.post('/api/invoices/'+inv['id']+'/issue').status_code==200
    assert client.post('/api/invoices/'+inv['id']+'/payments',json={'amount':'200.00','paid_on':'2026-02-01','reference':'PAY-'+suffix}).status_code==201
    r=client.get('/api/billing/customers/'+customer['id']);assert r.status_code==200
    d=r.json();assert d['job_count']==1 and d['total_billed']==500 and d['total_collected']==200 and d['outstanding']==300 and d['overdue']==300
    assert d['invoices'][0]['balance']==300
    page=client.get('/api/invoices/'+inv['id']+'/document')
    assert page.status_code==200 and 'BILL-'+suffix in page.text and '$300.00' in page.text
    assert client.get('/billing').status_code==200
