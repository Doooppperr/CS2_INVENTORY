"""Test actual mobile admin tasks on a synthetic, isolated SQLite database, never a live account."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright
from test_admin_workspace import ADMIN, PASSWORD, fixture
from werkzeug.serving import WSGIRequestHandler, make_server

from cs2_inventory.database import db


class Quiet(WSGIRequestHandler):
    def log(self, *args):
        pass


def run(channel, report):
    with TemporaryDirectory() as tmp:
        app = fixture(Path(tmp))
        srv = make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)
        thread = Thread(target=srv.serve_forever)
        thread.start()
        views = []
        try:
            origin = f"http://127.0.0.1:{srv.server_port}"
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel=channel)
                ctx = browser.new_context(
                    viewport={"width": 390, "height": 844}, locale="zh-CN"
                )
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(type(e).__name__))
                page.goto(origin + "/?auth=login")
                page.locator("#username").fill(ADMIN)
                page.locator("#password").fill(PASSWORD)
                page.locator("#authSubmit").click()
                expect(page.locator("#navUsername")).to_have_text(ADMIN)
                page.goto(origin + "/app?view=admin&section=targets&target_page=3")
                expect(page.locator("#targetPageLabel")).to_have_text("3 / 3")
                search = page.locator("#adminTargetSearch")
                search.fill("neeDle")
                search.press("Enter")
                expect(page.locator("#adminTargets tbody tr")).to_have_count(1)
                expect(page.locator("#adminTargetStatus")).to_have_text("共 1 个目标")
                assert parse_qs(urlsplit(page.url).query)["target_page"] == ["1"]
                page.reload()
                expect(search).to_have_value("neeDle")
                expect(page.locator("#adminTargets tbody tr")).to_have_count(1)
                page.locator("#adminTargets .target-open").click()
                page.locator("#backDetail").click()
                expect(search).to_have_value("neeDle")
                expect(page.locator("#adminTargets tbody tr")).to_have_count(1)
                page.locator("#adminTargetClear").click()
                expect(page.locator("#adminTargetStatus")).to_have_text("共 45 个目标")
                search.fill("76561198000000001")
                search.press("Enter")
                expect(page.locator("#adminTargets tbody tr")).to_have_count(1)
                expect(page.locator("#adminTargets tbody tr")).to_contain_text(
                    "Needle_5% Alpha"
                )
                for width, height in ((320, 740), (390, 844), (1440, 960)):
                    page.set_viewport_size({"width": width, "height": height})
                    bounds = geometry(page, "#adminTargets")
                    views.append(bounds)
                    assert bounds["w"] <= bounds["room"] + 1
                    assert bounds["mobileLabels"] == (width < 760)
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("#adminUsersTab").click()
                expect(page.locator("#users tbody tr").first).to_be_visible()
                before = geometry(page, "#users")
                views.append(before)
                assert before["w"] <= before["room"] + 1 and before["mobileLabels"]
                page.locator('[data-admin-user-task="codes"]').click()
                page.locator("#createCode").click()
                code = page.locator("#createdCode")
                expect(code).to_be_visible()
                expect(page.locator("#activationCodes tbody tr")).to_have_count(1)
                after = geometry(page, "#activationCodes")
                views.append(after)
                assert after["w"] <= after["room"] + 1 and after["mobileLabels"]
                assert (
                    code.inner_text()
                    not in page.locator("#activationCodes").inner_text()
                )
                # A test clipboard in this isolated context; do not read/modify the operator's OS clipboard.
                page.evaluate(
                    """() => Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async v=>{window.__uxWritten=v}}})"""
                )
                page.locator("#copyCreatedCode").click()
                expect(page.locator("#copyCodeStatus")).to_have_text("已复制续期码")
                assert page.evaluate(
                    "window.__uxWritten===document.querySelector('#createdCode').textContent"
                )
                page.evaluate(
                    """() => Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new DOMException('Denied','NotAllowedError')}}})"""
                )
                page.locator("#copyCreatedCode").click()
                expect(page.locator("#copyCodeStatus")).to_have_text(
                    "已选中续期码，请手动复制"
                )
                assert page.evaluate(
                    "getSelection().toString()===document.querySelector('#createdCode').textContent"
                )
                page.reload()
                expect(page.locator("#issuedCodePanel")).to_be_hidden()
                assert not errors
                ctx.close()
                browser.close()
        finally:
            srv.shutdown()
            thread.join()
            srv.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
        result = {
            "server_pagination": True,
            "search_modes": ["name", "steamid"],
            "reload_and_return": True,
            "clipboard_settled_feedback": True,
            "clipboard_failure_manual": True,
            "historical_plain_code": False,
            "page_errors": 0,
            "layouts": views,
        }
        if report:
            report.write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf8"
            )
        print(
            "ADMIN: 45-target search / inventory return / 3 mobile tables / copy success and denial / no historical raw code = PASS"
        )


def geometry(page, root):
    return page.locator(
        root
    ).evaluate("""(el) => {
      const table=el.querySelector('table'), label=table.querySelector('tbody td .admin-field-label');
      return {section:el.id,viewport:innerWidth,room:el.clientWidth,w:table.getBoundingClientRect().width,mobileLabels:!!label.getClientRects().length};}""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default="chrome")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    run(args.channel, args.report)
