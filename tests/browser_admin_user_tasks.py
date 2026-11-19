"""真实 Chrome + SQLite：按客户任务工作，不在此测试中读取真实客户账户。"""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from browser_admin_workspace import Quiet
from playwright.sync_api import expect, sync_playwright
from test_admin_workspace import ADMIN, PASSWORD, fixture
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

from cs2_inventory.database import db
from cs2_inventory.models import User


def run(channel, report):
    with TemporaryDirectory() as tmp:
        app = fixture(Path(tmp))
        with app.app_context():
            hashed = generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000")
            db.session.add_all(
                [
                    User(
                        username="Needle_client" if not i else f"qa_client_{i:02}",
                        role="user",
                        account_kind="internal",
                        password_hash=hashed,
                    )
                    for i in range(45)
                ]
            )
            db.session.commit()
        srv = make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)
        thread = Thread(target=srv.serve_forever)
        thread.start()
        origin = f"http://127.0.0.1:{srv.server_port}"
        result = {"viewports": []}
        try:
            with sync_playwright() as pw:
                b = pw.chromium.launch(channel=channel)
                c = b.new_context(
                    viewport={"width": 320, "height": 720}, locale="zh-CN"
                )
                page = c.new_page()
                page.goto(origin + "/?auth=login")
                page.locator("#username").fill(ADMIN)
                page.locator("#password").fill(PASSWORD)
                page.locator("#authSubmit").click()
                expect(page.locator("#navUsername")).to_have_text(ADMIN)
                page.goto(origin + "/app?view=admin&section=users&user_page=3")
                expect(page.locator("#userPageLabel")).to_have_text("3 / 3")
                for w, h in ((320, 720), (390, 844), (1440, 960)):
                    page.set_viewport_size({"width": w, "height": h})
                    task = page.locator("[data-admin-user-task=codes]")
                    y = round(
                        task.evaluate("el=>el.getBoundingClientRect().bottom+scrollY")
                    )
                    assert y < h
                    result["viewports"].append(
                        {"width": w, "height": h, "renewal_entry_bottom": y}
                    )
                page.set_viewport_size({"width": 390, "height": 844})
                field = page.locator("#adminUserSearch")
                field.fill("needLE")
                field.press("Enter")
                expect(page.locator("#users tbody tr")).to_have_count(1)
                expect(page.locator("#userPageLabel")).to_have_text("1 / 1")
                expect(page.locator("#users tbody tr")).to_contain_text("Needle_client")
                page.reload()
                expect(field).to_have_value("needLE")
                field.fill("nothing_to_match")
                field.press("Enter")
                expect(page.locator("#adminUserStatus")).to_have_text("没有符合条件的账号")
                page.locator("#adminUserClear").click()
                expect(page.locator("#userPageLabel")).to_have_text("1 / 3")
                page.locator("[data-admin-user-task=create]").click()
                password = page.locator("#newPassword")
                expect(password).to_have_attribute("type", "password")
                password.fill("fixture-only-8")
                page.locator("#showInitialPassword").click()
                expect(password).to_have_attribute("type", "text")
                page.locator("[data-admin-user-task=list]").click()
                page.locator("[data-admin-user-task=create]").click()
                expect(password).to_have_attribute("type", "password")
                page.locator("#newUsername").fill("locally_created_customer")
                page.locator("#createUser").click()
                expect(page.locator("#adminUserSearch")).to_have_value(
                    "locally_created_customer"
                )
                expect(page.locator("#users tbody tr")).to_have_count(1)
                expect(page.locator("#users tbody tr")).to_contain_text(
                    "locally_created_customer"
                )
                page.locator("[data-admin-user-task=codes]").click()
                expect(page.locator("#createCodeForm")).to_be_visible()
                page.reload()
                expect(page.locator("#createCodeForm")).to_be_visible()
                assert parse_qs(urlsplit(page.url).query)["user_task"] == ["codes"]
                assert page.locator("#codeLimit").evaluate(
                    'el=>el.form.id==="createCodeForm"&&el.labels.length===1'
                )
                assert page.locator("#codePlan").evaluate("el=>el.labels.length===1")
                requests = []
                page.on(
                    "request",
                    lambda req: (
                        requests.append(True)
                        if req.method == "POST"
                        and urlsplit(req.url).path == "/api/admin/activation-codes"
                        else None
                    ),
                )
                cap = page.locator("#codeLimit")
                cap.fill("0")
                cap.press("Enter")
                assert not cap.evaluate("el=>el.checkValidity()") and not requests
                cap.fill("6")
                with page.expect_response(
                    lambda r: (
                        r.request.method == "POST"
                        and urlsplit(r.url).path == "/api/admin/activation-codes"
                    )
                ) as created:
                    cap.press("Enter")
                assert created.value.status == 201 and len(requests) == 1
                expect(page.locator("#issuedCodePanel")).to_be_visible()
                assert created.value.json()["item"]["monitor_limit"] == 6
                page.locator("[data-admin-user-task=list]").click()
                page.locator("[data-admin-user-task=codes]").click()
                expect(page.locator("#issuedCodePanel")).to_be_hidden()
                assert (
                    created.value.json()["code"]
                    not in page.locator("#activationCodes").inner_text()
                )
                result.update(
                    user_search="casefold/full-list/empty/clear/reload",
                    new_password="masked/reveal/remask",
                    create_result="exact account",
                    code_form="invalid blocked; Enter creates once",
                    history_no_raw_code=True,
                )
                c.close()
                b.close()
        finally:
            srv.shutdown()
            thread.join()
            srv.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
        if report:
            report.write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf8"
            )
        print(
            "ADMIN TASKS: one-click entry / global search / transient password reveal / create to exact user / native code form = PASS"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default="chrome")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    run(args.channel, args.report)
