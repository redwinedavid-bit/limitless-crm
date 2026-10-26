from server.deploy_config import validate_settings
import pytest
from fastapi.testclient import TestClient
from server.main import app


def test_staging_requires_private_db_https_and_secure_cookie():
    required = {'APP_ENV':'staging', 'DATABASE_URL':'postgresql+psycopg://u:password@localhost/db',
       'APP_PUBLIC_URL':'https://crm-staging.example.com', 'ALLOWED_HOSTS':'crm-staging.example.com', 'COOKIE_SECURE':'1'}
    assert validate_settings(required)['mode'] == 'staging'
    for key,val in [('DATABASE_URL','sqlite:///unsafe.db'),('APP_PUBLIC_URL','http://crm-staging.example.com'),
                    ('COOKIE_SECURE','0'),('ALLOWED_HOSTS','*')]:
        with pytest.raises(RuntimeError):validate_settings({**required,key:val})
    with pytest.raises(RuntimeError):validate_settings({**required,'APP_ENV':'production'})


def test_api_cache_and_security_headers():
    with TestClient(app) as client:
        r=client.get('/api/health')
        assert r.status_code == 200
        assert r.headers['x-content-type-options'] == 'nosniff'
        assert r.headers['cache-control'] == 'no-store'


def test_dry_run_and_commit_cannot_double_import(tmp_path):
    import uuid
    from sqlalchemy import create_engine, select, func
    from server.main import Base,Customer,Job,Location,PurchaseOrder,LaborEntry
    from server.import_staging_snapshot import migrate
    src=create_engine('sqlite:///'+str(tmp_path/'source.sqlite3'))
    dest=create_engine('sqlite:///'+str(tmp_path/'target.sqlite3'))
    Base.metadata.create_all(src);Base.metadata.create_all(dest)
    customer_id=str(uuid.uuid4());job_id=str(uuid.uuid4());location_id=str(uuid.uuid4())
    with src.begin() as con:
        con.execute(Customer.__table__.insert(),[{'id':customer_id,'name':'QA Example'}])
        con.execute(Location.__table__.insert(),[{'id':location_id,'customer_id':customer_id,'name':'Plant'}])
        con.execute(Job.__table__.insert(),[{'id':job_id,'job_number':'LMS9988','customer_id':customer_id,'location_id':location_id}])
        con.execute(PurchaseOrder.__table__.insert(),[{'id':str(uuid.uuid4()),'po_number':'DR9999','job_id':job_id,'vendor':'Example','amount':40}])
        con.execute(LaborEntry.__table__.insert(),[{'id':str(uuid.uuid4()),'job_id':job_id,'employee':'Example','hours':2}])
    source=str(tmp_path/'source.sqlite3')
    summary=migrate(source,dest,allow_sqlite_test_target=True)
    assert summary['counts']=={'customers':1,'locations':1,'jobs':1,'purchase_orders':1,'labor_entries':1}
    with dest.connect() as con:assert con.execute(select(func.count()).select_from(Customer.__table__)).scalar_one()==0
    migrate(source,dest,commit=True,allow_sqlite_test_target=True)
    with pytest.raises(ValueError,match='not empty'):
        migrate(source,dest,commit=True,allow_sqlite_test_target=True)
