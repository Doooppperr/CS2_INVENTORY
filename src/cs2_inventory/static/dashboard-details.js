/* 明细使用同一刻度比较增减；待核实记录不绘制有效变动条。 */
(() => {
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const signed=value=>`${value>0?'+':''}${value}`;
  const fmt=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
  function render(items,section,app){
    const pending=section==='pending',maximum=Math.max(1,...items.flatMap(item=>[item.increase,item.decrease]));
    const account=a=>`<div class="account-detail"><a href="${esc(app.replace(/\/$/,''))}/monitors/${encodeURIComponent(a.target_id)}">${esc(a.label)}</a><strong>${a.before} → ${a.after}</strong><p>首次发现 ${a.first_seen} · 再次出现 ${a.reappeared}</p>${a.verified===false?`<p>${esc(a.reason)}</p>`:''}<p class="muted">${fmt(a.baseline_at)} → ${fmt(a.scanned_at)}</p></div>`;
    return items.map(item=>`<details class="change-item"><summary><span class="change-name">${esc(item.name)}<small>${item.accounts.length} 个账号</small></span><span class="change-bars">${pending?'<span class="muted">待核实</span>':[['up',item.increase,'+'],['down',item.decrease,'−']].map(([direction,count,sign])=>`<span class="change-bar"><span class="change-track" aria-hidden="true"><i class="change-fill-${direction}" style="width:${100*count/maximum}%"></i></span><b class="rank-${direction}">${sign}${count}</b></span>`).join('')}</span><span class="change-net"><small>${pending?'观测净变化':'净变化'}</small><b>${signed(item.net)}</b></span><span class="change-chevron" aria-hidden="true">⌄</span></summary><div class="change-expanded"><p class="change-detail-meta">${pending?'待核实观察':`${item.account_count} 个账号增加 · 单账号最大增加 +${item.max_increase}`}</p><div class="change-accounts">${item.accounts.map(account).join('')}</div></div></details>`).join('');
  }
  window.InventoryDetails={render};
})();
