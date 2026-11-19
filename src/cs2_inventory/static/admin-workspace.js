(() => {
  'use strict';
  function labelRows(container) {
    const headers=[...container.querySelectorAll('thead th')];
    for (const th of headers) th.scope='col';
    for (const tr of container.querySelectorAll('tbody tr')) {
      [...tr.cells].forEach((cell,i)=>{
        const label=document.createElement('span');
        label.className='admin-field-label';
        label.setAttribute('aria-hidden','true');
        label.textContent=headers[i].textContent;
        cell.prepend(label);
      });
    }
  }
  function bindFilter(prefix,initial,onSearch,onDraft) {
    const search=document.getElementById(prefix+'Search'),form=document.getElementById(prefix+'Form'),clear=document.getElementById(prefix+'Clear');
    search.value=initial;clear.hidden=!initial;
    let timer,last=initial,draft=false;
    const submit=()=>{
      clearTimeout(timer);
      const q=search.value.trim();clear.hidden=!q;
      if(q!==last||draft){last=q;draft=false;onSearch(q);}
    };
    form.addEventListener('submit',ev=>{ev.preventDefault();submit();});
    search.addEventListener('input',ev=>{
      const q=search.value.trim();clear.hidden=!q;clearTimeout(timer);
      if(q!==last||draft){draft=true;onDraft(q);}
      if(!ev.isComposing)timer=setTimeout(submit,220);
    });
    search.addEventListener('compositionend',()=>{clearTimeout(timer);timer=setTimeout(submit,220);});
    clear.onclick=()=>{search.value='';submit();search.focus();};
    return value=>{clearTimeout(timer);draft=false;last=search.value=value;clear.hidden=!value;};
  }
  function bind({onSearch,initial,onUserSearch,userInitial,onTask,onDraft,onRetry}) {
    const byId=id=>document.getElementById(id);
    bindFilter('adminTarget',initial,onSearch,q=>onDraft('target',q));
    const setUserSearch=bindFilter('adminUser',userInitial,onUserSearch,q=>onDraft('user',q));
    const lists=Object.fromEntries([['target','adminTarget','adminTargets'],['user','adminUser','users']].map(([key,prefix,id])=>{
      const retry=byId(prefix+'Retry');retry.onclick=()=>onRetry(key);
      return [key,{root:byId(id),status:byId(prefix+'Status'),retry,prev:byId(key+'Prev'),next:byId(key+'Next'),page:byId(key+'PageLabel'),search:byId(prefix+'Search')}];
    }));
    function listState(key,phase,q,page){
      const l=lists[key], target=key==='target'?'目标':'账号';
      if(phase==='loading'&&(document.activeElement===l.prev||document.activeElement===l.next)){
        l.status.tabIndex=-1;l.status.focus();
      }
      if(phase==='loaded'){
        l.root.setAttribute('aria-busy','false');return;
      }
      l.root.replaceChildren();
      l.prev.disabled=l.next.disabled=true;
      l.root.setAttribute('aria-busy',String(phase==='loading'));
      if(phase==='loading'){
        l.status.textContent=q?`正在查找「${q}」…`:`正在读取第 ${page} 页${target}…`;
        if(document.activeElement===l.retry)l.search.focus();
        l.retry.hidden=true;l.page.textContent=`第 ${page} 页`;
      }else{
        l.status.textContent=q?`「${q}」的结果读取失败`:`第 ${page} 页${target}读取失败`;
        l.retry.hidden=false;
      }
    }
    const text=document.getElementById('createdCode'),copy=document.getElementById('copyCreatedCode'),feedback=document.getElementById('copyCodeStatus');
    const password=document.getElementById('newPassword'),visibility=document.getElementById('showInitialPassword');
    function mask(reveal){
      password.type=reveal?'text':'password';visibility.setAttribute('aria-pressed',String(reveal));visibility.textContent=reveal?'隐藏密码':'显示密码';
    }
    visibility.onclick=()=>mask(password.type==='password');
    const tasks=[...document.querySelectorAll('[data-admin-user-task]')];
    for(const btn of tasks)btn.onclick=()=>onTask(btn.dataset.adminUserTask);
    function showTask(task){
      for(const btn of tasks)btn.setAttribute('aria-pressed',String(task===btn.dataset.adminUserTask));
      for(const pane of document.querySelectorAll('[data-user-panel]'))pane.hidden=pane.dataset.userPanel!==task;
      if(task!=='create')mask(false);
      if(task!=='codes'){text.textContent='';document.getElementById('issuedCodePanel').hidden=true;feedback.textContent='';}
    }
    copy.onclick=async()=>{
      const code=text.textContent;feedback.textContent='';
      try {
        await navigator.clipboard.writeText(code);
        if(text.textContent===code)feedback.textContent='已复制续期码';
      } catch {
        if(text.textContent!==code)return;
        const range=document.createRange();range.selectNodeContents(text);
        const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
        feedback.textContent='已选中续期码，请手动复制';
      }
    };
    function showCode(code){
      text.textContent=code;feedback.textContent='';
      document.getElementById('issuedCodePanel').hidden=false;
    }
    return {setUserSearch,showTask,showCode,listState};
  }
  window.AdminWorkspace={bind,labelRows};
})();
