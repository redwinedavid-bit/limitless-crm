"""v1.4 field service: immutable customer acknowledgments, equipment readings,
private work photos, and printable service report PDFs.

Local development only. Signatures evidence customer acknowledgment, not verified e-sign.
"""
from __future__ import annotations
import base64, binascii, hashlib, io, json, os, re, uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional
from fastapi import Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import ForeignKey, String, Text, Numeric, DateTime, select
from sqlalchemy.orm import Mapped, mapped_column, Session
from . import main as m
from . import private_storage as storage
from .purchasing_v13 import sniff_mime

UPLOAD_DIR = Path(os.getenv('LMS_FIELD_UPLOAD_DIR',str(m.ROOT/'private_field_uploads')))
MAX_PHOTO=10*1024*1024
MAX_SIGNATURE=600*1024

class FieldReport(m.Base):
    __tablename__='field_reports'
    work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),primary_key=True)
    customer_concern:Mapped[str]=mapped_column(Text,default='')
    diagnosis:Mapped[str]=mapped_column(Text,default='')
    work_performed:Mapped[str]=mapped_column(Text,default='')
    recommendations:Mapped[str]=mapped_column(Text,default='')
    follow_up_required:Mapped[bool]=mapped_column(default=False,nullable=False)
    updated_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc),nullable=False)

class EquipmentReading(m.Base):
    __tablename__='equipment_readings'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
    equipment_id:Mapped[str]=mapped_column(ForeignKey('equipment.id',ondelete='RESTRICT'),nullable=False)
    parameter:Mapped[str]=mapped_column(String(90),nullable=False)
    value:Mapped[Decimal]=mapped_column(Numeric(14,3),nullable=False)
    unit:Mapped[str]=mapped_column(String(24),nullable=False)
    note:Mapped[str]=mapped_column(String(300),default='')
    recorded_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    recorded_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc),nullable=False)

class EquipmentCondition(m.Base):
    """Condition assessment for an asset during one work order."""
    __tablename__='equipment_conditions'
    work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),primary_key=True)
    equipment_id:Mapped[str]=mapped_column(ForeignKey('equipment.id',ondelete='RESTRICT'),primary_key=True)
    rating:Mapped[str]=mapped_column(String(30),nullable=False)
    note:Mapped[str]=mapped_column(Text,default='')
    recorded_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    recorded_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc),nullable=False)

class WorkPhoto(m.Base):
    __tablename__='work_photos'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=False,index=True)
    equipment_id:Mapped[Optional[str]]=mapped_column(ForeignKey('equipment.id',ondelete='RESTRICT'))
    filename:Mapped[str]=mapped_column(String(220),nullable=False)
    storage_key:Mapped[str]=mapped_column(String(80),unique=True,nullable=False)
    sha256:Mapped[str]=mapped_column(String(64),nullable=False)
    media_type:Mapped[str]=mapped_column(String(64),nullable=False)
    bytes_size:Mapped[int]=mapped_column(nullable=False)
    caption:Mapped[str]=mapped_column(String(300),default='')
    uploaded_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    uploaded_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc),nullable=False)

class CustomerAcknowledgment(m.Base):
    __tablename__='customer_acknowledgments'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),unique=True,nullable=False)
    signer_name:Mapped[str]=mapped_column(String(180),nullable=False)
    signer_title:Mapped[str]=mapped_column(String(180),default='')
    signature_key:Mapped[str]=mapped_column(String(80),unique=True,nullable=False)
    signature_sha256:Mapped[str]=mapped_column(String(64),nullable=False)
    report_snapshot_json:Mapped[str]=mapped_column(Text,nullable=False)
    report_sha256:Mapped[str]=mapped_column(String(64),nullable=False)
    recorded_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    signed_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc),nullable=False)

class ReportIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    customer_concern:str=Field(default='',max_length=5000)
    diagnosis:str=Field(default='',max_length=10000)
    work_performed:str=Field(default='',max_length=10000)
    recommendations:str=Field(default='',max_length=10000)
    follow_up_required:bool=False

class ReadingIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    equipment_id:str
    parameter:str=Field(min_length=2,max_length=90)
    value:Decimal
    unit:str=Field(min_length=1,max_length=24)
    note:str=Field(default='',max_length=300)

class ConditionIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    rating:str
    note:str=Field(default='',max_length=2500)


CONDITION_RATINGS=('Excellent','Good','Fair','Poor','Failed','Not assessed')

class SignIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    signer_name:str=Field(min_length=2,max_length=180)
    signer_title:str=Field(default='',max_length=180)
    signature_data:str=Field(min_length=40,max_length=850000)
    acknowledgment:bool


def _allowed(db,wo_id,user):
    return m.permitted_wo(db,wo_id,user)

def _signed(db,wo_id):
    return db.scalar(select(CustomerAcknowledgment).where(CustomerAcknowledgment.work_order_id==wo_id))

def ensure_editor(user):
    if user['role'] not in ('owner','office','technician'):
        raise HTTPException(403,'Only field staff and supervisors can change service reports')

def ensure_open(db,wo_id):
    if _signed(db,wo_id):raise HTTPException(409,'Work order service report is signed and locked; create a new work order for subsequent service')

def _asset_linked(db,wo_id,equipment_id):
    if not db.get(m.WorkOrderEquipment,(wo_id,equipment_id)):
        raise HTTPException(422,'Equipment must be linked to this work order before adding measurements or photos')

def _reading_dict(r,db):
    asset=db.get(m.Equipment,r.equipment_id)
    return {'id':r.id,'equipment_id':r.equipment_id,'asset_tag':asset.asset_tag if asset else '',
            'parameter':r.parameter,'value':float(r.value),'unit':r.unit,'note':r.note,'recorded_at':r.recorded_at.isoformat()}

def _photo_dict(p):
    return {'id':p.id,'equipment_id':p.equipment_id,'filename':p.filename,'caption':p.caption,
            'media_type':p.media_type,'uploaded_at':p.uploaded_at.isoformat(),
            'download_url':'/api/field/photos/'+p.id+'/file'}

def _snapshot(db,w):
    from .call_field_notes import customer_note_payloads
    job=db.get(m.Job,w.job_id);cust=db.get(m.Customer,job.customer_id)
    location=db.get(m.Location,job.location_id) if job.location_id else None
    rep=db.get(FieldReport,w.id)
    assets=list(db.scalars(select(m.Equipment).join(m.WorkOrderEquipment,m.Equipment.id==m.WorkOrderEquipment.equipment_id)
             .where(m.WorkOrderEquipment.work_order_id==w.id).order_by(m.Equipment.asset_tag)))
    readings=list(db.scalars(select(EquipmentReading).where(EquipmentReading.work_order_id==w.id).order_by(EquipmentReading.recorded_at,EquipmentReading.id)))
    photos=list(db.scalars(select(WorkPhoto).where(WorkPhoto.work_order_id==w.id).order_by(WorkPhoto.uploaded_at,WorkPhoto.id)))
    notes=list(db.scalars(select(m.ServiceNote).where(m.ServiceNote.work_order_id==w.id).order_by(m.ServiceNote.created_at,m.ServiceNote.id)))
    return {'job_number':job.job_number,'work_order_id':w.id,'summary':w.summary,
            'customer':cust.name,'location':location.name if location else '',
            'address':location.address if location else '',
            'customer_concern':rep.customer_concern if rep else '',
            'diagnosis':rep.diagnosis if rep else '',
            'work_performed':rep.work_performed if rep else '',
            'recommendations':rep.recommendations if rep else '',
            'follow_up_required':rep.follow_up_required if rep else False,
            'assets':[{'id':a.id,'tag':a.asset_tag,'make':a.manufacturer,'model':a.model,'serial':a.serial,'refrigerant':a.refrigerant} for a in assets],
            'conditions':[{'equipment_id':c.equipment_id,'asset_tag':(db.get(m.Equipment,c.equipment_id).asset_tag if db.get(m.Equipment,c.equipment_id) else ''),'rating':c.rating,'note':c.note,'recorded_at':c.recorded_at.isoformat()} for c in db.scalars(select(EquipmentCondition).where(EquipmentCondition.work_order_id==w.id))],
            'readings':[_reading_dict(r,db) for r in readings],
            'photos':[{'id':p.id,'caption':p.caption,'sha256':p.sha256,'media_type':p.media_type,'storage_key':p.storage_key} for p in photos],
            'notes':[{'note':n.note,'author':db.get(m.User,n.technician_id).display_name} for n in notes]+customer_note_payloads(db,w.id)}

def _json_snapshot(snapshot):
    return json.dumps(snapshot,sort_keys=True,separators=(',',':'),ensure_ascii=False)

def _render_pdf(snapshot,signature=None):
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image as PDFImage,KeepTogether
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.utils import ImageReader
    from xml.sax.saxutils import escape
    buf=io.BytesIO()
    doc=SimpleDocTemplate(buf,pagesize=(612,792),leftMargin=46,rightMargin=46,topMargin=42,bottomMargin=45,
                          title='Limitless Mechanical - Service Report')
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='LMBrand',parent=styles['Heading1'],fontSize=17,leading=20,textColor=colors.HexColor('#14252B'),spaceAfter=6))
    styles.add(ParagraphStyle(name='LMSection',parent=styles['Heading2'],fontSize=11,leading=14,textColor=colors.HexColor('#156E78'),spaceBefore=13,spaceAfter=6))
    styles.add(ParagraphStyle(name='LMBody',parent=styles['Normal'],fontSize=9,leading=13,spaceAfter=5,textColor=colors.HexColor('#24363C')))
    styles.add(ParagraphStyle(name='LMFine',parent=styles['Normal'],fontSize=8,leading=11,textColor=colors.HexColor('#5C6E75')))
    safe=lambda s:escape(str(s or '')).replace('\n','<br/>')
    story=[Paragraph('LIMITLESS MECHANICAL',styles['LMBrand']),Paragraph('Service Work Report  |  '+safe(snapshot['job_number']),styles['LMFine']),Spacer(1,15)]
    meta=[['Customer',snapshot['customer']],['Location',snapshot['location']],['Address',snapshot['address']],['Work order',snapshot['summary']]]
    table=Table([[Paragraph('<b>'+safe(a)+'</b>',styles['LMBody']),Paragraph(safe(b),styles['LMBody'])] for a,b in meta],colWidths=[105,410],hAlign='LEFT')
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#F0F5F5')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
    story.append(table)
    for heading,key in [('Customer concern','customer_concern'),('Diagnosis','diagnosis'),('Work performed','work_performed'),('Recommendations','recommendations')]:
        story.append(Paragraph(heading,styles['LMSection']))
        story.append(Paragraph(safe(snapshot.get(key)) or 'Not recorded',styles['LMBody']))
    story.append(Paragraph('Follow-up required: '+('Yes' if snapshot['follow_up_required'] else 'No'),styles['LMBody']))
    story.append(Paragraph('Equipment serviced',styles['LMSection']))
    if snapshot['assets']:
        for a in snapshot['assets']:
            story.append(Paragraph(safe(a['tag']+'  ·  '+a['make']+' '+a['model']+'  ·  Serial '+a['serial']),styles['LMBody']))
    else:story.append(Paragraph('No equipment tagged',styles['LMBody']))
    if snapshot.get('conditions'):
        story.append(Paragraph('Equipment condition',styles['LMSection']))
        for c in snapshot['conditions']:
            story.append(Paragraph(safe(c['asset_tag']+' — '+c['rating']+((': '+c['note']) if c['note'] else '')),styles['LMBody']))
    story.append(Paragraph('Equipment readings',styles['LMSection']))
    if snapshot['readings']:
        for r in snapshot['readings']:
            story.append(Paragraph(safe(f"{r['asset_tag']}: {r['parameter']} = {r['value']:g} {r['unit']}"+(f"  ({r['note']})" if r['note'] else '')),styles['LMBody']))
    else:story.append(Paragraph('No measurements entered',styles['LMBody']))
    if snapshot['notes']:
        story.append(Paragraph('Service notes',styles['LMSection']))
        for note in snapshot['notes']:
            story.append(Paragraph(safe(note['author']+': '+note['note']),styles['LMBody']))
    if snapshot['photos']:
        story.append(Paragraph('Field photographs',styles['LMSection']))
        for p in snapshot['photos'][:12]:
            block=[Paragraph(safe(p.get('caption') or 'Work photo'),styles['LMBody'])]
            try:
                from PIL import Image as PILImage, ImageOps
                with PILImage.open(io.BytesIO(storage.read(UPLOAD_DIR,p['storage_key']))) as im:
                    im=ImageOps.exif_transpose(im).convert('RGB')
                    im.thumbnail((1050,650))
                    mini=io.BytesIO();im.save(mini,format='JPEG',quality=78);mini.seek(0)
                    iw,ih=im.size
                block.append(PDFImage(mini,width=min(410,iw*.43),height=min(410,iw*.43)*ih/iw))
            except (OSError,ValueError,HTTPException):block.append(Paragraph('Photo stored with work order; preview unavailable',styles['LMFine']))
            story.append(KeepTogether(block))
        if len(snapshot['photos'])>12:story.append(Paragraph('Additional photos retained in CRM',styles['LMFine']))
    story.append(Paragraph('Customer acknowledgment',styles['LMSection']))
    if signature:
        story.append(Paragraph('Signed by '+safe(signature.signer_name)+((' — '+safe(signature.signer_title)) if signature.signer_title else '')+' on '+safe(signature.signed_at.strftime('%Y-%m-%d %H:%M UTC')),styles['LMBody']))
        try:
            data=storage.read(UPLOAD_DIR,signature.signature_key)
            from PIL import Image as PILImage
            with PILImage.open(io.BytesIO(data)) as img:
                px,py=img.size
            story.append(PDFImage(io.BytesIO(data),width=230,height=max(35,230*py/px)))
        except (OSError,ValueError):story.append(Paragraph('Stored signature image unavailable',styles['LMFine']))
        story.append(Paragraph('Signed report SHA-256: '+safe(signature.report_sha256),styles['LMFine']))
    else:story.append(Paragraph('Unsigned draft — not a completed customer acknowledgment',styles['LMFine']))
    story.append(Spacer(1,12))
    story.append(Paragraph('Customer signature acknowledges receipt of service documentation; it does not independently authorize payment or certify repairs.',styles['LMFine']))
    def footer(canv,document):
        canv.setStrokeColor(colors.HexColor('#187780'));canv.line(46,35,566,35)
        canv.setFont('Helvetica',8);canv.setFillColor(colors.HexColor('#526972'))
        canv.drawString(46,24,'Limitless Mechanical Services, Inc.');canv.drawRightString(566,24,'Page '+str(document.page))
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return buf.getvalue()


def _asset_history(db,asset,limit=40):
    """Unified asset history, across visits; read-only and scoped by callers."""
    links=list(db.scalars(select(m.WorkOrderEquipment).join(m.WorkOrder,
        m.WorkOrderEquipment.work_order_id==m.WorkOrder.id)
        .where(m.WorkOrderEquipment.equipment_id==asset.id)
        .order_by(m.WorkOrder.created_at.desc(),m.WorkOrder.id.desc()).limit(limit+1)))
    truncated=len(links)>limit
    visits=[]
    for link in links[:limit]:
        w=db.get(m.WorkOrder,link.work_order_id)
        job=db.get(m.Job,w.job_id)
        readings=list(db.scalars(select(EquipmentReading).where(
            EquipmentReading.work_order_id==w.id,EquipmentReading.equipment_id==asset.id)
            .order_by(EquipmentReading.recorded_at,EquipmentReading.id)))
        condition=db.get(EquipmentCondition,(w.id,asset.id))
        photos=list(db.scalars(select(WorkPhoto).where(WorkPhoto.work_order_id==w.id,
            WorkPhoto.equipment_id==asset.id).order_by(WorkPhoto.uploaded_at)))
        report=db.get(FieldReport,w.id)
        pm=db.scalar(select(m.PMVisit).join(m.PMPlan,m.PMVisit.plan_id==m.PMPlan.id).where(
            m.PMVisit.work_order_id==w.id,m.PMPlan.equipment_id==asset.id))
        pm_items=[]
        if pm:
            for inspection in db.scalars(select(m.PMInspection).where(m.PMInspection.visit_id==pm.id)):
                pm_items.append({'date':inspection.created_at.isoformat(),
                                 'responses':json.loads(inspection.responses_json)})
        visits.append({'work_order_id':w.id,'job_number':job.job_number,
          'date':w.created_at.isoformat(),'summary':w.summary,'status':w.status,
          'condition':({'rating':condition.rating,'note':condition.note,
              'recorded_at':condition.recorded_at.isoformat()} if condition else None),
          'readings':[_reading_dict(r,db) for r in readings],
          'work_performed':report.work_performed if report else '',
          'findings':report.diagnosis if report else '',
          'recommendations':report.recommendations if report else '',
          'notes_scope':'Work order notes may describe multiple assets',
          'photos':[{'id':p.id,'caption':p.caption,'date':p.uploaded_at.isoformat()} for p in photos],
          'pm_inspections':pm_items})
    latest={}
    for visit in reversed(visits):
        for reading in visit['readings']:
            key=(reading['parameter'].casefold(),reading['unit'].casefold())
            latest.setdefault(key,[]).append(reading)
    trends=[{'parameter':values[-1]['parameter'],'unit':values[-1]['unit'],
             'latest':values[-1]['value'],
             'previous':values[-2]['value'] if len(values)>1 else None,
             'latest_at':values[-1]['recorded_at'],
             'samples':len(values)} for values in latest.values()]
    trends.sort(key=lambda item:item['parameter'])
    return {'equipment':m.equipment_payload(asset,db),'visits':visits,
            'has_more':truncated,'trends':trends}


def attach_field(app):
    # Local dev-only additive schema creation. Use a proper versioned migration in production.
    m.Base.metadata.create_all(m.engine,tables=[FieldReport.__table__,EquipmentReading.__table__,EquipmentCondition.__table__,WorkPhoto.__table__,CustomerAcknowledgment.__table__])

    @app.get('/api/field/work-orders/{wo_id}')
    def field_detail(wo_id:str,user=Depends(m.current_user)):
        with Session(m.engine) as db:
            w=_allowed(db,wo_id,user)
            snapshot=_snapshot(db,w)
            sig=_signed(db,w.id)
            return {'report':snapshot,'signed':bool(sig),'signer':sig.signer_name if sig else None,
                    'signed_at':sig.signed_at.isoformat() if sig else None,
                    'photos':[_photo_dict(p) for p in db.scalars(select(WorkPhoto).where(WorkPhoto.work_order_id==w.id).order_by(WorkPhoto.uploaded_at))]}

    @app.put('/api/field/work-orders/{wo_id}')
    def save_field_report(wo_id:str,data:ReportIn,user=Depends(m.current_user)):
        with Session(m.engine) as db:
            w=_allowed(db,wo_id,user);ensure_editor(user);ensure_open(db,w.id)
            r=db.get(FieldReport,w.id)
            if not r:r=FieldReport(work_order_id=w.id,updated_by=user['id']);db.add(r)
            for key,value in data.model_dump().items():setattr(r,key,value)
            r.updated_by=user['id'];r.updated_at=datetime.now(timezone.utc)
            m.log(db,'SAVE_FIELD_REPORT','work_order',w.id,user['id']);db.commit()
            return {'ok':True}

    @app.get('/api/field/work-orders/{wo_id}/equipment/{equipment_id}/history')
    def field_equipment_history(wo_id:str,equipment_id:str,user=Depends(m.current_user)):
        with Session(m.engine) as db:
            w=_allowed(db,wo_id,user)
            _asset_linked(db,w.id,equipment_id)
            return _asset_history(db,db.get(m.Equipment,equipment_id))

    @app.get('/api/equipment/{equipment_id}/history')
    def office_equipment_history(equipment_id:str,user=Depends(m.require('read_all'))):
        with Session(m.engine) as db:
            asset=db.get(m.Equipment,equipment_id)
            if not asset:raise HTTPException(404,'Equipment not found')
            return _asset_history(db,asset)

    @app.put('/api/field/work-orders/{wo_id}/equipment/{equipment_id}/condition')
    def save_equipment_condition(wo_id:str,equipment_id:str,data:ConditionIn,user=Depends(m.current_user)):
        if data.rating not in CONDITION_RATINGS:raise HTTPException(422,'Select a valid equipment condition')
        with Session(m.engine) as db:
            w=_allowed(db,wo_id,user);ensure_editor(user);ensure_open(db,w.id)
            _asset_linked(db,w.id,equipment_id)
            assessment=db.get(EquipmentCondition,(w.id,equipment_id))
            if not assessment:
                assessment=EquipmentCondition(work_order_id=w.id,equipment_id=equipment_id,
                    rating=data.rating,recorded_by=user['id'])
                db.add(assessment)
            assessment.rating=data.rating;assessment.note=data.note.strip()
            assessment.recorded_by=user['id'];assessment.recorded_at=datetime.now(timezone.utc)
            m.log(db,'ASSESS_EQUIPMENT','equipment',equipment_id,w.id)
            db.commit();return {'rating':assessment.rating,'note':assessment.note}

    @app.get('/api/field/work-orders/{wo_id}/equipment/{equipment_id}/photos/{photo_id}')
    def field_history_photo(wo_id:str,equipment_id:str,photo_id:str,user=Depends(m.current_user)):
        with Session(m.engine) as db:
            w=_allowed(db,wo_id,user);_asset_linked(db,w.id,equipment_id)
            p=db.get(WorkPhoto,photo_id)
            if not p or p.equipment_id!=equipment_id:raise HTTPException(404,'Equipment photo not found')
            if not db.get(m.WorkOrderEquipment,(p.work_order_id,equipment_id)):
                raise HTTPException(404,'Equipment photo not found')
            return storage.response(UPLOAD_DIR,p.storage_key,p.media_type,p.filename)

    @app.post('/api/field/work-orders/{wo_id}/readings',status_code=201)
    def add_reading(wo_id:str,data:ReadingIn,user=Depends(m.current_user)):
        if not data.value.is_finite() or abs(data.value)>Decimal('1000000') or data.value.as_tuple().exponent < -3:
            raise HTTPException(422,'Enter a finite reading with at most three decimal places')
        with Session(m.engine) as db:
            w=_allowed(db,wo_id,user);ensure_editor(user);ensure_open(db,w.id);_asset_linked(db,w.id,data.equipment_id)
            r=EquipmentReading(work_order_id=w.id,equipment_id=data.equipment_id,
               parameter=data.parameter.strip(),value=data.value,unit=data.unit.strip(),note=data.note,
               recorded_by=user['id']);db.add(r);db.flush()
            out=_reading_dict(r,db);m.log(db,'FIELD_READING','work_order',w.id,r.id);db.commit();return out

    @app.delete('/api/field/work-orders/{wo_id}/readings/{reading_id}')
    def remove_reading(wo_id:str,reading_id:str,user=Depends(m.current_user)):
        with Session(m.engine) as db:
            w=_allowed(db,wo_id,user);ensure_editor(user);ensure_open(db,w.id)
            reading=db.get(EquipmentReading,reading_id)
            if not reading or reading.work_order_id!=w.id:raise HTTPException(404,'Reading not found')
            if reading.recorded_by!=user['id'] and user['role'] not in ('owner','office'):
                raise HTTPException(403,'Can only correct readings you entered')
            db.delete(reading);m.log(db,'REMOVE_FIELD_READING','work_order',w.id,reading_id);db.commit();return {'ok':True}

    @app.post('/api/field/work-orders/{wo_id}/photos',status_code=201)
    async def add_photo(wo_id:str,file:UploadFile=File(...),caption:str=Form(''),equipment_id:Optional[str]=Form(None),user=Depends(m.current_user)):
        if len(caption)>300:raise HTTPException(422,'Caption is too long')
        with Session(m.engine) as db:
            w=_allowed(db,wo_id,user);ensure_editor(user);ensure_open(db,w.id)
            if equipment_id:_asset_linked(db,w.id,equipment_id)
            content=await file.read(MAX_PHOTO+1)
            if len(content)>MAX_PHOTO:raise HTTPException(413,'Field photo exceeds 10 MB')
            mime=sniff_mime(content)
            if mime not in ('image/jpeg','image/png','image/webp','image/heic'):
                raise HTTPException(415,'Upload a JPEG, PNG, WebP or HEIC image')
            if mime!='image/heic':
                try:
                    from PIL import Image as PILImage
                    with PILImage.open(io.BytesIO(content)) as check:check.verify()
                except Exception:raise HTTPException(422,'Photo data is not a valid image')
            original=(file.filename or 'photo').replace('\\','/').split('/')[-1].replace('\n','')[:180]
            key=uuid.uuid4().hex
            key=storage.put(UPLOAD_DIR,key,content,mime)
            try:
                p=WorkPhoto(work_order_id=w.id,equipment_id=equipment_id,filename=original,storage_key=key,
                   sha256=hashlib.sha256(content).hexdigest(),media_type=mime,bytes_size=len(content),
                   caption=caption.strip(),uploaded_by=user['id'])
                db.add(p);db.flush();m.log(db,'UPLOAD_FIELD_PHOTO','work_order',w.id,p.id)
                result=_photo_dict(p);db.commit();return result
            except Exception:
                storage.remove(UPLOAD_DIR,key);raise

    @app.get('/api/field/photos/{photo_id}/file')
    def get_field_photo(photo_id:str,user=Depends(m.current_user)):
        with Session(m.engine) as db:
            p=db.get(WorkPhoto,photo_id)
            if not p:raise HTTPException(404,'Photo not found')
            _allowed(db,p.work_order_id,user)
            return storage.response(UPLOAD_DIR,p.storage_key,p.media_type,p.filename)

    @app.post('/api/field/work-orders/{wo_id}/signature',status_code=201)
    def sign_report(wo_id:str,data:SignIn,user=Depends(m.current_user)):
        if not data.acknowledgment:raise HTTPException(422,'Customer acknowledgment consent is required')
        if user['role']!='technician':raise HTTPException(403,'A technician must capture the customer signature at the service visit')
        if not data.signature_data.startswith('data:image/png;base64,'):
            raise HTTPException(422,'A drawn PNG signature is required')
        try:signature_bytes=base64.b64decode(data.signature_data.split(',',1)[1],validate=True)
        except (ValueError,binascii.Error):raise HTTPException(422,'Invalid signature image')
        if not signature_bytes or len(signature_bytes)>MAX_SIGNATURE:raise HTTPException(413,'Signature image exceeds limit')
        try:
            from PIL import Image as PILImage
            with PILImage.open(io.BytesIO(signature_bytes)) as image:
                image.verify()
            with PILImage.open(io.BytesIO(signature_bytes)) as image:
                if image.format!='PNG' or image.width<100 or image.height<50:
                    raise ValueError('Invalid dimensions')
                from PIL import ImageStat
                gray=image.convert('L')
                if ImageStat.Stat(gray).mean[0]>252 and gray.getextrema()[0]>230:
                    raise ValueError('Blank signature')
        except Exception:raise HTTPException(422,'Invalid drawn signature image')
        with Session(m.engine) as db:
            w=_allowed(db,wo_id,user);ensure_editor(user);ensure_open(db,w.id)
            rep=db.get(FieldReport,w.id)
            if not rep or not rep.diagnosis.strip() or not rep.work_performed.strip():
                raise HTTPException(422,'Enter diagnosis and work performed before requesting customer acknowledgment')
            snapshot=_snapshot(db,w)
            text=_json_snapshot(snapshot)
            key=uuid.uuid4().hex
            if db.scalar(select(m.ActiveTimeClock.technician_id).where(m.ActiveTimeClock.work_order_id==w.id)):
                raise HTTPException(409,'Clock out before completing and signing the call')
            key=storage.put(UPLOAD_DIR,key,signature_bytes,'image/png')
            try:
                sig=CustomerAcknowledgment(work_order_id=w.id,signer_name=data.signer_name.strip(),
                    signer_title=data.signer_title.strip(),signature_key=key,
                    signature_sha256=hashlib.sha256(signature_bytes).hexdigest(),
                    report_snapshot_json=text,report_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(),
                    recorded_by=user['id'])
                db.add(sig);w.status='Completed';db.flush()
                m.log(db,'SIGN_SERVICE_REPORT','work_order',w.id,'signature_id='+sig.id)
                result={'signed':True,'signed_at':sig.signed_at.isoformat(),'report_sha256':sig.report_sha256}
                db.commit();return result
            except Exception:
                storage.remove(UPLOAD_DIR,key);raise

    @app.get('/api/field/work-orders/{wo_id}/report.pdf')
    def field_report_pdf(wo_id:str,user=Depends(m.current_user)):
        with Session(m.engine) as db:
            w=_allowed(db,wo_id,user)
            sig=_signed(db,w.id)
            if sig:
                snap=json.loads(sig.report_snapshot_json)
                if hashlib.sha256(_json_snapshot(snap).encode('utf-8')).hexdigest()!=sig.report_sha256:
                    raise HTTPException(409,'Signed report integrity validation failed')
            else:snap=_snapshot(db,w)
            pdf=_render_pdf(snap,sig)
            job=db.get(m.Job,w.job_id)
            safe_job=re.sub(r'[^A-Za-z0-9-]','-',job.job_number)
            return StreamingResponse(io.BytesIO(pdf),media_type='application/pdf',headers={
                'Content-Disposition':f'inline; filename="Limitless-Service-{safe_job}.pdf"',
                'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    @app.get('/field.js')
    def field_script():return FileResponse(m.ROOT/'field.js',media_type='application/javascript')

    @app.get('/field')
    def field_page():return FileResponse(m.ROOT/'field.html')
