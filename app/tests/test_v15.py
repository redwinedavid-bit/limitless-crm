"""v1.5 options/revisions/PDF, PM agreements, job-site isolation and deficiency conversion."""
import uuid
from datetime import datetime,timezone,timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from server.main import app,engine
from server.security import User,password_hash

PW='only-for-local-tests-2026'

def client(role='owner'):
    suffix=uuid.uuid4().hex[:10]
    with Session(engine) as db:
        db.add(User(username='q15'+suffix,display_name='V15 '+role,role=role,password_hash=password_hash(PW)))
        db.commit()
    c=TestClient(app)
    assert c.post('/api/auth/login',json={'username':'q15'+suffix,'password':PW}).status_code==200
    return c,suffix

def setup(c,tag):
    cust=c.post('/api/customers',json={'name':'Maintenance Customer '+tag}).json()
    number='LMS'+str(int(tag,16))
    assert c.post('/api/jobs',json={'job_number':number,'customer_id':cust['id'],'location_name':'Service Site A','address':'100 Test Rd'}).status_code==201
    locations=[x for x in c.get('/api/locations').json() if x['customer_id']==cust['id']]
    equipment=c.post('/api/equipment',json={'location_id':locations[0]['id'],'asset_tag':'RTU-1','manufacturer':'Carrier','model':'TEST-123'})
    assert equipment.status_code==201,equipment.text
    return cust,number,equipment.json()['id']

def option(name, price, cost):return {'name':name,'description':'Install tested equipment and verify operation','price':price,'estimated_cost':cost}

def quote_body(job,tag):
    return {'job_number':job,'number':'EST-'+tag,'title':'Replace HVAC unit','scope':'Remove failed unit. Install new split system. Start up and test.',
      'inclusions':'Labor, equipment, wiring, refrigerant','exclusions':'Drywall restoration and structural work',
      'warranty':'1 year workmanship; materials per manufacturer','payment_terms':'50% acceptance / 50% completion',
      'project_timeline':'4–8 weeks after equipment release',
      'options':[option('Two stage',151055.98,110000),option('Inverter',137370.51,100000)]}

def test_proposal_options_revision_acceptance_and_pdf():
    c,tag=client();cust,job,asset=setup(c,tag)
    data=quote_body(job,tag)
    p=c.post('/api/estimates/advanced',json=data);assert p.status_code==201,p.text
    record=p.json();id=record['id'];assert record['revision']==1 and record['total']==151055.98
    assert len(c.get('/api/estimates/advanced').json())>=1
    pdf=c.get('/api/estimates/advanced/'+id+'/pdf')
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF-')
    assert c.post('/api/proposals/'+id+'/approve').status_code==409  # old endpoint must not bypass selection
    edit={k:v for k,v in data.items() if k not in ('job_number','number')}
    edit['expected_revision']=1;edit['warranty']='Two years on workmanship'
    up=c.put('/api/estimates/advanced/'+id,json=edit)
    assert up.status_code==200,up.text
    assert up.json()['revision']==2
    assert c.put('/api/estimates/advanced/'+id,json=edit).status_code==409
    revs=c.get('/api/estimates/advanced/'+id+'/revisions').json();assert [x['version'] for x in revs]==[1,2]
    assert c.post('/api/estimates/advanced/'+id+'/record-acceptance',json={'selected_option':1,'accepted_by':'Facilities Director','reference':'email-ref-100','customer_authorization_confirmed':False}).status_code==422
    r=c.post('/api/estimates/advanced/'+id+'/record-acceptance',json={'selected_option':1,'accepted_by':'Facilities Director','reference':'email-ref-100','customer_authorization_confirmed':True})
    assert r.status_code==200,r.text
    assert r.json()['total']==137370.51 and r.json()['selected_option']==1
    assert c.put('/api/estimates/advanced/'+id,json={**edit,'expected_revision':2}).status_code==409
    assert c.post('/api/estimates/advanced/'+id+'/record-acceptance',json={'selected_option':0,'accepted_by':'Director','reference':'duplicate','customer_authorization_confirmed':True}).status_code==409
    assert c.get('/api/estimates/advanced/'+id+'/pdf').content.startswith(b'%PDF-')
    # Existing invoice module still requires selected proposal total.
    x=c.post('/api/invoices',json={'job_number':job,'number':'I-'+tag,'proposal_id':id,'amount':'137370.51'})
    assert x.status_code==201,x.text
    x2=c.post('/api/invoices',json={'job_number':job,'number':'I2-'+tag,'proposal_id':id,'amount':'137370.51'})
    assert x2.status_code==409
    noauth=TestClient(app)
    assert noauth.get('/api/estimates/advanced/'+id+'/pdf').status_code==401


def test_pm_agreement_scopes_idempotence_renewal_and_deficiency_to_estimate():
    c,tag=client();cust,job,asset=setup(c,tag)
    other=c.post('/api/customers',json={'name':'Unrelated '+tag}).json()
    b='LMS'+str(int(tag+'1',16))
    assert c.post('/api/jobs',json={'job_number':b,'customer_id':other['id'],'location_name':'Wrong Site'}).status_code==201
    details={'number':'PM-'+tag,'customer_id':cust['id'],'start_date':'2026-10-01','end_date':'2027-10-01','amount':'2400','terms':'Quarterly',
             'coverage':[{'equipment_id':asset,'job_number':job,'interval_days':90,'first_due':'2026-11-01','checklist':['Inspect filters','Measure amp draw']} ]}
    wrong={**details,'coverage':[{**details['coverage'][0],'job_number':b}]}
    assert c.post('/api/pm/agreements',json=wrong).status_code==422
    p=c.post('/api/pm/agreements',json=details)
    assert p.status_code==201,p.text
    a=p.json();id=a['id'];assert a['status']=='Draft'
    assert c.post('/api/pm/agreements/'+id+'/schedule',json={'through_date':'2026-11-10'}).status_code==409
    assert c.post('/api/pm/agreements/'+id+'/activate').status_code==200
    assert c.post('/api/pm/agreements/'+id+'/activate').status_code==409
    first=c.post('/api/pm/agreements/'+id+'/schedule',json={'through_date':'2026-11-10'});assert first.status_code==200,first.text
    assert len(first.json()['created'])==1
    visit=first.json()['created'][0]
    assert visit['job_number']==job
    second=c.post('/api/pm/agreements/'+id+'/schedule',json={'through_date':'2026-11-10'})
    assert len(second.json()['created'])==0 and second.json()['skipped'][0]['reason']=='already scheduled'
    assert c.post('/api/estimates/from-pm-visit/'+visit['visit_id'],json={'number':'PD-'+tag,'price':200}).status_code==409
    # Inspection can only be submitted by a technician assigned to the PM work order.
    w=visit['work_order_id'];technical,salt=client('technician')
    assert technical.post('/api/pm/visits/'+visit['visit_id']+'/inspection',json={'results':{'Inspect filters':'fail','Measure amp draw':'pass'}}).status_code==403
    from sqlalchemy import select
    from server.security import User
    with Session(engine) as db:
        uid=db.scalar(select(User.id).where(User.username=='q15'+salt))
    start=datetime(2026,11,1,13,tzinfo=timezone.utc)
    assign=c.post('/api/work-orders/'+w+'/assignments',json={'technician_id':uid,'scheduled_start':start.isoformat(),'scheduled_end':(start+timedelta(hours=1)).isoformat()})
    assert assign.status_code==201,assign.text
    inspect=technical.post('/api/pm/visits/'+visit['visit_id']+'/inspection',json={'results':{'Inspect filters':'fail','Measure amp draw':'pass'}})
    assert inspect.status_code==201,inspect.text
    assert inspect.json()['failed_items']==['Inspect filters']
    response=c.post('/api/estimates/from-pm-visit/'+visit['visit_id'],json={'number':'PD-'+tag,'price':'275','estimated_cost':'125'})
    assert response.status_code==201,response.text
    assert 'Inspect filters' in response.json()['scope']
    assert c.post('/api/estimates/from-pm-visit/'+visit['visit_id'],json={'number':'P2-'+tag,'price':'300'}).status_code==409
    # The inspection advances due date by 90 days and scheduling the next visit works.
    next_=c.post('/api/pm/agreements/'+id+'/schedule',json={'through_date':'2027-02-01'})
    assert next_.status_code==200,next_.text
    assert len(next_.json()['created'])==1
    assert c.get('/pm').status_code==200 and c.get('/pm_v15.js').status_code==200
    assert c.get('/estimates-workspace').status_code==200 and c.get('/estimates-workspace.js').status_code==200


def test_work_order_to_estimate_and_authorization():
    c,tag=client();cust,job,asset=setup(c,tag)
    w=c.post('/api/work-orders',json={'job_number':job,'summary':'Refrigerant leak'}).json()['id']
    payload={'work_order_id':w,'number':'REC-'+tag,'price':'850.00'}
    assert c.post('/api/estimates/from-work-order',json=payload).status_code==422
    technician,_=client('technician');assert technician.post('/api/estimates/from-work-order',json=payload).status_code==403
    # Office must not write technician field notes; seed a note through the same persisted model.
    from server.field_v14 import FieldReport
    from sqlalchemy import select
    from server.security import User
    with Session(engine) as db:
        uid=db.scalar(select(User.id).where(User.username=='q15'+tag))
        db.add(FieldReport(work_order_id=w,recommendations='Replace damaged contactor, verify compressor current',updated_by=uid))
        db.commit()
    out=c.post('/api/estimates/from-work-order',json=payload)
    assert out.status_code==201,out.text
    assert out.json()['source_type']=='work_order' and 'Replace damaged contactor' in out.json()['scope']
    assert c.post('/api/estimates/from-work-order',json={**payload,'number':'RECC-'+tag}).status_code==409
