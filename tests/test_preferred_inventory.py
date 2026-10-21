"""API 优先、公开展示兜底、超级管理员主动查询与并发去重。"""
import gzip
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest import mock

from cs2_inventory import inventory_engine as engine
from cs2_inventory.app import create_app
from cs2_inventory.database import db
from cs2_inventory.inventory_views import inventory_view
from cs2_inventory.models import QuotaUsage, ScanJob, Snapshot, User, utcnow
from cs2_inventory.query_jobs import enqueue_queries
from cs2_inventory.services import (
    prune_expired,
    snapshot_diff,
    snapshot_public,
    store_snapshot,
)
from cs2_inventory.steam_visible import fetch_visible_inventory
from cs2_inventory.unified import unify_inventory
from tests import test_api_inventory as fixtures


def visible(ids=("201",), total=None, **extra):
    return {**fixtures.ordinary(ids), "success": 1,
            "total_inventory_count": len(ids) if total is None else total, **extra}


class VisibleAdapterTests(unittest.TestCase):
    def fetch(self, *pages):
        with mock.patch.object(engine, "http_get_json", side_effect=pages) as request:
            rows, context = fetch_visible_inventory("76561198000000001")
        return rows, context, request

    def test_complete_visible_and_never_infers_protection(self):
        page = visible()
        page["descriptions"][0].update(tradable=False, tradeprotected=True)
        rows, context, _ = self.fetch(page)
        self.assertTrue(context["displayable"])
        self.assertTrue(context["inventory_complete"])
        self.assertFalse(context["complete"])
        self.assertTrue(all(not row.tradeprotected for row in rows))

    def test_partial_visible_subset_is_display_only(self):
        rows, context, _ = self.fetch(visible(total=4))
        self.assertEqual(len(rows), 1)
        self.assertTrue(context["displayable"])
        self.assertFalse(context["inventory_complete"])

    def test_empty_requires_explicit_zero_and_success(self):
        for page, accepted in [(visible((), 0), True), (visible((), 3), False),
                               ({"success": 1, "total_inventory_count": 0}, True),
                               ({"assets": [], "total_inventory_count": 0}, False),
                               ({"success": False, "assets": [], "total_inventory_count": 0}, False)]:
            with self.subTest(page=page):
                rows, context, _ = self.fetch(page)
                self.assertEqual(context["displayable"], accepted)
                self.assertEqual(rows, [])

    def test_complete_pagination_and_dedup(self):
        rows, context, request = self.fetch(visible(("201",), 2, more_items=1, last_assetid="201"),
                                             visible(("201", "202"), 2))
        self.assertEqual(len(rows), 2)
        self.assertTrue(context["inventory_complete"])
        self.assertEqual(request.call_args_list[1].args[1]["start_assetid"], "201")

    def test_repeated_cursor_failure_discards_incomplete_pages(self):
        page = visible(total=2, more_items=1, last_assetid="201")
        rows, context, _ = self.fetch(page, page)
        self.assertEqual(rows, [])
        self.assertFalse(context["displayable"])

    def test_bad_metadata_and_transport_failures_are_not_zero(self):
        for page in (None, {"success": 1, "assets": "bad"}, visible(total=0),
                     engine.SteamQueryError("HTTP 403: private"),
                     engine.SteamQueryError("HTTP 429: limited"), TimeoutError("timeout")):
            with self.subTest(page=page):
                rows, context, _ = self.fetch(page)
                self.assertFalse(context["displayable"])
                self.assertIsNone(context["scanned_at"])
                self.assertEqual(rows, [])

    def test_missing_name_preserves_real_asset(self):
        page = visible()
        page["descriptions"] = []
        rows, context, _ = self.fetch(page)
        self.assertTrue(context["displayable"])
        self.assertEqual(rows[0].assetid, "201")


class PreferredInventoryTests(unittest.TestCase):
    setUp = fixtures.ApiInventoryTests.setUp
    tearDown = fixtures.ApiInventoryTests.tearDown
    query = fixtures.ApiInventoryTests.query
    save = fixtures.ApiInventoryTests.save
    client = fixtures.ApiInventoryTests.client
    assert_business = fixtures.ApiInventoryTests.assert_business

    def fallback_query(self, page, first=None):
        with mock.patch("cs2_inventory.api_inventory.fetch_visible_inventory", wraps=fetch_visible_inventory), \
             mock.patch.object(engine, "http_get_json", return_value=page):
            return self.query(first=first, second=engine.SteamQueryError('HTTP 411: {"error":"NO_ITEMS"}'))[0]

    def post(self, user, path, body):
        client = self.client(user)
        csrf = client.get("/api/bootstrap").json["csrf_token"]
        return client.post(path, json=body, headers={"X-CSRF-Token": csrf})

    def test_fallback_never_complete_and_keeps_paid_requests(self):
        result = self.fallback_query(visible(total=4))
        self.assertEqual(result["quality"]["source_policy"], "api_preferred_v2")
        self.assertFalse(result["quality"]["comparable"])
        self.assertEqual(result["sources"]["other"]["provider_requests"], 1)
        row = store_snapshot(self.target, unify_inventory(result))
        db.session.commit()
        view = inventory_view(self.target.id, self.user)
        self.assertEqual(view["inventory_sections"]["protected"]["total_items"], 1)
        self.assertEqual(view["inventory_sections"]["other"]["total_items"], 1)
        self.assertIsNone(view["total_items"])
        self.assertFalse(view["sections_same_complete_round"])
        self.assertIsNone(snapshot_public(row)["total_items"])

    def test_new_visible_result_replaces_instead_of_reviving_history(self):
        self.save(second=fixtures.ordinary(("202", "203")))
        row = store_snapshot(self.target, unify_inventory(self.fallback_query(visible(("201",), 4))))
        db.session.commit()
        self.assertEqual({x.asset_key for x in row.items}, {"101", "201"})
        self.assertEqual(inventory_view(self.target.id, self.user)["inventory_sections"]["other"]["total_items"], 1)

    def test_failed_protection_keeps_timestamp_and_no_cross_round_total(self):
        self.save()
        before = inventory_view(self.target.id, self.user)
        result = self.fallback_query(visible(), first=engine.SteamQueryError("HTTP 403: PRIVATE"))
        store_snapshot(self.target, unify_inventory(result))
        db.session.commit()
        after = inventory_view(self.target.id, self.user)
        self.assertEqual(before["inventory_sections"]["protected"], after["inventory_sections"]["protected"])
        self.assertFalse(after["sections_same_complete_round"])

    def test_old_v1_is_readable_but_not_v2_comparison_baseline(self):
        result, _ = self.query()
        result["quality"]["source_policy"] = "api_only_v1"
        old = store_snapshot(self.target, unify_inventory(result))
        old.scanned_at = utcnow() - timedelta(days=1)
        db.session.commit()
        current = self.save()
        self.assertFalse(snapshot_diff(current, old)["comparable"])
        data = self.client(self.user).get(f"/api/monitors/{self.target.id}/compare?days=1").json
        self.assertIsNone(data["baseline"])

    def test_only_super_admin_can_submit_and_no_rejected_charge(self):
        for user in (self.user, self.other_admin):
            for path in ("/api/admin/query", "/api/admin/queries", f"/api/admin/targets/{self.target.id}/scan"):
                self.assertEqual(self.post(user, path, {"steamid": self.target.steamid}).status_code, 403)
            self.assertFalse(self.client(user).get("/api/bootstrap").json["user"]["can_query_inventory"])
        self.assertEqual(ScanJob.query.count(), 0)
        self.assertEqual(QuotaUsage.query.count(), 0)
        self.admin.is_active = False
        db.session.commit()
        self.assertEqual(self.post(self.admin, "/api/admin/query", {"steamid": self.target.steamid}).status_code, 401)

    def test_batch_validation_atomic_dedup_and_alias(self):
        body = {"items": [{"steamid": self.target.steamid}, {"steamid": "bad"}]}
        self.assertEqual(self.post(self.admin, "/api/admin/queries", body).status_code, 400)
        self.assertEqual(ScanJob.query.count(), 0)
        body = {"items": [{"steamid": self.target.steamid, "alias": "我的备注"}] * 2}
        first = self.post(self.admin, "/api/admin/queries", body)
        second = self.post(self.admin, "/api/admin/queries", body)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(len(first.json["items"]), 1)
        self.assertEqual(first.json["job_id"], second.json["job_id"])
        self.assertEqual(second.json["estimated_requests"], 0)
        self.assertEqual(ScanJob.query.count(), 1)
        self.assertEqual(json.loads(ScanJob.query.one().result_json)["query_alias"], "我的备注")

    def test_batch_limit_and_instant_expiry_access(self):
        body = {"items": [{"steamid": f"7656119800000{i:04}"} for i in range(21)]}
        self.assertEqual(self.post(self.admin, "/api/admin/queries", body).status_code, 400)
        response = self.post(self.admin, "/api/admin/query", {"steamid": "76561198000000999"})
        job = db.session.get(ScanJob, response.json["job_id"])
        self.assertIsNone(job.target_id)
        self.assertEqual(job.kind, "instant")
        for user in (self.user, self.other_admin):
            self.assertEqual(self.client(user).get(f"/api/jobs/{job.id}").status_code, 404)
            self.assertEqual(self.client(user).get("/api/admin/queries").status_code, 403)
        job.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        self.assertEqual(self.client(self.admin).get(f"/api/jobs/{job.id}").status_code, 410)

    def test_metadata_repair_and_business_response_do_not_leak(self):
        row = store_snapshot(self.target, unify_inventory(self.fallback_query(visible(total=4))))
        db.session.commit()
        before = json.loads(gzip.decompress(row.payload_gzip))["quality"]
        row.payload_gzip = fixtures._snapshot_payload(row)
        self.assertEqual(json.loads(gzip.decompress(row.payload_gzip))["quality"], before)
        for user in (self.user, self.other_admin):
            for path in (f"/api/monitors/{self.target.id}", f"/api/monitors/{self.target.id}/snapshots/{row.id}",
                         f"/api/monitors/{self.target.id}/compare?days=1", "/api/dashboard"):
                self.assert_business(self.client(user).get(path).json)

    def test_retention_keeps_last_partition_and_complete_display_dependencies(self):
        old = self.save()
        old.scanned_at = utcnow() - timedelta(days=10)
        result = self.fallback_query(visible(total=4), first=engine.SteamQueryError("HTTP 403: PRIVATE"))
        current = store_snapshot(self.target, unify_inventory(result))
        db.session.commit()
        prune_expired()
        self.assertIsNotNone(db.session.get(Snapshot, old.id))
        self.assertIsNotNone(db.session.get(Snapshot, current.id))
        view = inventory_view(self.target.id, self.user)
        self.assertEqual(view["last_complete_snapshot_id"], old.id)
        self.assertEqual(view["inventory_sections"]["protected"]["total_items"], 1)

    def test_worker_charges_failed_api_before_public_fallback_and_no_extra_credits(self):
        from cs2_inventory.worker import process_job
        job = ScanJob(steamid=self.target.steamid, target_id=self.target.id, kind="manual", status="running",
                      requested_by=self.admin.id)
        db.session.add(job)
        db.session.commit()

        def paid(url, params, **kwargs):
            kwargs["request_observer"]()
            if params["trade_locked"] == "1":
                return fixtures.protected(), fixtures.LIVE
            raise engine.SteamQueryError('HTTP 411: {"error":"NO_ITEMS"}')

        def public(*args, **kwargs):
            self.assertEqual(sum(row.credits for row in QuotaUsage.query.all()), 6)
            return visible(total=4)

        with mock.patch.object(engine, "http_get_json_with_headers", side_effect=paid), \
             mock.patch.object(engine, "http_get_json", side_effect=public), \
             mock.patch("cs2_inventory.api_inventory.fetch_visible_inventory", wraps=fetch_visible_inventory):
            process_job(job.id)
        self.assertEqual(job.status, "completed")
        self.assertEqual(sum(row.credits for row in QuotaUsage.query.all()), 6)

    def test_instant_failed_partition_keeps_unexpired_success_time(self):
        from cs2_inventory.worker import instant_view
        first, _ = self.query()
        before = ScanJob(steamid=self.target.steamid, kind="instant", status="completed",
                         expires_at=utcnow() + timedelta(hours=1))
        db.session.add(before)
        db.session.flush()
        before.result_json = json.dumps(instant_view(before, unify_inventory(first), "旧结果"))
        job = ScanJob(steamid=self.target.steamid, kind="instant", expires_at=utcnow() + timedelta(hours=1))
        db.session.add(job)
        db.session.flush()
        partial, _ = self.query(second=engine.SteamQueryError("HTTP 429: limited"))
        result = instant_view(job, unify_inventory(partial), "新查询")
        self.assertEqual(result["inventory_sections"]["other"], json.loads(before.result_json)["inventory_sections"]["other"])
        self.assertFalse(result["sections_same_complete_round"])
        self.assertIsNone(result["total_items"])
        before.expires_at = utcnow() - timedelta(seconds=1)
        db.session.flush()
        result = instant_view(job, unify_inventory(partial), "新查询")
        self.assertIsNone(result["inventory_sections"]["other"]["updated_at"])


class ConcurrentQueueTests(unittest.TestCase):
    def test_two_database_connections_reuse_one_job(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True, "STATE_DIR": Path(directory),
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(Path(directory) / "fixture.db")})
            barrier = threading.Barrier(2)

            def submit():
                with app.app_context():
                    user = User.query.filter_by(username="cs2inventory_admin").one()
                    barrier.wait(timeout=10)
                    result = enqueue_queries(user, [("76561198000000999", "fixture")])
                    db.session.commit()
                    return result[0]["job_id"]

            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = list(pool.map(lambda _: submit(), range(2)))
            self.assertEqual(jobs[0], jobs[1])
            with app.app_context():
                self.assertEqual(ScanJob.query.count(), 1)
                db.session.remove()
                db.engine.dispose()
