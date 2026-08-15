from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from werkzeug.security import generate_password_hash

from cs2_inventory.app import create_app
from cs2_inventory.cli import enqueue_daily
from cs2_inventory.database import db
from cs2_inventory.models import (
    ScanBatch,
    ScanJob,
    Snapshot,
    SnapshotItem,
    SteamTarget,
    Subscription,
    User,
    beijing_iso,
    utcnow,
)
from cs2_inventory.services import (
    add_monitor,
    prune_expired,
    snapshot_diff,
    snapshot_public,
    store_snapshot,
)
from cs2_inventory.unified import unify_inventory
from cs2_inventory.worker import (
    process_job,
    profile_refresh_due,
    recover_interrupted_jobs,
)


class PlatformTests(unittest.TestCase):
    def test_beijing_timestamp_serialization(self):
        expected = "2026-08-14T18:00:00+08:00"
        self.assertEqual(beijing_iso(datetime(2026, 8, 14, 10, 0, 0)), expected)
        self.assertEqual(
            beijing_iso(datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc)),
            expected,
        )

    def test_profile_refresh_accepts_sqlite_naive_datetime(self):
        self.assertFalse(profile_refresh_due(datetime.now(), days=7))
        self.assertTrue(profile_refresh_due(datetime.now() - timedelta(days=8), days=7))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "STATE_DIR": root,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(root / 'test.db').as_posix()}",
            "SECRET_KEY": "test-secret",
            "PASSWORD_VAULT_KEY": "test-password-vault-key",
            "SESSION_COOKIE_PATH": "/",
            "STEAMWEBAPI_KEY": "test-key",
            "OBSERVATION_CACHE": str(root / "observations.json"),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            test_hash = generate_password_hash("platform-test-password")
            User.query.filter(User.username.in_(["cs2inventory_admin", "cs2inventory_user"])).update(
                {User.password_hash: test_hash}, synchronize_session=False
            )
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def csrf(self):
        return self.client.get("/api/bootstrap").get_json()["csrf_token"]

    def login(self, username="cs2inventory_user", password="platform-test-password"):
        response = self.client.post("/api/auth/login", json={"username": username, "password": password}, headers={"X-CSRF-Token": self.csrf()})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["csrf_token"]

    def test_bootstrap_accounts_are_hashed_and_three_monitors_seeded(self):
        with self.app.app_context():
            admin = User.query.filter_by(username="cs2inventory_admin").one()
            user = User.query.filter_by(username="cs2inventory_user").one()
            self.assertEqual(admin.role, "admin")
            self.assertTrue(admin.password_hash.startswith("scrypt:"))
            self.assertTrue(user.password_hash.startswith("scrypt:"))
            self.assertEqual({row.target.steamid for row in user.subscriptions}, {
                "76561198441561382", "76561199771254049", "76561198413577373"
            })

    def test_open_registration_and_login(self):
        token = self.csrf()
        response = self.client.post("/api/auth/register", json={"username": "new_user", "password": "long-pass-123"}, headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 201)
        response = self.client.post("/api/auth/login", json={"username": "new_user", "password": "long-pass-123"}, headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)

    def test_shared_target_is_deduplicated(self):
        with self.app.app_context():
            second = User(username="second", password_hash="hash", role="user")
            db.session.add(second)
            db.session.commit()
            target, job, created = add_monitor(second, "76561198441561382")
            self.assertFalse(created)
            self.assertIsNotNone(job)
            self.assertEqual(SteamTarget.query.filter_by(steamid=target.steamid).count(), 1)
            self.assertEqual(Subscription.query.filter_by(target_id=target.id).count(), 2)

    def test_monitor_api_is_paginated_at_twenty(self):
        self.login()
        with self.app.app_context():
            user = User.query.filter_by(username="cs2inventory_user").one()
            for index in range(18):
                add_monitor(user, f"7656119{1000000000 + index:010d}")
        response = self.client.get("/api/monitors?page=1")
        data = response.get_json()
        self.assertEqual(data["per_page"], 20)
        self.assertEqual(len(data["items"]), 20)
        self.assertEqual(data["pages"], 2)

    def test_monitor_summary_uses_latest_scan_across_all_pages(self):
        self.login()
        expected = datetime(2026, 8, 15, 1, 30, tzinfo=timezone.utc)
        with self.app.app_context():
            user = User.query.filter_by(username="cs2inventory_user").one()
            for index in range(18):
                target, _job, _created = add_monitor(user, f"7656119{3000000000 + index:010d}")
            target.last_scan_at = expected
            db.session.commit()
        data = self.client.get("/api/monitors?page=1").get_json()
        self.assertEqual(data["latest_scan_at"], "2026-08-15T09:30:00+08:00")

    def test_platform_cap_is_thirty_five_unique_targets(self):
        with self.app.app_context():
            user = User.query.filter_by(username="cs2inventory_user").one()
            for index in range(32):
                add_monitor(user, f"7656119{2000000000 + index:010d}")
            self.assertEqual(SteamTarget.query.count(), 35)
            with self.assertRaises(OverflowError):
                add_monitor(user, "76561199999999999")

    def test_maintenance_blocks_user_but_admin_can_add(self):
        user_token = self.login()
        with self.app.app_context():
            from cs2_inventory.services import state_set
            state_set("maintenance", "1")
            db.session.commit()
        response = self.client.post("/api/monitors", json={"steamid": "76561199000000001"}, headers={"X-CSRF-Token": user_token})
        self.assertEqual(response.status_code, 423)
        self.client.post("/api/auth/logout", headers={"X-CSRF-Token": user_token})
        admin_token = self.login("cs2inventory_admin")
        response = self.client.post("/api/monitors", json={"steamid": "76561199000000001"}, headers={"X-CSRF-Token": admin_token})
        self.assertEqual(response.status_code, 201)

    def test_unified_inventory_includes_all_groups_and_deduplicates_assetid(self):
        result = {
            "steamid": "76561198441561382",
            "protected_live": [{"name": "Item A", "count": 1, "assetids": ["1"], "sources": ["a"]}],
            "public_missing_live": [{"name": "Item A", "count": 1, "assetids": ["1"], "sources": ["b"]}, {"name": "Item B", "count": 1, "assetids": ["2"]}],
            "public": [{"name": "Item C", "count": 2, "assetids": ["3", "4"]}],
            "coverage": {"status": "complete"},
            "elapsed_ms": 10,
        }
        unified = unify_inventory(result)
        self.assertEqual(unified["total_items"], 4)
        self.assertEqual(unified["item_types"], 3)
        self.assertEqual({row["name"]: row["count"] for row in unified["items"]}, {"Item A": 1, "Item B": 1, "Item C": 2})
        self.assertNotIn("protected", json.dumps({key: value for key, value in unified.items() if not key.startswith("_")}))

    def test_snapshot_diff_and_eight_day_prune(self):
        with self.app.app_context():
            target = SteamTarget.query.first()
            first = store_snapshot(target, {"total_items": 1, "item_types": 1, "coverage": "ok", "elapsed_ms": 1, "errors": [], "_assets": [{"asset_key": "1", "name": "A", "amount": 1, "sources": []}]})
            db.session.commit()
            second = store_snapshot(target, {"total_items": 2, "item_types": 2, "coverage": "ok", "elapsed_ms": 1, "errors": [], "_assets": [{"asset_key": "1", "name": "A", "amount": 1, "sources": []}, {"asset_key": "2", "name": "B", "amount": 1, "sources": []}]})
            db.session.commit()
            self.assertEqual(snapshot_diff(second, first)["added"], [{"name": "B", "count": 1}])
            first.scanned_at = utcnow() - timedelta(days=9)
            db.session.commit()
            result = prune_expired()
            self.assertEqual(result["snapshots"], 1)
            self.assertIsNone(db.session.get(Snapshot, first.id))

    def test_user_changed_password_is_visible_to_admin_but_not_stored_plaintext(self):
        token = self.login()
        response = self.client.post(
            "/api/auth/password",
            json={"old_password": "platform-test-password", "new_password": "visible-new-password"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.client.post("/api/auth/logout", headers={"X-CSRF-Token": token})
        self.login("cs2inventory_admin")
        response = self.client.get("/api/admin/users")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        row = next(item for item in response.get_json()["items"] if item["username"] == "cs2inventory_user")
        self.assertEqual(row["password"], "visible-new-password")
        self.assertTrue(row["password_available"])
        self.assertIsNotNone(row["password_changed_at"])
        with self.app.app_context():
            user = User.query.filter_by(username="cs2inventory_user").one()
            self.assertNotEqual(user.password_ciphertext, "visible-new-password")
            self.assertNotIn("visible-new-password", user.password_hash)

    def test_compare_endpoint_supports_latest_to_one_three_and_seven_day_baselines(self):
        with self.app.app_context():
            target = SteamTarget.query.first()
            baseline = store_snapshot(target, {
                "total_items": 1, "item_types": 1, "coverage": "ok", "elapsed_ms": 1,
                "errors": [], "_assets": [{"asset_key": "old", "name": "Old", "amount": 1, "sources": []}],
            })
            baseline.scanned_at = utcnow() - timedelta(days=7, minutes=1)
            db.session.commit()
            current = store_snapshot(target, {
                "total_items": 2, "item_types": 2, "coverage": "ok", "elapsed_ms": 1,
                "errors": [], "_assets": [
                    {"asset_key": "old", "name": "Old", "amount": 1, "sources": []},
                    {"asset_key": "new", "name": "New", "amount": 1, "sources": []},
                ],
            })
            db.session.commit()
            target_id, baseline_id, current_id = target.id, baseline.id, current.id
        self.login()
        response = self.client.get(f"/api/monitors/{target_id}/compare?days=7")
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["current"]["id"], current_id)
        self.assertEqual(response.get_json()["baseline"]["id"], baseline_id)
        self.assertEqual(response.get_json()["diff"]["added"], [{"name": "New", "count": 1}])
        self.assertEqual(self.client.get(f"/api/monitors/{target_id}/compare?days=2").status_code, 400)

    def test_item_groups_are_sorted_by_newest_constituent_without_exposing_time(self):
        with self.app.app_context():
            target = SteamTarget.query.first()
            first = store_snapshot(target, {
                "total_items": 2, "item_types": 2, "coverage": "ok", "elapsed_ms": 1,
                "errors": [], "_assets": [
                    {"asset_key": "abstract-old", "name": "抽象派", "amount": 1, "sources": []},
                    {"asset_key": "beta", "name": "先前物品", "amount": 1, "sources": []},
                ],
            })
            old_time = utcnow() - timedelta(days=3)
            for item in first.items:
                item.first_seen_at = old_time
            db.session.commit()
            latest = store_snapshot(target, {
                "total_items": 3, "item_types": 2, "coverage": "ok", "elapsed_ms": 1,
                "errors": [], "_assets": [
                    {"asset_key": "abstract-old", "name": "抽象派", "amount": 1, "sources": []},
                    {"asset_key": "abstract-new", "name": "抽象派", "amount": 1, "sources": []},
                    {"asset_key": "beta", "name": "先前物品", "amount": 1, "sources": []},
                ],
            })
            db.session.commit()
            public = snapshot_public(latest)
            self.assertEqual(public["items"][0], {"name": "抽象派", "count": 2})
            self.assertNotIn("first_seen", json.dumps(public, ensure_ascii=False))
            self.assertEqual(SnapshotItem.query.filter_by(snapshot_id=latest.id).count(), 3)

    def test_daily_batch_sets_maintenance_and_queues_all_targets(self):
        with self.app.app_context():
            result = enqueue_daily()
            self.assertEqual(result["jobs"], 3)
            batch = db.session.get(ScanBatch, result["batch_id"])
            self.assertEqual(batch.total_jobs, 3)
            self.assertEqual(ScanJob.query.filter_by(batch_id=batch.id).count(), 3)
            from cs2_inventory.services import maintenance_active
            self.assertTrue(maintenance_active())

    def test_worker_stores_unified_snapshot_without_public_classification(self):
        fake = {
            "steamid": "76561198441561382",
            "protected_live": [{"name": "Hidden item", "count": 1, "assetids": ["p1"], "sources": ["internal"]}],
            "public": [{"name": "Visible item", "count": 1, "assetids": ["v1"], "sources": ["public"]}],
            "coverage": {"status": "complete"}, "elapsed_ms": 12, "errors": [],
            "sources": {"inventory": {"requests": 7}},
        }
        with self.app.app_context():
            job = ScanJob.query.order_by(ScanJob.id).first()
            with mock.patch("cs2_inventory.worker.run_max_coverage_query", return_value=fake) as query, mock.patch("cs2_inventory.worker.fetch_persona_name", return_value="Steam User"):
                process_job(job.id)
            self.assertEqual(query.call_args.kwargs["language"], "schinese")
            db.session.expire_all()
            job = db.session.get(ScanJob, job.id)
            self.assertEqual(job.status, "completed")
            snapshot = Snapshot.query.filter_by(target_id=job.target_id).one()
            public = snapshot_public(snapshot)
            self.assertEqual(public["total_items"], 2)
            self.assertTrue(public["scanned_at"].endswith("+08:00"))
            self.assertEqual({row["name"] for row in public["items"]}, {"Hidden item", "Visible item"})
            self.assertNotIn("protected", json.dumps(public))

    def test_worker_recovers_interrupted_jobs(self):
        with self.app.app_context():
            job = ScanJob.query.order_by(ScanJob.id).first()
            job.status = "running"
            db.session.commit()
            self.assertEqual(recover_interrupted_jobs(), 1)
            self.assertEqual(db.session.get(ScanJob, job.id).status, "queued")

    def test_user_interface_has_only_unified_inventory(self):
        html = (Path(__file__).parents[1] / "src" / "cs2_inventory" / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("交易保护", html)
        self.assertNotIn("公开可见", html)
        self.assertNotIn("库存总量", html)
        self.assertIn("itemSearch", html)
        self.assertIn('data-days="1"', html)
        self.assertIn('data-days="3"', html)
        self.assertIn('data-days="7"', html)
        self.assertNotIn("latest_detected_at", html)
        self.assertIn("timeZone:'Asia/Shanghai'", html)

    def test_admin_can_open_any_target_snapshot_from_global_list(self):
        with self.app.app_context():
            admin = User.query.filter_by(username="cs2inventory_admin").one()
            target = SteamTarget.query.filter_by(steamid="76561198441561382").one()
            self.assertIsNone(Subscription.query.filter_by(user_id=admin.id, target_id=target.id).first())
            snapshot = store_snapshot(target, {
                "total_items": 1,
                "item_types": 1,
                "coverage": "ok",
                "elapsed_ms": 1,
                "errors": [],
                "_assets": [{"asset_key": "admin-view", "name": "测试物品", "amount": 1, "sources": []}],
            })
            db.session.commit()
            target_id, snapshot_id = target.id, snapshot.id

        self.login("cs2inventory_admin")
        detail = self.client.get(f"/api/monitors/{target_id}")
        self.assertEqual(detail.status_code, 200, detail.get_json())
        self.assertEqual(detail.get_json()["snapshot"]["items"], [{"name": "测试物品", "count": 1}])
        history = self.client.get(f"/api/monitors/{target_id}/snapshots/{snapshot_id}")
        self.assertEqual(history.status_code, 200, history.get_json())

        html = (Path(__file__).parents[1] / "src" / "cs2_inventory" / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="outline-primary target-open"', html)
        self.assertIn("source=admin", html)
        self.assertEqual(self.client.get(f"/monitors/{target_id}").status_code, 200)

    def test_health_and_ready(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/ready").status_code, 200)


if __name__ == "__main__":
    unittest.main()
