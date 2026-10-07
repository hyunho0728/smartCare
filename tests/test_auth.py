"""운영 DB 없이 역할별 로그인·등록·세션 호환성을 검사한다."""
import sys
import os
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from flask import Flask, redirect
from sqlalchemy import event
from models.models import db, User, Worker, LoginHistory
from routes.auth import auth_bp
from routes.user import user_bp
from routes.social_worker import worker_bp
from services.auth_service import current_role, DESTINATIONS

ROOT = Path(__file__).resolve().parents[1]


def build_test_app():
    app = Flask(__name__, template_folder=str(ROOT / 'app/templates'), static_folder=str(ROOT / 'app/static'))
    app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite://')
    db.init_app(app)
    for blueprint in (auth_bp, user_bp, worker_bp): app.register_blueprint(blueprint)
    @app.get('/')
    def index(): return redirect(DESTINATIONS.get(current_role(), '/login'))
    return app


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.app = build_test_app(); self.context = self.app.app_context(); self.context.push(); db.create_all()
        db.session.add_all([
            Worker(worker_id=1, login_id='worker', password='secret', name='복지사', phone_number='01011112222', address='a'),
            User(user_id=1, worker_id=1, name='어르신', age=75, phone_number='01033334444', address='a', session_token='old-token'),
        ]); db.session.commit()
        # SQLite는 BIGINT PK 자동 증가가 없으므로 테스트 이력에만 번호를 지정한다.
        def history_id(mapper, connection, row):
            row.history_id = len(connection.execute(db.select(LoginHistory.history_id)).all()) + 1
        self.history_id = history_id; event.listen(LoginHistory, 'before_insert', history_id)
        helper = types.ModuleType('app'); helper.check_and_update_missed_meals = lambda: None
        self.helper_patch = patch.dict(sys.modules, {'app': helper}); self.helper_patch.start()
        self.client = self.app.test_client()

    def tearDown(self):
        self.helper_patch.stop(); event.remove(LoginHistory, 'before_insert', self.history_id)
        db.session.remove(); db.engine.dispose(); self.context.pop()

    def worker_login(self, password='secret'):
        return self.client.post('/api/admin/login', json={'admin_id': 'worker', 'password': password})

    def user_login(self, phone='01033334444'):
        return self.client.post('/api/user/login', json={'phone_number': phone})

    def test_anonymous_entry_on_every_device(self):
        for agent in ('Desktop Chrome', 'iPhone Mobile'):
            for url in ('/', '/user', '/admin'):
                response = self.client.get(url, headers={'User-Agent': agent})
                self.assertEqual(response.location, '/login')
        html = self.client.get('/login').get_data(as_text=True)
        self.assertIn('worker-role', html); self.assertNotIn('id="loginPw"', html)

    def test_role_login_and_page_guards(self):
        self.assertEqual(self.user_login().status_code, 200)
        self.assertEqual(self.client.get('/api/auth/session').json['role'], 'user')
        self.assertEqual(self.client.get('/admin').location, '/user')
        self.assertEqual(self.client.get('/user').status_code, 200)
        self.assertEqual(self.client.get('/').location, '/user')
        self.assertEqual(self.worker_login().status_code, 200)
        with self.client.session_transaction() as session:
            self.assertNotIn('user_id', session); self.assertEqual(session['login_role'], 'worker')
        self.assertEqual(self.client.get('/api/user/check-session').json['valid'], False)
        self.assertEqual(self.client.get('/user').location, '/admin')
        self.assertEqual(self.client.get('/admin').status_code, 200)
        self.assertEqual(self.client.get('/login').location, '/admin')

    def test_switch_back_to_user_clears_worker(self):
        self.worker_login(); self.user_login()
        with self.client.session_transaction() as session:
            self.assertNotIn('admin_id', session); self.assertNotIn('admin_worker_id', session)
        self.assertFalse(self.client.get('/api/admin/check-session').json['is_logged_in'])
        self.assertEqual(self.client.get('/api/auth/session').json['role'], 'user')

    def test_failed_login_preserves_existing_session(self):
        self.user_login()
        self.assertEqual(self.worker_login('wrong').status_code, 401)
        self.assertEqual(self.client.get('/api/auth/session').json['role'], 'user')
        self.worker_login()
        self.assertEqual(self.user_login('999999999').status_code, 404)
        self.assertEqual(self.client.get('/api/auth/session').json['role'], 'worker')

    def test_database_failure_does_not_switch_roles(self):
        self.worker_login()
        with patch.object(db.session, 'commit', side_effect=RuntimeError('db failure')):
            self.assertEqual(self.user_login().status_code, 500)
        self.assertEqual(self.client.get('/api/auth/session').json['role'], 'worker')

    def test_legacy_and_ambiguous_sessions(self):
        with self.client.session_transaction() as session:
            session.update(user_id=1, user_token='old-token', user_phone='01033334444')
        self.assertEqual(self.client.get('/api/auth/session').json['role'], 'user')
        with self.client.session_transaction() as session:
            session['admin_id'] = 'worker'
        self.assertFalse(self.client.get('/api/auth/session').json['valid'])
        with self.client.session_transaction() as session: self.assertFalse(session)
        with self.client.session_transaction() as session: session['admin_id'] = 'worker'
        self.assertEqual(self.client.get('/api/auth/session').json['role'], 'worker')

    def test_expiry_logout_and_remote_logout(self):
        self.user_login(); db.session.get(User, 1).session_token = None; db.session.commit()
        self.assertEqual(self.client.get('/user').location, '/login')
        self.user_login(); self.worker_login()
        self.assertEqual(self.client.post('/api/admin/users/logout', json={'user_id': 1}).status_code, 200)
        self.assertIsNone(db.session.get(User, 1).session_token)
        self.assertTrue(self.client.post('/api/auth/logout').json['success'])
        self.assertFalse(self.client.get('/api/auth/session').json['valid'])

    def test_registration_routes_and_existing_forms(self):
        for url, marker in [('/register/user', 'screen-reg-privacy'), ('/register/worker', 'signupName')]:
            self.assertIn(marker, self.client.get(url).get_data(as_text=True))
        response = self.client.post('/api/user/register', json={
            'name': '새어르신', 'phone_number': '01055556666', 'address': 'b', 'age': 78, 'has_disease': False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.user_login('01055556666').status_code, 200)
        self.client.post('/api/auth/logout')
        response = self.client.post('/api/admin/signup', json={
            'name': '새복지사', 'admin_id': 'new-worker', 'phone': '01077778888', 'password': 'pass', 'region': 'b'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post('/api/admin/login', json={'admin_id': 'new-worker', 'password': 'pass'}).status_code, 200)

    def test_life_ai_configuration_failure_is_not_success(self):
        self.worker_login()
        with patch.dict(os.environ, {'GEMINI_API_KEY': ''}):
            response = self.client.post('/api/admin/elders/1/life-pattern-ai')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json['success'])

    def test_life_ai_quota_failure_is_not_success(self):
        from services.social_worker_ai_service import LifePatternAIError
        self.worker_login()
        with patch('routes.social_worker.analyze_life_pattern_with_gemini', side_effect=LifePatternAIError('quota', 429)):
            response = self.client.post('/api/admin/elders/1/life-pattern-ai')
        self.assertEqual(response.status_code, 429)
        self.assertFalse(response.json['success'])


if __name__ == '__main__': unittest.main()
