from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from werkzeug.security import generate_password_hash

from cs2_inventory.app import create_app
from cs2_inventory.database import db
from cs2_inventory.entitlements import (
    add_natural_period,
    cleanup_lifecycle,
    create_activation_code,
    entitlement_state,
)
from cs2_inventory.models import Snapshot, SteamTarget, Subscription, User, utcnow
from cs2_inventory.services import add_monitor, store_snapshot
from cs2_inventory.worker import process_job

FAKE_INVENTORY = {
    "steamid": "76561199000000901",
    "protected_live": [],
    "public": [{"name": "测试物品", "count": 1, "assetids": ["asset-1"], "sources": ["public"]}],
    "coverage": {"status": "complete"},
    "elapsed_ms": 12,
    "errors": [],
    "sources": {"inventory": {"requests": 1}},
}


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

    def register(self, username="trial_user"):
        token = self.csrf()
        response = self.client.post(
            "/api/auth/register",
            json={"username": username, "password": "trial-pass-123"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 201, response.get_json())

    def login(self, username="trial_user", password="trial-pass-123"):
        response = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["csrf_token"]

    def admin_login(self):
        return self.login("cs2inventory_admin", "platform-test-password")

    def test_registration_creates_seven_day_trial_and_public_landing(self):
        self.register()
        token = self.login()
        bootstrap = self.client.get("/api/bootstrap").get_json()
        entitlement = bootstrap["user"]["entitlement"]
        self.assertEqual(entitlement["kind"], "trial")
        self.assertEqual(entitlement["status"], "trial_registered")
        self.assertTrue(entitlement["can_add_monitor"])
        self.assertIsNotNone(entitlement["trial"]["registration_expires_at"])
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertIn("star0718@outlook.com", self.client.get("/").get_data(as_text=True))
        self.assertEqual(self.client.get("/app").status_code, 200)
        self.client.post("/api/auth/logout", headers={"X-CSRF-Token": token})

    def test_landing_login_accepts_legacy_short_password_but_registration_requires_eight(self):
        landing = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="password" type="password" minlength="1"', landing)
        self.assertIn("password.minLength=register?8:1", landing)
        self.assertIn("password.autocomplete=register?'new-password':'current-password'", landing)

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

        register = self.client.post(
            "/api/auth/register",
            json={"username": "short_password", "password": "123456"},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(register.status_code, 400, register.get_json())

    def test_warm_landing_and_three_part_admin_console_navigation(self):
        landing = self.client.get("/").get_data(as_text=True)
        self.assertIn("开始免费体验", landing)
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
        self.assertIn('<p id="trialNote" class="trial-note semantic-lines"', landing)
        for old_copy in (
            "导入 SteamID64，统一查看库存",
            "流程清晰且无需反复操作。",
            "三种套餐均包含完整功能，",
            "我们会为你生成专属一次性邀请码。",
            "注册成功，请登录",
        ):
            self.assertNotIn(old_copy, landing)

    def test_public_landing_displays_locked_rmb_prices(self):
        landing = self.client.get("/").get_data(as_text=True)

        self.assertIn("以下套餐价格均为人民币", landing)
        self.assertIn('<span class="price-currency">¥</span>328<span class="price-unit">/ 月</span>', landing)
        self.assertIn('<span class="price-currency">¥</span>2888<span class="price-unit">/ 年</span>', landing)
        self.assertIn('<span class="price-currency">¥</span>8888<span class="price-unit">/ 永久</span>', landing)
        self.assertNotIn("具体价格暂不公开", landing)

    def test_logged_in_landing_has_one_console_action_and_detail_back_url_has_no_slash_404(self):
        landing = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('>获取套餐</a>', landing)
        self.assertIn("const [primary,...duplicates]=document.querySelectorAll('[data-auth]')", landing)
        self.assertIn("duplicates.forEach(b=>b.hidden=true)", landing)

        console = self.client.get("/app").get_data(as_text=True)
        self.assertIn("value.startsWith('?')?`app${value}`:`app/${value}`", console)
        self.assertEqual(self.client.get("/app/").status_code, 200)
        self.assertEqual(self.client.get("/app/monitors/999/").status_code, 200)

    def test_successful_trial_is_pinned_for_seven_days_and_cannot_reimport(self):
        self.register()
        token = self.login()
        added = self.client.post(
            "/api/monitors",
            json={"steamid": "76561199000000901"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(added.status_code, 201, added.get_json())
        job_id = added.get_json()["job_id"]
        with self.app.app_context(), mock.patch(
            "cs2_inventory.worker.run_max_coverage_query", return_value=FAKE_INVENTORY
        ), mock.patch("cs2_inventory.worker.fetch_persona_name", return_value="Trial Steam"):
            process_job(job_id)
            user = User.query.filter_by(username="trial_user").one()
            self.assertEqual(entitlement_state(user), "trial_result")
            result_snapshot_id = user.trial_experience.result_snapshot_id
            target = SteamTarget.query.filter_by(steamid="76561199000000901").one()
            store_snapshot(target, {
                "total_items": 2,
                "item_types": 1,
                "coverage": "ok",
                "elapsed_ms": 1,
                "errors": [],
                "_assets": [{"asset_key": "newer", "name": "后续物品", "amount": 2, "sources": []}],
            })
            db.session.commit()
            self.assertNotEqual(Snapshot.query.order_by(Snapshot.id.desc()).first().id, result_snapshot_id)

        listing = self.client.get("/api/monitors").get_json()
        self.assertEqual(listing["items"][0]["latest"]["total_items"], 1)
        target_id = listing["items"][0]["id"]
        response = self.client.patch(
            f"/api/monitors/{target_id}",
            json={"remark": "我的主号"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["monitor"]["label"], "我的主号 -（Trial Steam）- 76561199000000901")
        second = self.client.post(
            "/api/monitors",
            json={"steamid": "76561199000000902"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(second.status_code, 409, second.get_json())
        self.assertEqual(self.client.delete(f"/api/monitors/{target_id}", headers={"X-CSRF-Token": token}).status_code, 200)
        again = self.client.post(
            "/api/monitors",
            json={"steamid": "76561199000000902"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(again.status_code, 409, again.get_json())

    def test_failed_trial_can_delete_and_switch_target(self):
        self.register()
        token = self.login()
        first = self.client.post(
            "/api/monitors",
            json={"steamid": "76561199000000911"},
            headers={"X-CSRF-Token": token},
        ).get_json()
        with self.app.app_context():
            user = User.query.filter_by(username="trial_user").one()
            from cs2_inventory.models import ScanJob

            job = db.session.get(ScanJob, first["job_id"])
            job.status = "failed"
            job.error = "private inventory"
            db.session.commit()
            target_id = user.trial_experience.current_target_id
        self.assertEqual(self.client.delete(f"/api/monitors/{target_id}", headers={"X-CSRF-Token": token}).status_code, 200)
        second = self.client.post(
            "/api/monitors",
            json={"steamid": "76561199000000912"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(second.status_code, 201, second.get_json())

    def test_expired_trial_deletes_account_and_allows_same_username_again(self):
        self.register()
        token = self.login()
        added = self.client.post(
            "/api/monitors",
            json={"steamid": "76561199000000915"},
            headers={"X-CSRF-Token": token},
        ).get_json()
        fake = {**FAKE_INVENTORY, "steamid": "76561199000000915"}
        with self.app.app_context(), mock.patch(
            "cs2_inventory.worker.run_max_coverage_query", return_value=fake
        ), mock.patch("cs2_inventory.worker.fetch_persona_name", return_value="Expired Trial"):
            process_job(added["job_id"])
            user = User.query.filter_by(username="trial_user").one()
            user.trial_experience.result_expires_at = utcnow() - timedelta(seconds=1)
            db.session.commit()
            result = cleanup_lifecycle()
            self.assertEqual(result["deleted_trial_users"], 1)
            self.assertIsNone(User.query.filter_by(username="trial_user").first())
            self.assertIsNone(SteamTarget.query.filter_by(steamid="76561199000000915").first())
        self.register()

    def test_invitation_redemption_and_monitor_limit(self):
        self.register()
        with self.app.app_context():
            admin = User.query.filter_by(username="cs2inventory_admin").one()
            _row, code = create_activation_code(admin, "monthly", 1)
        token = self.login()
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
