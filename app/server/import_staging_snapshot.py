"""Transfer validated historical LMS import from private SQLite snapshot to an EMPTY CRM DB.

Default mode reports counts without changing the target. --commit is explicit.
Only business history is copied: no account passwords, sessions, or uploaded files.
The production billing ledger is NOT reconciled by this tool.
"""
from __future__ import annotations
import argparse, json, os
from pathlib import Path
from sqlalchemy import MetaData, create_engine, inspect, select, func
from sqlalchemy.orm import Session

TABLES = ('customers','locations','jobs','purchase_orders','labor_entries')


def migrate(source_path: str, target_engine, commit: bool = False, allow_sqlite_test_target: bool = False):
    src_path = Path(source_path).resolve()
    if not src_path.is_file(): raise ValueError('Source SQLite snapshot not found')
    if target_engine.dialect.name != 'postgresql' and not allow_sqlite_test_target:
        raise ValueError('Import target must be PostgreSQL; SQLite allowed only in tests')
    if target_engine.dialect.name == 'sqlite':
        target_file = target_engine.url.database
        if target_file and src_path == Path(target_file).resolve():
            raise ValueError('Source and target database must be separate')
    source = create_engine('sqlite:///' + str(src_path))
    source_meta, target_meta = MetaData(), MetaData()
    source_meta.reflect(bind=source, only=TABLES)
    target_meta.reflect(bind=target_engine, only=TABLES)
    data = {}
    with source.connect() as con:
        for name in TABLES:
            data[name] = [dict(row._mapping) for row in con.execute(select(source_meta.tables[name]))]
    result = {'source':src_path.name, 'mode':'commit' if commit else 'dry-run',
              'counts':{name:len(data[name]) for name in TABLES},'orphaned_pos':0,'orphaned_labor':0,'orphaned_jobs':0}
    job_ids={row['id'] for row in data['jobs']}
    customer_ids={row['id'] for row in data['customers']}
    location_ids={row['id'] for row in data['locations']}
    result['orphaned_jobs'] = sum(bool(row['customer_id'] not in customer_ids or (row.get('location_id') and row['location_id'] not in location_ids)) for row in data['jobs'])
    result['orphaned_pos'] = sum(row['job_id'] not in job_ids for row in data['purchase_orders'])
    result['orphaned_labor'] = sum(row['job_id'] not in job_ids for row in data['labor_entries'])
    if any(result[k] for k in ('orphaned_jobs','orphaned_pos','orphaned_labor')):
        raise ValueError('Snapshot has unresolved foreign keys; import blocked')
    with target_engine.begin() as con:
        target_counts={name:con.execute(select(func.count()).select_from(target_meta.tables[name])).scalar_one() for name in TABLES}
        result['target_existing_counts'] = target_counts
        if commit:
            if any(target_counts.values()):
                raise ValueError('Destination not empty; import blocked to prevent duplicate company history')
            for name in TABLES:
                if data[name]:
                    con.execute(target_meta.tables[name].insert(), data[name])
            for name in TABLES:
                count = con.execute(select(func.count()).select_from(target_meta.tables[name])).scalar_one()
                if count != len(data[name]):
                    raise ValueError(f'Count mismatch in {name}: {count} != {len(data[name])}')
    source.dispose()
    return result


def main():
    from .main import engine  # Ensures the v1.6 schema exists in the destination.
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True,help='Existing private imported company database')
    parser.add_argument('--commit',action='store_true',help='Write to empty destination; omit for dry run')
    args=parser.parse_args()
    result=migrate(args.source,engine,args.commit)
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
