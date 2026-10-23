"""保护卡片新增统计：资产正向增量、自然日基线和独立分区。"""
import unittest
from datetime import datetime, timedelta, timezone

from cs2_inventory.database import db
from cs2_inventory.inventory_views import inventory_view
from tests import test_api_inventory as fixtures
from tests.test_api_inventory import ordinary, protected


class ProtectedAdditionsTests(unittest.TestCase):
    setUp = fixtures.ApiInventoryTests.setUp
    tearDown = fixtures.ApiInventoryTests.tearDown
    query = fixtures.ApiInventoryTests.query
    save = fixtures.ApiInventoryTests.save
    client = fixtures.ApiInventoryTests.client

    def snapshot(self, days=0, ids=("101",), hour=2, second=None, **fields):
        rows = [item for key in ids for item in protected(key, **fields)]
        row = self.save(first=rows, second=second)
        # 2026-10-23 北京时间 10:00；测试不依赖实际执行日期。
        row.scanned_at = datetime(2026, 10, 23, hour, tzinfo=timezone.utc) - timedelta(days=days)
        db.session.commit()
        return row

    def additions(self):
        return inventory_view(self.target.id, self.user, include_items=False)["protected_additions"]

    def test_no_history_is_unknown_not_zero(self):
        self.assertEqual(self.additions(), {"7": None, "1": None})
        self.snapshot()
        self.assertEqual(self.additions(), {"7": None, "1": None})

    def test_new_assets_count_without_offsetting_removed_assets(self):
        self.snapshot(7, ("101", "102"))
        self.snapshot(1, ("101", "103"))
        self.snapshot(0, ("103", "104", "105"))
        self.assertEqual(self.additions(), {"7": 3, "1": 2})

    def test_ordinary_assets_do_not_count(self):
        self.snapshot(7)
        self.snapshot(1)
        self.snapshot(second=ordinary(("201", "202", "203")))
        self.assertEqual(self.additions(), {"7": 0, "1": 0})

    def test_quantity_increase_counts_units_and_rename_is_not_new(self):
        self.snapshot(7, amount=2)
        self.snapshot(1, amount=3)
        self.snapshot(amount=5, name="改名后的皮肤")
        self.assertEqual(self.additions(), {"7": 3, "1": 2})

    def test_decrease_and_empty_are_zero(self):
        self.snapshot(7, ("101", "102"))
        self.snapshot(1, ("101",))
        self.snapshot(0, ())
        self.assertEqual(self.additions(), {"7": 0, "1": 0})

    def test_missing_target_day_does_not_fall_back(self):
        self.snapshot(8)
        self.snapshot(2)
        self.snapshot(ids=("101", "102"))
        self.assertEqual(self.additions(), {"7": None, "1": None})

    def test_last_snapshot_on_target_day_wins(self):
        self.snapshot(1, ("101",), hour=1)
        self.snapshot(1, ("101", "102"), hour=12)
        self.snapshot(ids=("101", "102", "103"))
        self.assertEqual(self.additions(), {"7": None, "1": 1})

    def test_beijing_midnight_boundary(self):
        self.snapshot(2, ("101",), hour=15)  # 北京 21 日 23:00，不是 22 日
        self.snapshot(2, ("101", "102"), hour=16)  # 北京 22 日 00:00
        self.snapshot(ids=("101", "102", "103"))
        self.assertEqual(self.additions()["1"], 1)

    def test_other_partition_failure_does_not_block_protected_growth(self):
        self.snapshot(1)
        self.snapshot(ids=("101", "102"), second=fixtures.engine.SteamQueryError("HTTP 403: fixture failure"))
        self.assertEqual(self.additions()["1"], 1)

    def test_failed_protected_refresh_keeps_previous_growth(self):
        self.snapshot(1)
        self.snapshot(ids=("101", "102"))
        before = self.additions()
        failed = self.save(first=fixtures.engine.SteamQueryError("HTTP 403: fixture failure"))
        failed.scanned_at = datetime(2026, 10, 23, 3, tzinfo=timezone.utc)
        db.session.commit()
        self.assertEqual(self.additions(), before)

    def test_policy_change_is_not_a_baseline(self):
        from unittest.mock import patch

        from cs2_inventory.inventory_views import snapshot_quality
        old = self.snapshot(1)
        self.snapshot(ids=("101", "102"))

        def quality(row):
            value = snapshot_quality(row)
            return {**value, "source_policy": "api_only_v1"} if row.id == old.id else value

        with patch("cs2_inventory.inventory_views.snapshot_quality", side_effect=quality):
            self.assertIsNone(self.additions()["1"])

    def test_incomplete_protected_partition_is_unknown(self):
        from unittest.mock import patch

        from cs2_inventory.inventory_views import snapshot_quality
        self.snapshot(1)
        self.snapshot(ids=("101", "102"))

        def quality(row):
            value = snapshot_quality(row)
            contexts = value["api_contexts"]
            return {**value, "api_contexts": {**contexts,
                    "protected": {**contexts["protected"], "complete": False}}}

        with patch("cs2_inventory.inventory_views.snapshot_quality", side_effect=quality):
            self.assertEqual(self.additions(), {"7": None, "1": None})

    def test_grace_period_does_not_read_post_expiry_growth(self):
        from unittest.mock import patch

        self.snapshot(2)
        self.snapshot(1, ("101", "102"))
        self.snapshot(0, ("101", "102", "103"))
        self.user.activation_expires_at = datetime(2026, 10, 22, 3, tzinfo=timezone.utc)
        db.session.commit()
        with patch("cs2_inventory.entitlements.entitlement_state", return_value="grace"):
            self.assertEqual(self.additions(), {"7": None, "1": 1})

    def test_list_api_includes_counts_and_stays_subscription_scoped(self):
        self.snapshot(1)
        self.snapshot(ids=("101", "102"))
        response = self.client(self.user).get("/api/monitors")
        self.assertEqual(response.status_code, 200)
        item = next(item for item in response.json["items"] if item["id"] == self.target.id)
        self.assertEqual(item["protected_additions"], {"7": None, "1": 1})
        self.assertEqual(self.client(self.other_admin).get("/api/monitors").json["items"], [])


if __name__ == "__main__":
    unittest.main()
