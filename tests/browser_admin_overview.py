"""管理员概况真实浏览器：实际批次时间、权限、成功/失败/排队以及弱网原地恢复。"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import expect, sync_playwright
from test_admin_workspace import ADMIN, MEMBER, PASSWORD, fixture
from werkzeug.serving import WSGIRequestHandler, make_server

from cs2_inventory.database import db
from cs2_inventory.models import ScanBatch, ScanJob, SteamTarget, User


class Quiet(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


def run(channel, evidence=None):
    now = datetime(2026, 11, 19, 6, 8, tzinfo=timezone.utc)
    results = {}
    with TemporaryDirectory() as tmp:
        app = fixture(Path(tmp))
        with app.app_context():
            User.query.filter_by(username="cs2inventory_admin").one().password_hash = User.query.filter_by(username=ADMIN).one().password_hash
            db.session.commit()
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel=channel)
                ctx = browser.new_context(base_url=f"http://127.0.0.1:{server.server_port}", timezone_id="Asia/Shanghai",
                                          viewport={"width": 320, "height": 740}, color_scheme="dark")
                api = ctx.request
                url = "/api/admin/status"
                def login(name):
                    token = api.get("/api/bootstrap").json()["csrf_token"]
                    assert api.post("/api/auth/login", data={"username": name, "password": PASSWORD},
                                    headers={"X-CSRF-Token": token}).ok
                assert api.get(url).status == 401
                login(MEMBER)
                assert api.get(url).status == 403
                login(ADMIN)
                public = api.get(url).json()
                assert public["latest_daily_batch"] is None
                assert public["scheduled_scan_hours_beijing"] == [7, 14, 21]
                assert not {"quota", "official_quota", "localization", "entitlements"} & public.keys()
                p = ctx.new_page()
                writes = []
                p.on("request", lambda req: writes.append(req.method) if "/api/" in req.url and req.method != "GET" else None)
                p.goto("/app?view=admin&section=overview")
                expect(p.locator("#batchTimes")).to_have_text("尚无定时扫描记录。")
                expect(p.locator("#batchTotals")).to_be_hidden()
                expect(p.locator("#overviewTargets")).to_be_visible()
                with app.app_context():
                    daily = ScanBatch(kind="daily", slot_key="2026-11-19-R3", status="completed_with_errors",
                                      total_jobs=9, completed_jobs=7, failed_jobs=2, created_at=now,
                                      started_at=now+timedelta(seconds=13), finished_at=now+timedelta(minutes=12))
                    db.session.add(daily)
                    db.session.add(ScanBatch(kind="manual", total_jobs=77, created_at=now+timedelta(hours=3)))
                    db.session.commit()
                    batch_id = daily.id
                p.reload()
                expect(p.locator("#batchState")).to_have_text("有失败项")
                expect(p.locator("#batchTimes")).to_contain_text("14:08")
                expect(p.locator("#batchTimes")).to_contain_text("14:20")
                expect(p.locator("#batchFailed")).to_have_text("2")
                expect(p.locator("#batchCompleted")).to_have_text("7")
                expect(p.locator("#batchTotal")).to_have_text("9")
                expect(p.locator("#batchPending")).to_have_text("0")
                expect(p.locator("#overviewDiagnostics")).to_be_hidden()
                assert p.locator("#batchFailures").get_attribute("href").endswith(f"&failed_batch={batch_id}")
                results["mobile_failure_action_bottom"] = p.locator("#batchFailures").evaluate("e=>Math.round(e.getBoundingClientRect().bottom)")
                assert results["mobile_failure_action_bottom"] < 740
                assert p.evaluate("document.documentElement.scrollWidth===innerWidth")
                if evidence:
                    evidence.mkdir(parents=True, exist_ok=True)
                    p.screenshot(path=str(evidence/"overview-320-after.png"))
                login("cs2inventory_admin")
                p.set_viewport_size({"width":1440,"height":900})
                p.reload()
                expect(p.locator("#overviewDiagnostics")).to_be_visible()
                expect(p.locator("#overviewMetrics")).to_be_hidden()
                p.locator("#overviewDiagnostics summary").focus()
                p.keyboard.press("Enter")
                expect(p.locator("#overviewMetrics")).to_contain_text("预警")
                expect(p.locator("#overviewMetrics")).to_contain_text("供应商")
                if evidence:
                    p.screenshot(path=str(evidence/"overview-1440-after.png"))
                def unavailable(route):
                    route.fulfill(status=503,json={"error":"暂时中断"})
                p.route("**/api/admin/status",unavailable)
                p.reload()
                expect(p.locator("#overviewMessage")).to_have_text("运行概况读取失败。")
                expect(p.locator("#overviewRetry")).to_be_visible()
                p.unroute("**/api/admin/status",unavailable)
                p.locator("#overviewRetry").focus()
                p.keyboard.press("Enter")
                expect(p.locator("#batchHeading")).to_be_focused()
                expect(p.locator("#batchFailed")).to_have_text("2")
                with app.app_context():
                    daily = db.session.get(ScanBatch,batch_id)
                    daily.status = "running"
                    daily.completed_jobs = 2
                    daily.failed_jobs = 1
                    daily.finished_at = None
                    target = SteamTarget.query.first()
                    db.session.add(ScanJob(batch_id=daily.id,steamid=target.steamid,kind="daily",target_id=target.id,status="cancelled"))
                    db.session.commit()
                progress=api.get(url).json()["latest_daily_batch"]
                assert (progress["total"],progress["completed"],progress["failed"],progress["remaining_jobs"],progress["not_run"]) == (9,2,1,5,1)
                p.reload()
                expect(p.locator("#batchPending")).to_have_text("5")
                expect(p.locator("#batchState")).to_have_text("进行中")
                expect(p.locator("#batchUnfinished")).to_contain_text("1 个目标")
                assert not writes
                results.update(empty_no_fake_zeros=True,finished=[9,7,2,0],actual_times=["14:08","14:20"],
                               running_with_cancelled=[9,2,1,5,1],superadmin_private_metrics=True,retry_focus=True,ui_writes=0)
                ctx.close()
                browser.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    if evidence:
        (evidence/"overview-after.json").write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    print("admin_overview=PASS; daily_actual_times=PASS; roles=PASS; mobile_first_screen=PASS; retry=PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel",default="chrome")
    parser.add_argument("--evidence",type=Path)
    args = parser.parse_args()
    run(args.channel,args.evidence)
