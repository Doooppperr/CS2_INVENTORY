/* 库存数据仅保存在当前页面内存；主题沿用监控页的非敏感偏好。 */
(() => {
  'use strict';
  const $ = id => document.getElementById(id), root = $('dashboard');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => value ? new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false}) : '—';
  const signed = value => `${value>0?'+':''}${value}`;
  const monitorUrl = id => `${root.dataset.app.replace(/\/$/,'')}/monitors/${encodeURIComponent(id)}`;
  let page=1, pages=1, generation=0, loading=false;
  const stateNames={ready:'校验通过',partial:'待核验',private:'已私密',failed:'扫描失败',pending:'待扫描 / 扫描中',missing:'没有早间记录'};
  const clear = () => {
    for(const id of ['rows','stats','qualityReasons','accountStatuses'])$(id).replaceChildren();
    $('quality').textContent='';$('note').textContent='';$('page').textContent='—';
    for(const id of ['countSignals','countReappeared','countPending'])$(id).textContent='—';
  };
  async function get(url) {
    const response=await fetch(url,{cache:'no-store',credentials:'same-origin'});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error||`请求失败 ${response.status}`);
    return data;
  }
  function parameters(){const values=new URLSearchParams(new FormData($('filters')));values.set('page',String(page));return values;}
  function syncSection(){
    $('signalFilters').disabled=$('section').value!=='signals';
    document.querySelectorAll('[data-section]').forEach(button=>{
      const active=button.dataset.section===$('section').value;
      button.classList.toggle('active',active);button.classList.toggle('ghost',!active);button.setAttribute('aria-pressed',String(active));
    });
  }
  function switchSection(section){$('section').value=section;syncSection();page=1;load();}
  function accountDetail(a){return `<div class="account-detail"><a href="${esc(monitorUrl(a.target_id))}">${esc(a.label)}</a><p>${a.before} → ${a.after} 件 · 首次发现 ${a.first_seen} · 再次出现 ${a.reappeared}</p>${!a.verified?`<p class="muted">未纳入排行：${esc(a.reason)}</p>`:''}<p class="muted">基线 ${fmt(a.baseline_at)}<br>本次 ${fmt(a.scanned_at)}</p></div>`;}
  async function load(){
    const serial=++generation;loading=true;
    $('error').textContent='';$('apply').disabled=true;$('prev').disabled=true;$('next').disabled=true;
    clear();$('title').textContent='正在读取日报…';$('reportStatus').textContent='正在读取';root.setAttribute('aria-busy','true');
    try{
      const values=parameters(),data=await get(`${root.dataset.api}?${values}`);
      if(serial!==generation)return;
      if(page>data.pages){page=data.pages;await load();return;}
      history.replaceState(null,'',`?${values}`);
      const s=data.summary;
      $('stats').innerHTML=[['已产出记录',`${s.observed} / ${s.targets}`],['有效对比账号',s.comparable],['已私密',s.private],['异常 / 不完整',`${s.failed} / ${s.partial}`],['待扫描 / 缺失',`${s.pending} / ${s.missing}`]].map(([label,value])=>`<div class="stat"><span>${label}</span><b>${esc(value)}</b></div>`).join('');
      const names={signals:'有效库存变动',reappeared:'再次出现',pending:'待核实观察'};
      const statuses={generating:'生成中',complete:'对比数据完整',partial:'部分账号可对比',no_comparable:'暂无完整对比'};
      $('reportStatus').textContent=statuses[data.status];$('reportStatus').className=`status ${data.status==='complete'?'ready':''}`;
      $('title').textContent=`${data.date} · ${names[data.section]}（${data.total} 项）`;
      $('quality').textContent=`对比区间 ${data.baseline_date} → ${data.date}，每个账号以实际采集时间为准。${s.baseline_only} 个账号仅建立基线。${s.comparable===0?'暂无可用完整对比，不代表没有库存变化。':''}`;
      $('qualityReasons').innerHTML=data.quality_summary.map(r=>`<span class="quality-chip">${esc(r.message)} · ${r.accounts} 个账号</span>`).join('');
      $('accountStatuses').innerHTML=data.account_statuses.map(a=>`<article class="account-quality"><a href="${esc(monitorUrl(a.target_id))}">${esc(a.label)}</a><span class="status ${esc(a.status)}">${esc(stateNames[a.status]||a.status)}</span><p class="meta">${esc(a.reasons.join('；')||(a.comparable?'前后两日校验通过，可参与正式排行':'仅建立基线'))}</p><p class="meta">本次 ${fmt(a.scanned_at)} · 基线 ${fmt(a.baseline_at)}</p></article>`).join('')||'<p class="meta">当前范围内没有监控账号。</p>';
      $('countSignals').textContent=data.section_counts.signals;$('countReappeared').textContent=data.section_counts.reappeared;$('countPending').textContent=data.section_counts.pending;
      const header=data.section==='signals'?'增加账号数':'涉及账号数';
      $('rows').innerHTML=data.items.length?`<table><thead><tr><th>饰品 / 展开账号明细</th><th>${header}</th><th>有效增加</th><th>有效减少</th><th>观测净变化</th><th>单账号最大增加</th></tr></thead><tbody>${data.items.map(item=>`<tr><td><details><summary>${esc(item.name)}</summary>${item.accounts.map(accountDetail).join('')}</details></td><td>${data.section==='signals'?item.account_count:item.accounts.length}</td><td class="positive">${data.section==='pending'?'—':`+${item.increase}`}</td><td class="negative">${data.section==='pending'?'—':`−${item.decrease}`}</td><td>${signed(item.net)}${data.section==='pending'?'（待核实）':''}</td><td>${data.section==='pending'?'—':item.max_increase}</td></tr>`).join('')}</tbody></table>`:`<div class="empty-state"><b>${data.status==='no_comparable'&&data.section==='signals'?'尚未形成可信的每日对比':'没有符合当前条件的记录'}</b><p>${data.status==='no_comparable'?'先查看上方具体原因。需要相邻两天的早间扫描均通过校验，才会生成正式排行；轻量刷新不替代早间基线。':'可调整日期、搜索内容或筛选门槛。'}</p>${data.section!=='pending'&&data.section_counts.pending?'<button type="button" id="showPending" class="ghost">查看待核实观察 →</button>':''}</div>`;
      if($('showPending'))$('showPending').onclick=()=>switchSection('pending');
      $('note').textContent=data.note;pages=data.pages;$('page').textContent=`${page} / ${pages}`;
    }catch(error){if(serial===generation){clear();pages=1;$('error').textContent=error.message;$('title').textContent='日报读取失败';$('reportStatus').textContent='读取失败';}}
    finally{if(serial===generation){loading=false;root.setAttribute('aria-busy','false');$('apply').disabled=false;$('prev').disabled=page<=1;$('next').disabled=page>=pages;}}
  }
  $('filters').addEventListener('submit',event=>{event.preventDefault();page=1;syncSection();load();});
  $('prev').onclick=()=>{if(!loading&&page>1){page--;load();}};$('next').onclick=()=>{if(!loading&&page<pages){page++;load();}};
  $('section').onchange=()=>switchSection($('section').value);
  $('date').onchange=()=>{if($('date').checkValidity()){page=1;load();}};
  $('scope').onchange=()=>{page=1;load();};
  document.querySelectorAll('[data-section]').forEach(button=>button.onclick=()=>switchSection(button.dataset.section));
  $('resetFilters').onclick=()=>{const date=$('date').value,scope=$('scope').value;$('filters').reset();$('date').value=date;$('scope').value=scope;syncSection();page=1;load();};
  addEventListener('pagehide',()=>{generation++;clear();$('viewer').textContent='';});
  addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
  async function boot(){
    try{
      const auth=await get(root.dataset.bootstrap);
      if(!auth.user){location.replace(`${root.dataset.home}?auth=login&next=${encodeURIComponent(location.pathname+location.search)}`);return;}
      $('viewer').textContent=`${auth.user.username} · 仅展示当前账号有权查看的监控`;
      $('scopeLabel').classList.toggle('hidden',auth.user.role!=='admin');
      const today=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'}),min=new Date(`${today}T00:00:00+08:00`);min.setUTCDate(min.getUTCDate()-7);
      $('date').value=today;$('date').max=today;$('date').min=min.toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});
      const query=new URLSearchParams(location.search);
      for(const element of $('filters').elements){if(!element.name||!query.has(element.name))continue;if(element.type==='checkbox')element.checked=query.get(element.name)==='1';else element.value=query.get(element.name);}
      if(auth.user.role!=='admin')$('scope').value='mine';syncSection();page=Math.max(1,parseInt(query.get('page'),10)||1);await load();
    }catch(error){clear();$('error').textContent=error.message;}
  }
  boot();
})();
