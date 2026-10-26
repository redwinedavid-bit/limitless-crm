"""Dashboard proposal tracking; acceptance links to the existing job, never duplicates it."""
from datetime import datetime, timezone
from typing import Literal
from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column, Session
from . import main as m
from .proposals_pm_v15 import QuoteDetail

class ProposalProgress(m.Base):
    __tablename__='proposal_dashboard_progress'
    proposal_id:Mapped[str]=mapped_column(ForeignKey('proposals.id',ondelete='RESTRICT'),primary_key=True)
    stage:Mapped[str]=mapped_column(String(20),nullable=False)
    reference:Mapped[str]=mapped_column(Text,nullable=False)
    quote_revision:Mapped[int]=mapped_column(nullable=False,default=0)
    revision:Mapped[int]=mapped_column(nullable=False,default=1)

class ProgressIn(BaseModel):
    stage:Literal['Draft','Sent','Declined']
    reference:str=Field(min_length=3,max_length=1000)
    expected_revision:int=Field(ge=0)

def payload(db,q):
    row=db.get(ProposalProgress,q.id);detail=db.get(QuoteDetail,q.id);job=db.get(m.Job,q.job_id)
    stage='Accepted' if q.status=='Approved' else row.stage if row and (not detail or row.quote_revision==detail.revision) else 'Draft'
    return {**m.proposal_data(q,db),'stage':stage,'tracking_revision':row.revision if row else 0,
        'reference':row.reference if row else '', 'job_status':job.status,'advanced':detail is not None,
        'job_url':'/?page=jobs&job='+job.job_number}

def attach_job_hub(app):
    m.Base.metadata.create_all(m.engine)
    @app.get('/proposal-dashboard.js')
    def script():return FileResponse(m.ROOT/'proposal-dashboard.js',media_type='application/javascript',headers={'Cache-Control':'private, no-cache'})
    @app.get('/api/admin/proposal-pipeline')
    def pipeline(user=Depends(m.require('view_financials'))):
        with Session(m.engine) as db:
            proposals=[payload(db,q) for q in db.scalars(select(m.Proposal).order_by(m.Proposal.created_at.desc()))]
            groups={name:{'count':0,'total':0} for name in ('Draft','Sent','Accepted','Declined')}
            jobs={}
            for q in proposals:
                groups[q['stage']]['count']+=1;groups[q['stage']]['total']+=q['total']
                if q['stage']=='Accepted' and q['job_number'] not in jobs:
                    jobs[q['job_number']]={'job_number':q['job_number'],'title':q['title'],'status':q['job_status'],'url':q['job_url']}
            return {'groups':groups,'proposals':proposals,'accepted_jobs':list(jobs.values())}
    @app.put('/api/admin/proposal-pipeline/{proposal_id}')
    def record_progress(proposal_id:str,data:ProgressIn,user=Depends(m.require('view_financials'))):
        if not data.reference.strip():raise HTTPException(422,'Enter a reference or reason')
        with Session(m.engine) as db:
            q=db.scalar(select(m.Proposal).where(m.Proposal.id==proposal_id).with_for_update())
            if not q:raise HTTPException(404,'Proposal not found')
            if q.status!='Draft':raise HTTPException(409,'Accepted proposals cannot be reclassified here')
            row=db.get(ProposalProgress,q.id)
            if data.expected_revision!=(row.revision if row else 0):raise HTTPException(409,'Proposal tracking changed; refresh before saving')
            detail=db.get(QuoteDetail,q.id)
            if not row:row=ProposalProgress(proposal_id=q.id,stage=data.stage,reference=data.reference.strip());db.add(row)
            else:row.stage=data.stage;row.reference=data.reference.strip();row.revision+=1
            row.quote_revision=detail.revision if detail else 0
            m.log(db,'PROPOSAL_TRACKING','proposal',q.id,data.model_dump_json());db.flush();result=payload(db,q);db.commit();return result
