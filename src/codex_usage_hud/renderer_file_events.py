"""Event-driven renderer file invalidation and watcher diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import logging
import sys
import threading

from .platforms.file_watcher import FileChangeWatcher, FileWatchSpec
from .refresh_diagnostics import RefreshDiagnosticTrace


_LOGGER = logging.getLogger(__name__)

# Debounce for non-immediate wake reasons (session-map, sessions-root, settings, sse-log).
# 0.2s balances new-session-file detection latency against coalescing frequent
# tree/watch writes. The loop's own wait delay further throttles processing.
DEFAULT_DEBOUNCE_SECONDS = 0.2
DEFAULT_FALLBACK_POLL_SECONDS = 5.0


def session_path_key(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.expanduser().resolve(strict=False)).casefold()
    except OSError:
        return str(path.expanduser().absolute()).casefold()


def skip_recursive_session_tree_watch() -> bool:
    """Avoid high-cost recursive polling for session trees on macOS kqueue."""
    return sys.platform == "darwin"


def renderer_file_watch_specs(
    context: object,
    session_path: Path | None,
) -> list[FileWatchSpec]:
    specs: list[FileWatchSpec] = []
    settings_path = getattr(getattr(context, "settings_store", None), "path", None)
    if settings_path is not None:
        specs.append(FileWatchSpec.file(Path(settings_path), "settings"))
    for attribute in ("session_index_path", "state_db_path"):
        path = getattr(context, attribute, None)
        if path is not None:
            specs.append(FileWatchSpec.file(Path(path), "session-map"))
    sqlite_log_path = getattr(context, "sqlite_log_path", None)
    if sqlite_log_path is not None:
        # SSE/OTel log DB carries response.in_progress and streaming deltas.
        # Watched with the default debounce (not immediate) to coalesce the
        # high-frequency WAL writes during token streaming.
        specs.append(FileWatchSpec.file(Path(sqlite_log_path), "sse-log"))
    sessions_root = getattr(context, "sessions_root", None)
    if sessions_root is not None and not skip_recursive_session_tree_watch():
        root = Path(sessions_root)
        specs.append(FileWatchSpec.tree(root, "sessions-root", suffixes=(".jsonl",)))
        if root.name == "sessions":
            specs.append(
                FileWatchSpec.tree(
                    root.parent / "archived_sessions",
                    "sessions-root",
                    suffixes=(".jsonl",),
                )
            )
    if session_path is not None:
        specs.append(FileWatchSpec.file(Path(session_path), "session"))
    return specs


class RendererFileEventSource:
    """Coalesce filesystem invalidations for one renderer event loop."""

    _OVERFLOW_REASON = "file_watcher.overflow"

    def __init__(
        self,
        context: object,
        wake_event: object,
        *,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        fallback_poll_seconds: float = DEFAULT_FALLBACK_POLL_SECONDS,
        diagnostic_setup: Callable[[object], None] | None = None,
    ) -> None:
        self._context = context
        if diagnostic_setup is not None:
            diagnostic_setup(context)
        self._wake_event = wake_event
        self._debounce_seconds = max(0.0, float(debounce_seconds))
        self._fallback_poll_seconds = max(0.0, float(fallback_poll_seconds))
        self._lock = threading.Lock()
        self._reasons: set[str] = set()
        self._paths: set[Path] = set()
        self._session_path: Path | None = None
        self._timer: threading.Timer | None = None
        self._closed = False
        self._receive_trace = RefreshDiagnosticTrace(_LOGGER, "renderer_file_received")
        self._consume_trace = RefreshDiagnosticTrace(_LOGGER, "renderer_file_consumed")
        self._watcher = FileChangeWatcher(
            self._on_change,
            fallback_poll_seconds=self._fallback_poll_seconds,
        )
        self.update_session_path(None)

    @property
    def event_driven(self) -> bool:
        return self._watcher.event_driven

    def update_session_path(self, session_path: Path | None) -> None:
        if self._same_path(self._session_path, session_path):
            return
        self._session_path = Path(session_path) if session_path is not None else None
        specs = renderer_file_watch_specs(self._context, self._session_path)
        self._watcher.update(specs)
        self._record_degraded_state(specs)

    def take_reasons(self) -> set[str]:
        reasons, _paths = self.take_changes()
        return reasons

    def take_changes(self) -> tuple[set[str], set[Path]]:
        with self._lock:
            reasons = set(self._reasons)
            paths = set(self._paths)
            self._reasons.clear()
            self._paths.clear()
        if reasons:
            self._consume_trace.begin(reasons=sorted(reasons), paths=len(paths))
        return reasons, paths

    def close(self) -> None:
        with self._lock:
            self._closed = True
            timer = self._timer
            self._timer = None
        if timer is not None:
            timer.cancel()
        self._watcher.close()

    def _on_change(self, reasons: set[str], paths: set[Path]) -> None:
        reasons = set(reasons)
        if self._OVERFLOW_REASON in reasons:
            reasons.discard(self._OVERFLOW_REASON)
            self._record_overflow(reasons, paths)
        with self._lock:
            if self._closed:
                return
            self._reasons.update(reasons)
            self._paths.update(paths)
            self._receive_trace.begin(
                reasons=sorted(reasons), paths=len(paths),
                current_session_changed=self._session_path in paths,
            )
            if self._should_wake_immediately(reasons) or self._debounce_seconds <= 0:
                wake_now = True
            elif self._timer is None:
                self._timer = threading.Timer(
                    self._debounce_seconds, self._flush_debounced_change
                )
                self._timer.daemon = True
                self._timer.start()
                wake_now = False
            else:
                wake_now = False
        if wake_now:
            self._publish_runtime_events(reasons, paths)
            self._wake_event.set()

    @staticmethod
    def _should_wake_immediately(reasons: set[str]) -> bool:
        return "session" in reasons

    def _flush_debounced_change(self) -> None:
        with self._lock:
            self._timer = None
            if self._closed or not self._reasons:
                return
            reasons = set(self._reasons)
            paths = set(self._paths)
        self._publish_runtime_events(reasons, paths)
        self._wake_event.set()

    def _publish_runtime_events(self, reasons: set[str], paths: set[Path]) -> None:
        publish = getattr(getattr(self._context, "runtime_events", None), "publish", None)
        if not callable(publish):
            return
        context = {
            "reasons": sorted(reasons),
            "paths": sorted(session_path_key(path) for path in paths),
        }
        session = (
            session_path_key(sorted(paths, key=session_path_key)[0]) if paths else None
        )
        if reasons.intersection({"session", "sessions-root", "sse-log"}):
            publish("session_file_changed", source="file_watcher", session=session, context=context)
        if "settings" in reasons:
            publish("settings_changed", source="file_watcher", context=context)

    def _record_overflow(self, reasons: set[str], paths: set[Path]) -> None:
        registry = getattr(self._context, "runtime_errors", None)
        if registry is not None:
            registry.record(
                source="file_watcher",
                severity="warning",
                code="overflow",
                message="Windows file watcher overflowed; reconciled watched paths.",
                context={
                    "reasons": sorted(reasons),
                    "paths": sorted(session_path_key(path) for path in paths),
                },
            )

    def _record_degraded_state(self, specs: list[FileWatchSpec]) -> None:
        registry = getattr(self._context, "runtime_errors", None)
        if registry is None:
            return
        if specs and not self._watcher.event_driven:
            cause = str(getattr(self._watcher, "polling_cause", "") or "native_unavailable")
            registry.record(
                source="file_watcher",
                severity="warning",
                code="degraded",
                message="Renderer file watcher is using polling fallback.",
                context={
                    "mode": "polling",
                    "cause": cause,
                    "reasons": sorted({spec.reason for spec in specs}),
                    "specs": len(specs),
                    "fallbackPollSeconds": self._fallback_poll_seconds,
                },
            )
            return
        registry.resolve(source="file_watcher", code="degraded")

    @staticmethod
    def _same_path(left: Path | None, right: Path | None) -> bool:
        if left is None or right is None:
            return left is None and right is None
        return session_path_key(left) == session_path_key(right)


__all__ = [
    "DEFAULT_DEBOUNCE_SECONDS",
    "DEFAULT_FALLBACK_POLL_SECONDS",
    "RendererFileEventSource",
    "renderer_file_watch_specs",
    "session_path_key",
    "skip_recursive_session_tree_watch",
]
