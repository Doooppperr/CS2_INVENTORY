"""本地真实看板：子区域失败后可就地重试，不改变其他区域的筛选。"""
from browser_monitor_workbench import WorkbenchBrowserTests
from playwright.sync_api import expect


def run():
    cls = WorkbenchBrowserTests
    cls.setUpClass()
    try:
        test = cls()
        test.setUp()
        try:
            test.open()
            page = test.page
            page.locator("#signalTarget").select_option(label="北岸收藏")
            expect(page.locator("#signalUnits")).to_have_text("8")
            expect(page.locator("#overviewScopeCaption")).to_have_text("我的全部监控")
            root = page.locator("#dashboard")
            def unavailable(route):
                route.fulfill(status=503, json={"error": "测试网络中断"})
            url = "**" + root.get_attribute("data-overview") + "?*"
            page.route(url, unavailable)
            page.locator("#overviewDays").select_option("3")
            expect(page.locator("#overviewRetry")).to_be_visible()
            page.unroute(url, unavailable)
            page.locator("#overviewRetry").click()
            expect(page.locator("#trendChart svg")).to_be_visible()
            expect(page.locator("#overviewDays")).to_have_value("3")
            url = "**" + root.get_attribute("data-api") + "?*"
            page.route(url, unavailable)
            page.locator("#dailyDetails > summary").click()
            expect(page.locator("#dailyRetry")).to_be_visible()
            page.unroute(url, unavailable)
            page.locator("#dailyRetry").click()
            expect(page.locator("#dailyRetry")).to_be_hidden()
            expect(page.locator("#dailyDetails")).to_have_attribute("aria-busy", "false")
            expect(page.locator("#title")).not_to_contain_text("失败")
            expect(page.locator("#signalUnits")).to_have_text("8")
            print("scope_caption=PASS; overview_retry=PASS; daily_retry=PASS; filters_preserved=PASS")
        finally:
            test.tearDown()
    finally:
        cls.tearDownClass()


if __name__ == "__main__":
    run()
