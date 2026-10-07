"""역할별 기존 세션을 통합 로그인에서 일관되게 해석한다."""
from flask import session, redirect
from models.models import db, User, Worker

USER_KEYS = ('user_id', 'user_phone', 'user_token')
WORKER_KEYS = ('admin_id', 'admin_worker_id', 'admin_name')
DESTINATIONS = {'user': '/user', 'worker': '/admin'}


def select_role(role):
    for key in WORKER_KEYS if role == 'user' else USER_KEYS:
        session.pop(key, None)
    session['login_role'] = role


def current_role():
    user_present = any(session.get(key) is not None for key in USER_KEYS)
    worker_present = any(session.get(key) is not None for key in WORKER_KEYS)
    marker = session.get('login_role')
    if user_present and worker_present:
        session.clear()
        return None
    role = 'user' if user_present else 'worker' if worker_present else None
    if not role:
        session.pop('login_role', None)
        return None
    if marker is not None and marker != role:
        session.clear()
        return None
    if role == 'user':
        user = db.session.get(User, session.get('user_id')) if session.get('user_id') else None
        valid = user and user.is_active and user.session_token and user.session_token == session.get('user_token')
    else:
        worker = Worker.query.filter_by(login_id=session.get('admin_id')).first() if session.get('admin_id') else None
        valid = worker and (not session.get('admin_worker_id') or worker.worker_id == session['admin_worker_id'])
    if not valid:
        session.clear()
        return None
    session['login_role'] = role
    return role


def role_redirect(expected):
    role = current_role()
    if role != expected:
        return redirect(DESTINATIONS.get(role, '/login'))
    return None
