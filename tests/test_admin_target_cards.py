"""共享订阅命名、可见边界及按页批量库存回归；只使用本地临时库。"""
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import event

from cs2_inventory.database import db
from cs2_inventory.models import (
    ScanBatch,
    ScanJob,
    Snapshot,
    SnapshotItem,
    SteamTarget,
    Subscription,
    User,
)
from tests.test_admin_workspace import ADMIN, MEMBER, PASSWORD, fixture


def snapshot(target, at, protected, ordinary, *, partial=False):
    contexts = {key: {"complete": key == "protected" or not partial, "scanned_at": at.isoformat()}
                for key in ("protected", "other")}
    q = {"source_policy": "api_preferred_v2", "comparable": not partial, "api_contexts": contexts}
    row = Snapshot(target_id=target.id, scanned_at=at, coverage="partial" if partial else "complete",
                   total_items=protected+ordinary, item_types=2,
                   payload_gzip=gzip.compress(json.dumps({"quality": q}).encode()))
    db.session.add(row)
    db.session.flush()
    for amount, section in ((protected, "protected"), (ordinary, "other")):
        row.items.append(SnapshotItem(asset_key=section, name="样例", amount=amount,
                                      is_trade_protected=section == "protected", first_seen_at=at,
                                      evidence_json=json.dumps({"api_contexts": [section]})))
    return row


class AdminCardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = fixture(Path(self.temp.name))
        self.client = self.app.test_client()
        with self.app.app_context():
            admin, member = [User.query.filter_by(username=x).one() for x in (ADMIN, MEMBER)]
            other = User(username="second_subscriber", role="user", account_kind="internal", password_hash=member.password_hash)
            db.session.add(other)
            first, second, fallback, self_only = SteamTarget.query.order_by(SteamTarget.id).limit(4)
            fallback.persona_name = None
            self.tid, self.second, self.fallback, self.self_only = first.id, second.id, fallback.id, self_only.id
            db.session.add_all([
                Subscription(target=first, user=member, remark="  共同_仓%  "),
                Subscription(target=first, user=other, remark="重复_名"),
                Subscription(target=first, user=admin, remark="管理员自己的备注"),
                Subscription(target=second, user=other, remark="早订阅"),
                Subscription(target=second, user=member, remark="晚订阅"),
                Subscription(target=second, user=admin, remark="  "),
                Subscription(target=fallback, user=other, remark="  "),
                Subscription(target=self_only, user=member, remark="客户私有"),
            ])
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def login(self, who):
        token = self.client.get("/api/bootstrap").json["csrf_token"]
        resp = self.client.post("/api/auth/login", json={"username": who, "password": PASSWORD},
                                headers={"X-CSRF-Token": token})
        self.assertEqual(resp.status_code, 200)

    def search(self, q):
        return self.client.get("/api/admin/targets", query_string={"q": q, "page": 99}).json

    def test_admin_identity_search_and_non_admin_isolation(self):
        self.login(ADMIN)
        for term in ("共同_仓%", "76561198000000000", "本地验收 00", "重复_名", "管理员自己的备注"):
            with self.subTest(term=term):
                response = self.search(term)
                self.assertEqual((response["total"], response["page"]), (1, 1))
                card = response["items"][0]
                self.assertEqual((card["label"], card["remark"], card["subscriber_count"]), ("管理员自己的备注", "管理员自己的备注", 3))
                self.assertEqual(card["remarks"], [
                    {"username": ADMIN, "remark": "管理员自己的备注"},
                    {"username": MEMBER, "remark": "共同_仓%"},
                    {"username": "second_subscriber", "remark": "重复_名"},
                ])
        self.assertEqual(self.search("晚订阅")["items"][0]["label"], "早订阅")
        self.assertEqual(self.search("76561198000000002")["items"][0]["label"], "76561198000000002")
        self.assertEqual(self.search("76561198000000004")["items"][0]["label"], "本地验收 04")
        self.assertEqual(self.search("共同a仓%")["total"], 0)
        detail = self.client.get(f"/api/monitors/{self.tid}?source=admin").json["monitor"]
        self.assertEqual((detail["label"], detail["remark"]), ("管理员自己的备注", "管理员自己的备注"))
        with self.app.app_context():
            self.assertEqual(db.session.get(SteamTarget, self.tid).persona_name, "本地验收 00")
        self.login(MEMBER)
        private = self.client.get("/api/monitors", query_string={"q":"共同_仓%"}).json["items"][0]
        self.assertEqual(private["remark"].strip(), "共同_仓%")
        self.assertNotIn("remarks", private)
        scoped = self.client.get(f"/api/monitors/{self.tid}?source=admin").json["monitor"]
        self.assertEqual(scoped["remark"].strip(), "共同_仓%")
        self.assertNotIn("remarks", scoped)
        self.assertEqual(self.client.get("/api/admin/targets?q=管理员自己的备注").status_code, 403)

    def test_inventory_unknown_values_and_real_baseline_match_personal_cards(self):
        self.login(ADMIN)
        with self.app.app_context():
            first = db.session.get(SteamTarget, self.tid)
            start = datetime(2026, 11, 17, 13, 0, tzinfo=timezone.utc)
            snapshot(first, start, 2, 5)
            snapshot(first, start+timedelta(days=1), 4, 6)
            snapshot(first, start+timedelta(days=2), 9, 0, partial=True)
            db.session.commit()
        admin = self.search("共同_仓%")["items"][0]
        self.assertEqual(admin["total_items"], 10)
        self.assertEqual(admin["inventory_sections"]["protected"]["total_items"], 9)
        self.assertEqual(admin["inventory_sections"]["other"]["total_items"], 6)
        self.assertEqual(admin["protected_additions"], {"1": 5, "7": None})
        self.assertFalse(admin["sections_same_complete_round"])
        empty = self.search("早订阅")["items"][0]
        self.assertIsNone(empty["total_items"])
        self.assertIsNone(empty["inventory_sections"]["protected"]["total_items"])
        self.login(MEMBER)
        personal = self.client.get("/api/monitors", query_string={"q":"共同_仓%"}).json["items"][0]
        for field in ("inventory_sections", "total_items", "protected_additions", "last_complete_snapshot_at"):
            self.assertEqual(personal[field], admin[field])

    def test_populated_page_query_count_is_independent_of_number_of_targets(self):
        self.login(ADMIN)
        with self.app.app_context():
            date = datetime(2026, 11, 19, 2, tzinfo=timezone.utc)
            for target in SteamTarget.query.all():
                snapshot(target, date, 3, 2)
            db.session.commit()
            selects = []
            def capture(conn, cursor, stmt, *_):
                if stmt.lstrip().upper().startswith("SELECT"):
                    selects.append(stmt)
            event.listen(db.engine, "before_cursor_execute", capture)
            try:
                few = self.client.get("/api/admin/targets?q=早订阅").json
                single_count = len(selects)
                selects.clear()
                many = self.client.get("/api/admin/targets").json
            finally:
                event.remove(db.engine, "before_cursor_execute", capture)
            self.assertEqual((len(few["items"]), len(many["items"])), (1, 20))
            self.assertEqual(single_count, len(selects))
            self.assertLessEqual(single_count, 13)
            self.assertTrue(all(item["inventory_sections"]["protected"]["total_items"] == 3 for item in many["items"]))

    def test_historical_batch_and_subscriber_scopes_intersect_before_pagination(self):
        with self.app.app_context():
            targets = SteamTarget.query.order_by(SteamTarget.id).all()
            member = User.query.filter_by(username=MEMBER).one()
            active = ScanBatch(status="completed_with_errors",
                               created_at=datetime(2026, 11, 16, 16, 10, tzinfo=timezone.utc))
            other_batch = ScanBatch(status="completed_with_errors")
            empty_batch = ScanBatch(status="completed")
            db.session.add_all([active, other_batch, empty_batch])
            db.session.flush()
            for target in targets[:22]:
                target.scan_status = "ready"  # Later success does not erase this batch's failed job.
                db.session.add(ScanJob(kind="daily", steamid=target.steamid, target_id=target.id,
                                       batch_id=active.id, status="failed"))
            last = targets[-1]
            last.scan_status = "failed"
            db.session.add_all([
                ScanJob(kind="daily", steamid=last.steamid, target_id=last.id, batch_id=active.id, status="completed"),
                ScanJob(kind="daily", steamid=last.steamid, target_id=last.id, batch_id=other_batch.id, status="failed"),
                ScanJob(kind="daily", steamid=targets[0].steamid, target_id=targets[0].id, batch_id=active.id, status="failed"),
            ])
            db.session.commit()
            active_id, empty_id, uid = active.id, empty_batch.id, member.id
        url = "/api/admin/targets"
        # The admin gate is checked before either scope can disclose the existence of accounts or batches.
        self.assertEqual(self.client.get(url, query_string={"subscriber_id": uid}).status_code, 401)
        self.login(MEMBER)
        self.assertEqual(self.client.get(url, query_string={"failed_batch": active_id}).status_code, 403)
        self.login(ADMIN)
        def get(**params):
            return self.client.get(url, query_string=params)

        data = get(failed_batch=active_id, page=99).json
        self.assertEqual((data["total"], data["page"], data["pages"], len(data["items"])), (22, 2, 2, 2))
        self.assertEqual(data["scope"], {"failed_batch": {"id": active_id, "label": "2026-11-17 00:10 扫描"}, "subscriber": None})
        selected = get(failed_batch=active_id, subscriber_id=uid, q="共同_仓%", page=2).json
        self.assertEqual((selected["total"], selected["page"], selected["items"][0]["id"]), (1, 1, self.tid))
        self.assertEqual(selected["scope"]["subscriber"], {"id": uid, "username": MEMBER})
        customer = get(subscriber_id=uid).json
        self.assertEqual(customer["total"], 3)
        self.assertEqual(get(failed_batch=empty_id, subscriber_id=uid).json["items"], [])
        for param in ("failed_batch", "subscriber_id"):
            self.assertEqual(get(**{param: 9999999}).status_code, 404)
            for bad in ("", "-2", "abc", "2.2", "9"*30):
                self.assertEqual(get(**{param: bad}).status_code, 400)

    def test_selected_customer_remark_matches_detail_and_does_not_replace_global_name(self):
        self.login(ADMIN)
        with self.app.app_context():
            admin_id = User.query.filter_by(username=ADMIN).one().id
            member_id = User.query.filter_by(username=MEMBER).one().id
        def listing(uid, term):
            return self.client.get("/api/admin/targets", query_string={"subscriber_id": uid, "q": term}).json["items"][0]
        def opening(tid, uid):
            return self.client.get(f"/api/monitors/{tid}", query_string={"source": "admin", "subscriber_id": uid})
        member = listing(member_id, "共同_仓%")
        self.assertEqual(member["label"], "共同_仓%")
        self.assertEqual(member["remarks"][0], {"remark": "共同_仓%", "username": MEMBER})
        self.assertEqual(opening(self.tid, member_id).json["monitor"]["label"], member["label"])
        self.assertEqual(self.search("共同_仓%")["items"][0]["label"], "管理员自己的备注")
        # The selected administrator subscribes to the second target but assigned only blank space.
        no_name = listing(admin_id, "晚订阅")
        self.assertEqual((no_name["label"], no_name["remark"]), ("Needle_5% Alpha", None))
        self.assertEqual(opening(self.second, admin_id).json["monitor"]["label"], "Needle_5% Alpha")
        # An old customer-specific bookmark must not silently show some other subscriber's alias.
        self.assertEqual(opening(self.self_only, admin_id).status_code, 404)
        self.assertEqual(opening(self.tid, "bad").status_code, 400)
        self.login(MEMBER)
        private = opening(self.tid, admin_id).json["monitor"]
        self.assertEqual(private["remark"].strip(), "共同_仓%")
        self.assertNotIn("remarks", private)

    def test_cross_page_orders_have_stable_ties_and_read_only_the_selected_inventory_page(self):
        self.login(ADMIN)
        now = datetime(2026, 11, 20, 2, tzinfo=timezone.utc)
        with self.app.app_context():
            targets = SteamTarget.query.order_by(SteamTarget.id).all()
            for i, target in enumerate(targets):
                target.created_at = now+timedelta(seconds=i)
            targets[0].last_scan_at, targets[1].last_scan_at = now, now+timedelta(minutes=2)
            targets[0].last_success_at, targets[1].last_success_at = now, now-timedelta(minutes=2)
            db.session.commit()
        def ordered(kind, **params):
            return self.client.get("/api/admin/targets", query_string={"order": kind, **params})
        with self.app.app_context():
            selects = []
            def save(conn, cursor, stmt, args, *_):
                if "from snapshots " in stmt.replace("\n", " ").lower():
                    selects.append(args)
            event.listen(db.engine, "before_cursor_execute", save)
            try:
                popular = ordered("subscribers").json
            finally:
                event.remove(db.engine, "before_cursor_execute", save)
        self.assertEqual([r["id"] for r in popular["items"][:2]], [self.second, self.tid])
        self.assertEqual([r["subscriber_count"] for r in popular["items"][:2]], [3, 3])
        self.assertEqual(set(selects[0][:-2]), {r["id"] for r in popular["items"]})
        self.assertEqual(len(selects), 1)  # Ranking 45 targets only reads snapshots for the 20 requested cards.
        recent = ordered("recent_scan").json
        self.assertEqual(recent["order_time_field"], "last_success_at")
        self.assertEqual([r["id"] for r in recent["items"][:2]], [self.tid, self.second])
        self.assertEqual({r["last_success_at"] for r in ordered("recent_scan", page=99).json["items"]}, {None})
        self.assertNotIn("last_scan_at", recent["items"][0])
        self.assertNotIn(self.tid, [r["id"] for r in ordered("newest").json["items"]])
        self.assertEqual(ordered("bogus").status_code, 400)
        from tests.session_fixture import sign_in_fixture
        with self.app.app_context():
            sign_in_fixture(self.client, User.query.filter_by(username="cs2inventory_admin").one())
        admin = ordered("recent_scan").json
        self.assertEqual(admin["order_time_field"], "last_scan_at")
        self.assertEqual([r["id"] for r in admin["items"][:2]], [self.second, self.tid])
