"""Transport includes the same PDF for accounting without a second send."""
import base64
import io
import json
from email import policy
from email.parser import BytesParser
import pytest
from server import invoice_mail

@pytest.mark.parametrize('recipient,copy',[('customer@example.com',True),('Accounting <accounting@limitlessmechanical.net>',False)])
def test_pdf_copy_to_accounting(monkeypatch,recipient,copy):
    for key in ('CLIENT_ID','CLIENT_SECRET','REFRESH_TOKEN'):monkeypatch.setenv('LMS_GMAIL_'+key,'test-only')
    monkeypatch.setenv('LMS_GMAIL_SENDER','accounting@limitlessmechanical.net')
    sent=[]
    def post(url,body,headers):
        if 'oauth2.googleapis.com' in url:return {'access_token':'test-only'}
        raw=json.loads(body)['raw'];sent.append(BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4))))
        return {'id':'test-message'}
    monkeypatch.setattr(invoice_mail,'_post',post)
    monkeypatch.setattr(invoice_mail.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(json.dumps({'sendAsEmail':'accounting@limitlessmechanical.net','verificationStatus':'accepted'}).encode()))
    pdf=b'%PDF-1.4 test invoice'
    assert invoice_mail.gmail_send(recipient,'Invoice 212','Attached',pdf,'Invoice-212.pdf',message_id='<stable@limitlessmechanical.net>')=='test-message'
    assert len(sent)==1
    message=sent[0]
    assert message['To']==recipient and message['Message-ID']=='<stable@limitlessmechanical.net>'
    assert (str(message['Bcc']) if message['Bcc'] else None)==('accounting@limitlessmechanical.net' if copy else None)
    attachment=list(message.iter_attachments())[0]
    assert attachment.get_payload(decode=True)==pdf and attachment.get_filename()=='Invoice-212.pdf'
