"""선택적 브라우저 회귀 테스트: playwright와 로컬 Chrome이 있을 때 실행."""
import copy
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_checkup as fixtures
from flask import session, send_from_directory
from werkzeug.serving import make_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

CHROME = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
STATIC = Path(__file__).resolve().parents[1] / 'app' / 'static'


@unittest.skipUnless(sync_playwright and CHROME.exists(), 'playwright 또는 Chrome이 없습니다.')
class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ApiTests(); self.fixture.setUp()
        self.app = self.fixture.app
        @self.app.before_request
        def login(): session['admin_worker_id'] = 1
        @self.app.get('/review')
        def review():
            return '''<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
            <link rel="stylesheet" href="/assets/css/checkup_review.css"></head><body>
            <button onclick="openCheckupReview(1)">Review</button>
            <script src="/assets/js/checkup_review.js"></script></body></html>'''
        @self.app.get('/assets/<path:filename>')
        def assets(filename): return send_from_directory(STATIC, filename)
        self.server = make_server('127.0.0.1', 0, self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.runtime = sync_playwright().start()
        self.browser = self.runtime.chromium.launch(executable_path=str(CHROME), headless=True)
        self.page = self.browser.new_page(viewport={'width': 1440, 'height': 1000})
        self.errors = []; self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.url = f'http://127.0.0.1:{self.server.server_port}/review'

    def tearDown(self):
        self.browser.close(); self.runtime.stop(); self.server.shutdown(); self.thread.join()
        self.server.server_close(); self.fixture.tearDown()

    def test_review_save_reload_reanalysis_and_mobile(self):
        page = self.page; page.goto(self.url); page.get_by_text('Review', exact=True).click()
        page.wait_for_function("document.querySelector('[data-status]').textContent.includes('AI')")
        data = fixtures.result(); data['items'][0]['raw_text'] = '<script>window.injected=true</script>'
        def extraction(_): time.sleep(1); return copy.deepcopy(data)
        with patch('routes.checkup.extract_document', side_effect=extraction) as mocked:
            page.locator('[data-analyze]').click()
            page.wait_for_function("document.querySelector('[data-analyze]').disabled")
            page.locator('[data-close]').click(); page.get_by_text('Review', exact=True).click()
            page.locator('[data-items] tr').first.wait_for()
            page.wait_for_function("!document.querySelector('[data-analyze]').disabled")
            self.assertEqual(page.locator('[data-items] tr').count(), 2)
            self.assertEqual(mocked.call_count, 1)
            self.assertIsNone(page.evaluate('window.injected'))
            page.locator('[data-items] tr').first.locator('input[type=text]').nth(1).fill('99')
            page.locator('[data-reviewed]').check(); page.locator('[data-confirm]').click()
            page.wait_for_function("document.querySelector('[data-confirmed]').textContent.includes('99')")
            page.reload(); page.get_by_text('Review', exact=True).click()
            page.wait_for_function("document.querySelector('[data-confirmed]').textContent.includes('99')")
            data['items'][0]['value'] = '110'
            page.locator('[data-analyze]').click()
            page.wait_for_function("document.querySelector('[data-items] input[aria-label$=value]').value === '110'")
            self.assertIn('99', page.locator('[data-confirmed]').inner_text())
        page.set_viewport_size({'width': 390, 'height': 844})
        self.assertLessEqual(page.locator('#checkup-review').bounding_box()['width'], 390)
        self.assertEqual(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), True)
        frame = page.locator('[data-original] iframe').bounding_box()
        modal = page.locator('#checkup-review').bounding_box()
        self.assertLessEqual(frame['x'] + frame['width'], modal['x'] + modal['width'])
        page.screenshot(path=str(Path(__file__).parent / 'fixtures' / 'checkups' / 'review_mobile_preview.png'))
        self.assertFalse(self.errors)


if __name__ == '__main__': unittest.main()
