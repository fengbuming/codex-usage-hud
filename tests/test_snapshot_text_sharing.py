"""Request history sharing keeps exact copy/locate strings and mutable rows."""

from dataclasses import asdict
import json

from codex_usage_hud.core.parser import (
    JsonlSessionParser,
    RequestRound,
    TaskHistory,
    ParsedSession,
)


def _rows():
    text = "完整的历史输出 😀\n保留定位与复制。" * 200

    def row(index):
        return RequestRound(
            index=index,
            status="confirmed",
            model="model",
            input_tokens=1,
            cached_tokens=0,
            output_tokens=2,
            reasoning_tokens=0,
            total_tokens=3,
            estimated=False,
            copy_text=text.encode().decode(),
            activity_texts=(text.encode().decode(),),
        )

    return row(1), row(5)


def test_equal_text_is_shared_but_round_numbers_and_mutable_state_are_independent():
    first, second = _rows()
    snapshot = ParsedSession(
        session_request_history=[first],
        activity_tasks=[TaskHistory(index=1, count=1, request_history=[second])],
    )
    before = asdict(snapshot)
    assert (
        first.copy_text == second.copy_text and first.copy_text is not second.copy_text
    )
    JsonlSessionParser._share_request_history_texts(snapshot)
    assert asdict(snapshot) == before
    assert first is not second and first.index == 1 and second.index == 5
    assert first.copy_text is second.copy_text
    assert first.activity_texts[0] is second.activity_texts[0]
    second.status = "changed"
    second.copy_text = "new text"
    assert first.status == "confirmed"
    assert len(first.copy_text) > 1000


def test_previous_snapshot_reuses_text_without_mutation_or_cross_session_pool():
    first, second = _rows()
    old = ParsedSession(session_request_history=[first])
    new = ParsedSession(session_request_history=[second])
    before = asdict(old)
    JsonlSessionParser._share_request_history_texts(new, old)
    assert first.copy_text is second.copy_text
    assert asdict(old) == before
    third, _ = _rows()
    unrelated = ParsedSession(session_request_history=[third])
    JsonlSessionParser._share_request_history_texts(unrelated)
    assert unrelated.session_request_history[0].copy_text is not first.copy_text


def test_real_parse_and_incremental_append_share_historical_text(tmp_path):
    path = tmp_path / "history.jsonl"
    text = "Long clipboard and locate history content " * 300

    def record(kind, payload, second):
        return {
            "type": kind,
            "timestamp": f"2026-11-11T08:00:{second:02d}Z",
            "payload": payload,
        }

    records = [
        record("session_meta", {"id": "history", "model_provider": "openai"}, 0),
        record("event_msg", {"type": "task_started"}, 1),
        record("event_msg", {"type": "user_message", "message": text}, 2),
        record(
            "event_msg",
            {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 100,
                        "output_tokens": 1,
                        "total_tokens": 101,
                    },
                    "total_token_usage": {
                        "input_tokens": 100,
                        "output_tokens": 1,
                        "total_tokens": 101,
                    },
                },
            },
            3,
        ),
        record("event_msg", {"type": "task_complete"}, 4),
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
    )
    parser = JsonlSessionParser()
    first, state = parser.parse_file_incremental(path)
    assert first.session_request_history[0].copy_text == "输入：\n" + text.rstrip()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(record("event_msg", {"type": "task_started"}, 5)) + "\n"
        )
    second, _ = parser.parse_file_incremental(path, state)
    assert (
        first.session_request_history[0].copy_text
        is second.session_request_history[0].copy_text
    )
    task_row = second.activity_tasks[0].request_history[0]
    assert task_row.copy_text is second.session_request_history[0].copy_text
    assert task_row is not second.session_request_history[0]
