"""Technician-reported parts and explicitly classified customer/internal notes."""
from decimal import Decimal
import hashlib
import uuid
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import String, Text, ForeignKey, DateTime, Numeric, select
from sqlalchemy.orm import Mapped, mapped_column, Session
from . import main as m, call_pricing as pricing
from .productivity import access
from .time_clock import as_utc

class CallPartUsed(m.Base):
    __tablename__='call_parts_used'
    client_request_id:Mapped[str|None]=mapped_column(String(36),unique=True,nullable=True)
    request_fingerprint:Mapped[str|None]=mapped_column(String(64),nullable=True)
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
    part_id:Mapped[str|None]=mapped_column(ForeignKey('catalog_parts.id',ondelete='RESTRICT'),nullable=True)
    description:Mapped[str]=mapped_column(String(500),nullable=False)
    unit:Mapped[str]=mapped_column(String(30),nullable=False)
    quantity:Mapped[Decimal]=mapped_column(Numeric(12,3),nullable=False)
    internal_note:Mapped[str]=mapped_column(Text,nullable=False,default='')
    author_id:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    active:Mapped[bool]=mapped_column(nullable=False,default=True)
    revision:Mapped[int]=mapped_column(nullable=False,default=1)
    created_at:Mapped[m.datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=pricing.now)

class CallNote(m.Base):
    __tablename__='call_notes'
    client_request_id:Mapped[str|None]=mapped_column(String(36),unique=True,nullable=True)
    request_fingerprint:Mapped[str|None]=mapped_column(String(64),nullable=True)
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
    kind:Mapped[str]=mapped_column(String(30),nullable=False)
    audience:Mapped[str]=mapped_column(String(15),nullable=False)
    text:Mapped[str]=mapped_column(Text,nullable=False)
    author_id:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    created_at:Mapped[m.datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=pricing.now)

class UsedPartIn(BaseModel):
    client_request_id:uuid.UUID|None=None
    part_id:str|None=None
    description:str=Field(default='',max_length=500)
    unit:str=Field(default='each',min_length=1,max_length=30)
    quantity:Decimal=Field(gt=0,le=1000000,decimal_places=3)
    internal_note:str=Field(default='',max_length=2000)
    expected_revision:int=Field(default=0,ge=0)
    active:bool=True
    @model_validator(mode='after')
    def named(self):
        if not self.part_id and not self.description.strip():raise ValueError('Describe the unlisted part')
        return self

class CallNoteIn(BaseModel):
    client_request_id:uuid.UUID|None=None
    kind:Literal['service_description','recommendation','follow_up']
    audience:Literal['customer','internal']='internal'
    text:str=Field(min_length=1,max_length=10000)


def parts_payload(db,row):
    return {'id':row.id,'part_id':row.part_id,'sku':db.get(pricing.CatalogPart,row.part_id).sku if row.part_id else '',
            'description':row.description,'unit':row.unit,'quantity':str(row.quantity),'internal_note':row.internal_note,
            'author':db.get(m.User,row.author_id).display_name,'author_id':row.author_id,'active':row.active,'revision':row.revision}


def note_payload(db,row):
    return {'id':row.id,'kind':row.kind,'audience':row.audience,'text':row.text,
            'author':db.get(m.User,row.author_id).display_name,'created_at':as_utc(row.created_at).isoformat()}


def usage_rows(db,wo_id):
    return list(db.scalars(select(CallPartUsed).where(CallPartUsed.work_order_id==wo_id,CallPartUsed.active==True).order_by(CallPartUsed.id)))


def customer_note_payloads(db,wo_id):
    labels={'service_description':'Service performed','recommendation':'Recommendation','follow_up':'Follow-up'}
    notes=[{'author':db.get(m.User,n.author_id).display_name,'note':labels[n.kind]+': '+n.text} for n in db.scalars(select(CallNote).where(CallNote.work_order_id==wo_id,CallNote.audience=='customer').order_by(CallNote.created_at))]
    for row in usage_rows(db,wo_id):
        notes.append({'author':db.get(m.User,row.author_id).display_name,'note':f'Parts used: {row.quantity} {row.unit} — {row.description}'})
    return notes


def parts_editable(db,wo_id):
    linked=db.get(pricing.CallPricingExport,(wo_id,'invoice'))
    if linked and db.get(m.InvoiceRecord,linked.record_id).status!='Draft':raise HTTPException(409,'Issued call parts are locked; ask the office to review a billing adjustment')
    m.field_report_open(db,wo_id)


def attach_call_field_notes(app):
    m.Base.metadata.create_all(m.engine)
    @app.get('/api/field/parts-catalog')
    def safe_catalog(user=Depends(m.current_user)):
        if user['role'] not in ('owner','technician'):raise HTTPException(403,'Field technician or admin access required')
        with Session(m.engine) as db:
            return [{'id':p.id,'sku':p.sku,'description':p.description,'unit':p.unit} for p in db.scalars(select(pricing.CatalogPart).where(pricing.CatalogPart.active==True).order_by(pricing.CatalogPart.sku))]
    @app.get('/api/calls/{wo_id}/parts-used')
    def used_parts(wo_id:str,user=Depends(m.current_user)):
        with Session(m.engine) as db:
            access(db,wo_id,user)
            return [parts_payload(db,row) for row in db.scalars(select(CallPartUsed).where(CallPartUsed.work_order_id==wo_id).order_by(CallPartUsed.created_at))]
    @app.post('/api/calls/{wo_id}/parts-used',status_code=201)
    def add_part(wo_id:str,data:UsedPartIn,user=Depends(m.current_user)):
        if user['role'] not in ('owner','technician'):raise HTTPException(403,'Field technician or admin access required')
        with Session(m.engine) as db:
            pricing.call(db,wo_id,lock=True);access(db,wo_id,user);parts_editable(db,wo_id)
            if data.client_request_id:
                existing=db.scalar(select(CallPartUsed).where(CallPartUsed.client_request_id==str(data.client_request_id)))
                if existing:
                    if existing.work_order_id!=wo_id or existing.author_id!=user['id'] or existing.request_fingerprint!=hashlib.sha256(data.model_dump_json().encode()).hexdigest():raise HTTPException(409,'This part request was already saved with different details; reload the call before adding another line')
                    return parts_payload(db,existing)
            part=db.get(pricing.CatalogPart,data.part_id) if data.part_id else None
            if data.part_id and (not part or not part.active):raise HTTPException(422,'Select an active part or enter an unlisted part')
            row=CallPartUsed(request_fingerprint=hashlib.sha256(data.model_dump_json().encode()).hexdigest(),client_request_id=str(data.client_request_id) if data.client_request_id else None,work_order_id=wo_id,part_id=part.id if part else None,description=part.description if part else data.description.strip(),unit=part.unit if part else data.unit.strip(),quantity=data.quantity,internal_note=data.internal_note,author_id=user['id'])
            db.add(row);db.flush();m.log(db,'ADD_CALL_PART','call_part_used',row.id,str(row.quantity));db.commit();return parts_payload(db,row)
    @app.put('/api/calls/{wo_id}/parts-used/{usage_id}')
    def edit_part(wo_id:str,usage_id:str,data:UsedPartIn,user=Depends(m.current_user)):
        with Session(m.engine) as db:
            pricing.call(db,wo_id,lock=True);access(db,wo_id,user);parts_editable(db,wo_id)
            row=db.get(CallPartUsed,usage_id)
            if not row or row.work_order_id!=wo_id:raise HTTPException(404,'Reported part not found')
            if user['role']!='owner' and row.author_id!=user['id']:raise HTTPException(403,'Only the author or admin may correct a reported part')
            if row.revision!=data.expected_revision:raise HTTPException(409,'Part usage changed; reload before editing')
            if data.part_id!=row.part_id:raise HTTPException(422,'Add a new line to change the selected catalog part')
            before=parts_payload(db,row);row.quantity=data.quantity;row.internal_note=data.internal_note;row.active=data.active;row.revision+=1
            if not row.active:
                from .operations_v110 import ReceiptAllocation
                for allocation in db.scalars(select(ReceiptAllocation).where(ReceiptAllocation.usage_id==row.id)):
                    allocation.amount=Decimal('0');allocation.revision+=1
            if not row.part_id:row.description=data.description.strip();row.unit=data.unit.strip()
            m.log(db,'EDIT_CALL_PART','call_part_used',row.id,pricing.dump({'before':before,'after':parts_payload(db,row)}));db.commit();return parts_payload(db,row)
    @app.get('/api/calls/{wo_id}/notes')
    def notes(wo_id:str,user=Depends(m.current_user)):
        with Session(m.engine) as db:
            access(db,wo_id,user)
            return [note_payload(db,row) for row in db.scalars(select(CallNote).where(CallNote.work_order_id==wo_id).order_by(CallNote.created_at.desc()))]
    @app.post('/api/calls/{wo_id}/notes',status_code=201)
    def add_note(wo_id:str,data:CallNoteIn,user=Depends(m.current_user)):
        if user['role'] not in ('owner','technician'):raise HTTPException(403,'Field technician or admin access required')
        if not data.text.strip():raise HTTPException(422,'Enter note text')
        with Session(m.engine) as db:
            pricing.call(db,wo_id,lock=True);access(db,wo_id,user)
            if data.client_request_id:
                existing=db.scalar(select(CallNote).where(CallNote.client_request_id==str(data.client_request_id)))
                if existing:
                    if existing.work_order_id!=wo_id or existing.author_id!=user['id'] or existing.request_fingerprint!=hashlib.sha256(data.model_dump_json().encode()).hexdigest():raise HTTPException(409,'This note request was already saved with different text; reload before submitting a new note')
                    return note_payload(db,existing)
            if data.audience=='customer':m.field_report_open(db,wo_id)
            row=CallNote(request_fingerprint=hashlib.sha256(data.model_dump_json().encode()).hexdigest(),client_request_id=str(data.client_request_id) if data.client_request_id else None,work_order_id=wo_id,kind=data.kind,audience=data.audience,text=data.text.strip(),author_id=user['id'])
            db.add(row);db.flush();m.log(db,'ADD_CLASSIFIED_CALL_NOTE','call_note',row.id,data.audience+'; '+data.kind);db.commit();return note_payload(db,row)
    @app.get('/api/calls/{wo_id}/customer-summary')
    def customer_summary(wo_id:str,user=Depends(m.current_user)):
        with Session(m.engine) as db:
            access(db,wo_id,user)
            return {'notes':[{'author':db.get(m.User,n.technician_id).display_name,'note':n.note} for n in db.scalars(select(m.ServiceNote).where(m.ServiceNote.work_order_id==wo_id))]+customer_note_payloads(db,wo_id)}
    @app.get('/api/admin/calls/{wo_id}/reported-parts')
    def priced_parts(wo_id:str,user=Depends(pricing.owner)):
        with Session(m.engine) as db:
            pricing.call(db,wo_id);result=[]
            for row in usage_rows(db,wo_id):
                part=db.get(pricing.CatalogPart,row.part_id) if row.part_id else None
                result.append({'usage_id':row.id,'part_id':row.part_id,'catalog_revision':part.revision if part else None,
                               'description':row.description,'quantity':str(row.quantity),'unit_cost':str(part.unit_cost) if part else '',
                               'unit_price':str(part.unit_price) if part else '', 'active':part.active if part else True})
            return result
