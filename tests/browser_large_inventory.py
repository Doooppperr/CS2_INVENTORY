"""大库存按页显示，数量排序与搜索仍覆盖完整快照。"""
from browser_monitor_workbench import WorkbenchBrowserTests
from playwright.sync_api import expect


def run():
    cls = WorkbenchBrowserTests
    cls.setUpClass()
    try:
        test = cls()
        test.setUp()
        try:
            page = test.page
            def many(route):
                response = route.fetch()
                payload = response.json()
                payload["sections_same_complete_round"] = True
                for section in payload["inventory_sections"].values():
                    section["items"] = []
                payload["inventory_sections"]["protected"]["items"] = [
                    dict(name=f"藏品 {i:03}", count=i+1, is_trade_protected=True) for i in range(105)
                ]
                route.fulfill(response=response, json=payload)
            page.route("**/api/monitors/1", many)
            page.goto("/app/monitors/1")
            rows = page.locator("#itemRows .item-row")
            expect(rows).to_have_count(40)
            page.locator("#itemMore").click()
            expect(rows).to_have_count(80)
            expect(rows.nth(40)).to_be_focused()
            page.locator("#itemSort").select_option("count")
            expect(rows.first).to_contain_text("藏品 104")
            page.locator("#itemSearch").fill("藏品 104")
            expect(rows).to_have_count(1)
            page.locator("#itemSearch").fill("找不到的名字")
            expect(rows).to_have_count(0)
            page.locator("#clearItemFilters").click()
            expect(rows).to_have_count(40)
            expect(page.locator("#itemSearch")).to_be_focused()
            print("large_inventory=PASS; next_focus=PASS; full_search=PASS; count_sort=PASS")
        finally:
            test.tearDown()
    finally:
        cls.tearDownClass()


if __name__ == "__main__":
    run()
