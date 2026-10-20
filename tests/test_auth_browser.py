"""실제 템플릿의 통합 로그인·반응형·AI 요청 상태 회귀 테스트."""
import copy
import datetime as dt
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import jsonify
from werkzeug.serving import make_server
import test_auth as fixtures
from models.models import db, User, HealthStatus, HealthAnalysis, CheckupDocument, CheckupResult
from services.health_analysis_service import HealthAnalysisError
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

CHROME = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
OUTPUT = Path(__file__).parent / 'ui_artifacts'


@unittest.skipUnless(sync_playwright and CHROME.exists(), 'playwright 또는 Chrome이 없습니다.')
class AuthBrowserTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AuthTests(); self.fixture.setUp()
        app = self.fixture.app
        self.elder = {'id': 1, 'name': '가상어르신', 'age': 75, 'phone': '010-3333-4444',
                      'address': '가상시 가상구 테스트로 123', 'risk': 'watch', 'score': 75,
                      'health': 3, 'last': '오늘 오전 9시', 'disease': '없음', 'desc': '기존 생활 패턴 요약',
                      'meal_short': '아침 완료<br>점심 예정<br>저녁 예정', 'careLevel': '일반 관리',
                      'checkup_docs': [], 'score_breakdown': [], 'action_history': [], 'scores_7days': [80]*7}
        app.view_functions['worker.api_get_elders'] = lambda: jsonify(success=True, data=[copy.deepcopy(self.elder)], unassigned=[], alert_history=[])
        self.ai_calls = 0; self.ai_fail = False
        self.used_models = []
        user = db.session.get(User, 1); user.has_underlying_disease = True; user.note = '고혈압'; db.session.commit()
        extracted = {'is_checkup': True, 'patient_name': '가상어르신', 'institution': '가상기관', 'checkup_date': '2026-10-01',
                     'items': [{'name': '공복혈당', 'value': '110', 'unit': 'mg/dL', 'reference_range': '<100',
                                'raw_text': '공복혈당 110', 'page': 1, 'unreadable': False}]}
        db.session.add(CheckupDocument(doc_id=1, user_id=1, file_path='/static/normal.pdf'))
        db.session.add(CheckupResult(doc_id=1, extraction=extracted, confirmed_result=extracted,
                                     confirmed_revision=1, confirmed_by=1))
        db.session.commit()
        def ai(snapshot, model=None):
            self.used_models.append(model)
            self.ai_calls += 1; time.sleep(1.5)
            if self.ai_fail: raise HealthAnalysisError('테스트 분석 실패')
            return {'summary': '저장된 AI 결과', 'findings': [{'title': '등록 정보 확인', 'detail': '기록을 확인하세요.', 'source_refs': [s['ref'] for s in snapshot['sources']]}],
                    'recommended_actions': ['안부 확인'], 'limitations': snapshot['limitations'],
                    'priority_actions': [{'action': '연락 확인', 'reason': '기록과 안부를 확인하세요.',
                                          'source_refs': [snapshot['sources'][0]['ref']], 'priority': '우선 확인'}]}
        self.ai_patch = patch('routes.social_worker.analyze', side_effect=ai); self.ai_mock = self.ai_patch.start()
        self.server = make_server('127.0.0.1', 0, app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.runtime = sync_playwright().start()
        self.browser = self.runtime.chromium.launch(executable_path=str(CHROME), headless=True)
        self.page = self.browser.new_page(viewport={'width': 1440, 'height': 1000})
        self.errors = []; self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('dialog', lambda dialog: dialog.accept())
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        OUTPUT.mkdir(exist_ok=True)

    def tearDown(self):
        self.browser.close(); self.runtime.stop(); self.server.shutdown(); self.thread.join()
        self.server.server_close(); self.ai_patch.stop(); self.fixture.tearDown()

    def login(self, role='worker'):
        self.page.goto(self.url + '/login')
        if role == 'worker':
            self.page.locator('#worker-role').check()
            self.page.locator('#worker-id').fill('worker'); self.page.locator('#worker-password').fill('secret')
        else: self.page.locator('#phone').fill('01033334444')
        self.page.locator('#login-submit').click()
        self.page.wait_for_url(self.url + ('/admin' if role == 'worker' else '/user'))

    def no_overflow(self):
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), self.page.viewport_size)

    def test_login_role_switch_error_loading_and_pc_user(self):
        page = self.page; page.goto(self.url + '/')
        page.wait_for_url(self.url + '/login')
        page.locator('#phone').press_sequentially('01033334444')
        self.assertEqual(page.locator('#phone').input_value(), '010-3333-4444')
        page.locator('#phone').fill('01033334444')
        self.assertEqual(page.locator('#phone').input_value(), '010-3333-4444')
        page.locator('#phone').press('Backspace')
        self.assertEqual(page.locator('#phone').input_value(), '010-3333-444')
        page.locator('#worker-role').check()
        self.assertEqual(page.locator('#phone').input_value(), '')
        page.locator('#worker-id').fill('worker'); page.locator('#worker-password').fill('wrong')
        page.locator('#login-submit').click(); page.locator('#login-error').filter(has_text='올바르지').wait_for()
        page.locator('#worker-role').uncheck()
        self.assertEqual(page.locator('#worker-password').input_value(), '')
        self.assertEqual(page.locator('#login-error').inner_text(), '')
        page.locator('#phone').fill('01033334444')
        gate = threading.Event()
        original = self.fixture.app.view_functions['user.api_user_login']
        calls = []
        def slow_login():
            calls.append(1); gate.wait(5); return original()
        self.fixture.app.view_functions['user.api_user_login'] = slow_login
        page.locator('#login-submit').click()
        page.wait_for_function("document.getElementById('worker-role').disabled")
        self.assertTrue(page.locator('#phone').is_disabled())
        page.evaluate("document.getElementById('login-form').dispatchEvent(new Event('submit', {cancelable: true}))")
        gate.set(); page.wait_for_url(self.url + '/user')
        page.locator('#screen-main.active').wait_for()
        page.wait_for_function("getComputedStyle(document.getElementById('screen-main')).opacity === '1'")
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(page.locator('body').bounding_box()['width'], 480)
        page.screenshot(path=str(OUTPUT / 'pc_user.png'))
        page.locator('#user-logout').click(); page.wait_for_url(self.url + '/login')
        self.assertFalse(page.locator('#worker-role').is_checked())
        self.assertFalse(self.errors)

    def test_admin_responsive_navigation_forms_and_ai_state(self):
        page = self.page; page.set_viewport_size({'width': 390, 'height': 844}); self.login()
        page.locator('#tbody .main-row').wait_for()
        self.assertEqual(page.locator('#dashboard').evaluate('n=>getComputedStyle(n).backgroundColor'),'rgb(238, 244, 250)')
        self.assertEqual(page.locator('#mobile-menu-toggle').inner_text().strip(),'')
        self.assertEqual(page.locator('#mobile-menu-toggle .hamburger-icon span').count(),3)
        self.assertEqual(page.locator('.topbar').evaluate('n=>getComputedStyle(n).backgroundColor'),'rgb(18, 29, 51)')
        self.assertEqual(page.locator('#pageTitle').evaluate('n=>getComputedStyle(n).color'),'rgb(255, 255, 255)')
        for width in (360, 390, 768, 1280, 1440):
            page.set_viewport_size({'width': width, 'height': 1000})
            self.no_overflow()
            if not page.locator('.detail-row.open').count():
                page.locator('#tbody .main-row').first.click()
            page.locator('.detail-row.open').wait_for(); self.no_overflow()
            if width == 360:
                self.assertNotIn('검진표 AI 분석 결과', page.locator('.detail-row.open').inner_text())
                page.screenshot(path=str(OUTPUT / 'live_mobile.png'))
            if width <= 768:
                page.locator('#mobile-menu-toggle').click()
                self.assertEqual(page.locator('#mobile-menu-toggle').get_attribute('aria-expanded'), 'true')
                self.assertEqual(page.locator('#mobile-menu-toggle').get_attribute('aria-label'), '메뉴 닫기')
                page.keyboard.press('Escape')
                self.assertEqual(page.locator('#mobile-menu-toggle').get_attribute('aria-expanded'), 'false')
                self.assertEqual(page.locator('#mobile-menu-toggle').get_attribute('aria-label'), '메뉴 열기')
                page.locator('#mobile-menu-toggle').click()
                page.locator('.nav button[onclick*="analysis"]').click()
                self.assertFalse(page.locator('#dashboardLayout').evaluate("el=>el.classList.contains('mobile-menu-open')"))
            else: page.locator('.nav button[onclick*="analysis"]').click()
            self.no_overflow()
            page.screenshot(path=str(OUTPUT / f'admin_{width}.png'))
            page.evaluate("nav('live',document.querySelector('.nav button'))")
        page.set_viewport_size({'width': 390, 'height': 844})
        page.evaluate("nav('alerts',document.querySelector('.nav button[onclick*=alerts]'))")
        self.no_overflow()
        page.evaluate("openFeedback(1,'test','전화확인')")
        modal = page.locator('#feedback .modal'); modal.wait_for()
        self.assertLessEqual(modal.bounding_box()['width'], 390)
        self.assertLessEqual(modal.bounding_box()['height'], 844)
        page.locator('#feedback .close').click()
        page.evaluate("nav('analysis',Array.from(document.querySelectorAll('.nav button')).find(b=>b.getAttribute('onclick').includes('analysis')))")
        button = page.locator('#ai-analysis-list [data-life-ai-button-id="1"]')
        button.click(); page.locator('#ai-analysis-list .ai-spinner').wait_for()
        page.evaluate('renderTable(); renderOtherSections(); analyzeLifePatternAI(1,"test")')
        self.assertTrue(page.locator('#ai-analysis-list [data-life-ai-button-id="1"]').is_disabled())
        page.locator('#ai-analysis-list').filter(has_text='저장된 AI 결과').wait_for()
        self.assertEqual(self.ai_calls, 1)
        self.ai_fail = True
        page.locator('#ai-analysis-list [data-life-ai-button-id="1"]').click()
        self.assertIn('저장된 AI 결과', page.locator('#ai-analysis-list').inner_text())
        page.locator('#ai-analysis-list [role=alert]').wait_for()
        self.assertIn('저장된 AI 결과', page.locator('#ai-analysis-list').inner_text())
        self.assertEqual(page.locator('#ai-analysis-list [data-life-ai-button-id="1"]').inner_text(), '다시 시도')
        page.evaluate('renderTable(); renderOtherSections()')
        self.assertIn('테스트 분석 실패', page.locator('#ai-analysis-list').inner_text())
        self.no_overflow(); self.assertFalse(self.errors)
        page.reload(); page.locator('#tbody .main-row').wait_for(state='attached')
        page.evaluate("nav('analysis',document.querySelector('.nav button[onclick*=analysis]'))")
        page.locator('#ai-analysis-list').filter(has_text='저장된 AI 결과').wait_for()
        self.assertEqual(self.ai_calls, 2)
        self.assertFalse(self.errors)

    def test_dashboard_emergency_and_unassigned_count_badges(self):
        page = self.page; self.login()
        page.locator('#tbody .main-row').wait_for()
        page.evaluate("""
          stopEmergencyPolling(); stopElderDataPolling();
          emergencyUnreadCount = 2;
          emergencyAlerts = [
            {alert_id: 1, user_name: '긴급어르신1', is_read: false},
            {alert_id: 2, user_name: '긴급어르신2', is_read: false}
          ];
          unassignedUsers = [{id: 2, name: '미배정어르신', age: 80, address: '테스트 주소', phone: '010-0000-0000'}];
          renderEmergencyAlerts(); renderUnassignedList();
        """)
        emergency = page.locator('[data-emergency-alert-count]')
        unassigned = page.locator('[data-unassigned-count]')
        self.assertEqual(emergency.inner_text(), '2')
        self.assertEqual(unassigned.inner_text(), '1')
        self.assertEqual(emergency.evaluate('n => getComputedStyle(n).backgroundColor'), 'rgb(239, 68, 68)')
        self.assertEqual(unassigned.evaluate('n => getComputedStyle(n).backgroundColor'), 'rgb(14, 165, 233)')
        self.assertEqual(unassigned.evaluate('n => getComputedStyle(n).color'), 'rgb(255, 255, 255)')
        page.evaluate("""
          emergencyUnreadCount = 0; emergencyAlerts = []; unassignedUsers = [];
          renderEmergencyAlerts(); renderUnassignedList();
        """)
        self.assertTrue(emergency.is_hidden())
        self.assertTrue(unassigned.is_hidden())
        self.assertFalse(self.errors)

    def test_health_analysis_history_sources_and_restore(self):
        page = self.page; self.login()
        page.locator('#tbody .main-row').wait_for()
        page.evaluate("nav('analysis',document.querySelector('.nav button[onclick*=analysis]'))")
        button = page.locator('#ai-analysis-list [data-life-ai-button-id="1"]')
        page.wait_for_function("!document.querySelector('#ai-analysis-list [data-life-ai-button-id]').disabled")
        button.click(); page.locator('#ai-analysis-list').filter(has_text='저장된 AI 결과').wait_for()
        self.assertEqual(page.locator('#ai-analysis-list .health-summary-toggle:visible').count(), 0)
        first = page.locator('#ai-analysis-list select[aria-label="건강 종합 분석 이력"]').input_value()
        for width in (360, 390, 768, 1280, 1440):
            page.set_viewport_size({'width': width, 'height': 1000}); self.no_overflow()
            if width in (390, 1440): page.screenshot(path=str(OUTPUT / f'health_analysis_{width}.png'))
        page.evaluate('window.openCheckupReview = id => { window.openedReviewId = id; }')
        page.locator('#ai-analysis-list summary').filter(has_text='전체 확인 사항·관련 근거').click()
        page.locator('#ai-analysis-list .health-analysis-finding summary').click()
        page.locator('#ai-analysis-list button').filter(has_text='페이지 원본 확인').click()
        self.assertEqual(page.evaluate('window.openedReviewId'), 1)
        button.click()
        page.wait_for_function("document.querySelector('#ai-analysis-list select[aria-label=\"건강 종합 분석 이력\"]').options.length === 2")
        page.locator('#ai-analysis-list summary').filter(has_text='모델 설정·분석 이력').click()
        page.locator('#ai-analysis-list select[aria-label="건강 종합 분석 이력"]').select_option(first)
        page.wait_for_function("!document.querySelector('#ai-analysis-list [data-life-ai-button-id]').disabled")
        self.assertEqual(page.locator('#ai-analysis-list select[aria-label="건강 종합 분석 이력"]').input_value(), first)
        page.request.post(self.url + '/api/auth/logout'); self.login()
        page.locator('#tbody .main-row').wait_for(state='attached')
        page.evaluate("nav('analysis',document.querySelector('.nav button[onclick*=analysis]'))")
        page.locator('#ai-analysis-list').filter(has_text='저장된 AI 결과').wait_for()
        self.assertEqual(page.locator('#ai-analysis-list select[aria-label="건강 종합 분석 이력"] option').count(), 2)
        self.assertNotEqual(page.locator('#ai-analysis-list select[aria-label="건강 종합 분석 이력"]').input_value(), first)
        self.assertEqual(self.ai_calls, 2); self.assertFalse(self.errors)

    def test_compact_analysis_folds_latest_score_and_missing_metrics(self):
        original_ai = self.ai_mock.side_effect
        def extended_report(snapshot, model=None):
            report = original_ai(snapshot, model)
            report['summary'] += ' 생활 기록과 확인할 내용을 사회복지사가 검토해주세요.' * 15
            for action in ('검진 원본 확인', '기존 의료 상담 여부 확인'):
                extra = copy.deepcopy(report['priority_actions'][0]); extra['action'] = action
                extra['priority'] = '일반 확인'; report['priority_actions'].append(extra)
            return report
        self.ai_mock.side_effect = extended_report
        self.elder['status_as_of'] = '2020-01-01T00:00:00'
        page = self.page; self.login()
        page.locator('#tbody .main-row').wait_for()
        page.evaluate("nav('analysis',document.querySelector('.nav button[onclick*=analysis]'))")
        panel = page.locator('#ai-analysis-list [data-life-ai-user-id="1"]')
        button = page.locator('#ai-analysis-list [data-life-ai-button-id="1"]')
        page.wait_for_function("!document.querySelector('#ai-analysis-list [data-life-ai-button-id]').disabled")
        button.click(); panel.filter(has_text='저장된 AI 결과').wait_for()
        self.assertIn('혈압·혈당 기록 없음', panel.inner_text())
        self.assertNotIn('비교 불가', panel.inner_text())
        self.assertEqual(panel.locator('.health-priority-card:visible').count(), 2)
        self.assertNotIn('일반 확인: 기존 의료 상담 여부 확인', panel.inner_text())
        panel.locator('summary').filter(has_text='추가 확인할 일').click()
        self.assertEqual(panel.locator('.health-priority-card:visible').count(), 3)
        panel.locator('summary').filter(has_text='추가 확인할 일').click()
        summary = panel.locator('.health-summary-text')
        toggle = panel.get_by_role('button', name='자세히 보기')
        toggle.wait_for()
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'false')
        self.assertEqual(toggle.get_attribute('aria-controls'), summary.get_attribute('id'))
        self.assertEqual(summary.evaluate("el => getComputedStyle(el).webkitLineClamp"), '3')
        self.assertLess(summary.evaluate('el => el.clientHeight'), summary.evaluate('el => el.scrollHeight'))
        for width in (360, 390, 768, 1280, 1440):
            page.set_viewport_size({'width': width, 'height': 1000})
            toggle.wait_for(); self.no_overflow()
        toggle.press('Enter')
        self.assertEqual(panel.get_by_role('button', name='간략히 보기').get_attribute('aria-expanded'), 'true')
        self.assertNotIn('is-collapsed', summary.get_attribute('class'))
        self.assertGreater(panel.inner_text().count('생활 기록과 확인할 내용'), 10)
        page.evaluate('window.setLifePatternAIButtons(1)')
        panel.get_by_role('button', name='간략히 보기').wait_for()
        self.assertEqual(panel.get_by_role('button', name='간략히 보기').get_attribute('aria-expanded'), 'true')
        panel.get_by_role('button', name='간략히 보기').click()
        self.assertEqual(panel.get_by_role('button', name='자세히 보기').get_attribute('aria-expanded'), 'false')
        self.assertNotIn('전체 요약 보기', panel.inner_text())
        self.assertNotIn('근거 s', panel.inner_text())
        self.assertNotIn('현재 목록 상태', page.locator('#ai-analysis-list').inner_text())
        score = page.evaluate('users[0].score')
        current_score = panel.locator('.health-current-status strong').inner_text()
        self.assertIn(f'{score}점', current_score)
        page.evaluate('loadEldersData()')
        self.assertEqual(page.evaluate('users[0].score'), score)
        panel.locator('summary').filter(has_text='전체 확인 사항·관련 근거').click()
        page.wait_for_timeout(50)
        page.evaluate('renderTable(); renderOtherSections()')
        self.assertTrue(panel.locator('details').filter(has=page.locator('summary', has_text='전체 확인 사항·관련 근거')).evaluate('el=>el.open'))
        self.ai_fail = True; button.click(); panel.locator('.ai-spinner').wait_for()
        self.assertIn('저장된 AI 결과', panel.inner_text())
        panel.locator('[role=alert]').wait_for()
        self.assertIn('저장된 AI 결과', panel.inner_text())
        panel.locator('.health-contact-actions').get_by_role('button', name='조치 결과 작성').click()
        page.locator('#feedback .modal').wait_for()
        self.assertFalse(self.errors)

    def test_registration_entries_and_expiry(self):
        page = self.page
        for width in (360, 1440):
            page.set_viewport_size({'width': width, 'height': 1000})
            page.goto(self.url + '/register/user'); page.locator('#screen-reg-privacy.active').wait_for(); self.no_overflow()
        page.goto(self.url + '/register/worker'); page.locator('#signup').wait_for(); self.no_overflow()
        self.login('user'); page.locator('#screen-main.active').wait_for()
        page.evaluate("fetch('/api/auth/logout',{method:'POST'})")
        page.wait_for_url(self.url + '/login', timeout=10000)
        self.assertFalse(self.errors)

    def test_user_registration_multiple_diseases_mobile(self):
        page = self.page; page.set_viewport_size({'width': 360, 'height': 640})
        page.goto(self.url + '/register/user')
        page.evaluate('speak = () => {}')
        page.locator('#privacy-agree').check()
        page.locator('#screen-reg-privacy').get_by_role('button', name='다음', exact=True).click()
        for step, value in [('name', '복수선택테스트'), ('phone', '01055556666'), ('address', '가상주소'), ('age', '78')]:
            page.locator('#reg-' + step).fill(value)
            page.locator('#screen-reg-' + step).get_by_role('button', name='다음', exact=True).click()
        group = page.locator('#reg-disease')
        none = group.locator('input[value="없음"]')
        self.assertTrue(none.is_checked())
        bp = group.locator('input[value="고혈압"]'); bp.focus(); bp.press('Space')
        group.get_by_text('당뇨', exact=True).click()
        self.assertFalse(none.is_checked())
        none.check()
        self.assertEqual(group.locator('input:checked').count(), 1)
        none.uncheck()
        page.locator('#screen-reg-disease').get_by_role('button', name='등록 완료').click()
        self.assertIn('없음을 선택', page.locator('#modal-text').inner_text())
        page.evaluate('closeModal()')
        bp.check(); group.locator('input[value="기타"]').check()
        page.locator('#screen-reg-disease').get_by_role('button', name='등록 완료').click()
        self.assertIn('직접 입력', page.locator('#modal-text').inner_text())
        page.evaluate('closeModal()')
        page.locator('#reg-disease-other').fill(' 천식, 고혈압, , 천식 ')
        page.locator('#screen-reg-disease').get_by_role('button', name='이전').click()
        page.locator('#screen-reg-age').get_by_role('button', name='다음').click()
        self.assertTrue(bp.is_checked())
        self.assertEqual(page.locator('#reg-disease-other').input_value(), ' 천식, 고혈압, , 천식 ')
        captured = []
        def fail(route):
            captured.append(route.request.post_data_json)
            route.fulfill(status=500, json={'success': False, 'message': '테스트 저장 실패'})
        page.route('**/api/user/register', fail)
        submit = page.locator('#screen-reg-disease').get_by_role('button', name='등록 완료')
        submit.click(); page.wait_for_function("document.querySelector('#modal-text').textContent.includes('테스트 저장 실패')")
        self.assertEqual(captured[0]['disease_note'], '고혈압, 천식')
        self.assertTrue(captured[0]['has_disease'])
        self.assertTrue(bp.is_checked())
        page.evaluate('closeModal()'); page.unroute('**/api/user/register', fail)
        submit.click(); page.wait_for_function("document.querySelector('#modal-text').textContent.includes('등록이 완료')")
        db.session.expire_all()
        self.assertEqual(User.query.filter_by(phone_number='01055556666').one().note, '고혈압, 천식')
        page.evaluate('closeModal()')
        self.no_overflow(); submit.scroll_into_view_if_needed()
        self.assertGreaterEqual(submit.bounding_box()['y'], 0)
        page.locator('#reg-disease').screenshot(path=str(Path(tempfile.gettempdir()) / 'smartcare_user_diseases.png'))
        page.evaluate('startRegister()')
        self.assertTrue(none.is_checked())
        self.assertEqual(group.locator('input:checked').count(), 1)
        self.assertEqual(page.locator('#reg-disease-other').input_value(), '')
        self.assertFalse(self.errors)

    def test_proxy_registration_multiple_diseases_mobile(self):
        page = self.page; self.login()
        page.set_viewport_size({'width': 360, 'height': 640})
        page.evaluate('openAdd()')
        for field, value in [('Name', '대리등록테스트'), ('Age', '75'), ('Phone', '01055557777'), ('Address', '가상주소')]:
            page.locator('#newElder' + field).fill(value)
        group = page.locator('#newElderDisease')
        self.assertTrue(group.locator('input[value="없음"]').is_checked())
        submit = page.locator('#add').get_by_role('button', name='등록 완료')
        group.locator('input[value="없음"]').uncheck()
        with page.expect_event('dialog') as invalid:
            submit.click()
        self.assertIn('없음을 선택', invalid.value.message)
        group.locator('input[value="고혈압"]').check()
        group.locator('input[value="당뇨병"]').check()
        group.locator('input[value="기타"]').check()
        with page.expect_event('dialog') as invalid:
            submit.click()
        self.assertIn('직접 입력', invalid.value.message)
        page.locator('#newDiseaseOther').fill(' 천식, 당뇨병, , 천식 ')
        captured = []
        def fail(route):
            captured.append(route.request.post_data_json)
            route.fulfill(status=500, json={'success': False, 'message': '테스트 저장 실패'})
        page.route('**/api/admin/elders/register', fail)
        with page.expect_event('dialog') as failed:
            submit.click()
        self.assertEqual(failed.value.message, '테스트 저장 실패')
        self.assertEqual(captured[0]['disease_note'], '고혈압, 당뇨병, 천식')
        self.assertEqual(group.locator('input:checked').count(), 3)
        self.no_overflow()
        group.screenshot(path=str(Path(tempfile.gettempdir()) / 'smartcare_proxy_diseases.png'))
        page.unroute('**/api/admin/elders/register', fail)
        submit.click(); page.wait_for_function("!document.querySelector('#add').classList.contains('show')")
        db.session.expire_all()
        user = User.query.filter_by(phone_number='01055557777').one()
        self.assertEqual(user.note, '고혈압, 당뇨병, 천식')
        self.assertTrue(user.has_underlying_disease)
        self.assertEqual(user.worker_id, 1)
        page.evaluate('openAdd()')
        self.assertEqual(group.locator('input:checked').count(), 1)
        self.assertTrue(group.locator('input[value="없음"]').is_checked())
        self.assertTrue(page.locator('#newDiseaseOther').is_hidden())
        self.assertEqual(page.locator('#newDiseaseOther').input_value(), '')
        self.assertFalse(self.errors)

    def test_background_refresh_keeps_reading_position_and_expanded_panels(self):
        page = self.page; self.login()
        page.locator('#tbody .main-row').wait_for()
        page.locator('#tbody .main-row').first.click()
        page.wait_for_function("!document.querySelector('.detail-row.open [data-life-ai-button-id]').disabled")
        page.locator('.detail-row.open [data-life-ai-button-id="1"]').click()
        detail = page.locator('.detail-row.open')
        detail.filter(has_text='저장된 AI 결과').wait_for()
        detail.locator('.life-ai-result summary').filter(has_text='분석 당시 건강 추세').click()
        for width in (360, 390, 768, 1280, 1440):
            page.set_viewport_size({'width': width, 'height': 844})
            page.evaluate('window.scrollTo(0,1400)'); page.wait_for_timeout(100)
            position = page.evaluate('window.scrollY')
            for _ in range(2):
                page.evaluate('loadEldersData()'); page.wait_for_timeout(100)
                self.assertAlmostEqual(page.evaluate('window.scrollY'), position, delta=2)
                graphs = detail.locator('.life-ai-result details').filter(has=page.locator('summary', has_text='분석 당시 건강 추세'))
                self.assertIsNotNone(graphs.get_attribute('open'))
                self.assertIn('저장된 AI 결과', detail.inner_text())
                self.no_overflow()
        # Exercise the real ten-second poll, including a changed server value.
        self.elder['address'] = '자동 갱신 주소'
        position = page.evaluate('window.scrollY')
        detail.filter(has_text='자동 갱신 주소').wait_for(timeout=15000)
        page.wait_for_timeout(100)
        self.assertAlmostEqual(page.evaluate('window.scrollY'), position, delta=2)
        # A user scrolling during a slow response must keep their new position.
        page.evaluate('stopElderDataPolling()')
        held = []
        page.route('**/api/admin/elders', lambda route: held.append(route))
        page.evaluate('void loadEldersData()')
        for _ in range(50):
            if held: break
            page.wait_for_timeout(20)
        self.assertTrue(held)
        page.evaluate('window.scrollTo(0,1800)')
        self.elder['address'] = '지연 응답 주소'
        held[0].continue_()
        detail.filter(has_text='지연 응답 주소').wait_for()
        page.wait_for_timeout(100)
        self.assertAlmostEqual(page.evaluate('window.scrollY'), 1800, delta=2)
        self.assertEqual(page.evaluate('document.body.style.minHeight'), '')
        self.assertEqual(page.evaluate('document.documentElement.style.overflowAnchor'), '')
        self.assertFalse(self.errors)

    def test_score_reasons_support_numeric_and_legacy_shapes_after_refresh(self):
        page = self.page; self.login(); page.locator('#tbody .main-row').wait_for()
        page.wait_for_function("!document.querySelector('[data-life-ai-button-id]').disabled")
        at = (dt.datetime.now() + dt.timedelta(minutes=1)).isoformat()
        page.evaluate("""at => updateHealthAnalysisStatus(1, {
          score:34, level:'DANGER', label:'위험', as_of:at,
          breakdown:[{code:'disease',item:'등록 기저질환 감점',points:-5},
                     {code:'elapsed',item:'건강 기록 미입력 경과 (23시간)',points:-46},
                     {code:'irregular',item:'입력 시간 불규칙',points:-15}]
        })""", at)
        page.locator('#tbody .main-row').first.click()
        reasons = page.locator('.detail-row.open .score-reason-list')
        for text in ('-5점', '-46점', '-15점'): self.assertIn(text, reasons.inner_text())
        self.assertIn('건강 기록 미입력 경과 (23시간)', reasons.inner_text())
        self.assertNotIn('undefined', reasons.inner_text())
        self.assertEqual(reasons.locator('.score-tag.minus').count(), 3)
        page.evaluate('loadEldersData()')
        page.wait_for_function("document.querySelector('.detail-row.open .score-reason-list').textContent.includes('-46점')")
        self.assertNotIn('undefined', reasons.inner_text())
        # 기존 목록 API 형식도 점수·스타일을 유지하고, 새 상태에는 감점만 전달한다.
        self.assertIn('건강 기록 미입력 경과 (23시간)', reasons.inner_text())
        self.elder.update(score=70, risk='watch', status_as_of=(dt.datetime.now()+dt.timedelta(minutes=2)).isoformat(),
            score_breakdown=[{'item':'기본 점수','score':'100점','type':'base'},
                             {'item':'건강 기록 미입력 경과','score':'-30점','type':'minus'}])
        page.evaluate('loadEldersData()')
        page.wait_for_function("document.querySelector('.detail-row.open .score-reason-list').textContent.includes('-30점')")
        self.assertIn('100점',reasons.inner_text()); self.assertNotIn('undefined',reasons.inner_text())
        self.assertEqual(reasons.locator('.score-tag.minus').count(),1)
        self.assertEqual(reasons.locator('.score-tag.base').count(),1)
        page.locator('.detail-row.open .life-ai-result summary').filter(has_text='모델 설정·분석 이력').click()
        page.evaluate("nav('analysis',document.querySelector('.nav button[onclick*=analysis]'))")
        panel=page.locator('#ai-analysis-list [data-life-ai-user-id="1"]')
        self.assertIn('주요 감점: 건강 기록 미입력 경과',panel.inner_text())
        self.assertNotIn('기본 점수',panel.inner_text())
        self.assertFalse(self.errors)

    def test_health_comparison_state_changes_and_legacy_mobile(self):
        now = dt.datetime.now()
        for index, days, sugar in [(1, 10, 100), (2, 1, 120)]:
            at = now - dt.timedelta(days=days)
            db.session.add(HealthStatus(status_id=index, user_id=1, target_date=at.date(), recorded_at=at,
                condition_level=3, breakfast_status='완료', lunch_status='완료' if index == 1 else '결식',
                dinner_status='예정', blood_pressure='120/80', blood_sugar=sugar))
        # 확정된 과거 검진을 하나 추가하여 실제 비교 UI를 검사한다.
        old = copy.deepcopy(db.session.query(CheckupResult).first().confirmed_result)
        old['checkup_date'] = '2025-10-01'; old['items'][0]['value'] = '100'
        db.session.add(CheckupDocument(doc_id=2, user_id=1, file_path='/static/normal.pdf'))
        db.session.add(CheckupResult(doc_id=2, extraction=old, confirmed_result=old, confirmed_revision=1, confirmed_by=1))
        db.session.commit()
        page = self.page; self.login()
        page.locator('#tbody .main-row').wait_for()
        page.evaluate("nav('analysis',document.querySelector('.nav button[onclick*=analysis]'))")
        panel = page.locator('#ai-analysis-list [data-life-ai-user-id="1"]')
        button = page.locator('#ai-analysis-list [data-life-ai-button-id="1"]')
        page.wait_for_function("!document.querySelector('#ai-analysis-list [data-life-ai-button-id]').disabled")
        button.click(); panel.locator('.ai-spinner').wait_for()
        page.evaluate("analyzeLifePatternAI(1,'가상어르신')")
        panel.filter(has_text='저장된 AI 결과').wait_for()
        self.assertEqual(self.ai_calls, 1)
        visible = panel.inner_text()
        for expected in ('현재 상태', '점수는 높을수록 안전', '우선 확인: 연락 확인', '핵심 변화', '표본 1'):
            self.assertIn(expected, visible)
        for hidden in ('첫 분석', '근거 s1:', '수치 차이: +10', '현재 조회 상태', '사회복지사 권장 확인'):
            self.assertNotIn(hidden, visible)
        self.assertEqual(panel.locator('details[open]').count(), 0)
        self.assertEqual(panel.locator('.health-current-status').count(), 1)
        panel.locator('summary').filter(has_text='변화 수치·기간 자세히 보기').click()
        self.assertIn('수치 차이: +10', panel.inner_text())
        self.assertNotIn('첫 분석', panel.inner_text())
        panel.locator('summary').filter(has_text='변화 수치·기간 자세히 보기').click()
        for width in (360, 390, 768, 1280, 1440):
            page.set_viewport_size({'width': width, 'height': 1000}); self.no_overflow()
            self.assertTrue(panel.evaluate('el => el.scrollWidth <= el.clientWidth'))
            if width in (390, 1440): page.screenshot(path=str(OUTPUT / f'health_comparison_{width}.png'))
        button.click()
        panel.filter(has_text='같은 자료로 재분석').wait_for()
        self.assertEqual(self.ai_calls, 2)
        db.session.expire_all()
        record = db.session.query(CheckupResult).filter_by(doc_id=1).first()
        record.confirmed_revision += 1; db.session.commit()
        panel.locator('summary').filter(has_text='모델 설정·분석 이력').click()
        panel.get_by_role('button', name='현재 자료·저장 결과 새로고침').click()
        panel.locator('.health-reanalysis-note').wait_for()
        panel.locator('summary').filter(has_text='사용한 검진 자료·데이터 한계').click()
        limitations = panel.locator('.health-analysis-data ul')
        self.assertGreater(limitations.evaluate("el => parseFloat(getComputedStyle(el).paddingInlineStart)"), 0)
        self.assertIn('재확정되었다면', panel.inner_text())
        panel.locator('summary').filter(has_text='전체 확인 사항·관련 근거').click()
        panel.locator('.health-analysis-finding summary').click()
        page.evaluate('window.openCheckupReview = id => { window.openedReviewId = id; }')
        panel.get_by_role('button', name='검진표 1 · 1페이지 원본 확인', exact=True).first.click()
        self.assertEqual(page.evaluate('window.openedReviewId'), 1)
        db.session.expire_all()
        legacy = db.session.query(HealthAnalysis).order_by(HealthAnalysis.analysis_id.desc()).first()
        data = copy.deepcopy(legacy.input_snapshot)
        for key in ('version', 'system_status', 'comparisons', 'previous_analysis_id'): data.pop(key, None)
        result = copy.deepcopy(legacy.result); result.pop('priority_actions')
        legacy.input_snapshot = data; legacy.result = result; db.session.commit()
        page.reload(); page.locator('#tbody .main-row').wait_for(state='attached')
        page.evaluate("nav('analysis',document.querySelector('.nav button[onclick*=analysis]'))")
        panel.filter(has_text='이 분석에는 비교 정보가 저장되지 않았습니다.').wait_for()
        self.assertIn('이전 형식의 결과', panel.inner_text()); self.no_overflow()
        self.assertFalse(self.errors)

    def test_ai_model_panel_selection_pending_and_checkup(self):
        page = self.page; self.login()
        page.locator('#tbody .main-row').wait_for()
        page.evaluate("nav('analysis',document.querySelector('.nav button[onclick*=analysis]'))")
        page.locator('#ai-model-open').click()
        panel = page.locator('#ai-model-settings'); panel.locator('[data-ai-usage] .ai-usage-card').first.wait_for()
        self.assertEqual(panel.locator('.ai-usage-card').count(), 4)
        self.assertIn('앱 호출 기준 예상치', panel.inner_text())
        for model in ('gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-2.5-flash', 'gemini-3.5-flash-lite'):
            panel.locator('select').select_option(model)
            self.assertEqual(page.evaluate("localStorage.getItem('smartcare_ai_model')"), model)
        for width in (360, 390, 768, 1280, 1440):
            page.set_viewport_size({'width': width, 'height': 1000})
            self.assertTrue(panel.evaluate('el => el.scrollWidth <= el.clientWidth'))
            if width in (390, 1440): page.screenshot(path=str(OUTPUT / f'ai_models_{width}.png'))
        panel.locator('select').select_option('gemini-3.8-flash')
        panel.locator('[data-ai-close]').click()
        button = page.locator('#ai-analysis-list [data-life-ai-button-id="1"]')
        page.wait_for_function("!document.querySelector('#ai-analysis-list [data-life-ai-button-id]').disabled")
        button.click(); page.locator('#ai-analysis-list .ai-spinner').wait_for()
        page.locator('#ai-model-open').click(); panel.locator('select').select_option('gemini-2.5-flash'); panel.locator('[data-ai-close]').click()
        page.locator('#ai-analysis-list').filter(has_text='저장된 AI 결과').wait_for()
        self.assertEqual(self.used_models, ['gemini-3.8-flash'])
        page.locator('#ai-analysis-list summary').filter(has_text='모델 설정·분석 이력').click()
        self.assertIn('분석에 사용한 모델: Gemini 3.8 Flash', page.locator('#ai-analysis-list').inner_text())
        page.reload(); page.locator('#tbody .main-row').wait_for(state='attached')
        page.locator('#ai-model-open').click(); self.assertEqual(panel.locator('select').input_value(), 'gemini-2.5-flash')
        panel.locator('[data-ai-usage] .ai-usage-card').first.wait_for()
        before = panel.locator('[data-ai-status]').inner_text()
        page.route('**/api/admin/ai/usage', lambda route: route.fulfill(status=503, content_type='application/json', body='{"success":false,"message":"offline"}'))
        panel.locator('[data-ai-refresh]').click(); panel.locator('[data-ai-error]').filter(has_text='갱신 실패').wait_for()
        self.assertEqual(panel.locator('[data-ai-status]').inner_text(), before)
        self.assertEqual(panel.locator('.ai-usage-card').count(), 4)
        page.unroute('**/api/admin/ai/usage'); panel.locator('[data-ai-close]').click()
        usage = page.request.get(self.url + '/api/admin/ai/usage').json()
        usage['models'][0]['unknown_token_calls'] = 1; usage['models'][0]['remaining']['tpm'] = None
        import json
        page.route('**/api/admin/ai/usage', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps(usage)))
        page.locator('#ai-model-open').click()
        panel.locator('.ai-usage-card').first.filter(has_text='확인 불가').wait_for()
        self.assertEqual(panel.locator('.ai-usage-card').first.locator('progress').count(), 2)
        panel.locator('[data-ai-close]').click(); page.unroute('**/api/admin/ai/usage')
        page.evaluate('openCheckupReview(1)')
        review = page.locator('#checkup-review'); review.locator('[data-items] tr').first.wait_for()
        review.get_by_role('button', name='모델 선택·사용량').click()
        panel.locator('select').select_option('gemini-3.5-flash-lite'); panel.locator('[data-ai-close]').click()
        from test_checkup import result
        recorded = []
        def extract(path, model=None):
            recorded.append(model); time.sleep(1.5); return result('가상어르신')
        with patch('routes.checkup.extract_document', side_effect=extract):
            review.locator('[data-analyze]').click(); review.locator('.ai-spinner').wait_for()
            self.assertIn('Gemini 3.5 Flash Lite', review.locator('[data-status]').inner_text())
            review.locator('[data-model-used]').filter(has_text='gemini-3.5-flash-lite').wait_for()
        self.assertEqual(recorded, ['gemini-3.5-flash-lite'])
        review.locator('[data-close]').click()
        page.evaluate("localStorage.setItem('smartcare_ai_model','invalid-model')")
        page.reload(); page.locator('#ai-model-open').click()
        page.wait_for_function("document.querySelector('#ai-model-settings select').options.length === 4")
        self.assertEqual(panel.locator('select').input_value(), 'gemini-3.6-flash')
        self.assertFalse(self.errors)


if __name__ == '__main__': unittest.main()
