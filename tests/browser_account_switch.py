"""用真实 Flask 会话、两个本地账号和两个浏览器标签页检查账号切换。"""
import argparse
import logging
import re
import tempfile
from pathlib import Path
from threading import Thread

from playwright.sync_api import expect, sync_playwright
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

from cs2_inventory.app import create_app
from cs2_inventory.database import db
from cs2_inventory.models import SteamTarget, Subscription, User


def run(channel):
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        app = create_app({'TESTING': True, 'SECRET_KEY': 'browser-account-fixture', 'STATE_DIR': root,
                          'SQLALCHEMY_DATABASE_URI': f"sqlite:///{(root / 'test.db').as_posix()}"})
        with app.app_context():
            test_hash = generate_password_hash('browser-test-password')
            for name, steamid in [('alpha', '76561198000000001'), ('beta', '76561198000000002')]:
                user = User(username=name, password_hash=test_hash, role='user', account_kind='internal', plan='permanent')
                target = SteamTarget(steamid=steamid, persona_name=f'{name}的目标')
                db.session.add_all([user, target])
                db.session.flush()
                db.session.add(Subscription(user_id=user.id, target_id=target.id))
            db.session.commit()
        server = make_server('127.0.0.1', 0, app, threaded=True)
        thread = Thread(target=server.serve_forever)
        thread.start()
        home = f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as driver:
                def open_browser():
                    return driver.chromium.launch_persistent_context(
                        str(root / 'browser-profile'), channel=channel,
                        viewport={'width': 390, 'height': 844},
                    )

                context = open_browser()
                first, second = context.new_page(), context.new_page()
                first.goto(home + '/?auth=login')
                first.locator('#username').fill('alpha')
                first.locator('#password').fill('browser-test-password')
                first.locator('#authSubmit').click()
                expect(first.locator('#navUsername')).to_have_text('alpha')
                for tab in (first, second):
                    tab.goto(home + '/app?view=monitors')
                    expect(tab.locator('#monitorList')).to_contain_text('alpha的目标')

                first.locator('#accountMenu summary').click()
                first.locator('#navSwitch').click()
                expect(first.locator('#switchUsername')).to_be_focused()
                expect(first.locator('#switchCurrentUser')).to_have_text('alpha')
                first.locator('#switchUsername').fill('beta')
                first.locator('#switchPassword').fill('wrong-password')
                first.locator('#switchAccountSubmit').click()
                expect(first.locator('#switchAccountMessage')).to_have_text('用户名或密码错误')
                expect(second.locator('#navUsername')).to_have_text('alpha')
                first.locator('#switchAccountCancel').click()
                first.locator('#accountMenu summary').click()
                first.locator('#navSwitch').click()
                expect(first.locator('#switchPassword')).to_have_value('')
                first.locator('#switchUsername').fill('beta')
                first.locator('#switchPassword').fill('browser-test-password')
                first.locator('#switchAccountSubmit').click()
                for tab in (first, second):
                    expect(tab).to_have_url(re.compile(rf'^{re.escape(home)}/app/dashboard(?:\?|$)'))
                    expect(tab.locator('#navUsername')).to_have_text('beta')
                    tab.locator('[data-workspace="monitors"]').click()
                    expect(tab.locator('#monitorList')).to_contain_text('beta的目标')
                    expect(tab.locator('#monitorList')).not_to_contain_text('alpha的目标')
                # 真正关闭浏览器，再用同一用户目录启动。不导出、注入或重建 Cookie。
                context.close()
                context = open_browser()
                first, second = context.new_page(), context.new_page()
                for tab in (first, second):
                    tab.goto(home + '/app/dashboard')
                    expect(tab.locator('#navUsername')).to_have_text('beta')
                # 两个账号均验证过后，来回切换只点账号名，不再提交密码。
                logins=[]
                switches=[]
                first.on('request',lambda req: logins.append(req.method) if req.url.endswith('/api/auth/login') else None)
                first.on('request',lambda req: switches.append(req.method) if req.url.endswith('/api/auth/switch') else None)
                for name in ('alpha','beta','alpha'):
                    first.locator('#accountMenu summary').click()
                    first.locator('#savedAccounts [data-switch-user]').filter(has_text=name).click()
                    for tab in (first,second):
                        expect(tab.locator('#navUsername')).to_have_text(name)
                    expect(first.locator('#switchAccountDialog')).to_be_hidden()
                    own=context.request.get(home+'/api/monitors').json()['items']
                    self_labels=[item['persona_name'] for item in own]
                    assert self_labels==[name+'的目标']
                assert logins==[] and switches==['POST']*3
                first.locator('#accountMenu summary').click()
                first.locator('#savedAccounts [data-forget-user]').click()
                expect(first.locator('#savedAccounts [data-switch-user]')).to_have_count(0)
                remaining=context.request.get(home+'/api/auth/accounts').json()['accounts']
                assert all(u['username']!='beta' for u in remaining)
                first.locator('#navLogout').click()
                for tab in (first, second):
                    expect(tab).to_have_url(home + '/?auth=login')
                context.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    print('ACCOUNT SWITCH: wrong password / cancel / mobile login / persistent browser restart / two tabs / scoped inventory / three one-click switches with 0 password submissions / forget / logout = PASS')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--channel', default=None)
    run(parser.parse_args().channel)
