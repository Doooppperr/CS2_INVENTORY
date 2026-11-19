const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const nodes=new Map(),events={};
function element(){return {textContent:'',value:'',dataset:{},children:[],classList:{toggle(){}},events:{},
 addEventListener(name,fn){this.events[name]=fn;},reset(){this.wasReset=true;},showModal(){this.open=true;},
 close(){this.open=false;this.events.close?.();},focus(){this.focused=true;},select(){this.selected=true;},
 setAttribute(k,v){this[k]=v;},append(...xs){this.children.push(...xs);},replaceChildren(){this.children=[];},
 querySelector(sel){return sel==='[data-switch-user]'?(this.children[0]?.children[0]||null):node('adminLink');},
 querySelectorAll(){return [];}
};}
function node(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);}
node('workspaceNavigation').dataset.home='/';
const links=['dashboard','monitors','admin'].map(name=>({dataset:{workspace:name},attributes:{},classList:{toggle(key,value){this[key]=value;}},setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];}}));
let request,ok=true,failStatus=401;
let savedAccounts=[{id:1,username:'<img>'},{id:2,username:'B<b>'}];

const channels=[];
class Channel{constructor(name){this.name=name;this.sent=[];channels.push(this);}postMessage(value){this.sent.push(value);}close(){this.closed=true;}}
const context={URL,BroadcastChannel:Channel,window:{},document:{body:{style:{}},getElementById:node,createElement:element,querySelectorAll:()=>links,addEventListener(){}},location:{origin:'https://example.test',replace(url){this.replaced=url;},reload(){this.reloaded=true;}},addEventListener(name,cb){events[name]=cb;},fetch:async(url,options)=>{request={url:String(url),options};if(ok&&String(url).endsWith('/forget'))savedAccounts=savedAccounts.filter(a=>a.id!==JSON.parse(options.body).user_id);
 return{ok,status:ok?200:failStatus,json:async()=>ok?(String(url).endsWith('/accounts')?{accounts:savedAccounts,current_id:1}:{ok:true}):{error:'用户名或密码错误'}};}};
vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/cs2_inventory/static/navigation.js'),'utf8'),context);
const nav=context.window.WorkspaceNavigation;
for(const bad of [null,'','//evil.test/app','https://evil.test/app','/api/admin/users','javascript:alert(1)','/\\evil.test/app','/app'])assert.equal(nav.destination(bad,'/'),'/app/dashboard');
assert.equal(nav.destination('/app?view=monitors&page=2','/'),'/app?view=monitors&page=2');
assert.equal(nav.destination('/app/monitors/1?source=dashboard','/'),'/app/monitors/1?source=dashboard');
assert.equal(nav.destination('/prefix/app?view=admin','/prefix/'),'/prefix/app?view=admin');
assert.equal(nav.destination('/app?view=admin','/prefix/'),'/prefix/app/dashboard');
nav.activate('dashboard');assert.equal(links[0].attributes['aria-current'],'page');assert.ok(!links[1].attributes['aria-current']);
nav.activate('monitors');assert.ok(!links[0].attributes['aria-current']);assert.equal(links[1].attributes['aria-current'],'page');
nav.init({user:{id:1,username:'<img>',role:'user'},csrf:'fixture-token',active:'monitors'});
assert.equal(node('navUsername').textContent,'<img>');
(async()=>{
 node('navSwitch').onclick();
 assert.equal(node('switchCurrentUser').textContent,'<img>');assert.ok(node('switchUsername').focused);
 node('switchAccountCancel').onclick();assert.ok(!node('switchAccountDialog').open);assert.ok(node('switchAccountForm').wasReset);
 node('navSwitch').onclick();node('switchUsername').value=' other ';node('switchPassword').value='fixture-password';ok=false;
 const submit=()=>node('switchAccountForm').onsubmit({preventDefault(){}});
 await submit();assert.equal(context.location.replaced,undefined);assert.deepEqual(channels[0].sent,[]);
 assert.equal(node('switchAccountMessage').textContent,'用户名或密码错误');assert.ok(node('switchPassword').selected);
 ok=true;await submit();
 assert.equal(request.url,'https://example.test/api/auth/login');assert.equal(request.options.headers['X-CSRF-Token'],'fixture-token');
 assert.deepEqual(JSON.parse(request.options.body),{username:'other',password:'fixture-password'});
 assert.equal(context.location.replaced,'/app/dashboard');assert.deepEqual(channels[0].sent,['login']);
 const openMenu=async()=>{node('accountMenu').open=true;node('accountMenu').events.toggle();await new Promise(resolve=>setImmediate(resolve));};
 await openMenu();
 assert.equal(request.url,'https://example.test/api/auth/accounts');assert.equal(request.options.cache,'no-store');
 assert.equal(node('savedAccountSection').hidden,false);
 let [choose,forget]=node('savedAccounts').children[0].children;
 assert.equal(choose.textContent,'B<b>');assert.equal(choose.dataset.switchUser,'2');assert.equal(forget['aria-label'],'移除账号 B<b>');
 await choose.onclick();
 assert.equal(request.url,'https://example.test/api/auth/switch');
 assert.deepEqual(JSON.parse(request.options.body),{user_id:2});assert.equal(request.options.headers['X-CSRF-Token'],'fixture-token');
 assert.equal(context.location.replaced,'/app/dashboard');assert.equal(channels[0].sent.at(-1),'login');
 ok=false;failStatus=403;await choose.onclick();assert.equal(node('switchUsername').value,'B<b>');
 assert.equal(node('switchAccountTitle').textContent,'验证账号');assert.ok(node('switchPassword').focused);
 node('switchAccountCancel').onclick();ok=true;await openMenu();
 forget=node('savedAccounts').children[0].children[1];
 await forget.onclick();assert.equal(node('savedAccountSection').hidden,true);assert.equal(node('savedAccounts').children.length,0);
 channels[0].onmessage({data:'logout'});assert.equal(context.location.replaced,'/?auth=login');
 await node('navLogout').onclick();assert.equal(request.url,'https://example.test/api/auth/logout');assert.equal(request.options.headers['X-CSRF-Token'],'fixture-token');assert.equal(context.location.replaced,'/?auth=login');
 node('selfNewPassword').value='one';node('selfConfirmPassword').value='two';request=null;
 await node('selfPasswordForm').onsubmit({preventDefault(){}});assert.equal(request,null);assert.equal(node('selfPasswordMessage').textContent,'两次输入的新密码不一致');
 events.pageshow({persisted:true});assert.ok(context.location.reloaded);
 console.log('NAVIGATION default/deep-link/origin/prefix/active/logout/password-validation/session/account-switch/cross-tab/saved/forget/reauth=PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
