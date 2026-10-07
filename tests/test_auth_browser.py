"""실제 템플릿의 통합 로그인·반응형·AI 요청 상태 회귀 테스트."""
import copy
import threading
import time
import unittest
from pathlib import Path
from flask import jsonify
from werkzeug.serving import make_server
import test_auth as fixtures
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
        def ai(user_id):
            self.ai_calls += 1; time.sleep(1.5)
            if self.ai_fail: return jsonify(success=False, message='테스트 분석 실패'), 502
            return jsonify(success=True, analysis='저장된 AI 결과')
        app.view_functions['worker.api_analyze_life_pattern'] = ai
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
        self.server.server_close(); self.fixture.tearDown()

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
        page.locator('#phone').fill('01033334444'); page.locator('#worker-role').check()
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
        for width in (360, 390, 768, 1280, 1440):
            page.set_viewport_size({'width': width, 'height': 1000})
            self.no_overflow()
            if not page.locator('.detail-row.open').count():
                page.locator('#tbody .main-row').first.click()
            page.locator('.detail-row.open').wait_for(); self.no_overflow()
            if width == 360: page.screenshot(path=str(OUTPUT / 'live_mobile.png'))
            if width <= 768:
                page.locator('#mobile-menu-toggle').click()
                self.assertEqual(page.locator('#mobile-menu-toggle').get_attribute('aria-expanded'), 'true')
                page.keyboard.press('Escape')
                self.assertEqual(page.locator('#mobile-menu-toggle').get_attribute('aria-expanded'), 'false')
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


if __name__ == '__main__': unittest.main()
