"""Account switching through real Flask cookies and the public authentication routes."""

import secrets
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from cs2_inventory.account_sessions import DEVICE, LEGACY, PREFIX, prune_devices
from cs2_inventory.app import create_app
from cs2_inventory.database import db
from cs2_inventory.models import SystemState, User, utcnow
from cs2_inventory.password_vault import set_user_password


class SavedAccountsTests(unittest.TestCase):
    password = "account-switch-fixture"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "STATE_DIR": Path(self.temp.name),
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
                "SESSION_COOKIE_PATH": "/",
                "SECRET_KEY": "account-switch-test",
                "STEAMWEBAPI_KEY": "fixture",
            }
        )
        self.context = self.app.app_context()
        self.context.push()
        self.client = self.app.test_client()
        self.a, self.b, self.c = [
            User(
                username=name,
                role="user",
                account_kind="internal",
                plan="permanent",
                password_hash=generate_password_hash(
                    self.password, method="pbkdf2:sha256:1000"
                ),
                created_at=utcnow() - timedelta(days=2),
            )
            for name in ("alice", "bob", "charlie")
        ]
        db.session.add_all([self.a, self.b, self.c])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.temp.cleanup()

    def csrf(self, client):
        return client.get("/api/bootstrap").json["csrf_token"]

    def post(self, path, data=None, client=None):
        client = client or self.client
        return client.post(
            "/api/auth/" + path, json=data, headers={"X-CSRF-Token": self.csrf(client)}
        )

    def login(self, user, client=None):
        response = self.post(
            "login", {"username": user.username, "password": self.password}, client
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["user"]["id"], user.id)
        return response.json["csrf_token"]

    def accounts(self, *users, current=None, client=None):
        client = client or self.client
        response = client.get("/api/auth/accounts")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(
            response.json,
            {
                "accounts": [
                    {"id": u.id, "username": u.username}
                    for u in sorted(users, key=lambda u: u.username)
                ],
                "current_id": current.id,
            },
        )
        self.assertTrue(response.cache_control.no_store)
        self.assertIn("Cookie", response.headers["Vary"])

    def switch(self, user, status=200, client=None):
        response = self.post("switch", {"user_id": user.id}, client)
        self.assertEqual(response.status_code, status, response.json)
        if status == 200:
            self.assertEqual(response.json["user"]["id"], user.id)
            return response.json["csrf_token"]
        self.assertIn("error", response.json)

    def captured(self):
        other = self.app.test_client()
        other.set_cookie("session", self.client.get_cookie("session").value)
        return other

    def test_password_once_then_switch_both_ways_forget_and_logout(self):
        first = self.login(self.a)
        self.assertEqual(
            self.post(
                "login", {"username": self.b.username, "password": "nope"}
            ).status_code,
            401,
        )
        self.accounts(self.a, current=self.a)
        before_add = self.captured()
        second = self.login(self.b)
        self.assertNotEqual(first, second)
        self.accounts(self.a, self.b, current=self.b)
        before_switch = self.captured()
        third = self.switch(self.a)
        self.assertNotEqual(second, third)
        self.accounts(self.a, self.b, current=self.a)
        self.switch(self.b)
        self.accounts(self.a, self.b, current=self.b)
        self.assertEqual(
            self.post("accounts/forget", {"user_id": self.b.id}).status_code, 400
        )
        before_forget = self.captured()
        csrf = self.csrf(self.client)
        self.assertEqual(
            self.post("accounts/forget", {"user_id": self.a.id}).status_code, 200
        )
        self.assertEqual(self.csrf(self.client), csrf)
        self.accounts(self.b, current=self.b)
        self.switch(self.a, 403)
        self.login(self.a)
        before_logout = self.captured()
        self.assertEqual(self.post("logout").status_code, 200)
        for invalid in (
            before_add,
            before_switch,
            before_forget,
            before_logout,
            self.client,
        ):
            self.assertEqual(invalid.get("/api/auth/accounts").status_code, 401)
            self.assertIsNone(invalid.get("/api/bootstrap").json["user"])
        self.login(self.a)
        self.accounts(self.a, current=self.a)

    def test_csrf_target_validation_and_device_bound_list(self):
        self.login(self.a)
        self.login(self.b)
        other = self.app.test_client()
        self.login(self.c, other)
        self.accounts(self.c, current=self.c, client=other)
        for path in ("/api/auth/switch", "/api/auth/accounts/forget"):
            self.assertEqual(
                self.client.post(path, json={"user_id": self.a.id}).status_code, 400
            )
            for invalid in (
                {"user_id": True},
                {"user_id": str(self.a.id)},
                [self.a.id],
            ):
                self.assertEqual(
                    self.client.post(
                        path,
                        json=invalid,
                        headers={"X-CSRF-Token": self.csrf(self.client)},
                    ).status_code,
                    400,
                )
        self.switch(self.a, 403, other)
        self.switch(self.c, 403)
        self.accounts(self.a, self.b, current=self.b)
        before = self.csrf(self.client)
        self.switch(self.a)
        self.assertEqual(
            self.client.post(
                "/api/auth/switch",
                json={"user_id": self.b.id},
                headers={"X-CSRF-Token": before},
            ).status_code,
            400,
        )

    def test_empty_csrf_is_rejected_even_without_a_session_token(self):
        for supplied in ({}, {"X-CSRF-Token": ""}):
            anonymous = self.app.test_client()
            response = anonymous.post(
                "/api/auth/login",
                json={"username": self.a.username, "password": self.password},
                headers=supplied,
            )
            self.assertEqual(response.status_code, 400)
            self.assertIsNone(anonymous.get("/api/bootstrap").json["user"])
        self.login(self.a)
        self.login(self.b)
        with self.client.session_transaction() as browser:
            browser.pop("csrf_token")
        for action in ("switch", "accounts/forget"):
            response = self.client.post(
                "/api/auth/" + action,
                json={"user_id": self.a.id},
                headers={"X-CSRF-Token": ""},
            )
            self.assertEqual(response.status_code, 400)
        self.accounts(self.a, self.b, current=self.b)

    def test_grants_remain_at_original_password_expiry_even_after_switches(self):
        start = utcnow()
        with patch("cs2_inventory.account_sessions.utcnow") as now:
            now.return_value = start
            self.login(self.a)
            now.return_value = start + timedelta(hours=10)
            self.login(self.b)
            now.return_value = start + timedelta(hours=20)
            self.switch(self.a)
            now.return_value = start + timedelta(hours=21)
            self.switch(self.b)
            now.return_value = start + timedelta(hours=24)
            self.accounts(self.b, current=self.b)
            self.switch(self.a, 403)
            now.return_value = start + timedelta(hours=34)
            self.assertEqual(self.client.get("/api/auth/accounts").status_code, 401)

    def test_password_reset_inactive_and_recreated_users_require_password_again(self):
        self.login(self.a)
        other = self.app.test_client()
        self.login(self.a, other)
        self.login(self.b)
        old_hash = self.a.password_hash
        set_user_password(self.a, "after-reset")
        db.session.commit()
        self.switch(self.a, 403)
        self.assertEqual(other.get("/api/auth/accounts").status_code, 401)
        self.accounts(self.b, current=self.b)
        self.login(self.c)
        self.switch(self.b)
        self.c.is_active = False
        db.session.commit()
        self.switch(self.c, 403)
        self.accounts(self.b, current=self.b)
        self.a.password_hash = old_hash
        db.session.commit()
        self.login(self.a)
        self.switch(self.b)
        old_id = self.a.id
        db.session.delete(self.a)
        db.session.commit()
        self.assertEqual(self.post("switch", {"user_id": old_id}).status_code, 403)
        replacement = User(
            id=old_id,
            username="alice",
            password_hash=old_hash,
            role="user",
            account_kind="internal",
            plan="permanent",
        )
        db.session.add(replacement)
        db.session.commit()
        self.switch(replacement, 403)
        self.accounts(self.b, current=self.b)

    def test_password_change_keeps_verified_device_but_retires_other_sessions(self):
        self.login(self.b)
        self.login(self.a)
        other = self.app.test_client()
        self.login(self.a, other)
        token = self.csrf(self.client)
        reply = self.post(
            "password",
            {"old_password": self.password, "new_password": "new-test-password"},
        )
        self.assertEqual(reply.status_code, 200, reply.json)
        self.assertEqual(self.csrf(self.client), token)
        self.accounts(self.a, self.b, current=self.a)
        self.switch(self.b)
        self.switch(self.a)
        self.assertEqual(other.get("/api/auth/accounts").status_code, 401)

    def legacy(self):
        with self.client.session_transaction() as old:
            old.clear()
            old.permanent = True
            old["user_id"] = self.a.id
            old["csrf_token"] = secrets.token_urlsafe(32)
        raw = self.client.get_cookie("session").value
        _, signed = self.app.session_interface.get_signing_serializer(self.app).loads(
            raw, return_timestamp=True
        )
        self.a.last_login_at = signed + timedelta(milliseconds=10)
        db.session.commit()
        return signed

    def test_verified_legacy_cookie_retains_original_account_and_expiry_only_once(self):
        signed = self.legacy()
        old = self.captured()
        self.accounts(self.a, current=self.a)
        with patch(
            "cs2_inventory.account_sessions.utcnow",
            return_value=signed + timedelta(hours=5),
        ):
            self.login(self.b)
            self.switch(self.a)
            self.switch(self.b)
        self.assertEqual(old.get("/api/auth/accounts").status_code, 401)
        self.login(self.c, old)
        self.accounts(self.c, current=self.c, client=old)
        with patch(
            "cs2_inventory.account_sessions.utcnow",
            return_value=signed + timedelta(hours=24),
        ):
            self.switch(self.a, 403)
            self.accounts(self.b, current=self.b)

    def test_unproven_old_session_is_not_authenticated_or_imported(self):
        for field, delta in (
            ("password_changed_at", 0),
            ("created_at", 0),
            ("last_login_at", -2),
        ):
            with self.subTest(field=field):
                self.a.created_at = utcnow() - timedelta(days=2)
                self.a.password_changed_at = None
                signed = self.legacy()
                setattr(self.a, field, signed + timedelta(seconds=delta))
                db.session.commit()
                self.assertIsNone(self.client.get("/api/bootstrap").json["user"])
                self.login(self.b)
                self.accounts(self.b, current=self.b)
                self.switch(self.a, 403)
        with self.client.session_transaction() as old:
            old.clear()
            old["user_id"] = self.a.id
        self.assertEqual(self.client.get("/api/auth/accounts").status_code, 401)

    def test_session_storage_and_scoped_cleanup(self):
        self.legacy()
        self.login(self.b)
        with self.client.session_transaction() as session:
            device = session[DEVICE]
        records = SystemState.query.filter(SystemState.key.startswith(PREFIX)).all()
        self.assertEqual(len(records), 1)
        (row,) = records
        self.assertNotIn(device, row.key + row.value)
        for secret in (self.password, self.a.password_hash, self.b.password_hash):
            self.assertNotIn(secret, row.value)
        self.assertTrue(self.client.get_cookie("session").http_only)
        stale = utcnow() - timedelta(hours=25)
        unrelated = SystemState(key="spare-setting", value="keep", updated_at=stale)
        fresh = SystemState(key=PREFIX + "fresh", value="{}")
        db.session.add_all([unrelated, fresh])
        row.updated_at = stale
        SystemState.query.filter(
            SystemState.key.startswith(LEGACY)
        ).one().updated_at = stale
        db.session.commit()
        prune_devices(utcnow())
        db.session.commit()
        self.assertEqual(
            [
                r.key
                for r in SystemState.query.filter(
                    SystemState.key.startswith(PREFIX)
                ).all()
            ],
            [fresh.key],
        )
        self.assertEqual(
            SystemState.query.filter(SystemState.key.startswith(LEGACY)).count(), 0
        )
        self.assertEqual(db.session.get(SystemState, unrelated.key).value, "keep")


if __name__ == "__main__":
    unittest.main()
