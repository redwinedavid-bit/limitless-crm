import json
from datetime import datetime,timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,event,select,func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from server import main as m
from server import site_cleanup as sc
from server.customer_import import CustomerImportRun
from server.productivity import PricingDefaults,QuickJobCounter
from server.operations_v110 import SiteMetadata

@pytest.fixture
def seeded(monkeypatch):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @event.listens_for(engine,'connect')
    def fk(conn,_):conn.execute('PRAGMA foreign_keys=ON')
    m.Base.metadata.create_all(engine);monkeypatch.setattr(m,'engine',engine)
    with Session(engine) as db:
        db.add(m.User(id='owner',username='owner',display_name='Owner',role='owner',password_hash='unused'));db.flush()
        for id,name in [('a','All About Kids'),('b','All About Kids - Josh Tarter'),('t','Timothy Maple'),('f','Frankfort Plant Board'),('s','Schools')]:db.add(m.Customer(id=id,name=name))
        db.flush()
        for id,c,n,a in [('a1','a','CM',''),('b1','b','All About Kids','695 Vincent Way'),('t1','t','CASH','436 Main'),('t2','t','Primary','463 Main'),('noc','f','NOC Building','220 Steele St'),('noc2','f','NOC Blg',''),('h305','f','Service Center','305 Hickory Dr'),('h310','f','310 Hickory Drive',''),('w','s','Whitehall','')]:db.add(m.Location(id=id,customer_id=c,name=n,address=a,source_key='gs-site:'+id))
        for id in ('a','b','t'):db.add(PricingDefaults(scope=id,customer_id=id,values_json=json.dumps({'labor_sell_per_hour':'125','overtime_sell_per_hour':'187.50'})))
        db.add(PricingDefaults(scope='global',values_json=json.dumps({'target_margin':35})))
        db.add(QuickJobCounter(key='jobs',last_number='261'))
        db.add(CustomerImportRun(key='active-job-board-customers-v1',fingerprint='source',backup_json='{}',result_json='{}',created_by='owner',created_at=datetime.now(timezone.utc).isoformat()));db.commit()
    yield engine
    engine.dispose();m.app.dependency_overrides.clear()

def client(role='owner'):
    m.app.dependency_overrides[m.current_user]=lambda:{'id':'owner','role':role,'username':'owner','po_prefix':None}
    return TestClient(m.app)
def payload():return {'customer_merge':{'source_id':'b','target_id':'a'},'remove_customer_id':'t','site_merges':[{'target_id':'noc','duplicate_ids':['noc2'],'name':'NOC Building','address':'220 Steele St, Frankfort, KY 40601'}],'site_updates':[{'site_id':'w','name':'White Hall Elementary','address':'2166 Lexington Rd, Richmond, KY 40475'}]}
def commit_body(c):
    response=c.post('/api/admin/site-cleanup/preview',json=payload());assert response.status_code==200,response.text
    return {**payload(),'expected_fingerprint':response.json()['fingerprint'],'confirmation':'APPLY REVIEWED SITE CLEANUP'}

def test_merge_exclusion_addresses_and_distinct_sites(seeded):
    c=client();body=commit_body(c);r=c.post('/api/admin/site-cleanup/commit',json=body);assert r.status_code==200,r.text
    assert r.json()['customers_after']==3 and r.json()['sites_after']==6
    with Session(seeded) as db:
        assert db.get(m.Customer,'t') is None and db.get(m.Customer,'b') is None
        assert db.get(m.Location,'t1') is None and db.get(m.Location,'t2') is None
        assert db.get(m.Location,'b1').customer_id=='a'
        assert db.get(m.Location,'a1') and db.get(m.Location,'b1')
        assert db.get(m.Location,'h305').address=='305 Hickory Dr'
        assert db.get(m.Location,'h310').name=='310 Hickory Drive'
        assert db.get(m.Location,'noc2') is None
        assert db.get(m.Location,'w').address.startswith('2166 Lexington')
        assert db.get(QuickJobCounter,'jobs').last_number=='261'
        assert json.loads(db.get(PricingDefaults,'a').values_json)['labor_sell_per_hour']=='125'
        assert db.get(m.User,'owner') and db.get(PricingDefaults,'global')
        assert 'noc2' in db.get(CustomerImportRun,sc.KEY).backup_json
    assert c.post('/api/admin/site-cleanup/commit',json=body).json()['already_applied']

def test_owner_only_stale_and_cross_customer_merges_rejected(seeded):
    assert client('technician').post('/api/admin/site-cleanup/preview',json=payload()).status_code==403
    c=client();body=commit_body(c)
    with Session(seeded) as db:db.get(m.Location,'h310').address='Actual independent site';db.commit()
    assert c.post('/api/admin/site-cleanup/commit',json=body).status_code==409
    bad=payload();bad['site_merges'][0]['duplicate_ids']=['b1']
    assert c.post('/api/admin/site-cleanup/preview',json=bad).status_code==422
    with Session(seeded) as db:assert db.get(m.Customer,'t') and db.get(m.Location,'noc2')

def test_history_and_pricing_conflicts_block_cleanup(seeded):
    c=client()
    with Session(seeded) as db:db.get(PricingDefaults,'b').values_json='{"labor_sell_per_hour":"100"}';db.commit()
    assert c.post('/api/admin/site-cleanup/preview',json=payload()).status_code==409
    with Session(seeded) as db:
        db.get(PricingDefaults,'b').values_json=db.get(PricingDefaults,'a').values_json
        db.add(m.Job(job_number='LMS0262',customer_id='t',description='Real job'));db.commit()
    assert c.post('/api/admin/site-cleanup/preview',json=payload()).status_code==409
    with Session(seeded) as db:assert db.get(m.Location,'noc2')

def test_contact_conflicts_and_rollback(seeded):
    c=client()
    with Session(seeded) as db:
        db.add_all([SiteMetadata(location_id='noc',contact='Contact A'),SiteMetadata(location_id='noc2',contact='Contact B')]);db.commit()
    assert c.post('/api/admin/site-cleanup/preview',json=payload()).status_code==409
    with Session(seeded) as db:db.get(SiteMetadata,'noc2').contact='Contact A';db.commit()
    body=commit_body(c)
    def reject(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('DELETE FROM customers'):raise RuntimeError('simulated cleanup failure')
    event.listen(seeded,'before_cursor_execute',reject)
    try:
        with pytest.raises(RuntimeError,match='simulated cleanup failure'):c.post('/api/admin/site-cleanup/commit',json=body)
    finally:event.remove(seeded,'before_cursor_execute',reject)
    with Session(seeded) as db:
        assert db.get(m.Customer,'b') and db.get(m.Customer,'t') and db.get(m.Location,'noc2')
        assert not db.get(CustomerImportRun,sc.KEY)
