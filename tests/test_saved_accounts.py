"""Account switching through real Flask cookies and the public authentication routes."""

import json
import secrets
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from flask.sessions import SecureCookieSessionInterface
from werkzeug.security import generate_password_hash

from cs2_inventory.account_sessions import (
    DEVICE,
    LEGACY,
    PREFIX,
    _digest,
    _version,
    prune_devices,
)
from cs2_inventory.app import create_app
from cs2_inventory.database import db
from cs2_inventory.models import SystemState, User, utcnow
from cs2_inventory.password_vault import set_user_password


@contextmanager
def at(instant):
    """Advance both the application clock and the real signed-cookie verifier."""
    with (
        patch("cs2_inventory.account_sessions.utcnow", return_value=instant),
        patch(
            "itsdangerous.TimestampSigner.get_timestamp",
            return_value=int(instant.timestamp()),
        ),
        patch("flask.sessions.datetime", wraps=datetime) as cookie_clock,
    ):
        cookie_clock.now.return_value = instant
        yield


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
        # A second browser with exactly the pre-action cookies, not credentials from the new session.
        other._cookies.update(self.client._cookies)
        return other

    def remembered(self):
        (cookie,) = [
            c
            for c in self.client._cookies.values()
            if c.key.startswith("session.remember.")
        ]
        return cookie

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

    def test_accounts_can_be_switched_after_inactivity_without_another_login(self):
        start = utcnow()
        with at(start):
            self.login(self.a)
            self.login(self.b)
        with at(start + timedelta(days=45)):
            self.accounts(self.a, self.b, current=self.b)
            self.switch(self.a)
        with at(start + timedelta(days=300)):
            self.accounts(self.a, self.b, current=self.a)
            self.switch(self.b)
            self.accounts(self.a, self.b, current=self.b)
            self.assertEqual(self.post("logout").status_code, 200)
        with at(start + timedelta(days=301)):
            self.assertIsNone(self.client.get("/api/bootstrap").json["user"])

    def test_an_active_browser_renews_its_cookie_across_multiple_years(self):
        start = utcnow()
        with at(start):
            self.login(self.a)
            self.login(self.b)
            initial = self.remembered()
            self.assertTrue(initial.http_only)
            self.assertEqual(initial.same_site, "Lax")
            self.assertGreater(initial.expires, start + timedelta(days=365))
        with at(start + timedelta(days=1)):
            read = self.client.get("/api/bootstrap")
            self.assertEqual(read.json["user"]["id"], self.b.id)
            self.assertFalse(
                any(
                    "session.remember." in h for h in read.headers.getlist("Set-Cookie")
                )
            )
        for days in (360, 720, 1080):
            instant = start + timedelta(days=days)
            with at(instant):
                # A browser would genuinely still have a valid, persistent cookie on each visit.
                self.assertGreater(self.remembered().expires, instant)
                self.accounts(self.a, self.b, current=self.b)
                self.assertGreater(
                    self.remembered().expires, instant + timedelta(days=365)
                )
        with at(start + timedelta(days=1081)):
            self.switch(self.a)
            self.accounts(self.a, self.b, current=self.a)

    def test_a_delayed_read_cannot_undo_switch_or_logout(self):
        start = utcnow()
        with at(start):
            self.login(self.a)
            self.login(self.b)
        with at(start + timedelta(days=45)):
            self.accounts(self.a, self.b, current=self.b)
            delayed = self.remembered()
            self.switch(self.a)
            # Simulate an older tab delivering its renewal response AFTER the switch response.
            self.client.set_cookie(delayed.key, delayed.value, expires=delayed.expires)
            self.accounts(self.a, self.b, current=self.a)
            self.assertEqual(self.post("logout").status_code, 200)
            self.client.set_cookie(delayed.key, delayed.value, expires=delayed.expires)
            self.assertIsNone(self.client.get("/api/bootstrap").json["user"])

    def test_removing_or_signing_out_does_not_clear_another_browser(self):
        other = self.app.test_client()
        start = utcnow()
        with at(start):
            for browser in (self.client, other):
                self.login(self.a, browser)
                self.login(self.b, browser)
        with at(start + timedelta(days=90)):
            self.assertEqual(
                self.post("accounts/forget", {"user_id": self.a.id}).status_code, 200
            )
            self.switch(self.a, 403)
            self.assertEqual(self.post("logout").status_code, 200)
            self.accounts(self.a, self.b, current=self.b, client=other)
            self.switch(self.a, client=other)
            self.assertEqual(self.client.get("/api/auth/accounts").status_code, 401)

    def test_a_role_change_requires_new_verification_for_that_account(self):
        self.login(self.a)
        self.login(self.b)
        self.a.role = "admin"
        db.session.commit()
        self.switch(self.a, 403)
        self.accounts(self.b, current=self.b)

    def test_logout_clears_both_sessions_created_by_two_concurrent_login_tabs(self):
        second_tab, other_device = self.app.test_client(), self.app.test_client()
        # Two tabs of the same browser submit their first login against the same anonymous state,
        # before either response (and its unique cookie name) arrives.
        self.csrf(self.client)
        second_tab._cookies.update(self.client._cookies)
        self.login(self.a)
        tab_one = self.captured()
        self.login(self.b, second_tab)
        self.login(self.c, other_device)
        self.client._cookies.update(second_tab._cookies)
        self.assertEqual(self.post("logout").status_code, 200)
        for previous in (self.client, tab_one, second_tab):
            self.assertIsNone(previous.get("/api/bootstrap").json["user"])
        self.accounts(self.c, current=self.c, client=other_device)

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

    def old_cookie(self, user, **fields):
        """Only the isolated fixture's old-style Flask cookie, before the new session interface."""
        self.client = self.app.test_client()
        signer = SecureCookieSessionInterface().get_signing_serializer(self.app)
        raw = signer.dumps(
            {
                "_permanent": True,
                "user_id": user.id,
                "csrf_token": secrets.token_urlsafe(32),
                **fields,
            }
        )
        _, signed = signer.loads(raw, return_timestamp=True)
        self.client.set_cookie("session", raw, expires=signed + timedelta(days=1))
        return signed

    def legacy(self):
        signed = self.old_cookie(self.a)
        self.a.last_login_at = signed + timedelta(milliseconds=10)
        db.session.commit()
        return signed

    def old_device(self, current, accounts):
        """Seed exactly one previous-release device, mixing individual 24-hour end times."""
        token = secrets.token_urlsafe(32)
        row = SystemState(
            key="authdev:" + _digest(token),
            value=json.dumps(
                {
                    str(u.id): [expires.timestamp(), _version(u)]
                    for u, expires in accounts
                }
            ),
        )
        db.session.add(row)
        db.session.commit()
        return self.old_cookie(current, **{DEVICE: token})

    def test_only_still_valid_previous_release_grants_are_remembered(self):
        start = utcnow()
        with at(start):
            self.old_device(
                self.c,
                [
                    (self.a, start - timedelta(hours=2)),
                    (self.b, start + timedelta(hours=3)),
                    (self.c, start + timedelta(hours=10)),
                ],
            )
            old = self.captured()
            self.accounts(self.b, self.c, current=self.c)
            self.assertGreater(self.remembered().expires, start + timedelta(days=365))
            self.switch(self.a, 403)
            self.assertEqual(old.get("/api/auth/accounts").status_code, 401)
        with at(start + timedelta(days=100)):
            self.switch(self.b)
            self.accounts(self.b, self.c, current=self.b)

    def test_expired_old_browser_is_not_revived_even_when_another_entry_was_valid(self):
        start = utcnow()
        with at(start):
            self.old_device(
                self.a,
                [
                    (self.a, start - timedelta(hours=1)),
                    (self.b, start + timedelta(hours=4)),
                ],
            )
            self.assertIsNone(self.client.get("/api/bootstrap").json["user"])
            self.login(self.c)
            self.accounts(self.c, current=self.c)

    def test_unseen_legacy_cookie_still_expires_after_one_day(self):
        signed = self.legacy()
        with at(signed + timedelta(days=1, seconds=1)):
            self.assertIsNone(self.client.get("/api/bootstrap").json["user"])
            self.login(self.b)
            self.accounts(self.b, current=self.b)

    def test_logout_also_retires_a_coexisting_legacy_cookie(self):
        for old_type in ("single_account", "device"):
            with self.subTest(old_type=old_type):
                if old_type == "device":
                    self.old_device(self.a, [(self.a, utcnow() + timedelta(hours=12))])
                else:
                    self.legacy()
                before_upgrade = self.captured()
                self.client = self.app.test_client()
                self.login(self.b)
                # An already-open legacy tab received its response while this tab logged in.
                self.client._cookies.update(before_upgrade._cookies)
                self.accounts(self.b, current=self.b)
                self.assertEqual(self.post("logout").status_code, 200)
                self.assertIsNone(before_upgrade.get("/api/bootstrap").json["user"])
                self.assertIsNone(self.client.get("/api/bootstrap").json["user"])

    def test_verified_legacy_cookie_keeps_the_original_account_only_once(self):
        signed = self.legacy()
        old = self.captured()
        self.accounts(self.a, current=self.a)
        with at(signed + timedelta(hours=5)):
            self.login(self.b)
            self.switch(self.a)
            self.switch(self.b)
        self.assertEqual(old.get("/api/auth/accounts").status_code, 401)
        self.login(self.c, old)
        self.accounts(self.c, current=self.c, client=old)
        with at(signed + timedelta(days=14)):
            self.switch(self.a)
            self.accounts(self.a, self.b, current=self.a)

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
        self.assertTrue(self.remembered().http_only)
        stale = utcnow() - timedelta(days=500)
        unrelated = SystemState(key="spare-setting", value="keep", updated_at=stale)
        expired_v1_key = "authdev:expired-device-fixture"
        fresh = SystemState(key="authdev:fresh-v1", value="{}")
        db.session.add_all(
            [
                unrelated,
                fresh,
                SystemState(key=expired_v1_key, value="{}", updated_at=stale),
            ]
        )
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
            [row.key],
        )
        self.assertIsNone(db.session.get(SystemState, expired_v1_key))
        self.assertIsNotNone(db.session.get(SystemState, fresh.key))
        self.assertEqual(
            SystemState.query.filter(SystemState.key.startswith(LEGACY)).count(), 0
        )
        self.assertEqual(db.session.get(SystemState, unrelated.key).value, "keep")
        self.accounts(self.a, self.b, current=self.b)
        self.switch(self.a)


if __name__ == "__main__":
    unittest.main()
