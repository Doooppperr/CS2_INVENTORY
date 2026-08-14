# -*- coding: utf-8 -*-
import collections
import io
import json
import unittest
import urllib.error
from unittest import mock

from cs2_inventory.inventory_engine import (
    APPID_CS2,
    CONTEXTID_CS2,
    TRADE_PROTECTION_SECONDS,
    AssetRecord,
    apply_observation_cache,
    apply_hidden_budget,
    asset_records_from_parsed_payload,
    asset_records_from_raw_payload,
    build_cookie_header,
    classify_protected_assets,
    classify_hidden_assets,
    classify_owner_view_hidden,
    counter_by_name,
    fetch_public_inventory,
    fetch_steamwebapi_raw_inventory,
    group_records_by_name,
    inventory_items_from_payload,
    merge_asset_records,
    seed_observation_cache,
    parse_history_html_payload,
    protected_items_from_trade_payloads,
    run_max_coverage_query,
    merge_public_missing_third_party_items,
    remove_public_visible_false_protected,
    render_report,
    steamwebapi_items_from_payload,
    InventoryItem,
    ProtectedItem,
    RawInventoryPage,
    http_get_json_with_headers,
    render_maxcoverage_report,
)


class CS2InventoryQueryTests(unittest.TestCase):
    def test_inventory_items_from_payload_groups_visible_names(self):
        payload = {
            "assets": [
                {"appid": APPID_CS2, "contextid": CONTEXTID_CS2, "assetid": "a1", "classid": "c1", "instanceid": "i1", "amount": "1"},
                {"appid": APPID_CS2, "contextid": CONTEXTID_CS2, "assetid": "a2", "classid": "c2", "instanceid": "0", "amount": "2"},
                {"appid": 570, "contextid": "2", "assetid": "ignored", "classid": "c3", "instanceid": "0", "amount": "1"},
            ],
            "descriptions": [
                {"appid": APPID_CS2, "contextid": CONTEXTID_CS2, "classid": "c1", "instanceid": "i1", "market_hash_name": "AK-47 | Redline (Field-Tested)"},
                {"appid": APPID_CS2, "contextid": CONTEXTID_CS2, "classid": "c2", "instanceid": "0", "market_hash_name": "Fracture Case"},
            ],
        }
        items = inventory_items_from_payload(payload)
        self.assertEqual(counter_by_name(items), collections.Counter({"AK-47 | Redline (Field-Tested)": 1, "Fracture Case": 2}))

    def test_protected_items_from_recent_trade_history(self):
        now = 1_800_000_000
        recent = now - 3600
        old = now - TRADE_PROTECTION_SECONDS - 60
        payload = {
            "trades": [
                {
                    "tradeid": "t_recent",
                    "time_init": recent,
                    "status": 3,
                    "assets_received": [
                        {"appid": APPID_CS2, "contextid": CONTEXTID_CS2, "new_assetid": "new1", "classid": "c1", "instanceid": "i1", "amount": "1"},
                        {"appid": 570, "contextid": "2", "assetid": "ignored", "classid": "c_dota", "instanceid": "0", "amount": "1"},
                    ],
                },
                {
                    "tradeid": "t_old",
                    "time_init": old,
                    "status": 3,
                    "assets_received": [
                        {"appid": APPID_CS2, "contextid": CONTEXTID_CS2, "new_assetid": "old1", "classid": "c2", "instanceid": "0", "amount": "1"},
                    ],
                },
            ],
            "descriptions": [
                {"appid": APPID_CS2, "contextid": CONTEXTID_CS2, "classid": "c1", "instanceid": "i1", "market_hash_name": "M4A1-S | Printstream (Minimal Wear)"},
                {"appid": APPID_CS2, "contextid": CONTEXTID_CS2, "classid": "c2", "instanceid": "0", "market_hash_name": "Old Item"},
            ],
        }
        protected = protected_items_from_trade_payloads([payload], now=now)
        self.assertEqual(len(protected), 1)
        self.assertEqual(protected[0].name, "M4A1-S | Printstream (Minimal Wear)")
        self.assertEqual(protected[0].protected_until, recent + TRADE_PROTECTION_SECONDS)

    def test_render_report_contains_two_requested_sections(self):
        report = render_report("76561198000000000", [], [], show_details=False)
        self.assertIn("1. 受到交易保护 / 公开库存直接查不到的物品名称", report)
        self.assertIn("2. 不处于交易保护的物品名称", report)
        self.assertIn("（空）", report)
        self.assertIn("合计 0 件", report)

    def test_render_details_contains_public_assetid_and_totals(self):
        protected = [ProtectedItem("Protected Sticker", "p1", "", 0, 0, 2)]
        visible = [InventoryItem("v1", "c1", "0", "Visible Case", 3)]
        report = render_report("76561198000000000", protected, visible, show_details=True)
        self.assertIn("Visible Case（assetid=v1，数量=3）", report)
        self.assertIn("交易保护/公开缺失 2 件；公开可见 3 件；合计 5 件", report)

    def test_failed_trade_is_not_counted_as_protected(self):
        now = 1_800_000_000
        payload = {
            "trades": [
                {
                    "tradeid": "t_failed",
                    "time_init": now - 60,
                    "status": "failed",
                    "assets_received": [
                        {"appid": APPID_CS2, "contextid": CONTEXTID_CS2, "new_assetid": "failed1", "classid": "c1", "instanceid": "0", "amount": "1"},
                    ],
                }
            ],
            "descriptions": [
                {"appid": APPID_CS2, "contextid": CONTEXTID_CS2, "classid": "c1", "instanceid": "0", "market_hash_name": "Failed Trade Item"},
            ],
        }
        self.assertEqual(protected_items_from_trade_payloads([payload], now=now), [])

    def test_keyless_history_html_parser_extracts_received_cs2_items(self):
        body = """
        <html><head><script>
        var g_rgHistoryInventory = {"730":{"2":{
          "class_ak":{"appid":"730","contextid":"2","classid":"c_ak","instanceid":"0","id":"asset_new_1","market_hash_name":"AK-47 | Redline (Field-Tested)"},
          "class_given":{"appid":"730","contextid":"2","classid":"c_given","instanceid":"0","id":"asset_given","market_hash_name":"Given Item"}
        }}};
        HistoryPageCreateItemHover( 'history_row_1_received_0', 730, '2', 'class_ak', '1' );
        HistoryPageCreateItemHover( 'history_row_1_given_0', 730, '2', 'class_given', '1' );
        </script></head><body>
          <div class="tradehistoryrow" id="trade_9991">
            <div class="tradehistory_date">Jan 15, 2027</div>
            <div class="tradehistory_timestamp">8:00am</div>
            <div class="tradehistory_event_description"><a href="https://steamcommunity.com/profiles/1">Partner</a></div>
            <div class="history_item" id="history_row_1_received_0"></div>
            <div class="history_item" id="history_row_1_given_0"></div>
          </div>
        </body></html>
        """
        payload = parse_history_html_payload(body)
        protected = protected_items_from_trade_payloads([payload], now=1_800_003_600)
        self.assertEqual(len(protected), 1)
        self.assertEqual(protected[0].name, "AK-47 | Redline (Field-Tested)")
        self.assertEqual(protected[0].assetid, "asset_new_1")

    def test_cookie_header_accepts_login_secure_without_api_key(self):
        cookie = build_cookie_header(steam_login_secure="abc%7Cdef", sessionid="sid")
        self.assertIn("steamLoginSecure=abc%7Cdef", cookie)
        self.assertIn("sessionid=sid", cookie)

    def test_steamwebapi_payload_splits_trade_protected_items(self):
        payload = [
            {
                "markethashname": "★ Butterfly Knife | Marble Fade (Factory New)",
                "assetid": "knife1",
                "tradeprotected": True,
                "tradable": False,
                "tradelocked": True,
                "count": 1,
            },
            {
                "markethashname": "Fracture Case",
                "assetid": "case1",
                "tradeprotected": False,
                "count": 3,
            },
        ]
        result = steamwebapi_items_from_payload(payload, now=1_800_000_000)
        self.assertEqual(counter_by_name(result.protected_items), collections.Counter({"★ Butterfly Knife | Marble Fade (Factory New)": 1}))
        self.assertEqual(counter_by_name(result.visible_items), collections.Counter({"Fracture Case": 3}))

    def test_public_visible_assetid_removes_steamwebapi_false_positive(self):
        protected = [
            ProtectedItem("Sticker | frozen (Glitter) | Shanghai 2024", "43434208210", "", 0, 0, 1),
            ProtectedItem("Desert Eagle | Mecha Industries (Factory New)", "53261193441", "", 0, 0, 1),
        ]
        public_visible = [
            InventoryItem("43434208210", "5835584431", "6405281258", "USP-S", 1),
        ]
        filtered = remove_public_visible_false_protected(protected, public_visible)
        self.assertEqual([item.name for item in filtered], ["Desert Eagle | Mecha Industries (Factory New)"])

    def test_public_missing_third_party_items_are_task1_candidates(self):
        protected = [
            ProtectedItem("Sticker | frozen (Glitter) | Shanghai 2024", "43434208210", "", 0, 0, 1),
            ProtectedItem("Desert Eagle | Mecha Industries (Factory New)", "53261193441", "", 0, 0, 1),
        ]
        third_party_visible = [
            InventoryItem("53192582604", "", "0", "FAMAS | Dark Water (Minimal Wear)", 1),
            InventoryItem("53184560840", "", "0", "MP7 | Fade (Factory New)", 1),
            InventoryItem("43434208210", "", "0", "Sticker | frozen (Glitter) | Shanghai 2024", 1),
        ]
        public_visible = [
            InventoryItem("43434208210", "5835584431", "6405281258", "USP-S", 1),
        ]

        merged = merge_public_missing_third_party_items(protected, third_party_visible, public_visible)
        self.assertEqual(
            [item.name for item in merged],
            [
                "Desert Eagle | Mecha Industries (Factory New)",
                "FAMAS | Dark Water (Minimal Wear)",
                "MP7 | Fade (Factory New)",
            ],
        )


class MaxCoverageTests(unittest.TestCase):
    def test_http_429_uses_retry_after_seconds_from_json_body(self):
        error = urllib.error.HTTPError(
            "https://example.invalid/inventory",
            429,
            "Too Many Requests",
            {"Retry-After": "5"},
            io.BytesIO(json.dumps({"retryAfterSeconds": 37}).encode("utf-8")),
        )

        class Response:
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"ok":true}'

        with mock.patch(
            "cs2_inventory.inventory_engine.urllib.request.urlopen",
            side_effect=[error, Response()],
        ), mock.patch("cs2_inventory.inventory_engine.time.sleep") as sleep:
            payload, headers = http_get_json_with_headers(
                "https://example.invalid/inventory", retries=1
            )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(headers["content-type"], "application/json")
        sleep.assert_called_once_with(37.0)

    def test_max_coverage_defaults_to_two_parse1_samples(self):
        self.assertEqual(run_max_coverage_query.__kwdefaults__["parse1_samples"], 2)

    def test_report_marks_public_missing_state_for_client_confirmation(self):
        report = render_maxcoverage_report({"steamid": "76561198000000000"})
        self.assertIn("公开库存缺失、保护状态以客户端为准", report)
        self.assertNotIn("但交易保护已结束", report)

    def _asset(self, assetid, classid, instanceid, name, amount=1, **extra):
        return AssetRecord(
            assetid=assetid,
            classid=classid,
            instanceid=instanceid,
            name=name,
            amount=amount,
            sources=extra.get("sources", ("fixture",)),
            tradeprotected=extra.get("tradeprotected", False),
            tradelocked=extra.get("tradelocked", False),
            protected_until=extra.get("protected_until", 0),
        )

    def test_assetid_union_across_fluctuating_snapshots(self):
        first = [
            self._asset("a1", "c1", "0", "Old Name", sources=("snapshot_1",)),
            self._asset("a2", "c2", "0", "Protected Sticker", sources=("snapshot_1",)),
        ]
        second = [
            self._asset("a1", "c1", "0", "New Name", sources=("snapshot_2",)),
            self._asset("a3", "c3", "0", "Only In Second", sources=("snapshot_2",)),
        ]
        merged = merge_asset_records([first, second])
        by_assetid = {record.assetid: record for record in merged}
        self.assertEqual(set(by_assetid), {"a1", "a2", "a3"})
        self.assertEqual(by_assetid["a1"].name, "New Name")
        self.assertEqual(tuple(sorted(by_assetid["a1"].sources)), ("snapshot_1", "snapshot_2"))

    def test_same_name_counts_are_merged_at_display_time(self):
        groups = group_records_by_name(
            [
                self._asset("a1", "c1", "0", "Sticker X", 1),
                self._asset("a2", "c2", "0", "Sticker X", 4),
                self._asset("a3", "c3", "0", "Skin B", 2),
            ]
        )
        self.assertEqual(groups[0]["name"], "Skin B")
        self.assertEqual(groups[1]["name"], "Sticker X")
        self.assertEqual(groups[1]["count"], 5)
        self.assertEqual(groups[1]["assetids"], ["a1", "a2"])

    def test_parse1_sticker_attachment_false_positive_is_excluded(self):
        public = [
            self._asset("43434208210", "5835584431", "6405281258", "USP-S", sources=("steam_public_contextid2",)),
        ]
        parsed = [
            self._asset(
                "43434208210",
                "6360314986",
                "188530139",
                "Sticker | frozen (Glitter) | Shanghai 2024",
                tradeprotected=True,
                tradelocked=True,
                sources=("steamwebapi:parse=1:mode=2",),
            ),
            self._asset(
                "53261193441",
                "7993037304",
                "8307313678",
                "Desert Eagle | Mecha Industries (Factory New)",
                tradeprotected=True,
                tradelocked=True,
                sources=("steamwebapi:parse=1:mode=2",),
            ),
        ]
        protected, excluded = classify_protected_assets([], public, parsed)
        self.assertEqual([record.assetid for record in protected], ["53261193441"])
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["assetid"], "43434208210")
        self.assertIn("假阳性", excluded[0]["reason"])

    def test_ended_trade_protection_items_are_public_missing_not_protected(self):
        public = [
            self._asset("a_public", "c_pub", "0", "Visible Case", sources=("steam_public_contextid2",)),
        ]
        trading = [
            self._asset("a_hold", "c1", "0", "Trade Hold Skin", sources=("steamwebapi:parse=0:mode=2",)),
            self._asset("a_ended", "c2", "0", "Ended Protection Sticker", sources=("steamwebapi:parse=0:mode=2",)),
        ]
        parsed = [
            self._asset(
                "a_hold",
                "c1",
                "0",
                "Trade Hold Skin",
                tradeprotected=True,
                tradelocked=True,
                sources=("steamwebapi:parse=1:mode=2",),
            ),
            self._asset(
                "a_ended",
                "c2",
                "0",
                "Ended Protection Sticker",
                tradeprotected=False,
                tradelocked=False,
                sources=("steamwebapi:parse=1:mode=2",),
            ),
        ]
        protected, public_missing, excluded = classify_hidden_assets(trading, public, parsed, now=1_800_000_000)
        self.assertEqual([record.assetid for record in protected], ["a_hold"])
        self.assertEqual([record.assetid for record in public_missing], ["a_ended"])
        self.assertEqual(protected[0].protection_state, "active")
        self.assertEqual(public_missing[0].protection_state, "ended")
        self.assertEqual(excluded, [])

    def test_trading_item_without_parse1_counterpart_is_unknown_state(self):
        public = [self._asset("a_public", "c_pub", "0", "Visible Case")]
        trading = [self._asset("a_unknown", "c1", "0", "Unseen Protection State")]
        protected, public_missing, excluded = classify_hidden_assets(trading, public, [], now=1_800_000_000)
        self.assertEqual(protected, [])
        self.assertEqual(public_missing[0].protection_state, "unknown")

    def test_owner_view_splits_locked_vs_visibility_hidden(self):
        public = [self._asset("a_public", "c_pub", "0", "Visible Case")]
        owner = [
            self._asset("a_public", "c_pub", "0", "Visible Case", sources=("steam_owner_session_contextid2",)),
            self._asset("a_lock", "c1", "0", "Trade Locked Skin", sources=("steam_owner_session_contextid2",)),
            self._asset("a_end", "c2", "0", "Hidden But Tradeable", sources=("steam_owner_session_contextid2",)),
        ]
        tradable_map = {"a_public": True, "a_lock": False, "a_end": True}
        protected, public_missing = classify_owner_view_hidden(owner, public, tradable_map)
        self.assertEqual([record.assetid for record in protected], ["a_lock"])
        self.assertEqual([record.assetid for record in public_missing], ["a_end"])
        self.assertTrue(protected[0].tradelocked)
        self.assertFalse(public_missing[0].tradelocked)

    def test_fetch_public_inventory_passes_owner_cookie(self):
        captured = {}

        def fake_get_json(url, params, *, timeout=None, retries=None, user_agent=None, cookie=None):
            captured["cookie"] = cookie
            return {"assets": [], "descriptions": [], "total_inventory_count": 0, "success": 1}

        with mock.patch("cs2_inventory.inventory_engine.http_get_json", side_effect=fake_get_json):
            fetch_public_inventory(
                "76561198000000000", cookie="steamLoginSecure=abc", timeout=10
            )
        self.assertEqual(captured["cookie"], "steamLoginSecure=abc")

    def test_hidden_budget_flags_over_budget_parse1_rows_as_unverified(self):
        protected = [self._asset("ph1", "c1", "0", "Phantom A"), self._asset("ph2", "c1", "0", "Phantom B")]
        public_missing = [self._asset("cor1", "c2", "0", "Corroborated A")]
        kept_protected, kept_missing, excluded, unverified = apply_hidden_budget(
            protected,
            public_missing,
            trading_assetids={"cor1"},
            hidden_budget=2,
        )
        self.assertEqual(kept_protected, [])
        self.assertEqual([record.assetid for record in kept_missing], ["cor1"])
        self.assertEqual(excluded, [])
        self.assertEqual([record.assetid for record in unverified], ["ph1", "ph2"])

    def test_hidden_budget_keeps_parse1_only_rows_within_room(self):
        protected = [self._asset("real_lock", "c1", "0", "Real Locked A")]
        public_missing = [self._asset("cor1", "c2", "0", "Corroborated A")]
        kept_protected, kept_missing, excluded, unverified = apply_hidden_budget(
            protected,
            public_missing,
            trading_assetids={"cor1"},
            hidden_budget=3,
        )
        self.assertEqual([record.assetid for record in kept_protected], ["real_lock"])
        self.assertEqual([record.assetid for record in kept_missing], ["cor1"])
        self.assertEqual(excluded, [])
        self.assertEqual(unverified, [])

    def test_default_max_union_keeps_parse1_claims_without_budget_check(self):
        import tempfile
        from pathlib import Path

        public_payload = {
            "assets": [
                {"appid": "730", "contextid": "2", "assetid": "pub1", "classid": "cp", "instanceid": "0", "amount": "1"},
            ],
            "descriptions": [
                {"appid": "730", "contextid": "2", "classid": "cp", "instanceid": "0", "market_hash_name": "Public Case"},
            ],
            "total_inventory_count": 1,
        }
        parsed_payload = [
            {
                "markethashname": "★ Skeleton Knife | Tiger Tooth (Factory New)",
                "assetid": "53247111033",
                "classid": "ck",
                "instanceid": "ik",
                "count": 1,
                "tradeprotected": True,
                "tradelocked": True,
                "tradable": False,
            }
        ]

        class FakeRaw:
            def __init__(self, payload, label):
                self.pages = [
                    RawInventoryPage(
                        source=f"{label}:1:1",
                        payload=payload,
                        item_count=len(payload.get("assets", [])) if isinstance(payload, dict) else len(payload),
                        total_inventory_count=payload.get("total_inventory_count") if isinstance(payload, dict) else None,
                        last_assetid="",
                        duration_ms=1,
                    )
                ]
                self.upstream_item_counts = [self.pages[0].item_count]
                self.sources = [self.pages[0].source]
                self.total_inventory_counts = [self.pages[0].total_inventory_count]
                self.errors = []

        def fake_raw(steamid, *, key, mode, parse, samples=1, **kwargs):
            if parse == "0" and mode == "0":
                return FakeRaw(public_payload, "m0")
            if parse == "0" and mode in ("1", "2"):
                return FakeRaw({"assets": [], "descriptions": [], "total_inventory_count": 0}, "m2")
            if parse == "1":
                return FakeRaw(parsed_payload, "p1")
            raise AssertionError((mode, parse))

        with mock.patch("cs2_inventory.inventory_engine.fetch_public_inventory", return_value=public_payload):
            with mock.patch("cs2_inventory.inventory_engine.fetch_steamwebapi_raw_inventory", side_effect=fake_raw):
                with tempfile.TemporaryDirectory() as directory:
                    cache = str(Path(directory) / "obs.json")
                    result = run_max_coverage_query(
                        "76561198000000000",
                        key="fake",
                        language="english",
                        trading_samples=1,
                        normal_samples=1,
                        include_mode1=False,
                        include_parse1=True,
                        parse1_samples=1,
                        observation_cache_path=cache,
                        now=1_800_000_000,
                    )
        self.assertEqual(result["counts"]["protected_live"], 1)
        self.assertEqual(result["protected_live"][0]["name"], "★ Skeleton Knife | Tiger Tooth (Factory New)")
        self.assertEqual(result["excluded_false_positives"], [])

    def test_seeded_historical_asset_is_not_fake_observed_but_stays_cached(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            cache_path = str(Path(directory) / "observations.json")
            seeded = self._asset("53192582604", "7993038103", "8734226635", "FAMAS | Dark Water (Minimal Wear)")
            seed_observation_cache("76561198000000000", [seeded], cache_path=cache_path, now=1_800_000_000)
            live, observed = apply_observation_cache(
                "76561198000000000", [], set(), cache_path=cache_path, now=1_800_000_100
            )
            self.assertEqual((live, observed), ([], []))
            from cs2_inventory.inventory_engine import _read_observation_cache

            rows = _read_observation_cache(cache_path).get("76561198000000000", {})
            self.assertIn("53192582604", rows)
            self.assertEqual(rows["53192582604"]["sources"], ["historical_seed"])

    def test_raw_assets_ignore_contextid_6_and_require_description(self):
        payload = {
            "assets": [
                {"appid": "730", "contextid": "2", "assetid": "a1", "classid": "c1", "instanceid": "0", "amount": "1"},
                {"appid": "730", "contextid": "6", "assetid": "a_bad", "classid": "c6", "instanceid": "0", "amount": "1"},
                {"appid": "730", "contextid": "2", "assetid": "a_no_desc", "classid": "c_missing", "instanceid": "0", "amount": "1"},
            ],
            "descriptions": [
                {"appid": "730", "contextid": "2", "classid": "c1", "instanceid": "0", "market_hash_name": "AK-47 | Redline (Field-Tested)"},
            ],
        }
        records = asset_records_from_raw_payload(payload, source="fixture")
        self.assertEqual([record.assetid for record in records], ["a1"])
        self.assertEqual(records[0].name, "AK-47 | Redline (Field-Tested)")

    def test_observation_cache_retains_then_removes_when_public(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            cache_path = str(Path(directory) / "observations.json")
            live = self._asset("p1", "c1", "0", "Sticker | Fnatic (Holo)", 2)
            first_live, first_observed = apply_observation_cache(
                "76561198000000000", [live], set(), cache_path=cache_path, now=1000
            )
            self.assertEqual(([r.assetid for r in first_live], first_observed), (["p1"], []))

            second_live, second_observed = apply_observation_cache(
                "76561198000000000", [], set(), cache_path=cache_path, now=1001
            )
            self.assertEqual(second_live, [])
            self.assertEqual([(r.assetid, r.amount) for r in second_observed], [("p1", 2)])

            third_live, third_observed = apply_observation_cache(
                "76561198000000000", [], {"p1"}, cache_path=cache_path, now=1002
            )
            self.assertEqual((third_live, third_observed), ([], []))

    def test_fetch_raw_inventory_follows_last_assetid_pagination(self):
        responses = iter(
            [
                (
                    {
                        "assets": [
                            {"appid": "730", "contextid": "2", "assetid": "a1", "classid": "c1", "instanceid": "0", "amount": "1"},
                        ],
                        "descriptions": [
                            {"appid": "730", "contextid": "2", "classid": "c1", "instanceid": "0", "market_hash_name": "Page One"},
                        ],
                        "total_inventory_count": 2,
                        "success": 1,
                        "last_assetid": "a1",
                    },
                    {"last_assetid": "a1"},
                ),
                (
                    {
                        "assets": [
                            {"appid": "730", "contextid": "2", "assetid": "a2", "classid": "c2", "instanceid": "0", "amount": "1"},
                        ],
                        "descriptions": [
                            {"appid": "730", "contextid": "2", "classid": "c2", "instanceid": "0", "market_hash_name": "Page Two"},
                        ],
                        "total_inventory_count": 2,
                        "success": 1,
                        "last_assetid": "",
                    },
                    {},
                ),
            ]
        )
        call_params = []

        def fake_get(url, params, **kwargs):
            call_params.append(dict(params))
            return next(responses)

        with mock.patch("cs2_inventory.inventory_engine.http_get_json_with_headers", side_effect=fake_get):
            result = fetch_steamwebapi_raw_inventory(
                "76561198000000000",
                key="fake-key",
                mode="0",
                parse="0",
                samples=1,
                rate_gap=0,
                timeout=10,
            )
        self.assertEqual([page.item_count for page in result.pages], [1, 1])
        self.assertEqual(call_params[0].get("start_assetid"), None)
        self.assertEqual(call_params[1].get("start_assetid"), "a1")

    def test_same_assetid_from_different_sources_is_deduplicated(self):
        merged = merge_asset_records(
            [
                [
                    self._asset("dup", "c1", "0", "Skin D", sources=("steamwebapi:parse=0:mode=2",)),
                ],
                [
                    self._asset("dup", "c1", "0", "Skin D", sources=("steamwebapi:parse=0:mode=1",)),
                ],
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(
            tuple(sorted(merged[0].sources)),
            ("steamwebapi:parse=0:mode=1", "steamwebapi:parse=0:mode=2"),
        )

    def test_parsed_payload_keeps_protected_flags(self):
        payload = [
            {
                "markethashname": "FAMAS | Dark Water (Minimal Wear)",
                "assetid": "53192582604",
                "classid": "7993038103",
                "instanceid": "8734226635",
                "count": 1,
                "tradeprotected": True,
                "tradelocked": True,
            }
        ]
        records = asset_records_from_parsed_payload(payload, source="fixture")
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0].tradeprotected)
        self.assertTrue(records[0].tradelocked)


if __name__ == "__main__":
    unittest.main()


