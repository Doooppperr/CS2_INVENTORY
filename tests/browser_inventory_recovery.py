"""实际账号的库存书签：弱网可在原处重试，失效链接不能误触取消。"""
from browser_monitor_workbench import WorkbenchBrowserTests
from playwright.sync_api import expect


def run():
    cls = WorkbenchBrowserTests
    cls.setUpClass()
    try:
        test = cls()
        test.setUp()
        try:
            p = test.page
            url = "**/api/monitors/1"
            p.route(url, lambda r: r.fulfill(status=503, json={"error": "暂时离线"}))
            p.goto("/app/monitors/1?item=红线")
            expect(p.locator("#retryDetail")).to_be_visible()
            expect(p.locator("#detailData")).to_be_hidden()
            expect(p.locator("#deleteMonitor")).to_be_hidden()
            p.unroute(url)
            p.locator("#retryDetail").click()
            expect(p.locator("#detailTitle")).to_be_focused()
            expect(p.locator("#itemSearch")).to_have_value("红线")
            expect(p.locator("#detailData")).to_be_visible()
            expect(p.locator("#itemRows .item-row").first).to_be_visible()
            boot = p.request.get("/api/bootstrap").json()
            assert p.request.delete("/api/monitors/1", headers={"X-CSRF-Token": boot["csrf_token"]}).ok
            p.reload()
            expect(p.locator("#detailNotice")).to_contain_text("当前账号没有订阅此对象")
            expect(p.locator("#retryDetail")).to_be_hidden()
            expect(p.locator("#deleteMonitor")).to_be_hidden()
            expect(p.locator("#detailHistoryTab")).to_be_hidden()
            p.locator("#backDetail").click()
            expect(p.locator("#monitorList")).to_be_visible()
            print("network_retry=PASS; filtered_bookmark=PASS; real_unsubscribed_404=PASS; return=PASS")
        finally:
            test.tearDown()
    finally:
        cls.tearDownClass()


if __name__ == "__main__":
    run()
