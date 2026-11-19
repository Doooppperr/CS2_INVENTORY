"""窄屏上区分同名前缀的已登录账号，通过搜索定位后直接切换。"""
from browser_monitor_workbench import WorkbenchBrowserTests
from playwright.sync_api import expect

from cs2_inventory.database import db
from cs2_inventory.models import User


def run():
    cls = WorkbenchBrowserTests
    cls.setUpClass()
    try:
        check = cls()
        check.setUp()
        try:
            names = [f"collection_workgroup_eastzone_{i:02}" for i in range(6)]
            with cls.app.app_context():
                seed = User.query.filter_by(username="演示账号").one()
                for username in names:
                    db.session.add(User(username=username, password_hash=seed.password_hash, account_kind="internal"))
                db.session.commit()
            page = check.page
            for username in [*names, "演示账号"]:
                api = page.request
                token = api.get("/api/bootstrap").json()["csrf_token"]
                assert api.post("/api/auth/login", data={"username": username, "password": "browser-fixture"},
                                headers={"X-CSRF-Token": token}).ok
            page.set_viewport_size({"width": 320, "height": 740})
            page.goto("/app/dashboard")
            page.locator("#accountMenu > summary").click()
            field = page.locator("#savedAccountSearch")
            expect(field).to_be_visible()
            field.fill("zone_04")
            choice = page.locator("[data-switch-user]:visible")
            expect(choice).to_have_count(1)
            expect(choice).to_have_text(names[4])
            field.press("Enter")
            expect(choice).to_be_focused()
            page.add_style_tag(content="#accountMenu button{font-size:24px!important}")
            assert choice.evaluate("b=>b.clientWidth>=b.scrollWidth&&b.clientHeight>44")
            choice.press("Enter")
            expect(page.locator("#navUsername")).to_have_text(names[4])
            expect(page.locator("#switchAccountDialog")).to_be_hidden()
            print("similar_account_names=PASS; filtered_choice=PASS; switch_without_password=PASS")
        finally:
            check.tearDown()
    finally:
        cls.tearDownClass()


if __name__ == "__main__":
    run()
