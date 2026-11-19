/* 轮次改变才取新记录；搜索、按账号筛选和展开明细都在当前页完成。 */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = value => value == null ? '—' : value.toLocaleString('zh-CN');
  const stamp = value => new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value));
  const batchSize = 6;
  let data, request, search, target, limit, base, read, sync, view, reader, followInventory;
  let targetLabels=new Map(),sameNames=new Set();
  const marketKey=item=>item.market_name||item.name;
  function repeated(rows){
    const seen=new Set(),duplicates=new Set();
    for(const key of rows){if(seen.has(key))duplicates.add(key);else seen.add(key);}
    return duplicates;
  }
  const accountLabel=a=>targetLabels.get(String(a.target_id))||a.label;
  const variant=item=>sameNames.has(item.name)&&item.market_name?item.market_name:'';
  // 阅读进度仅属于当前历史条目；不保存饰品/账号名称、查询词或库存响应。
  let reading=history.state?.feedReading, moved=false;
  const readingRoute=location.pathname+location.search;
  if(reading){
    history.scrollRestoration='manual';
    for(const event of ['wheel','touchstart','pointerdown','keydown'])addEventListener(event,()=>{moved=true;},{once:true,passive:true});
  }
  function saveReading(event){
    const link=event.target.closest('#signalList li a');
    if(!link || event.defaultPrevented || event.button!==0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target==='_blank')return;
    const rows=[...byId('signalList').children], row=link.closest('details');
    const saved={user:reader,day:data.date,slot:data.slot,revision:data.revision,limit,
      open:rows.flatMap((d,i)=>d.open?[i]:[]),index:rows.indexOf(row),branch:[...row.querySelectorAll('li a')].indexOf(link),
      offset:link.getBoundingClientRect().top,y:scrollY};
    followInventory(event,link,{feedReading:saved});
  }
  function restoreReading(saved){
    const rows=byId('signalList').children;
    for(const i of saved.open)if(rows[i])rows[i].open=true;
    const link=rows[saved.index]?.querySelectorAll('li a')[saved.branch];
    requestAnimationFrame(()=>requestAnimationFrame(()=>{
      if(!moved){
        if(link){link.focus({preventScroll:true});scrollBy({top:link.getBoundingClientRect().top-saved.offset,behavior:'instant'});}
        else scrollTo({top:saved.y,behavior:'instant'});
      }
      history.scrollRestoration='auto';
    }));
  }
  function openKeys(){return new Set([...byId('signalList').querySelectorAll('details[open]')].map(d=>d.dataset.groupKey));}
  function reopen(keys){for(const d of byId('signalList').children)d.open=keys.has(d.dataset.groupKey);}
  function liveFocus(){
    const el=document.activeElement,group=el.closest('#signalList details');
    if(!group)return null;
    return {view,group:group.dataset.groupKey,branch:el.dataset.branchKey??null};
  }
  function restoreLiveFocus(point){
    const row=[...byId('signalList').children].find(el=>el.dataset.groupKey===point.group);
    if(!row||point.view!==view||document.activeElement!==document.body)return;
    const el=point.branch===null?row.querySelector('summary'):[...row.querySelectorAll('a')].find(a=>a.dataset.branchKey===point.branch);
    el?.focus({preventScroll:true});
  }
  function urlFields(params) {
    if(view==='accounts')params.set('group','accounts');else params.delete('group');
    for (const [k,v] of [['slot',byId('signalSlot').value],['focus_item',search],['focus_account',target]]) {
      if (v && (k !== 'slot' || v !== 'latest')) params.set(k,v); else params.delete(k);
    }
  }
  function notice(message='') {
    byId('signalNotice').textContent = message;
    byId('signalNotice').hidden = !message;
  }
  function clear() {
    request?.abort();
    data = null;
    for (const id of ['signalUnits','signalTypes','signalOwners']) byId(id).textContent = '—';
    for (const id of ['signalStamp','signalCoverage','signalList']) byId(id).replaceChildren();
    for (const id of ['signalTools','signalMore','signalClear','signalManage','signalRetry','signalPrevious']) byId(id).hidden = true;
    notice();
  }
  function monitorLink(a,name) {
    const params = new URLSearchParams({source:'dashboard', return_to:location.pathname+location.search, protected:'1',item:name});
    return `${base.replace(/\/$/,'')}/monitors/${encodeURIComponent(a.target_id)}?${params}`;
  }
  function groupRow(key,label,preview,count,branches) {
    return `<details class="signal-item" data-group-key="${esc(key)}"><summary><span class="signal-name"><b>${esc(label)}</b><small>${esc(preview)}</small></span><strong class="signal-count">+${num(count)} <small>件</small></strong><span class="signal-chevron" aria-hidden="true">⌄</span></summary><ul>${branches}</ul></details>`;
  }
  function branch(label,meta,a,item,key) {
    return `<li><div><strong>${esc(label)}</strong><small>${esc(meta)}</small></div><b>+${num(a.count)}</b><a data-branch-key="${esc(key)}" href="${esc(monitorLink(a,item.name))}">查看当前库存 →</a></li>`;
  }
  function itemRow(item) {
    const labels=item.accounts.slice(0,2).map(a=>`${accountLabel(a)} +${num(a.count)}`).join(' · ') + (item.accounts.length>2?` 等 ${num(item.accounts.length)} 个账号`:'');
    return groupRow(marketKey(item),item.name,[variant(item),labels].filter(Boolean).join(' · '),item.count,item.accounts.map(a=>branch(accountLabel(a),`${stamp(a.baseline_at)} → ${stamp(a.scanned_at)}`,a,item,a.target_id)).join(''));
  }
  function byAccount(items) {
    const groups=new Map();
    for(const item of items)for(const account of item.accounts){
      const id=account.target_id;
      if(!groups.has(id))groups.set(id,{id,label:accountLabel(account),count:0,entries:[]});
      const group=groups.get(id);
      group.count+=account.count;group.entries.push({item,account});
    }
    return [...groups.values()].sort((a,b)=>b.count-a.count || a.label.localeCompare(b.label,'zh-CN') || a.id-b.id);
  }
  function accountRow(group) {
    const entries=group.entries.sort((a,b)=>b.account.count-a.account.count || a.item.name.localeCompare(b.item.name,'zh-CN'));
    const first=entries[0];
    const preview=`${num(entries.length)} 种 · ${first.item.name} +${num(first.account.count)}`;
    return groupRow(group.id,group.label,preview,group.count,entries.map(({item,account:a})=>branch(item.name,[variant(item),`${stamp(a.baseline_at)} → ${stamp(a.scanned_at)}`].filter(Boolean).join(' · '),a,item,marketKey(item))).join(''));
  }
  function paint() {
    if (!data) return;
    const {items,targets}=data;
    const scope = target ? targets.filter(a=>String(a.target_id)===target) : targets;
    const observed = scope.filter(a=>a.scanned_at), compared = scope.filter(a=>a.baseline_at);
    const matches = window.InventoryItemSearch.compile(search);
    let selected = items;
    if (target) {
      selected = items.flatMap(item=>{
        const account=item.accounts.find(a=>String(a.target_id)===target);
        return account ? [{...item,accounts:[account],count:account.count}] : [];
      }).sort((a,b)=>b.count-a.count || a.name.localeCompare(b.name,'zh-CN'));
    }
    const filtered=selected.filter(item=>matches(item.name,item.market_name));
    const units=filtered.reduce((sum,i)=>sum+i.count,0);
    const owners=new Set(filtered.flatMap(i=>i.accounts.map(a=>a.target_id))).size;
    const updated=observed.reduce((latest,a)=>a.scanned_at>latest?a.scanned_at:latest,'');
    const grouping=view==='accounts', rows=grouping?byAccount(filtered):filtered;
    const term=search.trim();
    byId('signalUnitsLabel').textContent=target?(term?'该账号匹配件数':'该账号新增件数'):(term?'搜索匹配件数':'本轮新增件数');
    byId('signalTypesLabel').textContent=term?'匹配饰品种类':'新增饰品种类';
    byId('signalList').innerHTML=rows.slice(0,limit).map(grouping?accountRow:itemRow).join('');
    byId('signalUnits').textContent=num(compared.length ? units : null);
    byId('signalTypes').textContent=num(compared.length ? filtered.length : null);
    byId('signalOwners').textContent=num(compared.length ? owners : null);
    byId('signalCoverage').textContent=scope.length ? `${compared.length} / ${scope.length} 个账号已对比` : '';
    byId('signalStamp').textContent=updated?`更新于 ${stamp(updated)}`:'';
    byId('signalMore').hidden=rows.length<=limit;
    const unit=grouping?'个账号':'种';
    byId('signalMore').textContent=`再看 ${Math.min(batchSize,Math.max(0,rows.length-limit))} ${unit} · 共 ${rows.length} ${unit}`;
    byId('signalTools').hidden=!targets.length && !target;
    byId('signalClear').hidden=!(term || target);
    byId('signalManage').hidden=!!targets.length || data.scope!=='mine';
    byId('signalPrevious').hidden=!scope.length || !!observed.length || byId('date').value<=byId('date').min;
    if (target && !scope.length) notice('该账号不在当前监控范围。');
    else if (!scope.length) notice('还没有监控对象。');
    else if (!observed.length) notice(target?'该账号本轮暂无可用的扫描结果。':'所选轮次暂无可用的扫描结果。');
    else if (!compared.length) notice(target?'该账号本轮暂无可对比记录。':'本轮暂无可对比记录。');
    else if (!selected.length) notice(target?'该账号本轮没有新增交易保护物品。':'已对比的账号本轮没有新增交易保护物品。');
    else if (!filtered.length) notice('没有匹配的新增饰品。');
    else if (term || target) notice([target?targetLabels.get(target):'',term?`搜索「${term}」`:''].filter(Boolean).join(' · '));
    else notice();
  }
  async function refresh({recent=false,background=false}={}) {
    if(background && data)request?.abort();else clear();
    request = new AbortController();
    const {signal} = request, query = new URLSearchParams({date:recent?'recent':byId('date').value,scope:byId('scope').value,slot:byId('signalSlot').value});
    byId('protectedFeed').setAttribute('aria-busy','true');if(!background)notice('正在读取本轮记录…');
    try {
      const result=await read(`${byId('dashboard').dataset.protected}?${query}`, {signal});
      if (signal.aborted) return;
      if(recent){byId('date').value=result.date;sync();}
      const expanded=background?openKeys():null;
      // 在实际替换前取当前焦点，慢请求期间读者已选择的另一个控件优先。
      const focus=background&&data?.scope===result.scope?liveFocus():null;
      const saved=reading && readingRoute===location.pathname+location.search && reading.user===reader && reading.day===result.date && reading.slot===result.slot && reading.revision===result.revision ? reading : null;
      if(reading&&!saved)history.scrollRestoration='auto';
      reading=null;
      data=result;if(!background)limit=saved?saved.limit:batchSize;
      byId('signalRetry').hidden=true;
      const select=byId('signalTarget');
      select.replaceChildren(new Option('全部监控账号',''));
      const accounts=[...result.targets].sort((a,b)=>a.label.localeCompare(b.label,'zh-CN'));
      const sameAccounts=repeated(accounts.map(a=>a.label));
      targetLabels=new Map(accounts.map(a=>[String(a.target_id),sameAccounts.has(a.label)?`#${a.target_id} · ${a.label}`:a.label]));
      sameNames=repeated(result.items.map(item=>item.name));
      accounts.forEach(a=>select.add(new Option(accountLabel(a),String(a.target_id))));
      if (target && !accounts.some(a=>String(a.target_id)===target)) {
        const unavailable=new Option('不在当前范围的账号',target);
        unavailable.disabled=true;select.add(unavailable);
      }
      select.value=target;paint();
      if(expanded?.size)reopen(expanded);
      if(focus)restoreLiveFocus(focus);
      if(saved)restoreReading(saved);
      return true;
    } catch (error) {
      if (signal.aborted) return;
      notice(`新增记录读取失败：${error.message}`);byId('signalRetry').hidden=false;
    } finally {
      if (!signal.aborted) byId('protectedFeed').setAttribute('aria-busy','false');
    }
  }
  function syncSlot(){
    const value=byId('signalSlot').value;
    for(const button of byId('slotRail').children)button.classList.toggle('selected',button.dataset.slotChoice===value);
  }
  function init({get,syncUrl,query,pinDate,retry,userId,navigate}) {
    reader=userId;followInventory=navigate;byId('signalList').addEventListener('click',saveReading);
    read=get;sync=syncUrl;base=byId('dashboard').dataset.app;
    const slot=byId('signalSlot'), input=byId('signalSearch'), select=byId('signalTarget');
    view=query.get('group')==='accounts'?'accounts':'items';
    const toggles=byId('signalViews').querySelectorAll('[data-feed-view]');
    const markView=()=>toggles.forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.feedView===view)));
    markView();
    toggles.forEach(b=>b.onclick=()=>{if(b.dataset.feedView!==view){view=b.dataset.feedView;limit=batchSize;markView();sync();paint();}});
    slot.value=['07','14','21'].includes(query.get('slot'))?query.get('slot'):'latest';
    search=input.value=query.get('focus_item')||'';target=query.get('focus_account')||'';
    const rail=byId('slotRail');rail.replaceChildren();
    for(const option of slot.options){
      const button=document.createElement('button');button.type='button';button.tabIndex=-1;
      button.dataset.slotChoice=option.value;button.textContent=option.value==='latest'?'最新':option.textContent;
      button.onclick=()=>{
        if(slot.value!==option.value){slot.value=option.value;slot.dispatchEvent(new Event('change'));}
        slot.focus({preventScroll:true});
      };rail.append(button);
    }
    byId('slotControl').classList.add('enhanced');syncSlot();
    slot.onchange=()=>{pinDate();sync();refresh();};
    input.oninput=()=>{search=input.value;limit=batchSize;sync();paint();};
    select.onchange=()=>{target=select.value;limit=batchSize;sync();paint();};
    byId('signalClear').onclick=()=>{search=input.value='';target=select.value='';limit=batchSize;sync();paint();input.focus();};
    byId('signalMore').onclick=()=>{const previous=limit,expanded=openKeys();limit+=batchSize;paint();reopen(expanded);byId('signalList').children[previous]?.querySelector('summary').focus();};
    byId('signalPrevious').onclick=()=>{const date=byId('date');date.stepDown();date.dispatchEvent(new Event('change'));};
    byId('signalRetry').onclick=retry;
  }
  window.ProtectedFeed={init,syncSlot,refresh,clear,urlFields,get revision(){return data?.revision;}};
})();
