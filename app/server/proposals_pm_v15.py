"""Limitless CRM v1.5: versioned estimates, printable PDF, office-recorded acceptance,
maintenance agreements, idempotent PM scheduling and deficiency-to-estimate conversion.
Designed for a LOCAL development server. Real customer e-sign/automated dispatch is not implemented.
"""
from __future__ import annotations
import hashlib
import io
import json
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import String, Text, Date, DateTime, Numeric, ForeignKey, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column, Session
from . import main as m
from .field_v14 import FieldReport
from .quote_template import PresentationIn,presentation,save_presentation,document_context


def now(): return datetime.now(timezone.utc)
def money(v): return Decimal(str(v)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
def identifier(): return str(uuid.uuid4())

class QuoteDetail(m.Base):
    __tablename__='quote_details_v15'
    proposal_id:Mapped[str]=mapped_column(ForeignKey('proposals.id',ondelete='RESTRICT'),primary_key=True)
    inclusions:Mapped[str]=mapped_column(Text,default='')
    exclusions:Mapped[str]=mapped_column(Text,default='')
    warranty:Mapped[str]=mapped_column(Text,default='')
    payment_terms:Mapped[str]=mapped_column(Text,default='Net 30')
    project_timeline:Mapped[str]=mapped_column(Text,default='')
    source_type:Mapped[Optional[str]]=mapped_column(String(25),nullable=True)
    source_id:Mapped[Optional[str]]=mapped_column(String(36),nullable=True)
    options_json:Mapped[str]=mapped_column(Text,nullable=False)
    revision:Mapped[int]=mapped_column(default=1,nullable=False)
    selected_option:Mapped[Optional[int]]=mapped_column(nullable=True)
    acceptance_name:Mapped[str]=mapped_column(String(250),default='')
    acceptance_reference:Mapped[str]=mapped_column(String(250),default='')
    accepted_at:Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True),nullable=True)
    recorded_by:Mapped[Optional[str]]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=True)
    # Snapshot to preserve exactly what was accepted, even if future software changes.
    accepted_snapshot:Mapped[str]=mapped_column(Text,default='')
    __table_args__=(UniqueConstraint('source_type','source_id',name='uq_quote_source_v15'),)

class QuoteRevision(m.Base):
    __tablename__='quote_revisions_v15'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=identifier)
    proposal_id:Mapped[str]=mapped_column(ForeignKey('proposals.id',ondelete='RESTRICT'),nullable=False)
    version:Mapped[int]=mapped_column(nullable=False)
    snapshot_json:Mapped[str]=mapped_column(Text,nullable=False)
    sha256:Mapped[str]=mapped_column(String(64),nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    __table_args__=(UniqueConstraint('proposal_id','version',name='uq_quote_version_v15'),)

class PMAgreement(m.Base):
    __tablename__='pm_agreements_v15'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=identifier)
    number:Mapped[str]=mapped_column(String(70),unique=True,nullable=False)
    customer_id:Mapped[str]=mapped_column(ForeignKey('customers.id',ondelete='RESTRICT'),nullable=False)
    start_date:Mapped[date]=mapped_column(Date,nullable=False)
    end_date:Mapped[date]=mapped_column(Date,nullable=False)
    amount:Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    terms:Mapped[str]=mapped_column(Text,default='')
    notes:Mapped[str]=mapped_column(Text,default='')
    status:Mapped[str]=mapped_column(String(20),default='Draft',nullable=False)
    renewal_notice_days:Mapped[int]=mapped_column(default=30,nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class PMCoverage(m.Base):
    __tablename__='pm_agreement_coverage_v15'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=identifier)
    agreement_id:Mapped[str]=mapped_column(ForeignKey('pm_agreements_v15.id',ondelete='RESTRICT'),nullable=False)
    equipment_id:Mapped[str]=mapped_column(ForeignKey('equipment.id',ondelete='RESTRICT'),nullable=False)
    plan_id:Mapped[str]=mapped_column(ForeignKey('pm_plans.id',ondelete='RESTRICT'),nullable=False)
    job_id:Mapped[str]=mapped_column(ForeignKey('jobs.id',ondelete='RESTRICT'),nullable=False)
    __table_args__=(UniqueConstraint('agreement_id','equipment_id',name='uq_pm_coverage_asset_v15'),)

class OptionIn(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    description:str=Field(default='',max_length=4000)
    price:Decimal=Field(gt=0,max_digits=14,decimal_places=2)
    estimated_cost:Decimal=Field(default=Decimal('0'),ge=0,max_digits=14,decimal_places=2)

class QuoteIn(BaseModel):
    presentation:PresentationIn|None=None
    job_number:str=Field(min_length=3,max_length=40)
    number:str=Field(min_length=2,max_length=70)
    title:str=Field(min_length=2,max_length=250)
    scope:str=Field(min_length=1,max_length=20000)
    inclusions:str=Field(default='',max_length=15000)
    exclusions:str=Field(default='',max_length=15000)
    warranty:str=Field(default='',max_length=10000)
    payment_terms:str=Field(default='Net 30',max_length=3000)
    project_timeline:str=Field(default='',max_length=3000)
    options:list[OptionIn]=Field(min_length=1,max_length=5)

class QuoteEdit(BaseModel):
    presentation:PresentationIn|None=None
    title:str=Field(min_length=2,max_length=250)
    scope:str=Field(min_length=1,max_length=20000)
    inclusions:str=Field(default='',max_length=15000)
    exclusions:str=Field(default='',max_length=15000)
    warranty:str=Field(default='',max_length=10000)
    payment_terms:str=Field(default='Net 30',max_length=3000)
    project_timeline:str=Field(default='',max_length=3000)
    options:list[OptionIn]=Field(min_length=1,max_length=5)
    expected_revision:int=Field(ge=1)

class AcceptQuote(BaseModel):
    selected_option:int=Field(ge=0,le=4)
    accepted_by:str=Field(min_length=2,max_length=250)
    reference:str=Field(min_length=3,max_length=250)
    # Office verifies the actual customer authorization outside the CRM.
    customer_authorization_confirmed:bool=False

class FromWorkOrder(BaseModel):
    work_order_id:str
    number:str=Field(min_length=2,max_length=70)
    title:str=Field(default='Recommended HVAC Repairs',min_length=2,max_length=250)
    price:Decimal=Field(gt=0,max_digits=14,decimal_places=2)
    estimated_cost:Decimal=Field(default=Decimal('0'),ge=0,max_digits=14,decimal_places=2)

class CoveredAsset(BaseModel):
    equipment_id:str
    job_number:str
    interval_days:int=Field(ge=7,le=730)
    first_due:date
    checklist:list[str]=Field(min_length=1,max_length=30)
    existing_plan_id:Optional[str]=None
    @field_validator('checklist')
    @classmethod
    def items_ok(cls,items):
        if any(not x.strip() or len(x)>300 for x in items) or len(items)!=len(set(items)):
            raise ValueError('Checklist items must be distinct, nonblank and no longer than 300 characters')
        return items

class AgreementIn(BaseModel):
    number:str=Field(min_length=2,max_length=70)
    customer_id:str
    start_date:date
    end_date:date
    amount:Decimal=Field(ge=0,max_digits=14,decimal_places=2)
    terms:str=Field(default='Net 30',max_length=3000)
    notes:str=Field(default='',max_length=5000)
    renewal_notice_days:int=Field(default=30,ge=0,le=365)
    coverage:list[CoveredAsset]=Field(min_length=1,max_length=100)

class ScheduleIn(BaseModel):
    through_date:date

class FromPMIn(BaseModel):
    number:str=Field(min_length=2,max_length=70)
    title:str=Field(default='PM Deficiency Repairs',min_length=2,max_length=250)
    price:Decimal=Field(gt=0,max_digits=14,decimal_places=2)
    estimated_cost:Decimal=Field(default=Decimal('0'),ge=0,max_digits=14,decimal_places=2)


def option_dict(o):
    return {'name':o.name.strip(),'description':o.description.strip(),
            'price':str(money(o.price)),'estimated_cost':str(money(o.estimated_cost))}

def snapshot(db,quote,detail):
    return {'number':quote.number,'title':quote.title,'job_number':db.get(m.Job,quote.job_id).job_number,
            'scope':quote.scope,'inclusions':detail.inclusions,'exclusions':detail.exclusions,
            'warranty':detail.warranty,'payment_terms':detail.payment_terms,
            'project_timeline':detail.project_timeline,'options':json.loads(detail.options_json),
            'revision':detail.revision,'presentation':presentation(db,quote.id),'document_context':document_context(db,quote)}

def add_revision(db,quote,detail):
    obj=snapshot(db,quote,detail)
    serialized=json.dumps(obj,sort_keys=True,ensure_ascii=False)
    db.add(QuoteRevision(proposal_id=quote.id,version=detail.revision,
                         snapshot_json=serialized,sha256=hashlib.sha256(serialized.encode()).hexdigest()))

def quote_out(db,quote):
    detail=db.get(QuoteDetail,quote.id)
    payload=m.proposal_data(quote,db)
    if detail:
        payload.update(snapshot(db,quote,detail))
        payload.update({'selected_option':detail.selected_option,'accepted_by':detail.acceptance_name,
                        'acceptance_reference':detail.acceptance_reference,
                        'accepted_at':detail.accepted_at.isoformat() if detail.accepted_at else None,
                        'source_type':detail.source_type,'source_id':detail.source_id,
                        'can_invoice':quote.status=='Approved'})
    return payload

def _create_quote(db, user, data,source_type=None,source_id=None,commit=True):
    job=db.scalar(select(m.Job).where(m.Job.job_number==data.job_number))
    if not job:raise HTTPException(422,'Job not found')
    if db.scalar(select(m.Proposal.id).where(m.Proposal.number==data.number)):
        raise HTTPException(409,'Proposal number already exists')
    if source_id and db.scalar(select(QuoteDetail.proposal_id).where(QuoteDetail.source_type==source_type,QuoteDetail.source_id==source_id)):
        raise HTTPException(409,'Estimate already exists for this source')
    quote=m.Proposal(job_id=job.id,number=data.number,title=data.title,scope=data.scope,
                      total=money(data.options[0].price))
    db.add(quote);db.flush()
    detail=QuoteDetail(proposal_id=quote.id,inclusions=data.inclusions,exclusions=data.exclusions,
                       warranty=data.warranty,payment_terms=data.payment_terms,
                       project_timeline=data.project_timeline,
                       options_json=json.dumps([option_dict(x) for x in data.options]),
                       source_type=source_type,source_id=source_id)
    db.add(detail);db.flush();save_presentation(db,quote.id,data.presentation or PresentationIn(),data.options);add_revision(db,quote,detail)
    m.log(db,'CREATE_ADVANCED','proposal',quote.id,source_type or '')
    if commit:db.commit()
    return quote_out(db,quote)


def proposal_pdf(db,quote,detail):
    # Every freshly built customer proposal uses the same retained layout.
    from .quote_template import render,legacy_detail
    return render(db,quote,detail or legacy_detail(quote))


def agreement_out(db,a):
    customer=db.get(m.Customer,a.customer_id)
    covered=[]
    for c in db.scalars(select(PMCoverage).where(PMCoverage.agreement_id==a.id)):
        equipment=db.get(m.Equipment,c.equipment_id);plan=db.get(m.PMPlan,c.plan_id);job=db.get(m.Job,c.job_id)
        covered.append({'equipment_id':c.equipment_id,'asset_tag':equipment.asset_tag,'job_number':job.job_number,
                        'plan_id':c.plan_id,'interval_days':plan.interval_days,
                        'next_due':plan.next_due.isoformat(),'checklist':json.loads(plan.checklist)})
    return {'id':a.id,'number':a.number,'customer':customer.name,'customer_id':a.customer_id,
            'start_date':a.start_date.isoformat(),'end_date':a.end_date.isoformat(),
            'annual_amount':float(a.amount),'status':a.status,'terms':a.terms,'notes':a.notes,
            'renewal_notice_days':a.renewal_notice_days,'coverage':covered,
            'days_to_renewal':(a.end_date-date.today()).days}


def schedule_agreement(db,a,through):
    if a.status!='Active':raise HTTPException(409,'Only active PM agreements can generate visits')
    if through<a.start_date: return {'created':[],'skipped':[]}
    created=[];skipped=[]
    for coverage in db.scalars(select(PMCoverage).where(PMCoverage.agreement_id==a.id)):
        p=db.get(m.PMPlan,coverage.plan_id)
        if not p.active:continue
        due=p.next_due
        if due>min(through,a.end_date) or due<a.start_date:continue
        existing=db.scalar(select(m.PMVisit).where(m.PMVisit.plan_id==p.id,m.PMVisit.scheduled_due==due))
        if existing:
            skipped.append({'plan_id':p.id,'due':due.isoformat(),'reason':'already scheduled'});continue
        # Do not fabricate jobs or duplicate uncompleted PM visits.
        pending=db.scalars(select(m.PMVisit).where(m.PMVisit.plan_id==p.id)).all()
        if any(not db.scalar(select(m.PMInspection.id).where(m.PMInspection.visit_id==v.id)) for v in pending):
            skipped.append({'plan_id':p.id,'due':due.isoformat(),'reason':'previous visit awaiting inspection'});continue
        job=db.get(m.Job,coverage.job_id)
        equipment=db.get(m.Equipment,coverage.equipment_id)
        loc=db.get(m.Location,equipment.location_id)
        if not job or job.customer_id!=a.customer_id or job.location_id!=loc.id:
            raise HTTPException(422,'Agreement coverage job no longer matches its equipment location')
        w=m.WorkOrder(job_id=job.id,summary=f'PM {a.number}: {equipment.asset_tag} — due {due.isoformat()}',priority='Normal',status='Open')
        db.add(w);db.flush();db.add(m.WorkOrderEquipment(work_order_id=w.id,equipment_id=equipment.id))
        visit=m.PMVisit(plan_id=p.id,work_order_id=w.id,scheduled_due=due)
        db.add(visit);db.flush();m.log(db,'GENERATE_AGREEMENT_PM','pm_visit',visit.id,a.number)
        created.append({'visit_id':visit.id,'work_order_id':w.id,'asset_tag':equipment.asset_tag,'job_number':job.job_number,'due':due.isoformat()})
    return {'created':created,'skipped':skipped}


def attach_v15(app):
    # Importing this module registers tables; create_all adds tables without rewriting existing financial data.
    m.Base.metadata.create_all(m.engine)
    @app.get('/api/quote-templates')
    def quote_templates(user=Depends(m.require('view_financials'))):
        return {'default':'limitless','templates':[{'id':'limitless','name':'!limitless - Primary proposal','description':'Retained Limitless layout, logo, numbered sections, equipment schedule, payment cards and acceptance.'}]}

    @app.get('/api/proposals/{proposal_id}/pdf')
    def any_proposal_pdf(proposal_id:str,user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            quote=db.get(m.Proposal,proposal_id)
            if not quote:raise HTTPException(404,'Proposal not found')
            payload=proposal_pdf(db,quote,db.get(QuoteDetail,proposal_id))
            return StreamingResponse(io.BytesIO(payload),media_type='application/pdf',headers={'Cache-Control':'no-store','Content-Disposition':'attachment; filename="Limitless-Proposal.pdf"'})

    @app.post('/api/estimates/advanced',status_code=201)
    def create_advanced(data:QuoteIn,user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:return _create_quote(db,user,data)

    @app.put('/api/estimates/advanced/{proposal_id}')
    def edit_advanced(proposal_id:str,data:QuoteEdit,user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            quote=db.get(m.Proposal,proposal_id);detail=db.get(QuoteDetail,proposal_id)
            if not quote or not detail:raise HTTPException(404,'Advanced estimate not found')
            if quote.status!='Draft':raise HTTPException(409,'Accepted proposals are immutable; create a new estimate')
            if detail.revision!=data.expected_revision:raise HTTPException(409,'Stale revision; reload before saving')
            from .call_pricing import validate_quote_margin
            validate_quote_margin(db,quote,data.options)
            quote.title=data.title;quote.scope=data.scope;quote.total=money(data.options[0].price)
            for attr in ('inclusions','exclusions','warranty','payment_terms','project_timeline'):setattr(detail,attr,getattr(data,attr))
            save_presentation(db,quote.id,data.presentation,data.options)
            detail.options_json=json.dumps([option_dict(x) for x in data.options]);detail.revision+=1
            db.flush();add_revision(db,quote,detail);m.log(db,'REVISE','proposal',quote.id,str(detail.revision));db.commit()
            return quote_out(db,quote)

    @app.get('/api/estimates/advanced')
    def list_advanced(user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            quotes=db.scalars(select(m.Proposal).join(QuoteDetail,QuoteDetail.proposal_id==m.Proposal.id).order_by(m.Proposal.created_at.desc())).all()
            return [quote_out(db,p) for p in quotes]

    @app.get('/api/estimates/advanced/{proposal_id}')
    def get_advanced(proposal_id:str,user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            quote=db.get(m.Proposal,proposal_id)
            if not quote or not db.get(QuoteDetail,proposal_id):raise HTTPException(404,'Advanced estimate not found')
            return quote_out(db,quote)

    @app.get('/api/estimates/advanced/{proposal_id}/revisions')
    def revisions(proposal_id:str,user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            if not db.get(QuoteDetail,proposal_id):raise HTTPException(404,'Advanced estimate not found')
            return [{'version':r.version,'snapshot':json.loads(r.snapshot_json),'sha256':r.sha256} for r in db.scalars(select(QuoteRevision).where(QuoteRevision.proposal_id==proposal_id).order_by(QuoteRevision.version))]

    @app.post('/api/estimates/advanced/{proposal_id}/record-acceptance')
    def record_acceptance(proposal_id:str,data:AcceptQuote,user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            quote=db.get(m.Proposal,proposal_id);detail=db.get(QuoteDetail,proposal_id)
            if not quote or not detail:raise HTTPException(404,'Advanced estimate not found')
            if quote.status!='Draft':raise HTTPException(409,'Proposal already accepted or no longer a draft')
            if not data.customer_authorization_confirmed:raise HTTPException(422,'Confirm that the customer actually authorized this proposal')
            opts=json.loads(detail.options_json)
            if data.selected_option>=len(opts):raise HTTPException(422,'Selected option does not exist')
            quote.status='Approved';quote.total=money(opts[data.selected_option]['price'])
            detail.selected_option=data.selected_option;detail.acceptance_name=data.accepted_by.strip()
            detail.acceptance_reference=data.reference.strip();detail.accepted_at=now();detail.recorded_by=user['id']
            detail.accepted_snapshot=json.dumps(snapshot(db,quote,detail),sort_keys=True)
            m.log(db,'RECORD_CUSTOMER_ACCEPTANCE','proposal',quote.id,data.reference)
            db.commit();return quote_out(db,quote)

    @app.get('/api/estimates/advanced/{proposal_id}/pdf')
    def advanced_pdf(proposal_id:str,user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            p=db.get(m.Proposal,proposal_id);d=db.get(QuoteDetail,proposal_id)
            if not p or not d:raise HTTPException(404,'Advanced estimate not found')
            payload=proposal_pdf(db,p,d)
            return StreamingResponse(io.BytesIO(payload),media_type='application/pdf',headers={'Cache-Control':'no-store','Content-Disposition':f'attachment; filename="Limitless-Proposal-{p.number}.pdf"'})

    @app.post('/api/estimates/from-work-order',status_code=201)
    def quote_from_work(data:FromWorkOrder,user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            work=db.get(m.WorkOrder,data.work_order_id)
            if not work:raise HTTPException(404,'Work order not found')
            field=db.get(FieldReport,work.id)
            rec=(field.recommendations or '').strip() if field else ''
            if not rec:raise HTTPException(422,'Technician recommendations must be documented before converting to an estimate')
            job=db.get(m.Job,work.job_id)
            scope='Recommended repairs based on service work order:\n'+rec
            opt=OptionIn(name='Recommended repair',description=rec,price=data.price,estimated_cost=data.estimated_cost)
            q=QuoteIn(job_number=job.job_number,number=data.number,title=data.title,scope=scope,options=[opt],inclusions='Services and materials described in the scope.',exclusions='Work not specifically described is excluded.',warranty='Per written proposal terms.')
            return _create_quote(db,user,q,'work_order',work.id)

    @app.post('/api/pm/agreements',status_code=201)
    def create_agreement(data:AgreementIn,user=Depends(m.require('manage_jobs'))):
        if data.end_date<data.start_date:raise HTTPException(422,'End date precedes start date')
        if len(set(x.equipment_id for x in data.coverage))!=len(data.coverage):raise HTTPException(422,'Equipment cannot be covered twice within an agreement')
        with Session(m.engine) as db:
            if not db.get(m.Customer,data.customer_id):raise HTTPException(422,'Customer not found')
            if db.scalar(select(PMAgreement.id).where(PMAgreement.number==data.number)):raise HTTPException(409,'Agreement number already exists')
            a=PMAgreement(number=data.number,customer_id=data.customer_id,start_date=data.start_date,end_date=data.end_date,
                          amount=money(data.amount),terms=data.terms,notes=data.notes,renewal_notice_days=data.renewal_notice_days)
            db.add(a);db.flush()
            for item in data.coverage:
                equipment=db.get(m.Equipment,item.equipment_id)
                if not equipment:raise HTTPException(422,'Unknown equipment')
                location=db.get(m.Location,equipment.location_id)
                job=db.scalar(select(m.Job).where(m.Job.job_number==item.job_number))
                if location.customer_id!=a.customer_id or not job or job.customer_id!=a.customer_id or job.location_id!=location.id:
                    raise HTTPException(422,'Covered equipment and job must belong to this customer and service location')
                if item.first_due<a.start_date or item.first_due>a.end_date:raise HTTPException(422,'First PM due date must fall within the agreement period')
                if item.existing_plan_id:
                    plan=db.get(m.PMPlan,item.existing_plan_id)
                    if not plan or plan.equipment_id!=equipment.id or not plan.active or plan.interval_days!=item.interval_days:
                        raise HTTPException(422,'Existing PM plan does not match this asset and interval')
                    # Avoid inadvertently overriding a previous plan schedule.
                    if plan.next_due!=item.first_due:raise HTTPException(422,'Existing PM plan next due date differs')
                else:
                    existing=db.scalar(select(m.PMPlan.id).where(m.PMPlan.equipment_id==equipment.id,m.PMPlan.active==True))
                    if existing:raise HTTPException(422,'Asset already has an active PM plan; supply existing_plan_id')
                    plan=m.PMPlan(equipment_id=equipment.id,interval_days=item.interval_days,
                                  next_due=item.first_due,checklist=json.dumps(item.checklist))
                    db.add(plan);db.flush()
                linked=db.scalar(select(PMCoverage.id).join(PMAgreement,PMCoverage.agreement_id==PMAgreement.id).where(
                    PMCoverage.plan_id==plan.id,PMAgreement.status.in_(['Draft','Active'])))
                if linked:raise HTTPException(409,'PM plan already linked to an active or draft agreement')
                db.add(PMCoverage(agreement_id=a.id,equipment_id=equipment.id,plan_id=plan.id,job_id=job.id))
            db.flush();m.log(db,'CREATE_AGREEMENT','pm_agreement',a.id,a.number);db.commit();return agreement_out(db,a)

    @app.get('/api/pm/agreements')
    def list_agreements(user=Depends(m.require('manage_jobs'))):
        with Session(m.engine) as db:return [agreement_out(db,a) for a in db.scalars(select(PMAgreement).order_by(PMAgreement.end_date))]

    @app.get('/api/pm/agreements/renewals')
    def renewals(within_days:int=60,user=Depends(m.require('manage_jobs'))):
        if within_days<0 or within_days>365:raise HTTPException(422,'within_days out of range')
        cutoff=date.today()+timedelta(days=within_days)
        with Session(m.engine) as db:
            rows=db.scalars(select(PMAgreement).where(PMAgreement.status=='Active',PMAgreement.end_date<=cutoff)).all()
            return [agreement_out(db,a) for a in rows]

    @app.post('/api/pm/agreements/{agreement_id}/activate')
    def activate(agreement_id:str,user=Depends(m.require('manage_jobs'))):
        with Session(m.engine) as db:
            a=db.get(PMAgreement,agreement_id)
            if not a:raise HTTPException(404,'Agreement not found')
            if a.status!='Draft':raise HTTPException(409,'Only draft agreements may be activated')
            a.status='Active';m.log(db,'ACTIVATE_AGREEMENT','pm_agreement',a.id);db.commit()
            return agreement_out(db,a)

    @app.post('/api/pm/agreements/{agreement_id}/schedule')
    def schedule(agreement_id:str,data:ScheduleIn,user=Depends(m.require('manage_jobs'))):
        if data.through_date>date.today()+timedelta(days=366):raise HTTPException(422,'Scheduling window exceeds 366 days')
        with Session(m.engine) as db:
            a=db.get(PMAgreement,agreement_id)
            if not a:raise HTTPException(404,'Agreement not found')
            result=schedule_agreement(db,a,data.through_date);db.commit();return result

    @app.post('/api/estimates/from-pm-visit/{visit_id}',status_code=201)
    def quote_from_pm(visit_id:str,data:FromPMIn,user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            visit=db.get(m.PMVisit,visit_id)
            if not visit:raise HTTPException(404,'PM visit not found')
            inspection=db.scalar(select(m.PMInspection).where(m.PMInspection.visit_id==visit.id))
            if not inspection:raise HTTPException(409,'Complete the PM inspection first')
            failed=[item for item,result in json.loads(inspection.responses_json).items() if result=='fail']
            if not failed:raise HTTPException(422,'No failed checklist items to quote')
            work=db.get(m.WorkOrder,visit.work_order_id);job=db.get(m.Job,work.job_id)
            plan=db.get(m.PMPlan,visit.plan_id);asset=db.get(m.Equipment,plan.equipment_id)
            scope=f'PM deficiencies for equipment {asset.asset_tag}:\n'+ '\n'.join('- '+i for i in failed)
            option=OptionIn(name='Deficiency corrective repairs',description=scope,price=data.price,estimated_cost=data.estimated_cost)
            q=QuoteIn(job_number=job.job_number,number=data.number,title=data.title,scope=scope,options=[option],warranty='Warranty applies to listed work only.',exclusions='Unlisted repairs excluded.')
            return _create_quote(db,user,q,'pm_visit',visit.id)

    @app.get('/estimates-workspace')
    def estimates_page():return FileResponse(m.ROOT/'estimates-workspace.html')

    @app.get('/estimates-workspace.js')
    def estimates_script():return FileResponse(m.ROOT/'estimates-workspace.js',media_type='application/javascript')
