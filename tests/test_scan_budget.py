from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from cs2_inventory.app import create_app
from cs2_inventory.cli import enqueue_daily, scheduled_slot_key
from cs2_inventory.database import db
from cs2_inventory.models import (
    QuotaUsage,
    ScanBatch,
    ScanJob,
    SteamTarget,
    Subscription,
    User,
)
from cs2_inventory.services import quota_status, state_set
from cs2_inventory.worker import _process_work, claim_next_work, process_job


class ScanBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "STATE_DIR": Path(self.temp.name),
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "OBSERVATION_CACHE": str(Path(self.temp.name) / "observations.json"),
            "STEAMWEBAPI_KEY": "test-key",
        })
        self.context = self.app.app_context()
        self.context.push()
        self.target = SteamTarget(steamid="76561198000000000")
        db.session.add(self.target)
        db.session.flush()
        user = User.query.filter_by(username="cs2inventory_user").one()
        db.session.add(Subscription(user_id=user.id, target_id=self.target.id))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.temp.cleanup()

    def job(self, kind, **kwargs):
        job = ScanJob(target_id=self.target.id, steamid=self.target.steamid, kind=kind, **kwargs)
        db.session.add(job)
        db.session.commit()
        return job

    def test_worker_dispatches_light_job_to_single_request_path(self):
        job = self.job("daily_light")
        fake = {
            "steamid": self.target.steamid,
            "protected_live": [{"name": "新保护物品", "count": 1, "assetids": ["held-1"]}],
            "coverage": {"status": "partial"},
            "sources": {"steamwebapi:parse=1:mode=1": {"provider_requests": 1}},
        }
        with mock.patch("cs2_inventory.worker.run_api_inventory_query", return_value=fake) as light, \
                mock.patch("cs2_inventory.worker.fetch_persona_name", return_value="测试玩家"):
            work = claim_next_work()
            self.assertEqual(work, ("scan_group", [job.id]))
            _process_work(*work)
            light.assert_called_once()
            self.assertIn("on_progress", light.call_args.kwargs)
        self.assertEqual(job.status, "completed")
        self.assertEqual(QuotaUsage.query.one().credits, 3)
        self.assertTrue(self.target.snapshots[0].items[0].is_trade_protected)

    def test_mixed_queue_preserves_deep_jobs_and_does_not_steal_light_jobs(self):
        light = self.job("daily_light")
        for kind in ("initial", "manual", "instant", "daily"):
            deep = self.job(kind)
            self.assertEqual(claim_next_work(), ("scan", deep.id))
        self.assertEqual(claim_next_work(), ("scan_group", [light.id]))
        self.assertIsNone(claim_next_work())

    def test_process_job_defensively_routes_light_jobs(self):
        job = self.job("daily_light", status="running")
        with mock.patch("cs2_inventory.worker.process_batch_group") as group, \
                mock.patch("cs2_inventory.worker.run_api_inventory_query") as deep:
            process_job(job.id)
            group.assert_called_once_with([job.id])
            deep.assert_not_called()

    def test_light_job_rechecks_subscription_before_calling_provider(self):
        job = self.job("daily_light")
        work = claim_next_work()
        Subscription.query.delete()
        db.session.commit()
        with mock.patch("cs2_inventory.worker.run_api_inventory_query") as query:
            _process_work(*work)
            query.assert_not_called()
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(QuotaUsage.query.count(), 0)

    def test_schedule_never_creates_more_than_two_batches_per_beijing_day(self):
        start = datetime(2026, 10, 13, 16, tzinfo=timezone.utc)
        for minute in range(0, 1440, 15):
            enqueue_daily(now=start + timedelta(minutes=minute))
        self.assertEqual(ScanBatch.query.count(), 2)
        self.assertEqual({r.slot_key for r in ScanBatch.query.all()}, {"2026-10-14-R2", "2026-10-14-R5"})
        self.assertEqual({r.kind for r in ScanJob.query.all()}, {"daily", "daily_light"})

    def test_early_timer_catchup_skips_without_provider_call(self):
        now = datetime(2026, 10, 14, 23, 59, tzinfo=timezone.utc)  # 北京次日 07:59
        with mock.patch("cs2_inventory.cli.refresh_official_usage") as refresh:
            result = enqueue_daily(now=now)
        self.assertEqual(result["jobs"], 0)
        self.assertTrue(result["skipped"])
        refresh.assert_not_called()
        self.assertIsNone(scheduled_slot_key(now))

    def test_existing_r2_batch_survives_release_without_duplicate(self):
        old = ScanBatch(kind="daily", slot_key="2026-10-14-R2", total_jobs=1)
        db.session.add(old)
        db.session.commit()
        result = enqueue_daily(now=datetime(2026, 10, 14, 8, tzinfo=timezone.utc))
        self.assertTrue(result["existing"])
        self.assertEqual(result["batch_id"], old.id)

    def test_timer_and_report_match_two_round_schedule(self):
        timer = (Path(__file__).resolve().parents[1] / "deploy/cs2-inventory-schedule.timer").read_text()
        calendars = [line for line in timer.splitlines() if line.startswith("OnCalendar=")]
        self.assertEqual(calendars, ["OnCalendar=*-*-* 08:00:00 Asia/Shanghai", "OnCalendar=*-*-* 20:00:00 Asia/Shanghai"])
        status = quota_status()
        self.assertEqual(status["scheduled_scans_per_day"], 2)
        self.assertEqual(status["estimated_scheduled_credits_per_target_day"], 12)

    def test_renewed_subscription_excludes_previous_period_usage(self):
        state_set("official_quota_json", json.dumps({"available": True, "payload": {
            "package": "Inventory Small", "subscriptionstart": "2026-10-05T00:00:00+00:00",
            "subscriptionuntil": "2026-11-06T06:57:41+00:00",
            "month": 90000,  # 自然月不是账期用量，禁止替代本地账本。
        }}))
        db.session.add_all([
            QuotaUsage(credits=9000, used_at=datetime(2026, 10, 4, 23, 59, tzinfo=timezone.utc)),
            QuotaUsage(credits=1200, used_at=datetime(2026, 10, 5, tzinfo=timezone.utc)),
        ])
        db.session.commit()
        with mock.patch("cs2_inventory.services.utcnow", return_value=datetime(2026, 10, 14, tzinfo=timezone.utc)):
            status = quota_status()
        self.assertEqual(status["billing_used"], 1200)
        self.assertEqual(status["billing_remaining"], 148800)
        self.assertEqual(status["billing_period_source"], "provider_subscription")
        self.assertEqual(status["billing_period_start"], "2026-10-05T08:00:00+08:00")

    def test_invalid_or_expired_provider_period_uses_explicit_fallback(self):
        for payload in (None, [], {"subscriptionstart": "bad"},
                        {"subscriptionstart": "2026-11-05T00:00:00Z"},
                        {"subscriptionstart": "2026-08-25T00:00:00Z", "subscriptionuntil": "2026-10-05T00:00:00Z"}):
            with self.subTest(payload=payload):
                state_set("official_quota_json", json.dumps({"available": True, "payload": payload}))
                db.session.commit()
                with mock.patch("cs2_inventory.services.utcnow", return_value=datetime(2026, 10, 14, tzinfo=timezone.utc)):
                    status = quota_status()
                self.assertEqual(status["billing_period_source"], "legacy_25th_estimate")
                self.assertEqual(status["billing_period_start"], "2026-09-25T08:00:00+08:00")

    def test_zero_provider_attempts_are_not_charged_as_seven_requests(self):
        from cs2_inventory.worker import estimate_inventory_credits
        self.assertEqual(estimate_inventory_credits({"sources": {"single": {"provider_requests": 0}}}), 0)
        self.assertEqual(estimate_inventory_credits({"sources": {"legacy": {"requests": 7}}}), 6)

    def test_daily_usage_rolls_over_at_beijing_midnight(self):
        db.session.add_all([
            QuotaUsage(credits=21, used_at=datetime(2026, 10, 14, 15, 59, tzinfo=timezone.utc)),
            QuotaUsage(credits=3, used_at=datetime(2026, 10, 14, 16, 0, tzinfo=timezone.utc)),
        ])
        db.session.commit()
        with mock.patch("cs2_inventory.services.utcnow", return_value=datetime(2026, 10, 14, 16, 1, tzinfo=timezone.utc)):
            self.assertEqual(quota_status()["daily_used"], 3)

    def test_group_limit_claims_each_job_only_once(self):
        ids = [self.job("daily_light").id for _ in range(21)]
        self.assertEqual(claim_next_work(), ("scan_group", ids[:20]))
        self.assertEqual(claim_next_work(), ("scan_group", ids[20:]))
        self.assertIsNone(claim_next_work())

    def test_admin_template_exposes_estimate_period_and_schedule(self):
        html = self.app.test_client().get("/app").get_data(as_text=True)
        self.assertIn("q.billing_period_start", html)
        self.assertIn("q.billing_period_source", html)
        self.assertIn("q.scheduled_scan_hours_beijing", html)
        self.assertIn("q.estimated_scheduled_credits_per_target_day", html)
