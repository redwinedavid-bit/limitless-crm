"""Technician closeout, recent asset context, offline recovery and document storage."""
import hashlib,io,json,uuid
from datetime import datetime,timedelta,timezone
from decimal import Decimal,ROUND_HALF_UP
from typing import Literal
from fastapi import Depends,HTTPException,UploadFile,File,Form
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field,model_validator
from sqlalchemy import String,Text,ForeignKey,select,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column,Session
from . import main as m,call_pricing as p,profit_tracking as profit
from .productivity import access
from .time_clock import as_utc,clock_now,ensure_no_time_overlap,lock_time_user

class CallCloseout(m.Base):
 __tablename__='call_closeouts_v111'
 work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),primary_key=True)
 revision:Mapped[int]=mapped_column(default=1,nullable=False)
 evidence:Mapped[str]=mapped_column(Text,nullable=False)
 reviewed_by:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)

class OfflineTime(m.Base):
 __tablename__='offline_time_receipts_v111'
 request_id:Mapped[str]=mapped_column(String(36),primary_key=True)
 technician_id:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
 fingerprint:Mapped[str]=mapped_column(String(64),nullable=False)
 entry_id:Mapped[str]=mapped_column(ForeignKey('field_time_entries.id',ondelete='RESTRICT'),nullable=False)

class OfflinePhoto(m.Base):
 __tablename__='offline_photo_receipts_v111'
 request_id:Mapped[str]=mapped_column(String(36),primary_key=True)
 work_order_id:Mapped[str]=mapped_column(ForeignKey('work_orders.id',ondelete='RESTRICT'),nullable=False)
 author_id:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
 photo_id:Mapped[str]=mapped_column(ForeignKey('work_photos.id',ondelete='RESTRICT'),nullable=False)
 fingerprint:Mapped[str]=mapped_column(String(64),nullable=False)

class EquipmentDocument(m.Base):
 __tablename__='equipment_documents_v111'
 id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
 equipment_id:Mapped[str]=mapped_column(ForeignKey('equipment.id',ondelete='RESTRICT'),nullable=False,index=True)
 title:Mapped[str]=mapped_column(String(200),nullable=False)
 filename:Mapped[str]=mapped_column(String(200),nullable=False)
 key:Mapped[str]=mapped_column(String(80),nullable=False,unique=True)
 mime:Mapped[str]=mapped_column(String(80),nullable=False)
 sha256:Mapped[str]=mapped_column(String(64),nullable=False)

class CloseIn(BaseModel):
 parts_confirmed:bool
 no_parts:bool=False
 no_equipment_reason:str=Field(default='',max_length=1000)
 no_mileage_reason:str=Field(default='',max_length=1000)
 expected_revision:int=Field(default=0,ge=0)

class OfflineIn(BaseModel):
 request_id:uuid.UUID
 started_at:datetime
 ended_at:datetime
 note:str=Field(default='Offline call time — admin review required',max_length=1000)
 @model_validator(mode='after')
 def dates(self):
  if not self.started_at.tzinfo or not self.ended_at.tzinfo or self.ended_at<=self.started_at or self.ended_at-self.started_at>timedelta(hours=24):raise ValueError('Use ordered timezone-aware times up to 24 hours')
  return self


def closeout_state(db,wo_id):
 from .call_field_notes import CallNote,usage_rows
 from .field_v14 import FieldReport
 w=p.call(db,wo_id);notes=list(db.scalars(select(CallNote).where(CallNote.work_order_id==wo_id,CallNote.kind=='service_description')));rep=db.get(FieldReport,wo_id)
 work=any(n.text.strip() for n in notes) or bool(rep and rep.work_performed.strip()) or bool(db.scalar(select(m.ServiceNote.id).where(m.ServiceNote.work_order_id==wo_id)))
 mileage=db.scalar(select(m.CallMileage).where(m.CallMileage.work_order_id==wo_id));assets=list(db.scalars(select(m.WorkOrderEquipment.equipment_id).where(m.WorkOrderEquipment.work_order_id==wo_id)));parts=usage_rows(db,wo_id)
 blockers=[]
 if db.scalar(select(m.ActiveTimeClock.technician_id).where(m.ActiveTimeClock.work_order_id==wo_id)):blockers.append('Clock out before finishing')
 if not work:blockers.append('Add work performed')
 if not mileage or mileage.end_odometer is None:blockers.append('Finish odometer readings, or give a reason mileage does not apply')
 if not assets:blockers.append('Choose the serviced system, or explain why there is no equipment record')
 r=db.get(CallCloseout,wo_id)
 return {'revision':r.revision if r else 0,'status':w.status,'parts_count':len(parts),'has_work_performed':work,'has_complete_mileage':bool(mileage and mileage.end_odometer is not None),'has_equipment':bool(assets),'blockers':blockers,'saved':json.loads(r.evidence) if r else None}


def finish(db,wo_id,data,user):
 from .field_v14 import ensure_open
 access(db,wo_id,user);w=p.call(db,wo_id,lock=True);ensure_open(db,wo_id)
 state=closeout_state(db,wo_id);r=db.get(CallCloseout,wo_id)
 if data.expected_revision!=state['revision']:raise HTTPException(409,'Closeout changed; reload')
 if not state['has_work_performed']:raise HTTPException(422,'Add the service description before finishing')
 if not data.parts_confirmed or data.no_parts!=(state['parts_count']==0):raise HTTPException(422,'Confirm reported parts, or explicitly confirm no parts were used')
 if not state['has_equipment'] and len(data.no_equipment_reason.strip())<5:raise HTTPException(422,'Choose equipment or explain the missing equipment record')
 if not state['has_complete_mileage'] and len(data.no_mileage_reason.strip())<5:raise HTTPException(422,'Finish odometer readings or explain why mileage does not apply')
 if db.scalar(select(m.ActiveTimeClock.technician_id).where(m.ActiveTimeClock.work_order_id==wo_id)):raise HTTPException(409,'Clock out before finishing')
 if not r:r=CallCloseout(work_order_id=wo_id);db.add(r)
 else:r.revision+=1
 r.reviewed_by=user['id'];r.evidence=p.dump({**data.model_dump(),'at':profit.now().isoformat() if hasattr(profit,'now') else profit.stamp().isoformat()});w.status='Completed';db.flush();m.log(db,'FINISH_CALL_REVIEW','work_order',wo_id,r.evidence);return closeout_state(db,wo_id)


def context(db,wo_id,user):
 access(db,wo_id,user);rows=[]
 from .operations_v110 import warranties_for
 from .call_field_notes import CallNote
 for link in db.scalars(select(m.WorkOrderEquipment).where(m.WorkOrderEquipment.work_order_id==wo_id)):
  asset=db.get(m.Equipment,link.equipment_id);visits=list(db.scalars(select(m.WorkOrder).join(m.WorkOrderEquipment,m.WorkOrderEquipment.work_order_id==m.WorkOrder.id).where(m.WorkOrderEquipment.equipment_id==asset.id,m.WorkOrder.id!=wo_id).order_by(m.WorkOrder.created_at.desc()).limit(3)))
  history=[]
  for v in visits:
   notes=list(db.scalars(select(CallNote).where(CallNote.work_order_id==v.id,CallNote.kind.in_(['service_description','recommendation','follow_up'])).order_by(CallNote.created_at.desc()).limit(6)))
   history.append({'id':v.id,'date':as_utc(v.created_at).isoformat(),'summary':v.summary,'notes':[{'kind':n.kind,'audience':n.audience,'text':n.text} for n in notes]})
  health=profit.equipment_health(db,asset,db.get(m.Location,asset.location_id).customer_id)
  docs=[{'id':d.id,'title':d.title,'url':'/api/calls/'+wo_id+'/equipment-documents/'+d.id} for d in db.scalars(select(EquipmentDocument).where(EquipmentDocument.equipment_id==asset.id))]
  rows.append({'id':asset.id,'tag':asset.asset_tag,'model':asset.model,'serial':asset.serial,'recent_visits':history,'repeat_warning':health['reasons'],'documents':docs})
 return {'equipment':rows,'warranties':warranties_for(db,wo_id)}


def attach_field_improvements(app):
 m.Base.metadata.create_all(m.engine)
 @app.get('/field-offline.js')
 def offline_script():return FileResponse(m.ROOT/'field-offline.js',media_type='application/javascript')
 @app.get('/field-worker.js')
 def worker():return FileResponse(m.ROOT/'field-worker.js',media_type='application/javascript',headers={'Service-Worker-Allowed':'/','Cache-Control':'no-cache'})
 @app.get('/field-offline-shell')
 def shell():return FileResponse(m.ROOT/'call-workspace.html')
 @app.get('/api/calls/{wo_id}/service-context')
 def get_context(wo_id:str,user=Depends(m.current_user)):
  with Session(m.engine) as db:return context(db,wo_id,user)
 @app.get('/api/calls/{wo_id}/closeout')
 def closeout(wo_id:str,user=Depends(m.current_user)):
  with Session(m.engine) as db:access(db,wo_id,user);return closeout_state(db,wo_id)
 @app.post('/api/calls/{wo_id}/finish')
 def finish_call(wo_id:str,data:CloseIn,user=Depends(m.current_user)):
  with Session(m.engine) as db:r=finish(db,wo_id,data,user);db.commit();return r
 @app.post('/api/calls/{wo_id}/offline-time',status_code=201)
 def offline_time(wo_id:str,data:OfflineIn,user=Depends(m.current_user)):
  if user['role']!='technician':raise HTTPException(403,'Technician access required')
  with Session(m.engine) as db:
   lock_time_user(db,user['id']);fingerprint=hashlib.sha256(p.dump([wo_id,data.model_dump(mode='json')]).encode()).hexdigest();old=db.get(OfflineTime,str(data.request_id))
   if old:
    if old.technician_id!=user['id'] or old.fingerprint!=fingerprint:raise HTTPException(409,'Offline request identifier reused for different time')
    return {'id':old.entry_id,'already_saved':True}
   access(db,wo_id,user);start=as_utc(data.started_at);end=as_utc(data.ended_at)
   if end>clock_now()+timedelta(seconds=30) or start<clock_now()-timedelta(days=7):raise HTTPException(422,'Offline time must be within the last seven days and not in the future')
   ensure_no_time_overlap(db,user['id'],start,end)
   r=m.FieldTimeEntry(work_order_id=wo_id,technician_id=user['id'],started_at=start,ended_at=end,hours=(Decimal(str((end-start).total_seconds()))/3600).quantize(Decimal('.01'),rounding=ROUND_HALF_UP),mileage=0,note=data.note)
   db.add(r);db.flush();db.add(OfflineTime(request_id=str(data.request_id),technician_id=user['id'],fingerprint=fingerprint,entry_id=r.id));m.log(db,'RECOVER_OFFLINE_TIME','work_order',wo_id,r.id);db.commit();return {'id':r.id,'already_saved':False}
 @app.post('/api/calls/{wo_id}/offline-photo',status_code=201)
 async def offline_photo(wo_id:str,request_id:uuid.UUID=Form(...),caption:str=Form(''),file:UploadFile=File(...),user=Depends(m.current_user)):
  from .field_v14 import WorkPhoto,UPLOAD_DIR,_photo_dict,ensure_open,ensure_editor
  from .purchasing_v13 import sniff_mime
  from . import private_storage as store
  content=await file.read(10*1024*1024+1)
  if len(content)>10*1024*1024:raise HTTPException(413,'Photo exceeds 10 MB')
  mime=sniff_mime(content)
  if mime not in ('image/jpeg','image/png','image/webp','image/heic'):raise HTTPException(415,'Use a supported image')
  if len(caption)>300:raise HTTPException(422,'Caption exceeds 300 characters')
  if mime!='image/heic':
   try:
    from PIL import Image
    with Image.open(io.BytesIO(content)) as image:image.verify()
   except Exception:raise HTTPException(422,'Invalid photo')
  fingerprint=hashlib.sha256(content+caption.encode()).hexdigest()
  with Session(m.engine) as db:
   access(db,wo_id,user);p.call(db,wo_id,lock=True);old=db.get(OfflinePhoto,str(request_id))
   if old:
    if old.work_order_id!=wo_id or old.author_id!=user['id'] or old.fingerprint!=fingerprint:raise HTTPException(409,'Photo request identifier reused')
    return _photo_dict(db.get(WorkPhoto,old.photo_id))
   ensure_editor(user);ensure_open(db,wo_id);key=store.put(UPLOAD_DIR,uuid.uuid4().hex,content,mime);photo=WorkPhoto(work_order_id=wo_id,filename=(file.filename or 'photo').replace('\\','/').split('/')[-1][:180],storage_key=key,sha256=hashlib.sha256(content).hexdigest(),media_type=mime,bytes_size=len(content),caption=caption,uploaded_by=user['id']);db.add(photo);db.flush();db.add(OfflinePhoto(request_id=str(request_id),work_order_id=wo_id,author_id=user['id'],photo_id=photo.id,fingerprint=fingerprint));db.commit();return _photo_dict(photo)
 @app.post('/api/admin/equipment/{equipment_id}/documents',status_code=201)
 async def upload_document(equipment_id:str,title:str=Form(...),file:UploadFile=File(...),user=Depends(p.owner)):
  from . import private_storage as store
  from .field_v14 import UPLOAD_DIR
  from .purchasing_v13 import sniff_mime
  payload=await file.read(12*1024*1024+1)
  if len(payload)>12*1024*1024:raise HTTPException(413,'Document exceeds 12 MB')
  mime=sniff_mime(payload)
  if mime not in ('application/pdf','image/png','image/jpeg','image/webp'):raise HTTPException(415,'Use PDF, PNG, JPEG or WebP')
  if not title.strip() or len(title)>200:raise HTTPException(422,'Enter a document title up to 200 characters')
  with Session(m.engine) as db:
   if not db.get(m.Equipment,equipment_id):raise HTTPException(404,'Equipment not found')
   key=store.put(UPLOAD_DIR,uuid.uuid4().hex,payload,mime);r=EquipmentDocument(equipment_id=equipment_id,title=title.strip(),filename=(file.filename or 'document').replace('\\','/').split('/')[-1][:200],key=key,mime=mime,sha256=hashlib.sha256(payload).hexdigest());db.add(r);db.flush();db.commit();return {'id':r.id}
 @app.get('/api/calls/{wo_id}/equipment-documents/{document_id}')
 def document(wo_id:str,document_id:str,user=Depends(m.current_user)):
  from . import private_storage as store
  from .field_v14 import UPLOAD_DIR
  with Session(m.engine) as db:
   access(db,wo_id,user);r=db.get(EquipmentDocument,document_id)
   if not r or not db.get(m.WorkOrderEquipment,(wo_id,r.equipment_id)):raise HTTPException(404,'Document is not linked to this call equipment')
   return store.response(UPLOAD_DIR,r.key,r.mime,r.filename)
