"""Run from a TRUSTED server scheduler after production deployment.
Example: python -m server.schedule_maintenance --days-ahead 7
Idempotent: pending inspections/previously generated due dates are skipped.
No technician assignment or customer email is created automatically.
"""
from __future__ import annotations
import argparse
from datetime import date,timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from .main import engine,log
from .proposals_pm_v15 import PMAgreement, schedule_agreement


def run(days_ahead=7):
    if not 0 <= days_ahead <= 365:raise ValueError('days_ahead must be 0..365')
    through=date.today()+timedelta(days=days_ahead)
    summary={'agreements':0,'created':0,'skipped':0,'through_date':through.isoformat()}
    with Session(engine) as db:
        for agreement in db.scalars(select(PMAgreement).where(PMAgreement.status=='Active')).all():
            output=schedule_agreement(db,agreement,through)
            summary['agreements']+=1;summary['created']+=len(output['created']);summary['skipped']+=len(output['skipped'])
        db.commit()
    return summary

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Generate due PM work orders for active Limitless agreements')
    parser.add_argument('--days-ahead',type=int,default=7)
    args=parser.parse_args()
    print(run(args.days_ahead))
