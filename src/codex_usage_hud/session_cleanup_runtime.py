"""Serialized session-cleanup worker with injected usage refresh integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import logging
import queue
from pathlib import Path
import sqlite3
from threading import Event, Lock, Thread, Timer, current_thread
import time
import uuid

from .core.session_cleanup import (
    _canonical_uuid,
    SessionCleanupError,
    SessionCleanupItem,
    SessionCleanupManager,
)
from .core.session_search import DEFAULT_RANGE
from .core.session_transfer import CodexAppServerClient
from .core.deleted_usage import DeletedUsageLedger, DeletedUsageLedgerError
from .platforms.codex_desktop_threads import CodexDesktopThreadLifecycle
from .platforms.file_watcher import FileChangeWatcher, FileWatchSpec
from .runtime_paths import SESSION_SEARCH_DATABASE_FILENAME, hud_runtime_dir


_LOGGER = logging.getLogger("codex_usage_hud.session_cleanup_runtime")

MIGRATED_SESSION_SOURCES_FILENAME = "migrated_session_sources.json"


@dataclass(frozen=True)
class _SearchIndexJob:
    revision: str
    generation: int
    entries: tuple[tuple[str, tuple[Path, ...], str, str, str, str], ...]
    prune_missing: bool


class DeletedUsageTransactions:
    """Own prepare/commit/discard around the deleted-session usage ledger."""

    def __init__(
        self,
        ledger: DeletedUsageLedger | None,
        parser: object,
        *,
        on_commit: Callable[[], None],
    ) -> None:
        self.ledger = ledger
        self.parser = parser
        self.on_commit = on_commit

    def prepare(self, item: object) -> str:
        if self.ledger is None:
            raise DeletedUsageLedgerError(
                "Deleted-session usage ledger is not configured."
            )
        return self.ledger.prepare(
            session_id=getattr(item, "_session_id"),
            family_session_ids=(
                getattr(item, "_session_id"),
                *getattr(item, "_descendant_ids"),
            ),
            title=getattr(item, "title"),
            workdir_name=getattr(item, "workdir_name"),
            rollout_paths=getattr(item, "_rollout_paths"),
            parser=self.parser,
        )

    def commit(self, receipt: object) -> None:
        if self.ledger is None:
            raise DeletedUsageLedgerError(
                "Deleted-session usage ledger is not configured."
            )
        self.ledger.commit(str(receipt or ""))
        self.on_commit()

    def discard(self, receipt: object) -> None:
        if self.ledger is not None:
            self.ledger.discard(str(receipt or ""))


def cleanup_string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _session_transfer_provider_values(
    command: Mapping[str, object],
) -> dict[str, str]:
    return {
        "sourceProvider": str(
            command.get("sourceProvider") or command.get("source_provider") or ""
        )
        .strip()
        .casefold(),
        "targetProvider": str(
            command.get("targetProvider") or command.get("target_provider") or ""
        )
        .strip()
        .casefold(),
    }


def _transfer_inherited_session_ids(context: object) -> set[str]:
    values = getattr(context, "_work_overlay_transfer_inherited_session_ids", None)
    if isinstance(values, set):
        return values
    values = set()
    try:
        setattr(context, "_work_overlay_transfer_inherited_session_ids", values)
    except Exception:
        pass
    return values


def _load_transfer_inherited_session_ids(state_db_path: object) -> set[str]:
    try:
        path = Path(state_db_path)
        if not path.is_file():
            return set()
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro",
            uri=True,
        )
        try:
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(threads)")
            }
            required = {"id", "has_user_event", "thread_source"}
            if not required.issubset(columns):
                return set()
            rows = connection.execute(
                "SELECT id FROM threads "
                "WHERE COALESCE(has_user_event, 0) = 0 "
                "AND LOWER(COALESCE(thread_source, '')) = 'user'"
            ).fetchall()
            return {
                canonical
                for row in rows
                if (canonical := _canonical_uuid(row[0]))
            }
        finally:
            connection.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return set()


class SessionCleanupWorker:
    _ACTIONS = {
        "sessionCleanupScan",
        "sessionCleanupSearch",
        "sessionCleanupPreview",
        "sessionCleanupExecute",
        "sessionCleanupCancel",
        "providerDelete",
        "sessionTransfer",
    }

    def __init__(
        self,
        context: object,
        manager: SessionCleanupManager,
        *,
        on_deleted: Callable[[object, str], None],
    ) -> None:
        self._context = context
        self.manager = manager
        self._on_deleted = on_deleted
        _transfer_inherited_session_ids(context).update(
            _load_transfer_inherited_session_ids(
                getattr(context, "state_db_path", None)
            )
        )
        # This only captures the configured CDP endpoint.  It attaches to the
        # Desktop process if and when a migration reaches its source lifecycle
        # phase after the target has been fully verified.
        self._desktop_thread_lifecycle = CodexDesktopThreadLifecycle()
        self._queue: queue.Queue[dict[str, object] | None] = queue.Queue()
        self._index_queue: queue.Queue[_SearchIndexJob | None] = queue.Queue()
        self._closed = Event()
        self._index_closed = Event()
        self._index_idle = Event()
        self._index_idle.set()
        self._index_schedule_lock = Lock()
        self._index_generation = 0
        self._index_clear_in_progress = False
        self._index_operations_idle = Event()
        self._index_operations_idle.set()
        self._index_active_operations = 0
        self._pending_search_paths: set[Path] = set()
        self._pending_search_full = False
        self._search_change_timer: Timer | None = None
        self._search_watcher = FileChangeWatcher(self._on_search_files_changed)
        self._worker = Thread(
            target=self._run,
            name="codex-usage-hud-session-cleanup",
            daemon=True,
        )
        self._index_worker = Thread(
            target=self._run_search_indexer,
            name="codex-usage-hud-session-search-index",
            daemon=True,
        )
        self._worker.start()
        self._index_worker.start()

    def enqueue(self, command: Mapping[str, object]) -> dict[str, object]:
        action = str(command.get("action") or "").strip()
        if action == "deleteProvider":
            action = "providerDelete"
        if action not in self._ACTIONS:
            raise SessionCleanupError("unsupported session-cleanup command")
        if self._closed.is_set():
            raise SessionCleanupError("session cleanup worker is closed")
        request_id = str(command.get("requestId") or "").strip() or uuid.uuid4().hex
        payload = dict(command)
        payload["action"] = action
        payload["requestId"] = request_id
        if action == "sessionTransfer":
            payload["startedAt"] = int(time.time() * 1000)
        if action == "sessionCleanupCancel":
            self._publish(self.manager.cancel(request_id=request_id))
        else:
            accepted_values = (
                _session_transfer_provider_values(payload)
                if action == "sessionTransfer"
                else {}
            )
            if action == "sessionTransfer":
                accepted_values["startedAt"] = payload["startedAt"]
                accepted_values["mode"] = (
                    str(payload.get("mode") or "copy").strip().casefold()
                )
                accepted_values["selectedIds"] = cleanup_string_list(
                    payload.get("itemIds") or payload.get("sessionIds")
                )
                accepted_values["sessionCount"] = len(accepted_values["selectedIds"])
                accepted_values["transferPhase"] = "targets"
                accepted_values["phaseLabel"] = "创建目标会话"
                accepted_values["phaseIndex"] = 1
                accepted_values["phaseCount"] = (
                    2 if accepted_values["mode"] == "migrate" else 1
                )
            self._publish(
                self.manager.mark_operation(
                    request_id=request_id,
                    action=action,
                    state="scanning" if action == "sessionCleanupScan" else "accepted",
                    progress=0,
                    include_sessions=action != "sessionCleanupSearch",
                    **accepted_values,
                )
            )
            self._queue.put_nowait(payload)
        return {"status": "accepted", "requestId": request_id, "action": action}

    def _real_search_manager(self) -> SessionCleanupManager | None:
        return self.manager if isinstance(self.manager, SessionCleanupManager) else None

    def refresh_warm_search(self) -> None:
        """Serialize warm-index notifications behind pending search responses."""
        if not self._closed.is_set():
            self._queue.put_nowait({"action": "sessionCleanupWarmSearch"})

    def _start_search_watcher(self) -> None:
        manager = self._real_search_manager()
        if manager is None:
            return
        roots = [manager.sessions_root]
        if manager.sessions_root.name == "sessions":
            roots.append(manager.sessions_root.parent / "archived_sessions")
        specs = [
            FileWatchSpec.tree(root, "session-search-rollout", suffixes=(".jsonl",))
            for root in roots
        ]
        specs.append(FileWatchSpec.file(manager.session_index_path, "session-search-metadata"))
        try:
            self._search_watcher.update(specs)
        except Exception:
            _LOGGER.debug("session_search_watcher_start_failed", exc_info=True)

    def _on_search_files_changed(
        self,
        reasons: set[str],
        changed_paths: set[Path],
    ) -> None:
        if self._closed.is_set():
            return
        with self._index_schedule_lock:
            self._pending_search_paths.update(changed_paths)
            self._pending_search_full = self._pending_search_full or (
                "session-search-metadata" in reasons or not changed_paths
            )
            if self._search_change_timer is None:
                self._search_change_timer = Timer(
                    0.15,
                    self._flush_search_file_changes,
                )
                self._search_change_timer.daemon = True
                self._search_change_timer.start()

    def _flush_search_file_changes(self) -> None:
        with self._index_schedule_lock:
            self._search_change_timer = None
            changed_paths = set(self._pending_search_paths)
            full = self._pending_search_full
            self._pending_search_paths.clear()
            self._pending_search_full = False
        if self._closed.is_set():
            return
        manager = self._real_search_manager()
        if manager is None:
            return
        snapshot = manager.snapshot(include_sessions=False)
        scheduled = self._schedule_search_index(
            snapshot,
            changed_paths=() if full else changed_paths,
            prune_missing=full,
        )
        if scheduled is not None:
            self._publish(scheduled)

    def _schedule_search_index(
        self,
        snapshot: Mapping[str, object],
        *,
        changed_paths: Sequence[Path] = (),
        prune_missing: bool = False,
    ) -> Mapping[str, object] | None:
        with self._index_schedule_lock:
            if self._index_clear_in_progress:
                return None
            self._index_active_operations += 1
            if self._index_active_operations == 1:
                self._index_operations_idle.clear()
        try:
            return self._schedule_search_index_impl(
                snapshot,
                changed_paths=changed_paths,
                prune_missing=prune_missing,
            )
        finally:
            with self._index_schedule_lock:
                self._index_active_operations = max(
                    0, self._index_active_operations - 1
                )
                if self._index_active_operations == 0:
                    self._index_operations_idle.set()

    def _schedule_search_index_impl(
        self,
        snapshot: Mapping[str, object],
        *,
        changed_paths: Sequence[Path] = (),
        prune_missing: bool = False,
    ) -> Mapping[str, object] | None:
        manager = self._real_search_manager()
        if manager is None:
            return None
        revision = str(snapshot.get("revision") or "").strip()
        if not revision:
            return None

        # The progressive warm job is the canonical owner of the resident
        # search index in Renderer mode for full inventory updates. File-change
        # deltas still use the compatibility worker so already-indexed changed
        # sessions are refreshed; both paths use the clear-operation lease
        # above so deletion cannot overlap a database writer.
        warm_job = getattr(self._context, "session_index_warm_job", None)
        try:
            warm_status = (
                warm_job.status()
                if warm_job is not None
                and callable(getattr(warm_job, "status", None))
                else None
            )
        except Exception:
            warm_status = None
        if isinstance(warm_status, Mapping) and warm_status.get("enabled") is False:
            # The feature switch applies to both full-scan and file-change
            # paths. Do not let the compatibility worker build a changed-file
            # delta while automatic indexing is disabled.
            return manager.snapshot()
        if (
            not changed_paths
            and isinstance(warm_status, Mapping)
        ):
            try:
                selected_range = str(
                    warm_status.get("selectedRange") or DEFAULT_RANGE
                ).strip().casefold()
                job_state = str(warm_status.get("jobState") or "idle").strip().casefold()
                if prune_missing or job_state not in {"running", "attached"}:
                    starter = getattr(warm_job, "start", None)
                    if callable(starter):
                        if not bool(starter(selected_range or DEFAULT_RANGE)):
                            # A concurrent clear/close can reject the start;
                            # never let the cleanup-only reconciliation below
                            # reopen the database after that rejection.
                            return manager.snapshot()
                if prune_missing:
                    remove_missing = getattr(manager._search_index, "remove_missing", None)
                    if callable(remove_missing):
                        remove_missing(
                            entry[0] for entry in manager.search_index_entries()
                        )
                return manager.snapshot()
            except Exception:
                # Fall back to the compatibility indexer if the optional warm
                # job is unavailable or fails during startup.  Search remains
                # functional, and the exception is retained for diagnostics.
                _LOGGER.debug("session_index_warm_delegate_failed", exc_info=True)
        entries = manager.search_index_entries(changed_paths)
        if changed_paths and not entries:
            return None
        started = manager.begin_search_index(revision, len(entries))
        search = started.get("search")
        if not isinstance(search, Mapping):
            return None
        generation = int(search.get("generation") or 0)
        if generation <= 0:
            return None
        with self._index_schedule_lock:
            if self._index_clear_in_progress:
                return None
            self._index_generation = generation
            self._index_queue.put_nowait(
                _SearchIndexJob(
                    revision=revision,
                    generation=generation,
                    entries=entries,
                    prune_missing=bool(prune_missing),
                )
            )
        return manager.snapshot()

    def _search_index_job_current(self, job: _SearchIndexJob) -> bool:
        with self._index_schedule_lock:
            return (
                not self._closed.is_set()
                and not self._index_closed.is_set()
                and self._index_generation == job.generation
            )

    def prepare_search_index_clear(self, timeout_seconds: float = 15.0) -> bool:
        """Invalidate and drain the compatibility index worker before clear.

        Renderer warm jobs own normal indexing, but file-change updates can
        still be queued on this compatibility worker during construction or a
        fallback path.  Advancing its generation makes the active batch stop
        at its next cancellation check and makes queued work stale; the idle
        event prevents the caller from deleting SQLite while it still owns a
        connection.
        """
        with self._index_schedule_lock:
            self._index_clear_in_progress = True
            self._index_generation += 1
            timer = self._search_change_timer
            self._search_change_timer = None
            self._pending_search_paths.clear()
            self._pending_search_full = False
        if timer is not None:
            timer.cancel()
        timeout = max(0.0, float(timeout_seconds))
        deadline = time.monotonic() + timeout
        ready = True
        for idle_event in (self._index_idle, self._index_operations_idle):
            remaining = max(0.0, deadline - time.monotonic())
            if not idle_event.wait(timeout=remaining):
                ready = False
                break
        if not ready:
            with self._index_schedule_lock:
                self._index_clear_in_progress = False
        return ready

    def finish_search_index_clear(self) -> None:
        """Allow new compatibility work after the index files are removed."""
        with self._index_schedule_lock:
            self._index_clear_in_progress = False

    def _run_search_indexer(self) -> None:
        manager = self._real_search_manager()
        if manager is None:
            while not self._index_closed.is_set():
                job = self._index_queue.get()
                if job is None:
                    return
            return
        while not self._index_closed.is_set():
            job = self._index_queue.get()
            if job is None:
                return
            with self._index_schedule_lock:
                if (
                    self._closed.is_set()
                    or self._index_closed.is_set()
                    or self._index_clear_in_progress
                    or self._index_generation != job.generation
                ):
                    continue
                # Checking the generation and marking the worker in-flight are
                # one critical section. Otherwise clear could observe the idle
                # event between these two operations and delete SQLite just
                # before this worker opens it again.
                self._index_idle.clear()
            try:
                loaded = manager._search_index.load()
                if not self._search_index_job_current(job):
                    continue
                self._publish(
                    manager.update_search_index(
                        job.revision,
                        0,
                        len(job.entries),
                        int(loaded.get("indexed") or 0),
                        generation=job.generation,
                    )
                )

                def report(processed: int, total: int, indexed: int) -> None:
                    if not self._search_index_job_current(job):
                        return
                    self._publish(
                        manager.update_search_index(
                            job.revision,
                            processed,
                            total,
                            indexed,
                            generation=job.generation,
                        )
                    )

                manager._search_index.sync_batches(
                    job.entries,
                    total=len(job.entries),
                    batch_size=64,
                    progress_callback=report,
                    cancelled=lambda: not self._search_index_job_current(job),
                )
                if not self._search_index_job_current(job):
                    continue
                if job.prune_missing:
                    manager._search_index.remove_missing(
                        entry[0] for entry in job.entries
                    )
                indexed = manager._search_index.count()
                self._publish(
                    manager.update_search_index(
                        job.revision,
                        len(job.entries),
                        len(job.entries),
                        indexed,
                        state="ready",
                        generation=job.generation,
                    )
                )
            except Exception as exc:
                if not self._search_index_job_current(job):
                    continue
                _LOGGER.debug("session_search_index_failed", exc_info=True)
                self._publish(
                    manager.update_search_index(
                        job.revision,
                        0,
                        len(job.entries),
                        0,
                        state="failed",
                        error=type(exc).__name__,
                        generation=job.generation,
                    )
                )
            finally:
                self._index_idle.set()

    def _latest_queued_search(self, command: dict[str, object]) -> dict[str, object]:
        """Collapse consecutive keystroke searches before doing any work."""

        latest = command
        deferred: dict[str, object] | None = None
        while True:
            try:
                queued = self._queue.get_nowait()
            except queue.Empty:
                break
            if queued is None:
                self._queue.put_nowait(None)
                break
            if str(queued.get("action") or "") == "sessionCleanupSearch":
                latest = queued
                continue
            deferred = queued
            break
        if deferred is not None:
            self._queue.put_nowait(deferred)
        return latest

    def close(self, timeout_seconds: float = 2.0) -> bool:
        if self._closed.is_set():
            return not self._worker.is_alive() and not self._index_worker.is_alive()
        self._closed.set()
        with self._index_schedule_lock:
            timer = self._search_change_timer
            self._search_change_timer = None
            self._pending_search_paths.clear()
            self._pending_search_full = False
        if timer is not None:
            timer.cancel()
        self._search_watcher.close()
        self._index_closed.set()
        self._index_queue.put_nowait(None)
        self._queue.put_nowait(None)
        if self._worker is not current_thread() and self._worker.is_alive():
            self._worker.join(timeout=max(0.0, float(timeout_seconds)))
        if self._index_worker is not current_thread() and self._index_worker.is_alive():
            self._index_worker.join(timeout=max(0.0, float(timeout_seconds)))
        return not self._worker.is_alive() and not self._index_worker.is_alive()

    def _run(self) -> None:
        while True:
            command = self._queue.get()
            if command is None:
                return
            action = str(command.get("action") or "")
            if action == "sessionCleanupSearch":
                command = self._latest_queued_search(command)
                action = str(command.get("action") or "")
            request_id = str(command.get("requestId") or "")
            refresh_after_delete = False
            transfer_values = (
                _session_transfer_provider_values(command)
                if action == "sessionTransfer"
                else {}
            )
            try:
                if action == "sessionCleanupScan":
                    previous_publisher = getattr(
                        self.manager, "progress_publisher", None
                    )
                    self.manager.progress_publisher = self._publish
                    try:
                        snapshot = self.manager.scan(request_id=request_id)
                    finally:
                        self.manager.progress_publisher = previous_publisher
                    indexed_snapshot = self._schedule_search_index(snapshot, prune_missing=True)
                    if indexed_snapshot is not None:
                        snapshot = indexed_snapshot
                    self._start_search_watcher()
                elif action == "sessionCleanupWarmSearch":
                    manager = self._real_search_manager()
                    status = getattr(self._context, "session_index_payload", {})
                    if manager is None or not status.get("enabled", True):
                        continue
                    current = manager.snapshot(include_sessions=False)
                    search = current.get("search", {})
                    if not str(search.get("query") or "").strip():
                        continue
                    index_state = (
                        "failed" if status.get("jobState") == "error"
                        else "indexing" if status.get("jobState") in {"running", "attached"}
                        or not manager._search_index.memory_loaded
                        else "ready"
                    )
                    snapshot = manager.update_search_index(
                        str(current.get("revision") or ""),
                        int(status.get("builtCount") or 0),
                        int(status.get("totalCount") or 0),
                        int(status.get("builtCount") or 0),
                        state=index_state,
                        error=str(status.get("error") or ""),
                        generation=int(search.get("generation") or 0),
                    )
                elif action == "sessionCleanupSearch":
                    snapshot = self.manager.search(
                        str(command.get("query") or command.get("search") or ""),
                        workdir_id=str(
                            command.get("workdirId")
                            or command.get("workdir_id")
                            or ""
                        ),
                        request_id=request_id,
                        include_sessions=False,
                    )
                elif action == "sessionCleanupPreview":
                    snapshot = self.manager.preview(
                        cleanup_string_list(
                            command.get("itemIds") or command.get("sessionIds")
                        ),
                        str(command.get("inventoryRevision") or ""),
                        request_id=request_id,
                    )
                elif action == "providerDelete":
                    # config.toml / 单价删除已在 dispatch 同步请求阶段完成；此处只负责
                    # 后台清理该供应商的会话历史，避免历史较多时阻塞前端删除反馈。
                    provider_id = (
                        str(command.get("provider") or command.get("providerId") or "")
                        .strip()
                        .casefold()
                    )
                    app_provider = (
                        str(getattr(self._context, "app_provider", "") or "")
                        .strip()
                        .casefold()
                    )
                    if provider_id and provider_id == app_provider:
                        raise SessionCleanupError(
                            "默认 Codex App Provider 不支持删除供应商配置。"
                        )
                    history_snapshot: Mapping[str, object] | None = None
                    if bool(command.get("deleteSessionHistory")):
                        history_snapshot = self.manager.delete_provider_history(
                            provider_id,
                            request_id=request_id,
                        )
                    history_operation = (
                        history_snapshot.get("operation")
                        if isinstance(history_snapshot, Mapping)
                        else {}
                    )
                    history_deleted = int(
                        history_operation.get("deletedCount") or 0
                        if isinstance(history_operation, Mapping)
                        else 0
                    )
                    history_actual_bytes = int(
                        history_operation.get("actualBytes") or 0
                        if isinstance(history_operation, Mapping)
                        else 0
                    )
                    snapshot = self.manager.mark_operation(
                        request_id=request_id,
                        action=action,
                        state="completed",
                        progress=100,
                        provider=provider_id,
                        historyDeletedCount=history_deleted,
                        deletedCount=history_deleted,
                        actualBytes=history_actual_bytes,
                        failedCount=0,
                    )
                    refresh_after_delete = history_deleted > 0
                elif action == "sessionTransfer":
                    source_provider = transfer_values["sourceProvider"]
                    target_provider = transfer_values["targetProvider"]
                    mode = str(command.get("mode") or "copy").strip().casefold()
                    self._validate_transfer_provider(
                        source_provider,
                        target_provider,
                    )
                    item_ids = cleanup_string_list(
                        command.get("itemIds") or command.get("sessionIds")
                    )
                    revision = str(command.get("inventoryRevision") or "")
                    previous_publisher = getattr(
                        self.manager, "progress_publisher", None
                    )
                    self.manager.progress_publisher = self._publish
                    try:
                        sessions_root = getattr(self._context, "sessions_root", None)
                        codex_home = (
                            Path(sessions_root).parent
                            if sessions_root is not None
                            else None
                        )
                        # ``thread/fork`` keeps the child rollout writer owned by
                        # its App Server process.  On Windows that handle can
                        # reject the materializer's atomic ``os.replace`` while
                        # the same connection remains open.  Close the fork
                        # connection before rewriting the target, then create a
                        # fresh connection for the durable ``thread/read`` check.
                        app_server_client: CodexAppServerClient | None = None
                        app_server: object | None = None
                        app_server_lifecycle_lock = Lock()

                        def open_app_server() -> object:
                            nonlocal app_server_client, app_server
                            with app_server_lifecycle_lock:
                                app_server_client = CodexAppServerClient(
                                    codex_home=codex_home
                                )
                                app_server = app_server_client.__enter__()
                                return app_server

                        def close_app_server() -> None:
                            nonlocal app_server_client, app_server
                            with app_server_lifecycle_lock:
                                client = app_server_client
                                app_server_client = None
                                app_server = None
                                if client is not None:
                                    client.__exit__(None, None, None)

                        def current_app_server() -> object:
                            if app_server is None:
                                raise SessionCleanupError(
                                    "Codex App Server 连接当前不可用。"
                                )
                            return app_server

                        def materialize_target(
                            target_id: str,
                            source_id: str,
                        ) -> object:
                            close_app_server()
                            try:
                                return self.manager.materialize_target_rollout(
                                    target_id,
                                    source_id,
                                )
                            finally:
                                open_app_server()

                        def materialize_targets(
                            targets: Sequence[tuple[str, str]],
                            progress_callback: Callable[
                                [str, str, bool, str], None
                            ] | None = None,
                        ) -> Mapping[str, object]:
                            # Fork writers keep Windows file handles open until
                            # the App Server connection closes. Materialize the
                            # whole fork phase behind one close/reopen boundary.
                            close_app_server()
                            try:
                                return self.manager.materialize_target_rollouts(
                                    targets,
                                    progress_callback=progress_callback,
                                )
                            finally:
                                open_app_server()

                        def archive_and_delete_source(
                            source_id: str,
                            cwd: str,
                        ) -> Mapping[str, object]:
                            # The target has already passed its durable/resume
                            # checks when the manager enters this callback.
                            # Release the separate fork connection so Desktop's
                            # own App Server is the sole owner of the source
                            # lifecycle and its Local Thread Catalog update.
                            close_app_server()
                            return self._desktop_thread_lifecycle.archive_then_delete(
                                source_id,
                                cwd=cwd,
                            ).to_payload()

                        def preflight_source_family(
                            source_ids: Sequence[str],
                            cwd: str,
                        ) -> Mapping[str, object]:
                            close_app_server()
                            return self._desktop_thread_lifecycle.preflight(
                                source_ids,
                                cwd=cwd,
                            )

                        open_app_server()
                        try:
                            snapshot = self.manager.transfer(
                                item_ids,
                                revision,
                                source_provider,
                                target_provider,
                                mode,
                                fork=lambda session_id, provider, cwd: (
                                    current_app_server().fork(
                                        session_id,
                                        provider,
                                        cwd=cwd,
                                    )
                                ),
                                materialize=materialize_target,
                                verify=lambda session_id, provider: (
                                    current_app_server().verify_persistent_thread(
                                        session_id,
                                        provider,
                                    )
                                ),
                                materialize_batch=materialize_targets,
                                verify_batch=lambda targets, progress_callback=None: (
                                    current_app_server().verify_persistent_threads(
                                        targets,
                                        progress_callback=progress_callback,
                                    )
                                ),
                                desktop_source_lifecycle=archive_and_delete_source,
                                desktop_source_preflight=preflight_source_family,
                                started_at_ms=command.get("startedAt"),
                                request_id=request_id,
                            )
                        finally:
                            close_app_server()
                    finally:
                        self.manager.progress_publisher = previous_publisher
                    transfer_operation = snapshot.get("operation")
                    refresh_after_delete = bool(
                        isinstance(transfer_operation, Mapping)
                        and int(transfer_operation.get("migratedCount") or 0) > 0
                    )
                else:
                    snapshot = self.manager.execute(
                        cleanup_string_list(
                            command.get("itemIds") or command.get("sessionIds")
                        ),
                        str(command.get("inventoryRevision") or ""),
                        str(command.get("confirmationToken") or ""),
                        request_id=request_id,
                    )
                    operation = snapshot.get("operation")
                    if (
                        isinstance(operation, Mapping)
                        and int(operation.get("deletedCount") or 0) > 0
                    ):
                        refresh_after_delete = True
            except Exception as exc:
                failure_values: dict[str, object] = {}
                if action == "providerDelete":
                    failure_values["provider"] = (
                        str(command.get("provider") or command.get("providerId") or "")
                        .strip()
                        .lower()
                    )
                elif action == "sessionTransfer":
                    failure_values.update(transfer_values)
                    if command.get("startedAt"):
                        failure_values["startedAt"] = command.get("startedAt")
                snapshot = self.manager.mark_operation(
                    request_id=request_id,
                    action=action,
                    state="failed",
                    progress=100,
                    error=str(exc) or type(exc).__name__,
                    **failure_values,
                )
            operation = snapshot.get("operation")
            operation_state = (
                str(operation.get("state") or "").casefold()
                if isinstance(operation, Mapping)
                else ""
            )
            if (
                action in {"sessionCleanupExecute", "providerDelete", "sessionTransfer"}
                and operation_state not in {"failed", "cancelled"}
            ):
                indexed_snapshot = self._schedule_search_index(
                    snapshot,
                    prune_missing=True,
                )
                if indexed_snapshot is not None:
                    snapshot = indexed_snapshot
            self._publish(snapshot)
            if refresh_after_delete:
                Thread(
                    target=self._refresh_deleted_usage,
                    args=(request_id,),
                    name="codex-usage-hud-deleted-usage-refresh",
                    daemon=True,
                ).start()

    def _validate_transfer_provider(self, source: str, target: str) -> None:
        if not source or not target:
            raise SessionCleanupError("会话迁移需要源和目标 Provider。")
        if source == target:
            raise SessionCleanupError("源 Provider 与目标 Provider 不能相同。")
        registry = getattr(self._context, "provider_registry", None)
        entries = getattr(registry, "entries", {})
        target_entry = entries.get(target) if isinstance(entries, Mapping) else None
        app_provider = (
            str(getattr(self._context, "app_provider", "") or "").strip().casefold()
        )
        if target != app_provider and target_entry is None:
            raise SessionCleanupError("目标 Provider 不在当前 Codex 配置中。")
        if target != app_provider and target_entry is not None:
            configured = any(
                bool(getattr(target_entry, name, False))
                for name in (
                    "from_base_config",
                    "from_profile",
                    "from_provider_definition",
                    "from_saved_settings",
                )
            )
            if not configured:
                raise SessionCleanupError("目标 Provider 只有历史记录，尚未配置。")

    def _refresh_deleted_usage(self, request_id: str) -> None:
        try:
            self._on_deleted(self._context, request_id)
        except Exception:
            _LOGGER.exception("deleted_session_usage_refresh_callback_failed")

    def _publish(self, payload: Mapping[str, object]) -> None:
        snapshot = dict(payload)
        previous = getattr(self._context, "session_cleanup_payload", {})
        setattr(self._context, "session_cleanup_delta", snapshot)
        if (
            "sessions" not in snapshot
            and isinstance(previous, Mapping)
            and isinstance(previous.get("sessions"), list)
        ):
            stored = {**previous, **snapshot}
            stored["sessions"] = previous["sessions"]
            if "workdirs" not in snapshot and "workdirs" in previous:
                stored["workdirs"] = previous["workdirs"]
            snapshot = stored
        setattr(self._context, "session_cleanup_payload", snapshot)
        operation = snapshot.get("operation")
        values = operation if isinstance(operation, Mapping) else {}
        if str(values.get("action") or "").strip().casefold() == "sessiontransfer":
            results = values.get("results")
            if isinstance(results, Sequence) and not isinstance(
                results,
                (str, bytes, bytearray),
            ):
                inherited_ids = _transfer_inherited_session_ids(self._context)
                for result in results:
                    if not isinstance(result, Mapping):
                        continue
                    if not bool(result.get("forked") or result.get("targetCreated")):
                        continue
                    target_id = _canonical_uuid(result.get("targetSessionId"))
                    if target_id:
                        inherited_ids.add(target_id)
        event_bus = getattr(self._context, "runtime_events", None)
        publish = getattr(event_bus, "publish", None)
        if not callable(publish):
            return
        publish(
            "session_cleanup_changed",
            source="session_cleanup",
            context={
                "requestId": str(values.get("requestId") or ""),
                "action": str(values.get("action") or ""),
                "state": str(values.get("state") or ""),
                "revision": str(snapshot.get("revision") or ""),
            },
        )


def _session_cleanup_current_ids(context: object) -> tuple[str, ...]:
    values: list[str] = []
    values.append(
        str(getattr(context, "session_management_current_session_id", "") or "")
    )
    resolver = getattr(context, "session_resolver", None)
    values.append(str(getattr(resolver, "session_id", "") or ""))
    tracker = getattr(context, "active_session_tracker", None)
    values.append(str(getattr(tracker, "latest_session_id", "") or ""))
    return tuple(values)


def _session_cleanup_active_ids(context: object) -> tuple[str, ...]:
    values = getattr(context, "session_management_active_session_ids", set())
    return tuple(str(value) for value in values)


def _prepare_session_cleanup_usage(context: object, item: SessionCleanupItem) -> object:
    try:
        return getattr(context, "usage_cache").prepare_deleted_session_usage(item)
    except DeletedUsageLedgerError as exc:
        raise SessionCleanupError(str(exc)) from exc


def _commit_session_cleanup_usage(context: object, receipt: object) -> None:
    try:
        getattr(context, "usage_cache").commit_deleted_session_usage(receipt)
    except DeletedUsageLedgerError as exc:
        raise SessionCleanupError(str(exc)) from exc


def _discard_session_cleanup_usage(context: object, receipt: object) -> None:
    try:
        getattr(context, "usage_cache").discard_deleted_session_usage(receipt)
    except DeletedUsageLedgerError:
        pass


def _build_session_cleanup_manager(context: object) -> SessionCleanupManager:
    return SessionCleanupManager(
        state_db_path=Path(getattr(context, "state_db_path")),
        sessions_root=Path(getattr(context, "sessions_root")),
        session_index_path=Path(getattr(context, "session_index_path")),
        thread_history_db_path=(
            Path(getattr(context, "sessions_root")).parent
            / "thread_history_1.sqlite"
        ),
        current_session_ids=lambda: _session_cleanup_current_ids(context),
        active_session_ids=lambda: _session_cleanup_active_ids(context),
        usage_snapshot_prepare=lambda item: _prepare_session_cleanup_usage(
            context, item
        ),
        usage_snapshot_commit=lambda receipt: _commit_session_cleanup_usage(
            context, receipt
        ),
        usage_snapshot_discard=lambda receipt: _discard_session_cleanup_usage(
            context, receipt
        ),
        transfer_max_workers=max(
            1,
            min(8, int(getattr(context, "session_transfer_max_workers", 4) or 4)),
        ),
        # Migrated sources must stay hidden even after a HUD restart while
        # Desktop's state-db deletion lags behind its confirmed notification.
        migrated_source_store=(
            hud_runtime_dir() / MIGRATED_SESSION_SOURCES_FILENAME
        ),
        search_index_path=(
            hud_runtime_dir() / SESSION_SEARCH_DATABASE_FILENAME
        ),
    )
