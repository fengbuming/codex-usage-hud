"""Provider deletion hides the settings row before background work completes."""

import json
import subprocess

from codex_usage_hud.renderer_assets.settings_shell import TEXT


def test_provider_delete_ui_is_immediate_and_correlates_background_results():
    def section(start, end):
        offset = TEXT.index(start)
        return TEXT[offset:TEXT.index(end, offset)]

    source = {
        "confirm": section("function confirmProviderDeleteDialog()", "function typedSettingsRequestId"),
        "names": section("function settingsProviderNames(settings)", "function cloneSettingsPriceTable"),
        "workflow": section("function clearProviderDeleteWorkflow()", "function openSettingsLoading("),
        "terminal": section("let providerDeleteTerminalHandled = false;", 'if (status && typeof status === "object"'),
        "accepted": section('if (String(status.action || "") === "deleteProvider" && !providerDeleteTerminalHandled)', "if (!providerDeleteTerminalHandled)"),
    }
    probe = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = JSON.parse(fs.readFileSync(0, 'utf8'));
function fixture({history = true, prices = true, accepted = true} = {}) {
  const payload = {app_provider: 'app', provider_order: ['app', 'target', 'other'],
    provider_registry: {app: {}, target: {}, other: {}},
    provider_settings: {app: {}, target: {model_prices: {m: {input: 7}}}, other: {}}};
  const makeDraft = () => ({appProvider: 'app', activeProvider: 'target',
    order: ['app', 'target', 'other'], providers: {app: {}, target: {value: 7}, other: {}},
    quickLaunchProviders: new Set(['target', 'other'])});
  const original = JSON.stringify(payload);
  const events = [];
  let serial = 0;
  const context = {
    settingsModalId: 'settings', settingsProviderName: 'selected', window: {},
    settingsProviderDraft: makeDraft(), settingsDirtyProviders: new Set(['target', 'other']),
    codexProviderDrafts: new Map([['target', {apiKey: 'unsaved'}], ['other', {}]]),
    codexProviderDirty: new Set(['target', 'other']),
    providerDeleteUiState: new Map(), pricingWorkflowState: {},
    document: {querySelector: selector => selector.includes('provider-delete-dialog') ? {
      dataset: {providerDeleteProvider: ' TARGET '},
      querySelector: selector => ({checked: selector.includes('session-history') ? history : prices}),
    } : null},
    hudSettingsFromPayload: () => payload,
    typedSettingsRequestId: () => `req-${++serial}`,
    closeSettingsConfirm: () => events.push('close'),
    renderSettingsProviderEditor: () => events.push('editor'),
    renderSettingsProviderTabs: () => events.push('tabs'),
    setSettingsStatus: (message, kind) => events.push({message, kind}),
    providerDisplayName: (_settings, provider) => provider,
    modal: {querySelector: () => null},
    submitSettingsCommand: command => {
      assert(!context.settingsProviderDraft.providers.target, 'must hide before dispatch');
      assert.deepEqual(context.settingsProviderDraft.order, ['app', 'other']);
      assert.equal(context.settingsProviderDraft.activeProvider, 'app');
      assert.equal(context.window.selected, 'app');
      assert.equal(context.codexProviderDrafts.has('target'), false);
      assert.equal(context.settingsDirtyProviders.has('target'), false);
      assert.equal(events[0], 'close');
      assert.deepEqual(JSON.parse(JSON.stringify(command)), {action: 'deleteProvider',
        requestId: `req-${serial}`, provider: 'target', deleteModelPrices: prices, deleteSessionHistory: history});
      return accepted;
    },
  };
  vm.createContext(context);
  vm.runInContext(source.confirm + source.names + source.workflow, context);
  return {c: context, payload, original, makeDraft};
}
function names(c, payload) {return Array.from(c.settingsProviderNames(payload));}
function terminal(c, state, provider = 'target', requestId = 'req-1') {
  c.syncProviderDeleteUi({sessionCleanup: {operation: {action: 'providerDelete',
    provider, requestId, state}}});
}
function apply(c, payload = {}, status) {
  c.syncProviderDeleteUi(payload, status);
  c.payload = payload;
  c.status = status;
  // Run the actual status handlers in their original order after reconciliation.
  vm.runInContext('{' + source.terminal + 'if (status) {' + source.accepted + '}}', c);
}
for (const history of [false, true]) for (const prices of [false, true]) {
  const {c, payload, original, makeDraft} = fixture({history, prices});
  assert.equal(c.confirmProviderDeleteDialog(), true);
  assert.equal(c.pricingWorkflowState.providerDeleteRequestId, 'req-1');
  assert.equal(JSON.stringify(payload), original, 'optimistic removal must preserve payload');
  assert.deepEqual(names(c, payload), ['app', 'other']);
  for (let i = 0; i < 100; i++) {
    c.syncProviderDeleteUi({}, {action: 'deleteProvider', providerId: 'target',
      requestId: 'req-1', providerDeletePending: true});
    assert.deepEqual(names(c, payload), ['app', 'other'], 'pending payload must not re-add');
  }
  c.settingsProviderDraft = null;
  assert.deepEqual(names(c, payload), ['app', 'other'], 'reopening must filter stale payload');
  terminal(c, 'completed');
  assert.equal(c.providerDeleteUiState.get('target').pending, false);
  assert.deepEqual(names(c, payload), ['app', 'other']);
  c.settingsProviderDraft = makeDraft();
  c.removeProviderFromSettingsUi('target');
  c.removeProviderFromSettingsUi('target');
  assert.equal(c.settingsProviderDraft.providers.target, undefined);
}
for (const failure of ['rejected', 'transport', 'status', 'worker', 'closed']) {
  const {c, payload} = fixture({accepted: failure !== 'rejected'});
  const entry = c.settingsProviderDraft.providers.target;
  const codexDraft = c.codexProviderDrafts.get('target');
  c.confirmProviderDeleteDialog();
  if (failure === 'transport') {
    c.handleSettingsCommandSubmissionError(new Error('offline'),
      {action: 'deleteProvider', provider: 'target', requestId: 'req-1'});
  } else if (failure === 'status') {
    c.syncProviderDeleteUi({}, {action: 'deleteProvider', requestId: 'req-1', kind: 'error'});
  } else if (failure === 'worker' || failure === 'closed') {
    if (failure === 'closed') c.settingsProviderDraft = null;
    terminal(c, 'failed');
  }
  assert.equal(c.providerDeleteUiState.has('target'), false);
  assert.deepEqual(names(c, payload), ['app', 'target', 'other']);
  if (failure !== 'closed') {
    assert.equal(c.settingsProviderDraft.providers.target, entry);
    assert.equal(c.codexProviderDrafts.get('target'), codexDraft);
    assert.deepEqual(c.settingsProviderDraft.order, ['app', 'target', 'other']);
    assert(c.settingsDirtyProviders.has('target'));
    assert(c.codexProviderDirty.has('target'));
    assert(c.settingsProviderDraft.quickLaunchProviders.has('target'));
  }
}
{
  const {c, payload} = fixture();
  c.confirmProviderDeleteDialog();
  terminal(c, 'failed', 'other');
  terminal(c, 'completed', 'target', 'old-request');
  c.handleSettingsCommandSubmissionError(new Error('old'),
    {action: 'deleteProvider', provider: 'target', requestId: 'old-request'});
  assert.equal(c.providerDeleteUiState.get('target').pending, true);
  assert.deepEqual(names(c, payload), ['app', 'other']);
  c.providerDeleteUiState.set('other', {requestId: 'req-other', pending: true,
    entry: c.settingsProviderDraft.providers.other, index: 2});
  c.removeProviderFromSettingsUi('other');
  terminal(c, 'completed', 'target');
  terminal(c, 'failed', 'other', 'req-other');
  assert.deepEqual(names(c, payload), ['app', 'other']);
  assert(c.settingsProviderDraft.providers.other);
  assert(!c.settingsProviderDraft.providers.target);
}
{
  const {c} = fixture({history: false});
  c.confirmProviderDeleteDialog();
  c.syncProviderDeleteUi({}, {action: 'deleteProvider', providerId: 'target', requestId: 'req-1'});
  terminal(c, 'failed');
  assert.equal(c.providerDeleteUiState.get('target').pending, false, 'late duplicate must not undo success');
}
{
  const {c, payload} = fixture();
  c.confirmProviderDeleteDialog();
  const accepted = {action: 'deleteProvider', providerId: 'target', requestId: 'req-1',
    message: 'queued', providerDeletePending: true};
  apply(c, {}, accepted);
  assert.equal(c.pricingWorkflowState.providerDeleteRequestId, 'req-1');
  apply(c, {sessionCleanup: {operation: {action: 'providerDelete', provider: 'target',
    requestId: 'req-1', state: 'failed', error: 'stop-failed'}}});
  assert(c.settingsProviderDraft.providers.target);
  assert.deepEqual(names(c, payload), ['app', 'target', 'other']);
  c.confirmProviderDeleteDialog();
  assert.equal(c.pricingWorkflowState.providerDeleteRequestId, 'req-2');
  apply(c, {sessionCleanup: {operation: {action: 'providerDelete', provider: 'target',
    requestId: 'req-1', state: 'completed'}}}, accepted);
  assert.equal(c.providerDeleteUiState.get('target').requestId, 'req-2');
  apply(c, {sessionCleanup: {operation: {action: 'providerDelete', provider: 'target',
    requestId: 'req-2', state: 'completed', providerResult: {message: 'deleted'}}}});
  assert.equal(c.pricingWorkflowState.providerDeleteRequestId, '');
  assert(!c.settingsProviderDraft.providers.target);
  assert.deepEqual(names(c, payload), ['app', 'other']);
  // Re-adding a completed provider must survive duplicate terminal payloads.
  c.providerDeleteUiState.delete('target');
  c.settingsProviderDraft.providers.target = {new: true};
  c.settingsProviderDraft.order.push('target');
  apply(c, {sessionCleanup: {operation: {action: 'providerDelete', provider: 'target',
    requestId: 'req-2', state: 'completed'}}});
  assert.equal(c.settingsProviderDraft.providers.target.new, true);
  assert.equal(c.providerDeleteUiState.has('target'), false);
}
console.log('immediate removal, stale payloads, rollback and request correlation passed');
'''
    result = subprocess.run(
        ["node", "-e", probe], input=json.dumps(source),
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_provider_delete_results_are_reconciled_when_settings_is_closed():
    start = TEXT.index("function applySettingsCommandStatus(payload)")
    end = TEXT.index("function submitSettingsCommand(", start)
    source = TEXT[start:end]
    assert source.index("syncProviderDeleteUi(payload, status);") < source.index(
        "if (!modal || modal.hidden) return;"
    )
