"""Audited requests for missed clock-out / time corrections, reviewed by the owner."""
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import String, Text, ForeignKey, DateTime, select
from sqlalchemy.orm import Mapped, mapped_column, Session
from . import main as m, call_pricing as pricing
from .time_clock import as_utc, clock_now

class TimeCorrection(m.Base):
    __tablename__='time_correction_requests'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
    technician_id:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    entry_id:Mapped[str|None]=mapped_column(ForeignKey('field_time_entries.id',ondelete='RESTRICT'),nullable=True)
    clock_id:Mapped[str|None]=mapped_column(String(36),nullable=True)
    started_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
    ended_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
    reason:Mapped[str]=mapped_column(Text,nullable=False)
    original_json:Mapped[str]=mapped_column(Text,nullable=False)
    status:Mapped[str]=mapped_column(String(15),nullable=False,default='Pending')
    review_note:Mapped[str]=mapped_column(Text,nullable=False,default='')
    requested_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    reviewed_by:Mapped[str|None]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=pricing.now)
    reviewed_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)

class CorrectionIn(BaseModel):
    entry_id:str|None=None
    clock_id:str|None=None
    started_at:datetime
    ended_at:datetime
    reason:str=Field(min_length=5,max_length=2000)
    @model_validator(mode='after')
    def valid(self):
        if bool(self.entry_id)==bool(self.clock_id):raise ValueError('Choose a time entry or active clock')
        if any(not t.tzinfo or t.utcoffset() is None for t in (self.started_at,self.ended_at)):raise ValueError('Times require a timezone offset')
        hours=(self.ended_at-self.started_at).total_seconds()/3600
        if hours<=0 or hours>24:raise ValueError('Corrected shift must be greater than zero and at most 24 hours')
        if not self.reason.strip():raise ValueError('Enter a reason')
        return self

class ReviewIn(BaseModel):
    decision:Literal['approve','reject']
    note:str=Field(min_length=3,max_length=2000)


def pending(db,wo_id=None,entry_id=None):
    query=select(TimeCorrection).where(TimeCorrection.status=='Pending')
    if wo_id:query=query.where(TimeCorrection.work_order_id==wo_id)
    if entry_id:query=query.where(TimeCorrection.entry_id==entry_id)
    return list(db.scalars(query))


def invoiced(db,wo_id):
    link=db.get(pricing.CallPricingExport,(wo_id,'invoice'))
    return bool(link and db.get(m.InvoiceRecord,link.record_id).status!='Draft')


def original_entry(db,entry):
    approval=db.scalar(select(m.TimeApproval).where(m.TimeApproval.time_entry_id==entry.id))
    return {'started_at':as_utc(entry.started_at).isoformat(),'ended_at':as_utc(entry.ended_at).isoformat(),
            'hours':str(entry.hours),'mileage':str(entry.mileage),'note':entry.note,
            'approval':{'id':approval.id,'labor_cost':str(approval.labor_cost),'mileage_cost':str(approval.mileage_cost),'approved_by':approval.approved_by,'regular_hours':str(approval.regular_hours),'overtime_hours':str(approval.overtime_hours),'approved_at':as_utc(approval.approved_at).isoformat()} if approval else None}


def serialize(db,row,include_costs=False):
    original=json.loads(row.original_json)
    if not include_costs and 'approval' in original:original['approval']={'previously_approved':bool(original['approval'])}
    return {'id':row.id,'work_order_id':row.work_order_id,'technician':db.get(m.User,row.technician_id).display_name,
            'entry_id':row.entry_id,'clock_id':row.clock_id,'started_at':as_utc(row.started_at).isoformat(),
            'ended_at':as_utc(row.ended_at).isoformat(),'reason':row.reason,'status':row.status,
            'review_note':row.review_note,'original':original,'created_at':as_utc(row.created_at).isoformat()}


def attach_time_corrections(app):
    m.Base.metadata.create_all(m.engine)
    @app.get('/api/time/corrections')
    def list_corrections(user=Depends(m.current_user)):
        if user['role'] not in ('owner','technician'):raise HTTPException(403,'Technician or admin access required')
        with Session(m.engine) as db:
            query=select(TimeCorrection).order_by(TimeCorrection.created_at.desc())
            if user['role']!='owner':query=query.where(TimeCorrection.technician_id==user['id'])
            return [serialize(db,row,include_costs=user['role']=='owner') for row in db.scalars(query)]
    @app.post('/api/time/corrections',status_code=201)
    def request(data:CorrectionIn,user=Depends(m.current_user)):
        if user['role'] not in ('owner','technician'):raise HTTPException(403,'Technician or admin access required')
        with Session(m.engine) as db:
            if data.entry_id:
                target=db.get(m.FieldTimeEntry,data.entry_id)
                if not target:raise HTTPException(404,'Time entry not found')
                technician_id=target.technician_id
            else:
                target=db.scalar(select(m.ActiveTimeClock).where(m.ActiveTimeClock.id==data.clock_id))
                if not target:raise HTTPException(404,'Active clock not found')
                technician_id=target.technician_id
            if user['role']!='owner' and technician_id!=user['id']:raise HTTPException(403,'You may request corrections only to your own time')
            m.lock_time_user(db,technician_id)
            if invoiced(db,target.work_order_id):raise HTTPException(409,'Time is on an issued invoice; ask the office to review a billing adjustment')
            if db.scalar(select(TimeCorrection.id).where(TimeCorrection.status=='Pending',TimeCorrection.technician_id==technician_id,TimeCorrection.entry_id==data.entry_id,TimeCorrection.clock_id==data.clock_id)):
                raise HTTPException(409,'A correction is already awaiting review for this time')
            if data.ended_at.astimezone(timezone.utc)>clock_now():raise HTTPException(422,'Corrected clock-out cannot be in the future')
            original=original_entry(db,target) if data.entry_id else {'started_at':as_utc(target.started_at).isoformat(),'clock_id':target.id}
            row=TimeCorrection(work_order_id=target.work_order_id,technician_id=technician_id,entry_id=data.entry_id,clock_id=data.clock_id,
                started_at=data.started_at.astimezone(timezone.utc),ended_at=data.ended_at.astimezone(timezone.utc),reason=data.reason.strip(),original_json=pricing.dump(original),requested_by=user['id'])
            db.add(row);db.flush();m.log(db,'REQUEST_TIME_CORRECTION','time_correction',row.id,row.reason);db.commit();return serialize(db,row)
    @app.post('/api/time/corrections/{request_id}/review')
    def review(request_id:str,data:ReviewIn,user=Depends(pricing.owner)):
        with Session(m.engine) as db:
            row=db.get(TimeCorrection,request_id)
            if not row:raise HTTPException(404,'Correction request not found')
            m.lock_time_user(db,row.technician_id)
            db.refresh(row)
            if row.status!='Pending':raise HTTPException(409,'Correction has already been reviewed')
            if data.decision=='approve':
                if invoiced(db,row.work_order_id):raise HTTPException(409,'Time is on an issued invoice; review a billing adjustment')
                start=as_utc(row.started_at);end=as_utc(row.ended_at)
                overlap=db.scalar(select(m.FieldTimeEntry.id).where(m.FieldTimeEntry.technician_id==row.technician_id,
                    m.FieldTimeEntry.id!=row.entry_id if row.entry_id else True,m.FieldTimeEntry.started_at<end,m.FieldTimeEntry.ended_at>start))
                if overlap:raise HTTPException(409,'Corrected time overlaps another entry')
                active=db.get(m.ActiveTimeClock,row.technician_id)
                if active and active.id!=row.clock_id and end>as_utc(active.started_at):raise HTTPException(409,'Corrected time overlaps a running clock')
                hours=(Decimal(str((end-start).total_seconds()))/3600).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                if row.entry_id:
                    entry=db.get(m.FieldTimeEntry,row.entry_id)
                    original=json.loads(row.original_json)
                    if as_utc(entry.started_at).isoformat()!=original['started_at'] or as_utc(entry.ended_at).isoformat()!=original['ended_at']:raise HTTPException(409,'Time changed after the request; submit a new correction')
                    approval=db.scalar(select(m.TimeApproval).where(m.TimeApproval.time_entry_id==entry.id))
                    if approval:db.delete(approval)
                    entry.started_at=start;entry.ended_at=end;entry.hours=hours
                else:
                    if not active or active.id!=row.clock_id:raise HTTPException(409,'This clock has since stopped; request a correction for the saved entry')
                    entry=m.FieldTimeEntry(work_order_id=row.work_order_id,technician_id=row.technician_id,started_at=start,ended_at=end,hours=hours,mileage=0,note='Admin-reviewed clock correction')
                    db.add(entry);db.flush();db.add(m.ClockCompletion(clock_id=active.id,time_entry_id=entry.id));db.delete(active);row.entry_id=entry.id
                row.status='Approved'
            else:row.status='Rejected'
            row.review_note=data.note;row.reviewed_by=user['id'];row.reviewed_at=pricing.now()
            m.log(db,'REVIEW_TIME_CORRECTION','time_correction',row.id,pricing.dump({'decision':row.status,'note':data.note,'original':json.loads(row.original_json)}));db.commit();return serialize(db,row)
