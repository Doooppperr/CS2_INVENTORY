"""监控工作台真实浏览器验收：临时 SQLite、真实 API、合成账号与库存。

PYTHONPATH=src python tests/browser_monitor_workbench.py --channel chrome [--screenshots DIR]
"""
import argparse
import json
import threading
import unittest
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from werkzeug.security import generate_password_hash
from werkzeug.serving import WSGIRequestHandler, make_server

from cs2_inventory.app import create_app
from cs2_inventory.dashboard import save_daily
from cs2_inventory.database import db
from cs2_inventory.models import (
    BEIJING_TIMEZONE,
    ScanBatch,
    ScanJob,
    SteamTarget,
    Subscription,
    User,
)
from cs2_inventory.services import store_snapshot

cli = argparse.ArgumentParser()
cli.add_argument("--channel", default="chrome")
cli.add_argument("--screenshots", type=Path)
args, test_args = cli.parse_known_args()
NAMES = ["AK-47 | 红线（久经沙场）", "M4A1 消音型 | 印花集（略有磨损）", "AWP | 二西莫夫（久经沙场）",
         "格洛克 18 型 | 水灵（崭新出厂）", "USP 消音版 | 脑洞大开（略有磨损）"]
MARKET_NAMES = ["AK-47 | Redline (Field-Tested)", *NAMES[1:]]
OWNERS = ["北岸收藏", "枫叶仓", "海盐俱乐部"]
AMOUNTS = [[5, 2, 1, 0, 0], [3, 0, 2, 0, 1], [0, 1, 0, 2, 0]]
TOTAL = str(sum(map(sum, AMOUNTS)))


def clock(day, hour):
    return datetime.combine(day, time(hour, 3), BEIJING_TIMEZONE)


def seed(today):
    user = User(username="演示账号", account_kind="internal", role="user", plan="permanent",
                password_hash=generate_password_hash("browser-fixture"))
    db.session.add(user)
    targets = []
    for owner, label in enumerate(OWNERS):
        target = SteamTarget(steamid=str(76561198000000001+owner), persona_name=label)
        db.session.add(target)
        db.session.flush()
        db.session.add(Subscription(user_id=user.id, target_id=target.id))
        targets.append(target)
        for age in reversed(range(7)):
            day = today - timedelta(days=age)
            scanned = clock(day, 7)
            amounts = AMOUNTS[owner] if not age else [((k+owner+age) % 4) for k in range(len(NAMES))]
            assets = []
            for k, count in enumerate([*amounts, 9]):  # 普通区也有同名红线，筛保护时不应累加这九件。
                name = NAMES[k % len(NAMES)]
                for n in range(count):
                    assets.append({"asset_key": str(10000*(owner+1)+k*100+n), "name": name, "raw_name": name,
                                   "amount": 1, "market_name": name, "item_type": "CSGO_Type_Rifle",
                                   "is_trade_protected": k < len(NAMES), "name_localized": True,
                                   "observation": "live", "api_contexts": ["protected" if k < len(NAMES) else "other"]})
            quality = {"comparable": True, "source_policy": "api_preferred_v2", "scan_round": str(scanned),
                       "api_contexts": {s: {"complete": True, "scanned_at": scanned.isoformat()}
                                        for s in ("protected", "other")}}
            with patch("cs2_inventory.services.utcnow", return_value=scanned.astimezone(timezone.utc)):
                snapshot = store_snapshot(target, {"total_items": len(assets), "item_types": len({a["name"] for a in assets}),
                    "coverage": "complete", "elapsed_ms": 1, "_assets": assets, "_quality": quality})
            save_daily(target, day=day.isoformat(), scanned_at=scanned.astimezone(timezone.utc),
                       status="ready", snapshot=snapshot)
    yesterday = today - timedelta(days=1)
    # 昨日三轮分别为「比对为 0」「仅有首次记录」「有新增」，今早再留一轮有新增记录。
    for day, hour, suffix, state in ((yesterday, 7, "R2", "zero"), (yesterday, 14, "R3", "baseline"),
                                     (yesterday, 21, "R5", "items"), (today, 7, "R2", "items")):
        moment = clock(day, hour)
        batch = ScanBatch(kind="daily", slot_key=f"{day}-{suffix}", status="completed", total_jobs=len(targets),
                          completed_jobs=len(targets), finished_at=moment.astimezone(timezone.utc))
        db.session.add(batch)
        db.session.flush()
        for target, amounts in zip(targets, AMOUNTS):
            rows = [{"key": market, "name": name, "count": count}
                    for market, name, count in zip(MARKET_NAMES, NAMES, amounts) if count]
            report = {"scanned_at": moment.isoformat(),
                      "baseline_at": (moment-timedelta(hours=10 if hour==7 else 7)).isoformat() if state != "baseline" else None,
                      "items": rows if state == "items" else []}
            db.session.add(ScanJob(target_id=target.id, steamid=target.steamid, batch_id=batch.id,
                         kind="daily" if suffix=="R2" else "daily_light", status="completed",
                         finished_at=moment.astimezone(timezone.utc), result_json=json.dumps({"protected_signal": report})))
    db.session.commit()


class QuietHandler(WSGIRequestHandler):
    def log(self, kind, message, *values):
        pass


class WorkbenchBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.app = create_app({"TESTING": True, "STATE_DIR": root, "STEAMWEBAPI_KEY": "",
                             "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(root/'sample.db').as_posix()}",
                             "SESSION_COOKIE_SECURE": False, "SESSION_COOKIE_PATH": "/", "SECRET_KEY": "browser-test"})
        cls.today = datetime.now(BEIJING_TIMEZONE).date()
        cls.yesterday = str(cls.today-timedelta(days=1))
        with cls.app.app_context():
            seed(cls.today)
        cls.server = make_server("127.0.0.1", 0, cls.app, threaded=True, request_handler=QuietHandler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel=args.channel)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()
        with cls.app.app_context():
            db.engine.dispose()
        cls.temp.cleanup()

    def setUp(self):
        self.context = self.browser.new_context(base_url=self.base, locale="zh-CN", timezone_id="Asia/Shanghai",
                                                viewport={"width":1440, "height":940})
        boot = self.context.request.get("/api/bootstrap").json()
        login = self.context.request.post("/api/auth/login", data={"username": "演示账号", "password":"browser-fixture"},
                                          headers={"X-CSRF-Token":boot["csrf_token"]})
        self.assertEqual(login.status, 200)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.find = self.page.locator

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def open(self):
        self.page.goto("/app")
        expect(self.find("#signalSlot")).to_have_value("latest")
        expect(self.find("#signalUnits")).to_have_text(TOTAL)
        expect(self.find("#signalList details")).to_have_count(5)
        expect(self.find("#error")).to_be_empty()

    def test_responsive_previews(self):
        self.open()
        for width, height, filename in [(1440, 940, "preview-desktop.png"), (390, 844, "preview-mobile.png"), (320, 740, None)]:
            with self.subTest(width=width):
                self.page.set_viewport_size({"width":width, "height":height})
                expect(self.find("#signalHeading")).to_be_in_viewport()
                overflow = self.page.evaluate("document.documentElement.scrollWidth - innerWidth")
                self.assertLessEqual(overflow, 0, f"{width}px 宽页面水平溢出 {overflow}px")
                if args.screenshots and filename:
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    self.page.screenshot(path=args.screenshots/filename, animations="disabled")

    def test_search_owner_open_and_return(self):
        self.open()
        self.find("#signalSearch").fill("红线")
        expect(self.find("#signalList details")).to_have_count(1)
        expect(self.find("#signalList .signal-count")).to_contain_text("+8")
        self.find("#signalTarget").select_option(label=OWNERS[0])
        expect(self.find("#signalList .signal-count")).to_contain_text("+5")
        self.find("#signalList summary").click()
        self.find("#signalList a").click()
        expect(self.find("#itemSearch")).to_have_value(NAMES[0])
        expect(self.find("#protectedOnly")).to_be_checked()
        expect(self.find("#itemRows .item-row")).to_have_count(1)
        expect(self.find("#itemRows .item-row")).to_contain_text(NAMES[0])
        self.assertNotIn("14", self.find("#itemRows .item-row").inner_text())
        expect(self.find("#itemRows .item-row")).to_contain_text("5")
        self.find("#backDetail").click()
        expect(self.find("#signalTarget option:checked")).to_have_text(OWNERS[0])
        expect(self.find("#signalSearch")).to_have_value("红线")
        expect(self.find("#signalList .signal-count")).to_contain_text("+5")
        self.find("#signalSearch").fill("不存在的测试物品")
        expect(self.find("#signalNotice")).to_contain_text("没有匹配")
        self.find("#signalClear").click()
        expect(self.find("#signalList details")).to_have_count(5)

    def test_focus_account_survives_zero_baseline_and_unobserved_rounds(self):
        self.open()
        self.find("#signalTarget").select_option(label=OWNERS[0])
        self.find("#signalSearch").fill("红线")
        expect(self.find("#signalUnits")).to_have_text("5", timeout=1500)
        expect(self.find("#signalTypes")).to_have_text("1")
        expect(self.find("#signalOwners")).to_have_text("1")
        self.find("#date").fill(self.yesterday)
        self.find("#signalSlot").select_option("07")
        expect(self.find("#signalUnits")).to_have_text("0")
        expect(self.find("#signalTarget option:checked")).to_have_text(OWNERS[0])
        self.find("#signalSlot").select_option("14")
        expect(self.find("#signalUnits")).to_have_text("—")
        expect(self.find("#signalNotice")).to_contain_text("可对比")
        self.find("#date").fill(self.today.isoformat())
        expect(self.find("#signalNotice")).to_contain_text("扫描结果")
        expect(self.find("#signalTarget option:checked")).to_have_text(OWNERS[0])
        self.find("#signalSlot").select_option("07")
        expect(self.find("#signalUnits")).to_have_text("5")

    def test_default_before_first_scan_uses_real_date_but_explicit_date_is_kept(self):
        next_day=self.today+timedelta(days=1)
        before_scan=datetime.combine(next_day,time(6,50),BEIJING_TIMEZONE)
        self.page.clock.set_fixed_time(before_scan)
        calls=[]
        self.page.on("request",lambda req: calls.append(req.url))
        with patch("cs2_inventory.protected_feed.utcnow",return_value=before_scan.astimezone(timezone.utc)), \
             patch("cs2_inventory.dashboard_overview.utcnow",return_value=before_scan.astimezone(timezone.utc)):
            self.page.goto("/app/dashboard")
            expect(self.find("#signalUnits")).to_have_text(TOTAL)
            expect(self.find("#date")).to_have_value(str(self.today))
            expect(self.find("#signalStamp")).to_contain_text(self.today.strftime("%m/%d"))
            expect(self.find("#trendChart svg")).to_be_visible()
            self.assertEqual(len([u for u in calls if "/protected-additions?" in u]),1)
            self.assertTrue(any(f"/overview?date={self.today}" in u for u in calls))
            self.page.goto(f"/app/dashboard?date={next_day}")
            expect(self.find("#signalNotice")).to_contain_text("暂无可用")
            expect(self.find("#date")).to_have_value(str(next_day))
            expect(self.find("#signalUnits")).to_have_text("—")

    def test_search_by_weapon_finish_and_wear_without_new_requests(self):
        self.open()
        requests=[]
        self.page.on("request", lambda req: requests.append(urlsplit(req.url).path))
        for term in ("AK47 红线", "久经 红线", "ＡＫ４７ redline", "redline field tested"):
            self.find("#signalSearch").fill(term)
            expect(self.find("#signalUnits")).to_have_text("8")
            expect(self.find("#signalList details")).to_have_count(1)
            expect(self.find("#signalList summary")).to_contain_text(NAMES[0])
        self.find("#signalSearch").fill("红线 崭新")
        expect(self.find("#signalList details")).to_have_count(0)
        self.assertNotIn("/api/dashboard/protected-additions",requests)

    def test_open_dashboard_picks_up_finished_scan_and_leaves_history_pinned(self):
        start = datetime.combine(self.today, time(13,59), BEIJING_TIMEZONE)
        finished = start + timedelta(minutes=4)
        now = [start]
        batch_id = None
        self.page.clock.set_fixed_time(start)
        try:
            with patch("cs2_inventory.protected_feed.utcnow", side_effect=lambda:now[0].astimezone(timezone.utc)):
                self.open()
                with self.app.app_context():
                    batch = ScanBatch(kind="daily",slot_key=f"{self.today}-R3",status="completed",total_jobs=3,
                                      completed_jobs=3,finished_at=finished.astimezone(timezone.utc))
                    db.session.add(batch)
                    db.session.flush()
                    batch_id=batch.id
                    for t in SteamTarget.query.all():
                        if t.persona_name not in OWNERS:
                            continue
                        report={"scanned_at":finished.isoformat(),"baseline_at":clock(self.today,7).isoformat(),
                                "items":[{"key":MARKET_NAMES[0],"name":NAMES[0],"count":7}]}
                        db.session.add(ScanJob(target_id=t.id,steamid=t.steamid,batch_id=batch.id,kind="daily_light",
                            status="completed",finished_at=finished.astimezone(timezone.utc),result_json=json.dumps({"protected_signal":report})))
                    db.session.commit()
                now[0]=finished+timedelta(minutes=4)
                self.page.clock.set_fixed_time(now[0])
                actual=self.context.request.get(f"/api/dashboard/protected-additions?date={self.today}").json()
                self.assertEqual(actual["summary"]["added_units"],21)
                # 回到一个已打开的监控标签页；真实后台刚完成新一轮。
                self.page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(self.find("#signalUnits")).to_have_text("21", timeout=2500)
                self.find("#date").fill(self.yesterday)
                expect(self.find("#signalUnits")).to_have_text(TOTAL)
                now[0]+=timedelta(hours=1)
                self.page.clock.set_fixed_time(now[0])
                self.page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(self.find("#date")).to_have_value(self.yesterday)
                self.find("#followLatest").click()
                expect(self.find("#date")).to_have_value(str(self.today))
                expect(self.find("#signalUnits")).to_have_text("21")
        finally:
            if batch_id is not None:
                with self.app.app_context():
                    ScanJob.query.filter_by(batch_id=batch_id).delete()
                    db.session.delete(db.session.get(ScanBatch,batch_id))
                    db.session.commit()

    def test_unchanged_or_hidden_board_does_not_reload_reports(self):
        start=datetime.combine(self.today,time(11,15),BEIJING_TIMEZONE)
        self.page.clock.set_fixed_time(start)
        self.open()
        rows=self.find("#signalList details")
        rows.first.locator("summary").click()
        seen=[]
        self.page.on("request", lambda req:seen.append(urlsplit(req.url).path))
        self.page.clock.set_fixed_time(start+timedelta(minutes=6))
        with self.page.expect_response(lambda r: r.url.split("?")[0].endswith("/scan-revision")):
            self.page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        self.page.wait_for_load_state("networkidle")
        self.assertEqual(seen,["/api/dashboard/scan-revision"])
        self.assertTrue(rows.first.evaluate("el=>el.open"))
        self.page.clock.set_fixed_time(start+timedelta(minutes=12))
        self.page.evaluate("""() => { Object.defineProperty(document,'hidden',{configurable:true,value:true});
          document.dispatchEvent(new Event('visibilitychange')); }""")
        self.assertEqual(seen,["/api/dashboard/scan-revision"])

    def test_daily_fetch_is_lazy_and_filters_independent(self):
        calls = Counter()
        self.page.on("request", lambda req: calls.update([urlsplit(req.url).path]))
        self.open()
        daily = "/api/dashboard"
        overview, additions = daily + "/overview", daily + "/protected-additions"
        expect(self.find("#trendChart svg")).to_be_visible()
        expect(self.find("#trendChart g.chart-day")).to_have_count(7)
        self.assertFalse(self.find("#dailyDetails").evaluate("el=>el.open"))
        self.assertEqual([calls[p] for p in (daily, overview, additions)], [0, 1, 1])
        self.find("#dailyDetails > summary").press("Enter")
        expect(self.find("#title")).to_contain_text("有效库存变动")
        expect(self.find("#rows .change-item").first).to_be_visible()
        self.assertEqual([calls[p] for p in (daily, overview, additions)], [1, 1, 1])
        self.find("#filters [name=q]").fill("红线")
        expect(self.find("#rows .change-item")).to_have_count(1)
        expect(self.find("#rows .change-item")).to_contain_text("红线")
        self.assertEqual([calls[p] for p in (daily, overview, additions)], [2, 1, 1])
        self.find("#overviewDays").select_option("3")
        expect(self.find("#trendChart g.chart-day")).to_have_count(3)
        self.assertEqual([calls[p] for p in (daily, overview, additions)], [2, 2, 1])
        self.assertEqual(self.find("#stats b").all_text_contents(), ["3", "3"])
        expect(self.find(".quality-panel")).not_to_be_visible()

    def test_previous_day_from_empty_round(self):
        self.page.goto("/app/dashboard?slot=21")
        expect(self.find("#signalNotice")).to_contain_text("暂无可用的扫描结果")
        self.find("#signalPrevious").click()
        expect(self.find("#date")).to_have_value(self.yesterday)
        expect(self.find("#signalSlot")).to_have_value("21")
        expect(self.find("#signalUnits")).to_have_text(TOTAL)
        expect(self.find("#signalPrevious")).to_be_hidden()
        expect(self.find("#signalList details")).to_have_count(5)

    def test_dark_mobile_keyboard_drilldown(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.emulate_media(color_scheme="dark")
        self.open()
        expect(self.find("html")).to_have_attribute("data-theme", "dark")
        first = self.find("#signalList details").first
        first.locator("summary").focus()
        self.page.keyboard.press("Enter")
        expect(first.locator("a").first).to_be_visible()
        self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth-innerWidth"), 0)
        self.page.keyboard.press("Tab")
        expect(first.locator("a").first).to_be_focused()
        self.page.keyboard.press("Enter")
        expect(self.find("#protectedOnly")).to_be_checked()
        expect(self.find("#itemSearch")).to_have_value(NAMES[0])

    def test_round_races_and_empty_states(self):
        self.page.goto(f"/app/dashboard?date={self.yesterday}")
        slot = self.find("#signalSlot")
        expect(self.find("#signalUnits")).to_have_text(TOTAL)
        slot.select_option("14")
        expect(self.find("#signalNotice")).to_contain_text("暂无可对比记录")
        expect(self.find("#signalUnits")).to_have_text("—")
        slot.select_option("07")
        expect(self.find("#signalUnits")).to_have_text("0")
        expect(self.find("#signalNotice")).to_contain_text("没有新增")
        slot.select_option("21")
        expect(self.find("#signalUnits")).to_have_text(TOTAL)
        # 故意让下一次早间查询晚于晚间查询完成，覆盖旧响应覆盖当前选择的竞态。
        self.page.evaluate("""() => { const real = window.fetch; window.fetch = async (url, opt) => {
          if (String(url).includes('protected-additions') && new URL(url,location.href).searchParams.get('slot')==='07') {
            const response = await real(url, {...opt, signal:undefined});
            await new Promise(resolve => window.releaseEarlySlot = resolve);
            return response;
          } return real(url, opt); }}""")
        slot.select_option("07")
        self.page.wait_for_function("typeof releaseEarlySlot === 'function'")
        slot.select_option("21")
        expect(self.find("#signalUnits")).to_have_text(TOTAL)
        self.page.evaluate("async()=>{releaseEarlySlot(); await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))}")
        expect(self.find("#signalUnits")).to_have_text(TOTAL)
        expect(self.find("#signalList details")).to_have_count(5)
        self.find("#date").fill(str(self.today-timedelta(days=2)))
        expect(self.find("#signalNotice")).to_contain_text("暂无可用的扫描结果")
        expect(self.find("#signalUnits")).to_have_text("—")


if __name__ == "__main__":
    unittest.main(argv=[__file__, *test_args], verbosity=2)
