import subprocess

from codex_usage_hud.renderer_assets.settings_shell import TEXT
from codex_usage_hud.renderer_assets.router import TEXT as ROUTER


def management_script():
    start = TEXT.index("      function hudManagementDraftsPending()")
    end = TEXT.index("      function restartCodexFromModal()", start)
    return r'''
const assert=require('node:assert/strict');
const settingsDirtyProviders=new Set(),codexProviderDirty=new Set();
const layers=[],status=[],commands=[];
const settings={provider_gateway:{active:true,default_provider:'alpha'},provider_gateway_enabled:true};
let selection={value:'proxy',disabled:false},closed=0,release,reject,rendered=0;
let layer=null;
function settingsDialogRoot(){return {appendChild(x){layers.push(x);layer=x;}}}
function captureSettingsProviderForm(){}
function setSettingsStatus(x){status.push(x)}
function closeSettingsConfirm(){if(layer?.dataset.busy!=='true')layer=null;}
function closeSettingsModal(){closed++}
function codexGatewayCommand(x){commands.push(x);return new Promise((a,b)=>{release=a;reject=b})}
function hudSettingsFromPayload(){return settings;}
function currentPayload(){return {settings};}
function settingsProviderNames(){return ['codex_hud','alpha','beta'];}
function providerDisplayName(_s,p){return p;}
function escapeHtml(x){return String(x);}
function renderSettingsModal(){rendered++;}
const document={createElement(){return {dataset:{},querySelector(){return {focus(){}}},querySelectorAll(){return [selection]}}},querySelector(selector){
 if(selector.includes('data-hud-reload-mode'))return selection;
 if(selector.includes('data-hud-management-status'))return {dataset:{}};
 if(selector.includes('data-provider-exit-selection'))return {value:'beta'};
 if(selector.includes('data-hud-management'))return layer;
 return null;
}};
''' + TEXT[start:end]


def run_js(script):
    result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_reload_picker_protects_drafts_waits_for_ack_and_keeps_failures_visible():
    run_js(management_script() + r'''
(async()=>{
 openSettingsReloadConfirm();assert.equal(layers.length,1);
 for(const mode of ['hud','proxy','all'])assert(layers[0].innerHTML.includes(`value="${mode}"`));
 codexProviderDirty.add('alpha');openSettingsReloadConfirm();assert.equal(layers.length,1);
 assert.match(status.at(-1),/未保存/);codexProviderDirty.clear();
 let pending=reloadHudFromModal();assert.equal(closed,0);assert(selection.disabled);
 closeSettingsConfirm();assert(layer,'busy operation cannot be dismissed');
 await reloadHudFromModal();assert.equal(commands.length,1,'no duplicate submission');
 release({});await pending;assert.equal(closed,0);assert.match(status.at(-1),/已有请求继续/);
 openSettingsReloadConfirm();selection={value:'hud',disabled:false};pending=reloadHudFromModal();
 release({});await pending;assert.equal(closed,1);
 openSettingsReloadConfirm();selection={value:'all',disabled:false};pending=reloadHudFromModal();reject(new Error('keep proxy'));
 await pending;assert.equal(closed,1);assert.equal(selection.disabled,false);assert.match(status.at(-1),/keep proxy/);
 assert(layer,'failed dialog remains available');assert.equal(layer.dataset.busy,'false');
 assert.deepEqual(commands.map(x=>x.mode),['proxy','hud','all']);
 settings.provider_gateway.active=false;openSettingsManagement();
 assert(layer.innerHTML.includes('value="hud"'));assert(!layer.innerHTML.includes('value="proxy"'));
 assert(!layer.innerHTML.includes('value="all"'));
 openSettingsManagement('exit');assert(!layer.innerHTML.includes('data-provider-exit-selection'));
})().catch(error=>{console.error(error);process.exitCode=1});
''')


def test_management_and_gateway_actions_are_exported_and_routed():
    assert 'data-action="settings-manage"' in TEXT
    footer = TEXT.split('<div class="codex-usage-hud-settings-actions">', 1)[1].split('</div>\n          </div>', 1)[0]
    assert 'data-action="settings-manage" data-variant="ghost">重载/退出</button>' in footer
    assert 'codex-usage-hud-settings-footnote' not in TEXT
    assert 'data-setting-key="stop_hud_on_lock_screen"' not in TEXT
    # The gateway switch moved to the menubar provider menu.
    assert 'data-action="settings-gateway-toggle"' not in TEXT
    assert 'settings-gateway' not in ROUTER
    assert 'data-action="settings-reload"' not in TEXT
    assert 'data-action="settings-exit"' not in TEXT
    assert TEXT.count("      openSettingsManagement,") == 1
    assert "\n    openSettingsManagement," in TEXT
    for name in ('openSettingsGatewayConfirm', 'toggleSettingsGateway'):
        assert name not in TEXT
        assert name not in ROUTER
    assert 'action.dataset.action === "settings-reload-confirm"' in ROUTER
    assert "void reloadHudFromModal();" in ROUTER
