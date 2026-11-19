"""Temporary monthly customer: keyboard renewal, durable results, native return and contact actions."""

import argparse
import json
import re
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from browser_admin_workspace import Quiet
from playwright.sync_api import expect, sync_playwright
from test_admin_workspace import ADMIN, MEMBER, PASSWORD, fixture
from werkzeug.serving import make_server

from cs2_inventory.database import db
from cs2_inventory.entitlements import create_activation_code
from cs2_inventory.models import ActivationCode, User, utcnow


def run(channel, report, baseline=False):
    result = {"phase": "before" if baseline else "after", "viewport": [320, 740]}
    with TemporaryDirectory() as folder:
        app = fixture(Path(folder))
        with app.app_context():
            user = User.query.filter_by(username=MEMBER).one()
            user.account_kind = "customer"
            user.plan = "monthly"
            user.monitor_limit = 3
            user.activated_at = utcnow()
            user.activation_expires_at = utcnow() + timedelta(days=7)
            db.session.commit()
            initial_expiry = user.activation_expires_at
            uid = user.id
            admin = User.query.filter_by(username=ADMIN).one()
            annual_row, annual = create_activation_code(admin, "annual", 12)
            perm_row, permanent = create_activation_code(admin, "permanent", 16)
            annual_id, perm_id = annual_row.id, perm_row.id
        srv = make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)
        thread = Thread(target=srv.serve_forever)
        thread.start()
        origin = f"http://127.0.0.1:{srv.server_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel=channel)
                ctx = browser.new_context(
                    viewport={"width": 320, "height": 740},
                    locale="zh-CN",
                    color_scheme="dark",
                    is_mobile=True,
                    has_touch=True,
                    permissions=["clipboard-read", "clipboard-write"],
                )
                page = ctx.new_page()
                errors, logins, redemptions = [], [], []
                page.on("pageerror", lambda err: errors.append(str(err)))

                def trace(response):
                    path = urlsplit(response.url).path
                    if response.request.method == "POST":
                        if path == "/api/auth/login":
                            logins.append(response.status)
                        elif path == "/api/activation/redeem":
                            redemptions.append(response.status)

                page.on("response", trace)
                page.goto(origin + "/")
                assert page.locator(".hero [data-auth]").is_visible()
                page.bring_to_front()
                address = page.locator(".contact a[href^=mailto]").inner_text()
                old_clipboard = page.evaluate("navigator.clipboard.readText()")
                try:
                    page.locator("#copyEmail").tap()
                    page.wait_for_function(
                        "email => navigator.clipboard.readText().then(value => value === email)",
                        arg=address,
                    )
                    success_feedback = page.locator(".contact").inner_text()
                    assert "已复制" in success_feedback
                finally:
                    page.evaluate(
                        "previous => navigator.clipboard.writeText(previous)",
                        old_clipboard,
                    )
                page.reload()
                page.evaluate("""Object.defineProperty(navigator.clipboard,'writeText',{
                    configurable:true,value:()=>Promise.reject(new DOMException('QA clipboard denied','NotAllowedError'))})""")
                page.locator("#copyEmail").tap()
                if baseline:
                    page.wait_for_timeout(120)
                else:
                    expect(page.locator("#contactCopyStatus")).to_contain_text("选中")
                result["contact"] = {
                    "native_copy_verified": True,
                    "denied_selection_matches_address": page.evaluate(
                        "getSelection().toString()"
                    )
                    == address,
                    "denied_inline_feedback": "手动复制"
                    in page.locator(".contact").inner_text(),
                    "denied_javascript_errors": len(errors),
                }
                if not baseline:
                    assert result["contact"]["denied_selection_matches_address"]
                    assert result["contact"]["denied_inline_feedback"] and not errors
                page.locator(".hero [data-auth]").tap()
                expect(page.locator("#username")).to_be_focused()
                page.locator("#username").fill(MEMBER)
                page.locator("#password").fill(PASSWORD)
                page.locator("#password").press("Enter")
                expect(page.locator("#navUsername")).to_have_text(MEMBER)
                page.goto(origin + "/app?view=subscriptions&q=fixture")
                expect(page.locator("#entitlementTitle")).to_have_text("月度")
                page.locator("#activationCard summary").tap()
                field = page.locator("#activationCode")
                field.press("Enter")
                empty_required = field.evaluate("e=>e.validity.valueMissing")
                assert not redemptions
                field.fill("UX-NOT-REDEEMABLE")
                if baseline:
                    field.press("Enter")
                    page.wait_for_timeout(160)
                    invalid_enter_count = len(redemptions)
                    with page.expect_response("**/api/activation/redeem"):
                        page.locator("#redeemCode").tap()
                else:
                    with page.expect_response("**/api/activation/redeem"):
                        field.press("Enter")
                    expect(page.locator("#activationFeedback")).to_have_text(
                        "邀请码无效"
                    )
                    invalid_enter_count = len(redemptions)
                page.wait_for_timeout(3450)
                result["invalid"] = {
                    "required_and_empty_did_not_post": empty_required,
                    "label_count": field.evaluate("e=>e.labels.length"),
                    "invalid_requests_from_enter": invalid_enter_count,
                    "http": redemptions[:],
                    "retained": field.input_value() == "UX-NOT-REDEEMABLE",
                    "reason_after_toast_window": "邀请码无效"
                    in page.locator("#entitlementPanel").inner_text(),
                    "linked_error": field.evaluate(
                        "e=>(e.getAttribute('aria-describedby')||'').split(/\\s+/).some(id=>document.getElementById(id)?.textContent==='邀请码无效')"
                    ),
                    "invalid_semantics": field.get_attribute("aria-invalid"),
                    "focus_on_input": field.evaluate("e=>document.activeElement===e"),
                }
                if not baseline:
                    assert (
                        redemptions == [409]
                        and invalid_enter_count == 1
                        and empty_required
                    )
                    assert all(
                        result["invalid"][k]
                        for k in (
                            "label_count",
                            "retained",
                            "linked_error",
                            "reason_after_toast_window",
                            "focus_on_input",
                        )
                    )
                    assert result["invalid"]["invalid_semantics"] == "true"
                # The real server has already committed the code. Hold its response at the browser so
                # repeat Enter can be tested deterministically without issuing a second redemption.
                page.evaluate("""() => { window.fixturePageAnchor=1; const native=window.fetch.bind(window);
                  window.fetch=async (...args)=>{const response=await native(...args);
                    if(new URL(args[0],location.href).pathname==='/api/activation/redeem'){
                      await new Promise(resolve=>{window.releaseRedemption=resolve;});
                    }return response;};}""")
                field.fill(annual)
                if not baseline:
                    expect(page.locator("#activationFeedback")).to_be_empty()
                    assert field.get_attribute("aria-invalid") is None
                with page.expect_response("**/api/activation/redeem"):
                    if baseline:
                        page.locator("#redeemCode").tap()
                    else:
                        field.press("Enter")
                page.wait_for_function("typeof window.releaseRedemption==='function'")
                busy = page.locator("#redeemCode").is_disabled()
                readonly = field.evaluate("e=>e.readOnly")
                if not baseline:
                    field.press("Enter")
                    page.wait_for_timeout(100)
                    assert busy and readonly and redemptions == [409, 200]
                page.evaluate("window.releaseRedemption()")
                expect(page.locator("#entitlementTitle")).to_have_text("年度")
                expect(page.locator("#entitlementText")).to_contain_text("12")
                with app.app_context():
                    current = db.session.get(User, uid)
                    annual_on_server = (
                        current.plan == "annual"
                        and current.monitor_limit == 12
                        and current.activation_expires_at
                        > initial_expiry + timedelta(days=364)
                        and db.session.get(ActivationCode, annual_id).redeemed_by_id
                        == uid
                    )
                result["annual"] = {
                    "http": redemptions[:],
                    "server_plan_quota_expiry_and_grant_match": annual_on_server,
                    "pending_submit_disabled": busy,
                    "pending_readonly": readonly,
                    "same_document": page.evaluate("window.fixturePageAnchor===1"),
                    "filter_retained": parse_qs(urlsplit(page.url).query).get("q")
                    == ["fixture"],
                    "receipt_focused": page.evaluate(
                        "document.activeElement.id==='activationReceipt'"
                    ),
                    "horizontal_overflow": page.evaluate(
                        "document.documentElement.scrollWidth-innerWidth"
                    ),
                }
                assert annual_on_server
                if not baseline:
                    assert (
                        result["annual"]["same_document"]
                        and result["annual"]["filter_retained"]
                        and result["annual"]["receipt_focused"]
                    )
                    assert result["annual"]["horizontal_overflow"] == 0
                    expect(page.locator("#activationReceipt")).to_contain_text("12")
                    # Permanent plan intentionally hides the form; the result remains visible/focused.
                    page.evaluate("delete window.releaseRedemption")
                    page.locator("#activationCard summary").tap()
                    field.fill(permanent)
                    with page.expect_response("**/api/activation/redeem"):
                        field.press("Enter")
                    page.wait_for_function(
                        "typeof window.releaseRedemption==='function'"
                    )
                    page.evaluate("window.releaseRedemption()")
                    expect(page.locator("#entitlementTitle")).to_have_text("永久")
                    expect(page.locator("#activationCard")).to_be_hidden()
                    expect(page.locator("#activationReceipt")).to_be_focused()
                    expect(page.locator("#activationReceipt")).to_contain_text("16")
                    with app.app_context():
                        customer = db.session.get(User, uid)
                        assert (
                            customer.plan == "permanent"
                            and customer.monitor_limit == 16
                            and customer.activation_expires_at is None
                        )
                        assert (
                            db.session.get(ActivationCode, perm_id).redeemed_by_id
                            == uid
                        )
                    page.reload()
                    expect(page.locator("#entitlementTitle")).to_have_text("永久")
                    expect(page.locator("#entitlementText")).to_contain_text("16")
                    result["permanent"] = {
                        "http": redemptions[-1],
                        "server_grant_redeemed": True,
                        "persisted_after_reload": True,
                        "form_hidden_receipt_readable": True,
                    }
                landed = page.goto(origin + "/")
                if baseline:
                    expect(page.locator(".nav [data-auth]")).to_have_text("进入看板")
                hero = page.locator(".hero a[href$='/app/dashboard']")
                header = page.locator(".nav a[href$='/app/dashboard']")
                hit = hero.bounding_box() if hero.count() else None
                result["return"] = {
                    "header_native_link": header.is_visible(),
                    "hero_native_link": hero.is_visible(),
                    "hero_first_screen": bool(hit and 0 <= hit["y"] < 740),
                    "html_varies_by_cookie": "cookie"
                    in (landed.header_value("vary") or "").lower(),
                    "private_no_store": {"private", "no-store"}.issubset(
                        (landed.header_value("cache-control") or "")
                        .replace(" ", "")
                        .split(",")
                    ),
                }
                if not baseline:
                    assert all(result["return"].values())
                    hero.focus()
                    page.keyboard.press("Enter")
                    expect(page).to_have_url(re.compile(r"/app/dashboard(?:\?|$)"))
                    expect(page.locator("#navUsername")).to_have_text(MEMBER)
                page.goto(origin + "/?auth=login&next=%2Fapp%3Fview%3Dsubscriptions")
                if not baseline:
                    expect(page.locator("#subscriptionTools")).to_be_visible()
                    assert parse_qs(urlsplit(page.url).query).get("view") == [
                        "subscriptions"
                    ]
                result["return"]["bookmarked_login_target_resumed"] = (
                    urlsplit(page.url).path == "/app"
                )
                result["password_posts"] = len(logins)
                assert logins == [200]
                if not baseline:
                    assert not errors and redemptions == [409, 200, 200]
                ctx.close()
                browser.close()
        finally:
            srv.shutdown()
            thread.join()
            srv.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    if report:
        report.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf8"
        )
    print(
        "CUSTOMER JOURNEY:",
        result["phase"],
        "Chrome / annual redemption / persistent error / clipboard / return visit",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default="chrome")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    run(args.channel, args.report, args.baseline)
