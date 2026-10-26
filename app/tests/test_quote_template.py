"""Retained proposal template, privacy, exact deposits and revision immutability."""
import io,json,uuid,os
from datetime import date,timedelta
from decimal import Decimal
from pathlib import Path
import pytest
from sqlalchemy import select,delete
from sqlalchemy.orm import Session
from test_v15 import client,setup,quote_body
from server import main as m
from server.quote_template import QuotePresentation

@pytest.fixture
def proposal():
 c,tag=client();customer,job,asset=setup(c,tag);body=quote_body(job,tag)
 body['title']='MOCK CUSTOMER - HVAC replacement proposal'
 body['options']=[{'name':'Base replacement','description':'Replace confirmed equipment and verify operation.','price':'15001.01','estimated_cost':'12345.67'}]
 body['presentation']={'template':'limitless','proposal_date':str(date.today()),'valid_until':str(date.today()+timedelta(days=30)),'equipment_schedule':[{'component':'Heat pump','description':'Confirmed demonstration model DEMO-60'}],'client_contact':'Demo Facilities Manager','deposit_percent':'50'}
 r=c.post('/api/estimates/advanced',json=body);assert r.status_code==201,r.text
 return c,body,r.json(),customer

def pdf_text(data):
 from pypdf import PdfReader
 doc=PdfReader(io.BytesIO(data));return '\n'.join(p.extract_text() or '' for p in doc.pages),len(doc.pages)

def test_branded_pdf_logo_payment_rounding_and_cost_privacy(proposal,tmp_path):
 c,body,q,customer=proposal;r=c.get('/api/estimates/advanced/'+q['id']+'/pdf');assert r.status_code==200,r.text
 text,pages=pdf_text(r.content);assert pages>=2
 for value in ['15,001.01','7,500.51','7,500.50','DEMO-60','HM05937','Facilities Manager','Investment','Acceptance']:
  assert value.lower() in text.lower(),value
 assert 'DEPOSIT(50%)' in ''.join(text.split())
 assert '12,345.67' not in text and '12345.67' not in text and 'estimated_cost' not in text
 from pypdf import PdfReader
 doc=PdfReader(io.BytesIO(r.content));images=doc.pages[0]['/Resources']['/XObject'].get_object();assert any(o.get_object().get('/Width')==900 for o in images.values())
 # A customer-safe validation sample is an intermediate, not a real customer proposal.
 out=Path(os.getenv('LMS_PDF_VALIDATION_DIR',str(tmp_path)));out.mkdir(exist_ok=True);(out/'sample.pdf').write_bytes(r.content)

def test_presentation_revision_history_and_accepted_party_snapshot(proposal):
 c,body,q,customer=proposal;edit={k:v for k,v in body.items() if k not in ('job_number','number')};edit['expected_revision']=1
 edit['presentation']={**body['presentation'],'project_subtitle':'Revised customer-facing subtitle'}
 r=c.put('/api/estimates/advanced/'+q['id'],json=edit);assert r.status_code==200,r.text
 history=c.get('/api/estimates/advanced/'+q['id']+'/revisions').json();assert history[-1]['snapshot']['presentation']['project_subtitle']=='Revised customer-facing subtitle'
 assert c.put('/api/estimates/advanced/'+q['id'],json=edit).status_code==409
 accepted=c.post('/api/estimates/advanced/'+q['id']+'/record-acceptance',json={'selected_option':0,'accepted_by':'Demo Customer','reference':'Mock authorization only','customer_authorization_confirmed':True});assert accepted.status_code==200,accepted.text
 assert c.put('/api/estimates/advanced/'+q['id'],json={**edit,'expected_revision':2}).status_code==409
 with Session(m.engine) as db:
  db.get(m.Customer,customer['id']).name='CHANGED NAME MUST NOT REPLACE ACCEPTED CUSTOMER';db.commit()
 text,_=pdf_text(c.get('/api/estimates/advanced/'+q['id']+'/pdf').content)
 assert customer['name'] in text and 'CHANGED NAME' not in text and 'Demo Customer' in text

def test_fixed_deposit_and_options_validate_on_later_price_edits(proposal):
 c,body,q,customer=proposal;edit={k:v for k,v in body.items() if k not in ('job_number','number')};edit['expected_revision']=1;edit['presentation']={**body['presentation'],'deposit_percent':None,'deposit_amount':'20000'}
 assert c.put('/api/estimates/advanced/'+q['id'],json=edit).status_code==422
 edit['presentation']['deposit_amount']='10000';r=c.put('/api/estimates/advanced/'+q['id'],json=edit);assert r.status_code==200,r.text
 # Omitting presentation does not discard or bypass the saved fixed deposit.
 del edit['presentation'];edit['expected_revision']=2;edit['options']=[{**body['options'][0],'price':'9000'}]
 assert c.put('/api/estimates/advanced/'+q['id'],json=edit).status_code==422


def test_existing_quotes_also_use_the_standard_layout(proposal):
 c,body,q,customer=proposal
 with Session(m.engine) as db:db.execute(delete(QuotePresentation).where(QuotePresentation.proposal_id==q['id']));db.commit()
 old=c.get('/api/estimates/advanced/'+q['id']).json();assert old['presentation']=={'template':'limitless'}
 text,_=pdf_text(c.get('/api/estimates/advanced/'+q['id']+'/pdf').content);assert 'project understanding' in text.lower() and 'investment' in text.lower()


def test_public_view_includes_customer_equipment_and_exact_deposits(proposal):
 c,body,q,customer=proposal
 grant=c.post('/api/admin/customer-links',json={'kind':'Quote','proposal_id':q['id']}).json();token=grant['url'].split('#')[1]
 view=c.post('/api/admin/customer-preview/resolve',json={'token':token});assert view.status_code==200,view.text
 assert 'DEMO-60' in view.text and '12345.67' not in view.text
 option=view.json()['quote']['options'][0];assert option['deposit']=='7500.51' and option['balance']=='7500.50'


def test_net30_no_invented_deposit_or_expiry_and_template_permissions(proposal):
 from fastapi.testclient import TestClient
 c,body,q,customer=proposal;edit={k:v for k,v in body.items() if k not in ('job_number','number')};edit['expected_revision']=1;edit['presentation']={'template':'limitless'}
 assert c.put('/api/estimates/advanced/'+q['id'],json=edit).status_code==200
 text,_=pdf_text(c.get('/api/estimates/advanced/'+q['id']+'/pdf').content)
 assert 'DEPOSIT (50%)' not in text and q['number'] in text
 assert TestClient(m.app).get('/api/quote-templates').status_code==401
 tech,_=client('technician');assert tech.get('/api/quote-templates').status_code==403 and tech.get('/api/estimates/advanced/'+q['id']+'/pdf').status_code==403
 assert c.get('/api/quote-templates').json()['default']=='limitless'


def test_long_scope_and_alternatives_render_without_lost_text(proposal,tmp_path):
 c,body,q,customer=proposal;edit={k:v for k,v in body.items() if k not in ('job_number','number')};edit['expected_revision']=1
 edit['scope']='Inspect and replace equipment. '*450+' FINAL SCOPE LINE'
 edit['options']=[{**body['options'][0],'description':'Customer-facing alternative description. '*60},{'name':'Alternate equipment','description':'Second fully described scope','price':'14000.00','estimated_cost':'7654.32'}]
 assert c.put('/api/estimates/advanced/'+q['id'],json=edit).status_code==200
 r=c.get('/api/estimates/advanced/'+q['id']+'/pdf');assert r.status_code==200,r.text
 text,pages=pdf_text(r.content);assert 'FINAL SCOPE LINE' in text and 'Alternate equipment' in text and '14,000.00' in text
 assert '7,654.32' not in text
 out=Path(os.getenv('LMS_PDF_VALIDATION_DIR',str(tmp_path)));out.mkdir(parents=True,exist_ok=True);(out/'long-sample.pdf').write_bytes(r.content)


def test_simple_legacy_proposals_use_identical_layout_and_reject_other_templates():
 c,tag=client();customer,job,asset=setup(c,tag)
 q=c.post('/api/proposals',json={'job_number':job,'number':'LEGACY-'+tag,'title':'Simple repair','scope':'Replace the failed control and verify operation.','total':'450.00'});assert q.status_code==201,q.text
 r=c.get('/api/proposals/'+q.json()['id']+'/pdf');assert r.status_code==200,r.text
 text,_=pdf_text(r.content);assert 'project understanding' in text.lower() and '450.00' in text and 'acceptance' in text.lower()
 body=quote_body(job,tag+'A');body['presentation']={'template':'classic'}
 assert c.post('/api/estimates/advanced',json=body).status_code==422
 templates=c.get('/api/quote-templates').json();assert len(templates['templates'])==1
