"""History is read-only: open a completed fixture from page two without ever creating a query."""

import argparse
import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from browser_admin_workspace import Quiet
from playwright.sync_api import expect, sync_playwright
from test_admin_workspace import ADMIN, MEMBER, PASSWORD, fixture
from werkzeug.serving import make_server

from cs2_inventory.database import db
from cs2_inventory.models import ScanJob, User, utcnow


def run(channel, report, baseline):
    with TemporaryDirectory() as tmp:
        app = fixture(Path(tmp))
        with app.app_context():
            su = User.query.filter_by(username="cs2inventory_admin").one()
            user = User.query.filter_by(username=MEMBER).one()
            su.password_hash = user.password_hash

            def completed(owner, alias, kind="instant", expired=False):
                job = ScanJob(
                    requested_by=owner.id,
                    steamid="76561198000000001",
                    kind=kind,
                    status="completed",
                    finished_at=utcnow(),
                    expires_at=utcnow() + timedelta(days=-1 if expired else 1),
                    result_json=json.dumps(
                        {
                            "query_alias": alias,
                            "inventory_sections": {
                                "protected": {
                                    "total_items": 2,
                                    "items": [
                                        {"name": alias + " · 原始饰品", "count": 2}
                                    ],
                                },
                                "other": {"total_items": 0, "items": []},
                            },
                        },
                        ensure_ascii=False,
                    ),
                )
                db.session.add(job)
                db.session.flush()
                return job.id

            foreign = completed(user, "别的请求人")
            internal = completed(su, "非历史任务", kind="daily")
            expired = completed(su, "已过期的本地历史", expired=True)
            keys = [completed(su, f"本地历史{i:02}") for i in range(24)]
            db.session.commit()
            key = keys[3]
            count = ScanJob.query.count()
        srv = make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)
        thread = Thread(target=srv.serve_forever)
        thread.start()
        base = f"http://127.0.0.1:{srv.server_port}"
        result = {"phase": "before" if baseline else "after", "initial_jobs": count}
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel=channel)
                ctx = browser.new_context(
                    viewport={"width": 390, "height": 844},
                    has_touch=True,
                    locale="zh-CN",
                )

                def login(context, name):
                    token = context.request.get(base + "/api/bootstrap").json()[
                        "csrf_token"
                    ]
                    assert (
                        context.request.post(
                            base + "/api/auth/login",
                            data={"username": name, "password": PASSWORD},
                            headers={"X-CSRF-Token": token},
                        ).status
                        == 200
                    )

                login(ctx, "cs2inventory_admin")
                page = ctx.new_page()
                writes = []
                errors = []
                page.on(
                    "request",
                    lambda request: (
                        writes.append(request.method)
                        if "/api/" in request.url and request.method != "GET"
                        else None
                    ),
                )
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/app?view=queryHistory&query_page=2")
                expect(page.locator("#queryHistoryPage")).to_have_text("2 / 2")
                row = page.locator(".query-history-open").first
                expect(row).to_be_visible()
                native = row.evaluate("e=>e.tagName==='A' && !!e.href")
                row.focus()
                page.keyboard.press("Enter")
                visitor = page.locator(
                    ".query-body" if baseline else "#queryRecordBody"
                )
                expect(visitor).to_contain_text("本地历史03 · 原始饰品")
                selected_url = page.url
                params = parse_qs(urlsplit(selected_url).query)
                focused = page.evaluate(
                    "document.activeElement.id||document.activeElement.tagName"
                )
                page.reload()
                if baseline:
                    expect(page.locator("#queryView")).to_be_visible()
                else:
                    expect(visitor).to_contain_text("本地历史03 · 原始饰品")
                result.update(
                    native_row_link=native,
                    query=params,
                    after_open_focus=focused,
                    stable_on_reload=visitor.count() > 0
                    and "原始饰品" in visitor.first.inner_text(),
                    active_paid_query_control=page.locator("#adminQuery").is_visible(),
                )
                if not baseline:
                    assert native and params == {
                        "view": ["queryHistory"],
                        "query_page": ["2"],
                        "job": [str(key)],
                    }
                    assert (
                        focused == "queryRecordTitle"
                        and result["stable_on_reload"]
                        and not result["active_paid_query_control"]
                    )
                    expect(page).to_have_title("本地历史03 · 查询记录 · CS2 Inventory")
                    page.go_back()
                    expect(page.locator("#queryHistoryPage")).to_have_text("2 / 2")
                    expect(
                        page.locator(f".query-history-open[data-id='{key}']")
                    ).to_be_focused()
                    page.goto(selected_url)
                    expect(visitor).to_contain_text("本地历史03 · 原始饰品")
                    page.locator("#queryRecordBack").focus()
                    page.keyboard.press("Enter")
                    expect(page.locator("#queryHistoryPage")).to_have_text("2 / 2")
                    expect(
                        page.locator(f".query-history-open[data-id='{key}']")
                    ).to_be_focused()
                    # Open as a copied bookmark in an independent tab. It needs no navigation history.
                    tab = ctx.new_page()
                    tab.goto(selected_url)
                    expect(tab.locator("#queryRecordTitle")).to_have_text("本地历史03")
                    expect(tab.locator("#queryRecordBody")).to_contain_text("原始饰品")
                    tab.close()
                    path = "/api/admin/queries/"
                    assert ctx.request.get(base + path + str(foreign)).status == 404
                    assert ctx.request.get(base + path + str(internal)).status == 404
                    assert ctx.request.get(base + path + str(expired)).status == 410
                    for name, expected in [(None, 401), (ADMIN, 403), (MEMBER, 403)]:
                        c = browser.new_context()
                        if name:
                            login(c, name)
                        assert c.request.get(base + path + str(key)).status == expected
                        c.close()
                    page.goto(
                        base + f"/app?view=queryHistory&query_page=2&job={expired}"
                    )
                    expect(page.locator("#queryRecordStatus")).to_contain_text("过期")
                    assert not page.locator("#queryRecordRetry").is_visible()
                    assert page.locator("#queryRecordBack").is_visible()
                    page.route(
                        "**" + path + str(key),
                        lambda route: route.fulfill(
                            status=503,
                            content_type="application/json",
                            body='{"error":"fixture temporary failure"}',
                        ),
                    )
                    page.goto(selected_url)
                    expect(page.locator("#queryRecordRetry")).to_be_visible()
                    page.unroute("**" + path + str(key))
                    page.locator("#queryRecordRetry").focus()
                    page.keyboard.press("Enter")
                    expect(page.locator("#queryRecordBody")).to_contain_text(
                        "本地历史03 · 原始饰品"
                    )
                    expect(page.locator("#queryRecordTitle")).to_be_focused()
                    result.update(
                        browser_back_focused_origin=True,
                        explicit_return_focused_origin=True,
                        independent_bookmark=True,
                        private_read_scopes=[401, 403, 403, 404, 404, 410],
                        temporary_error_retry_get_only=True,
                    )
                ctx.close()
                browser.close()
                result["api_writes_during_history"] = writes
                result["javascript_errors"] = errors
                assert not writes and not errors
                with app.app_context():
                    result["final_jobs"] = ScanJob.query.count()
                    assert result["final_jobs"] == count
        finally:
            srv.shutdown()
            thread.join()
            srv.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    if report:
        report.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
        )
    print("ARCHIVED QUERY:", result["phase"], "no writes; jobs unchanged; Chrome")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--channel", default="chrome")
    p.add_argument("--report", type=Path)
    p.add_argument("--baseline", action="store_true")
    args = p.parse_args()
    run(args.channel, args.report, args.baseline)
