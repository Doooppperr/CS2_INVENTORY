/* 轮次改变才取新记录；搜索、按账号筛选和展开明细都在当前页完成。 */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = value => value == null ? '—' : value.toLocaleString('zh-CN');
  const stamp = value => new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value));
  const batchSize = 6;
  let data, request, search, target, limit, base, read, sync;
  function urlFields(params) {
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
  function row(item) {
    const labels = item.accounts.slice(0,2).map(a=>a.label).join('、') + (item.accounts.length>2?` 等 ${item.accounts.length} 个账号`:'');
    const branches = item.accounts.map(a=>`<li><div><strong>${esc(a.label)}</strong><small>${esc(stamp(a.baseline_at))} → ${esc(stamp(a.scanned_at))}</small></div><b>+${num(a.count)}</b><a href="${esc(monitorLink(a,item.name))}">查看当前库存 →</a></li>`).join('');
    return `<details class="signal-item"><summary><span class="signal-name"><b>${esc(item.name)}</b><small>${esc(labels)}</small></span><strong class="signal-count">+${num(item.count)} <small>件</small></strong><span class="signal-chevron" aria-hidden="true">⌄</span></summary><ul>${branches}</ul></details>`;
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
    byId('signalList').innerHTML=filtered.slice(0,limit).map(row).join('');
    byId('signalUnits').textContent=num(compared.length ? units : null);
    byId('signalTypes').textContent=num(compared.length ? filtered.length : null);
    byId('signalOwners').textContent=num(compared.length ? owners : null);
    byId('signalCoverage').textContent=scope.length ? `${compared.length} / ${scope.length} 个账号已对比` : '';
    byId('signalStamp').textContent=updated?`更新于 ${stamp(updated)}`:'';
    byId('signalMore').hidden=filtered.length<=limit;
    byId('signalMore').textContent=`再看 ${Math.min(batchSize,Math.max(0,filtered.length-limit))} 种 · 共 ${filtered.length} 种`;
    byId('signalTools').hidden=!targets.length && !target;
    byId('signalClear').hidden=!(search || target);
    byId('signalManage').hidden=!!targets.length || data.scope!=='mine';
    byId('signalPrevious').hidden=!scope.length || !!observed.length || byId('date').value<=byId('date').min;
    if (target && !scope.length) notice('该账号不在当前监控范围。');
    else if (!scope.length) notice('还没有监控对象。');
    else if (!observed.length) notice(target?'该账号本轮暂无可用的扫描结果。':'所选轮次暂无可用的扫描结果。');
    else if (!compared.length) notice(target?'该账号本轮暂无可对比记录。':'本轮暂无可对比记录。');
    else if (!selected.length) notice(target?'该账号本轮没有新增交易保护物品。':'已对比的账号本轮没有新增交易保护物品。');
    else if (!filtered.length) notice('没有匹配的新增饰品。');
    else if (search.trim() || target) notice(`筛选结果：${filtered.length} 种 · ${num(units)} 件`);
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
      const expanded=background ? new Set([...byId('signalList').querySelectorAll('details[open] .signal-name b')].map(el=>el.textContent)) : null;
      data=result;if(!background)limit=batchSize;
      byId('signalRetry').hidden=true;
      const select=byId('signalTarget');
      select.replaceChildren(new Option('全部监控账号',''));
      const accounts=[...result.targets].sort((a,b)=>a.label.localeCompare(b.label,'zh-CN'));
      accounts.forEach(a=>select.add(new Option(a.label,String(a.target_id))));
      if (target && !accounts.some(a=>String(a.target_id)===target)) {
        const unavailable=new Option('不在当前范围的账号',target);
        unavailable.disabled=true;select.add(unavailable);
      }
      select.value=target;paint();
      if(expanded?.size)for(const d of byId('signalList').querySelectorAll('details'))d.open=expanded.has(d.querySelector('.signal-name b').textContent);
      return true;
    } catch (error) {
      if (signal.aborted) return;
      notice(`新增记录读取失败：${error.message}`);byId('signalRetry').hidden=false;
    } finally {
      if (!signal.aborted) byId('protectedFeed').setAttribute('aria-busy','false');
    }
  }
  function init({get,syncUrl,query,pinDate,retry}) {
    read=get;sync=syncUrl;base=byId('dashboard').dataset.app;
    const slot=byId('signalSlot'), input=byId('signalSearch'), select=byId('signalTarget');
    slot.value=['07','14','21'].includes(query.get('slot'))?query.get('slot'):'latest';
    search=input.value=query.get('focus_item')||'';target=query.get('focus_account')||'';
    slot.onchange=()=>{pinDate();sync();refresh();};
    input.oninput=()=>{search=input.value;limit=batchSize;sync();paint();};
    select.onchange=()=>{target=select.value;limit=batchSize;sync();paint();};
    byId('signalClear').onclick=()=>{search=input.value='';target=select.value='';limit=batchSize;sync();paint();input.focus();};
    byId('signalMore').onclick=()=>{const previous=limit;limit+=batchSize;paint();byId('signalList').children[previous]?.querySelector('summary').focus();};
    byId('signalPrevious').onclick=()=>{const date=byId('date');date.stepDown();date.dispatchEvent(new Event('change'));};
    byId('signalRetry').onclick=retry;
  }
  window.ProtectedFeed={init,refresh,clear,urlFields,get revision(){return data?.revision;}};
})();
