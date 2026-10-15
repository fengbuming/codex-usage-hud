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
const checkbox = (value, checked=false) => ({value, checked});
const listNode = {innerHTML:'',_checks:null,querySelectorAll(){
  if(this._checks)return this._checks;
  const out=[];
  const re=/value="([^"]*)"([^>]*)>/g;
  let m;
  while((m=re.exec(this.innerHTML))){const option={hidden:false};out.push({value:m[1],checked:m[2].includes('checked'),closest(){return option;}});}
  return out;
}};
const summary={textContent:'',setAttribute(){}};
const empty={hidden:false,textContent:''},selectAll={},clear={};
const panel={matches(){return false;}};
const container = {dataset:{},querySelector(selector){
  if(selector==='summary'||selector.includes('models-summary'))return summary;
  if(selector.includes('models-panel'))return panel;
  if(selector.includes('models-toggle'))return summary;
  if(selector.includes('models-list'))return listNode;
  if(selector.includes('models-empty'))return empty;
  if(selector.includes('models-select-all'))return selectAll;
  if(selector.includes('models-clear'))return clear;
  if(selector.includes('models-refresh'))return button;
  return null;
},querySelectorAll(){return listNode.querySelectorAll();}};
const statusNode = {textContent:'',classList:{add(){},remove(){}}};
const button = {disabled:true};
const layer = {querySelector(selector) {
  if(selector.includes('connectivity-status')) return statusNode;
  if(selector.includes('models-refresh')) return button;
  if(selector.includes('config-models')) return container;
},querySelectorAll(selector){return listNode.querySelectorAll(selector);}};
let currentLayer=layer;
const pricingWorkflowState={providerModelsFetching:true,providerModelsRequestId:'one',providerModelsDialog:layer};
function providerConfigDialogLayer(){return currentLayer;}
function showProviderChatTest(){}
function escapeHtml(text){return String(text).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}
''' + SETTINGS[start:end] + r'''
applyProviderConnectivityStatus({requestId:'old',providerConnected:true,models:['wrong']});
assert.equal(listNode.innerHTML,'');assert.equal(button.disabled,true);
applyProviderConnectivityStatus({requestId:'one',providerConnected:true,models:['glm-5.3','glm-5.3']});
assert.equal(button.disabled,false);
assert.equal((listNode.innerHTML.match(/value="glm-5.3"/g)||[]).length,1);
assert.match(listNode.innerHTML,/data-provider-config-model-option/);
assert.deepEqual(providerDesktopModelSelectionValues(layer),[]);
assert.equal(summary.textContent,'选择模型');
const selectedRow={hidden:false};
listNode._checks=[{...checkbox('glm-5.3',true),closest(){return selectedRow;}}];
syncProviderDesktopModelPicker(layer);
const previousHtml=listNode.innerHTML;
pricingWorkflowState.providerModelsFetching=true;
applyProviderConnectivityStatus({requestId:'one',kind:'error',message:'offline'});
assert.equal(listNode.innerHTML,previousHtml);
assert.equal(summary.textContent,'模型 · 1');
pricingWorkflowState.providerModelsFetching=true;
applyProviderConnectivityStatus({requestId:'one',providerConnected:true,models:['glm-5.3','glm-5.4']});
listNode._checks=null;
assert.match(listNode.innerHTML,/value="glm-5.3" checked/);
assert.match(listNode.innerHTML,/value="glm-5.4"/);
assert.match(listNode.innerHTML,/data-provider-config-model-default="true"/);
assert.deepEqual(providerDesktopModelSelectionValues(layer),['glm-5.3']);
listNode._checks=listNode.querySelectorAll();
listNode._checks.forEach(node=>{node.checked=false;});
pricingWorkflowState.providerModelsFetching=true;
applyProviderConnectivityStatus({requestId:'one',providerConnected:true,models:['glm-5.3','glm-5.4']});
listNode._checks=null;
assert.deepEqual(providerDesktopModelSelectionValues(layer),[]);
assert.equal(summary.textContent,'选择模型');
currentLayer={};pricingWorkflowState.providerModelsFetching=true;listNode.innerHTML='keep';
applyProviderConnectivityStatus({requestId:'one',providerConnected:true,models:['stale']});
assert.equal(listNode.innerHTML,'keep');
assert(!providerDesktopModelOptionsHtml(['<img src=x>'],[]).includes('<img'));
''')


def test_provider_model_list_attaches_every_selected_model_to_native_picker():
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
const liveProps={model:'gpt-6.1-sol',reasoningEffort:'max',models:[nativeModel],modelOptions:[],onSelectModel:(...args)=>calls.push(args)};
const root={memoizedProps:{conversationId:'10000000-0000-4000-8000-000000000001'},return:null};
root.stateNode={current:root};
const fiber={memoizedProps:liveProps,return:root};
const trigger=new Node('');trigger.__reactFiber$test=fiber;trigger.parentElement=new Node();
trigger.querySelector=()=>null;
const nativeItem=new Node('6.1 Sol',{role:'menuitemradio'});nativeItem.__reactFiber$test=fiber;
const menu=new Node();menu.children=[nativeItem];nativeItem.parentElement=menu;
const payload={activeSessionProvider:'cheaprouter',settings:{provider_registry:{cheaprouter:{desktopModel:'glm-5.3-flash',desktopModels:['glm-5.3-flash','glm-5.3','deepseek-v4']}}}};
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
assert.deepEqual(menu.children.map(n=>n.textContent),['glm-5.3-flash','glm-5.3','deepseek-v4','6.1 Sol']);
listeners.click({type:'click',target:menu.children[1],preventDefault(){},stopPropagation(){}});
assert.deepEqual(calls,[['glm-5.3','low']]);
payload.settings.provider_registry.cheaprouter.desktopModels=['deepseek-v4'];
payload.settings.provider_registry.cheaprouter.desktopModel='deepseek-v4';
domain.apply();
assert.deepEqual(menu.children.map(n=>n.textContent),['deepseek-v4','6.1 Sol']);
''')


def test_provider_model_dropdown_preserves_empty_selection_and_filters_bulk_actions():
    start = SETTINGS.index("      function providerDesktopModelSelection(")
    end = SETTINGS.index("      function showProviderChatTest(", start)
    run_js(r'''
const assert = require('node:assert/strict');
const options = (values, checked) => values.map((value) => ({value, checked: checked.includes(value)}));
function fakeLayer(checks) {
  return {
    querySelectorAll(selector) { return selector.includes('model-option') ? checks : []; },
  };
}
function escapeHtml(text){return String(text).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}
global.document={createElement(){return {textContent:'',remove(){this.removed=true;}};}};
''' + SETTINGS[start:end] + r'''
const emptyHtml = providerDesktopModelOptionsHtml([], []);
assert.equal(emptyHtml, '');
const html = providerDesktopModelOptionsHtml(['a', 'b', 'c'], ['b', 'c']);
assert.equal((html.match(/data-provider-config-model-option=""/g)||[]).length, 3);
assert.match(html, /value="b" checked/);
assert.match(html, /value="c" checked/);
assert.equal((html.match(/data-provider-config-model-default="true"/g)||[]).length, 1);
assert.equal((html.match(/<small>默认<\/small>/g)||[]).length, 1);
const noneChecked = providerDesktopModelOptionsHtml(['a'], []);
assert(!noneChecked.includes('data-provider-config-model-default'));
assert.deepEqual(providerDesktopModelSelection({desktopModels:['x','y']}), ['x','y']);
assert.deepEqual(providerDesktopModelSelection({desktopModel:'z'}), ['z']);
assert.deepEqual(providerDesktopModelSelection(null), []);
assert.deepEqual(providerDesktopModelSelectionValues(fakeLayer(options(['a','b','c'], ['a','c']))), ['a','c']);
assert(!providerDesktopModelOptionsHtml(['<img src=x>'], ['<img src=x>']).includes('<img'));
// 徽章刷新：首个勾选项获得「默认」，取消全部勾选后徽章清空
function fakeOption(value, checked) {
  const option = {attrs:new Map(),badge:null,
    toggleAttribute(name,on){on?this.attrs.set(name,'true'):this.attrs.delete(name);},
    querySelector(sel){return sel==='small'?this.badge:null;},
    appendChild(node){node.remove=()=>{this.badge=null;};this.badge=node;}};
  const input = {value, checked, closest(){return option;}};
  return {input, option};
}
const items = [fakeOption('a',true), fakeOption('b',true), fakeOption('c',false)];
const listStub = {querySelectorAll(){return items.map(i=>i.input);}};
syncProviderDesktopModelBadges(listStub);
assert.equal(items[0].option.attrs.get('data-provider-config-model-default'),'true');
assert.equal(items[0].option.badge.textContent,'默认');
assert(!items[1].option.attrs.has('data-provider-config-model-default'));
items[0].input.checked=false;
syncProviderDesktopModelBadges(listStub);
assert(!items[0].option.attrs.has('data-provider-config-model-default'));
assert.equal(items[0].option.badge,null);
assert.equal(items[1].option.attrs.get('data-provider-config-model-default'),'true');
// 下拉框汇总选择；清空后保留全部选项，搜索全选只影响匹配项。
const summary={textContent:'',attrs:{},setAttribute(key,value){this.attrs[key]=value;}};
const search={value:''},empty={},selectAll={},clear={};
const panel={open:true,matches(){return this.open;}};
const picker={dataset:{},querySelector(sel){
  if(sel==='summary'||sel.includes('models-summary'))return summary;
  if(sel.includes('models-panel'))return panel;
  if(sel.includes('models-toggle'))return summary;
  if(sel.includes('models-search'))return search;
  if(sel.includes('models-empty'))return empty;
  if(sel.includes('models-select-all'))return selectAll;
  if(sel.includes('models-clear'))return clear;
  return null;
},querySelectorAll(){return items.map(i=>i.input);}};
const pickerLayer={querySelector(sel){
  if(sel.includes('models-list'))return listStub;
  if(sel==='[data-provider-config-models=""]')return picker;
  return null;
},querySelectorAll(){return items.map(i=>i.input);}};
syncProviderDesktopModelPicker(pickerLayer);
assert.equal(summary.textContent,'模型 · 1');
assert.equal(summary.attrs['aria-expanded'],'true');
setProviderDesktopModelsChecked(pickerLayer,false);
assert.equal(summary.textContent,'选择模型');
assert.equal(panel.open,true);
assert.equal(empty.hidden,true);
assert.equal(clear.disabled,true);
assert(items.every(i=>i.option.badge===null));
setProviderDesktopModelsChecked(pickerLayer,true);
assert(items.every(i=>i.input.checked===true));
assert.equal(items[0].option.attrs.get('data-provider-config-model-default'),'true');
assert.equal(summary.textContent,'模型 · 3');
assert.equal(selectAll.disabled,true);
setProviderDesktopModelsChecked(pickerLayer,false);
assert(items.every(i=>i.input.checked===false));
search.value=' B ';
syncProviderDesktopModelPicker(pickerLayer);
assert.equal(selectAll.textContent,'全选搜索结果');
assert.deepEqual(items.map(i=>i.option.hidden),[true,false,true]);
setProviderDesktopModelsChecked(pickerLayer,true);
assert.deepEqual(providerDesktopModelSelectionValues(pickerLayer),['b']);
assert.equal(items[1].option.badge.textContent,'默认');
search.value='no match';
syncProviderDesktopModelPicker(pickerLayer);
assert.equal(empty.hidden,false);
assert.equal(empty.textContent,'没有匹配的模型');
assert.equal(selectAll.disabled,true);
// 搜索时取消全选仍能清空被过滤隐藏的选择。
setProviderDesktopModelsChecked(pickerLayer,false);
assert.deepEqual(providerDesktopModelSelectionValues(pickerLayer),[]);
''')


def test_provider_model_button_fetches_on_open_and_invalidates_changed_credentials():
    start = SETTINGS.index("      function providerDesktopModelSelection(")
    end = SETTINGS.index("      function showProviderChatTest(", start)
    run_js(r'''
const assert=require('node:assert/strict');
const element=(props={})=>({events:{},attrs:{},dataset:{},style:{},
  addEventListener(type,handler){(this.events[type]??=[]).push(handler);},
  emit(type,event={}){for(const handler of this.events[type]||[])handler(event);},
  setAttribute(key,value){this.attrs[key]=value;},focus(){this.focused=true;},...props});
const toggle=element({getBoundingClientRect(){return {top:80,bottom:111,right:620};}});
const panel=element({open:false,matches(){return this.open;},hidePopover(){this.open=false;}});
const search=element({value:''}),list=element({querySelectorAll(){return [];}});
const summary=element(),refresh=element(),selectAll=element(),clear=element(),empty=element();
const nodes={
  '[data-provider-config-models-toggle=""]':toggle,
  '[data-provider-config-models-panel=""]':panel,
  '[data-provider-config-models-search=""]':search,
  '[data-provider-config-models-list=""]':list,
  '[data-provider-config-models-summary=""]':summary,
  '[data-provider-config-models-refresh=""]':refresh,
  '[data-provider-config-models-select-all=""]':selectAll,
  '[data-provider-config-models-clear=""]':clear,
  '[data-provider-config-models-empty=""]':empty,
};
const picker=element({querySelector(selector){return nodes[selector]||null;},querySelectorAll(){return [];}});
const fields=Object.fromEntries(['base_url','api_key','use_proxy','proxy_port'].map(name=>[name,element()]));
const status={textContent:'previous connection status'};
const layer={querySelector(selector){
  if(selector==='[data-provider-config-models=""]')return picker;
  if(selector==='[data-provider-config-connectivity-status=""]')return status;
  const match=selector.match(/data-provider-config-field="([^"]+)"/);
  return match?fields[match[1]]:null;
},querySelectorAll(){return [];}};
const pricingWorkflowState={providerModelsFetching:false,providerModelsDialog:null};
global.window={innerWidth:640,innerHeight:480};
let requests=0;
function testProviderConnectivityFromDialog(){requests++;pricingWorkflowState.providerModelsFetching=true;pricingWorkflowState.providerModelsDialog=layer;}
''' + SETTINGS[start:end] + r'''
bindProviderDesktopModelPicker(layer);
panel.emit('beforetoggle',{newState:'closed'});
assert.equal(requests,0);
panel.emit('beforetoggle',{newState:'open'});
assert.equal(requests,1);
assert.equal(panel.style.width,'380px');
assert.equal(panel.style.left,'240px');
assert.equal(panel.style.top,'116px');
picker.dataset.modelsLoaded='true';
panel.emit('beforetoggle',{newState:'open'});
assert.equal(requests,1,'reopening uses the fetched model list');
panel.emit('toggle',{newState:'open'});
assert.equal(toggle.attrs['aria-expanded'],'true');
assert.equal(search.focused,true);
fields.api_key.emit('input');
assert.equal(picker.dataset.modelsLoaded,'false');
assert.equal(pricingWorkflowState.providerModelsFetching,false);
assert.equal(pricingWorkflowState.providerModelsDialog,null,'old in-flight response loses its dialog');
assert.equal(status.textContent,'');
panel.emit('beforetoggle',{newState:'open'});
assert.equal(requests,2,'changed credentials trigger a new fetch');
panel.emit('toggle',{newState:'closed'});
assert.equal(toggle.attrs['aria-expanded'],'false');
panel.open=true;
let prevented=false,stopped=false;
panel.emit('keydown',{key:'Escape',preventDefault(){prevented=true;},stopPropagation(){stopped=true;}});
assert.equal(panel.open,false);
assert.equal(toggle.focused,true);
assert(prevented&&stopped,'Escape is handled before the parent application');
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
