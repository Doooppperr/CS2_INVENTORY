/* 趋势仅绘制可对比的日期；留空日期不连线、不补零。 */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=value=>Number.isFinite(value)?value.toLocaleString('zh-CN'):'—';
  const delta=(value,sign)=>Number.isFinite(value)?`${sign}${num(value)}`:'—';
  const fmt=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
  let currentData=null;
  function clear(){
    currentData=null;
    for(const id of ['trendLegend','trendChart','increaseRanking','decreaseRanking'])$(id).replaceChildren();
    $('overviewUpdated').textContent='';$('overviewCoverage').textContent='';
    $('trendTooltip').classList.add('hidden');$('trendTooltip').textContent='';
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
      svg+=`<text class="chart-axis" x="${x(i)}" y="${height-15}" text-anchor="middle">${esc(point.date.slice(5).replace('-','/'))}</text><g class="chart-day" tabindex="0" data-day="${i}" role="group" aria-label="${esc(label)}"><line class="chart-guide" x1="${x(i)}" x2="${x(i)}" y1="${top}" y2="${floor}"/><rect x="${start}" y="${top}" width="${end-start}" height="${floor-top}" fill="transparent"/><title>${esc(label)}</title></g>`;
    });
    $('trendChart').innerHTML=svg+'</svg>';
    $('trendChart').querySelectorAll('[data-day]').forEach(el=>{
      const show=()=>{const point=points[Number(el.dataset.day)];$('trendTooltip').textContent=`${point.date} · 加仓 ${delta(point.increase,'+')} · 减仓 ${delta(point.decrease,'−')} · ${point.compared_accounts}/${point.total_accounts} 个账号可对比`;$('trendTooltip').classList.remove('hidden');};
      const hide=()=>$('trendTooltip').classList.add('hidden');
      el.addEventListener('pointerenter',show);el.addEventListener('focus',show);el.addEventListener('pointerleave',hide);el.addEventListener('blur',hide);
    });
  }
  function renderRanking(items,direction,available){
    const up=direction==='increase',sign=up?'+':'−',tone=up?'up':'down';
    if(!items.length)return `<div class="ranking-empty">${available?`暂无${up?'加仓':'减仓'}记录`:'暂无可对比记录'}</div>`;
    const base=$('dashboard').dataset.app.replace(/\/$/,'');
    const account=row=>`<div class="rank-account"><a href="${esc(`${base}/monitors/${encodeURIComponent(row.target_id)}`)}" title="${esc(row.label)}">${esc(row.label)}</a><span class="rank-${tone}">${delta(row.count,sign)}</span><span class="rank-holding" title="${esc(fmt(row.holding_at))}">持有 ${num(row.holding)}</span></div>`;
    return `<ol class="ranking-list">${items.map((item,i)=>`<li class="rank-item" style="--share:${Math.round(item.count/items[0].count*100)}%"><span class="rank-index rank-index-${i+1}">${i+1}</span><div class="rank-content"><div class="rank-title"><h3>${esc(item.name)}</h3><b class="rank-${tone}">${delta(item.count,sign)}</b></div><div class="rank-track"><span class="rank-fill-${tone}"></span></div><div class="rank-accounts">${item.accounts.slice(0,3).map(account).join('')}${item.accounts.length>3?`<details><summary>另 ${item.accounts.length-3} 个账号</summary>${item.accounts.slice(3).map(account).join('')}</details>`:''}</div></div></li>`).join('')}</ol>`;
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
  let resizeTimer;addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(currentData){$('trendTooltip').classList.add('hidden');drawTrend(currentData);}},120);});
  window.InventoryOverview={render,clear};
})();
