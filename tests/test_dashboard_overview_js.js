/* 无浏览器依赖的渲染回归：node tests/test_dashboard_overview_js.js */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const nodes = new Map();
const node = id => {
  if (!nodes.has(id)) nodes.set(id, {innerHTML:'',textContent:'',clientWidth:390,dataset:{app:'/app'},
    classList:{add(){},remove(){}},querySelectorAll(){return[];},replaceChildren(){this.innerHTML='';}});
  return nodes.get(id);
};
const context = {document:{getElementById:node,querySelectorAll:()=>[]},window:{},addEventListener(){},setTimeout,clearTimeout};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/cs2_inventory/static/dashboard-overview.js'),'utf8'),context);
const overview = context.window.InventoryOverview;
function data(values) {
  return {days:3,start_date:'2026-10-21',end_date:'2026-10-23',updated_at:null,
    summary:{increase:2,decrease:0,available_days:2},
    trend:values.map((v,i)=>({date:`2026-10-${21+i}`,increase:v,decrease:v===null?null:0,compared_accounts:v===null?0:1,total_accounts:1})),
    rankings:{increase:[],decrease:[]}};
}
overview.render(data([1,null,1]));
assert.equal((node('trendChart').innerHTML.match(/<polyline/g)||[]).length,0,'缺失日期不能被线段跨过');
assert.ok(node('trendChart').innerHTML.includes('加仓 —'));
overview.render(data([0,0,0]));
assert.equal((node('trendChart').innerHTML.match(/<polyline/g)||[]).length,2,'已知零值应正常画线');
assert.ok(!node('trendChart').innerHTML.includes('NaN'));
const empty=data([null,null,null]);empty.summary={increase:null,decrease:null,available_days:0};
overview.render(empty);
assert.ok(node('trendChart').innerHTML.includes('暂无可对比记录'));
assert.ok(node('trendLegend').innerHTML.includes('—'));
const escaped=data([1,1,1]);
escaped.rankings.increase=[{name:'<script>alert(1)</script>',count:5,account_count:1,
  accounts:[{target_id:42,label:'<img src=x onerror=alert(1)>',count:5,holding:0,holding_at:null}]}];
overview.render(escaped);
const html=node('increaseRanking').innerHTML;
assert.ok(!html.includes('<script>')&&!html.includes('<img '));
assert.ok(html.includes('&lt;script&gt;')&&html.includes('持有 0'));
assert.ok(html.includes('/app/monitors/42'));
escaped.rankings.increase[0].accounts[0].holding=null;
overview.render(escaped);
assert.ok(node('increaseRanking').innerHTML.includes('持有 —'));
overview.clear();
assert.equal(node('increaseRanking').innerHTML,'');
assert.equal(node('trendChart').innerHTML,'');
console.log('OVERVIEW_RENDER gap/zero/empty/escaping/links/unknown/clear=PASS');
