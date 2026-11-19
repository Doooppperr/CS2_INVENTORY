/* 校验/估算在本地进行；惟有提交表单会创建任务。重试库存结果永远是 GET。 */
(() => {
  'use strict';
  const idRe=/^7656119\d{10}$/,urlRe=/^https?:\/\/(?:www\.)?steamcommunity\.com\/profiles\/(7656119\d{10})\/?(?:[?#][^\s]*)?$/i;
  function draft(text){
    const items=new Map(), errors=[];let duplicates=0,start=0;
    text.split('\n').forEach((raw,i)=>{
      const line=raw.trim(),row={number:i+1,start,end:start+raw.length};start+=raw.length+1;
      if(!line)return;
      const match=/^([^\s,，]+)(?:[\s,，]+(.*))?$/u.exec(line);
      const token=match?.[1]||'',alias=(match?.[2]||'').trim(),id=idRe.test(token)?token:urlRe.exec(token)?.[1];
      if(!id)errors.push({...row,reason:'请输入有效的 SteamID64 或数字主页链接'});
      else if([...alias].length>50)errors.push({...row,reason:'备注超过 50 个字符'});
      else if(items.has(id))duplicates++;
      else items.set(id,{steamid:id,alias,number:row.number});
    });
    return {items:[...items.values()],errors,duplicates};
  }
  function bind({api,user,active,esc,sectionsMarkup,recordUrl,targetUrl,rate}){
    const $=id=>document.getElementById(id),field=$('adminSteamid'),button=$('adminQuery'),status=$('instantStatus'),preview=$('instantPreview'),errors=$('instantProblems');
    let posting=false,submitted='',version=0;
    const create=(tag,text)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;return el;};
    function paintDraft(){
      const parsed=draft(field.value),n=parsed.items.length,over=n>20,bad=over||!!parsed.errors.length;
      field.setAttribute('aria-invalid',String(bad));errors.hidden=!bad;errors.replaceChildren();
      if(over)errors.append(create('p',`共 ${n} 个不同目标，单批最多 20 个，请分批提交。`));
      if(parsed.errors.length){
        const list=create('ul');
        for(const row of parsed.errors.slice(0,5)){const li=create('li'),fix=create('button',`第 ${row.number} 行：${row.reason}`);
          fix.type='button';fix.className='instant-row-fix';fix.onclick=()=>{field.focus();field.setSelectionRange(row.start,row.end);};
          li.append(fix);list.append(li);}
        errors.append(list);if(parsed.errors.length>5)errors.append(create('p',`另有 ${parsed.errors.length-5} 行需要修改`));
      }
      $('queryEstimate').textContent=n?`${n} 个有效目标 · 若均为新任务，基础 ${n*2} 次请求 / ${n*2*rate} credits${parsed.duplicates?` · 已合并 ${parsed.duplicates} 行重复目标`:''}。分页和重试另计。`:'尚无有效目标 · 0 次请求';
      preview.hidden=!n;
      $('instantPreviewLabel').textContent=over?`核对前 20 个目标（共 ${n} 个）`:`核对 ${n} 个目标`;
      $('instantPreviewList').replaceChildren(...parsed.items.slice(0,20).map(x=>{const li=create('li',x.steamid);if(x.alias)li.append(create('small',x.alias));return li;}));
      button.disabled=posting||!n||bad||submitted===field.value;
      return parsed;
    }
    const states={queued:'等待扫描',running:'正在更新库存',completed:'已完成',succeeded:'已完成',failed:'查询失败',cancelled:'已取消'};
    function showCards(entries,{restore=false,focus=false}={}){
      const batch=++version,container=$('adminQueryResult');container.replaceChildren();$('instantResults').hidden=false;
      $('instantResultsTitle').textContent=`本批结果 · ${entries.length} 个目标`;
      for(const entry of entries){
        const card=create('article');card.className='query-result';const heading=create('h4',entry.alias||entry.steamid||`查询记录 #${entry.job_id}`),info=create('p'),summary=create('p','正在读取结果…'),body=create('div'),actions=create('div'),retry=create('button','重新读取结果');
        info.className='meta';summary.setAttribute('role','status');body.className='query-body';actions.className='instant-result-actions';retry.hidden=true;retry.type='button';retry.className='ghost';actions.append(retry);
        if(!entry.reused){const historyLink=create('a','查看本条历史记录');historyLink.href=recordUrl(entry.job_id);actions.append(historyLink);}
        if(entry.target_id){const stock=create('a','查看当前库存');stock.href=targetUrl(entry.target_id);actions.append(stock);}
        info.textContent=[entry.alias?entry.steamid:'',entry.reused?'复用已有任务':''].filter(Boolean).join(' · ');
        heading.tabIndex=-1;card.append(heading,info,summary,body,actions);container.append(card);let markup=null;
        const yours=()=>batch===version&&active()&&card.isConnected;
        async function read(){
          if(!yours())return;const fromRetry=document.activeElement===retry;retry.disabled=true;
          try{
            const d=await api(`api/jobs/${entry.job_id}`);if(!yours())return;
            if(!entry.alias&&!entry.steamid&&d.result?.query_alias)heading.textContent=d.result.query_alias;
            const ongoing=d.status==='queued'||d.status==='running';let result=d.result;
            if(result?.target_id)result=await api(`api/monitors/${result.target_id}`);if(!yours())return;
            const sections=result?.inventory_sections;let html=sections?sectionsMarkup(sections):'';
            if(!ongoing&&d.error)html+=`<p>${esc(d.error)}</p>`;
            if(!ongoing&&!sections&&!d.error)html+='<p>本条记录没有物品明细。</p>';
            if(!ongoing&&(d.result?.diagnostics||d.result?.quality))html+=`<details><summary>查询诊断</summary><pre>${esc(JSON.stringify(d.result.diagnostics||d.result.quality,null,2))}</pre></details>`;
            if(html!==markup){const old=[...body.querySelectorAll(':scope > details')],open=old.map(x=>x.open),idx=old.findIndex(x=>x.querySelector('summary')===document.activeElement);body.innerHTML=html;markup=html;
              const next=body.querySelectorAll(':scope > details');open.forEach((v,i)=>{if(next[i])next[i].open=v;});if(idx>=0)next[idx]?.querySelector('summary')?.focus({preventScroll:true});}
            summary.textContent=states[d.status]||d.status;retry.hidden=true;
            if(fromRetry&&(document.activeElement===document.body||document.activeElement===retry))heading.focus();
            if(ongoing)setTimeout(read,2500);
          }catch(error){
            if(!yours())return;
            const ended=[401,403,404,410].includes(error.status);
            summary.textContent=ended?error.message:'这次未读到查询结果。';retry.hidden=ended;
          }finally{retry.disabled=false;}
        }
        if(restore&&!entry.reused)api(`api/admin/queries/${entry.job_id}`).then(meta=>{
          if(!yours())return;
          heading.textContent=meta.alias||meta.steamid;
          info.textContent=meta.alias?meta.steamid:'';
        }).catch(()=>{/* 记录详情可以单独打开，实时结果仍由任务接口读取。 */});
        retry.onclick=read;read();
      }
      if(restore)status.textContent='已恢复本页查询结果，无需再次提交。';
      if(focus)$('instantResultsTitle').focus();
    }
    field.addEventListener('input',()=>{submitted='';if(!posting)status.textContent='';paintDraft();});
    field.addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&!e.isComposing){e.preventDefault();if(!e.repeat&&!button.disabled)$('instantQueryForm').requestSubmit();}});
    $('instantQueryForm').onsubmit=async event=>{
      event.preventDefault();const parsed=paintDraft();if(button.disabled)return;
      posting=true;field.readOnly=true;button.disabled=true;button.textContent='正在提交…';status.textContent='';
      try{
        const items=parsed.items.map(({steamid,alias})=>({steamid,alias}));
        const d=await api('api/admin/queries',{method:'POST',body:JSON.stringify({items})});
        submitted=field.value;const reused=d.items.filter(x=>x.reused).length;
        status.textContent=`已新建 ${d.items.length-reused} 个任务，复用 ${reused} 个。新建任务基础 ${d.estimated_requests} 次请求 / ${d.estimated_requests*d.credits_per_request} credits。`;
        // 只记本历史条目的编号，不保存账号名、备注和请求正文。刷新只重新读取，不重发查询。
        const saved=d.items.map(({job_id,target_id,reused})=>({job_id,target_id,reused}));
        history.replaceState({...history.state,instantResults:{owner:user(),entries:saved}},'',location.href);
        showCards(d.items,{focus:document.activeElement===document.body||document.activeElement===field||document.activeElement===button});
      }catch(error){
        status.textContent=error.status&&error.status<500?error.message:'提交结果未确认，请先查看查询历史。';
      }finally{posting=false;field.readOnly=false;button.textContent='开始查询';paintDraft();}
    };
    paintDraft();
    return {resume(){const state=history.state?.instantResults;if(state?.owner===user()&&state.entries.length)showCards(state.entries,{restore:true});}};
  }
  window.AdminInstantQuery={draft,bind};
})();
