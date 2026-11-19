"""普通管理员以真实快照展开1/7日加仓；覆盖聚合、时间、恢复阅读和迟到请求。"""

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from test_admin_workspace import ADMIN, PASSWORD, fixture
from werkzeug.serving import WSGIRequestHandler, make_server

from cs2_inventory.database import db
from cs2_inventory.models import SteamTarget
from cs2_inventory.services import store_snapshot


class Quiet(WSGIRequestHandler):
    def log(self, *args):
        pass


def seed(app):
    with app.app_context():
        a, b, c = SteamTarget.query.order_by(SteamTarget.id.desc()).limit(3).all()
        a.persona_name, b.persona_name, c.persona_name = (
            "成长观察 A",
            "成长观察 B",
            "成长观察 C",
        )
        for target in (a, b):
            for age in (1, 0):
                at = datetime(2026, 11, 19, 6, tzinfo=timezone.utc) - timedelta(
                    days=age
                )
                assets = []
                for i in range(9 if target == a and age == 0 else 1):
                    name = f"保护饰品 {i:02}"
                    assets.append(
                        dict(
                            asset_key=str(100 + i),
                            name=name,
                            raw_name=name,
                            amount=i + 1,
                            market_name=name,
                            item_type="CSGO_Type_Rifle",
                            is_trade_protected=True,
                            name_localized=True,
                            observation="live",
                            api_contexts=["protected"],
                        )
                    )
                quality = {
                    "comparable": True,
                    "source_policy": "api_preferred_v2",
                    "scan_round": str(at),
                    "api_contexts": {
                        s: {"complete": True, "scanned_at": at.isoformat()}
                        for s in ("protected", "other")
                    },
                }
                with patch("cs2_inventory.services.utcnow", return_value=at):
                    store_snapshot(
                        target,
                        {
                            "total_items": sum(x["amount"] for x in assets),
                            "item_types": len(assets),
                            "coverage": "complete",
                            "elapsed_ms": 1,
                            "_assets": assets,
                            "_quality": quality,
                        },
                    )
        db.session.commit()
        return a.id


def run(channel, baseline, output):
    with TemporaryDirectory() as tmp:
        app = fixture(Path(tmp))
        target_id = seed(app)
        srv = make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)
        thread = Thread(target=srv.serve_forever)
        thread.start()
        origin = f"http://127.0.0.1:{srv.server_port}"
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
                writes = []
                reads = []
                p.on("pageerror", lambda e: errors.append(str(e)))
                p.on(
                    "request",
                    lambda req: (
                        writes.append(req.method)
                        if "/api/" in req.url and req.method != "GET"
                        else None
                    ),
                )
                p.on(
                    "request",
                    lambda req: (
                        reads.append(req.url)
                        if "protected-additions" in req.url
                        else None
                    ),
                )
                p.goto("/app?view=admin&section=targets&target_q=" + "成长观察")
                cards = p.locator("#adminTargets article")
                expect(cards).to_have_count(3)
                a = cards.filter(has=p.get_by_role("heading", name="成长观察 A"))
                btn = a.locator('[data-growth-days="1"]')
                report["expand_buttons"] = a.locator("[data-growth-days]").count()
                if not baseline:
                    assert not reads
                    endpoint = (
                        f"**/api/admin/targets/{target_id}/protected-additions?days=1"
                    )
                    p.route(
                        endpoint,
                        lambda route: route.fulfill(
                            status=503, json={"error": "本地模拟中断"}
                        ),
                    )
                    btn.click()
                    panel = a.locator(".target-growth-detail")
                    expect(panel.locator(".growth-retry")).to_be_visible()
                    p.unroute(endpoint)
                    panel.locator(".growth-retry").click()
                    links = panel.locator("[data-growth-item]")
                    expect(links).to_have_count(6)
                    expect(panel).to_contain_text("新增 44 件")
                    expect(panel.locator("time")).to_have_count(2)
                    expect(panel.locator("li").first).to_contain_text("+9")
                    panel.locator(".growth-more").click()
                    expect(links).to_have_count(8)
                    expect(links.nth(6)).to_be_focused()
                    pick = links.nth(6)
                    name = pick.inner_text()
                    pick.click()
                    expect(p.locator("#itemSearch")).to_have_value(name)
                    expect(p.locator("#protectedOnly")).to_be_checked()
                    expect(p.locator("#itemRows .item-row")).to_have_count(1)
                    expect(p.locator("#deleteMonitor")).to_be_hidden()
                    p.locator("#backDetail").click()
                    expect(links).to_have_count(8)
                    expect(links.nth(6)).to_be_focused()
                    expect(links.nth(6)).to_have_text(name)
                    # Delay one completed response while user switches periods; do not rely only on request cancellation.
                    p.evaluate("""() => {const native=window.fetch;window.delayGrowth=false;window.fetch=async (url,opts={})=>{
                      if(window.delayGrowth&&String(url).includes('/protected-additions?days=1')){window.delayGrowth=false;
                       const response=await native(url,{...opts,signal:undefined});await new Promise(r=>window.releaseGrowth=r);return response;}
                      return native(url,opts);};}""")
                    btn.click()
                    expect(panel).to_be_hidden()
                    p.evaluate("window.delayGrowth=true")
                    btn.click()
                    p.wait_for_function("typeof window.releaseGrowth==='function'")
                    a.locator('[data-growth-days="7"]').click()
                    expect(panel).to_contain_text("没有可对比的库存记录")
                    p.evaluate("window.releaseGrowth()")
                    expect(links).to_have_count(0)
                    expect(panel).to_contain_text("没有可对比的库存记录")
                    b = cards.filter(has=p.get_by_role("heading", name="成长观察 B"))
                    b.locator('[data-growth-days="1"]').click()
                    expect(b.locator(".target-growth-detail")).to_contain_text(
                        "本时段没有新增"
                    )
                    c = cards.filter(has=p.get_by_role("heading", name="成长观察 C"))
                    c.locator('[data-growth-days="1"]').click()
                    expect(c.locator(".target-growth-detail")).to_contain_text(
                        "还没有交易保护库存记录"
                    )

                    def named_variants(route):
                        response = route.fetch()
                        data = response.json()
                        data["items"] = [
                            {"name": "收藏纪念版", "market_name": market, "count": 1}
                            for market in ("Edition A", "Edition B")
                        ]
                        data["total"] = 2
                        route.fulfill(response=response, json=data)

                    p.route(endpoint, named_variants)
                    btn.click()
                    expect(links).to_have_count(2)
                    expect(links.nth(0)).to_contain_text("Edition A")
                    expect(links.nth(1)).to_contain_text("Edition B")
                    p.unroute(endpoint, named_variants)
                    p.set_viewport_size({"width": 320, "height": 740})
                    assert p.evaluate(
                        "document.documentElement.scrollWidth<=innerWidth"
                    )
                    report.update(
                        verified_units=44,
                        first_mounted=6,
                        expanded_items=8,
                        filtered_roundtrip=True,
                        stale_ignored=True,
                        no_observation_separate=True,
                        zero_separate=True,
                        no_baseline_separate=True,
                        retry_ok=True,
                        same_name_variants=True,
                    )
                assert not writes and not errors, (writes, errors)
                report.update(writes=0, errors=[])
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
    print("inline_growth_buttons=" + str(report["expand_buttons"]) + "; writes=0")


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--channel", default="chrome")
    cli.add_argument("--baseline", action="store_true")
    cli.add_argument("--out", type=Path)
    args = cli.parse_args()
    run(args.channel, args.baseline, args.out)
