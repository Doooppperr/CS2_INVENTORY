"""可视化看板的周期聚合、缺口、权限与真实持有量。"""
import json
import unittest
from datetime import timedelta
from unittest import mock

from cs2_inventory.dashboard_overview import overview_data
from cs2_inventory.database import db
from cs2_inventory.models import DailyObservation, QuotaUsage
from tests import test_dashboard as fixtures


class DashboardOverviewTests(unittest.TestCase):
    setUp = fixtures.DashboardTests.setUp
    tearDown = fixtures.DashboardTests.tearDown
    add_target = fixtures.DashboardTests.add_target
    asset = fixtures.DashboardTests.asset
    snapshot = fixtures.DashboardTests.snapshot
    day_snapshot = fixtures.DashboardTests.day_snapshot

    def overview(self, **args):
        return overview_data(self.user, {"date": self.day, **args})

    def test_empty_has_seven_gaps_not_zero(self):
        data = self.overview()
        self.assertEqual(len(data["trend"]), 7)
        self.assertIsNone(data["summary"]["increase"])
        self.assertTrue(all(p["increase"] is None for p in data["trend"]))
        self.assertEqual(data["rankings"], {"increase": [], "decrease": []})

    def test_baseline_only_is_not_growth(self):
        self.day_snapshot(["a", "b"], self.today)
        data = self.overview()
        self.assertEqual(data["summary"]["available_days"], 0)
        self.assertEqual(data["trend"][-1]["observed_accounts"], 1)

    def test_both_directions_and_latest_holdings(self):
        self.day_snapshot(["a"], self.today - timedelta(days=2))
        self.day_snapshot(["a", "b", "c"], self.today - timedelta(days=1))
        self.day_snapshot(["c"], self.today)
        data = self.overview(days="3")
        self.assertEqual(data["summary"]["increase"], 2)
        self.assertEqual(data["summary"]["decrease"], 2)
        self.assertEqual(data["summary"]["changed_accounts"], 1)
        self.assertEqual([p["increase"] for p in data["trend"]], [None, 2, 0])
        for direction in ("increase", "decrease"):
            row = data["rankings"][direction][0]
            self.assertEqual(row["count"], 2)
            self.assertEqual(row["account_count"], 1)
            self.assertEqual(row["accounts"][0]["holding"], 1)

    def test_same_account_is_not_counted_once_per_day(self):
        for days, keys in [(2, []), (1, ["a"]), (0, ["a", "b"])]:
            self.day_snapshot(keys, self.today - timedelta(days=days))
        item = self.overview()["rankings"]["increase"][0]
        self.assertEqual(item["count"], 2)
        self.assertEqual(item["account_count"], 1)
        self.assertEqual(len(item["accounts"]), 1)
        self.assertEqual(item["accounts"][0]["holding"], 2)

    def test_multiple_accounts_aggregate_and_keep_remarks(self):
        second = self.add_target("76561198000000002")
        for target in (self.target, second):
            self.day_snapshot([], self.today - timedelta(days=1), target)
            self.day_snapshot(["a"], self.today, target)
        item = self.overview()["rankings"]["increase"][0]
        self.assertEqual(item["count"], 2)
        self.assertEqual(item["account_count"], 2)
        self.assertEqual(item["accounts"][0]["label"], "我的备注")

    def test_missing_day_is_not_bridged(self):
        self.day_snapshot([], self.today - timedelta(days=2))
        self.day_snapshot(["a"], self.today)
        data = self.overview(days=3)
        self.assertTrue(all(point["increase"] is None for point in data["trend"]))

    def test_incomplete_and_cached_data_do_not_enter_rankings(self):
        self.day_snapshot([], self.today - timedelta(days=2))
        self.day_snapshot(["cached"], self.today - timedelta(days=1), observation="cached")
        self.day_snapshot(["partial"], self.today, status="partial")
        self.assertEqual(self.overview()["rankings"]["increase"], [])

    def test_latest_incomplete_holding_is_unknown(self):
        self.day_snapshot([], self.today - timedelta(days=2))
        self.day_snapshot(["a"], self.today - timedelta(days=1))
        self.day_snapshot([], self.today, status="partial")
        item = self.overview()["rankings"]["increase"][0]
        self.assertIsNone(item["accounts"][0]["holding"])

    def test_zero_change_is_known_zero(self):
        self.day_snapshot(["a"], self.today - timedelta(days=1))
        self.day_snapshot(["a"], self.today)
        self.assertEqual(self.overview()["trend"][-1]["increase"], 0)

    def test_top_five_order_and_search_does_not_filter_overview(self):
        self.day_snapshot([], self.today - timedelta(days=1))
        assets = [self.asset(f"a{i}", name=f"饰品{i}", market_name=f"AK-47 | Skin {i}", amount=i)
                  for i in range(1, 8)]
        self.day_snapshot(assets, self.today)
        items = self.overview(q="unmatched", page="999")["rankings"]["increase"]
        self.assertEqual([item["count"] for item in items], [7, 6, 5, 4, 3])

    def test_private_target_is_not_exposed(self):
        target = self.add_target("76561198000000002", subscribe=False)
        self.day_snapshot([], self.today - timedelta(days=1), target)
        self.day_snapshot(["a"], self.today, target)
        self.assertEqual(self.overview()["rankings"]["increase"], [])
        self.assertEqual(self.client.get('/api/dashboard/overview?scope=all').status_code, 403)
        data = overview_data(self.admin, {"scope": "all"})
        self.assertEqual(data["rankings"]["increase"][0]["count"], 1)

    def test_grace_period_filters_new_observations(self):
        self.day_snapshot([], self.today - timedelta(days=2))
        self.day_snapshot(["a"], self.today - timedelta(days=1))
        self.day_snapshot(["a", "b"], self.today)
        self.user.activation_expires_at = self.today - timedelta(hours=12)
        db.session.commit()
        with mock.patch('cs2_inventory.dashboard_overview.entitlement_state', return_value='grace'):
            data = self.overview()
        self.assertEqual(data["summary"]["increase"], 1)
        self.assertIsNone(data["trend"][-1]["increase"])
        self.assertEqual(data["rankings"]["increase"][0]["accounts"][0]["holding"], 1)

    def test_window_boundaries_exclude_older_events(self):
        self.day_snapshot([], self.today - timedelta(days=4))
        self.day_snapshot(["a"], self.today - timedelta(days=3))
        self.day_snapshot(["a", "b"], self.today - timedelta(days=2))
        self.assertEqual(self.overview(days=3)["summary"]["increase"], 1)
        self.assertEqual(self.overview(days=7)["summary"]["increase"], 2)

    def test_verified_false_report_is_never_ranked(self):
        self.day_snapshot([], self.today - timedelta(days=1))
        self.day_snapshot(["a"], self.today)
        row = db.session.get(DailyObservation, (self.target.id, self.day))
        report = json.loads(row.report_json)
        report["changes"][0]["verified"] = False
        row.report_json = json.dumps(report)
        db.session.commit()
        self.assertEqual(self.overview()["rankings"]["increase"], [])

    def test_api_validation_auth_and_no_external_requests(self):
        for query in ('days=14', 'days=oops', 'date=oops', 'date=2099-01-01', 'scope=unknown'):
            self.assertEqual(self.client.get('/api/dashboard/overview?' + query).status_code, 400)
        with mock.patch('urllib.request.urlopen', side_effect=AssertionError('不应访问外部 API')):
            response = self.client.get('/api/dashboard/overview')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertIn('Cookie', response.headers['Vary'])
        self.assertEqual(QuotaUsage.query.count(), 0)
        self.assertNotIn('assets_json', str(response.json))
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get('/api/dashboard/overview').status_code, 401)


if __name__ == '__main__':
    unittest.main()
