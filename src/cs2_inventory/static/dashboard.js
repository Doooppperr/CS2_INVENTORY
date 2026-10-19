/* 每次请求做服务端鉴权，不在 localStorage 保存账号或库存数据。 */
(() => {
  'use strict';
  const $ = id => document.getElementById(id), root = $('dashboard');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => value ? new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false}) : '—';
  let page = 1, pages = 1, generation = 0;
  const clear = () => { $('rows').replaceChildren(); $('stats').replaceChildren(); $('quality').textContent=''; $('note').textContent=''; };
  async function get(url) {
    const response = await fetch(url, {cache:'no-store', credentials:'same-origin'});
    const data = await response.json();
    if (!response.ok) { clear(); throw new Error(data.error || `请求失败 ${response.status}`); }
    return data;
  }
  function parameters() {
    const values = new URLSearchParams(new FormData($('filters')));
    values.set('page', String(page));
    return values;
  }
  async function load() {
    const serial = ++generation;
    $('error').textContent=''; $('apply').disabled=true; $('prev').disabled=true; $('next').disabled=true;
    clear(); $('title').textContent='正在读取日报…';
    try {
      const values=parameters(), data=await get(`${root.dataset.api}?${values}`);
      if (serial !== generation) return;
      history.replaceState(null, '', `?${values}`);
      const s=data.summary;
      $('stats').innerHTML=[['可对比账号',`${s.comparable} / ${s.targets}`],['已私密',s.private],['异常 / 不完整',`${s.failed} / ${s.partial}`],['待扫描 / 缺失',`${s.pending} / ${s.missing}`],['仅建立基线',s.baseline_only]].map(([label,value])=>`<div class="stat"><span class="muted">${label}</span><b>${esc(value)}</b></div>`).join('');
      const names={signals:'有效库存变动',reappeared:'再次出现',pending:'待核实观察'};
      const statuses={generating:'生成中',complete:'对比数据完整',partial:'部分账号可对比',no_comparable:'暂无可用完整对比'};
      $('title').textContent=`${data.date} · ${names[data.section]}（${data.total} 项）`;
      $('quality').textContent=`${statuses[data.status]}。基线日期 ${data.baseline_date}。有效变动 ${data.section_counts.signals} 项，再次出现 ${data.section_counts.reappeared} 项，待核实 ${data.section_counts.pending} 项。`;
      const header=data.section==='signals'?'增加账号数':'涉及账号数';
      $('rows').innerHTML=data.items.length ? `<table><thead><tr><th>饰品 / 展开账号明细</th><th>${header}</th><th>有效增加</th><th>有效减少</th><th>观测净变化</th><th>单账号最大增加</th></tr></thead><tbody>${data.items.map(item=>`<tr><td><details><summary>${esc(item.name)}</summary>${item.accounts.map(a=>`<p><a href="${esc(root.dataset.app.replace(/\/$/,''))}/monitors/${a.target_id}">${esc(a.label)}</a><br>${a.before} → ${a.after} 件 · 首次发现 ${a.first_seen} · 再次出现 ${a.reappeared}${!a.verified?`<br><span class="muted">${esc(a.reason)}</span>`:''}<br><span class="muted">${fmt(a.baseline_at)} → ${fmt(a.scanned_at)}</span></p>`).join('')}</details></td><td>${data.section==='signals'?item.account_count:item.accounts.length}</td><td class="positive">+${item.increase}</td><td class="negative">−${item.decrease}</td><td>${item.net>0?'+':''}${item.net}</td><td>${item.max_increase}</td></tr>`).join('')}</tbody></table>` : `<p class="muted">${data.status==='no_comparable'&&data.section==='signals'?'当前没有可用的完整对比，不代表零变化。可切换到“待核实观察”。':'没有符合当前条件的记录。'}</p>`;
      $('note').textContent=data.note;
      pages=data.pages; $('page').textContent=`${page} / ${pages}`;
    } catch(error) { if(serial===generation){clear();$('error').textContent=error.message;$('title').textContent='日报读取失败';} }
    finally {if(serial===generation){$('apply').disabled=false;$('prev').disabled=page<=1;$('next').disabled=page>=pages;}}
  }
  $('filters').addEventListener('submit', event=>{event.preventDefault();page=1;load();});
  $('prev').onclick=()=>{if(page>1){page--;load();}};
  $('next').onclick=()=>{if(page<pages){page++;load();}};
  $('section').onchange=()=>{$('signalFilters').disabled=$('section').value!=='signals';page=1;load();};
  // 离开或从浏览器历史恢复时清除旧账号数据并重新鉴权。
  addEventListener('pagehide',()=>{generation++;clear();});
  addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
  async function boot(){
    try {
      const auth=await get(root.dataset.bootstrap);
      if(!auth.user){location.replace(`${root.dataset.home}?auth=login&next=${encodeURIComponent(location.pathname+location.search)}`);return;}
      $('viewer').textContent=`${auth.user.username} · 仅展示当前账号有权查看的监控`;
      $('scopeLabel').classList.toggle('hidden',auth.user.role!=='admin');
      const today=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});
      const min=new Date(`${today}T00:00:00+08:00`);min.setUTCDate(min.getUTCDate()-7);
      $('date').value=today;$('date').max=today;$('date').min=min.toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});
      const query=new URLSearchParams(location.search);
      for(const element of $('filters').elements){if(!element.name||!query.has(element.name))continue;if(element.type==='checkbox')element.checked=query.get(element.name)==='1';else element.value=query.get(element.name);}
      if(auth.user.role!=='admin')$('scope').value='mine';
      $('signalFilters').disabled=$('section').value!=='signals';
      page=Math.max(1,parseInt(query.get('page'),10)||1);await load();
    } catch(error){clear();$('error').textContent=error.message;}
  }
  boot();
})();
