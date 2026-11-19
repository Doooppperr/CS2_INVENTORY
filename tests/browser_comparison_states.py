"""账户库存历史区分缺少基准、没有变化与读取失败，并保留所选时间。"""
from urllib.parse import parse_qs, urlsplit

from browser_monitor_workbench import WorkbenchBrowserTests
from playwright.sync_api import expect


def run():
    cls = WorkbenchBrowserTests
    cls.setUpClass()
    try:
        check = cls()
        check.setUp()
        try:
            p = check.page
            url = "**/api/monitors/1/compare?*"
            def compare(route):
                days = parse_qs(urlsplit(route.request.url).query)["days"][0]
                if days == "7":
                    route.fulfill(status=503, json={"error": "临时中断"})
                    return
                current = {"scanned_at": "2026-11-19T07:00:00+08:00"}
                baseline = None if days == "1" else {"scanned_at": "2026-11-16T07:00:00+08:00"}
                changes = {} if baseline else None
                route.fulfill(json={"current": current, "baseline": baseline, "diff": changes,
                                    "requested_baseline_date": "2026-11-18"})
            p.route(url, compare)
            p.goto("/app/monitors/1")
            expect(p.locator("#detailData")).to_be_visible()
            p.locator("#detailChangesTab").click()
            expect(p.locator("#compareContent")).to_contain_text("2026-11-18")
            p.locator('#compareSwitch [data-days="3"]').click()
            expect(p.locator("#compareContent")).to_contain_text("没有已确认的")
            p.locator('#compareSwitch [data-days="7"]').click()
            expect(p.locator("#comparisonRetry")).to_be_visible()
            p.unroute(url)
            p.locator("#comparisonRetry").click()
            expect(p.locator("#comparisonRetry")).to_be_hidden()
            expect(p.locator('#compareSwitch [data-days="7"]')).to_have_attribute("aria-pressed", "true")
            print("missing_baseline=PASS; no_change=PASS; retry_with_period=PASS")
        finally:
            check.tearDown()
    finally:
        cls.tearDownClass()


if __name__ == "__main__":
    run()
