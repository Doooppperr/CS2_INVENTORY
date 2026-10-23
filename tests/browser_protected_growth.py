"""保护新增卡片的桌面、移动端及明暗主题布局回归。

需安装 playwright 并准备 Chromium，或通过 --channel msedge 指定本机浏览器。
可通过 --root 指定待验证源码副本。
"""
import argparse
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from flask import Flask, render_template
from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--channel', default=None)
args, rest = parser.parse_known_args()
ROOT = args.root.resolve()


class ProtectedGrowthBrowserTests(unittest.TestCase):
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
                  'inventory_sections': {'protected': {'total_items': 28, 'updated_at': '2026-10-23T13:44:11+08:00'}, 'other': {'total_items': 122, 'updated_at': '2026-10-23T13:44:11+08:00'}},
                  'protected_additions': ({'7': 12, '1': 3} if n == 1 else {'7': 0, '1': None}),
                  'total_items': 150, 'item_types': 48, 'last_complete_snapshot_at': '2026-10-23T13:44:11+08:00'} for n in range(1, 31)]
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

    def test_growth_layout(self):
        for width, theme in [(1440, 'dark'), (1440, 'light'), (390, 'dark'), (320, 'light')]:
            with self.subTest(width=width, theme=theme):
                self.page.set_viewport_size({'width': width, 'height': 960})
                self.page.goto('http://inventory.test/app')
                expect(self.page.locator('.inventory-card')).to_have_count(30)
                self.page.locator('#app [data-theme-selector]').select_option(theme)
                card = self.page.locator('.inventory-card').first
                expect(card.locator('.protection-growth')).to_have_text('七日内新增+121日内新增+3')
                expect(self.page.locator('.inventory-card').nth(1).locator('.protection-growth')).to_have_text('七日内新增+01日内新增—')
                geometry = card.locator('.protection-count').evaluate("""el => {
                    const bounds=el.getBoundingClientRect(), main=el.querySelector('strong').getBoundingClientRect(), growth=el.querySelector('.protection-growth').getBoundingClientRect();
                    return {overflow:el.scrollWidth>el.clientWidth, overlap:main.right>growth.left, inside:growth.right<=bounds.right};
                }""")
                self.assertEqual(geometry, {'overflow': False, 'overlap': False, 'inside': True})
                card.screenshot(path=str(ROOT.parent / f'preview-{width}-{theme}.png'))
                self.assertEqual(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'), False)


if __name__ == '__main__':
    unittest.main(argv=[__file__, *rest], verbosity=2)
