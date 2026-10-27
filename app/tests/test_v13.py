"""Job-specific auto-numbered POs, receipts, security, and job-cost lifecycle."""
import base64
import pytest
import uuid
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import select, create_engine
from sqlalchemy.orm import Session
from server.main import app,engine,Base,Customer,Job,PurchaseOrder,PONumberCounter
from server.security import User,password_hash


def actor(role='owner',prefix=None):
    key=uuid.uuid4().hex[:9]
    with Session(engine) as db:
        db.add(User(username='p13'+key,display_name='Purchasing QA',role=role,
                    po_prefix=prefix,password_hash=password_hash('test-password-2026')))
        db.commit()
    c=TestClient(app)
    assert c.post('/api/auth/login',json={'username':'p13'+key,'password':'test-password-2026'}).status_code==200
    return c,key


def job_for(c,key):
    cu=c.post('/api/customers',json={'name':'Purchasing QA '+key})
    assert cu.status_code==201,cu.text
    number='LMS'+str(int(key,16))
    assert c.post('/api/jobs',json={'job_number':number,'customer_id':cu.json()['id'],
        'contract_amount':'500.00'}).status_code==201
    return number


def create(c,job,amount='100.00',**kwargs):
    return c.post('/api/purchasing/pos',json={'job_number':job,'vendor':'URI Supply',
        'description':'ACR pipe and fittings','amount':amount,**kwargs})


def step(c,number,action,payload=None):
    return c.post('/api/purchasing/pos/'+number+'/'+action,json=payload)


def test_dr_auto_number_no_approval_costs_receiving_credits():
    c,key=actor(prefix='DR');j=job_for(c,key)
    # Historical sequence must advance, never restart at DR0001.
    with Session(engine) as db:
        job=db.scalar(select(Job).where(Job.job_number==j))
        # The real imported Job Board already contains DR0210; don't add a duplicate.
        if not db.scalar(select(PurchaseOrder.id).where(PurchaseOrder.po_number=='DR0210')):
            db.add(PurchaseOrder(po_number='DR0210',job_id=job.id,vendor='URI',amount=Decimal('10'),
                                 received_value=Decimal('10'),status='Historical',source='historical'))
        db.commit()
    # Other integration tests share this database and may already have issued DR POs.
    expected=c.get('/api/purchasing/my-number').json()['next_preview']
    assert expected.startswith('DR') and int(expected[2:])>=250
    assert create(c,'LMS999999999').status_code==422
    assert create(c,j,'5.125').status_code==422
    assert create(c,j,number='AR9999').status_code==422
    po=create(c,j);assert po.status_code==201,po.text
    assert po.json()['number']==expected and po.json()['status']=='Issued' and po.json()['job_number']==j
    assert c.get('/api/purchasing/my-number').json()['next_preview']==f'DR{int(expected[2:])+1:04d}'
    n=po.json()['number']
    assert step(c,n,'approve').status_code==410
    assert step(c,n,'issue').status_code==410
    costs=c.get('/api/jobs/'+j+'/costs').json()
    assert costs['new_po_actual']==100 and costs['open_committed']==0
    assert step(c,n,'receipts',{'reference':'PACK1','amount':'60','received_on':'2026-10-24'}).status_code==201
    assert step(c,n,'receipts',{'reference':'PACK1','amount':'1','received_on':'2026-10-24'}).status_code==409
    assert step(c,n,'receipts',{'reference':'OVER','amount':'40.01','received_on':'2026-10-24'}).status_code==422
    assert step(c,n,'vendor-invoices',{'reference':'BILL1','amount':'65','matched_receipt_value':'60','invoice_date':'2026-10-24'}).status_code==201
    assert c.get('/api/jobs/'+j+'/costs').json()['new_po_actual']==105
    assert step(c,n,'receipts',{'reference':'PACK2','amount':'40','received_on':'2026-10-24'}).status_code==201
    assert step(c,n,'vendor-invoices',{'reference':'BILL2','amount':'40','matched_receipt_value':'40','invoice_date':'2026-10-24'}).status_code==201
    assert c.get('/api/jobs/'+j+'/costs').json()['new_po_actual']==105
    assert step(c,n,'returns',{'reference':'RTN1','amount':'20','reason':'Unused fittings returned','returned_on':'2026-10-24'}).status_code==201
    assert step(c,n,'credits',{'reference':'CREDIT1','amount':'20','reason':'Vendor credited return','credited_on':'2026-10-24'}).status_code==201
    assert c.get('/api/jobs/'+j+'/costs').json()['new_po_actual']==85
    assert step(c,n,'close',{'reason':'All items accounted for'}).status_code==200
    assert step(c,n,'receipts',{'reference':'EXTRA','amount':'1','received_on':'2026-10-24'}).status_code==409


def test_ar_assignment_and_photo_receipts():
    buyer,key=actor('technician','AR')
    owner,_=actor('owner')
    j=job_for(owner,key)
    n=create(buyer,j).json()['number']
    assert n.startswith('AR')
    assert buyer.get('/api/purchasing/jobs/'+j).status_code==200
    assert owner.get('/api/purchasing/pos/'+n).status_code==200
    tiny_png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Xx0+zgAAAAASUVORK5CYII=')
    uploaded=buyer.post('/api/purchasing/pos/'+n+'/attachments',data={'kind':'receipt'},files={'file':('purchase.png',tiny_png,'image/png')})
    assert uploaded.status_code==201,uploaded.text
    url=uploaded.json()['download_url']
    assert buyer.get(url).status_code==200
    assert buyer.get(url).content==tiny_png
    assert owner.get(url).status_code==200
    assert len(buyer.get('/api/purchasing/pos/'+n).json()['attachments'])==1
    bad=buyer.post('/api/purchasing/pos/'+n+'/attachments',data={'kind':'receipt'},files={'file':('receipt.txt',b'not a photo','text/plain')})
    assert bad.status_code==415
    assert step(buyer,n,'receipts',{'reference':'PACK1','amount':'100','received_on':'2026-10-24'}).status_code==201
    assert owner.get('/api/purchasing/jobs/'+j).json()['new_po_actual']==100


def test_db_separate_sequence_cross_buyer_denial_and_historical_preservation():
    buyer,key=actor('technician','DB')
    owner,_=actor('owner')
    j=job_for(owner,key)
    po=create(buyer,j);assert po.status_code==201,po.text
    n=po.json()['number'];assert n.startswith('DB')
    assert step(buyer,n,'cancel',{'reason':'Order no longer required'}).status_code==200
    assert owner.get('/api/purchasing/jobs/'+j).json()['new_po_actual']==0
    # Another buyer cannot read private receipts or modify this PO.
    with Session(engine) as db:
        b=db.scalar(select(User).where(User.po_prefix=='AR'))
        if b:b.po_prefix=None;db.commit()
    outsider,_=actor('technician','AR')
    assert outsider.get('/api/purchasing/pos/'+n).status_code==403
    assert step(outsider,n,'close',{'reason':'Other buyer'}).status_code==403
    assert outsider.post('/api/purchasing/pos/'+n+'/attachments',data={'kind':'receipt'},files={'file':('x.pdf',b'%PDF-1.4\n%%EOF','application/pdf')}).status_code==403
    assert all(x['number'].startswith('AR') for x in outsider.get('/api/purchasing/pos').json())
    hist='DRHIST'+key
    with Session(engine) as db:
        job=db.scalar(select(Job).where(Job.job_number==j))
        job.historical_material_cost=Decimal('150.00')
        db.add(PurchaseOrder(po_number=hist,job_id=job.id,vendor='Legacy',amount=Decimal('150'),received_value=Decimal('150'),status='Historical',source='historical'))
        db.commit()
    assert owner.get('/api/purchasing/jobs/'+j).json()['historical_direct_cost']==150
    assert step(owner,hist,'receipts',{'reference':'NO','amount':'1','received_on':'2026-10-24'}).status_code==403
    assert owner.get('/api/jobs/'+j+'/costs').json()['new_po_actual']==0



def test_historical_po_number_seeds_sequence_in_isolated_database():
    from server.purchasing_v13 import generate_number
    isolated=create_engine('sqlite://')
    try:
        Base.metadata.create_all(isolated)
        with Session(isolated) as db:
            customer=Customer(name='Isolated numbering test');db.add(customer);db.flush()
            job=Job(job_number='LMS-NUMBERING',customer_id=customer.id);db.add(job);db.flush()
            db.add(PurchaseOrder(po_number='DR0300',job_id=job.id,vendor='Legacy supplier',
                amount=Decimal('10'),received_value=Decimal('10'),status='Historical',source='historical'))
            db.commit()
            assert generate_number(db,'DR')=='DR0301'
            db.commit()
        with Session(isolated) as db:
            assert generate_number(db,'DR')=='DR0302'
    finally:
        isolated.dispose()


@pytest.mark.parametrize('prefix,first,second',[
    ('DR','DR0250','DR0251'),
    ('AR','AR0350','AR0351'),
    ('DB','DB0050','DB0051'),
])
@pytest.mark.parametrize('existing_counter',[False,True])
def test_po_requested_starts_preview_and_reservation(prefix,first,second,existing_counter):
    from server.purchasing_v13 import generate_number,next_po_number
    isolated=create_engine('sqlite://')
    try:
        Base.metadata.create_all(isolated)
        with Session(isolated) as db:
            if existing_counter:
                db.add(PONumberCounter(prefix=prefix,last_number=7));db.commit()
            assert next_po_number(db,prefix)==first
            assert next_po_number(db,prefix)==first  # Preview must not reserve a number.
            assert generate_number(db,prefix)==first
            db.commit()
        with Session(isolated) as db:
            assert next_po_number(db,prefix)==second
            assert generate_number(db,prefix)==second
            db.commit()
    finally:
        isolated.dispose()


def test_po_numbers_preserve_higher_counters_and_skip_imported_numeric_aliases():
    from server.purchasing_v13 import generate_number,next_po_number
    isolated=create_engine('sqlite://')
    try:
        Base.metadata.create_all(isolated)
        with Session(isolated) as db:
            customer=Customer(name='Numbering safety');db.add(customer);db.flush()
            job=Job(job_number='LMS-NUMBERING-SAFETY',customer_id=customer.id);db.add(job);db.flush()
            db.add_all([PONumberCounter(prefix='DR',last_number=900),
                        PONumberCounter(prefix='AR',last_number=12),
                        PONumberCounter(prefix='DB',last_number=70)])
            for number in ['AR0350','AR00351','ARHIST99999','AR99999-VOID']:
                db.add(PurchaseOrder(po_number=number,job_id=job.id,vendor='Imported',
                    amount=10,received_value=10,status='Historical',source='historical'))
            db.commit()
            assert next_po_number(db,'DR')=='DR0901'
            assert next_po_number(db,'AR')=='AR0352'
            assert next_po_number(db,'DB')=='DB0071'
            assert generate_number(db,'AR')=='AR0352'
            assert generate_number(db,'DR')=='DR0901'
            assert generate_number(db,'DB')=='DB0071'
            db.commit()
    finally:
        isolated.dispose()
