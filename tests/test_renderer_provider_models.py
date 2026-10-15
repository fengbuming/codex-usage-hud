import subprocess

from codex_usage_hud.renderer_assets.model_picker import TEXT as MODEL_PICKER
from codex_usage_hud.renderer_assets.settings_shell import TEXT as SETTINGS


def run_js(script):
    result = subprocess.run(["node", "--input-type=commonjs"], input=script,
        capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


def test_model_list_preserves_selection_default_and_request_identity():
    start = SETTINGS.index("      function applyProviderConnectivityStatus(")
    end = SETTINGS.index("      function showProviderChatTest(", start)
    run_js(r'''
const assert = require('node:assert/strict');
const select = {value:'glm-5.3-flash',innerHTML:'',hidden:true};
const statusNode = {textContent:'',classList:{add(){},remove(){}}};
const button = {disabled:true};
const layer = {querySelector(selector) {
  if(selector.includes('connectivity-status')) return statusNode;
  if(selector.includes('test-connectivity')) return button;
  if(selector.includes('config-models')) return select;
}};
let currentLayer=layer;
const pricingWorkflowState={providerModelsFetching:true,providerModelsRequestId:'one',providerModelsDialog:layer};
function providerConfigDialogLayer(){return currentLayer;}
function showProviderChatTest(){}
function escapeHtml(text){return String(text).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}
''' + SETTINGS[start:end] + r'''
applyProviderConnectivityStatus({requestId:'old',providerConnected:true,models:['wrong']});
assert.equal(select.innerHTML,'');assert.equal(button.disabled,true);
applyProviderConnectivityStatus({requestId:'one',providerConnected:true,models:['glm-5.3','glm-5.3']});
assert.equal(button.disabled,false);assert.equal(select.hidden,false);
assert.equal(select.value,'glm-5.3-flash');
assert.match(select.innerHTML,/value="glm-5.3-flash" selected/);
assert.equal((select.innerHTML.match(/value="glm-5.3"/g)||[]).length,1);
assert.match(select.innerHTML,/value=""/);assert(!select.innerHTML.includes('disabled'));
pricingWorkflowState.providerModelsFetching=true;
applyProviderConnectivityStatus({requestId:'one',kind:'error',message:'offline'});
assert.equal(select.value,'glm-5.3-flash');assert.match(select.innerHTML,/glm-5.3-flash/);
select.value='';pricingWorkflowState.providerModelsFetching=true;
applyProviderConnectivityStatus({requestId:'one',providerConnected:true,models:['glm-5.3-flash']});
assert.equal(select.value,'');assert.match(select.innerHTML,/value="" selected/);
currentLayer={};pricingWorkflowState.providerModelsFetching=true;select.innerHTML='keep';
applyProviderConnectivityStatus({requestId:'one',providerConnected:true,models:['stale']});
assert.equal(select.innerHTML,'keep');
assert(!providerDesktopModelOptionsHtml(['<img src=x>'],'').includes('<img'));
''')


def test_custom_model_label_and_menu_follow_committed_fiber_and_provider():
    factory = MODEL_PICKER.split('  const modelPickerDomain = ctx.domains.register(', 1)[0]
    run_js(r'''
const assert=require('node:assert/strict');
class Node {
  constructor(text='', attrs={}) {this.textContent=text;this.attrs=new Map(Object.entries(attrs));this.isConnected=true;this.children=[];}
  getAttribute(key){return this.attrs.get(key)??null;}
  setAttribute(key,value){this.attrs.set(key,String(value));}
  removeAttribute(key){this.attrs.delete(key);}
  hasAttribute(key){return this.attrs.has(key);}
  cloneNode(){return new Node(this.textContent,Object.fromEntries(this.attrs));}
  remove(){this.isConnected=false;if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(n=>n!==this);}
  closest(){return this;}
  insertBefore(node, ref){node.parentElement=this;const i=this.children.indexOf(ref);this.children.splice(i<0?this.children.length:i,0,node);}
  querySelector(selector){const match=selector.match(/\[([^=]+)="([^"]+)"\]/);return match?this.children.find(n=>n.getAttribute(match[1])===match[2])||null:null;}
}
const nativeModel={model:'gpt-6.1-sol',displayName:'GPT-6.1-Sol',inputModalities:['text']};
const calls=[];
const liveProps={model:'glm-5.3-flash',reasoningEffort:'low',models:[nativeModel],modelOptions:[],onSelectModel:(...args)=>calls.push(args)};
const liveRoot={memoizedProps:{conversationId:'10000000-0000-4000-8000-000000000001'},return:null};
const staleRoot={memoizedProps:liveRoot.memoizedProps,return:null};
liveRoot.stateNode=staleRoot.stateNode={current:liveRoot};
const liveFiber={memoizedProps:liveProps,return:liveRoot};
const staleFiber={memoizedProps:{...liveProps,model:'gpt-6.1-sol',reasoningEffort:'max'},return:staleRoot,alternate:liveFiber};
const label=new Node('自定义');
const trigger=new Node('');trigger.__reactFiber$test=staleFiber;trigger.parentElement=new Node();
trigger.querySelector=()=>label;
const nativeItem=new Node('6.1 Sol',{role:'menuitemradio'});nativeItem.__reactFiber$test=staleFiber;
const menu=new Node();menu.children=[nativeItem];nativeItem.parentElement=menu;
const payload={activeSessionProvider:'cheaprouter',settings:{provider_registry:{cheaprouter:{desktopModel:'glm-5.3-flash'},other:{desktopModel:''}}}};
global.document={querySelectorAll(selector){
  if(selector.includes('button['))return [trigger];
  if(selector.includes('[role='))return menu.children;
  return menu.children.filter(n=>n.hasAttribute('data-codex-usage-hud-model-option'));
},querySelector(){return null;}};
global.window={CSS:{escape:String}};
const listeners={};const ctx={state:{payload:()=>payload},frames:{cancel(){},schedule(_owner,fn){fn();return 1;}},
 lifecycle:{listen(_owner,_target,type,fn){listeners[type]=fn;},clearTimeout(){},timeout(){return 1;}},observers:{clear(){}}};
const normalize=value=>String(value||'').replace(/\s+/g,' ').trim(),visible=node=>!!node?.isConnected,cssEscape=String;
const codexModelPickerCatalog=[];
const modelPickerPatchHandlerName='patch',modelPickerPatchRafName='raf',modelPickerPatchTimersName='timers',modelPickerSelectionName='selection';
''' + factory + r'''
const domain=createModelPickerDomain(ctx,{normalize,visible,cssEscape});domain.install();domain.apply();
assert.equal(domain.selectionContext().selectedModel,'glm-5.3-flash');
assert.equal(label.textContent,'glm-5.3-flash');assert.equal(label.getAttribute('title'),'glm-5.3-flash');
assert.deepEqual(menu.children.map(n=>n.textContent),['glm-5.3-flash','6.1 Sol']);
listeners.click({type:'click',target:menu.children[0],preventDefault(){},stopPropagation(){}});
assert.deepEqual(calls,[['glm-5.3-flash','low']]);
payload.activeSessionProvider='other';liveProps.model='gpt-6.1-sol';liveProps.reasoningEffort='max';label.textContent='6.1 Sol';
domain.apply();assert.equal(label.textContent,'6.1 Sol');assert.deepEqual(menu.children.map(n=>n.textContent),['6.1 Sol']);
payload.activeSessionProvider='cheaprouter';liveProps.model='glm-5.3-flash';label.textContent='自定义';domain.apply();
domain.dispose();assert.equal(label.textContent,'自定义');assert.equal(label.getAttribute('title'),null);
assert.deepEqual(menu.children.map(n=>n.textContent),['6.1 Sol']);
''')


def test_save_applies_to_next_turn_of_same_provider_and_restores_native_default():
    start = SETTINGS.index('      function desktopModelReasoningEffort(')
    end = SETTINGS.index('      function renderCodexProviderSwitchMenu(', start)
    run_js(r'''
const assert=require('node:assert/strict');
const id='10000000-0000-4000-8000-000000000001';
const config={model_provider:'cheaprouter',model:'glm-5.3-flash',model_reasoning_effort:'max'};
let selection={conversationId:id},actualProvider='cheaprouter',changeSelectionOnRead=false;
const conversation={latestModel:'gpt-6.1-sol',latestReasoningEffort:'max'};
const updates=[];const order=[];
const catalog=[{model:'gpt-6.1-sol',isDefault:true,defaultReasoningEffort:'medium',supportedReasoningEfforts:[{reasoningEffort:'max'},{reasoningEffort:'medium'}]}];
const manager={async sendRequest(method){order.push(method);
 if(method==='config/read')return {config:{...config}};
 if(method==='model/list')return {data:catalog};
 if(method==='thread/read'){if(changeSelectionOnRead)selection={conversationId:'another'};return {thread:{id,modelProvider:actualProvider,status:{type:'active'}}};}
 throw Error('Unexpected RPC '+method);},
 async setDefaultModelConfig(model,effort){config.model=model;config.model_reasoning_effort=effort;return {status:'ok'};},
 async getConversation(){return {...conversation};},
 async updateThreadSettingsForNextTurn(threadId,settings){updates.push({threadId,...settings});conversation.latestModel=settings.model;conversation.latestReasoningEffort=settings.effort;return true;},
 async clearPrewarmedThreads(){order.push('clear');}};
const scope={queryClient:{async invalidateQueries(){order.push('cache');}}};
const codexProviderSwitchMenuState={pendingRequestId:'one'};
const modelPickerDomain={selectionContext:()=>selection,apply(){order.push('label');}};
async function resolveCodexProviderRuntime(){return {manager,scope};}
''' + SETTINGS[start:end] + r'''
(async()=>{
 await refreshCodexProviderRuntime('cheaprouter','one',{modelConfiguration:{providerId:'cheaprouter',customModel:'glm-5.3-flash',model:'glm-5.3-flash',reasoningEffort:'max',modelChanged:true}});
 assert.deepEqual(updates,[{threadId:id,model:'glm-5.3-flash',effort:'low'}]);
 assert.equal(config.model_reasoning_effort,'low');assert(order.indexOf('cache')<order.indexOf('clear'));assert(!order.includes('turn/interrupt'));
 await refreshCodexProviderRuntime('cheaprouter','one',{modelUpdates:[{providerId:'cheaprouter',customModel:'glm-5.3-flash',model:'glm-5.3-flash'}]});
 assert.equal(updates.length,1,'unchanged model must not enqueue another update');
 actualProvider='different';conversation.latestModel='gpt-6.1-sol';
 await refreshCodexProviderRuntime('', '', {modelUpdates:[{providerId:'cheaprouter',customModel:'glm-5.3-flash',model:'glm-5.3-flash'}]});assert.equal(updates.length,1);
 actualProvider='cheaprouter';changeSelectionOnRead=true;
 await refreshCodexProviderRuntime('', '', {modelUpdates:[{providerId:'cheaprouter',customModel:'glm-5.3-flash',model:'glm-5.3-flash'}]});assert.equal(updates.length,1);
 changeSelectionOnRead=false;selection={conversationId:id};conversation.latestModel='glm-5.3-flash';
 config.model='gpt-6.1-sol';config.model_reasoning_effort='max';
 await refreshCodexProviderRuntime('', '', {modelUpdates:[{providerId:'cheaprouter',customModel:'',model:'gpt-6.1-sol',reasoningEffort:'max',preferenceChanged:true}],modelConfiguration:{providerId:'cheaprouter',customModel:'',model:'gpt-6.1-sol',reasoningEffort:'max',restoredNativeModel:true}});
 assert.deepEqual(updates.at(-1),{threadId:id,model:'gpt-6.1-sol',effort:'max'});
})().catch(e=>{console.error(e);process.exitCode=1;});
''')


def test_newer_model_save_supersedes_pending_model_refresh():
    start = SETTINGS.index('      function desktopModelReasoningEffort(')
    end = SETTINGS.index('      function renderCodexProviderSwitchMenu(', start)
    run_js(r'''
const assert=require('node:assert/strict');let release,lists=0;const applied=[];
const id='10000000-0000-4000-8000-000000000001';let currentModel='old';
const manager={async sendRequest(method){
 if(method==='config/read')return {config:{model_provider:'cheaprouter'}};
 if(method==='model/list'){lists++;if(lists===1)return new Promise(r=>{release=r;});return {data:[]};}
 if(method==='thread/read')return {thread:{modelProvider:'cheaprouter'}};},
 async getConversation(){return {latestModel:currentModel};},
 async updateThreadSettingsForNextTurn(_id,{model}){applied.push(model);currentModel=model;return true;},async clearPrewarmedThreads(){}};
const scope={queryClient:{async invalidateQueries(){}}};
const modelPickerDomain={selectionContext:()=>({conversationId:id}),apply(){}};
const codexProviderSwitchMenuState={};async function resolveCodexProviderRuntime(){return {manager,scope};}
''' + SETTINGS[start:end] + r'''
(async()=>{
 const old=refreshCodexProviderRuntime('','',{modelUpdates:[{providerId:'cheaprouter',customModel:'old-model',model:'old-model'}]});
 const rejected=assert.rejects(old,/替代/);await new Promise(setImmediate);
 await refreshCodexProviderRuntime('','',{modelUpdates:[{providerId:'cheaprouter',customModel:'new-model',model:'new-model'}]});
 release({data:[]});await rejected;assert.deepEqual(applied,['new-model']);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')
