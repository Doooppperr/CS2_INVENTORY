"""以真实分区夹具验证逐轮加仓口径、可见范围和按班次回看。"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from cs2_inventory.database import db
from cs2_inventory.models import ScanBatch, ScanJob, Snapshot, SteamTarget, Subscription
from cs2_inventory.protected_feed import scan_signal
from cs2_inventory.services import prune_expired, store_snapshot
from cs2_inventory.unified import unify_inventory
from cs2_inventory.worker import process_job
from tests import test_api_inventory as fixtures
from tests.test_api_inventory import ordinary, protected

NOW = datetime(2026, 11, 19, 14, tzinfo=timezone.utc)
ROUTE = "/api/dashboard/protected-additions"


class ProtectedFeedTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiInventoryTests.setUp(self)
        clock = patch("cs2_inventory.protected_feed.utcnow", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.round = 10

    tearDown = fixtures.ApiInventoryTests.tearDown
    query = fixtures.ApiInventoryTests.query
    client = fixtures.ApiInventoryTests.client

    def snapshot(self, hour, ids, *, others=(), round_id=None, policy=None):
        result, _ = self.query(first=ids, second=ordinary(others))
        self.round += 1
        result["quality"]["scan_round"] = str(round_id or self.round)
        if policy:
            result["quality"]["source_policy"] = policy
        with patch(
            "cs2_inventory.services.utcnow", return_value=NOW + timedelta(hours=hour)
        ):
            row = store_snapshot(self.target, unify_inventory(result))
            db.session.commit()
        return row, result["quality"]

    def get(self, args="", user=None):
        response = self.client(user or self.user).get(ROUTE + args)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIn("no-store", response.headers["Cache-Control"])
        return response.json

    def test_missing_baseline_and_zero_are_distinct(self):
        self.assertEqual(self.get()["summary"]["observed_accounts"], 0)
        self.assertIsNone(self.get()["summary"]["added_units"])
        self.snapshot(-8, protected())
        self.assertEqual(self.get()["summary"]["baseline_accounts"], 1)
        self.assertIsNone(self.get()["summary"]["added_units"])
        self.snapshot(-1, protected())
        self.assertEqual(self.get()["summary"]["added_units"], 0)

    def test_arrivals_are_not_net_holdings_or_status_changes(self):
        self.snapshot(-20, protected("110"))  # 曾出现的资产 110 再次回来
        self.snapshot(
            -8, protected("100", amount=2) + protected("101"), others=("102",)
        )
        self.snapshot(
            -1,
            protected("100", amount=4, name="新翻译")
            + protected("102")
            + protected("103")
            + protected("110"),
        )
        data = self.get()
        # 100 增加 2 个单位；103 新来 1 个；101 卖出不冲抵；
        # 102 普通变保护、110 重现均不计。
        self.assertEqual(data["summary"]["added_units"], 3)
        self.assertEqual(len(data["items"]), 1)
        account = data["items"][0]["accounts"][0]
        self.assertEqual((account["target_id"], account["count"]), (self.target.id, 3))
        self.assertNotIn("key", data["items"][0])

    def test_progress_rows_from_one_scan_never_form_a_comparison(self):
        self.snapshot(-15, protected("100"), round_id=111)
        self.snapshot(-8, protected("101"), round_id=112)
        row, quality = self.snapshot(-7, protected("101"), round_id=112)
        self.assertEqual(self.get()["summary"]["added_units"], 1)
        self.assertEqual(scan_signal(row, quality)["items"][0]["count"], 1)
        self.assertTrue(
            scan_signal(row, quality)["baseline_at"].endswith("07:00:00+08:00")
        )

    def test_other_partition_failure_does_not_hide_a_confirmed_protected_arrival(self):
        self.snapshot(-8, protected("100"))
        result, _ = self.query(
            first=protected("101"),
            second=fixtures.engine.SteamQueryError("HTTP 403: fixture"),
        )
        with patch(
            "cs2_inventory.services.utcnow", return_value=NOW - timedelta(minutes=10)
        ):
            store_snapshot(self.target, unify_inventory(result))
            db.session.commit()
        self.assertEqual(self.get()["summary"]["added_units"], 1)

    def test_history_compaction_and_source_change_do_not_create_signals(self):
        self.snapshot(-51, protected("100"))
        self.snapshot(-50, protected("101"))
        self.assertIsNone(self.get()["summary"]["added_units"])
        self.snapshot(-3, protected("102"), policy="api_only_v1")
        self.snapshot(-1, protected("103"))
        self.assertIsNone(self.get()["summary"]["added_units"])

    def job(self, hour, slot, first):
        row, quality = self.snapshot(hour, first)
        batch = ScanBatch(slot_key="2026-11-19-" + slot)
        db.session.add(batch)
        db.session.flush()
        job = ScanJob(
            target_id=self.target.id,
            steamid=self.target.steamid,
            batch_id=batch.id,
            status="completed",
            finished_at=NOW + timedelta(hours=hour),
            result_json=json.dumps({"protected_signal": scan_signal(row, quality)}),
        )
        db.session.add(job)
        db.session.commit()

    def test_historical_slots_survive_snapshot_pruning_and_return_real_times(self):
        self.snapshot(-24, protected("100"))
        self.job(-15, "R2", protected("101"))
        self.job(-8, "R3", protected("101") + protected("102", amount=4))
        self.job(-1, "R5", protected("101") + protected("102", amount=4))
        for row in Snapshot.query.all():
            db.session.delete(row)
        db.session.commit()
        for slot, count in (("07", 1), ("14", 4), ("21", 0), ("latest", 0)):
            payload = self.get("?date=2026-11-19&slot=" + slot)
            self.assertEqual(payload["summary"]["added_units"], count)
        self.assertEqual(
            self.get("?slot=07")["updated_at"], "2026-11-19T07:00:00+08:00"
        )
        self.assertIsNone(
            self.get("?date=2026-11-18&slot=14")["summary"]["added_units"]
        )

    def test_regular_admin_and_grace_access(self):
        self.snapshot(-24, protected("100"))
        self.job(-15, "R2", protected("101"))
        self.job(-8, "R3", protected("103", amount=5))
        self.assertEqual(
            self.get(user=self.other_admin)["summary"]["observed_accounts"], 0
        )
        for user in (self.user, self.other_admin):
            self.assertEqual(
                self.client(user).get(ROUTE + "?scope=all").status_code, 403
            )
        self.user.activation_expires_at = NOW - timedelta(hours=10)
        db.session.commit()
        with patch(
            "cs2_inventory.protected_feed.entitlement_state", return_value="grace"
        ):
            self.assertEqual(self.get()["summary"]["added_units"], 1)
            self.assertIsNone(self.get("?slot=14")["summary"]["added_units"])
        with patch(
            "cs2_inventory.protected_feed.entitlement_state", return_value="expired"
        ):
            self.assertEqual(self.client(self.user).get(ROUTE).status_code, 403)

    def test_filter_rejects_unknown_dates_and_slots(self):
        for query in ("?date=bad", "?date=2026-10-31", "?date=2026-11-20", "?slot=24"):
            self.assertEqual(self.client(self.user).get(ROUTE + query).status_code, 400)

    def test_aggregation_keeps_account_counts_labels_and_order(self):
        self.snapshot(-6, [])
        self.snapshot(-1, protected("105", amount=2))
        first_target_id = self.target.id
        self.target = SteamTarget(steamid="76561198000000002", persona_name="乙")
        db.session.add(self.target)
        db.session.flush()
        db.session.add(
            Subscription(
                user_id=self.user.id, target_id=self.target.id, remark="另一个监控"
            )
        )
        db.session.commit()
        self.snapshot(-8, [])
        self.snapshot(-3, protected("106", amount=3))
        data = self.get()
        self.assertEqual(data["summary"]["added_units"], 5)
        self.assertEqual(data["items"][0]["account_count"], 2)
        self.assertEqual(
            [a["target_id"] for a in data["items"][0]["accounts"]],
            [self.target.id, first_target_id],
        )
        self.assertEqual(data["items"][0]["accounts"][0]["label"], "另一个监控")

    def test_baseline_with_unobserved_ordinary_inventory_is_not_comparable(self):
        result, _ = self.query(
            first=protected("100"),
            second=fixtures.engine.SteamQueryError("HTTP 403: fixture"),
        )
        with patch(
            "cs2_inventory.services.utcnow", return_value=NOW - timedelta(hours=14)
        ):
            store_snapshot(self.target, unify_inventory(result))
            db.session.commit()
        self.job(-8, "R3", protected("109"))
        for args in ("", "?slot=14"):
            self.assertIsNone(self.get(args)["summary"]["added_units"])

    def test_failed_slot_does_not_replace_the_timestamp_of_the_latest_result(self):
        self.snapshot(-15, protected("100"))
        self.job(-8, "R3", protected("106"))
        batch = ScanBatch(slot_key="2026-11-19-R5")
        db.session.add(batch)
        db.session.flush()
        failed = ScanJob(
            target_id=self.target.id,
            steamid=self.target.steamid,
            batch_id=batch.id,
            kind="daily_light",
            status="failed",
            finished_at=NOW - timedelta(hours=1),
        )
        db.session.add(failed)
        db.session.commit()
        self.assertIsNone(self.get("?slot=21")["summary"]["added_units"])
        latest = self.get("?date=2026-11-19")
        self.assertEqual(latest["updated_at"], "2026-11-19T14:00:00+08:00")
        self.assertEqual(latest["summary"]["added_units"], 1)

    def test_cleanup_removes_only_the_expired_summary_at_beijing_midnight(self):
        self.snapshot(-8, protected("100"))
        result = {"protected_signal": {"items": []}, "diagnostics": {"credits": 12}}
        oldest = datetime(2026, 11, 11, 16, tzinfo=timezone.utc)
        jobs = [
            ScanJob(
                target_id=self.target.id,
                steamid=self.target.steamid,
                status="completed",
                finished_at=oldest + timedelta(seconds=offset),
                result_json=json.dumps(result),
            )
            for offset in (-1, 0)
        ]
        db.session.add_all(jobs)
        db.session.commit()
        with patch("cs2_inventory.services.utcnow", return_value=NOW):
            prune_expired()
        self.assertEqual(
            json.loads(jobs[0].result_json), {"diagnostics": {"credits": 12}}
        )
        self.assertEqual(json.loads(jobs[1].result_json), result)

    def test_worker_writes_slot_report_after_real_progress_callback(self):
        self.snapshot(-16, protected("100"))
        batch = ScanBatch(slot_key="2026-11-19-R5")
        db.session.add(batch)
        db.session.flush()
        job = ScanJob(
            target_id=self.target.id,
            steamid=self.target.steamid,
            batch_id=batch.id,
            kind="daily_light",
            status="running",
        )
        db.session.add(job)
        db.session.commit()
        result, _ = self.query(first=protected("103"))
        result["quality"]["scan_round"] = str(job.id)

        def scan(*args, on_progress, **kwargs):
            on_progress(result)
            return result

        with (
            patch("cs2_inventory.worker.run_api_inventory_query", side_effect=scan),
            patch("cs2_inventory.worker.profile_refresh_due", return_value=False),
            patch("cs2_inventory.worker.utcnow", return_value=NOW),
            patch("cs2_inventory.services.utcnow", return_value=NOW),
        ):
            process_job(job.id)
        self.assertEqual(job.status, "completed", job.error)
        self.assertEqual(self.get("?slot=21")["summary"]["added_units"], 1)


if __name__ == "__main__":
    unittest.main()
