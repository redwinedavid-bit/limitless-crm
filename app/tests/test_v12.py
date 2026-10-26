"""v1.2 accounting safety tests - uses a disposable DATABASE_URL when run."""
import uuid
from datetime import date
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from server.main import app,engine,InvoiceRecord,invoice_ledger,InvoiceLine
from server.security import User,password_hash

def client_with_job():
    key=uuid.uuid4().hex[:11]
    with Session(engine) as db:
        db.add(User(username='v12'+key,display_name='Owner QA',role='owner',password_hash=password_hash('testing-pass-123')))
        db.commit()
    c=TestClient(app)
    assert c.post('/api/auth/login',json={'username':'v12'+key,'password':'testing-pass-123'}).status_code==200
    cus=c.post('/api/customers',json={'name':'V12 Test Customer '+key}).json()
    job='LMS'+str(int(key,16))
    assert c.post('/api/jobs',json={'job_number':job,'customer_id':cus['id'],'description':'Service call'}).status_code==201
    return c,key,cus,job

def test_itemized_invoice_and_credit_refund_workflow():
    c,key,cus,job=client_with_job()
    invoice_request={
        'job_number':job,'number':'ITEM-'+key,'due_on':'2026-01-10',
        'lines':[
            {'description':'Service hours','quantity':'2.5','unit_price':'100.00','tax_rate':'0'},
            {'description':'Materials','quantity':'3','unit_price':'19.99','tax_rate':'6'}
        ]}
    notmatching=c.post('/api/invoices',json={**invoice_request,'amount':'310.00'})
    assert notmatching.status_code==422
    r=c.post('/api/invoices',json=invoice_request);assert r.status_code==201,r.text
    i=r.json(); assert i['amount']==313.57 and len(i['lines'])==2 and i['lines'][1]['tax']==3.6
    iid=i['id']
    assert c.put('/api/invoices/'+iid+'/lines',json={'lines':[{'description':'Materials','quantity':'1','unit_price':'100','tax_rate':'6'}]}).status_code==200
    i=c.get('/api/invoices').json()[-1] if False else None
    with Session(engine) as db:
        assert db.get(InvoiceRecord,iid).amount==Decimal('106.00')
    assert c.post('/api/invoices/'+iid+'/issue').status_code==200
    assert c.put('/api/invoices/'+iid+'/lines',json={'lines':[{'description':'Too late','quantity':'1','unit_price':'2'}]}).status_code==409
    p=c.post('/api/invoices/'+iid+'/payments',json={'amount':'100','reference':'P-'+key,'paid_on':'2026-01-12'})
    assert p.status_code==201 and p.json()['balance']==6.0
    credit=c.post('/api/invoices/'+iid+'/credits',json={'number':'CM-'+key,'amount':'26','reason':'Incorrect labor bill','credited_on':'2026-01-14'})
    assert credit.status_code==201,credit.text
    assert credit.json()['net_billed']==80 and credit.json()['credit_balance']==20
    assert credit.json()['status']=='Credit Balance'
    assert c.post('/api/invoices/'+iid+'/refunds',json={'reference':'RF-OVER-'+key,'amount':'20.01','reason':'Over refund','refunded_on':'2026-01-14'}).status_code==422
    refund=c.post('/api/invoices/'+iid+'/refunds',json={'reference':'RF-'+key,'amount':'20','reason':'Refund after credit','refunded_on':'2026-01-14'})
    assert refund.status_code==201 and refund.json()['paid']==80 and refund.json()['balance']==0
    assert c.post('/api/invoices/'+iid+'/refunds',json={'reference':'RF2-'+key,'amount':'1','reason':'Extra refund','refunded_on':'2026-01-14'}).status_code==422
    detail=c.get('/api/billing/customers/'+cus['id']).json()
    assert detail['total_billed']==80 and detail['total_collected']==80 and detail['outstanding']==0
    pdf=c.get('/api/invoices/'+iid+'/pdf');assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
    html=c.get('/api/invoices/'+iid+'/document');assert html.status_code==200 and 'Incorrect' not in html.text and 'Materials' in html.text
    txn=c.get('/api/invoices/'+iid+'/transactions').json()
    assert len(txn['payments'])==1 and len(txn['credits'])==1 and len(txn['refunds'])==1

def test_payment_reversal_aging_and_role_restrictions():
    c,key,cus,job=client_with_job()
    inv=c.post('/api/invoices',json={'job_number':job,'number':'AGE-'+key,'amount':'1000','issued_on':'2026-01-01','due_on':'2026-02-01'}).json()
    iid=inv['id']
    assert c.post('/api/invoices/'+iid+'/issue').status_code==200
    payment=c.post('/api/invoices/'+iid+'/payments',json={'amount':'400','reference':'PM-'+key,'paid_on':'2026-03-01'})
    assert payment.status_code==201
    transaction=c.get('/api/invoices/'+iid+'/transactions').json()['payments'][0]
    reverse={'reference':'RV-'+key,'amount':'150','reason':'Duplicate posted amount','reversed_on':'2026-03-04'}
    r=c.post('/api/payments/'+transaction['id']+'/reverse',json=reverse)
    assert r.status_code==201 and r.json()['balance']==750 and r.json()['paid']==250
    assert c.post('/api/payments/'+transaction['id']+'/reverse',json=reverse).status_code==409
    assert c.post('/api/payments/'+transaction['id']+'/reverse',json={**reverse,'reference':'RV2-'+key,'amount':'251'}).status_code==422
    aging=c.get('/api/billing/summary?as_of=2026-04-02').json()
    assert aging['aging']['31_60']>=750
    d=c.get('/api/billing/aging?as_of=2026-04-02').json()
    assert any(x['invoice']=='AGE-'+key and x['aging_bucket']=='31_60' and x['balance']==750 for x in d['invoices'])
    statement=c.get('/api/billing/customers/'+cus['id']+'/statement.pdf')
    assert statement.status_code==200 and statement.content.startswith(b'%PDF')
    # An unauthenticated user has no access to finance endpoints.
    anonymous=TestClient(app)
    for url in ['/api/billing/aging','/api/billing/summary','/api/invoices/'+iid+'/transactions']:
        assert anonymous.get(url).status_code==401
    with Session(engine) as db:
        db.add(User(username='v12tech'+key,display_name='Field Tech',role='technician',password_hash=password_hash('passpass123')))
        db.commit()
    tech=TestClient(app)
    assert tech.post('/api/auth/login',json={'username':'v12tech'+key,'password':'passpass123'}).status_code==200
    assert tech.get('/api/billing/summary').status_code==403
    assert tech.post('/api/invoices/'+iid+'/credits',json={'number':'NO-'+key,'amount':'1','reason':'Not allowed','credited_on':'2026-03-01'}).status_code==403

def test_existing_legacy_invoice_works_without_line_items():
    c,key,cus,job=client_with_job()
    inv=c.post('/api/invoices',json={'job_number':job,'number':'LEG-'+key,'amount':'850.00'}).json()
    assert inv['lines']==[] and inv['amount']==850
    assert c.get('/api/invoices/'+inv['id']+'/pdf').status_code==200
    assert c.post('/api/invoices/'+inv['id']+'/issue').status_code==200
    assert c.post('/api/invoices/'+inv['id']+'/credits',json={'number':'CM-LEG-'+key,'amount':'1000','reason':'Too much','credited_on':'2026-02-01'}).status_code==422
