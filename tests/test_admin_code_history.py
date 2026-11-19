"""续期码浏览、过滤、撤销：隔离数据库，不把完整续期码送入历史。"""

import tempfile
import unittest
from pathlib import Path

from test_admin_workspace import ADMIN, MEMBER, PASSWORD, fixture

from cs2_inventory.database import db
from cs2_inventory.models import ActivationCode, User, utcnow


def add_rows(app):
    with app.app_context():
        admin = User.query.filter_by(username=ADMIN).one()
        member = User.query.filter_by(username=MEMBER).one()
        for i in range(26):
            db.session.add(
                ActivationCode(
                    code_prefix=f"CS2-{i:04X}-CAFE",
                    code_digest=f"{i:064x}",
                    plan="annual",
                    monitor_limit=5 + i,
                    created_by_id=admin.id,
                    revoked_at=utcnow() if i == 2 else None,
                    redeemed_at=utcnow() if i == 1 else None,
                    redeemed_by_id=member.id if i == 1 else None,
                )
            )
        db.session.commit()


class CodeHistoryTests(unittest.TestCase):
    def test_cross_page_prefix_status_and_safe_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = fixture(Path(tmp))
            add_rows(app)
            c = app.test_client()
            try:
                url = "/api/admin/activation-codes"
                self.assertEqual(c.get(url).status_code, 401)

                def login(name):
                    csrf = c.get("/api/bootstrap").json["csrf_token"]
                    return c.post(
                        "/api/auth/login",
                        json=dict(username=name, password=PASSWORD),
                        headers={"X-CSRF-Token": csrf},
                    )

                self.assertEqual(login(MEMBER).status_code, 200)
                self.assertEqual(c.get(url).status_code, 403)
                self.assertEqual(login(ADMIN).status_code, 200)
                page2 = c.get(url, query_string=dict(page=2)).json
                self.assertEqual((page2["pages"], len(page2["items"])), (2, 6))
                old = c.get(
                    url, query_string=dict(q="cs2-0001", status="redeemed")
                ).json
                self.assertEqual(old["total"], 1)
                self.assertEqual(old["items"][0]["redeemed_by_username"], MEMBER)
                for x in page2["items"]:
                    self.assertNotIn("code", x)
                    self.assertNotIn("code_digest", x)
                self.assertEqual(
                    c.get(url, query_string=dict(status="unused")).json["total"], 24
                )
                self.assertEqual(
                    c.get(url, query_string=dict(status="revoked")).json["total"], 1
                )
                self.assertEqual(c.get(url, query_string=dict(q="%")).json["total"], 0)
                self.assertEqual(
                    c.get(url, query_string=dict(status="invalid")).status_code, 400
                )
            finally:
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()
