"""Operator-run office queue worker; scheduling/provisioning is intentionally external.
python -m server.office_worker --admin-username NAME [--prepare-reminders] [--process-mail]
"""
import argparse,json
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import main as m
from .billing_v111 import MailOutbox,prepare_reminders,process_mail
from .security import User

def main():
 parser=argparse.ArgumentParser(description='Process authorized CRM office queues')
 parser.add_argument('--admin-username',required=True)
 parser.add_argument('--prepare-reminders',action='store_true')
 parser.add_argument('--process-mail',action='store_true')
 args=parser.parse_args();result={}
 with Session(m.engine) as db:
  user=db.scalar(select(User).where(User.username==args.admin_username,User.active==True,User.role=='owner'))
  if not user:raise SystemExit('An active admin account is required')
  if args.prepare_reminders:result['prepared']=prepare_reminders(db,{'id':user.id,'role':'owner'});db.commit()
  queued=list(db.scalars(select(MailOutbox.id).where(MailOutbox.status=='Queued')))
 if args.process_mail:result['processed']=[process_mail(id) for id in queued]
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
