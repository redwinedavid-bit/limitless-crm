"""Owner-only, one-time replacement of the reviewed starter test dataset.
The source workbook stays read-only. Preview fingerprints every affected row;
commit locks the dataset, revalidates it and replaces it in one transaction.
"""
import hashlib,json,re,uuid
from datetime import datetime,timezone
from decimal import Decimal
from fastapi import Depends,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import String,Text,select,or_,delete,func
from sqlalchemy.orm import Mapped,mapped_column,Session
from . import main as m
from .call_pricing import owner
from .productivity import PricingDefaults,QuickJobCounter

SOURCE='10OO6jARZ4EDOxBx3nsr0bRlT0n5X7PsNPNSJhuQQpHo'
TEST_IDS={'37111d5e-c93b-4347-97eb-64e035262f73','605656a3-709a-4d20-b139-6e157b969ab3'}
EXPECTED={'customers':2,'jobs':4,'work_orders':4,'purchase_orders':2,'proposals':1,'crm_invoices':1}
class CustomerImportRun(m.Base):
    __tablename__='customer_import_runs'
    key:Mapped[str]=mapped_column(String(80),primary_key=True)
    fingerprint:Mapped[str]=mapped_column(String(64),nullable=False)
    backup_json:Mapped[str]=mapped_column(Text,nullable=False)
    result_json:Mapped[str]=mapped_column(Text,nullable=False)
    created_by:Mapped[str]=mapped_column(String(36),nullable=False)
    created_at:Mapped[str]=mapped_column(String(50),nullable=False)
class ImportIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_spreadsheet_id:str
    customer_rows:list[list]=Field(min_length=2,max_length=1002)
    job_rows:list[list]=Field(min_length=2,max_length=2004)
    standard_rate:Decimal=Field(gt=0,le=10000,decimal_places=2)
    special_rate:Decimal=Field(gt=0,le=10000,decimal_places=2)
    overtime_rate:Decimal=Field(gt=0,le=10000,decimal_places=2)
    next_job_number:str=Field(pattern=r'^LMS\d{4,8}$')
class CommitIn(ImportIn):
    expected_fingerprint:str
    confirmation:str

def clean(v):return re.sub(r'\s+',' ',str(v or '')).strip()
def key(v):return clean(v).casefold()
def dump(v):return json.dumps(v,sort_keys=True,default=str,separators=(',',':'))
def source_payload(data):return data.model_dump(exclude={'expected_fingerprint','confirmation'},mode='json')
def digest(v):return hashlib.sha256(dump(v).encode()).hexdigest()
def plan(data):
    if data.source_spreadsheet_id!=SOURCE:raise HTTPException(422,'Use the active LMS Job Board')
    if data.customer_rows[0][:8]!=['Customer Name','Billing Address','Billing City State Zip','Special Rate','Point of Contact','Service Location','Service Address','Service City ST Zip']:raise HTTPException(422,'Customer headers do not match')
    if [clean(x) for x in data.job_rows[0][:6]]!=['Job Number','Customer','Payment Complete','Location','Address','City, State, Zip']:raise HTTPException(422,'Job headers do not match')
    customers={};site_candidates=[];warnings=[]
    def customer(name):
        name=clean(name);k=key(name)
        if not k or k in ('1-dtm','1-ovhd'):return None
        if len(name)>250:raise HTTPException(422,'Customer name is too long')
        return customers.setdefault(k,{'name':name,'billing_address':'','contact':'','special':False})
    for ix,raw in enumerate(data.customer_rows[1:],2):
        r=list(raw)+['']*8;c=customer(r[0])
        if not c:continue
        address=', '.join(filter(None,map(clean,(r[1],r[2]))));contact=clean(r[4])
        if address:
            if c['billing_address'] and key(c['billing_address'])!=key(address):warnings.append(f"Customer row {ix}: {c['name']} has multiple billing addresses; the latest populated customer row is used.")
            c['billing_address']=address
        if contact:c['contact']=contact
        c['special']=c['special'] or key(r[3]) in ('y','yes','true')
        site,address=clean(r[5]),', '.join(filter(None,map(clean,(r[6],r[7]))))
        if key(site)=='same as billing address':site_candidates.append((key(c['name']),'Primary Service Location',c['billing_address']))
        elif site or address:site_candidates.append((key(c['name']),site or address,address))
    for raw in data.job_rows[1:]:
        r=list(raw)+['']*6
        if not re.fullmatch(r'LMS\d+',clean(r[0])):continue
        c=customer(r[1])
        if not c:continue
        site,address=clean(r[3]),', '.join(filter(None,map(clean,(r[4],r[5]))))
        if site or address:site_candidates.append((key(c['name']),site or address,address))
    # Fill a missing address only when the same customer + exact site name has
    # one known address. Distinct names at one address remain distinct systems/sites.
    known={}
    for c,n,a in site_candidates:
        if a:known.setdefault((c,key(n)),set()).add(a)
    sites={}
    for c,n,a in site_candidates:
        if not a and len(known.get((c,key(n)),set()))==1:a=next(iter(known[c,key(n)]))
        if len(n)>250 or len(a)>2000:raise HTTPException(422,'Service location exceeds the supported length')
        sites.setdefault((c,key(n),key(a)),{'customer_key':c,'name':n,'address':a})
    for c in customers.values():
        if len(c['billing_address'])>2000 or len(c['contact'])>250:raise HTTPException(422,'Customer detail exceeds the supported length')
        if not c['billing_address']:warnings.append(c['name']+': billing address missing in source')
    missing=sum(not s['address'] for s in sites.values())
    if missing:warnings.append(f'{missing} named service locations have no address in the source; addresses are left blank for review.')
    warnings.append('Site name spelling variations are preserved rather than merged automatically. No historical jobs, invoices or costs are imported.')
    return {'customers':list(customers.values()),'sites':list(sites.values()),'warnings':list(dict.fromkeys(warnings))}

def affected_rows(db,metadata,ids):
    tables=list(metadata.sorted_tables);rows={}
    root=metadata.tables['customers'];rows['customers']=[dict(r) for r in db.execute(select(root).where(root.c.id.in_(ids))).mappings()]
    for _ in range(len(tables)+1):
        changed=False
        for table in tables:
            if table.name in ('customers','customer_import_runs'):continue
            conditions=[]
            for fk in table.foreign_keys:
                parent=rows.get(fk.column.table.name,[])
                values={r[fk.column.name] for r in parent if r[fk.column.name] is not None}
                if values:conditions.append(fk.parent.in_(values))
            if not conditions:continue
            found=[dict(r) for r in db.execute(select(table).where(or_(*conditions))).mappings()]
            if len(found)>len(rows.get(table.name,[])):rows[table.name]=found;changed=True
        if not changed:break
    # These two retained-history tables use logical keys rather than SQL FKs.
    for name,col,values in [('profit_policies','scope',ids),('labor_cost_snapshots','approval_id',[r['id'] for r in rows.get('time_approvals',[])])]:
        if name in metadata.tables and values:
            table=metadata.tables[name];found=[dict(r) for r in db.execute(select(table).where(table.c[col].in_(values))).mappings()]
            if found:rows[name]=found
    for name,items in rows.items():items.sort(key=lambda r:dump(r))
    return rows

def preview(db,data):
    result=plan(data)
    ids=set(db.scalars(select(m.Customer.id)))
    if ids!=TEST_IDS:raise HTTPException(409,'Customer records differ from the reviewed two-customer test dataset. Nothing was changed.')
    rows=affected_rows(db,m.Base.metadata,ids);counts={k:len(v) for k,v in rows.items() if v}
    if any(counts.get(k,0)!=v for k,v in EXPECTED.items()):raise HTTPException(409,'Linked test record counts changed. Review the dataset again before cleanup.')
    if rows.get('active_time_clocks'):raise HTTPException(409,'Clock out of all test calls before cleanup')
    counter=db.get(QuickJobCounter,'jobs')
    fingerprint=digest({'rows':rows,'counter':counter.last_number if counter else None,'source':source_payload(data)})
    return {'fingerprint':fingerprint,'remove':counts,'import_customers':len(result['customers']),'import_locations':len(result['sites']),'next_job_number':data.next_job_number,'standard_rate':str(data.standard_rate),'special_rate':str(data.special_rate),'overtime_rate':str(data.overtime_rate),'warnings':result['warnings']},rows,result

def apply(db,data,user):
    if db.bind.dialect.name=='postgresql':
        db.connection().exec_driver_sql("SET LOCAL lock_timeout = '15s'")
        db.connection().exec_driver_sql('SELECT pg_advisory_xact_lock(734260262)')
    prior=db.get(CustomerImportRun,'active-job-board-customers-v1')
    source_hash=digest(source_payload(data))
    if prior:
        if prior.fingerprint!=source_hash:raise HTTPException(409,'The one-time customer replacement was already completed with a different source')
        return {**json.loads(prior.result_json),'already_applied':True}
    if data.confirmation!='REPLACE REVIEWED TEST DATA':raise HTTPException(422,'Enter the displayed replacement confirmation')
    # Lock all tables for this brief transaction, preventing inserts/edits between
    # fingerprint verification, dependent cleanup, import and counter reset.
    if db.bind.dialect.name=='postgresql':
        names=sorted(m.Base.metadata.tables)
        db.connection().exec_driver_sql('LOCK TABLE '+','.join(db.bind.dialect.identifier_preparer.quote(n) for n in names)+' IN EXCLUSIVE MODE')
        # Recheck after acquiring locks (another owner may have completed it).
        prior=db.get(CustomerImportRun,'active-job-board-customers-v1',populate_existing=True)
        if prior:
            if prior.fingerprint!=source_hash:raise HTTPException(409,'Customer replacement already completed')
            return {**json.loads(prior.result_json),'already_applied':True}
    summary,rows,parsed=preview(db,data)
    if summary['fingerprint']!=data.expected_fingerprint:raise HTTPException(409,'Test data or source changed after preview; preview again')
    # Keep a private DB backup and audit record. No files or credentials are exposed.
    archive=CustomerImportRun(key='active-job-board-customers-v1',fingerprint=source_hash,backup_json=dump(rows),result_json='{}',created_by=user['id'],created_at=datetime.now(timezone.utc).isoformat());db.add(archive);db.flush()
    for table in reversed(m.Base.metadata.sorted_tables):
        for row in rows.get(table.name,[]):
            db.execute(delete(table).where(*[col==row[col.name] for col in table.primary_key]))
    customer_ids={}
    for c in parsed['customers']:
        row=m.Customer(name=c['name'],billing_address=c['billing_address'],contact=c['contact'],source_key='gs-customer:'+digest(key(c['name'])));db.add(row);db.flush();customer_ids[key(c['name'])]=row.id
        db.add(PricingDefaults(scope=row.id,customer_id=row.id,values_json=dump({'labor_sell_per_hour':str(data.special_rate if c['special'] else data.standard_rate),'overtime_sell_per_hour':str(data.overtime_rate)})))
    for site in parsed['sites']:
        db.add(m.Location(customer_id=customer_ids[site['customer_key']],name=site['name'],address=site['address'],source_key='gs-site:'+digest(site)))
    global_row=db.get(PricingDefaults,'global');values=json.loads(global_row.values_json) if global_row else {}
    values.update(labor_sell_per_hour=str(data.standard_rate),overtime_sell_per_hour=str(data.overtime_rate))
    if global_row:global_row.values_json=dump(values);global_row.revision+=1
    else:db.add(PricingDefaults(scope='global',values_json=dump(values)))
    counter=db.get(QuickJobCounter,'jobs');last=str(int(data.next_job_number[3:])-1)
    if counter:counter.last_number=last
    else:db.add(QuickJobCounter(key='jobs',last_number=last))
    db.flush();summary['already_applied']=False;archive.result_json=dump(summary)
    m.log(db,'REPLACE_TEST_CUSTOMERS','migration',archive.key,dump({k:summary[k] for k in ('remove','import_customers','import_locations','next_job_number')}))
    return summary

def attach_customer_import(app):
    m.Base.metadata.create_all(m.engine)
    @app.get('/customer-import')
    def page(user=Depends(owner)):return FileResponse(m.ROOT/'customer-import.html',headers={'Cache-Control':'no-store'})
    @app.get('/customer-import.js')
    def script(user=Depends(owner)):return FileResponse(m.ROOT/'customer-import.js',media_type='application/javascript',headers={'Cache-Control':'no-store'})
    @app.post('/api/admin/customer-import/preview')
    def inspect(data:ImportIn,user=Depends(owner)):
        with Session(m.engine) as db:
            prior=db.get(CustomerImportRun,'active-job-board-customers-v1')
            if prior:return {**json.loads(prior.result_json),'already_applied':True}
            return preview(db,data)[0]
    @app.post('/api/admin/customer-import/commit')
    def commit(data:CommitIn,user=Depends(owner)):
        with Session(m.engine) as db:
            result=apply(db,data,user);db.commit();return result
