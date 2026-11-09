"""Native Fast stays owned by Codex, including live selection and availability."""

import subprocess

import pytest

from codex_usage_hud.config import UserConfig, UserConfigStore
from codex_usage_hud.renderer_assets.model_picker import TEXT
from codex_usage_hud.renderer_assets.settings_shell import TEXT as SETTINGS
from codex_usage_hud.runtime_settings import (
    changed_config_keys,
    config_from_payload,
    partial_domains_for_changed_config,
)


HARNESS = r"""
const assert = require('node:assert/strict');
class Node {
  constructor() { this.attrs = new Map(); this.isConnected = true; this.children = []; this.disabled = false; this.events = {}; }
  getAttribute(k) { return this.attrs.get(k) ?? null; }
  setAttribute(k,v) { this.attrs.set(k, String(v)); }
  hasAttribute(k) { return this.attrs.has(k); }
  removeAttribute(k) { this.attrs.delete(k); }
  addEventListener(k,fn) { this.events[k] = fn; }
  removeEventListener(k,fn) { if (this.events[k] === fn) delete this.events[k]; }
  querySelector() { return null; }
  appendChild(node) { this.children.push(node); node.parentElement = this; node.isConnected = true; }
  contains(node) { return this === node || this.children.includes(node); }
  remove() { this.isConnected = false; if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(n => n !== this); }
}
const payload = {sessionId:'10000000-0000-4000-8000-000000000001',settings:{show_native_fast_toggle:true}};
const calls = [];
const gatewayCalls = [];
const settingsShellDomain = {async codexGatewayCommand(command) {
  gatewayCalls.push(command);
  return {sessionId:command.sessionId,enabled:command.enabled,
    serviceTier:command.enabled?'fast':'default',gateway:true};
}};
function makeTrigger(tier = null, fastValue = 'fast') {
  const trigger = new Node(); trigger.setAttribute('data-codex-intelligence-trigger', 'true');
  const props = {selectedServiceTier:tier, serviceTierOptions:[{value:null}, {value:fastValue,iconKind:'fast'}],
    onSelectServiceTier(value) { calls.push(value); }};
  const root = {return:null}; root.stateNode = {current:root};
  trigger.__reactFiber$test = {memoizedProps:props,return:root};
  const parent = new Node(); parent.appendChild(trigger);
  return {trigger, props, parent};
}
let current = makeTrigger();
const frames = new Map(), timers = new Map(), observers = new Map(); let next = 0;
global.MutationObserver = class {
  constructor(fn) { this.callback = fn; }
  observe(node,opts) { this.node = node; this.opts = opts; }
  disconnect() { this.disconnected = true; }
};
global.document = {
  createElement() { return new Node(); }, querySelector() { return null; },
  querySelectorAll(selector) {
    if (selector.includes('button[')) return current ? [current.trigger] : [];
    return [];
  },
};
global.window = {};
const ctx = {state:{payload:()=>payload}, frames:{
  cancel(owner) { frames.delete(owner); }, schedule(owner,fn) { frames.set(owner, fn); return 1; }},
  lifecycle:{listen(){},clearTimeout(id){timers.delete(id);},timeout(owner,fn){timers.set(++next, fn); return next;}},
  observers:{clear(owner){observers.get(owner)?.disconnect();observers.delete(owner);},set(owner,o){observers.set(owner,o);}}};
const shared = {normalize:v=>String(v||'').trim(),visible:n=>!!n?.isConnected,cssEscape:String};
const codexModelPickerCatalog = [];
const modelPickerPatchHandlerName='handler',modelPickerPatchRafName='raf',modelPickerPatchTimersName='timers',modelPickerSelectionName='selection';
function flush() { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn=>fn()); }
function button() { return current.parent.children.find(n=>n.hasAttribute('data-codex-usage-hud-fast-toggle')); }
function apply() { domain.apply(); flush(); }
async function click(node=button()) { await node.events.click({preventDefault(){},stopPropagation(){}}); flush(); }
"""


def run_js(body):
    factory = TEXT.split("  const modelPickerDomain = ctx.domains.register(", 1)[0]
    result = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=HARNESS + factory + "\nconst domain = createModelPickerDomain(ctx, shared); domain.install();\n"
        + "(async()=>{\n" + body + "\n})().catch(e=>{console.error(e);process.exitCode=1;});",
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("tier", ["fast", "priority", "provider-fast"])
def test_native_callback_uses_offered_values_and_never_optimistically_marks_enabled(tier):
    run_js("current = makeTrigger(null, " + repr(tier) + r""");
apply();
assert.equal(button().getAttribute('aria-checked'), 'false');
await click(); assert.deepEqual(calls, [current.props.serviceTierOptions[1].value]);
assert.equal(button().getAttribute('aria-checked'), 'false');
current.props.selectedServiceTier = calls[0]; apply();
assert.equal(button().getAttribute('aria-checked'), 'true');
await click(); assert.equal(calls[1], null);
""")


def test_missing_native_capabilities_do_not_disable_user_request_choice():
    run_js(r"""
for (const modify of [
  p=>p.serviceTierOptions=[], p=>p.onSelectServiceTier=null,
  p=>p.serviceTierOptionsLoading=true, p=>p.disabled=true,
  p=>p.serviceTierOptions[1].disabled=true, p=>p.serviceTierOptions[0].disabled=true,
  p=>p.serviceTierOptions=[{value:'fast',iconKind:'fast'}],
  p=>p.serviceTierOptions=[{value:null},{iconKind:'fast'}],
]) {
  current = makeTrigger(); modify(current.props); apply();
  assert.equal(button().disabled, false);
  assert(!button().textContent.includes('不可用')); await click();
}
current = makeTrigger(); current.trigger.disabled = true; apply(); await click();
current = makeTrigger(); current.trigger.setAttribute('aria-disabled','true'); apply(); await click();
assert.deepEqual(calls, []);
assert.equal(gatewayCalls.length,10);
assert(gatewayCalls.every(c=>c.action==='fastRequestSet'));
""")


def test_click_resolves_current_committed_react_props_and_discards_old_chat_button():
    run_js(r"""
apply(); const oldButton = button(); const oldObserver = observers.get('native_fast');
current = makeTrigger('fast');
await click(oldButton); assert.deepEqual(calls, []);
apply(); assert.equal(oldButton.isConnected,false); assert.equal(oldObserver.disconnected,true);
const fiber = current.trigger.__reactFiber$test;
fiber.return.stateNode.current = {};
fiber.alternate = {memoizedProps:{...current.props,selectedServiceTier:null,
  onSelectServiceTier(v){calls.push(['committed',v]);}},return:fiber.return};
await click(); assert.deepEqual(calls, [['committed','fast']]);
""")


def test_state_observer_syncs_and_ignores_own_mutations_and_dispose_cleans_up():
    run_js(r"""
apply(); const owned = button(), observer = observers.get('native_fast');
assert.equal(observer.node,current.parent);
current.props.selectedServiceTier = 'fast';
observer.callback([{target:current.trigger}]); flush();
assert.equal(button().getAttribute('aria-checked'),'true');
observer.callback([{target:owned}]); assert.equal(frames.size,0);
payload.settings.show_native_fast_toggle=false; apply();
assert.equal(owned.isConnected,false); assert.equal(observers.size,0); assert.deepEqual(calls,[]);
payload.settings.show_native_fast_toggle=true; apply(); const second=button();
domain.dispose(); assert.equal(second.isConnected,false); assert.equal(observers.size,0);
assert.equal(frames.size,0); assert.equal(timers.size,0);
""")


def test_pending_failure_and_ultrafast_preserve_native_state():
    run_js(r"""
apply(); let reject;
current.props.onSelectServiceTier=v=>{calls.push(v);return new Promise((_,r)=>reject=r);};
const request=click(); assert.equal(button().disabled,true);
await click(); assert.deepEqual(calls,['fast']);
reject(new Error('offline')); await request;
assert.equal(button().disabled,false); assert.match(button().title,/offline/);
assert.equal(button().getAttribute('aria-checked'),'false');
current.props.serviceTierOptions.push({value:'ultrafast',iconKind:'ultrafast'});
current.props.selectedServiceTier='ultrafast';
current.props.onSelectServiceTier=v=>calls.push(v); apply();
assert.match(button().textContent,/Ultrafast/); await click();
assert.deepEqual(calls,['fast','fast']);
""")


def test_gateway_choices_are_confirmed_and_scoped_to_each_chat():
    run_js(r"""
payload.settings.provider_gateway={active:true};
current.props.serviceTierOptions=[]; current.props.onSelectServiceTier=undefined;
apply(); await click();
assert.equal(button().getAttribute('aria-checked'),'true');
assert.match(button().title,/上游决定/);
const firstId=payload.sessionId;
payload.sessionId='10000000-0000-4000-8000-000000000002';apply();
assert.equal(button().getAttribute('aria-checked'),'false');
await click(); assert.equal(gatewayCalls[1].sessionId,payload.sessionId);
payload.sessionId=firstId;apply();assert.equal(button().getAttribute('aria-checked'),'true');
await click();assert.equal(button().getAttribute('aria-checked'),'false');
assert.equal(gatewayCalls[2].enabled,false);
assert.deepEqual(calls,[]);
""")


def test_unconfirmed_gateway_write_leaves_state_unchanged_and_can_retry():
    run_js(r"""
current.props.serviceTierOptions=[]; apply();
settingsShellDomain.codexGatewayCommand=async()=>{throw new Error('write failed');};
await click();assert.equal(button().getAttribute('aria-checked'),'false');
assert.equal(button().disabled,false);assert.match(button().title,/write failed/);
settingsShellDomain.codexGatewayCommand=async c=>({sessionId:c.sessionId,enabled:c.enabled,serviceTier:'fast',gateway:false});
await click();assert.equal(button().getAttribute('aria-checked'),'true');
assert.match(button().title,/开启 HUD 请求代理/);
""")


def test_native_new_chat_does_not_reuse_previous_payload_identity():
    run_js(r"""
Object.assign(current.props,{model:'test',reasoningEffort:'high',models:[],modelOptions:[],onSelectModel(){}});
current.trigger.__reactFiber$test.return.memoizedProps={conversationId:null};
current.props.serviceTierOptions=[];apply();await click();
assert.equal(gatewayCalls[0].sessionId,'');assert.match(button().title,/新聊天默认/);
""")


def test_show_native_fast_round_trips_and_updates_only_settings(tmp_path):
    original = UserConfig.from_dict(UserConfig.defaults().to_dict())
    assert original.show_native_fast_toggle is False
    enabled = config_from_payload(original, {"show_native_fast_toggle": True})
    store = UserConfigStore(tmp_path / "settings.json")
    store.save(enabled)
    assert store.load().show_native_fast_toggle is True
    keys = changed_config_keys(original, enabled)
    assert keys == {"show_native_fast_toggle"}
    assert partial_domains_for_changed_config(keys) == {"settings"}
    assert config_from_payload(enabled, {"show_native_fast_toggle": False}).show_native_fast_toggle is False
    assert UserConfig.from_dict({"show_native_fast_toggle": "invalid"}).show_native_fast_toggle is False


def test_settings_checkbox_renders_and_collects_without_touching_native_speed():
    toggle_start = SETTINGS.index("      function nativeFastSettingHtml(")
    toggle_end = SETTINGS.index("        function renderSettingsModal(", toggle_start)
    panel_start = SETTINGS.index("      function settingsPanelHtml(")
    panel_end = SETTINGS.index("      function restReminderStatusTitle(", panel_start)
    assert "show_native_fast_toggle" not in SETTINGS[panel_start:panel_end]
    footer_start = SETTINGS.index('<div class="codex-usage-hud-settings-actions">', toggle_end)
    footer_end = SETTINGS.index("const nextDialog", footer_start)
    assert '${activeTab === "settings" ? nativeFastSettingHtml(settings) : ""}' in SETTINGS[footer_start:footer_end]
    assert SETTINGS.index("nativeFastSettingHtml(settings)", footer_start) < SETTINGS.index('data-action="settings-manage"', footer_start)
    collect_start = SETTINGS.index("      function collectSettingsForm()")
    collect_end = SETTINGS.index("      function collectCodexProviderUpdates()", collect_start)
    script = r"""
const assert = require('node:assert/strict');
const settings = {show_native_fast_toggle:false}, draft = {order:[],providers:{}};
function ensureSettingsProviderDraft(){return draft;}
function workOverlaySelectableMax(){return 4;}
const escapeHtml = String, thresholdText = ()=>'', desktopOverlayDependencyHtml=()=>'',settingsProviderEditorHtml=()=>'';
let settingsProviderDraft = null;
const settingsModalId = 'settings';
let checkbox = {checked:true};
global.document = {getElementById(){return {querySelector(selector){return selector.includes('show_native_fast_toggle') ? checkbox : null;}};}};
const hudSettingsFromPayload = ()=>settings, captureSettingsProviderForm=()=>{};
""" + SETTINGS[toggle_start:toggle_end] + SETTINGS[collect_start:collect_end] + r"""
let html = nativeFastSettingHtml(settings);
assert.match(html, /data-setting-key="show_native_fast_toggle"/);
assert(!html.includes('show_native_fast_toggle" checked'));
assert.equal(collectSettingsForm().show_native_fast_toggle,true);
settings.show_native_fast_toggle=true;
assert(nativeFastSettingHtml(settings).includes('show_native_fast_toggle" checked'));
checkbox.checked=false; assert.equal(collectSettingsForm().show_native_fast_toggle,false);
checkbox=null; assert.equal(collectSettingsForm().show_native_fast_toggle,true);
"""
    result = subprocess.run(["node", "--input-type=commonjs"], input=script,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
