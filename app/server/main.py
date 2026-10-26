"""Limitless Mechanical CRM local development API.
Run: uvicorn server.main:app --reload from the limitless-crm directory.
Not production-authenticated. Do not expose to the public Internet.
"""
from __future__ import annotations
import os, re, uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Request, Response, Cookie
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import create_engine, ForeignKey, String, Numeric, Date, DateTime, Text, CheckConstraint, select, func, UniqueConstraint, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, Session

from .deploy_config import validate_settings
RUNTIME = validate_settings()
ROOT = Path(__file__).resolve().parent.parent
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///' + str(ROOT / 'limitless-dev.sqlite3'))
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread':False} if DATABASE_URL.startswith('sqlite') else {}, future=True)
if DATABASE_URL.startswith('sqlite'):
    @event.listens_for(engine, 'connect')
    def enforce_sqlite_foreign_keys(connection, _):
        cursor=connection.cursor();cursor.execute('PRAGMA foreign_keys=ON');cursor.close()


class Base(DeclarativeBase): pass
class Customer(Base):
    __tablename__='customers'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    name: Mapped[str]=mapped_column(String(250),nullable=False,index=True)
    billing_address: Mapped[str]=mapped_column(Text,default='')
    contact: Mapped[str]=mapped_column(String(250),default='')
    phone: Mapped[str]=mapped_column(String(60),default='')
    email: Mapped[str]=mapped_column(String(250),default='')
    source_key: Mapped[Optional[str]]=mapped_column(String(200),unique=True,nullable=True)
class Location(Base):
    __tablename__='locations'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    customer_id: Mapped[str]=mapped_column(ForeignKey('customers.id',ondelete='RESTRICT'),nullable=False,index=True)
    name: Mapped[str]=mapped_column(String(250),nullable=False)
    address: Mapped[str]=mapped_column(Text,default='')
    source_key: Mapped[Optional[str]]=mapped_column(String(200),unique=True,nullable=True)
class Job(Base):
    __tablename__='jobs'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    job_number: Mapped[str]=mapped_column(String(40),unique=True,nullable=False,index=True)
    customer_id: Mapped[str]=mapped_column(ForeignKey('customers.id',ondelete='RESTRICT'),nullable=False,index=True)
    location_id: Mapped[Optional[str]]=mapped_column(ForeignKey('locations.id',ondelete='RESTRICT'),nullable=True)
    description: Mapped[str]=mapped_column(Text,default='')
    status: Mapped[str]=mapped_column(String(60),default='Open')
    contract_amount: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    historical_labor_cost: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    historical_material_cost: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    historical_mileage_cost: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    source: Mapped[str]=mapped_column(String(40),default='manual')
class PurchaseOrder(Base):
    __tablename__='purchase_orders'
    __table_args__=(CheckConstraint('amount >= 0',name='ck_po_nonnegative'),CheckConstraint('received_value >= 0',name='ck_po_received_nonnegative'),CheckConstraint('received_value <= amount',name='ck_po_received_lte_amount'))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    po_number: Mapped[str]=mapped_column(String(60),unique=True,nullable=False,index=True)
    job_id: Mapped[str]=mapped_column(ForeignKey('jobs.id',ondelete='RESTRICT'),nullable=False,index=True)
    vendor: Mapped[str]=mapped_column(String(250),nullable=False)
    description: Mapped[str]=mapped_column(Text,default='')
    issue_date: Mapped[Optional[date]]=mapped_column(Date,nullable=True)
    amount: Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    received_value: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0,nullable=False)
    status: Mapped[str]=mapped_column(String(50),default='Draft')
    source: Mapped[str]=mapped_column(String(40),default='manual')
class PONumberCounter(Base):
    __tablename__='po_number_counters'
    prefix: Mapped[str]=mapped_column(String(3), primary_key=True)
    last_number: Mapped[int]=mapped_column(nullable=False,default=0)

class POAttachment(Base):
    __tablename__='po_attachments'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    po_id: Mapped[str]=mapped_column(ForeignKey('purchase_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
    kind: Mapped[str]=mapped_column(String(30),nullable=False)
    filename: Mapped[str]=mapped_column(String(255),nullable=False)
    storage_key: Mapped[str]=mapped_column(String(80),nullable=False,unique=True)
    mime_type: Mapped[str]=mapped_column(String(80),nullable=False)
    size_bytes: Mapped[int]=mapped_column(nullable=False)
    uploaded_by: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    uploaded_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class LaborEntry(Base):
    __tablename__='labor_entries'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    job_id: Mapped[str]=mapped_column(ForeignKey('jobs.id',ondelete='RESTRICT'),nullable=False,index=True)
    employee: Mapped[str]=mapped_column(String(120),nullable=False)
    work_date: Mapped[Optional[date]]=mapped_column(Date,nullable=True)
    hours: Mapped[Decimal]=mapped_column(Numeric(8,2),nullable=False)
    overtime_hours: Mapped[Decimal]=mapped_column(Numeric(8,2),default=0)
    mileage: Mapped[Decimal]=mapped_column(Numeric(10,2),default=0)
    source_key: Mapped[Optional[str]]=mapped_column(String(250),unique=True,nullable=True)
class WorkOrder(Base):
    __tablename__='work_orders'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    job_id: Mapped[str]=mapped_column(ForeignKey('jobs.id',ondelete='RESTRICT'),nullable=False,index=True)
    summary: Mapped[str]=mapped_column(Text,nullable=False)
    priority: Mapped[str]=mapped_column(String(20),default='Normal')
    status: Mapped[str]=mapped_column(String(30),default='Open')
    findings: Mapped[str]=mapped_column(Text,default='')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class DispatchAssignment(Base):
    __tablename__='dispatch_assignments'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    work_order_id: Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
    technician_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False,index=True)
    scheduled_start: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
    scheduled_end: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
    status: Mapped[str]=mapped_column(String(30),default='Scheduled')
class ServiceNote(Base):
    __tablename__='service_notes'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    work_order_id: Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
    technician_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False,index=True)
    note: Mapped[str]=mapped_column(Text,nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class Equipment(Base):
    __tablename__='equipment'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    location_id: Mapped[str]=mapped_column(ForeignKey('locations.id',ondelete='RESTRICT'),nullable=False,index=True)
    asset_tag: Mapped[str]=mapped_column(String(100),nullable=False)
    equipment_type: Mapped[str]=mapped_column(String(120),default='')
    manufacturer: Mapped[str]=mapped_column(String(120),default='')
    model: Mapped[str]=mapped_column(String(120),default='')
    serial: Mapped[str]=mapped_column(String(120),default='')
    refrigerant: Mapped[str]=mapped_column(String(60),default='')
    notes: Mapped[str]=mapped_column(Text,default='')
    __table_args__=(__import__('sqlalchemy').UniqueConstraint('location_id','asset_tag',name='uq_asset_tag_per_location'),)
class WorkOrderEquipment(Base):
    __tablename__='work_order_equipment'
    work_order_id: Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),primary_key=True)
    equipment_id: Mapped[str]=mapped_column(ForeignKey('equipment.id',ondelete='RESTRICT'),primary_key=True)
class FieldTimeEntry(Base):
    __tablename__='field_time_entries'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    work_order_id: Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
    technician_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False,index=True)
    started_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
    ended_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
    hours: Mapped[Decimal]=mapped_column(Numeric(8,2),nullable=False)
    mileage: Mapped[Decimal]=mapped_column(Numeric(10,2),default=0)
    note: Mapped[str]=mapped_column(Text,default='')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class ClockCompletion(Base):
    __tablename__='clock_completions'
    clock_id: Mapped[str]=mapped_column(String(36),primary_key=True)
    time_entry_id: Mapped[str]=mapped_column(ForeignKey('field_time_entries.id',ondelete='RESTRICT'),nullable=False,unique=True)

class ActiveTimeClock(Base):
    __tablename__='active_time_clocks'
    technician_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),primary_key=True)
    id: Mapped[str]=mapped_column(String(36),nullable=False,unique=True,default=lambda:str(uuid.uuid4()))
    work_order_id: Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
    started_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)

class CallMileage(Base):
    __tablename__='call_mileage'
    work_order_id: Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),primary_key=True)
    technician_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),primary_key=True)
    start_odometer: Mapped[Decimal]=mapped_column(Numeric(10,2),nullable=False)
    end_odometer: Mapped[Optional[Decimal]]=mapped_column(Numeric(10,2),nullable=True)
    miles: Mapped[Decimal]=mapped_column(Numeric(10,2),nullable=False)
    revision: Mapped[int]=mapped_column(nullable=False,default=1)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
    approved_cost: Mapped[Optional[Decimal]]=mapped_column(Numeric(14,2),nullable=True)
    approved_by: Mapped[Optional[str]]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=True)
    approved_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True),nullable=True)
    __table_args__=(CheckConstraint('miles >= 0',name='ck_call_mileage_nonnegative'),UniqueConstraint('work_order_id',name='uq_mileage_one_per_call'),CheckConstraint('start_odometer >= 0 AND (end_odometer IS NULL OR end_odometer >= start_odometer)',name='ck_odometer_range'),)

class MileageInvoiceLink(Base):
    __tablename__='mileage_invoice_links'
    work_order_id: Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),primary_key=True)
    technician_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),primary_key=True)
    invoice_id: Mapped[str]=mapped_column(ForeignKey('crm_invoices.id',ondelete='RESTRICT'),nullable=False,index=True)
    invoice_line_id: Mapped[str]=mapped_column(ForeignKey('crm_invoice_lines.id',ondelete='RESTRICT'),nullable=False,unique=True)

class LaborRate(Base):
    __tablename__='labor_rates'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    technician_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False,unique=True)
    hourly_cost: Mapped[Decimal]=mapped_column(Numeric(12,2),nullable=False)
    overtime_multiplier: Mapped[Decimal]=mapped_column(Numeric(5,2),nullable=False,default=Decimal('1.5'))
    mileage_rate: Mapped[Decimal]=mapped_column(Numeric(8,3),nullable=False,default=0)
class TimeApproval(Base):
    __tablename__='time_approvals'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    time_entry_id: Mapped[str]=mapped_column(ForeignKey('field_time_entries.id',ondelete='RESTRICT'),nullable=False,unique=True)
    approved_by: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    approved_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=lambda:datetime.now(timezone.utc))
    regular_hours: Mapped[Decimal]=mapped_column(Numeric(8,2),nullable=False)
    overtime_hours: Mapped[Decimal]=mapped_column(Numeric(8,2),nullable=False)
    labor_cost: Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    mileage_cost: Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
class PMPlan(Base):
    __tablename__='pm_plans'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    equipment_id: Mapped[str]=mapped_column(ForeignKey('equipment.id',ondelete='RESTRICT'),nullable=False)
    interval_days: Mapped[int]=mapped_column(nullable=False)
    next_due: Mapped[date]=mapped_column(Date,nullable=False)
    checklist: Mapped[str]=mapped_column(Text,nullable=False)
    active: Mapped[bool]=mapped_column(default=True,nullable=False)
class PMVisit(Base):
    __tablename__='pm_visits'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    plan_id: Mapped[str]=mapped_column(ForeignKey('pm_plans.id',ondelete='RESTRICT'),nullable=False)
    work_order_id: Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=False,unique=True)
    scheduled_due: Mapped[date]=mapped_column(Date,nullable=False)
    __table_args__=(UniqueConstraint('plan_id','scheduled_due',name='uq_pm_plan_due'),)
class PMInspection(Base):
    __tablename__='pm_inspections'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    visit_id: Mapped[str]=mapped_column(ForeignKey('pm_visits.id',ondelete='RESTRICT'),nullable=False)
    technician_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    responses_json: Mapped[str]=mapped_column(Text,nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=lambda:datetime.now(timezone.utc))


class Proposal(Base):
    __tablename__='proposals'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    job_id: Mapped[str]=mapped_column(ForeignKey('jobs.id',ondelete='RESTRICT'),nullable=False,index=True)
    number: Mapped[str]=mapped_column(String(70),unique=True,nullable=False)
    title: Mapped[str]=mapped_column(String(250),nullable=False)
    scope: Mapped[str]=mapped_column(Text,default='')
    status: Mapped[str]=mapped_column(String(30),default='Draft')
    total: Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class InvoiceRecord(Base):
    __tablename__='crm_invoices'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    job_id: Mapped[str]=mapped_column(ForeignKey('jobs.id',ondelete='RESTRICT'),nullable=False,index=True)
    proposal_id: Mapped[Optional[str]]=mapped_column(ForeignKey('proposals.id',ondelete='RESTRICT'),nullable=True)
    number: Mapped[str]=mapped_column(String(70),unique=True,nullable=False)
    status: Mapped[str]=mapped_column(String(30),default='Draft')
    amount: Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    issued_on: Mapped[Optional[date]]=mapped_column(Date,nullable=True)
    due_on: Mapped[Optional[date]]=mapped_column(Date,nullable=True)
class InvoicePayment(Base):
    __tablename__='crm_invoice_payments'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    invoice_id: Mapped[str]=mapped_column(ForeignKey('crm_invoices.id',ondelete='RESTRICT'),nullable=False,index=True)
    amount: Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    paid_on: Mapped[date]=mapped_column(Date,nullable=False)
    reference: Mapped[str]=mapped_column(String(150),nullable=False,unique=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

# v1.2 immutable financial adjustment records. Existing invoice/payment rows are never rewritten by the migration.
class InvoiceLine(Base):
    __tablename__='crm_invoice_lines'
    __table_args__=(UniqueConstraint('invoice_id','position',name='uq_invoice_line_position'),)
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    invoice_id:Mapped[str]=mapped_column(ForeignKey('crm_invoices.id',ondelete='RESTRICT'),nullable=False,index=True)
    position:Mapped[int]=mapped_column(nullable=False)
    description:Mapped[str]=mapped_column(Text,nullable=False)
    quantity:Mapped[Decimal]=mapped_column(Numeric(12,3),nullable=False)
    unit_price:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    tax_rate:Mapped[Decimal]=mapped_column(Numeric(7,3),nullable=False,default=0)
    subtotal:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    tax_amount:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)

class InvoiceCredit(Base):
    __tablename__='crm_invoice_credits'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    invoice_id:Mapped[str]=mapped_column(ForeignKey('crm_invoices.id',ondelete='RESTRICT'),nullable=False,index=True)
    number:Mapped[str]=mapped_column(String(70),nullable=False,unique=True)
    amount:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    reason:Mapped[str]=mapped_column(Text,nullable=False)
    credited_on:Mapped[date]=mapped_column(Date,nullable=False)
    created_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)

class PaymentReversal(Base):
    __tablename__='crm_payment_reversals'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    payment_id:Mapped[str]=mapped_column(ForeignKey('crm_invoice_payments.id',ondelete='RESTRICT'),nullable=False,index=True)
    reference:Mapped[str]=mapped_column(String(150),nullable=False,unique=True)
    amount:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    reason:Mapped[str]=mapped_column(Text,nullable=False)
    reversed_on:Mapped[date]=mapped_column(Date,nullable=False)
    created_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)

class InvoiceRefund(Base):
    __tablename__='crm_invoice_refunds'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    invoice_id:Mapped[str]=mapped_column(ForeignKey('crm_invoices.id',ondelete='RESTRICT'),nullable=False,index=True)
    reference:Mapped[str]=mapped_column(String(150),nullable=False,unique=True)
    amount:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    reason:Mapped[str]=mapped_column(Text,nullable=False)
    refunded_on:Mapped[date]=mapped_column(Date,nullable=False)
    created_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)


# v1.3 - append-only purchasing history. Each record inherits exactly one job through its PO.
class POApproval(Base):
    __tablename__='po_approvals'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    po_id:Mapped[str]=mapped_column(ForeignKey('purchase_orders.id',ondelete='RESTRICT'),nullable=False,unique=True)
    approved_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    approved_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class POReceipt(Base):
    __tablename__='po_receipts'
    __table_args__=(UniqueConstraint('po_id','reference',name='uq_po_receipt_ref'),CheckConstraint('amount > 0',name='ck_po_receipt_positive'))
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    po_id:Mapped[str]=mapped_column(ForeignKey('purchase_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
    reference:Mapped[str]=mapped_column(String(100),nullable=False)
    amount:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    received_on:Mapped[date]=mapped_column(Date,nullable=False)
    note:Mapped[str]=mapped_column(Text,default='')
    recorded_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)

class POVendorInvoice(Base):
    __tablename__='po_vendor_invoices'
    __table_args__=(UniqueConstraint('vendor_key','reference',name='uq_vendor_invoice_ref'),CheckConstraint('amount > 0',name='ck_vendor_bill_positive'),CheckConstraint('matched_receipt_value > 0',name='ck_vendor_bill_match_positive'))
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    po_id:Mapped[str]=mapped_column(ForeignKey('purchase_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
    vendor_key:Mapped[str]=mapped_column(String(250),nullable=False)
    reference:Mapped[str]=mapped_column(String(100),nullable=False)
    amount:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    matched_receipt_value:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    invoice_date:Mapped[date]=mapped_column(Date,nullable=False)
    variance_reason:Mapped[str]=mapped_column(Text,default='')
    recorded_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)

class POReturn(Base):
    __tablename__='po_returns'
    __table_args__=(UniqueConstraint('po_id','reference',name='uq_po_return_ref'),CheckConstraint('amount > 0',name='ck_po_return_positive'))
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    po_id:Mapped[str]=mapped_column(ForeignKey('purchase_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
    reference:Mapped[str]=mapped_column(String(100),nullable=False)
    amount:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    returned_on:Mapped[date]=mapped_column(Date,nullable=False)
    reason:Mapped[str]=mapped_column(Text,nullable=False)
    recorded_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)

class POVendorCredit(Base):
    __tablename__='po_vendor_credits'
    __table_args__=(UniqueConstraint('vendor_key','reference',name='uq_vendor_credit_ref'),CheckConstraint('amount > 0',name='ck_vendor_credit_positive'))
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    po_id:Mapped[str]=mapped_column(ForeignKey('purchase_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
    vendor_key:Mapped[str]=mapped_column(String(250),nullable=False)
    reference:Mapped[str]=mapped_column(String(100),nullable=False)
    amount:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    credited_on:Mapped[date]=mapped_column(Date,nullable=False)
    reason:Mapped[str]=mapped_column(Text,nullable=False)
    recorded_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)

class AuditEvent(Base):
    __tablename__='audit_events'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    event: Mapped[str]=mapped_column(String(100),nullable=False)
    entity: Mapped[str]=mapped_column(String(60),nullable=False)
    entity_id: Mapped[str]=mapped_column(String(60),nullable=False)
    detail: Mapped[str]=mapped_column(Text,default='')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

from .security import User, UserSession, ROLE_SCOPES, verify_password, make_token, token_hash, expiry
Base.metadata.create_all(engine)
# Local development schema upgrade for pre-v1.3 databases. Production uses versioned migrations.
with engine.begin() as connection:
    if engine.dialect.name=='sqlite':
        columns={r[1] for r in connection.exec_driver_sql('PRAGMA table_info(users)').all()}
        if 'po_prefix' not in columns:
            connection.exec_driver_sql('ALTER TABLE users ADD COLUMN po_prefix VARCHAR(3)')
        connection.exec_driver_sql('CREATE UNIQUE INDEX IF NOT EXISTS uq_users_po_prefix ON users(po_prefix) WHERE po_prefix IS NOT NULL')
    elif engine.dialect.name=='postgresql':
        connection.exec_driver_sql('ALTER TABLE users ADD COLUMN IF NOT EXISTS po_prefix VARCHAR(3)')
        connection.exec_driver_sql('CREATE UNIQUE INDEX IF NOT EXISTS uq_users_po_prefix ON users(po_prefix) WHERE po_prefix IS NOT NULL')

app=FastAPI(title='Limitless Mechanical CRM',version='1.15')

if RUNTIME['mode'] in ('staging','production'):
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=RUNTIME['hosts'])

@app.middleware('http')
async def hosted_request_guard(request: Request, call_next):
    # Same-origin browser writes only; does not replace OAuth, MFA, or rate limiting.
    if RUNTIME['mode'] in ('staging', 'production'):
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            supplied = request.headers.get('origin', '')
            if supplied.rstrip('/') != RUNTIME['public_url'].rstrip('/'):
                return JSONResponse({'detail':'Cross-origin or missing Origin forbidden'}, status_code=403)
        # Initial Cloud Run deployment has ephemeral storage. Prevent lost photo uploads.
        if request.method in ('POST','PUT') and ('/attachments' in request.url.path or request.url.path.endswith('/photos') or request.url.path.endswith('/signature') or request.url.path.endswith('/documents')):
            from .private_storage import durable_configured
            if not durable_configured():
                return JSONResponse({'detail':'Photo/receipt uploads unavailable until durable private storage is configured'}, status_code=503)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    if request.url.path.startswith('/api/'):
        response.headers.setdefault('Cache-Control','no-store')
    if RUNTIME['mode'] in ('staging','production'):
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


class CustomerIn(BaseModel):
    name:str=Field(min_length=1,max_length=250)
    billing_address:str=''
    contact:str=''
    phone:str=''
    email:str=''
class JobIn(BaseModel):
    job_number:str=Field(min_length=3,max_length=40)
    customer_id:str
    location_id:Optional[str]=None
    description:str=''
    location_name:str=''
    address:str=''
    status:str='Open'
    contract_amount:Decimal=Field(default=Decimal('0'),ge=0)
    @field_validator('job_number')
    @classmethod
    def valid_job_number(cls,v):
        if not re.fullmatch(r'(LMS\d+|DTM|OVHD)',v): raise ValueError('Use an LMS job number or an approved internal job code')
        return v
class POIn(BaseModel):
    po_number:str=Field(min_length=2,max_length=60)
    job_id:str
    vendor:str=Field(min_length=1,max_length=250)
    description:str=''
    amount:Decimal=Field(ge=0)
    received_value:Decimal=Field(default=Decimal('0'),ge=0)
    status:str='Draft'
    issue_date:Optional[date]=None
    @field_validator('status')
    @classmethod
    def valid_status(cls,v):
        if v not in ('Draft','Issued','Partially Received','Received','Historical','Cancelled'):raise ValueError('Invalid PO status')
        return v

def log(db,event,entity,entity_id,detail=''):
    db.add(AuditEvent(event=event,entity=entity,entity_id=entity_id,detail=detail))
def as_customer(c):return {'id':c.id,'name':c.name,'billing':c.billing_address,'contact':c.contact,'phone':c.phone,'email':c.email}
def as_job(j,db):
    loc=db.get(Location,j.location_id) if j.location_id else None
    return {'id':j.job_number,'customerId':j.customer_id,'location':loc.name if loc else '', 'address':loc.address if loc else '', 'description':j.description,'status':j.status,'contract':float(j.contract_amount),'labor':float(j.historical_labor_cost),'materials':float(j.historical_material_cost),'mileage':float(j.historical_mileage_cost),'source':j.source}
def as_po(p,db):
    j=db.get(Job,p.job_id)
    return {'id':p.po_number,'jobId':j.job_number,'vendor':p.vendor,'description':p.description,'amount':float(p.amount),'received':float(p.received_value),'status':p.status,'date':p.issue_date.isoformat() if p.issue_date else '', 'source':p.source}
def current_user(session: Optional[str] = Cookie(default=None)):
    if not session:raise HTTPException(401,'Login required')
    with Session(engine) as db:
        record=db.get(UserSession, token_hash(session))
        if not record or record.expires_at.replace(tzinfo=timezone.utc)<datetime.now(timezone.utc):raise HTTPException(401,'Session expired')
        user=db.get(User,record.user_id)
        if not user or not user.active:raise HTTPException(401,'Account unavailable')
        return {'id':user.id,'username':user.username,'name':user.display_name,'role':user.role,'po_prefix':user.po_prefix}

def require(scope):
    def dependency(user=Depends(current_user)):
        if scope not in ROLE_SCOPES[user['role']]:raise HTTPException(403,'Insufficient permissions')
        return user
    return dependency

class LoginIn(BaseModel):
    username:str
    password:str
@app.post('/api/auth/login')
def login(data:LoginIn,response:Response):
    with Session(engine) as db:
        u=db.scalar(select(User).where(User.username==data.username.lower().strip()))
        if not u or not u.active or not verify_password(data.password,u.password_hash):raise HTTPException(401,'Invalid credentials')
        token=make_token()
        db.add(UserSession(token_hash=token_hash(token),user_id=u.id,expires_at=expiry()))
        log(db,'LOGIN','user',u.id)
        db.commit()
        response.set_cookie('session',token,max_age=43200,httponly=True,secure=os.getenv('COOKIE_SECURE','0')=='1',samesite='strict',path='/')
        return {'username':u.username,'name':u.display_name,'role':u.role,'po_prefix':u.po_prefix}
@app.post('/api/auth/logout')
def logout(response:Response,session:Optional[str]=Cookie(default=None),user=Depends(current_user)):
    if session:
        with Session(engine) as db:
            entry=db.get(UserSession,token_hash(session))
            if entry:db.delete(entry);db.commit()
    response.delete_cookie('session',path='/')
    return {'ok':True}
@app.get('/api/auth/me')
def me(user=Depends(current_user)):return user

@app.get('/api/health')
def health():
    with engine.connect() as connection: connection.exec_driver_sql('SELECT 1')
    return {'status':'ok','database':'connected','mode':RUNTIME['mode']}
@app.get('/api/snapshot')
def snapshot(user=Depends(require('manage_pos'))):
    with Session(engine) as db:
        customers=list(db.scalars(select(Customer).order_by(Customer.name)))
        jobs=list(db.scalars(select(Job).order_by(Job.job_number)))
        pos=list(db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.po_number)))
        return {'customers':[as_customer(x) for x in customers], 'jobs':[as_job(x,db) for x in jobs], 'pos':[as_po(x,db) for x in pos], 'workOrders':[],'estimates':[],'invoices':[],'pm':[],'equipment':[],'time':[],'source':'database','version':2}
@app.get('/api/customers')
def list_customers(user=Depends(require('manage_customers'))):
    with Session(engine) as db:
        return [as_customer(c) for c in db.scalars(select(Customer).order_by(Customer.name))]
@app.get('/api/jobs')
def list_jobs(user=Depends(require('manage_pos'))):
    # Match snapshot permissions: this representation contains job costing.
    with Session(engine) as db:
        return [as_job(j,db) for j in db.scalars(select(Job).order_by(Job.job_number))]
@app.post('/api/customers',status_code=201)
def add_customer(data:CustomerIn,user=Depends(require('manage_customers'))):
    with Session(engine) as db:
        c=Customer(name=data.name,billing_address=data.billing_address,contact=data.contact,phone=data.phone,email=data.email);db.add(c);db.flush();log(db,'CREATE','customer',c.id);db.commit();return as_customer(c)
@app.post('/api/jobs',status_code=201)
def add_job(data:JobIn,user=Depends(require('manage_jobs'))):
    with Session(engine) as db:
        if not db.get(Customer,data.customer_id):raise HTTPException(422,'Customer not found')
        if db.scalar(select(Job.id).where(Job.job_number==data.job_number)):raise HTTPException(409,'Job number already exists')
        if data.location_id:
            l=db.get(Location,data.location_id)
            if not l or l.customer_id!=data.customer_id:raise HTTPException(422,'Location does not belong to customer')
        payload=data.model_dump(exclude={'location_name','address'})
        if not data.location_id and (data.location_name.strip() or data.address.strip()):
            location=Location(customer_id=data.customer_id,name=data.location_name.strip() or data.address.strip(),address=data.address.strip());db.add(location);db.flush();payload['location_id']=location.id
        j=Job(**payload);db.add(j);db.flush();log(db,'CREATE','job',j.id,j.job_number);db.commit();return as_job(j,db)
@app.post('/api/purchase-orders',status_code=410)
def legacy_purchase_order_disabled(user=Depends(current_user)):
    # Avoid bypassing server-assigned DR/AR/DB number sequences and single-job validation.
    raise HTTPException(410,'Use Purchasing to create automatically numbered job POs')

@app.get('/api/jobs/{job_number}/costs')
def job_costs(job_number:str,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        j=financial_job(db,job_number)
        field=approved_field_cost(db,j.id)
        po=job_purchase_totals(db,j)
        historical=cents(j.historical_labor_cost+j.historical_material_cost+j.historical_mileage_cost)
        actual=cents(historical+field+po['actual'])
        return {'job':job_number,'revenue':float(j.contract_amount),'historical_cost':float(historical),
                'approved_field_cost':float(field),'new_po_actual':float(po['actual']),
                'open_committed':float(po['committed']),'projected_cost':float(actual+po['committed']),
                'projected_profit':float(j.contract_amount-actual-po['committed']),
                'historical_po_excluded_from_actual':True}

class WorkOrderIn(BaseModel):
    job_number: str
    summary: str=Field(min_length=3,max_length=2000)
    priority: str='Normal'
    @field_validator('priority')
    @classmethod
    def priority_valid(cls,v):
        if v not in ('Low','Normal','High','Emergency'):raise ValueError('Invalid priority')
        return v
class AssignmentIn(BaseModel):
    technician_id:str
    scheduled_start:datetime
    scheduled_end:datetime
class NoteIn(BaseModel):
    note:str=Field(min_length=1,max_length=10000)
class StatusIn(BaseModel):
    status:str

def work_order_payload(w,db):
    j=db.get(Job,w.job_id)
    return {'id':w.id,'job_number':j.job_number,'summary':w.summary,'priority':w.priority,'status':w.status,'findings':w.findings,'created_at':w.created_at.isoformat()}

def permitted_wo(db,wo_id,user):
    w=db.get(WorkOrder,wo_id)
    if not w:raise HTTPException(404,'Work order not found')
    if user['role']=='technician' and not db.scalar(select(DispatchAssignment.id).where(DispatchAssignment.work_order_id==w.id,DispatchAssignment.technician_id==user['id'])):
        raise HTTPException(403,'Work order not assigned to you')
    return w

@app.get('/api/work-orders')
def list_work_orders(user=Depends(current_user)):
    with Session(engine) as db:
        q=select(WorkOrder).order_by(WorkOrder.created_at.desc())
        if user['role']=='technician':q=q.join(DispatchAssignment).where(DispatchAssignment.technician_id==user['id'])
        return [work_order_payload(w,db) for w in db.scalars(q).unique()]

@app.post('/api/work-orders',status_code=201)
def create_work_order(data:WorkOrderIn,user=Depends(require('manage_jobs'))):
    with Session(engine) as db:
        j=db.scalar(select(Job).where(Job.job_number==data.job_number))
        if not j:raise HTTPException(422,'Job does not exist')
        w=WorkOrder(job_id=j.id,summary=data.summary,priority=data.priority)
        db.add(w);db.flush();log(db,'CREATE','work_order',w.id,j.job_number);db.commit()
        return work_order_payload(w,db)

@app.get('/api/technicians')
def technicians(user=Depends(require('manage_jobs'))):
    with Session(engine) as db:
        return [{'id':u.id,'name':u.display_name,'username':u.username} for u in db.scalars(select(User).where(User.role=='technician',User.active==True).order_by(User.display_name))]

@app.post('/api/work-orders/{wo_id}/assignments',status_code=201)
def assign_work_order(wo_id:str,data:AssignmentIn,user=Depends(require('manage_jobs'))):
    if data.scheduled_start.tzinfo is None or data.scheduled_end.tzinfo is None or data.scheduled_start.utcoffset() is None or data.scheduled_end.utcoffset() is None:raise HTTPException(422,'Schedule requires explicit timezone offset')
    if data.scheduled_end<=data.scheduled_start:raise HTTPException(422,'End must follow start')
    with Session(engine) as db:
        w=permitted_wo(db,wo_id,user)
        t=db.get(User,data.technician_id)
        if not t or not t.active or t.role!='technician':raise HTTPException(422,'Active technician required')
        existing=db.scalar(select(DispatchAssignment).where(DispatchAssignment.technician_id==t.id,DispatchAssignment.status!='Cancelled',DispatchAssignment.scheduled_start<data.scheduled_end,DispatchAssignment.scheduled_end>data.scheduled_start))
        if existing:raise HTTPException(409,'Technician has an overlapping assignment')
        a=DispatchAssignment(work_order_id=w.id,technician_id=t.id,scheduled_start=data.scheduled_start,scheduled_end=data.scheduled_end)
        db.add(a);db.flush();log(db,'ASSIGN','dispatch',a.id,w.id);db.commit()
        return {'id':a.id,'work_order_id':w.id,'technician':t.display_name,'start':a.scheduled_start.isoformat(),'end':a.scheduled_end.isoformat()}

@app.get('/api/dispatch')
def dispatch(user=Depends(require('manage_jobs'))):
    with Session(engine) as db:
        result=[]
        for a in db.scalars(select(DispatchAssignment).order_by(DispatchAssignment.scheduled_start)):
            w=db.get(WorkOrder,a.work_order_id); j=db.get(Job,w.job_id);t=db.get(User,a.technician_id)
            result.append({'id':a.id,'work_order_id':w.id,'job_number':j.job_number,'summary':w.summary,'technician':t.display_name,'start':a.scheduled_start.isoformat(),'end':a.scheduled_end.isoformat(),'status':a.status})
        return result

@app.post('/api/work-orders/{wo_id}/notes',status_code=201)
def add_service_note(wo_id:str,data:NoteIn,user=Depends(current_user)):
    with Session(engine) as db:
        w=permitted_wo(db,wo_id,user);field_report_open(db,w.id)
        n=ServiceNote(work_order_id=w.id,technician_id=user['id'],note=data.note)
        db.add(n);db.flush();log(db,'ADD_NOTE','work_order',w.id);db.commit()
        return {'id':n.id,'note':n.note,'created_at':n.created_at.isoformat()}

@app.get('/api/work-orders/{wo_id}/notes')
def get_service_notes(wo_id:str,user=Depends(current_user)):
    with Session(engine) as db:
        permitted_wo(db,wo_id,user)
        return [{'id':n.id,'note':n.note,'created_at':n.created_at.isoformat(),'author':db.get(User,n.technician_id).display_name} for n in db.scalars(select(ServiceNote).where(ServiceNote.work_order_id==wo_id).order_by(ServiceNote.created_at))]

@app.patch('/api/work-orders/{wo_id}/status')
def update_work_order_status(wo_id:str,data:StatusIn,user=Depends(current_user)):
    if data.status not in ('Open','Scheduled','In Progress','Completed','Needs Follow-up','Waiting for Parts','Waiting for Approval','Cancelled'):raise HTTPException(422,'Invalid status')
    with Session(engine) as db:
        w=permitted_wo(db,wo_id,user);field_report_open(db,w.id)
        if data.status=='Completed' and db.scalar(select(ActiveTimeClock.technician_id).where(ActiveTimeClock.work_order_id==w.id)):
            raise HTTPException(409,'Clock out before marking the call completed')
        if data.status=='Completed':
            from .field_v111 import closeout_state
            review=closeout_state(db,wo_id)
            if not review['saved']:raise HTTPException(409,'Use Finish call to review work performed, parts, mileage and equipment')
        w.status=data.status;log(db,'STATUS','work_order',w.id,data.status);db.commit()
        return work_order_payload(w,db)

class EquipmentIn(BaseModel):
    location_id:str
    asset_tag:str=Field(min_length=1,max_length=100)
    equipment_type:str=''
    manufacturer:str=''
    model:str=''
    serial:str=''
    refrigerant:str=''
    notes:str=''
class TimeEntryIn(BaseModel):
    started_at:datetime
    ended_at:datetime
    mileage:Decimal=Field(default=Decimal('0'),ge=0)
    note:str=Field(default='',max_length=2000)
    @field_validator('started_at','ended_at')
    @classmethod
    def offset_required(cls,v):
        if not v.tzinfo or v.utcoffset() is None:raise ValueError('Explicit timezone offset required')
        return v

def equipment_payload(e,db):
    loc=db.get(Location,e.location_id)
    return {'id':e.id,'location_id':e.location_id,'customer_id':loc.customer_id,'asset_tag':e.asset_tag,'equipment_type':e.equipment_type,'manufacturer':e.manufacturer,'model':e.model,'serial':e.serial,'refrigerant':e.refrigerant,'notes':e.notes}

@app.get('/api/locations')
def list_locations(user=Depends(require('read_all'))):
    with Session(engine) as db:
        return [{'id':l.id,'customer_id':l.customer_id,'name':l.name,'address':l.address} for l in db.scalars(select(Location).order_by(Location.name))]

@app.get('/api/equipment')
def list_equipment(location_id:Optional[str]=None,user=Depends(require('read_all'))):
    with Session(engine) as db:
        q=select(Equipment).order_by(Equipment.asset_tag)
        if location_id:q=q.where(Equipment.location_id==location_id)
        return [equipment_payload(e,db) for e in db.scalars(q)]

@app.post('/api/equipment',status_code=201)
def create_equipment(data:EquipmentIn,user=Depends(require('manage_jobs'))):
    with Session(engine) as db:
        if not db.get(Location,data.location_id):raise HTTPException(422,'Location not found')
        if db.scalar(select(Equipment.id).where(Equipment.location_id==data.location_id,Equipment.asset_tag==data.asset_tag)):
            raise HTTPException(409,'Asset tag already exists at this location')
        e=Equipment(**data.model_dump());db.add(e);db.flush();log(db,'CREATE','equipment',e.id,e.asset_tag);db.commit();return equipment_payload(e,db)

@app.post('/api/work-orders/{wo_id}/equipment/{equipment_id}',status_code=201)
def attach_equipment(wo_id:str,equipment_id:str,user=Depends(require('manage_jobs'))):
    with Session(engine) as db:
        w=permitted_wo(db,wo_id,user);field_report_open(db,w.id);e=db.get(Equipment,equipment_id)
        if not e:raise HTTPException(404,'Equipment not found')
        j=db.get(Job,w.job_id);l=db.get(Location,e.location_id)
        if l.customer_id!=j.customer_id or (j.location_id and j.location_id!=e.location_id):raise HTTPException(422,'Equipment is not at this job location/customer')
        if db.get(WorkOrderEquipment,(wo_id,equipment_id)):raise HTTPException(409,'Already attached')
        db.add(WorkOrderEquipment(work_order_id=wo_id,equipment_id=equipment_id));log(db,'LINK','equipment',equipment_id,wo_id);db.commit()
        return {'work_order_id':wo_id,'equipment_id':equipment_id}

@app.get('/api/work-orders/{wo_id}/equipment')
def work_order_equipment(wo_id:str,user=Depends(current_user)):
    with Session(engine) as db:
        permitted_wo(db,wo_id,user)
        items=db.scalars(select(Equipment).join(WorkOrderEquipment,Equipment.id==WorkOrderEquipment.equipment_id).where(WorkOrderEquipment.work_order_id==wo_id)).all()
        return [equipment_payload(e,db) for e in items]

@app.post('/api/work-orders/{wo_id}/time',status_code=201)
def add_field_time(wo_id:str,data:TimeEntryIn,user=Depends(current_user)):
    start=data.started_at.astimezone(timezone.utc);end=data.ended_at.astimezone(timezone.utc)
    elapsed=(end-start).total_seconds()/3600
    if elapsed<=0 or elapsed>24:raise HTTPException(422,'Shift must be greater than zero and no longer than 24 hours')
    with Session(engine) as db:
        w=permitted_wo(db,wo_id,user);field_report_open(db,w.id)
        if user['role']=='technician': tech_id=user['id']
        else:raise HTTPException(403,'Time entries must be entered by assigned technician')
        lock_time_user(db,tech_id)
        ensure_no_time_overlap(db,tech_id,start,end)
        if data.mileage>0 and db.scalar(select(CallMileage).where(CallMileage.work_order_id==wo_id)):
            raise HTTPException(409,'Use the call mileage field; do not also record mileage on time entries')
        entry=FieldTimeEntry(work_order_id=w.id,technician_id=tech_id,started_at=start,ended_at=end,hours=Decimal(str(round(elapsed,2))),mileage=data.mileage,note=data.note)
        db.add(entry);db.flush()
        log(db,'ADD_TIME','work_order',w.id,entry.id);db.commit()
        return time_entry_payload(db,entry)

@app.get('/api/work-orders/{wo_id}/time')
def list_field_time(wo_id:str,user=Depends(current_user)):
    with Session(engine) as db:
        permitted_wo(db,wo_id,user)
        rows=db.scalars(select(FieldTimeEntry).where(FieldTimeEntry.work_order_id==wo_id).order_by(FieldTimeEntry.started_at)).all()
        if user['role']=='technician':rows=[t for t in rows if t.technician_id==user['id']]
        return [time_entry_payload(db,t) for t in rows]

@app.get('/tech')
def technician_portal(): return FileResponse(ROOT/'tech.html')
@app.get('/tech.js')
def technician_script(): return FileResponse(ROOT/'tech.js',media_type='application/javascript')

@app.get('/')
def index(): return FileResponse(ROOT/'index.html')
@app.get('/app.js')
def app_script(): return FileResponse(ROOT/'app.js',media_type='application/javascript')
@app.get('/styles.css')
def app_styles(): return FileResponse(ROOT/'styles.css',media_type='text/css')
@app.get('/office.css')
def office_styles(): return FileResponse(ROOT/'office.css',media_type='text/css')

@app.get('/equipment')
def equipment_page(): return FileResponse(ROOT/'equipment.html')
@app.get('/equipment.js')
def equipment_script(): return FileResponse(ROOT/'equipment.js',media_type='application/javascript')

# v0.7: job costing, read-only printable reports, PM planning and inspections.
from pydantic import model_validator
from fastapi.responses import HTMLResponse
from html import escape
import json

class RateIn(BaseModel):
    technician_id:str
    hourly_cost:Decimal=Field(ge=0)
    overtime_multiplier:Decimal=Field(default=Decimal('1.5'),ge=1,le=4)
    mileage_rate:Decimal=Field(default=Decimal('0'),ge=0)

@app.put('/api/labor-rates')
def set_labor_rate(data:RateIn,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        t=db.get(User,data.technician_id)
        if not t or not t.active or t.role not in ROLE_SCOPES:raise HTTPException(422,'Select an active team member')
        rate=db.scalar(select(LaborRate).where(LaborRate.technician_id==t.id))
        if not rate:rate=LaborRate(technician_id=t.id);db.add(rate)
        rate.hourly_cost=data.hourly_cost;rate.overtime_multiplier=data.overtime_multiplier;rate.mileage_rate=data.mileage_rate
        log(db,'SET_RATE','technician',t.id);db.commit()
        return {'technician_id':t.id,'saved':True}

@app.get('/api/labor-rates')
def get_labor_rates(user=Depends(require('view_financials'))):
    with Session(engine) as db:
        return [{'technician_id':r.technician_id,'name':db.get(User,r.technician_id).display_name,'hourly_cost':float(r.hourly_cost),'overtime_multiplier':float(r.overtime_multiplier),'mileage_rate':float(r.mileage_rate)} for r in db.scalars(select(LaborRate))]

class ApprovalIn(BaseModel):
    overtime_hours:Decimal=Field(default=Decimal('0'),ge=0)

@app.post('/api/time/{entry_id}/approve',status_code=201)
def approve_time(entry_id:str,data:ApprovalIn,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        entry=db.get(FieldTimeEntry,entry_id)
        if not entry:raise HTTPException(404,'Time entry not found')
        lock_time_user(db,entry.technician_id)
        from .time_corrections import pending
        if pending(db,entry_id=entry.id):raise HTTPException(409,'Review the pending time correction before approving hours')
        if db.scalar(select(TimeApproval).where(TimeApproval.time_entry_id==entry_id)):raise HTTPException(409,'Time already approved')
        rate=db.scalar(select(LaborRate).where(LaborRate.technician_id==entry.technician_id))
        if not rate:raise HTTPException(422,'Configure technician cost rate first')
        if data.overtime_hours>entry.hours:raise HTTPException(422,'Overtime exceeds total hours')
        regular=entry.hours-data.overtime_hours
        labor=(regular*rate.hourly_cost+data.overtime_hours*rate.hourly_cost*rate.overtime_multiplier).quantize(Decimal('0.01'))
        from .profit_tracking import burden_cost, LaborCostSnapshot
        burden=burden_cost(db,entry,rate,regular,data.overtime_hours)
        if burden:labor=Decimal(burden['total'])
        mileage=(entry.mileage*rate.mileage_rate).quantize(Decimal('0.01'))
        row=TimeApproval(time_entry_id=entry_id,approved_by=user['id'],regular_hours=regular,overtime_hours=data.overtime_hours, labor_cost=labor,mileage_cost=mileage)
        db.add(row);db.flush()
        if burden:
            import json
            db.add(LaborCostSnapshot(approval_id=row.id,details_json=json.dumps(burden)))
        log(db,'APPROVE_TIME','field_time_entry',entry_id);db.commit()
        return {'id':row.id,'labor_cost':float(labor),'mileage_cost':float(mileage)}

@app.get('/api/jobs/{job_number}/field-costs')
def field_costs(job_number:str,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        job=db.scalar(select(Job).where(Job.job_number==job_number))
        if not job:raise HTTPException(404,'Job not found')
        entries=db.execute(select(TimeApproval,FieldTimeEntry).join(FieldTimeEntry,TimeApproval.time_entry_id==FieldTimeEntry.id).join(WorkOrder,FieldTimeEntry.work_order_id==WorkOrder.id).where(WorkOrder.job_id==job.id)).all()
        call_mileage=approved_call_mileage_cost(db,job.id)
        costs=sum((a.labor_cost+a.mileage_cost for a,e in entries),Decimal('0'))+call_mileage
        return {'job_number':job_number,'approved_field_labor_and_mileage':float(costs),'approved_entries':len(entries),'approved_hours':float(sum((e.hours for a,e in entries),Decimal('0'))),'approved_call_mileage_cost':float(call_mileage),'historical_labor_cost':float(job.historical_labor_cost),'historical_mileage_cost':float(job.historical_mileage_cost),'note':'Approved field costs are separate from historical spreadsheet totals and must not be added twice for overlapping periods.'}

def report_html(db,w):
    job=db.get(Job,w.job_id);customer=db.get(Customer,job.customer_id)
    location=db.get(Location,job.location_id) if job.location_id else None
    notes=db.scalars(select(ServiceNote).where(ServiceNote.work_order_id==w.id).order_by(ServiceNote.created_at)).all()
    assets=db.scalars(select(Equipment).join(WorkOrderEquipment,Equipment.id==WorkOrderEquipment.equipment_id).where(WorkOrderEquipment.work_order_id==w.id)).all()
    safe=lambda v:escape(str(v or ''))
    note_html=''.join(f'<li>{safe(n.created_at.date())}: {safe(n.note)}</li>' for n in notes) or '<li>No notes recorded</li>'
    from .call_field_notes import customer_note_payloads
    note_html+=''.join('<li>'+safe(n['note'])+'</li>' for n in customer_note_payloads(db,w.id))
    asset_html=''.join(f'<li>{safe(e.asset_tag)} - {safe(e.manufacturer)} {safe(e.model)} ({safe(e.serial)})</li>' for e in assets) or '<li>No equipment linked</li>'
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Service Report</title><style>body{{font:15px Arial;max-width:850px;margin:35px auto;color:#24353b}}header{{background:#172a30;color:white;padding:22px;border-bottom:8px solid #187780}}h1{{margin:0}}h2{{color:#146c75;border-bottom:1px solid #ccc}}li{{margin:8px 0}}@media print{{button{{display:none}}body{{margin:12mm}}}}</style></head><body><header><h1>LIMITLESS MECHANICAL</h1><p>Service Report · {safe(job.job_number)}</p></header><p><b>Customer:</b> {safe(customer.name)}<br><b>Location:</b> {safe(location.name if location else '')} · {safe(location.address if location else '')}<br><b>Status:</b> {safe(w.status)}<br><b>Work:</b> {safe(w.summary)}</p><h2>Equipment</h2><ul>{asset_html}</ul><h2>Technician Notes</h2><ul>{note_html}</ul><h2>Findings</h2><p>{safe(w.findings)}</p><p><small>Generated service summary. Customer signatures and certified PDF generation are not yet supported.</small></p><button onclick="window.print()">Print / Save as PDF</button></body></html>"""

@app.get('/api/work-orders/{wo_id}/report',response_class=HTMLResponse)
def service_report(wo_id:str,user=Depends(current_user)):
    with Session(engine) as db:
        w=permitted_wo(db,wo_id,user)
        return HTMLResponse(report_html(db,w),headers={'Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"})

class PMPlanIn(BaseModel):
    equipment_id:str
    interval_days:int=Field(ge=7,le=730)
    next_due:date
    checklist:list[str]=Field(min_length=1,max_length=30)
    @field_validator('checklist')
    @classmethod
    def check_items(cls,items):
        if any(not x.strip() or len(x)>300 for x in items):raise ValueError('Each item must have text of 300 characters or fewer')
        return items

@app.post('/api/pm/plans',status_code=201)
def create_pm_plan(data:PMPlanIn,user=Depends(require('manage_jobs'))):
    with Session(engine) as db:
        if not db.get(Equipment,data.equipment_id):raise HTTPException(422,'Equipment not found')
        p=PMPlan(equipment_id=data.equipment_id,interval_days=data.interval_days,next_due=data.next_due,checklist=json.dumps(data.checklist));db.add(p);db.flush();log(db,'CREATE_PM_PLAN','pm_plan',p.id);db.commit()
        return {'id':p.id,'next_due':p.next_due.isoformat()}

@app.get('/api/pm/plans')
def list_pm_plans(user=Depends(require('manage_jobs'))):
    with Session(engine) as db:
        return [{'id':p.id,'equipment_id':p.equipment_id,'interval_days':p.interval_days,'next_due':p.next_due.isoformat(),'checklist':json.loads(p.checklist),'active':p.active} for p in db.scalars(select(PMPlan).order_by(PMPlan.next_due))]

@app.post('/api/pm/plans/{plan_id}/generate',status_code=201)
def generate_pm(plan_id:str,user=Depends(require('manage_jobs'))):
    with Session(engine) as db:
        p=db.get(PMPlan,plan_id)
        if not p or not p.active:raise HTTPException(404,'Active PM plan not found')
        e=db.get(Equipment,p.equipment_id);loc=db.get(Location,e.location_id)
        existing=db.scalar(select(PMVisit).where(PMVisit.plan_id==p.id,PMVisit.scheduled_due==p.next_due))
        if existing:raise HTTPException(409,'Visit already generated for this due date')
        # PM work must be linked to a real job. Do not fabricate an LMS job number.
        return {'requires_job':True,'next_due':p.next_due.isoformat(),'message':'Provide a valid job number to generate a work order.'}

class GeneratePMIn(BaseModel):
    job_number:str
@app.post('/api/pm/plans/{plan_id}/work-order',status_code=201)
def create_pm_wo(plan_id:str,data:GeneratePMIn,user=Depends(require('manage_jobs'))):
    with Session(engine) as db:
        p=db.get(PMPlan,plan_id)
        if not p or not p.active:raise HTTPException(404,'Active PM plan not found')
        e=db.get(Equipment,p.equipment_id);loc=db.get(Location,e.location_id)
        job=db.scalar(select(Job).where(Job.job_number==data.job_number))
        if not job or job.customer_id!=loc.customer_id or job.location_id!=loc.id:raise HTTPException(422,'Job must belong to equipment customer and location')
        if db.scalar(select(PMVisit).where(PMVisit.plan_id==p.id,PMVisit.scheduled_due==p.next_due)):raise HTTPException(409,'Visit already exists')
        w=WorkOrder(job_id=job.id,summary=f'Preventive maintenance: {e.asset_tag}',priority='Normal',status='Open');db.add(w);db.flush()
        db.add(WorkOrderEquipment(work_order_id=w.id,equipment_id=e.id))
        v=PMVisit(plan_id=p.id,work_order_id=w.id,scheduled_due=p.next_due);db.add(v);db.flush();log(db,'GENERATE_PM','pm_visit',v.id);db.commit()
        return {'visit_id':v.id,'work_order_id':w.id,'due':v.scheduled_due.isoformat()}

class InspectionIn(BaseModel):
    results:dict[str,str]
    @field_validator('results')
    @classmethod
    def validate_results(cls,results):
        if any(v not in ('pass','fail','na') for v in results.values()):raise ValueError('Use pass, fail or na')
        return results

@app.post('/api/pm/visits/{visit_id}/inspection',status_code=201)
def inspect_pm(visit_id:str,data:InspectionIn,user=Depends(current_user)):
    with Session(engine) as db:
        visit=db.get(PMVisit,visit_id)
        if not visit:raise HTTPException(404,'PM visit not found')
        permitted_wo(db,visit.work_order_id,user)
        if user['role']!='technician':raise HTTPException(403,'Technician inspection required')
        p=db.get(PMPlan,visit.plan_id);items=json.loads(p.checklist)
        if set(data.results)!=set(items):raise HTTPException(422,'All checklist items must be answered exactly once')
        if db.scalar(select(PMInspection).where(PMInspection.visit_id==visit.id)):raise HTTPException(409,'Inspection already submitted')
        row=PMInspection(visit_id=visit.id,technician_id=user['id'],responses_json=json.dumps(data.results));db.add(row)
        # Advance from scheduled due date, not current date, avoiding drift.
        p.next_due=visit.scheduled_due+timedelta(days=p.interval_days)
        log(db,'SUBMIT_PM','pm_visit',visit.id);db.commit()
        return {'id':row.id,'failed_items':[k for k,v in data.results.items() if v=='fail'],'next_due':p.next_due.isoformat()}

@app.get('/pm')
def pm_page():return FileResponse(ROOT/'pm.html')
@app.get('/pm.js')
def pm_script():return FileResponse(ROOT/'pm.js',media_type='application/javascript')


# v0.8 - Financial workflow. Local development; payment recording is not a payment processor.
class ProposalInput(BaseModel):
    job_number:str
    number:str=Field(min_length=2,max_length=70)
    title:str=Field(min_length=2,max_length=250)
    scope:str=''
    total:Decimal=Field(gt=0,max_digits=14,decimal_places=2)
class InvoiceLineInput(BaseModel):
    description:str=Field(min_length=1,max_length=1000)
    quantity:Decimal=Field(gt=0,max_digits=12,decimal_places=3)
    unit_price:Decimal=Field(ge=0,max_digits=14,decimal_places=2)
    tax_rate:Decimal=Field(default=Decimal('0'),ge=0,le=100,max_digits=7,decimal_places=3)

class InvoiceInput(BaseModel):
    job_number:str
    number:str=Field(min_length=2,max_length=70)
    proposal_id:Optional[str]=None
    amount:Optional[Decimal]=Field(default=None,gt=0,max_digits=14,decimal_places=2)
    lines:list[InvoiceLineInput]=Field(default_factory=list,max_length=150)
    issued_on:Optional[date]=None
    due_on:Optional[date]=None

class InvoiceLinesUpdate(BaseModel):
    lines:list[InvoiceLineInput]=Field(min_length=1,max_length=150)

class CreditInput(BaseModel):
    number:str=Field(min_length=2,max_length=70)
    amount:Decimal=Field(gt=0,max_digits=14,decimal_places=2)
    reason:str=Field(min_length=3,max_length=2000)
    credited_on:date

class PaymentReversalInput(BaseModel):
    reference:str=Field(min_length=2,max_length=150)
    amount:Decimal=Field(gt=0,max_digits=14,decimal_places=2)
    reason:str=Field(min_length=3,max_length=2000)
    reversed_on:date

class RefundInput(BaseModel):
    reference:str=Field(min_length=2,max_length=150)
    amount:Decimal=Field(gt=0,max_digits=14,decimal_places=2)
    reason:str=Field(min_length=3,max_length=2000)
    refunded_on:date

class PaymentInput(BaseModel):
    amount:Decimal=Field(gt=0,max_digits=14,decimal_places=2)
    paid_on:date
    reference:str=Field(min_length=2,max_length=150)

def financial_job(db,number):
    j=db.scalar(select(Job).where(Job.job_number==number))
    if not j:raise HTTPException(404,'Job not found')
    return j

def proposal_data(p,db):
    return {'id':p.id,'number':p.number,'job_number':db.get(Job,p.job_id).job_number,'title':p.title,'scope':p.scope,'status':p.status,'total':float(p.total)}
CENTS=Decimal('0.01')
def cents(value):return Decimal(str(value)).quantize(CENTS,rounding=ROUND_HALF_UP)

def line_calculation(row):
    subtotal=cents(row.quantity*row.unit_price)
    tax=cents(subtotal*row.tax_rate/Decimal('100'))
    return subtotal,tax

def invoice_lines(db,inv):
    return list(db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id==inv.id).order_by(InvoiceLine.position)))

def put_invoice_lines(db,inv,items):
    if not items:raise HTTPException(422,'At least one item is required')
    if db.scalar(select(MileageInvoiceLink.invoice_line_id).where(MileageInvoiceLink.invoice_id==inv.id)):
        raise HTTPException(409,'Remove linked call mileage from the draft before replacing invoice lines')
    for old in invoice_lines(db,inv):db.delete(old)
    db.flush()
    net=tax=Decimal('0')
    for i, row in enumerate(items,1):
        subtotal,tax_value=line_calculation(row)
        net+=subtotal;tax+=tax_value
        db.add(InvoiceLine(invoice_id=inv.id,position=i,description=row.description,quantity=row.quantity,unit_price=row.unit_price,tax_rate=row.tax_rate,subtotal=subtotal,tax_amount=tax_value))
    return cents(net),cents(tax)

def invoice_ledger(db,inv,as_of=None):
    credit_q=select(func.coalesce(func.sum(InvoiceCredit.amount),0)).where(InvoiceCredit.invoice_id==inv.id)
    payment_q=select(func.coalesce(func.sum(InvoicePayment.amount),0)).where(InvoicePayment.invoice_id==inv.id)
    reversal_q=select(func.coalesce(func.sum(PaymentReversal.amount),0)).join(InvoicePayment,PaymentReversal.payment_id==InvoicePayment.id).where(InvoicePayment.invoice_id==inv.id)
    refund_q=select(func.coalesce(func.sum(InvoiceRefund.amount),0)).where(InvoiceRefund.invoice_id==inv.id)
    if as_of is not None:
        credit_q=credit_q.where(InvoiceCredit.credited_on<=as_of)
        payment_q=payment_q.where(InvoicePayment.paid_on<=as_of)
        reversal_q=reversal_q.where(PaymentReversal.reversed_on<=as_of,InvoicePayment.paid_on<=as_of)
        refund_q=refund_q.where(InvoiceRefund.refunded_on<=as_of)
    credits=db.scalar(credit_q) or Decimal('0')
    payments=db.scalar(payment_q) or Decimal('0')
    reversals=db.scalar(reversal_q) or Decimal('0')
    refunds=db.scalar(refund_q) or Decimal('0')
    adjusted=cents(inv.amount-credits)
    collected=cents(payments-reversals-refunds)
    balance=cents(adjusted-collected)
    return {'original_amount':cents(inv.amount),'credits':cents(credits),'net_billed':adjusted,'payments':cents(payments),'reversals':cents(reversals),'refunds':cents(refunds),'collected':collected,'balance':max(Decimal('0'),balance),'credit_balance':max(Decimal('0'),-balance)}

def refresh_invoice_status(inv,ledger):
    if inv.status=='Draft':return
    if ledger['credit_balance']>0:inv.status='Credit Balance'
    elif ledger['net_billed']==0:inv.status='Credited'
    elif ledger['balance']==0:inv.status='Paid'
    elif ledger['collected']>0:inv.status='Partially Paid'
    else:inv.status='Issued'

def invoice_data(inv,db):
    x=invoice_ledger(db,inv)
    lines=invoice_lines(db,inv)
    return {'id':inv.id,'number':inv.number,'job_number':db.get(Job,inv.job_id).job_number,'proposal_id':inv.proposal_id,'status':inv.status,
        'amount':float(inv.amount),'credits':float(x['credits']),'net_billed':float(x['net_billed']),'paid':float(x['collected']),'payments_gross':float(x['payments']),'reversals':float(x['reversals']),'refunds':float(x['refunds']),'balance':float(x['balance']),'credit_balance':float(x['credit_balance']),
        'issued_on':inv.issued_on.isoformat() if inv.issued_on else None,'due_on':inv.due_on.isoformat() if inv.due_on else None,
        'lines':[{'description':l.description,'quantity':float(l.quantity),'unit_price':float(l.unit_price),'tax_rate':float(l.tax_rate),'subtotal':float(l.subtotal),'tax':float(l.tax_amount)} for l in lines]}
@app.post('/api/proposals',status_code=201)
def create_proposal(data:ProposalInput,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        j=financial_job(db,data.job_number)
        if db.scalar(select(Proposal.id).where(Proposal.number==data.number)):raise HTTPException(409,'Proposal number exists')
        p=Proposal(job_id=j.id,number=data.number,title=data.title,scope=data.scope,total=data.total)
        db.add(p);db.flush();log(db,'CREATE','proposal',p.id,data.number);db.commit();return proposal_data(p,db)
@app.get('/api/proposals')
def proposals(user=Depends(require('view_financials'))):
    with Session(engine) as db:return [proposal_data(p,db) for p in db.scalars(select(Proposal).order_by(Proposal.created_at.desc()))]
@app.post('/api/proposals/{proposal_id}/approve')
def approve_proposal(proposal_id:str,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        p=db.get(Proposal,proposal_id)
        if not p:raise HTTPException(404,'Proposal missing')
        from .operations_v110 import GuardedQuote
        if db.get(GuardedQuote,p.id):raise HTTPException(409,'Use the customer authorization workflow for this service quote')
        if p.status!='Draft':raise HTTPException(409,'Only draft proposals can be approved')
        from .proposals_pm_v15 import QuoteDetail
        if db.get(QuoteDetail,p.id):raise HTTPException(409,'Select an option and record customer acceptance through the advanced estimate workflow')
        p.status='Approved';log(db,'APPROVE','proposal',p.id);db.commit();return proposal_data(p,db)
@app.post('/api/invoices',status_code=201)
def create_invoice(data:InvoiceInput,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        j=financial_job(db,data.job_number)
        if db.scalar(select(InvoiceRecord.id).where(InvoiceRecord.number==data.number)):raise HTTPException(409,'Invoice number exists')
        if data.due_on and data.issued_on and data.due_on<data.issued_on:raise HTTPException(422,'Due date precedes issue date')
        if data.lines:
            subtotal=sum((line_calculation(row)[0] for row in data.lines),Decimal('0'))
            tax=sum((line_calculation(row)[1] for row in data.lines),Decimal('0'))
            total=cents(subtotal+tax)
            if data.amount is not None and data.amount!=total:raise HTTPException(422,'Invoice amount does not match item subtotal plus tax')
        else:
            if data.amount is None:raise HTTPException(422,'Provide invoice amount or itemized lines')
            total=data.amount
        if total<=0:raise HTTPException(422,'Invoice total must be positive')
        if data.proposal_id:
            p=db.get(Proposal,data.proposal_id)
            if not p or p.job_id!=j.id or p.status!='Approved':raise HTTPException(422,'Invoice requires approved proposal for same job')
            existing=db.scalar(select(InvoiceRecord.id).where(InvoiceRecord.proposal_id==p.id))
            if existing:raise HTTPException(409,'Proposal already invoiced; create separate uninvoiced progress billing if needed')
            if total!=p.total:raise HTTPException(422,'Proposal invoice total must match approved proposal')
        inv=InvoiceRecord(job_id=j.id,number=data.number,proposal_id=data.proposal_id,amount=total,issued_on=data.issued_on,due_on=data.due_on)
        db.add(inv);db.flush()
        if data.lines:put_invoice_lines(db,inv,data.lines)
        log(db,'CREATE','invoice',inv.id,data.number);db.commit();return invoice_data(inv,db)

@app.put('/api/invoices/{invoice_id}/lines')
def replace_draft_invoice_lines(invoice_id:str,data:InvoiceLinesUpdate,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        inv=db.get(InvoiceRecord,invoice_id)
        if not inv:raise HTTPException(404,'Invoice missing')
        if inv.status!='Draft':raise HTTPException(409,'Issued invoice lines are immutable; use a credit memo')
        net,tax=put_invoice_lines(db,inv,data.lines)
        if inv.proposal_id and net+tax!=db.get(Proposal,inv.proposal_id).total:raise HTTPException(422,'Approved proposal total mismatch')
        inv.amount=net+tax
        if inv.amount<=0:raise HTTPException(422,'Invoice amount must be positive')
        log(db,'REPLACE_LINES','invoice',inv.id);db.commit();return invoice_data(inv,db)

@app.get('/api/invoices')
def invoices(user=Depends(require('view_financials'))):
    with Session(engine) as db:return [invoice_data(i,db) for i in db.scalars(select(InvoiceRecord).order_by(InvoiceRecord.number))]
@app.post('/api/invoices/{invoice_id}/issue')
def issue_invoice(invoice_id:str,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        inv=db.scalar(select(InvoiceRecord).where(InvoiceRecord.id==invoice_id).with_for_update())
        if not inv:raise HTTPException(404,'Invoice missing')
        if inv.status!='Draft':raise HTTPException(409,'Invoice is not in draft')
        validate_pricing_invoice_margin(db,inv)
        inv.status='Issued';inv.issued_on=inv.issued_on or date.today()
        from .billing_v111 import freeze_invoice
        freeze_invoice(db,inv)
        log(db,'ISSUE','invoice',inv.id);db.commit();return invoice_data(inv,db)
@app.post('/api/invoices/{invoice_id}/payments',status_code=201)
def record_payment(invoice_id:str,data:PaymentInput,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        inv=db.get(InvoiceRecord,invoice_id)
        if not inv:raise HTTPException(404,'Invoice missing')
        if inv.status=='Draft':raise HTTPException(409,'Invoice must be issued')
        if db.scalar(select(InvoicePayment.id).where(InvoicePayment.reference==data.reference)):raise HTTPException(409,'Payment reference already recorded')
        ledger=invoice_ledger(db,inv)
        if data.amount>ledger['balance']:raise HTTPException(422,'Payment exceeds outstanding balance')
        db.add(InvoicePayment(invoice_id=inv.id,amount=data.amount,paid_on=data.paid_on,reference=data.reference))
        db.flush();refresh_invoice_status(inv,invoice_ledger(db,inv))
        log(db,'PAYMENT','invoice',inv.id,f'reference={data.reference}')
        db.commit();return invoice_data(inv,db)

@app.post('/api/invoices/{invoice_id}/credits',status_code=201)
def create_invoice_credit(invoice_id:str,data:CreditInput,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        inv=db.get(InvoiceRecord,invoice_id)
        if not inv:raise HTTPException(404,'Invoice missing')
        if inv.status=='Draft':raise HTTPException(409,'Cannot credit draft invoice')
        if db.scalar(select(InvoiceCredit.id).where(InvoiceCredit.number==data.number)):raise HTTPException(409,'Credit memo number already exists')
        ledger=invoice_ledger(db,inv)
        if data.amount>ledger['net_billed']:raise HTTPException(422,'Credit exceeds uncredited invoice amount')
        db.add(InvoiceCredit(invoice_id=inv.id,number=data.number,amount=data.amount,reason=data.reason,credited_on=data.credited_on,created_by=user['id']))
        db.flush();refresh_invoice_status(inv,invoice_ledger(db,inv))
        log(db,'CREDIT_MEMO','invoice',inv.id,data.number);db.commit();return invoice_data(inv,db)

@app.post('/api/payments/{payment_id}/reverse',status_code=201)
def reverse_payment(payment_id:str,data:PaymentReversalInput,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        payment=db.get(InvoicePayment,payment_id)
        if not payment:raise HTTPException(404,'Payment missing')
        inv=db.get(InvoiceRecord,payment.invoice_id)
        if db.scalar(select(PaymentReversal.id).where(PaymentReversal.reference==data.reference)):raise HTTPException(409,'Reversal reference already exists')
        reversed_amount=db.scalar(select(func.coalesce(func.sum(PaymentReversal.amount),0)).where(PaymentReversal.payment_id==payment.id)) or Decimal('0')
        if data.amount>payment.amount-reversed_amount:raise HTTPException(422,'Reversal exceeds remaining payment amount')
        # A reversal cannot make total collected negative if a refund was already paid.
        if data.amount>invoice_ledger(db,inv)['collected']:raise HTTPException(422,'Cannot reverse funds already refunded')
        db.add(PaymentReversal(payment_id=payment.id,reference=data.reference,amount=data.amount,reason=data.reason,reversed_on=data.reversed_on,created_by=user['id']))
        db.flush();refresh_invoice_status(inv,invoice_ledger(db,inv))
        log(db,'PAYMENT_REVERSAL','invoice',inv.id,data.reference);db.commit();return invoice_data(inv,db)

@app.post('/api/invoices/{invoice_id}/refunds',status_code=201)
def record_refund(invoice_id:str,data:RefundInput,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        inv=db.get(InvoiceRecord,invoice_id)
        if not inv:raise HTTPException(404,'Invoice missing')
        if inv.status=='Draft':raise HTTPException(409,'Invoice must be issued')
        if db.scalar(select(InvoiceRefund.id).where(InvoiceRefund.reference==data.reference)):raise HTTPException(409,'Refund reference already exists')
        ledger=invoice_ledger(db,inv)
        if data.amount>ledger['credit_balance']:raise HTTPException(422,'Refund exceeds customer credit balance; issue a credit memo first')
        db.add(InvoiceRefund(invoice_id=inv.id,reference=data.reference,amount=data.amount,reason=data.reason,refunded_on=data.refunded_on,created_by=user['id']))
        db.flush();refresh_invoice_status(inv,invoice_ledger(db,inv))
        log(db,'REFUND','invoice',inv.id,data.reference);db.commit();return invoice_data(inv,db)

@app.get('/api/invoices/{invoice_id}/transactions')
def invoice_transactions(invoice_id:str,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        inv=db.get(InvoiceRecord,invoice_id)
        if not inv:raise HTTPException(404,'Invoice missing')
        return {
            'payments':[{'id':p.id,'amount':float(p.amount),'date':p.paid_on.isoformat(),'reference':p.reference} for p in db.scalars(select(InvoicePayment).where(InvoicePayment.invoice_id==inv.id).order_by(InvoicePayment.paid_on))],
            'credits':[{'number':x.number,'amount':float(x.amount),'reason':x.reason,'date':x.credited_on.isoformat()} for x in db.scalars(select(InvoiceCredit).where(InvoiceCredit.invoice_id==inv.id))],
            'refunds':[{'reference':x.reference,'amount':float(x.amount),'reason':x.reason,'date':x.refunded_on.isoformat()} for x in db.scalars(select(InvoiceRefund).where(InvoiceRefund.invoice_id==inv.id))],
            'reversals':[{'payment_id':x.payment_id,'reference':x.reference,'amount':float(x.amount),'reason':x.reason,'date':x.reversed_on.isoformat()} for x in db.scalars(select(PaymentReversal).join(InvoicePayment,PaymentReversal.payment_id==InvoicePayment.id).where(InvoicePayment.invoice_id==inv.id))],
        }

@app.get('/api/jobs/{job_number}/financial-overview')
def financial_overview(job_number:str,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        j=financial_job(db,job_number)
        inv=list(db.scalars(select(InvoiceRecord).where(InvoiceRecord.job_id==j.id)))
        billed=sum((invoice_ledger(db,i)['net_billed'] for i in inv if i.status!='Draft'),Decimal('0'))
        payments=sum((invoice_ledger(db,i)['collected'] for i in inv if i.status!='Draft'),Decimal('0'))
        approvals=list(db.scalars(select(TimeApproval).join(FieldTimeEntry,FieldTimeEntry.id==TimeApproval.time_entry_id).join(WorkOrder,WorkOrder.id==FieldTimeEntry.work_order_id).where(WorkOrder.job_id==j.id)))
        field=sum((a.labor_cost+a.mileage_cost for a in approvals),Decimal('0'))+approved_call_mileage_cost(db,j.id)
        purchasing=job_purchase_totals(db,j)
        cost=cents(j.historical_labor_cost+j.historical_material_cost+j.historical_mileage_cost+field+purchasing['actual'])
        commitment=purchasing['committed']
        return {'job_number':j.job_number,'billed':float(billed),'collected':float(payments),'receivable':float(billed-payments),
                'historical_and_approved_actual_cost':float(cost),'new_po_actual':float(purchasing['actual']),
                'open_new_po_commitment':float(commitment),'projected_cost':float(cost+commitment),
                'estimated_contract_profit':float(j.contract_amount-cost-commitment),
                'notes':'Historical material totals include legacy purchase orders; imported PO amounts are excluded from new actual costs. Contract amounts may not equal billed revenue.'}
@app.get('/financials')
def financials_page():return FileResponse(ROOT/'financials.html')
@app.get('/financials.js')
def financials_script():return FileResponse(ROOT/'financials.js',media_type='application/javascript')

# v0.9 billing visibility and customer invoice documents.
from html import escape as html_escape
from fastapi.responses import HTMLResponse

def aging_for_invoice(inv,ledger,as_of):
    remaining=ledger['balance']
    if not remaining:return 'current'
    if not inv.due_on or inv.due_on>=as_of:return 'current'
    days=(as_of-inv.due_on).days
    return '1_30' if days<=30 else '31_60' if days<=60 else '61_90' if days<=90 else '90_plus'

def customer_bill_summary(db, customer, as_of=None):
    as_of=as_of or date.today()
    jobs=list(db.scalars(select(Job).where(Job.customer_id==customer.id)))
    ids=[j.id for j in jobs]
    inv=list(db.scalars(select(InvoiceRecord).where(InvoiceRecord.job_id.in_(ids)))) if ids else []
    issued=[i for i in inv if i.status!='Draft' and (i.issued_on is None or i.issued_on<=as_of)]
    billed=collected=overdue=customer_credits=Decimal('0')
    aging={key:Decimal('0') for key in ('current','1_30','31_60','61_90','90_plus')}
    for i in issued:
        x=invoice_ledger(db,i,as_of)
        billed+=x['net_billed'];collected+=x['collected'];customer_credits+=x['credit_balance']
        bucket=aging_for_invoice(i,x,as_of)
        aging[bucket]+=x['balance']
        if bucket!='current':overdue+=x['balance']
    return {'customer_id':customer.id,'name':customer.name,'job_count':len(jobs),'invoice_count':len(issued),
       'total_billed':float(billed),'total_collected':float(collected),'outstanding':float(sum(aging.values())),
       'customer_credit_balance':float(customer_credits),'overdue':float(overdue),
       'aging':{k:float(v) for k,v in aging.items()},'as_of':as_of.isoformat(),
       'historical_contract_total':float(sum((j.contract_amount for j in jobs),Decimal('0')))}

@app.get('/api/billing/customers')
def billing_customers(user=Depends(require('view_financials'))):
    with Session(engine) as db:
        return [customer_bill_summary(db,c) for c in db.scalars(select(Customer).order_by(Customer.name))]

@app.get('/api/billing/customers/{customer_id}')
def billing_customer(customer_id:str,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        c=db.get(Customer,customer_id)
        if not c: raise HTTPException(404,'Customer not found')
        info=customer_bill_summary(db,c)
        jobs=list(db.scalars(select(Job).where(Job.customer_id==customer_id)))
        info['jobs']=[{'job_number':j.job_number,'description':j.description,'status':j.status,'contract_amount':float(j.contract_amount)} for j in jobs]
        ids=[j.id for j in jobs]
        info['invoices']=[invoice_data(i,db) for i in db.scalars(select(InvoiceRecord).where(InvoiceRecord.job_id.in_(ids)).order_by(InvoiceRecord.number))] if ids else []
        return info

@app.get('/api/billing/summary')
def billing_summary(as_of:Optional[date]=None,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        summary=[customer_bill_summary(db,c,as_of) for c in db.scalars(select(Customer))]
        totals={k:round(sum(s[k] for s in summary),2) for k in ('total_billed','total_collected','outstanding','overdue','customer_credit_balance')}
        totals['aging']={k:round(sum(s['aging'][k] for s in summary),2) for k in ('current','1_30','31_60','61_90','90_plus')}
        totals['as_of']=(as_of or date.today()).isoformat()
        totals['note']='CRM-issued invoices only; historical Wave billing not yet reconciled. Credits and refunds are included.'
        return totals

@app.get('/api/billing/aging')
def billing_aging(as_of:Optional[date]=None,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        all_inv=list(db.scalars(select(InvoiceRecord).where(InvoiceRecord.status!='Draft')))
        rows=[]
        for inv in all_inv:
            if inv.issued_on and inv.issued_on>(as_of or date.today()):continue
            x=invoice_ledger(db,inv,as_of or date.today())
            if not x['balance']:continue
            job=db.get(Job,inv.job_id); customer=db.get(Customer,job.customer_id)
            rows.append({'invoice':inv.number,'customer':customer.name,'job_number':job.job_number,'due_on':inv.due_on.isoformat() if inv.due_on else None,
                'balance':float(x['balance']),'aging_bucket':aging_for_invoice(inv,x,as_of or date.today())})
        return {'as_of':(as_of or date.today()).isoformat(),'invoices':sorted(rows,key=lambda x:(x['aging_bucket'],x['customer'],x['invoice']))}

@app.get('/api/invoices/{invoice_id}/document',response_class=HTMLResponse)
def invoice_document(invoice_id:str,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        inv=db.get(InvoiceRecord,invoice_id)
        if not inv:raise HTTPException(404,'Invoice not found')
        from .billing_v111 import freeze_invoice
        from types import SimpleNamespace
        j=db.get(Job,inv.job_id);snapshot=freeze_invoice(db,inv);customer=SimpleNamespace(**snapshot['customer']);commercial=snapshot['commercial']
        service_location=SimpleNamespace(**snapshot['site']) if snapshot['site'] else None
        service_html=('<h2>Service Location</h2><p>'+html_escape(service_location.name)+'<br>'+html_escape(service_location.address)+'</p>') if service_location else ''
        ledger=invoice_ledger(db,inv)
        e=lambda v:html_escape(str(v if v is not None else ''))
        fmt=lambda n:'${:,.2f}'.format(n)
        lines=invoice_lines(db,inv)
        if lines:
            rows=''.join('<tr><td>'+e(line.description)+'</td><td class="right">'+e(line.quantity)+'</td><td class="right">'+fmt(line.unit_price)+'</td><td class="right">'+e(line.tax_rate)+'%</td><td class="right">'+fmt(line.tax_amount)+'</td><td class="right">'+fmt(line.subtotal+line.tax_amount)+'</td></tr>' for line in lines)
            subtotal=sum((x.subtotal for x in lines),Decimal('0'))
            tax=sum((x.tax_amount for x in lines),Decimal('0'))
        else:
            rows='<tr><td>'+e(j.description or 'HVAC services')+'</td><td class="right">1</td><td class="right">'+fmt(inv.amount)+'</td><td class="right">0%</td><td class="right">$0.00</td><td class="right">'+fmt(inv.amount)+'</td></tr>'
            subtotal=inv.amount;tax=Decimal('0')
        commercial_html='<p>Customer PO: '+e(commercial.get('customer_po',''))+'<br>Service dates: '+e(commercial.get('service_from',''))+' — '+e(commercial.get('service_to',''))+'</p><p>'+e(commercial.get('note',''))+'</p>'
        remit_html='<p>Make checks payable to '+e(commercial.get('remit_to',''))+'<br>'+e(commercial.get('remit_address',''))+'<br>'+e(commercial.get('payment_terms',''))+'<br>'+e(commercial.get('reply_to',''))+'</p>'
        totals=''.join('<div><span>'+e(label)+'</span><strong>'+fmt(value)+'</strong></div>' for label,value in [
          ('Subtotal',subtotal),('Tax',tax),('Invoice total',inv.amount),('Credits applied',-ledger['credits']),
          ('Net billed',ledger['net_billed']),('Net collected',ledger['collected']),('Balance due',ledger['balance'])])
        if ledger['credit_balance']:totals+='<div><span>Customer credit to refund</span><strong>'+fmt(ledger['credit_balance'])+'</strong></div>'
        return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Invoice {e(inv.number)}</title><style>
        body{{margin:0;background:#eaf1f2;font:15px Arial,sans-serif;color:#20363b}}main{{max-width:870px;margin:32px auto;background:white;padding:45px;box-shadow:0 8px 30px #2342}}
        header{{border-bottom:5px solid #176f77;padding-bottom:22px;display:flex;justify-content:space-between;gap:22px}}h1{{margin:0;color:#176f77;font-size:24px}}h2{{font-size:17px}}.muted{{color:#63777b}}
        .grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:30px 0}}.table-scroll{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;margin-top:24px;min-width:620px}}td,th{{text-align:left;border-bottom:1px solid #dce7e8;padding:12px 8px}}th{{background:#e6f1f1}}.right{{text-align:right}}
        .totals{{margin:25px 0 15px auto;max-width:345px}}.totals div{{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #e2e9ea}}.totals div:last-child{{border-bottom:3px solid #176f77}}
        button{{background:#176f77;color:white;border:0;border-radius:6px;padding:12px 18px;cursor:pointer}}@media print{{body{{background:white}}main{{margin:0;padding:18px;box-shadow:none}}button{{display:none}}}}@media(max-width:650px){{main{{margin:0;padding:24px 16px}}header{{flex-wrap:wrap}}.grid{{grid-template-columns:1fr}}}}</style></head><body><main>
        <header><div><h1>LIMITLESS MECHANICAL</h1><div>Services, Inc.</div><p class="muted">167 Falling Branch Rd<br>Waco, KY 40385<br>(859) 979-0081</p></div><div><h1>INVOICE</h1><strong>{e(inv.number)}</strong><br>{e(inv.status)}</div></header>
        <div class="grid"><div><h2>Bill To</h2><strong>{e(customer.name)}</strong><p>{e(customer.billing_address)}</p>{service_html}</div><div><h2>Invoice Details</h2>Job: {e(j.job_number)}<br>Issued: {e(inv.issued_on or 'Draft')}<br>Due: {e(inv.due_on or 'Not specified')}</div></div>
        {commercial_html}<div class="table-scroll"><table><thead><tr><th>Description</th><th class="right">Qty</th><th class="right">Unit</th><th class="right">Tax rate</th><th class="right">Tax</th><th class="right">Amount</th></tr></thead><tbody>{rows}</tbody></table></div>
        <div class="totals">{totals}</div>{remit_html}<p class="muted">Please reference invoice {e(inv.number)} with payment.</p><button onclick="window.print()">Print / Save PDF</button></main></body></html>''')

@app.get('/billing')
def billing_page():return FileResponse(ROOT/'billing.html')
@app.get('/billing.js')
def billing_script():return FileResponse(ROOT/'billing.js',media_type='application/javascript')

# v1.0: direct invoice PDF email through configured Google Workspace OAuth credentials.
# This application has no live provider connection until the operator configures OAuth.
from fastapi.responses import StreamingResponse
import io as _io
from .invoice_mail import make_invoice_pdf, gmail_send, configured as gmail_configured

class InvoiceEmailInput(BaseModel):
    recipient:str=Field(min_length=5,max_length=250)
    subject:str=Field(min_length=1,max_length=250)
    message:str=Field(min_length=1,max_length=5000)
    confirm_resend:bool=False
    @field_validator('recipient')
    @classmethod
    def validate_recipient(cls,v):
        v=v.strip()
        if '\n' in v or '\r' in v or not re.fullmatch(r'[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+',v):raise ValueError('Single valid email required')
        return v

class InvoiceEmailEvent(Base):
    __tablename__='crm_invoice_email_events'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    invoice_id:Mapped[str]=mapped_column(ForeignKey('crm_invoices.id',ondelete='RESTRICT'),nullable=False,index=True)
    recipient:Mapped[str]=mapped_column(String(250),nullable=False)
    subject:Mapped[str]=mapped_column(String(250),nullable=False)
    sent_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    gmail_message_id:Mapped[str]=mapped_column(String(250),nullable=False)
    sent_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

Base.metadata.create_all(engine)

def _invoice_pdf(db,invoice):
    job=db.get(Job,invoice.job_id)
    customer=db.get(Customer,job.customer_id)
    ledger=invoice_ledger(db,invoice)
    from .billing_v111 import freeze_invoice
    from types import SimpleNamespace
    frozen=freeze_invoice(db,invoice)
    return make_invoice_pdf(invoice,job,SimpleNamespace(**frozen['customer']),ledger['collected'],invoice_lines(db,invoice),ledger,service_location=SimpleNamespace(**frozen['site']) if frozen['site'] else None,commercial=frozen['commercial'])

@app.get('/api/billing/email/status')
def invoice_email_status(user=Depends(require('view_financials'))):
    from .billing_v111 import settings
    with Session(engine) as db:preview=settings(db)['values']['test_mode']
    return {'configured':gmail_configured(),'sender':os.environ.get('LMS_GMAIL_SENDER','') if gmail_configured() else '', 'preview_mode':preview}

@app.get('/api/invoices/{invoice_id}/pdf')
def invoice_pdf(invoice_id:str,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        invoice=db.get(InvoiceRecord,invoice_id)
        if not invoice:raise HTTPException(404,'Invoice not found')
        payload=_invoice_pdf(db,invoice)
        return StreamingResponse(_io.BytesIO(payload),media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="Invoice-{invoice.number}.pdf"','Cache-Control':'no-store'})

@app.get('/api/invoices/{invoice_id}/email-history')
def email_history(invoice_id:str,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        if not db.get(InvoiceRecord,invoice_id):raise HTTPException(404,'Invoice not found')
        records=db.scalars(select(InvoiceEmailEvent).where(InvoiceEmailEvent.invoice_id==invoice_id).order_by(InvoiceEmailEvent.sent_at.desc())).all()
        return [{'recipient':x.recipient,'subject':x.subject,'sent_at':x.sent_at.isoformat(),'gmail_message_id':x.gmail_message_id} for x in records]

@app.post('/api/invoices/{invoice_id}/send-email')
def send_invoice_email(invoice_id:str,payload:InvoiceEmailInput,user=Depends(require('view_financials'))):
    from .billing_v111 import enqueue,process_mail,mail_payload
    with Session(engine) as db:
        invoice=db.scalar(select(InvoiceRecord).where(InvoiceRecord.id==invoice_id).with_for_update())
        if not invoice:raise HTTPException(404,'Invoice not found')
        previous=db.scalar(select(InvoiceEmailEvent).where(InvoiceEmailEvent.invoice_id==invoice.id,InvoiceEmailEvent.recipient==payload.recipient))
        if previous and not payload.confirm_resend:raise HTTPException(409,'Invoice already emailed; explicitly confirm a resend')
        row=enqueue(db,invoice,user,str(uuid.uuid4()),payload.recipient,payload.subject,payload.message,payload.confirm_resend)
        ident=row.id;db.commit()
    result=process_mail(ident)
    return {'status':result['status'].lower(),'message_id':result['provider_id'],'recipient':result['recipient'],'outbox_id':ident}



# v1.1: customer statement as an independent billing document.
def _customer_statement_pdf(db, customer):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import simpleSplit
    buf=_io.BytesIO()
    c=canvas.Canvas(buf,pagesize=letter)
    W,H=letter
    jobs=list(db.scalars(select(Job).where(Job.customer_id==customer.id)))
    job_map={j.id:j.job_number for j in jobs}
    invoices=list(db.scalars(select(InvoiceRecord).where(InvoiceRecord.job_id.in_(list(job_map)),InvoiceRecord.status!='Draft').order_by(InvoiceRecord.issued_on,InvoiceRecord.number))) if jobs else []
    due_total=Decimal('0'); billed=Decimal('0'); collected=Decimal('0')
    rows=[]
    for inv in invoices:
        ledger=invoice_ledger(db,inv)
        paid=ledger['collected'];due=ledger['balance']
        billed+=ledger['net_billed'];collected+=paid;due_total+=due
        rows.append((inv.number,job_map[inv.job_id],str(inv.issued_on or ''),str(inv.due_on or ''),ledger['net_billed'],paid,due))
    def header(page):
        c.setFillColor(HexColor('#142830'));c.rect(0,H-103,W,103,fill=1,stroke=0)
        c.setFillColor(HexColor('#73CDD0'));c.setFont('Helvetica-Bold',18);c.drawString(42,H-45,'LIMITLESS MECHANICAL')
        c.setFillColor(HexColor('#FFFFFF'));c.setFont('Helvetica',10);c.drawString(42,H-65,'CUSTOMER ACCOUNT STATEMENT')
        c.setFillColor(HexColor('#294149'));c.setFont('Helvetica',10)
        c.drawString(42,H-128,'Customer: '+customer.name[:85]);c.drawRightString(W-42,H-128,'As of '+date.today().isoformat())
        c.setFont('Helvetica',9)
        for i,line in enumerate(simpleSplit(customer.billing_address or '', 'Helvetica',9,450)[:3]):c.drawString(42,H-145-i*12,line)
        c.setFillColor(HexColor('#176F77'));c.rect(42,H-199,W-84,25,fill=1,stroke=0)
        c.setFillColor(HexColor('#FFFFFF'));c.setFont('Helvetica-Bold',9)
        cols=[(46,'Invoice'),(136,'Job'),(208,'Issued'),(272,'Due'),(387,'Billed'),(458,'Paid'),(562,'Balance')]
        for x,text in cols:c.drawRightString(x,H-190,text) if x>300 else c.drawString(x,H-190,text)
        c.setFont('Helvetica',9);c.setFillColor(HexColor('#294149'))
        c.drawRightString(W-42,38,'Page '+str(page))
        return H-216
    y=header(1);page=1
    for number,job,issued,due,amount,paid,balance in rows:
        if y<105:
            c.showPage();page+=1;y=header(page)
        c.setFillColor(HexColor('#263A3F'));c.setFont('Helvetica',8)
        for x,value in [(46,number[:19]),(136,job[:15]),(208,issued),(272,due)]:c.drawString(x,y,value)
        for x,num in [(387,amount),(458,paid),(562,balance)]:c.drawRightString(x,y,'${:,.2f}'.format(num))
        c.setStrokeColor(HexColor('#DFE9EA'));c.line(42,y-9,W-42,y-9)
        y-=26
    if y<132:c.showPage();page+=1;y=header(page)
    c.setFont('Helvetica-Bold',10);c.setFillColor(HexColor('#176F77'))
    for label,num in [('Total invoiced',billed),('Payments received',collected),('Outstanding balance',due_total)]:
        c.drawString(340,y,label);c.drawRightString(562,y,'${:,.2f}'.format(num));y-=21
    if y<150:c.showPage();page+=1;y=header(page)
    buckets={key:Decimal('0') for key in ('current','1_30','31_60','61_90','90_plus')}
    for inv in invoices:
        ledger=invoice_ledger(db,inv);buckets[aging_for_invoice(inv,ledger,date.today())]+=ledger['balance']
    y-=10;c.setFont('Helvetica-Bold',9);c.setFillColor(HexColor('#176F77'));c.drawString(42,y,'RECEIVABLES AGING');y-=17
    for label,key in [('Current','current'),('1-30 days','1_30'),('31-60 days','31_60'),('61-90 days','61_90'),('90+ days','90_plus')]:
        c.drawString(42,y,label);c.drawRightString(190,y,'${:,.2f}'.format(buckets[key]));y-=15
    c.save();return buf.getvalue()

@app.get('/api/billing/customers/{customer_id}/statement.pdf')
def customer_statement_pdf(customer_id:str,user=Depends(require('view_financials'))):
    with Session(engine) as db:
        customer=db.get(Customer,customer_id)
        if not customer:raise HTTPException(404,'Customer not found')
        payload=_customer_statement_pdf(db,customer)
        return StreamingResponse(_io.BytesIO(payload),media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="Limitless-Customer-Statement.pdf"','Cache-Control':'no-store'})

# v1.3 endpoints and job-cost accounting
from .purchasing_v13 import attach_purchasing
attach_purchasing(app,globals())

# v1.4 field documentation and mobile service closeout
from .field_v14 import attach_field, ensure_open as field_report_open
attach_field(app)

# v1.5 - detailed proposals, acceptance, maintenance agreements and scheduled PM visits.
from .proposals_pm_v15 import attach_v15
attach_v15(app)

@app.get('/pm_v15.js')
def pm_v15_script():return FileResponse(ROOT/'pm_v15.js',media_type='application/javascript')

# Technician clock and per-call mileage; additive tables preserve existing v1.6 data.
from .time_clock import attach_time_clock, lock_time_user, ensure_no_time_overlap, time_entry_payload, approved_call_mileage_cost
attach_time_clock(app)

# Owner-only parts catalog and per-call margin controls.
from .call_pricing import attach_call_pricing, validate_invoice_margin as validate_pricing_invoice_margin
attach_call_pricing(app)

from .productivity import attach_productivity
attach_productivity(app)
from .call_field_notes import attach_call_field_notes
attach_call_field_notes(app)

from .time_corrections import attach_time_corrections
attach_time_corrections(app)

from .admin_metrics import attach_admin_metrics
attach_admin_metrics(app)

from .profit_tracking import attach_profit_tracking
attach_profit_tracking(app)

from .operations_v110 import attach_operations
attach_operations(app)

from .accountant_export import attach_accountant_export
attach_accountant_export(app)

from .billing_v111 import attach_billing
attach_billing(app)

from .field_v111 import attach_field_improvements
attach_field_improvements(app)

from .customer_v111 import attach_customer_workflows
attach_customer_workflows(app)

from .business_overview import attach_overview
attach_overview(app)
