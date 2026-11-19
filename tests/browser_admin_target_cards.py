"""普通管理员：按订阅备注读全局库存、不改变个人订阅、真实点入/回到原卡片。"""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright
from test_admin_workspace import ADMIN, MEMBER, PASSWORD, fixture
from werkzeug.serving import WSGIRequestHandler, make_server

from cs2_inventory.database import db
from cs2_inventory.models import SteamTarget, Subscription, User


class Quiet(WSGIRequestHandler):
    def log(self, *args):
        pass


def run(channel, baseline, out):
    with TemporaryDirectory() as tmp:
        app = fixture(Path(tmp))
        with app.app_context():
            subscriber = User.query.filter_by(username=MEMBER).one()
            other = User(username='别名来源二', account_kind='internal', password_hash=subscriber.password_hash)
            db.session.add(other)
            db.session.flush()
            t = SteamTarget.query.filter_by(persona_name='Needle_5% Alpha').one()
            key, sid = t.id, t.steamid
            db.session.add(Subscription(user_id=subscriber.id, target_id=key, remark='主仓·红线'))
            db.session.flush()
            db.session.add(Subscription(user_id=other.id, target_id=key, remark='朋友给的收藏别名'))
            db.session.commit()
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=Quiet)
        thread = Thread(target=server.serve_forever)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        report = {}
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel=channel)
                ctx = browser.new_context(base_url=base, locale='zh-CN')
                token = ctx.request.get('/api/bootstrap').json()['csrf_token']
                assert ctx.request.post('/api/auth/login', data={'username':ADMIN,'password':PASSWORD},
                                        headers={'X-CSRF-Token':token}).ok
                page = ctx.new_page()
                errs = []
                page.on('pageerror', lambda e: errs.append(str(e)))
                for width, theme in ((1440, 'light'), (390, 'dark'), (320,'dark')):
                    page.set_viewport_size({'width':width,'height':844})
                    page.emulate_media(color_scheme=theme)
                    page.goto('/app?view=admin&section=targets&target_page=3')
                    expect(page.locator('#targetPageLabel')).to_have_text('3 / 3')
                    search = page.locator('#adminTargetSearch')
                    rows = page.locator('#adminTargets tbody tr' if baseline else '#adminTargets article')
                    search.fill(sid)
                    search.press('Enter')
                    expect(rows).to_have_count(1)
                    first = rows.first
                    title = first.locator('td b' if baseline else 'h4').first.inner_text()
                    search.fill('朋友给的收藏别名')
                    search.press('Enter')
                    expect(page.locator('#adminTargetStatus')).to_have_text('没有匹配的目标' if baseline else '共 1 个目标')
                    report[width] = dict(title=title, alias_hits=rows.count(),table=page.locator('#adminTargets table').count())
                    if not baseline:
                        assert title=='主仓·红线'
                        expect(first).to_contain_text(sid)
                        expect(first).to_contain_text('Needle_5% Alpha')
                        expect(first.get_by_text('当前交易保护',exact=True)).to_be_visible()
                        assert '—' in first.locator('[data-metric=protected]').inner_text()
                        alias = first.locator('.target-aliases')
                        alias.locator('summary').click()
                        expect(alias).to_contain_text('别名来源二')
                        expect(alias).to_contain_text('朋友给的收藏别名')
                        alias.locator('summary').click()
                        if out:
                            out.mkdir(exist_ok=True,parents=True)
                            page.screenshot(path=out/f'admin-targets-{width}.png')
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        link=first.locator('.target-open')
                        assert link.evaluate("e=>e.tagName==='A' && !!e.href")
                        link.click()
                        expect(page.locator('#detailTitle')).to_have_text('主仓·红线')
                        expect(page.locator('#deleteMonitor')).to_be_hidden()
                        assert parse_qs(urlsplit(page.url).query)['source']==['admin']
                        page.locator('#backDetail').click()
                        expect(search).to_have_value('朋友给的收藏别名')
                        expect(page.locator('.target-open')).to_be_focused()
                        assert parse_qs(urlsplit(page.url).query)['section']==['targets']
                        page.locator('.target-open').click()
                        page.go_back()
                        expect(page.locator('.target-open')).to_be_focused()
                        expect(search).to_have_value('朋友给的收藏别名')
                    if baseline and out:
                        out.mkdir(exist_ok=True,parents=True)
                        search.fill(sid)
                        search.press('Enter')
                        expect(rows).to_have_count(1)
                        page.screenshot(path=out/f'admin-targets-old-{width}.png')
                assert ctx.request.get('/api/monitors').json()['total']==0
                assert not errs, errs
                ctx.close()
                browser.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    if out:
        (out/('before.json' if baseline else 'after.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print('admin_name='+report[390]['title']+'; secondary_remark_hits='+str(report[390]['alias_hits'])+
          '; cards='+str(not bool(report[390]['table'])).lower()+'; personal_subscriptions=0')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--channel',default='chrome')
    parser.add_argument('--baseline',action='store_true')
    parser.add_argument('--out',type=Path)
    args = parser.parse_args()
    run(args.channel,args.baseline,args.out)
