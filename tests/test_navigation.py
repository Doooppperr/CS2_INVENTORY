"""共享导航、二级入口、旧链接及登录跳转的页面契约。"""
import unittest

from bs4 import BeautifulSoup
from werkzeug.security import generate_password_hash

from cs2_inventory.database import db
from cs2_inventory.models import User
from tests import test_dashboard as fixtures


class NavigationTests(unittest.TestCase):
    setUp = fixtures.DashboardTests.setUp
    tearDown = fixtures.DashboardTests.tearDown
    add_target = fixtures.DashboardTests.add_target

    def soup(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.get_data(as_text=True), 'html.parser')

    def test_primary_navigation_is_identical_across_pages(self):
        for path in ('/app/dashboard', '/app?view=monitors', '/app?view=admin', '/app/monitors/1'):
            soup = self.soup(path)
            links = soup.select('.workspace-nav a')
            self.assertEqual([a.text for a in links], ['每日看板', '我的监控', '管理后台'])
            self.assertEqual([a['href'] for a in links], ['/app/dashboard', '/app?view=monitors', '/app?view=admin'])
            self.assertIn('hidden', links[2]['class'])
            self.assertIsNotNone(soup.select_one('#accountMenu #navLogout'))
            ids = [node['id'] for node in soup.select('[id]')]
            self.assertEqual(len(ids), len(set(ids)), path)

    def test_admin_tools_are_secondary_and_subscription_is_personal(self):
        soup = self.soup('/app?view=monitors')
        self.assertEqual([a.text for a in soup.select('#mainTabs a')], ['库存概览', '管理订阅'])
        self.assertEqual([a.text for a in soup.select('#adminNav a')], ['运行概况', '用户与续期', '全部监控', '实时查询', '查询历史'])
        self.assertIsNone(soup.select_one('#adminView #adminNav'))
        self.assertEqual(soup.select_one('#queryHistoryTab')['href'], '/app?view=queryHistory')

    def test_account_actions_do_not_mix_with_primary_navigation(self):
        for path in ('/app/dashboard', '/app?view=monitors'):
            soup = self.soup(path)
            self.assertIsNotNone(soup.select_one('#accountMenu #homeLink'))
            self.assertIsNotNone(soup.select_one('#accountMenu #navPassword'))
            self.assertIsNotNone(soup.select_one('#accountMenu #navSwitch'))
            self.assertEqual(soup.select_one('#switchAccountDialog #switchPassword')['type'], 'password')
            self.assertIsNone(soup.select_one('.workspace-nav button'))
            for name in ('selfOldPassword', 'selfNewPassword', 'selfConfirmPassword'):
                self.assertEqual(soup.select_one('#'+name)['type'], 'password')

    def test_legacy_urls_keep_serving_navigation(self):
        for path in ('/app', '/app/', '/app?view=subscriptions', '/app?view=query',
                     '/app?view=queryHistory&query_page=2', '/monitors/1?source=admin'):
            self.assertIsNotNone(self.soup(path).select_one('#workspaceNavigation'))
        with self.client.get('/static/inventory.js') as response:
            js = response.get_data(as_text=True)
        self.assertIn("location.replace(consoleUrl('dashboard'))", js)
        self.assertIn("location.hash!=='#admin'", js)
        self.assertIn("queryHistoryPage:positiveInt(params.get('query_page'))", js)
        self.assertIn("window.WorkspaceNavigation.destination(params.get('return_to'),basePath)", js)

    def test_landing_defaults_dashboard_and_validates_return_path(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn("url('app/dashboard')", html)
        self.assertIn('window.WorkspaceNavigation.destination(next,root)', html)
        self.assertNotIn("next&&next.startsWith('/')", html)

    def test_account_switch_reauthenticates_and_replaces_personal_scope(self):
        other = User(username='other_user', role='user', account_kind='internal', plan='permanent',
                     password_hash=generate_password_hash('fixture-password'))
        db.session.add(other)
        db.session.commit()
        token = self.client.get('/api/bootstrap').get_json()['csrf_token']
        headers = {'X-CSRF-Token': token}
        payload = {'username': other.username, 'password': 'wrong'}
        self.assertEqual(self.client.post('/api/auth/login', json=payload, headers=headers).status_code, 401)
        self.assertEqual(self.client.get('/api/monitors').get_json()['total'], 1)

        payload['password'] = 'fixture-password'
        self.assertEqual(self.client.post('/api/auth/login', json=payload).status_code, 400)
        result = self.client.post('/api/auth/login', json=payload, headers=headers)
        self.assertEqual(result.status_code, 200)
        self.assertNotEqual(result.get_json()['csrf_token'], token)
        self.assertEqual(self.client.get('/api/bootstrap').get_json()['user']['id'], other.id)
        monitors = self.client.get('/api/monitors')
        self.assertEqual(monitors.get_json()['total'], 0)
        self.assertTrue(monitors.cache_control.no_store)
        self.assertNotIn('Set-Cookie', monitors.headers)
        self.assertIn(self.client.get(f'/api/monitors/{self.target.id}').status_code, (403, 404))
        self.assertEqual(self.client.post('/api/auth/logout', headers=headers).status_code, 400)


if __name__ == '__main__':
    unittest.main()
