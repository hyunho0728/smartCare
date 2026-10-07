"""웹 알림 복원·읽음·오류·반응형·자동 갱신을 실제 Chromium에서 검증한다."""
import datetime as dt
import json
import unittest
from unittest.mock import patch
import test_auth_browser as browser_fixtures
from test_status_alerts import snapshot
from models.models import db, StatusAlert
from services import status_alert_service as service


@unittest.skipUnless(browser_fixtures.sync_playwright and browser_fixtures.CHROME.exists(), 'playwright 또는 Chrome이 없습니다.')
class StatusAlertBrowserTests(unittest.TestCase):
    setUp = browser_fixtures.AuthBrowserTests.setUp
    tearDown = browser_fixtures.AuthBrowserTests.tearDown
    login = browser_fixtures.AuthBrowserTests.login
    no_overflow = browser_fixtures.AuthBrowserTests.no_overflow

    def change(self, level='WATCH', score=70, hours=10, at=None):
        at=at or dt.datetime.now()
        data=snapshot(level,score,hours)
        data['status']['as_of']=at.isoformat()
        data['health_records']=[{'record_id':41,'recorded_at':at.isoformat(),'target_date':at.date().isoformat(),
            'condition_level':3,'breakfast':'완료','lunch':'완료','dinner':'예정','blood_pressure':'120/80','blood_sugar':100}]
        with patch.object(service,'_snapshot',return_value=data):
            return service.check_user(1,at)

    def test_restore_detail_explicit_read_mobile_and_new_poll_notice(self):
        at=dt.datetime.now()-dt.timedelta(minutes=10)
        self.change('SAFE',90,0,at); alert_id=self.change(at=at+dt.timedelta(minutes=5))
        page=self.page; self.login()
        dashboard=page.locator('[data-status-alert-dashboard]')
        dashboard.locator('.status-alert-card').wait_for()
        self.assertEqual(page.locator('.nav [data-status-alert-count]').inner_text(),'1')
        self.assertTrue(page.locator('.status-alert-notice').is_hidden())
        dashboard.get_by_role('button',name='원인·관련 기록').click()
        modal=page.locator('#status-alert-detail')
        modal.filter(has_text='변경된 점수 산정 근거').wait_for()
        self.assertIn('10시간',modal.inner_text())
        self.assertFalse(db.session.get(StatusAlert,alert_id).is_read)
        modal.get_by_text('변경 후 관련 기록 보기',exact=True).click()
        self.assertIn('건강 #41',modal.inner_text()); self.assertIn('120/80',modal.inner_text())
        for width in (360,390,768,1280,1440):
            page.set_viewport_size({'width':width,'height':844}); self.no_overflow()
            self.assertTrue(modal.evaluate('n=>n.scrollWidth<=n.clientWidth'))
            buttons=modal.get_by_role('button').all()
            self.assertTrue(all(b.bounding_box()['height']>=44 for b in buttons))
        modal.get_by_role('button',name='읽음 처리',exact=True).click()
        modal.locator('p').filter(has_text='읽음 '+str(dt.datetime.now().year)).wait_for()
        page.wait_for_function("document.querySelector('.nav [data-status-alert-count]').textContent==='0'")
        db.session.expire_all(); self.assertTrue(db.session.get(StatusAlert,alert_id).is_read)
        modal.get_by_role('button',name='닫기',exact=True).click()
        page.reload(); page.locator('#tbody .main-row').wait_for()
        page.evaluate("nav('alerts',document.querySelector('.nav button[onclick*=alerts]')); switchAlertTab('history',document.getElementById('history-alert-tab'))")
        history=page.locator('[data-status-alert-history]')
        history.locator('.is-read').wait_for()
        self.assertIn('읽음',history.inner_text())
        # The real poll surfaces an event created after the initial load.
        page.wait_for_timeout(100)
        self.change('DANGER',20,35)
        page.locator('.nav [data-status-alert-count]').filter(has_text='1').wait_for(timeout=15000)
        self.assertTrue(page.locator('.status-alert-notice').is_visible())
        page.locator('#recent-alert-tab').click()
        page.locator('[data-status-alert-recent] button').filter(has_text='원인·관련 기록').click()
        modal.filter(has_text='변경된 점수 산정 근거').wait_for()
        modal.get_by_role('button',name='어르신 현재 상세 보기').click()
        page.locator('.detail-row.open').wait_for()
        self.assertTrue(modal.is_hidden())
        self.assertFalse(self.errors)

    def test_query_read_errors_slow_duplicate_and_detail_races(self):
        at=dt.datetime.now()-dt.timedelta(minutes=20)
        self.change('SAFE',90,0,at)
        first=self.change(at=at+dt.timedelta(minutes=5))
        second=self.change('DANGER',20,35,at+dt.timedelta(minutes=10))
        page=self.page; self.login()
        dashboard=page.locator('[data-status-alert-dashboard]')
        dashboard.locator('.status-alert-card').first.wait_for()
        stamp=page.locator('[data-status-alert-query]').first.inner_text()
        page.route('**/api/admin/status-alerts',lambda route:route.fulfill(status=503,content_type='application/json',body=json.dumps({'success':False,'message':'조회 실패'})))
        page.locator('[data-status-alert-refresh]').first.click()
        page.locator('[data-status-alert-error]').first.filter(has_text='갱신 실패').wait_for()
        self.assertEqual(page.locator('[data-status-alert-query]').first.inner_text(),stamp)
        self.assertEqual(dashboard.locator('.status-alert-card').count(),2)
        page.unroute('**/api/admin/status-alerts')
        held=[]
        page.route(f'**/api/admin/status-alerts/{first}',lambda route:held.append(route))
        page.evaluate(f'void StatusAlerts.open({first})')
        for _ in range(50):
            if held: break
            page.wait_for_timeout(20)
        self.assertTrue(held)
        page.evaluate(f'StatusAlerts.open({second})')
        modal=page.locator('#status-alert-detail')
        modal.filter(has_text='주의 70점 → 위험 20점').wait_for()
        held[0].continue_(); page.wait_for_timeout(100)
        self.assertIn('주의 70점 → 위험 20점',modal.inner_text())
        page.route(f'**/api/admin/status-alerts/{second}/read',lambda route:route.fulfill(status=500,content_type='application/json',body=json.dumps({'success':False,'message':'읽음 실패'})))
        modal.get_by_role('button',name='읽음 처리',exact=True).click()
        modal.filter(has_text='읽음 실패').wait_for()
        db.session.expire_all(); self.assertFalse(db.session.get(StatusAlert,second).is_read)
        page.unroute(f'**/api/admin/status-alerts/{second}/read')
        reads=[]
        page.route(f'**/api/admin/status-alerts/{second}/read',lambda route:reads.append(route))
        modal.get_by_role('button',name='읽음 처리',exact=True).click()
        for _ in range(50):
            if reads: break
            page.wait_for_timeout(20)
        self.assertEqual(len(reads),1)
        self.assertTrue(modal.get_by_role('button',name='읽음 처리',exact=True).is_disabled())
        reads[0].continue_()
        modal.locator('p').filter(has_text='읽음 '+str(dt.datetime.now().year)).wait_for()
        self.assertFalse(self.errors)

    def test_stale_list_after_read_does_not_restore_unread_and_refresh_keeps_scroll(self):
        at=dt.datetime.now()-dt.timedelta(minutes=10)
        self.change('SAFE',90,0,at); alert_id=self.change(at=at+dt.timedelta(minutes=5))
        page=self.page; self.login()
        page.locator('[data-status-alert-dashboard] .status-alert-card').wait_for()
        page.locator('#tbody .main-row').wait_for(); page.locator('#tbody .main-row').first.click()
        for width in (390,1440):
            page.set_viewport_size({'width':width,'height':844})
            page.evaluate('window.scrollTo(0,700)'); page.wait_for_timeout(100)
            y=page.evaluate('scrollY')
            page.evaluate('StatusAlerts.refresh()'); page.wait_for_timeout(100)
            self.assertAlmostEqual(page.evaluate('scrollY'),y,delta=2)
        stale=page.request.get(self.url+'/api/admin/status-alerts').json()
        held=[]
        page.route('**/api/admin/status-alerts',lambda route:held.append(route))
        page.evaluate('void StatusAlerts.refresh()')
        for _ in range(50):
            if held: break
            page.wait_for_timeout(20)
        self.assertTrue(held)
        page.evaluate(f'StatusAlerts.open({alert_id})')
        modal=page.locator('#status-alert-detail')
        modal.get_by_role('button',name='읽음 처리',exact=True).click()
        modal.locator('p').filter(has_text='읽음 '+str(dt.datetime.now().year)).wait_for()
        held[0].fulfill(status=200,content_type='application/json',body=json.dumps(stale))
        page.unroute('**/api/admin/status-alerts')
        page.wait_for_timeout(300)
        self.assertEqual(page.locator('.nav [data-status-alert-count]').inner_text(),'0')
        self.assertEqual(page.locator('[data-status-alert-dashboard] .status-alert-card').count(),0)
        self.assertFalse(self.errors)


if __name__=='__main__': unittest.main()
