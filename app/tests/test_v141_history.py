"""Equipment history across work orders and technician scope."""
import io, uuid
from datetime import datetime,timezone,timedelta
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from server.main import app,engine
from server.security import User,password_hash


def test_equipment_history_access_condition_and_trends(tmp_path,monkeypatch):
    from server import field_v14
    monkeypatch.setattr(field_v14,'UPLOAD_DIR',tmp_path/'field-uploads')
    token=uuid.uuid4().hex[:10];pw='strong-password-for-history-2026'
    with Session(engine) as db:
        for name,role in [('owner','owner'),('assigned','technician'),('outsider','technician')]:
            db.add(User(username=name+token,display_name=name,role=role,password_hash=password_hash(pw)))
        db.commit()
        tech=db.query(User).filter_by(username='assigned'+token).one().id
    c=TestClient(app)
    def login(name):
        c.cookies.clear()
        response=c.post('/api/auth/login',json={'username':name+token,'password':pw})
        assert response.status_code==200,response.text
    login('owner')
    cust=c.post('/api/customers',json={'name':'Equipment History '+token}).json()
    jobnum='LMS'+str(int(token,16))
    r=c.post('/api/jobs',json={'job_number':jobnum,'customer_id':cust['id'],
         'location_name':'School mechanical room','address':'125 Equipment Lane','description':'Heating and cooling repairs'})
    assert r.status_code==201,r.text
    loc=next(x for x in c.get('/api/locations').json() if x['customer_id']==cust['id'])
    asset=c.post('/api/equipment',json={'location_id':loc['id'],'asset_tag':'RTU-1',
         'manufacturer':'Carrier','model':'ABC-20','serial':'TEST-001'}).json()
    other=c.post('/api/equipment',json={'location_id':loc['id'],'asset_tag':'RTU-2',
         'manufacturer':'Carrier','model':'ABC-10'}).json()
    ids=[]
    for day in (1,2):
        r=c.post('/api/work-orders',json={'job_number':jobnum,'summary':f'Equipment visit {day}'})
        assert r.status_code==201,r.text
        wo=r.json()['id'];ids.append(wo)
        assert c.post(f'/api/work-orders/{wo}/equipment/{asset["id"]}').status_code==201
        dt=datetime(2026,12,day,14,tzinfo=timezone.utc)
        a=c.post(f'/api/work-orders/{wo}/assignments',json={'technician_id':tech,
             'scheduled_start':dt.isoformat(),'scheduled_end':(dt+timedelta(hours=2)).isoformat()})
        assert a.status_code==201,a.text
    old,new=ids
    login('assigned')
    for wo,amps,rating in [(old,'7.6','Poor'),(new,'6.4','Good')]:
        r=c.post(f'/api/field/work-orders/{wo}/readings',json={'equipment_id':asset['id'],
             'parameter':'Compressor amps L1','value':amps,'unit':'A','note':'Circuit 1'})
        assert r.status_code==201,r.text
        r=c.put(f'/api/field/work-orders/{wo}/equipment/{asset["id"]}/condition',
             json={'rating':rating,'note':'Contactor inspected'})
        assert r.status_code==200,r.text
    assert c.put(f'/api/field/work-orders/{new}/equipment/{asset["id"]}/condition',
         json={'rating':'not-a-value','note':'bad'}).status_code==422
    assert c.put(f'/api/field/work-orders/{new}/equipment/{other["id"]}/condition',
         json={'rating':'Good','note':'Not linked'}).status_code==422
    assert c.get(f'/api/field/work-orders/{new}/equipment/{other["id"]}/history').status_code==422
    history=c.get(f'/api/field/work-orders/{new}/equipment/{asset["id"]}/history')
    assert history.status_code==200,history.text
    data=history.json()
    assert data['equipment']['asset_tag']=='RTU-1'
    assert len(data['visits'])==2
    assert {v['condition']['rating'] for v in data['visits']}=={'Poor','Good'}
    trend=next(t for t in data['trends'] if t['parameter']=='Compressor amps L1')
    assert trend['latest']==6.4 and trend['previous']==7.6,trend
    assert all(len(v['readings'])==1 for v in data['visits'])
    im=Image.new('RGB',(40,50),'white');buf=io.BytesIO();im.save(buf,format='PNG')
    r=c.post(f'/api/field/work-orders/{old}/photos',data={'equipment_id':asset['id'],'caption':'Before service'},
        files={'file':('motor.png',buf.getvalue(),'image/png')})
    assert r.status_code==201,r.text
    ph=r.json()['id']
    path=f'/api/field/work-orders/{new}/equipment/{asset["id"]}/photos/{ph}'
    assert c.get(path).status_code==200
    login('outsider')
    assert c.get(f'/api/field/work-orders/{new}/equipment/{asset["id"]}/history').status_code==403
    assert c.get(path).status_code==403
    assert c.get(f'/api/equipment/{asset["id"]}/history').status_code==403
    login('owner')
    assert c.get(f'/api/equipment/{asset["id"]}/history').status_code==200
