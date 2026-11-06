"""Native project new-chat pages must override stale sidebar selection."""

import subprocess

from codex_usage_hud.renderer_assets.active_session import TEXT as ACTIVE
from codex_usage_hud.renderer_assets.settings_shell import TEXT as SETTINGS


def run_js(script):
    result = subprocess.run(["node", "-"], input=script, text=True,
                            capture_output=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_home_overrides_stale_sidebar_in_provider_menu():
    read = ACTIVE[ACTIVE.index("      function activeSessionHomeLooksNewSession()"):
                  ACTIVE.index("      function activeSessionContainer()")]
    menu = SETTINGS[SETTINGS.index("      function codexProviderSwitchSessionContext()"):
                    SETTINGS.index("      function syncCodexCliQuickLaunchProviderLabel(")]
    run_js(r'''
const assert=require('node:assert/strict');
let locationId='', composer=true, hero={hidden:false,hud:false};
const location={href:'app://-/index.html'};
const document={querySelectorAll(selector){
  assert.equal(selector,'div.heading-xl[data-feature="game-source"]');
  return hero ? [hero] : [];
}};
const visible=node=>!!node&&!node.hidden;
const activeSessionNodeOwnedByHud=node=>node.hud;
const activeSessionLocationId=()=>locationId;
const activeSessionComposerVisible=()=>composer;
const activeSessionComposerDraftText=()=>'unsent draft';
const row={};
const activeSessionRows=()=>[row];
const activeSessionHeaderTitleText=()=>'Previous conversation';
const activeSessionHeaderLooksNewSession=()=>false;
const activeSessionRowSelected=()=>true;
const activeSessionRowMatchesLocation=()=>false;
const activeSessionRefFromRow=()=>({sessionId:'source',rendererSessionId:'local:source',rawSessionId:'native-row-key',title:'Previous conversation'});
const activeSessionTitleIsNewSession=()=>false;
const cleanActiveSessionTitle=value=>value;
const settings={provider_gateway:{active:true},default_provider:'alpha'};
const currentPayload=()=>({sessionId:'source',activeSessionProvider:'alpha',newSession:false});
const hudSettingsFromPayload=()=>settings;
const providerDisplayName=(_settings,provider)=>provider;
const submitted=[],dialogs=[],begun=[];
const item={dataset:{provider:'beta'}};
const codexCliQuickLaunchMenuState={};
const codexProviderSwitchMenuState={surface:{contains:()=>true}};
const closeCodexProviderSwitchMenu=()=>{};
const typedSettingsRequestId=()=>'switch';
const openCodexProviderMigrateDialog=context=>dialogs.push(context);
const beginDefaultProviderSwitch=context=>{begun.push(context);return true;};
const submitSettingsCommand=command=>{submitted.push(command);return true;};
const showDefaultProviderSwitchFeedback=()=>{throw Error('new chat must not wait for the previous conversation');};
const event={preventDefault(){},stopPropagation(){},target:{closest:selector=>selector.includes('provider-switch=')?item:null}};
''' + read + menu + r'''
const ref=readActiveSessionRef();
assert.equal(ref.newSession,true);assert.equal(ref.sessionId,'');assert.equal(ref.rendererSessionId,'');
assert.equal(ref.draftText,'unsent draft');assert.equal(ref.matchedBy,'home-hero');
assert.equal(codexProviderSwitchSessionContext().provider,'');
const label=codexCliQuickLaunchProviderLabelState();
assert.equal(label.provider,'alpha');assert.equal(label.suffix,'默认');
handleCodexCliQuickLaunchMenuClick(event);
assert.equal(submitted.length,1);assert.equal(submitted[0].provider,'beta');
assert.equal(dialogs.length,0);assert.equal(begun[0].migration,null);
// A mounted but hidden welcome page, HUD content, an absent composer, and
// an explicit conversation URL must not hide the current real conversation.
for (const condition of ['hidden','hud','no-composer','thread-url','absent']) {
  hero={hidden:condition==='hidden',hud:condition==='hud'};
  if(condition==='absent')hero=null;
  composer=condition!=='no-composer';locationId=condition==='thread-url'?'source':'';
  assert.equal(readActiveSessionRef().sessionId,'source',condition);
  assert.equal(readActiveSessionRef().rawSessionId,'native-row-key',condition);
  assert.equal(codexProviderSwitchSessionContext().provider,'alpha',condition);
  handleCodexCliQuickLaunchMenuClick(event);
}
assert.equal(dialogs.length,5);assert.equal(submitted.length,1);
''')


def test_home_observation_clears_recent_identity_without_settle_delay():
    post = ACTIVE[ACTIVE.index('      function postActiveSession('):
                  ACTIVE.index('      function scheduleActiveSessionReport(')]
    run_js(r'''
const assert=require('node:assert/strict');
const window={canonical:'source',canonicalAt:Date.now()};
const location={href:'app://-/index.html'};
const activeSessionCanonicalIdName='canonical',activeSessionCanonicalAtName='canonicalAt';
const activeSessionSettledTimerName='settled',activeSessionSelectionKeyName='selection';
const activeSessionSelectionSeqName='seq',activeSessionAppliedSeqName='applied';
const activeSessionLastSignatureName='signature',activeSessionBindingName='binding';
const normalize=value=>String(value||'').trim();
const normalizeThreadId=normalize;
const activeSessionIdIsProvisional=()=>false;
const settingsBridgeUrl=()=>'';
const sent=[],timers=[];
const ctx={bindings:{available:()=>true,send:(_name,payload)=>sent.push(payload)},
  lifecycle:{clearTimeout(){},timeout:(_scope,fn)=>{timers.push(fn);return timers.length;}}};
''' + post + r'''
const fresh={sessionId:'',title:'',newSession:true,draftText:'draft',matchedBy:'header-empty'};
postActiveSession('observer',fresh);
assert.equal(sent.length,0,'ambiguous header transitions retain their existing grace period');
assert.equal(timers.length,1);
postActiveSession('observer',{...fresh,matchedBy:'home-hero'});
assert.equal(sent.length,1,'visible home must clear the old thread immediately');
assert.equal(sent[0].sessionId,'');assert.equal(sent[0].newSession,true);
assert.equal(sent[0].reason,'new-session');assert.equal(sent[0].draftText,'draft');
assert.equal(window.canonical,'');assert.equal(window.canonicalAt,0);
''')
