"""真实浏览器回归（不访问线上数据）：python tests/browser_inventory_scroll.py。

需安装 playwright 并准备 Chromium，或通过 --channel msedge 指定本机浏览器。
可通过 --root 指定待验证源码副本。
"""
import argparse
import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from flask import Flask, render_template
from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--channel', default=None)
parser.add_argument('--probe', choices=['original', 'restored'])
args, rest = parser.parse_known_args()
ROOT = args.root.resolve()


class InventoryScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel=args.channel)
        app = Flask(__name__, template_folder=str(ROOT / 'src/cs2_inventory/templates'),
                    static_folder=str(ROOT / 'src/cs2_inventory/static'))
        app.add_url_rule('/dashboard', endpoint='dashboard_page', view_func=lambda: '')
        app.config['INVENTORY_CREDITS_PER_REQUEST'] = 3
        with app.test_request_context('/'):
            cls.html = render_template('index.html', credits_per_request=3)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1280, 'height': 800})
        self.context.route('http://inventory.test/**', self.route)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.requests = []
        self.user_id = 7

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def route(self, route):
        url = urlsplit(route.request.url)
        path = url.path
        if path.startswith('/static/'):
            file = ROOT / 'src/cs2_inventory/static' / Path(path).name
            route.fulfill(path=str(file))
            return
        if not path.startswith('/api/'):
            route.fulfill(body=self.html, content_type='text/html')
            return
        self.requests.append(url.path + '?' + url.query)
        entitlement = {'status': 'internal', 'kind': 'internal', 'monitor_count': 90,
                       'monitor_limit': None, 'can_add_monitor': True}
        items = [{'id': n, 'steamid': str(76561198000000000 + n), 'label': f'测试玩家 {n}',
                  'persona_name': f'测试玩家 {n}', 'scan_status': 'ready', 'subscriber_count': 1,
                  'inventory_sections': {'protected': {'total_items': 5}, 'other': {'total_items': 8}},
                  'total_items': 13, 'item_types': 4} for n in range(1, 31)]
        if path == '/api/bootstrap':
            data = {'csrf_token': 'test', 'user': {'id': self.user_id, 'username': '测试账号',
                    'role': 'admin', 'can_query_inventory': True,
                    'can_view_inventory_diagnostics': False, 'entitlement': entitlement}}
        elif path in ['/api/monitors', '/api/admin/targets']:
            data = {'page': int(parse_qs(url.query).get('page', ['1'])[0]), 'pages': 3,
                    'total': 90, 'items': items, 'entitlement': entitlement}
        elif path.endswith('/compare'):
            data = {}
        elif path.startswith('/api/monitors/'):
            data = {'monitor': {'label': '测试玩家', 'scan_status': 'ready'}, 'total_items': 13,
                    'inventory_sections': {'protected': {'total_items': 5, 'items': []}}}
        else:
            data = {}
        route.fulfill(json=data)

    def open_list(self, view='monitors', mobile=False):
        if mobile:
            self.page.set_viewport_size({'width': 390, 'height': 844})
        query = 'view=admin&section=targets&target_page=2' if view == 'admin' else f'view={view}&page=2&q=test'
        self.page.goto('http://inventory.test/app?' + query)
        selector = '.target-open' if view == 'admin' else '.card-open'
        expect(self.page.locator(selector)).to_have_count(30)
        return selector

    def enter_detail(self, selector):
        button = self.page.locator(selector).nth(20)
        button.scroll_into_view_if_needed()
        self.page.wait_for_timeout(100)
        y = self.page.evaluate('window.scrollY')
        self.assertGreater(y, 400)
        button.click()
        expect(self.page.locator('#detailTitle')).to_have_text('测试玩家')
        return y

    def assert_restored(self, selector, y, view):
        expect(self.page.locator(selector)).to_have_count(30)
        self.page.wait_for_function('(y)=>Math.abs(window.scrollY-y)<=2', arg=y, timeout=2500)
        self.assertIn(f'view={view}', self.page.url)
        if view != 'admin':
            expect(self.page.locator('#monitorSearch')).to_have_value('test')
            expect(self.page.locator('#pageLabel')).to_have_text('2 / 3')
            self.assertTrue(any('/api/monitors?page=2&q=test' in request for request in self.requests))
        else:
            expect(self.page.locator('#targetPageLabel')).to_have_text('2 / 3')

    def roundtrip(self, view='monitors', mobile=False, native=False, reload_detail=False):
        selector = self.open_list(view, mobile)
        y = self.enter_detail(selector)
        if reload_detail:
            self.page.reload()
            expect(self.page.locator('#detailTitle')).to_have_text('测试玩家')
        if native:
            self.page.go_back()
        else:
            self.page.locator('#backDetail').click()
        self.assert_restored(selector, y, view)
        print(json.dumps({'view': view, 'mobile': mobile, 'native': native,
                          'before': y, 'after': self.page.evaluate('window.scrollY')}, ensure_ascii=False))

    def test_monitor_button(self):
        self.roundtrip()

    def test_monitor_native(self):
        self.roundtrip(native=True)

    def test_subscription_button(self):
        self.roundtrip(view='subscriptions')

    def test_admin_button(self):
        self.roundtrip(view='admin')

    def test_admin_native(self):
        self.roundtrip(view='admin', native=True)

    def test_mobile_monitor(self):
        self.roundtrip(mobile=True)

    def test_mobile_subscription(self):
        self.roundtrip(view='subscriptions', mobile=True)

    def test_mobile_admin(self):
        self.roundtrip(view='admin', mobile=True)

    def test_detail_refresh(self):
        self.roundtrip(reload_detail=True)

    def test_repeated_back_forward(self):
        selector = self.open_list()
        y = self.enter_detail(selector)
        self.page.go_back()
        self.assert_restored(selector, y, 'monitors')
        self.page.go_forward()
        expect(self.page.locator('#detailTitle')).to_have_text('测试玩家')
        self.page.go_back()
        self.assert_restored(selector, y, 'monitors')

    def test_fresh_list_stays_at_top(self):
        selector = self.open_list()
        self.enter_detail(selector)
        self.page.goto('http://inventory.test/app?view=monitors&page=1')
        expect(self.page.locator(selector)).to_have_count(30)
        self.assertEqual(self.page.evaluate('window.scrollY'), 0)

    def test_storage_disabled_native_back(self):
        self.context.add_init_script("Object.defineProperty(window,'sessionStorage',{get(){throw new Error('disabled')}})")
        self.roundtrip(native=True)

    def test_storage_disabled_button_fallback(self):
        self.context.add_init_script("Object.defineProperty(window,'sessionStorage',{get(){throw new Error('disabled')}})")
        selector = self.open_list()
        self.enter_detail(selector)
        self.page.locator('#backDetail').click()
        expect(self.page.locator(selector)).to_have_count(30)
        expect(self.page.locator('#pageLabel')).to_have_text('2 / 3')

    def test_direct_detail_fallback(self):
        self.page.goto('http://inventory.test/app/monitors/1?source=admin&section=targets&target_page=2&return=missing')
        expect(self.page.locator('#detailTitle')).to_have_text('测试玩家')
        self.page.locator('#backDetail').click()
        expect(self.page.locator('.target-open')).to_have_count(30)
        expect(self.page.locator('#targetPageLabel')).to_have_text('2 / 3')

    def test_return_records_are_account_scoped(self):
        selector = self.open_list()
        self.enter_detail(selector)
        self.user_id = 8
        self.page.reload()
        expect(self.page.locator('#detailTitle')).to_have_text('测试玩家')
        self.page.locator('#backDetail').click()
        expect(self.page.locator(selector)).to_have_count(30)
        self.assertNotIn('restore=', self.page.url)
        self.assertEqual(self.page.evaluate('window.scrollY'), 0)

    def test_slow_list_waits_for_render(self):
        selector = self.open_list()
        y = self.enter_detail(selector)
        self.context.add_init_script("""const originalFetch=window.fetch;window.fetch=async(...args)=>{
          if(String(args[0]).includes('api/monitors?'))await new Promise(r=>setTimeout(r,700));
          return originalFetch(...args);};""")
        self.page.locator('#backDetail').click()
        self.assert_restored(selector, y, 'monitors')

    def test_user_scroll_cancels_pending_restore(self):
        selector = self.open_list()
        self.enter_detail(selector)
        self.context.add_init_script("""const originalFetch=window.fetch;window.fetch=async(...args)=>{
          if(String(args[0]).includes('api/monitors?'))await new Promise(r=>setTimeout(r,700));
          return originalFetch(...args);};""")
        self.page.locator('#backDetail').click()
        expect(self.page.locator('#app')).to_be_visible()
        self.page.mouse.wheel(0, 10)
        expect(self.page.locator(selector)).to_have_count(30)
        self.page.wait_for_timeout(150)
        self.assertLess(self.page.evaluate('window.scrollY'), 400)

    def probe_behavior(self):
        selector = self.open_list()
        before = self.enter_detail(selector)
        self.page.locator('#backDetail').click()
        expect(self.page.locator(selector)).to_have_count(30)
        self.page.wait_for_timeout(200)
        after = self.page.evaluate('window.scrollY')
        print(f'SCROLL before={before} after={after} expected={args.probe}')
        self.assertEqual(after, 0 if args.probe == 'original' else before)


if __name__ == '__main__':
    selection = ['InventoryScrollTests.probe_behavior'] if args.probe else rest
    unittest.main(argv=[__file__, *selection], verbosity=2)
