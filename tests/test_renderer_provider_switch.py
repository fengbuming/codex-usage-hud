"""Exercise the real menu switching lifecycle with a deterministic browser clock."""
import subprocess

from codex_usage_hud.renderer_assets.settings_shell import TEXT


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
