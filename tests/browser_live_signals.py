"""Chrome：同名账号、同名饰品、筛选口径与后台刷新时的键盘阅读位置。"""
from browser_monitor_workbench import NAMES, WorkbenchBrowserTests
from playwright.sync_api import expect


def run():
    suite = WorkbenchBrowserTests
    suite.setUpClass()
    try:
        case = suite()
        case.setUp()
        try:
            page, loc = case.page, case.find
            page.set_viewport_size({"width": 320, "height": 740})
            page.emulate_media(color_scheme="dark")
            order = {"changed": False}

            def same_names(route):
                response = route.fetch()
                data = response.json()
                for owner in data["targets"]:
                    if owner["target_id"] in (1, 2):
                        owner["label"] = "同名仓库"
                for item in data["items"]:
                    for account in item["accounts"]:
                        if account["target_id"] in (1, 2):
                            account["label"] = "同名仓库"
                    if item["name"] in (NAMES[1], NAMES[2]):
                        item["market_name"], item["name"] = item["name"], "同名饰品"
                    if order["changed"]:
                        item["accounts"].reverse()
                        if item.get("market_name") == NAMES[2]:
                            for account in item["accounts"]:
                                if account["target_id"] == 1:
                                    account["count"] = 10
                            item["count"] = sum(account["count"] for account in item["accounts"])
                route.fulfill(response=response, json=data)

            page.route("**/api/dashboard/protected-additions?*", same_names)
            case.open()
            label, units = loc("#signalUnitsLabel"), loc("#signalUnits")
            all_label = label.inner_text()
            names = loc("#signalTarget option").all_text_contents()
            assert len(set(names)) == len(names)
            loc("#signalTarget").select_option("1")
            expect(units).to_have_text("8")
            account_label = label.inner_text()
            loc("#signalSearch").fill("红线")
            expect(units).to_have_text("5")
            both_label = label.inner_text()
            loc("#signalTarget").select_option("")
            expect(units).to_have_text("8")
            assert len({all_label, account_label, both_label, label.inner_text()}) == 4
            loc("#signalClear").click()
            expect(loc("#signalSearch")).to_be_focused()
            expect(units).to_have_text("17")

            red = loc("#signalList details").filter(has=loc("summary b").filter(has_text="红线"))
            red.locator("summary").click()
            second_owner = red.locator('a[href*="/monitors/2?"]')
            red.locator('a[href*="/monitors/1?"]').focus()
            page.evaluate("""() => {const read=fetch;window.fetch=async(...args)=>{
              const value=await read(...args);if(String(args[0]).includes('protected-additions'))
              await new Promise(r=>window.releaseRefresh=r);return value;};}""")

            def refresh(during=None):
                page.evaluate("()=>{window.releaseRefresh=null;window.pendingRefresh=ProtectedFeed.refresh({background:true,recent:true});}")
                page.wait_for_function("typeof releaseRefresh==='function'")
                if during:
                    during()
                page.evaluate("()=>{releaseRefresh();return pendingRefresh;}")

            order["changed"] = True
            refresh(second_owner.focus)
            expect(second_owner).to_be_focused()
            refresh(loc("#signalSearch").focus)
            expect(loc("#signalSearch")).to_be_focused()
            loc('[data-feed-view="accounts"]').click()
            group = loc('#signalList details[data-group-key="1"]')
            group.locator("summary").click()
            exact = group.locator("li").filter(has_text=NAMES[1]).locator("a")
            exact.focus()
            order["changed"] = False
            refresh()
            expect(exact).to_be_focused()
            assert page.evaluate("document.documentElement.scrollWidth-innerWidth") == 0
            print("FILTER_TOTALS / DUPLICATE_LABELS / LIVE_FOCUS / USER_MOVE / DISTINCT_MARKET = PASS")
        finally:
            case.tearDown()
    finally:
        suite.tearDownClass()


if __name__ == "__main__":
    run()
