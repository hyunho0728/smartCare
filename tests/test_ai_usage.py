"""외부 호출을 모의하고 실제 격리 DB에서 모델 선택·호출 집계·시간 경계를 검사한다."""
import datetime as dt
import json
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from sqlalchemy import create_engine, text
import test_auth as fixtures
import test_checkup as checkup_fixtures
from models.models import db, User, Worker, AIUsage, HealthAnalysis, CheckupDocument, CheckupResult
from services import ai_service as service
from test_health_analysis import report


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AuthTests(); self.fixture.setUp()
        self.client = self.fixture.client; self.fixture.worker_login()
        self.env = patch.dict(os.environ, {'GEMINI_API_KEY': 'fake', 'GEMINI_USAGE_SCOPE': 'test-project',
                                          'GEMINI_MODEL_LIMITS': '{}', 'GEMINI_DEFAULT_MODEL': service.DEFAULT_MODEL})
        self.env.start()
        person = db.session.get(User, 1); person.has_underlying_disease = True; person.note = '고혈압'
        db.session.add(CheckupDocument(doc_id=1, user_id=1, file_path='/static/normal.pdf'))
        db.session.commit()

    def tearDown(self):
        self.env.stop(); self.fixture.tearDown()

    def mock_response(self, text_value='{}', tokens=120):
        return NS(text=text_value, usage_metadata=NS(prompt_token_count=tokens))

    def call(self, response=None, errors=None, **options):
        with patch.object(service.genai, 'Client') as client, patch.object(service.time, 'sleep'):
            method = client.return_value.models.generate_content
            method.return_value = response or self.mock_response()
            method.side_effect = errors
            result = service.generate_content(model=service.DEFAULT_MODEL, feature='checkup', contents=['private prompt'], **options)
            return result, client

    def test_catalog_defaults_overrides_and_auth(self):
        data = self.client.get('/api/admin/ai/models').json
        self.assertEqual([r['id'] for r in data['models']], list(service.MODELS))
        self.assertEqual(data['default_model'], service.DEFAULT_MODEL)
        self.assertEqual(data['models'][-1]['limits'], {'rpm': 15, 'tpm': 250000, 'rpd': 500})
        with patch.dict(os.environ, {'GEMINI_MODEL_LIMITS': json.dumps({service.DEFAULT_MODEL: {'rpm': 9}})}):
            self.assertEqual(service.model_catalog()[1]['limits']['rpm'], 9)
        for invalid in ('invalid-json', '{"other": {}}', '{"gemini-3.6-flash":{"rpm":0}}'):
            with patch.dict(os.environ, {'GEMINI_MODEL_LIMITS': invalid}):
                self.assertEqual(self.client.get('/api/admin/ai/models').status_code, 503)
        self.client.post('/api/auth/logout')
        for endpoint in ('models', 'usage'):
            self.assertEqual(self.client.get('/api/admin/ai/' + endpoint).status_code, 401)

    def test_all_models_both_apis_and_confirmation_model(self):
        with patch.object(service.genai, 'Client') as client:
            method = client.return_value.models.generate_content
            for model in service.MODELS:
                method.return_value = self.mock_response(json.dumps(report()))
                response = self.client.post('/api/admin/elders/1/life-pattern-ai', json={'model': model})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['model'], model)
                self.assertEqual(method.call_args.kwargs['model'], model)
                method.return_value = self.mock_response(json.dumps(checkup_fixtures.result('어르신')))
                response = self.client.post('/api/admin/checkup/analyze/1', json={'model': model})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['extraction_model'], model)
                if model == 'gemini-3.8-flash':
                    confirmed = self.client.put('/api/admin/checkup/1/confirm', json={'revision': 1,
                        'result': response.json['extraction'], 'identity_verified': True, 'issues_reviewed': True})
                    self.assertEqual(confirmed.status_code, 200)
                    self.assertEqual(confirmed.json['confirmed_model'], model)
                else:
                    self.assertEqual(response.json['confirmed_model'], 'gemini-3.8-flash')
            data = service.usage_summary()
            self.assertTrue(all(r['used']['rpd'] == 2 for r in data['models']))
            self.assertEqual(db.session.query(AIUsage).count(), 8)
            self.assertEqual(db.session.query(HealthAnalysis).count(), 4)

    def test_missing_model_uses_defaults_and_invalid_rejected_before_call(self):
        with patch.object(service.genai, 'Client') as client:
            method = client.return_value.models.generate_content
            method.return_value = self.mock_response(json.dumps(report()))
            self.assertEqual(self.client.post('/api/admin/elders/1/life-pattern-ai').json['model'], service.DEFAULT_MODEL)
            for value in ('other-model', '', None, [], 12):
                for url in ('/api/admin/elders/1/life-pattern-ai', '/api/admin/checkup/analyze/1'):
                    self.assertEqual(self.client.post(url, json={'model': value}).status_code, 400)
            self.assertEqual(method.call_count, 1)

    def test_retry_every_attempt_and_no_hidden_sdk_retry(self):
        error = RuntimeError('server'); error.code = 503
        _, client = self.call(errors=[error, error, self.mock_response()], attempts=3)
        self.assertEqual(client.return_value.models.generate_content.call_count, 3)
        self.assertEqual(client.call_args.kwargs['http_options'].retry_options.attempts, 1)
        rows = db.session.query(AIUsage).order_by(AIUsage.usage_id).all()
        self.assertEqual([r.status for r in rows], ['error', 'error', 'response'])
        self.assertEqual(rows[-1].input_tokens, 120)
        data = service.usage_summary()['models'][1]
        self.assertEqual(data['used']['rpm'], 3)
        self.assertEqual(data['unknown_token_calls'], 2)
        self.assertIsNone(data['remaining']['tpm'])

    def test_quota_not_retried_and_access_errors_not_switched(self):
        for code in (429, 403, 404):
            error = RuntimeError('provider'); error.code = code
            with patch.object(service.genai, 'Client') as client, self.assertRaises(RuntimeError):
                client.return_value.models.generate_content.side_effect = error
                service.generate_content(model=service.DEFAULT_MODEL, feature='checkup', contents=[], attempts=3)
            self.assertEqual(client.return_value.models.generate_content.call_count, 1)
            self.assertEqual(client.return_value.models.generate_content.call_args.kwargs['model'], service.DEFAULT_MODEL)
        self.assertEqual(db.session.query(AIUsage).count(), 3)

    def test_record_failure_prevents_call_and_final_record_failure_keeps_pending(self):
        with patch.object(service, 'Session') as ledger, patch.object(service.genai, 'Client') as client:
            ledger.return_value.__enter__.return_value.commit.side_effect = RuntimeError('db')
            with self.assertRaises(service.AIError):
                service.generate_content(model=service.DEFAULT_MODEL, feature='checkup', contents=[])
            client.return_value.models.generate_content.assert_not_called()
        with patch.object(service, 'finish_attempt', side_effect=service.AIError('save')):
            with self.assertRaises(service.AIError): self.call()
        self.assertEqual(db.session.query(AIUsage).first().status, 'pending')
        self.assertIsNone(service.usage_summary()['models'][1]['remaining']['tpm'])

    def test_json_and_analysis_save_failure_still_count_received_tokens(self):
        with patch.object(service.genai, 'Client') as client:
            client.return_value.models.generate_content.return_value = self.mock_response('bad json', 23)
            self.assertEqual(self.client.post('/api/admin/elders/1/life-pattern-ai').status_code, 502)
            client.return_value.models.generate_content.return_value = self.mock_response(json.dumps(report()), 25)
            with patch.object(db.session, 'commit', side_effect=RuntimeError('analysis save')):
                self.assertEqual(self.client.post('/api/admin/elders/1/life-pattern-ai').status_code, 500)
        self.assertEqual(db.session.query(HealthAnalysis).count(), 0)
        self.assertEqual(service.usage_summary()['models'][1]['used']['tpm'], 48)
        self.assertEqual(db.session.query(AIUsage).count(), 2)

    def test_pending_and_unknown_tokens_are_not_zero_estimate(self):
        usage_id = service.record_attempt(service.DEFAULT_MODEL, 'checkup')
        self.assertIsNone(service.usage_summary()['models'][1]['remaining']['tpm'])
        service.finish_attempt(usage_id, self.mock_response(tokens=None))
        summary = service.usage_summary()
        self.assertIsNone(summary['models'][1]['remaining']['tpm'])
        self.assertIsNotNone(summary['tracking_started_at'])
        self.assertEqual(summary['basis'], 'app_estimate')

    def insert(self, when, model=None, tokens=100, scope='test-project'):
        db.session.add(AIUsage(scope=scope, model=model or service.DEFAULT_MODEL, feature='checkup',
            started_at=when.replace(tzinfo=None), status='response', input_tokens=tokens))
        db.session.commit()

    def test_rolling_window_scope_model_and_remaining_clamp(self):
        now = dt.datetime(2026, 10, 7, 12, tzinfo=dt.timezone.utc)
        self.insert(now - dt.timedelta(seconds=60))
        self.insert(now - dt.timedelta(seconds=59))
        self.insert(now, model='gemini-3.8-flash', tokens=None)
        self.insert(now, scope='other-project')
        for _ in range(6): self.insert(now)
        data = service.usage_summary(now)
        flash = data['models'][1]
        self.assertEqual(flash['used']['rpm'], 7)
        self.assertEqual(flash['used']['rpd'], 8)
        self.assertEqual(flash['remaining']['rpm'], 0)
        self.assertTrue(flash['exceeded']['rpm'])
        self.assertEqual(data['models'][0]['unknown_token_calls'], 1)
        self.assertEqual(data['models'][2]['used']['rpm'], 0)

    def test_pacific_midnight_and_dst(self):
        # 서머타임 시작일은 23시간, 종료일은 25시간이다.
        for now, hours in ((dt.datetime(2026, 3, 8, 8, tzinfo=dt.timezone.utc), 23),
                           (dt.datetime(2026, 11, 1, 7, tzinfo=dt.timezone.utc), 25)):
            data = service.usage_summary(now)
            reset = dt.datetime.fromisoformat(data['next_daily_reset_at'])
            self.assertEqual((reset - now).total_seconds(), hours * 3600)
        now = dt.datetime(2026, 10, 7, 7, tzinfo=dt.timezone.utc)
        self.insert(now - dt.timedelta(seconds=1)); self.insert(now)
        data = service.usage_summary(now)['models'][1]
        self.assertEqual(data['used']['rpd'], 1)
        self.assertEqual(data['used']['rpm'], 2)

    def test_records_survive_new_session_and_no_sensitive_fields(self):
        self.call(); db.session.remove()
        self.assertEqual(service.usage_summary()['models'][1]['used']['rpd'], 1)
        fields = set(AIUsage.__table__.columns.keys())
        self.assertEqual(fields, {'usage_id', 'scope', 'model', 'feature', 'started_at', 'status', 'input_tokens', 'error_code'})
        self.assertEqual(self.client.get('/api/admin/ai/usage').status_code, 200)

    def test_usage_shared_between_workers(self):
        self.call()
        first = self.client.get('/api/admin/ai/usage').json['models'][1]['used']['rpd']
        db.session.add(Worker(worker_id=2, login_id='other', password='secret', name='다른 담당자',
                              phone_number='01077778888', address='test'))
        db.session.commit()
        self.client.post('/api/admin/login', json={'admin_id': 'other', 'password': 'secret'})
        self.assertEqual(self.client.get('/api/admin/ai/usage').json['models'][1]['used']['rpd'], first)


class MigrationTests(unittest.TestCase):
    def test_existing_sqlite_table_data_and_idempotency(self):
        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE CHECKUP_RESULT (result_id INTEGER PRIMARY KEY, confirmed_result TEXT)'))
            connection.execute(text("INSERT INTO CHECKUP_RESULT VALUES (1, 'preserved')"))
        service.ensure_checkup_model_columns(engine); service.ensure_checkup_model_columns(engine)
        with engine.connect() as connection:
            row = connection.execute(text('SELECT confirmed_result, extraction_model, confirmed_model FROM CHECKUP_RESULT')).one()
            self.assertEqual(tuple(row), ('preserved', None, None))
        engine.dispose()


if __name__ == '__main__': unittest.main()
