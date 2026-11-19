"""实际普通管理员：客户权益、限额修改与按订阅者查看全部监控。"""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import expect, sync_playwright
from test_admin_workspace import ADMIN, MEMBER, PASSWORD, fixture
from werkzeug.serving import make_server

from cs2_inventory.database import db
from cs2_inventory.models import SteamTarget, Subscription, User


def run(channel):
    with TemporaryDirectory() as tmp:
        app = fixture(Path(tmp))
        with app.app_context():
            u = User.query.filter_by(username=MEMBER).one()
            u.account_kind = "customer"
            u.plan = "monthly"
            u.monitor_limit = 15
            db.session.add(Subscription(user_id=u.id, target_id=SteamTarget.query.first().id, remark="客户专仓"))
            uid = u.id
            db.session.commit()
        srv = make_server("127.0.0.1", 0, app, threaded=True)
        th = Thread(target=srv.serve_forever)
        th.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel=channel)
                ctx = browser.new_context(base_url=f"http://127.0.0.1:{srv.server_port}", locale="zh-CN",
                                          viewport={"width":320, "height":740})
                token = ctx.request.get("/api/bootstrap").json()["csrf_token"]
                assert ctx.request.post("/api/auth/login", data={"username":ADMIN, "password":PASSWORD},
                                        headers={"X-CSRF-Token":token}).ok
                p = ctx.new_page()
                p.goto(f"/app?view=admin&section=users&user_q={MEMBER}")
                row = p.locator(f'#users tr[data-customer="{uid}"]')
                expect(row).to_contain_text("已到期")
                expect(row).to_contain_text("月度")
                edits = []
                p.on("request", lambda req: edits.append(req.post_data_json) if req.method=="PATCH" else None)
                summ = row.locator(".limit-editor summary")
                summ.click()
                field = row.locator(".user-limit")
                expect(field).to_have_accessible_name(f"{MEMBER} 的新限额")
                field.fill("")
                field.press("Enter")
                assert not field.evaluate("e=>e.validity.valid") and not edits
                field.fill("8")
                field.press("Enter")
                expect(row.locator(".limit-result")).to_have_text("限额已更新为 8")
                expect(summ).to_be_focused()
                assert edits == [{"monitor_limit":8}]
                p.reload()
                expect(row.locator(".limit-summary b")).to_have_text("8")
                row.locator(".customer-monitors").click()
                expect(p.locator("#adminTargets")).to_contain_text("客户专仓")
                assert f"subscriber_id={uid}" in p.url
                p.go_back()
                expect(p.locator("#adminUserSearch")).to_have_value(MEMBER)
                assert p.evaluate("document.documentElement.scrollWidth===innerWidth")
                ctx.close()
                browser.close()
        finally:
            srv.shutdown()
            th.join()
            srv.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    print("expired_zh=PASS; blank_patch=0; enter_patch=1; persisted_limit=8; correct_subscriber_scope=PASS")


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--channel", default="chrome")
    run(cli.parse_args().channel)
