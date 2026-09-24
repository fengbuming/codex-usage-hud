"""Selected-session preview, incremental hydration, and stale-sequence rules."""

from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass
import logging
from pathlib import Path
import threading
import time

from .core import JsonlSessionParser, JsonlTailState, ParsedSession, SseRequestStateMachine
from .core.runtime_events import RuntimeEventBus
from .platforms import is_new_session_source, is_pending_session_source
from .usage_contributions import usage_parser_version

_LOGGER = logging.getLogger(__name__)


def session_path_key(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.expanduser().resolve(strict=False)).casefold()
    except OSError:
        return str(path.expanduser().absolute()).casefold()


def selection_is_stale(snapshot: ParsedSession, tracker: object | None) -> bool:
    snapshot_seq = int(getattr(snapshot, "selection_seq", 0) or 0)
    current_seq = int(getattr(tracker, "selection_seq", 0) or 0)
    return bool(snapshot_seq and current_seq and snapshot_seq != current_seq)


@dataclass(frozen=True, slots=True)
class SelectedSessionSnapshot:
    snapshot: ParsedSession
    path: Path | None
    selection_source: str


def _missing_snapshot(context: object, selection_source: str) -> ParsedSession:
    resolver = context.session_resolver
    if (
        is_new_session_source(selection_source)
        or is_pending_session_source(selection_source)
        or str(selection_source or "").startswith("renderer-waiting")
    ):
        return ParsedSession(status="waiting")
    if resolver.session_id:
        return ParsedSession(
            status="missing",
            error=f"Session id not found under {context.sessions_root}: {resolver.session_id}",
        )
    if resolver.session_file is not None:
        return ParsedSession(
            status="missing",
            error=f"Session file not found: {resolver.session_file}",
        )
    if context.sessions_root.exists():
        return ParsedSession(
            status="waiting",
            error=f"No local Codex session JSONL found under {context.sessions_root}",
        )
    return ParsedSession(
        status="missing",
        error=f"Sessions directory not found: {context.sessions_root}",
    )


def resolve_selected_snapshot(context: object) -> SelectedSessionSnapshot:
    session_path, selection_source = context.session_resolver.resolve()
    if session_path is None:
        snapshot = _missing_snapshot(context, selection_source)
    else:
        cache = getattr(context, "session_snapshot_cache", None)
        snapshot_for = getattr(cache, "snapshot_for", None)
        if callable(snapshot_for):
            snapshot = snapshot_for(
                session_path,
                session_id=str(
                    getattr(context.session_resolver, "session_id", "") or ""
                ),
            )
        else:
            snapshot, tail_state = context.parser.parse_file_incremental(
                session_path,
                getattr(context, "current_session_tail_state", None),
                sse_tracker=context.sse_tracker,
            )
            context.current_session_tail_state = tail_state
    snapshot.selection_source = selection_source
    return SelectedSessionSnapshot(snapshot, session_path, selection_source)


@dataclass
class _CacheEntry:
    state: JsonlTailState
    snapshot: ParsedSession
    file_size: int
    mtime: float
    accessed_at: float
    parser_version: str


def clone_cached_snapshot(snapshot: ParsedSession) -> ParsedSession:
    cloned = copy.copy(snapshot)
    cloned.request = copy.copy(snapshot.request)
    cloned.budget_warnings = list(snapshot.budget_warnings)
    cloned.active_work_items = list(snapshot.active_work_items)
    cloned.activity_steps = [
        copy.copy(step) for step in getattr(snapshot, "activity_steps", [])
    ]
    cloned.follow_timing = dict(snapshot.follow_timing)
    cloned.match_candidates = [dict(item) for item in snapshot.match_candidates]
    return cloned


class SessionSnapshotCache:
    """Serve bounded previews while one worker hydrates incremental session state."""

    def __init__(
        self,
        parser: JsonlSessionParser,
        *,
        event_bus: RuntimeEventBus | None = None,
        sse_tracker: SseRequestStateMachine | None = None,
        max_entries: int = 4,
        preview_bytes: int = 256 * 1024,
    ) -> None:
        self._parser = parser
        self._event_bus = event_bus
        self._sse_tracker = sse_tracker
        self._max_entries = max(1, int(max_entries))
        self._preview_bytes = max(1, int(preview_bytes))
        self._entries: dict[Path, _CacheEntry] = {}
        self._pending: deque[Path] = deque()
        self._pending_session_ids: dict[Path, str] = {}
        self._queued: set[Path] = set()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._worker = threading.Thread(
            target=self._run,
            name="codex-usage-hud-session-cache",
            daemon=True,
        )
        self._worker.start()

    @staticmethod
    def _cache_path(path: Path) -> Path:
        try:
            return path.expanduser().resolve(strict=False)
        except OSError:
            return path.expanduser().absolute()

    def snapshot_for(self, path: Path, *, session_id: str = "") -> ParsedSession:
        key = self._cache_path(path)
        parser_version = usage_parser_version(self._parser)
        try:
            stat = key.stat()
        except OSError:
            return self._parser.parse_file_tail_preview(
                key, session_id=session_id or None, max_bytes=self._preview_bytes
            )
        preserve_previous_cost = False
        previous_cost: float | None = None
        with self._lock:
            entry = self._entries.get(key)
            if (
                entry is not None
                and entry.parser_version == parser_version
                and entry.file_size == int(stat.st_size)
                and entry.mtime == stat.st_mtime
            ):
                entry.accessed_at = time.monotonic()
                cached = clone_cached_snapshot(entry.snapshot)
                if session_id and str(cached.session_id or "").strip() in {"", "n/a"}:
                    cached.session_id = session_id
                return cached
            if (
                entry is not None
                and entry.parser_version == parser_version
                and int(stat.st_size) > entry.file_size
            ):
                current_file_id = self._parser._file_id(key, stat)
                if entry.state.file_id == current_file_id:
                    preserve_previous_cost = True
                    previous_cost = entry.snapshot.confirmed.cumulative_cost_usd
            self._enqueue_locked(key, session_id)
        preview = self._parser.parse_file_tail_preview(
            key, session_id=session_id or None, max_bytes=self._preview_bytes
        )
        if preserve_previous_cost:
            preview.confirmed.cumulative_cost_usd = previous_cost
        return preview

    def _enqueue_locked(self, path: Path, session_id: str) -> None:
        if self._closed.is_set():
            return
        if session_id:
            self._pending_session_ids[path] = session_id
        if path in self._queued:
            return
        self._queued.add(path)
        self._pending.append(path)
        self._wake.set()

    def _run(self) -> None:
        while not self._closed.is_set():
            self._wake.wait()
            self._wake.clear()
            while not self._closed.is_set():
                with self._lock:
                    if not self._pending:
                        break
                    path = self._pending.popleft()
                    session_id = self._pending_session_ids.get(path, "")
                    previous = self._entries.get(path)
                    parser_version = usage_parser_version(self._parser)
                    state = (
                        previous.state
                        if previous is not None
                        and previous.parser_version == parser_version
                        else None
                    )
                pricing_changed = False
                try:
                    snapshot, state = self._parser.parse_file_incremental(
                        path,
                        state,
                        session_id=session_id or None,
                        sse_tracker=self._sse_tracker,
                    )
                    stat = path.stat()
                except OSError as exc:
                    _LOGGER.info(
                        "renderer_session_cache_hydrate_failed path=%s error=%s",
                        path,
                        exc,
                    )
                except Exception:
                    _LOGGER.exception(
                        "renderer_session_cache_hydrate_failed path=%s", path
                    )
                else:
                    with self._lock:
                        pricing_changed = (
                            parser_version != usage_parser_version(self._parser)
                        )
                        if not self._closed.is_set() and not pricing_changed:
                            latest_session_id = self._pending_session_ids.get(path, "")
                            if latest_session_id and str(snapshot.session_id or "").strip() in {
                                "",
                                "n/a",
                            }:
                                snapshot.session_id = latest_session_id
                            self._entries[path] = _CacheEntry(
                                state=state,
                                snapshot=snapshot,
                                file_size=int(stat.st_size),
                                mtime=stat.st_mtime,
                                accessed_at=time.monotonic(),
                                parser_version=parser_version,
                            )
                            self._trim_locked()
                    if not self._closed.is_set() and not pricing_changed:
                        self._publish_hydrated(path)
                finally:
                    with self._lock:
                        self._queued.discard(path)
                        latest_session_id = self._pending_session_ids.pop(
                            path, session_id
                        )
                        if pricing_changed and not self._closed.is_set():
                            self._enqueue_locked(path, latest_session_id)

    def _trim_locked(self) -> None:
        while len(self._entries) > self._max_entries:
            oldest = min(self._entries, key=lambda key: self._entries[key].accessed_at)
            del self._entries[oldest]

    def _publish_hydrated(self, path: Path) -> None:
        publish = getattr(self._event_bus, "publish", None)
        if callable(publish):
            publish(
                "session_snapshot_hydrated",
                source="session_snapshot_cache",
                session=session_path_key(path),
            )

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._wake.set()
        if self._worker.is_alive():
            self._worker.join(timeout=0.2)


__all__ = [
    "SessionSnapshotCache",
    "SelectedSessionSnapshot",
    "clone_cached_snapshot",
    "selection_is_stale",
    "resolve_selected_snapshot",
    "session_path_key",
]
