"""卡片近1/7天加仓展开：按相同基线通算，仅按目标读已有库存。"""
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import event

from cs2_inventory.database import db
from cs2_inventory.models import ScanJob, Snapshot, SnapshotItem, SteamTarget
from tests.test_admin_workspace import ADMIN, MEMBER, PASSWORD, fixture

NOW = datetime(2026, 11, 19, 6, 1, tzinfo=timezone.utc)


def add_snapshot(target, t, entries):
    ctx = {s: {"complete": True, "scanned_at": t.isoformat()} for s in ("protected", "other")}
    quality = {"comparable": True, "source_policy": "api_preferred_v2", "api_contexts": ctx}
    row = Snapshot(target=target, scanned_at=t, coverage="complete", total_items=sum(x[3] for x in entries),
                   item_types=len(entries), payload_gzip=gzip.compress(json.dumps({"quality": quality}).encode()))
    db.session.add(row)
    for key, title, market, amount, held, context, first_seen in entries:
        row.items.append(SnapshotItem(asset_key=key, name=title, amount=amount, first_seen_at=first_seen,
                                      is_trade_protected=held, evidence_json=json.dumps({"market_name": market,
                                                                                       "api_contexts": [context]})))


class TargetAdditionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = fixture(Path(self.temp.name))
        self.client = self.app.test_client()
        prev = NOW-timedelta(days=1)
        older = NOW-timedelta(days=8)
        def entry(key, name, count, *, market=None, held=True, context="protected", first=NOW):
            return key, name, market or name, count, held, context, first
        with self.app.app_context():
            a, b, c = SteamTarget.query.order_by(SteamTarget.id).limit(3)
            self.ids = [t.id for t in (a, b, c)]
            add_snapshot(a, prev, [entry("existing", "红线", 3, market="Redline", first=older),
                                   entry("moved", "换区", 4, held=False, context="other", first=older)])
            add_snapshot(a, NOW, [entry("existing", "红线", 5, market="Redline", first=older),
                                  entry("new", "红线", 3, market="Redline"),
                                  entry("a", "同名", 2, market="Variant A"), entry("b", "同名", 1, market="Variant B"),
                                  entry("moved", "换区", 4, first=older), entry("returned", "回流", 12, first=older),
                                  entry("synthetic:x", "虚拟标识", 10), entry("unproven", "无保护证据", 22, context="other"),
                                  entry("ordinary", "不受保护", 100, held=False, context="other")])
            foreign = [entry("foreign", "另一目标", 4, first=older)]
            add_snapshot(b, prev, foreign)
            add_snapshot(b, NOW, foreign)
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def login(self, name):
        token = self.client.get("/api/bootstrap").json["csrf_token"]
        self.assertEqual(self.client.post("/api/auth/login", json={"username": name, "password": PASSWORD},
                                         headers={"X-CSRF-Token": token}).status_code, 200)

    def uri(self, index=0):
        return f"/api/admin/targets/{self.ids[index]}/protected-additions"

    def test_additions_share_card_count_exclude_old_assets_and_never_enqueue(self):
        self.login(ADMIN)
        seen = []
        with self.app.app_context():
            def capture(conn, cursor, statement, parameters, *_):
                seen.append((statement.lower(), parameters))
            jobs = ScanJob.query.count()
            event.listen(db.engine, "before_cursor_execute", capture)
            try:
                response = self.client.get(self.uri()+"?days=1")
            finally:
                event.remove(db.engine, "before_cursor_execute", capture)
            self.assertEqual(ScanJob.query.count(), jobs)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("Cookie", response.vary)
        report = response.json
        self.assertEqual((report["state"], report["days"], report["total"]), ("ready", 1, 8))
        self.assertEqual(report["baseline_at"], "2026-11-18T14:01:00+08:00")
        self.assertEqual(report["observed_at"], "2026-11-19T14:01:00+08:00")
        self.assertEqual([(x["name"], x["market_name"], x["count"]) for x in report["items"]],
                         [("红线", "Redline", 5), ("同名", "Variant A", 2), ("同名", "Variant B", 1)])
        card = self.client.get("/api/admin/targets?q=76561198000000000").json["items"][0]
        self.assertEqual(card["protected_additions"]["1"], sum(item["count"] for item in report["items"]))
        snap_reads = [(sql, args) for sql, args in seen if "from snapshots " in sql.replace("\n", " ")]
        self.assertEqual(len(snap_reads), 1)
        self.assertIn("snapshots.target_id = ?", snap_reads[0][0])
        self.assertEqual(snap_reads[0][1][0], self.ids[0])

    def test_valid_zero_is_not_missing_and_missing_is_not_an_error(self):
        self.login(ADMIN)
        zero = self.client.get(self.uri(1)+"?days=1").json
        self.assertEqual((zero["state"], zero["total"], zero["items"]), ("ready", 0, []))
        missing = self.client.get(self.uri()+"?days=7").json
        self.assertEqual((missing["state"], missing["total"], missing["baseline_at"]), ("no_baseline", None, None))
        no_stock = self.client.get(self.uri(2)+"?days=1").json
        self.assertEqual((no_stock["state"], no_stock["total"], no_stock["observed_at"]), ("no_observation", None, None))
        with self.app.app_context():
            target = db.session.get(SteamTarget, self.ids[0])
            add_snapshot(target, NOW-timedelta(days=7), [])
            db.session.commit()
        week = self.client.get(self.uri()+"?days=7").json
        card = self.client.get("/api/admin/targets?q=76561198000000000").json["items"][0]
        self.assertEqual((week["state"], week["days"], week["total"]), ("ready", 7, card["protected_additions"]["7"]))
        self.assertEqual(week["total"], sum(x["count"] for x in week["items"]))

    def test_access_and_period_are_enforced(self):
        self.assertEqual(self.client.get(self.uri()).status_code, 401)
        self.login(MEMBER)
        self.assertEqual(self.client.get(self.uri()).status_code, 403)
        self.login(ADMIN)
        for days in ("0", "3", "x", ""):
            self.assertEqual(self.client.get(self.uri(), query_string={"days": days}).status_code, 400)
        self.assertEqual(self.client.get("/api/admin/targets/99999/protected-additions").status_code, 404)
