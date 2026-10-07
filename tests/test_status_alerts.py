"""외부 AI 없이 상태 악화·저장·담당 권한·백그라운드 검사를 검증한다."""
import copy
import datetime as dt
import threading
import uuid
from pathlib import Path
import unittest
from unittest.mock import patch
import test_auth as fixtures
from models.models import db, User, Worker, HealthStatus, StatusAlert, StatusAlertState
from services import status_alert_service as service
from services.health_comparison_service import LEVEL_LABELS

NOW = dt.datetime(2026, 10, 7, 12)


def snapshot(level='SAFE', score=90, hours=0):
    return {'status': {'score': score, 'level': level, 'label': LEVEL_LABELS[level],
        'as_of': NOW.isoformat(), 'breakdown': [{'code':'elapsed', 'item':f'건강 기록 미입력 경과 ({hours}시간)', 'points':-hours * 2}]},
        'health_records': [], 'login_records': [], 'record_limit':30, 'pattern_insights':[]}


class StatusAlertTests(unittest.TestCase):
    setUp = fixtures.AuthTests.setUp
    tearDown = fixtures.AuthTests.tearDown
    worker_login = fixtures.AuthTests.worker_login
    user_login = fixtures.AuthTests.user_login
    def create_change(self):
        with patch.object(service, '_snapshot', return_value=snapshot()): service.check_user(1, NOW)
        with patch.object(service, '_snapshot', return_value=snapshot('WATCH', 70, 10)):
            return service.check_user(1, NOW + dt.timedelta(minutes=5))

    def test_first_baseline_worsening_recovery_same_grade_and_jump(self):
        sequence = [('SAFE',90,0),('SAFE',81,1),('WATCH',70,10),('WATCH',60,15),
            ('SAFE',90,0),('DANGER',20,35),('DANGER',10,40)]
        results=[]
        for index, (level,score,hours) in enumerate(sequence):
            with patch.object(service, '_snapshot', return_value=snapshot(level,score,hours)):
                results.append(service.check_user(1, NOW+dt.timedelta(minutes=index)))
        self.assertEqual(sum(value is not None for value in results),2)
        records=StatusAlert.query.order_by(StatusAlert.alert_id).all()
        self.assertEqual(records[0].snapshot['changes'][0]['current_points'],-20)
        self.assertEqual(records[1].snapshot['previous']['status']['level'],'SAFE')
        self.assertEqual(records[1].snapshot['current']['status']['level'],'DANGER')

    def test_actual_elapsed_time_without_input_and_no_ai(self):
        db.session.add(HealthStatus(status_id=1,user_id=1,target_date=NOW.date(),recorded_at=NOW,
            condition_level=5,breakfast_status='완료',lunch_status='완료',dinner_status='완료'))
        db.session.commit()
        with patch('services.social_worker_ai_service.generate_content') as ai:
            service.check_user(1,NOW)
            alert_id=service.check_user(1,NOW+dt.timedelta(hours=11))
            self.assertIsNotNone(alert_id)
            alert=db.session.get(StatusAlert,alert_id)
            elapsed=next(b for b in alert.snapshot['current']['status']['breakdown'] if b['code']=='elapsed')
            self.assertIn('11시간',elapsed['item'])
            self.assertEqual(alert.snapshot['current']['health_records'][0]['record_id'],1)
            ai.assert_not_called()

    def test_saved_evidence_survives_later_changes_and_session_restart(self):
        alert_id=self.create_change()
        expected=copy.deepcopy(db.session.get(StatusAlert,alert_id).snapshot)
        db.session.remove()
        with patch.object(service,'_snapshot',return_value=snapshot('DANGER',10,40)):
            service.check_user(1,NOW+dt.timedelta(hours=1))
        self.assertEqual(db.session.get(StatusAlert,alert_id).snapshot,expected)

    def test_failure_keeps_baseline_and_retry_creates_once(self):
        with patch.object(service,'_snapshot',return_value=snapshot()): service.check_user(1,NOW)
        before=copy.deepcopy(db.session.get(StatusAlertState,'user:1').snapshot)
        with patch.object(service,'_snapshot',return_value=snapshot('DANGER',20,35)), \
             patch.object(db.session,'commit',side_effect=RuntimeError('save failed')):
            with self.assertRaises(RuntimeError): service.check_user(1,NOW+dt.timedelta(minutes=5))
        self.assertEqual(db.session.get(StatusAlertState,'user:1').snapshot,before)
        self.assertEqual(StatusAlert.query.count(),0)
        with patch.object(service,'_snapshot',return_value=snapshot('DANGER',20,35)):
            service.check_user(1,NOW+dt.timedelta(minutes=5)); service.check_user(1,NOW+dt.timedelta(minutes=6))
        self.assertEqual(StatusAlert.query.count(),1)

    def test_calculation_failure_and_out_of_order_check_keep_state(self):
        self.create_change()
        version=db.session.get(StatusAlertState,'user:1').version
        with patch.object(service,'_snapshot',side_effect=ValueError('calculation failed')):
            with self.assertRaises(ValueError): service.check_user(1,NOW+dt.timedelta(hours=1))
        with patch.object(service,'_snapshot',return_value=snapshot('DANGER',0,50)):
            service.check_user(1,NOW-dt.timedelta(minutes=1))
        self.assertEqual(db.session.get(StatusAlertState,'user:1').version,version)
        self.assertEqual(StatusAlert.query.count(),1)

    def test_inactive_unassigned_and_assignment_reset(self):
        self.create_change()
        db.session.add(Worker(worker_id=2,login_id='other',password='secret',name='다른복지사',phone_number='01055556666',address='b'))
        user=db.session.get(User,1); user.worker_id=2; service.reset_baseline(1); db.session.commit()
        with patch.object(service,'_snapshot',return_value=snapshot('DANGER',0,50)):
            self.assertIsNone(service.check_user(1,NOW+dt.timedelta(hours=1)))
        self.assertEqual(StatusAlert.query.count(),1)
        user.is_active=False; db.session.commit()
        with patch.object(service,'_snapshot') as calculate:
            service.check_user(1,NOW+dt.timedelta(hours=2)); calculate.assert_not_called()
        user.is_active=True; user.worker_id=None; db.session.commit()
        with patch.object(service,'_snapshot') as calculate:
            service.check_user(1,NOW+dt.timedelta(hours=3)); calculate.assert_not_called()

    def test_api_permissions_read_idempotency_and_pure_queries(self):
        alert_id=self.create_change()
        self.assertEqual(self.client.get('/api/admin/status-alerts').status_code,401)
        self.worker_login()
        with patch.object(service,'calculate_risk',side_effect=AssertionError('query calculated risk')):
            response=self.client.get('/api/admin/status-alerts').json
            self.assertEqual(response['unread_count'],1)
            self.assertEqual(self.client.get(f'/api/admin/status-alerts/{alert_id}').json['data']['snapshot']['current']['status']['score'],70)
            self.assertFalse(db.session.get(StatusAlert,alert_id).is_read)
            first=self.client.post(f'/api/admin/status-alerts/{alert_id}/read').json
            second=self.client.post(f'/api/admin/status-alerts/{alert_id}/read').json
            self.assertEqual(first['data']['read_at'],second['data']['read_at'])
            self.assertEqual(second['unread_count'],0)
            self.assertEqual(self.client.get('/api/admin/status-alerts?unread_only=true').json['data'],[])
        self.assertEqual(StatusAlert.query.count(),1)
        db.session.add(Worker(worker_id=2,login_id='other',password='secret',name='다른복지사',phone_number='01055556666',address='b'))
        db.session.get(User,1).worker_id=2; db.session.commit()
        self.assertEqual(self.client.get('/api/admin/status-alerts').json['data'],[])
        self.assertEqual(self.client.get(f'/api/admin/status-alerts/{alert_id}').status_code,404)
        self.assertEqual(self.client.post(f'/api/admin/status-alerts/{alert_id}/read').status_code,404)
        self.client.post('/api/admin/login',json={'admin_id':'other','password':'secret'})
        self.assertEqual(self.client.get(f'/api/admin/status-alerts/{alert_id}').status_code,404)

    def test_background_success_metadata_persisted_and_failure_not_advanced(self):
        with patch.object(service,'_snapshot',return_value=snapshot()):
            self.assertTrue(service.run_checks(NOW))
        db.session.remove(); self.worker_login()
        first=self.client.get('/api/admin/status-alerts').json['last_background_success_at']
        self.assertEqual(first,NOW.isoformat())
        with patch.object(service,'_snapshot',side_effect=RuntimeError('test')):
            self.assertFalse(service.run_checks(NOW+dt.timedelta(minutes=5)))
        self.assertEqual(self.client.get('/api/admin/status-alerts').json['last_background_success_at'],first)

    def test_worker_loop_and_testing_auto_start_disabled(self):
        stop=threading.Event()
        def sweep(): stop.set(); return True
        with patch.object(service,'run_checks',side_effect=sweep) as run:
            service.run_loop(self.app,stop); self.assertEqual(run.call_count,1)
        service.start_local_runner(self.app)
        self.assertNotIn('status_alert_thread',self.app.extensions)
        service.register_runner(self.app)
        with patch.object(service,'run_loop') as loop:
            result=self.app.test_cli_runner().invoke(args=['status-alert-worker'])
            self.assertEqual(result.exit_code,0); loop.assert_called_once_with(self.app)

    def test_health_save_checks_and_alert_failure_keeps_health(self):
        now=dt.datetime.now()
        db.session.add(HealthStatus(status_id=1,user_id=1,target_date=now.date(),recorded_at=now,
            condition_level=3,breakfast_status='완료',lunch_status='완료',dinner_status='완료'))
        db.session.commit(); self.user_login()
        with patch.object(service,'check_user',side_effect=RuntimeError('notification failed')) as check, \
             patch('routes.user.evaluate_and_record_risk',return_value={'score':90,'risk_level':'safe'}):
            response=self.client.post('/api/user/health',json={'condition_level':5,'breakfast':'yes','lunch':'yes','dinner':'yes'})
        self.assertEqual(response.status_code,200); check.assert_called_once_with(1)
        self.assertEqual(db.session.get(HealthStatus,1).condition_level,5)

    def test_assignment_route_establishes_new_baseline_without_notification(self):
        self.create_change()
        self.worker_login()
        user=db.session.get(User,1); user.worker_id=None; db.session.commit()
        with patch.object(service,'_snapshot',return_value=snapshot('DANGER',20,35)):
            response=self.client.post('/api/admin/elders/assign',json={'user_id':1})
        self.assertEqual(response.status_code,200)
        self.assertEqual(StatusAlert.query.count(),1)
        state=db.session.get(StatusAlertState,'user:1')
        self.assertEqual(state.worker_id,1)
        self.assertEqual(state.snapshot['status']['level'],'DANGER')

    def test_read_save_failure_keeps_unread(self):
        alert_id=self.create_change(); self.worker_login()
        with patch.object(db.session,'commit',side_effect=RuntimeError('read save failed')):
            response=self.client.post(f'/api/admin/status-alerts/{alert_id}/read')
        self.assertEqual(response.status_code,500)
        self.assertFalse(db.session.get(StatusAlert,alert_id).is_read)

    def test_concurrent_checks_create_one_event(self):
        from flask import Flask
        # Separate file connections exercise real SQLite transaction serialization.
        path=Path(__file__).parent/'ui_artifacts'/f'status_alert_concurrency_{uuid.uuid4().hex}.db'
        path.parent.mkdir(exist_ok=True)
        app=Flask('concurrent-status-alerts')
        app.config.update(TESTING=True,SQLALCHEMY_DATABASE_URI='sqlite:///'+path.resolve().as_posix())
        db.init_app(app)
        with app.app_context():
            db.create_all()
            db.session.add_all([Worker(worker_id=1,login_id='w',password='s',name='복지사',phone_number='0101',address='a'),
                User(user_id=1,worker_id=1,name='어르신',age=75,phone_number='0102',address='a')])
            db.session.commit()
            with patch.object(service,'_snapshot',return_value=snapshot()): service.check_user(1,NOW)
        barrier=threading.Barrier(3); errors=[]
        def run():
            try:
                with app.app_context():
                    barrier.wait(timeout=5); service.check_user(1,NOW+dt.timedelta(minutes=5))
            except Exception as exc: errors.append(exc)
        with patch.object(service,'_snapshot',return_value=snapshot('DANGER',20,35)):
            threads=[threading.Thread(target=run) for _ in range(3)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=10)
        self.assertFalse(errors); self.assertTrue(all(not t.is_alive() for t in threads))
        with app.app_context():
            self.assertEqual(StatusAlert.query.count(),1)
            self.assertEqual(StatusAlertState.query.filter_by(user_id=1).count(),1)
            db.session.remove(); db.engine.dispose()


if __name__=='__main__': unittest.main()
