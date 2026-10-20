from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from cs2_inventory.app import create_app
from cs2_inventory.dashboard import dashboard_data, record_daily_job, save_daily
from cs2_inventory.database import db
from cs2_inventory.inventory_engine import (
    asset_records_from_parsed_payload,
    asset_records_from_public_payload,
    classify_hidden_assets,
    fetch_public_inventory,
    group_records_by_name,
)
from cs2_inventory.models import (
    BEIJING_TIMEZONE,
    DailyObservation,
    QuotaUsage,
    ScanBatch,
    ScanJob,
    Snapshot,
    SteamTarget,
    Subscription,
    TargetAssetHistory,
    User,
    utcnow,
)
from cs2_inventory.network import steam_urlopen
from cs2_inventory.observation import private_errors, scan_quality, signal_kind
from cs2_inventory.reporting_backfill import backfill
from cs2_inventory.services import (
    latest_snapshot,
    prune_expired,
    snapshot_diff,
    store_snapshot,
)
from cs2_inventory.unified import unify_inventory
from cs2_inventory.worker import process_batch_group, process_job


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "STATE_DIR": Path(self.temp.name),
            "SQLALCHEMY_DATABASE_URI": "sqlite://", "SECRET_KEY": "dashboard-test",
            "OBSERVATION_CACHE": str(Path(self.temp.name) / "cache.json"), "STEAMWEBAPI_KEY": "fixture"})
        self.context = self.app.app_context()
        self.context.push()
        self.user = User.query.filter_by(username="cs2inventory_user").one()
        self.admin = User.query.filter_by(username="cs2inventory_admin").one()
        self.target = self.add_target("76561198000000001")
        today = utcnow().astimezone(BEIJING_TIMEZONE).date()
        self.today = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
        self.day = today.isoformat()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.user.id

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.temp.cleanup()

    def add_target(self, steamid, subscribe=True):
        target = SteamTarget(steamid=steamid, persona_name="测试账号", profile_updated_at=utcnow())
        db.session.add(target)
        db.session.flush()
        if subscribe:
            db.session.add(Subscription(user_id=self.user.id, target_id=target.id, remark="我的备注"))
        db.session.commit()
        return target

    def asset(self, key, **kwargs):
        return {"asset_key": key, "name": "AK-47 | 红线（久经沙场）", "market_name": "AK-47 | Redline (Field-Tested)",
                "raw_name": "AK-47 | Redline (Field-Tested)", "item_type": "CSGO_Type_Rifle", "amount": 1,
                "classid": "100", "instanceid": "0", "name_localized": True, "sources": ["steam_public_contextid2"],
                "observation": "live", **kwargs}

    def snapshot(self, keys, when, target=None, status="ready", **kwargs):
        target = target or self.target
        assets = [self.asset(k, **kwargs) if isinstance(k, str) else k for k in keys]
        unified = {"_assets": assets, "items": [], "total_items": sum(a["amount"] for a in assets),
                   "item_types": len({a["name"] for a in assets}), "coverage": "complete" if status == "ready" else status,
                   "_scan_status": status, "elapsed_ms": 1, "errors": []}
        with mock.patch("cs2_inventory.services.utcnow", return_value=when):
            snapshot = store_snapshot(target, unified)
        db.session.commit()
        return snapshot

    def day_snapshot(self, keys, when, target=None, status="ready", **kwargs):
        target = target or self.target
        snapshot = self.snapshot(keys, when, target, status, **kwargs)
        save_daily(target, day=when.astimezone(BEIJING_TIMEZONE).date().isoformat(), scanned_at=when,
                   status=status, snapshot=snapshot)
        db.session.commit()
        return snapshot

    def data(self, **kwargs):
        return dashboard_data(self.admin if kwargs.get("section") == "pending" else self.user, {"date": self.day, **({"scope": "all"} if kwargs.get("section") == "pending" else {}), **kwargs})

    def test_first_snapshot_is_baseline_only(self):
        snapshot = self.day_snapshot(["1"], self.today)
        self.assertEqual(snapshot_diff(snapshot, None)["added"], [])
        self.assertEqual(self.data()["summary"]["baseline_only"], 1)
        self.assertEqual(self.data()["status"], "no_comparable")

    def test_reappearance_not_new_even_after_pruning(self):
        self.snapshot(["1"], self.today - timedelta(days=10))
        baseline = self.snapshot([], self.today - timedelta(days=1))
        prune_expired()
        current = self.snapshot(["1"], self.today)
        diff = snapshot_diff(current, baseline)
        self.assertEqual(diff["added"], [])
        self.assertEqual(diff["reappeared"][0]["count"], 1)
        self.assertEqual(TargetAssetHistory.query.count(), 1)

    def test_evening_first_seen_counts_in_next_morning_interval(self):
        self.day_snapshot(["1"], self.today - timedelta(days=1))
        self.snapshot(["1", "2"], self.today - timedelta(hours=12), is_trade_protected=True)
        self.day_snapshot(["1", "2"], self.today)
        self.assertEqual(self.data()["items"][0]["increase"], 1)

    def test_asset_replacement_same_count_not_increase(self):
        self.day_snapshot(["1"], self.today - timedelta(days=1))
        self.day_snapshot(["2"], self.today)
        self.assertEqual(self.data()["items"], [])

    def test_returns_are_subtracted_from_net_increase(self):
        self.snapshot(["1", "old"], self.today - timedelta(days=2))
        self.day_snapshot(["1"], self.today - timedelta(days=1))
        self.day_snapshot(["1", "old", "new"], self.today)
        self.assertEqual(self.data()["items"][0]["increase"], 1)
        self.assertEqual(self.data(section="reappeared")["items"][0]["accounts"][0]["reappeared"], 1)

    def test_private_does_not_overwrite_inventory(self):
        before = self.snapshot(["1"], self.today - timedelta(days=1))
        current = self.snapshot([], self.today, status="private")
        self.assertIsNone(current)
        self.assertEqual(self.target.scan_status, "private")
        self.assertEqual(self.target.last_error, "已私密")
        self.assertEqual(latest_snapshot(self.target.id).id, before.id)
        self.assertEqual(Snapshot.query.count(), 1)

    def test_private_error_requires_explicit_provider_code(self):
        for error in ['HTTP 403', 'steamwebapi:x: HTTP 403: {"error":"FORBIDDEN"}', '官方公开库存: PRIVATE',
                      'steamwebapi:x: HTTP 403: {"name":"PRIVATE"}']:
            self.assertFalse(private_errors([error]))
        self.assertTrue(private_errors(['steamwebapi:x: HTTP 403: {"error":"PRIVATE"}']))
        self.assertTrue(private_errors(['steamwebapi:parse=1:mode=1: PRIVATE']))

    def test_partial_never_confirms_removals(self):
        before = self.day_snapshot(["1"], self.today - timedelta(days=1))
        after = self.day_snapshot([], self.today, status="partial")
        diff = snapshot_diff(after, before)
        self.assertEqual(diff["removed"], [])
        self.assertEqual(diff["unconfirmed"][0]["count"], 1)
        self.assertEqual(self.data()["items"], [])
        self.assertEqual(len(self.data(section="pending")["items"]), 1)

    def test_cached_assets_do_not_create_new_signal_or_history(self):
        self.day_snapshot([], self.today - timedelta(days=1))
        self.day_snapshot(["cached"], self.today, observation="cached")
        self.assertIsNone(db.session.get(TargetAssetHistory, (self.target.id, "cached")))
        self.assertEqual(self.data()["items"], [])
        self.assertEqual(self.data(section="pending")["items"][0]["accounts"][0]["first_seen"], 0)

    def test_synthetic_assets_never_enter_history_or_ranking(self):
        self.day_snapshot([], self.today - timedelta(days=1))
        self.day_snapshot(["synthetic:x:1"], self.today)
        self.assertEqual(TargetAssetHistory.query.count(), 0)
        self.assertEqual(self.data()["items"], [])

    def test_medals_excluded_but_locked_skin_included(self):
        self.day_snapshot([], self.today - timedelta(days=1))
        self.day_snapshot([self.asset("medal", item_type="CSGO_Type_Collectible", market_name="2021 Service Medal"),
                           self.asset("skin", is_trade_protected=True)], self.today)
        self.assertEqual(len(self.data()["items"]), 1)
        self.assertEqual(self.data()["items"][0]["increase"], 1)
        self.assertEqual(signal_kind("Skin", ""), "unknown")
        self.assertEqual(signal_kind("Case", "CSGO_Type_WeaponCase"), "eligible")
        self.assertEqual(signal_kind("Gloves", "CSGO_Type_Hands"), "eligible")

    def test_translation_and_unlock_do_not_change_inventory(self):
        before = self.day_snapshot(["1"], self.today - timedelta(days=1), is_trade_protected=True)
        after = self.day_snapshot(["1"], self.today, name="AK-47 | Redline (Field-Tested)", is_trade_protected=False)
        self.assertEqual(snapshot_diff(after, before)["added"], [])
        self.assertEqual(self.data()["items"], [])

    def test_unknown_group_translation_does_not_create_pending_change(self):
        self.day_snapshot(["1"], self.today - timedelta(days=1), market_name="", item_type="")
        self.day_snapshot(["1"], self.today, market_name="", item_type="", name="English display name")
        self.assertEqual(self.data(section="pending")["items"], [])

    def test_exact_wear_and_stattrak_are_separate(self):
        self.day_snapshot([], self.today - timedelta(days=1))
        self.day_snapshot([self.asset("1"), self.asset("2", market_name="AK-47 | Redline (Minimal Wear)"),
                           self.asset("3", market_name="StatTrak™ AK-47 | Redline (Field-Tested)")], self.today)
        self.assertEqual(len(self.data()["items"]), 3)

    def test_peer_single_filters_and_pagination(self):
        second = self.add_target("76561198000000002")
        for target in (self.target, second):
            self.day_snapshot([], self.today - timedelta(days=1), target)
        self.day_snapshot(["1", "2", "3"], self.today)
        self.day_snapshot(["4"], self.today, second)
        self.assertEqual(self.data(peers="1")["items"][0]["account_count"], 2)
        self.assertEqual(self.data(peers="1", min_per_account="2")["items"], [])
        self.assertEqual(len(self.data(peers="1", single="1", min_accounts="3", match="any")["items"]), 1)
        self.assertEqual(self.data(peers="1", single="1", min_accounts="3", match="all")["items"], [])
        self.assertEqual(self.data(single="1", min_single="4")["items"], [])
        self.assertEqual(self.data(page="2", page_size="1")["items"], [])

    def test_missing_yesterday_does_not_fall_back(self):
        self.day_snapshot([], self.today - timedelta(days=2))
        self.day_snapshot(["1"], self.today)
        self.assertEqual(self.data()["items"], [])
        self.assertEqual(self.data()["summary"]["baseline_only"], 1)

    def test_failed_empty_and_confirmed_empty_differ(self):
        before = self.day_snapshot(["1"], self.today - timedelta(days=1))
        self.snapshot([], self.today, status="failed")
        self.assertEqual(latest_snapshot(self.target.id).id, before.id)
        self.day_snapshot([], self.today)
        self.assertEqual(self.data()["items"][0]["decrease"], 1)

    def test_private_and_failed_worker_keep_credits(self):
        for kind in ("daily", "daily_light"):
            job = ScanJob(target_id=self.target.id, steamid=self.target.steamid, kind=kind, status="running")
            db.session.add(job)
            db.session.commit()
            result = {"coverage": {"status": "failed"}, "errors": ['steamwebapi:x: HTTP 403: {"error":"PRIVATE"}'],
                      "sources": {"steamwebapi:parse=1:mode=1": {"provider_requests": 1}}}
            path = "run_api_inventory_query"
            with mock.patch("cs2_inventory.worker." + path, return_value=result):
                process_job(job.id) if kind == "daily" else process_batch_group([job.id])
            self.assertEqual(job.status, "failed")
            self.assertEqual(self.target.scan_status, "private")
        self.assertEqual(QuotaUsage.query.count(), 2)
        self.assertEqual(Snapshot.query.count(), 0)

    def test_storage_failure_keeps_consumed_credits(self):
        job = ScanJob(target_id=self.target.id, steamid=self.target.steamid, kind="manual", status="running")
        db.session.add(job)
        db.session.commit()
        result = {"coverage": {"status": "complete"}, "sources": {"steamwebapi:x": {"provider_requests": 1}}}
        with mock.patch("cs2_inventory.worker.run_api_inventory_query", return_value=result), mock.patch(
            "cs2_inventory.worker.store_snapshot", side_effect=RuntimeError("fixture")):
            process_job(job.id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(QuotaUsage.query.count(), 1)

    def test_only_r2_daily_jobs_generate_reports(self):
        snapshot = self.snapshot(["1"], self.today)
        batch = ScanBatch(kind="daily", slot_key=self.day + "-R2")
        db.session.add(batch)
        db.session.flush()
        job = ScanJob(target_id=self.target.id, steamid=self.target.steamid, kind="daily_light", batch_id=batch.id)
        record_daily_job(job, self.target, snapshot)
        self.assertEqual(DailyObservation.query.count(), 0)
        job.kind = "daily"
        record_daily_job(job, self.target, snapshot)
        record_daily_job(job, self.target, snapshot)
        db.session.commit()
        self.assertEqual(DailyObservation.query.count(), 1)

    def test_daily_report_survives_snapshot_deletion(self):
        self.day_snapshot([], self.today - timedelta(days=1))
        self.day_snapshot(["1"], self.today)
        before = self.data()
        for snapshot in Snapshot.query.all():
            db.session.delete(snapshot)
        db.session.commit()
        self.assertEqual(self.data(), before)

    def test_target_delete_cascades_and_shared_subscription_dedupes(self):
        db.session.add(Subscription(user_id=self.admin.id, target_id=self.target.id))
        self.day_snapshot([], self.today - timedelta(days=1))
        self.day_snapshot(["1"], self.today)
        self.assertEqual(self.data()["summary"]["targets"], 1)
        db.session.delete(self.target)
        db.session.commit()
        self.assertEqual(TargetAssetHistory.query.count(), 0)
        self.assertEqual(DailyObservation.query.count(), 0)

    def test_dashboard_permissions_and_no_store(self):
        other = self.add_target("76561198000000003", subscribe=False)
        self.day_snapshot([], self.today - timedelta(days=1), other)
        self.day_snapshot(["secret"], self.today, other)
        response = self.client.get("/api/dashboard?date=" + self.day)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["items"], [])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(self.client.get("/api/dashboard?scope=all").status_code, 403)
        with self.client.session_transaction() as session:
            session["user_id"] = self.admin.id
        self.assertEqual(self.client.get("/api/dashboard?scope=all").get_json()["items"][0]["increase"], 1)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get("/api/dashboard").status_code, 401)

    def test_frozen_account_does_not_see_later_reports(self):
        self.day_snapshot([], self.today - timedelta(days=1))
        self.day_snapshot(["secret"], self.today)
        self.user.account_kind = "customer"
        self.user.plan = "monthly"
        self.user.activation_expires_at = self.today - timedelta(hours=1)
        db.session.commit()
        with mock.patch("cs2_inventory.entitlements.utcnow", return_value=self.today + timedelta(hours=1)):
            self.assertEqual(self.data()["items"], [])
        self.user.activation_expires_at = self.today - timedelta(days=40)
        db.session.commit()
        with self.assertRaises(PermissionError):
            self.data()

    def test_generating_and_input_validation(self):
        batch = ScanBatch(slot_key=self.day + "-R2", kind="daily")
        db.session.add(batch)
        db.session.flush()
        db.session.add(ScanJob(target_id=self.target.id, steamid=self.target.steamid, kind="daily", batch_id=batch.id))
        db.session.commit()
        self.assertEqual(self.data()["status"], "generating")
        for query in ('date=garbage', 'min_accounts=0', 'min_single=nan', 'page_size=101', 'match=invalid', 'section=secret'):
            self.assertEqual(self.client.get('/api/dashboard?' + query).status_code, 400)

    def test_template_and_navigation_render(self):
        response = self.client.get('/app/dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertIn('每日库存看板', response.get_data(as_text=True))
        self.assertIn('/app/dashboard', self.client.get('/app').get_data(as_text=True))

    def test_backfill_is_idempotent_preserves_snapshots(self):
        snapshot = self.snapshot(["1"], self.today - timedelta(hours=1))
        TargetAssetHistory.query.delete()
        db.session.commit()
        before = Snapshot.query.count()
        self.assertEqual(backfill()["assets"], 1)
        self.assertEqual(backfill()["assets"], 0)
        self.assertEqual(Snapshot.query.count(), before)
        self.assertIsNotNone(db.session.get(Snapshot, snapshot.id))

    def test_eight_day_report_retention_not_identity_retention(self):
        self.day_snapshot(["1"], self.today - timedelta(days=8))
        self.day_snapshot(["2"], self.today - timedelta(days=7))
        prune_expired()
        self.assertEqual(DailyObservation.query.count(), 1)
        self.assertEqual(TargetAssetHistory.query.count(), 2)

    def test_type_and_market_metadata_survive_protection_classification(self):
        records = asset_records_from_parsed_payload([{"assetid": "99", "classid": "1", "instanceid": "0",
            "markethashname": "AK-47 | Redline (Field-Tested)", "type": "Rifle", "tradeprotected": True,
            "cached": True}], source="steamwebapi:parse=1:mode=1")
        protected, _, _ = classify_hidden_assets(records, [], records)
        unified = unify_inventory({"protected_live": group_records_by_name(protected)})
        asset = unified["_assets"][0]
        self.assertEqual(asset["market_name"], "AK-47 | Redline (Field-Tested)")
        self.assertEqual(asset["observation"], "cached")
        self.assertEqual(asset["item_type"], "Rifle")

    def test_official_public_records_are_live(self):
        rows = asset_records_from_public_payload({"assets": [{"assetid": "1", "classid": "100", "instanceid": "0"}],
            "descriptions": [{"classid": "100", "instanceid": "0", "market_hash_name": "Skin", "name": "皮肤",
                              "tags": [{"category": "Type", "internal_name": "CSGO_Type_Rifle"}]}]}, source="steam_public_contextid2")
        self.assertEqual(rows[0].observation, "live")
        self.assertEqual(rows[0].market_name, "Skin")

    def test_incomplete_official_pagination_is_error(self):
        with mock.patch('cs2_inventory.inventory_engine.http_get_json', return_value={"success": 1, "more_items": 1, "last_assetid": "1"}):
            with self.assertRaisesRegex(Exception, "分页未完成"):
                fetch_public_inventory(self.target.steamid, max_pages=1)

    def test_no_items_and_unknown_freshness_not_complete(self):
        self.assertEqual(scan_quality({"coverage": {"status": "failed"}, "errors": ['steamwebapi:x: HTTP 411: {"error":"NO_ITEMS"}']}), 'failed')
        self.assertEqual(scan_quality({"coverage": {"status": "complete"}, "quality": {"comparable": False}}), 'partial')

    def test_proxy_only_routes_steam_and_redacts_credentials(self):
        with mock.patch.dict("os.environ", {"CS2_STEAM_PROXY_URL": "http://user:secret@127.0.0.1:17890"}), mock.patch(
            "urllib.request.build_opener") as build, mock.patch("urllib.request.urlopen") as direct:
            steam_urlopen("https://www.steamwebapi.com/steam/api/inventory", timeout=1)
            direct.assert_called_once()
            build.assert_not_called()
            steam_urlopen("https://steamcommunity.com/inventory/fixture", timeout=1)
            build.return_value.open.assert_called_once()
            build.return_value.open.side_effect = OSError("secret")
            with self.assertRaisesRegex(OSError, "专用代理请求失败") as error:
                steam_urlopen("https://steamcommunity.com/", timeout=1)
            self.assertNotIn("secret", str(error.exception))

    def test_no_proxy_keeps_original_transport(self):
        with mock.patch.dict("os.environ", {"CS2_STEAM_PROXY_URL": ""}), mock.patch("urllib.request.urlopen") as direct:
            steam_urlopen("https://steamcommunity.com/", timeout=1)
            direct.assert_called_once()

    def test_partial_flag_overrides_complete_label(self):
        unified = unify_inventory({"coverage": {"status": "complete"}, "quality": {"comparable": False}})
        snapshot = store_snapshot(self.target, unified)
        self.assertEqual(snapshot.coverage, "partial")


    def test_diagnostics_repair_is_idempotent_and_never_promotes_old_data(self):
        import gzip
        import json

        from cs2_inventory.reporting_backfill import repair_diagnostics
        snapshot = self.day_snapshot(["1"], self.today, status="partial")
        snapshot.errors_json = json.dumps(["官方公开库存: 网络请求失败：[Errno 101] Network is unreachable"])
        snapshot.payload_gzip = gzip.compress(b"{}")
        row = db.session.get(DailyObservation, (self.target.id, self.day))
        report = json.loads(row.report_json)
        report.pop("quality", None)
        row.report_json = json.dumps(report)
        db.session.commit()
        before = row.assets_json
        self.assertEqual(repair_diagnostics()["diagnostics_updated"], 1)
        self.assertEqual(repair_diagnostics()["diagnostics_updated"], 0)
        data = self.data()
        self.assertEqual(row.status, "partial")
        self.assertEqual(row.assets_json, before)
        self.assertEqual(data["summary"]["comparable"], 0)
        self.assertIn("steam_network", data["account_statuses"][0]["reason_codes"])
        self.assertIn("legacy_evidence", data["account_statuses"][0]["reason_codes"])

    def test_quality_details_follow_same_target_permissions(self):
        other = self.add_target("76561198000000007", subscribe=False)
        self.day_snapshot(["secret"], self.today, other, status="partial")
        data = self.data()
        self.assertNotIn(other.id, [r["target_id"] for r in data["account_statuses"]])
        self.assertEqual(data["quality_summary"], [{"code": "missing", "message": "该日没有早间深度扫描记录", "accounts": 1}])

    def test_pending_and_missing_are_not_double_counted(self):
        batch = ScanBatch(slot_key=self.day + "-R2", kind="daily")
        db.session.add(batch)
        db.session.flush()
        db.session.add(ScanJob(target_id=self.target.id, steamid=self.target.steamid, kind="daily", batch_id=batch.id))
        db.session.commit()
        data = self.data()
        self.assertEqual(data["summary"]["pending"], 1)
        self.assertEqual(data["summary"]["missing"], 0)
        self.assertEqual(data["account_statuses"][0]["reason_codes"], ["pending"])

    def test_dashboard_uses_shared_theme_and_quality_controls(self):
        html = self.client.get('/app/dashboard').get_data(as_text=True)
        self.assertIn('cs2-inventory-theme', html)
        self.assertIn(':root[data-theme="dark"]', html)
        self.assertIn('data-theme-selector', html)
        self.assertIn('id="accountStatuses"', html)
        self.assertIn('id="resetFilters"', html)
        self.assertIn('/static/dashboard.css', html)
        self.assertLess(html.index('cs2-inventory-theme'), html.index('<style>'))


if __name__ == '__main__':
    unittest.main()
