from __future__ import annotations

from pathlib import Path
from threading import Event
from types import SimpleNamespace
from datetime import datetime, timedelta
from dataclasses import replace

import pytest

from codex_usage_hud.active_work import RendererActiveWorkPump


@pytest.mark.parametrize("follow_target", [False, True])
@pytest.mark.parametrize("old_first", [False, True])
@pytest.mark.parametrize("terminal", ["complete", "abort", "running"])
def test_resumed_rollouts_use_latest_task_before_updating_caches(
    tmp_path, monkeypatch, follow_target, old_first, terminal,
) -> None:
    from codex_usage_hud import active_work
    from codex_usage_hud.core import ParsedSession

    now = datetime.now().astimezone()
    old_path, new_path = tmp_path / "old.jsonl", tmp_path / "resumed.jsonl"
    old = ParsedSession(
        session_id="same-session", session_path=old_path,
        task_started_at=now - timedelta(minutes=20),
        session_started_at=now - timedelta(minutes=20),
        last_event_time=now - timedelta(minutes=19), task_prompt="old task",
    )
    old.request.status = "running"
    newer = replace(
        old, session_path=new_path, task_started_at=now - timedelta(minutes=2),
        session_started_at=now - timedelta(minutes=2),
        last_event_time=now - timedelta(seconds=2), task_prompt="new task",
    )
    parsed = {old_path: old, new_path: newer}
    context = SimpleNamespace(
        sessions_root=tmp_path, active_session_tracker=None,
        parser=SimpleNamespace(parse_file=lambda path: parsed[path]),
    )
    paths = [old_path, new_path] if old_first else [new_path, old_path]
    monkeypatch.setattr(active_work, "_recent_session_files", lambda *a, **kw: paths)
    current = old if follow_target else ParsedSession(session_id="unrelated")
    current_path = old_path if follow_target else None
    # Observe the latest task running, then process its terminal transition
    # with both the abandoned and resumed files still discoverable.
    items = active_work.active_work_items_for_snapshot(context, current, current_path)
    assert len(items) == 1
    assert items[0].task_started_at == newer.task_started_at
    if terminal == "complete":
        parsed[new_path] = replace(newer, task_completed_at=now)
    elif terminal == "abort":
        parsed[new_path] = replace(newer, task_aborted_at=now)
    items = active_work.active_work_items_for_snapshot(context, current, current_path)
    if terminal == "abort":
        assert items == []
    else:
        assert len(items) == 1
        assert items[0].task_started_at == newer.task_started_at
        assert items[0].status == ("recent" if terminal == "complete" else "running")
    # After HUD restart, historical completion must not resurrect the old task.
    if terminal != "running":
        fresh = SimpleNamespace(
            sessions_root=tmp_path, active_session_tracker=None, parser=context.parser,
        )
        assert active_work.active_work_items_for_snapshot(fresh, current, current_path) == []


def test_active_work_pump_publishes_sequence_bound_result_and_wakes() -> None:
    wake = Event()
    expected = [SimpleNamespace(id="work-1")]
    pump = RendererActiveWorkPump(
        "context",
        wake,
        build_items=lambda context, snapshot, path, priority_paths: expected,
    )
    try:
        assert pump.request(SimpleNamespace(selection_seq=12), Path("session.jsonl"))
        assert wake.wait(1.0)
        assert pump.take_latest() == (12, expected)
        assert pump.take_latest() is None
    finally:
        pump.close()


def test_active_work_pump_coalesces_to_latest_pending_request() -> None:
    first_started = Event()
    release_first = Event()
    calls: list[tuple[int, tuple[Path, ...]]] = []

    def build_items(
        context: object,
        snapshot: object,
        path: Path | None,
        priority_paths: tuple[Path, ...],
    ) -> list[int]:
        sequence = int(getattr(snapshot, "selection_seq"))
        calls.append((sequence, priority_paths))
        if sequence == 1:
            first_started.set()
            assert release_first.wait(1.0)
        return [sequence]

    wake = Event()
    pump = RendererActiveWorkPump("context", wake, build_items=build_items)
    try:
        assert pump.request(
            SimpleNamespace(selection_seq=1),
            None,
            (Path("first.jsonl"),),
        )
        assert first_started.wait(1.0)
        assert pump.request(
            SimpleNamespace(selection_seq=2),
            None,
            (Path("second.jsonl"),),
        )
        assert pump.request(
            SimpleNamespace(selection_seq=3),
            None,
            (Path("third.jsonl"),),
        )
        release_first.set()
        assert wake.wait(1.0)
        assert pump.take_latest() == (3, [3])
        assert calls == [
            (1, (Path("first.jsonl"),)),
            (3, (Path("second.jsonl"), Path("third.jsonl"))),
        ]
    finally:
        release_first.set()
        pump.close()


def test_active_work_pump_uses_a_shallow_snapshot_copy() -> None:
    release = Event()
    observed: list[str] = []

    def build_items(
        context: object,
        snapshot: object,
        path: Path | None,
        priority_paths: tuple[Path, ...],
    ) -> list[object]:
        assert release.wait(1.0)
        observed.append(str(getattr(snapshot, "title")))
        return []

    wake = Event()
    snapshot = SimpleNamespace(selection_seq=4, title="before")
    pump = RendererActiveWorkPump("context", wake, build_items=build_items)
    try:
        assert pump.request(snapshot, None)
        snapshot.title = "after"
        release.set()
        assert wake.wait(1.0)
        assert observed == ["before"]
    finally:
        release.set()
        pump.close()


def test_active_work_pump_publishes_empty_result_after_builder_error() -> None:
    def fail(
        context: object,
        snapshot: object,
        path: Path | None,
        priority_paths: tuple[Path, ...],
    ) -> list[object]:
        raise RuntimeError("boom")

    wake = Event()
    pump = RendererActiveWorkPump("context", wake, build_items=fail)
    try:
        assert pump.request(SimpleNamespace(selection_seq=7), None)
        assert wake.wait(1.0)
        assert pump.take_latest() == (7, [])
    finally:
        pump.close()


def test_active_work_pump_close_during_work_suppresses_publish() -> None:
    started = Event()
    release = Event()

    def build_items(
        context: object,
        snapshot: object,
        path: Path | None,
        priority_paths: tuple[Path, ...],
    ) -> list[int]:
        started.set()
        assert release.wait(1.0)
        return [1]

    wake = Event()
    pump = RendererActiveWorkPump("context", wake, build_items=build_items)
    assert pump.request(SimpleNamespace(selection_seq=9), None)
    assert started.wait(1.0)
    pump.close()
    release.set()
    pump.close()

    assert not wake.wait(0.05)
    assert pump.take_latest() is None
    assert not pump.request(SimpleNamespace(selection_seq=10), None)
