"""Bundled !limitless proposal presentation, with customer-only PDF mapping."""
import io,json,textwrap
from datetime import date
from decimal import Decimal,ROUND_HALF_UP
from html import escape
from typing import Literal
from pydantic import BaseModel,Field,ConfigDict,model_validator
from sqlalchemy import ForeignKey,Text
from sqlalchemy.orm import Mapped,mapped_column
from . import main as m

class QuotePresentation(m.Base):
 __tablename__='quote_presentations_v113'
 proposal_id:Mapped[str]=mapped_column(ForeignKey('proposals.id',ondelete='RESTRICT'),primary_key=True)
 json:Mapped[str]=mapped_column(Text,nullable=False)

class EquipmentRow(BaseModel):
 model_config=ConfigDict(extra='forbid')
 component:str=Field(min_length=1,max_length=120)
 description:str=Field(min_length=1,max_length=2000)

class PresentationIn(BaseModel):
 model_config=ConfigDict(extra='forbid')
 template:Literal['limitless']='limitless'
 proposal_date:date=Field(default_factory=date.today)
 valid_until:date|None=None
 project_subtitle:str=Field(default='',max_length=300)
 project_understanding:str=Field(default='',max_length=10000)
 client_contact:str=Field(default='',max_length=250)
 client_phone:str=Field(default='',max_length=80)
 equipment_schedule:list[EquipmentRow]=Field(default_factory=list,max_length=60)
 deposit_percent:Decimal|None=Field(default=None,ge=0,le=100,max_digits=5,decimal_places=2)
 deposit_amount:Decimal|None=Field(default=None,ge=0,max_digits=14,decimal_places=2)
 @model_validator(mode='after')
 def valid(self):
  if self.valid_until and self.valid_until<self.proposal_date:raise ValueError('Expiry cannot precede the proposal date')
  if self.deposit_amount is not None and self.deposit_percent is not None:raise ValueError('Use a deposit percentage or fixed amount, not both')
  if any(not r.component.strip() or not r.description.strip() for r in self.equipment_schedule):raise ValueError('Enter equipment labels and descriptions')
  return self

COMPANY={'name':'LIMITLESS','name_full':'Limitless Mechanical Services, Inc.','name_line':'LIMITLESS MECHANICAL SERVICES, INC.','tagline':'MECHANICAL SERVICES, INC.','prepared_by':'David Redwine, Owner','contact':'859-979-0081<br/>redwine.david@limitlessmechanical.net','license':'Kentucky HVAC HM05937','footer_left':'167 Falling Branch Rd  |  Waco, KY 40385  |  859-979-0081','closer_line':'David Redwine, Owner  |  859-979-0081  |  redwine.david@limitlessmechanical.net  |  KY HVAC HM05937'}

def presentation(db,proposal_id):
 row=db.get(QuotePresentation,proposal_id)
 values=json.loads(row.json) if row else {}
 return {**values,'template':'limitless'}

def save_presentation(db,proposal_id,data,options):
 if data is None:
  existing=db.get(QuotePresentation,proposal_id)
  if not existing:return
  data=PresentationIn(**{**json.loads(existing.json),'template':'limitless'})
 if data.deposit_amount is not None and (len(options)!=1 or data.deposit_amount>Decimal(str(options[0].price))):
  from fastapi import HTTPException
  raise HTTPException(422,'A fixed deposit requires one option and cannot exceed its price; use a percentage for alternatives')
 row=db.get(QuotePresentation,proposal_id)
 if not row:row=QuotePresentation(proposal_id=proposal_id);db.add(row)
 row.json=data.model_dump_json();db.flush()

def document_context(db,quote):
 job=db.get(m.Job,quote.job_id);customer=db.get(m.Customer,job.customer_id);site=db.get(m.Location,job.location_id) if job.location_id else None
 return {'customer':customer.name,'customer_phone':customer.phone or '', 'site':site.name if site else '', 'site_address':site.address if site else '', 'job_number':job.job_number}

def safe(value):return escape(str(value or '')).replace('\n','<br/>')
def chunks(value):
 result=[]
 for line in str(value or '').splitlines():
  if line.strip():result.extend(textwrap.wrap(line.strip(),width=700,break_long_words=True,break_on_hyphens=False))
 return result

def render(db,quote,detail):
 from .proposal_assets.limitless_template import build_proposal_pdf
 frozen=json.loads(detail.accepted_snapshot) if quote.status=='Approved' and detail.accepted_snapshot else None
 values=frozen or {'number':quote.number,'title':quote.title,'scope':quote.scope,'inclusions':detail.inclusions,'exclusions':detail.exclusions,'warranty':detail.warranty,'payment_terms':detail.payment_terms,'project_timeline':detail.project_timeline,'options':json.loads(detail.options_json),'revision':detail.revision,'presentation':presentation(db,quote.id),'document_context':document_context(db,quote)}
 config=values.get('presentation') or presentation(db,quote.id);context=values.get('document_context') or document_context(db,quote);options=values['options']
 selected=detail.selected_option if quote.status=='Approved' and detail.selected_option is not None else 0
 main=options[selected];priced=[]
 for i,o in enumerate(options):
  amount=m.cents(o['price']);deposit=None
  if config.get('deposit_percent') is not None:deposit=(amount*Decimal(str(config['deposit_percent']))/100).quantize(Decimal('.01'),rounding=ROUND_HALF_UP);label='DEPOSIT ('+format(Decimal(str(config['deposit_percent'])).normalize(),'f')+'%)'
  elif config.get('deposit_amount') is not None:deposit=Decimal(str(config['deposit_amount']));label='DEPOSIT'
  suffix=' / ACCEPTED' if quote.status=='Approved' and i==selected else ''
  priced.append({'name':safe(o['name']+suffix),'description':safe(o.get('description','')),'total_price':f'${amount:,.2f}','show_deposit':deposit is not None,'deposit':f'${deposit:,.2f}' if deposit is not None else '', 'balance':f'${amount-deposit:,.2f}' if deposit is not None else '', 'deposit_label':label if deposit is not None else '', 'balance_label':'BALANCE'})
 scope=[('',safe(v)) for v in chunks(values['scope'])]
 if values.get('inclusions'):scope += [('Included:',safe(v)) for v in chunks(values['inclusions'])]
 if values.get('project_timeline'):scope += [('Schedule:',safe(v)) for v in chunks(values['project_timeline'])]
 acceptance='By signing below, the customer authorizes the selected scope and acknowledges the stated price, payment terms, warranty and exclusions.'
 if len(options)>1:acceptance+=' Select one option: '+'; '.join(safe(o['name']) for o in options)+'.'
 if quote.status=='Approved' and detail.acceptance_name:acceptance+=' Approval recorded for '+safe(main['name'])+' by '+safe(detail.acceptance_name)+', '+safe(detail.accepted_at.date() if detail.accepted_at else '')+'. Reference: '+safe(detail.acceptance_reference)+'.'
 data={'company':COMPANY.copy(),'project_title':safe(values['title']),'project_subtitle':safe(config.get('project_subtitle') or ('Proposal '+values['number']+' / Revision '+str(values['revision'])+' / Job '+context['job_number'])),'prepared_for':safe(context['customer'])+('<br/>'+safe(config['client_contact']) if config.get('client_contact') else ''),'project_site':safe(context['site'])+('<br/>'+safe(context['site_address']) if context['site_address'] else ''),'client_phone':safe(config.get('client_phone') or context.get('customer_phone') or 'Not entered'),'proposal_date':safe(config.get('proposal_date') or quote.created_at.date()),'total_price':priced[selected]['total_price'],'price_description':safe((main.get('description') or values['title'])[:240])+('<br/>Price shown for '+safe(main['name'])+'. Select one alternative in Section 06.' if len(options)>1 and quote.status!='Approved' else ''),'validity_label':'Proposal validity' if config.get('valid_until') else 'Proposal reference','validity_text':('Valid through '+safe(config['valid_until'])+'.') if config.get('valid_until') else safe(values['number']+' / Revision '+str(values['revision'])),'project_understanding':safe(config.get('project_understanding') or 'This proposal describes the scope, equipment and commercial terms for the project listed above.'),'equipment_schedule':[(safe(r['component']),safe(r['description'])) for r in config.get('equipment_schedule',[])] or [('Equipment','As described in the included scope; no separate equipment schedule entered.')],'scope_of_work':scope,'warranty_text':safe(values.get('warranty') or 'No warranty terms entered in this proposal.'),'warranty_note':'','exclusions':[safe(v) for v in chunks(values.get('exclusions'))] or ['No separate exclusions or clarifications entered.'],'pricing_options':priced,'payment_note':safe(values.get('payment_terms') or 'No payment terms entered.'),'acceptance_text':acceptance,'thank_you':'Thank you for the opportunity to provide this proposal. We appreciate your consideration.'}
 buffer=io.BytesIO();build_proposal_pdf(data,buffer);return buffer.getvalue()



def legacy_detail(quote):
 from types import SimpleNamespace
 return SimpleNamespace(inclusions='',exclusions='',warranty='',payment_terms='',project_timeline='',options_json=json.dumps([{'name':'Proposal','description':quote.title,'price':str(quote.total)}]),revision=1,selected_option=0 if quote.status=='Approved' else None,acceptance_name='',acceptance_reference='',accepted_at=None,accepted_snapshot='')
