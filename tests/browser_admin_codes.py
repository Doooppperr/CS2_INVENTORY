"""普通管理员实际浏览全量续期码、组合搜索，核对定位、撤销与恢复。"""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright
from test_admin_code_history import add_rows
from test_admin_workspace import ADMIN, MEMBER, PASSWORD, fixture
from werkzeug.serving import WSGIRequestHandler, make_server

from cs2_inventory.database import db


class Quiet(WSGIRequestHandler):
    def log(self, *args):
        pass


def run(channel, baseline, output):
    with TemporaryDirectory() as tmp:
        app = fixture(Path(tmp))
        add_rows(app)
        srv = make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)
        thread = Thread(target=srv.serve_forever)
        thread.start()
        origin = f"http://127.0.0.1:{srv.server_port}"
        history = "/api/admin/activation-codes"
        report = {}
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel=channel)
                ctx = browser.new_context(
                    base_url=origin,
                    locale="zh-CN",
                    viewport={"width": 390, "height": 844},
                )
                token = ctx.request.get("/api/bootstrap").json()["csrf_token"]
                assert ctx.request.post(
                    "/api/auth/login",
                    data={"username": ADMIN, "password": PASSWORD},
                    headers={"X-CSRF-Token": token},
                ).ok
                p = ctx.new_page()
                errors = []
                deletes = []
                p.on("pageerror", lambda e: errors.append(str(e)))
                p.on(
                    "request",
                    lambda req: (
                        deletes.append(req.url) if req.method == "DELETE" else None
                    ),
                )
                p.goto("/app?view=admin&section=users&user_task=codes&code_page=2")
                rows = p.locator("#activationCodes tbody tr")
                expect(rows).to_have_count(20 if baseline else 6)
                report.update(
                    visible_at_page2=rows.count(),
                    has_page_picker=p.locator("#codePager:visible").count() == 1,
                    translated_label="已兑换"
                    in p.locator("#activationCodes").inner_text(),
                )
                if not baseline:
                    expect(p.locator("#codePage")).to_have_text("2 / 2")
                    p.reload()
                    expect(rows).to_have_count(6)
                    p.locator("#codeStatusFilter").select_option("redeemed")
                    expect(rows).to_have_count(1)
                    expect(rows).to_contain_text(MEMBER)
                    expect(rows).to_contain_text("年度")
                    search = p.locator("#codeSearch")
                    p.locator("#codeStatusFilter").select_option("unused")
                    search.fill("cs2-0000")
                    search.press("Enter")
                    expect(rows).to_have_count(1)
                    expect(rows).to_contain_text("CS2-0000-CAFE")
                    assert parse_qs(urlsplit(p.url).query)["code_q"] == ["CS2-0000"]
                    button = rows.locator(".code-revoke")
                    messages = []

                    def dismiss(dialog):
                        messages.append(dialog.message)
                        dialog.dismiss()

                    p.once("dialog", dismiss)
                    button.click()
                    assert (
                        not deletes
                        and "CS2-0000-CAFE" in messages[0]
                        and "年度" in messages[0]
                        and "限额 5 个" in messages[0]
                    )
                    p.once("dialog", lambda dialog: dialog.accept())
                    button.click()
                    expect(p.locator("#codeActionStatus")).to_be_focused()
                    expect(p.locator("#codeActionStatus")).to_contain_text(
                        "CS2-0000-CAFE"
                    )
                    expect(rows).to_have_count(0)
                    assert len(deletes) == 1
                    p.locator("#codeStatusFilter").select_option("revoked")
                    expect(rows).to_have_count(1)
                    expect(rows).to_contain_text("已撤销")
                    route = "**/api/admin/activation-codes?*"
                    p.route(
                        route,
                        lambda rt: rt.fulfill(status=503, json={"error": "临时中断"}),
                    )
                    p.reload()
                    expect(p.locator("#codeRetry")).to_be_visible()
                    expect(rows).to_have_count(0)
                    assert search.input_value() == "CS2-0000"
                    p.unroute(route)
                    p.locator("#codeRetry").click()
                    expect(rows).to_have_count(1)
                    expect(p.locator("#codeListStatus")).to_be_focused()
                    p.locator("#codeClear").click()
                    expect(rows).to_have_count(20)
                    # Only the POST reveals this one-time value, never the GET projection or history UI.
                    p.locator("#createCode").click()
                    issued = p.locator("#createdCode")
                    expect(issued).to_be_visible()
                    raw = issued.inner_text()
                    assert raw and raw not in ctx.request.get(history).text()
                    assert raw not in p.locator("#activationCodes").inner_text()
                    p.locator("[data-admin-user-task=list]").click()
                    p.locator("[data-admin-user-task=codes]").click()
                    expect(p.locator("#issuedCodePanel")).to_be_hidden()
                    p.set_viewport_size({"width": 320, "height": 740})
                    assert p.evaluate(
                        "document.documentElement.scrollWidth<=innerWidth"
                    )
                    report.update(
                        cancel_deletes=0,
                        confirmed_deletes=1,
                        named_confirmation=True,
                        lookup_oldest=True,
                        recovered_same_filters=True,
                        public_history_contains_raw=False,
                        mobile_overflow=0,
                    )
                assert not errors, errors
                browser.close()
        finally:
            srv.shutdown()
            thread.join()
            srv.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    if output:
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
        )
    print(
        "history_page2_rows="
        + str(report["visible_at_page2"])
        + "; pagination="
        + str(report["has_page_picker"])
        + "; translated="
        + str(report["translated_label"])
    )


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--channel", default="chrome")
    cli.add_argument("--baseline", action="store_true")
    cli.add_argument("--out", type=Path)
    args = cli.parse_args()
    run(args.channel, args.baseline, args.out)
