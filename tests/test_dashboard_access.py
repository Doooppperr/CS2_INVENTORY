"""每日看板：用户隔离、超级管理员全量范围与参数越权回归。"""
import unittest
from datetime import timedelta

from cs2_inventory.database import db
from cs2_inventory.models import Subscription, User
from tests import test_dashboard as fixtures


class DashboardAccessTests(unittest.TestCase):
    tearDown = fixtures.DashboardTests.tearDown
    add_target = fixtures.DashboardTests.add_target
    asset = fixtures.DashboardTests.asset
    snapshot = fixtures.DashboardTests.snapshot
    day_snapshot = fixtures.DashboardTests.day_snapshot
    endpoints = ('/api/dashboard', '/api/dashboard/overview')

    def setUp(self):
        fixtures.DashboardTests.setUp(self)
        self.other = User(username='other_owner', password_hash='fixture', role='user',
                          account_kind='customer', plan='permanent')
        self.manager = User(username='ordinary_admin', password_hash='fixture', role='admin',
                            account_kind='internal')
        db.session.add_all([self.other, self.manager])
        db.session.flush()
        self.foreign = self.add_target('76561198000000002', subscribe=False)
        db.session.add(Subscription(user_id=self.other.id, target_id=self.foreign.id, remark='他人专属备注'))
        db.session.add(Subscription(user_id=self.other.id, target_id=self.target.id, remark='他人的共享目标备注'))
        db.session.add(Subscription(user_id=self.manager.id, target_id=self.target.id, remark='普通管理员备注'))
        db.session.commit()
        for target, amount in ((self.target, 2), (self.foreign, 9)):
            self.day_snapshot([], self.today-timedelta(days=1), target)
            self.day_snapshot([str(i) for i in range(amount)], self.today, target)

    def login(self, user):
        with self.client.session_transaction() as session:
            session.clear()
            session['user_id'] = user.id

    def check_scope(self, target_ids, increase):
        for endpoint in self.endpoints:
            response = self.client.get(endpoint)
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response.headers['Cache-Control'])
            self.assertIn('Cookie', response.headers['Vary'])
            data = response.json
            if endpoint.endswith('overview'):
                self.assertEqual(data['summary']['total_accounts'], len(target_ids))
                self.assertEqual(data['summary']['increase'], increase if target_ids else None)
                self.assertTrue(all(p['total_accounts'] == len(target_ids) for p in data['trend']))
                items = data['rankings']['increase']
            else:
                self.assertEqual(data['summary']['targets'], len(target_ids))
                items = data['items']
                self.assertEqual(sum(item['increase'] for item in items), increase)
            self.assertEqual({a['target_id'] for item in items for a in item['accounts']}, set(target_ids))

    def test_users_only_own_targets_and_shared_target_own_remark(self):
        self.check_scope([self.target.id], 2)
        for endpoint in self.endpoints:
            body = self.client.get(endpoint).get_data(as_text=True)
            self.assertNotIn('他人专属备注', body)
            self.assertNotIn('他人的共享目标备注', body)
        self.login(self.other)
        self.check_scope([self.target.id, self.foreign.id], 11)

    def test_parameters_cannot_select_another_user_or_target(self):
        for endpoint in self.endpoints:
            self.assertEqual(self.client.get(endpoint+'?scope=all').status_code, 403)
            self.assertEqual(self.client.get(endpoint+'?scope=all&scope=mine').status_code, 403)
            for query in (f'user_id={self.other.id}', f'target_id={self.foreign.id}', 'role=admin', 'scope=mine&scope=all'):
                response = self.client.get(endpoint+'?'+query)
                self.assertEqual(response.status_code, 200)
                data = response.json
                items = data.get('items', data.get('rankings', {}).get('increase', []))
                self.assertEqual({a['target_id'] for item in items for a in item['accounts']}, {self.target.id})

    def test_super_admin_defaults_all_even_without_subscriptions(self):
        self.login(self.admin)
        self.check_scope([self.target.id, self.foreign.id], 11)
        self.assertEqual(self.client.get('/api/dashboard/overview').json['scope'], 'all')
        for endpoint in self.endpoints:
            data = self.client.get(endpoint+'?scope=mine').json
            self.assertEqual(data['summary'].get('targets', data['summary'].get('total_accounts')), 0)

    def test_ordinary_admin_does_not_inherit_global_scope(self):
        self.login(self.manager)
        self.check_scope([self.target.id], 2)
        for endpoint in self.endpoints:
            self.assertEqual(self.client.get(endpoint+'?scope=all').status_code, 403)
        self.assertEqual(self.client.get('/api/dashboard?section=pending').status_code, 403)

    def test_removing_subscription_immediately_removes_data(self):
        self.check_scope([self.target.id], 2)
        sub = Subscription.query.filter_by(user_id=self.user.id, target_id=self.target.id).one()
        db.session.delete(sub)
        db.session.commit()
        self.check_scope([], 0)

    def test_session_switch_does_not_reuse_other_users_results(self):
        self.login(self.admin)
        self.check_scope([self.target.id, self.foreign.id], 11)
        self.login(self.user)
        self.check_scope([self.target.id], 2)

    def test_anonymous_and_inactive_accounts_have_no_dashboard(self):
        self.user.is_active = False
        db.session.commit()
        for endpoint in self.endpoints:
            self.assertEqual(self.client.get(endpoint).status_code, 401)


if __name__ == '__main__':
    unittest.main()
