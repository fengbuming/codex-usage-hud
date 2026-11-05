import subprocess

from codex_usage_hud.renderer_assets.settings_shell import TEXT
from codex_usage_hud.renderer_assets.router import TEXT as ROUTER


def test_reload_picker_protects_drafts_waits_for_ack_and_keeps_failures_visible():
    start = TEXT.index("      function openSettingsReloadConfirm()")
    end = TEXT.index("      function restartCodexFromModal()", start)
    script = r'''
const assert=require('node:assert/strict');
const settingsDirtyProviders=new Set(),codexProviderDirty=new Set();
const layers=[],status=[],commands=[];
let selection={value:'proxy',disabled:false},closed=0,release,reject;
function settingsDialogRoot(){return {appendChild(x){layers.push(x)}}}
function captureSettingsProviderForm(){}
function setSettingsStatus(x){status.push(x)}
function closeSettingsConfirm(){}
function closeSettingsModal(){closed++}
function codexGatewayCommand(x){commands.push(x);return new Promise((a,b)=>{release=a;reject=b})}
const document={createElement(){return {dataset:{}}},querySelector(){return selection}};
''' + TEXT[start:end] + r'''
(async()=>{
 openSettingsReloadConfirm();assert.equal(layers.length,1);
 for(const mode of ['hud','proxy','all'])assert(layers[0].innerHTML.includes(`value="${mode}"`));
 codexProviderDirty.add('alpha');openSettingsReloadConfirm();assert.equal(layers.length,1);
 assert.match(status.at(-1),/未保存/);codexProviderDirty.clear();
 let pending=reloadHudFromModal();assert.equal(closed,0);assert(selection.disabled);
 release({});await pending;assert.equal(closed,0);assert.match(status.at(-1),/已有请求继续/);
 selection={value:'hud',disabled:false};pending=reloadHudFromModal();assert.equal(closed,0);
 release({});await pending;assert.equal(closed,1);
 selection={value:'all',disabled:false};pending=reloadHudFromModal();reject(new Error('keep proxy'));
 await pending;assert.equal(closed,1);assert.equal(selection.disabled,false);assert.match(status.at(-1),/keep proxy/);
 assert.deepEqual(commands.map(x=>x.mode),['proxy','hud','all']);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_reload_actions_are_exported_and_routed_and_button_is_visible():
    assert 'data-action="settings-reload"' in TEXT
    assert TEXT.count("      openSettingsReloadConfirm,") == 1
    assert "    openSettingsReloadConfirm," in TEXT
    assert 'action.dataset.action === "settings-reload-confirm"' in ROUTER
    assert "void reloadHudFromModal();" in ROUTER
