"""Local login/session foundation. Requires TLS and deployment hardening before internet use."""
import hashlib, hmac, os, secrets, uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from .main import Base

ROLE_SCOPES = {
 'owner': {'read_all','manage_customers','manage_jobs','manage_pos','view_financials','manage_users'},
 'office': {'read_all','manage_customers','manage_jobs','manage_pos','view_financials'},
 'dispatcher': {'read_all','manage_jobs'},
 'estimator': {'read_all','manage_customers','manage_jobs','manage_pos','view_financials'},
 'technician': {'read_assigned'},
 'bookkeeper': {'read_all','view_financials'},
}
class User(Base):
 __tablename__='users'
 id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
 username:Mapped[str]=mapped_column(String(120),unique=True,nullable=False)
 display_name:Mapped[str]=mapped_column(String(160),nullable=False)
 password_hash:Mapped[str]=mapped_column(String(250),nullable=False)
 role:Mapped[str]=mapped_column(String(30),nullable=False)
 po_prefix:Mapped[str|None]=mapped_column(String(3),nullable=True,unique=True)
 active:Mapped[bool]=mapped_column(default=True,nullable=False)
class UserSession(Base):
 __tablename__='user_sessions'
 token_hash:Mapped[str]=mapped_column(String(64),primary_key=True)
 user_id:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),nullable=False)
 expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)

def password_hash(password):
 salt=secrets.token_bytes(16)
 digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,600000)
 return 'pbkdf2_sha256$600000$'+salt.hex()+'$'+digest.hex()
def verify_password(password,stored):
 try:
  method,rounds,salt,digest=stored.split('$')
  if method!='pbkdf2_sha256':return False
  calculated=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),int(rounds))
  return hmac.compare_digest(calculated,bytes.fromhex(digest))
 except (ValueError,TypeError):return False

def token_hash(token):return hashlib.sha256(token.encode()).hexdigest()
def make_token():return secrets.token_urlsafe(48)
def expiry():return datetime.now(timezone.utc)+timedelta(hours=12)
