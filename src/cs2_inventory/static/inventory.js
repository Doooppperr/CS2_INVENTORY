const T={private:"已私密",partial:"待核验",login:'\u767b\u5f55',ready:'\u5c31\u7eea',queued:'\u6392\u961f\u4e2d',running:'\u626b\u63cf\u4e2d',failed:'\u5931\u8d25',pending:'\u7b49\u5f85\u626b\u63cf',unknown:'\u672a\u77e5'};
const params=new URLSearchParams(location.search),positiveInt=(value,fallback=1)=>{const parsed=Number.parseInt(value,10);return Number.isInteger(parsed)&&parsed>0?parsed:fallback},validAdminSection=value=>['overview','users','targets'].includes(value)?value:'overview';
const queryRecordId=value=>Number.isSafeInteger(Number(value)) && Number(value)>0?Number(value):0;
const state={historyJob:queryRecordId(params.get('job')),historyFocus:queryRecordId(params.get('focus_job')),csrf:'',user:null,page:positiveInt(params.get('page')),pages:1,currentView:'monitors',currentTarget:null,detail:null,comparison:null,detailSource:['admin','dashboard'].includes(params.get('source'))?params.get('source'):'monitors',queryHistoryPage:positiveInt(params.get('query_page')),adminSection:validAdminSection(params.get('section')),adminUserPage:positiveInt(params.get('user_page')),adminUserPages:1,adminUserQuery:params.get('user_q')||'',adminUserState:params.get('user_state')||'',adminUserTask:['create','codes'].includes(params.get('user_task'))?params.get('user_task'):'list',adminTargetPage:positiveInt(params.get('target_page')),adminTargetPages:1,adminTargetQuery:params.get('target_q')||'',adminOrder:['recent_scan','subscribers'].includes(params.get('order'))?params.get('order'):'newest',adminFailedBatch:params.get('failed_batch'),adminSubscriber:params.get('subscriber_id'),resetUserId:null,remarkTargetId:null};
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const detailMatch=location.pathname.match(/\/(?:app\/)?monitors\/(\d+)\/?$/);let basePath=location.pathname.replace(/(?:app\/)?monitors\/\d+\/?$/,'').replace(/app\/?$/,'');if(!basePath.endsWith('/'))basePath+='/';const appUrl=path=>basePath+String(path).replace(/^\//,''),consoleUrl=path=>{const value=String(path||'').replace(/^\//,'');return appUrl(value.startsWith('?')?`app${value}`:`app/${value}`)};
async function api(path,options={}){options.headers={...(options.headers||{}),'X-CSRF-Token':state.csrf};if(options.body&&!options.headers['Content-Type'])options.headers['Content-Type']='application/json';const r=await fetch(appUrl(path),options);const d=await r.json().catch(()=>({error:'\u54cd\u5e94\u683c\u5f0f\u9519\u8bef'}));if(!r.ok)throw Object.assign(new Error(d.error||`\u8bf7\u6c42\u5931\u8d25 ${r.status}`),{status:r.status});return d}
function fmt(v){return v?new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'--'}function statusText(s){return T[s]||s||T.unknown}function toast(text,ok=true){const e=$('toast');e.textContent=text;e.className=`toast ${ok?'ok':'bad'}`;clearTimeout(toast.timer);toast.timer=setTimeout(()=>e.classList.add('hidden'),3200)}function msg(text,ok=false){$('message').className=ok?'success':'error';$('message').textContent=text||''}async function busy(b,text,fn){const old=b.textContent;b.disabled=true;b.textContent=text;try{return await fn()}finally{b.disabled=false;b.textContent=old}}
function rootStateUrl(view=state.currentView){const next=new URLSearchParams();next.set('view',view);if(['monitors','subscriptions'].includes(view)){next.set('page',String(state.page));const q=$('monitorSearch').value;if(q)next.set('q',q);}else if(view==='queryHistory'){return historyUrl(state.queryHistoryPage,state.historyJob,state.historyFocus);}else{next.set('section',state.adminSection);if(state.adminSection==='users'){if(state.adminUserTask!=='list')next.set('user_task',state.adminUserTask);if(state.adminUserTask==='codes')codeHistory.urlFields(next);if(state.adminUserQuery)next.set('user_q',state.adminUserQuery);if(state.adminUserState)next.set('user_state',state.adminUserState);next.set('user_page',String(state.adminUserPage))}if(state.adminSection==='targets'){next.set('target_page',String(state.adminTargetPage));targetScopeFields(next);if(state.adminTargetQuery)next.set('target_q',state.adminTargetQuery)}}return consoleUrl(`?${next}`)}function syncRootUrl(){
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
function navigateToInventory(path,focusTarget,growth){
 syncRootUrl();
 const saved={url:rootStateUrl(),focusTarget,growth,x:window.scrollX,y:window.scrollY,userId:state.user.id,at:Date.now()};
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
// 只有从本站看板实际点进本标签页，才返回上一历史条目；复制链接、另开标签走普通看板链接。
const feedFrom=params.get('feed_from');
if(detailMatch && feedFrom && history.length>1 && document.referrer){
 try{
  const parent=new URL(document.referrer);
  if(parent.origin===location.origin && parent.pathname===consoleUrl('dashboard') && sessionStorage.getItem('cs2:feed-return')===feedFrom){
   history.replaceState({...history.state,feedFrom},'');sessionStorage.removeItem('cs2:feed-return');
  }
 }catch{}
}
function returnFromInventory(){
 if(state.detailSource==='dashboard'){
  if(feedFrom && history.state?.feedFrom===feedFrom && history.length>1){history.back();return;}
  const back=window.WorkspaceNavigation.destination(params.get('return_to'),basePath),url=new URL(back,location.origin);
  location.href=url.pathname===consoleUrl('dashboard')?back:consoleUrl('dashboard');return;
 }
 const token=params.get('return'),saved=readListReturn(token);
 if(validListReturn(saved)){location.href=saved.url+'&restore='+encodeURIComponent(token);return;}
 if(state.detailSource==='admin'){state.adminSection='targets';location.href=rootStateUrl('admin');}
 else location.href=rootStateUrl('monitors');
}
async function restoreListPosition(){
 const saved=pendingListRestore;
 if(validListReturn(saved)&&saved.url===rootStateUrl()){
  // 必须等列表和布局完成；加载期间用户已滚动时不抢夺位置。
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  let link=Number.isSafeInteger(saved.focusTarget)?$('adminTargets').querySelector('.target-open[data-id="'+saved.focusTarget+'"]'):null;
  if(!listRestoreCancelled&&link&&saved.growth)link=await AdminTargetGrowth.resume(link.closest('article'),saved.growth)||link;
  if(!listRestoreCancelled){link?.focus({preventScroll:true});window.scrollTo({left:saved.x,top:saved.y,behavior:'instant'});history.replaceState({...history.state,inventoryList:saved},'',saved.url);}
 }
 if('scrollRestoration' in history)history.scrollRestoration='auto';
}
addEventListener('pageshow',event=>{if(event.persisted&&'scrollRestoration' in history)history.scrollRestoration='auto';});
function adminHeading(){
 const title={overview:'运行概况',targets:'全部监控',users:{list:'客户与权益',create:'创建客户',codes:'续期码管理'}[state.adminUserTask]}[state.adminSection];
 $('workspaceTitle').textContent=title;document.title=title+' · 管理后台 · CS2 Inventory';
}
function showAdminSection(section){state.adminSection=validAdminSection(section);if(state.currentView==='admin')adminHeading();['overview','users','targets'].forEach(name=>{const active=name===state.adminSection;$(`admin${name[0].toUpperCase()+name.slice(1)}Page`).classList.toggle('hidden',!active);const tab=$(`admin${name[0].toUpperCase()+name.slice(1)}Tab`);tab.classList.toggle('active',active);tab.classList.toggle('ghost',!active);if(active)tab.setAttribute('aria-current','page');else tab.removeAttribute('aria-current')})}
function showView(v){
 state.currentView=v;
 $('entitlementPanel').classList.toggle('hidden',!['monitors','subscriptions'].includes(v));
 for(const [name,id] of [['monitors','monitorView'],['subscriptions','monitorView'],['admin','adminView'],['detail','detailView'],['query','queryView'],['queryHistory','queryHistoryView']]){
  if(name==='subscriptions')continue;
  $(id).classList.toggle('hidden',name==='monitors'?!['monitors','subscriptions'].includes(v):v!==name);
 }
 $('subscriptionTools').classList.toggle('hidden',v!=='subscriptions');
 $('inventoryHeading').textContent=v==='subscriptions'?'订阅列表':'库存概览';
 const inAdmin=['admin','query','queryHistory'].includes(v)||(v==='detail'&&state.detailSource==='admin');
 $('mainTabs').classList.toggle('hidden',!['monitors','subscriptions'].includes(v));
 $('adminNav').classList.toggle('hidden',!inAdmin||v==='detail');
 window.WorkspaceNavigation.activate(v==='detail'&&state.detailSource==='dashboard'?'dashboard':inAdmin?'admin':'monitors');
 $('workspaceTitle').classList.toggle('detail-sr-title',['detail','monitors','subscriptions'].includes(v));
 $('workspaceTitle').textContent=v==='detail'?'库存详情':inAdmin?'管理后台':'我的监控';
 if(v==='query')$('workspaceTitle').textContent='实时库存查询';
 if(v==='queryHistory')$('workspaceTitle').textContent='查询历史';
 document.title=$('workspaceTitle').textContent+' · CS2 Inventory';
 if(v==='detail')$('backDetail').textContent=state.detailSource==='dashboard'?'← 返回看板':state.detailSource==='admin'?'← 返回全部监控':'← 返回我的监控';
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
 if(v==='admin')await loadAdmin();else if(v==='queryHistory')await loadQueryHistory();else if(v==='query')adminInstant.resume();else await loadMonitors();
}
function renderEntitlement(e){if(!e)return;const names={admin:'\u7ba1\u7406\u5458',internal:'\u5185\u90e8\u2014\u6c38\u4e45',monthly:'\u6708\u5ea6',annual:'\u5e74\u5ea6',permanent:'\u6c38\u4e45'};let title=names[e.plan]||names[e.status]||names[e.kind]||e.status,text='';$('entitlementPanel').classList.toggle('readonly-banner',e.status==='grace');if(e.status==='grace'){text=`\u5df2\u4e8e ${fmt(e.expires_at)} \u5230\u671f \u6570\u636e\u5df2\u51bb\u7ed3 \u8bf7\u5728 ${fmt(e.grace_ends_at)} \u524d\u4f7f\u7528\u9080\u8bf7\u7801\u7eed\u671f`}else if(e.status==='expired'){text='\u76d1\u63a7\u6570\u636e\u5df2\u6e05\u7406 \u4f7f\u7528\u9080\u8bf7\u7801\u53ef\u91cd\u65b0\u5f00\u59cb'}else{text=e.monitor_limit==null?`\u5df2\u7528 ${e.monitor_count} / \u65e0\u9650`:`\u5df2\u7528 ${e.monitor_count} / ${e.monitor_limit}${e.expires_at?` \u6709\u6548\u81f3 ${fmt(e.expires_at)}`:''}`}$('entitlementTitle').innerHTML=`<span class="plan-badge">${esc(title)}</span>`;$('entitlementText').textContent=text;$('addMonitor').disabled=!e.can_add_monitor;$('steamid').disabled=!e.can_add_monitor;if(!e.can_add_monitor&&e.add_block_reason)msg(e.add_block_reason);$('activationCard').classList.toggle('hidden',['admin','internal','active_permanent'].includes(e.status));if(['grace','expired'].includes(e.status))$('activationCard').open=true}
async function boot(){const d=await api('api/bootstrap');state.csrf=d.csrf_token;state.user=d.user;$('maintenance').classList.toggle('hidden',!d.maintenance);$('maintenance').textContent=d.maintenance_message;if(!state.user){location.href=appUrl(`?auth=login&next=${encodeURIComponent(location.pathname+location.search)}`);return}$('auth').classList.add('hidden');$('app').classList.remove('hidden');window.WorkspaceNavigation.init({user:state.user,csrf:state.csrf,active:'monitors'});renderEntitlement(state.user.entitlement);if(detailMatch){showView('detail');await openDetail(Number(detailMatch[1]));return}if(!params.has('view')&&location.hash!=='#admin'){location.replace(consoleUrl('dashboard'));return}const requested=params.get('view')||(location.hash==='#admin'?'admin':'monitors');$('monitorSearch').value=params.get('q')||'';await rootView(requested);await restoreListPosition()}
function monitorEmptyState(entitlement){
 const filtered=Boolean($('monitorSearch').value.trim());
 const title=filtered?'没有找到匹配的监控对象':'还没有监控对象';
 const action=filtered?'清除搜索':(entitlement?.can_add_monitor?'添加第一个监控':'');
 return '<div class="empty monitor-empty"><strong>'+title+'</strong>'+(action?'<button id="monitorEmptyAction" type="button" class="ghost">'+action+'</button>':'')+'</div>';
}
const monitorClock=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
function monitorTime(value){return value?monitorClock.format(new Date(value)):'—';}
function cardTime(value){return value?`<time class="meta" datetime="${esc(value)}" title="${esc(fmt(value))}">${esc(monitorTime(value))}</time>`:'';}
let monitorRead=0;
async function loadMonitors(){
 const serial=++monitorRead,term=$('monitorSearch').value;
 const d=await api(`api/monitors?page=${state.page}&q=${encodeURIComponent(term)}`);
 if(serial!==monitorRead || term!==$('monitorSearch').value || !['monitors','subscriptions'].includes(state.currentView))return;
 state.page=d.page;state.pages=Math.max(1,d.pages);state.monitors=d.items;renderEntitlement(d.entitlement);syncRootUrl();
 $('myCount').textContent=d.entitlement.monitor_count??d.total;
 $('monitorMatches').hidden=!term.trim();$('monitorMatches').textContent=term.trim()?`匹配 ${d.total} 个`:'';
 $('lastScan').textContent=monitorTime(d.latest_scan_at);$('lastScan').title=fmt(d.latest_scan_at);
 $('pageLabel').parentElement.hidden=state.pages<=1;
 $('pageLabel').textContent=`${state.page} / ${state.pages}`;$('prev').disabled=state.page<=1;$('next').disabled=state.page>=state.pages;
 $('monitorList').innerHTML=d.items.length?d.items.map(x=>{
  const sections=x.inventory_sections||{},protectedSection=sections.protected||{},other=sections.other||{};
  const same=protectedSection.updated_at && protectedSection.updated_at===other.updated_at && protectedSection.updated_at===x.last_complete_snapshot_at;
  const focus=new URLSearchParams({focus_account:x.id,group:'accounts'});
  return `<article class="inventory-card"><div class="card-title"><div><h3>${esc(x.remark||x.persona_name||x.steamid)}</h3><span class="meta">${esc(x.steamid)}</span></div><a class="card-round" aria-label="${esc((x.remark||x.persona_name||x.steamid)+'的本轮新增')}" href="${esc(consoleUrl('dashboard')+'?'+focus)}">本轮新增 →</a></div>
   <div class="protection-count"><span class="protection-label">当前交易保护</span><strong>${protectedSection.total_items??'—'} <small>件</small></strong><div class="protection-growth">${[['7','七日内新增'],['1','1日内新增']].map(([days,label])=>{const value=x.protected_additions?.[days];return `<div><span>${label}</span><b>${Number.isInteger(value)&&value>=0?`+${value}`:'—'}</b></div>`}).join('')}</div>${same?'':cardTime(protectedSection.updated_at)}</div>
   <div class="card-metrics"><div><span>其他可见物品</span><b>${other.total_items??'—'}</b>${same?'':cardTime(other.updated_at)}</div><div><span>最近完整库存</span><b>${x.total_items??'—'} <small>件 / ${x.item_types??'—'} 种</small></b>${same?'':cardTime(x.last_complete_snapshot_at)}</div></div>
   <div class="card-actions">${same?`<span class="card-stamp">更新 ${cardTime(protectedSection.updated_at)}</span>`:''}<button class="card-open" data-id="${x.id}">查看库存</button>${state.currentView==='subscriptions'?`<button class="ghost remark-edit" data-id="${x.id}">备注</button>`:''}${state.user.can_query_inventory?`<button class="ghost card-scan" data-id="${x.id}">立即扫描</button>`:''}</div></article>`;
 }).join(''):monitorEmptyState(d.entitlement);
 const emptyAction=$('monitorEmptyAction');
 if(emptyAction)emptyAction.onclick=async()=>{
  if($('monitorSearch').value.trim()){$('monitorSearch').value='';state.page=1;await loadMonitors();$('monitorSearch').focus();}
  else{await rootView('subscriptions');$('steamid').focus();}
 };
 $('monitorList').querySelectorAll('.card-open').forEach(b=>b.onclick=()=>navigateToInventory(`monitors/${b.dataset.id}?source=monitors&page=${state.page}`));
 $('monitorList').querySelectorAll('.remark-edit').forEach(b=>b.onclick=()=>{const x=d.items.find(x=>x.id===Number(b.dataset.id));openRemark(x.id,x.steamid,x.remark)});
 $('monitorList').querySelectorAll('.card-scan').forEach(b=>b.onclick=()=>busy(b,'提交中',async()=>{try{await api(`api/admin/targets/${b.dataset.id}/scan`,{method:'POST'});toast('扫描已进入队列')}catch(e){toast(e.message,false)}}));
}
async function openDetail(id){
  const first=state.currentTarget!==id, retrying=document.activeElement===$('retryDetail');
  clearTimeout(state.detailPoll);
  if(first){$('detailTitle').textContent='当前库存';$('detailFeedback').hidden=false;$('detailNotice').textContent='正在读取库存…';}
  $('retryDetail').disabled=true;
  let d;
  try{d=await api(`api/monitors/${id}${state.detailSource==='admin'?'?'+new URLSearchParams(state.adminSubscriber===null?{source:'admin'}:{source:'admin',subscriber_id:state.adminSubscriber}):''}`);}
  catch(error){
    const removed=error.status===404;
    if(first||removed){$('detailData').hidden=true;$('deleteMonitor').classList.add('hidden');$('detailTitle').textContent=removed?'监控对象不可用':'库存尚未载入';$('detailMeta').textContent='';}
    const missing=state.detailSource==='admin'?(state.adminSubscriber!==null?'此对象已不在该客户的监控中。':'此监控对象不存在或已移除。'):'当前账号没有订阅此对象。';
    $('detailFeedback').hidden=false;$('detailNotice').textContent=removed?missing:first?'这次没有读到库存，请重试。':'这次没有读到最新库存，仍显示上次结果。';
    $('retryDetail').hidden=removed;return;
  }finally{$('retryDetail').disabled=false;}
  state.currentTarget=id;state.detail=d;$('detailData').hidden=false;$('detailFeedback').hidden=true;$('retryDetail').hidden=true;
  $('deleteMonitor').classList.toggle('hidden',state.detailSource!=='monitors');const m=d.monitor;
  $('detailTitle').textContent=m.remark||m.persona_name||m.label;document.title=$('detailTitle').textContent+' · 库存详情 · CS2 Inventory';
  $('detailMeta').textContent=[m.remark&&m.persona_name!==m.remark?m.persona_name:null,m.steamid!==$('detailTitle').textContent?m.steamid:null].filter(Boolean).join(' · ');
  const show=!!state.user.can_view_inventory_diagnostics;$('detailQuality').classList.toggle('hidden',!show);
  $('detailQuality').innerHTML=show?`<details><summary>扫描诊断</summary><p>${esc(d.monitor.scan_label||statusText(d.monitor.scan_status))} · ${esc(d.monitor.last_error||'')}</p><pre style="white-space:pre-wrap">${esc(JSON.stringify(d.diagnostics||{},null,2))}</pre></details>`:'';
  if(first){inventoryLimit=inventoryPageSize;state.historicalDetail=null;$('itemSearch').value=params.get('item')||'';$('protectedOnly').checked=params.get('protected')==='1';}renderDetailSummary();renderItems();if(!$('detailChanges').classList.contains('hidden'))await loadComparison(state.compareDays||1);
  if(retrying)$('detailTitle').focus();
  if(d.monitor.updating)state.detailPoll=setTimeout(()=>{if(state.currentView==='detail')openDetail(id).catch(()=>{});},3000);
}
// 区分当前合并库存和单次历史记录；摘要、列表和时间使用同一份来源。
let snapshotRead=0;
function renderDetailSummary(){
 const snap=state.historicalDetail,d=snap||state.detail;
 if(!d)return;
 const protectedCount=d.inventory_sections?.protected?.total_items ??
   (snap && !snap.inventory_sections && snap.total_items!=null && Array.isArray(snap.items) ? snap.items.reduce((n,x)=>n+(x.is_trade_protected?x.count:0),0) : null);
 $('detailTotal').textContent=d.total_items??'—';
 $('detailTypes').textContent=(snap?snap.item_types:(d.last_complete_snapshot_id?d.snapshot?.item_types:null))??'—';
 $('detailProtected').textContent=protectedCount??'—';
 $('detailTimeLabel').textContent=snap?'快照记录时间':'完整库存更新时间';
 $('detailTime').textContent=fmt(snap?snap.scanned_at:d.last_complete_snapshot_at);
 $('historicalContext').hidden=!snap;
 $('historicalDate').textContent=snap?fmt(snap.scanned_at):'';
 if(snap)$('historicalDate').dateTime=snap.scanned_at;
}
const inventoryPageSize=40;
let inventoryLimit=inventoryPageSize;
function renderItems(reveal=false){
 const q=$('itemSearch').value.trim(),matches=window.InventoryItemSearch.compile(q),only=$('protectedOnly').checked,d=state.historicalDetail||state.detail||{},sections=d.inventory_sections||{};
 let groups=[['protected','交易保护中'],['other','其他可见物品']].filter(([key])=>!only||key==='protected').map(([key,title])=>({title,...sections[key]}));
 if(state.historicalDetail&&!d.inventory_sections){groups=[{title:'历史库存',updated_at:d.scanned_at,items:(d.items||[]).filter(x=>!only||x.is_trade_protected)}];}
 if(d.sections_same_complete_round){
  const byName=new Map();for(const section of Object.values(sections))for(const item of section.items||[]){const row=byName.get(item.name)||{name:item.name,count:0,protectedCount:0};row.count+=item.count;if(item.is_trade_protected)row.protectedCount+=item.count;byName.set(item.name,row);}
  groups=[{title:'库存物品',updated_at:sections.protected?.updated_at,items:[...byName.values()].filter(x=>!only||x.protectedCount>0).sort((a,b)=>(b.protectedCount>0)-(a.protectedCount>0)||a.name.localeCompare(b.name,'zh-CN'))}];
 }
 const sort=$('itemSort').value,protectedUnits=x=>x.protectedCount??(x.is_trade_protected?x.count:0);
 const qty=x=>only?protectedUnits(x):x.count,byName=(a,b)=>a.name.localeCompare(b.name,'zh-CN');
 let count=0,shown=0;
 $('itemRows').innerHTML=groups.map(section=>{
  const rows=(section.items||[]).filter(x=>matches(x.name));
  if(sort==='count')rows.sort((a,b)=>qty(b)-qty(a)||byName(a,b));
  if(sort==='name')rows.sort(byName);
  const visible=rows.slice(0,inventoryLimit);count+=rows.length;shown+=visible.length;
  return `<section><h4>${section.title}<small class="meta">更新时间 ${fmt(section.updated_at)}</small></h4>${visible.length?visible.map((x,i)=>{
   const n=protectedUnits(x);return `<div tabindex="-1" ${reveal&&i===inventoryLimit-inventoryPageSize?'data-new-item':''} class="item-row ${n?'protected-item':''}"><span class="item-name">${esc(x.name)}${n?`<span class="protected-badge">${n} 件交易保护中</span>`:''}</span><span class="qty">${qty(x)} 件</span></div>`;
  }).join(''):`<div class="empty">${q?'没有找到匹配物品':only?'暂无交易保护中的物品':'暂无库存物品'}</div>`}</section>`;
 }).join('');
 $('itemResultMeta').textContent=`${count} 项${q?'匹配结果':''}${shown<count?` · 已显示 ${shown} 项`:''}`;
 $('itemMore').hidden=shown>=count;$('clearItemFilters').hidden=!q&&!only;
 if(reveal)$('itemRows').querySelector('[data-new-item]')?.focus();
}
async function loadComparison(days){state.compareDays=days;$('compareSwitch').querySelectorAll('button').forEach(b=>{const active=Number(b.dataset.days)===days;b.classList.toggle('active',active);b.classList.toggle('ghost',!active);b.setAttribute('aria-pressed',String(active));b.disabled=true});$('compareContent').innerHTML='<p class="meta">正在读取变化记录…</p>';try{state.comparison=await api(`api/monitors/${state.currentTarget}/compare?days=${days}`);renderComparison()}catch(e){$('compareContent').innerHTML=`<p class="error">${esc(e.message)}</p><button type="button" class="ghost" id="comparisonRetry">重新读取变化记录</button>`;$('comparisonRetry').onclick=()=>loadComparison(days)}finally{$('compareSwitch').querySelectorAll('button').forEach(b=>b.disabled=false)}}
function renderComparison(){
  const d=state.comparison,f=d?.diff,show=!!state.user.can_view_inventory_diagnostics;
  if(!d?.baseline||!d.current||!f){const message=!d?.current?'还没有完整库存可供对比。':`${d.requested_baseline_date||state.compareDays+' 天前'}没有可对比的完整库存。`;$('compareContent').innerHTML=`<div class="empty">${esc(message)}</div>`;return;}
  const rows=[...(f.added||[]).map(x=>({...x,l:'新增',v:`+${x.count} 件`})),...(f.removed||[]).map(x=>({...x,l:'移除',v:`−${x.count} 件`})),...(f.changed||[]).map(x=>({...x,l:'数量',v:`${x.before} → ${x.after} 件`})),...(f.reappeared||[]).map(x=>({...x,l:'再次出现',v:`${x.count} 件`}))];
  const diagnostic=show?`<details><summary>对比诊断</summary><p>${esc(f.comparison_note||'')}</p>${[...(f.unconfirmed||[]).map(x=>({...x,label:'本轮未确认'})),...(f.pending||[]).map(x=>({...x,label:'待核实观察'})),...(f.historical_closed||[]).map(x=>({...x,label:'历史观察结束'}))].map(x=>`<p>${x.label} · ${esc(x.name)} · ${x.count} 件</p>`).join('')}</details>`:'';
  $('compareContent').innerHTML=`<p class="meta">${fmt(d.baseline.scanned_at)} → ${fmt(d.current.scanned_at)}</p><div class="items">${rows.length?rows.map(x=>`<div class="item-row diff-row"><span class="diff-badge">${x.l}</span><span>${esc(x.name)}</span><span class="qty">${esc(x.v)}</span></div>`).join(''):'<div class="empty">两次库存对比没有已确认的新增、减少或数量变化。</div>'}</div>${diagnostic}`;
}

const listGeneration={user:0,target:0};
function adminDraft(kind,q){listGeneration[kind]++;adminTools.listState(kind,'loading',q,1)}
async function loadAdminTable(kind){
 const user=kind==='user',section=user?'users':'targets',key=user?'adminUser':'adminTarget';
 const query=state[key+'Query'],page=state[key+'Page'],generation=++listGeneration[kind];
 const current=()=>generation===listGeneration[kind]&&state.adminSection===section&&state.currentView==='admin'&&(!user||state.adminUserTask==='list');
 adminTools.listState(kind,'loading',query,page);
 const params=new URLSearchParams({page});if(query)params.set('q',query);if(user){params.set('compact','1');if(state.adminUserState)params.set('user_state',state.adminUserState);}if(!user){targetScopeFields(params);showTargetScope();}
 try{
   const data=await api(`api/admin/${section}?${params}`);if(!current())return;
   state[key+'Page']=data.page;state[key+'Pages']=Math.max(1,data.pages);
   syncRootUrl();(user?renderUsers:renderTargets)(data);adminTools.listState(kind,'loaded',query,data.page);
 }catch(error){if(current()){
   adminTools.listState(kind,'error',query,page);
   if(!user&&[400,404].includes(error.status)&&(state.adminFailedBatch!==null||state.adminSubscriber!==null)){
    $('adminTargetStatus').textContent=error.message;$('adminTargetRetry').hidden=true;
   }
 }}
}
const adminTools=AdminWorkspace.bind({
 initial:state.adminTargetQuery,userInitial:state.adminUserQuery,onDraft:adminDraft,onRetry:kind=>loadAdminTable(kind),
 onSearch:q=>{state.adminTargetQuery=q;state.adminTargetPage=1;loadAdmin().catch(e=>toast(e.message,false))},
 onUserSearch:q=>{state.adminUserQuery=q;state.adminUserPage=1;loadAdmin().catch(e=>toast(e.message,false))},
 onTask:task=>{if(task!==state.adminUserTask){state.adminUserTask=task;loadAdmin().catch(e=>toast(e.message,false))}}
});
async function loadAdmin(){showAdminSection(state.adminSection);syncRootUrl();if(state.adminSection==='overview')return AdminOverview.load({read:()=>api('api/admin/status'),privileged:state.user.can_view_inventory_diagnostics,active:()=>state.currentView==='admin'&&state.adminSection==='overview'});
 if(state.adminSection==='users'){const task=state.adminUserTask;adminTools.showTask(task);if(task==='create')return;
 if(task==='codes')return codeHistory.load();
 return loadAdminTable('user')}
if(state.adminSection==='targets')return loadAdminTable('target')}
$('adminUserState').value=state.adminUserState;
$('adminUserState').onchange=()=>{
 state.adminUserState=$('adminUserState').value;state.adminUserPage=1;state.adminUserQuery=$('adminUserSearch').value.trim();adminTools.setUserSearch(state.adminUserQuery);loadAdminTable('user');
};
function renderUsers(d){
 const counts=d.state_counts;
 if(counts){const labels={'':'全部状态',expiring:'7天内到期',grace:'宽限期',expired:'已到期'};for(const option of $('adminUserState').options){option.textContent=labels[option.value]+' · '+counts[option.value||'all'];}}
 const root=$('users'),list=d.items,meaning={active:'使用中',active_permanent:'永久有效',grace:'宽限期',expired:'已到期',admin:'管理员',internal:'内部账号'},plans={monthly:'月度',annual:'年度',permanent:'永久'};
 $('adminUserStatus').textContent=d.total?`共 ${d.total} 个账号`:state.adminUserQuery||state.adminUserState?'没有符合条件的账号':'还没有账号';
 $('userPageLabel').textContent=`${state.adminUserPage} / ${state.adminUserPages}`;$('userPrev').disabled=state.adminUserPage<=1;$('userNext').disabled=state.adminUserPage>=state.adminUserPages;
 if(!list.length){root.replaceChildren();return;}
 root.innerHTML=`<table class="data-table customer-directory"><thead><tr><th>账号</th><th>权益状态</th><th>监控与限额</th><th>管理操作</th></tr></thead><tbody>${list.map(x=>{
  const e=x.entitlement||{},customer=e.kind==='customer'&&x.role!=='admin',limit=e.monitor_limit??'不限',name=esc(x.username);
  const date=e.status==='grace'?e.grace_ends_at:e.expires_at;
  const when=customer&&date?`<small>${e.status==='grace'?'宽限至':e.status==='expired'?'到期于':'有效至'} ${esc(fmt(date))}</small>`:'';
  const stateText=meaning[e.status]||'未设置';
  return `<tr data-customer="${x.id}"><td><strong>${name}</strong><small>${x.role==='admin'?'管理员':customer?'客户':'内部账号'}${x.id===state.user.id?' · 当前账号':''}</small></td>
  <td><span class="entitlement-tag" data-status="${esc(e.status)}">${stateText}</span>${customer&&plans[e.plan]?` <span>${plans[e.plan]}</span>`:''}${when}</td>
  <td><a class="customer-monitors" href="${esc(consoleUrl('?view=admin&section=targets&subscriber_id='+x.id))}" aria-label="查看 ${name} 的 ${x.monitor_count} 个监控">${x.monitor_count} 个监控</a>${customer?`<span class="limit-summary"> / 限额 <b>${limit}</b></span><details class="limit-editor"><summary aria-label="修改 ${name} 的监控限额">修改限额</summary><form class="customer-limit-form"><label for="limit-${x.id}">${name} 的新限额</label><input id="limit-${x.id}" class="user-limit" type="number" inputmode="numeric" min="0" max="10000" step="1" value="${e.monitor_limit??''}" required><button type="submit" class="limit-save">保存限额</button></form></details><span class="limit-result" role="status"></span>`:''}</td>
  <td><details class="customer-tools"><summary aria-label="管理账号 ${name}">管理账号</summary><div class="actions"><button type="button" class="ghost user-reset" data-id="${x.id}" data-name="${name}" aria-label="重置 ${name} 的密码">重置密码</button>${x.id===state.user.id?'':`<button type="button" class="danger user-delete" data-id="${x.id}" data-name="${name}" aria-label="删除账号 ${name}">删除账号</button>`}</div></details><span class="customer-action-result" role="status"></span></td></tr>`;
 }).join('')}</tbody></table>`;AdminWorkspace.labelRows(root);
 const lookup=new Map(list.map(x=>[String(x.id),x]));
 for(const row of root.querySelectorAll('tr[data-customer]')){
  const x=lookup.get(row.dataset.customer),del=row.querySelector('.user-delete'),reset=row.querySelector('.user-reset');
  reset.onclick=()=>openPassword(x.id,x.username);
  if(del)del.onclick=()=>{if(confirm(`永久删除账号 ${x.username}？共享目标保留，无主目标和快照将一并删除。`))busy(del,'删除中…',async()=>{try{const result=await api(`api/admin/users/${x.id}`,{method:'DELETE'});toast(`账号已删除，清理 ${result.deleted_targets} 个无主目标`);await loadAdmin()}catch(e){toast(e.message,false)}});};
  const form=row.querySelector('.customer-limit-form');if(!form)continue;
  const field=form.querySelector('input'),editor=form.closest('details'),msg=row.querySelector('.limit-result'),save=form.querySelector('button');
  field.oninput=()=>{msg.textContent='';};
  form.onsubmit=e=>{e.preventDefault();if(save.disabled)return;return busy(save,'保存中…',async()=>{
   const amount=field.valueAsNumber;field.readOnly=true;
   try{await adminPatch(x.id,{monitor_limit:amount});row.querySelector('.limit-summary b').textContent=amount;msg.textContent=`限额已更新为 ${amount}`;editor.open=false;editor.querySelector('summary').focus();}
   catch(err){msg.textContent=err.message;field.focus();}finally{field.readOnly=false;}
  });};
 }
}
function targetScopeFields(query){
 if(state.adminOrder!=='newest')query.set('order',state.adminOrder);
 if(state.adminFailedBatch!==null)query.set('failed_batch',state.adminFailedBatch);
 if(state.adminSubscriber!==null)query.set('subscriber_id',state.adminSubscriber);
}
let targetScopeInfo,targetOrderTimeField='last_success_at';
function showTargetScope(scope=targetScopeInfo){
 const node=$('adminTargetScope'),values=[
  ['failed_batch',state.adminFailedBatch,'本轮失败',scope?.failed_batch?.label],
  ['subscriber_id',state.adminSubscriber,'订阅客户',scope?.subscriber?.username]
 ].filter(([,value])=>value!==null);
 node.hidden=!values.length;
 node.innerHTML=values.map(([key,value,title,label])=>{
  const href=new URL(rootStateUrl('admin'),location.origin);href.searchParams.delete(key);href.searchParams.set('target_page','1');
  return `<span class="scope-chip">${title} · ${esc(label||'#'+value)}<a data-clear-target-scope="${key}" aria-label="清除${title}筛选" href="${esc(href.pathname+href.search)}">移除</a></span>`;
 }).join('');
}
$('adminTargetOrder').value=state.adminOrder;
$('adminTargetOrder').onchange=()=>{
 const order=$('adminTargetOrder').value;if(order===state.adminOrder)return;
 const old=rootStateUrl('admin');
 state.adminOrder=order;state.adminTargetQuery=$('adminTargetSearch').value.trim();
 adminTools.setTargetSearch(state.adminTargetQuery);state.adminTargetPage=1;
 const next=rootStateUrl('admin');if(next!==old)history.pushState({},'',next);
 loadAdminTable('target');
};
function targetDetailPath(id){
 const list=new URL(rootStateUrl('admin'),location.origin).searchParams;
 list.delete('view');list.set('source','admin');
 return `monitors/${id}?${list}`;
}
function adminTargetCard(x){
 const title=x.label,remarks=x.remarks||[],term=state.adminTargetQuery.trim().toLocaleLowerCase();
 const matched=term&&remarks.find(r=>r.remark!==title&&r.remark.toLocaleLowerCase().includes(term));
 const {protected:locked={}}=x.inventory_sections||{},detail=targetDetailPath(x.id);
 const adminStatus=state.user.can_view_inventory_diagnostics&&x.scan_label;
 return `<article class="inventory-card admin-target-card" aria-labelledby="target-name-${x.id}">
  <div class="target-identity"><header class="admin-target-heading"><div><h4 id="target-name-${x.id}">${esc(title)}</h4>
   ${x.persona_name&&x.persona_name!==title?`<div class="target-nickname">Steam 昵称：${esc(x.persona_name)}</div>`:''}
   ${title!==x.steamid?`<span class="target-steamid">${esc(x.steamid)}</span>`:''}</div>
   ${adminStatus?`<span class="target-state">${esc(adminStatus)}</span>`:''}</header>
  ${state.adminOrder==='recent_scan'?`<div class="target-sorted-time">${targetOrderTimeField==='last_scan_at'?'上次扫描':'最近成功扫描'} ${cardTime(x[targetOrderTimeField])||'尚无记录'}</div>`:''}
  ${matched?`<div class="target-match">匹配备注：${esc(matched.remark)}</div>`:''}
  </div><div class="target-readout"><div class="target-metrics"><div class="target-protected" data-metric="protected"><span>当前交易保护</span><strong>${locked.total_items??'—'} <small>件</small></strong>
   ${cardTime(locked.updated_at)||(locked.total_items==null?'<small class="target-not-read">暂无记录</small>':'')}</div><div data-metric="stock"><span>最近完整库存</span><strong>${x.total_items??'—'} <small>件</small></strong>${cardTime(x.last_complete_snapshot_at)||(x.total_items==null?'<small class="target-not-read">暂无完整记录</small>':'')}</div></div>
  <div class="target-growth" role="group" aria-label="查看交易保护新增物品">${[['1','1日内新增'],['7','7日内新增']].map(([days,label])=>{const n=x.protected_additions?.[days];return `<button type="button" class="ghost growth-window" data-growth-days="${days}" aria-expanded="false" aria-controls="target-growth-${x.id}">${label} <b>${n==null?'—':'+'+n}</b><span aria-hidden="true">⌄</span></button>`;}).join('')}</div><div class="target-growth-detail" id="target-growth-${x.id}" aria-label="加仓物品" aria-live="polite" hidden></div>
  </div>${remarks.length?`<details class="target-aliases"><summary>${x.subscriber_count} 人订阅 · ${remarks.length} 条备注</summary><dl>${remarks.map(r=>`<div><dt>${esc(r.username)}</dt><dd>${esc(r.remark)}</dd></div>`).join('')}</dl></details>`:`<p class="target-subs">${x.subscriber_count} 人订阅</p>`}
  <div class="target-actions"><a class="outline-primary target-open" href="${esc(consoleUrl(detail))}" data-id="${x.id}" aria-label="查看${esc(title)}的库存">查看库存 <span aria-hidden="true">→</span></a>
   <details class="target-options"><summary aria-label="管理${esc(title)}">管理</summary><div>${state.user.can_query_inventory?`<button type="button" class="ghost target-scan" data-id="${x.id}">立即扫描</button>`:''}
   <button type="button" class="ghost target-delete" data-id="${x.id}">删除目标</button></div></details></div>
 </article>`;
}
function renderTargets(d){
 targetScopeInfo=d.scope;showTargetScope();targetOrderTimeField=d.order_time_field||(state.user.can_view_inventory_diagnostics?'last_scan_at':'last_success_at');
 $('adminTargetOrder').querySelector('[value="recent_scan"]').textContent=targetOrderTimeField==='last_scan_at'?'最近扫描':'最近成功扫描';
 const root=$('adminTargets');$('adminTargetStatus').textContent=d.total?`共 ${d.total} 个目标`:state.adminTargetQuery?'没有匹配的目标':state.adminFailedBatch!==null||state.adminSubscriber!==null?'此范围内没有监控目标':'还没有监控目标';
 $('targetPageLabel').parentElement.hidden=state.adminTargetPages<=1;$('targetPageLabel').textContent=`${state.adminTargetPage} / ${state.adminTargetPages}`;$('targetPrev').disabled=state.adminTargetPage<=1;$('targetNext').disabled=state.adminTargetPage>=state.adminTargetPages;
 root.innerHTML=d.items.map(adminTargetCard).join('');
 AdminTargetGrowth.attach(root.querySelectorAll('article'),{read:api,fmt,esc,detailPath:id=>consoleUrl(targetDetailPath(id)),navigate:(id,name,progress)=>{
  const filters=new URLSearchParams({protected:'1',item:name});navigateToInventory(targetDetailPath(id)+'&'+filters,id,progress);
 }});
 for(const link of root.querySelectorAll('.target-open'))link.onclick=e=>{
  if(e.button||e.ctrlKey||e.shiftKey||e.metaKey||e.altKey)return;
  e.preventDefault();navigateToInventory(targetDetailPath(link.dataset.id),Number(link.dataset.id));
 };
 for(const b of root.querySelectorAll('.target-scan'))b.onclick=()=>busy(b,'提交中',async()=>{try{await api(`api/admin/targets/${b.dataset.id}/scan`,{method:'POST'});toast('扫描任务已进入队列');await loadAdmin()}catch(e){toast(e.message,false)}});
 for(const b of root.querySelectorAll('.target-delete'))b.onclick=()=>{const target=d.items.find(x=>String(x.id)===b.dataset.id);
  if(confirm(`删除「${target.label}」及其全部订阅和快照？`))busy(b,'删除中',async()=>{try{await api(`api/admin/targets/${b.dataset.id}`,{method:'DELETE'});toast('监控目标已删除');await loadAdmin()}catch(e){toast(e.message,false)}});
 };
}
const codeHistory=AdminCodeHistory.bind({params,read:api,sync:syncRootUrl,esc,fmt,
 active:()=>state.currentView==='admin'&&state.adminSection==='users'&&state.adminUserTask==='codes',
 currentCode:()=>$('issuedCodePanel').dataset.codeId,clearCode:()=>adminTools.showCode('')});

const adminPatch=(id,data)=>api(`api/admin/users/${id}`,{method:'PATCH',body:JSON.stringify(data)});function openPassword(id,name){state.resetUserId=id;$('passwordDialogUser').textContent=`\u7528\u6237\uff1a${name}`;$('adminNewPassword').value='';$('passwordDialog').showModal();$('adminNewPassword').focus()}function openRemark(id,steamid,remark){state.remarkTargetId=id;$('remarkTarget').textContent=`SteamID: ${steamid}`;$('remarkInput').value=remark||'';$('remarkDialog').showModal();$('remarkInput').focus()}$('authForm').onsubmit=async e=>{e.preventDefault();try{const d=await api('api/auth/login',{method:'POST',body:JSON.stringify({username:$('username').value.trim(),password:$('password').value})});state.csrf=d.csrf_token;await boot()}catch(x){$('authMessage').className='error';$('authMessage').textContent=x.message}};
function steamProfileId(value){
 const text=value.trim();
 if(/^7656119\d{10}$/.test(text))return text;
 const match=/^https?:\/\/(?:www\.)?steamcommunity\.com\/profiles\/(7656119\d{10})\/?(?:[?#].*)?$/i.exec(text);
 return match?.[1]||'';
}
$('subscriptionTools').onsubmit=async event=>{
 event.preventDefault();const field=$('steamid'),button=$('addMonitor');
 if(button.disabled)return;
 const steamid=steamProfileId(field.value);
 if(!steamid){msg('请填入 17 位 SteamID64，或含 /profiles/ 的个人主页链接');field.focus();return;}
 await busy(button,'正在添加…',async()=>{try{
  const d=await api('api/monitors',{method:'POST',body:JSON.stringify({steamid})});
  field.value='';state.page=1;$('monitorSearch').value='';
  await loadMonitors();msg(d.job_id?'已添加，首次扫描已进入队列':'已添加，已使用最近一次库存记录',true);
 }catch(e){msg(e.message)}});
 button.disabled=field.disabled;
};
$('prev').onclick=async()=>{if(state.page>1){state.page--;syncRootUrl();await loadMonitors()}};$('next').onclick=async()=>{if(state.page<state.pages){state.page++;syncRootUrl();await loadMonitors()}};$('backDetail').onclick=returnFromInventory;$('deleteMonitor').onclick=()=>{if(state.currentTarget&&confirm('\u53d6\u6d88\u6b64\u76d1\u63a7\uff1f'))busy($('deleteMonitor'),'...',async()=>{try{await api(`api/monitors/${state.currentTarget}`,{method:'DELETE'});location.href=rootStateUrl('monitors')}catch(e){toast(e.message,false)}})};function resetInventoryList(){inventoryLimit=inventoryPageSize;renderItems();}
$('itemSearch').oninput=resetInventoryList;$('protectedOnly').onchange=resetInventoryList;$('itemSort').onchange=resetInventoryList;
$('itemMore').onclick=()=>{inventoryLimit+=inventoryPageSize;renderItems(true);};
$('clearItemFilters').onclick=()=>{$('itemSearch').value='';$('protectedOnly').checked=false;resetInventoryList();$('itemSearch').focus();};$('compareSwitch').querySelectorAll('button').forEach(b=>b.onclick=()=>loadComparison(Number(b.dataset.days)));
$('userPrev').onclick=async()=>{if(state.adminUserPage>1){state.adminUserPage--;syncRootUrl();await loadAdmin()}};$('userNext').onclick=async()=>{if(state.adminUserPage<state.adminUserPages){state.adminUserPage++;syncRootUrl();await loadAdmin()}};$('targetPrev').onclick=async()=>{if(state.adminTargetPage>1){state.adminTargetPage--;syncRootUrl();await loadAdmin()}};$('targetNext').onclick=async()=>{if(state.adminTargetPage<state.adminTargetPages){state.adminTargetPage++;syncRootUrl();await loadAdmin()}};$('createUserForm').onsubmit=async e=>{e.preventDefault();await busy($('createUser'),'...',async()=>{try{await api('api/admin/users',{method:'POST',body:JSON.stringify({username:$('newUsername').value.trim(),password:$('newPassword').value,plan:$('newPlan').value,monitor_limit:Number($('newLimit').value)})});state.adminUserQuery=$('newUsername').value.trim();state.adminUserState=$('adminUserState').value='';state.adminUserPage=1;state.adminUserTask='list';adminTools.setUserSearch(state.adminUserQuery);$('newUsername').value='';$('newPassword').value='';toast('\u5ba2\u6237\u8d26\u6237\u5df2\u521b\u5efa');await loadAdmin()}catch(x){toast(x.message,false)}})};$('passwordCancel').onclick=()=>$('passwordDialog').close();$('passwordResetForm').onsubmit=async e=>{e.preventDefault();await busy($('passwordConfirm'),'...',async()=>{try{await adminPatch(state.resetUserId,{new_password:$('adminNewPassword').value});const row=$('users').querySelector(`tr[data-customer="${state.resetUserId}"]`);if(row)row.querySelector('.customer-action-result').textContent='密码已重置';$('passwordDialog').close();toast('密码已重置');}catch(x){toast(x.message,false)}})};$('remarkCancel').onclick=()=>$('remarkDialog').close();$('remarkForm').onsubmit=async e=>{e.preventDefault();await busy($('remarkSave'),'...',async()=>{try{await api(`api/monitors/${state.remarkTargetId}`,{method:'PATCH',body:JSON.stringify({remark:$('remarkInput').value})});$('remarkDialog').close();toast('\u5907\u6ce8\u5df2\u66f4\u65b0');await loadMonitors()}catch(x){toast(x.message,false)}})};{
 const form=$('redeemCodeForm'),field=$('activationCode'),button=$('redeemCode'),feedback=$('activationFeedback'),receipt=$('activationReceipt');
 field.oninput=()=>{field.removeAttribute('aria-invalid');feedback.textContent='';receipt.textContent='';};
 form.onsubmit=async event=>{
  event.preventDefault();
  if(button.disabled)return;
  if(!field.value.trim()){field.value='';field.reportValidity();return;}
  feedback.textContent=receipt.textContent='';field.removeAttribute('aria-invalid');
  form.setAttribute('aria-busy','true');field.readOnly=true;
  let result;
  try{
   result=await busy(button,'兑换中…',()=>api('api/activation/redeem',{method:'POST',body:JSON.stringify({code:field.value.trim()})}));
  }catch(error){
   feedback.textContent=error.message;field.setAttribute('aria-invalid','true');
   if(form.contains(document.activeElement)||document.activeElement===document.body)field.focus();
   return;
  }finally{field.readOnly=false;form.removeAttribute('aria-busy');}
  const moveFocus=form.contains(document.activeElement)||document.activeElement===document.body;
  state.user=result.user;field.value='';msg('');renderEntitlement(result.user.entitlement);$('activationCard').open=false;
  receipt.textContent=`已兑换：${$('entitlementTitle').textContent}套餐，监控额度 ${result.user.entitlement.monitor_limit} 个`;
  if(moveFocus)receipt.focus();
  if(['monitors','subscriptions'].includes(state.currentView))loadMonitors().catch(error=>msg(error.message));
 };
}
$('createCodeForm').onsubmit=e=>{e.preventDefault();return busy($('createCode'),'...',async()=>{try{const d=await api('api/admin/activation-codes',{method:'POST',body:JSON.stringify({plan:$('codePlan').value,monitor_limit:Number($('codeLimit').value)})});if(state.currentView!=='admin'||state.adminSection!=='users'||state.adminUserTask!=='codes')return;$('issuedCodePanel').dataset.codeId=String(d.item.id);adminTools.showCode(d.code);toast('\u7eed\u671f\u9080\u8bf7\u7801\u5df2\u751f\u6210');await loadAdmin()}catch(e){toast(e.message,false)}})};
$('monitorSearch').oninput=()=>{clearTimeout(state.searchTimer);state.searchTimer=setTimeout(()=>{state.page=1;loadMonitors().catch(e=>toast(e.message,false));},250)};
const adminInstant = AdminInstantQuery.bind({
 api,esc,user:()=>state.user?.id,active:()=>state.currentView==='query',
 sectionsMarkup:querySectionsMarkup,recordUrl:id=>historyUrl(1,id),targetUrl:id=>consoleUrl(`monitors/${id}?source=admin`),
 rate:Number(document.body.dataset.creditRate)
});
function querySectionsMarkup(sections){return [['protected','交易保护中'],['other','其他可见物品']].map(([key,title])=>{const section=sections[key]||{};return `<details ${key==='protected'?'open':''}><summary>${title} · ${section.total_items??'—'} 件</summary><p class="meta">更新时间 ${fmt(section.updated_at)}</p>${(section.items||[]).map(x=>`<div class="item-row"><span>${esc(x.name)}</span><span>${x.count} 件</span></div>`).join('')}</details>`}).join('');}
function clearHistoryFocus(){state.historyFocus=0;history.replaceState({...history.state,queryFocus:0},'',location.href);}
function historyUrl(page,job=0,focus=0){
 const next=new URLSearchParams({view:'queryHistory',query_page:String(page)});
 if(job)next.set('job',String(job));else if(focus)next.set('focus_job',String(focus));
 return consoleUrl('?'+next);
}
function readHistoryRecord(focusTitle=true){
 const id=state.historyJob,title=$('queryRecordTitle'),status=$('queryRecordStatus'),retry=$('queryRecordRetry'),body=$('queryRecordBody');
 $('queryHistoryList').hidden=true;$('queryRecordPanel').hidden=false;
 $('queryRecordBack').href=historyUrl(state.queryHistoryPage,0,id);retry.hidden=true;
 if(focusTitle){title.textContent='查询结果';$('queryRecordMeta').textContent='';body.replaceChildren();status.textContent='正在读取查询结果…';}
 const focusIfIdle=()=>{if(focusTitle && (document.activeElement===document.body || document.activeElement===retry))title.focus();};
 return api(`api/admin/queries/${id}`).then(d=>{
  title.textContent=d.alias||d.steamid;document.title=title.textContent+' · 查询记录 · CS2 Inventory';
  const active=['queued','running'].includes(d.status);
  $('queryRecordMeta').textContent=[d.steamid,d.status==='completed'?'已完成':statusText(d.status),fmt(d.finished_at||d.created_at)].join(' · ');
  status.textContent=active?'查询正在进行…':'';
  body.innerHTML=d.inventory_sections?querySectionsMarkup(d.inventory_sections):'';
  if(d.target_id)body.insertAdjacentHTML('beforeend',`<p><a href="${consoleUrl(`monitors/${d.target_id}?source=admin`)}">查看当前库存 →</a></p>`);
  if(!d.inventory_sections&&!d.target_id&&!active&&!d.error)status.textContent='这条记录没有保留物品明细';
  if(d.diagnostics||d.error)body.insertAdjacentHTML('beforeend',`<details><summary>查询诊断</summary><pre>${esc(JSON.stringify({...d.diagnostics,error:d.error},null,2))}</pre></details>`);
  focusIfIdle();if(active)setTimeout(()=>readHistoryRecord(false),2500);
 }).catch(error=>{
  status.textContent=[401,403,404,410].includes(error.status)?error.message:'查询记录暂时未能读取';
  retry.hidden=[401,403,404,410].includes(error.status);focusIfIdle();
 });
}
$('queryRecordRetry').onclick=()=>readHistoryRecord();
let historyListRead=0;
async function loadQueryHistory(){
 if(state.historyJob)return readHistoryRecord();
 const generation=++historyListRead,requested=state.queryHistoryPage||1;
 const current=()=>generation===historyListRead&&state.currentView==='queryHistory'&&!state.historyJob;
 const rows=$('queryHistoryRows'),status=$('historyListStatus'),retry=$('historyListRetry'),prev=$('queryHistoryPrev'),next=$('queryHistoryNext');
 const fromControl=[prev,next,retry].includes(document.activeElement);
 $('queryHistoryList').hidden=false;$('queryRecordPanel').hidden=true;document.title='查询历史 · CS2 Inventory';syncRootUrl();
 if(fromControl)status.focus();
 rows.replaceChildren();rows.setAttribute('aria-busy','true');prev.disabled=next.disabled=true;retry.hidden=true;
 status.textContent='正在读取第 '+requested+' 页…';$('queryHistoryPage').textContent='第 '+requested+' 页';
 let d;
 try{d=await api(`api/admin/queries?page=${requested}`);}
 catch{
  if(!current())return;
  rows.setAttribute('aria-busy','false');status.textContent='第 '+requested+' 页查询历史读取失败。';retry.hidden=false;
  if(fromControl&&document.activeElement===status)retry.focus();return;
 }
 if(!current())return;
 rows.setAttribute('aria-busy','false');state.queryHistoryPage=d.page;state.queryHistoryPages=Math.max(1,d.pages);syncRootUrl();
 status.textContent=d.total?'共 '+d.total+' 条查询记录':'';
 $('queryHistoryPage').textContent=`${d.page} / ${state.queryHistoryPages}`;prev.disabled=d.page<=1;next.disabled=d.page>=state.queryHistoryPages;
 $('queryHistoryRows').innerHTML=d.items.length?d.items.map(x=>`<div class="history-row"><div><b>${esc(x.alias||x.steamid)}</b><p>${esc(x.steamid)} · ${fmt(x.created_at)}</p><small>${esc(x.status==='completed'?'已完成':statusText(x.status))}</small></div><a class="query-history-open" data-id="${x.id}" href="${esc(historyUrl(d.page,x.id))}" aria-label="${esc('查看 '+(x.alias||x.steamid)+' 的结果')}">查看结果</a></div>`).join(''):'<div class="empty">暂无查询记录</div>';
 for(const link of rows.querySelectorAll('.query-history-open')){
  link.onclick=event=>{if(!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){history.replaceState({...history.state,queryFocus:Number(link.dataset.id)},'',location.href);}};
 }
 const focus=state.historyFocus||history.state?.queryFocus;
 if(focus && (document.activeElement===document.body||document.activeElement===status))(rows.querySelector(`[data-id="${Number(focus)}"]`)||$('queryHistoryHeading')).focus();
 else if(fromControl&&document.activeElement===status)(rows.querySelector('a')||$('queryHistoryHeading')).focus();
}
$('historyListRetry').onclick=()=>loadQueryHistory();
$('queryHistoryPrev').onclick=()=>{clearHistoryFocus();state.queryHistoryPage=Math.max(1,state.queryHistoryPage-1);loadQueryHistory().catch(e=>toast(e.message,false))};
$('queryHistoryNext').onclick=()=>{clearHistoryFocus();state.queryHistoryPage=Math.min(state.queryHistoryPages,state.queryHistoryPage+1);loadQueryHistory().catch(e=>toast(e.message,false))};
function detailTab(tab,snapshot=null){
 snapshotRead++;
 if(state.historicalDetail!==snapshot)inventoryLimit=inventoryPageSize;
 state.historicalDetail=tab==='inventory'?snapshot:null;
 renderDetailSummary();
 for(const [key,id] of [['inventory','detailInventory'],['changes','detailChanges'],['history','detailHistory']])$(id).classList.toggle('hidden',tab!==key);
 for(const [key,id] of [['inventory','detailInventoryTab'],['changes','detailChangesTab'],['history','detailHistoryTab']]){const active=tab===key;$(id).classList.toggle('ghost',!active);$(id).setAttribute('aria-pressed',String(active));}
 if(tab==='changes')loadComparison(state.compareDays||1);
 if(tab==='history'){
 const rows=state.detail?.history||[];
 $('detailHistory').innerHTML=rows.length?rows.map(x=>`<div class="history-row"><div><b>${fmt(x.scanned_at)}</b><p class="meta">库存 ${x.total_items??'—'} 件</p></div><button class="ghost snapshot-open" data-id="${x.id}">查看记录</button></div>`).join(''):'<div class="empty">暂无库存历史</div>';
 $('detailHistory').querySelectorAll('button').forEach(b=>b.onclick=async()=>{
  const serial=++snapshotRead,target=state.currentTarget;
  b.setAttribute('aria-busy','true');b.textContent='正在读取…';
  try{const d=await api(`api/monitors/${target}/snapshots/${b.dataset.id}`);
   if(serial!==snapshotRead || target!==state.currentTarget || state.currentView!=='detail')return;
   detailTab('inventory',d.snapshot);$('currentInventory').focus();
  }catch(e){if(serial===snapshotRead)toast(e.message,false);}
  finally{b.removeAttribute('aria-busy');b.textContent='查看记录';}
 });
 }else if(tab==='inventory'){renderItems();}
}
$('retryDetail').onclick=()=>openDetail(Number(detailMatch[1]));
$('currentInventory').onclick=()=>{detailTab('inventory');$('detailInventoryTab').focus();};
$('detailInventoryTab').onclick=()=>detailTab('inventory');$('detailChangesTab').onclick=()=>detailTab('changes');$('detailHistoryTab').onclick=()=>detailTab('history');
addEventListener('popstate',()=>location.reload());
boot().catch(e=>toast(e.message,false));

// 重绘后的列表也能回到同一客户/监控对象的按钮。
$('passwordDialog').addEventListener('close',()=>{
 $('adminNewPassword').value='';
 document.querySelector('.user-reset[data-id="'+state.resetUserId+'"]')?.focus();
});
$('remarkDialog').addEventListener('close',()=>{
 document.querySelector('.remark-edit[data-id="'+state.remarkTargetId+'"]')?.focus();
});
