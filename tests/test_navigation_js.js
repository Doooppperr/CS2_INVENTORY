const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const nodes=new Map(),events={};
function node(id){if(!nodes.has(id))nodes.set(id,{textContent:'',value:'',dataset:{home:'/'},classList:{toggle(){}},addEventListener(){},reset(){},querySelector(){return node('adminLink');}});return nodes.get(id);}
const links=['dashboard','monitors','admin'].map(name=>({dataset:{workspace:name},attributes:{},classList:{toggle(key,value){this[key]=value;}},setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];}}));
let request;
const context={URL,window:{},document:{getElementById:node,querySelectorAll:()=>links,addEventListener(){}},location:{origin:'https://example.test',replace(url){this.replaced=url;},reload(){this.reloaded=true;}},addEventListener(name,cb){events[name]=cb;},fetch:async(url,options)=>{request={url:String(url),options};return{ok:true,json:async()=>({ok:true})};}};
vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/cs2_inventory/static/navigation.js'),'utf8'),context);
const nav=context.window.WorkspaceNavigation;
for(const bad of [null,'','//evil.test/app','https://evil.test/app','/api/admin/users','javascript:alert(1)','/\\evil.test/app','/app'])assert.equal(nav.destination(bad,'/'),'/app/dashboard');
assert.equal(nav.destination('/app?view=monitors&page=2','/'),'/app?view=monitors&page=2');
assert.equal(nav.destination('/app/monitors/1?source=dashboard','/'),'/app/monitors/1?source=dashboard');
assert.equal(nav.destination('/prefix/app?view=admin','/prefix/'),'/prefix/app?view=admin');
assert.equal(nav.destination('/app?view=admin','/prefix/'),'/prefix/app/dashboard');
nav.activate('dashboard');assert.equal(links[0].attributes['aria-current'],'page');assert.ok(!links[1].attributes['aria-current']);
nav.activate('monitors');assert.ok(!links[0].attributes['aria-current']);assert.equal(links[1].attributes['aria-current'],'page');
nav.init({user:{username:'<img>',role:'user'},csrf:'fixture-token',active:'monitors'});
assert.equal(node('navUsername').textContent,'<img>');
(async()=>{
 await node('navLogout').onclick();assert.equal(request.url,'https://example.test/api/auth/logout');assert.equal(request.options.headers['X-CSRF-Token'],'fixture-token');assert.equal(context.location.replaced,'/?auth=login');
 node('selfNewPassword').value='one';node('selfConfirmPassword').value='two';request=null;
 await node('selfPasswordForm').onsubmit({preventDefault(){}});assert.equal(request,null);assert.equal(node('selfPasswordMessage').textContent,'两次输入的新密码不一致');
 events.pageshow({persisted:true});assert.ok(context.location.reloaded);
 console.log('NAVIGATION default/deep-link/origin/prefix/active/logout/password-validation/session=PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
