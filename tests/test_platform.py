from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
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
    SteamTarget,
    Subscription,
    User,
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

    def test_snapshot_diff_and_seven_day_prune(self):
        with self.app.app_context():
            target = SteamTarget.query.first()
            first = store_snapshot(target, {"total_items": 1, "item_types": 1, "coverage": "ok", "elapsed_ms": 1, "errors": [], "_assets": [{"asset_key": "1", "name": "A", "amount": 1, "sources": []}]})
            db.session.commit()
            second = store_snapshot(target, {"total_items": 2, "item_types": 2, "coverage": "ok", "elapsed_ms": 1, "errors": [], "_assets": [{"asset_key": "1", "name": "A", "amount": 1, "sources": []}, {"asset_key": "2", "name": "B", "amount": 1, "sources": []}]})
            db.session.commit()
            self.assertEqual(snapshot_diff(second, first)["added"], [{"name": "B", "count": 1}])
            first.scanned_at = utcnow() - timedelta(days=8)
            db.session.commit()
            result = prune_expired()
            self.assertEqual(result["snapshots"], 1)
            self.assertIsNone(db.session.get(Snapshot, first.id))

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
        self.assertIn("库存总量", html)

    def test_health_and_ready(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/ready").status_code, 200)


if __name__ == "__main__":
    unittest.main()
