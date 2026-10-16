"""Exercise the real menu switching lifecycle with a deterministic browser clock."""
import subprocess

import pytest

from codex_usage_hud.renderer_assets.settings_shell import TEXT


@pytest.mark.parametrize(
    "manager_bundle,shared_unavailable",
    [("app-shared", False), ("app-initial", False), ("app-initial", True)],
)
def test_runtime_resolves_manager_across_desktop_bundles(
    tmp_path, manager_bundle, shared_unavailable
):
    start = TEXT.index("      async function resolveCodexProviderRuntime()")
    end = TEXT.index("      function desktopModelReasoningEffort", start)
    resolver = TEXT[start:end].replace("await import(href)", "await importModule(href)")
    script = (r'''
const assert = require('node:assert/strict');
const managerBundle = MANAGER_BUNDLE;
const sharedUnavailable = SHARED_UNAVAILABLE;
const imported = [];
const manager = {clearPrewarmedThreads() {}, sendRequest() {}};
const scope = {node: {}, query() {}, get() {}, set() {}};
const root = {__reactContainer$test: {stateNode: {current: {
  memoizedState: {memoizedState: {current: scope}},
}}}};
const window = {};
const document = {
  querySelectorAll() { return [
    {href: 'app://-/assets/app-shared-legacy123.js'},
    {href: 'app://-/assets/app-shared-style.css'},
    {href: 'app://-/assets/app-initial-current456.js'},
    {href: 'app://-/assets/unrelated-789.js'},
  ]; },
  getElementById(id) { assert.equal(id, 'root'); return root; },
};
function managerForHost(candidate, hostId) {
  if (candidate !== scope || hostId !== 'local') {
    throw new Error('AppServerManager RPC is unavailable for hostId: ' + hostId);
  }
  return manager;
}
async function importModule(href) {
  imported.push(href);
  if (sharedUnavailable && href.includes('app-shared-')) throw new Error('old chunk unavailable');
  // The manager keeps its behavior while minified export names change.
  return href.includes(managerBundle + '-') ? {renamedExport: managerForHost} : {unrelated() {}};
}
'''.replace('MANAGER_BUNDLE', repr(manager_bundle)).replace(
        'SHARED_UNAVAILABLE', str(shared_unavailable).lower()
    ) + resolver + r'''
(async () => {
  const result = await resolveCodexProviderRuntime();
  assert.equal(result.manager, manager);
  assert.equal(result.scope, scope);
  assert(!imported.some(href => href.endsWith('.css') || href.includes('unrelated-')));
  if (managerBundle === 'app-initial') assert.equal(imported.length, 2);
  document.querySelectorAll = () => [];
  await assert.rejects(resolveCodexProviderRuntime(), /当前版本不支持清理预热会话/);
})().catch(error => { console.error(error); process.exitCode = 1; });
''')
    path = tmp_path / "provider_runtime_bundles.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


def test_provider_switch_feedback_and_correlated_completion(tmp_path):
    start = TEXT.index("      function clearDefaultProviderSwitchFeedback(")
    end = TEXT.index("      function renderCodexProviderSwitchMenu(", start)
    script = r'''
const assert = require('node:assert/strict');
const timers = new Map();
let serial = 0, migrationCount = 0, syncCount = 0;
const window = {
  setTimeout(fn, ms) { const id = ++serial; timers.set(id, {fn, ms}); return id; },
  clearTimeout(id) { timers.delete(id); },
};
const layers = new Set();
const document = {
  createElement() { return {dataset: {}, setAttribute() {}, innerHTML: '',
    querySelector() { return {addEventListener() {}}; },
    remove() { layers.delete(this); }}; },
  body: {appendChild(node) {layers.add(node);}},
};
const settings = {default_provider: 'old'};
const settingsProviderDraft = {appProvider: 'old'};
const codexProviderSwitchMenuState = {};
function hudSettingsFromPayload() { return settings; }
function providerDisplayName(_settings, provider) { return provider; }
function escapeHtml(text) { return String(text); }
function syncCodexCliQuickLaunchMenu() { syncCount++; }
function openCodexProviderMigrateDialog() { migrationCount++; }
function fire(ms) {
  for (const [id, timer] of [...timers]) if (timer.ms === ms) {timers.delete(id); timer.fn();}
}
''' + TEXT[start:end] + r'''
assert(beginDefaultProviderSwitch({provider:'new', requestId:'one', migration:{}}));
assert.equal(settings.default_provider, 'old', 'pending must not masquerade as persisted default');
assert.equal(layers.size, 0, 'fast path must not flash a loading overlay');
assert(!beginDefaultProviderSwitch({provider:'third', requestId:'duplicate'}));
assert(!finishDefaultProviderSwitch({requestId:'stale', provider:'new'}));
assert(!finishDefaultProviderSwitch({provider:'new'}));
assert.equal(timers.size, 2);
assert(finishDefaultProviderSwitch({requestId:'one', provider:'new'}));
assert.equal(timers.size, 0);
assert.equal(settings.default_provider, 'new');
assert.equal(settingsProviderDraft.appProvider, 'new');
assert.equal(migrationCount, 1);
assert(!finishDefaultProviderSwitch({requestId:'one', provider:'new'}));
assert.equal(migrationCount, 1, 'replayed payload cannot reopen migration');
beginDefaultProviderSwitch({provider:'slow', requestId:'two'});
fire(350);
assert.equal(layers.size, 1);
assert.match([...layers][0].innerHTML, /slow/);
finishDefaultProviderSwitch({requestId:'two', provider:'slow'});
assert.equal(layers.size, 0);
beginDefaultProviderSwitch({provider:'broken', requestId:'three', migration:{}});
finishDefaultProviderSwitch({requestId:'three', ok:false, message:'disk failed'});
assert.equal(settings.default_provider, 'slow');
assert.equal(codexProviderSwitchMenuState.pendingRequestId, '');
assert.match([...layers][0].innerHTML, /disk failed/);
assert.equal(migrationCount, 1);
beginDefaultProviderSwitch({provider:'timeout', requestId:'four'});
fire(350); fire(15000);
assert.equal(timers.size, 0);
assert.equal(codexProviderSwitchMenuState.pendingRequestId, '');
assert.equal(settings.default_provider, 'slow');
assert.match([...layers][0].innerHTML, /暂未收到/);
clearDefaultProviderSwitchFeedback();
assert.equal(layers.size, 0);
console.log('provider switch lifecycle ok');
'''
    path = tmp_path / "provider_switch.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


def test_provider_switch_waits_for_prewarm_clear_and_deduplicates_status(tmp_path):
    start = TEXT.index("        if (String(status?.action || \"\") === \"providerSetDefault\") {")
    end = TEXT.index("        const providerCloneSwitch", start)
    fragment = TEXT[start:end]
    script = r'''
const assert = require('node:assert/strict');
let resolveRefresh, rejectRefresh, calls = 0;
const finished = [];
const codexProviderSwitchMenuState = {pendingRequestId:'one'};
function refreshCodexProviderRuntime(provider, requestId) {
  assert.equal(provider, 'new'); assert.equal(requestId, 'one'); calls++;
  return new Promise((resolve, reject) => { resolveRefresh=resolve; rejectRefresh=reject; });
}
function finishDefaultProviderSwitch(result) { finished.push(result); }
function apply(status) {
''' + fragment + r'''
}
(async () => {
  const status = {action:'providerSetDefault',requestId:'one',providerSetDefaultProvider:'new'};
  apply(status); apply(status);
  assert.equal(calls, 1); assert.equal(finished.length, 0);
  resolveRefresh(); await new Promise(setImmediate);
  assert.equal(finished.length, 1); assert.equal(finished[0].ok, true);
  codexProviderSwitchMenuState.refreshPendingRequestId = '';
  apply(status); rejectRefresh(new Error('unsupported-build'));
  await new Promise(setImmediate);
  assert.equal(finished[1].ok, false); assert.match(finished[1].message, /unsupported-build/);
})().catch(e => { console.error(e); process.exitCode=1; });
'''
    path = tmp_path / "provider_ack.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_clears_old_prewarm_only_after_verified_config(tmp_path):
    runtime_start = TEXT.index("      async function refreshCodexProviderRuntime(")
    runtime_end = TEXT.index("      function renderCodexProviderSwitchMenu(", runtime_start)
    runtime = TEXT[runtime_start:runtime_end]
    script = r'''
const assert = require('node:assert/strict');
let provider='new'; const order=[];
const codexProviderSwitchMenuState={pendingRequestId:'one'};
const manager={
  async sendRequest(method) {assert.equal(method,'config/read');order.push('read');return {config:{model_provider:provider}};},
  async clearPrewarmedThreads() {order.push('clear');}
};
const scope={queryClient:{async invalidateQueries() {order.push('cache');}}};
async function resolveCodexProviderRuntime() {return {manager,scope};}
''' + runtime + r'''
(async()=>{
 await refreshCodexProviderRuntime('new','one'); assert.deepEqual(order,['read','cache','clear']);
 order.length=0; provider='old';
 await assert.rejects(refreshCodexProviderRuntime('new','one')); assert.deepEqual(order,['read']);
 order.length=0; provider='new';codexProviderSwitchMenuState.pendingRequestId='two';
 await assert.rejects(refreshCodexProviderRuntime('new','one'));assert(!order.includes('clear'));
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    path = tmp_path / "provider_runtime.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


def test_provider_save_refreshes_once_and_reports_failure_without_restart(tmp_path):
    start = TEXT.index('        if (status?.codexProvidersSaved && status.kind !== "error") {')
    end = TEXT.index("        const providerCloneSwitch", start)
    script = r'''
const assert = require('node:assert/strict');
let lastProviderSaveRefreshRequestId='', calls=0, cleared=0, resolveRefresh, rejectRefresh;
const messages=[];
function refreshCodexProviderRuntime() {calls++; return new Promise((a,b)=>{resolveRefresh=a;rejectRefresh=b;});}
function clearSettingsRestartPrompt() {cleared++;}
function setSettingsStatus(message,kind) {messages.push({message,kind});}
function apply(status) {
''' + TEXT[start:end] + r'''
}
(async()=>{
 const one={requestId:'one',codexProvidersSaved:{providerIds:['qiyu']}};
 apply(one);apply(one);assert.equal(calls,1);assert.equal(cleared,0);
 resolveRefresh();await new Promise(setImmediate);assert.equal(cleared,1);
 assert.match(messages[0].message,/无需重启/);
 apply({requestId:'two',codexProvidersSaved:{}});rejectRefresh(new Error('native-cache-unavailable'));
 await new Promise(setImmediate);assert.equal(cleared,1);assert.equal(messages[1].kind,'error');
 assert.match(messages[1].message,/native-cache-unavailable/);
 assert(!messages[1].message.includes('请重启'));
 apply({requestId:'failed',kind:'error',codexProvidersSaved:{}});assert.equal(calls,2);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    path = tmp_path / "provider_save.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
