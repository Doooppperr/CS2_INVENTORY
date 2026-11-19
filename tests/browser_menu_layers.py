"""Open the real account menu where the sticky section bar crosses it; click actual painted pixels."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from browser_monitor_workbench import WorkbenchBrowserTests
from playwright.sync_api import expect

from cs2_inventory.database import db
from cs2_inventory.models import User

CONTROLS = ["#navSwitch", "#accountMenu select", "#navPassword", "#homeLink", "#navLogout"]
MEASURE = """e => {
  const rect=e.getBoundingClientRect(), jumps=document.querySelector('.dashboard-jumps').getBoundingClientRect();
  const top=Math.max(rect.top,jumps.top), bottom=Math.min(rect.bottom,jumps.bottom);
  const x=(rect.left+rect.right)/2,y=bottom>top?(top+bottom)/2:(rect.top+rect.bottom)/2;
  const target=document.elementFromPoint(x,y);
  return {label:e.id||e.tagName,x,y,overlaps:bottom>top,ours:e.contains(target),hit:target?.closest('a,button,select,nav')?.id||target?.closest('nav')?.className||target?.tagName};
}"""


def auth(context, username):
    token = context.request.get("/api/bootstrap").json()["csrf_token"]
    result = context.request.post("/api/auth/login", data={"username": username, "password": "browser-fixture"},
                                  headers={"X-CSRF-Token": token})
    assert result.ok


def open_menu(page):
    page.goto("/app/dashboard")
    expect(page.locator("#signalUnits")).to_have_text("17")
    page.locator("#accountMenu>summary").click()
    expect(page.locator("#savedAccounts")).to_have_attribute("aria-busy", "false")


def raw_click(page, metric, touch):
    device = page.touchscreen if touch else page.mouse
    (device.tap if touch else device.click)(metric["x"], metric["y"])


def check_size(cls, width, height, baseline, out):
    touch = width < 500
    theme = "light" if width == 390 else "dark"
    with cls.browser.new_context(base_url=cls.base, viewport={"width": width, "height": height},
                                 device_scale_factor=1.5 if width == 1468 else 1, has_touch=touch,
                                 locale="zh-CN", timezone_id="Asia/Shanghai", color_scheme=theme) as ctx:
        auth(ctx, "演示账号")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        open_menu(page)
        hits = [page.locator(s).evaluate(MEASURE) for s in CONTROLS]
        assert any(hit["overlaps"] for hit in hits)
        if out:
            stage = "before" if baseline else "after"
            page.screenshot(path=str(out / f"menu-{stage}-{width}-{theme}.png"))
        home = hits[3]
        assert home["overlaps"], "This scenario must actually exercise the sticky bar covering the link."
        raw_click(page, home, touch)
        if home["ours"]:
            expect(page).to_have_url(cls.base + "/")
        else:
            expect(page.locator("#accountMenu")).not_to_have_attribute("open", "")
        reached_home = urlsplit(page.url).path == "/"
        result = {"theme": theme, "width": width, "elements": hits, "raw_click_reached_home": reached_home}
        if baseline:
            assert not home["ours"] and not reached_home and home["hit"] == "dashboard-jumps"
            return result

        assert all(hit["ours"] for hit in hits) and reached_home
        open_menu(page)
        page.locator("#navPassword").click()
        expect(page.locator("#selfPasswordDialog")).to_be_visible()
        page.keyboard.press("Escape")
        trigger = page.locator("#accountMenu>summary")
        expect(trigger).to_be_focused()
        trigger.press("Enter")
        for _ in range(4):
            page.keyboard.press("Tab")
        expect(page.locator("#homeLink")).to_be_focused()
        page.keyboard.press("Escape")
        expect(trigger).to_be_focused()
        expect(page.locator("#accountMenu")).not_to_have_attribute("open", "")
        trigger.press("Enter")
        page.mouse.click(2, 2)
        expect(page.locator("#accountMenu")).not_to_have_attribute("open", "")

        # The bar still works as a sticky control when the menu is closed.
        page.locator('.dashboard-jumps a[href="#trendHeading"]').click()
        expect(page.locator("#trendHeading")).to_be_focused()
        assert abs(page.locator(".dashboard-jumps").bounding_box()["y"]) <= 1
        result["keyboard_escape_and_sticky_anchor"] = True

        # The bar becomes sticky across an existing menu when the logo scrolls off screen.
        open_menu(page)
        page.evaluate("y=>window.scrollTo({top:y,behavior:'instant'})",
                      page.locator("#homeLink").bounding_box()["y"] - 8)
        scrolled = page.locator("#homeLink").evaluate(MEASURE)
        assert scrolled["overlaps"] and scrolled["ours"] and page.evaluate("scrollY") > 0
        raw_click(page, scrolled, touch)
        expect(page).to_have_url(cls.base + "/")
        result["scrolled_menu_hit"] = scrolled
        assert not errors
        return result


def check_long_menu(cls):
    names = [f"menu_layer_fixture_{n}" for n in range(9)]
    with cls.app.app_context():
        source = User.query.filter_by(username="演示账号").one()
        db.session.add_all(User(username=n, account_kind="internal", password_hash=source.password_hash) for n in names)
        db.session.commit()
    with cls.browser.new_context(base_url=cls.base, viewport={"width": 320, "height": 568}, has_touch=True,
                                 locale="zh-CN", color_scheme="dark") as ctx:
        for name in [*names, "演示账号"]:
            auth(ctx, name)
        page = ctx.new_page()
        open_menu(page)
        expect(page.locator("[data-switch-user]")).to_have_count(len(names))
        panel = page.locator(".account-menu-body")
        panel.hover()
        # Scroll just the long menu, then make a second wheel gesture at its boundary.
        page.mouse.wheel(0, 1300)
        page.wait_for_function("()=>{const p=document.querySelector('.account-menu-body');return p.scrollTop>0&&p.scrollHeight-p.scrollTop-p.clientHeight<2}")
        page.mouse.wheel(0, 540)
        # Let a native scroll sequence settle before testing whether the page moved.
        page.wait_for_timeout(250)
        assert page.evaluate("scrollY") == 0, "A menu scroll must not carry the dashboard away."
        for key in ("#navSwitch", "#navPassword", "#homeLink", "#navLogout"):
            m = page.locator(key).evaluate(MEASURE)
            assert m["ours"], m
        raw_click(page, page.locator("#navLogout").evaluate(MEASURE), True)
        expect(page.locator("#authDialog")).to_be_visible()
        return {"saved_accounts": len(names), "page_scroll_at_menu_boundary": 0, "logout_tap": True}


def run(baseline=False, output=None):
    if output:
        output.mkdir(parents=True, exist_ok=True)
    cls = WorkbenchBrowserTests
    cls.setUpClass()
    try:
        report = {"cases": [check_size(cls, w, h, baseline, output)
                            for w, h in [(1468, 864), (390, 844), (320, 740)]]}
        if not baseline:
            report["long_menu"] = check_long_menu(cls)
    finally:
        cls.tearDownClass()
    if output:
        (output / ("before.json" if baseline else "after.json")).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print("menu_link_click=blocked; measured_widths=1468,390,320" if baseline else
          "menu_link_click=opens_home; measured_widths=1468,390,320; keyboard_sticky_long_menu=pass")


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--channel", default="chrome")
    cli.add_argument("--baseline", action="store_true")
    cli.add_argument("--output", type=Path)
    options = cli.parse_args()
    run(options.baseline, options.output)
