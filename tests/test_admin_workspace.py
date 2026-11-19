"""管理员跨页目标搜索、权限边界与一次性续期码。"""

import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from cs2_inventory.app import create_app
from cs2_inventory.database import db
from cs2_inventory.models import SteamTarget, User

PASSWORD = "synthetic-local-only"
ADMIN, MEMBER = "ux_admin_test", "ux_member_test"


def fixture(directory):
    app = create_app(
        {
            "TESTING": True,
            "STATE_DIR": directory,
            "SECRET_KEY": "local-ux-test",
            "STEAMWEBAPI_KEY": "",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///"
            + (directory / "ux.sqlite").as_posix(),
        }
    )
    with app.app_context():
        hashed = generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000")
        db.session.add_all(
            [
                User(
                    username=n, password_hash=hashed, role=role, account_kind="internal"
                )
                for n, role in ((ADMIN, "admin"), (MEMBER, "user"))
            ]
        )
        db.session.add_all(
            [
                SteamTarget(
                    steamid=str(76561198000000000 + n),
                    persona_name=("Needle_5% Alpha" if n == 1 else f"本地验收 {n:02}"),
                )
                for n in range(45)
            ]
        )
        db.session.commit()
    return app


class AdminWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = fixture(Path(self.temp.name))
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def post(self, path, data):
        token = self.client.get("/api/bootstrap").json["csrf_token"]
        return self.client.post(path, json=data, headers={"X-CSRF-Token": token})

    def login(self, name):
        self.assertEqual(
            self.post(
                "/api/auth/login", {"username": name, "password": PASSWORD}
            ).status_code,
            200,
        )

    def test_admin_search_filters_before_pagination(self):
        self.login(ADMIN)
        target = "/api/admin/targets"
        first = self.client.get(target).json
        self.assertEqual(
            (first["total"], first["pages"], len(first["items"])), (45, 3, 20)
        )
        self.assertNotIn("Needle_5% Alpha", [row["label"] for row in first["items"]])
        for query in ("needle", "76561198000000001", "_5%", "%"):
            with self.subTest(query=query):
                hits = self.client.get(
                    target, query_string={"q": query, "page": 99}
                ).json
                self.assertEqual(
                    (hits["total"], hits["page"], hits["items"][0]["persona_name"]),
                    (1, 1, "Needle_5% Alpha"),
                )
        self.assertEqual(
            self.client.get(target, query_string={"q": "没有这个名称"}).json["items"],
            [],
        )

    def test_target_search_keeps_admin_gate(self):
        url = "/api/admin/targets?q=needle"
        self.assertEqual(self.client.get(url).status_code, 401)
        self.login(MEMBER)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_user_search_is_global_literal_and_admin_only(self):
        with self.app.app_context():
            hashed = generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000")
            db.session.add_all(
                [
                    User(
                        username="FindMe_1" if n == 0 else f"account_fixture_{n:02}",
                        role="user",
                        account_kind="internal",
                        password_hash=hashed,
                    )
                    for n in range(45)
                ]
            )
            db.session.commit()
        url = "/api/admin/users"
        self.assertEqual(
            self.client.get(url, query_string={"q": "findme"}).status_code, 401
        )
        self.login(MEMBER)
        self.assertEqual(
            self.client.get(url, query_string={"q": "findme"}).status_code, 403
        )
        self.login(ADMIN)
        first = self.client.get(url).json
        self.assertGreaterEqual(first["pages"], 3)
        self.assertNotIn("FindMe_1", [r["username"] for r in first["items"]])
        for query in ("findME_", "FindMe_1"):
            payload = self.client.get(url, query_string={"q": query, "page": 99}).json
            self.assertEqual(
                (payload["total"], payload["page"], payload["items"][0]["username"]),
                (1, 1, "FindMe_1"),
            )
        self.assertEqual(self.client.get(url, query_string={"q": "%"}).json["total"], 0)

    def test_plain_invite_only_in_creation_response(self):
        self.login(ADMIN)
        created = self.post(
            "/api/admin/activation-codes", {"plan": "monthly", "monitor_limit": 5}
        )
        self.assertEqual(created.status_code, 201)
        code = created.json["code"]
        history = self.client.get("/api/admin/activation-codes")
        self.assertNotIn(code, history.get_data(as_text=True))
        self.assertIn("prefix", history.json["items"][0])
        self.assertNotIn("code", history.json["items"][0])
