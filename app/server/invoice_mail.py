"""Invoice PDF generation and Gmail API transport; no credentials stored in database."""
import base64
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from xml.sax.saxutils import escape

def make_invoice_pdf(invoice, job, customer, paid, lines=None, ledger=None,service_location=None,commercial=None):
    """Compact branded invoice; overflow paginates without hiding customer details."""
    from decimal import Decimal
    from .proposal_assets.limitless_template import (
        INK, ACCENT, ACCENT_BRIGHT, ACCENT_TINT, LINE, LOGO_PATH,
    )
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import LongTable
    buf=io.BytesIO()
    doc=SimpleDocTemplate(buf,pagesize=letter,leftMargin=42,rightMargin=42,
                         topMargin=126,bottomMargin=43)
    width=528
    styles={
        'body':ParagraphStyle('InvoiceBody',fontName='Body',fontSize=8.5,leading=11.5,textColor=INK),
        'label':ParagraphStyle('InvoiceLabel',fontName='Cond-Bold',fontSize=8,leading=11,textColor=ACCENT),
        'right':ParagraphStyle('InvoiceRight',fontName='Body',fontSize=8.5,leading=11.5,alignment=TA_RIGHT,textColor=INK),
        'balance':ParagraphStyle('InvoiceBalance',fontName='Cond-Bold',fontSize=14,leading=18,alignment=TA_RIGHT,textColor=ACCENT),
    }
    money=lambda n:('-' if n < 0 else '')+'${:,.2f}'.format(abs(n))
    p=lambda t,style='body':Paragraph(escape(str(t or '')).replace('\n','<br/>'),styles[style])
    commercial=commercial or {}
    def page(canvas,document):
        canvas.saveState()
        canvas.setFillColor(INK);canvas.rect(0,690,612,102,fill=1,stroke=0)
        canvas.drawImage(ImageReader(LOGO_PATH),42,713,width=180,height=61.6,mask='auto')
        canvas.setFillColor(ACCENT_BRIGHT);canvas.setFont('Cond-Bold',23)
        canvas.drawRightString(570,752,'INVOICE')
        canvas.setFillColor(colors.white);canvas.setFont('Body',10)
        canvas.setFont('Body',7.5)
        canvas.drawRightString(570,714,'859-979-0081  |  Kentucky HVAC HM05937')
        canvas.setStrokeColor(ACCENT_BRIGHT);canvas.setLineWidth(3);canvas.line(0,690,612,690)
        canvas.setStrokeColor(LINE);canvas.setLineWidth(.5);canvas.line(42,34,570,34)
        canvas.setFillColor(INK);canvas.setFont('Body',7)
        canvas.drawString(42,22,'Limitless Mechanical Services, Inc.  |  accounting@limitlessmechanical.net')
        canvas.drawRightString(570,22,f'{invoice.number}  /  {document.page}')
        canvas.restoreState()
    def table(rows,widths,**kwargs):
        t=Table(rows,colWidths=widths,hAlign='LEFT',**kwargs)
        t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
        return t
    details='Invoice number: '+str(invoice.number)+'\nIssued: '+str(invoice.issued_on or 'Not issued')+'\nDue: '+str(invoice.due_on or 'Not specified')+'\nJob: '+str(job.job_number)
    if commercial.get('customer_po'):details+='\nCustomer PO / reference: '+commercial['customer_po']
    if commercial.get('service_from') or commercial.get('service_to'):
        details+='\nService dates: '+str(commercial.get('service_from') or '')+' to '+str(commercial.get('service_to') or '')
    left=[p('BILL TO','label'),p(customer.name+'\n'+(customer.billing_address or ''))]
    if service_location:
        left.extend([Spacer(1,6),p('SERVICE LOCATION','label'),p(service_location.name+'\n'+service_location.address)])
    story=[table([[left,[p('INVOICE DETAILS','label'),p(details)]]],[300,228]),Spacer(1,12)]
    if commercial.get('job_summary'):story.extend([p('JOB SUMMARY','label'),p(commercial['job_summary']),Spacer(1,10)])
    rows=[[p('DESCRIPTION','label'),p('QTY','label'),p('UNIT','label'),p('TAX','label'),p('AMOUNT','label')]]
    lines=lines or []
    subtotal=sum((x.subtotal for x in lines),Decimal('0')) if lines else invoice.amount
    tax=sum((x.tax_amount for x in lines),Decimal('0')) if lines else Decimal('0')
    if lines:
        for line in lines:
            # Chunk exceptionally long descriptions so even a single line can paginate.
            text=str(line.description or '')
            chunks=[text[i:i+650] for i in range(0,len(text),650)] or ['']
            rows.append([p(chunks[0]),p('{:g}'.format(line.quantity)),p(money(line.unit_price),'right'),p(money(line.tax_amount),'right'),p(money(line.subtotal+line.tax_amount),'right')])
            for chunk in chunks[1:]:rows.append([p(chunk),p(''),p(''),p(''),p('')])
    else:
        text=str(job.description or 'HVAC services')
        rows.append([p(text[:650]),p('1'),p(money(invoice.amount),'right'),p('$0.00','right'),p(money(invoice.amount),'right')])
        for i in range(650,len(text),650):rows.append([p(text[i:i+650]),p(''),p(''),p(''),p('')])
    if 'separate_material_labor' in commercial and lines:
        labor=sum((line.subtotal for line in lines if str(line.description).startswith(('Labor','Call labor','Overtime labor'))),Decimal('0'))
        materials=subtotal-labor
        display=[('Labor',labor),('Materials',materials)] if commercial['separate_material_labor'] else [('Services & Materials',subtotal)]
        rows=[[p('DESCRIPTION','label'),p('AMOUNT','label')]]+[[p(name),p(money(value),'right')] for name,value in display if value]
        widths=[400,128]
    else:widths=[249,32,78,65,104]
    items=LongTable(rows,colWidths=widths,repeatRows=1,hAlign='LEFT')
    items.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),ACCENT_TINT),('LINEBELOW',(0,0),(-1,0),1,ACCENT),('LINEBELOW',(0,1),(-1,-1),.4,LINE),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
    story.extend([items,Spacer(1,12)])
    if ledger is not None:
        net_billed=ledger['net_billed'];credits=ledger['credits'];due=ledger['balance'];customer_credit=ledger['credit_balance']
    else:
        net_billed=invoice.amount;credits=Decimal('0');due=invoice.amount-paid;customer_credit=Decimal('0')
    totals=[('Subtotal',subtotal),('Sales tax',tax),('Invoice total',invoice.amount)]
    if credits:totals.extend([('Credit memos',-credits),('Net billed',net_billed)])
    totals.append(('Net payments received',paid))
    summary=table([[p(label),p(money(value),'right')] for label,value in totals]+[[p('BALANCE DUE','label'),p(money(due),'balance')]],[135,105])
    summary.setStyle(TableStyle([('BACKGROUND',(0,-1),(-1,-1),ACCENT_TINT),('TOPPADDING',(0,-1),(-1,-1),7),('BOTTOMPADDING',(0,-1),(-1,-1),7)]))
    footer_start=len(story)
    story.append(table([['',summary]],[288,240]))
    if customer_credit:story.append(p('Customer credit pending refund: '+money(customer_credit)))
    notes=[]
    if commercial.get('payment_terms'):notes.append(p('Terms: '+commercial['payment_terms']))
    if commercial.get('note'):notes.append(p(commercial['note']))
    if notes:story.extend([Spacer(1,10),p('NOTES / TERMS','label'),*notes])
    # Compact remittance block below terms, separate from the financial summary.
    remit=[p('PLEASE REMIT PAYMENT TO','label'),p(commercial.get('remit_to') or 'Limitless Mechanical Services, Inc.'),p(commercial.get('remit_address') or 'Remittance address to be confirmed'),p('Please reference invoice '+str(invoice.number)+' with payment.')]
    if commercial.get('reply_to'):remit.append(p('Billing questions: '+commercial['reply_to']))
    story.extend([Spacer(1,10),*remit])
    story[footer_start:]=[KeepTogether(story[footer_start:])]
    doc.build(story,onFirstPage=page,onLaterPages=page)
    return buf.getvalue()

def configured():
    names=('LMS_GMAIL_CLIENT_ID','LMS_GMAIL_CLIENT_SECRET','LMS_GMAIL_REFRESH_TOKEN','LMS_GMAIL_SENDER')
    return all(os.getenv(x) for x in names)

def _post(url,body,headers=None):
    req=urllib.request.Request(url,data=body,headers=headers or {},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=15) as resp:return json.loads(resp.read())
    except urllib.error.HTTPError as ex:
        raise RuntimeError('Gmail provider rejected request (HTTP '+str(ex.code)+')') from ex
    except (urllib.error.URLError,TimeoutError) as ex:
        raise RuntimeError('Gmail provider unavailable') from ex

def gmail_send(to,subject,body,pdf,filename,message_id=None):
    if not configured():raise RuntimeError('Gmail integration not configured')
    sender=os.environ['LMS_GMAIL_SENDER']
    token=_post('https://oauth2.googleapis.com/token',urllib.parse.urlencode({'client_id':os.environ['LMS_GMAIL_CLIENT_ID'],'client_secret':os.environ['LMS_GMAIL_CLIENT_SECRET'],'refresh_token':os.environ['LMS_GMAIL_REFRESH_TOKEN'],'grant_type':'refresh_token'}).encode(),{'Content-Type':'application/x-www-form-urlencoded'})['access_token']
    request=urllib.request.Request('https://gmail.googleapis.com/gmail/v1/users/me/settings/sendAs/'+urllib.parse.quote(sender,safe=''),headers={'Authorization':'Bearer '+token})
    try:
        with urllib.request.urlopen(request,timeout=15) as response:alias=json.loads(response.read())
    except Exception as exc:raise RuntimeError('Unable to verify authorized accounting send-as address') from exc
    if alias.get('sendAsEmail','').lower()!=sender.lower() or alias.get('verificationStatus')!='accepted':raise RuntimeError('Accounting send-as address is not verified')
    msg=EmailMessage()
    msg['From']=sender
    msg['To']=to
    from email.utils import getaddresses
    accounting_copy='accounting@limitlessmechanical.net'
    if accounting_copy not in {address.lower() for _,address in getaddresses([to])}:
        msg['Bcc']=accounting_copy
    msg['Reply-To']=sender
    if message_id:msg['Message-ID']=message_id
    msg['Subject']=subject
    msg.set_content(body)
    msg.add_attachment(pdf,maintype='application',subtype='pdf',filename=filename)
    raw=base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip('=')
    result=_post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',json.dumps({'raw':raw}).encode(),{'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    if not result.get('id'):raise RuntimeError('Gmail did not return a message identifier')
    return result['id']

