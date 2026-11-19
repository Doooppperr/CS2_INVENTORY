"""介绍页：实际访客按钮和返访原生链接共用看板主题；窄屏、系统主题及键盘回访。"""
from browser_monitor_workbench import WorkbenchBrowserTests
from playwright.sync_api import expect

PAINT = """el=>{
 const s=getComputedStyle(el), ctx=document.createElement('canvas').getContext('2d');
 function lum(css){
  ctx.fillStyle=css;ctx.fillRect(0,0,1,1);
  const rgb=ctx.getImageData(0,0,1,1).data;
  return [.2126,.7152,.0722].reduce((sum,m,i)=>{
   const v=rgb[i]/255;return sum+m*(v<=.04045?v/12.92:((v+.055)/1.055)**2.4);},0);
 }
 const a=lum(s.color),b=lum(s.backgroundColor);
 return [s.color,s.backgroundColor, (Math.max(a,b)+.05)/(Math.min(a,b)+.05)];
}"""


def run():
    suite = WorkbenchBrowserTests
    suite.setUpClass()
    try:
        member = suite()
        member.setUp()
        guest = suite.browser.new_context(base_url=suite.base, locale="zh-CN")
        try:
            pages = (member.page, guest.new_page())
            for theme, width in (("dark", 1468), ("light", 320)):
                for page in pages:
                    page.set_viewport_size({"width": width, "height": 840})
                    page.emulate_media(color_scheme=theme, reduced_motion="reduce")
                    page.goto("/")
                    expect(page.locator("html")).to_have_attribute("data-theme", theme)
                    assert page.locator("h1").evaluate("e=>getComputedStyle(e).textShadow") == "none"
                    assert page.locator("body").evaluate("e=>getComputedStyle(e).backgroundImage") == "none"
                    assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
                    expect(page.locator(".preview-items li")).to_have_count(3)
                    assert not page.locator(".inventory-preview :is(button,a,input,select)").count()
                for area in (".nav", ".hero"):
                    signed_in = pages[0].locator(f"{area} a[data-dashboard-entry]")
                    signed_out = pages[1].locator(f"{area} button[data-auth]")
                    for page in pages:
                        page.mouse.move(0, 0)
                    signed_paint = signed_in.evaluate(PAINT)
                    assert signed_paint == signed_out.evaluate(PAINT)
                    assert signed_paint[2] >= 4.5
                    signed_in.hover()
                    assert signed_in.evaluate(PAINT)[2] >= 4.5
                    pages[0].keyboard.press("Tab")
                    signed_in.focus()
                    assert signed_in.evaluate("e=>getComputedStyle(e).outlineStyle") != "none"
            pages[1].locator(".hero button[data-auth]").click()
            expect(pages[1].locator("#username")).to_be_focused()
            assert pages[1].locator("#authSubmit").evaluate(PAINT)[2] >= 4.5
            pages[1].keyboard.press("Escape")
            chooser = pages[0].locator("[data-theme-selector]")
            chooser.select_option("dark")
            expect(pages[0].locator("html")).to_have_attribute("data-theme", "dark")
            chooser.select_option("system")
            expect(pages[0].locator("html")).to_have_attribute("data-theme", "light")
            pages[0].emulate_media(color_scheme="dark")
            expect(pages[0].locator("html")).to_have_attribute("data-theme", "dark")
            entry = pages[0].locator(".hero a[data-dashboard-entry]")
            entry.focus()
            entry.press("Enter")
            expect(pages[0].locator("#signalHeading")).to_be_visible()
            print("shared_guest_and_signed_in_palette=PASS; theme_and_geometry=PASS; keyboard_link=PASS")
        finally:
            guest.close()
            member.tearDown()
    finally:
        suite.tearDownClass()


if __name__ == "__main__":
    run()
