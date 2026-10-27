"""Run in authenticated Cloud Shell with a dedicated Web OAuth client JSON file.

Stores credentials in Secret Manager and adds references to the existing CRM service.
Does not connect mailboxes, switch preview mode off, or send any email.
"""
import argparse,base64,json,secrets,subprocess

PROJECT='limitless-crm-512612'
SERVICE='lms-crm-test'
REGION='us-central1'

def run(*args,data=None):
 result=subprocess.run(['gcloud',*args,'--project='+PROJECT,'--quiet'],input=data,text=True,capture_output=True)
 if result.returncode:raise RuntimeError('Google Cloud operation failed: '+result.stderr[:800])
 return result.stdout

def ensure_secret(name,value=None,create_only=False):
 exists=subprocess.run(['gcloud','secrets','describe',name,'--project='+PROJECT,'--format=value(name)'],capture_output=True).returncode==0
 if not exists:run('secrets','create',name,'--replication-policy=automatic')
 if create_only and exists:return
 run('secrets','versions','add',name,'--data-file=-',data=value)

def configure(path,enable_sending=False):
 with open(path,encoding='utf-8') as handle:client=json.load(handle).get('web',{})
 service=json.loads(run('run','services','describe',SERVICE,'--region='+REGION,'--format=json'))
 template=service['spec']['template']['spec'];runtime=template['serviceAccountName']
 env=template['containers'][0].get('env',[])
 origin=next((item.get('value','') for item in env if item['name']=='APP_PUBLIC_URL'),'').rstrip('/')
 if not origin.startswith('https://'):raise RuntimeError('CRM public HTTPS origin is missing')
 callback=origin+'/api/admin/email/oauth/callback'
 if callback not in client.get('redirect_uris',[]) or not client.get('client_id') or not client.get('client_secret'):
  raise RuntimeError('Use a dedicated Web OAuth client JSON with redirect URI '+callback)
 values={'lms-email-client-id':client['client_id'],'lms-email-client-secret':client['client_secret']}
 for name,value in values.items():ensure_secret(name,value)
 ensure_secret('lms-email-token-key',base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),create_only=True)
 for name in (*values,'lms-email-token-key'):
  run('secrets','add-iam-policy-binding',name,'--member=serviceAccount:'+runtime,'--role=roles/secretmanager.secretAccessor','--format=none')
 run('services','enable','gmail.googleapis.com')
 refs='LMS_GMAIL_CLIENT_ID=lms-email-client-id:latest,LMS_GMAIL_CLIENT_SECRET=lms-email-client-secret:latest,LMS_EMAIL_TOKEN_KEY=lms-email-token-key:latest'
 args=['run','services','update',SERVICE,'--region='+REGION,'--update-secrets='+refs]
 if enable_sending:args.append('--update-env-vars=LMS_ALLOW_EXTERNAL_EMAIL=1')
 run(*args)
 print('Secure email setup complete. In the CRM, open Email accounts & quotes and connect each exact mailbox.')
 print('Invoice preview mode stays unchanged. No email has been sent.')

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('client_json');parser.add_argument('--enable-sending',action='store_true',help='Enable the server delivery gate; the CRM preview setting still applies')
 options=parser.parse_args();configure(options.client_json,options.enable_sending)

