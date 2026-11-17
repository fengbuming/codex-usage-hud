"""On-demand history must preserve data and acknowledged renderer ownership."""

from collections.abc import Mapping
from datetime import datetime, timezone
import json

from codex_usage_hud.core.parser import ParsedSession, RequestRound, RequestTokens, TaskHistory
from codex_usage_hud.disk_sequence import DecodedHistoryCache, DiskSequence
from codex_usage_hud.renderer_history import RendererHistoryStore
from codex_usage_hud import renderer_payload_builder as builder


def request(index, text=''):
    return RequestRound(index=index, status='confirmed', model='gpt-5.5', input_tokens=100,
                        cached_tokens=20, output_tokens=10, reasoning_tokens=0, total_tokens=110,
                        estimated=False, cost_usd=index / 100, copy_text=text, activity_texts=('locate full text',))


def snapshot():
    moment = datetime(2026, 11, 12, 8, tzinfo=timezone.utc)
    tasks = [TaskHistory(index=i, count=3, prompt=f'用户需求 {i}', turn_id=f'turn-{i}',
                         started_at=moment, completed_at=moment, last_event_time=moment,
                         request_history=[request(i, f'完整复制 {i} ' * 2000)],
                         request=RequestTokens(status='confirmed', model='gpt-5.5')) for i in range(1, 4)]
    return ParsedSession(session_id='one', status='parsed', task_index=3, task_count=3,
                         request=RequestTokens(status='confirmed', model='gpt-5.5'),
                         request_history=tasks[-1].request_history,
                         session_request_history=[item.request_history[0] for item in tasks],
                         activity_tasks=tasks)


def resolve(store, value):
    if isinstance(value, Mapping):
        if value.get('_hudHistoryRef'):
            return resolve(store, store.read(value['_hudHistoryRef'])['value'])
        return {key: resolve(store, item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(store, item) for item in value]
    return value


def test_default_projection_stays_compatible_and_refs_return_complete_copy():
    store = RendererHistoryStore()
    store.url = 'http://127.0.0.1:1/renderer-history?access_token=test'
    snap = snapshot()
    baseline = builder.payload_from_snapshot(snap, support_images=[])
    try:
        scope = store.begin(snap)
        payload = builder.payload_from_snapshot(snap, support_images=[], history_scope=scope)
        scope.finish()
        assert len(json.dumps(payload.to_json())) < len(json.dumps(baseline.to_json())) / 3
        tasks = payload.top_details['activityTasks']
        assert 'detailRef' not in tasks[-1] and 'activityTrail' in tasks[-1]
        assert 'activityTrail' not in tasks[0]
        assert resolve(store, tasks[0]['detailRef']) == baseline.top_details['activityTasks'][0]
        assert resolve(store, payload.top_details['heavyRounds']) == baseline.top_details['heavyRounds']
        assert payload.top_details['heavyRounds'][0]['copyText']['kind'] == 'text'
        assert store.read('unknown') is None
    finally:
        store.close()
    assert not store._tokens


def test_failed_send_keeps_acknowledged_text_and_success_retires_old_generations():
    store = RendererHistoryStore()
    store.url = 'http://127.0.0.1/history'
    snap = snapshot()
    scope = store.begin(snap)
    ref = scope.externalize('old full text ' * 1000, text=True)
    scope.finish()
    scope.acknowledge()
    for index in range(4):
        scope = store.begin(snap)
        scope.externalize(f'changed {index} ' * 1000, text=True)
        scope.finish()
        assert store.read(ref['_hudHistoryRef'])['value'] == 'old full text ' * 1000
    scope.acknowledge()
    scope = store.begin(snap)
    scope.externalize('final ' * 1000, text=True)
    scope.finish()
    assert store.read(ref['_hudHistoryRef']) is None
    store.close()


def test_lazy_task_fetch_survives_refresh_and_does_not_fetch_unselected_task():
    store = RendererHistoryStore()
    store.url = 'http://127.0.0.1/history'
    snap = snapshot()
    scope = store.begin(snap)
    called = []
    tasks = scope.tasks(snap, lambda snap, task: called.append(task.index) or {'copies': {'slow': 'complete ' * 1000}})
    scope.finish()
    scope.acknowledge()
    assert called == [3]  # Only the current task is projected for normal updates.
    ref = tasks[0]['detailRef']
    value = store.read(ref['_hudHistoryRef'])['value']
    assert called == [3, 1]
    nested = value['copies']['slow']
    scope = store.begin(snap)
    # Plain-list tasks intentionally have per-build revision identity.
    scope.tasks(snap, lambda snap, task: {})
    scope.finish()
    assert resolve(store, nested) == 'complete ' * 1000
    store.close()


def test_session_lru_eviction_and_close_remove_temporary_artifacts():
    store = RendererHistoryStore(max_sessions=2)
    refs = []
    files = []
    for session in ('a', 'b', 'c'):
        scope = store.begin(ParsedSession(session_id=session))
        ref = scope.externalize(session * 4096, text=True)
        refs.append(ref)
        files.append(store._tokens[ref['_hudHistoryRef']].file)
    assert files[0].closed
    assert store.read(refs[0]['_hudHistoryRef']) is None
    store.close()
    assert all(file.closed for file in files)


def test_disk_task_revision_reuses_historical_projection_on_no_content_change(monkeypatch):
    from codex_usage_hud.core.selected_history import _TaskHistoryView

    store = RendererHistoryStore()
    store.url = 'http://127.0.0.1/history'
    snap = snapshot()
    cache = DecodedHistoryCache()
    tasks = snap.activity_tasks
    closed = DiskSequence(tasks[:-1], cache=cache)
    current = DiskSequence(tasks[-1:], cache=cache)
    metadata = tuple({'index':t.index,'turnId':t.turn_id,'currentTask':t.prompt,'timed':False} for t in tasks[:-1])
    snap.activity_tasks = _TaskHistoryView(closed.freeze(), current, 3, frozenset(), snap.refreshed_at, metadata)
    calls = []
    original = builder._top_heavy_rounds
    monkeypatch.setattr(builder, '_top_heavy_rounds', lambda value, **kw: calls.append(len(value.activity_tasks)) or original(value, **kw))
    try:
        for index in range(2):
            scope = store.begin(snap)
            payload = builder.payload_from_snapshot(snap, support_images=[], history_scope=scope)
            scope.finish()
            scope.acknowledge()
            assert resolve(store, payload.top_details['heavyRounds']) == original(snap)
        assert calls == [2, 1, 1]
    finally:
        store.close()
