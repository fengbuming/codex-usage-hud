"""Provider mismatch recovery requires explicit user consent after a native error."""

import subprocess

from codex_usage_hud.renderer_assets.settings_shell import TEXT


def test_mismatch_recovery_confirmation_and_native_retry(tmp_path):
    start = TEXT.index("      function codexProviderMismatchThreadId()")
    end = TEXT.index("      function startCodexProviderMigrate()", start)
    lifecycle_start = TEXT.index("      function clearDefaultProviderSwitchFeedback(")
    lifecycle_end = TEXT.index("      async function resolveCodexProviderRuntime()", lifecycle_start)
    script = r'''
const assert = require('node:assert/strict');
const id = '01a1a4a1-2f7e-7791-8443-8528822e3202';
const timers = new Map(); let serial = 0;
let activeThreadId=id;
let selectedRef=null;
const window = {location:{pathname:'/index.html', hash:''},getComputedStyle(node){return {position:node.position || 'fixed'};},
  setTimeout(fn, ms) {const n=++serial; timers.set(n,{fn,ms}); return n;},
  clearTimeout(n) {timers.delete(n);}};
let defaultProvider='crab-ai', threadProvider='custom', readsFail=false, submitFails=false;
let refreshFails=false, delayedRead=null, submitCount=0, retryCount=0, pending=null;
const reads=[], writes=[], layers=new Set(), cliProviders=[];
const codexProviderMismatchState={layer:null,context:null,attempt:null,checking:false,requestId:'',seen:new WeakMap()};
const codexProviderSwitchMenuState={pendingRequestId:'',pendingProvider:'',migration:null};
const settings={default_provider:'crab-ai'};
const settingsProviderDraft=null;
const rootId='hud';
function syncCodexCliQuickLaunchMenu() {}
function openCodexProviderMigrateDialog() {throw new Error('must not fork or migrate');}
function openCodexCliFromApplicationMenu(provider) {cliProviders.push(provider);}
function currentPayload() {return {sessionId:activeThreadId};}
function readActiveSessionRef() {return selectedRef;}
function hudSettingsFromPayload() {return settings;}
function providerDisplayName(_, provider) {return provider;}
function escapeHtml(value) {return String(value);}
function visible(node) {return node.shown !== false;}
function typedSettingsRequestId() {return 'switch-' + (++serial);}
const input={value:'Please continue', innerHTML:'Please continue'};
const button={isConnected:true,click(){retryCount++;capture();}};
const composer={isConnected:true,contains(n){return n===button || n===input;},
  querySelector(){return input;},querySelectorAll(selector){return selector.startsWith('button')?[button]:[];}};
function composerElement() {return composer;}
function activeSessionComposerSubmitButton(node) {return node===button && !button.disabled;}
function makeLayer() {return {dataset:{},isConnected:true,innerHTML:'', handlers:{},
  setAttribute(){}, querySelector(key){return {addEventListener:(_,fn)=>this.handlers[key]=fn};},
  remove(){layers.delete(this);this.isConnected=false;}};}
const document={createElement:makeLayer, body:{appendChild(node){layers.add(node);}}};
async function codexBridgeRequest(method) {
  reads.push(method);
  if (delayedRead) await delayedRead;
  if (readsFail) return {error:{message:'read unavailable'}};
  if (method==='thread/read') return {result:{thread:{modelProvider:threadProvider}}};
  if (method==='config/read') return {result:{config:{model_provider:defaultProvider}}};
  throw new Error('unexpected RPC mutation ' + method);
}
async function refreshCodexProviderRuntime(provider, requestId) {
  assert.equal(defaultProvider, provider);
  assert.equal(codexProviderSwitchMenuState.pendingRequestId, requestId);
  if (refreshFails) throw new Error('runtime not confirmed');
}
function submitSettingsCommand(command) {
  submitCount++;
  assert.equal(command.action,'providerSetDefault');
  if (submitFails) return false;
  writes.push(command.provider); defaultProvider=command.provider; pending=command; return true;
}
// The actual event target is inside the native composer.
composer.contains = node => node===button || node===input || node?.sendTarget===true;
function capture() {captureCodexProviderMismatchAttempt({type:'click',target:{sendTarget:true,closest:()=>button}});}
''' + TEXT[lifecycle_start:lifecycle_end] + TEXT[start:end] + r'''
const mismatch=parseCodexProviderMismatch('This chat uses custom, but your current provider is crab-ai. Switch to custom to continue.');
function banner(text, options={}) {return {textContent:text,shown:options.shown,position:options.position || 'fixed',
  getBoundingClientRect(){return {top:options.top ?? 48,height:options.height ?? 60};},
  matches(selector){return options.native && selector==='[data-sonner-toast]';},
  getAttribute(key){return options.attrs?.[key] ?? null;},
  querySelectorAll(){return [];},closest(selector){return selector==='.codex-toast-area' ? (options.native ? {} : null) : options.hud ? {} : null;}};}
function observe(node) {observeCodexProviderMismatchErrors([{target:node,addedNodes:[]}]);}
function cancel() {codexProviderMismatchState.layer.handlers['[data-provider-mismatch-cancel]']();}
async function finish(ok=true) {
  assert(pending); const command=pending;pending=null;
  if(ok) await refreshCodexProviderRuntime(command.provider,command.requestId);
  finishDefaultProviderSwitch({requestId:command.requestId,provider:command.provider,ok});
}
function reset() {
  closeCodexProviderMismatchDialog(); clearDefaultProviderSwitchFeedback();
  codexProviderMismatchState.requestId='';codexProviderMismatchState.attempt=null;
  codexProviderMismatchState.seen=new WeakMap();codexProviderMismatchState.dismissedKey='';codexProviderSwitchMenuState.pendingRequestId='';
  pending=null;defaultProvider='crab-ai';threadProvider='custom';activeThreadId=id;selectedRef=null;window.location.pathname='/index.html';
  input.value='Please continue';readsFail=false;submitFails=false;refreshFails=false;
}
(async()=>{
  assert.deepEqual(mismatch,{provider:'custom',currentProvider:'crab-ai'});
  assert.equal(parseCodexProviderMismatch('This chat uses custom, but your current provider is crab-ai. Switch to other to continue.'),null);
  assert.equal(parseCodexProviderMismatch('network error'),null);
  assert.equal(parseCodexProviderMismatch('This chat uses custom, but your current provider is custom. Switch to custom to continue.'),null);
  const text='提交消息出错 This chat uses custom, but your current provider is crab-ai. Switch to custom to continue.';
  for(const options of [{hud:true},{shown:false}]) observe(banner(text,options));
  assert.equal(codexProviderMismatchState.layer,null,'quoted conversation text/hidden/HUD content cannot prompt');
  let toast=banner(text); observe(toast);
  assert.equal(writes.length,0,'observing error must not change config');
  assert.equal(reads.length,0,'opening/canceling prompt needs no RPC');
  assert.match(codexProviderMismatchState.layer.innerHTML,/所有窗口共用/);
  assert.match(codexProviderMismatchState.layer.innerHTML,/持续生效/);
  const layer=codexProviderMismatchState.layer;observe(toast);
  assert.equal(codexProviderMismatchState.layer,layer,'observer must not redraw itself');
  cancel();observe(toast);assert.equal(codexProviderMismatchState.layer,null,'cancel stays dismissed');
  const transcript=banner(text,{position:'static',top:420});observe(transcript);
  assert.equal(codexProviderMismatchState.layer,null,'a lower transcript block is not a native toast');
  assert.equal(codexProviderMismatchThreadId(),id,'static index.html route uses the active session ID');
  activeThreadId='invalid';assert.equal(codexProviderMismatchThreadId(),'');activeThreadId=id;
  selectedRef={sessionId:'00000000-0000-0000-0000-000000000002'};
  assert.equal(codexProviderMismatchThreadId(),selectedRef.sessionId,'live selection takes precedence over a stale HUD payload');
  selectedRef={sessionId:id,rendererSessionId:'ssh:'+id};assert.equal(codexProviderMismatchThreadId(),'');selectedRef=null;
  reset();const native=banner(text,{native:true,position:'static',top:280});
  const wrapper={querySelectorAll(){return [native];},closest(){return null;},textContent:''};
  observeCodexProviderMismatchErrors([{target:wrapper,addedNodes:[wrapper]}]);
  assert(codexProviderMismatchState.layer,'recognize a native toast inserted as a nested subtree without geometry guesses');
  reset();const hiddenNative=banner(text,{native:true,attrs:{'data-visible':'false'}});observe(hiddenNative);
  assert.equal(codexProviderMismatchState.layer,null);
  reset();const mounted=banner(text,{native:true});observeCodexProviderMismatchErrors([{target:mounted,type:'attributes'}]);
  assert(codexProviderMismatchState.layer,'mount attribute changes must be rechecked');
  reset();observe(toast);
  codexProviderMismatchState.layer.handlers['[data-provider-mismatch-cli]']();
  assert.deepEqual(cliProviders,['custom'],'CLI action must reuse the existing provider launcher');
  assert.equal(defaultProvider,'crab-ai','CLI launch leaves Desktop global default unchanged');
  assert.equal(writes.length,0);
  assert.equal(codexProviderMismatchState.layer,null);
  assert.equal(codexProviderMismatchState.attempt,null);
  capture();observe(toast);assert.equal(codexProviderMismatchState.layer,null,'submitting must not revive an old toast');
  toast=banner(text);observe(toast);assert(codexProviderMismatchState.layer,'a new error can prompt again');
  await confirmCodexProviderMismatchSwitch();
  assert.deepEqual(writes,['custom']);assert.equal(retryCount,0,'wait for confirmed runtime');
  await finish();assert.equal(retryCount,1);assert.equal(defaultProvider,'custom','no automatic rollback');
  observe(toast);assert.equal(codexProviderMismatchState.layer,null,'automatic retry cannot reopen the stale error');
  assert.equal(timers.size,0);
  finishDefaultProviderSwitch({requestId:'old',provider:'custom'});assert.equal(retryCount,1);

  reset();capture();openCodexProviderMismatchDialog(mismatch);input.value='Edited after error';
  await confirmCodexProviderMismatchSwitch();await finish();
  assert.equal(retryCount,1,'changed draft cannot be automatically sent');
  assert.match(codexProviderSwitchMenuState.feedbackLayer.innerHTML,/请在当前会话重新发送/);
  assert.match(codexProviderSwitchMenuState.feedbackLayer.innerHTML,/已切换默认供应商/);

  reset();openCodexProviderMismatchDialog(mismatch);
  window.location.pathname='/local/00000000-0000-0000-0000-000000000001';
  const before=submitCount;await confirmCodexProviderMismatchSwitch();assert.equal(submitCount,before);
  reset();capture();openCodexProviderMismatchDialog(mismatch);defaultProvider='third';
  await confirmCodexProviderMismatchSwitch();assert.equal(submitCount,before);
  assert.equal(defaultProvider,'third');assert.match(codexProviderSwitchMenuState.feedbackLayer.innerHTML,/其他操作修改/);
  reset();capture();openCodexProviderMismatchDialog(mismatch);threadProvider='third';
  await confirmCodexProviderMismatchSwitch();assert.equal(submitCount,before);
  reset();capture();openCodexProviderMismatchDialog(mismatch);readsFail=true;
  await confirmCodexProviderMismatchSwitch();assert.equal(submitCount,before);assert.equal(defaultProvider,'crab-ai');

  reset();capture();openCodexProviderMismatchDialog(mismatch);submitFails=true;
  await confirmCodexProviderMismatchSwitch();assert.equal(retryCount,1);
  assert.equal(codexProviderMismatchState.requestId,'');assert.equal(timers.size,0);
  reset();capture();openCodexProviderMismatchDialog(mismatch);
  await confirmCodexProviderMismatchSwitch();await finish(false);assert.equal(retryCount,1,'failed refresh cannot resend');

  reset();capture();openCodexProviderMismatchDialog(mismatch);defaultProvider='custom';
  const count=writes.length;await confirmCodexProviderMismatchSwitch();
  assert.equal(writes.length,count,'already selected provider needs no config write');assert.equal(retryCount,2);
  reset();capture();openCodexProviderMismatchDialog(mismatch);defaultProvider='custom';refreshFails=true;
  await confirmCodexProviderMismatchSwitch();assert.equal(retryCount,2);

  reset();capture();openCodexProviderMismatchDialog(mismatch);
  let release; delayedRead=new Promise(resolve=>{release=resolve;});
  const first=confirmCodexProviderMismatchSwitch();const second=confirmCodexProviderMismatchSwitch();
  release();await Promise.all([first,second]);delayedRead=null;
  assert.equal(codexProviderMismatchState.checking,false);assert(pending);
  const last=writes.length;await finish();assert.equal(writes.length,last,'single confirmation must not restore old default');
  reset();assert.equal(timers.size,0);assert.equal(layers.size,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(
        ["node", "-"], input=script, capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_history_click_no_longer_writes_global_config_and_observer_is_event_driven():
    assert "handleCodexProviderResumeClick" not in TEXT
    assert "codexRestoreDefaultProviderAfterResume" not in TEXT
    assert "codexResumeConfigWrite" not in TEXT
    assert '\"config/value/write\"' not in TEXT
    assert "observeCodexProviderMismatchErrors(records);" in TEXT
    start = TEXT.index("      function codexProviderMismatchThreadId()")
    end = TEXT.index("      function startCodexProviderMigrate()", start)
    assert "setInterval" not in TEXT[start:end]
    assert "setTimeout" not in TEXT[start:end]


def test_mismatch_observer_uses_existing_lifecycle_and_disposes():
    start = TEXT.index("      function installCodexCliQuickLaunchMenu()")
    end = TEXT.index("    function install()", start)
    script = r'''
const assert = require('node:assert/strict');
const listeners=[], observations=[], scans=[];
let observer, closed=0, disconnected=0;
const rootId='hud';
const window={};
const document={body:{},getElementById(){return {remove(){}};},querySelector(){return null;},querySelectorAll(){return [];}};
class MutationObserver {
  constructor(fn){this.fn=fn;observer=this;}
  observe(target,options){observations.push({target,options});}
}
const ctx={lifecycle:{listen(...args){listeners.push(args);}},observers:{
  set(key,value){assert.equal(key,'codex_cli_quick_launch_menu');return value;},
  clear(key){assert.equal(key,'codex_cli_quick_launch_menu');disconnected++;}
}};
const codexProviderSwitchMenuState={},codexCliQuickLaunchMenuState={};
const codexProviderMismatchState={requestId:'pending',attempt:{}};
function ensureCodexCliQuickLaunchMenuStyle(){}
function captureCodexProviderMismatchAttempt(){}
function handleCodexCliQuickLaunchMenuClick(){}
function syncCodexCliQuickLaunchMenu(){}
function observeCodexProviderMismatchErrors(records){scans.push(records);}
function closeCodexProviderMismatchDialog(){closed++;}
function disposeGatewayResumeAdapter(){}
function clearDefaultProviderSwitchFeedback(){}
function closeCodexCliQuickLaunchMenu(){}
function closeCodexProviderSwitchMenu(){}
function codexCliQuickLaunchLayer(){return null;}
''' + TEXT[start:end] + r'''
installCodexCliQuickLaunchMenu();
assert.equal(observations.length,1,'reuse existing observer, no extra poller');
assert.equal(observations[0].options.characterData,true);
assert.equal(observations[0].options.attributes,true);
assert.deepEqual(observations[0].options.attributeFilter,['data-mounted','data-visible','data-removed']);
assert.equal(scans.length,1,'detect a visible error at installation');
const records=[{addedNodes:[]}];observer.fn(records);assert.equal(scans[1],records);
assert.equal(listeners.filter(x=>x[3]===captureCodexProviderMismatchAttempt).length,2);
disposeCodexCliQuickLaunchMenu();assert.equal(closed,1);assert.equal(disconnected,1);
assert.equal(codexProviderMismatchState.requestId,'');
assert.equal(codexProviderMismatchState.attempt,null);
'''
    result = subprocess.run(
        ["node", "-"], input=script, capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
