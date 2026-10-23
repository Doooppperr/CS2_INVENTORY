/* 库存数据仅保存在当前页面内存；主题沿用监控页的非敏感偏好。 */
(() => {
  'use strict';
  const $ = id => document.getElementById(id), root = $('dashboard');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => value ? new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false}) : '—';
  const monitorUrl = id => `${root.dataset.app.replace(/\/$/,'')}/monitors/${encodeURIComponent(id)}`;
  let page=1, pages=1, generation=0, loading=false, diagnostics=false, overviewCache=null;
  const stateNames={ready:'校验通过',partial:'待核验',private:'已私密',failed:'扫描失败',pending:'待扫描 / 扫描中',missing:'没有早间记录'};
  const clear = (keepOverview=false) => {
    for(const id of ['rows','stats','qualityReasons','accountStatuses'])$(id).replaceChildren();
    if(!keepOverview){window.InventoryOverview.clear();overviewCache=null;}$('quality').textContent='';$('note').textContent='';$('page').textContent='—';
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
    const disabled=$('section').value!=='signals';
    $('signalFilters').disabled=disabled;$('direction').disabled=disabled;
    $('advancedFilters').classList.toggle('hidden',disabled);
    updateFilterCount();
    document.querySelectorAll('[data-section]').forEach(button=>{
      const active=button.dataset.section===$('section').value;
      button.classList.toggle('active',active);button.classList.toggle('ghost',!active);button.setAttribute('aria-pressed',String(active));
    });
  }
  function switchSection(section){$('section').value=section;syncSection();page=1;load();}
  async function load(){
    const serial=++generation,values=parameters(),overviewKey=['date','scope','days'].map(name=>values.get(name)).join('|');
    const sameOverview=overviewCache?.key===overviewKey;loading=true;
    $('error').textContent='';$('apply').disabled=true;$('prev').disabled=true;$('next').disabled=true;
    clear(!!sameOverview);$('title').textContent='正在读取日报…';$('reportStatus').textContent='正在读取';root.setAttribute('aria-busy','true');
    try{
      const [data,overview]=await Promise.all([get(`${root.dataset.api}?${values}`),get(`${root.dataset.overview}?${values}`)]);
      if(serial!==generation)return;
      if(page>data.pages){page=data.pages;await load();return;}
      history.replaceState(null,'',`?${values}`);
      const fingerprint=JSON.stringify(overview);
      if(!sameOverview||overviewCache?.fingerprint!==fingerprint)window.InventoryOverview.render(overview);
      overviewCache={key:overviewKey,fingerprint};
      const s=data.summary;
      $('stats').innerHTML=[['已产出记录',`${s.observed} / ${s.targets}`],['有效对比账号',s.comparable],['已私密',s.private],['异常 / 不完整',`${s.failed} / ${s.partial}`],['待扫描 / 缺失',`${s.pending} / ${s.missing}`]].map(([label,value])=>`<div class="stat"><span>${label}</span><b>${esc(value)}</b></div>`).join('');
      if(!diagnostics)$('stats').innerHTML=[['监控账号',s.targets],['已有记录',s.observed]].map(([label,value])=>`<div class="stat"><span>${label}</span><b>${value??'—'}</b></div>`).join('');
      const names={signals:'有效库存变动',reappeared:'再次出现',pending:'待核实观察'};
      const statuses={generating:'生成中',complete:'对比数据完整',partial:'部分账号可对比',no_comparable:'暂无完整对比'};
      $('reportStatus').textContent=statuses[data.status];$('reportStatus').className=`status ${data.status==='complete'?'ready':''}`;
      $('title').textContent=`${data.date} · ${names[data.section]}（${data.total} 项）`;
      $('quality').textContent=`对比区间 ${data.baseline_date} → ${data.date}，每个账号以实际采集时间为准。${s.baseline_only} 个账号仅建立基线。${s.comparable===0?'暂无可用完整对比，不代表没有库存变化。':''}`;
      $('qualityReasons').innerHTML=(data.quality_summary||[]).map(r=>`<span class="quality-chip">${esc(r.message)} · ${r.accounts} 个账号</span>`).join('');
      $('accountStatuses').innerHTML=(data.account_statuses||[]).map(a=>`<article class="account-quality"><a href="${esc(monitorUrl(a.target_id))}">${esc(a.label)}</a><span class="status ${esc(a.status)}">${esc(stateNames[a.status]||a.status)}</span><p class="meta">${esc(a.reasons.join('；')||(a.comparable?'前后两日校验通过，可参与正式排行':'仅建立基线'))}</p><p class="meta">本次 ${fmt(a.scanned_at)} · 基线 ${fmt(a.baseline_at)}</p></article>`).join('')||'<p class="meta">当前范围内没有监控账号。</p>';
      $('countSignals').textContent=data.section_counts.signals;$('countReappeared').textContent=data.section_counts.reappeared;$('countPending').textContent=data.section_counts.pending;
      $('rows').innerHTML=data.items.length?window.InventoryDetails.render(data.items,data.section,root.dataset.app):`<div class="empty-state">${data.status==='no_comparable'&&data.section==='signals'?'暂无完整对比':'没有符合条件的变化'}</div>`;
      if($('showPending'))$('showPending').onclick=()=>switchSection('pending');
      if(!diagnostics){$('reportStatus').textContent='';$('quality').textContent='';$('qualityReasons').replaceChildren();$('accountStatuses').replaceChildren();$('note').textContent='';if(!data.items.length)$('rows').innerHTML='<div class="empty-state">暂无可展示的变化</div>';}else{$('note').textContent=data.note||'';}pages=data.pages;$('page').textContent=`${page} / ${pages}`;
    }catch(error){if(serial===generation){clear();pages=1;$('error').textContent=error.message;$('title').textContent='日报读取失败';$('reportStatus').textContent='读取失败';}}
    finally{if(serial===generation){loading=false;root.setAttribute('aria-busy','false');$('apply').disabled=false;$('prev').disabled=page<=1;$('next').disabled=page>=pages;}}
  }
  let searchTimer;
  function updateFilterCount(){const form=$('filters'),count=['peers','single'].filter(name=>form.elements.namedItem(name).checked).length;$('filterCount').textContent=count;$('filterCount').classList.toggle('hidden',!count);}
  function applyFilters(){clearTimeout(searchTimer);if(!$('filters').reportValidity())return;page=1;syncSection();load();}
  $('filters').addEventListener('submit',event=>{event.preventDefault();if(!$('filters').reportValidity())return;$('advancedFilters').open=false;applyFilters();});
  $('filters').elements.namedItem('q').addEventListener('input',event=>{if(event.isComposing)return;clearTimeout(searchTimer);searchTimer=setTimeout(applyFilters,300);});
  $('filters').elements.namedItem('q').addEventListener('compositionend',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(applyFilters,300);});
  for(const name of ['sort','direction'])$('filters').elements.namedItem(name).addEventListener('change',applyFilters);
  $('signalFilters').addEventListener('change',updateFilterCount);
  document.addEventListener('click',event=>{if(!$('advancedFilters').contains(event.target))$('advancedFilters').open=false;});
  $('advancedFilters').addEventListener('keydown',event=>{if(event.key==='Escape'){$('advancedFilters').open=false;$('advancedFilters').querySelector('summary').focus();}});
  $('prev').onclick=()=>{if(!loading&&page>1){page--;load();}};$('next').onclick=()=>{if(!loading&&page<pages){page++;load();}};
  $('section').onchange=()=>switchSection($('section').value);
  $('date').onchange=()=>{if($('date').checkValidity()){page=1;load();}};
  $('scope').onchange=()=>{page=1;load();};
  $('overviewDays').onchange=()=>{page=1;load();};
  document.querySelectorAll('[data-section]').forEach(button=>button.onclick=()=>switchSection(button.dataset.section));
  $('resetFilters').onclick=()=>{const date=$('date').value,scope=$('scope').value,days=$('overviewDays').value;clearTimeout(searchTimer);$('filters').reset();$('advancedFilters').open=false;$('date').value=date;$('scope').value=scope;$('overviewDays').value=days;syncSection();page=1;load();};
  addEventListener('pagehide',()=>{clearTimeout(searchTimer);generation++;clear();$('viewer').textContent='';});
  addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
  async function boot(){
    try{
      const auth=await get(root.dataset.bootstrap);
      if(!auth.user){location.replace(`${root.dataset.home}?auth=login&next=${encodeURIComponent(location.pathname+location.search)}`);return;}
      diagnostics=!!auth.user.can_view_inventory_diagnostics;document.querySelector('.quality-panel').classList.toggle('hidden',!diagnostics);document.querySelector('[data-section="pending"]').classList.toggle('hidden',!diagnostics);document.querySelector('#section option[value="pending"]').disabled=!diagnostics;document.querySelector('#section option[value="pending"]').hidden=!diagnostics;if(!diagnostics)document.querySelector('#section option[value="pending"]').remove();
      $('viewer').textContent=auth.user.username;
      $('scopeLabel').classList.toggle('hidden',!diagnostics);
      $('scope').value=diagnostics?'all':'mine';
      const today=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'}),min=new Date(`${today}T00:00:00+08:00`);min.setUTCDate(min.getUTCDate()-7);
      $('date').value=today;$('date').max=today;$('date').min=min.toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});
      const query=new URLSearchParams(location.search);
      for(const element of $('filters').elements){if(!element.name||!query.has(element.name))continue;if(element.type==='checkbox')element.checked=query.get(element.name)==='1';else element.value=query.get(element.name);}
      if(!diagnostics&&!['signals','reappeared'].includes($('section').value))$('section').value='signals';if(!diagnostics)$('scope').value='mine';syncSection();page=Math.max(1,parseInt(query.get('page'),10)||1);await load();
    }catch(error){clear();$('error').textContent=error.message;}
  }
  boot();
})();
