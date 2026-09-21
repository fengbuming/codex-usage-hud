from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from codex_usage_hud.active_work import (
    _refresh_visible_current_work_item,
    _work_item_from_snapshot,
)
from codex_usage_hud.core import Activity, ParsedSession, RequestTokens
from codex_usage_hud.core.parser import JsonlSessionParser


def _record(at, kind, payload):
    return {"timestamp": at.isoformat(), "_dt": at, "type": kind, "payload": payload}


@pytest.mark.parametrize("terminal", ["final_answer", "task_complete", "turn_aborted"])
@pytest.mark.parametrize("user_format", ["event_msg", "response_item"])
def test_continue_reopens_terminal_until_the_new_answer(terminal, user_format):
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=5)
    records = [_record(start, "event_msg", {"type": "task_started"})]
    if terminal == "final_answer":
        records.append(_record(start + timedelta(seconds=1), "response_item", {
            "type": "message", "role": "assistant", "phase": "final_answer",
            "content": [{"type": "output_text", "text": "previous answer"}],
        }))
    else:
        records.append(_record(start + timedelta(seconds=1), "event_msg", {"type": terminal}))
    user = ({"type": "user_message", "message": "继续"} if user_format == "event_msg" else {
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": "继续"}],
    })
    records.append(_record(now - timedelta(seconds=3), user_format, user))
    parser = JsonlSessionParser()
    for extra in [None, {"type": "reasoning"}, {
        "type": "custom_tool_call", "name": "exec", "call_id": "call-1", "input": "dir",
    }]:
        if extra:
            records.append(_record(now - timedelta(seconds=1), "response_item", extra))
        snapshot = parser.parse_records(records)
        assert snapshot.task_prompt == "继续"
        assert snapshot.task_completed_at is None
        assert snapshot.task_aborted_at is None
        assert snapshot.final_answer_at is None
        assert snapshot.slow.current_gap_active
        item = _work_item_from_snapshot(snapshot, current=True, now=now)
        assert item is not None and item.status != "recent"
    records.append(_record(now, "event_msg", {"type": "task_complete"}))
    completed = parser.parse_records(records)
    assert _work_item_from_snapshot(completed, current=True, now=now).status == "recent"


def test_synthetic_abort_message_does_not_reopen_completed_task():
    now = datetime.now(timezone.utc)
    snapshot = JsonlSessionParser().parse_records([
        _record(now, "event_msg", {"type": "task_started"}),
        _record(now, "event_msg", {"type": "turn_aborted"}),
        _record(now, "response_item", {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "<turn_aborted>interrupted</turn_aborted>"},
        ]}),
    ])
    assert snapshot.task_aborted_at == now
    assert not snapshot.slow.current_gap_active
    assert _work_item_from_snapshot(snapshot, current=True, now=now) is None


def test_visible_refresh_creates_missing_current_running_bubble():
    now = datetime.now(timezone.utc)
    snapshot = ParsedSession(
        session_id="resumed", task_started_at=now,
        request=RequestTokens(status="running", started_at=now, updated_at=now),
        activity=Activity(kind="user", detail="继续", timestamp=now),
    )
    items = _refresh_visible_current_work_item(SimpleNamespace(), [], snapshot)
    assert len(items) == 1
    assert items[0].session_id == "resumed"
    assert items[0].status == "running"


@pytest.mark.parametrize("terminal", ["final_answer_at", "task_completed_at"])
def test_background_cli_started_before_restart_keeps_completion(tmp_path, monkeypatch, terminal):
    from dataclasses import replace
    from codex_usage_hud import active_work
    from codex_usage_hud.overlay_projection import _stabilize_published_work_overlay_items

    now = datetime.now(timezone.utc)
    started = now - timedelta(minutes=5)
    running = ParsedSession(
        session_id="cli-before-restart", client_kind="cli", task_started_at=started,
        request=RequestTokens(status="running", started_at=started, updated_at=now),
        activity=Activity(kind="tool call", detail="exec", timestamp=now),
    )
    parser = SimpleNamespace(parse_file=lambda path: running)
    context = SimpleNamespace(
        sessions_root=tmp_path, parser=parser, active_session_tracker=None,
        work_overlay_started_at=now - timedelta(seconds=10),
    )
    monkeypatch.setattr(active_work, "_recent_session_files", lambda *a, **kw: [tmp_path / "cli.jsonl"])
    desktop = ParsedSession(session_id="idle-desktop")

    items = active_work.active_work_items_for_snapshot(context, desktop, None)
    visible = _stabilize_published_work_overlay_items(context, items)
    assert len(visible) == 1 and visible[0].status == "running"
    assert not visible[0].current

    completed = replace(running, **{terminal: now - timedelta(seconds=2)})
    parser.parse_file = lambda path: completed
    items = active_work.active_work_items_for_snapshot(context, desktop, None)
    visible = _stabilize_published_work_overlay_items(context, items)
    assert len(visible) == 1 and visible[0].status == "recent"

    # A second restart sees only history and must not emit a new completion bubble.
    cold = SimpleNamespace(
        sessions_root=tmp_path, parser=parser, active_session_tracker=None,
        work_overlay_started_at=now,
    )
    assert active_work.active_work_items_for_snapshot(cold, desktop, None) == []
