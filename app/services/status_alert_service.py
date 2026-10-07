"""기존 시스템 등급의 악화를 저장한다. 외부 AI 호출은 하지 않는다."""
import datetime as dt
import threading
import time

from flask import current_app
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError
from models.models import db, User, HealthStatus, LoginHistory, StatusAlertState, StatusAlert
from services.social_worker_ai_service import calculate_risk
from services.health_comparison_service import system_status

LEVEL_ORDER = {'SAFE': 0, 'WATCH': 1, 'WARN': 2, 'DANGER': 3}


def reset_baseline(user_id):
    """담당 변경/비활성화와 같은 트랜잭션에서 호출한다. 과거 알림은 보존한다."""
    db.session.execute(update(StatusAlertState).where(StatusAlertState.user_id == user_id).values(
        worker_id=None, snapshot=None, version=StatusAlertState.version + 1))


def _snapshot(user, now):
    health = HealthStatus.query.filter(HealthStatus.user_id == user.user_id,
        HealthStatus.recorded_at <= now).order_by(HealthStatus.recorded_at.desc(), HealthStatus.status_id.desc()).all()
    logins = LoginHistory.query.filter(LoginHistory.user_id == user.user_id,
        LoginHistory.auth_time <= now).order_by(LoginHistory.auth_time.desc(), LoginHistory.history_id.desc()).all()
    risk = calculate_risk(user, health, logins, now=now)
    status = system_status(risk, now)
    return {'status': status,
        'health_records': [{'record_id': h.status_id, 'recorded_at': h.recorded_at.isoformat(),
            'target_date': h.target_date.isoformat(), 'condition_level': h.condition_level,
            'breakfast': h.breakfast_status, 'lunch': h.lunch_status, 'dinner': h.dinner_status,
            'blood_pressure': h.blood_pressure, 'blood_sugar': h.blood_sugar} for h in health[:30]],
        'login_records': [{'record_id': row.history_id, 'at': row.auth_time.isoformat()}
            for row in logins[:30] if row.auth_time],
        'record_limit': 30, 'pattern_insights': risk.get('pattern_insights', [])}


def changes(previous, recent):
    before = {b['code']: b for b in previous['status']['breakdown']}
    after = {b['code']: b for b in recent['status']['breakdown']}
    result = []
    for code in sorted(before.keys() | after.keys()):
        old, new = before.get(code, {}).get('points', 0), after.get(code, {}).get('points', 0)
        if old != new:
            result.append({'code': code, 'item': (after.get(code) or before[code])['item'],
                'previous_points': old, 'current_points': new, 'difference': new - old})
    return result


def check_user(user_id, now=None):
    """상태 CAS와 알림을 한 트랜잭션에 저장. 실패 시 기존 기준도 보존한다."""
    now = now or dt.datetime.now()
    for attempt in range(3):
        try:
            # A no-op row update serializes checks and assignment changes on MySQL/SQLite.
            locked = db.session.execute(update(User).where(User.user_id == user_id,
                User.worker_id.isnot(None), User.is_active.is_(True)).values(
                    worker_id=User.worker_id, updated_at=User.updated_at))
            if not locked.rowcount:
                db.session.rollback()
                return None
            user = db.session.get(User, user_id, populate_existing=True)
            recent = _snapshot(user, now)
            key = f'user:{user_id}'
            state = db.session.get(StatusAlertState, key, populate_existing=True)
            if state and state.checked_at > now:
                db.session.rollback()
                return None
            previous = state.snapshot if state and state.worker_id == user.worker_id else None
            version = state.version + 1 if state else 1
            if state:
                changed = db.session.execute(update(StatusAlertState).where(
                    StatusAlertState.state_key == key, StatusAlertState.version == state.version).values(
                        snapshot=recent, worker_id=user.worker_id, version=version, checked_at=now))
                if changed.rowcount != 1:
                    db.session.rollback()
                    continue
            else:
                db.session.add(StatusAlertState(state_key=key, user_id=user_id, worker_id=user.worker_id,
                    version=version, snapshot=recent, checked_at=now))
            alert = None
            if previous and LEVEL_ORDER[recent['status']['level']] > LEVEL_ORDER[previous['status']['level']]:
                alert = StatusAlert(user_id=user_id, worker_id=user.worker_id, event_key=f'{user_id}:{version}',
                    detected_at=now, snapshot={'version': 1, 'previous': previous, 'current': recent,
                        'changes': changes(previous, recent)})
                db.session.add(alert)
            db.session.commit()
            return alert.alert_id if alert else None
        except (IntegrityError, OperationalError):
            db.session.rollback()
            if attempt == 2:
                raise
            time.sleep(0.05 * (attempt + 1))
        except Exception:
            db.session.rollback()
            raise
    raise RuntimeError('상태 기준 동시 갱신에 실패했습니다.')


def check_user_safely(user_id):
    try:
        return check_user(user_id)
    except Exception:
        current_app.logger.exception('상태 변화 검사 실패 user_id=%s', user_id)
        return None


def run_checks(now=None):
    now = now or dt.datetime.now()
    ids = [u.user_id for u in User.query.filter(User.is_active.is_(True), User.worker_id.isnot(None)).all()]
    success = True
    for user_id in ids:
        try:
            check_user(user_id, now)
        except Exception:
            success = False
            current_app.logger.exception('정기 상태 검사 실패 user_id=%s', user_id)
    if success:
        try:
            # Keep the last completed sweep across process restarts, even with no targets.
            state = db.session.get(StatusAlertState, 'background')
            if not state:
                state = StatusAlertState(state_key='background', checked_at=now)
                db.session.add(state)
            if not state.snapshot or state.checked_at <= now:
                state.checked_at = now
                state.snapshot = {'last_success_at': now.isoformat()}
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception('정기 상태 검사 완료 시각 저장 실패')
            success = False
    return success


def run_loop(app, stop=None):
    stop = stop or threading.Event()
    while not stop.is_set():
        with app.app_context():
            try:
                run_checks()
            except Exception:
                db.session.rollback()
                app.logger.exception('정기 상태 검사 실행 실패')
            finally:
                db.session.remove()
        stop.wait(300)


def register_runner(app):
    @app.cli.command('status-alert-worker')
    def status_alert_worker():
        """담당 대상자의 상태 악화를 시작 시/5분마다 확인한다."""
        try:
            run_loop(app)
        except KeyboardInterrupt:
            pass


def start_local_runner(app):
    if app.testing or app.config.get('STATUS_ALERT_AUTO_START', True) is False:
        return
    if app.extensions.get('status_alert_thread'):
        return
    stop = threading.Event()
    thread = threading.Thread(target=run_loop, args=(app, stop), daemon=True, name='status-alerts')
    app.extensions['status_alert_thread'] = thread
    app.extensions['status_alert_stop'] = stop
    thread.start()
