"""Event-driven runtime for the local background-usage audit store."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
import logging
from pathlib import Path
from queue import Empty, SimpleQueue
import threading
import time
from typing import Any

from .background_control import BackgroundControlService
from .core.background_usage import BackgroundUsageScanner, BackgroundUsageStore
from .core.runtime_errors import RuntimeErrorRegistry
from .core.runtime_events import RuntimeEventBus
from .platforms.file_watcher import FileChangeWatcher, FileWatchSpec


_LOGGER = logging.getLogger("codex_usage_hud.background_usage")
_LOGGER.addHandler(logging.NullHandler())
BACKGROUND_USAGE_DATABASE_FILENAME = "background-usage.sqlite3"
BACKGROUND_USAGE_WATCHER_FALLBACK_SECONDS = 5.0


class BackgroundUsageRuntime:
    """Coalesce Codex SQLite events into one non-blocking audit worker."""

    def __init__(
        self,
        *,
        logs_path: str | Path,
        state_path: str | Path,
        database_path: str | Path,
        provider: str,
        price_table: Mapping[str, Mapping[str, Any]],
        pricing_versions: Iterable[Any] | None = None,
        app_process_ids: Iterable[int] = (),
        event_bus: RuntimeEventBus | None = None,
        runtime_errors: RuntimeErrorRegistry | None = None,
        watcher_factory: Callable[..., FileChangeWatcher] = FileChangeWatcher,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.logs_path = Path(logs_path)
        self.state_path = Path(state_path)
        self.store = BackgroundUsageStore(database_path)
        self.control = BackgroundControlService(
            Path(database_path).parent,
        )
        self.event_bus = event_bus
        self.runtime_errors = runtime_errors
        self._clock = clock or time.time
        self._scanner = BackgroundUsageScanner(
            logs_path=self.logs_path,
            state_path=self.state_path,
            store=self.store,
            provider=provider,
            price_table=price_table,
            pricing_versions=pricing_versions,
            app_process_ids=app_process_ids,
            now=self._clock,
        )
        self._scanner_lock = threading.Lock()
        self._notification_lock = threading.Lock()
        self._notification_index: dict[str, dict[str, object]] = {}
        self._refresh_notification_index()
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._idle = threading.Event()
        self._source_evidence_queue: SimpleQueue[tuple[str, str]] = SimpleQueue()
        self._provider_proxy: object | None = None
        self._provider_proxy_listener_token = ""
        self._watcher = watcher_factory(
            self._on_source_changed,
            fallback_poll_seconds=BACKGROUND_USAGE_WATCHER_FALLBACK_SECONDS,
        )
        self._worker = threading.Thread(
            target=self._run,
            name="codex-hud-background-usage",
            daemon=True,
        )
        self._started = False
        self._diagnostic_codes: set[str] = set()

    def start(self) -> "BackgroundUsageRuntime":
        if self._started:
            return self
        self._started = True
        self._connect_provider_proxy_source_evidence()
        self._worker.start()
        self._watcher.update(
            [
                FileWatchSpec.file(self.logs_path, "background-usage-log"),
                FileWatchSpec.file(self.state_path, "background-usage-state"),
            ]
        )
        self.request_scan()
        return self

    def request_scan(self) -> None:
        if self._closed.is_set():
            return
        self._idle.clear()
        self._wake.set()

    def reconfigure(
        self,
        *,
        provider: str,
        price_table: Mapping[str, Mapping[str, Any]],
        pricing_versions: Iterable[Any] | None = None,
        app_process_ids: Iterable[int] = (),
    ) -> None:
        with self._scanner_lock:
            self._scanner.reconfigure(
                provider=provider,
                price_table=price_table,
                pricing_versions=pricing_versions,
                app_process_ids=app_process_ids,
            )

    def confirm(self, event_id: object) -> bool:
        changed = self.store.confirm(event_id)
        if changed:
            self._refresh_notification_index()
            self._publish_changed(reason="confirmed", event_id=str(event_id or ""))
        return changed

    def query(self, **filters: object) -> dict[str, object]:
        allowed = {
            "range_key": str(filters.get("range_key") or "today"),
            "feature": str(filters.get("feature") or ""),
            "model": str(filters.get("model") or ""),
            "event_id": str(filters.get("event_id") or ""),
        }
        return self.store.query(**allowed)

    def detail(self, event_id: object) -> dict[str, object] | None:
        return self.store.detail(event_id)

    def policy_query(self, feature_key: object, event_id: object = "") -> dict[str, object]:
        return self.control.query(feature_key, event_id)

    def policy_set(
        self,
        feature_key: object,
        desired_state: object,
        expected_policy_revision: object = None,
        event_id: object = "",
        source: object = "usage_detail",
    ) -> dict[str, object]:
        return self.control.set(
            feature_key,
            desired_state,
            expected_policy_revision,
            event_id,
            source,
        )

    def pending_today(self) -> list[dict[str, object]]:
        return self.store.pending_today()

    def notification_for_session(self, session_id: object) -> dict[str, object]:
        """Return the unread background notification for one canonical session."""
        normalized = str(session_id or "").strip()
        if not normalized:
            return {}
        with self._notification_lock:
            notification = self._notification_index.get(normalized)
            return dict(notification) if notification is not None else {}

    def range_for_event(self, event_id: object) -> str:
        return self.store.range_for_event(event_id, now=self._local_now())

    def wait_until_idle(self, timeout: float = 2.0) -> bool:
        return self._idle.wait(max(0.0, float(timeout)))

    @property
    def watcher_event_driven(self) -> bool:
        return bool(getattr(self._watcher, "event_driven", False))

    @property
    def watcher_polling_cause(self) -> str:
        return str(getattr(self._watcher, "polling_cause", "") or "")

    @property
    def worker_alive(self) -> bool:
        return self._worker.is_alive()

    def close(self, timeout: float = 2.0) -> bool:
        if self._closed.is_set():
            return not self._worker.is_alive()
        self._closed.set()
        self._disconnect_provider_proxy_source_evidence()
        self._wake.set()
        try:
            self._watcher.close()
        finally:
            if self._worker.is_alive():
                self._worker.join(timeout=max(0.0, float(timeout)))
        return not self._worker.is_alive()

    def _on_source_changed(self, reasons: set[str], paths: set[Path]) -> None:
        del paths
        _LOGGER.debug("background_usage_source_changed reasons=%s", sorted(reasons))
        self.request_scan()

    def _run(self) -> None:
        pending_deadline: float | None = None
        while not self._closed.is_set():
            timeout: float | None = None
            if pending_deadline is not None:
                timeout = max(0.0, pending_deadline - float(self._clock()))
            awakened = self._wake.wait(timeout)
            if self._closed.is_set():
                break
            if awakened:
                self._wake.clear()
            elif pending_deadline is None:
                continue
            try:
                self._flush_source_evidence()
                with self._scanner_lock:
                    result = self._scanner.scan()
                pending_deadline = result.pending_deadline
                self._sync_diagnostics(result.diagnostics)
                if result.content_changed:
                    self._refresh_notification_index()
                    self._publish_changed(reason="scan")
            except Exception as exc:
                pending_deadline = None
                self._record_runtime_error(
                    "worker_failed",
                    "Background usage scan failed without affecting the renderer HUD.",
                    context={"errorType": type(exc).__name__},
                )
                _LOGGER.exception("background_usage_scan_failed")
            finally:
                self._idle.set()

    def _connect_provider_proxy_source_evidence(self) -> None:
        try:
            from .provider_proxy import provider_proxy_relay

            self._provider_proxy = provider_proxy_relay
            self._provider_proxy_listener_token = (
                provider_proxy_relay.add_source_evidence_listener(
                    self.submit_source_evidence
                )
            )
        except Exception:
            self._provider_proxy = None
            self._provider_proxy_listener_token = ""

    def _disconnect_provider_proxy_source_evidence(self) -> None:
        relay = self._provider_proxy
        token = self._provider_proxy_listener_token
        self._provider_proxy = None
        self._provider_proxy_listener_token = ""
        if relay is None or not token:
            return
        remove_listener = getattr(relay, "remove_source_evidence_listener", None)
        if callable(remove_listener):
            remove_listener(token)

    def submit_source_evidence(self, thread_id: str, feature_key: str) -> None:
        if self._closed.is_set():
            return
        self._source_evidence_queue.put((str(thread_id or ""), str(feature_key or "")))
        self.request_scan()

    def _flush_source_evidence(self) -> None:
        while True:
            try:
                thread_id, feature_key = self._source_evidence_queue.get_nowait()
            except Empty:
                return
            self.store.record_source_evidence(
                thread_id,
                feature_key,
                observed_at=int(self._clock()),
            )

    def _local_now(self) -> datetime:
        return datetime.fromtimestamp(float(self._clock())).astimezone()

    def _refresh_notification_index(self) -> None:
        index = self.store.notification_index(now=self._local_now())
        with self._notification_lock:
            self._notification_index = {
                str(session_id): dict(notification)
                for session_id, notification in index.items()
            }

    def _publish_changed(self, *, reason: str, event_id: str = "") -> None:
        publish = getattr(self.event_bus, "publish", None)
        if not callable(publish):
            return
        publish(
            "background_usage_changed",
            source="background_usage",
            context={
                "reason": str(reason or ""),
                "eventId": str(event_id or ""),
                "revision": self.store.revision(),
            },
        )

    def _sync_diagnostics(self, diagnostics: tuple[str, ...]) -> None:
        next_codes = {str(value).split(":", 1)[0] for value in diagnostics if value}
        for code in sorted(next_codes):
            self._record_runtime_error(
                code,
                "Background usage source is temporarily unavailable.",
                severity="warning",
            )
        registry = self.runtime_errors
        if registry is not None:
            for code in sorted(self._diagnostic_codes - next_codes):
                registry.resolve(source="background_usage", code=code)
        self._diagnostic_codes = next_codes

    def _record_runtime_error(
        self,
        code: str,
        message: str,
        *,
        severity: str = "error",
        context: Mapping[str, Any] | None = None,
    ) -> None:
        registry = self.runtime_errors
        if registry is None:
            return
        if registry.event_bus is None and self.event_bus is not None:
            registry.event_bus = self.event_bus
        registry.record(
            source="background_usage",
            code=code,
            message=message,
            severity=severity,
            context=dict(context or {}),
        )


__all__ = [
    "BACKGROUND_USAGE_DATABASE_FILENAME",
    "BACKGROUND_USAGE_WATCHER_FALLBACK_SECONDS",
    "BackgroundUsageRuntime",
]
