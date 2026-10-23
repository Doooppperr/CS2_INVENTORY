/* 跨页面导航与账号操作；页面数据仍由各自后端权限校验。 */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
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
    nav.querySelector('[data-workspace="admin"]').classList.toggle('hidden',user.role!=='admin');
    activate(active);
    async function post(path,body){
      const response=await fetch(new URL(path,new URL(home,location.origin)),{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body||{})});
      const data=await response.json();if(!response.ok)throw new Error(data.error||'操作失败');return data;
    }
    $('navLogout').onclick=async()=>{
      $('navLogout').disabled=true;$('navError').textContent='';
      try{await post('api/auth/logout');location.replace(home+'?auth=login');}
      catch(error){$('navError').textContent=error.message;$('navLogout').disabled=false;}
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
  window.WorkspaceNavigation={init,activate,destination};
})();
