"""普通管理员以客户自己的备注贯通卡片、加仓与库存；排序在全量记录中执行。"""

import argparse
import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from browser_admin_target_growth import seed
from playwright.sync_api import expect, sync_playwright
from test_admin_workspace import ADMIN, MEMBER, PASSWORD, fixture
from werkzeug.serving import WSGIRequestHandler, make_server

from cs2_inventory.database import db
from cs2_inventory.models import SteamTarget, Subscription, User, utcnow


class Quiet(WSGIRequestHandler):
    def log(self, *args):
        pass


def run(channel, baseline, out):
    with TemporaryDirectory() as tmp:
        app = fixture(Path(tmp))
        chosen = seed(app)
        with app.app_context():
            all_targets = SteamTarget.query.order_by(SteamTarget.id).all()
            favorite, recent, failed_recent = all_targets[:3]
            alt = all_targets[-2]
            primary = User.query.filter_by(username=MEMBER).one()
            secondary = User(
                username="本地客户乙",
                account_kind="internal",
                password_hash=primary.password_hash,
            )
            third = User(
                username="本地用户丙",
                account_kind="internal",
                password_hash=primary.password_hash,
            )
            db.session.add_all([secondary, third])
            db.session.flush()
            for who, target_id, remark in [
                (primary, chosen, "甲的默认仓库"),
                (secondary, chosen, "乙的主武器仓"),
                (primary, alt.id, "甲给B的称呼"),
                (secondary, alt.id, None),
                (primary, favorite.id, "收藏热门"),
                (secondary, favorite.id, "乙的热门收藏"),
                (third, favorite.id, "热门一号"),
            ]:
                db.session.add(
                    Subscription(user_id=who.id, target_id=target_id, remark=remark)
                )
                db.session.flush()
            for t in all_targets:
                t.last_success_at = t.last_scan_at = None
            recent.last_success_at = recent.last_scan_at = utcnow() - timedelta(hours=1)
            failed_recent.last_success_at = utcnow() - timedelta(days=1)
            failed_recent.last_scan_at = utcnow()
            db.session.commit()
            sid, first_scan, first_count, altid = (
                secondary.id,
                recent.id,
                favorite.id,
                alt.id,
            )
        srv = make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)
        thread = Thread(target=srv.serve_forever)
        thread.start()
        origin = f"http://127.0.0.1:{srv.server_port}"
        res = {}
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
                detail_calls = []
                p.on("pageerror", lambda e: errors.append(str(e)))
                p.on(
                    "request",
                    lambda req: (
                        detail_calls.append(parse_qs(urlsplit(req.url).query))
                        if urlsplit(req.url).path == f"/api/monitors/{chosen}"
                        else None
                    ),
                )
                path = "/app?view=admin&section=targets"
                p.goto(path + f"&subscriber_id={sid}")
                cards = p.locator("#adminTargets article")
                expect(cards).to_have_count(3)
                card = cards.filter(has=p.locator(f'.target-open[data-id="{chosen}"]'))
                res["customer_heading"] = card.locator("h4").inner_text()
                sort = p.locator("#adminTargetOrder")
                res["sort_choices"] = p.locator("#adminTargetOrder option").count()
                if not baseline:
                    assert res["customer_heading"] == "乙的主武器仓"
                    expect(
                        cards.filter(
                            has=p.locator(f'.target-open[data-id="{altid}"]')
                        ).locator("h4")
                    ).to_have_text("成长观察 B")
                    sort.select_option("subscribers")
                    expect(cards.first.locator(".target-open")).to_have_attribute(
                        "data-id", str(first_count)
                    )
                    card.locator('[data-growth-days="1"]').click()
                    item = card.locator("[data-growth-item]").first
                    expect(item).to_be_visible()
                    item.click()
                    expect(p.locator("#detailTitle")).to_have_text("乙的主武器仓")
                    assert detail_calls[-1]["subscriber_id"] == [str(sid)]
                    expect(p.locator("#protectedOnly")).to_be_checked()
                    p.locator("#backDetail").click()
                    expect(card.locator("[data-growth-item]").first).to_be_focused()
                    expect(sort).to_have_value("subscribers")
                    p.locator("[data-clear-target-scope=subscriber_id]").click()
                    expect(p.locator("#adminTargetStatus")).to_have_text("共 45 个目标")
                    expect(sort).to_have_value("subscribers")
                    expect(cards.first.locator(".target-open")).to_have_attribute(
                        "data-id", str(first_count)
                    )
                    sort.select_option("recent_scan")
                    expect(cards.first.locator(".target-open")).to_have_attribute(
                        "data-id", str(first_scan)
                    )
                    expect(cards.first.locator(".target-sorted-time")).to_contain_text(
                        "最近成功扫描"
                    )
                    expect(sort.locator("option:checked")).to_have_text("最近成功扫描")
                    p.locator("#targetNext").click()
                    expect(p.locator("#targetPageLabel")).to_have_text("2 / 3")
                    p.reload()
                    expect(sort).to_have_value("recent_scan")
                    expect(p.locator("#targetPageLabel")).to_have_text("2 / 3")
                    search = p.locator("#adminTargetSearch")
                    search.fill("甲的默认仓库")
                    search.press("Enter")
                    expect(cards).to_have_count(1)
                    expect(card.locator("h4")).to_have_text("甲的默认仓库")
                    card.locator(".target-open").click()
                    expect(p.locator("#detailTitle")).to_have_text("甲的默认仓库")
                    assert "subscriber_id" not in detail_calls[-1]
                    p.go_back()
                    expect(sort).to_have_value("recent_scan")
                    expect(card.locator(".target-open")).to_be_focused()
                    assert parse_qs(urlsplit(p.url).query)["order"] == ["recent_scan"]
                    p.goto(
                        f"/app/monitors/{chosen}?source=admin&section=targets&subscriber_id=999999"
                    )
                    expect(p.locator("#detailNotice")).to_have_text(
                        "此对象已不在该客户的监控中。"
                    )
                    expect(p.locator("#detailData")).to_be_hidden()
                    res.update(
                        full_list=45,
                        missing_customer_scope_explained=True,
                        customer_fallback_same_as_personal=True,
                        growth_detail_customer=True,
                        global_order_counts=True,
                        role_visible_scan_time=True,
                        scope_clear_keeps_order=True,
                        page_two_reload=True,
                        search_order_return=True,
                    )
                assert not errors, errors
                res["page_errors"] = 0
                browser.close()
        finally:
            srv.shutdown()
            thread.join()
            srv.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    if out:
        out.write_text(
            json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf8"
        )
    print(
        "customer_context="
        + res["customer_heading"]
        + "; sort_choices="
        + str(res["sort_choices"])
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--channel", default="chrome")
    p.add_argument("--baseline", action="store_true")
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    run(args.channel, args.baseline, args.out)
