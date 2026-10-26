"""One hourly call clock (including travel) plus independent per-call mileage."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from . import main as m


def clock_now():
    return datetime.now(timezone.utc)


def as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def lock_time_user(db, technician_id):
    # PostgreSQL serializes timer/manual writes per technician across devices.
    db.scalar(select(m.User).where(m.User.id == technician_id).with_for_update())


def ensure_no_time_overlap(db, technician_id, start, end, ignore_clock=False):
    overlap = db.scalar(select(m.FieldTimeEntry.id).where(
        m.FieldTimeEntry.technician_id == technician_id,
        m.FieldTimeEntry.started_at < end, m.FieldTimeEntry.ended_at > start))
    if overlap:
        raise HTTPException(409, 'Time entry overlaps an existing entry')
    active = db.get(m.ActiveTimeClock, technician_id)
    if not ignore_clock and active and end > as_utc(active.started_at):
        raise HTTPException(409, 'Time entry overlaps your running clock; clock out first')


def time_entry_payload(db, entry):
    return {'id': entry.id, 'technician': db.get(m.User, entry.technician_id).display_name,
            'work_order_id': entry.work_order_id, 'technician_id': entry.technician_id,
            'started_at': as_utc(entry.started_at).isoformat(),
            'ended_at': as_utc(entry.ended_at).isoformat(),
            'hours': float(entry.hours), 'mileage': float(entry.mileage), 'note': entry.note,
            'approved': bool(db.scalar(select(m.TimeApproval.id).where(m.TimeApproval.time_entry_id == entry.id))),
            'needs_review': (as_utc(entry.ended_at) - as_utc(entry.started_at)).total_seconds() > 86400}


def approved_call_mileage_cost(db, job_id):
    return Decimal(db.scalar(select(func.coalesce(func.sum(m.CallMileage.approved_cost), 0)).join(
        m.WorkOrder, m.CallMileage.work_order_id == m.WorkOrder.id).where(m.WorkOrder.job_id == job_id)) or 0)


def technician(user=Depends(m.current_user)):
    if user['role'] != 'technician':
        raise HTTPException(403, 'Only technicians can use the time clock')
    return user


def active_payload(db, active):
    if not active:
        return None
    work_order = db.get(m.WorkOrder, active.work_order_id)
    job = db.get(m.Job, work_order.job_id)
    return {'id': active.id, 'work_order_id': active.work_order_id,
            'job_number': job.job_number, 'summary': work_order.summary,
            'started_at': as_utc(active.started_at).isoformat()}


def mileage_payload(db, row):
    return {'work_order_id': row.work_order_id, 'technician_id': row.technician_id,
            'technician': db.get(m.User, row.technician_id).display_name,
            'start_odometer': float(row.start_odometer), 'end_odometer': float(row.end_odometer) if row.end_odometer is not None else None,
            'complete': row.end_odometer is not None, 'miles': float(row.miles), 'revision': row.revision,
            'invoiced': bool(db.get(m.MileageInvoiceLink, (row.work_order_id, row.technician_id))),
            'updated_at': as_utc(row.updated_at).isoformat(), 'approved': row.approved_cost is not None}


class StartClock(BaseModel):
    work_order_id: str


class StopClock(BaseModel):
    clock_id: str
    captured_stop_at: datetime | None = None


class MileageIn(BaseModel):
    start_odometer: Decimal = Field(ge=0, le=99999999, decimal_places=2)
    end_odometer: Decimal | None = Field(default=None, ge=0, le=99999999, decimal_places=2)
    expected_revision: int = Field(ge=0)

    @model_validator(mode='after')
    def odometer_order(self):
        if self.end_odometer is not None and self.end_odometer < self.start_odometer:
            raise ValueError('Ending odometer must be at least the starting odometer')
        return self


class MileageApprovalIn(BaseModel):
    expected_revision: int = Field(ge=1)


class MileageInvoiceIn(BaseModel):
    work_order_id: str
    unit_price: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    tax_rate: Decimal = Field(default=Decimal('0'), ge=0, le=100, decimal_places=3)


def attach_time_clock(app):
    from fastapi.responses import FileResponse

    @app.get('/time-review')
    def review_page():
        return FileResponse(m.ROOT / 'time-review.html')

    @app.get('/time-review.js')
    def review_script():
        return FileResponse(m.ROOT / 'time-review.js', media_type='application/javascript')

    @app.get('/api/time/clock')
    def current_clock(user=Depends(technician)):
        with Session(m.engine) as db:
            return {'active': active_payload(db, db.get(m.ActiveTimeClock, user['id'])),
                    'server_now': clock_now().isoformat()}

    @app.post('/api/time/clock/start', status_code=201)
    def start_clock(data: StartClock, user=Depends(technician)):
        with Session(m.engine) as db:
            lock_time_user(db, user['id'])
            w = m.permitted_wo(db, data.work_order_id, user)
            m.field_report_open(db, w.id)
            if db.get(m.ActiveTimeClock, user['id']):
                raise HTTPException(409, 'A clock is already running; clock out before starting another call')
            now = clock_now()
            if db.scalar(select(m.FieldTimeEntry.id).where(m.FieldTimeEntry.technician_id == user['id'],
                                                          m.FieldTimeEntry.ended_at > now)):
                raise HTTPException(409, 'A recorded entry extends past the current time; review it first')
            active = m.ActiveTimeClock(technician_id=user['id'], work_order_id=w.id, started_at=now)
            db.add(active); db.flush()
            m.log(db, 'CLOCK_IN', 'work_order', w.id)
            payload = active_payload(db, active); db.commit()
            return {'active': payload, 'server_now': now.isoformat()}

    @app.post('/api/time/clock/stop')
    def stop_clock(data: StopClock, user=Depends(technician)):
        with Session(m.engine) as db:
            lock_time_user(db, user['id'])
            saved = db.scalar(select(m.FieldTimeEntry).join(m.ClockCompletion).where(
                m.ClockCompletion.clock_id == data.clock_id,
                m.FieldTimeEntry.technician_id == user['id']))
            if saved:
                return {'entry': time_entry_payload(db, saved),
                        'active': active_payload(db, db.get(m.ActiveTimeClock, user['id'])),
                        'server_now': clock_now().isoformat()}
            active = db.get(m.ActiveTimeClock, user['id'])
            if not active or active.id != data.clock_id:
                raise HTTPException(409, 'Clock changed on another device; refresh before clocking out')
            current=clock_now(); start=as_utc(active.started_at)
            if data.captured_stop_at and (not data.captured_stop_at.tzinfo or as_utc(data.captured_stop_at)>current+timedelta(seconds=30) or as_utc(data.captured_stop_at)<current-timedelta(days=7)):
                raise HTTPException(422,'Offline clock-out must be timezone-aware and within the last seven days')
            now=as_utc(data.captured_stop_at) if data.captured_stop_at else current
            seconds = (now - start).total_seconds()
            if seconds <= 0:
                raise HTTPException(409, 'Clock start must be earlier than clock out')
            ensure_no_time_overlap(db, user['id'], start, now, ignore_clock=True)
            # Stopping an existing clock remains possible if the call was reassigned or signed.
            entry = m.FieldTimeEntry(work_order_id=active.work_order_id, technician_id=user['id'],
                started_at=start, ended_at=now,
                hours=(Decimal(str(seconds)) / 3600).quantize(Decimal('.01'), rounding=ROUND_HALF_UP),
                mileage=0, note='Recorded by call clock; includes travel and on-site time')
            db.add(entry); db.flush()
            db.add(m.ClockCompletion(clock_id=active.id, time_entry_id=entry.id))
            payload = time_entry_payload(db, entry)
            db.delete(active)
            m.log(db, 'CLOCK_OUT', 'work_order', entry.work_order_id, entry.id)
            db.commit()
            return {'entry': payload, 'active': None, 'server_now': now.isoformat()}

    @app.get('/api/work-orders/{wo_id}/mileage')
    def get_mileage(wo_id: str, user=Depends(m.current_user)):
        with Session(m.engine) as db:
            m.permitted_wo(db, wo_id, user)
            query = select(m.CallMileage).where(m.CallMileage.work_order_id == wo_id)
            return [mileage_payload(db, row) for row in db.scalars(query)]

    @app.put('/api/work-orders/{wo_id}/mileage')
    def save_mileage(wo_id: str, data: MileageIn, user=Depends(m.current_user)):
        with Session(m.engine) as db:
            db.scalar(select(m.WorkOrder).where(m.WorkOrder.id==wo_id).with_for_update())
            m.permitted_wo(db, wo_id, user)
            recorder = db.scalar(select(m.CallMileage).where(m.CallMileage.work_order_id==wo_id))
            # One shared trip per call. Preserve its cost-rate attribution when another
            # authorized team member edits it, and record that editor in the audit log.
            mileage_user_id = recorder.technician_id if recorder else user['id']
            lock_time_user(db, mileage_user_id)
            # Avoid counting the same trip both on an old time entry and on the call.
            if db.scalar(select(m.FieldTimeEntry.id).where(m.FieldTimeEntry.work_order_id == wo_id,
                    m.FieldTimeEntry.mileage > 0)):
                raise HTTPException(409, 'Mileage is already recorded on legacy time entries for this call; ask the office to review before adding call mileage')
            if db.get(m.MileageInvoiceLink,(wo_id,mileage_user_id)):
                raise HTTPException(409,'Mileage is linked to an invoice; the office must remove it from the draft before editing')
            row = recorder
            if data.expected_revision != (row.revision if row else 0):
                raise HTTPException(409, 'Mileage changed on another device; reload the call before saving')
            old = str(row.miles) if row else '0'
            miles = data.end_odometer - data.start_odometer if data.end_odometer is not None else Decimal('0')
            if row:
                row.start_odometer = data.start_odometer; row.end_odometer = data.end_odometer
                row.miles = miles; row.revision += 1; row.updated_at = clock_now()
                row.approved_cost = None; row.approved_by = None; row.approved_at = None
            else:
                row = m.CallMileage(work_order_id=wo_id, technician_id=mileage_user_id, start_odometer=data.start_odometer, end_odometer=data.end_odometer, miles=miles,
                                     revision=1, updated_at=clock_now())
                db.add(row)
            db.flush()
            m.log(db, 'SAVE_CALL_MILEAGE', 'work_order', wo_id, f'Edited by {user["id"]}; rate member {mileage_user_id}; {old} -> {miles} miles; odometer {data.start_odometer} -> {data.end_odometer}; revision {row.revision}')
            result = mileage_payload(db, row); db.commit()
            return result

    @app.get('/api/time/entries')
    def review_entries(user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            entries = db.scalars(select(m.FieldTimeEntry).order_by(m.FieldTimeEntry.started_at.desc()))
            return [time_entry_payload(db, entry) for entry in entries]

    @app.get('/api/time/mileage')
    def review_mileage(user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            return [mileage_payload(db, row) for row in db.scalars(select(m.CallMileage))]

    @app.post('/api/work-orders/{wo_id}/mileage/{technician_id}/approve')
    def approve_mileage(wo_id: str, technician_id: str, data: MileageApprovalIn,
                        user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            lock_time_user(db, technician_id)
            row = db.get(m.CallMileage, (wo_id, technician_id))
            if not row:
                raise HTTPException(404, 'Call mileage not found')
            if row.revision != data.expected_revision:
                raise HTTPException(409, 'Mileage changed; reload before approving')
            if row.end_odometer is None:
                raise HTTPException(422, 'Enter the ending odometer before approving mileage')
            if row.approved_cost is not None:
                raise HTTPException(409, 'Mileage already approved')
            rate = db.scalar(select(m.LaborRate).where(m.LaborRate.technician_id == technician_id))
            if not rate:
                raise HTTPException(422, 'Configure the recording team member\'s mileage cost rate first')
            row.approved_cost = (row.miles * rate.mileage_rate).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            row.approved_by = user['id']; row.approved_at = clock_now()
            m.log(db, 'APPROVE_CALL_MILEAGE', 'work_order', wo_id, f'{technician_id}; revision {row.revision}')
            db.commit()
            return {'approved': True, 'mileage_cost': float(row.approved_cost)}

    @app.post('/api/invoices/{invoice_id}/call-mileage')
    def invoice_call_mileage(invoice_id: str, data: MileageInvoiceIn,
                              user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            w = db.scalar(select(m.WorkOrder).where(m.WorkOrder.id == data.work_order_id).with_for_update())
            if not w:
                raise HTTPException(404, 'Call not found')
            inv = db.scalar(select(m.InvoiceRecord).where(m.InvoiceRecord.id == invoice_id).with_for_update())
            if not inv:
                raise HTTPException(404, 'Invoice not found')
            if inv.status != 'Draft':
                raise HTTPException(409, 'Mileage can only be added to a draft invoice')
            if inv.job_id != w.job_id:
                raise HTTPException(422, 'The call and invoice must belong to the same job')
            if inv.proposal_id:
                raise HTTPException(409, 'This invoice has an approved proposal total; use a separate authorized mileage invoice')
            rows = list(db.scalars(select(m.CallMileage).where(m.CallMileage.work_order_id == w.id).order_by(m.CallMileage.technician_id)))
            if not rows:
                raise HTTPException(422, 'No odometer readings recorded for this call')
            for row in rows:
                lock_time_user(db, row.technician_id)
                db.refresh(row)
            if any(row.end_odometer is None or row.approved_cost is None for row in rows):
                raise HTTPException(422, 'Complete and approve all odometer records for this call before invoicing')
            if any(db.get(m.MileageInvoiceLink,(w.id,row.technician_id)) for row in rows):
                raise HTTPException(409, 'This call mileage is already linked to an invoice')
            rows = [row for row in rows if row.miles > 0]
            if not rows:
                raise HTTPException(422, 'Calculated mileage is zero; no invoice charge added')
            existing = m.invoice_lines(db, inv)
            if not existing:
                raise HTTPException(422, 'Create an itemized draft invoice before adding call mileage')
            if len(existing)+len(rows)>150:
                raise HTTPException(422, 'Invoice would exceed 150 lines')
            job = db.get(m.Job,w.job_id)
            position = max(line.position for line in existing)
            for row in rows:
                position += 1
                item = m.InvoiceLineInput(description=f'Call mileage — {job.job_number} / {w.summary[:100]} — {db.get(m.User,row.technician_id).display_name}',
                        quantity=row.miles, unit_price=data.unit_price, tax_rate=data.tax_rate)
                subtotal,tax = m.line_calculation(item)
                line = m.InvoiceLine(invoice_id=inv.id,position=position,description=item.description,
                        quantity=row.miles,unit_price=data.unit_price,tax_rate=data.tax_rate,subtotal=subtotal,tax_amount=tax)
                db.add(line);db.flush()
                db.add(m.MileageInvoiceLink(work_order_id=w.id,technician_id=row.technician_id,invoice_id=inv.id,invoice_line_id=line.id))
                inv.amount += subtotal+tax
            m.log(db,'INVOICE_CALL_MILEAGE','invoice',inv.id,w.id)
            db.commit()
            return m.invoice_data(inv,db)

    @app.delete('/api/invoices/{invoice_id}/call-mileage/{wo_id}')
    def remove_draft_mileage(invoice_id: str, wo_id: str, user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            db.scalar(select(m.WorkOrder).where(m.WorkOrder.id==wo_id).with_for_update())
            inv=db.scalar(select(m.InvoiceRecord).where(m.InvoiceRecord.id==invoice_id).with_for_update())
            if not inv:raise HTTPException(404,'Invoice not found')
            if inv.status!='Draft':raise HTTPException(409,'Issued invoices are immutable; use the billing adjustment workflow')
            links=list(db.scalars(select(m.MileageInvoiceLink).where(m.MileageInvoiceLink.invoice_id==invoice_id,m.MileageInvoiceLink.work_order_id==wo_id)))
            if not links:raise HTTPException(404,'No linked call mileage on this invoice')
            lines=[db.get(m.InvoiceLine,link.invoice_line_id) for link in links]
            for link in links:db.delete(link)
            db.flush()
            for line in lines:
                inv.amount-=line.subtotal+line.tax_amount
                db.delete(line)
            m.log(db,'REMOVE_DRAFT_CALL_MILEAGE','invoice',inv.id,wo_id)
            db.commit()
            return m.invoice_data(inv,db)
