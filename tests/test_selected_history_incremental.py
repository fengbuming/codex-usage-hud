"""Selected-history reuse must preserve the full parser's observable data."""

from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
import json

import pytest

from codex_usage_hud.core import parser as parser_module
from codex_usage_hud.core.parser import JsonlSessionParser
from codex_usage_hud.disk_sequence import DecodedHistoryCache, spill_snapshot_history


class FixedClock(datetime):
    instant = datetime(2026, 11, 12, 8, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    FixedClock.instant = datetime(2026, 11, 12, 8, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(parser_module, 'datetime', FixedClock)


def plain(value):
    if is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [plain(item) for item in value]
    return value


def record(kind, payload, second=0):
    return {'type': kind, 'payload': payload, 'timestamp': f'2026-11-12T07:{second // 60:02d}:{second % 60:02d}Z'}


def event(kind, second=0, **values):
    return record('event_msg', {'type': kind, **values}, second)


def usage(total, second=5):
    return event('token_count', second, info={'last_token_usage': {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 10, 'reasoning_output_tokens': 2, 'total_tokens': 110}, 'total_token_usage': {'total_tokens': total}})


def seed():
    return [record('session_meta', {'id': 'history', 'model_provider': 'openai'}),
            record('turn_context', {'model': 'gpt-5.5'}),
            event('task_started', 1, turn_id='one'), event('user_message', 2, message='第一项需求'),
            record('response_item', {'type': 'function_call_output', 'call_id': 'tool', 'output': '完整工具结果' * 1000}, 3),
            usage(110), event('task_complete', 6),
            event('task_started', 7, turn_id='two'), event('user_message', 8, message='第二项需求'), usage(220, 9)]


def write(path, rows, mode='w'):
    with path.open(mode, encoding='utf-8') as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + '\n')


def test_every_append_matches_full_parse_and_published_views_stay_frozen(tmp_path):
    path = tmp_path / 'session.jsonl'
    rows = seed()
    write(path, rows)
    parser = JsonlSessionParser()
    cache = DecodedHistoryCache(2048)
    state = None
    published = []
    batches = [[], [usage(220, 9)], [event('agent_message', 10, message='继续输出')],
               [usage(330, 11)], [record('turn_context', {'model': 'gpt-5.4'}, 12), usage(440, 13)],
               [usage(0, 14), usage(0, 14)],
               [event('task_complete', 15), event('task_started', 16, turn_id='three'), event('user_message', 17, message='第三项需求')],
               [event('thread_rolled_back', 18, num_turns=2), event('task_started', 19, turn_id='retry'), usage(550, 20)],
               [record('compacted', {'message': 'compaction'}, 21), event('context_compacted', 22), usage(660, 23)]]
    for batch in batches:
        write(path, batch, 'a')
        expected, _ = parser.parse_file_incremental(path)
        actual, state = parser.parse_file_incremental(path, state, _record_cache=cache)
        spill_snapshot_history(actual, cache)
        assert plain(actual) == plain(expected)
        for previous, frozen in published:
            assert plain(previous) == frozen
        published.append((actual, plain(actual)))
        assert cache.retained_bytes <= cache.max_bytes


def test_closed_tasks_and_session_rounds_are_not_rebuilt_on_append(tmp_path, monkeypatch):
    path = tmp_path / 'session.jsonl'
    write(path, seed())
    parser = JsonlSessionParser()
    cache = DecodedHistoryCache(4096)
    _, state = parser.parse_file_incremental(path, _record_cache=cache)
    processed = []
    original = parser.task_history_item
    monkeypatch.setattr(parser, 'task_history_item', lambda records, parsed, latest, starts, ordinal, rolled: (processed.append(ordinal), original(records, parsed, latest, starts, ordinal, rolled))[1])
    cursor_before = state.records._projection.cursor.next_record
    # The session accumulator should never inspect already consumed records.
    visit = parser.round_activity_entry
    seen = []
    monkeypatch.setattr(parser, 'round_activity_entry', lambda kind, typ, payload: (seen.append(payload.get('message')), visit(kind, typ, payload))[1])
    write(path, [event('agent_message', 11, message='追加内容')], 'a')
    _, state = parser.parse_file_incremental(path, state, _record_cache=cache)
    assert processed == [2]
    assert state.records._projection.cursor.next_record == cursor_before + 1
    # Current-task projection still reads its own prefix; completed task one does not.
    assert '第一项需求' not in seen


def test_pricing_change_and_file_reset_rebuild_full_history(tmp_path):
    path = tmp_path / 'session.jsonl'
    write(path, seed())
    parser = JsonlSessionParser()
    cache = DecodedHistoryCache(4096)
    _, state = parser.parse_file_incremental(path, _record_cache=cache)
    old = state.records._projection
    parser.cost_estimator.pricing_fingerprint = 'changed'
    actual, state = parser.parse_file_incremental(path, state, _record_cache=cache)
    expected, _ = parser.parse_file_incremental(path)
    assert plain(actual) == plain(expected)
    assert state.records._projection is not old
    write(path, seed()[:5])
    actual, state = parser.parse_file_incremental(path, state, _record_cache=cache)
    expected, _ = parser.parse_file_incremental(path)
    assert plain(actual) == plain(expected)
    replacement = path.with_suffix('.new')
    write(replacement, seed())
    replacement.replace(path)
    actual, state = parser.parse_file_incremental(path, state, _record_cache=cache)
    assert plain(actual) == plain(parser.parse_file_incremental(path)[0])


def test_reused_unfinished_historical_task_keeps_time_dependent_gap(tmp_path):
    path = tmp_path / 'session.jsonl'
    rows = seed()
    rows = [row for row in rows if row.get('payload', {}).get('type') != 'task_complete']
    write(path, rows)
    parser = JsonlSessionParser()
    cache = DecodedHistoryCache(4096)
    previous, state = parser.parse_file_incremental(path, _record_cache=cache)
    frozen = plain(previous)
    FixedClock.instant = datetime(2026, 11, 12, 8, 5, tzinfo=timezone.utc)
    actual, state = parser.parse_file_incremental(path, state, _record_cache=cache)
    assert plain(actual) == plain(parser.parse_file_incremental(path)[0])
    assert plain(previous) == frozen


def test_cursor_failure_resets_instead_of_losing_a_confirmed_round(tmp_path, monkeypatch):
    from codex_usage_hud.disk_sequence import DiskSequence

    path = tmp_path / 'session.jsonl'
    write(path, seed())
    parser = JsonlSessionParser()
    cache = DecodedHistoryCache(4096)
    _, state = parser.parse_file_incremental(path, _record_cache=cache)
    target = state.records._projection.rounds
    append = DiskSequence.append
    def fail(self, item):
        if self is target:
            raise OSError('simulated disk full')
        return append(self, item)
    write(path, [usage(330, 11)], 'a')
    with monkeypatch.context() as patch:
        patch.setattr(DiskSequence, 'append', fail)
        with pytest.raises(OSError):
            parser.parse_file_incremental(path, state, _record_cache=cache)
    actual, state = parser.parse_file_incremental(path, state, _record_cache=cache)
    assert plain(actual) == plain(parser.parse_file_incremental(path)[0])


def test_fork_replay_and_late_provider_metadata_keep_full_parse_semantics(tmp_path):
    path = tmp_path / 'fork.jsonl'
    rows = seed()
    rows[0]['payload'].update(forked_from_id='parent', source={'subagent': {'thread_spawn': {'parent_thread_id': 'parent'}}})
    rows.insert(5, event('thread_settings_applied', 3))
    write(path, rows)
    parser = JsonlSessionParser()
    cache = DecodedHistoryCache(4096)
    _, state = parser.parse_file_incremental(path, _record_cache=cache)
    write(path, [usage(330, 30), event('turn_aborted', 31)], 'a')
    actual, state = parser.parse_file_incremental(path, state, _record_cache=cache)
    assert plain(actual) == plain(parser.parse_file_incremental(path)[0])


def test_late_session_metadata_invalidates_cached_closed_task_provider(tmp_path):
    path = tmp_path / 'late-meta.jsonl'
    rows = [row for row in seed() if row['type'] != 'session_meta']
    write(path, rows)
    parser = JsonlSessionParser()
    cache = DecodedHistoryCache(4096)
    _, state = parser.parse_file_incremental(path, _record_cache=cache)
    write(path, [record('session_meta', {'id': 'late', 'model_provider': 'new-provider'}, 32)], 'a')
    actual, state = parser.parse_file_incremental(path, state, _record_cache=cache)
    expected, _ = parser.parse_file_incremental(path)
    assert plain(actual) == plain(expected)
    assert actual.activity_tasks[0].request_history[0].cost_usd == expected.activity_tasks[0].request_history[0].cost_usd
    # A log that only acquires its first metadata row later must reprice/rebuild
    # the same history the complete parser would see.
    write(path, seed()[1:])
    _, state = parser.parse_file_incremental(path, state, _record_cache=cache)
    write(path, [record('session_meta', {'id': 'late', 'model_provider': 'new-provider'}, 32)], 'a')
    actual, state = parser.parse_file_incremental(path, state, _record_cache=cache)
    assert plain(actual) == plain(parser.parse_file_incremental(path)[0])


@pytest.mark.parametrize('status', ['running', 'confirmed', 'error'])
def test_current_task_keeps_live_sse_request_when_closed_history_is_reused(tmp_path, status):
    from codex_usage_hud.core.parser import RequestTokens

    class Sse:
        def build(self, _session_id, _task_started_at):
            return RequestTokens(status=status, model='gpt-5.5', source='sse',
                                 round_index=3, input_tokens=120, output_tokens=15,
                                 started_at=datetime(2026, 11, 12, 7, 0, 10, tzinfo=timezone.utc),
                                 updated_at=datetime(2026, 11, 12, 7, 0, 12, tzinfo=timezone.utc),
                                 error='test transport error' if status == 'error' else ''), []

    path = tmp_path / 'sse.jsonl'
    write(path, seed())
    parser = JsonlSessionParser()
    cache = DecodedHistoryCache(4096)
    _, state = parser.parse_file_incremental(path, sse_tracker=Sse(), _record_cache=cache)
    write(path, [event('agent_message', 13, message='实时输出')], 'a')
    expected, _ = parser.parse_file_incremental(path, sse_tracker=Sse())
    actual, state = parser.parse_file_incremental(path, state, sse_tracker=Sse(), _record_cache=cache)
    assert plain(actual) == plain(expected)
