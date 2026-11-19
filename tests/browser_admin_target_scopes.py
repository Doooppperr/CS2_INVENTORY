"""管理员细分范围能组合、可单独清除，库存详情返回仍在该范围与当前页。"""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright
from test_admin_workspace import ADMIN, MEMBER, PASSWORD, fixture
from werkzeug.serving import WSGIRequestHandler, make_server

from cs2_inventory.database import db
from cs2_inventory.models import (
    ScanBatch,
    ScanJob,
    SteamTarget,
    Subscription,
    User,
    utcnow,
)


class Quiet(WSGIRequestHandler):
    def log(self, *args):
        pass


def run(channel, baseline, out):
    with TemporaryDirectory() as tmp:
        app = fixture(Path(tmp))
        with app.app_context():
            user = User.query.filter_by(username=MEMBER).one()
            batch = ScanBatch(
                kind="daily",
                slot_key="2026-11-19-R3",
                status="completed_with_errors",
                total_jobs=42,
                failed_jobs=25,
                completed_jobs=17,
                finished_at=utcnow(),
            )
            db.session.add(batch)
            db.session.flush()
            targets = SteamTarget.query.order_by(SteamTarget.id).all()
            for i, t in enumerate(targets[:42]):
                db.session.add(
                    ScanJob(
                        batch_id=batch.id,
                        kind="daily",
                        target_id=t.id,
                        steamid=t.steamid,
                        status="failed" if i < 25 else "succeeded",
                        expires_at=utcnow(),
                    )
                )
                if 3 <= i < 32:
                    db.session.add(
                        Subscription(
                            user_id=user.id, target_id=t.id, remark=f"客户范围 {i:02}"
                        )
                    )
            db.session.commit()
            uid, bid = user.id, batch.id
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)
        thread = Thread(target=server.serve_forever)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        evidence = {}
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
                    data=dict(username=ADMIN, password=PASSWORD),
                    headers={"X-CSRF-Token": token},
                ).ok
                p = ctx.new_page()
                errors = []
                writes = []
                p.on("pageerror", lambda e: errors.append(str(e)))
                p.on(
                    "request",
                    lambda r: (
                        writes.append(r.method)
                        if "/api/" in r.url and r.method != "GET"
                        else None
                    ),
                )
                path = f"/app?view=admin&section=targets&failed_batch={bid}&subscriber_id={uid}"
                p.goto(path)
                status = p.locator("#adminTargetStatus")
                expect(status).to_have_text(
                    "共 45 个目标" if baseline else "共 22 个目标"
                )
                evidence.update(
                    initial_count=status.inner_text(),
                    url_after_open=p.url.split("?", 1)[-1],
                    visible_scopes=p.locator(
                        "#adminTargetScope:visible .scope-chip"
                    ).count(),
                )
                if not baseline:
                    chip = p.locator("#adminTargetScope")
                    expect(chip).to_contain_text("本轮失败")
                    expect(chip).to_contain_text(MEMBER)
                    next_ = p.locator("#targetNext")
                    next_.click()
                    expect(p.locator("#targetPageLabel")).to_have_text("2 / 2")
                    targets = p.locator("#adminTargets article")
                    expect(targets).to_have_count(2)
                    picked = targets.first.locator("h4").inner_text()
                    targets.first.locator(".target-open").click()
                    expect(p.locator("#detailTitle")).to_have_text(picked)
                    params = parse_qs(urlsplit(p.url).query)
                    assert params["subscriber_id"] == [str(uid)] and params[
                        "failed_batch"
                    ] == [str(bid)]
                    p.locator("#backDetail").click()
                    expect(p.locator("#targetPageLabel")).to_have_text("2 / 2")
                    expect(targets.first.locator(".target-open")).to_be_focused()
                    search = p.locator("#adminTargetSearch")
                    search.fill("客户范围 06")
                    search.press("Enter")
                    expect(targets).to_have_count(1)
                    expect(status).to_have_text("共 1 个目标")
                    expect(targets).to_contain_text("客户范围 06")
                    p.locator("[data-clear-target-scope=failed_batch]").click()
                    expect(chip).not_to_contain_text("本轮失败")
                    expect(chip).to_contain_text(MEMBER)
                    expect(search).to_have_value("客户范围 06")
                    assert "failed_batch" not in parse_qs(urlsplit(p.url).query)
                    p.go_back()
                    expect(
                        p.locator("[data-clear-target-scope=failed_batch]")
                    ).to_be_visible()
                    expect(status).to_have_text("共 1 个目标")
                    p.locator("#adminTargetClear").click()
                    expect(status).to_have_text("共 22 个目标")
                    p.locator("[data-clear-target-scope=subscriber_id]").click()
                    expect(status).to_have_text("共 25 个目标")
                    p.locator("[data-clear-target-scope=failed_batch]").click()
                    expect(status).to_have_text("共 45 个目标")
                    p.goto("/app?view=admin&section=targets&subscriber_id=999999")
                    expect(p.locator("#adminTargets article")).to_have_count(0)
                    expect(status).to_contain_text("不存在")
                    expect(p.locator("#adminTargetRetry")).to_be_hidden()
                    p.locator("[data-clear-target-scope=subscriber_id]").click()
                    expect(status).to_have_text("共 45 个目标")
                    evidence.update(
                        intersection=22,
                        failed_only=25,
                        all_unique=45,
                        filtered=1,
                        page2=2,
                        back_to_exact_target=True,
                        remember_search_on_clear=True,
                        missing_scope_recovered=True,
                    )
                assert not writes and not errors, (writes, errors)
                evidence.update(writes=0, js_errors=[])
                browser.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf8"
        )
    print(
        "target_scopes="
        + str(evidence["visible_scopes"])
        + "; read="
        + evidence["initial_count"]
        + "; writes=0"
    )


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--channel", default="chrome")
    cli.add_argument("--baseline", action="store_true")
    cli.add_argument("--out", type=Path)
    opt = cli.parse_args()
    run(opt.channel, opt.baseline, opt.out)
