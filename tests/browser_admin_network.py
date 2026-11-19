"""Slow, overlapping and failed reads through real local admin APIs; no real accounts or destructive clicks."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from browser_admin_workspace import Quiet
from playwright.sync_api import expect, sync_playwright
from test_admin_workspace import ADMIN, PASSWORD, fixture
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

from cs2_inventory.database import db
from cs2_inventory.models import User

HOLD_ONE = """(() => {
  const native=window.fetch.bind(window);
  window.fetch=async (input,opts)=>{
    const u=new URL(typeof input==='string'?input:input.url||input,location.href),g=window.nextTestRead;
    if(g && u.pathname===g.path && (u.searchParams.get('q')||'')===g.q && u.searchParams.get('page')===String(g.page)){
      window.nextTestRead=null;const real=await native(input,opts);
      return new Promise((ok,fail)=>{window.releaseTestRead=success=>{window.releaseTestRead=null;
        if(success)ok(real);else fail(new TypeError('delayed read failed'));};});
    }
    return native(input,opts);
  };
})();"""


def arm(page, path, q, page_no=1):
    page.evaluate("g=>window.nextTestRead=g", {"path": path, "q": q, "page": page_no})


def settle(page, success):
    page.wait_for_function("typeof window.releaseTestRead==='function'")
    page.evaluate("v=>window.releaseTestRead(v)", success)


def check_table(page, origin, section, area, prefix, stem, name):
    path = "/api/admin/" + section
    page.goto(origin + "/app?view=admin&section=" + section)
    table = page.locator("#" + area)
    rows = table.locator("article" if section == "targets" else "tbody tr")
    search = page.locator("#" + prefix + "Search")
    clear = page.locator("#" + prefix + "Clear")
    retry = page.locator("#" + prefix + "Retry")
    status = page.locator("#" + prefix + "Status")
    prev = page.locator("#" + stem + "Prev")
    next_ = page.locator("#" + stem + "Next")
    expect(rows).to_have_count(20)
    arm(page, path, name)
    search.fill(name)
    # The input immediately retires stale interactive rows, even before its debounced GET.
    expect(table).to_have_attribute("aria-busy", "true")
    expect(rows).to_have_count(0)
    expect(search).to_be_focused()
    expect(prev).to_be_disabled()
    expect(next_).to_be_disabled()
    page.wait_for_function("typeof releaseTestRead==='function'")
    expect(status).to_contain_text(name)
    assert table.locator("button,input").count() == 0
    settle(page, True)
    expect(table).to_have_attribute("aria-busy", "false")
    expect(rows).to_have_count(1)
    # A superseded failure must leave the newer cleared list and feedback untouched.
    arm(page, path, "stale")
    search.fill("stale")
    search.press("Enter")
    page.wait_for_function("typeof releaseTestRead==='function'")
    clear.click()
    expect(rows).to_have_count(20)
    settle(page, False)
    expect(retry).to_be_hidden()
    expect(table).to_have_attribute("aria-busy", "false")
    expect(search).to_be_focused()
    expect(status).to_contain_text("共")
    expect(page.locator("#toast")).to_be_hidden()
    # An actual current failure gives a local keyboard-operable retry.
    arm(page, path, name)
    search.fill(name)
    search.press("Enter")
    settle(page, False)
    expect(retry).to_be_visible()
    expect(rows).to_have_count(0)
    expect(status).to_contain_text("读取失败")
    retry.focus()
    retry.press("Enter")
    expect(rows).to_have_count(1)
    expect(retry).to_be_hidden()
    expect(search).to_be_focused()
    # Page changes announce the requested page and no previous rows are actionable.
    clear.click()
    expect(rows).to_have_count(20)
    arm(page, path, "", 2)
    next_.focus()
    next_.press("Enter")
    page.wait_for_function("typeof releaseTestRead==='function'")
    expect(status).to_be_focused()
    expect(status).to_contain_text("第 2 页")
    expect(rows).to_have_count(0)
    expect(next_).to_be_disabled()
    settle(page, True)
    expect(rows).to_have_count(20)
    expect(page.locator("#" + stem + "PageLabel")).to_have_text("2 / 3")
    return dict(
        table=section,
        stale_row_actions=0,
        pager_locked=True,
        stale_failure_ignored=True,
        current_failure_retry=True,
        search_focus_preserved=True,
        pager_focus_moved_to_result=True,
    )


def run(channel, report):
    with TemporaryDirectory() as tmp:
        app = fixture(Path(tmp))
        with app.app_context():
            h = generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000")
            db.session.add_all(
                [
                    User(
                        username="admin_find_needle" if not i else f"load_user_{i}",
                        role="user",
                        account_kind="internal",
                        password_hash=h,
                    )
                    for i in range(45)
                ]
            )
            db.session.commit()
        srv = make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)
        thread = Thread(target=srv.serve_forever)
        thread.start()
        origin = f"http://127.0.0.1:{srv.server_port}"
        results = []
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel=channel)
                ctx = browser.new_context(
                    viewport={"width": 390, "height": 844}, locale="zh-CN"
                )
                ctx.add_init_script(HOLD_ONE)
                page = ctx.new_page()
                page.goto(origin + "/?auth=login")
                page.locator("#username").fill(ADMIN)
                page.locator("#password").fill(PASSWORD)
                page.locator("#authSubmit").click()
                expect(page.locator("#navUsername")).to_have_text(ADMIN)
                writes = []
                page.on(
                    "request",
                    lambda r: (
                        writes.append(r.method)
                        if "/api/admin/" in r.url and r.method != "GET"
                        else None
                    ),
                )
                results.append(
                    check_table(
                        page,
                        origin,
                        "targets",
                        "adminTargets",
                        "adminTarget",
                        "target",
                        "Needle",
                    )
                )
                results.append(
                    check_table(
                        page,
                        origin,
                        "users",
                        "users",
                        "adminUser",
                        "user",
                        "admin_find_needle",
                    )
                )
                assert not writes
                ctx.close()
                browser.close()
        finally:
            srv.shutdown()
            thread.join()
            srv.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
        if report:
            report.write_text(
                json.dumps(dict(results=results, admin_writes=0), indent=2) + "\n",
                encoding="utf8",
            )
        print(
            "ADMIN NETWORK: both lists clear stale actions / stale failures ignored / keyboard retry / page announcement=PASS; writes=0"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default="chrome")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    run(args.channel, args.report)
