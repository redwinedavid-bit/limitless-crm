"""v1.4 field report / photos / signature security and locking."""
import base64, io, uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
from PIL import Image, ImageDraw
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from server.main import app,engine
from server.security import User,password_hash


def login(c,name,password):
    c.cookies.clear()
    r=c.post('/api/auth/login',json={'username':name,'password':password})
    assert r.status_code==200,r.text


def png(size=(260,100),draw=True):
    im=Image.new('RGB',size,'white')
    if draw:
        pen=ImageDraw.Draw(im)
        pen.line([(20,62),(70,34),(130,60),(210,27)],fill='black',width=4)
    b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()


def test_v14_field_service_readings_photos_signature_and_pdf(tmp_path,monkeypatch):
    from server import field_v14
    monkeypatch.setattr(field_v14,'UPLOAD_DIR',tmp_path/'private-photo-store')
    mark=uuid.uuid4().hex[:10];pw='secure-workflow-test-2026'
    names=['owner','assigned','other','estimator']
    with Session(engine) as db:
        for who in names:
            role={'owner':'owner','assigned':'technician','other':'technician','estimator':'estimator'}[who]
            db.add(User(username=who+mark,display_name=who.title(),role=role,password_hash=password_hash(pw)))
        db.commit()
        assigned_id=db.query(User).filter_by(username='assigned'+mark).one().id
    c=TestClient(app);login(c,'owner'+mark,pw)
    cust=c.post('/api/customers',json={'name':'Field Work QA '+mark});assert cust.status_code==201,cust.text
    number='LMS'+str(int(mark,16))
    assert c.post('/api/jobs',json={'job_number':number,'customer_id':cust.json()['id'],
      'location_name':'Building Q','address':'123 Testing Lane','description':'Heating repair'}).status_code==201
    loc=next(x for x in c.get('/api/locations').json() if x['customer_id']==cust.json()['id'])
    asset=c.post('/api/equipment',json={'location_id':loc['id'],'asset_tag':'AHU-A',
      'manufacturer':'Daikin','model':'REYQ123','serial':'TESTSN123','refrigerant':'R410A'})
    assert asset.status_code==201,asset.text
    w=c.post('/api/work-orders',json={'job_number':number,'summary':'No heat reported'}).json();id=w['id']
    assert c.post(f'/api/work-orders/{id}/equipment/{asset.json()["id"]}').status_code==201
    start=datetime(2026,11,11,14,tzinfo=timezone.utc)
    assert c.post(f'/api/work-orders/{id}/assignments',json={'technician_id':assigned_id,
        'scheduled_start':start.isoformat(),'scheduled_end':(start+timedelta(hours=2)).isoformat()}).status_code==201
    login(c,'other'+mark,pw)
    assert c.get(f'/api/field/work-orders/{id}').status_code==403
    assert c.get(f'/api/field/work-orders/{id}/report.pdf').status_code==403
    assert c.put(f'/api/field/work-orders/{id}',json={'diagnosis':'Unauthorised'}).status_code==403
    login(c,'estimator'+mark,pw)
    assert c.put(f'/api/field/work-orders/{id}',json={'diagnosis':'Not my role'}).status_code==403
    login(c,'assigned'+mark,pw)
    assert c.get('/field').status_code==200
    assert c.get('/field.js').status_code==200
    assert c.post(f'/api/field/work-orders/{id}/readings',json={'equipment_id':'nonsense','parameter':'Suction pressure','value':'125','unit':'psig'}).status_code==422
    reading=c.post(f'/api/field/work-orders/{id}/readings',json={'equipment_id':asset.json()['id'],
        'parameter':'Suction pressure','value':'125.500','unit':'psig','note':'Circuit 1'})
    assert reading.status_code==201,reading.text
    assert c.post(f'/api/field/work-orders/{id}/readings',json={'equipment_id':asset.json()['id'],
        'parameter':'Suction pressure','value':'NaN','unit':'psig'}).status_code==422
    bad=c.post(f'/api/field/work-orders/{id}/photos',data={'caption':'test'},files={'file':('bad.txt',b'not an image','text/plain')})
    assert bad.status_code==415,bad.text
    photo=c.post(f'/api/field/work-orders/{id}/photos',data={'caption':'Before repair','equipment_id':asset.json()['id']},
        files={'file':('unit.png',png(),'image/png')})
    assert photo.status_code==201,photo.text
    download=photo.json()['download_url'];assert c.get(download).content==png()
    report={'customer_concern':'No heat in east wing','diagnosis':'Loose compressor connector',
      'work_performed':'Replaced damaged terminal connector, verified heat operation',
      'recommendations':'Continue quarterly maintenance','follow_up_required':False}
    assert c.put(f'/api/field/work-orders/{id}',json=report).status_code==200
    pdf=c.get(f'/api/field/work-orders/{id}/report.pdf')
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF-'),pdf.text[:200] if not pdf.content.startswith(b'%PDF-') else ''
    assert b'Unsigned draft' in pdf.content or pdf.headers['content-type']=='application/pdf'
    invalid_sign=c.post(f'/api/field/work-orders/{id}/signature',json={'signer_name':'Maintenance Supervisor',
      'signature_data':'data:image/png;base64,'+base64.b64encode(png(draw=False)).decode(), 'acknowledgment':True})
    assert invalid_sign.status_code==422
    before=c.post(f'/api/field/work-orders/{id}/signature',json={'signer_name':'Maintenance Supervisor',
      'signature_data':'data:image/png;base64,'+base64.b64encode(png()).decode(),'acknowledgment':False})
    assert before.status_code==422
    login(c,'other'+mark,pw);assert c.get(download).status_code==403
    login(c,'assigned'+mark,pw)
    signed=c.post(f'/api/field/work-orders/{id}/signature',json={'signer_name':'Maintenance Supervisor',
      'signer_title':'Facilities','signature_data':'data:image/png;base64,'+base64.b64encode(png()).decode(),
      'acknowledgment':True})
    assert signed.status_code==201,signed.text
    data=c.get(f'/api/field/work-orders/{id}').json();assert data['signed'] and data['report']['diagnosis']==report['diagnosis']
    assert c.get(f'/api/field/work-orders/{id}/report.pdf').status_code==200
    assert c.put(f'/api/field/work-orders/{id}',json=report).status_code==409
    assert c.post(f'/api/field/work-orders/{id}/readings',json={'equipment_id':asset.json()['id'],
        'parameter':'Voltage','value':'220','unit':'V'}).status_code==409
    assert c.post(f'/api/field/work-orders/{id}/photos',files={'file':('add.png',png(),'image/png')}).status_code==409
    assert c.post(f'/api/work-orders/{id}/notes',json={'note':'Late unauthorized addition'}).status_code==409
    assert c.patch(f'/api/work-orders/{id}/status',json={'status':'In Progress'}).status_code==409
    assert c.post(f'/api/field/work-orders/{id}/signature',json={'signer_name':'Repeated',
      'signature_data':'data:image/png;base64,'+base64.b64encode(png()).decode(),'acknowledgment':True}).status_code==409
    login(c,'owner'+mark,pw)
    assert c.get(f'/api/field/work-orders/{id}/report.pdf').status_code==200
    assert c.post(f'/api/work-orders/{id}/equipment/{asset.json()["id"]}').status_code==409
