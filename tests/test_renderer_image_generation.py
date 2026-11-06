"""Execute the native-picker image switch against a small DOM/Fiber fixture."""

import subprocess

from codex_usage_hud.renderer_assets.model_picker import TEXT


def test_image_switch_matches_header_position_and_scopes_async_acknowledgements():
    factory = TEXT.split('  const modelPickerDomain = ctx.domains.register(', 1)[0]
    script = r'''
const assert = require('node:assert/strict');
const A='10000000-0000-4000-8000-000000000001', B='10000000-0000-4000-8000-000000000002';
let writes=0;
class Node {
  constructor(text='',attrs={}) {this.textContent=text;this.attrs=new Map(Object.entries(attrs));this.style={position:''};this.children=[];this.isConnected=true;}
  getAttribute(k){return this.attrs.get(k)??null;}
  setAttribute(k,v){writes++;this.attrs.set(k,String(v));}
  removeAttribute(k){this.attrs.delete(k);}
  hasAttribute(k){return this.attrs.has(k);}
  cloneNode(){return new Node(this.textContent,Object.fromEntries(this.attrs));}
  appendChild(n){n.parentElement=this;this.children.push(n);return n;}
  insertBefore(n,ref){n.parentElement=this;const i=this.children.indexOf(ref);this.children.splice(i<0?this.children.length:i,0,n);}
  closest(selector){
    if(selector.includes('role='))return popup;
    for(const attr of ['data-codex-usage-hud-image-toggle','data-codex-usage-hud-model-option','data-codex-usage-hud-reasoning-option'])
      if(selector.includes(attr)&&this.hasAttribute(attr))return this;
    return null;
  }
  remove(){this.isConnected=false;if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(n=>n!==this);}
  querySelector(selector){
    if(selector==='[data-image-mode-thumb]')return this.thumb;
    if(selector==='[data-image-mode-track]')return this.track;
    const match=selector.match(/\[([^=]+)="([^"]+)"\]/);
    return match?this.children.find(n=>n.getAttribute(match[1])===match[2])||null:null;
  }
  set innerHTML(value){this.html=value;this.thumb={style:{}};this.track={style:{}};}
}
const nativeModel={model:'gpt-6.1-sol',displayName:'GPT-6.1-Sol'};
const selected=[];
const props={model:'gpt-6.1-sol',reasoningEffort:'high',models:[nativeModel],modelOptions:[],onSelectModel(model,effort){props.model=model;selected.push([model,effort]);}};
const root={memoizedProps:{conversationId:A},return:null};root.stateNode={current:root};
const fiber={memoizedProps:props,return:root};
const trigger=new Node();trigger.__reactFiber$test=fiber;trigger.parentElement=new Node();
const nativeItem=new Node('6.1 Sol',{role:'menuitemradio'});nativeItem.__reactFiber$test=fiber;
const modelList=new Node();modelList.appendChild(nativeItem);
const popup=new Node('',{role:'menu'});
const header=new Node('高');popup.appendChild(header);
const slider=new Node('',{'data-reasoning-slider':'true',role:'menuitem'});popup.appendChild(slider);
const commands=[];let resolveCommand, rejectCommand;
const payload={sessionId:A,activeSessionProvider:'alpha',settings:{default_provider:'alpha',provider_gateway:{active:true,default_provider:'alpha',image_sessions:{}},
  provider_registry:{alpha:{desktopModels:['gpt-6.1-sol','gpt-image-2','gpt-image-2.5']}}}};
global.window={CSS:{escape:String}};
global.getComputedStyle=()=>({position:'static'});
global.document={
  createElement(){return new Node();},
  querySelector(selector){return selector.includes('data-reasoning-slider')?slider:null;},
  querySelectorAll(selector){
    if(selector.includes('button['))return [trigger];
    if(selector.includes('[role='))return [...modelList.children,slider];
    if(selector.includes('image-toggle'))return popup.children.filter(n=>n.hasAttribute('data-codex-usage-hud-image-toggle'));
    return modelList.children.filter(n=>n.hasAttribute('data-codex-usage-hud-model-option'));
  }
};
const handlers={};
const ctx={state:{payload:()=>payload},domains:{get(){return {codexGatewayCommand(command){commands.push(command);return new Promise((resolve,reject)=>{resolveCommand=resolve;rejectCommand=reject;});}};}},
  frames:{cancel(){},schedule(_owner,fn){fn();return 1;}},observers:{clear(){}},
  lifecycle:{listen(_o,_t,type,fn,capture=false){(handlers[type]??=[]).push({fn,capture});},clearTimeout(){},timeout(){return 1;}}};
const normalize=x=>String(x||'').trim(),visible=n=>!!n?.isConnected,cssEscape=String;
const codexModelPickerCatalog=[];
const modelPickerPatchHandlerName='patch',modelPickerPatchRafName='raf',modelPickerPatchTimersName='timers',modelPickerSelectionName='selection';
''' + factory + r'''
async function tick(){await new Promise(resolve=>setImmediate(resolve));}
function click(node,key){
 const e={type:key?'keydown':'click',key:key||'',target:node,preventDefault(){},stopPropagation(){}};
 for(const handler of handlers[e.type])if(handler.capture)handler.fn(e);
 // The native popup consumes bubbling; only capture handlers see the switch.
}
(async()=>{
 const domain=createModelPickerDomain(ctx,{normalize,visible,cssEscape});domain.install();domain.apply();
 const toggle=popup.querySelector('[data-codex-usage-hud-image-toggle="true"]');
 assert(toggle);assert.equal(toggle.getAttribute('role'),'switch');
 assert.match(toggle.style.cssText,/left:10px;top:7px/);
 assert.equal(popup.children[0],header);assert.equal(popup.children[1],slider);
 assert.equal(toggle.getAttribute('aria-checked'),'false');
 const count=writes;domain.apply();assert.equal(writes,count,'unchanged state must not rewrite DOM');
 click(toggle);click(toggle);assert.equal(commands.length,1,'duplicate clicks during save must not toggle twice');
 assert.deepEqual(commands[0],{action:'imageGenerationSet',sessionId:A,enabled:true,model:'gpt-image-2'});
 resolveCommand({sessionId:A,provider:'alpha',enabled:true,model:'gpt-image-2'});await tick();
 assert.equal(toggle.getAttribute('aria-checked'),'true');assert.equal(props.model,'gpt-image-2');
 click(toggle,' ');resolveCommand({sessionId:A,provider:'alpha',enabled:false,model:''});await tick();
 assert.equal(props.model,'gpt-6.1-sol');assert.equal(toggle.getAttribute('aria-checked'),'false');
 assert.deepEqual(selected.at(-1),['gpt-6.1-sol','high']);
 // A delayed save for A may persist A, but must never select a model in B.
 click(toggle);root.memoizedProps.conversationId=B;payload.sessionId=B;domain.apply();const calls=selected.length;
 resolveCommand({sessionId:A,provider:'alpha',enabled:true,model:'gpt-image-2'});await tick();
 assert.equal(selected.length,calls);assert.equal(toggle.getAttribute('aria-checked'),'false');
 assert(payload.settings.provider_gateway.image_sessions[A]);assert(!payload.settings.provider_gateway.image_sessions[B]);
 click(toggle);rejectCommand(new Error('额度查询失败'));await tick();
 assert.match(toggle.title,/额度查询失败/);assert.equal(toggle.getAttribute('aria-checked'),'false');
 payload.settings.provider_gateway.active=false;domain.apply();click(toggle);assert.equal(commands.length,4);
 assert.equal(toggle.getAttribute('aria-disabled'),'true');
 // A fresh composer has no conversation UUID until its first send.
 payload.settings.provider_gateway.active=true;payload.sessionId='n/a';payload.newSession=true;
 root.memoizedProps.conversationId=null;props.model='gpt-image-2';domain.apply();
 assert.equal(toggle.getAttribute('aria-disabled'),'false');
 click(toggle);assert.deepEqual(commands.at(-1),{action:'imageGenerationDraft',enabled:true,model:'gpt-image-2',token:''});
 const token='a'.repeat(32),alias='hud-image/'+token+'/gpt-image-2';
 resolveCommand({draft:true,enabled:true,provider:'alpha',model:'gpt-image-2',nativeModel:alias});await tick();
 assert.equal(props.model,alias);assert.equal(toggle.getAttribute('aria-checked'),'true');
 click(toggle,'Enter');assert.equal(commands.at(-1).token,token);assert.equal(commands.at(-1).enabled,false);
 resolveCommand({draft:true,enabled:false});await tick();assert.equal(props.model,'gpt-6.1-sol');
 assert.equal(toggle.getAttribute('aria-checked'),'false');
 // The first request's UUID hands the mode over to the persisted chat and
 // removes the temporary native alias before another empty chat can inherit it.
 click(toggle);resolveCommand({draft:true,enabled:true,provider:'alpha',model:'gpt-image-2',nativeModel:alias});await tick();
 const C='10000000-0000-4000-8000-000000000003';
 root.memoizedProps.conversationId=C;payload.sessionId=C;payload.newSession=false;
 payload.settings.provider_gateway.image_sessions[C]={provider:'alpha',model:'gpt-image-2'};
 domain.apply();assert.equal(props.model,'gpt-image-2');assert.equal(toggle.getAttribute('aria-checked'),'true');
 root.memoizedProps.conversationId=null;payload.sessionId='n/a';payload.newSession=true;domain.apply();
 assert.equal(toggle.getAttribute('aria-checked'),'false');
 // A stale HUD selection cannot supply the current existing chat's provider.
 root.memoizedProps.conversationId=B;payload.sessionId=A;payload.activeSessionProvider='wrong';
 payload.newSession=false;domain.apply();
 assert.deepEqual(commands.at(-1),{action:'imageGenerationContext',sessionId:B});
 resolveCommand({sessionId:B,provider:'alpha'});await tick();
 assert.equal(toggle.getAttribute('aria-disabled'),'false');
 domain.dispose();assert.equal(toggle.isConnected,false);assert.equal(popup.style.position,'');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
