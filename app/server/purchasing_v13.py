"""v1.3 purchasing ledger: buyer numbering, receiving, vendor matching, returns and credits.

Append-only evidence records. Historical imported PO amounts never become new costs.
One purchase order always references one existing job (database NOT NULL FK).
"""
from __future__ import annotations
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from pathlib import Path
import os, re, uuid
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, func, text, ForeignKey, Numeric, String
from sqlalchemy.orm import Session, Mapped, mapped_column
from fastapi import Depends, HTTPException, UploadFile, File, Form
from . import main as m
from . import private_storage as storage
from fastapi.responses import FileResponse

class POInvoiceCost(m.Base):
    __tablename__='po_invoice_costs'
    po_id:Mapped[str]=mapped_column(ForeignKey('purchase_orders.id',ondelete='RESTRICT'),primary_key=True)
    invoice_id:Mapped[str]=mapped_column(ForeignKey('crm_invoices.id',ondelete='RESTRICT'),nullable=False,index=True)
    cost:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)


def invoice_materials(db,wo_id):
    from .call_pricing import CallPricingExport
    work=db.get(m.WorkOrder,wo_id)
    export=db.get(CallPricingExport,(wo_id,'invoice'))
    invoice_id=export.record_id if export else None
    result=[]
    for po in db.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.job_id==work.job_id,m.PurchaseOrder.source!='historical').order_by(m.PurchaseOrder.po_number)):
        link=db.get(POInvoiceCost,po.id)
        if link and link.invoice_id!=invoice_id:continue
        cost=details(db,po)['actual']
        if cost>0:result.append({'id':po.id,'number':po.po_number,'cost':str(cost)})
    return result

MONEY = Decimal('0.01')
def val(x):return m.cents(x)
def rows(db,table,po_id):return list(db.scalars(select(table).where(table.po_id==po_id)))
def total(items,attr='amount'):return sum((val(getattr(i,attr)) for i in items),Decimal('0.00'))

def details(db,p):
    if p.source=='historical':
        return {'actual':Decimal('0.00'),'committed':Decimal('0.00'), 'received':val(p.received_value),
                'returns':Decimal('0.00'),'invoiced':Decimal('0.00'),'credits':Decimal('0.00'),
                'matched':Decimal('0.00'),'unbilled_received':Decimal('0.00')}
    returned=total(rows(db,m.POReturn,p.id))
    bills=rows(db,m.POVendorInvoice,p.id)
    invoiced=total(bills)
    matched=total(bills,'matched_receipt_value')
    credits=total(rows(db,m.POVendorCredit,p.id))
    gross_received=val(p.received_value)
    net_received=max(Decimal('0.00'),gross_received-returned)
    open_received=max(Decimal('0.00'),net_received-matched)
    actual=val(invoiced-credits+open_received)
    # Each newly created PO is immediately issued and committed; no manager approval is required.
    committed=max(Decimal('0.00'),val(p.amount)-gross_received) if p.status in ('Issued','Partially Received','Received') else Decimal('0.00')
    # Job cost is posted on issue. Receiving/bills replace that baseline; never add it twice.
    actual=val(actual+committed)
    committed=Decimal('0.00')
    return {'actual':actual,'committed':committed,'received':gross_received,'returns':returned,
            'invoiced':invoiced,'credits':credits,'matched':matched,'unbilled_received':open_received}

def job_purchase_totals(db,j):
    pos=db.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.job_id==j.id)).all()
    entries=[details(db,p) for p in pos if p.source!='historical']
    return {'actual':val(sum((x['actual'] for x in entries),Decimal('0'))),
            'committed':val(sum((x['committed'] for x in entries),Decimal('0'))),
            'po_count':len(pos), 'new_po_count':len(entries), 'historical_po_count':len(pos)-len(entries)}

def approved_field_cost(db,job_id):
    query=select(func.coalesce(func.sum(m.TimeApproval.labor_cost+m.TimeApproval.mileage_cost),0)).join(
        m.FieldTimeEntry,m.TimeApproval.time_entry_id==m.FieldTimeEntry.id).join(
        m.WorkOrder,m.FieldTimeEntry.work_order_id==m.WorkOrder.id).where(m.WorkOrder.job_id==job_id)
    return val(db.scalar(query) or 0)+m.approved_call_mileage_cost(db,job_id)

def serialize(db,p,with_events=False):
    j=db.get(m.Job,p.job_id);d=details(db,p)
    result={'id':p.id,'number':p.po_number,'job_number':j.job_number,'vendor':p.vendor,
            'description':p.description,'amount':float(p.amount),'status':p.status,'source':p.source,
            'issued_on':p.issue_date.isoformat() if p.issue_date else None,
            'buyer_designator':p.po_number[:2] if p.po_number[:2] in ('DR','AR','DB') else None,
            'gross_received':float(d['received']),'returned':float(d['returns']),
            'vendor_invoiced':float(d['invoiced']),'vendor_credits':float(d['credits']),
            'matched_receipt_value':float(d['matched']), 'unbilled_received':float(d['unbilled_received']),
            'actual_cost':float(d['actual']),'open_commitment':float(d['committed']),
            'historical_cost_already_in_job_totals':p.source=='historical','cost_applied_to_job':p.source!='historical' and p.status!='Cancelled'}
    if with_events:
        result['attachments']=[attachment_info(a) for a in db.scalars(select(m.POAttachment).where(m.POAttachment.po_id==p.id).order_by(m.POAttachment.uploaded_at))]
        specs=[('receipts',m.POReceipt,'received_on'),('vendor_invoices',m.POVendorInvoice,'invoice_date'),
               ('returns',m.POReturn,'returned_on'),('vendor_credits_history',m.POVendorCredit,'credited_on')]
        for key,table,datefield in specs:
            result[key]=[{'id':x.id,'reference':x.reference,'amount':float(x.amount),
                         'date':getattr(x,datefield).isoformat(),
                         **({'matched_receipt_value':float(x.matched_receipt_value),'variance_reason':x.variance_reason} if key=='vendor_invoices' else {}),
                         **({'reason':x.reason} if key in ('returns','vendor_credits_history') else {})} for x in rows(db,table,p.id)]
    return result

class PONew(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_number:str=Field(min_length=3,max_length=40)
    vendor:str=Field(min_length=1,max_length=250)
    description:str=''
    amount:Decimal=Field(gt=0)
    issue_date:Optional[date]=None
class CallPONew(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:uuid.UUID
    vendor:str=Field(min_length=1,max_length=250)
    description:str=Field(min_length=1,max_length=5000)
    amount:Decimal=Field(gt=0,max_digits=14,decimal_places=2)

class ReceiptNew(BaseModel):
    reference:str=Field(min_length=2,max_length=100)
    amount:Decimal=Field(gt=0)
    received_on:date=Field(default_factory=date.today)
    note:str=''
class BillNew(BaseModel):
    reference:str=Field(min_length=2,max_length=100)
    amount:Decimal=Field(gt=0)
    matched_receipt_value:Decimal=Field(gt=0)
    invoice_date:date=Field(default_factory=date.today)
    variance_reason:str=''
class ReturnNew(BaseModel):
    reference:str=Field(min_length=2,max_length=100)
    amount:Decimal=Field(gt=0)
    returned_on:date=Field(default_factory=date.today)
    reason:str=Field(min_length=3,max_length=2000)
class CreditNew(BaseModel):
    reference:str=Field(min_length=2,max_length=100)
    amount:Decimal=Field(gt=0)
    credited_on:date=Field(default_factory=date.today)
    reason:str=Field(min_length=3,max_length=2000)
class CloseNew(BaseModel):
    reason:str=Field(min_length=3,max_length=2000)

def require_finance_or_purchase(user=Depends(m.current_user)):
    if 'view_financials' not in m.ROLE_SCOPES[user['role']] and not user.get('po_prefix'):raise HTTPException(403,'Purchasing access required')
    return user

def require_buyer(user=Depends(m.current_user)):
    if user.get('po_prefix') not in ('DR','AR','DB'):raise HTTPException(403,'Assign a unique DR/AR/DB PO designator to this account before purchasing')
    return user


def require_bill_entry(user=Depends(m.current_user)):
    if user['role'] not in ('owner','office','bookkeeper'):raise HTTPException(403,'Accounts payable access required')
    return user

PO_PREFIXES=('DR','AR','DB')
MAX_ATTACHMENT=12*1024*1024
ATTACHMENT_ROOT=Path(os.getenv('LMS_ATTACHMENT_DIR',str(m.ROOT/'private_uploads')))

def attachment_info(a):
    return {'id':a.id,'kind':a.kind,'filename':a.filename,'size_bytes':a.size_bytes,
            'mime_type':a.mime_type,'uploaded_at':a.uploaded_at.isoformat(),
            'download_url':'/api/purchasing/attachments/'+a.id+'/download'}

def visible_po(db,p,user):
    if not p:raise HTTPException(404,'Purchase order not found')
    if user['role']=='technician' and p.po_number[:2]!=user.get('po_prefix'):
        raise HTTPException(403,'This purchase order belongs to a different buyer')
    return p

def editable_po(db,number,user):
    p=writable(db,number)
    if p.po_number[:2]!=user.get('po_prefix') and user['role'] not in ('owner','office'):
        raise HTTPException(403,'You may only edit purchase orders using your own designator')
    return p

def generate_number(db,prefix):
    if prefix not in PO_PREFIXES:raise HTTPException(403,'No assigned PO designator')
    # Seed from the largest real (not preformatted empty row) historical code.
    if not db.get(m.PONumberCounter,prefix):
        historical=db.scalars(select(m.PurchaseOrder.po_number).where(m.PurchaseOrder.po_number.like(prefix+'%'))).all()
        maximum=max((int(x[2:]) for x in historical if re.fullmatch(re.escape(prefix)+r'\d+',x)),default=0)
        if db.bind.dialect.name=='sqlite':
            db.execute(text('INSERT OR IGNORE INTO po_number_counters(prefix,last_number) VALUES (:p,:n)'),{'p':prefix,'n':maximum})
        else:
            db.execute(text('INSERT INTO po_number_counters(prefix,last_number) VALUES (:p,:n) ON CONFLICT (prefix) DO NOTHING'),{'p':prefix,'n':maximum})
    sequence=db.scalar(text('UPDATE po_number_counters SET last_number=last_number+1 WHERE prefix=:p RETURNING last_number'),{'p':prefix})
    return f'{prefix}{int(sequence):04d}'

def sniff_mime(data):
    if data.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if data[:4]==b'RIFF' and data[8:12]==b'WEBP':return 'image/webp'
    if data.startswith(b'%PDF-'):return 'application/pdf'
    if len(data)>12 and data[4:8]==b'ftyp' and data[8:12] in (b'heic',b'heix',b'hevc',b'heim',b'mif1'):return 'image/heic'
    return None

def writable(db,number):
    p=db.scalar(select(m.PurchaseOrder).where(m.PurchaseOrder.po_number==number))
    if not p:raise HTTPException(404,'Purchase order not found')
    if p.source=='historical':raise HTTPException(409,'Historical imported PO is read-only; reconcile separately')
    return p

def fail_if_invalid_amount(x):
    if not x.is_finite() or val(x)<=0 or val(x)!=x:raise HTTPException(422,'Use a positive amount with at most two decimals')

def attach_purchasing(app,ns):
    ns['job_purchase_totals']=job_purchase_totals
    ns['approved_field_cost']=approved_field_cost

    @app.get('/api/purchasing/pos')
    def list_pos(job_number:Optional[str]=None,user=Depends(require_finance_or_purchase)):
        with Session(m.engine) as db:
            q=select(m.PurchaseOrder).order_by(m.PurchaseOrder.po_number.desc())
            if user['role']=='technician':q=q.where(m.PurchaseOrder.po_number.like(user['po_prefix']+'%'))
            if job_number:
                j=db.scalar(select(m.Job).where(m.Job.job_number==job_number))
                if not j:raise HTTPException(404,'Job not found')
                q=q.where(m.PurchaseOrder.job_id==j.id)
            return [serialize(db,p) for p in db.scalars(q)]

    @app.get('/api/purchasing/pos/{number}')
    def get_po(number:str,user=Depends(require_finance_or_purchase)):
        with Session(m.engine) as db:
            p=db.scalar(select(m.PurchaseOrder).where(m.PurchaseOrder.po_number==number))
            return serialize(db,visible_po(db,p,user),True)

    @app.get('/api/purchasing/jobs/{number}')
    def job_purchasing(number:str,user=Depends(require_finance_or_purchase)):
        with Session(m.engine) as db:
            j=db.scalar(select(m.Job).where(m.Job.job_number==number))
            if not j:raise HTTPException(404,'Job not found')
            if user['role']=='technician':
                own=db.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.job_id==j.id,
                    m.PurchaseOrder.po_number.like(user['po_prefix']+'%'))).all()
                return {'job_number':number,'purchase_orders':[serialize(db,p) for p in own],
                        'financials_hidden':True}
            summary=job_purchase_totals(db,j)
            field=approved_field_cost(db,j.id)
            base=val(j.historical_labor_cost+j.historical_material_cost+j.historical_mileage_cost)
            return {'job_number':number,'contract_amount':float(j.contract_amount),
                'historical_direct_cost':float(base),'approved_field_cost':float(field),
                'new_po_actual':float(summary['actual']),'new_po_commitment':float(summary['committed']),
                'total_actual':float(base+field+summary['actual']),
                'projected_cost':float(base+field+summary['actual']+summary['committed']),
                'projected_profit':float(j.contract_amount-base-field-summary['actual']-summary['committed']),
                'historical_po_excluded_from_actual':True,
                'purchase_orders':[serialize(db,p) for p in db.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.job_id==j.id).order_by(m.PurchaseOrder.po_number))]}

    @app.get('/api/purchasing/my-number')
    def my_number(user=Depends(m.current_user)):
        prefix=user.get('po_prefix')
        if prefix not in PO_PREFIXES:
            return {'po_prefix':None,'next_preview':None,'message':'Owner must assign this user a DR, AR or DB designator'}
        with Session(m.engine) as db:
            existing=db.get(m.PONumberCounter,prefix)
            prior=existing.last_number if existing else max((int(x[2:]) for x in db.scalars(select(m.PurchaseOrder.po_number).where(m.PurchaseOrder.po_number.like(prefix+'%'))) if re.fullmatch(re.escape(prefix)+r'\d+',x)),default=0)
            return {'po_prefix':prefix,'next_preview':f'{prefix}{prior+1:04d}','message':'Number is reserved on creation and may change if another session creates a PO first'}

    @app.post('/api/calls/{wo_id}/purchase-orders',status_code=201)
    def create_call_po(wo_id:str,data:CallPONew,user=Depends(require_buyer)):
        fail_if_invalid_amount(data.amount)
        if not data.vendor.strip() or not data.description.strip():raise HTTPException(422,'Enter a vendor and description')
        with Session(m.engine) as db:
            m.lock_time_user(db,user['id'])
            w=m.permitted_wo(db,wo_id,user)
            job=db.get(m.Job,w.job_id)
            key=str(uuid.uuid5(uuid.NAMESPACE_URL,'lms-call-po:'+user['id']+':'+str(data.request_id)))
            existing=db.get(m.PurchaseOrder,key)
            description=data.description.strip()
            if existing:
                if (existing.job_id!=job.id or existing.vendor!=data.vendor.strip() or existing.description!=description or val(existing.amount)!=val(data.amount)):
                    raise HTTPException(409,'This PO request was already used with different details. Review existing purchases before trying again.')
                return serialize(db,existing)
            number=generate_number(db,user['po_prefix'])
            po=m.PurchaseOrder(id=key,po_number=number,job_id=job.id,vendor=data.vendor.strip(),description=description,
                amount=val(data.amount),received_value=Decimal('0'),status='Issued',source='crm_v13',issue_date=date.today())
            db.add(po);db.flush();m.log(db,'ISSUE','purchase_order',po.id,'buyer='+user['id']+'; job='+job.job_number+'; call='+wo_id)
            result=serialize(db,po);db.commit();return result

    @app.post('/api/purchasing/pos',status_code=201)
    def create_po(data:PONew,user=Depends(require_buyer)):
        fail_if_invalid_amount(data.amount)
        with Session(m.engine) as db:
            job=db.scalar(select(m.Job).where(m.Job.job_number==data.job_number))
            if not job:raise HTTPException(422,'A PO must belong to one existing job')
            number=generate_number(db,user['po_prefix'])
            p=m.PurchaseOrder(po_number=number,job_id=job.id,vendor=data.vendor.strip(),description=data.description,
                amount=val(data.amount),received_value=Decimal('0'),status='Issued',source='crm_v13',issue_date=data.issue_date or date.today())
            db.add(p);db.flush();m.log(db,'ISSUE','purchase_order',p.id,'buyer='+user['id']+'; job='+job.job_number)
            result=serialize(db,p);db.commit();return result

    @app.post('/api/purchasing/pos/{number}/approve',status_code=410)
    def no_approvals(number:str,user=Depends(m.current_user)):
        raise HTTPException(410,'Manager approval is not part of the Limitless PO workflow')

    @app.post('/api/purchasing/pos/{number}/issue',status_code=410)
    def no_issue_step(number:str,user=Depends(m.current_user)):
        raise HTTPException(410,'New purchase orders are automatically issued when created')

    @app.post('/api/purchasing/pos/{number}/receipts',status_code=201)
    def receive(number:str,data:ReceiptNew,user=Depends(require_buyer)):
        fail_if_invalid_amount(data.amount)
        with Session(m.engine) as db:
            p=editable_po(db,number,user)
            if p.status not in ('Issued','Partially Received','Received'):raise HTTPException(409,'Only issued POs can receive materials')
            if db.scalar(select(m.POReceipt.id).where(m.POReceipt.po_id==p.id,m.POReceipt.reference==data.reference)):
                raise HTTPException(409,'Duplicate receiving reference')
            if val(p.received_value+data.amount)>val(p.amount):raise HTTPException(422,'Receiving exceeds authorized PO amount')
            db.add(m.POReceipt(po_id=p.id,reference=data.reference,amount=val(data.amount),received_on=data.received_on,
                note=data.note,recorded_by=user['id']))
            p.received_value=val(p.received_value+data.amount)
            p.status='Received' if val(p.received_value)==val(p.amount) else 'Partially Received'
            m.log(db,'RECEIVE','purchase_order',p.id,data.reference);db.flush();out=serialize(db,p,True);db.commit();return out

    @app.post('/api/purchasing/pos/{number}/vendor-invoices',status_code=201)
    def match_vendor_bill(number:str,data:BillNew,user=Depends(require_bill_entry)):
        fail_if_invalid_amount(data.amount);fail_if_invalid_amount(data.matched_receipt_value)
        with Session(m.engine) as db:
            p=writable(db,number)
            if p.status not in ('Partially Received','Received','Closed'):raise HTTPException(409,'Receive material before matching a vendor invoice')
            vendor=p.vendor.strip().casefold();reference=data.reference.strip().casefold()
            if db.scalar(select(m.POVendorInvoice.id).where(m.POVendorInvoice.vendor_key==vendor,m.POVendorInvoice.reference==reference)):
                raise HTTPException(409,'This vendor invoice number is already recorded')
            d=details(db,p)
            if val(data.matched_receipt_value)>d['received']-d['returns']-d['matched']:
                raise HTTPException(422,'Invoice match exceeds available unbilled received value')
            difference=abs(val(data.amount)-val(data.matched_receipt_value))
            threshold=max(Decimal('5.00'),val(data.matched_receipt_value*Decimal('0.02')))
            if difference>threshold and (user['role'] not in ('owner','office') or len(data.variance_reason.strip())<10):
                raise HTTPException(422,'Variance above tolerance requires manager entry with a documented reason')
            bill=m.POVendorInvoice(po_id=p.id,vendor_key=vendor,reference=reference,
                amount=val(data.amount),matched_receipt_value=val(data.matched_receipt_value),
                invoice_date=data.invoice_date,variance_reason=data.variance_reason.strip(),recorded_by=user['id'])
            db.add(bill);db.flush();m.log(db,'MATCH_VENDOR_INVOICE','purchase_order',p.id,data.reference);out=serialize(db,p,True);db.commit();return out

    @app.post('/api/purchasing/pos/{number}/returns',status_code=201)
    def return_goods(number:str,data:ReturnNew,user=Depends(require_buyer)):
        fail_if_invalid_amount(data.amount)
        with Session(m.engine) as db:
            p=editable_po(db,number,user)
            if p.status not in ('Partially Received','Received','Closed'):raise HTTPException(409,'Return requires a received PO')
            if db.scalar(select(m.POReturn.id).where(m.POReturn.po_id==p.id,m.POReturn.reference==data.reference)):
                raise HTTPException(409,'Duplicate return reference')
            d=details(db,p)
            if val(data.amount)>d['received']-d['returns']:raise HTTPException(422,'Return exceeds material received')
            db.add(m.POReturn(po_id=p.id,reference=data.reference,amount=val(data.amount),
                returned_on=data.returned_on,reason=data.reason,recorded_by=user['id']))
            m.log(db,'RETURN','purchase_order',p.id,data.reference);db.flush();out=serialize(db,p,True);db.commit();return out

    @app.post('/api/purchasing/pos/{number}/credits',status_code=201)
    def vendor_credit(number:str,data:CreditNew,user=Depends(require_bill_entry)):
        fail_if_invalid_amount(data.amount)
        with Session(m.engine) as db:
            p=writable(db,number)
            vendor=p.vendor.strip().casefold();ref=data.reference.strip().casefold()
            if db.scalar(select(m.POVendorCredit.id).where(m.POVendorCredit.vendor_key==vendor,m.POVendorCredit.reference==ref)):
                raise HTTPException(409,'Duplicate vendor credit reference')
            d=details(db,p)
            if val(data.amount)>d['invoiced']-d['credits']:raise HTTPException(422,'Credit exceeds recorded vendor invoices')
            db.add(m.POVendorCredit(po_id=p.id,vendor_key=vendor,reference=ref,amount=val(data.amount),
                credited_on=data.credited_on,reason=data.reason,recorded_by=user['id']))
            m.log(db,'VENDOR_CREDIT','purchase_order',p.id,data.reference);db.flush();out=serialize(db,p,True);db.commit();return out

    @app.post('/api/purchasing/pos/{number}/close')
    def close_po(number:str,data:CloseNew,user=Depends(require_buyer)):
        with Session(m.engine) as db:
            p=editable_po(db,number,user)
            if p.status not in ('Issued','Partially Received','Received'):raise HTTPException(409,'Only issued POs can be closed')
            p.status='Closed';m.log(db,'CLOSE','purchase_order',p.id,data.reason);db.flush();out=serialize(db,p);db.commit();return out

    @app.post('/api/purchasing/pos/{number}/cancel')
    def cancel_po(number:str,data:CloseNew,user=Depends(require_buyer)):
        with Session(m.engine) as db:
            p=editable_po(db,number,user)
            if p.status!='Issued' or val(p.received_value)>0:
                raise HTTPException(409,'Cannot cancel a received or closed PO; close open balances instead')
            p.status='Cancelled';m.log(db,'CANCEL','purchase_order',p.id,data.reason);db.flush();out=serialize(db,p);db.commit();return out

    @app.post('/api/purchasing/pos/{number}/attachments',status_code=201)
    async def upload_po_attachment(number:str,file:UploadFile=File(...),kind:str=Form('receipt'),user=Depends(m.current_user)):
        if kind not in ('receipt','vendor_invoice','packing_slip','other'):
            raise HTTPException(422,'Invalid attachment type')
        if user['role'] not in ('owner','office') and not user.get('po_prefix'):
            raise HTTPException(403,'No purchasing access')
        with Session(m.engine) as db:
            p=db.scalar(select(m.PurchaseOrder).where(m.PurchaseOrder.po_number==number))
            visible_po(db,p,user)
            if p.po_number[:2]!=user.get('po_prefix') and user['role'] not in ('owner','office'):
                raise HTTPException(403,'Can only attach receipts to your own POs')
            data=await file.read(MAX_ATTACHMENT+1)
            if len(data)>MAX_ATTACHMENT:raise HTTPException(413,'Receipt files must be 12 MB or smaller')
            mime=sniff_mime(data)
            if mime is None:raise HTTPException(415,'Upload JPEG, PNG, WebP, HEIC, or PDF')
            if not data:raise HTTPException(422,'Empty file')
            original=(file.filename or 'receipt').replace('\\','/').split('/')[-1].replace('\r','').replace('\n','')[:200]
            storage_key=uuid.uuid4().hex
            storage_key=storage.put(ATTACHMENT_ROOT,storage_key,data,mime,namespace='purchasing')
            try:
                attachment=m.POAttachment(po_id=p.id,kind=kind,filename=original,storage_key=storage_key,
                    mime_type=mime,size_bytes=len(data),uploaded_by=user['id'])
                db.add(attachment);db.flush();m.log(db,'UPLOAD_RECEIPT','purchase_order',p.id,'attachment='+attachment.id)
                result=attachment_info(attachment);db.commit();return result
            except Exception:
                storage.remove(ATTACHMENT_ROOT,storage_key)
                raise

    @app.get('/api/purchasing/attachments/{attachment_id}/download')
    def download_po_attachment(attachment_id:str,user=Depends(m.current_user)):
        with Session(m.engine) as db:
            a=db.get(m.POAttachment,attachment_id)
            if not a:raise HTTPException(404,'Attachment not found')
            p=db.get(m.PurchaseOrder,a.po_id)
            if user['role'] not in ('owner','office','bookkeeper','estimator') and (
                    not user.get('po_prefix') or p.po_number[:2]!=user.get('po_prefix')):
                raise HTTPException(403,'Receipt access denied')
            return storage.response(ATTACHMENT_ROOT,a.storage_key,a.mime_type,a.filename)

    @app.get('/purchasing')
    def purchasing_page():return m.FileResponse(m.ROOT/'purchasing.html')
    @app.get('/purchasing.js')
    def purchasing_script():return m.FileResponse(m.ROOT/'purchasing.js',media_type='application/javascript')


