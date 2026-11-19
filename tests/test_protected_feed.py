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

    def revision(self, user=None):
        response = self.client(user or self.user).get("/api/dashboard/scan-revision")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("Cookie", response.headers["Vary"])
        self.assertEqual(set(response.json), {"revision"})
        return response.json["revision"]

    def test_revision_changes_on_completed_job_or_new_snapshot_but_not_pending_job(self):
        original = self.revision()
        self.assertEqual(self.get("?date=recent")["revision"], original)
        self.snapshot(-6, protected("100"))
        snapshot_token = self.revision()
        self.assertNotEqual(snapshot_token, original)
        job = ScanJob(target_id=self.target.id, steamid=self.target.steamid, status="running")
        db.session.add(job)
        db.session.commit()
        self.assertEqual(self.revision(), snapshot_token)
        job.status, job.finished_at = "completed", NOW
        job.result_json = json.dumps({"protected_signal": {"scanned_at": NOW.astimezone(
            timezone(timedelta(hours=8))).isoformat(), "baseline_at": None, "items": []}})
        db.session.commit()
        done = self.revision()
        self.assertNotEqual(done, snapshot_token)
        self.assertEqual(done, self.get()["revision"])
        self.assertEqual(self.revision(), done)

    def test_revision_scope_entitlement_and_subscription_changes(self):
        route = "/api/dashboard/scan-revision"
        self.assertEqual(self.app.test_client().get(route).status_code, 401)
        self.assertEqual(self.client(self.user).get(route+"?scope=all").status_code, 403)
        self.assertEqual(self.client(self.other_admin).get(route+"?scope=all").status_code, 403)
        original = self.revision()
        foreign = self.revision(self.other_admin)
        self.snapshot(-4, protected("101"))
        self.assertEqual(self.revision(self.other_admin), foreign)
        sub = Subscription.query.filter_by(user_id=self.user.id, target_id=self.target.id).one()
        before_rename = self.revision()
        sub.remark = "现在的备注"
        db.session.commit()
        self.assertNotEqual(self.revision(), before_rename)
        db.session.delete(sub)
        db.session.commit()
        self.assertNotEqual(self.revision(), original)
        self.assertEqual(self.revision(self.other_admin), foreign)

    def test_revision_freezes_at_grace_cutoff_and_skips_asset_payloads(self):
        from sqlalchemy import event

        self.user.activation_expires_at = NOW-timedelta(hours=8)
        db.session.commit()
        with patch("cs2_inventory.protected_feed.entitlement_state", return_value="grace"):
            first = self.revision()
            self.snapshot(-1, protected("100"))
            self.assertEqual(self.revision(), first)
            self.assertEqual(self.get()["revision"], first)

        sql = []
        def capture(conn, cursor, statement, params, context, many):
            sql.append(statement.lower())

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            self.revision()
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        self.assertFalse(any(key in " ".join(sql) for key in ("snapshot_items", "payload_gzip", "scan_jobs.result_json")))
        with patch("cs2_inventory.protected_feed.entitlement_state", return_value="expired"):
            self.assertEqual(self.client(self.user).get("/api/dashboard/scan-revision").status_code,403)

    def test_missing_baseline_and_zero_are_distinct(self):
        self.assertEqual(self.get()["summary"]["observed_accounts"], 0)
        self.assertIsNone(self.get()["summary"]["added_units"])
        self.snapshot(-8, protected())
        self.assertEqual(self.get()["summary"]["baseline_accounts"], 1)
        self.assertIsNone(self.get()["summary"]["added_units"])
        self.snapshot(-1, protected())
        self.assertEqual(self.get()["summary"]["added_units"], 0)

    def test_target_choices_survive_missing_baseline_zero_and_additions(self):
        tid = self.target.id
        sub = Subscription.query.filter_by(user_id=self.user.id, target_id=tid).one()
        sub.remark = "自选目标甲"
        db.session.commit()

        def choice():
            return next(row for row in self.get()["targets"] if row["target_id"] == tid)

        self.assertEqual(choice(), {
            "target_id": tid, "label": "自选目标甲", "scanned_at": None, "baseline_at": None,
        })
        self.snapshot(-8, protected("101"))
        self.assertIsNotNone(choice()["scanned_at"])
        self.assertIsNone(choice()["baseline_at"])
        self.snapshot(-4, protected("101"))
        zero = choice()
        self.assertIsNotNone(zero["baseline_at"])
        self.assertEqual(self.get()["items"], [])
        self.snapshot(-1, protected("101") + protected("102"))
        self.assertEqual(choice()["baseline_at"], zero["scanned_at"])
        self.assertEqual(self.get()["items"][0]["accounts"][0]["count"], 1)

    def test_target_choices_follow_scope_without_leaking_other_users_remarks(self):
        other = SteamTarget(steamid="76561198000012345", persona_name="其他范围的账号")
        db.session.add(other)
        db.session.flush()
        db.session.add(Subscription(user_id=self.other_admin.id, target_id=other.id, remark="私人备注乙"))
        db.session.commit()
        mine = self.get("?slot=14")["targets"]
        self.assertIn(self.target.id, {row["target_id"] for row in mine})
        self.assertNotIn(other.id, {row["target_id"] for row in mine})
        other_user = self.get("?slot=14", user=self.other_admin)["targets"]
        self.assertEqual([(a["target_id"], a["label"]) for a in other_user], [(other.id, "私人备注乙")])
        all_targets = self.get("?scope=all", user=self.admin)["targets"]
        self.assertEqual(next(a["label"] for a in all_targets if a["target_id"] == other.id), "其他范围的账号")

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

    def test_search_exposes_market_name_alongside_localized_name(self):
        self.snapshot(-8, protected("100"))
        self.snapshot(-1, protected("100")+protected("103"))
        item=self.get()["items"][0]
        self.assertEqual(item["name"], "测试皮肤")
        self.assertEqual(item["market_name"], "AK-47 | Test")
        self.assertNotIn("key", item)

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

    def test_recent_day_accepts_zero_and_ignores_older_targets(self):
        a_id = self.target.id
        self.snapshot(-46, protected("101"))
        self.job(-26, "R5", protected("102"))
        # 另一个目标今天有有效的零新增结果；不把甲昨天的新增加进来。
        self.target = SteamTarget(steamid="76561198000000002", persona_name="乙")
        db.session.add(self.target)
        db.session.flush()
        db.session.add(Subscription(user_id=self.user.id, target_id=self.target.id))
        db.session.commit()
        self.snapshot(-10, protected("103"))
        self.job(-1, "R3", protected("103"))
        payload = self.get("?date=recent")
        self.assertEqual(payload["date"], "2026-11-19")
        self.assertEqual(payload["summary"]["added_units"], 0)
        self.assertEqual(payload["summary"]["observed_accounts"], 1)
        self.assertIsNone(next(a for a in payload["targets"] if a["target_id"] == a_id)["scanned_at"])

    def test_recent_day_handles_no_data_first_record_and_mixed_storage(self):
        self.assertEqual(self.get("?date=recent")["date"], "2026-11-19")
        self.job(-25, "R5", protected("101"))
        # 只有首次记录也应选中前一天。
        first = self.get("?date=recent")
        self.assertEqual(first["date"], "2026-11-18")
        self.assertIsNone(first["summary"]["added_units"])
        self.snapshot(-1, protected("105"))
        data = self.get("?date=recent")
        self.assertEqual(data["date"], "2026-11-19")
        self.assertEqual(data["summary"]["added_units"], 1)
        self.assertEqual(self.get("?date=2026-11-18")["date"], "2026-11-18")

    def test_recent_day_uses_visible_retained_results_and_grace_cutoff(self):
        self.job(-26, "R5", protected("100"))
        self.job(-3, "R2", protected("101"))
        self.assertEqual(self.get("?date=recent",user=self.other_admin)["summary"]["observed_accounts"],0)
        self.user.activation_expires_at = NOW - timedelta(hours=15)
        db.session.commit()
        with patch("cs2_inventory.protected_feed.entitlement_state", return_value="grace"):
            visible = self.get("?date=recent")
            self.assertEqual(visible["date"], "2026-11-18")
            self.assertEqual(visible["summary"]["observed_accounts"], 1)
        with patch("cs2_inventory.protected_feed.utcnow", return_value=NOW+timedelta(days=9)):
            payload = self.get("?date=recent")
            self.assertEqual(payload["date"], "2026-11-28")
            self.assertEqual(payload["summary"]["observed_accounts"], 0)
        self.assertEqual(self.client(self.user).get(ROUTE+"?date=recent&slot=07").status_code, 400)

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
