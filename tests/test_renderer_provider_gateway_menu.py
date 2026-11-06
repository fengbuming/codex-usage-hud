"""Execute the menubar request-proxy toggle without a real Codex process."""

import subprocess

from codex_usage_hud.renderer_assets.settings_shell import TEXT


def run_js(script):
    result = subprocess.run(
        ["node", "-"], input=script, text=True, capture_output=True,
        encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_provider_label_drops_prefix_and_marks_proxy_state():
    start = TEXT.index("      function codexCliQuickLaunchProviderLabelState()")
    end = TEXT.index("      function syncCodexCliQuickLaunchMenu()", start)
    run_js(r"""
const assert = require('node:assert/strict');
let active = '', gateway = false, writes = 0;
const settings = () => ({default_provider: 'alpha', provider_gateway: {active: gateway}});
const codexProviderSwitchMenuState = {pendingProvider: '', toggle: null};
function codexProviderSwitchSessionContext() { return {provider: active}; }
function hudSettingsFromPayload() { return settings(); }
function providerDisplayName(_s, p) { return p.toUpperCase(); }
function escapeHtml(x) { return String(x); }
function closeCodexProviderSwitchMenu() {}
const label = {dataset: {}, parentElement: null, previousElementSibling: null,
  setAttribute(name, value) { this[name] = value; }, set innerHTML(v) { writes++; this.html = v; }};
const menubar = {querySelector(selector) { return selector.includes('cli-provider-label') ? label : null; }};
const toggle = {insertAdjacentElement() {}};
label.parentElement = menubar; label.previousElementSibling = toggle;
""" + TEXT[start:end] + r"""
let state = codexCliQuickLaunchProviderLabelState();
assert.equal(state.text, 'ALPHA');
assert.equal(state.suffix, '默认');
assert.equal(state.gateway, false);
syncCodexCliQuickLaunchProviderLabel(menubar, toggle);
assert.equal(label.dataset.gateway, 'false');
assert(!label.html.includes('供应商'));
assert(label.html.includes('默认'));
const before = writes;
syncCodexCliQuickLaunchProviderLabel(menubar, toggle);
assert.equal(writes, before, 'steady state must not write the DOM');
gateway = true; active = 'beta';
state = codexCliQuickLaunchProviderLabelState();
assert.equal(state.text, 'BETA');
assert.equal(state.suffix, '');
assert.equal(state.mismatch, true);
assert.match(state.title, /HUD 请求代理已开启/);
syncCodexCliQuickLaunchProviderLabel(menubar, toggle);
assert.equal(label.dataset.gateway, 'true');
assert.equal(label.dataset.mismatch, 'true');
""")


def test_provider_menu_renders_proxy_switch_row_first():
    start = TEXT.index("      function renderCodexProviderSwitchMenu()")
    end = TEXT.index("      function closeCodexProviderSwitchMenu()", start)
    run_js(r"""
const assert = require('node:assert/strict');
let gateway = false;
const surface = {dataset: {}, style: {}, isConnected: true, scrollWidth: 200, setAttribute() {}};
const label = {isConnected: true, dataset: {}, setAttribute() {},
  getBoundingClientRect() { return {left: 10, bottom: 30}; }};
const codexProviderSwitchMenuState = {toggle: label, surface};
const innerWidth = 1200, innerHeight = 800;
function hudSettingsFromPayload() { return {default_provider: 'alpha', provider_gateway: {active: gateway}}; }
function settingsProviderNames() { return ['alpha', 'beta']; }
function codexProviderSwitchSessionContext() { return {provider: ''}; }
function providerDisplayName(_s, p) { return p; }
function escapeHtml(x) { return String(x); }
function closeCodexProviderSwitchMenu() { throw new Error('must stay open'); }
""" + TEXT[start:end] + r"""
renderCodexProviderSwitchMenu();
const off = surface.innerHTML;
assert(off.indexOf('data-codex-usage-hud-provider-gateway-toggle') < off.indexOf('data-provider="alpha"'));
assert(off.includes('role="menuitemcheckbox"'));
assert(off.includes('aria-checked="false"'));
assert(off.includes('已关闭 · 直连供应商'));
gateway = true;
renderCodexProviderSwitchMenu();
assert(surface.innerHTML.includes('aria-checked="true"'));
assert(surface.innerHTML.includes('已开启 · HUD 统一转发'));
""")


def test_proxy_dialog_confirms_waits_for_ack_and_keeps_failures_visible():
    start = TEXT.index("      function closeCodexProviderGatewayDialog(")
    end = TEXT.index("      function closeCodexProviderMigrateDialog()", start)
    run_js(r"""
const assert = require('node:assert/strict');
const commands = [], feedback = [], appended = [];
let release, reject, synced = 0, refreshed = 0, dirty = false;
const payloadSettings = {default_provider: 'codex_hud', provider_gateway: {active: true}, provider_gateway_enabled: true};
const codexProviderGatewayDialogState = {layer: null, busy: false};
const codexProviderSwitchMenuState = {pendingRequestId: '', open: false};
const controls = [{disabled: false}, {disabled: false}];
const status = {dataset: {}, hidden: true, textContent: ''};
const exitProvider = {value: 'beta'};
function makeLayer() {
  return {dataset: {}, listeners: [], innerHTML: '', removed: false,
    setAttribute() {}, addEventListener(type, fn) { this.listeners.push(fn); },
    remove() { this.removed = true; },
    querySelector(selector) {
      if (selector.includes('gateway-status')) return status;
      if (selector.includes('provider-exit-selection')) return exitProvider;
      if (selector.includes('gateway-confirm')) return {focus() {}};
      return null;
    },
    querySelectorAll() { return controls; }};
}
const document = {createElement: makeLayer, body: {appendChild(node) { appended.push(node); }}};
function ensureCodexCliQuickLaunchMenuStyle() {}
function hudManagementDraftsPending() { return dirty; }
function hudSettingsFromPayload() { return payloadSettings; }
function currentPayload() { return {settings: payloadSettings}; }
function hudDirectProviderSelection() { return '<select data-provider-exit-selection="true"></select>'; }
function providerDisplayName(_s, p) { return p; }
function showDefaultProviderSwitchFeedback(message, failed, completed) { feedback.push({message, failed, completed}); }
function codexGatewayCommand(command) { commands.push(command); return new Promise((a, b) => { release = a; reject = b; }); }
function syncCodexCliQuickLaunchMenu() { synced++; }
function renderCodexProviderSwitchMenu() {}
function refreshCodexProviderRuntime() { refreshed++; return Promise.resolve(); }
""" + TEXT[start:end] + r"""
(async () => {
  dirty = true;
  assert.equal(openCodexProviderGatewayDialog(), false);
  assert.match(feedback.at(-1).message, /未保存/);
  dirty = false;
  codexProviderSwitchMenuState.pendingRequestId = 'busy';
  assert.equal(openCodexProviderGatewayDialog(), false);
  codexProviderSwitchMenuState.pendingRequestId = '';

  assert(openCodexProviderGatewayDialog());
  const layer = codexProviderGatewayDialogState.layer;
  assert(layer.innerHTML.includes('data-enabled="false"'));
  assert(layer.innerHTML.includes('data-provider-exit-selection'));
  assert.equal(commands.length, 0, 'opening must not change the proxy');

  let pending = toggleCodexProviderGateway(false);
  assert.deepEqual(commands[0], {action: 'providerGatewayToggle', enabled: false, provider: 'beta'});
  assert(controls.every(node => node.disabled));
  closeCodexProviderGatewayDialog();
  assert.equal(layer.removed, false, 'busy dialog cannot be dismissed');
  await toggleCodexProviderGateway(false);
  assert.equal(commands.length, 1, 'no duplicate submission');
  reject(new Error('write denied')); await pending;
  assert.equal(codexProviderGatewayDialogState.layer, layer);
  assert.match(status.textContent, /write denied/);
  assert.equal(status.dataset.kind, 'error');
  assert(controls.every(node => !node.disabled));
  assert.equal(payloadSettings.provider_gateway.active, true);

  pending = toggleCodexProviderGateway(false);
  release({enabled: false, gateway: false, providerId: 'beta'}); await pending;
  assert.equal(layer.removed, true);
  assert.equal(codexProviderGatewayDialogState.layer, null);
  assert.equal(payloadSettings.provider_gateway.active, false);
  assert.equal(payloadSettings.provider_gateway_enabled, false);
  assert.equal(payloadSettings.default_provider, 'beta');
  assert.equal(synced, 1); assert.equal(refreshed, 1);
  assert.equal(feedback.at(-1).completed, true);
  assert.match(feedback.at(-1).message, /直连「beta」/);

  assert(openCodexProviderGatewayDialog());
  const enableLayer = codexProviderGatewayDialogState.layer;
  assert(enableLayer.innerHTML.includes('data-enabled="true"'));
  assert(!enableLayer.innerHTML.includes('data-provider-exit-selection'));
  pending = toggleCodexProviderGateway(true);
  assert.equal(commands.at(-1).enabled, true);
  release({enabled: true, gateway: true}); await pending;
  assert.equal(payloadSettings.provider_gateway.active, true);
  assert.match(feedback.at(-1).message, /已开启/);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_menu_click_routes_proxy_row_to_dialog():
    start = TEXT.index("      function handleCodexCliQuickLaunchMenuClick(event)")
    end = TEXT.index("      function codexCliQuickLaunchProviderLabelState()", start)
    run_js(r"""
const assert = require('node:assert/strict');
let opened = 0, closed = 0;
const row = {dataset: {}};
const codexCliQuickLaunchMenuState = {};
const codexProviderSwitchMenuState = {surface: {contains(node) { return node === row; }}};
const event = {preventDefault() {}, stopPropagation() {},
  target: {closest(selector) { return selector.includes('provider-gateway-toggle') ? row : null; }}};
function codexCliQuickLaunchLayer() { return null; }
function closeCodexProviderSwitchMenu() { closed++; }
function openCodexProviderGatewayDialog() { opened++; }
""" + TEXT[start:end] + r"""
handleCodexCliQuickLaunchMenuClick(event);
assert.equal(opened, 1);
assert.equal(closed, 1);
""")