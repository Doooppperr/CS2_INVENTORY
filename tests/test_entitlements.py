from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.security import generate_password_hash

from cs2_inventory.app import create_app
from cs2_inventory.database import db
from cs2_inventory.entitlements import (
    add_natural_period,
    cleanup_lifecycle,
    create_activation_code,
)
from cs2_inventory.models import Subscription, User, utcnow
from cs2_inventory.services import add_monitor, store_snapshot


class EntitlementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "STATE_DIR": root,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(root / 'test.db').as_posix()}",
            "SECRET_KEY": "entitlement-test",
            "PASSWORD_VAULT_KEY": "entitlement-password-vault",
            "SESSION_COOKIE_PATH": "/",
            "STEAMWEBAPI_KEY": "test-key",
            "OBSERVATION_CACHE": str(root / "observations.json"),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            password_hash = generate_password_hash("platform-test-password")
            User.query.update({User.password_hash: password_hash}, synchronize_session=False)
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
        response = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["csrf_token"]

    def admin_login(self):
        return self.login("cs2inventory_admin", "platform-test-password")

    def admin_create(self, username, plan="monthly", monitor_limit=5, password="customer-pass-123"):
        token = self.admin_login()
        response = self.client.post(
            "/api/admin/users",
            json={
                "username": username,
                "password": password,
                "plan": plan,
                "monitor_limit": monitor_limit,
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        self.client.post("/api/auth/logout", headers={"X-CSRF-Token": token})
        return response.get_json()

    def test_public_registration_is_closed_without_side_effects(self):
        before = self.client.get("/api/bootstrap").get_json()["csrf_token"]
        with self.app.app_context():
            user_count = User.query.count()
        response = self.client.post(
            "/api/auth/register",
            json={"username": "new_user", "password": "long-pass-123"},
            headers={"X-CSRF-Token": before},
        )
        self.assertEqual(response.status_code, 403, response.get_json())
        self.assertEqual(response.get_json(), {"error": "账号仅由管理员创建"})
        with self.app.app_context():
            self.assertEqual(User.query.count(), user_count)
            self.assertIsNone(User.query.filter_by(username="new_user").first())

    def test_landing_login_accepts_legacy_short_password_and_has_no_registration(self):
        landing = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="password" type="password" minlength="1"', landing)
        self.assertNotIn("registerTab", landing)
        self.assertNotIn("api/auth/register", landing)

        with self.app.app_context():
            user = User.query.filter_by(username="cs2inventory_user").one()
            user.password_hash = generate_password_hash("123456")
            db.session.commit()
        response = self.client.post(
            "/api/auth/login",
            json={"username": "cs2inventory_user", "password": "123456"},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_warm_landing_and_three_part_admin_console_navigation(self):
        landing = self.client.get("/").get_data(as_text=True)
        self.assertIn("登录进入平台", landing)
        self.assertNotIn("免费体验", landing)
        self.assertIn("一键完成库存追踪", landing)
        self.assertNotIn("一处完成库存追踪", landing)
        self.assertIn("--orange:#e9853d", landing)
        self.assertIn("background-image:var(--scanlines)", landing)

        console = self.client.get("/app").get_data(as_text=True)
        self.assertIn('id="homeLink"', console)
        self.assertIn("$('homeLink').onclick=()=>location.href=appUrl('')", console)
        self.assertEqual(console.count('data-section="'), 3)
        self.assertNotIn('id="adminCodesTab"', console)
        self.assertIn("['overview','users','targets']", console)
        self.assertIn("Promise.all([api(`api/admin/users", console)
        self.assertIn('id="createUserForm"', console)
        self.assertIn("api/admin/users',{method:'POST'", console)
        self.assertNotIn('id="registerTab"', console)
        self.assertLess(console.index('id="adminUsersPage"'), console.index('id="adminCodesPage"'))
        self.assertLess(console.index('id="adminCodesPage"'), console.index('id="adminTargetsPage"'))
        self.assertNotIn("/* Warm retro console theme */", console)
        self.assertIn(':root[data-theme="dark"]', console)
        self.assertIn("grid-template-columns:minmax(0,1fr) 76px", console)
        self.assertIn(".remark-edit{align-self:center;justify-self:center;margin:0", console)

    def test_three_state_theme_is_shared_early_and_system_aware(self):
        landing = self.client.get("/").get_data(as_text=True)
        console = self.client.get("/app").get_data(as_text=True)
        theme_response = self.client.get("/static/theme.js")

        self.assertEqual(theme_response.status_code, 200)
        script = theme_response.get_data(as_text=True)
        theme_response.close()
        for html in (landing, console):
            self.assertIn("cs2-inventory-theme", html)
            self.assertIn("data-theme-selector", html)
            self.assertIn('<option value="system">跟随系统</option>', html)
            self.assertIn('<option value="light">浅色</option>', html)
            self.assertIn('<option value="dark">深色</option>', html)
            self.assertIn("dataset.themePreference", html)
            self.assertIn("prefers-color-scheme: dark", html)
            self.assertIn(':root[data-theme="dark"]', html)
            self.assertIn("#0b0814", html)
            self.assertIn("#9b5cff", html)
            self.assertIn("#2dd4bf", html)
            self.assertLess(html.index("cs2-inventory-theme"), html.index("<style>"))
            self.assertLess(html.index("<style>"), html.index("<body"))

        self.assertEqual(landing.count("data-theme-selector"), 1)
        self.assertEqual(console.count("data-theme-selector"), 2)
        self.assertIn('const STORAGE_KEY = "cs2-inventory-theme"', script)
        self.assertIn('new Set(["system", "light", "dark"])', script)
        self.assertIn('window.matchMedia("(prefers-color-scheme: dark)")', script)
        self.assertIn('root.dataset.themePreference === "system"', script)
        self.assertIn('window.addEventListener("storage"', script)
        self.assertIn("localStorage.setItem", script)

    def test_theme_loader_uses_deployment_prefix_and_marketing_copy_has_semantic_lines(self):
        landing = self.client.get("/").get_data(as_text=True)
        console = self.client.get("/app/monitors/999/").get_data(as_text=True)

        for html in (landing, console):
            self.assertIn("location.pathname.match(/^(.*\\/)app", html)
            self.assertIn("script.src=`${base}static/theme.js`", html)
            self.assertNotIn('src="/static/theme.js"', html)

        self.assertIn(
            '<span>导入 SteamID64 统一查看库存与历史变化</span>'
            '<span>清晰记录交易保护资产 新增 移除和数量波动</span>',
            landing,
        )
        self.assertIn('<p class="lead semantic-lines">', landing)
        self.assertNotIn("trialNote", landing)
        self.assertIn("管理员确认后提供登录账户", landing)
        for old_copy in (
            "导入 SteamID64，统一查看库存",
            "流程清晰且无需反复操作。",
            "三种套餐均包含完整功能，",
            "我们会为你生成专属一次性邀请码。",
            "注册成功，请登录",
        ):
            self.assertNotIn(old_copy, landing)

    def test_public_landing_displays_masked_rmb_prices(self):
        landing = self.client.get("/").get_data(as_text=True)

        self.assertNotIn("以下套餐价格均为人民币", landing)
        self.assertIn('<span class="price-currency">¥</span>3xx<span class="price-unit">/ 月</span>', landing)
        self.assertIn('<span class="price-currency">¥</span>2xxx<span class="price-unit">/ 年</span>', landing)
        self.assertIn('<span class="price-currency">¥</span>xxxx<span class="price-unit">/ 永久</span>', landing)
        for exact_price in ("328", "2888", "8888"):
            self.assertNotIn(exact_price, landing)

    def test_public_landing_places_usage_notice_between_features_and_plans(self):
        landing = self.client.get("/").get_data(as_text=True)

        self.assertLess(landing.index('id="features"'), landing.index('id="usage"'))
        self.assertLess(landing.index('id="usage"'), landing.index('id="plans"'))
        self.assertIn("受到交易保护的物品查询涉及复杂的数据转换", landing)
        self.assertIn("首次提交一个 SteamID64 的库存查询通常需要 1 至 3 分钟", landing)
        self.assertIn("开始查询后任务会转入后台执行", landing)
        self.assertIn("期间可继续提交其他 SteamID64 并依次排队等待完成", landing)
        self.assertIn(".notice-grid{grid-template-columns:repeat(2,minmax(0,1fr))", landing)
        self.assertIn(".grid,.notice-grid{grid-template-columns:1fr}", landing)

    def test_logged_in_landing_has_one_console_action_and_detail_back_url_has_no_slash_404(self):
        landing = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('>获取套餐</a>', landing)
        self.assertIn("const [primary,...duplicates]=document.querySelectorAll('[data-auth]')", landing)
        self.assertIn("duplicates.forEach(b=>b.hidden=true)", landing)

        console = self.client.get("/app").get_data(as_text=True)
        self.assertIn("value.startsWith('?')?`app${value}`:`app/${value}`", console)
        self.assertEqual(self.client.get("/app/").status_code, 200)
        self.assertEqual(self.client.get("/app/monitors/999/").status_code, 200)

    def test_admin_create_requires_admin_and_csrf(self):
        payload = {
            "username": "customer_one",
            "password": "customer-pass-123",
            "plan": "monthly",
            "monitor_limit": 5,
        }
        self.assertEqual(self.client.post("/api/admin/users", json=payload).status_code, 401)
        token = self.login()
        response = self.client.post(
            "/api/admin/users", json=payload, headers={"X-CSRF-Token": token}
        )
        self.assertEqual(response.status_code, 403, response.get_json())

    def test_admin_creates_all_customer_plans_without_changing_existing_passwords(self):
        with self.app.app_context():
            before = {
                user.username: (user.password_hash, user.password_ciphertext)
                for user in User.query.order_by(User.id).all()
            }
        token = self.admin_login()
        created = {}
        for plan, monitor_limit in (("monthly", 3), ("annual", 8), ("permanent", 13)):
            response = self.client.post(
                "/api/admin/users",
                json={
                    "username": f"customer_{plan}",
                    "password": f"{plan}-customer-pass",
                    "plan": plan,
                    "monitor_limit": monitor_limit,
                },
                headers={"X-CSRF-Token": token},
            )
            self.assertEqual(response.status_code, 201, response.get_json())
            created[plan] = response.get_json()["user"]
            self.assertEqual(created[plan]["role"], "user")
            self.assertEqual(created[plan]["entitlement"]["kind"], "customer")
            self.assertEqual(created[plan]["entitlement"]["plan"], plan)
            self.assertEqual(created[plan]["entitlement"]["monitor_limit"], monitor_limit)
        self.assertIsNotNone(created["monthly"]["entitlement"]["expires_at"])
        self.assertIsNotNone(created["annual"]["entitlement"]["expires_at"])
        self.assertIsNone(created["permanent"]["entitlement"]["expires_at"])
        self.client.post("/api/auth/logout", headers={"X-CSRF-Token": token})
        self.login("customer_monthly", "monthly-customer-pass")
        with self.app.app_context():
            after = {
                user.username: (user.password_hash, user.password_ciphertext)
                for user in User.query.filter(User.username.in_(before)).all()
            }
            self.assertEqual(after, before)

    def test_admin_create_validates_fields_and_case_insensitive_uniqueness(self):
        token = self.admin_login()
        valid = {
            "username": "validation_user",
            "password": "customer-pass-123",
            "plan": "monthly",
            "monitor_limit": 5,
        }
        invalid = (
            {**valid, "username": "x"},
            {**valid, "password": "short"},
            {**valid, "plan": "trial"},
            {**valid, "monitor_limit": 0},
            {**valid, "monitor_limit": 10001},
            {**valid, "monitor_limit": True},
            {**valid, "monitor_limit": 1.5},
            {**valid, "role": "admin"},
            {**valid, "account_kind": "internal"},
        )
        for payload in invalid:
            response = self.client.post(
                "/api/admin/users", json=payload, headers={"X-CSRF-Token": token}
            )
            self.assertEqual(response.status_code, 400, (payload, response.get_json()))
        created = self.client.post(
            "/api/admin/users", json=valid, headers={"X-CSRF-Token": token}
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        duplicate = self.client.post(
            "/api/admin/users",
            json={**valid, "username": "VALIDATION_USER"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.get_json())

    def test_invitation_redemption_and_monitor_limit(self):
        self.admin_create("customer_invite", plan="monthly", monitor_limit=1)
        with self.app.app_context():
            admin = User.query.filter_by(username="cs2inventory_admin").one()
            _row, code = create_activation_code(admin, "monthly", 1)
        token = self.login("customer_invite", "customer-pass-123")
        redeemed = self.client.post(
            "/api/activation/redeem",
            json={"code": code},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(redeemed.status_code, 200, redeemed.get_json())
        self.assertEqual(redeemed.get_json()["user"]["entitlement"]["plan"], "monthly")
        first = self.client.post(
            "/api/monitors",
            json={"steamid": "76561199000000921"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(first.status_code, 201, first.get_json())
        second = self.client.post(
            "/api/monitors",
            json={"steamid": "76561199000000922"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(second.status_code, 409, second.get_json())
        replay = self.client.post(
            "/api/activation/redeem",
            json={"code": code},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(replay.status_code, 409, replay.get_json())

    def test_paid_grace_is_frozen_then_monitor_data_is_purged(self):
        with self.app.app_context():
            user = User(
                username="grace_user",
                password_hash=generate_password_hash("grace-pass-123"),
                role="user",
                account_kind="customer",
                plan="monthly",
                activated_at=utcnow() - timedelta(days=32),
                activation_expires_at=utcnow() + timedelta(days=1),
                monitor_limit=5,
            )
            db.session.add(user)
            db.session.commit()
            target, _job, _created = add_monitor(user, "76561199000000931")
            user.activation_expires_at = utcnow() - timedelta(days=1)
            old = store_snapshot(target, {
                "total_items": 1, "item_types": 1, "coverage": "ok", "elapsed_ms": 1,
                "errors": [], "_assets": [{"asset_key": "old", "name": "到期快照", "amount": 1, "sources": []}],
            })
            old.scanned_at = user.activation_expires_at - timedelta(minutes=1)
            newer = store_snapshot(target, {
                "total_items": 2, "item_types": 1, "coverage": "ok", "elapsed_ms": 1,
                "errors": [], "_assets": [{"asset_key": "new", "name": "到期后快照", "amount": 2, "sources": []}],
            })
            newer.scanned_at = user.activation_expires_at + timedelta(minutes=1)
            db.session.commit()
            target_id = target.id
        token = self.login("grace_user", "grace-pass-123")
        listing = self.client.get("/api/monitors").get_json()
        self.assertEqual(listing["entitlement"]["status"], "grace")
        self.assertEqual(listing["items"][0]["latest"]["total_items"], 1)
        self.assertEqual(
            self.client.patch(f"/api/monitors/{target_id}", json={"remark": "blocked"}, headers={"X-CSRF-Token": token}).status_code,
            403,
        )
        self.assertEqual(self.client.delete(f"/api/monitors/{target_id}", headers={"X-CSRF-Token": token}).status_code, 403)
        with self.app.app_context():
            user = User.query.filter_by(username="grace_user").one()
            user.activation_expires_at = utcnow() - timedelta(days=8)
            db.session.commit()
            result = cleanup_lifecycle()
            self.assertEqual(result["purged_customers"], 1)
            self.assertIsNotNone(User.query.filter_by(username="grace_user").first())
            self.assertEqual(Subscription.query.filter_by(user_id=user.id).count(), 0)

    def test_calendar_period_clamps_month_end_and_leap_day(self):
        january = datetime(2026, 1, 31, 4, 0, tzinfo=timezone.utc)
        self.assertEqual(add_natural_period(january, "monthly").astimezone(timezone(timedelta(hours=8))).day, 28)
        leap = datetime(2024, 2, 29, 4, 0, tzinfo=timezone.utc)
        annual = add_natural_period(leap, "annual").astimezone(timezone(timedelta(hours=8)))
        self.assertEqual((annual.year, annual.month, annual.day), (2025, 2, 28))


if __name__ == "__main__":
    unittest.main()
