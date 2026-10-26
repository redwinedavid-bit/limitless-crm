import json,uuid
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from server import main as m
from server import customer_import as ci
from server.productivity import QuickJobCounter,PricingDefaults
from server.call_pricing import PricingIn,calculate
from server.profit_tracking import LaborCostSnapshot

@pytest.fixture
def seeded(monkeypatch):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    from sqlalchemy import event
    @event.listens_for(engine,'connect')
    def fk(conn,_):conn.execute('PRAGMA foreign_keys=ON')
    m.Base.metadata.create_all(engine);monkeypatch.setattr(m,'engine',engine)
    with Session(engine) as db:
        u=m.User(id='owner',username='admin',display_name='Owner',role='owner',password_hash='unused');db.add(u);db.flush()
        cs=[m.Customer(id=i,name=n,billing_address='Test address') for i,n in zip(sorted(ci.TEST_IDS),['TEST','Madison Co. EMS'])];db.add_all(cs);db.flush()
        js=[m.Job(id='j'+str(i),job_number='LMS'+str(i),customer_id=cs[i%2].id,description='Test job') for i in range(1,5)];db.add_all(js);db.flush()
        db.add_all([m.WorkOrder(id='w'+str(i),job_id='j'+str(i),summary='Test work') for i in range(1,5)])
        db.add_all([m.PurchaseOrder(id='p'+str(i),po_number='DR'+str(i),job_id='j'+str(i),vendor='Vendor',amount=10) for i in (1,2)])
        db.add(m.Proposal(id='q',job_id='j1',number='Q1',title='Test proposal',scope='Test',status='Approved',total=100));db.flush()
        db.add(m.InvoiceRecord(id='i',job_id='j1',proposal_id='q',number='I1',amount=100,status='Draft'))
        db.add(QuickJobCounter(key='jobs',last_number='4'));db.add(PricingDefaults(scope='global',values_json=json.dumps({'mileage_sell_per_mile':'1.25','target_margin':35})))
        db.commit()
    yield engine
    engine.dispose();m.app.dependency_overrides.clear()

def payload():
    return {'source_spreadsheet_id':ci.SOURCE,'customer_rows':[['Customer Name','Billing Address','Billing City State Zip','Special Rate','Point of Contact','Service Location','Service Address','Service City ST Zip'],['Customer A','100 Billing','KY','','Office','Same As Billing Address'],['Customer B','200 Billing','KY','Y','Person','Site One','300 Site','KY'],['Customer B','200 Billing','KY','Y','Person','Site One','300 Site','KY']], 'job_rows':[['Job Number','Customer ','Payment Complete','Location','Address','City, State, Zip'],['LMS0498','Customer B','','Site One','300 Site','KY'],['LMS0400','Customer C','','Unaddressed Site']], 'standard_rate':'125','special_rate':'100','overtime_rate':'187.50','next_job_number':'LMS0262'}

def client(role='owner'):
    m.app.dependency_overrides[m.current_user]=lambda:{'id':'owner','role':role,'username':'admin','name':'Owner','po_prefix':None}
    return TestClient(m.app)

def test_plan_same_as_billing_dedup_and_missing_addresses():
    plan=ci.plan(ci.ImportIn(**payload()))
    assert len(plan['customers'])==3 and len(plan['sites'])==3
    assert plan['sites'][0]['address']=='100 Billing, KY'
    assert plan['sites'][-1]['address']==''
    assert any('no address' in w for w in plan['warnings'])

def test_preview_auth_and_validation_do_not_change_data(seeded):
    c=client('technician');assert c.post('/api/admin/customer-import/preview',json=payload()).status_code==403
    c=client();r=c.post('/api/admin/customer-import/preview',json=payload());assert r.status_code==200,r.text
    assert r.json()['import_customers']==3 and r.json()['import_locations']==3
    assert r.json()['remove']['crm_invoices']==1
    with Session(seeded) as db:assert db.scalar(select(func.count()).select_from(m.Customer))==2
    bad=payload();bad['source_spreadsheet_id']='other';assert c.post('/api/admin/customer-import/preview',json=bad).status_code==422

def test_stale_preview_refuses_changes_and_commit_is_atomic(seeded):
    c=client();r=c.post('/api/admin/customer-import/preview',json=payload()).json()
    with Session(seeded) as db:db.get(m.WorkOrder,'w1').summary='Changed';db.commit()
    body={**payload(),'expected_fingerprint':r['fingerprint'],'confirmation':'REPLACE REVIEWED TEST DATA'}
    assert c.post('/api/admin/customer-import/commit',json=body).status_code==409
    with Session(seeded) as db:
        assert db.get(m.InvoiceRecord,'i') and db.get(m.Customer,sorted(ci.TEST_IDS)[0])
        assert not db.get(ci.CustomerImportRun,'active-job-board-customers-v1')

def test_replacement_preserves_team_and_settings_and_starts_at_0262(seeded):
    c=client();r=c.post('/api/admin/customer-import/preview',json=payload()).json()
    body={**payload(),'expected_fingerprint':r['fingerprint'],'confirmation':'REPLACE REVIEWED TEST DATA'}
    response=c.post('/api/admin/customer-import/commit',json=body);assert response.status_code==200,response.text
    with Session(seeded) as db:
        assert db.get(m.User,'owner');assert db.get(m.InvoiceRecord,'i') is None
        assert db.scalar(select(func.count()).select_from(m.Job))==0
        assert db.scalar(select(func.count()).select_from(m.Customer))==3
        b=db.scalar(select(m.Customer).where(m.Customer.name=='Customer B'))
        assert json.loads(db.get(PricingDefaults,b.id).values_json)=={'labor_sell_per_hour':'100','overtime_sell_per_hour':'187.50'}
        assert json.loads(db.get(PricingDefaults,'global').values_json)['mileage_sell_per_mile']=='1.25'
        backup=json.loads(db.get(ci.CustomerImportRun,'active-job-board-customers-v1').backup_json)
        assert len(backup['customers'])==2 and len(backup['proposals'])==1
        cid=b.id
    again=c.post('/api/admin/customer-import/commit',json=body);assert again.status_code==200 and again.json()['already_applied']
    new={'request_id':str(uuid.uuid4()),'customer_id':cid,'summary':'Actual new call'}
    one=c.post('/api/calls/quick',json=new);assert one.status_code==201,one.text;assert one.json()['job_number']=='LMS0262'
    assert c.post('/api/calls/quick',json=new).json()['job_number']=='LMS0262'
    assert c.post('/api/calls/quick',json={**new,'request_id':str(uuid.uuid4())}).json()['job_number']=='LMS0263'

def test_unexpected_customer_and_active_clock_prevent_cleanup(seeded):
    c=client()
    with Session(seeded) as db:db.add(m.ActiveTimeClock(technician_id='owner',work_order_id='w1',started_at=datetime.now(timezone.utc)));db.commit()
    assert c.post('/api/admin/customer-import/preview',json=payload()).status_code==409
    with Session(seeded) as db:
        db.delete(db.get(m.ActiveTimeClock,'owner'));db.add(m.Customer(name='Unexpected customer'));db.commit()
    assert c.post('/api/admin/customer-import/preview',json=payload()).status_code==409

def test_actual_labor_billing_splits_regular_and_approved_overtime(seeded):
    now=datetime.now(timezone.utc)
    with Session(seeded) as db:
        db.add(m.FieldTimeEntry(id='t1',work_order_id='w1',technician_id='owner',started_at=now-timedelta(hours=3),ended_at=now,hours=3,mileage=0));db.flush()
        db.add(m.TimeApproval(time_entry_id='t1',approved_by='owner',regular_hours=2,overtime_hours=1,labor_cost=90,mileage_cost=0));db.flush()
        data=PricingIn(mode='actual',labor_sell_per_hour=100,overtime_sell_per_hour=Decimal('187.50'),mileage_sell_per_mile=0,auto_target=False)
        p=calculate(db,'w1',data)
        assert p['selling_subtotal']=='387.50'
        assert p['lines'][0]['quantity']=='2.000' or Decimal(p['lines'][0]['quantity'])==2
        assert p['lines'][1]['unit_price']=='187.50'

def test_import_failure_rolls_back_cleanup_and_backup(seeded):
    from sqlalchemy import event
    c=client();r=c.post('/api/admin/customer-import/preview',json=payload()).json()
    def reject_location(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO locations'):raise RuntimeError('simulated import failure')
    event.listen(seeded,'before_cursor_execute',reject_location)
    try:
        with pytest.raises(RuntimeError,match='simulated import failure'):
            c.post('/api/admin/customer-import/commit',json={**payload(),'expected_fingerprint':r['fingerprint'],'confirmation':'REPLACE REVIEWED TEST DATA'})
    finally:event.remove(seeded,'before_cursor_execute',reject_location)
    with Session(seeded) as db:
        assert db.get(m.InvoiceRecord,'i') and db.get(m.Proposal,'q')
        assert db.scalar(select(func.count()).select_from(m.Customer))==2
        assert not db.get(ci.CustomerImportRun,'active-job-board-customers-v1')
