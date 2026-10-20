"""API 单源、独立刷新和诊断权限的端到端回归。"""

from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from cs2_inventory import inventory_engine as engine
from cs2_inventory.api_inventory import run_api_inventory_query
from cs2_inventory.app import create_app
from cs2_inventory.database import db
from cs2_inventory.inventory_views import DIAGNOSTIC_KEYS, inventory_view
from cs2_inventory.localization import _snapshot_payload
from cs2_inventory.models import (
    QuotaUsage,
    ScanJob,
    Snapshot,
    SteamTarget,
    Subscription,
    User,
    utcnow,
)
from cs2_inventory.services import prune_expired, snapshot_diff, store_snapshot
from cs2_inventory.unified import unify_inventory
from cs2_inventory.worker import process_job

LIVE = {"steamwebapi-cache": "no"}


def protected(assetid="101", **fields):
    return [
        {
            "assetid": assetid,
            "classid": "100",
            "name": "测试皮肤",
            "markethashname": "AK-47 | Test",
            "tradable": False,
            "tradeprotected": True,
            **fields,
        }
    ]


def ordinary(ids=("102",)):
    return {
        "assets": [
            {
                "assetid": key,
                "classid": "100",
                "instanceid": "0",
                "amount": "1",
                "appid": "730",
                "contextid": "2",
            }
            for key in ids
        ],
        "descriptions": [
            {
                "classid": "100",
                "instanceid": "0",
                "name": "测试皮肤",
                "market_hash_name": "AK-47 | Test",
            }
        ],
        "total_inventory_count": len(ids),
    }


class ApiInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "STATE_DIR": Path(self.temp.name),
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
                "STEAMWEBAPI_KEY": "fixture",
                "OBSERVATION_CACHE": str(Path(self.temp.name) / "cache.json"),
            }
        )
        self.context = self.app.app_context()
        self.context.push()
        self.admin = User.query.filter_by(username="cs2inventory_admin").one()
        self.user = User.query.filter_by(username="cs2inventory_user").one()
        self.other_admin = User(
            username="other_admin",
            role="admin",
            account_kind="internal",
            password_hash="fixture",
            is_active=True,
        )
        self.target = SteamTarget(
            steamid="76561198000000001",
            persona_name="fixture",
            profile_updated_at=utcnow(),
        )
        db.session.add_all([self.other_admin, self.target])
        db.session.flush()
        db.session.add(Subscription(user_id=self.user.id, target_id=self.target.id))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.temp.cleanup()

    def query(self, first=None, second=None, **kwargs):
        def response(url, params, **options):
            if options.get("request_observer"):
                options["request_observer"]()
            value = first if params["trade_locked"] == "1" else second
            if isinstance(value, Exception):
                raise value
            return (
                protected()
                if value is None and params["trade_locked"] == "1"
                else ordinary()
                if value is None
                else value
            ), LIVE

        with (
            mock.patch.object(
                engine, "http_get_json_with_headers", side_effect=response
            ) as request,
            mock.patch.object(
                engine,
                "fetch_public_inventory",
                side_effect=AssertionError("禁止 Steam 库存查询"),
            ),
            mock.patch.object(
                engine,
                "apply_observation_cache",
                side_effect=AssertionError("禁止历史补入"),
            ),
            mock.patch.object(
                engine,
                "localize_asset_records",
                side_effect=AssertionError("禁止网络补译"),
            ),
        ):
            result = run_api_inventory_query(
                self.target.steamid, key="fixture", round_id="round-1", **kwargs
            )
        return result, request

    def save(self, first=None, second=None):
        result, _ = self.query(first, second)
        snapshot = store_snapshot(self.target, unify_inventory(result))
        db.session.commit()
        return snapshot

    def client(self, user):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user.id
        return client

    def assert_business(self, value):
        if isinstance(value, dict):
            self.assertFalse(set(value) & DIAGNOSTIC_KEYS)
            for item in value.values():
                self.assert_business(item)
        elif isinstance(value, list):
            for item in value:
                self.assert_business(item)

    def test_two_explicit_live_requests_and_immediate_protected_progress(self):
        progress = []
        result, request = self.query(
            on_progress=lambda value: progress.append(unify_inventory(value))
        )
        self.assertEqual(
            [call.args[1]["trade_locked"] for call in request.call_args_list],
            ["1", "0"],
        )
        for call in request.call_args_list:
            for key, value in {
                "state": "active",
                "no_cache": "1",
                "with_no_tradable": "1",
                "group": "0",
                "try_first_seven_days_blocked_items": "2",
            }.items():
                self.assertEqual(call.args[1][key], value)
        self.assertEqual(
            [call.args[1]["parse"] for call in request.call_args_list], ["1", "0"]
        )
        self.assertEqual(progress[0]["total_items"], 1)
        self.assertFalse(progress[0]["_quality"]["comparable"])
        self.assertTrue(unify_inventory(result)["_quality"]["comparable"])

    def test_same_id_keeps_protection_and_same_name_is_split_for_users(self):
        snapshot = self.save(second=ordinary(("101", "102")))
        self.assertEqual(snapshot.total_items, 2)
        data = self.client(self.user).get(f"/api/monitors/{self.target.id}").json
        self.assert_business(data)
        self.assertEqual(
            data["snapshot"]["items"],
            [
                {"name": "测试皮肤", "count": 1, "is_trade_protected": True},
                {"name": "测试皮肤", "count": 1, "is_trade_protected": False},
            ],
        )
        self.assertEqual(data["inventory_sections"]["protected"]["total_items"], 1)
        self.assertEqual(data["inventory_sections"]["other"]["total_items"], 1)

    def test_context16_membership_is_protection_even_if_flag_missing(self):
        result, _ = self.query(first=protected(tradeprotected=False, tradable=False))
        row = next(
            asset
            for asset in unify_inventory(result)["_assets"]
            if asset["asset_key"] == "101"
        )
        self.assertTrue(row["is_trade_protected"])

    def test_unknown_class_and_name_do_not_drop_real_protected_asset(self):
        result, _ = self.query(
            first=[
                {
                    "assetid": "101",
                    "markethashname": "Untranslated Skin",
                    "tradable": False,
                }
            ]
        )
        self.assertEqual(unify_inventory(result)["_scan_status"], "ready")
        self.assertTrue(unify_inventory(result)["_assets"][0]["is_trade_protected"])

    def test_unlock_is_not_an_inventory_event(self):
        before = self.save(second=ordinary(()))
        after = self.save(first=[], second=ordinary(("101",)))
        diff = snapshot_diff(after, before)
        self.assertTrue(diff["comparable"])
        for key in ("added", "removed", "changed", "reappeared"):
            self.assertEqual(diff[key], [])
        self.assertFalse(after.items[0].is_trade_protected)

    def test_successful_empty_and_scoped_no_items(self):
        for first in ([], engine.SteamQueryError('HTTP 411: {"error":"NO_ITEMS"}')):
            result, _ = self.query(first=first)
            self.assertEqual(unify_inventory(result)["_scan_status"], "ready")
            self.assertEqual(unify_inventory(result)["total_items"], 1)
        result, _ = self.query(
            first=[], second=engine.SteamQueryError('HTTP 411: {"error":"NO_ITEMS"}')
        )
        self.assertFalse(result["quality"]["comparable"])

    def test_private_timeout_and_rate_limit_do_not_clear_previous_other_section(self):
        before = self.save()
        for error in (
            'HTTP 451: {"error":"UNAVAILABLE"}',
            'HTTP 429: {"error":"RATE_LIMIT"}',
            'HTTP 403: {"error":"PRIVATE"}',
        ):
            self.save(first=protected("103"), second=engine.SteamQueryError(error))
            data = inventory_view(self.target.id, self.user)
            self.assertEqual(data["total_items"], before.total_items)
            self.assertEqual(data["last_complete_snapshot_id"], before.id)
            self.assertEqual(data["inventory_sections"]["protected"]["total_items"], 1)
            self.assertEqual(
                data["inventory_sections"]["other"]["items"][0]["count"], 1
            )
            self.assertEqual(self.target.last_success_at, before.scanned_at)

    def test_failed_protected_request_retains_last_protected_success(self):
        before = self.save()
        self.save(
            first=engine.SteamQueryError("HTTP 451: unavailable"),
            second=ordinary(("104", "105")),
        )
        view = inventory_view(self.target.id, self.user)
        self.assertEqual(
            view["inventory_sections"]["protected"]["updated_at"],
            json.loads(gzip.decompress(before.payload_gzip))["quality"]["api_contexts"][
                "protected"
            ]["scanned_at"],
        )
        self.assertEqual(view["inventory_sections"]["other"]["total_items"], 2)

    def test_no_synthetic_id_and_no_cached_or_unknown_page_is_accepted(self):
        result, _ = self.query(first=[{"name": "metadata only"}])
        self.assertFalse(result["quality"]["api_contexts"]["protected"]["complete"])
        for headers in ({"steamwebapi-cache": "yes"}, {}):
            with mock.patch.object(
                engine,
                "http_get_json_with_headers",
                return_value=(protected(), headers),
            ):
                result = run_api_inventory_query(self.target.steamid, key="fixture")
            self.assertFalse(result["quality"]["comparable"])

    def test_paginated_inventory_follows_cursor_even_short_page(self):
        calls = []

        def response(url, params, **kwargs):
            calls.append(dict(params))
            if kwargs.get("request_observer"):
                kwargs["request_observer"]()
            if params["trade_locked"] == "1":
                return protected(), LIVE
            if "start_assetid" not in params:
                return ordinary(("102",)), {**LIVE, "last_assetid": "102"}
            return ordinary(("103",)), LIVE

        with (
            mock.patch.object(
                engine, "http_get_json_with_headers", side_effect=response
            ),
            mock.patch.object(engine.time, "sleep"),
        ):
            result = run_api_inventory_query(self.target.steamid, key="fixture")
        self.assertEqual(calls[-1]["start_assetid"], "102")
        self.assertEqual(unify_inventory(result)["total_items"], 3)

    def test_repeated_cursor_does_not_replace_successful_section(self):
        before = self.save()

        def response(url, params, **kwargs):
            if kwargs.get("request_observer"):
                kwargs["request_observer"]()
            return (
                (protected(), LIVE)
                if params["trade_locked"] == "1"
                else (ordinary(("104",)), {**LIVE, "last_assetid": "104"})
            )

        with (
            mock.patch.object(
                engine, "http_get_json_with_headers", side_effect=response
            ),
            mock.patch.object(engine.time, "sleep"),
        ):
            result = run_api_inventory_query(self.target.steamid, key="fixture")
        store_snapshot(self.target, unify_inventory(result))
        db.session.commit()
        self.assertFalse(result["quality"]["comparable"])
        self.assertEqual(
            inventory_view(self.target.id, self.user)["last_complete_snapshot_id"],
            before.id,
        )

    def test_old_cache_cannot_reappear_and_cross_policy_is_not_compared(self):
        old = store_snapshot(
            self.target,
            {
                "_assets": [
                    {
                        "asset_key": "999",
                        "name": "旧缓存物品",
                        "amount": 1,
                        "observation": "cached",
                    }
                ],
                "items": [{"name": "旧缓存物品", "count": 1}],
                "total_items": 1,
                "item_types": 1,
                "elapsed_ms": 1,
                "coverage": "partial",
                "errors": [],
                "_scan_status": "partial",
            },
        )
        db.session.commit()
        current = self.save()
        self.assertNotIn("999", [item.asset_key for item in current.items])
        self.assertEqual(snapshot_diff(current, old)["removed"], [])

    def test_diagnostics_only_named_super_admin_and_job_results_are_redacted(self):
        self.save()
        job = ScanJob(
            target_id=self.target.id,
            steamid=self.target.steamid,
            requested_by=self.user.id,
            status="failed",
            error="provider failure",
            result_json=json.dumps(
                {
                    "diagnostics": {"errors": ["provider failure"]},
                    "target_id": self.target.id,
                }
            ),
        )
        db.session.add(job)
        db.session.commit()
        for user in (self.user, self.other_admin):
            client = self.client(user)
            for path in (
                "/api/monitors",
                f"/api/monitors/{self.target.id}",
                f"/api/jobs/{job.id}",
                "/api/dashboard",
            ):
                response = client.get(path)
                if response.status_code == 200:
                    self.assert_business(response.json)
            self.assertFalse(
                client.get("/api/bootstrap").json["user"][
                    "can_view_inventory_diagnostics"
                ]
            )
            self.assertEqual(
                client.get("/api/dashboard?section=pending").status_code, 403
            )
        client = self.client(self.admin)
        self.assertTrue(
            client.get("/api/bootstrap").json["user"]["can_view_inventory_diagnostics"]
        )
        self.assertIn("diagnostics", client.get(f"/api/monitors/{self.target.id}").json)
        self.assertIn("diagnostics", client.get(f"/api/jobs/{job.id}").json["result"])

    def test_name_repair_preserves_source_policy_and_context_metadata(self):
        row = self.save()
        before = json.loads(gzip.decompress(row.payload_gzip))["quality"]
        row.payload_gzip = _snapshot_payload(row)
        self.assertEqual(
            json.loads(gzip.decompress(row.payload_gzip))["quality"], before
        )

    def test_all_job_kinds_use_same_api_engine_and_charge_real_requests(self):
        for kind in ("initial", "manual", "instant", "daily", "daily_light"):
            result, _ = self.query()
            job = ScanJob(
                target_id=self.target.id,
                steamid=self.target.steamid,
                kind=kind,
                status="running",
            )
            db.session.add(job)
            db.session.commit()
            with mock.patch(
                "cs2_inventory.worker.run_api_inventory_query", return_value=result
            ) as query:
                process_job(job.id)
            self.assertEqual(query.call_count, 1)
            self.assertEqual(job.status, "completed")
        self.assertEqual(sum(row.credits for row in QuotaUsage.query.all()), 30)

    def test_pruning_preserves_day_last_complete_and_latest_context(self):
        complete = self.save()
        complete.scanned_at = utcnow() - timedelta(days=3, hours=2)
        partial = self.save(
            first=protected("103"),
            second=engine.SteamQueryError("HTTP 451: unavailable"),
        )
        partial.scanned_at = complete.scanned_at + timedelta(minutes=1)
        db.session.commit()
        prune_expired()
        self.assertIsNotNone(db.session.get(Snapshot, complete.id))
        self.assertIsNotNone(db.session.get(Snapshot, partial.id))
        self.assertEqual(
            inventory_view(self.target.id, self.user)["last_complete_snapshot_id"],
            complete.id,
        )

    def test_worker_commits_protected_items_before_other_request_finishes(self):
        before = self.save()
        job = ScanJob(
            target_id=self.target.id,
            steamid=self.target.steamid,
            status="running",
            kind="manual",
        )
        db.session.add(job)
        db.session.commit()

        def response(url, params, **kwargs):
            kwargs["request_observer"]()
            if params["trade_locked"] == "1":
                return protected("103"), LIVE
            latest = (
                Snapshot.query.filter_by(target_id=self.target.id)
                .order_by(Snapshot.id.desc())
                .first()
            )
            self.assertEqual([item.asset_key for item in latest.items], ["103"])
            self.assertEqual(latest.coverage, "partial")
            self.assertEqual(self.target.last_success_at, before.scanned_at)
            raise engine.SteamQueryError("HTTP 451: unavailable")

        with (
            mock.patch.object(
                engine, "http_get_json_with_headers", side_effect=response
            ),
            mock.patch.object(
                engine,
                "fetch_public_inventory",
                side_effect=AssertionError("禁止直接 Steam 查询"),
            ),
        ):
            process_job(job.id)
        self.assertEqual(sum(row.credits for row in QuotaUsage.query.all()), 6)
        self.assertEqual(
            inventory_view(self.target.id, self.user)["last_complete_snapshot_id"],
            before.id,
        )
        self.assertEqual(self.target.scan_status, "partial")

    def test_compare_uses_last_complete_same_policy_on_exact_day(self):
        baseline = self.save()
        baseline.scanned_at = utcnow() - timedelta(days=1, hours=1)
        partial = self.save(
            first=protected("103"),
            second=engine.SteamQueryError("HTTP 451: unavailable"),
        )
        partial.scanned_at = baseline.scanned_at + timedelta(minutes=1)
        current = self.save()
        db.session.commit()
        data = (
            self.client(self.user)
            .get(f"/api/monitors/{self.target.id}/compare?days=1")
            .json
        )
        self.assertEqual(data["current"]["id"], current.id)
        self.assertEqual(data["baseline"]["id"], baseline.id)
        self.assert_business(data)

    def test_both_context_failures_do_not_create_zero_inventory(self):
        before = self.save()
        result, _ = self.query(
            first=engine.SteamQueryError("HTTP 451: unavailable"),
            second=engine.SteamQueryError("HTTP 429: rate limited"),
        )
        self.assertIsNone(store_snapshot(self.target, unify_inventory(result)))
        db.session.commit()
        self.assertEqual(
            inventory_view(self.target.id, self.user)["last_complete_snapshot_id"],
            before.id,
        )
        self.assertEqual(Snapshot.query.filter_by(target_id=self.target.id).count(), 1)

    def test_provider_false_success_never_confirms_empty(self):
        result, _ = self.query(first=[], second={"success": False, "assets": []})
        self.assertFalse(result["quality"]["comparable"])
        self.assertFalse(result["quality"]["api_contexts"]["other"]["complete"])

    def test_own_api_page_count_shortfall_blocks_only_that_context(self):
        payload = ordinary()
        payload["total_inventory_count"] = 8
        result, _ = self.query(second=payload)
        self.assertTrue(result["quality"]["api_contexts"]["protected"]["complete"])
        self.assertFalse(result["quality"]["api_contexts"]["other"]["complete"])


if __name__ == "__main__":
    unittest.main()
