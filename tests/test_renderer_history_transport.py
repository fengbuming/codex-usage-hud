import json
import shutil
import subprocess

import pytest

from codex_usage_hud.renderer_payload_delta import RendererPayloadDeltaCache
from codex_usage_hud.renderer_client import RendererHudClient


def payload(session='one', value=1):
    current = {'sessionId': session, 'rendererSessionId': 'local:' + session,
               'topDetails': {'title': f'updated {value}', 'activityTasks': [{'index': 1}],
                              'heavyRounds': [{'copyText': 'same' * 1000}],
                              'activityTrail': [{'title': 'current'}], 'workTools': []}}
    return {**current, 'payloadDomains': {'currentSession': current}}


def test_history_delta_waits_for_ack_and_resets_on_new_session_or_page():
    cache = RendererPayloadDeltaCache()
    digest = RendererHudClient._payload_domain_digest
    first = payload()
    full, pending = cache.prepare(first, {}, digest)
    assert full == first
    assert cache.prepare(first, {'currentSession': 'ack'}, digest)[0] == first
    cache.commit(pending)
    second, pending = cache.prepare(payload(value=2), {'currentSession': 'ack'}, digest)
    assert second['topDetails']['_retainHistoryFields'] == ['activityTasks', 'heavyRounds', 'activityTrail', 'workTools']
    assert 'heavyRounds' not in second['topDetails']
    assert len(json.dumps(second)) < len(json.dumps(first)) / 4
    assert cache.prepare(payload('two'), {'currentSession': 'ack'}, digest)[0] == payload('two')
    assert cache.prepare(first, {}, digest)[0] == first


def test_renderer_hydrates_fields_and_rejects_an_unrelated_session():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    from codex_usage_hud.renderer_assets.router import TEXT
    source = TEXT[TEXT.index('  function hydrateRetainedHistory('):TEXT.index('  window.__codexUsageHudUpdate =')]
    cache = RendererPayloadDeltaCache()
    digest = RendererHudClient._payload_domain_digest
    first = payload()
    _, pending = cache.prepare(first, {}, digest)
    cache.commit(pending)
    second, _ = cache.prepare(payload(value=2), {'currentSession': 'ack'}, digest)
    script = source + '\n' + f'const first={json.dumps(first)};const second={json.dumps(second)};'
    script += r'''
const actual=hydrateRetainedHistory(second,first);
if(actual.topDetails.heavyRounds!==first.topDetails.heavyRounds)throw Error('history was copied/lost');
if('_retainHistoryFields' in actual.topDetails)throw Error('patch marker leaked');
let rejected=false;
try{hydrateRetainedHistory(second,{...first,sessionId:'other'});}catch(_){rejected=true;}
if(!rejected)throw Error('foreign session accepted');
console.log(JSON.stringify(actual));
'''
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, encoding='utf-8', timeout=10, check=True)
    assert json.loads(result.stdout) == payload(value=2)


def test_browser_history_cache_cancels_stale_reads_and_keeps_only_three_tasks():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    from codex_usage_hud.renderer_assets.session_view import TEXT
    source = TEXT[TEXT.index('  const historyTaskCache ='):TEXT.index('  function speedTitleThreshold(')]
    script = r'''
let payload={sessionId:'one'};
function currentPayload(){return payload;}
const ctx={lifecycle:{active:()=>true}};
let calls=0;
let resolveFetch=null;
let responseMode='normal';
async function fetch(url,options){
 calls++;
 if(responseMode==='delayed')return new Promise(resolve=>{resolveFetch=resolve;});
 const id=url;
 return {ok:true,json:async()=>({ref:id,kind:'task',value:{text:'full '+id}})};
}
function renderTopDetails(){}
''' + source + r'''
(async()=>{
 syncHistorySession(payload);
 for(let i=0;i<5;i++)await readHistoryReference({_hudHistoryRef:String(i),kind:'task',url:String(i)});
 if(historyTaskCache.size!==3)throw Error('unbounded task cache');
 await readHistoryReference({_hudHistoryRef:'4',kind:'task',url:'4'});
 if(calls!==5)throw Error('unchanged task fetched again');
 responseMode='delayed';
 const pending=readHistoryReference({_hudHistoryRef:'old',kind:'task',url:'old'}).then(()=>false,()=>true);
 const request=historyRequests.get('old');
 payload={sessionId:'two'};syncHistorySession(payload);
 if(!request.controller.signal.aborted)throw Error('old fetch not cancelled');
 resolveFetch({ok:true,json:async()=>({ref:'old',kind:'task',value:{text:'stale'}})});
 if(!(await pending)||historyTaskCache.size)throw Error('stale result accepted');
 console.log('ok');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=10, check=True)
    assert result.stdout.strip() == 'ok'


def test_full_copy_survives_row_refresh_but_cancels_on_session_switch():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    from codex_usage_hud.renderer_assets.session_view import TEXT
    source = TEXT[TEXT.index('  const historyTaskCache ='):TEXT.index('  function speedTitleThreshold(')]
    script = r'''
let payload={sessionId:'one'};
const ctx={lifecycle:{active:()=>true}};
function currentPayload(){return payload;}
function renderTopDetails(){}
let finish;
function fetch(){return new Promise(resolve=>{finish=resolve;});}
''' + source + r'''
(async()=>{
 const ref={_hudHistoryRef:'full',kind:'text',url:'full'};
 const node={isConnected:true,dataset:{copyRef:JSON.stringify(ref)}};
 const pending=copyNodeText({},node);
 node.isConnected=false;
 const full='完整复制😀\n'.repeat(10000);
 finish({ok:true,json:async()=>({ref:'full',kind:'text',value:full})});
 if(await pending!==full)throw Error('same-chat copy was truncated or lost on refresh');
 const next=copyNodeText({},node).then(value=>value,()=>null);
 payload={sessionId:'two'};syncHistorySession(payload);
 finish({ok:true,json:async()=>({ref:'full',kind:'text',value:full})});
 if(await next!==null)throw Error('copied a stale chat response');
 console.log('ok');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=10, check=True)
    assert result.stdout.strip() == 'ok'


def test_reading_historical_task_reuses_result_without_rebuilding_current_view():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    from codex_usage_hud.renderer_assets.session_view import TEXT
    source = TEXT[TEXT.index('  const historyTaskCache ='):TEXT.index('  function speedTitleThreshold(')]
    script = r'''
const ref={_hudHistoryRef:'task-one',kind:'task',url:'task-one'};
let payload={sessionId:'chat'};
const ctx={lifecycle:{active:()=>true}};
function currentPayload(){return payload;}
let rerenders=0,fetches=0;
function renderTopDetails(){rerenders++;}
const root={isConnected:true,dataset:{activityTaskIndex:'1'}};
const detail={index:1,count:2,currentTask:'历史内容',activityTrail:[{title:'全部轨迹'}]};
async function fetch(){fetches++;return {ok:true,json:async()=>({ref:'task-one',kind:'task',value:detail})};}
''' + source + r'''
(async()=>{
 syncHistorySession(payload);
 const descriptor={index:1,count:2,detailRef:ref};
 const pending=selectedHistoryTask(root,payload,descriptor);
 if(pending.activityTrail.length)throw Error('displayed current task as historical detail');
 await readHistoryReference(ref);
 await Promise.resolve();
 const ready=selectedHistoryTask(root,payload,descriptor);
 if(ready!==detail||fetches!==1||rerenders!==1)throw Error('history hydration duplicated or lost');
 console.log('ok');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=10, check=True)
    assert result.stdout.strip() == 'ok'
