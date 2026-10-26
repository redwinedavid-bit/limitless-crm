"""One-time owner review and consolidation of the imported starter locations."""
import json
from datetime import datetime,timezone
from fastapi import Depends,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from . import main as m
from .call_pricing import owner
from .customer_import import CustomerImportRun,digest,dump
from .productivity import PricingDefaults,QuickJobCounter
from .operations_v110 import SiteMetadata,BillingRevision
from .profit_tracking import ProfitPolicy

KEY='imported-site-cleanup-v1'
class MergeSite(BaseModel):
    model_config=ConfigDict(extra='forbid')
    target_id:str
    duplicate_ids:list[str]=Field(min_length=1,max_length=30)
    name:str=Field(min_length=2,max_length=250)
    address:str=Field(min_length=3,max_length=2000)
class UpdateSite(BaseModel):
    model_config=ConfigDict(extra='forbid')
    site_id:str
    name:str=Field(min_length=2,max_length=250)
    address:str=Field(min_length=3,max_length=2000)
class MergeCustomer(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_id:str
    target_id:str
class CleanupIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    customer_merge:MergeCustomer
    remove_customer_id:str
    site_merges:list[MergeSite]=Field(min_length=1,max_length=30)
    site_updates:list[UpdateSite]=Field(default_factory=list,max_length=50)
class CommitIn(CleanupIn):
    expected_fingerprint:str
    confirmation:str

def body(data):return data.model_dump(exclude={'expected_fingerprint','confirmation'},mode='json')
def snapshots(db):
    result={}
    for model in (m.Customer,m.Location,PricingDefaults,SiteMetadata,BillingRevision,ProfitPolicy,QuickJobCounter):
        table=model.__table__;rows=[dict(row) for row in db.execute(select(table)).mappings()];rows.sort(key=dump);result[table.name]=rows
    return result

def compatible(values,label):
    values={str(v).strip() for v in values if str(v or '').strip()}
    if len(values)>1:raise HTTPException(409,label+' conflict; review before consolidating')
    return next(iter(values),'')

def preview(db,data):
    if not db.get(CustomerImportRun,'active-job-board-customers-v1'):raise HTTPException(409,'Complete the customer import first')
    if db.scalar(select(func.count()).select_from(m.Job)) or db.scalar(select(func.count()).select_from(m.Equipment)):
        raise HTTPException(409,'Jobs or equipment now exist. This one-time imported-data cleanup is blocked so linked history can be reviewed.')
    source=db.get(m.Customer,data.customer_merge.source_id);target=db.get(m.Customer,data.customer_merge.target_id);removed=db.get(m.Customer,data.remove_customer_id)
    if not source or not target or not removed:raise HTTPException(409,'A reviewed customer changed; review the cleanup again')
    if (source.name,target.name,removed.name)!=('All About Kids - Josh Tarter','All About Kids','Timothy Maple'):raise HTTPException(422,'Customer changes do not match the approved cleanup')
    if len({source.id,target.id,removed.id})!=3:raise HTTPException(422,'Choose distinct customers')
    for field in ('billing_address','contact','phone','email'):compatible([getattr(source,field),getattr(target,field)],'Customer '+field)
    for model,column in ((PricingDefaults,'scope'),(ProfitPolicy,'scope')):
        a=db.get(model,source.id);b=db.get(model,target.id)
        if a and b and json.loads(a.values_json)!=json.loads(b.values_json):raise HTTPException(409,'Customer pricing settings conflict')
    all_sites={s.id:s for s in db.scalars(select(m.Location))};seen=set();deleted=[]
    for group in data.site_merges:
        ids=[group.target_id,*group.duplicate_ids]
        if len(set(ids))!=len(ids) or set(ids)&seen:raise HTTPException(422,'A service site appears in multiple merge actions')
        seen.update(ids);sites=[all_sites.get(i) for i in ids]
        if any(not s for s in sites):raise HTTPException(409,'A service site changed; review again')
        if len({s.customer_id for s in sites})!=1:raise HTTPException(422,'Service sites must belong to the same customer')
        if sites[0].customer_id==removed.id:raise HTTPException(422,'Do not merge sites for the excluded customer')
        if any(not (s.source_key or '').startswith('gs-site:') for s in sites):raise HTTPException(409,'Only reviewed imported sites can be consolidated')
        metas=[db.get(SiteMetadata,i) for i in ids]
        for field in ('contact','phone'):compatible([getattr(meta,field) for meta in metas if meta],'Site '+field)
        deleted.extend(group.duplicate_ids)
    for action in data.site_updates:
        if action.site_id in seen:raise HTTPException(422,'A site cannot be both updated and merged')
        seen.add(action.site_id);site=all_sites.get(action.site_id)
        if not site or site.customer_id==removed.id:raise HTTPException(422,'Choose an existing retained service site')
    excluded_sites=[s.id for s in all_sites.values() if s.customer_id==removed.id];deleted.extend(excluded_sites)
    # Refuse unknown relationships rather than deleting or reassigning live history.
    customer_ids={source.id,removed.id};site_ids=set(deleted)
    allowed={'locations','pricing_defaults','service_site_metadata_v110','customer_billing_revisions_v110'}
    for table in m.Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            ids=customer_ids if fk.column.table.name=='customers' else site_ids if fk.column.table.name=='locations' else set()
            if ids and table.name not in allowed and db.scalar(select(func.count()).select_from(table).where(fk.parent.in_(ids))):
                raise HTTPException(409,'Linked records in '+table.name+' need review; nothing was changed')
    # Both Hickory locations are retained: never infer a shared site from nearby numbers.
    old=snapshots(db);counter=db.get(QuickJobCounter,'jobs')
    next_number='LMS'+str(int(counter.last_number)+1).zfill(4) if counter else None
    result={'fingerprint':digest({'before':old,'changes':body(data)}),'customers_before':len(old['customers']),'customers_after':len(old['customers'])-2,'sites_before':len(all_sites),'sites_after':len(all_sites)-len(deleted),'merged_duplicate_sites':len(deleted)-len(excluded_sites),'address_updates':len(data.site_updates),'excluded_customer':'Timothy Maple','merged_customer':'All About Kids','next_job_number':next_number,'already_applied':False}
    return result,old

def bump_site(db,site_id):
    meta=db.get(SiteMetadata,site_id)
    if meta:meta.revision+=1
    else:db.add(SiteMetadata(location_id=site_id))

def apply(db,data,user):
    if db.bind.dialect.name=='postgresql':
        db.connection().exec_driver_sql("SET LOCAL lock_timeout = '15s'")
        db.connection().exec_driver_sql('SELECT pg_advisory_xact_lock(734260263)')
        names=sorted(m.Base.metadata.tables);quote=db.bind.dialect.identifier_preparer.quote
        db.connection().exec_driver_sql('LOCK TABLE '+','.join(map(quote,names))+' IN EXCLUSIVE MODE')
    source_hash=digest(body(data));prior=db.get(CustomerImportRun,KEY)
    if prior:
        if prior.fingerprint!=source_hash:raise HTTPException(409,'Cleanup was already applied with a different plan')
        return {**json.loads(prior.result_json),'already_applied':True}
    if data.confirmation!='APPLY REVIEWED SITE CLEANUP':raise HTTPException(422,'Enter the displayed cleanup confirmation')
    result,old=preview(db,data)
    if result['fingerprint']!=data.expected_fingerprint:raise HTTPException(409,'Customer or site data changed after preview; review again')
    archive=CustomerImportRun(key=KEY,fingerprint=source_hash,backup_json=dump(old),result_json='{}',created_by=user['id'],created_at=datetime.now(timezone.utc).isoformat());db.add(archive)
    for group in data.site_merges:
        target=db.get(m.Location,group.target_id);metas=[db.get(SiteMetadata,i) for i in [group.target_id,*group.duplicate_ids]]
        values={field:compatible([getattr(meta,field) for meta in metas if meta],'Site '+field) for field in ('contact','phone')}
        bump_site(db,target.id);meta=db.get(SiteMetadata,target.id)
        for field,value in values.items():setattr(meta,field,value)
        target.name=group.name.strip();target.address=group.address.strip()
        for site_id in group.duplicate_ids:
            meta=db.get(SiteMetadata,site_id)
            if meta:db.delete(meta);db.flush()
            db.delete(db.get(m.Location,site_id))
        db.flush()
    for action in data.site_updates:
        site=db.get(m.Location,action.site_id);site.name=action.name.strip();site.address=action.address.strip();bump_site(db,site.id)
    source=db.get(m.Customer,data.customer_merge.source_id);target=db.get(m.Customer,data.customer_merge.target_id)
    for field in ('billing_address','contact','phone','email'):setattr(target,field,compatible([getattr(source,field),getattr(target,field)],'Customer '+field))
    for site in db.scalars(select(m.Location).where(m.Location.customer_id==source.id)):site.customer_id=target.id
    for model in (PricingDefaults,ProfitPolicy):
        a=db.get(model,source.id);b=db.get(model,target.id)
        if a and not b:
            values={'scope':target.id,'values_json':a.values_json}
            if model is PricingDefaults:values['customer_id']=target.id
            db.add(model(**values))
        if a:db.delete(a)
    source_revision=db.get(BillingRevision,source.id)
    if source_revision:db.delete(source_revision)
    target_revision=db.get(BillingRevision,target.id)
    if target_revision:target_revision.revision+=1
    else:db.add(BillingRevision(customer_id=target.id))
    db.flush();db.delete(source)
    removed=db.get(m.Customer,data.remove_customer_id)
    for site in list(db.scalars(select(m.Location).where(m.Location.customer_id==removed.id))):
        meta=db.get(SiteMetadata,site.id)
        if meta:db.delete(meta);db.flush()
        db.delete(site)
    for model in (PricingDefaults,ProfitPolicy,BillingRevision):
        row=db.get(model,removed.id)
        if row:db.delete(row)
    db.flush();db.delete(removed);db.flush()
    result['customers_after']=db.scalar(select(func.count()).select_from(m.Customer));result['sites_after']=db.scalar(select(func.count()).select_from(m.Location))
    result['sites_missing_address']=db.scalar(select(func.count()).select_from(m.Location).where(func.trim(m.Location.address)==''))
    archive.result_json=dump(result);m.log(db,'CLEAN_IMPORTED_SITES','migration',KEY,dump({k:v for k,v in result.items() if k!='fingerprint'}));db.flush()
    return result

def attach_site_cleanup(app):
    @app.get('/site-cleanup')
    def page(user=Depends(owner)):return FileResponse(m.ROOT/'site-cleanup.html',headers={'Cache-Control':'no-store'})
    @app.get('/site-cleanup.js')
    def script(user=Depends(owner)):return FileResponse(m.ROOT/'site-cleanup.js',media_type='application/javascript',headers={'Cache-Control':'no-store'})
    @app.post('/api/admin/site-cleanup/preview')
    def inspect(data:CleanupIn,user=Depends(owner)):
        with Session(m.engine) as db:
            prior=db.get(CustomerImportRun,KEY)
            if prior:return {**json.loads(prior.result_json),'already_applied':True}
            return preview(db,data)[0]
    @app.post('/api/admin/site-cleanup/commit')
    def commit(data:CommitIn,user=Depends(owner)):
        with Session(m.engine) as db:
            result=apply(db,data,user);db.commit();return result
