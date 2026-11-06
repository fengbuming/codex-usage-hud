"""Execute gateway menu/resume/exit code without a real Codex process."""

import subprocess

from codex_usage_hud.renderer_assets.settings_shell import TEXT


def run_js(script):
    result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_resume_uses_saved_identity_and_waits_for_binding_then_disposes():
    start = TEXT.index("      const gatewayCommandRequests = new Map();")
    end = TEXT.index("      function codexCliLaunchStateKey(", start)
    run_js(r'''
const assert = require('node:assert/strict');
const timers = new Map(), commands = [], rpc = [];
let serial = 0, release, saved = 'legacy', auto = false;
const window = {setTimeout(fn){const id=++serial;timers.set(id,fn);return id;},clearTimeout(id){timers.delete(id);}};
const settings = {provider_gateway:{active:true,provider_id:'codex_hud'}};
function hudSettingsFromPayload(){return settings;}
function typedSettingsRequestId(){return String(++serial);}
function submitSettingsCommand(command){commands.push(command);if(auto)queueMicrotask(()=>applyGatewayCommandStatus({requestId:command.requestId,providerGateway:{}}));return true;}
const manager = {async sendRequest(method, params){rpc.push({method,params});if(method==='thread/read')return {thread:{modelProvider:saved}};return {thread:{id:params?.threadId}};}};
const original = manager.sendRequest;
function resolveCodexProviderRuntime(){return Promise.resolve({manager});}
const tick = () => new Promise(setImmediate);
''' + TEXT[start:end] + r'''
(async()=>{
  installGatewayResumeAdapter();installGatewayResumeAdapter();await tick();
  const resumed = manager.sendRequest('thread/resume',{threadId:'source',modelProvider:'codex_hud',config:{custom:true}});
  await tick();
  assert.equal(commands.length,1);
  assert.equal(commands[0].provider,'legacy');assert.equal(commands[0].preserveExisting,true);
  assert.equal(rpc.filter(x=>x.method==='thread/resume').length,0,'binding must finish first');
  applyGatewayCommandStatus({requestId:'stale',providerGateway:{}});
  assert.equal(timers.size,1);
  applyGatewayCommandStatus({requestId:commands[0].requestId,providerGateway:{providerId:'target'}});
  await resumed;
  assert.deepEqual(rpc.at(-1).params,{threadId:'source',modelProvider:'legacy',config:{custom:true,model_provider:'legacy'}});
  assert.equal(timers.size,0);
  saved='';await assert.rejects(manager.sendRequest('thread/resume',{threadId:'missing'}),/无法确认/);
  assert.equal(rpc.filter(x=>x.method==='thread/resume').length,1);
  settings.provider_gateway.active=false;
  await manager.sendRequest('thread/resume',{threadId:'direct',modelProvider:'beta'});
  assert.equal(rpc.at(-1).params.modelProvider,'beta');
  const pending=codexGatewayCommand({action:'providerBindSession'});const rejected=assert.rejects(pending,/HUD 已关闭/);
  disposeGatewayResumeAdapter();await rejected;
  assert.equal(manager.sendRequest,original);assert.equal(timers.size,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_copy_binds_target_before_naming_and_opening_and_never_requests_cross_identity():
    start = TEXT.index("      function startCodexProviderMigrate()")
    end = TEXT.index("      function bindCodexProviderMigrateDialogClicks(", start)
    run_js(r'''
const assert=require('node:assert/strict');
const state={targetProvider:'beta',activeProvider:'alpha',activeSessionId:'source',layer:{}};
const codexProviderMigrateDialogState=state,sessionTransferState={};
const calls=[];let release, bindingFails=false, navigation=0;
const tick=()=>new Promise(setImmediate);
function hudSettingsFromPayload(){return {provider_gateway:{active:true},provider_registry:{beta:{hasApiKey:true}}};}
function renderCodexProviderMigrateDialog(){}
function renderSessionTransferDialog(){}
function closeCodexProviderMigrateDialog(){calls.push('closed');}
async function codexBridgeRequest(method,params){calls.push({method,params});if(method==='thread/read')return {result:{thread:{modelProvider:'legacy'}}};if(method==='thread/fork')return {result:{thread:{id:'copy',modelProvider:'legacy'}}};return {result:{}};}
function codexGatewayCommand(command){calls.push(command);if(command.action==='providerBindSession')return bindingFails?Promise.reject(new Error('bind failed')):new Promise(resolve=>release=resolve);return Promise.resolve({});}
function codexSourceSessionTitle(){return Promise.resolve('Original');}
function codexNavigateToThread(){navigation++;return Promise.resolve(true);}
function codexWaitForSidebarActive(){return Promise.resolve(true);}
''' + TEXT[start:end] + r'''
(async()=>{
  startCodexProviderMigrate();startCodexProviderMigrate();await tick();
  const fork=calls.find(x=>x.method==='thread/fork');
  assert.equal(fork.params.modelProvider,'legacy');assert.equal(fork.params.config.model_provider,'legacy');
  assert.equal(calls.filter(x=>x.method==='thread/fork').length,1);
  const bind=calls.find(x=>x.action==='providerBindSession');
  assert.deepEqual(bind,{action:'providerBindSession',provider:'beta',sessionId:'copy',copiedFrom:'source'});
  assert.equal(navigation,0);assert(!calls.some(x=>x.method==='thread/name/set'));
  release({});await tick();
  assert(calls.some(x=>x.action==='providerSetDefault'&&x.provider==='beta'));
  assert.equal(navigation,1);assert.equal(state.result.ok,true);assert.equal(state.busy,false);
  assert.equal(calls.find(x=>x.method==='thread/name/set').params.name,'(复制)Original');
  bindingFails=true;calls.length=0;startCodexProviderMigrate();await tick();
  assert.equal(navigation,1);assert.equal(state.result.ok,false);assert.match(state.result.error,/bind failed/);
  assert(!calls.some(x=>x.action==='providerSetDefault'));
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_history_menu_asks_before_changing_default_and_new_chat_switches_directly():
    start = TEXT.index("      function codexProviderSwitchSessionContext()")
    end = TEXT.index("      function codexCliQuickLaunchProviderLabelState()", start)
    run_js(r'''
const assert=require('node:assert/strict');
let submitted=[],dialogs=[],feedback=[],fresh=false,live=null,gateway=true,begun=[];
const item={dataset:{provider:'beta'}};
const codexCliQuickLaunchMenuState={};
const codexProviderSwitchMenuState={surface:{contains(){return true;}}};
const event={preventDefault(){},stopPropagation(){},target:{closest(selector){return selector.includes('provider-switch=')?item:null;}}};
function closeCodexProviderSwitchMenu(){}
function hudSettingsFromPayload(){return {provider_gateway:{active:gateway}};}
function currentPayload(){return {activeSessionProvider:'alpha',sessionId:'source',newSession:fresh};}
function readActiveSessionRef(){return live;}
function showDefaultProviderSwitchFeedback(message){feedback.push(message);}
function providerDisplayName(_s,p){return p;}
function typedSettingsRequestId(){return 'switch';}
function openCodexProviderMigrateDialog(value){dialogs.push(value);}
function beginDefaultProviderSwitch(options){begun.push(options);return true;}
function submitSettingsCommand(command){submitted.push(command);return true;}
''' + TEXT[start:end] + r'''
handleCodexCliQuickLaunchMenuClick(event);
assert.equal(dialogs.length,1);assert.equal(submitted.length,0,'canceling confirmation must not change defaults');
fresh=true;handleCodexCliQuickLaunchMenuClick(event);
assert.equal(dialogs.length,1);assert.equal(submitted[0].action,'providerSetDefault');
// A blank live page outranks the previous conversation, even without flags.
for (gateway of [true,false]) {
  for (live of [{sessionId:'',title:''},{sessionId:'',title:'启动CLI'},
    {newSession:true}]) {
    fresh=false;submitted=[];begun=[];
    handleCodexCliQuickLaunchMenuClick(event);
    assert.equal(dialogs.length,1);
    assert.equal(submitted.length,1,'new chat must switch directly');
    assert.equal(begun[0].migration,null,'must not reopen the old session dialog after saving');
    assert.equal(codexProviderSwitchSessionContext().provider,'');
  }
}
// A stale newSession payload cannot hide a real live conversation either.
gateway=true;fresh=true;live={sessionId:'source'};submitted=[];
handleCodexCliQuickLaunchMenuClick(event);
assert.equal(dialogs.length,2);assert.equal(submitted.length,0);
fresh=false;live={sessionId:'other'};
handleCodexCliQuickLaunchMenuClick(event);
assert.equal(dialogs.length,2);assert.equal(submitted.length,0);
assert.equal(feedback.length,1,'wait for matching provider data after navigation');
live={pendingSession:true,rendererSessionId:'local:client-new-thread:temporary'};
submitted=[];handleCodexCliQuickLaunchMenuClick(event);
assert.equal(submitted.length,0,'unresolved existing sessions must not change defaults as a new chat');
assert.equal(dialogs.length,2);
assert.equal(feedback.length,2);
''')


def test_exit_waits_for_successful_config_restore_and_does_not_hide_failures():
    start = TEXT.index("      async function exitHudFromModal()")
    end = TEXT.index("      function checkUpdateFromModal()", start)
    run_js(r'''
const assert=require('node:assert/strict');
const submitted=[],status=[];let release,reject;
const document={querySelector(){return {value:'beta'};}};
function hudSettingsFromPayload(){return {provider_gateway:{active:true}};}
function codexGatewayCommand(command){assert.equal(command.provider,'beta');return new Promise((a,b)=>{release=a;reject=b;});}
function setSettingsStatus(message){status.push(message);}
function hudManagementStatus(message){status.push(message);}
function hudManagementBusy(){}
function openSettingsLoading(){}
function submitSettingsCommand(command){submitted.push(command);return true;}
const ctx={lifecycle:{timeout(){throw new Error('cannot hide HUD before confirmed exit');}}};
''' + TEXT[start:end] + r'''
(async()=>{
  const pending=exitHudFromModal();assert.equal(submitted.length,0);
  release({exitToken:'confirmed-config'});await pending;
  assert.equal(submitted[0].action,'exit');assert.equal(submitted[0].exitToken,'confirmed-config');
  const failed=exitHudFromModal();reject(new Error('write denied'));await failed;
  assert.equal(submitted.length,1);assert.match(status.at(-1),/write denied/);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')
