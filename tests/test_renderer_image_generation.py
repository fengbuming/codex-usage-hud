"""Select image models directly through the existing native model list."""

import subprocess

from codex_usage_hud.renderer_assets.model_picker import TEXT


def test_image_model_selection_needs_no_toggle_command_or_draft_alias():
    factory = TEXT.split('  const modelPickerDomain = ctx.domains.register(', 1)[0]
    script = r'''
const assert = require('node:assert/strict');
const A='10000000-0000-4000-8000-000000000001';
class Node {
  constructor(text='',attrs={}) {this.textContent=text;this.attrs=new Map(Object.entries(attrs));this.children=[];this.isConnected=true;}
  getAttribute(k){return this.attrs.get(k)??null;}
  setAttribute(k,v){this.attrs.set(k,String(v));}
  removeAttribute(k){this.attrs.delete(k);}
  hasAttribute(k){return this.attrs.has(k);}
  cloneNode(){return new Node(this.textContent,Object.fromEntries(this.attrs));}
  appendChild(n){n.parentElement=this;this.children.push(n);return n;}
  insertBefore(n,ref){n.parentElement=this;const i=this.children.indexOf(ref);this.children.splice(i<0?this.children.length:i,0,n);}
  closest(selector){
    for(const attr of ['data-codex-usage-hud-model-option','data-codex-usage-hud-reasoning-option'])
      if(selector.includes(attr)&&this.hasAttribute(attr))return this;
    return null;
  }
  remove(){this.isConnected=false;if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(n=>n!==this);}
  querySelector(selector){
    const match=selector.match(/\[([^=]+)="([^"]+)"\]/);
    return match?this.children.find(n=>n.getAttribute(match[1])===match[2])||null:null;
  }
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
const commands=[];
const payload={sessionId:A,activeSessionProvider:'alpha',settings:{default_provider:'alpha',provider_gateway:{active:true,default_provider:'alpha',image_sessions:{[A]:{provider:'alpha',model:'gpt-image-2'}}},
  provider_registry:{alpha:{desktopModels:['gpt-6.1-sol','gpt-image-2','gpt-image-2.5']}}}};
global.window={CSS:{escape:String}};
global.document={
  createElement(){throw new Error('image routing must not create controls');},
  querySelector(selector){return selector.includes('data-reasoning-slider')?slider:null;},
  querySelectorAll(selector){
    if(selector.includes('button['))return [trigger];
    if(selector.includes('[role='))return [...modelList.children,...popup.children.filter(n=>n.hasAttribute('role'))];
    return [...modelList.children,...popup.children].filter(n=>n.hasAttribute('data-codex-usage-hud-model-option')||n.hasAttribute('data-codex-usage-hud-reasoning-option'));
  }
};
const handlers={};
const ctx={state:{payload:()=>payload},domains:{get(){return {codexGatewayCommand(command){commands.push(command);throw new Error('no image mode command expected');}};}},
  frames:{cancel(){},schedule(_owner,fn){fn();return 1;}},observers:{clear(){}},
  lifecycle:{listen(_o,_t,type,fn,capture=false){(handlers[type]??=[]).push({fn,capture});},clearTimeout(){},timeout(){return 1;}}};
const normalize=x=>String(x||'').trim(),visible=n=>!!n?.isConnected,cssEscape=String;
const codexModelPickerCatalog=[];
const modelPickerPatchHandlerName='patch',modelPickerPatchRafName='raf',modelPickerPatchTimersName='timers',modelPickerSelectionName='selection';
''' + factory + r'''
function choose(model,key=''){
 const target=modelList.children.find(n=>n.getAttribute('data-codex-usage-hud-model-option')===model);
 assert(target,model+' is selectable');
 const event={type:key?'keydown':'click',key,target,preventDefault(){},stopPropagation(){}};
 for(const handler of handlers[event.type]||[])handler.fn(event);
}
const domain=createModelPickerDomain(ctx,{normalize,visible,cssEscape});domain.install();domain.apply();
assert.deepEqual(popup.children,[header,slider],'reasoning popup must retain its original controls');
assert.equal(selected.length,0,'legacy saved toggle must not change the native selection');
choose('gpt-image-2');assert.deepEqual(selected.at(-1),['gpt-image-2','low']);
assert.equal(props.model,'gpt-image-2');
// A new composer selects the real model ID without waiting for a UUID or an acknowledgement.
root.memoizedProps.conversationId=null;payload.sessionId='n/a';payload.newSession=true;
domain.apply();choose('gpt-image-2.5','Enter');assert.equal(props.model,'gpt-image-2.5');
// Selecting another model from an old saved draft never propagates its token.
props.model='hud-image/'+'a'.repeat(32)+'/gpt-image-2';domain.apply();
choose('gpt-image-2.5');assert.equal(props.model,'gpt-image-2.5');
// A text model chosen by the native picker remains selected despite legacy image state.
props.model='gpt-6.1-sol';domain.apply();assert.equal(props.model,'gpt-6.1-sol');
assert.deepEqual(commands,[]);
domain.dispose();assert.deepEqual(popup.children,[header,slider]);
assert.deepEqual(modelList.children,[nativeItem]);
'''
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
