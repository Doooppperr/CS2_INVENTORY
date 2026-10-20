from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cs2_inventory import inventory_engine as engine
from cs2_inventory.observation import error_reason_codes, scan_quality, signal_kind
from cs2_inventory.unified import unify_inventory


class ScanQualityTests(unittest.TestCase):
    def asset(self, observation="live"):
        return engine.AssetRecord(assetid="1", classid="100", instanceid="0", name="皮肤", observation=observation)

    def test_complete_live_inventory_can_be_ready_for_light_and_deep(self):
        quality = engine.assess_inventory_quality([self.asset()], [], [], 1, [])
        self.assertTrue(quality["comparable"])
        self.assertEqual(scan_quality({"quality": quality}), "ready")

    def test_observed_union_above_steam_count_is_not_incomplete(self):
        public = [engine.AssetRecord(assetid=str(i), classid="100", instanceid="0", name="皮肤", observation="live") for i in range(644)]
        hidden = [engine.AssetRecord(assetid=str(i), classid="100", instanceid="0", name="保护皮肤", observation="live", tradeprotected=True) for i in range(644, 651)]
        quality = engine.assess_inventory_quality(public, hidden, [], 647, [])
        self.assertTrue(quality["comparable"])
        self.assertEqual(quality["observed_total"], 651)
        unified = unify_inventory({"public": engine.group_records_by_name(public),
            "protected_live": engine.group_records_by_name(hidden), "quality": quality})
        self.assertEqual(unified["total_items"], 651)
        self.assertEqual(unified["_scan_status"], "ready")

    def test_union_deduplicates_ids_and_does_not_invent_declared_missing_items(self):
        one = self.asset()
        rows = engine.merge_asset_records([[one], [one]])
        self.assertEqual(len(rows), 1)
        quality = engine.assess_inventory_quality(rows, [], [], 9, [])
        unified = unify_inventory({"public": engine.group_records_by_name(rows), "quality": quality})
        self.assertEqual(unified["total_items"], 1)
        self.assertEqual(unified["_scan_status"], "partial")
        self.assertEqual(quality["reason_codes"], ["count_shortfall"])

    def test_larger_count_does_not_override_freshness_or_transport_failure(self):
        for observation, errors, code in (("cached", [], "cached_items"), ("unknown", [], "freshness_unknown"),
                                          ("live", ["官方公开库存: HTTP 429"], "steam_rate_limit")):
            quality = engine.assess_inventory_quality([self.asset(observation)], [], [], 0, errors)
            self.assertFalse(quality["comparable"])
            self.assertIn(code, quality["reason_codes"])

    def test_real_light_path_is_not_permanently_partial(self):
        payload = {"assets": [{"assetid": "1", "classid": "100", "instanceid": "0"}],
                   "descriptions": [{"classid": "100", "instanceid": "0", "name": "皮肤"}], "total_inventory_count": 1}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(engine, "fetch_public_inventory", return_value=payload), mock.patch.object(
            engine, "localize_asset_records", side_effect=lambda rows, **kwargs: rows):
            result = engine.run_lightweight_query("76561198000000001", [], key="fixture",
                observation_cache_path=str(Path(directory) / "observations.json"))
        self.assertEqual(unify_inventory(result)["_scan_status"], "ready")

    def test_caches_unknown_identity_and_count_gaps_stay_partial(self):
        cases = [([self.asset("unknown")], [], [], 1, "freshness_unknown"),
                 ([self.asset("cached")], [], [], 1, "cached_items"),
                 ([self.asset()], [], [self.asset("cached")], 1, "cached_items"),
                 ([self.asset()], [], [], 2, "count_shortfall"),
                 ([self.asset()], [], [], None, "total_unknown")]
        for public, hidden, cached, total, code in cases:
            quality = engine.assess_inventory_quality(public, hidden, cached, total, [])
            self.assertFalse(quality["comparable"])
            self.assertIn(code, quality["reason_codes"])

    def test_no_items_only_does_not_confirm_empty_but_independent_full_view_can(self):
        errors = ['steamwebapi:sample=1: HTTP 411: {"error":"NO_ITEMS"}']
        unknown = engine.assess_inventory_quality([], [], [], None, errors)
        self.assertFalse(unknown["comparable"])
        confirmed = engine.assess_inventory_quality([], [], [], 0, errors)
        self.assertEqual(scan_quality({"quality": confirmed, "errors": errors}), "ready")
        self.assertEqual(scan_quality({"coverage": {"status": "failed"}, "quality": confirmed, "errors": errors}), "ready")
        for suffix in ('官方公开库存: HTTP 429', 'steamwebapi: HTTP 403'):
            quality = engine.assess_inventory_quality([], [], [], 0, errors + [suffix])
            self.assertFalse(quality["comparable"])

    def test_provider_freshness_requires_inventory_header_and_nonfallback_route(self):
        def freshness(headers, mode="2", trade_locked=None, payload=None):
            return engine.provider_page_freshness(headers, payload or [], state="active", mode=mode, trade_locked=trade_locked)
        self.assertEqual(freshness({"steamwebapi-cache": "no"}), "unknown")  # 隐含模式 4。
        self.assertEqual(freshness({"steamwebapi-cache": "no"}, trade_locked="1"), "live")
        self.assertEqual(freshness({"steamwebapi-cache": "no"}, trade_locked="0"), "live")
        self.assertEqual(freshness({"steamwebapi-cache": "no"}, mode="0"), "live")
        self.assertEqual(freshness({"cf-cache-status": "MISS"}, trade_locked="1"), "unknown")
        self.assertEqual(freshness({"steamwebapi-cache": "yes"}, trade_locked="1"), "cached")
        self.assertEqual(freshness({"steamwebapi-cache": "no"}, trade_locked="1", payload={"cached": True}), "cached")

    def test_strict_context_and_response_evidence_survive_fetch(self):
        with mock.patch.object(engine, "http_get_json_with_headers", return_value=(
            [{"assetid": "1", "markethashname": "Skin", "tradeprotected": True}], {"steamwebapi-cache": "no"})) as request:
            result = engine.fetch_steamwebapi_raw_inventory("fixture", key="secret", mode="2", parse="1", trade_locked="1")
        self.assertEqual(request.call_args.args[1]["trade_locked"], "1")
        row = engine.asset_records_from_parsed_payload(result.pages[0].payload, source=result.pages[0].source)[0]
        self.assertEqual(row.observation, "live")
        self.assertEqual(engine._freshness(result.pages[0].source, {"cached": True}), "cached")

    def test_redacted_error_categories_are_specific(self):
        codes = error_reason_codes(["官方公开库存: 网络请求失败：[Errno 101] Network is unreachable",
                                   "官方公开库存: HTTP 429", "官方公开库存: HTTP 403", "steamwebapi: 库存分页未完成"])
        self.assertEqual(codes, ["steam_network", "steam_rate_limit", "steam_unavailable", "pagination"])

    def test_provider_group_and_weapon_subtypes_are_not_missing_categories(self):
        for kind in ("ak-47", "glock-18", "moto gloves"):
            self.assertEqual(signal_kind("Skin", kind), "eligible")
        self.assertEqual(engine._item_type({"itemgroup": "rifle", "itemtype": "ak-47"}), "rifle")
        self.assertEqual(signal_kind("Medal", engine._item_type({"itemgroup": "collectible", "itemtype": "ak-47"})), "excluded")
        self.assertEqual(signal_kind("AK-47 | Skin", "unknown"), "unknown")


if __name__ == "__main__":
    unittest.main()
