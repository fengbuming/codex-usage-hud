from __future__ import annotations

from datetime import datetime, timezone
import json
import inspect
import logging
from pathlib import Path
from types import SimpleNamespace

from codex_usage_hud import runtime_diagnostics
from codex_usage_hud.refresh_diagnostics import RefreshDiagnosticTrace

from codex_usage_hud.runtime_diagnostics import (
    CRASH_DIAGNOSTICS_ENV,
    CrashDiagnostics,
    JsonlDiagnosticSink,
    append_runtime_error_diagnostic,
    ensure_runtime_error_diagnostics,
)


FIXED_NOW = datetime(2026, 7, 31, 12, 30, tzinfo=timezone.utc)


def test_refresh_loggers_write_to_daemon_file(tmp_path, monkeypatch) -> None:
    """The default module loggers must actually reach a persistent handler."""
    from codex_usage_hud import renderer_client, renderer_event_loop, session_activity

    configure = runtime_diagnostics.attach_cli_logger_to_daemon_log
    names = inspect.signature(configure).parameters["logger_names"].default
    for logger in (renderer_client._LOGGER, renderer_event_loop._LOGGER, session_activity._LOGGER):
        assert logger.name in names
    path = tmp_path / "daemon.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    daemon_logger = logging.getLogger("test.hud.daemon")
    monkeypatch.setattr(daemon_logger, "handlers", [handler])
    monkeypatch.setattr(daemon_logger, "level", logging.INFO)
    monkeypatch.setattr(runtime_diagnostics, "_cli_logging_attached", False)
    for name in names:
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, "handlers", list(logger.handlers))
        monkeypatch.setattr(logger, "level", logger.level)
        monkeypatch.setattr(logger, "propagate", logger.propagate)
    try:
        assert configure(daemon_logger_name=daemon_logger.name)
        assert configure(daemon_logger_name=daemon_logger.name)
        for name in names:
            logging.getLogger(name).info("persistent_refresh_marker %s", name)
        handler.flush()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == len(names)
        assert all(any(name in line for line in lines) for name in names)
    finally:
        handler.close()


def test_refresh_trace_throttles_content_but_records_state_transitions(caplog) -> None:
    now = [0.0]
    logger = logging.getLogger("test.hud.trace")
    caplog.set_level(logging.INFO, logger=logger.name)
    trace = RefreshDiagnosticTrace(logger, "refresh", monotonic=lambda: now[0])
    trace.begin(key="running", session_id="session-1")
    trace.phase("written", revision=1)
    for _ in range(10):
        now[0] += 1.0
        trace.begin(key="running")
        trace.phase("written")
    assert len(caplog.records) == 2
    trace.begin(key="completed")
    assert len(caplog.records) == 3
    now[0] = 40.0
    trace.begin(key="completed")
    assert len(caplog.records) == 4
    assert "session-1" in caplog.records[0].message


def test_jsonl_sink_writes_structured_record_and_omits_empty_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "renderer.log"
    sink = JsonlDiagnosticSink(path, now=lambda: FIXED_NOW)

    assert sink.append("initial_connect_failed", reason="timeout", empty="", none=None)

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record == {
        "time": "2026-07-31T12:30:00+00:00",
        "stage": "initial_connect_failed",
        "reason": "timeout",
    }


def test_runtime_error_adapter_preserves_existing_payload_fields() -> None:
    event = SimpleNamespace(
        source="cdp",
        severity="error",
        code="cdp.update_failed",
        message="timed out",
        to_payload=lambda: {
            "context": {"attempt": 2},
            "count": 3,
            "firstSeenAt": "first",
            "lastSeenAt": "last",
        },
    )
    records: list[tuple[str, dict[str, object]]] = []

    append_runtime_error_diagnostic(
        "recorded",
        event,
        append=lambda stage, **fields: records.append((stage, fields)),
    )

    assert records == [
        (
            "runtime_error_recorded",
            {
                "source": "cdp",
                "severity": "error",
                "code": "cdp.update_failed",
                "message": "timed out",
                "context": {"attempt": 2},
                "count": 3,
                "firstSeenAt": "first",
                "lastSeenAt": "last",
            },
        )
    ]


def test_runtime_error_callback_is_installed_once() -> None:
    registry = SimpleNamespace(diagnostic_callback=None)
    context = SimpleNamespace(runtime_errors=registry)

    def callback(_action: str, _event: object) -> None:
        return None

    assert ensure_runtime_error_diagnostics(context, callback=callback)
    assert registry.diagnostic_callback is callback
    assert not ensure_runtime_error_diagnostics(context, callback=lambda *_args: None)
    assert registry.diagnostic_callback is callback


class _FakeFaultHandler:
    def __init__(self) -> None:
        self.enable_calls: list[dict[str, object]] = []
        self.disable_calls = 0

    def enable(self, **kwargs: object) -> None:
        self.enable_calls.append(dict(kwargs))

    def disable(self) -> None:
        self.disable_calls += 1


def test_crash_diagnostics_owns_and_closes_faulthandler_file(tmp_path: Path) -> None:
    path = tmp_path / "crash.log"
    fake = _FakeFaultHandler()
    manager = CrashDiagnostics(
        path_provider=lambda: path,
        platform_provider=lambda: "win32",
        environ_provider=lambda: {},
        now=lambda: FIXED_NOW,
        faulthandler_loader=lambda: fake,
    )

    assert manager.enable() == path
    assert manager.enable() == path
    assert len(fake.enable_calls) == 1
    assert fake.enable_calls[0]["all_threads"] is True
    assert "pid=" in path.read_text(encoding="utf-8")

    manager.close()

    assert fake.disable_calls == 1
    assert fake.enable_calls[0]["file"].closed


def test_crash_diagnostics_respects_disable_flag(tmp_path: Path) -> None:
    path = tmp_path / "crash.log"
    manager = CrashDiagnostics(
        path_provider=lambda: path,
        platform_provider=lambda: "win32",
        environ_provider=lambda: {CRASH_DIAGNOSTICS_ENV: "off"},
    )

    assert manager.enable() is None
    assert not path.exists()
