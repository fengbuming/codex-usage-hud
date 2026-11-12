from dataclasses import replace
import tracemalloc

from codex_usage_hud.core.parser import ParsedSession, RequestRound, TaskHistory
from codex_usage_hud import renderer_payload_builder as builder


def row(index, cost=0.01, total=110):
    return RequestRound(index=index, status='confirmed', model='test', input_tokens=100,
                        cached_tokens=20, output_tokens=10, reasoning_tokens=0,
                        total_tokens=total, estimated=False, cost_usd=cost)


def test_top_three_preserves_stable_ties_metadata_and_rolled_back_copy():
    tasks = [TaskHistory(index=i, count=5, prompt=f'prompt {i}', turn_id=f'turn-{i}',
                         rolled_back=i == 2, request_history=[row(1, cost, total)])
             for i, cost, total in [(1, 0.02, 100), (2, 0.03, 100), (3, 0.02, 200), (4, 0.02, 200), (5, 0.01, 500)]]
    result = builder._top_heavy_rounds(ParsedSession(activity_tasks=tasks))
    assert [item['taskIndex'] for item in result] == [2, 3, 4]
    assert result[0]['rolledBack'] is True
    assert result[0]['taskPrompt'] == 'prompt 2'
    assert result[0]['taskTurnId'] == 'turn-2'
    assert '已回滚' in result[0]['title']


def test_top_three_does_not_rehydrate_all_large_request_texts():
    class Tasks:
        def __iter__(self):
            for i in range(1, 161):
                request = replace(row(1, i / 100), copy_text=str(i) + 'x' * 65536)
                yield TaskHistory(index=i, count=160, request_history=[request])

    snapshot = ParsedSession(activity_tasks=Tasks())
    tracemalloc.start()
    try:
        result = builder._top_heavy_rounds(snapshot)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert [item['taskIndex'] for item in result] == [160, 159, 158]
    assert all(len(item['copyText']) >= 65536 for item in result)
    assert peak < 2 * 1024 * 1024


def test_top_three_without_task_history_keeps_all_session_rounds_available():
    snapshot = ParsedSession(task_index=3, task_prompt='fallback', task_turn_id='turn',
                             session_request_history=[row(i, i / 100) for i in range(1, 21)])
    result = builder._top_heavy_rounds(snapshot)
    assert [item['roundIndex'] for item in result] == [20, 19, 18]
    assert all(item['taskIndex'] == 3 and item['taskPrompt'] == 'fallback' for item in result)
