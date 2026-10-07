"""실제 관측값·스냅샷 고정·읽기 전용 권한을 검증한다."""
import copy
import datetime as dt
import json
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import test_auth as fixtures
import test_health_analysis as analysis_fixtures
from models.models import db, User, Worker, HealthStatus, CheckupDocument, CheckupResult, HealthAnalysis, RiskAnalysis, AIUsage
from services import health_trend_service as service

NOW = dt.datetime(2026, 10, 7, 12)


def health(date=None, at=None, id=1, **values):
    return NS(target_date=date or NOW.date(), recorded_at=at or NOW, status_id=id,
              **{'condition_level':3, 'blood_pressure':'120/80', 'blood_sugar':110,
                 'breakfast_status':'완료', 'lunch_status':'예정', 'dinner_status':'결식', **values})


def risk(days=1, id=1, value=70, at=None):
    return NS(analyzed_at=at or NOW-dt.timedelta(days=days), analysis_id=id, risk_score=value, risk_level='WATCH')


class TrendDataTests(unittest.TestCase):
    def test_calendar_boundary_latest_target_date_and_equal_time_id(self):
        first = NOW.date()-dt.timedelta(days=6)
        rows = [health(first, NOW-dt.timedelta(days=5), 1, condition_level=1),
                health(first, NOW-dt.timedelta(days=5), 2, condition_level=5),
                health(first-dt.timedelta(days=1)), health(NOW.date()+dt.timedelta(days=1)),
                health(at=NOW+dt.timedelta(seconds=1))]
        result = service.daily_life(rows, NOW, 7)
        self.assertEqual(len(result), 7)
        self.assertEqual(result[0]['condition'], 5)
        self.assertEqual(result[0]['record_id'], 2)
        self.assertIsNone(result[-1]['condition'])
        self.assertEqual(result[0]['skip_percent'], 50)
        self.assertEqual(result[0]['known_meals'], 2)

    def test_invalid_values_excluded_per_metric_and_scheduled_meals(self):
        rows = [health(condition_level=9, blood_pressure='120 / broken', blood_sugar=None,
                       breakfast_status='예정', lunch_status='예정', dinner_status='예정')]
        last = service.daily_life(rows, NOW, 7)[-1]
        for field in ('condition','systolic','diastolic','blood_sugar','skip_percent'):
            self.assertIsNone(last[field])
        self.assertIsNotNone(last['recorded_at'])

    def test_real_zero_missing_current_and_future_score(self):
        status = {'score':36,'level':'DANGER','as_of':NOW.isoformat()}
        rows = [risk(value=0), risk(days=2,id=2), risk(days=2,id=3,value=80), risk(at=NOW+dt.timedelta(days=1))]
        result = service.daily_scores(rows,status,NOW,7)
        self.assertIsNone(result[0]['value'])
        self.assertEqual(result[-2]['value'],0)
        self.assertEqual(result[-3]['value'],80)
        self.assertEqual(result[-1]['value'],36)
        self.assertEqual(result[-1]['kind'],'calculated')
        self.assertEqual(result[-2]['kind'],'stored')

    def test_checkup_numeric_sides_units_and_raw_exclusions(self):
        item = analysis_fixtures.item
        docs = [analysis_fixtures.document(1, '2026-10-01', [item('시력(좌)','0.7',''),item('시력(우)','0.9',''),
                    item(value='<100'),item('혈압','120/80','mmHg'),item('AST','30','U/L',unreadable=True)]),
                analysis_fixtures.document(2, '2025-10-01', [item(value='90',unit='mg/dL')]),
                analysis_fixtures.document(3, None, [item(value='110')]),
                analysis_fixtures.document(4, confirmed=False)]
        result = service.checkup_series(docs,NOW)
        groups = {s['name']:s for s in result['series']}
        self.assertEqual(groups['시력(좌)']['entries'][0]['value'],0.7)
        self.assertEqual(groups['시력(우)']['entries'][0]['value'],0.9)
        self.assertEqual(groups['수축기혈압']['entries'][0]['value'],120)
        self.assertEqual(groups['이완기혈압']['entries'][0]['value'],80)
        self.assertEqual(groups['AST']['entries'][0]['reason'],'판독 불가')
        values = groups['공복혈당']['entries']
        self.assertTrue(any(e['reason']=='검진일 미상' for e in values))
        self.assertTrue(any(e['reason']=='부등호·비수치 또는 누락 값' and e['raw_value']=='<100' for e in values))
        self.assertTrue(any(e['old'] for e in values))
        self.assertEqual(result['total_documents'],3)

    def test_conflicting_values_different_units_and_duplicate_identical(self):
        item = analysis_fixtures.item; document = analysis_fixtures.document
        result = service.checkup_series([document(1,items=[item(value='100'),item(value='110')]),
                                        document(2,'2026-09-01',[item(value='90',unit='mmol/L')])],NOW)
        self.assertEqual(len(result['series']),2)
        conflict = next(s for s in result['series'] if s['unit']=='mg/dL')
        self.assertTrue(all(e['reason']=='같은 날짜의 상충 값' for e in conflict['entries']))
        same_date = service.checkup_series([document(1,items=[item()]),document(2,'2026-10-01',[item(unit='mmol/L')])],NOW)
        self.assertTrue(all(e['reason']=='같은 날짜의 단위 불일치' for s in same_date['series'] for e in s['entries']))
        identical = service.checkup_series([document(1,items=[item(),item()])],NOW)
        self.assertTrue(all(e['reason'] is None for e in identical['series'][0]['entries']))
        partial = service.checkup_series([document(1,items=[item(),item(value=None,unreadable=True)])],NOW)
        self.assertIsNone(partial['series'][0]['entries'][0]['reason'])
        self.assertEqual(partial['series'][0]['entries'][1]['reason'],'판독 불가')

    def test_limit_future_date_and_unsupported(self):
        docs = [analysis_fixtures.document(i,date=f'2026-09-{i:02}',items=[analysis_fixtures.item(name='미지원항목')]) for i in range(1,24)]
        result = service.checkup_series(docs,NOW)
        self.assertEqual(len(result['documents']),20)
        self.assertEqual(result['unsupported_items'],20)
        future = service.checkup_series([analysis_fixtures.document(date='2027-01-01')],NOW)
        self.assertEqual(future['series'][0]['entries'][0]['reason'],'미래 검진일 확인 필요')

    def test_legacy_uses_only_saved_values_and_never_invents_scores(self):
        snapshot = analysis_fixtures.service.build_input(analysis_fixtures.user(),[analysis_fixtures.health()],[],[analysis_fixtures.document()],NOW)
        original=copy.deepcopy(snapshot); data=service.legacy_trends(snapshot)
        self.assertFalse(data['scores_available']); self.assertEqual(data['scores'],[])
        self.assertEqual(data['checkups']['series'][0]['entries'][0]['value'],126)
        self.assertEqual(snapshot,original)
        self.assertEqual(data['life'][-2]['blood_sugar'],110)

    def test_graph_snapshot_is_not_added_to_external_ai_payload(self):
        ai = analysis_fixtures.service
        snapshot = ai.build_input(analysis_fixtures.user(), [], [], [analysis_fixtures.document()], NOW)
        snapshot['trends'] = {'private_graph_canary':'외부에보내면안되는그래프원문'}
        response = NS(text=json.dumps(analysis_fixtures.report()), candidates=[])
        with patch.dict(os.environ, {'GEMINI_API_KEY':'fake'}), patch.object(ai,'generate_content',return_value=response) as provider:
            ai.analyze(snapshot)
        prompt = str(provider.call_args.kwargs['contents'])
        self.assertNotIn('private_graph_canary', prompt)
        self.assertNotIn('외부에보내면안되는그래프원문', prompt)


class TrendApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.AuthTests(); self.fixture.setUp(); self.client=self.fixture.client
        self.fixture.worker_login()
        person=db.session.get(User,1); person.has_underlying_disease=True; person.note='고혈압'
        data=analysis_fixtures.document()[1].confirmed_result
        db.session.add(CheckupDocument(doc_id=1,user_id=1,file_path='/static/test.pdf'))
        db.session.add(CheckupResult(doc_id=1,extraction=data,confirmed_result=data,confirmed_revision=1))
        db.session.commit()

    def tearDown(self): self.fixture.tearDown()

    def post(self):
        with patch('routes.social_worker.analyze',side_effect=lambda snapshot,model=None:analysis_fixtures.report(snapshot['sources'][0]['ref'])):
            return self.client.post('/api/admin/elders/1/life-pattern-ai')

    def test_read_only_days_and_no_ai(self):
        with patch('routes.social_worker.analyze') as ai, patch.object(db.session,'commit') as commit:
            for days in ('7','30'):
                response=self.client.get('/api/admin/elders/1/health-trends?days='+days)
                self.assertEqual(response.status_code,200)
                self.assertEqual(len(response.json['trends']['scores']),int(days))
            ai.assert_not_called(); commit.assert_not_called()
        self.assertEqual(db.session.query(RiskAnalysis).count(),0)
        self.assertEqual(db.session.query(AIUsage).count(),0)
        self.assertEqual(self.client.get('/api/admin/elders/1/health-trends').json['trends']['days'],30)
        for days in ('0','31','07','seven',''):
            self.assertEqual(self.client.get('/api/admin/elders/1/health-trends?days='+days).status_code,400)

    def test_permissions_and_assignment_change(self):
        self.assertEqual(self.client.get('/api/admin/elders/99/health-trends').status_code,404)
        db.session.add(Worker(worker_id=2,login_id='other',password='secret',name='다른 담당자',phone_number='01055556666',address='test'))
        user=db.session.get(User,1); user.worker_id=2; db.session.commit()
        self.assertEqual(self.client.get('/api/admin/elders/1/health-trends').status_code,403)
        user.worker_id=None; db.session.commit()
        self.assertEqual(self.client.get('/api/admin/elders/1/health-trends').status_code,403)
        self.client.post('/api/auth/logout')
        self.assertEqual(self.client.get('/api/admin/elders/1/health-trends').status_code,401)

    def test_snapshot_survives_reconfirmation_new_record_and_reload(self):
        first=self.post(); self.assertEqual(first.status_code,200)
        saved=copy.deepcopy(first.json['input_snapshot']['trends']); id=first.json['analysis_id']
        record=db.session.query(CheckupResult).first(); data=copy.deepcopy(record.confirmed_result)
        data['items'][0]['value']='180'; record.confirmed_result=data; record.confirmed_revision=2
        now=dt.datetime.now()
        db.session.add(HealthStatus(status_id=1,user_id=1,target_date=now.date(),recorded_at=now,condition_level=5,blood_sugar=130,blood_pressure='130/85'))
        db.session.commit()
        latest=self.client.get('/api/admin/elders/1/health-trends').json['trends']
        self.assertEqual(latest['checkups']['series'][0]['entries'][0]['value'],180)
        selected=self.client.get(f'/api/admin/elders/1/health-analysis?analysis_id={id}').json['selected']
        self.assertEqual(selected['trends'],saved)
        self.assertEqual(selected['input_snapshot']['trends'],saved)
        second=self.post(); self.assertEqual(second.status_code,200)
        self.assertEqual(second.json['trends']['checkups']['series'][0]['entries'][0]['value'],180)
        self.assertEqual(second.json['trends']['life'][-1]['blood_sugar'],130)
        self.assertEqual(db.session.get(HealthAnalysis,id).input_snapshot['trends'],saved)
        user=db.session.get(User,1); user.worker_id=None; db.session.commit()
        self.assertEqual(self.client.get(f'/api/admin/elders/1/health-analysis?analysis_id={id}').status_code,403)

    def test_legacy_and_failed_analysis_preserve_previous(self):
        first=self.post(); id=first.json['analysis_id']
        record=db.session.get(HealthAnalysis,id); snapshot=copy.deepcopy(record.input_snapshot); snapshot.pop('trends'); record.input_snapshot=snapshot; db.session.commit()
        old=self.client.get('/api/admin/elders/1/health-analysis').json['selected']['trends']
        self.assertFalse(old['scores_available'])
        with patch('routes.social_worker.analyze',side_effect=analysis_fixtures.service.HealthAnalysisError('timeout')):
            self.assertEqual(self.client.post('/api/admin/elders/1/life-pattern-ai').status_code,502)
        self.assertEqual(self.client.get('/api/admin/elders/1/health-analysis').json['selected']['trends'],old)

    def test_calculation_failure_is_failure_without_writes(self):
        with patch('routes.social_worker.calculate_risk',side_effect=RuntimeError('test')):
            self.assertEqual(self.client.get('/api/admin/elders/1/health-trends').status_code,500)
        self.assertEqual(db.session.query(RiskAnalysis).count(),0)


if __name__ == '__main__': unittest.main()
