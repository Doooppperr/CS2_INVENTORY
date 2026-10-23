const T={private:"已私密",partial:"待核验",login:'\u767b\u5f55',ready:'\u5c31\u7eea',queued:'\u6392\u961f\u4e2d',running:'\u626b\u63cf\u4e2d',failed:'\u5931\u8d25',pending:'\u7b49\u5f85\u626b\u63cf',unknown:'\u672a\u77e5'};
const params=new URLSearchParams(location.search),positiveInt=(value,fallback=1)=>{const parsed=Number.parseInt(value,10);return Number.isInteger(parsed)&&parsed>0?parsed:fallback},validAdminSection=value=>['overview','users','targets'].includes(value)?value:'overview';
const state={csrf:'',user:null,page:positiveInt(params.get('page')),pages:1,currentView:'monitors',currentTarget:null,detail:null,comparison:null,detailSource:['admin','dashboard'].includes(params.get('source'))?params.get('source'):'monitors',queryHistoryPage:positiveInt(params.get('query_page')),adminSection:validAdminSection(params.get('section')),adminUserPage:positiveInt(params.get('user_page')),adminUserPages:1,adminTargetPage:positiveInt(params.get('target_page')),adminTargetPages:1,resetUserId:null,remarkTargetId:null};
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const detailMatch=location.pathname.match(/\/(?:app\/)?monitors\/(\d+)\/?$/);let basePath=location.pathname.replace(/(?:app\/)?monitors\/\d+\/?$/,'').replace(/app\/?$/,'');if(!basePath.endsWith('/'))basePath+='/';const appUrl=path=>basePath+String(path).replace(/^\//,''),consoleUrl=path=>{const value=String(path||'').replace(/^\//,'');return appUrl(value.startsWith('?')?`app${value}`:`app/${value}`)};
async function api(path,options={}){options.headers={...(options.headers||{}),'X-CSRF-Token':state.csrf};if(options.body&&!options.headers['Content-Type'])options.headers['Content-Type']='application/json';const r=await fetch(appUrl(path),options);const d=await r.json().catch(()=>({error:'\u54cd\u5e94\u683c\u5f0f\u9519\u8bef'}));if(!r.ok)throw new Error(d.error||`\u8bf7\u6c42\u5931\u8d25 ${r.status}`);return d}
function fmt(v){return v?new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'--'}function norm(v){return String(v||'').toLocaleLowerCase('zh-CN').trim()}function statusText(s){return T[s]||s||T.unknown}function toast(text,ok=true){const e=$('toast');e.textContent=text;e.className=`toast ${ok?'ok':'bad'}`;clearTimeout(toast.timer);toast.timer=setTimeout(()=>e.classList.add('hidden'),3200)}function msg(text,ok=false){$('message').className=ok?'success':'error';$('message').textContent=text||''}async function busy(b,text,fn){const old=b.textContent;b.disabled=true;b.textContent=text;try{return await fn()}finally{b.disabled=false;b.textContent=old}}
function rootStateUrl(view=state.currentView){const next=new URLSearchParams();next.set('view',view);if(['monitors','subscriptions'].includes(view)){next.set('page',String(state.page));const q=$('monitorSearch').value;if(q)next.set('q',q);}else if(view==='queryHistory'){next.set('query_page',String(state.queryHistoryPage));}else{next.set('section',state.adminSection);if(state.adminSection==='users')next.set('user_page',String(state.adminUserPage));if(state.adminSection==='targets')next.set('target_page',String(state.adminTargetPage))}return consoleUrl(`?${next}`)}function syncRootUrl(){
 if(detailMatch)return;
 const url=rootStateUrl(),saved=history.state?.inventoryList;
 history.replaceState({...history.state,inventoryList:saved?.url===url?saved:null},'',url);
}
// 每个历史条目独立保存位置；详情返回凭据仅在当前标签页内有效。
const listReturnPrefix='cs2-inventory-list-return:';
let listRestoreCancelled=false;
function readListReturn(token){
 if(!/^[a-z0-9-]{1,80}$/i.test(token||''))return null;
 try{return JSON.parse(sessionStorage.getItem(listReturnPrefix+token));}catch{return null;}
}
const pendingListRestore=detailMatch?null:(readListReturn(params.get('restore'))||history.state?.inventoryList);
if(pendingListRestore&&'scrollRestoration' in history)history.scrollRestoration='manual';
for(const event of ['wheel','touchstart','pointerdown','keydown']){
 addEventListener(event,()=>{listRestoreCancelled=true;},{once:true,passive:true});
}
function validListReturn(saved){
 if(!saved||saved.userId!==state.user?.id||!Number.isFinite(saved.y)||saved.y<0||
    !Number.isFinite(saved.x)||saved.x<0||!Number.isFinite(saved.at)||Date.now()-saved.at>30*60*1000)return false;
 try{const url=new URL(saved.url,location.origin);return url.origin===location.origin&&url.pathname===consoleUrl('').replace(/\/$/,'');}catch{return false;}
}
function navigateToInventory(path){
 syncRootUrl();
 const saved={url:rootStateUrl(),x:window.scrollX,y:window.scrollY,userId:state.user.id,at:Date.now()};
 history.replaceState({...history.state,inventoryList:saved},'',saved.url);
 const token=Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
 try{
  // 清理过期凭据，避免长期使用后占满 sessionStorage。
  for(let i=sessionStorage.length-1;i>=0;i--){const key=sessionStorage.key(i);if(key?.startsWith(listReturnPrefix)){
   let old;try{old=JSON.parse(sessionStorage.getItem(key));}catch{}
   if(!old||!Number.isFinite(old.at)||Date.now()-old.at>30*60*1000)sessionStorage.removeItem(key);
  }}
  sessionStorage.setItem(listReturnPrefix+token,JSON.stringify(saved));
  path+='&return='+encodeURIComponent(token);
 }catch{/* 存储被禁用时仍允许打开详情，浏览器后退使用 history.state。 */}
 location.href=consoleUrl(path);
}
function returnFromInventory(){
 if(state.detailSource==='dashboard'){
  const back=window.WorkspaceNavigation.destination(params.get('return_to'),basePath),url=new URL(back,location.origin);
  location.href=url.pathname===consoleUrl('dashboard')?back:consoleUrl('dashboard');return;
 }
 const token=params.get('return'),saved=readListReturn(token);
 if(validListReturn(saved)){location.href=saved.url+'&restore='+encodeURIComponent(token);return;}
 location.href=state.detailSource==='dashboard'?consoleUrl('dashboard'):state.detailSource==='admin'?rootStateUrl('admin'):rootStateUrl('monitors');
}
async function restoreListPosition(){
 const saved=pendingListRestore;
 if(validListReturn(saved)&&saved.url===rootStateUrl()){
  // 必须等列表和布局完成；加载期间用户已滚动时不抢夺位置。
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  if(!listRestoreCancelled){window.scrollTo({left:saved.x,top:saved.y,behavior:'instant'});history.replaceState({...history.state,inventoryList:saved},'',saved.url);}
 }
 if('scrollRestoration' in history)history.scrollRestoration='auto';
}
addEventListener('pageshow',event=>{if(event.persisted&&'scrollRestoration' in history)history.scrollRestoration='auto';});
function showAdminSection(section){state.adminSection=validAdminSection(section);['overview','users','targets'].forEach(name=>{const active=name===state.adminSection;$(`admin${name[0].toUpperCase()+name.slice(1)}Page`).classList.toggle('hidden',!active);const tab=$(`admin${name[0].toUpperCase()+name.slice(1)}Tab`);tab.classList.toggle('active',active);tab.classList.toggle('ghost',!active);if(active)tab.setAttribute('aria-current','page');else tab.removeAttribute('aria-current')})}
function showView(v){
 state.currentView=v;
 $('entitlementPanel').classList.toggle('hidden',!['monitors','subscriptions'].includes(v));
 for(const [name,id] of [['monitors','monitorView'],['subscriptions','monitorView'],['admin','adminView'],['detail','detailView'],['query','queryView'],['queryHistory','queryHistoryView']]){
  if(name==='subscriptions')continue;
  $(id).classList.toggle('hidden',name==='monitors'?!['monitors','subscriptions'].includes(v):v!==name);
 }
 $('subscriptionTools').classList.toggle('hidden',v!=='subscriptions');
 $('inventoryHeading').textContent=v==='subscriptions'?'订阅管理':'我的库存';
 const inAdmin=['admin','query','queryHistory'].includes(v)||(v==='detail'&&state.detailSource==='admin');
 $('mainTabs').classList.toggle('hidden',!['monitors','subscriptions'].includes(v));
 $('adminNav').classList.toggle('hidden',!inAdmin||v==='detail');
 window.WorkspaceNavigation.activate(v==='detail'&&state.detailSource==='dashboard'?'dashboard':inAdmin?'admin':'monitors');
 $('workspaceTitle').textContent=v==='detail'?'库存详情':inAdmin?'管理后台':'我的监控';
 document.title=$('workspaceTitle').textContent+' · CS2 Inventory';
 if(v==='detail')$('backDetail').textContent=state.detailSource==='dashboard'?'← 返回看板':state.detailSource==='admin'?'← 返回管理后台':'← 返回我的监控';
 for(const [id,view] of [['monitorTab','monitors'],['subscriptionsTab','subscriptions'],['queryTab','query'],['queryHistoryTab','queryHistory']]){
  const active=v===view;$(id).classList.toggle('active',active);$(id).classList.toggle('ghost',!active);
  if(active)$(id).setAttribute('aria-current','page');else $(id).removeAttribute('aria-current');
 }
 for(const id of ['queryTab','queryHistoryTab'])$(id).classList.toggle('hidden',!state.user?.can_query_inventory);
 document.querySelector('.nav-divider').classList.toggle('hidden',!state.user?.can_query_inventory);
 if(v==='admin')showAdminSection(state.adminSection);
}
async function rootView(v){
 if(!['monitors','subscriptions','admin','query','queryHistory'].includes(v))v='monitors';
 if(v==='admin'&&state.user?.role!=='admin')v='monitors';
 if(['query','queryHistory'].includes(v)&&!state.user?.can_query_inventory)v='monitors';
 showView(v);syncRootUrl();
 if(v==='admin')await loadAdmin();else if(v==='queryHistory')await loadQueryHistory();else if(v!=='query')await loadMonitors();
}
function renderEntitlement(e){if(!e)return;const names={admin:'\u7ba1\u7406\u5458',internal:'\u5185\u90e8\u2014\u6c38\u4e45',monthly:'\u6708\u5ea6',annual:'\u5e74\u5ea6',permanent:'\u6c38\u4e45'};let title=names[e.plan]||names[e.status]||names[e.kind]||e.status,text='';$('entitlementPanel').classList.toggle('readonly-banner',e.status==='grace');if(e.status==='grace'){text=`\u5df2\u4e8e ${fmt(e.expires_at)} \u5230\u671f \u6570\u636e\u5df2\u51bb\u7ed3 \u8bf7\u5728 ${fmt(e.grace_ends_at)} \u524d\u4f7f\u7528\u9080\u8bf7\u7801\u7eed\u671f`}else if(e.status==='expired'){text='\u76d1\u63a7\u6570\u636e\u5df2\u6e05\u7406 \u4f7f\u7528\u9080\u8bf7\u7801\u53ef\u91cd\u65b0\u5f00\u59cb'}else{text=e.monitor_limit==null?`\u5df2\u7528 ${e.monitor_count} / \u65e0\u9650`:`\u5df2\u7528 ${e.monitor_count} / ${e.monitor_limit}${e.expires_at?` \u6709\u6548\u81f3 ${fmt(e.expires_at)}`:''}`}$('entitlementTitle').innerHTML=`<span class="plan-badge">${esc(title)}</span>`;$('entitlementText').textContent=text;$('addMonitor').disabled=!e.can_add_monitor;$('steamid').disabled=!e.can_add_monitor;if(!e.can_add_monitor&&e.add_block_reason)msg(e.add_block_reason);$('activationCard').classList.toggle('hidden',['admin','internal','active_permanent'].includes(e.status))}
async function boot(){const d=await api('api/bootstrap');state.csrf=d.csrf_token;state.user=d.user;$('maintenance').classList.toggle('hidden',!d.maintenance);$('maintenance').textContent=d.maintenance_message;if(!state.user){location.href=appUrl(`?auth=login&next=${encodeURIComponent(location.pathname+location.search)}`);return}$('auth').classList.add('hidden');$('app').classList.remove('hidden');window.WorkspaceNavigation.init({user:state.user,csrf:state.csrf,active:'monitors'});renderEntitlement(state.user.entitlement);if(detailMatch){showView('detail');await openDetail(Number(detailMatch[1]));return}if(!params.has('view')&&location.hash!=='#admin'){location.replace(consoleUrl('dashboard'));return}const requested=params.get('view')||(location.hash==='#admin'?'admin':'monitors');$('monitorSearch').value=params.get('q')||'';await rootView(requested);await restoreListPosition()}
async function loadMonitors(){
 const d=await api(`api/monitors?page=${state.page}&q=${encodeURIComponent($('monitorSearch').value)}`);
 state.page=d.page;state.pages=Math.max(1,d.pages);state.monitors=d.items;renderEntitlement(d.entitlement);syncRootUrl();
 $('myCount').textContent=d.total;$('lastScan').textContent=fmt(d.latest_scan_at);
 $('pageLabel').textContent=`${state.page} / ${state.pages}`;$('prev').disabled=state.page<=1;$('next').disabled=state.page>=state.pages;
 $('monitorList').innerHTML=d.items.length?d.items.map(x=>{
  const sections=x.inventory_sections||{},protectedSection=sections.protected||{},other=sections.other||{};
  return `<article class="inventory-card"><div class="card-title"><div><h3>${esc(x.remark||x.persona_name||x.steamid)}</h3><span class="meta">${esc(x.steamid)}</span></div><span class="card-arrow">↗</span></div>
   <div class="protection-count"><span class="protection-label">交易保护中</span><strong>${protectedSection.total_items??'—'} <small>件</small></strong><div class="protection-growth">${[['7','七日内新增'],['1','1日内新增']].map(([days,label])=>{const value=x.protected_additions?.[days];return `<div><span>${label}</span><b>${Number.isInteger(value)&&value>=0?`+${value}`:'—'}</b></div>`}).join('')}</div><span class="meta">${fmt(protectedSection.updated_at)}</span></div>
   <div class="card-metrics"><div><span>其他可见物品</span><b>${other.total_items??'—'}</b><small>${fmt(other.updated_at)}</small></div><div><span>最近完整库存</span><b>${x.total_items??'—'} <small>件 / ${x.item_types??'—'} 种</small></b><small>${fmt(x.last_complete_snapshot_at)}</small></div></div>
   <div class="card-actions"><button class="card-open" data-id="${x.id}">查看库存</button>${state.currentView==='subscriptions'?`<button class="ghost remark-edit" data-id="${x.id}">备注</button>`:''}${state.user.can_query_inventory?`<button class="ghost card-scan" data-id="${x.id}">立即扫描</button>`:''}</div></article>`;
 }).join(''):'<div class="empty">暂无匹配的订阅目标</div>';
 $('monitorList').querySelectorAll('.card-open').forEach(b=>b.onclick=()=>navigateToInventory(`monitors/${b.dataset.id}?source=monitors&page=${state.page}`));
 $('monitorList').querySelectorAll('.remark-edit').forEach(b=>b.onclick=()=>{const x=d.items.find(x=>x.id===Number(b.dataset.id));openRemark(x.id,x.steamid,x.remark)});
 $('monitorList').querySelectorAll('.card-scan').forEach(b=>b.onclick=()=>busy(b,'提交中',async()=>{try{await api(`api/admin/targets/${b.dataset.id}/scan`,{method:'POST'});toast('扫描已进入队列')}catch(e){toast(e.message,false)}}));
}
async function openDetail(id){
  const first=state.currentTarget!==id,d=await api(`api/monitors/${id}`);clearTimeout(state.detailPoll);state.currentTarget=id;state.detail=d;
  $('deleteMonitor').classList.toggle('hidden',state.detailSource!=='monitors');$('detailTitle').textContent=d.monitor.label;
  $('detailMeta').textContent='';$('detailTotal').textContent=d.total_items??'—';$('detailTypes').textContent=d.last_complete_snapshot_id?(d.snapshot?.item_types??'—'):'—';
  $('detailProtected').textContent=d.inventory_sections?.protected?.total_items??'—';$('detailTime').textContent=fmt(d.last_complete_snapshot_at);
  const show=!!state.user.can_view_inventory_diagnostics;$('detailQuality').classList.toggle('hidden',!show);
  $('detailQuality').innerHTML=show?`<details><summary>扫描诊断</summary><p>${esc(d.monitor.scan_label||statusText(d.monitor.scan_status))} · ${esc(d.monitor.last_error||'')}</p><pre style="white-space:pre-wrap">${esc(JSON.stringify(d.diagnostics||{},null,2))}</pre></details>`:'';
  if(first){state.historicalDetail=null;$('itemSearch').value='';$('protectedOnly').checked=false;}renderItems();await loadComparison(state.compareDays||1);
  if(d.monitor.updating)state.detailPoll=setTimeout(()=>{if(state.currentView==='detail')openDetail(id).catch(()=>{});},3000);
}
function renderItems(){
 const q=norm($('itemSearch').value),only=$('protectedOnly').checked,d=state.historicalDetail||state.detail||{},sections=d.inventory_sections||{};
 let groups=[['protected','交易保护中'],['other','其他可见物品']].filter(([key])=>!only||key==='protected').map(([key,title])=>({title,...sections[key]}));
 if(state.historicalDetail&&!d.inventory_sections){groups=[{title:'历史库存',updated_at:d.scanned_at,items:(d.items||[]).filter(x=>!only||x.is_trade_protected)}];}
 if(d.sections_same_complete_round){
  const byName=new Map();for(const section of Object.values(sections))for(const item of section.items||[]){const row=byName.get(item.name)||{name:item.name,count:0,protectedCount:0};row.count+=item.count;if(item.is_trade_protected)row.protectedCount+=item.count;byName.set(item.name,row);}
  groups=[{title:'库存物品',updated_at:sections.protected?.updated_at,items:[...byName.values()].filter(x=>!only||x.protectedCount>0).sort((a,b)=>(b.protectedCount>0)-(a.protectedCount>0)||a.name.localeCompare(b.name,'zh-CN'))}];
 }
 let count=0;$('itemRows').innerHTML=groups.map(section=>{const rows=(section.items||[]).filter(x=>norm(x.name).includes(q));count+=rows.length;return `<section><h4>${section.title}<small class="meta">更新时间 ${fmt(section.updated_at)}</small></h4>${rows.length?rows.map(x=>{const protectedCount=x.protectedCount??(x.is_trade_protected?x.count:0);return `<div class="item-row ${protectedCount?'protected-item':''}"><span class="item-name">${esc(x.name)}${protectedCount?`<span class="protected-badge">${protectedCount} 件交易保护中</span>`:''}</span><span class="qty">${only?protectedCount:x.count} 件</span></div>`}).join(''):'<div class="empty">暂无匹配的物品</div>'}</section>`}).join('');$('itemResultMeta').textContent=`${count} 项${q?'匹配结果':''}`;
}
async function loadComparison(days){state.compareDays=days;$('compareSwitch').querySelectorAll('button').forEach(b=>{const active=Number(b.dataset.days)===days;b.classList.toggle('active',active);b.classList.toggle('ghost',!active);b.disabled=true});$('compareContent').innerHTML='<p class="meta">Loading...</p>';try{state.comparison=await api(`api/monitors/${state.currentTarget}/compare?days=${days}`);renderComparison()}catch(e){$('compareContent').innerHTML=`<p class="error">${esc(e.message)}</p>`}finally{$('compareSwitch').querySelectorAll('button').forEach(b=>b.disabled=false)}}
function renderComparison(){
  const d=state.comparison,f=d?.diff,show=!!state.user.can_view_inventory_diagnostics;
  if(!d?.baseline||!d.current||!f){$('compareContent').innerHTML='<div class="empty">暂无可展示的变化记录</div>';return;}
  const rows=[...(f.added||[]).map(x=>({...x,l:'新增',v:`+${x.count} 件`})),...(f.removed||[]).map(x=>({...x,l:'移除',v:`−${x.count} 件`})),...(f.changed||[]).map(x=>({...x,l:'数量',v:`${x.before} → ${x.after} 件`})),...(f.reappeared||[]).map(x=>({...x,l:'再次出现',v:`${x.count} 件`}))];
  const diagnostic=show?`<details><summary>对比诊断</summary><p>${esc(f.comparison_note||'')}</p>${[...(f.unconfirmed||[]).map(x=>({...x,label:'本轮未确认'})),...(f.pending||[]).map(x=>({...x,label:'待核实观察'})),...(f.historical_closed||[]).map(x=>({...x,label:'历史观察结束'}))].map(x=>`<p>${x.label} · ${esc(x.name)} · ${x.count} 件</p>`).join('')}</details>`:'';
  $('compareContent').innerHTML=`<p class="meta">${fmt(d.baseline.scanned_at)} → ${fmt(d.current.scanned_at)}</p><div class="items">${rows.length?rows.map(x=>`<div class="item-row diff-row"><span class="diff-badge">${x.l}</span><span>${esc(x.name)}</span><span class="qty">${esc(x.v)}</span></div>`).join(''):'<div class="empty">暂无可展示的变化记录</div>'}</div>${diagnostic}`;
}

async function loadAdmin(){showAdminSection(state.adminSection);syncRootUrl();if(state.adminSection==='overview'&&!state.user.can_view_inventory_diagnostics){const s=await api('api/admin/status');$('adminStatus').innerHTML=`<div class="stat"><span>监控目标</span><b>${s.targets}</b></div><div class="stat"><span>续期码</span><b>${s.activation_codes.unused}</b></div>`;return}if(state.adminSection==='overview'){const s=await api('api/admin/status'),q=s.quota||{},official=s.official_quota?.payload||{},quotaClass=q.warning_level==='critical'?'quota-critical':q.warning_level==='warning'?'quota-warning':'',quotaLabel=q.warning_level==='critical'?'\u4e25\u91cd\u9884\u8b66':q.warning_level==='warning'?'\u989d\u5ea6\u9884\u8b66':'\u6b63\u5e38';$('adminStatus').innerHTML=`<div class="stat"><span>\u5e73\u53f0\u76d1\u63a7\uff08\u53c2\u8003\u503c\uff09</span><b>${s.targets}/${s.target_limit}</b></div><div class="stat"><span>\u7b49\u5f85\u4efb\u52a1</span><b>${s.pending_jobs}</b></div><div class="stat"><span>\u4eca\u65e5\u4fdd\u5b88\u4f30\u7b97</span><b>${q.daily_used??0}/${q.daily_budget??'--'}</b></div><div class="stat ${quotaClass}"><span>\u8d26\u671f\u4f30\u7b97\u4f59\u989d \u4ec5\u9884\u8b66\u4e0d\u505c\u626b</span><b>${q.billing_remaining??'--'}</b><span>${quotaLabel}</span><span>账期起点 ${fmt(q.billing_period_start)} · ${q.billing_period_source==='provider_subscription'?'供应商订阅':'每月25日估算'}</span></div><div class="stat"><span>\u4f9b\u5e94\u5546\u4eca\u65e5 / \u672c\u6708</span><b>${official.today??'--'} / ${official.month??'--'}</b><span>${fmt(s.official_quota?.checked_at)}</span></div><div class="stat"><span>每日扫描计划（北京时间）</span><b>${q.scheduled_scans_per_day??'--'} 轮</b><span>${esc((q.scheduled_scan_hours_beijing||[]).map(h=>`${String(h).padStart(2,'0')}:00`).join(' / '))}</span><span>每目标每日约 ${q.estimated_scheduled_credits_per_target_day??'--'} credits（分页/重试另计）</span></div><div class="stat"><span>30k / 15k \u9884\u8b66\u7ebf</span><b>${q.warning_reserve??'--'} / ${q.critical_reserve??'--'}</b></div><div class="stat"><span>\u672a\u4f7f\u7528\u7eed\u671f\u7801</span><b>${s.activation_codes.unused}</b></div><div class="stat"><span>\u5df2\u5151\u6362\u7eed\u671f\u7801</span><b>${s.activation_codes.redeemed}</b></div>`;return}if(state.adminSection==='users'){const [d,codes]=await Promise.all([api(`api/admin/users?page=${state.adminUserPage}`),api('api/admin/activation-codes')]);state.adminUserPage=d.page;state.adminUserPages=Math.max(1,d.pages);syncRootUrl();renderUsers(d);renderCodes(codes);return}if(state.adminSection==='targets'){const d=await api(`api/admin/targets?page=${state.adminTargetPage}`);state.adminTargetPage=d.page;state.adminTargetPages=Math.max(1,d.pages);syncRootUrl();renderTargets(d);return}}
function renderUsers(d){$('userPageLabel').textContent=`${state.adminUserPage} / ${state.adminUserPages}`;$('userPrev').disabled=state.adminUserPage<=1;$('userNext').disabled=state.adminUserPage>=state.adminUserPages;$('users').innerHTML=`<table class="data-table"><thead><tr><th>\u7528\u6237</th><th>\u6743\u76ca</th><th>\u76d1\u63a7/\u9650\u989d</th><th>\u5f53\u524d\u5bc6\u7801</th><th>\u64cd\u4f5c</th></tr></thead><tbody>${d.items.map(x=>{const e=x.entitlement||{},limit=e.monitor_limit==null?'\u65e0\u9650':e.monitor_limit;return `<tr><td><b>${esc(x.username)}</b><br><span class="meta">${esc(x.role)} - ${x.steamids.map(esc).join(', ')||'\u65e0 SteamID'}</span></td><td>${esc(e.plan||e.status)}<br><span class="meta">${fmt(e.expires_at)}</span></td><td>${x.monitor_count}/${limit}${e.kind==='customer'?`<div class="actions"><input class="user-limit" data-id="${x.id}" type="number" min="0" max="10000" value="${e.monitor_limit??0}" style="width:90px"><button class="limit-save" data-id="${x.id}">\u4fdd\u5b58</button></div>`:''}</td><td class="password-cell">${x.password_available?esc(x.password):'<span class="meta">\u5386\u53f2\u5bc6\u7801\u4e0d\u53ef\u8bfb\u53d6</span>'}</td><td><div class="actions">${x.id===state.user.id?'<span class="meta">\u5f53\u524d\u8d26\u53f7</span>':`<button class="danger user-delete" data-id="${x.id}" data-name="${esc(x.username)}">\u5220\u9664\u8d26\u53f7</button>`}<button class="outline-primary user-reset" data-id="${x.id}" data-name="${esc(x.username)}">\u91cd\u7f6e\u5bc6\u7801</button></div></td></tr>`}).join('')}</tbody></table>`;$('users').querySelectorAll('.user-delete').forEach(b=>b.onclick=()=>{if(confirm(`\u6c38\u4e45\u5220\u9664\u8d26\u53f7 ${b.dataset.name}\uff1f\u5171\u4eab\u76ee\u6807\u4fdd\u7559\uff0c\u65e0\u4e3b\u76ee\u6807\u548c\u5feb\u7167\u5c06\u4e00\u5e76\u5220\u9664\u3002`))busy(b,'...',async()=>{try{const result=await api(`api/admin/users/${b.dataset.id}`,{method:'DELETE'});toast(`\u8d26\u53f7\u5df2\u5220\u9664\uff0c\u6e05\u7406 ${result.deleted_targets} \u4e2a\u65e0\u4e3b\u76ee\u6807`);await loadAdmin()}catch(e){toast(e.message,false)}})});$('users').querySelectorAll('.user-reset').forEach(b=>b.onclick=()=>openPassword(Number(b.dataset.id),b.dataset.name));$('users').querySelectorAll('.limit-save').forEach(b=>b.onclick=()=>{const input=$('users').querySelector(`.user-limit[data-id="${b.dataset.id}"]`);busy(b,'...',async()=>{try{await adminPatch(Number(b.dataset.id),{monitor_limit:Number(input.value)});toast('\u76d1\u63a7\u9650\u989d\u5df2\u66f4\u65b0');await loadAdmin()}catch(e){toast(e.message,false)}})})}
function renderTargets(d){$('targetPageLabel').textContent=`${state.adminTargetPage} / ${state.adminTargetPages}`;$('targetPrev').disabled=state.adminTargetPage<=1;$('targetNext').disabled=state.adminTargetPage>=state.adminTargetPages;$('adminTargets').innerHTML=`<table class="data-table"><thead><tr><th>\u76d1\u63a7\u76ee\u6807</th><th>\u8ba2\u9605</th><th>\u72b6\u6001</th><th>\u5e93\u5b58</th><th>\u6700\u8fd1\u626b\u63cf</th><th>\u64cd\u4f5c</th></tr></thead><tbody>${d.items.map(x=>`<tr><td><b>${esc(x.label)}</b></td><td>${x.subscriber_count}</td><td><span class="status ${esc(x.scan_status)}" title="${esc(x.scan_detail||x.last_error||'')}">${state.user.can_view_inventory_diagnostics?esc(x.scan_label||statusText(x.scan_status)):`交易保护中 ${x.inventory_sections?.protected?.total_items??'—'} 件`}</span>${x.scan_status==='partial'?`<div class="meta scan-quality-detail">${esc(x.scan_detail||x.last_error||'')}</div>`:''}</td><td>${'total_items' in x?(x.total_items??'—'):(x.latest?.total_items??'—')} \u4ef6</td><td>${fmt(x.last_complete_snapshot_at||x.inventory_sections?.protected?.updated_at)}</td><td><div class="actions"><button class="outline-primary target-open" data-id="${x.id}">\u67e5\u770b\u5e93\u5b58</button>${state.user.can_query_inventory?`<button class="target-scan" data-id="${x.id}">立即扫描</button>`:''}<button class="danger target-delete" data-id="${x.id}">\u5220\u9664\u76ee\u6807</button></div></td></tr>`).join('')}</tbody></table>`;$('adminTargets').querySelectorAll('.target-open').forEach(b=>b.onclick=()=>navigateToInventory(`monitors/${b.dataset.id}?source=admin&section=targets&target_page=${state.adminTargetPage}`));$('adminTargets').querySelectorAll('.target-scan').forEach(b=>b.onclick=()=>busy(b,'...',async()=>{try{await api(`api/admin/targets/${b.dataset.id}/scan`,{method:'POST'});toast('\u626b\u63cf\u4efb\u52a1\u5df2\u8fdb\u5165\u961f\u5217');await loadAdmin()}catch(e){toast(e.message,false)}}));$('adminTargets').querySelectorAll('.target-delete').forEach(b=>b.onclick=()=>{if(confirm('\u5220\u9664\u8be5\u76ee\u6807\u3001\u5168\u90e8\u8ba2\u9605\u548c\u5feb\u7167\uff1f'))busy(b,'...',async()=>{try{await api(`api/admin/targets/${b.dataset.id}`,{method:'DELETE'});toast('\u76d1\u63a7\u76ee\u6807\u5df2\u5220\u9664');await loadAdmin()}catch(e){toast(e.message,false)}})})}
function renderCodes(d){$('activationCodes').innerHTML=`<table class="data-table"><thead><tr><th>\u524d\u7f00</th><th>\u5957\u9910</th><th>\u9650\u989d</th><th>\u72b6\u6001</th><th>\u521b\u5efa/\u5151\u6362</th><th>\u64cd\u4f5c</th></tr></thead><tbody>${d.items.map(x=>`<tr><td><b>${esc(x.prefix)}...</b></td><td>${esc(x.plan)}</td><td>${x.monitor_limit}</td><td>${esc(x.status)}</td><td>${fmt(x.created_at)}<br><span class="meta">${fmt(x.redeemed_at)}</span></td><td>${x.status==='unused'?`<button class="danger code-revoke" data-id="${x.id}">\u64a4\u9500</button>`:'-'}</td></tr>`).join('')}</tbody></table>`;$('activationCodes').querySelectorAll('.code-revoke').forEach(b=>b.onclick=()=>busy(b,'...',async()=>{try{await api(`api/admin/activation-codes/${b.dataset.id}`,{method:'DELETE'});toast('\u9080\u8bf7\u7801\u5df2\u64a4\u9500');await loadAdmin()}catch(e){toast(e.message,false)}}))}
const adminPatch=(id,data)=>api(`api/admin/users/${id}`,{method:'PATCH',body:JSON.stringify(data)});function openPassword(id,name){state.resetUserId=id;$('passwordDialogUser').textContent=`\u7528\u6237\uff1a${name}`;$('adminNewPassword').value='';$('passwordDialog').showModal()}function openRemark(id,steamid,remark){state.remarkTargetId=id;$('remarkTarget').textContent=`SteamID: ${steamid}`;$('remarkInput').value=remark||'';$('remarkDialog').showModal()}$('authForm').onsubmit=async e=>{e.preventDefault();try{const d=await api('api/auth/login',{method:'POST',body:JSON.stringify({username:$('username').value.trim(),password:$('password').value})});state.csrf=d.csrf_token;await boot()}catch(x){$('authMessage').className='error';$('authMessage').textContent=x.message}};
$('addMonitor').onclick=()=>busy($('addMonitor'),'...',async()=>{try{const d=await api('api/monitors',{method:'POST',body:JSON.stringify({steamid:$('steamid').value.trim()})});msg(d.job_id?'\u5df2\u6dfb\u52a0\uff0c\u9996\u6b21\u626b\u63cf\u5df2\u8fdb\u5165\u961f\u5217':'\u5df2\u6dfb\u52a0\u5e76\u590d\u7528\u6700\u8fd1\u5feb\u7167',true);$('steamid').value='';await loadMonitors()}catch(e){msg(e.message)}});$('prev').onclick=async()=>{if(state.page>1){state.page--;syncRootUrl();await loadMonitors()}};$('next').onclick=async()=>{if(state.page<state.pages){state.page++;syncRootUrl();await loadMonitors()}};$('backDetail').onclick=returnFromInventory;$('deleteMonitor').onclick=()=>{if(state.currentTarget&&confirm('\u53d6\u6d88\u6b64\u76d1\u63a7\uff1f'))busy($('deleteMonitor'),'...',async()=>{try{await api(`api/monitors/${state.currentTarget}`,{method:'DELETE'});location.href=rootStateUrl('monitors')}catch(e){toast(e.message,false)}})};$('itemSearch').oninput=renderItems;$('protectedOnly').onchange=renderItems;$('compareSwitch').querySelectorAll('button').forEach(b=>b.onclick=()=>loadComparison(Number(b.dataset.days)));
$('userPrev').onclick=async()=>{if(state.adminUserPage>1){state.adminUserPage--;syncRootUrl();await loadAdmin()}};$('userNext').onclick=async()=>{if(state.adminUserPage<state.adminUserPages){state.adminUserPage++;syncRootUrl();await loadAdmin()}};$('targetPrev').onclick=async()=>{if(state.adminTargetPage>1){state.adminTargetPage--;syncRootUrl();await loadAdmin()}};$('targetNext').onclick=async()=>{if(state.adminTargetPage<state.adminTargetPages){state.adminTargetPage++;syncRootUrl();await loadAdmin()}};$('createUserForm').onsubmit=async e=>{e.preventDefault();await busy($('createUser'),'...',async()=>{try{await api('api/admin/users',{method:'POST',body:JSON.stringify({username:$('newUsername').value.trim(),password:$('newPassword').value,plan:$('newPlan').value,monitor_limit:Number($('newLimit').value)})});$('newUsername').value='';$('newPassword').value='';toast('\u5ba2\u6237\u8d26\u6237\u5df2\u521b\u5efa');await loadAdmin()}catch(x){toast(x.message,false)}})};$('passwordCancel').onclick=()=>$('passwordDialog').close();$('passwordResetForm').onsubmit=async e=>{e.preventDefault();await busy($('passwordConfirm'),'...',async()=>{try{await adminPatch(state.resetUserId,{new_password:$('adminNewPassword').value});$('passwordDialog').close();toast('\u5bc6\u7801\u5df2\u91cd\u7f6e\u5e76\u53ef\u5728\u5217\u8868\u4e2d\u67e5\u770b');await loadAdmin()}catch(x){toast(x.message,false)}})};$('remarkCancel').onclick=()=>$('remarkDialog').close();$('remarkForm').onsubmit=async e=>{e.preventDefault();await busy($('remarkSave'),'...',async()=>{try{await api(`api/monitors/${state.remarkTargetId}`,{method:'PATCH',body:JSON.stringify({remark:$('remarkInput').value})});$('remarkDialog').close();toast('\u5907\u6ce8\u5df2\u66f4\u65b0');await loadMonitors()}catch(x){toast(x.message,false)}})};$('redeemCode').onclick=()=>busy($('redeemCode'),'...',async()=>{try{await api('api/activation/redeem',{method:'POST',body:JSON.stringify({code:$('activationCode').value.trim()})});toast('\u7eed\u671f\u6216\u5347\u7ea7\u6210\u529f');location.reload()}catch(e){toast(e.message,false)}});$('createCode').onclick=()=>busy($('createCode'),'...',async()=>{try{const d=await api('api/admin/activation-codes',{method:'POST',body:JSON.stringify({plan:$('codePlan').value,monitor_limit:Number($('codeLimit').value)})});$('createdCode').textContent=`\u8bf7\u7acb\u5373\u590d\u5236 \u5b8c\u6574\u7eed\u671f\u9080\u8bf7\u7801\u53ea\u663e\u793a\u4e00\u6b21 ${d.code}`;toast('\u7eed\u671f\u9080\u8bf7\u7801\u5df2\u751f\u6210');await loadAdmin()}catch(e){toast(e.message,false)}});
$('monitorSearch').oninput=()=>{clearTimeout(state.searchTimer);state.searchTimer=setTimeout(()=>{state.page=1;loadMonitors().catch(e=>toast(e.message,false));},250)};
function queryEntries(){return $('adminSteamid').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean).map(line=>{const match=line.match(/^([0-9]+)(?:[\s,，]+(.*))?$/);return {steamid:match?.[1]||line,alias:match?.[2]||''};});}
$('adminSteamid').oninput=()=>{const count=new Set(queryEntries().map(x=>x.steamid)).size,rate=Number(document.body.dataset.creditRate)||3;$('queryEstimate').textContent=`${count} 个目标，基础约 ${count*2} 次付费请求 / ${count*2*rate} credits；分页、重试另计，已在运行的任务复用。`;};
$('adminQuery').onclick=()=>busy($('adminQuery'),'提交中',async()=>{
 try{const items=queryEntries();if(!items.length)throw new Error('请输入 SteamID64');
 const d=await api('api/admin/queries',{method:'POST',body:JSON.stringify({items})});
 state.queryToken=(state.queryToken||0)+1;const token=state.queryToken;
 $('queryEstimate').textContent=`本批新增任务基础 ${d.estimated_requests} 次请求，约 ${d.estimated_requests*d.credits_per_request} credits；分页、重试另计，复用任务不重复查询。`;
 $('adminQueryResult').innerHTML=d.items.map(x=>`<article class="query-result" id="query-${x.job_id}"><h3>${esc(x.alias||x.steamid)}</h3><p class="meta">${esc(x.steamid)} · ${x.reused?'复用已有任务':'已加入队列'}</p><div class="query-body">等待保护库存</div></article>`).join('');
 d.items.forEach(x=>refreshQuery(x,token));
 }catch(e){toast(e.message,false)}
});
async function refreshQuery(entry,token){
 if(token!==state.queryToken)return;
 const holder=$(`query-${entry.job_id}`);if(!holder)return;
 try{const d=await api(`api/jobs/${entry.job_id}`);let result=d.result;
 if(result?.target_id)result=await api(`api/monitors/${result.target_id}`);
 if(token!==state.queryToken)return;
 const sections=result?.inventory_sections;
 holder.querySelector('.query-body').innerHTML=(sections?[['protected','交易保护中'],['other','其他可见物品']].map(([key,title])=>{const section=sections[key]||{};return `<details ${key==='protected'?'open':''}><summary>${title} · ${section.total_items??'—'} 件</summary><p class="meta">更新时间 ${fmt(section.updated_at)}</p>${(section.items||[]).map(x=>`<div class="item-row"><span>${esc(x.name)}</span><span>${x.count} 件</span></div>`).join('')}</details>`}).join(''):'<p>正在查询库存…</p>')+`<p class="meta">${['queued','running'].includes(d.status)?'正在更新':'本次查询已结束'}${entry.target_id?` · <a href="${consoleUrl(`monitors/${entry.target_id}?source=admin`)}">查看库存</a>`:''}</p>`;
 if(!['queued','running'].includes(d.status)){
  const diagnostic=d.result?.diagnostics||d.result?.quality;
  if(diagnostic||d.error)holder.querySelector('.query-body').innerHTML+=`<details><summary>查询诊断</summary><pre>${esc(JSON.stringify({...(diagnostic||{}),error:d.error},null,2))}</pre></details>`;
  return;
 }
 setTimeout(()=>refreshQuery(entry,token),2500);
 }catch(e){holder.querySelector('.query-body').textContent=e.message;}
}
async function loadQueryHistory(){
 const d=await api(`api/admin/queries?page=${state.queryHistoryPage||1}`);state.queryHistoryPage=d.page;state.queryHistoryPages=Math.max(1,d.pages);syncRootUrl();
 $('queryHistoryPage').textContent=`${d.page} / ${state.queryHistoryPages}`;$('queryHistoryPrev').disabled=d.page<=1;$('queryHistoryNext').disabled=d.page>=state.queryHistoryPages;
 $('queryHistoryRows').innerHTML=d.items.length?d.items.map(x=>`<div class="history-row"><div><b>${esc(x.alias||x.steamid)}</b><p>${esc(x.steamid)} · ${fmt(x.created_at)}</p><small>${esc(statusText(x.status))}</small></div><button class="ghost query-history-open" data-id="${x.id}" data-target="${x.target_id||''}" data-steamid="${esc(x.steamid)}">查看结果</button></div>`).join(''):'<div class="empty">暂无查询记录</div>';
 $('queryHistoryRows').querySelectorAll('button').forEach(b=>b.onclick=async()=>{history.pushState({},'',consoleUrl('?view=query'));await rootView('query');state.queryToken=(state.queryToken||0)+1;$('adminQueryResult').innerHTML=`<article class="query-result" id="query-${b.dataset.id}"><h3>${esc(b.dataset.steamid)}</h3><div class="query-body"></div></article>`;refreshQuery({job_id:b.dataset.id,target_id:b.dataset.target},state.queryToken);});
}
$('queryHistoryPrev').onclick=()=>{state.queryHistoryPage=Math.max(1,state.queryHistoryPage-1);loadQueryHistory().catch(e=>toast(e.message,false))};
$('queryHistoryNext').onclick=()=>{state.queryHistoryPage=Math.min(state.queryHistoryPages,state.queryHistoryPage+1);loadQueryHistory().catch(e=>toast(e.message,false))};
function detailTab(tab){
 for(const [key,id] of [['inventory','detailInventory'],['changes','detailChanges'],['history','detailHistory']])$(id).classList.toggle('hidden',tab!==key);
 if(tab==='history'){
 const rows=state.detail?.history||[];
 $('detailHistory').innerHTML=rows.length?rows.map(x=>`<div class="history-row"><div><b>${fmt(x.scanned_at)}</b><p class="meta">库存 ${x.total_items??'—'} 件</p></div><button class="ghost snapshot-open" data-id="${x.id}">查看记录</button></div>`).join(''):'<div class="empty">暂无库存历史</div>';
 $('detailHistory').querySelectorAll('button').forEach(b=>b.onclick=async()=>{try{const d=await api(`api/monitors/${state.currentTarget}/snapshots/${b.dataset.id}`);state.historicalDetail=d.snapshot;renderItems();$('detailHistory').classList.add('hidden');$('detailInventory').classList.remove('hidden');$('itemResultMeta').textContent=`历史记录 · ${fmt(d.snapshot.scanned_at)}`;}catch(e){toast(e.message,false)}});
 }else if(tab==='inventory'){state.historicalDetail=null;renderItems();}
}
$('detailInventoryTab').onclick=()=>detailTab('inventory');$('detailChangesTab').onclick=()=>detailTab('changes');$('detailHistoryTab').onclick=()=>detailTab('history');
addEventListener('popstate',()=>location.reload());
boot().catch(e=>toast(e.message,false));
