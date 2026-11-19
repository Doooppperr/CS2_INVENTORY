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
    const {summary:s,items}=data;
    const needle = search.trim().toLocaleLowerCase();
    const filtered = items.filter(i=>i.name.toLocaleLowerCase().includes(needle)).map(i=>{
      if (!target) return i;
      const accounts=i.accounts.filter(a=>String(a.target_id)===target);
      return {...i,accounts,count:accounts.reduce((sum,a)=>sum+a.count,0)};
    }).filter(i=>i.accounts.length);
    if (target) filtered.sort((a,b)=>b.count-a.count || a.name.localeCompare(b.name,'zh-CN'));
    byId('signalList').innerHTML=filtered.slice(0,limit).map(row).join('');
    byId('signalUnits').textContent=num(s.added_units);
    byId('signalTypes').textContent=num(s.added_units==null?null:s.item_types);
    byId('signalOwners').textContent=num(s.added_units==null?null:s.changed_accounts);
    byId('signalCoverage').textContent=`${s.compared_accounts} / ${s.total_accounts} 个账号已对比`;
    byId('signalStamp').textContent=data.updated_at?`更新于 ${stamp(data.updated_at)}`:'';
    byId('signalMore').hidden=filtered.length<=limit;
    byId('signalMore').textContent=`再看 ${Math.min(batchSize,Math.max(0,filtered.length-limit))} 种 · 共 ${filtered.length} 种`;
    byId('signalTools').hidden=!items.length;
    byId('signalClear').hidden=!(items.length && (search || target));
    byId('signalManage').hidden=!!s.total_accounts || data.scope!=='mine';
    byId('signalPrevious').hidden=!s.total_accounts || !!s.observed_accounts || byId('date').value<=byId('date').min;
    if (!s.total_accounts) notice('还没有监控对象。');
    else if (!s.observed_accounts) notice('所选轮次暂无可用的扫描结果。');
    else if (!s.compared_accounts) notice('本轮暂无可对比记录。');
    else if (!items.length) notice('已对比的账号本轮没有新增交易保护物品。');
    else if (!filtered.length) notice('没有匹配的新增饰品。');
    else if (search || target) notice(`筛选结果：${filtered.length} 种 · ${num(filtered.reduce((n,i)=>n+i.count,0))} 件`);
    else notice();
  }
  async function refresh() {
    clear(); request = new AbortController();
    const {signal} = request, query = new URLSearchParams({date:byId('date').value,scope:byId('scope').value,slot:byId('signalSlot').value});
    byId('protectedFeed').setAttribute('aria-busy','true');notice('正在读取本轮记录…');
    try {
      const result=await read(`${byId('dashboard').dataset.protected}?${query}`, {signal});
      if (signal.aborted) return;
      data=result; limit=batchSize;
      const accounts = new Map(result.items.flatMap(i=>i.accounts.map(a=>[String(a.target_id),a.label])));
      const select=byId('signalTarget');
      select.replaceChildren(new Option('全部加仓账号',''));
      [...accounts].sort((a,b)=>a[1].localeCompare(b[1],'zh-CN')).forEach(([id,label])=>select.add(new Option(label,id)));
      if (target && !accounts.has(target)) {target='';sync();}
      select.value=target;paint();
    } catch (error) {
      if (signal.aborted) return;
      notice(`新增记录读取失败：${error.message}`);byId('signalRetry').hidden=false;
    } finally {
      if (!signal.aborted) byId('protectedFeed').setAttribute('aria-busy','false');
    }
  }
  function init({get,syncUrl,query}) {
    read=get;sync=syncUrl;base=byId('dashboard').dataset.app;
    const slot=byId('signalSlot'), input=byId('signalSearch'), select=byId('signalTarget');
    slot.value=['07','14','21'].includes(query.get('slot'))?query.get('slot'):'latest';
    search=input.value=query.get('focus_item')||'';target=query.get('focus_account')||'';
    slot.onchange=()=>{sync();refresh();};
    input.oninput=()=>{search=input.value;limit=batchSize;sync();paint();};
    select.onchange=()=>{target=select.value;limit=batchSize;sync();paint();};
    byId('signalClear').onclick=()=>{search=input.value='';target=select.value='';limit=batchSize;sync();paint();input.focus();};
    byId('signalMore').onclick=()=>{const previous=limit;limit+=batchSize;paint();byId('signalList').children[previous]?.querySelector('summary').focus();};
    byId('signalPrevious').onclick=()=>{const date=byId('date');date.stepDown();date.dispatchEvent(new Event('change'));};
    byId('signalRetry').onclick=refresh;
  }
  window.ProtectedFeed={init,refresh,clear,urlFields};
})();
