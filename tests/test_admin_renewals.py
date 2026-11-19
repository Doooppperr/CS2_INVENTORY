"""待续期清单：跨页范围、精确边界与不加载敏感字段。"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event

from cs2_inventory.database import db
from cs2_inventory.entitlements import GRACE_LIFETIME, entitlement_state
from cs2_inventory.models import SteamTarget, Subscription, User
from tests.test_admin_workspace import ADMIN, MEMBER, PASSWORD, fixture

NOW = datetime(2026, 11, 19, 6, 30, tzinfo=timezone.utc)


class RenewalInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = fixture(Path(self.temp.name))
        self.client = self.app.test_client()
        units = timedelta(microseconds=1)
        with self.app.app_context():
            def create(name, at, **changes):
                columns = dict(username="renew_"+name, password_hash="local-fixture-hash", password_ciphertext="opaque-test",
                               role="user", account_kind="customer", plan="monthly", monitor_limit=10,
                               activated_at=NOW-timedelta(days=60), activation_expires_at=at)
                columns.update(changes)
                user = User(**columns)
                db.session.add(user)
                return user
            for n in range(25):
                create(f"near_{n:02}", NOW+timedelta(hours=n+1))
            exact = create("edge", NOW+timedelta(days=7))
            create("later", NOW+timedelta(days=7)+units)
            create("start_grace", NOW)
            create("end_grace", NOW-GRACE_LIFETIME+units)
            create("expired", NOW-GRACE_LIFETIME)
            create("nodate", None)
            create("internal", NOW+units, account_kind="internal")
            create("admin", NOW+units, role="admin")
            create("permanent", NOW-GRACE_LIFETIME, plan="permanent")
            db.session.add(Subscription(user=exact, target=SteamTarget.query.first(), remark="私有备注"))
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

    def list(self, **options):
        with patch("cs2_inventory.app.utcnow", return_value=NOW):
            return self.client.get("/api/admin/users", query_string={"compact": 1, **options})

    def test_exact_time_boundaries_and_page_facet_counts(self):
        self.assertEqual(self.list(user_state="expired").status_code, 401)
        self.login(MEMBER)
        self.assertEqual(self.list(user_state="expired").status_code, 403)
        self.login(ADMIN)
        # Legacy compatibility does not turn new filters into an endpoint for credentials.
        with patch("cs2_inventory.app.recover_user_password", side_effect=AssertionError("unexpected credential read")):
            data = self.list(q="renew_", user_state="expiring", page=100).json
            self.assertEqual(data["state_counts"], {"all": 34, "expiring": 26, "grace": 2, "expired": 2})
            self.assertEqual((data["total"], data["page"], data["pages"], len(data["items"])), (26, 2, 2, 6))
            self.assertEqual(data["items"][0]["username"], "renew_near_20")
            last = data["items"][-1]
            self.assertEqual((last["username"], last["monitor_count"], last["entitlement"]["status"]), ("renew_edge", 1, "active"))
            self.assertFalse({"password", "password_available", "password_ciphertext", "password_hash", "steamids"} & last.keys())
            for key, group in (("grace", {"renew_end_grace", "renew_start_grace"}),
                               ("expired", {"renew_expired", "renew_nodate"})):
                items = self.list(q="renew_", user_state=key).json["items"]
                self.assertEqual({u["username"] for u in items}, group)
                with self.app.app_context():
                    for row in items:
                        self.assertEqual(entitlement_state(db.session.get(User, row["id"]), now=NOW), key)
                        self.assertEqual(row["entitlement"]["status"], key)
            none = self.list(q="missing_word%", user_state="expired", page=90).json
            self.assertEqual((none["total"], none["pages"], none["page"], none["items"]), (0, 0, 1, []))
            self.assertEqual(self.list(user_state="wrong").status_code, 400)
            self.assertEqual(self.client.get("/api/admin/users?user_state=expiring").status_code, 200)

    def test_compact_uses_aggregates_not_per_customer_queries_or_credentials(self):
        self.login(ADMIN)
        sql = []
        def track(conn, cursor, stmt, *_):
            if stmt.lstrip().upper().startswith("SELECT"):
                sql.append(stmt.lower())
        with self.app.app_context():
            event.listen(db.engine, "before_cursor_execute", track)
            try:
                narrow = self.list(q="renew_edge")
                one_count = len(sql)
                sql.clear()
                wide = self.list(q="renew_", user_state="all")
            finally:
                event.remove(db.engine, "before_cursor_execute", track)
        self.assertEqual(narrow.json["total"], 1)
        self.assertEqual(len(wide.json["items"]), 20)
        self.assertLessEqual(len(sql), one_count+1)
        page_query = [stmt for stmt in sql if "from users" in stmt and "order by" in stmt]
        self.assertEqual(len(page_query), 1)
        self.assertNotIn("password_", page_query[0])
        self.assertFalse(any("steam_targets" in stmt for stmt in sql))
        self.assertIn("no-store", wide.headers["Cache-Control"])
