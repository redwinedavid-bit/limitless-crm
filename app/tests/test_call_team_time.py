"""Every field team role can be assigned; each visit retains its own time."""
from datetime import timedelta
from server import main as m
from sqlalchemy.orm import Session
from test_time_clock import setup_clock


def test_admin_assignment_and_multiple_visit_time(setup_clock):
    clients,orders,now,tech_id,job=setup_clock
    owner=clients['owner'];owner_id=owner.get('/api/auth/me').json()['id'];wo=orders[0]
    ids={u['id'] for u in owner.get('/api/technicians').json()}
    assert owner_id in ids and tech_id in ids
    assert owner_id in {u['id'] for u in owner.get('/api/calls/options').json()['technicians']}
    def entry(day,person=None):
        start=now[0]-timedelta(days=day)
        return {'started_at':start.isoformat(),'ended_at':(start+timedelta(hours=2)).isoformat(),'technician_id':person,'note':'Return visit'}
    assert owner.post(f'/api/work-orders/{wo}/time',json=entry(4)).status_code==422
    start=now[0]+timedelta(days=5)
    schedule={'technician_id':owner_id,'scheduled_start':start.isoformat(),'scheduled_end':(start+timedelta(hours=2)).isoformat()}
    assert owner.post(f'/api/work-orders/{wo}/assignments',json=schedule).status_code==201
    assert any(a['technician_id']==owner_id for a in owner.get('/api/dispatch').json())
    assert owner.post(f'/api/work-orders/{wo}/time',json=entry(4)).status_code==201
    assert owner.post(f'/api/work-orders/{wo}/time',json=entry(3)).status_code==201
    assert owner.post(f'/api/work-orders/{wo}/time',json=entry(3)).status_code==409
    assert owner.post(f'/api/work-orders/{wo}/time',json=entry(2,tech_id)).status_code==201
    assert clients['tech'].post(f'/api/work-orders/{wo}/time',json=entry(1,owner_id)).status_code==403
    assert clients['other'].get(f'/api/work-orders/{wo}/time').status_code==403
    rows=owner.get(f'/api/work-orders/{wo}/time').json()
    assert len(rows)==3 and sum(t['hours'] for t in rows)==6
    assert all(not t['approved'] and t['note']=='Return visit' for t in rows)
    assert len(clients['tech'].get(f'/api/work-orders/{wo}/time').json())==1
    # Office admins are eligible too, without changing a technician's access scopes.
    with Session(m.engine) as db:
        other=db.get(m.User,clients['other'].get('/api/auth/me').json()['id']);other.role='office';oid=other.id;db.commit()
    assert oid in {u['id'] for u in owner.get('/api/technicians').json()}
    schedule['technician_id']=oid
    assert owner.post(f'/api/work-orders/{wo}/assignments',json=schedule).status_code==201
    assert clients['other'].post(f'/api/work-orders/{wo}/time',json=entry(1)).status_code==201


def test_call_time_controls_match_script():
    import re
    from pathlib import Path
    root=Path(__file__).parents[1]
    html=(root/'call-workspace.html').read_text();script=(root/'call-workspace.js').read_text()
    ids=set(re.findall(r'\bid="([^"]+)"',html))
    refs=set(re.findall(r"by\('([^']+)'\)",script))
    assert refs <= ids,refs-ids
    assert html.index('id="manual-time"') < html.index('id="time-list"')
