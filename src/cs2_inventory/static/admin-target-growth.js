/* 按需打开单个账号的加仓明细。当前窗口、读取状态、阅读批次均属于这一张卡片。 */
(() => {
 'use strict';
 const batchSize=6;
 function bind(root,{read,fmt,esc,detailPath,navigate}){
  let current=0,request,result,shown=batchSize,phase;
  const id=Number(root.querySelector('.target-open').dataset.id);
  const panel=root.querySelector('.target-growth-detail'),buttons=[...root.querySelectorAll('[data-growth-days]')];
  const button=days=>buttons.find(b=>Number(b.dataset.growthDays)===days);
  const numeric=(v)=>v==null?'—':String(v);
  function mark(){for(const b of buttons)b.setAttribute('aria-expanded',String(Number(b.dataset.growthDays)===current));}
  function close(){request?.abort();current=0;result=null;mark();panel.hidden=true;panel.replaceChildren();panel.setAttribute('aria-busy','false');}
  function stamp(data){
   const time=(tag,value)=>value?`<span>${tag} <time datetime="${esc(value)}">${esc(fmt(value))}</time></span>`:'';
   return `<p class="target-period">${time('对照',data.baseline_at)}${time('观测',data.observed_at)}</p>`;
  }
  function paint(focusIndex){
   const rows=result.items;
   let body;
   if(result.state==='no_observation')body='<p>还没有交易保护库存记录。</p>';
   else if(result.state==='no_baseline')body='<p>没有可对比的库存记录。</p>';
   else if(!rows.length)body='<p>本时段没有新增交易保护物品。</p>';
   else{
    const names=new Map();for(const x of rows)names.set(x.name,(names.get(x.name)||0)+1);
    body=`<p><strong>新增 ${numeric(result.total)} 件</strong> · ${rows.length} 种</p><ul>${rows.slice(0,shown).map((item,i)=>{
     const query=new URLSearchParams({protected:'1',item:item.name});
     const variant=names.get(item.name)>1&&item.market_name!==item.name?item.market_name:'';
     return `<li><a data-growth-item="${i}" href="${esc(detailPath(id)+'&'+query)}" aria-label="查看${esc(item.name)}的${variant?'同名':''}当前保护库存">${esc(item.name)}${variant?`<small>${esc(variant)}</small>`:''}</a><b>+${item.count}</b></li>`;
    }).join('')}</ul>${shown<rows.length?`<button type="button" class="ghost growth-more">再看 ${Math.min(batchSize,rows.length-shown)} 种 · 共 ${rows.length} 种</button>`:''}`;
   }
   panel.innerHTML=body+stamp(result);
   for(const a of panel.querySelectorAll('[data-growth-item]'))a.onclick=e=>{
    if(e.button||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
    e.preventDefault();const i=Number(a.dataset.growthItem),item=rows[i];
    navigate(id,item.name,{days:current,shown,index:i,baseline:result.baseline_at,observed:result.observed_at});
   };
   const more=panel.querySelector('.growth-more');
   if(more)more.onclick=()=>{const first=shown;shown+=batchSize;paint(first);};
   if(focusIndex!=null)panel.querySelector('[data-growth-item="'+focusIndex+'"]')?.focus();
  }
  async function load(days,limit=batchSize){
   request?.abort();const mine=request=new AbortController();current=days;shown=limit;result=null;mark();panel.hidden=false;
   phase='loading';panel.setAttribute('aria-busy','true');panel.textContent='正在读取加仓物品…';
   try{
    const data=await read(`api/admin/targets/${id}/protected-additions?days=${days}`,{signal:mine.signal});
    if(mine.signal.aborted||!root.isConnected)return;
    result=data;phase='ready';paint();
   }catch(e){
    if(mine.signal.aborted||!root.isConnected)return;
    phase='error';panel.innerHTML='<p>加仓明细读取失败。</p><button type="button" class="ghost growth-retry">重新读取</button>';
    panel.querySelector('.growth-retry').onclick=()=>{button(days).focus();load(days);};
   }finally{if(!mine.signal.aborted)panel.setAttribute('aria-busy','false');}
  }
  for(const b of buttons)b.onclick=()=>{const days=Number(b.dataset.growthDays);if(current===days)close();else load(days);};
  return async(saved)=>{
   await load(saved.days,saved.shown);
   if(!root.isConnected||current!==saved.days)return null;
   const same=phase==='ready'&&result.baseline_at===saved.baseline&&result.observed_at===saved.observed;
   return (same&&panel.querySelector('[data-growth-item="'+saved.index+'"]'))||button(saved.days);
  };
 }
 const controls=new WeakMap();
 function attach(cards,options){for(const root of cards)controls.set(root,bind(root,options));}
 function resume(root,saved){return controls.get(root)?.(saved);}
 window.AdminTargetGrowth={attach,resume};
})();
