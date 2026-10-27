from datetime import date
from decimal import Decimal
import uuid
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from server import main as m
from server.business_overview import purchasing_events
from server.purchasing_v13 import details
from test_time_clock import setup_clock


def test_existing_mileage_is_applied_once_at_startup(setup_clock):
    clients,orders,now,tech_id,job=setup_clock
    clients['owner'].put('/api/labor-rates',json={'technician_id':tech_id,'hourly_cost':30,'mileage_rate':'.60'})
    with Session(m.engine) as db:
        db.add(m.CallMileage(work_order_id=orders[0],technician_id=tech_id,start_odometer=100,end_odometer=110,miles=10,revision=1,updated_at=now[0]));db.commit()
    apply=next(f for f in m.app.router.on_startup if f.__name__=='apply_existing_mileage')
    apply();apply()
    with Session(m.engine) as db:
        row=db.get(m.CallMileage,(orders[0],tech_id))
        assert row.approved_cost==Decimal('6.00') and row.revision==1
        assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.entity_id==orders[0],m.AuditEvent.event=='AUTO_APPLY_CALL_MILEAGE'))==1
    assert clients['owner'].get('/api/jobs/'+job+'/costs').json()['approved_field_cost']==6


def test_issue_cost_posts_once_and_receipts_replace_it(setup_clock):
    clients,orders,now,tech_id,job_number=setup_clock
    uid=clients['owner'].get('/api/auth/me').json()['id']
    with Session(m.engine) as db:
        job=db.scalar(select(m.Job).where(m.Job.job_number==job_number))
        po=m.PurchaseOrder(po_number='TEST'+uuid.uuid4().hex,job_id=job.id,vendor='Supplier',amount=80,received_value=0,status='Issued',source='crm_v13',issue_date=date(2045,1,1))
        db.add(po);db.flush()
        assert details(db,po)['actual']==80 and details(db,po)['committed']==0
        assert sum(amount for day,amount,kind in purchasing_events(db) if day==date(2045,1,1))==80
        db.add(m.POReceipt(po_id=po.id,reference='Partial',amount=40,received_on=date(2045,1,2),recorded_by=uid));po.received_value=40;po.status='Partially Received';db.flush()
        assert details(db,po)['actual']==80
        assert sum(amount for day,amount,kind in purchasing_events(db) if date(2045,1,1)<=day<=date(2045,1,2))==80
        db.add(m.POVendorInvoice(po_id=po.id,vendor_key='supplier',reference=uuid.uuid4().hex,amount=45,matched_receipt_value=40,invoice_date=date(2045,1,3),variance_reason='Recorded final supplier charge',recorded_by=uid));db.flush()
        assert details(db,po)['actual']==85
        assert sum(amount for day,amount,kind in purchasing_events(db) if date(2045,1,1)<=day<=date(2045,1,3))==85
