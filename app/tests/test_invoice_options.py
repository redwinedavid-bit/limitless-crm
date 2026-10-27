import io
import uuid
from datetime import timedelta
from pypdf import PdfReader
from test_time_clock import setup_clock
from test_call_pricing import quote


def test_standard_mileage_layout_summary_and_tax_choices(setup_clock):
    clients,orders,now,tech_id,job=setup_clock;owner=clients['owner'];tech=clients['tech'];wo=orders[0]
    owner.put('/api/labor-rates',json={'technician_id':tech_id,'hourly_cost':30,'mileage_rate':'.5'})
    entry=tech.post(f'/api/work-orders/{wo}/time',json={'started_at':now[0].isoformat(),'ended_at':(now[0]+timedelta(hours=2)).isoformat()}).json()
    owner.post('/api/time/'+entry['id']+'/approve',json={})
    tech.put(f'/api/work-orders/{wo}/mileage',json={'start_odometer':100,'end_odometer':110,'expected_revision':0})
    pricing=quote(mode='actual',parts=[],labor_sell_per_hour=100,mileage_sell_per_mile=0)
    assert owner.put(f'/api/call-pricing/{wo}/actual',json=pricing).status_code==200
    r=owner.post(f'/api/call-pricing/{wo}/invoice/export',json={'expected_revision':1,'number':'OPT'+uuid.uuid4().hex,'separate_material_labor':True,'taxable':False,'tax_rate':6})
    assert r.status_code==200,r.text
    inv=r.json()['id'];record=next(i for i in owner.get('/api/invoices').json() if i['id']==inv)
    assert record['amount']==210
    assert [(l['description'],l['subtotal']) for l in record['lines']]==[('Labor',200),('Materials',10)]
    path='/api/admin/invoices/'+inv+'/details';meta=owner.get(path).json()
    assert meta['values']['job_summary']=='Assigned one'
    def update(separate,taxable,rate):
        current=owner.get(path).json()
        return owner.put(path,json={'expected_revision':current['revision'],'job_summary':'Completed HVAC repair','separate_material_labor':separate,'taxable':taxable,'tax_rate':rate})
    assert update(False,True,0).status_code==422
    assert update(False,True,6).status_code==200
    record=next(i for i in owner.get('/api/invoices').json() if i['id']==inv);assert record['amount']==222.6
    pdf=owner.get('/api/invoices/'+inv+'/pdf');assert pdf.status_code==200
    text='\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert 'JOB SUMMARY' in text and 'Completed HVAC repair' in text and 'Services & Materials' in text
    assert 'mileage' not in text.lower() and '$222.60' in text
    assert update(True,False,6).status_code==200
    record=next(i for i in owner.get('/api/invoices').json() if i['id']==inv);assert record['amount']==210
    assert tech.get(f'/api/work-orders/{wo}/mileage').json()[0]['invoiced']
    assert owner.post('/api/invoices/'+inv+'/issue').status_code==200
    assert update(False,True,6).status_code==409
