"""Give dashboard and inventory fixtures the same server-backed session as a password login."""

from flask import session

from cs2_inventory.account_sessions import password_login


def sign_in_fixture(client, user):
    with client.application.test_request_context("/"):
        password_login(user)
        signed_state = dict(session)
    with client.session_transaction() as browser:
        browser.clear()
        browser.update(signed_state)
