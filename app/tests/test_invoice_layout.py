"""Invoice branding, one-page fit, ledger accuracy and overflow integrity."""
import io,os
from pathlib import Path
from decimal import Decimal as D
from types import SimpleNamespace as NS
from pypdf import PdfReader
from server.invoice_mail import make_invoice_pdf

def sample(count=4,description=None,ledger=None):
 lines=[NS(description=description or f'HVAC service item {i+1}: customer-facing work performed',quantity=D('1'),unit_price=D('75'),subtotal=D('75'),tax_amount=D('4.50'),internal_cost='HIDDEN-INTERNAL-123') for i in range(count)]
 return make_invoice_pdf(NS(number='DEMO-1042',amount=D('79.50')*count,issued_on='2026-10-24',due_on='2026-11-23'),NS(job_number='DEMO-0048',description='Service visit'),NS(name='MOCK CUSTOMER - Example Facilities',billing_address='100 Example Street\nRichmond, KY 40475'),D('100'),lines,ledger,service_location=NS(name='North Office',address='200 Demonstration Drive\nRichmond, KY 40475'),commercial={'customer_po':'DEMO-PO-21','service_from':'2026-10-24','service_to':'2026-10-24','remit_to':'Limitless Mechanical Services, Inc.','remit_address':'167 Falling Branch Rd\nWaco, KY 40385','payment_terms':'Net 30','reply_to':'accounting@limitlessmechanical.net','note':'Demonstration only. Thank you for your business.'})

def read(data):
 r=PdfReader(io.BytesIO(data));return r,'\n'.join(p.extract_text() or '' for p in r.pages)

def save(name,data):
 if os.environ.get('LMS_PDF_VALIDATION_DIR'):
  p=Path(os.environ['LMS_PDF_VALIDATION_DIR']);p.mkdir(parents=True,exist_ok=True);(p/name).write_bytes(data)

def test_routine_invoice_one_page_and_branding():
 data=sample();r,t=read(data)
 assert len(r.pages)==1
 assert t.index('INVOICE DETAILS') < t.index('Invoice number: DEMO-1042')
 assert t.index('NOTES / TERMS') < t.index('PLEASE REMIT PAYMENT TO')
 for text in ['318.00','218.00','100.00','18.00','DEMO-PO-21','North Office','HM05937','Net 30','DEMO-1042','PLEASE REMIT PAYMENT TO']:assert text in t,text
 assert 'HIDDEN-INTERNAL' not in t
 assert any(o.get_object().get('/Subtype')=='/Image' for o in r.pages[0]['/Resources']['/XObject'].values())
 save('invoice-sample.pdf',data)

def test_large_invoice_retains_every_line_and_total():
 data=sample(60);r,t=read(data)
 assert len(r.pages)>1
 for i in range(60):assert f'HVAC service item {i+1}:' in t
 assert '4,770.00' in t and '4,670.00' in t
 for page in r.pages:assert 'DESCRIPTION' in page.extract_text() or 'BALANCE DUE' in page.extract_text()
 save('invoice-overflow.pdf',data)

def test_credit_ledger_and_long_single_description():
 ledger={'net_billed':D('298'),'credits':D('20'),'balance':D('198'),'credit_balance':D('0')}
 _,t=read(sample(4,ledger=ledger))
 assert '-$20.00' in t and '$298.00' in t and '$198.00' in t
 long='Confirmed work performed. '*210+'END-OF-DESCRIPTION'
 data=sample(1,description=long);r,t=read(data);assert len(r.pages)>1 and 'END-OF-DESCRIPTION' in t
 save('invoice-long-description.pdf',data)
