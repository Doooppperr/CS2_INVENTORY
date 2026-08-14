# -*- coding: utf-8 -*-
import collections
import unittest

from cs2_inventory.inventory_engine import (
    APPID_CS2,
    CONTEXTID_CS2,
    TRADE_PROTECTION_SECONDS,
    build_cookie_header,
    counter_by_name,
    inventory_items_from_payload,
    parse_history_html_payload,
    protected_items_from_trade_payloads,
    merge_public_missing_third_party_items,
    remove_public_visible_false_protected,
    render_report,
    steamwebapi_items_from_payload,
    InventoryItem,
    ProtectedItem,
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


if __name__ == "__main__":
    unittest.main()
