/* 计划时间和实际批次时间来自不同字段，不根据轮次代号推断实际扫描时间。 */
(() => {
  'use strict';
  const $=id=>document.getElementById(id), count=value=>Number(value).toLocaleString('zh-CN');
  const stamp=value=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value));
  const names={queued:'已排队',running:'进行中',completed:'已结束',completed_with_errors:'有失败项',failed:'失败',cancelled:'已取消'};
  function renderBatch(batch){
    const badge=$('batchState'), note=$('batchUnfinished'),failed=$('batchFailures');
    badge.hidden=!batch;$('batchTotals').hidden=!batch;failed.hidden=!batch?.failed;note.hidden=true;
    if(!batch){$('batchTimes').textContent='尚无定时扫描记录。';return;}
    badge.dataset.state=batch.status;badge.textContent=names[batch.status]||'待确认';
    const times=[];
    if(batch.started_at)times.push('开始 '+stamp(batch.started_at));else if(batch.created_at)times.push('创建 '+stamp(batch.created_at));
    if(batch.finished_at){
      const first=batch.started_at||batch.created_at;
      const when=batch.finished_at.slice(0,10)===first?.slice(0,10)?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(batch.finished_at)):stamp(batch.finished_at);
      times.push('结束 '+when);
    }
    $('batchTimes').textContent=times.join(' · ');
    for(const [id,key] of [['batchTotal','total'],['batchCompleted','completed'],['batchFailed','failed'],['batchPending','remaining_jobs']])$(id).textContent=count(batch[key]);
    $('batchFailed').parentElement.classList.toggle('has-failures',batch.failed>0);
    if(batch.not_run){note.hidden=false;note.textContent='另有 '+count(batch.not_run)+' 个目标未执行或未计入结果。';}
    if(batch.failed){const url=new URL($('overviewTargets').href);url.searchParams.set('failed_batch',batch.id);failed.href=url.href;failed.textContent='查看本轮失败目标（'+count(batch.failed)+'）';}
  }
  function diagnostics(s,visible){
    const box=$('overviewDiagnostics');box.hidden=!visible;
    if(!visible)return;
    const q=s.quota,o=s.official_quota, rows=[
      ['今日估算用量 / 参考值',count(q.daily_used)+' / '+count(q.daily_budget)+' credits'],
      ['账期估算余额',count(q.billing_remaining)+' credits'],
      ['账期起点',stamp(q.billing_period_start)+' · '+(q.billing_period_source==='provider_subscription'?'供应商订阅':'每月25日估算')],
      ['预警 / 严重预警线',count(q.warning_reserve)+' / '+count(q.critical_reserve)+' credits'],
      ['每目标每日预计用量',count(q.estimated_scheduled_credits_per_target_day)+' credits（分页、重试另计）'],
      ['供应商今日 / 本月',o?.payload?.today!=null&&o?.payload?.month!=null?o.payload.today+' / '+o.payload.month:'暂无供应商记录'],
      ['监控目标 / 参考值',count(s.targets)+' / '+count(s.target_limit)],
      ['续期码：已兑换 / 已撤销',count(s.activation_codes.redeemed)+' / '+count(s.activation_codes.revoked)],
      ['名称本地化：待处理 / 失败',count(s.localization.pending_jobs)+' / '+count(s.localization.failed_jobs)+' 项任务']
    ];
    if(o?.checked_at)rows.push(['供应商记录时间',stamp(o.checked_at)]);
    $('quotaState').textContent={warning:'额度预警 · 扫描继续',critical:'额度严重预警 · 扫描继续'}[q.warning_level]||'';
    const metrics=$('overviewMetrics');
    metrics.replaceChildren(...rows.map(([title,value])=>{const item=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=title;dd.textContent=value;item.append(dt,dd);return item;}));
  }
  let current=0;
  async function load({read,privileged,active}){
    const request=++current,root=$('adminStatus'),feedback=$('overviewFeedback'),message=$('overviewMessage'),retry=$('overviewRetry');
    const fromRetry=document.activeElement===retry;
    root.setAttribute('aria-busy','true');feedback.hidden=false;message.textContent='正在读取运行概况…';retry.disabled=true;$('overviewResults').hidden=true;
    retry.onclick=()=>load({read,privileged,active});
    try{
      const s=await read();if(request!==current||!active())return;
      renderBatch(s.latest_daily_batch);
      $('overviewSchedule').textContent=s.scheduled_scan_hours_beijing.map(h=>String(h).padStart(2,'0')+':00').join(' / ');
      $('overviewTargetCount').textContent=count(s.targets);$('overviewQueueSummary').hidden=!privileged;if(privileged)$('overviewQueue').textContent=count(s.pending_jobs);$('overviewCodes').textContent=count(s.activation_codes.unused);
      diagnostics(s,privileged);feedback.hidden=true;$('overviewResults').hidden=false;
      if(fromRetry && (document.activeElement===document.body||feedback.contains(document.activeElement)))$('batchHeading').focus();
    }catch{
      if(request!==current||!active())return;
      message.textContent='运行概况读取失败。';retry.hidden=false;
    }finally{
      if(request===current){root.setAttribute('aria-busy','false');retry.disabled=false;}
    }
  }
  window.AdminOverview={load};
})();
