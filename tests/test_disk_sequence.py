from dataclasses import asdict
import gc
import json

import pytest

from codex_usage_hud.core.parser import JsonlSessionParser, ParsedSession, RequestRound, TaskHistory
from codex_usage_hud.disk_sequence import DecodedHistoryCache, DiskSequence, spill_snapshot_history


def test_shared_budget_lazy_slices_and_reader_lifetime():
    cache = DecodedHistoryCache(2048)
    values = [{"text": str(index) * 1000} for index in range(30)]
    sequence = DiskSequence(values, cache=cache)
    other = DiskSequence(values, cache=cache)
    view = sequence[::-2][1:5]
    file = sequence._file
    del sequence
    gc.collect()
    assert list(view) == values[::-2][1:5]
    for item in other:
        assert item in values
        assert cache.retained_bytes <= cache.max_bytes
    assert not file.closed
    del view
    gc.collect()
    assert file.closed
    assert list(other) == values
    with pytest.raises(IndexError):
        other[len(other)]


def test_spilled_history_preserves_full_text_and_snapshot_deepcopy():
    text = "完整历史工具输出" * 10000
    row = RequestRound(index=1, status='confirmed', model='test', input_tokens=1,
                       cached_tokens=0, output_tokens=1, reasoning_tokens=0,
                       total_tokens=2, estimated=False, copy_text=text)
    snapshot = ParsedSession(
        request_history=[row], session_request_history=[row],
        activity_tasks=[TaskHistory(index=1, count=1, request_history=[row])],
    )
    expected = asdict(snapshot)
    cache = DecodedHistoryCache(1024)
    spill_snapshot_history(snapshot, cache)
    restored = ParsedSession(**{name: getattr(snapshot, name) for name in snapshot.__dataclass_fields__})
    for name in ('request_history', 'session_request_history', 'activity_tasks'):
        setattr(restored, name, list(getattr(snapshot, name)))
    assert asdict(restored) == expected
    assert snapshot.activity_tasks[0].request_history[0].copy_text == text
    assert cache.retained_bytes == 0  # An oversized value remains read-through.


def test_selected_disk_records_preserve_append_partial_truncate_and_replacement(tmp_path):
    path = tmp_path / "session.jsonl"
    lines = [
        {"type": "session_meta", "payload": {"id": "sample", "model_provider": "openai"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "完整内容" * 2000}},
    ]
    path.write_text(''.join(json.dumps(row) + '\n' for row in lines), encoding='utf-8')
    parser = JsonlSessionParser()
    cache = DecodedHistoryCache(1024)
    expected, _ = parser.parse_file_incremental(path)
    actual, state = parser.parse_file_incremental(path, _record_cache=cache)
    assert isinstance(state.records, DiskSequence)
    assert actual.task_prompt == expected.task_prompt
    assert actual.task_count == expected.task_count
    assert state.records == parser.load_records_incremental(path).records
    with path.open('ab') as file:
        file.write(b'{"type":"event_msg","payload":{"type":"task_complete"}}')
    _, partial = parser.parse_file_incremental(path, state, _record_cache=cache)
    assert len(partial.records) == len(lines)
    with path.open('ab') as file:
        file.write(b'\n')
    _, appended = parser.parse_file_incremental(path, partial, _record_cache=cache)
    assert len(appended.records) == len(lines) + 1
    path.write_text(json.dumps(lines[0]) + '\n', encoding='utf-8')
    _, truncated = parser.parse_file_incremental(path, appended, _record_cache=cache)
    assert len(truncated.records) == 1
    replacement = path.with_suffix('.new')
    replacement.write_text(json.dumps(lines[1]) + '\n', encoding='utf-8')
    replacement.replace(path)
    _, replaced = parser.parse_file_incremental(path, truncated, _record_cache=cache)
    assert replaced.records[0]['payload']['type'] == 'task_started'


def test_desktop_work_parser_keeps_its_existing_storage(tmp_path, monkeypatch):
    path = tmp_path / 'work.jsonl'
    path.write_text('{"type":"session_meta","payload":{"id":"work"}}\n', encoding='utf-8')
    monkeypatch.setattr('codex_usage_hud.disk_sequence.tempfile.TemporaryFile', lambda **kw: pytest.fail('Desktop bubble parser must not use disk history'))
    assert JsonlSessionParser().parse_work_file(path).session_id == 'work'


def test_usage_budget_counts_objects_instead_of_source_file_size(tmp_path):
    from types import SimpleNamespace
    from codex_usage_hud.core.parser import JsonlTailState
    from codex_usage_hud.usage_cache import UsageSummaryCache

    cache = UsageSummaryCache(JsonlSessionParser(), max_tail_state_bytes=4096)
    entry = SimpleNamespace(file_size=1, mtime=1, tail_state=JsonlTailState(records=[{'text': 'x' * 10000}]))
    cache._entries[tmp_path / 'a.jsonl'] = entry
    cache._trim_tail_states()
    assert entry.tail_state is None


def test_renderer_projection_preserves_complete_history_after_spill():
    from codex_usage_hud.renderer_payload_builder import payload_from_snapshot
    from codex_usage_hud.core.parser import RequestTokens

    row = RequestRound(index=1, status='confirmed', model='test', input_tokens=100,
                       cached_tokens=0, output_tokens=10, reasoning_tokens=0,
                       total_tokens=110, estimated=False, cost_usd=0.01,
                       copy_text='完整历史' * 1000, activity_texts=('定位文本',))
    snapshot = ParsedSession(status='parsed', session_id='history', task_index=1, task_count=1,
                             request=RequestTokens(status='confirmed', model='test'),
                             request_history=[row], session_request_history=[row],
                             activity_tasks=[TaskHistory(index=1, count=1, prompt='用户提示', request_history=[row])])
    expected = asdict(payload_from_snapshot(snapshot, support_images=[]))
    spill_snapshot_history(snapshot, DecodedHistoryCache(1024))
    assert asdict(payload_from_snapshot(snapshot, support_images=[])) == expected


def test_failed_history_write_does_not_commit_or_damage_previous_rows(monkeypatch):
    import pickle

    sequence = DiskSequence([{'row': 1}])
    original = pickle.dump
    def fail(value, file, **kwargs):
        file.write(b'partial')
        raise OSError('simulated disk full')
    monkeypatch.setattr(pickle, 'dump', fail)
    with pytest.raises(OSError):
        sequence.append({'row': 2})
    assert len(sequence) == 1
    monkeypatch.setattr(pickle, 'dump', original)
    sequence.append({'row': 3})
    assert list(sequence) == [{'row': 1}, {'row': 3}]
