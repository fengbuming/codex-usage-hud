"""Focused coverage of the menu's copy, name, and navigation workflow."""

import subprocess

from codex_usage_hud.renderer_assets.settings_shell import TEXT


def run_script(tmp_path, script):
    path = tmp_path / "provider_migrate.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        ["node", str(path)], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_menu_migration_finishes_without_settings_and_survives_dismissal(tmp_path):
    start = TEXT.index("      function openCodexProviderMigrateDialog(")
    end = TEXT.index("      function bindCodexProviderMigrateDialogClicks(", start)
    script = r'''
const assert = require('node:assert/strict');
const codexProviderMigrateDialogState = {};
const sessionTransferState = {};
const timers = new Map(), listeners = new Map(), calls = [], pending = [];
let serial = 0, active = false, observer, navigationFails = false;
let title = 'Original title', titleFromSidebar = true, nameFails = false;
const window = {
  location: {pathname: '/', hash: ''},
  setTimeout(fn, ms) {const id = ++serial; timers.set(id, {fn, ms}); return id;},
  clearTimeout(id) {timers.delete(id);},
  addEventListener(key, fn) {listeners.set(key, fn);},
  removeEventListener(key) {listeners.delete(key);},
  electronBridge: {async sendMessageFromView(message) {
    calls.push(message);
    if (navigationFails) throw new Error('navigation failed');
    active = true;
  }},
};
class MutationObserver {
  constructor(fn) {this.fn = fn; observer = this;}
  observe() {this.connected = true;}
  disconnect() {this.connected = false;}
}
const document = {
  body: {appendChild() {}},
  createElement() {return {dataset: {}, innerHTML: '', remove() {this.removed = true;}};},
  querySelector() {return titleFromSidebar ? {
    textContent: title + ' 2 minutes ago',
    querySelector() {return {textContent: title};},
  } : null;},
  querySelectorAll() {return active ? [{getAttribute(k) {
    return k === 'aria-current' ? 'page' : null;
  }}] : [];},
};
function escapeHtml(value) {return String(value);}
function hudSettingsFromPayload() {return {provider_registry: {new: {hasApiKey: true}}};}
function providerDisplayName(_settings, provider) {return provider;}
function ensureCodexCliQuickLaunchMenuStyle() {}
function bindCodexProviderMigrateDialogClicks() {}
function renderSessionTransferDialog() {throw new Error('unexpected settings dialog');}
function openSessionTransferDialog() {throw new Error('unexpected settings dialog');}
function renderSettingsModal() {throw new Error('unexpected settings modal');}
function codexBridgeRequest(method, params) {
  calls.push({method, params});
  if (method === 'thread/fork') return new Promise(resolve => pending.push(resolve));
  if (method === 'thread/name/set') return Promise.resolve(nameFails ? {error:{message:'name failed'}} : {result:{}});
  if (method === 'thread/read') return Promise.resolve({result:{thread:{name:title,preview:'Opening prompt'}}});
  throw new Error(method);
}
const tick = () => new Promise(setImmediate);
''' + TEXT[start:end] + r'''
function open() {
  return openCodexProviderMigrateDialog({targetProvider:'new', activeProvider:'old', activeSessionId:'source'});
}
function forked(id='copy', provider='new') {pending.shift()({result:{thread:{id,modelProvider:provider}}});}
(async () => {
  assert(open()); startCodexProviderMigrate(); startCodexProviderMigrate();
  assert.equal(pending.length, 1);
  assert(codexProviderMigrateDialogState.busy);
  assert.equal(timers.size, 0, 'a slow fork must not schedule a settings fallback');
  await tick();
  assert.match(codexProviderMigrateDialogState.layer.innerHTML, /正在复制/);
  forked(); await tick();
  assert.deepEqual(calls.find(c => c.method === 'thread/name/set').params,
    {threadId:'copy', name:'(复制)Original title'});
  assert.equal(calls.at(-1).path, '/local/copy?hostId=local');
  assert.equal(codexProviderMigrateDialogState.layer, null, 'success must reveal the chat');
  assert.equal(sessionTransferState.providerForkTask.result.navigated, true);
  assert.equal(timers.size, 0); assert.equal(listeners.size, 0);
  assert.equal(observer.connected, false);

  calls.length = 0; active = false; title = '(复制)Original title'; titleFromSidebar = false;
  assert(open()); startCodexProviderMigrate(); closeCodexProviderMigrateDialog();
  assert(codexProviderMigrateDialogState.busy, 'dismissal cannot start a duplicate fork');
  assert(!open()); startCodexProviderMigrate(); assert.equal(pending.length, 1);
  forked('second'); await tick();
  assert.equal(calls.find(c => c.method === 'thread/name/set').params.name, '(复制)Original title');
  assert.equal(calls.at(-1).path, '/local/second?hostId=local');
  assert.equal(codexProviderMigrateDialogState.busy, false);

  calls.length = 0; active = false;
  assert(open()); startCodexProviderMigrate(); forked('wrong', 'old'); await tick();
  assert.equal(calls.length, 1, 'mismatched provider must never be named or opened');
  assert.equal(codexProviderMigrateDialogState.result.ok, false);
  assert.match(codexProviderMigrateDialogState.layer.innerHTML, /未按目标迁移/);

  calls.length = 0; navigationFails = true;
  assert(open()); startCodexProviderMigrate(); forked(); await tick();
  assert.equal(codexProviderMigrateDialogState.result.navigated, false);
  assert.match(codexProviderMigrateDialogState.layer.innerHTML, /自动切换未成功/);

  navigationFails = false; nameFails = true;
  assert(open()); startCodexProviderMigrate(); forked(); await tick();
  assert.equal(codexProviderMigrateDialogState.result.navigated, true);
  assert.equal(codexProviderMigrateDialogState.result.named, false);
  assert.match(codexProviderMigrateDialogState.layer.innerHTML, /标题设置失败/);
})().catch(e => {console.error(e); process.exitCode = 1;});
'''
    run_script(tmp_path, script)


def test_navigation_observes_sidebar_and_accepts_route_without_sidebar(tmp_path):
    start = TEXT.index("      function codexWaitForSidebarActive(")
    end = TEXT.index("      function startCodexProviderMigrate(", start)
    script = r'''
const assert = require('node:assert/strict');
let callback, timeout, active = false, disconnected = 0;
const listeners = new Map();
const window = {
  location: {pathname:'/', hash:''},
  setTimeout(fn) {timeout = fn; return 1;}, clearTimeout() {timeout = null;},
  addEventListener(k, fn) {listeners.set(k, fn);}, removeEventListener(k) {listeners.delete(k);},
};
const document = {body:{}, querySelectorAll() {return active ? [{getAttribute() {return 'true';}}] : [];}};
class MutationObserver {
  constructor(fn) {callback = fn;} observe() {} disconnect() {disconnected++;}
}
''' + TEXT[start:end] + r'''
(async () => {
  const sidebar = codexWaitForSidebarActive('copy');
  active = true; callback(); assert.equal(await sidebar, true);
  assert.equal(timeout, null); assert.equal(listeners.size, 0);
  active = false; window.location.pathname = '/local/copy-other';
  const noMatch = codexWaitForSidebarActive('copy'); timeout(); assert.equal(await noMatch, false);
  window.location.pathname = '/local/copy';
  assert.equal(await codexWaitForSidebarActive('copy'), true);
  window.location.pathname = '/';
  const route = codexWaitForSidebarActive('copy');
  window.location.hash = '#/local/copy?hostId=local'; listeners.get('hashchange')();
  assert.equal(await route, true); assert.equal(listeners.size, 0);
  assert.equal(disconnected, 4);
})().catch(e => {console.error(e); process.exitCode = 1;});
'''
    run_script(tmp_path, script)


def test_bridge_response_clears_timeout_and_listener(tmp_path):
    start = TEXT.index("      function codexBridgeRequest(")
    end = TEXT.index("      function openCodexProviderMigrateDialog(", start)
    script = r'''
const assert = require('node:assert/strict');
let listener, timer, request;
const window = {
  electronBridge: {sendMessageFromView(message) {request = message.request; return Promise.resolve();}},
  addEventListener(_key, fn) {listener = fn;}, removeEventListener() {listener = null;},
  setTimeout(fn) {timer = fn; return 1;}, clearTimeout() {timer = null;},
};
''' + TEXT[start:end] + r'''
(async () => {
  const response = codexBridgeRequest('thread/fork', {threadId:'source'});
  listener({data:{type:'mcp-response',hostId:'local',message:{id:'stale',result:{}}}});
  assert(timer);
  listener({data:{type:'mcp-response',hostId:'local',message:{id:request.id,result:{thread:{id:'copy'}}}}});
  assert.equal((await response).result.thread.id, 'copy');
  assert.equal(listener, null); assert.equal(timer, null);
  const timeout = codexBridgeRequest('thread/fork', {}); timer();
  assert.equal((await timeout).error.message, 'timeout'); assert.equal(listener, null);
})().catch(e => {console.error(e); process.exitCode = 1;});
'''
    run_script(tmp_path, script)
