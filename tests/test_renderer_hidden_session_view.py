"""Background session projections coalesce instead of rebuilding DOM per token."""

import subprocess

from codex_usage_hud.renderer_assets.session_view import TEXT


def test_hidden_session_view_coalesces_and_resumes_latest_projection():
    start = TEXT.index("    let installed = false;")
    end = TEXT.index("    function dispose()", start)
    program = r'''
const assert = require('node:assert/strict');
let root = {dataset:{hudReady:'true'}};
const rootId='hud', calls=[], listeners=[];
const document={hidden:true,getElementById(){return root;}};
const ctx={lifecycle:{listen(scope,target,event,handler){listeners.push(handler);}}};
function applyCurrentSessionPayload(root,payload){calls.push(['full',payload.seq,root]);}
function applySessionSwitchPayload(root,payload){calls.push(['switch',payload.seq,root]);}
''' + TEXT[start:end] + r'''
for(let seq=0;seq<1000;seq++) apply(root,{seq});
assert.equal(calls.length,0,'hidden streaming must not render any intermediate state');
assert.equal(listeners.length,1,'no per-update listener or timer');
apply(root,{seq:1000},'sessionSwitch');
const replacement={dataset:{hudReady:'true'}};root=replacement;
document.hidden=false;listeners[0]();
assert.deepEqual(calls.map(c=>c.slice(0,2)),[['full',1000],['switch',1000]]);
assert(calls.every(c=>c[2]===replacement),'resume uses the current root');
listeners[0]();assert.equal(calls.length,2,'no replay on repeated visibility event');
document.hidden=true;apply(root,{seq:1001},'sessionSwitch');
document.hidden=false;listeners[0]();assert.deepEqual(calls.at(-1).slice(0,2),['switch',1001]);
document.hidden=true;apply(root,{seq:1002});
document.hidden=false;apply(root,{seq:1003},'sessionSwitch');
assert.deepEqual(calls.slice(-2).map(c=>c.slice(0,2)),[['full',1003],['switch',1003]]);
const count=calls.length;listeners[0]();assert.equal(calls.length,count);
document.hidden=true;root={dataset:{hudReady:'false'}};apply(root,{seq:1004});
assert.deepEqual(calls.at(-1).slice(0,2),['full',1004],'cold startup still mounts synchronously');
'''
    result = subprocess.run(
        ["node", "-"], input=program, text=True, capture_output=True,
        encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
