"""Structured runtime diagnostics with explicit sinks and lifecycle."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sys
from threading import Lock
from typing import Any, TextIO

from .runtime_paths import crash_diagnostic_path, renderer_diagnostic_path


CRASH_DIAGNOSTICS_ENV = "CODEX_USAGE_HUD_CRASH_DIAGNOSTICS"
RUNTIME_DEBUG_ENV = "CODEX_USAGE_HUD_DEBUG"
_DISABLED_VALUES = {"0", "false", "no", "off"}


def _local_now() -> datetime:
    return datetime.now().astimezone()


class JsonlDiagnosticSink:
    """Append timestamped records to one JSONL diagnostic stream."""

    def __init__(
        self,
        path: Path | Callable[[], Path],
        *,
        now: Callable[[], datetime] = _local_now,
    ) -> None:
        self._path_provider = path if callable(path) else lambda: Path(path)
        self._now = now
        self._lock = Lock()

    @property
    def path(self) -> Path:
        return Path(self._path_provider())

    def append(self, stage: str, **fields: object) -> bool:
        record: dict[str, object] = {
            "time": self._now().isoformat(),
            "stage": stage,
        }
        record.update(
            (key, value)
            for key, value in fields.items()
            if value is not None and value != ""
        )
        path = self.path
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            return False
        return True


class CrashDiagnostics:
    """Own the file handle used by Python's native crash reporter."""

    def __init__(
        self,
        *,
        path_provider: Callable[[], Path] = crash_diagnostic_path,
        platform_provider: Callable[[], str] = lambda: sys.platform,
        environ_provider: Callable[[], Mapping[str, str]] = lambda: os.environ,
        now: Callable[[], datetime] = _local_now,
        faulthandler_loader: Callable[[], object] | None = None,
    ) -> None:
        self._path_provider = path_provider
        self._platform_provider = platform_provider
        self._environ_provider = environ_provider
        self._now = now
        self._faulthandler_loader = faulthandler_loader or self._load_faulthandler
        self._handle: TextIO | None = None
        self._faulthandler: object | None = None

    @staticmethod
    def _load_faulthandler() -> object:
        import faulthandler

        return faulthandler

    def enable(self) -> Path | None:
        setting = self._environ_provider().get(CRASH_DIAGNOSTICS_ENV, "")
        if str(setting).strip().lower() in _DISABLED_VALUES:
            return None
        if not self._platform_provider().startswith("win"):
            return None
        path = Path(self._path_provider())
        if self._handle is not None:
            return path
        handle: TextIO | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a", encoding="utf-8", buffering=1)
            handle.write(
                "\n--- codex-usage-hud crash diagnostics enabled "
                f"pid={os.getpid()} time={self._now().isoformat()} ---\n"
            )
            faulthandler = self._faulthandler_loader()
            enable = getattr(faulthandler, "enable")
            enable(file=handle, all_threads=True)
        except Exception:
            if handle is not None:
                handle.close()
            return None
        self._handle = handle
        self._faulthandler = faulthandler
        return path

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        faulthandler = self._faulthandler
        self._faulthandler = None
        try:
            disable = getattr(faulthandler, "disable", None)
            if callable(disable):
                disable()
        finally:
            handle.close()


_renderer_sink = JsonlDiagnosticSink(renderer_diagnostic_path)
_crash_diagnostics = CrashDiagnostics()
_cli_logging_attached = False


def append_renderer_diagnostic(stage: str, **fields: object) -> None:
    _renderer_sink.append(stage, **fields)


def runtime_error_diagnostic_fields(event: object) -> dict[str, object]:
    to_payload = getattr(event, "to_payload", None)
    payload = to_payload() if callable(to_payload) else {}
    details = payload if isinstance(payload, Mapping) else {}
    return {
        "source": str(getattr(event, "source", "") or ""),
        "severity": str(getattr(event, "severity", "") or ""),
        "code": str(getattr(event, "code", "") or ""),
        "message": str(getattr(event, "message", "") or ""),
        "context": details.get("context"),
        "count": details.get("count"),
        "firstSeenAt": details.get("firstSeenAt"),
        "lastSeenAt": details.get("lastSeenAt"),
    }


def append_runtime_error_diagnostic(
    action: str,
    event: object,
    *,
    append: Callable[..., Any] | None = None,
) -> None:
    writer = append or append_renderer_diagnostic
    writer(f"runtime_error_{action}", **runtime_error_diagnostic_fields(event))


def ensure_runtime_error_diagnostics(
    context: object,
    *,
    callback: Callable[[str, object], None] | None = None,
) -> bool:
    registry = getattr(context, "runtime_errors", None)
    if registry is None or getattr(registry, "diagnostic_callback", None) is not None:
        return False
    registry.diagnostic_callback = callback or append_runtime_error_diagnostic
    return True


def enable_crash_diagnostics() -> Path | None:
    return _crash_diagnostics.enable()


def close_crash_diagnostics() -> None:
    _crash_diagnostics.close()


def attach_cli_logger_to_daemon_log(
    *,
    daemon_logger_name: str = "codex_usage_hud.daemon",
    logger_names: Sequence[str] = (
        "codex_usage_hud.cli",
        "codex_usage_hud.file_watcher",
        "codex_usage_hud.daemon_runtime",
        "codex_usage_hud.renderer_runtime",
    ),
) -> bool:
    """Mirror CLI diagnostics into already-configured daemon handlers once."""
    global _cli_logging_attached
    if _cli_logging_attached:
        return True
    daemon_logger = logging.getLogger(daemon_logger_name)
    handlers = [
        handler
        for handler in daemon_logger.handlers
        if not isinstance(handler, logging.NullHandler)
    ]
    if not handlers:
        return False
    for logger_name in logger_names:
        logger = logging.getLogger(logger_name)
        for handler in handlers:
            if handler not in logger.handlers:
                logger.addHandler(handler)
        logger.setLevel(daemon_logger.level or logging.INFO)
        logger.propagate = False
    _cli_logging_attached = True
    return True


__all__ = [
    "CRASH_DIAGNOSTICS_ENV",
    "RUNTIME_DEBUG_ENV",
    "CrashDiagnostics",
    "JsonlDiagnosticSink",
    "append_renderer_diagnostic",
    "append_runtime_error_diagnostic",
    "attach_cli_logger_to_daemon_log",
    "close_crash_diagnostics",
    "enable_crash_diagnostics",
    "ensure_runtime_error_diagnostics",
    "runtime_error_diagnostic_fields",
]
