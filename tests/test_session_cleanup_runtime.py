from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Event, Lock, Thread
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codex_usage_hud.core.runtime_events import RuntimeEventBus
from codex_usage_hud.core.session_cleanup import SessionCleanupError, SessionCleanupManager
from codex_usage_hud.session_cleanup_runtime import SessionCleanupWorker


def _operation(request_id: str, action: str, state: str, **extra: object) -> dict[str, object]:
    return {
        "revision": "revision-1",
        "operation": {
            "requestId": request_id,
            "action": action,
            "state": state,
            **extra,
        },
    }


def test_prepare_search_index_clear_waits_for_inflight_compat_worker() -> None:
    """Clear cannot pass the idle gate while a compatibility batch is active."""
    worker = SessionCleanupWorker.__new__(SessionCleanupWorker)
    worker._index_schedule_lock = Lock()
    worker._index_idle = Event()
    worker._index_idle.set()
    worker._index_generation = 9
    worker._index_clear_in_progress = False
    worker._index_operations_idle = Event()
    worker._index_operations_idle.set()
    worker._index_active_operations = 0
    worker._closed = Event()
    worker._index_closed = Event()
    worker._search_change_timer = None
    worker._pending_search_paths = set()
    worker._pending_search_full = False

    entered = Event()
    release = Event()

    def compatibility_batch() -> None:
        with worker._index_schedule_lock:
            worker._index_idle.clear()
            entered.set()
        release.wait(timeout=2)
        worker._index_idle.set()

    batch = Thread(target=compatibility_batch)
    batch.start()
    assert entered.wait(timeout=1)

    result: dict[str, bool] = {}

    def clear() -> None:
        result["ready"] = worker.prepare_search_index_clear(timeout_seconds=2)

    clear_thread = Thread(target=clear)
    clear_thread.start()
    deadline = time.monotonic() + 1
    while not worker._index_clear_in_progress and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "ready" not in result
    assert worker._index_clear_in_progress is True

    release.set()
    batch.join(timeout=2)
    clear_thread.join(timeout=2)
    assert result == {"ready": True}
    worker.finish_search_index_clear()
    assert worker._index_clear_in_progress is False


def test_disabled_index_blocks_changed_file_compatibility_build() -> None:
    worker = SessionCleanupWorker.__new__(SessionCleanupWorker)
    worker._index_schedule_lock = Lock()
    worker._index_generation = 1
    worker._index_clear_in_progress = False
    worker._index_operations_idle = Event()
    worker._index_operations_idle.set()
    worker._index_active_operations = 0
    manager = MagicMock()
    manager.snapshot.return_value = {"revision": "revision-1"}
    worker._real_search_manager = lambda: manager
    worker._context = SimpleNamespace(
        session_index_warm_job=SimpleNamespace(
            status=lambda: {"enabled": False, "jobState": "idle"}
        )
    )

    result = worker._schedule_search_index(
        {"revision": "revision-1"},
        changed_paths={Path("changed.jsonl")},
    )

    assert result == {"revision": "revision-1"}
    manager.search_index_entries.assert_not_called()


def test_clear_gate_waits_for_direct_index_schedule_lease() -> None:
    worker = SessionCleanupWorker.__new__(SessionCleanupWorker)
    worker._index_schedule_lock = Lock()
    worker._index_generation = 1
    worker._index_clear_in_progress = False
    worker._index_operations_idle = Event()
    worker._index_operations_idle.set()
    worker._index_active_operations = 0
    worker._index_idle = Event()
    worker._index_idle.set()
    worker._closed = Event()
    worker._index_closed = Event()
    worker._search_change_timer = None
    worker._pending_search_paths = set()
    worker._pending_search_full = False
    entered = Event()
    release = Event()

    def scheduled(*_args: object, **_kwargs: object) -> None:
        entered.set()
        release.wait(timeout=2)

    worker._schedule_search_index_impl = scheduled
    schedule_thread = Thread(
        target=lambda: worker._schedule_search_index({"revision": "revision-1"})
    )
    schedule_thread.start()
    assert entered.wait(timeout=1)

    result: dict[str, bool] = {}

    def clear() -> None:
        result["ready"] = worker.prepare_search_index_clear(timeout_seconds=2)

    clear_thread = Thread(target=clear)
    clear_thread.start()
    deadline = time.monotonic() + 1
    while not worker._index_clear_in_progress and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "ready" not in result
    assert worker._index_clear_in_progress is True

    release.set()
    schedule_thread.join(timeout=2)
    clear_thread.join(timeout=2)
    assert result == {"ready": True}
    worker.finish_search_index_clear()


def test_session_cleanup_worker_refreshes_usage_after_verified_delete() -> None:
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: _operation(
        str(values["request_id"]), str(values["action"]), str(values["state"])
    )
    manager.execute.side_effect = lambda *_args, request_id: _operation(
        request_id, "sessionCleanupExecute", "complete", deletedCount=1
    )
    refreshed: list[str] = []
    context = SimpleNamespace(
        session_cleanup_payload={}, runtime_events=RuntimeEventBus()
    )
    worker = SessionCleanupWorker(
        context,
        manager,
        on_deleted=lambda _context, request_id: refreshed.append(request_id),
    )
    try:
        result = worker.enqueue(
            {
                "action": "sessionCleanupExecute",
                "requestId": "request-1",
                "itemIds": ["session-1"],
                "inventoryRevision": "inventory-1",
                "confirmationToken": "confirm-1",
            }
        )
        assert result["status"] == "accepted"
        deadline = time.monotonic() + 2
        while not refreshed:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert refreshed == ["request-1"]
        assert context.session_cleanup_payload["operation"]["state"] == "complete"
    finally:
        assert worker.close()


def test_session_cleanup_worker_publishes_matching_terminal_state_before_refresh() -> None:
    events: list[str] = []
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: _operation(
        str(values["request_id"]), str(values["action"]), str(values["state"])
    )

    def execute(*_args: object, request_id: str) -> dict[str, object]:
        events.append("execute")
        return _operation(
            request_id, "execute", "completed", deletedCount=1
        )

    manager.execute.side_effect = execute
    event_bus = RuntimeEventBus()
    event_bus.subscribe(
        lambda event: events.append(
            f"published:{event.context.get('requestId')}:{event.context.get('state')}"
        )
        if event.type == "session_cleanup_changed"
        else None
    )
    context = SimpleNamespace(session_cleanup_payload={}, runtime_events=event_bus)
    worker = SessionCleanupWorker(
        context,
        manager,
        on_deleted=lambda _context, request_id: events.append(f"refresh:{request_id}"),
    )
    try:
        worker.enqueue(
            {
                "action": "sessionCleanupExecute",
                "requestId": "request-2",
                "itemIds": ["session-1"],
                "inventoryRevision": "inventory-1",
                "confirmationToken": "confirm-1",
            }
        )
        deadline = time.monotonic() + 2
        while "published:request-2:completed" not in events:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        while "refresh:request-2" not in events:
            assert time.monotonic() < deadline
            time.sleep(0.01)

        assert events.index("execute") < events.index("refresh:request-2")
        assert events.index("published:request-2:completed") < events.index(
            "refresh:request-2"
        )
    finally:
        assert worker.close()


def test_session_cleanup_worker_deletes_provider_history_before_config(monkeypatch) -> None:
    delete_config = MagicMock(return_value={"status": "ok", "providerId": "muyuan"})
    monkeypatch.setattr("codex_usage_hud.session_cleanup_runtime.delete_provider_for_context", delete_config)
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: _operation(
        str(values["request_id"]), str(values["action"]), str(values["state"])
    )
    manager.delete_provider_history.return_value = _operation(
        "provider-request",
        "providerHistoryDelete",
        "completed",
        deletedCount=2,
        actualBytes=1234,
    )
    # 历史成功之后才能删除配置，避免留下无法续跑的历史。
    context = SimpleNamespace(
        session_cleanup_payload={}, runtime_events=RuntimeEventBus()
    )
    worker = SessionCleanupWorker(
        context,
        manager,
        on_deleted=lambda *_args: None,
    )
    try:
        worker.enqueue(
            {
                "action": "providerDelete",
                "requestId": "provider-request",
                "provider": "muyuan",
                "deleteSessionHistory": True,
            }
        )
        deadline = time.monotonic() + 2
        while context.session_cleanup_payload.get("operation", {}).get("state") != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.01)

        manager.delete_provider_history.assert_called_once_with(
            "muyuan", request_id="provider-request", force=True,
            desktop_lifecycle=worker._force_delete_provider_session,
        )
        delete_config.assert_called_once()
        final_operation = manager.mark_operation.call_args_list[-1].kwargs
        assert final_operation["actualBytes"] == 1234
        assert final_operation["deletedCount"] == 2
    finally:
        assert worker.close()


def test_session_cleanup_worker_skips_provider_history_when_not_requested(monkeypatch) -> None:
    monkeypatch.setattr("codex_usage_hud.session_cleanup_runtime.delete_provider_for_context", lambda *_args: {})
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: _operation(
        str(values["request_id"]), str(values["action"]), str(values["state"])
    )
    context = SimpleNamespace(
        session_cleanup_payload={}, runtime_events=RuntimeEventBus()
    )
    worker = SessionCleanupWorker(
        context,
        manager,
        on_deleted=lambda *_args: None,
    )
    try:
        worker.enqueue(
            {
                "action": "providerDelete",
                "requestId": "provider-request",
                "provider": "muyuan",
                "deleteSessionHistory": False,
            }
        )
        deadline = time.monotonic() + 2
        while context.session_cleanup_payload.get("operation", {}).get("state") != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.01)

        manager.delete_provider_history.assert_not_called()
    finally:
        assert worker.close()


def test_session_cleanup_worker_accepts_renderer_provider_delete_alias(monkeypatch) -> None:
    monkeypatch.setattr("codex_usage_hud.session_cleanup_runtime.delete_provider_for_context", lambda *_args: {})
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: _operation(
        str(values["request_id"]), str(values["action"]), str(values["state"])
    )
    context = SimpleNamespace(
        app_provider="codex",
        session_cleanup_payload={},
        runtime_events=RuntimeEventBus(),
    )
    worker = SessionCleanupWorker(
        context,
        manager,
        on_deleted=lambda *_args: None,
    )
    command = {
        "action": "deleteProvider",
        "requestId": "provider-alias-1",
        "provider": "muyuan",
        "deleteSessionHistory": False,
    }
    try:
        result = worker.enqueue(command)
        assert result["status"] == "accepted"
        deadline = time.monotonic() + 2
        while context.session_cleanup_payload.get("operation", {}).get("state") != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.01)

        assert context.session_cleanup_payload["operation"]["action"] == "providerDelete"
    finally:
        assert worker.close()


def test_provider_delete_returns_within_one_second_while_cleanup_runs_in_background(
    monkeypatch, record_property
) -> None:
    from threading import Event
    from codex_usage_hud.runtime_commands import _handle_renderer_settings_command

    release = Event()
    started = Event()
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: _operation(
        str(values["request_id"]), str(values["action"]), str(values["state"])
    )

    def cleanup(*_args, **_kwargs):
        started.set()
        assert release.wait(2)
        return _operation(
            "fast-delete", "providerHistoryDelete", "completed", deletedCount=2
        )

    manager.delete_provider_history.side_effect = cleanup
    delete_config = MagicMock(return_value={"status": "ok"})
    monkeypatch.setattr(
        "codex_usage_hud.session_cleanup_runtime.delete_provider_for_context",
        delete_config,
    )
    context = SimpleNamespace(
        app_provider="custom",
        session_cleanup_payload={},
        runtime_events=RuntimeEventBus(),
    )
    worker = SessionCleanupWorker(context, manager, on_deleted=lambda *_args: None)
    context.session_cleanup_worker = worker
    try:
        begin = time.monotonic()
        result = _handle_renderer_settings_command(
            {
                "action": "deleteProvider",
                "provider": "token-x",
                "requestId": "fast-delete",
                "deleteSessionHistory": True,
            },
            context,
            MagicMock(),
            MagicMock(),
        )
        elapsed = time.monotonic() - begin
        record_property("foreground_ms", round(elapsed * 1000, 3))
        assert elapsed < 1
        assert result["providerDeletePending"] is True
        assert started.wait(1)
        delete_config.assert_not_called()
        release.set()
        deadline = time.monotonic() + 2
        while (
            context.session_cleanup_payload.get("operation", {}).get("state")
            != "completed"
        ):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        delete_config.assert_called_once()
    finally:
        release.set()
        assert worker.close()


def test_provider_delete_preserves_config_when_history_remains(monkeypatch) -> None:
    from codex_usage_hud.core.runtime_errors import RuntimeErrorRegistry

    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: _operation(
        str(values["request_id"]), str(values["action"]), str(values["state"])
    )
    manager.delete_provider_history.return_value = _operation(
        "failed-delete",
        "providerHistoryDelete",
        "failed",
        remainingCount=1,
        error="thread-still-loaded",
    )
    delete_config = MagicMock()
    monkeypatch.setattr(
        "codex_usage_hud.session_cleanup_runtime.delete_provider_for_context",
        delete_config,
    )
    context = SimpleNamespace(
        app_provider="custom",
        session_cleanup_payload={},
        runtime_events=RuntimeEventBus(),
        runtime_errors=RuntimeErrorRegistry(),
    )
    worker = SessionCleanupWorker(context, manager, on_deleted=lambda *_args: None)
    try:
        worker.enqueue(
            {
                "action": "providerDelete",
                "provider": "token-x",
                "requestId": "failed-delete",
                "deleteSessionHistory": True,
            }
        )
        deadline = time.monotonic() + 2
        while (
            context.session_cleanup_payload.get("operation", {}).get("state")
            != "failed"
        ):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        delete_config.assert_not_called()
        assert context.runtime_errors.to_payload()
    finally:
        assert worker.close()


def test_forced_provider_delete_uses_the_live_renderer_port(
    monkeypatch, tmp_path
) -> None:
    import json
    from codex_usage_hud.core.session_cleanup import SessionCleanupItem

    monkeypatch.setattr(
        "codex_usage_hud.session_cleanup_runtime.hud_runtime_dir", lambda: tmp_path
    )
    (tmp_path / "renderer_cdp_state.json").write_text(
        json.dumps({"lastSuccessfulPort": 51852}), encoding="utf-8"
    )
    context = SimpleNamespace(
        session_cleanup_payload={}, runtime_events=RuntimeEventBus()
    )
    manager = MagicMock()
    worker = SessionCleanupWorker(context, manager, on_deleted=lambda *_args: None)
    lifecycle = SimpleNamespace(port=9229, archive_then_delete=MagicMock())
    worker._desktop_thread_lifecycle = lifecycle
    item = SessionCleanupItem(
        id="orphan",
        title="orphan",
        workdir_name="",
        updated_at="",
        status="current",
        archived=False,
        size=0,
        descendant_count=0,
        selectable=False,
        blocked_reason="current",
        _session_id="10000000-0000-4000-8000-000000000001",
    )
    try:
        worker._force_delete_provider_session(item)
        assert lifecycle.port == 51852
        lifecycle.archive_then_delete.assert_called_once_with(
            item._session_id, cwd="", force=True, missing_rollout=True,
            already_archived=False, provider="unknown",
        )
    finally:
        assert worker.close()


def test_session_cleanup_worker_runs_indexed_search_without_delete_path() -> None:
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: _operation(
        str(values["request_id"]), str(values["action"]), str(values["state"])
    )
    manager.search.side_effect = lambda *_args, **kwargs: _operation(
        str(kwargs["request_id"]), "search", "completed", matches=["session-1"]
    )
    context = SimpleNamespace(
        session_cleanup_payload={}, runtime_events=RuntimeEventBus()
    )
    worker = SessionCleanupWorker(context, manager, on_deleted=lambda *_args: None)
    try:
        result = worker.enqueue(
            {
                "action": "sessionCleanupSearch",
                "requestId": "search-request",
                "query": "active_session.py",
                "workdirId": "opaque-workdir",
            }
        )
        assert result["status"] == "accepted"
        deadline = time.monotonic() + 2
        while context.session_cleanup_payload.get("operation", {}).get("state") != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        manager.search.assert_called_once_with(
            "active_session.py",
            workdir_id="opaque-workdir",
            request_id="search-request",
            include_sessions=False,
        )
    finally:
        assert worker.close()


def test_session_cleanup_worker_indexes_after_scan_and_searches_resident_index() -> None:
    session_id = "10000000-0000-4000-8000-000000000091"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        sessions_root = root / "sessions"
        sessions_root.mkdir()
        rollout = sessions_root / "session.jsonl"
        rollout.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": session_id,
                        "cwd": str(root),
                        "model_provider": "openai-custom",
                        "originator": "Codex Desktop",
                    },
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "resident-worker-marker",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        state_db = root / "state.sqlite"
        connection = sqlite3.connect(state_db)
        try:
            connection.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, title TEXT, "
                "cwd TEXT, archived INTEGER, updated_at_ms INTEGER)"
            )
            connection.execute(
                "CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)"
            )
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, str(rollout), "Resident worker", str(root), 0, 1_700_000_000_000),
            )
            connection.commit()
        finally:
            connection.close()
        session_index = root / "session_index.jsonl"
        session_index.write_text("", encoding="utf-8")
        manager = SessionCleanupManager(
            state_db_path=state_db,
            sessions_root=sessions_root,
            session_index_path=session_index,
            search_index_path=root / "search.sqlite",
        )
        context = SimpleNamespace(
            state_db_path=state_db,
            session_cleanup_payload={},
            runtime_events=RuntimeEventBus(),
        )
        worker = SessionCleanupWorker(context, manager, on_deleted=lambda *_args: None)
        try:
            accepted = worker.enqueue(
                {"action": "sessionCleanupScan", "requestId": "resident-scan"}
            )
            assert accepted["status"] == "accepted"
            deadline = time.monotonic() + 3
            while context.session_cleanup_payload.get("operation", {}).get("state") != "completed":
                assert time.monotonic() < deadline
                time.sleep(0.01)
            while context.session_cleanup_payload.get("search", {}).get("indexState") != "ready":
                assert time.monotonic() < deadline
                time.sleep(0.01)

            result = manager.search("resident-worker-marker", request_id="resident-search")
            assert result["search"]["matches"]
            assert manager._search_index.memory_loaded is True

            row_id = result["search"]["matches"][0]
            rollout.write_text(
                rollout.read_text(encoding="utf-8")
                + json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "resident-event-marker",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            deadline = time.monotonic() + 3
            while (
                context.session_cleanup_payload.get("search", {}).get("indexState") != "ready"
                or row_id
                not in context.session_cleanup_payload.get("search", {}).get("matches", [])
            ):
                assert time.monotonic() < deadline
                time.sleep(0.01)

            worker.enqueue(
                {
                    "action": "sessionCleanupSearch",
                    "requestId": "resident-search-command",
                    "query": "resident-event-marker",
                }
            )
            while context.session_cleanup_payload.get("operation", {}).get("requestId") != "resident-search-command":
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert "sessions" in context.session_cleanup_payload
            assert "sessions" not in context.session_cleanup_delta
        finally:
            assert worker.close()


def test_session_cleanup_worker_rejects_unknown_action_and_closed_enqueue() -> None:
    manager = MagicMock()
    context = SimpleNamespace(
        session_cleanup_payload={}, runtime_events=RuntimeEventBus()
    )
    worker = SessionCleanupWorker(context, manager, on_deleted=lambda *_args: None)
    with pytest.raises(SessionCleanupError, match="unsupported"):
        worker.enqueue({"action": "unknown"})
    assert worker.close()
    with pytest.raises(SessionCleanupError, match="closed"):
        worker.enqueue({"action": "sessionCleanupScan"})
