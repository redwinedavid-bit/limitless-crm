import uuid
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from server import main as m
from server.purchasing_v13 import POInvoiceCost
from test_time_clock import setup_clock
from test_call_pricing import quote


def test_job_pos_bill_once_without_parts_used_and_fee_is_optional(setup_clock):
    clients,orders,now,tech_id,job_number=setup_clock;o=clients['owner'];t=clients['tech']
    o.put('/api/labor-rates',json={'technician_id':tech_id,'hourly_cost':30,'mileage_rate':.5})
    with Session(m.engine) as db:
        job=db.scalar(select(m.Job).where(m.Job.job_number==job_number))
        po=m.PurchaseOrder(po_number='QA'+uuid.uuid4().hex,job_id=job.id,vendor='Supplier',description='Motor',amount=100,received_value=0,status='Issued',source='crm_v13',issue_date=now[0].date())
        db.add(po);db.commit();pid=po.id
    invoices=[]
    for i,wo in enumerate(orders):
        start=now[0]+timedelta(days=i)
        entry=t.post(f'/api/work-orders/{wo}/time',json={'started_at':start.isoformat(),'ended_at':(start+timedelta(hours=2)).isoformat()}).json()
        o.post('/api/time/'+entry['id']+'/approve',json={})
        t.put(f'/api/work-orders/{wo}/mileage',json={'start_odometer':100,'end_odometer':100,'expected_revision':0})
        data=quote(mode='actual',parts=[],labor_sell_per_hour=100,service_charge=100)
        base=f'/api/call-pricing/{wo}'
        saved=o.put(base+'/actual',json=data);assert saved.status_code==200,saved.text
        snapshot=saved.json()['pricing']
        assert snapshot['po_material_cost']==('100.00' if i==0 else '0')
        assert snapshot['selling_subtotal']==('400.00' if i==0 else '300.00')
        body={'expected_revision':1,'number':'POINV'+uuid.uuid4().hex,'taxable':False}
        exported=o.post(base+'/invoice/export',json=body);assert exported.status_code==200,exported.text
        inv=exported.json()['id'];invoices.append(inv)
        assert o.post(base+'/invoice/export',json=body).json()['id']==inv
        record=next(x for x in o.get('/api/invoices').json() if x['id']==inv)
        assert record['amount']==(400 if i==0 else 300)
        assert any(l['description']=='Service Fee' and l['subtotal']==100 for l in record['lines'])
    with Session(m.engine) as db:
        allocation=db.get(POInvoiceCost,pid);assert allocation.invoice_id==invoices[0] and allocation.cost==100
    path='/api/admin/invoices/'+invoices[0]+'/details';meta=o.get(path).json()
    assert o.put(path,json={'expected_revision':meta['revision'],'include_service_fee':False}).status_code==200
    record=next(x for x in o.get('/api/invoices').json() if x['id']==invoices[0]);assert record['amount']==300


def test_fee_addition_preserves_unitemized_invoice_total(setup_clock):
    o=setup_clock[0]['owner'];job=setup_clock[4]
    inv=o.post('/api/invoices',json={'job_number':job,'number':'FEE'+uuid.uuid4().hex,'amount':150}).json()
    path='/api/admin/invoices/'+inv['id']+'/details'
    assert o.put(path,json={'expected_revision':0,'include_service_fee':True,'taxable':False}).status_code==200
    record=next(x for x in o.get('/api/invoices').json() if x['id']==inv['id']);assert record['amount']==250
