"""실제 SVG·과거 이력·모바일·응답 순서를 최소 모의 자료로 검사한다."""
import copy
import datetime as dt
import json
import threading
import unittest

import test_auth_browser as browser_fixtures
from models.models import db, HealthStatus, RiskAnalysis, CheckupDocument, CheckupResult


@unittest.skipUnless(browser_fixtures.sync_playwright and browser_fixtures.CHROME.exists(), 'playwright 또는 Chrome 없음')
class HealthTrendBrowserTests(unittest.TestCase):
    def setUp(self):
        self.fixture=browser_fixtures.AuthBrowserTests(); self.fixture.setUp()
        self.page=self.fixture.page; self.now=dt.datetime.now()
        for id,days,value in [(1,3,70),(2,1,0)]:
            at=self.now-dt.timedelta(days=days)
            db.session.add(RiskAnalysis(analysis_id=id,user_id=1,risk_score=value,risk_level='WATCH',analyzed_at=at))
            db.session.add(HealthStatus(status_id=id,user_id=1,target_date=at.date(),recorded_at=at,
                condition_level=id+2,blood_pressure='120/80',blood_sugar=100+id*10,
                breakfast_status='완료',lunch_status='결식',dinner_status='예정'))
        old=copy.deepcopy(db.session.query(CheckupResult).first().confirmed_result)
        old['checkup_date']='2025-10-01'; old['items'][0]['value']='100'
        db.session.add(CheckupDocument(doc_id=2,user_id=1,file_path='/static/normal.pdf'))
        db.session.add(CheckupResult(doc_id=2,extraction=old,confirmed_result=old,confirmed_revision=1))
        db.session.commit()
        self.fixture.login(); self.page.locator('#tbody .main-row').wait_for()

    def tearDown(self): self.fixture.tearDown()

    def analysis(self):
        self.page.evaluate("nav('analysis',document.querySelector('.nav button[onclick*=analysis]'))")
        self.page.wait_for_function("!document.querySelector('#ai-analysis-list [data-life-ai-button-id]').disabled")
        return self.page.locator('#ai-analysis-list [data-life-ai-user-id="1"]')

    def test_current_chart_real_zero_gaps_keyboard_mobile_and_original(self):
        page=self.page; chart=page.locator('#main-health-trend')
        score=chart.locator('svg[aria-label="시스템 점수 · 높을수록 안전 추세 그래프"]')
        score.wait_for()
        self.assertEqual(score.locator('circle').count(),3)
        # 어제와 오늘만 연결한다. 3일 전 점수와는 빈 날짜를 가로질러 연결하지 않는다.
        self.assertEqual(score.locator('polyline').count(),1)
        self.assertEqual(len(score.locator('polyline').get_attribute('points').split()),2)
        # 실제 0점은 누락과 구분하여 점으로 표시한다.
        zero=score.locator('circle').filter(has=page.locator('title',has_text='점수 0 점'))
        zero.focus(); page.keyboard.press('Enter')
        self.assertIn('점수 0 점',chart.locator('.health-trend-point-info').inner_text())
        page.locator('#tbody .main-row').first.click()
        detail=page.locator('[data-current-trend-details="1"]')
        detail.locator('summary').first.click(); detail.get_by_label('생활 지표').wait_for()
        detail.get_by_label('생활 지표').select_option('pressure')
        pressure=detail.locator('svg[aria-label="혈압 추세 그래프"]'); pressure.wait_for()
        self.assertEqual(pressure.locator('circle').count(),4)
        self.assertEqual(pressure.locator('polyline').count(),0)
        detail.get_by_label('생활 지표').select_option('meals')
        self.assertEqual(detail.locator('svg[aria-label="결식 비율 추세 그래프"] circle').count(),2)
        for width in (360,390,768,1280,1440):
            page.set_viewport_size({'width':width,'height':1000}); self.fixture.no_overflow()
            self.assertTrue(detail.evaluate('el=>el.scrollWidth<=el.clientWidth'))
            if width in (390,1440): detail.screenshot(path=str(browser_fixtures.OUTPUT/f'health_trends_{width}.png'))
        detail.locator('.health-trend-table summary').filter(has_text='검진 값').click()
        detail.get_by_role('button',name='검진표 1 · 1페이지',exact=True).click()
        page.locator('#checkup-review [data-confirmed]').filter(has_text='저장된 확정 결과').wait_for()
        page.locator('#checkup-review [data-close]').click()
        self.assertFalse(self.fixture.errors)

    def test_history_uses_snapshot_after_reconfirmation_and_failed_reanalysis(self):
        page=self.page; panel=self.analysis(); button=panel.locator('xpath=..').locator('[data-life-ai-button-id="1"]')
        button.click(); panel.filter(has_text='저장된 AI 결과').wait_for()
        panel.locator('summary').filter(has_text='분석 당시 건강 추세').click()
        graph=panel.locator('details').filter(has=page.locator('summary',has_text='분석 당시 건강 추세')).first
        first=panel.get_by_label('건강 종합 분석 이력').input_value()
        graph.locator('.health-trend-table summary').filter(has_text='검진 값').click()
        self.assertIn('110',graph.locator('table').last.inner_text())
        graph.get_by_label('생활 지표').select_option('sugar')
        saved_values=graph.locator('svg[aria-label="생활 혈당 · 측정 조건 미확인 추세 그래프"] circle').count()
        db.session.expire_all(); result=db.session.query(CheckupResult).filter_by(doc_id=1).first()
        data=copy.deepcopy(result.confirmed_result); data['items'][0]['value']='180'
        result.confirmed_result=data; result.confirmed_revision=2; db.session.commit()
        self.fixture.ai_fail=True; button.click(); panel.locator('.ai-spinner').wait_for()
        self.assertIn('110',graph.locator('table').last.inner_text())
        panel.locator('[role=alert]').wait_for()
        self.assertEqual(graph.locator('svg[aria-label="생활 혈당 · 측정 조건 미확인 추세 그래프"] circle').count(),saved_values)
        self.fixture.ai_fail=False; button.click()
        page.wait_for_function("document.querySelector('#ai-analysis-list select[aria-label=\"건강 종합 분석 이력\"]').options.length===2")
        self.assertIn('180',graph.locator('table').last.text_content())
        panel.locator('summary').filter(has_text='모델 설정·분석 이력').click()
        panel.get_by_label('건강 종합 분석 이력').select_option(first)
        page.wait_for_function("!document.querySelector('#ai-analysis-list [data-life-ai-button-id]').disabled")
        self.assertIn('110',graph.locator('table').last.inner_text()); self.assertNotIn('180',graph.locator('table').last.inner_text())
        graph.get_by_role('button',name='검진표 1 · 1페이지',exact=True).click()
        page.locator('#checkup-review .health-reanalysis-note').filter(has_text='분석 당시 확정 버전 1 · 현재 확정 버전 2').wait_for()
        page.locator('#checkup-review [data-close]').click()
        page.reload(); page.locator('#ai-analysis-list').filter(has_text='저장된 AI 결과').wait_for()
        panel.locator('summary').filter(has_text='분석 당시 건강 추세').click()
        self.assertIn('180',graph.locator('table').last.text_content())
        self.assertFalse(self.fixture.errors)

    def test_slow_period_switch_no_duplicate_stale_response_or_false_refresh(self):
        page=self.page; chart=page.locator('#main-health-trend')
        chart.locator('svg').wait_for()
        first_asof=chart.locator('.health-analysis-meta').first.inner_text()
        calls=[]; held=[]
        body=page.request.get(self.fixture.url+'/api/admin/elders/1/health-trends?days=30').json()
        body['trends']['as_of']='2000-01-01T00:00:00'
        def hold(route): calls.append(route.request.url); held.append(route)
        page.route('**/health-trends?days=30',hold)
        chart.get_by_label('조회 기간').select_option('30')
        page.wait_for_function("document.querySelector('#main-health-trend').getAttribute('aria-busy')==='true'")
        page.evaluate('HealthTrends.scan(); HealthTrends.scan()')
        self.assertEqual(len(calls),1)
        chart.get_by_label('조회 기간').select_option('7')
        held[0].fulfill(status=200,content_type='application/json',body=json.dumps(body))
        self.assertEqual(chart.get_by_label('조회 기간').input_value(),'7')
        self.assertEqual(chart.locator('.health-analysis-meta').first.inner_text(),first_asof)
        page.unroute('**/health-trends?days=30')
        page.route('**/health-trends?days=7',lambda route:route.fulfill(status=503,content_type='application/json',body='{"success":false,"message":"통신 오류"}'))
        chart.get_by_role('button',name='새로고침',exact=True).click()
        chart.locator('[role=alert]').wait_for()
        self.assertEqual(chart.locator('.health-analysis-meta').first.inner_text(),first_asof)
        self.assertTrue(chart.locator('svg').count())
        self.assertFalse(chart.get_by_role('button',name='새로고침',exact=True).is_disabled())
        self.assertFalse(self.fixture.errors)

    def test_invalidation_during_request_fetches_fresh_data_after_completion(self):
        page=self.page; chart=page.locator('#main-health-trend'); chart.locator('svg').wait_for()
        body=page.request.get(self.fixture.url+'/api/admin/elders/1/health-trends?days=7').json()
        held=[]
        page.route('**/health-trends?days=7',lambda route:held.append(route))
        chart.get_by_role('button',name='새로고침',exact=True).click()
        page.wait_for_function("document.querySelector('#main-health-trend').getAttribute('aria-busy')==='true'")
        self.assertEqual(len(held),1)
        page.evaluate('HealthTrends.invalidate(1)')
        self.assertEqual(len(held),1)
        stale=copy.deepcopy(body); stale['trends']['as_of']='2000-01-01T00:00:00'
        held[0].fulfill(status=200,content_type='application/json',body=json.dumps(stale))
        for _ in range(20):
            if len(held)==2: break
            page.wait_for_timeout(50)
        self.assertEqual(len(held),2)
        held[1].fulfill(status=200,content_type='application/json',body=json.dumps(body))
        page.wait_for_function("document.querySelector('#main-health-trend').getAttribute('aria-busy')==='false'")
        self.assertNotIn('2000-01-01',chart.locator('.health-analysis-meta').first.inner_text())
        self.assertFalse(self.fixture.errors)


if __name__ == '__main__': unittest.main()
