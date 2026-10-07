"""의학적 판정을 만들지 않는 관측값·시스템 상태 비교 회귀 검사."""
import copy
import datetime as dt
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import test_health_analysis as fixtures
from services import health_comparison_service as comparison
from services.social_worker_ai_service import calculate_risk, _risk_level_from_score
from models.models import db, User, CheckupResult, HealthAnalysis

NOW = fixtures.NOW


def status(score=75, points=-25):
    level = _risk_level_from_score(score)[0]
    return {'score': score, 'level': level, 'label': comparison.LEVEL_LABELS[level],
            'as_of': NOW.isoformat(), 'breakdown': [{'code': 'elapsed', 'item': '건강 기록 미입력 경과', 'points': points}]}


def snapshot(health=None, docs=None, logins=None):
    return fixtures.service.build_input(fixtures.user(), health or [], logins or [], docs or [], NOW)


class ComparisonTests(unittest.TestCase):
    def test_grade_boundaries_and_original_score(self):
        for score, level in [(100, 'SAFE'), (80, 'SAFE'), (79, 'WATCH'), (60, 'WATCH'), (59, 'WARN'), (40, 'WARN'), (39, 'DANGER'), (0, 'DANGER')]:
            self.assertEqual(_risk_level_from_score(score)[0], level)
        row = fixtures.health(0)
        person = fixtures.user()
        result = calculate_risk(person, [row], [NS(auth_time=NOW)], now=NOW)
        self.assertEqual(result['score'], 68)  # 기존 당뇨 감점 12 + 최신 결식 20
        clean = comparison.system_status(result, NOW)
        self.assertEqual(clean['score'], result['score'])
        self.assertEqual(clean['level'], result['risk_level_db'])
        for secret in ('비밀이름', '비밀주소', '010-1234-5678'):
            self.assertNotIn(secret, json.dumps(clean, ensure_ascii=False))

    def test_elapsed_hours_survive_status_normalization_without_guessing(self):
        row = fixtures.health(0)
        row.recorded_at = NOW - dt.timedelta(hours=23, minutes=59)
        risk = calculate_risk(fixtures.user(), [row], [NS(auth_time=NOW)], now=NOW)
        normalized = comparison.system_status(risk, NOW)
        elapsed = next(p for p in normalized['breakdown'] if p['code'] == 'elapsed')
        self.assertEqual(elapsed['item'], '건강 기록 미입력 경과 (23시간)')
        self.assertEqual(elapsed['points'], -46)
        self.assertEqual(normalized['score'], risk['score'])
        # A legacy reason without a duration must not infer hours from its penalty.
        risk['score_breakdown'] = [{'item': '미입력 경과', 'score': '-46점', 'type': 'minus'}]
        elapsed = comparison.system_status(risk, NOW)['breakdown'][0]
        self.assertEqual(elapsed['item'], '건강 기록 미입력 경과')

    def test_existing_long_login_penalty_can_be_normalized(self):
        row = fixtures.health(0)
        risk = calculate_risk(fixtures.user(), [row], [NS(auth_time=NOW - dt.timedelta(days=5))], now=NOW)
        normalized = comparison.system_status(risk, NOW)
        self.assertEqual(normalized['score'], risk['score'])
        penalty = next(p for p in normalized['breakdown'] if p['code'] == 'login_elapsed')
        self.assertEqual(penalty['points'], -20)
        self.assertEqual(penalty['item'], '장기 미접속')

    def test_daily_last_record_boundaries_missing_values_and_meals(self):
        older = fixtures.health(1); older.blood_sugar = 100
        newer = fixtures.health(1); newer.recorded_at += dt.timedelta(hours=1)
        newer.blood_sugar = None; newer.blood_pressure = 'invalid'
        edge = fixtures.health(7); edge.blood_sugar = 120
        earlier = fixtures.health(7); earlier.recorded_at -= dt.timedelta(microseconds=1)
        data = snapshot([older, newer, edge, earlier, fixtures.health(30), fixtures.health(31)])
        comparison.enrich_snapshot(data, status())
        recent = data['comparisons']['life']['recent']; before = data['comparisons']['life']['previous']
        self.assertEqual(recent['records'], 2)
        self.assertEqual(before['records'], 1)  # 같은 날짜의 마지막 기록이 최근 구간에 있으므로 이전 중복은 제외
        self.assertEqual(recent['metrics']['blood_sugar'], {'mean': 120.0, 'count': 1})
        self.assertEqual(recent['metrics']['systolic'], {'mean': 120.0, 'count': 1})
        self.assertEqual(recent['meals'], {'skipped': 2, 'known': 4, 'skip_percent': 50.0})
        self.assertTrue(recent['missing_days'])

    def test_login_dates_not_total_requests(self):
        times = [NOW, NOW - dt.timedelta(hours=1), NOW - dt.timedelta(days=7),
                 NOW - dt.timedelta(days=7, microseconds=1), NOW - dt.timedelta(days=30), NOW - dt.timedelta(days=31)]
        data = snapshot(logins=[NS(auth_time=t) for t in times])
        comparison.enrich_snapshot(data, status(), login_times=times)
        life = data['comparisons']['life']
        self.assertEqual(life['recent']['login_days'], 2)
        self.assertEqual(life['previous']['login_days'], 2)
        self.assertLessEqual(life['recent']['login_day_percent'], 100)
        self.assertIsNone(life['recent']['metrics']['blood_sugar']['mean'])

    def test_checkup_numbers_and_left_right(self):
        docs = [fixtures.document(1, '2026-10-02', [fixtures.item(value='126'), fixtures.item(name='시력(좌)', value='0.6', unit=None), fixtures.item(name='시력(우)', value='0.9', unit=None)]),
                fixtures.document(2, '2025-10-02', [fixtures.item(value='100'), fixtures.item(name='시력(좌)', value='0.8', unit=None), fixtures.item(name='시력(우)', value='0.7', unit=None)])]
        data = comparison.enrich_snapshot(snapshot(docs=docs), status())
        values = {c['name']: c['delta'] for c in data['comparisons']['checkups']}
        self.assertEqual(values, {'공복혈당': 26.0, '시력(좌)': -0.2, '시력(우)': 0.2})
        self.assertIn('1년 이상', ' '.join(data['limitations']))

    def test_checkup_restrictions_preserve_values(self):
        cases = [(None, [fixtures.item()], '검진일 미상'),
                 ('2026-10-02', [fixtures.item()], '동일 검진일'),
                 ('2026-10-03', [fixtures.item(unit='mmol/L')], '단위'),
                 ('2026-10-03', [fixtures.item(value='<126')], '정확한 숫자'),
                 ('2026-10-03', [fixtures.item(), fixtures.item(value='130')], '상충'),
                 ('2026-10-08', [fixtures.item()], '미래')]
        for date, items, reason in cases:
            with self.subTest(reason=reason):
                data = comparison.enrich_snapshot(snapshot(docs=[fixtures.document(1, date, items), fixtures.document(2, '2026-10-02')]), status())
                value = data['comparisons']['checkups'][0]
                self.assertIsNone(value['delta']); self.assertIn(reason, value['reason'])
                self.assertTrue(value['previous']); self.assertTrue(value['recent'])

    def test_missing_item_and_unit_are_not_comparable(self):
        for items in ([fixtures.item(name='ALT', unit='U/L')], [fixtures.item(unit=None)]):
            data = comparison.enrich_snapshot(snapshot(docs=[fixtures.document(1, '2026-10-02', items), fixtures.document(2, '2025-10-02')]), status())
            self.assertTrue(all(c['delta'] is None and c['reason'] for c in data['comparisons']['checkups']))

    def test_previous_status_deltas_and_same_material(self):
        data = comparison.enrich_snapshot(snapshot([fixtures.health()], [fixtures.document()]), status(0, -140))
        old = NS(input_snapshot=copy.deepcopy(data), analysis_id=3, analyzed_at=NOW)
        current = comparison.enrich_snapshot(snapshot([fixtures.health()], [fixtures.document()]), status(0, -150), old)
        previous = current['comparisons']['previous_analysis']
        self.assertTrue(previous['same_inputs']); self.assertEqual(previous['new_life_records'], 0)
        self.assertEqual(previous['status']['delta'], 0)
        self.assertEqual(previous['status']['breakdown_changes'][0]['recent'], -150)
        self.assertEqual(current['previous_analysis_id'], 3)

    def test_previous_input_changes_and_old_schema(self):
        old = NS(input_snapshot=snapshot([fixtures.health()], [fixtures.document()]), analysis_id=2, analyzed_at=NOW)
        docs = [fixtures.document()]; docs[0][1].confirmed_revision = 3
        data = snapshot([fixtures.health(), fixtures.health(0)], docs)
        data['sources'] = [s for s in data['sources'] if s['kind'] != '등록기저질환']
        comparison.enrich_snapshot(data, status(), old)
        p = data['comparisons']['previous_analysis']
        self.assertFalse(p['same_inputs']); self.assertTrue(p['documents_changed']); self.assertTrue(p['diseases_changed'])
        self.assertEqual(p['new_life_records'], 1); self.assertNotIn('status', p)


class ApiComparisonTests(unittest.TestCase):
    setUp = fixtures.ApiTests.setUp
    tearDown = fixtures.ApiTests.tearDown
    post = fixtures.ApiTests.post
    def test_comparison_persisted_and_reconfirmation_stale(self):
        first = self.post().json
        self.assertEqual(first['input_snapshot']['version'], 2)
        self.assertFalse(first['needs_reanalysis'])
        self.assertIsNotNone(first['input_snapshot']['system_status'])
        original = copy.deepcopy(first['input_snapshot'])
        second = self.post().json
        self.assertTrue(second['input_snapshot']['comparisons']['previous_analysis']['same_inputs'])
        row = db.session.query(CheckupResult).first(); row.confirmed_revision += 1; db.session.commit()
        response = self.client.get('/api/admin/elders/1/health-analysis?analysis_id=' + str(first['analysis_id'])).json
        self.assertTrue(response['selected']['needs_reanalysis'])
        self.assertEqual(response['selected']['input_snapshot'], original)
        self.assertNotEqual(response['current_status']['as_of'], original['system_status']['as_of'])

    def test_risk_failure_no_external_call_or_history_write(self):
        self.post()
        with patch('routes.social_worker.calculate_risk', side_effect=ValueError('failed')), patch('routes.social_worker.analyze') as ai:
            response = self.client.post('/api/admin/elders/1/life-pattern-ai')
            self.assertEqual(response.status_code, 500); ai.assert_not_called()
        self.assertEqual(db.session.query(HealthAnalysis).count(), 1)

    def test_previous_shape_is_preserved(self):
        old = HealthAnalysis(user_id=1, worker_id=1, model='old-model', input_snapshot=snapshot(), result=fixtures.report(), summary='old')
        db.session.add(old); db.session.commit()
        data = self.post().json
        self.assertNotIn('status', data['input_snapshot']['comparisons']['previous_analysis'])
        stored = db.session.get(HealthAnalysis, old.analysis_id)
        self.assertNotIn('version', stored.input_snapshot)

    def test_current_score_failure_still_returns_stored_result(self):
        saved = self.post().json
        with patch('routes.social_worker.calculate_risk', side_effect=ValueError('failed')):
            response = self.client.get('/api/admin/elders/1/health-analysis')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['latest']['analysis_id'], saved['analysis_id'])
        self.assertIsNone(response.json['current_status'])
        self.assertIn('계산하지 못했습니다', response.json['current_status_error'])


class ProviderComparisonTests(unittest.TestCase):
    setUp = fixtures.ProviderTests.setUp
    call = fixtures.ProviderTests.call
    def test_comparison_payload_has_no_private_notes_or_prior_summary(self):
        data = comparison.enrich_snapshot(self.snapshot, status())
        old = NS(input_snapshot=copy.deepcopy(data), analysis_id=99, analyzed_at=NOW, summary='이전 비밀 자유 요약')
        comparison.enrich_snapshot(snapshot([fixtures.health()], [fixtures.document()]), status(), old)
        self.snapshot = comparison.enrich_snapshot(snapshot([fixtures.health()], [fixtures.document()]), status(), old)
        _, client = self.call(json.dumps(fixtures.report()))
        prompt = client.return_value.models.generate_content.call_args.kwargs['contents'][0]
        for secret in ('이전 비밀 자유 요약', '비밀이름', '비밀주소', '010-1234-5678', 'analysis_id', 'doc_id', 'record_id'):
            self.assertNotIn(secret, prompt)
        self.assertIn('생활기간비교', prompt); self.assertIn('시스템점수', prompt); self.assertIn('직전분석비교', prompt)

    def test_invalid_priority_action_and_reference_rejected(self):
        for field, value in [('action', '처방 변경'), ('priority', '위험'), ('source_refs', ['missing']), ('reason', ' ')]:
            report = fixtures.report(); report['priority_actions'][0][field] = value
            with self.subTest(field=field), self.assertRaises(fixtures.service.HealthAnalysisError):
                self.call(json.dumps(report))


if __name__ == '__main__':
    unittest.main()
