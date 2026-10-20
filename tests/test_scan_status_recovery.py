from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from cs2_inventory import inventory_engine as engine
from cs2_inventory.app import create_app
from cs2_inventory.database import db
from cs2_inventory.models import (
    QuotaUsage,
    ScanJob,
    Snapshot,
    SteamTarget,
    Subscription,
    User,
    utcnow,
)
from cs2_inventory.observation import scan_status_details
from cs2_inventory.services import (
    monitor_public,
    snapshot_diff,
    store_snapshot,
    target_public,
)
from cs2_inventory.unified import unify_inventory
from cs2_inventory.worker import estimate_inventory_credits, process_batch_group


class ScanStatusRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app({
            "TESTING": True, "STATE_DIR": self.root,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(self.root / 'test.db').as_posix()}",
            "SECRET_KEY": "fixture", "STEAMWEBAPI_KEY": "fixture",
            "OBSERVATION_CACHE": str(self.root / "observations.json"),
        })
        self.public = {
            "assets": [{"assetid": "1", "classid": "100", "instanceid": "0"}],
            "descriptions": [{"classid": "100", "instanceid": "0", "name": "公开皮肤"}],
            "total_inventory_count": 2,
        }
        self.protected = [{"assetid": "2", "classid": "200", "markethashname": "保护皮肤",
                           "tradeprotected": True, "itemgroup": "rifle"}]
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.temp.cleanup()

    def response(self, headers, error=None):
        def fetch(*args, **kwargs):
            kwargs["request_observer"]()
            if error:
                raise error
            return self.protected, headers
        return fetch

    def query(self, headers=None, error=None):
        with mock.patch.object(engine, "fetch_public_inventory", return_value=self.public), mock.patch.object(
            engine, "http_get_json_with_headers", side_effect=self.response(headers or {}, error),
        ) as request, mock.patch.object(engine, "localize_asset_records", side_effect=lambda rows, **kw: rows):
            result = engine.run_lightweight_query(
                "76561198000000001", key="fixture", single_inventory=True,
                observation_cache_path=str(self.root / "observations.json"),
            )
        return result, request

    def target(self):
        target = SteamTarget(steamid="76561198000000001")
        db.session.add(target)
        db.session.flush()
        user = User.query.filter_by(role="admin").first()
        subscription = Subscription(user_id=user.id, target_id=target.id)
        db.session.add(subscription)
        db.session.commit()
        return target, user, subscription

    def test_strict_live_response_round_trip_is_ready(self):
        result, request = self.query({"steamwebapi-cache": "no"})
        params = request.call_args.args[1]
        self.assertEqual(params["try_first_seven_days_blocked_items"], "2")
        self.assertEqual(params["trade_locked"], "1")
        self.assertEqual(params["parse"], "1")
        self.assertEqual(params["no_cache"], "1")
        unified = unify_inventory(result)
        self.assertEqual(unified["_scan_status"], "ready")
        self.assertEqual(unified["total_items"], 2)
        self.assertTrue(unified["_assets"][0]["is_trade_protected"])
        self.assertEqual(result["quality"]["unknown_count"], 0)
        self.assertEqual(result["sources"]["steamwebapi:parse=1:mode=2"]["provider_requests"], 1)
        self.assertEqual(request.call_count, 1)

    def test_gap_fetches_live_partner_context2_once_and_counts_actual_requests(self):
        self.public["total_inventory_count"] = 3
        extra = {"assets": [{"appid": "730", "contextid": "2", "assetid": "3", "classid": "300", "instanceid": "0"}],
                 "descriptions": [{"classid": "300", "instanceid": "0", "name": "已解锁仍隐藏的皮肤"}],
                 "total_inventory_count": 1}

        def response(url, params, **kwargs):
            kwargs["request_observer"]()
            return (extra if params["parse"] == "0" else self.protected), {"steamwebapi-cache": "no"}

        with mock.patch.object(engine, "fetch_public_inventory", return_value=self.public), mock.patch.object(
            engine, "http_get_json_with_headers", side_effect=response,
        ) as request, mock.patch.object(engine, "localize_asset_records", side_effect=lambda rows, **kw: rows):
            result = engine.run_lightweight_query(
                "76561198000000001", key="fixture", single_inventory=True,
                observation_cache_path=str(self.root / "observations.json"),
            )
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.args[1]["trade_locked"], "0")
        self.assertEqual(request.call_args.args[1]["try_first_seven_days_blocked_items"], "2")
        self.assertEqual(unify_inventory(result)["_scan_status"], "ready")
        self.assertEqual(unify_inventory(result)["total_items"], 3)
        self.assertEqual(estimate_inventory_credits(result), 6)

    def test_explicit_context2_zero_is_sent_and_has_live_response_evidence(self):
        with mock.patch.object(engine, "http_get_json_with_headers", return_value=(self.public, {"steamwebapi-cache": "no"})) as request:
            fetched = engine.fetch_steamwebapi_raw_inventory("fixture", key="fixture", mode="2", parse="0", trade_locked="0")
        self.assertEqual(request.call_args.args[1]["trade_locked"], "0")
        self.assertIn("freshness=live", fetched.pages[0].source)

    def test_unchanged_unverified_asset_does_not_repeat_a_change_event(self):
        target, _, _ = self.target()
        now = utcnow()
        before = self.snapshot(target, ["1"], now - timedelta(days=3), status="partial", observation="unknown")
        after = self.snapshot(target, ["1"], now, status="partial", observation="unknown")
        diff = snapshot_diff(after, before)
        self.assertEqual(diff["pending"], [])
        self.assertEqual(diff["unconfirmed"], [])

    def test_cached_or_missing_header_does_not_become_ready(self):
        for headers, code in (({}, "freshness_unknown"), ({"steamwebapi-cache": "yes"}, "cached_items")):
            result, _ = self.query(headers)
            self.assertEqual(unify_inventory(result)["_scan_status"], "partial")
            self.assertIn(code, result["quality"]["reason_codes"])

    def test_strict_failure_keeps_partial_and_public_inventory(self):
        result, _ = self.query(error=engine.SteamQueryError('HTTP 451: {"error":"STEAM_ERROR"}'))
        unified = unify_inventory(result)
        self.assertEqual(unified["_scan_status"], "partial")
        self.assertEqual(unified["total_items"], 1)
        self.assertIn("provider_error", result["quality"]["reason_codes"])

    def test_no_items_only_passes_with_independent_matching_public_inventory(self):
        self.public["total_inventory_count"] = 1
        result, _ = self.query(error=engine.SteamQueryError('HTTP 411: {"error":"NO_ITEMS"}'))
        self.assertEqual(unify_inventory(result)["_scan_status"], "ready")
        self.assertEqual(result["quality"]["warnings"], ["provider_no_items"])
        self.public["total_inventory_count"] = 2
        result, _ = self.query(error=engine.SteamQueryError('HTTP 411: {"error":"NO_ITEMS"}'))
        self.assertEqual(unify_inventory(result)["_scan_status"], "partial")
        self.assertIn("count_shortfall", result["quality"]["reason_codes"])

    def test_historical_item_stays_unconfirmed_and_does_not_confirm_removal(self):
        target, _, _ = self.target()
        result, _ = self.query({"steamwebapi-cache": "no"})
        before = store_snapshot(target, unify_inventory(result))
        db.session.commit()
        self.protected = []
        self.public["total_inventory_count"] = 1
        result, _ = self.query({"steamwebapi-cache": "no"})
        self.assertIn("cached_items", result["quality"]["reason_codes"])
        current = store_snapshot(target, unify_inventory(result))
        db.session.commit()
        self.assertEqual(target.scan_status, "partial")
        self.assertEqual(target_public(target)["scan_label"], "含历史记录")
        self.assertFalse(snapshot_diff(current, before)["comparable"])
        self.assertEqual(snapshot_diff(current, before)["removed"], [])

    def test_reason_specific_status_is_not_a_completeness_upgrade(self):
        for code, label in (("freshness_unknown", "实时性待核验"), ("cached_items", "含历史记录"),
                            ("count_shortfall", "数量待核对"), ("steam_rate_limit", "部分来源异常")):
            result = scan_status_details("partial", {"comparable": False, "reason_codes": [code]})
            self.assertEqual(result["scan_label"], label)
            self.assertIn("不用于确认物品移除", result["scan_detail"])
        self.assertEqual(scan_status_details("private", {"reason_codes": ["cached_items"]})["scan_label"], "已私密")

    def test_admin_and_monitor_api_expose_diagnosis_without_upgrading_status(self):
        target, user, _ = self.target()
        result, _ = self.query({})
        store_snapshot(target, unify_inventory(result))
        db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user.id
        for endpoint in ("/api/admin/targets", "/api/monitors"):
            response = client.get(endpoint)
            self.assertEqual(response.status_code, 200)
            row = next(x for x in response.json["items"] if x["id"] == target.id)
            self.assertEqual(row["scan_status"], "partial")
            self.assertEqual(row["scan_label"], "实时性待核验")
            self.assertIn("不用于确认物品移除", row["scan_detail"])

    def test_worker_stores_strict_live_evidence_and_charges_one_request(self):
        target, _, subscription = self.target()
        job = ScanJob(target_id=target.id, steamid=target.steamid, kind="daily_light", status="running")
        db.session.add(job)
        db.session.commit()
        with mock.patch.object(engine, "fetch_public_inventory", return_value=self.public), mock.patch.object(
            engine, "http_get_json_with_headers", side_effect=lambda url, params, **kw: (kw["request_observer"]() or (self.protected if params["parse"] == "1" else {**self.public, "total_inventory_count": len(self.public["assets"])}), {"steamwebapi-cache": "no"}),
        ), mock.patch.object(engine, "localize_asset_records", side_effect=lambda rows, **kw: rows), mock.patch(
            "cs2_inventory.worker.fetch_persona_name", return_value="fixture",
        ), mock.patch("cs2_inventory.worker.RATE_LIMITER.acquire"):
            process_batch_group([job.id])
        self.assertEqual(job.status, "completed")
        self.assertEqual(target.scan_status, "ready")
        self.assertIsNone(target.last_error)
        snapshot = Snapshot.query.filter_by(target_id=target.id, coverage="complete").one()
        self.assertTrue(json.loads(gzip.decompress(snapshot.payload_gzip))["quality"]["comparable"])
        self.assertEqual(sum(row.credits for row in QuotaUsage.query.all()), 6)
        self.assertEqual(monitor_public(subscription, subscription.user)["scan_label"], "校验通过")

    def test_deep_normal_source_explicitly_reads_public_context(self):
        self.public["total_inventory_count"] = 1
        fetched = engine.SteamwebapiRawFetchResult(
            pages=[], upstream_item_counts=[], sources=[], total_inventory_counts=[], errors=[], request_attempts=1,
        )
        with mock.patch.object(engine, "fetch_public_inventory", return_value=self.public), mock.patch.object(
            engine, "fetch_steamwebapi_raw_inventory", return_value=fetched,
        ) as request, mock.patch.object(engine, "localize_asset_records", side_effect=lambda rows, **kw: rows):
            result = engine.run_max_coverage_query(
                "76561198000000001", key="fixture", trading_samples=0, normal_samples=1,
                include_mode1=False, include_parse1=False, observation_cache_path=str(self.root / "obs.json"),
            )
        self.assertEqual(request.call_args.kwargs["trade_locked"], "0")
        self.assertEqual(unify_inventory(result)["_scan_status"], "ready")

    def snapshot(self, target, keys, when, *, status="ready", observation="live"):
        assets = [{"asset_key": key, "name": "测试皮肤", "raw_name": "测试皮肤", "amount": 1,
                   "observation": observation, "classid": "100", "item_type": "rifle"} for key in keys]
        unified = {"_assets": assets, "items": [{"name": "测试皮肤", "count": len(keys)}] if keys else [],
                   "total_items": len(keys), "item_types": int(bool(keys)), "elapsed_ms": 1,
                   "coverage": "complete" if status == "ready" else "partial", "errors": [],
                   "_scan_status": status, "_quality": {"comparable": status == "ready",
                   "reason_codes": [] if status == "ready" else ["freshness_unknown"]}}
        with mock.patch("cs2_inventory.services.utcnow", return_value=when):
            snapshot = store_snapshot(target, unified)
            db.session.commit()
        return snapshot

    def test_complete_current_resolves_known_live_asset_from_partial_baseline(self):
        target, _, _ = self.target()
        now = utcnow()
        before = self.snapshot(target, ["1"], now - timedelta(days=3), status="partial")
        after = self.snapshot(target, [], now)
        diff = snapshot_diff(after, before)
        self.assertFalse(diff["comparable"])
        self.assertTrue(diff["removal_comparable"])
        self.assertEqual(diff["removed"], [{"name": "测试皮肤", "count": 1}])
        self.assertEqual(diff["unconfirmed"], [])

    def test_unverified_history_closes_instead_of_becoming_confirmed_removal(self):
        target, _, _ = self.target()
        now = utcnow()
        before = self.snapshot(target, ["1"], now - timedelta(days=3), status="partial", observation="unknown")
        after = self.snapshot(target, [], now)
        diff = snapshot_diff(after, before)
        self.assertEqual(diff["removed"], [])
        self.assertEqual(diff["unconfirmed"], [])
        self.assertEqual(diff["historical_closed"], [{"name": "测试皮肤", "count": 1}])

    def test_persistent_missing_retains_first_missing_date(self):
        target, _, _ = self.target()
        now = utcnow()
        before = self.snapshot(target, ["1"], now - timedelta(days=4))
        self.snapshot(target, [], now - timedelta(days=3), status="partial")
        after = self.snapshot(target, [], now, status="partial")
        row = snapshot_diff(after, before)["unconfirmed"][0]
        self.assertEqual(row["pending_days"], 3)
        self.assertIsNotNone(row["last_confirmed_at"])
        self.assertEqual(snapshot_diff(after, before)["removed"], [])

    def test_unknown_reobservations_do_not_refresh_real_history(self):
        target, _, _ = self.target()
        now = utcnow()
        before = self.snapshot(target, [], now - timedelta(days=5))
        self.snapshot(target, ["1"], now - timedelta(days=3), status="partial", observation="unknown")
        after = self.snapshot(target, ["1"], now, status="partial", observation="unknown")
        row = snapshot_diff(after, before)["pending"][0]
        self.assertEqual(row["pending_days"], 3)
        self.assertIsNone(row["last_confirmed_at"])
        self.assertEqual(target.asset_history, [])

    def test_unverified_provider_rows_do_not_extend_cache_expiry(self):
        now = 1_800_000_000
        cache = str(self.root / "expiry.json")
        row = engine.AssetRecord(assetid="1", classid="100", instanceid="0", name="测试皮肤", observation="unknown")
        engine._write_observation_cache(cache, {"fixture": {"1": {
            "name": "测试皮肤", "classid": "100", "instanceid": "0", "amount": 1,
            "first_seen": now, "last_seen": now, "last_verified_at": now, "expires_at": now + 10 * 86400,
        }}})
        for days in (0, 5, 9):
            engine.apply_observation_cache("fixture", [row], set(), cache_path=cache, now=now + days * 86400)
            stored = engine._read_observation_cache(cache)["fixture"]["1"]
            self.assertEqual(stored["expires_at"], now + 10 * 86400)
            self.assertEqual(stored["last_seen"], now)
        engine.apply_observation_cache("fixture", [row], set(), cache_path=cache, now=now + 11 * 86400)
        self.assertEqual(engine._read_observation_cache(cache)["fixture"], {})
        engine.apply_observation_cache("fixture", [row], set(), cache_path=cache, now=now + 12 * 86400)
        self.assertEqual(engine._read_observation_cache(cache)["fixture"], {})

    def test_legacy_renewed_cache_expires_even_when_upstream_no_longer_returns_it(self):
        now = 1_800_000_000
        cache = str(self.root / "legacy.json")
        engine._write_observation_cache(cache, {"fixture": {"1": {
            "name": "测试皮肤", "classid": "100", "instanceid": "0", "amount": 1,
            "first_seen": now - 20 * 86400, "last_seen": now - 86400, "expires_at": now + 9 * 86400,
        }}})
        live, observed = engine.apply_observation_cache("fixture", [], set(), cache_path=cache, now=now)
        self.assertEqual((live, observed), ([], []))
        self.assertEqual(engine._read_observation_cache(cache)["fixture"], {})

    def test_legacy_unknown_cache_is_not_reused_as_verified_history(self):
        now = 1_800_000_000
        cache = str(self.root / "unknown.json")
        engine._write_observation_cache(cache, {"fixture": {"1": {
            "name": "测试皮肤", "classid": "100", "instanceid": "0", "amount": 1,
            "first_seen": now - 86400, "last_seen": now, "expires_at": now + 9 * 86400,
            "sources": ["steamwebapi:parse=1:mode=1:sample=1:page=1"],
        }}})
        self.assertEqual(engine.apply_observation_cache("fixture", [], set(), cache_path=cache, now=now), ([], []))
        self.assertEqual(engine._read_observation_cache(cache)["fixture"], {})


if __name__ == "__main__":
    unittest.main()
