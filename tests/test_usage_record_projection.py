"""Billing projection preserves replay boundaries and incremental semantics."""

import dataclasses
from datetime import datetime, timezone
import json

from codex_usage_hud.core import JsonlSessionParser
from codex_usage_hud.usage_cache import UsageSummaryCache


def _write(path, records, mode="w"):
    with path.open(mode, encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _record(kind, payload, second=0):
    return {
        "type": kind,
        "timestamp": f"2026-11-11T08:00:{second:02d}Z",
        "payload": payload,
    }


def _token(total, second):
    return _record(
        "event_msg",
        {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": total,
                    "cached_input_tokens": total // 2,
                    "cache_write_input_tokens": 0,
                    "output_tokens": total // 10,
                },
            },
        },
        second,
    )


def test_usage_projection_preserves_events_parent_and_every_record_position(tmp_path):
    path = tmp_path / "fork.jsonl"
    sid = "10000000-0000-4000-8000-000000000001"
    parent = "20000000-0000-4000-8000-000000000002"
    records = [
        _record(
            "session_meta",
            {
                "id": sid,
                "model_provider": "custom",
                "thread_source": "subagent",
                "forked_from_id": parent,
                "cwd": "/project",
                "name": "title",
            },
        ),
        _record(
            "turn_context",
            {"model": "gpt-5.6-sol", "developer_instructions": "hidden" * 10000},
        ),
        _token(100, 0),
        _record("inter_agent_communication", {"body": "large replay marker"}),
        _record(
            "response_item",
            {"type": "function_call_output", "output": "large tool output" * 10000},
        ),
        _token(200, 0),
        _record(
            "response_item",
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"<codex_delegation><source_thread_id>{parent}</source_thread_id>"
                        + "large prompt" * 10000,
                    }
                ],
            },
            10,
        ),
        _token(350, 11),
        _record(
            "response_item",
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "large answer" * 10000,
                    }
                ],
            },
            12,
        ),
    ]
    _write(path, records)
    parser = JsonlSessionParser()
    full = parser.load_records_incremental(path)
    compact = parser.load_usage_records_incremental(path)
    assert len(full.records) == len(compact.records)
    assert [r["_line"] for r in compact.records] == [r["_line"] for r in full.records]
    assert parser.usage_events(full.records) == parser.usage_events(compact.records)
    assert parser._history_replay_usage_start(
        full.records
    ) == parser._history_replay_usage_start(compact.records)
    cache = UsageSummaryCache(parser)
    assert cache._delegated_source_session_id(compact.records) == parent
    assert compact.last_complete_line == ""
    assert "large tool output" not in json.dumps(compact.records, default=str)
    assert "large prompt" not in json.dumps(compact.records, default=str)
    assert full.records[4]["payload"]["output"] == records[4]["payload"]["output"]
    # Incremental reads preserve cumulative deltas, model/provider metadata
    # and byte offsets without restoring discarded historical output.
    previous_offset = compact.offset
    _write(
        path,
        [
            _record("turn_context", {"info": {"model": "later-model"}}, 13),
            _token(500, 14),
        ],
        "a",
    )
    assert parser.load_usage_records_incremental(path, compact) is compact
    parser.load_records_incremental(path, full)
    assert compact.offset > previous_offset
    assert parser.usage_events(full.records) == parser.usage_events(compact.records)
    # Full UI parsing must not consume the budget projection as conversation.
    snapshot, restored = parser.parse_file_incremental(path, compact)
    assert restored is not compact and not restored.usage_only
    assert restored.records[4]["payload"]["output"] == records[4]["payload"]["output"]


def test_projection_handles_truncation_partial_lines_and_full_state_input(tmp_path):
    path = tmp_path / "replace.jsonl"
    parser = JsonlSessionParser()
    _write(path, [_token(10, 1)])
    full = parser.load_records_incremental(path)
    compact = parser.load_usage_records_incremental(path, full)
    assert compact is not full and compact.usage_only
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"event_msg"')
    offset = compact.offset
    parser.load_usage_records_incremental(path, compact)
    assert compact.offset == offset
    path.write_text("{}\n", encoding="utf-8")
    replaced = parser.load_usage_records_incremental(path, compact)
    assert replaced is not compact and replaced.usage_only
    assert not parser.usage_events(replaced.records)


def test_usage_cache_full_and_projected_contributions_match(tmp_path):
    class FullParser(JsonlSessionParser):
        load_usage_records_incremental = None

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "one.jsonl"
    _write(
        path,
        [
            _record(
                "session_meta",
                {
                    "id": "10000000-0000-4000-8000-000000000001",
                    "model_provider": "custom",
                    "source": "cli",
                    "cwd": "/project",
                },
            ),
            _record("turn_context", {"model": "gpt-5.6-sol"}),
            _record(
                "response_item",
                {"type": "function_call_output", "output": "large" * 10000},
            ),
            _token(100, 1),
        ],
    )
    full = UsageSummaryCache(FullParser(), min_rescan_seconds=0)
    projected = UsageSummaryCache(JsonlSessionParser(), min_rescan_seconds=0)
    day = datetime(2026, 11, 11, tzinfo=timezone.utc)
    week = datetime(2026, 11, 9, tzinfo=timezone.utc)
    for phase in range(2):
        assert full.summarize(sessions, day, week) == projected.summarize(
            sessions, day, week
        )
        a = dataclasses.asdict(next(iter(full._entries.values())))
        b = dataclasses.asdict(next(iter(projected._entries.values())))
        a.pop("tail_state")
        b.pop("tail_state")
        assert a == b
        if phase == 0:
            _write(path, [_token(300, 10)], "a")


def test_streaming_reader_preserves_line_numbers_and_partial_byte_cursor(tmp_path):
    path = tmp_path / "lines.jsonl"
    first = json.dumps(_token(10, 1)).encode() + b"\r\n"
    second = json.dumps(_token(30, 2)).encode()
    prefix = b"\n\xff\nnot-json\n[]\n" + first
    path.write_bytes(prefix + second[:20])
    parser = JsonlSessionParser()
    state = parser.load_usage_records_incremental(path)
    assert state.offset == len(prefix)
    assert state.line_count == 5
    assert state.records[0]["_line"] == 5
    with path.open("ab") as handle:
        handle.write(second[20:] + b"\n")
    parser.load_usage_records_incremental(path, state)
    assert state.offset == path.stat().st_size
    assert state.line_count == 6
    assert state.records[1]["_line"] == 6
    assert [event.input_tokens for event in parser.usage_events(state.records)] == [
        10,
        20,
    ]
