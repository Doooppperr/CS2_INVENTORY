(() => {
 'use strict';
 function bind({params,read,sync,esc,fmt,active,currentCode,clearCode}){
  const $=id=>document.getElementById(id),prefix=$('codeSearch'),filter=$('codeStatusFilter'),info=$('codeListStatus'),out=$('activationCodes'),action=$('codeActionStatus');
  const plans={monthly:'月度',annual:'年度',permanent:'永久'},states={unused:'待使用',redeemed:'已兑换',revoked:'已撤销'};
  let page=Math.max(1,parseInt(params.get('code_page'),10)||1),pages=1,seq=0,timer;
  let term=(params.get('code_q')||'').trim().toUpperCase().slice(0,13),status=Object.hasOwn(states,params.get('code_status'))?params.get('code_status'):'all';
  prefix.value=term;filter.value=status;
  function urlFields(q){
   if(page>1)q.set('code_page',String(page));
   if(term)q.set('code_q',term);
   if(status!=='all')q.set('code_status',status);
  }
  function updateDraft(){
   seq++;out.replaceChildren();out.setAttribute('aria-busy','true');
   $('codePrev').disabled=$('codeNext').disabled=true;$('codeRetry').hidden=true;
   $('codeClear').hidden=!prefix.value&&filter.value==='all';info.textContent='正在读取续期码…';
  }
  function apply(){
   clearTimeout(timer);term=prefix.value.trim().toUpperCase().slice(0,13);prefix.value=term;status=filter.value;page=1;action.textContent='';
   sync();load();
  }
  async function load(){
   clearTimeout(timer);
   if(document.activeElement===$('codePrev')||document.activeElement===$('codeNext'))info.focus();
   updateDraft();
   const generation=seq,live=()=>generation===seq&&active(),q=new URLSearchParams({page:String(page)});
   if(term)q.set('q',term);if(status!=='all')q.set('status',status);
   try{
    const result=await read('api/admin/activation-codes?'+q);
    if(!live())return;
    page=result.page;pages=Math.max(1,result.pages);sync();
    $('codePager').hidden=pages<=1;$('codePage').textContent=`${page} / ${pages}`;
    $('codePrev').disabled=page<=1;$('codeNext').disabled=page>=pages;
    info.textContent=result.total?`共 ${result.total} 个续期码`:(term||status!=='all'?'没有匹配的续期码':'暂无续期码');
    out.innerHTML=result.items.length?`<table class="data-table"><thead><tr><th>前缀</th><th>套餐</th><th>监控限额</th><th>状态</th><th>记录</th><th>操作</th></tr></thead><tbody>${result.items.map(x=>{
     const changed=x.status==='redeemed'?'兑换于':x.status==='revoked'?'撤销于':'创建于';
     const at=x.status==='redeemed'?x.redeemed_at:x.status==='revoked'?x.revoked_at:x.created_at;
     return `<tr><td><b>${esc(x.prefix)}…</b></td><td>${esc(plans[x.plan])}</td><td>${x.monitor_limit} 个</td><td><span class="code-status ${esc(x.status)}">${esc(states[x.status])}</span></td><td>${esc(changed)} ${esc(fmt(at))}${x.redeemed_by_username?`<br>兑换账号：${esc(x.redeemed_by_username)}`:''}</td><td>${x.status==='unused'?`<button type="button" class="ghost code-revoke" data-id="${x.id}" aria-label="撤销 ${esc(x.prefix)}">撤销</button>`:'—'}</td></tr>`;
    }).join('')}</tbody></table>`:'';
    if(result.items.length)AdminWorkspace.labelRows(out);
    const visible=new Map(result.items.map(x=>[String(x.id),x]));
    for(const b of out.querySelectorAll('.code-revoke'))b.onclick=async()=>{
     const item=visible.get(b.dataset.id),label=item.prefix+'…';
     if(!confirm(`撤销续期码 ${label}（${plans[item.plan]} · 限额 ${item.monitor_limit} 个）？撤销后该码不能再兑换。`))return;
     b.disabled=true;b.textContent='撤销中';
     try{
      await read('api/admin/activation-codes/'+item.id,{method:'DELETE'});
      if(currentCode()===String(item.id))clearCode();
      if(!active())return;
      action.textContent='已撤销 '+label;
      await load();
      if(active())action.focus();
     }catch(e){if(live()){action.textContent=e.message;b.disabled=false;b.textContent='撤销';}}
    };
   }catch(e){
    if(!live())return;
    $('codePager').hidden=true;info.textContent='续期码记录读取失败';$('codeRetry').hidden=false;
   }finally{if(live())out.setAttribute('aria-busy','false');}
  }
  prefix.oninput=e=>{clearTimeout(timer);updateDraft();if(!e.isComposing)timer=setTimeout(apply,260);};
  prefix.oncompositionend=()=>{clearTimeout(timer);timer=setTimeout(apply,260);};
  filter.onchange=apply;
  $('codeHistoryForm').onsubmit=e=>{e.preventDefault();apply();};
  $('codeClear').onclick=()=>{prefix.value='';filter.value='all';apply();prefix.focus();};
  $('codePrev').onclick=()=>{if(page>1){page--;sync();load();}};
  $('codeNext').onclick=()=>{if(page<pages){page++;sync();load();}};
  $('codeRetry').onclick=()=>{info.focus();load();};
  return {load,urlFields};
 }
 window.AdminCodeHistory={bind};
})();
