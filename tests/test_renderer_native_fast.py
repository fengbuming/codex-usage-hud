"""Settings control the global Fast request override without renderer toggles."""

import subprocess

from codex_usage_hud.config import UserConfig, UserConfigStore
from codex_usage_hud.renderer_assets.layout_style import TEXT as LAYOUT
from codex_usage_hud.renderer_assets.model_picker import TEXT as MODEL_PICKER
from codex_usage_hud.renderer_assets.settings_shell import TEXT as SETTINGS
from codex_usage_hud.runtime_settings import (
    changed_config_keys,
    config_from_payload,
    partial_domains_for_changed_config,
)


def test_force_fast_round_trips_and_updates_only_settings(tmp_path):
    original = UserConfig.from_dict(UserConfig.defaults().to_dict())
    assert original.force_fast_requests is False
    enabled = config_from_payload(original, {"force_fast_requests": True})
    store = UserConfigStore(tmp_path / "settings.json")
    store.save(enabled)
    assert store.load().force_fast_requests is True
    keys = changed_config_keys(original, enabled)
    assert keys == {"force_fast_requests"}
    assert partial_domains_for_changed_config(keys) == {"settings"}
    assert config_from_payload(enabled, {"force_fast_requests": False}).force_fast_requests is False
    assert UserConfig.from_dict({"force_fast_requests": "invalid"}).force_fast_requests is False


def test_settings_checkbox_collects_force_fast_and_renderer_has_no_fast_switch():
    setting_start = SETTINGS.index("      function forceFastSettingHtml(settings)")
    setting_end = SETTINGS.index("        function renderSettingsModal(", setting_start)
    collect_start = SETTINGS.index("      function collectSettingsForm()")
    collect_end = SETTINGS.index("      function collectCodexProviderUpdates()", collect_start)
    script = r"""
const assert = require('node:assert/strict');
const settings = {force_fast_requests:false}, draft = {order:[],providers:{}};
function ensureSettingsProviderDraft(){return draft;}
function workOverlaySelectableMax(){return 4;}
const escapeHtml = String, thresholdText = ()=>'', desktopOverlayDependencyHtml=()=>'',settingsProviderEditorHtml=()=>'';
let settingsProviderDraft = null;
const settingsModalId = 'settings';
let checkbox = {checked:true};
global.document = {getElementById(){return {querySelector(selector){return selector.includes('force_fast_requests') ? checkbox : null;}};}};
const hudSettingsFromPayload = ()=>settings, captureSettingsProviderForm=()=>{};
""" + SETTINGS[setting_start:setting_end] + SETTINGS[collect_start:collect_end] + r"""
let html = forceFastSettingHtml(settings);
assert.match(html, /data-setting-key="force_fast_requests"/);
assert.match(html, /强制启用 Fast/);
assert(!html.includes('显示 Fast 控件'));
assert.equal(collectSettingsForm().force_fast_requests,true);
settings.force_fast_requests=true;
assert(forceFastSettingHtml(settings).includes('force_fast_requests" checked'));
checkbox.checked=false; assert.equal(collectSettingsForm().force_fast_requests,false);
checkbox=null; assert.equal(collectSettingsForm().force_fast_requests,true);
"""
    result = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "data-codex-usage-hud-fast-toggle" not in MODEL_PICKER
    assert "fastRequestSet" not in MODEL_PICKER
    assert "[data-codex-usage-hud-fast-toggle]" not in LAYOUT
