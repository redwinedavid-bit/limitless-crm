"""Create/update a user locally: python -m server.create_user --username owner --role owner"""
import argparse, getpass
from sqlalchemy import select
from sqlalchemy.orm import Session
from .main import engine, Base
from .security import User, password_hash, ROLE_SCOPES

def main():
 p=argparse.ArgumentParser();p.add_argument('--username',required=True);p.add_argument('--role',required=True,choices=ROLE_SCOPES);p.add_argument('--name',default='');p.add_argument('--po-prefix',choices=['DR','AR','DB']);a=p.parse_args()
 password=getpass.getpass('Password (12+ characters): ')
 if len(password)<12:raise SystemExit('Password too short')
 Base.metadata.create_all(engine)
 with Session(engine) as db:
  user=db.scalar(select(User).where(User.username==a.username.lower()))
  if not user:user=User(username=a.username.lower(),display_name=a.name or a.username,role=a.role,password_hash=password_hash(password),po_prefix=a.po_prefix);db.add(user)
  else:user.password_hash=password_hash(password);user.role=a.role;user.display_name=a.name or user.display_name;user.active=True
  if a.po_prefix is not None:user.po_prefix=a.po_prefix
  db.commit()
 print('User saved:',a.username,'role:',a.role,'PO designator:',a.po_prefix or '(unchanged)')
if __name__=='__main__':main()
