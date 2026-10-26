"""Read-only CSV ingestion from the ACTIVE LMS Job Board, with dry-run by default.
Export these exact tabs from the active sheet as CSV files into a directory:
Customer.csv, Jobs Open_Costing.csv, David POs.csv, Adrian POs.csv, Danny POs.csv,
David Hrs Mileage.csv, Adrian Hrs Mileage.csv, Danny Hrs Mileage.csv
Run python -m server.import_job_board /path/to/csvs [--commit]
Does not modify the Google Sheet. Never uses secondary employee/cash boards.
"""
import argparse,csv,json,re
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from .main import engine, Customer,Location,Job,PurchaseOrder,LaborEntry,log

def read(folder, filename):
    p=folder / filename
    if not p.exists():return []
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def val(row,*names):
    for n in names:
        if n in row and row[n] is not None:return str(row[n]).strip()
    return ''
def amount(v):
    try:return Decimal(str(v or '0').replace(',','').replace('$','').replace('%','').strip() or '0')
    except InvalidOperation:return Decimal('0')
def dt(v):
    for fmt in ('%m/%d/%Y','%m/%d/%y','%Y-%m-%d'):
        try:return datetime.strptime(v,fmt).date()
        except ValueError:pass
    return None

def migrate(folder,commit=False):
    counts=Counter(); exceptions=[]
    def issue(kind,row,detail):exceptions.append({'type':kind,'row':row,'issue':detail})
    with Session(engine) as db:
      customers={x.name.casefold():x for x in db.scalars(select(Customer))}
      jobs={x.job_number:x for x in db.scalars(select(Job))}
      locations={(x.customer_id,x.name.casefold(),x.address.casefold()):x for x in db.scalars(select(Location))}
      for index,r in enumerate(read(folder,'Customer.csv'),2):
        name=val(r,'Customer Name')
        if not name or name in ('1-DTM','1-OVHD'):continue
        key=name.casefold()
        c=customers.get(key)
        if not c:
            c=Customer(name=name,billing_address=', '.join(filter(None,[val(r,'Billing Address'),val(r,'Billing City State Zip')])),contact=val(r,'Point of Contact'))
            db.add(c);db.flush();customers[key]=c;counts['customers']+=1
        site=val(r,'Service Location');address=', '.join(filter(None,[val(r,'Service Address'),val(r,'Service City ST Zip')]))
        if site and site.casefold()!='same as billing address':
            lk=(c.id,site.casefold(),address.casefold())
            if lk not in locations:
                l=Location(customer_id=c.id,name=site,address=address);db.add(l);db.flush();locations[lk]=l;counts['locations']+=1
      for index,r in enumerate(read(folder,'Jobs Open_Costing.csv'),2):
        number=val(r,'Job Number')
        if not re.fullmatch(r'LMS\d+',number):continue
        name=val(r,'Customer','Customer ')
        if not name:continue # preformatted unused row
        c=customers.get(name.casefold())
        if not c:
            c=Customer(name=name);db.add(c);db.flush();customers[name.casefold()]=c;counts['customers_from_jobs']+=1
        location_name=val(r,'Location');addr=', '.join(filter(None,[val(r,'Address'),val(r,'City, State, Zip')]))
        loc=None
        if location_name or addr:
            key=(c.id,location_name.casefold(),addr.casefold())
            loc=locations.get(key)
            if not loc:
                loc=Location(customer_id=c.id,name=location_name or addr,address=addr);db.add(loc);db.flush();locations[key]=loc;counts['locations_from_jobs']+=1
        if number in jobs:continue
        j=Job(job_number=number,customer_id=c.id,location_id=loc.id if loc else None,description=val(r,'Description'),status=val(r,'Payment Complete') or 'Open',contract_amount=amount(val(r,'Custom Bill Amount')),historical_labor_cost=amount(val(r,'Job Labor Cost w/ Overhead')),historical_material_cost=amount(val(r,'Job Cost Material')),historical_mileage_cost=amount(val(r,'Total Mileage Cost')),source='historical')
        db.add(j);db.flush();jobs[number]=j;counts['jobs']+=1
      for internal in ('DTM','OVHD'):
        if internal not in jobs:
            cname='1-'+internal
            c=customers.get(cname.casefold())
            if not c:
                c=Customer(name=cname);db.add(c);db.flush();customers[cname.casefold()]=c
            j=Job(job_number=internal,customer_id=c.id,description='Internal cost center',status='Internal',source='historical');db.add(j);db.flush();jobs[internal]=j;counts['internal_jobs']+=1
      for fn in ('David POs.csv','Adrian POs.csv','Danny POs.csv'):
        existing=set(db.scalars(select(PurchaseOrder.po_number)))
        for index,r in enumerate(read(folder,fn),2):
            pon=val(r,'PO','PO Number');num=val(r,'Job Number');cost=amount(val(r,'Amount'))
            if not pon or not re.match(r'^(DR|AR|DB)\d+$',pon):continue
            if not num and cost>0:issue('PO',f'{fn}:{index}',f'{pon}: amount exists but job missing');continue
            if not num or cost<=0:continue
            if num not in jobs:issue('PO',f'{fn}:{index}',f'{pon}: unmatched job {num}');continue
            if pon in existing:continue
            p=PurchaseOrder(po_number=pon,job_id=jobs[num].id,vendor=val(r,'Vendor') or 'Unspecified supplier',description=val(r,'Description'),issue_date=dt(val(r,'PO Date')),amount=cost,received_value=cost,status='Historical',source='historical')
            db.add(p);existing.add(pon);counts['purchase_orders']+=1
      for name,fn in [('David','David Hrs Mileage.csv'),('Adrian','Adrian Hrs Mileage.csv'),('Danny','Danny Hrs Mileage.csv')]:
        for i,r in enumerate(read(folder,fn),2):
            num=val(r,'Job Number');hours=amount(val(r,'Total'));miles=amount(val(r,'Total Mileage'))
            if hours<=0 or not num:continue
            if num not in jobs:issue('labor',f'{fn}:{i}',f'Unmatched job {num}');continue
            if miles<0:issue('mileage',f'{fn}:{i}',f'Negative mileage {miles}');miles=Decimal('0')
            key=f'{fn}:{i}'
            if db.scalar(select(LaborEntry.id).where(LaborEntry.source_key==key)):continue
            db.add(LaborEntry(job_id=jobs[num].id,employee=name,work_date=dt(val(r,'Date','e')),hours=hours,overtime_hours=amount(val(r,'OT')),mileage=miles,source_key=key));counts['labor_entries']+=1
      if commit:
          log(db,'IMPORT','batch','active-lms-job-board',json.dumps(dict(counts)));db.commit()
      else:db.rollback()
    return {'committed':commit,'counts':dict(counts),'exception_count':len(exceptions),'exceptions':exceptions}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('csv_folder',type=Path);p.add_argument('--commit',action='store_true');p.add_argument('--report',type=Path)
    a=p.parse_args();out=migrate(a.csv_folder,a.commit);s=json.dumps(out,indent=2,default=str)
    if a.report:a.report.write_text(s,encoding='utf-8')
    print(s)
