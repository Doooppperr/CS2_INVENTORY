/* 跨页面导航与账号操作；页面数据仍由各自后端权限校验。 */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const channelName=home=>'cs2-session:'+home;
  function announce(home,action){
    const channel=new BroadcastChannel(channelName(home));
    channel.postMessage(action);
    channel.close();
  }
  function destination(next,home,origin=location.origin){
    const base=new URL(home,origin),fallback=new URL('app/dashboard',base),app=new URL('app',base).pathname;
    if(!next)return fallback.pathname;
    try{
      const target=new URL(next,base);
      if(target.origin!==base.origin||!(target.pathname===app||target.pathname.startsWith(app+'/')))return fallback.pathname;
      if([app,app+'/'].includes(target.pathname)&&!target.search&&!target.hash)return fallback.pathname;
      return target.pathname+target.search+target.hash;
    }catch{return fallback.pathname;}
  }
  function activate(active){
    document.querySelectorAll('[data-workspace]').forEach(link=>{
      const selected=link.dataset.workspace===active;
      link.classList.toggle('active',selected);
      if(selected)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    });
  }
  function init({user,csrf,active}){
    const nav=$('workspaceNavigation'),home=nav.dataset.home,menu=$('accountMenu'),dialog=$('selfPasswordDialog');
    $('navUsername').textContent=user.username;
    const changes=new BroadcastChannel(channelName(home));
    function leaveWorkspace(action){
      document.body.style.visibility='hidden';
      location.replace(action==='logout'?home+'?auth=login':destination(null,home));
    }
    changes.onmessage=event=>leaveWorkspace(event.data);
    addEventListener('pagehide',()=>changes.close(),{once:true});
    nav.querySelector('[data-workspace="admin"]').classList.toggle('hidden',user.role!=='admin');
    activate(active);
    const url=path=>new URL(path,new URL(home,location.origin));
    async function request(path,options={}){
      const response=await fetch(url(path),{credentials:'same-origin',cache:'no-store',...options});
      const data=await response.json();
      if(!response.ok)throw Object.assign(new Error(data.error||'操作失败'),{status:response.status});
      return data;
    }
    function post(path,body){
      return request(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body||{})});
    }
    const switchDialog=$('switchAccountDialog'),form=$('switchAccountForm'),message=$('switchAccountMessage');
    const submit=$('switchAccountSubmit'),cancel=$('switchAccountCancel'),password=$('switchPassword');
    const saved=$('savedAccounts'),section=$('savedAccountSection'),error=$('navError');
    let pending=false,listVersion=0;

    function openLogin(username=''){
      menu.open=false;
      $('switchCurrentUser').textContent=user.username;
      $('switchAccountTitle').textContent=username?'验证账号':'添加账号';
      $('switchUsername').value=username;
      switchDialog.showModal();
      (username?password:$('switchUsername')).focus();
    }
    $('navSwitch').onclick=()=>openLogin();
    function switched(){changes.postMessage('login');leaveWorkspace('login');}

    function renderAccounts(accounts,currentId){
      const others=accounts.filter(a=>a.id!==currentId);
      saved.replaceChildren();
      for(const account of others){
        const row=document.createElement('div'),choose=document.createElement('button'),forget=document.createElement('button');
        row.className='saved-account-row';
        choose.type=forget.type='button';
        choose.dataset.switchUser=forget.dataset.forgetUser=String(account.id);
        choose.textContent=account.username;choose.title=account.username;
        forget.textContent='移除';forget.setAttribute('aria-label',`移除账号 ${account.username}`);
        choose.onclick=()=>actOnAccount(account,false);
        forget.onclick=()=>actOnAccount(account,true);
        row.append(choose,forget);saved.append(row);
      }
      section.hidden=!others.length;
    }
    async function loadAccounts(){
      const version=++listVersion;
      try{
        const data=await request('api/auth/accounts');
        if(version!==listVersion || !menu.open)return;
        if(data.current_id!==user.id){leaveWorkspace('login');return;}
        renderAccounts(data.accounts,data.current_id);
      }catch(failure){
        if(version!==listVersion || !menu.open)return;
        if(failure.status===401){leaveWorkspace('logout');return;}
        error.textContent=failure.message;
      }
    }
    menu.addEventListener('toggle',()=>{
      listVersion++;
      error.textContent='';
      if(menu.open)loadAccounts();
      else{saved.replaceChildren();section.hidden=true;}
    });
    function busy(value){
      pending=value;
      for(const button of menu.querySelectorAll('button'))button.disabled=value;
    }
    async function actOnAccount(account,forget){
      if(pending)return;
      busy(true);error.textContent='';
      try{
        if(forget){
          await post('api/auth/accounts/forget',{user_id:account.id});
          await loadAccounts();
        }else{
          await post('api/auth/switch',{user_id:account.id});
          switched();
        }
      }catch(failure){
        if(failure.status===401){leaveWorkspace('logout');return;}
        if(!forget && failure.status===403){openLogin(account.username);return;}
        error.textContent=failure.message;
      }finally{busy(false);}
      if(forget && menu.open)(saved.querySelector('[data-switch-user]')||$('navSwitch')).focus();
    }
    $('navLogout').onclick=async()=>{
      if(pending)return;
      busy(true);error.textContent='';
      try{await post('api/auth/logout');changes.postMessage('logout');leaveWorkspace('logout');}
      catch(failure){error.textContent=failure.message;busy(false);}
    };
    cancel.onclick=()=>switchDialog.close();
    switchDialog.addEventListener('cancel',event=>{if(submit.disabled)event.preventDefault();});
    switchDialog.addEventListener('close',()=>{
      form.reset();message.textContent='';
      menu.querySelector('summary').focus();
    });
    form.onsubmit=async event=>{
      event.preventDefault();if(submit.disabled)return;
      cancel.disabled=submit.disabled=true;submit.textContent='正在切换…';message.textContent='';
      try{
        await post('api/auth/login',{username:$('switchUsername').value.trim(),password:password.value});
        form.reset();switched();
      }catch(error){
        message.textContent=error.message;password.focus();password.select();
      }finally{
        cancel.disabled=submit.disabled=false;submit.textContent='登录并切换';
      }
    };
    $('navPassword').onclick=()=>{menu.open=false;$('selfPasswordForm').reset();$('selfPasswordMessage').textContent='';dialog.showModal();};
    $('selfPasswordCancel').onclick=()=>dialog.close();
    dialog.addEventListener('close',()=>{$('selfPasswordForm').reset();$('selfPasswordMessage').textContent='';});
    $('selfPasswordForm').onsubmit=async event=>{
      event.preventDefault();const message=$('selfPasswordMessage');message.textContent='';
      if($('selfNewPassword').value!==$('selfConfirmPassword').value){message.textContent='两次输入的新密码不一致';return;}
      $('selfPasswordSave').disabled=true;
      try{await post('api/auth/password',{old_password:$('selfOldPassword').value,new_password:$('selfNewPassword').value});$('selfPasswordForm').reset();message.textContent='密码已修改';}
      catch(error){message.textContent=error.message;}
      finally{$('selfPasswordSave').disabled=false;}
    };
    document.addEventListener('click',event=>{if(!menu.contains(event.target))menu.open=false;});
    menu.addEventListener('keydown',event=>{if(event.key==='Escape'){menu.open=false;menu.querySelector('summary').focus();}});
  }
  // 返回缓存页面时重新验证会话，避免账号切换后重用旧页面数据。
  addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
  window.WorkspaceNavigation={init,activate,destination,announce};
})();
