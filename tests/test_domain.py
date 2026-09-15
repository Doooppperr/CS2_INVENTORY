from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cs2_inventory.app import create_app
from cs2_inventory.database import db
from cs2_inventory.models import User
from cs2_inventory.password_vault import set_user_password


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "STATE_DIR": Path(self.temp.name),
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "SECRET_KEY": "domain-test-secret",
            "PASSWORD_VAULT_KEY": "domain-test-vault",
            "SESSION_COOKIE_PATH": "/",
            "SESSION_COOKIE_SECURE": True,
            "TRUSTED_HOSTS": ["cs2inventory.cn", "localhost", "127.0.0.1"],
        })
        self.client = self.app.test_client()
        self.base = "https://cs2inventory.cn"

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def test_footer_and_root_resources_on_all_page_shells(self):
        for path in ("/", "/app", "/app?view=admin", "/app/monitors/1", "/monitors/1"):
            with self.subTest(path=path):
                response = self.client.get(path, base_url=self.base)
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertEqual(html.count("沪ICP备2026034136号-2"), 1)
                self.assertIn('href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer"', html)
                self.assertIn('script.src="/static/theme.js"', html)
                self.assertIn('href="/static/site-footer.css"', html)
                self.assertNotIn("/cs2_inventory/", html)
        for path in ("/static/theme.js", "/static/site-footer.css"):
            response = self.client.get(path, base_url=self.base)
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_production_cookie_is_secure_host_only_and_root_scoped(self):
        response = self.client.get("/api/bootstrap", base_url=self.base)
        cookie = response.headers["Set-Cookie"]
        for value in ("Secure", "HttpOnly", "Path=/;", "SameSite=Lax"):
            self.assertIn(value, cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertNotIn("cs2_inventory", cookie)

    def test_untrusted_hosts_and_forwarded_host_cannot_reach_app(self):
        for host in ("111.229.87.94", "healthdoc.cn", "www.cs2inventory.cn", "unknown.invalid"):
            response = self.client.get("/api/bootstrap", base_url=f"https://{host}", headers={"X-Forwarded-Host": "cs2inventory.cn"})
            self.assertEqual(response.status_code, 400)
        for host in ("localhost", "127.0.0.1", "cs2inventory.cn"):
            self.assertEqual(self.client.get("/ready", base_url=f"http://{host}").status_code, 200)

    def test_old_prefix_does_not_redirect_or_serve_api(self):
        for path in ("/cs2_inventory", "/cs2_inventory/", "/cs2_inventory/app", "/cs2_inventory/api/bootstrap"):
            response = self.client.get(path, base_url=self.base)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn("Location", response.headers)

    def test_login_and_logout_under_canonical_domain(self):
        with self.app.app_context():
            user = User.query.filter_by(username="cs2inventory_user").one()
            set_user_password(user, "domain-test-password")
            db.session.commit()
        token = self.client.get("/api/bootstrap", base_url=self.base).json["csrf_token"]
        response = self.client.post("/api/auth/login", base_url=self.base, json={"username": "cs2inventory_user", "password": "domain-test-password"}, headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        bootstrap = self.client.get("/api/bootstrap", base_url=self.base).json
        self.assertEqual(bootstrap["user"]["username"], "cs2inventory_user")
        response = self.client.post("/api/auth/logout", base_url=self.base, headers={"X-CSRF-Token": bootstrap["csrf_token"]})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.client.get("/api/bootstrap", base_url=self.base).json["user"])


if __name__ == "__main__":
    unittest.main()
