/* 趋势仅绘制可对比的日期；留空日期不连线、不补零。 */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=value=>Number.isFinite(value)?value.toLocaleString('zh-CN'):'—';
  const delta=(value,sign)=>Number.isFinite(value)?`${sign}${num(value)}`:'—';
  const fmt=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
  let currentData=null,selected=-1;
  function clear(){
    currentData=null;selected=-1;
    for(const id of ['trendLegend','trendChart','increaseRanking','decreaseRanking'])$(id).replaceChildren();
    $('overviewUpdated').textContent='';$('overviewCoverage').textContent='';
    $('trendTooltip').textContent='';$('trendOpenDay').hidden=true;
    document.querySelectorAll('.rank-period').forEach(el=>el.textContent='');
  }
  function drawTrend(data){
    const points=data.trend, width=Math.max($('trendChart').clientWidth,280), height=width<500?240:290, left=44, right=18, top=20, bottom=40;
    const values=points.flatMap(p=>[p.increase,p.decrease]).filter(Number.isFinite);
    if(!values.length){$('trendChart').innerHTML='<div class="chart-empty">暂无可对比记录</div>';return;}
    const ceiling=Math.max(...values,1), step=10**Math.floor(Math.log10(ceiling));
    const max=Math.ceil(ceiling/step)*step, floor=height-bottom;
    const x=i=>left+i*(width-left-right)/Math.max(points.length-1,1),y=n=>floor-n/max*(floor-top);
    let svg=`<svg viewBox="0 0 ${width} ${height}" role="group" aria-label="每日库存增加与减少件数">`;
    for(let i=0;i<=4;i++){
      const value=max*i/4,cy=y(value);
      svg+=`<line class="chart-grid" x1="${left}" x2="${width-right}" y1="${cy}" y2="${cy}"/><text class="chart-axis" x="${left-12}" y="${cy+4}" text-anchor="end">${esc(num(Number(value.toFixed(2))))}</text>`;
    }
    for(const [key,color] of [['increase','up'],['decrease','down']]){
      let segment=[];
      const flush=()=>{if(segment.length>1)svg+=`<polygon class="chart-area-${color}" points="${segment[0].split(',')[0]},${floor} ${segment.join(' ')} ${segment[segment.length-1].split(',')[0]},${floor}"/><polyline class="chart-line chart-${color}" points="${segment.join(' ')}"/>`;segment=[];};
      points.forEach((point,i)=>{if(Number.isFinite(point[key]))segment.push(`${x(i)},${y(point[key])}`);else flush();});flush();
      points.forEach((point,i)=>{if(Number.isFinite(point[key]))svg+=`<circle class="chart-dot chart-${color}" cx="${x(i)}" cy="${y(point[key])}" r="4"/>`;});
    }
    points.forEach((point,i)=>{
      const label=`${point.date}，加仓 ${delta(point.increase,'+')}，减仓 ${delta(point.decrease,'−')}，${point.compared_accounts}/${point.total_accounts} 个账号可对比`;
      const half=(width-left-right)/Math.max(points.length-1,1)/2,start=Math.max(left-12,x(i)-half),end=Math.min(width-right+12,x(i)+half);
      svg+=`<text class="chart-axis" x="${x(i)}" y="${height-15}" text-anchor="middle">${esc(point.date.slice(5).replace('-','/'))}</text><g class="chart-day" tabindex="${i===(selected<0?points.length-1:selected)?0:-1}" data-day="${i}" role="button" aria-pressed="${i===selected}" aria-label="${esc(label)}"><line class="chart-guide" x1="${x(i)}" x2="${x(i)}" y1="${top}" y2="${floor}"/><rect x="${start}" y="${top}" width="${end-start}" height="${height-top-3}" fill="transparent"/><title>${esc(label)}</title></g>`;
    });
    $('trendChart').innerHTML=svg+'</svg>';
    const buttons=[...$('trendChart').querySelectorAll('[data-day]')], action=$('trendOpenDay');
    function show(i){
      const point=points[i];
      if(point)$('trendTooltip').innerHTML=[point.date,`加仓 ${delta(point.increase,'+')}`,`减仓 ${delta(point.decrease,'−')}`,`${point.compared_accounts}/${point.total_accounts} 个账号可对比`].map(part=>`<span>${esc(part)}</span>`).join(' · ');
      else $('trendTooltip').textContent='选择日期查看当日明细';
    }
    function select(i){
      selected=i;const date=points[i].date,hasReport=date>=$('date').min&&date<=$('date').max;
      buttons.forEach((el,j)=>{el.setAttribute('aria-pressed',String(i===j));el.tabIndex=i===j?0:-1;});
      action.dataset.date=date;action.textContent=`查看 ${date.slice(5).replace('-','/')} 明细 →`;action.hidden=!hasReport;
      show(i);if(!hasReport)$('trendTooltip').textContent+=' · 超出明细保留期';
    }
    buttons.forEach((el,i)=>{
      const preview=()=>{if(selected<0)show(i);},leave=()=>{if(selected<0)show(-1);};
      el.addEventListener('pointerenter',preview);el.addEventListener('focus',preview);el.addEventListener('pointerleave',leave);el.addEventListener('blur',leave);
      el.addEventListener('click',()=>select(i));
      el.addEventListener('keydown',e=>{
        if(e.key==='Enter'||e.key===' '){e.preventDefault();select(i);return;}
        const next={ArrowLeft:Math.max(0,i-1),ArrowRight:Math.min(buttons.length-1,i+1),Home:0,End:buttons.length-1}[e.key];
        if(next!==undefined){e.preventDefault();select(next);buttons[next].focus();}
      });
    });
    if(selected<0)show(-1);else select(selected);
  }
  function renderRanking(items,direction,available){
    const up=direction==='increase',sign=up?'+':'−',tone=up?'up':'down';
    if(!items.length)return `<div class="ranking-empty">${available?`暂无${up?'加仓':'减仓'}记录`:'暂无可对比记录'}</div>`;
    const base=$('dashboard').dataset.app.replace(/\/$/,'');
    const back=typeof location==='undefined'?base+'/dashboard':location.pathname+location.search;
    const account=(row,item)=>`<div class="rank-account"><a data-target="${esc(row.target_id)}" href="${esc(`${base}/monitors/${encodeURIComponent(row.target_id)}?source=dashboard&item=${encodeURIComponent(item.name)}&return_to=${encodeURIComponent(back)}`)}" title="${esc(row.label)}" aria-label="查看${esc(row.label)}的${esc(item.name)}库存"><span>${esc(row.label)}</span></a><span class="rank-${tone}">${delta(row.count,sign)}</span><span class="rank-holding" title="${esc(fmt(row.holding_at))}">持有 ${num(row.holding)}</span></div>`;
    return `<ol class="ranking-list">${items.map((item,i)=>`<li class="rank-item" data-rank="${i}" style="--share:${Math.round(item.count/items[0].count*100)}%"><span class="rank-index rank-index-${i+1}">${i+1}</span><div class="rank-content"><div class="rank-title"><h3>${esc(item.name)}</h3><b class="rank-${tone}">${delta(item.count,sign)}</b></div><div class="rank-track"><span class="rank-fill-${tone}"></span></div><div class="rank-accounts">${item.accounts.slice(0,3).map(row=>account(row,item)).join('')}${item.accounts.length>3?`<details><summary>另 ${item.accounts.length-3} 个账号</summary>${item.accounts.slice(3).map(row=>account(row,item)).join('')}</details>`:''}</div></div></li>`).join('')}</ol>`;
  }
  function render(data){
    clear();currentData=data;const summary=data.summary;
    $('overviewUpdated').textContent=data.updated_at?`更新于 ${fmt(data.updated_at)}`:'';
    $('overviewCoverage').textContent=summary.available_days?`${data.start_date.slice(5).replace('-','/')} — ${data.end_date.slice(5).replace('-','/')} · ${summary.available_days}/${data.days} 日可对比`:'';
    $('trendLegend').innerHTML=`<span><i class="legend-up"></i>加仓 <b>${delta(summary.increase,'+')}</b></span><span><i class="legend-down"></i>减仓 <b>${delta(summary.decrease,'−')}</b></span>`;
    drawTrend(data);
    document.querySelectorAll('.rank-period').forEach(el=>el.textContent=`近 ${data.days} 日`);
    $('increaseRanking').innerHTML=renderRanking(data.rankings.increase,'increase',summary.available_days);
    $('decreaseRanking').innerHTML=renderRanking(data.rankings.decrease,'decrease',summary.available_days);
  }
  let resizeTimer;addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(currentData)drawTrend(currentData);},120);});
  window.InventoryOverview={render,clear};
})();
