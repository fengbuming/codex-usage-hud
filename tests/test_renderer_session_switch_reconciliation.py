"""Exercise native navigation settling and provider identity handoff."""

import subprocess

from codex_usage_hud.renderer_assets.active_session import TEXT as ACTIVE
from codex_usage_hud.renderer_assets.settings_shell import TEXT as SETTINGS


def run_js(script: str) -> None:
    result = subprocess.run(
        ["node", "-"], input=script, text=True, capture_output=True,
        encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_switch_waits_for_commit_and_accepts_canonical_promotion() -> None:
    clear = ACTIVE[ACTIVE.index("      function clearActiveSessionSendFollowup()"):
                   ACTIVE.index("      function showActiveSessionFollowFeedback(")]
    follow = ACTIVE[ACTIVE.index("      function scheduleActiveSessionSendFollowup("):
                    ACTIVE.index("      function activeSessionComposerSubmitButton(")]
    run_js(r'''
const assert = require('node:assert/strict');
const window = {}, timers = new Map(), posted = [];
const activeSessionSendFollowupTimersName = 'followup', activeSessionSettledTimerName = 'settled';
const normalize = value => String(value || '').trim();
let serial = 0, selected = {sessionId:'old', rendererSessionId:'local:old'};
const ctx = {lifecycle:{
  clearTimeout(id) { timers.delete(id); },
  timeout(_owner, fn, ms) { const id = ++serial; timers.set(id, {fn, ms}); return id; },
}};
const readActiveSessionRef = () => selected;
const postActiveSession = (_reason, ref) => posted.push(ref);
const refreshActiveSessionObserver = () => {};
const step = () => {
  const [id, timer] = [...timers.entries()].sort((a,b) => a[1].ms-b[1].ms)[0];
  timers.delete(id); timer.fn();
};
''' + clear + follow + r'''
const raw = 'local:client-new-thread:temporary';
scheduleActiveSessionSendFollowup('click', raw);
assert.equal(posted.length, 0, 'capture-phase click must not publish the old page');
step(); // Native navigation still has not committed at 32ms.
assert(timers.size > 0, 'a slow native transition must not cancel later reconciliation');
selected = {sessionId:'', rendererSessionId:raw, rawSessionId:raw, pendingSession:true};
step();
assert.equal(posted.at(-1).pendingSession, true);
selected = {sessionId:'canonical', rendererSessionId:'local:canonical', rawSessionId:raw};
step(); // Canonical props arrived without another DOM attribute mutation.
assert.equal(posted.at(-1).sessionId, 'canonical');
assert.equal(timers.size, 0, 'confirmed selection must leave no recurring work');

posted.length = 0;
selected = {sessionId:'another', rendererSessionId:'local:another'};
scheduleActiveSessionSendFollowup('click', raw);
while (timers.size) step();
assert.equal(posted.length, 0, 'navigation away must never publish another thread under the old click');
assert.equal(timers.size, 0, 'unresolved transitions have a finite retry budget');
''')


def test_dom_navigation_starts_only_one_settling_burst_per_pending_identity() -> None:
    schedule = ACTIVE[ACTIVE.index('      function scheduleActiveSessionReport('):
                      ACTIVE.index('      function scheduleActiveSessionDraftReport(')]
    run_js(r'''
const assert = require('node:assert/strict');
const window = {}, activeSessionTimerName = 'report';
let pendingReconcileKey = '', callback, selected = {pendingSession:true, rendererSessionId:'raw-a', title:'A'};
const bursts = [], posted = [];
const ctx = {lifecycle:{clearTimeout(){}, timeout(_owner, fn){callback=fn;return 1;}}};
const readActiveSessionRef = () => selected;
const postActiveSession = (_reason, ref) => posted.push(ref);
const refreshActiveSessionObserver = () => {};
const scheduleActiveSessionSendFollowup = (reason, id) => bursts.push({reason,id});
''' + schedule + r'''
scheduleActiveSessionReport('active-session-dom'); callback();
assert.deepEqual(bursts, [{reason:'active-session-dom',id:'raw-a'}]);
for (let i=0;i<5;i++) { scheduleActiveSessionReport('payload'); callback(); }
assert.equal(bursts.length, 1, 'HUD refreshes must not keep restarting a pending retry budget');
selected = {pendingSession:true, rendererSessionId:'raw-b', title:'B'};
scheduleActiveSessionReport('active-session-dom'); callback();
assert.equal(bursts.length, 2);
selected = {sessionId:'b', rendererSessionId:'local:b', title:'B'};
scheduleActiveSessionReport('active-session-dom'); callback();
assert.equal(bursts.length, 2);
assert.equal(pendingReconcileKey, '');
selected = {newSession:true, title:''};
scheduleActiveSessionReport('active-session-dom'); callback();
assert.equal(bursts.length, 2, 'an idle welcome page needs no reconciliation polling');
''')


def test_confirmed_exact_row_mapping_survives_a_provisional_dom_key() -> None:
    factory = ACTIVE.split('  const activeSessionDomain = ctx.domains.register(', 1)[0]
    run_js(r'''
const assert = require('node:assert/strict');
const window = {};
const normalize = value => String(value || '').trim();
''' + factory + r'''
const raw = 'local:client-new-thread:temporary';
const id = '01a1af04-3818-7293-8b84-261899be67fe';
let payload = {rendererSessionId:raw, sessionId:id, followState:'confirmed'};
const domain = createActiveSessionDomain({state:{payload:()=>payload}}, {});
const row = {matches:()=>true, querySelector:()=>({textContent:'Old rendered title'}),
  getAttribute(name) {return ({'data-app-action-sidebar-thread-id':raw,
    'data-app-action-sidebar-thread-host-id':'local',
    'data-app-action-sidebar-thread-title':'Current native title'})[name] || '';}};
let ref = domain.activeSessionRefFromRow(row);
assert.equal(ref.sessionId, id);
assert.equal(ref.rendererSessionId, `local:${id}`);
assert.equal(ref.rawSessionId, raw);
assert.equal(ref.title, 'Current native title');
assert(!ref.pendingSession);
for (const change of [{rendererSessionId:'other'}, {pendingSession:true},
  {newSession:true}, {cachedPreview:true}, {followState:'pending'}, {sessionId:'not-a-canonical-id'}]) {
  payload = {rendererSessionId:raw, sessionId:id, followState:'confirmed', ...change};
  ref = domain.activeSessionRefFromRow(row);
  assert.equal(ref.sessionId, '');
  assert.equal(ref.pendingSession, true);
}
''')


def test_provider_label_waits_and_uses_only_the_matching_confirmed_identity() -> None:
    context = SETTINGS[SETTINGS.index('      function codexProviderSwitchSessionContext()'):
                       SETTINGS.index('      function handleCodexCliQuickLaunchMenuClick(')]
    label = SETTINGS[SETTINGS.index('      function codexCliQuickLaunchProviderLabelState()'):
                     SETTINGS.index('      function syncCodexCliQuickLaunchProviderLabel(')]
    run_js(r'''
const assert = require('node:assert/strict');
const raw = 'local:client-new-thread:temporary';
const id = '01a1af04-3818-7293-8b84-261899be67fe';
let live = {pendingSession:true, rendererSessionId:raw};
let payload = {sessionId:'old', activeSessionProvider:'aiapi'};
const readActiveSessionRef = () => live, currentPayload = () => payload;
const hudSettingsFromPayload = () => ({default_provider:'aiapi', provider_gateway:{active:true}});
const providerDisplayName = (_settings, provider) => provider;
const codexProviderSwitchMenuState = {};
''' + context + label + r'''
assert.equal(codexProviderSwitchSessionContext().reading, true);
let state = codexCliQuickLaunchProviderLabelState();
assert.equal(state.provider, '');
assert.equal(state.text, '识别中…');
assert.equal(state.suffix, '');
payload = {sessionId:id, rendererSessionId:raw, activeSessionProvider:'custom', followState:'confirmed'};
assert.equal(codexProviderSwitchSessionContext().reading, false);
assert.equal(codexProviderSwitchSessionContext().sessionId, id);
assert.equal(codexCliQuickLaunchProviderLabelState().provider, 'custom');
for (const change of [{rendererSessionId:'different'}, {pendingSession:true}, {cachedPreview:true}]) {
  payload = {sessionId:id, rendererSessionId:raw, activeSessionProvider:'custom', followState:'confirmed', ...change};
  assert.equal(codexProviderSwitchSessionContext().reading, true);
  assert.equal(codexCliQuickLaunchProviderLabelState().provider, '');
}
live = {sessionId:'another'};
assert.equal(codexCliQuickLaunchProviderLabelState().text, '识别中…');
live = {sessionId:id}; payload = {sessionId:id};
assert.equal(codexProviderSwitchSessionContext().reading, true, 'missing provider is not the default provider');
live = {newSession:true};
assert.equal(codexProviderSwitchSessionContext().reading, false);
assert.equal(codexCliQuickLaunchProviderLabelState().provider, 'aiapi');
assert.equal(codexCliQuickLaunchProviderLabelState().suffix, '默认');
''')
