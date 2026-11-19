"""实时查询的前置校验、成本估算、单次提交及刷新/弱网只读恢复；无供应商或 worker。"""
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
from cs2_inventory.models import ScanJob, User


class Quiet(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


def run(channel, evidence=None):
    results = {}
    with TemporaryDirectory() as tmp:
        app = fixture(Path(tmp))
        with app.app_context():
            actor = User.query.filter_by(username="cs2inventory_admin").one()
            actor.password_hash = User.query.filter_by(username=ADMIN).one().password_hash
            db.session.commit()
        server=make_server("127.0.0.1",0,app,threaded=True,request_handler=Quiet)
        thread=Thread(target=server.serve_forever)
        thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel=channel)
                ctx = browser.new_context(base_url=f"http://127.0.0.1:{server.server_port}", viewport={"width":320,"height":740},
                                          color_scheme="dark",locale="zh-CN",timezone_id="Asia/Shanghai")
                def login(name):
                    token=ctx.request.get("/api/bootstrap").json()["csrf_token"]
                    assert ctx.request.post("/api/auth/login",data={"username":name,"password":PASSWORD},headers={"X-CSRF-Token":token}).ok
                login(MEMBER)
                p=ctx.new_page()
                p.goto("/app?view=query")
                expect(p.locator("#queryView")).to_be_hidden()
                login("cs2inventory_admin")
                p.goto("/app?view=query")
                field=p.locator("#adminSteamid")
                go=p.locator("#adminQuery")
                expect(field).to_be_visible()
                expect(go).to_be_disabled()
                posts=[]
                page_errors=[]
                p.on("request",lambda req:posts.append(req.post_data_json) if req.method=="POST" and req.url.endswith("/api/admin/queries") else None)
                p.on("pageerror",lambda err:page_errors.append(str(err)))
                first_id="76561198909000001"
                second_id="76561198909000002"
                source=f"{first_id} 最初备注\nhttps://www.steamcommunity.com/profiles/{first_id}/?lang=cn 重复备注\n不是账号\n{first_id} 另一个备注"
                field.fill(source)
                expect(p.locator("#queryEstimate")).to_contain_text("1 个有效目标")
                expect(p.locator("#queryEstimate")).to_contain_text("6 credits")
                expect(p.locator("#queryEstimate")).to_contain_text("已合并 2 行")
                expect(go).to_be_disabled()
                field.press("Control+Enter")
                p.locator(".instant-row-fix").click()
                expect(field).to_be_focused()
                assert field.evaluate("e=>e.value.slice(e.selectionStart,e.selectionEnd)")=="不是账号"
                field.fill(first_id+" "+"😀"*51)
                expect(p.locator("#instantProblems")).to_contain_text("备注超过 50")
                expect(go).to_be_disabled()
                field.fill("\n".join(str(int(first_id)+i) for i in range(21)))
                expect(go).to_be_disabled()
                expect(p.locator("#instantProblems")).to_contain_text("21 个")
                if evidence:
                    evidence.mkdir(parents=True,exist_ok=True)
                    p.screenshot(path=str(evidence/"query-invalid-320-after.png"))
                with app.app_context():
                    assert ScanJob.query.count()==0
                assert not posts
                field.fill(f"https://steamcommunity.com/profiles/{first_id}/ 首选名\n{first_id} 次选名\n{second_id} 第二仓")
                expect(p.locator("#queryEstimate")).to_contain_text("2 个有效目标")
                expect(p.locator("#queryEstimate")).to_contain_text("12 credits")
                p.locator("#instantPreview summary").click()
                preview=p.locator("#instantPreviewList li")
                expect(preview).to_have_count(2)
                expect(preview.first).to_contain_text("首选名")
                field.focus()
                with p.expect_response(lambda res:res.request.method=="POST" and res.url.endswith("/api/admin/queries")) as reply:
                    field.press("Control+Enter")
                assert reply.value.status==202
                jobs=reply.value.json()["items"]
                assert [x["steamid"] for x in jobs]==[first_id,second_id]
                assert posts[0]["items"]==[{"steamid":first_id,"alias":"首选名"},{"steamid":second_id,"alias":"第二仓"}]
                expect(p.locator("#instantResultsTitle")).to_be_focused()
                field.press("Control+Enter")
                expect(go).to_be_disabled()
                result=p.locator("#adminQueryResult article")
                expect(result).to_have_count(2)
                expect(result.first).to_contain_text("等待扫描")
                assert len(posts)==1
                with app.app_context():
                    row=db.session.get(ScanJob,jobs[0]["job_id"])
                    row.status="completed"
                    row.result_json=json.dumps({"query_alias":"首选名","inventory_sections":{"protected":{"total_items":3,
                        "items":[{"name":"仅本地：红线","count":3}]},"other":{"total_items":0,"items":[]}}},ensure_ascii=False)
                    db.session.commit()
                path="**/api/jobs/"+str(jobs[0]["job_id"])
                p.route(path,lambda route:route.fulfill(status=503,json={"error":"只读模拟中断"}))
                p.reload()
                expect(p.locator("#instantStatus")).to_contain_text("已恢复")
                expect(result).to_have_count(2)
                retry=result.first.get_by_role("button",name="重新读取结果")
                expect(retry).to_be_visible()
                expect(result.first.locator("h4")).to_have_text("首选名")
                expect(result.first).to_contain_text(first_id)
                p.unroute(path)
                retry.focus()
                retry.press("Enter")
                expect(result.first).to_contain_text("仅本地：红线")
                expect(result.first.locator("h4")).to_be_focused()
                expect(result.first).to_contain_text("已完成")
                link=result.first.get_by_role("link",name="查看本条历史记录")
                link.click()
                expect(p.locator("#queryRecordBody")).to_contain_text("仅本地：红线")
                assert parse_qs(urlsplit(p.url).query)["job"]==[str(jobs[0]["job_id"])]
                assert len(posts)==1 and not page_errors
                with app.app_context():
                    assert ScanJob.query.count()==2
                results.update(invalid_queries_posted=0,valid_submission_count=1,submitted_order=[first_id,second_id],
                    jobs_created=2,refresh_and_retry_writes=0,history_matches=True,viewport=[320,740],provider_calls=0)
                ctx.close()
                browser.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    if evidence:
        (evidence/"instant-after.json").write_text(json.dumps(results,indent=2,ensure_ascii=False)+"\n",encoding="utf8")
    print("instant_draft=PASS; explicit_submit=1; invalid_writes=0; filtered_history_and_read_only_retry=PASS")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--channel",default="chrome")
    parser.add_argument("--evidence",type=Path)
    opt=parser.parse_args()
    run(opt.channel,opt.evidence)
