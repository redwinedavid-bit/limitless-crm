"""One-time private Cloud Run Job: never resets an existing user's password."""
import os
from sqlalchemy import select
from sqlalchemy.orm import Session
from .main import engine
from .security import User, password_hash

def main():
    password = os.environ.get('LMS_BOOTSTRAP_PASSWORD', '')
    if len(password) < 24:
        raise SystemExit('Bootstrap password must have at least 24 characters')
    with Session(engine) as db:
        if db.scalar(select(User).where(User.username == 'david')):
            raise SystemExit('Owner already exists; no changes made')
        db.add(User(username='david', display_name='David Redwine', role='owner',
                    password_hash=password_hash(password), po_prefix='DR'))
        db.commit()
    print('Private test owner created. No password was logged.')

if __name__ == '__main__':
    main()
