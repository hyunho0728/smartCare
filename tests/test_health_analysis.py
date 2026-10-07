"""종합 분석의 입력 허용 목록·Gemini 계약·저장·담당자 권한을 모의 데이터로 검사한다."""
import datetime as dt
import json
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import test_auth as fixtures
from models.models import db, User, HealthStatus, CheckupDocument, CheckupResult, HealthAnalysis
from services import health_analysis_service as service

NOW = dt.datetime(2026, 10, 7, 12)


def item(name='공복혈당', value='126', unit='mg/dL', **extra):
    return {'name': name, 'value': value, 'unit': unit, 'reference_range': '100 미만',
            'raw_text': '비밀이름 010-1234-5678 비밀주소', 'page': 1, 'unreadable': False, **extra}


def document(doc_id=1, date='2026-10-01', items=None, confirmed=True):
    doc = NS(doc_id=doc_id, uploaded_at=NOW - dt.timedelta(days=doc_id))
    result = {'is_checkup': True, 'patient_name': '비밀이름', 'institution': '비밀병원', 'checkup_date': date,
              'items': items if items is not None else [item()]}
    return doc, NS(confirmed_result=result if confirmed else None, confirmed_revision=2, confirmed_at=NOW)


def user(**extra):
    return NS(age=75, has_underlying_disease=True, note='고혈압, 당뇨병. 비밀이름 010-1234-5678 비밀주소', **extra)


def health(days=1):
    return NS(recorded_at=NOW - dt.timedelta(days=days), condition_level=3, breakfast_status='결식',
              lunch_status='완료', dinner_status='예정', blood_pressure='120/80', blood_sugar=110)


def report(ref='s1'):
    return {'summary': '생활 기록과 검진·등록 정보를 함께 확인하세요.',
            'findings': [{'title': '근거 확인', 'detail': '원본과 기록을 확인하세요.', 'source_refs': [ref]}],
            'recommended_actions': ['안부와 기록을 확인하세요.'], 'limitations': ['진단이 아닌 확인 보조입니다.'],
            'priority_actions': [{'action': '연락 확인', 'reason': '기록과 안부 확인이 필요합니다.',
                                  'source_refs': [ref], 'priority': '우선 확인'}]}


class InputTests(unittest.TestCase):
    def test_three_sources_and_periods(self):
        data = service.build_input(user(), [health(), health(10), health(31)],
                                   [NS(auth_time=NOW - dt.timedelta(days=2))], [document()], NOW)
        self.assertEqual({s['kind'] for s in data['sources']}, {'생활기록', '접속기록', '건강검진', '등록기저질환'})
        self.assertEqual({s['data']['period'] for s in data['sources'] if s['kind'] == '생활기록'}, {'최근7일', '이전23일'})
        self.assertEqual(data['documents'][0]['confirmed_revision'], 2)
        disease = next(s['data'] for s in data['sources'] if s['kind'] == '등록기저질환')
        self.assertEqual(disease['diseases'], ['당뇨병', '고혈압'])
        text = json.dumps(data, ensure_ascii=False)
        for secret in ('비밀이름', '비밀주소', '비밀병원', '010-1234-5678', 'raw_text', 'patient_name'):
            self.assertNotIn(secret, text)

    def test_latest_two_only_confirmed_and_date_fallback(self):
        data = service.build_input(user(), [], [], [document(1, '2023-01-01'), document(2, '2026-10-01'),
                                                   document(3, None), document(4, '2026-10-06', confirmed=False)], NOW)
        self.assertEqual([d['doc_id'] for d in data['documents']], [3, 2])
        self.assertIn('검진일 미상', ' '.join(data['limitations']))

    def test_old_missing_unsupported_and_conflicting(self):
        docs = [document(1, '2024-01-01', [item(), item(value='130'), item(unreadable=True),
                                         item(name='비밀이름'), item(value='010-1234-5678'), item(unit='비밀주소')])]
        data = service.build_input(user(), [], [], docs, NOW)
        self.assertEqual(data['documents'][0]['unreadable_items'], 1)
        self.assertEqual(data['documents'][0]['excluded_items'], 2)
        text = ' '.join(data['limitations'])
        for part in ('1년 이상', '상충 값', '전달하지 못한 단위', '생활 기록이 없습니다'):
            self.assertIn(part, text)

    def test_empty_and_limited_inputs(self):
        person = NS(age=75, has_underlying_disease=False, note='혈당')
        with self.assertRaises(service.HealthAnalysisError) as error:
            service.build_input(person, [], [], [document(confirmed=False)], NOW)
        self.assertEqual(error.exception.status, 422)
        data = service.build_input(person, [health()], [], [], NOW)
        self.assertIn('확정된 건강검진 자료가 없어', ' '.join(data['limitations']))
        person.has_underlying_disease = True
        data = service.build_input(person, [], [], [], NOW)
        self.assertEqual(data['sources'][0]['data']['diseases'], [])

    def test_aliases_and_numbers(self):
        for source, target in [('시력(좌)', '시력(좌)'), ('ALT(SGPT)', 'ALT'), ('혈청 크레아티닌', '크레아티닌')]:
            self.assertEqual(service.canonical_item(source), target)
        for valid in ('0.7', '<100', '100 미만', '120/80', '0.6-1.2', '음성', '++'):
            self.assertEqual(service.safe_measurement(valid), valid)
        for invalid in ('010-1234-5678', '비밀이름', '120 의사 김씨', '12345678'):
            self.assertIsNone(service.safe_measurement(invalid))
        self.assertEqual(service.safe_reference('정상범위: 70~100 mg/dL'), '정상범위: 70~100 mg/dL')
        self.assertIsNone(service.safe_reference('비밀이름: 70~100 mg/dL'))
        self.assertEqual(service.registered_diseases('당뇨병 없음, 고혈압 의심, 가족력: 뇌졸중'), [])


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = service.build_input(user(), [health()], [], [document()], NOW)
        for target in ('record_attempt', 'finish_attempt'):
            mock = patch('services.ai_service.' + target, return_value=1); mock.start(); self.addCleanup(mock.stop)

    def call(self, text=None, failure=None):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'fake'}), patch.object(service.genai, 'Client') as client:
            client.return_value.models.generate_content.return_value = NS(text=text)
            client.return_value.models.generate_content.side_effect = failure
            result = service.analyze(self.snapshot)
            return result, client

    def test_json_contract_and_private_payload(self):
        result, client = self.call(json.dumps(report()))
        kwargs = client.return_value.models.generate_content.call_args.kwargs
        self.assertEqual(kwargs['config'].response_mime_type, 'application/json')
        schema = kwargs['config'].response_schema
        self.assertIsNone(schema.additional_properties)
        self.assertIsNone(schema.properties['findings'].items.additional_properties)
        self.assertTrue(kwargs['config'].automatic_function_calling.disable)
        self.assertEqual(client.call_args.kwargs['http_options'].timeout, 60000)
        self.assertIn('data', kwargs['contents'][0])
        for secret in ('비밀이름', '비밀주소', '비밀병원', '010-1234-5678', 'raw_text', 'patient_name'):
            self.assertNotIn(secret, kwargs['contents'][0])
        self.assertIn(self.snapshot['limitations'][0], result['limitations'])

    def test_bad_json_empty_schema_and_reference(self):
        for value in ('', 'not-json', '{}', json.dumps(report('missing')), json.dumps({**report(), 'risk_score': 80})):
            with self.subTest(value=value), self.assertRaises(service.HealthAnalysisError):
                self.call(value)

    def test_provider_schema_guides_local_array_limits_without_rejected_constraints(self):
        schema = service.provider_schema()
        for name, maximum in [('findings', 12), ('recommended_actions', 8), ('limitations', 12), ('priority_actions', 8)]:
            self.assertIn(f'~{maximum}개', schema.properties[name].description)
            self.assertIsNone(schema.properties[name].max_items)
        self.assertIn('1~8개', schema.properties['priority_actions'].description)
        for name in ('findings', 'priority_actions'):
            refs = schema.properties[name].items.properties['source_refs']
            self.assertIn('1~20개', refs.description)
            self.assertIsNone(refs.min_items); self.assertIsNone(refs.max_items)
        summary = schema.properties['summary']
        self.assertIn('2000자', summary.description)
        self.assertIsNone(summary.max_length)  # 서버 미지원 속성은 보내지 않는다.

    def test_valid_json_over_limit_is_explained_and_not_logged(self):
        value = report(); value['limitations'] = ['비밀이름 010-1234-5678'] * 13
        with self.assertLogs(service.logger, level='WARNING') as logs:
            with self.assertRaises(service.HealthAnalysisError) as error:
                self.call(json.dumps(value))
        self.assertIn('데이터 한계', str(error.exception))
        self.assertIn('허용 범위', str(error.exception))
        self.assertIn('too_long', ''.join(logs.output))
        self.assertNotIn('비밀이름', ''.join(logs.output)); self.assertNotIn('010-1234-5678', ''.join(logs.output))

    def test_missing_priority_and_unknown_field_diagnostics(self):
        value = report(); value.pop('priority_actions')
        with self.assertRaises(service.HealthAnalysisError) as error:
            self.call(json.dumps(value))
        self.assertIn('우선 확인 행동', str(error.exception)); self.assertIn('누락', str(error.exception))
        value = report(); value['비밀이름'] = '010-1234-5678'
        with self.assertLogs(service.logger, level='WARNING') as logs, self.assertRaises(service.HealthAnalysisError):
            self.call(json.dumps(value))
        self.assertNotIn('비밀이름', ''.join(logs.output)); self.assertNotIn('010-1234-5678', ''.join(logs.output))

    def test_output_token_limit_is_reported_before_parsing(self):
        response = NS(text='{"summary":', candidates=[NS(finish_reason=service.types.FinishReason.MAX_TOKENS)])
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'fake'}), patch.object(service, 'generate_content', return_value=response):
            with self.assertRaises(service.HealthAnalysisError) as error:
                service.analyze(self.snapshot)
        self.assertIn('출력 한도', str(error.exception))

    def test_key_quota_and_timeout(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': ''}), self.assertRaises(service.HealthAnalysisError) as error:
            service.analyze(self.snapshot)
        self.assertEqual(error.exception.status, 503)
        quota = RuntimeError('quota'); quota.code = 429
        for failure, status in ((quota, 429), (TimeoutError(), 502)):
            with self.assertRaises(service.HealthAnalysisError) as error:
                self.call(failure=failure)
            self.assertEqual(error.exception.status, status)

    def test_request_format_error_is_not_model_permission_error(self):
        failure = RuntimeError('unsupported schema'); failure.code = 400
        with self.assertRaises(service.HealthAnalysisError) as error:
            self.call(failure=failure)
        self.assertEqual(error.exception.status, 502)
        self.assertIn('요청 형식 오류', str(error.exception))
        self.assertNotIn('모델을 사용할 수 없습니다', str(error.exception))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AuthTests(); self.fixture.setUp()
        self.client = self.fixture.client; self.fixture.worker_login()
        person = db.session.get(User, 1); person.has_underlying_disease = True; person.note = '고혈압'
        doc, result = document()
        db.session.add(CheckupDocument(doc_id=1, user_id=1, file_path='/static/test.pdf', uploaded_at=doc.uploaded_at))
        db.session.add(CheckupResult(doc_id=1, extraction=result.confirmed_result, confirmed_result=result.confirmed_result,
                                     confirmed_revision=2, confirmed_at=NOW, confirmed_by=1))
        db.session.commit()

    def tearDown(self):
        self.fixture.tearDown()

    def post(self):
        with patch('routes.social_worker.analyze', side_effect=lambda snapshot, model=None: report(snapshot['sources'][0]['ref'])):
            return self.client.post('/api/admin/elders/1/life-pattern-ai')

    def test_save_history_reload_and_snapshot(self):
        first = self.post(); self.assertEqual(first.status_code, 200)
        second = self.post(); self.assertNotEqual(first.json['analysis_id'], second.json['analysis_id'])
        data = self.client.get('/api/admin/elders/1/health-analysis').json
        self.assertEqual(len(data['history']), 2)
        self.assertEqual(data['latest']['analysis_id'], second.json['analysis_id'])
        self.assertEqual(data['latest']['input_snapshot']['documents'][0]['confirmed_revision'], 2)
        selected = self.client.get('/api/admin/elders/1/health-analysis?analysis_id=' + str(first.json['analysis_id'])).json
        self.assertEqual(selected['selected']['analysis_id'], first.json['analysis_id'])
        self.assertEqual(selected['latest']['analysis_id'], second.json['analysis_id'])
        self.client.post('/api/auth/logout'); self.fixture.worker_login()
        self.assertEqual(len(self.client.get('/api/admin/elders/1/health-analysis').json['history']), 2)
        self.assertEqual(db.session.query(HealthAnalysis).count(), 2)

    def test_failures_preserve_history(self):
        self.post()
        for failure in (service.HealthAnalysisError('timeout'), service.HealthAnalysisError('quota', 429)):
            with patch('routes.social_worker.analyze', side_effect=failure):
                self.assertFalse(self.client.post('/api/admin/elders/1/life-pattern-ai').json['success'])
        with patch.object(db.session, 'commit', side_effect=RuntimeError('db failure')):
            self.assertEqual(self.post().status_code, 500)
        self.assertEqual(db.session.query(HealthAnalysis).count(), 1)
        self.assertIsNotNone(db.session.query(CheckupResult).first().confirmed_result)

    def test_permissions_unassigned_changed_and_ambiguous(self):
        self.post()
        for worker_id in (None, 99):
            person = db.session.get(User, 1); person.worker_id = worker_id; db.session.commit()
            for method, url in ((self.client.get, '/api/admin/elders/1/health-analysis'),
                                (self.client.post, '/api/admin/elders/1/life-pattern-ai')):
                self.assertEqual(method(url).status_code, 403)
        with self.client.session_transaction() as session:
            session['user_id'] = 1
        self.assertEqual(self.client.get('/api/admin/elders/1/health-analysis').status_code, 401)

    def test_ownership_changes_during_analysis(self):
        def changed(snapshot, model=None):
            db.session.get(User, 1).worker_id = None; db.session.commit()
            return report()
        with patch('routes.social_worker.analyze', side_effect=changed):
            self.assertEqual(self.client.post('/api/admin/elders/1/life-pattern-ai').status_code, 403)
        self.assertEqual(db.session.query(HealthAnalysis).count(), 0)

    def test_no_history_and_invalid_selection(self):
        self.assertIsNone(self.client.get('/api/admin/elders/1/health-analysis').json['latest'])
        for value, status in (('bad', 400), ('-1', 400), ('99', 404)):
            self.assertEqual(self.client.get('/api/admin/elders/1/health-analysis?analysis_id=' + value).status_code, status)
        self.assertEqual(self.client.get('/api/admin/elders/999/health-analysis').status_code, 404)


if __name__ == '__main__':
    unittest.main()
