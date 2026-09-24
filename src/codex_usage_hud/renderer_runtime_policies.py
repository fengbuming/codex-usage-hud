"""Renderer-specific refresh and invalidation policies.

These helpers decide whether work is needed and build cheap change
signatures. They do not own runtime resources or perform CDP payload pushes.
The small context/file adapters here only read already-owned state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING

from . import runtime_policies, session_snapshots
from .config import normalize_display_mode

if TYPE_CHECKING:
    from .core import ParsedSession
    from .renderer_file_events import RendererFileEventSource
    from .runtime_context import RuntimeContext


RENDERER_IDLE_POLL_MS = 1500
RENDERER_ACTIVE_WORK_RESCAN_SECONDS = 5.0
RENDERER_UPDATE_FAILURE_LIMIT = 6
AUTO_RENDERER_TIMEOUT_FAILURE_LIMIT = 3
_REMOTE_DEBUGGING_PORT_PATTERN = re.compile(
    r"(?:^|\s)--remote-debugging-port(?:=|\s+)(\d{1,5})(?=\s|$)"
)


def _renderer_update_failure_limit(display_mode: str, last_error: str) -> int:
    """Return how many consecutive renderer update failures we tolerate."""
    if (
        normalize_display_mode(display_mode) == "auto"
        and "timed out" in str(last_error or "").lower()
    ):
        return AUTO_RENDERER_TIMEOUT_FAILURE_LIMIT
    return RENDERER_UPDATE_FAILURE_LIMIT


def _renderer_refresh_delay_seconds(
    context: "RuntimeContext",
    snapshot: "ParsedSession",
    elapsed_seconds: float,
    *,
    force_fast: bool = False,
) -> float:
    """Return the next renderer loop delay with slower idle refreshes."""
    request_status = str(getattr(snapshot.request, "status", "") or "")
    return runtime_policies.refresh_delay_seconds(
        poll_ms=context.poll_ms,
        request_status=request_status,
        elapsed_seconds=elapsed_seconds,
        idle_poll_ms=RENDERER_IDLE_POLL_MS,
        force_fast=force_fast,
    )


def _renderer_initial_failure_can_be_fixed_by_restart(last_error: str) -> bool:
    """Return whether an initial CDP failure likely means Codex lacks debug mode."""
    text = str(last_error or "").lower()
    if not text:
        return False
    if "timed out" in text or "timeout" in text:
        return False
    if "10013" in text or "access" in text or "permission" in text:
        return False
    return any(
        marker in text
        for marker in (
            "connection refused",
            "actively refused",
            "target has no websocket",
            "no page target",
            "no websocket",
            "connection reset",
            "winerror 10061",
        )
    )


def _renderer_initial_failure_should_recover_cdp_port(last_error: str) -> bool:
    """Return whether a fresh CDP port is a better first recovery than prompting."""
    text = str(last_error or "").lower()
    if not text:
        return False
    return any(
        marker in text
        for marker in (
            "timed out",
            "timeout",
            "connection refused",
            "actively refused",
            "connection reset",
            "winerror 10061",
            "winerror 10013",
            "访问权限不允许",
        )
    )


def _valid_renderer_cdp_port(value: object) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def _remote_debugging_ports_from_command_line(
    command_line: object,
) -> tuple[int, ...]:
    """Extract bounded Chromium remote-debugging ports from one command line."""
    text = str(command_line or "")
    ports: list[int] = []
    for match in _REMOTE_DEBUGGING_PORT_PATTERN.finditer(text):
        port = _valid_renderer_cdp_port(match.group(1))
        if port is not None and port not in ports:
            ports.append(port)
    return tuple(ports)


def _json_signature(value: Mapping[str, object] | None) -> str:
    """Build a stable, cheap signature for optional mapping state."""
    if not value:
        return ""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        return str(sorted(value.items()))


def _path_stat_signature(path: Path | None) -> tuple[str, int, int]:
    """Return path identity and stat values used by refresh invalidation."""
    if path is None:
        return "", 0, 0
    key = session_snapshots.session_path_key(path)
    try:
        stat = path.stat()
    except OSError:
        return key, 0, 0
    mtime_ns = int(
        getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
    )
    return key, mtime_ns, int(stat.st_size)


def _renderer_runtime_signature(
    context: "RuntimeContext",
    *,
    update_state: Mapping[str, object] | None = None,
    settings_command_status: Mapping[str, object] | None = None,
) -> tuple[object, ...]:
    """Build the event-driven invalidation key for one renderer refresh."""
    try:
        session_path, selection_source = context.session_resolver.resolve()
    except Exception as exc:
        session_path = None
        selection_source = f"resolve-error:{type(exc).__name__}"
    try:
        settings_mtime = context.settings_store.mtime()
    except Exception:
        settings_mtime = None
    try:
        day_start, week_start = runtime_policies.budget_windows(context.user_config)
        day_key = day_start.isoformat()
        week_key = week_start.isoformat()
    except Exception:
        day_key = ""
        week_key = ""
    return (
        _path_stat_signature(session_path),
        str(selection_source or ""),
        settings_mtime,
        day_key,
        week_key,
        _json_signature(update_state),
        _json_signature(settings_command_status),
    )


def _renderer_budget_window_keys(context: "RuntimeContext") -> tuple[str, str]:
    """Return normalized (day, week) budget-window keys for change detection."""
    try:
        day_start, week_start = runtime_policies.budget_windows(context.user_config)
        return day_start.isoformat(), week_start.isoformat()
    except Exception:
        return "", ""


def _renderer_budget_signature(context: "RuntimeContext") -> tuple[object, ...]:
    day_key, week_key = _renderer_budget_window_keys(context)
    estimator = getattr(getattr(context, "parser", None), "cost_estimator", None)
    return (
        session_snapshots.session_path_key(getattr(context, "sessions_root", None)),
        day_key,
        week_key,
        str(getattr(estimator, "pricing_fingerprint", "") or ""),
    )


def _paths_only_current_session(paths: set[Path], session_path: Path | None) -> bool:
    if not paths or session_path is None:
        return False
    current_key = session_snapshots.session_path_key(session_path)
    if not current_key:
        return False
    return all(session_snapshots.session_path_key(path) == current_key for path in paths)


def _renderer_budget_refresh_paths(
    file_change_paths: Iterable[Path],
) -> tuple[Path, ...]:
    paths = tuple(dict.fromkeys(Path(path) for path in file_change_paths))
    if not paths:
        return ()
    if any(path.suffix.lower() != ".jsonl" for path in paths):
        return ()
    return tuple(sorted(paths, key=session_snapshots.session_path_key))


def _renderer_should_refresh_budget_aggregate(
    *,
    latest_snapshot: "ParsedSession | None",
    latest_budget_signature: tuple[object, ...] | None,
    budget_signature: tuple[object, ...],
    file_change_reasons: set[str],
    file_change_paths: set[Path],
) -> bool:
    return runtime_policies.should_refresh_budget_aggregate(
        has_snapshot=latest_snapshot is not None,
        signature_changed=budget_signature != latest_budget_signature,
        file_change_reasons=file_change_reasons,
        has_incremental_jsonl_paths=bool(
            _renderer_budget_refresh_paths(file_change_paths)
        ),
    )


def _renderer_should_refresh_active_work_items(
    *,
    latest_snapshot: "ParsedSession | None",
    latest_active_work_refresh_at: float,
    now_monotonic: float,
    active_work_refresh_pending: bool,
    file_change_reasons: set[str],
    file_change_paths: set[Path],
) -> bool:
    return runtime_policies.should_refresh_active_work_items(
        has_snapshot=latest_snapshot is not None,
        latest_refresh_at=latest_active_work_refresh_at,
        now_monotonic=now_monotonic,
        refresh_pending=active_work_refresh_pending,
        file_change_reasons=file_change_reasons,
        file_change_paths=file_change_paths,
        rescan_seconds=RENDERER_ACTIVE_WORK_RESCAN_SECONDS,
    )


def _renderer_snapshot_selection_is_stale(
    snapshot: "ParsedSession",
    tracker: object | None,
) -> bool:
    return session_snapshots.selection_is_stale(snapshot, tracker)


def _renderer_active_session_observation_should_refresh(
    *,
    changed: bool,
    selection_seq: object,
    tracker: object | None,
) -> bool:
    """Retry the current selection until the renderer applies its sequence."""
    return runtime_policies.active_session_observation_should_refresh(
        changed=changed,
        selection_seq=selection_seq,
        current_seq=getattr(tracker, "selection_seq", 0),
    )


def _renderer_should_use_visible_first_active_session(
    *,
    active_session_requested: bool,
    latest_snapshot: "ParsedSession | None",
    has_command: bool,
    has_settings_command_status: bool,
    update_phase: str,
) -> bool:
    """Keep coalesced filesystem writes off the selected-session click path."""
    return runtime_policies.should_use_visible_first_active_session(
        active_session_requested=active_session_requested,
        has_snapshot=latest_snapshot is not None,
        has_command=has_command,
        has_settings_command_status=has_settings_command_status,
        update_phase=update_phase,
    )


def _renderer_deferred_active_work_refresh_due(
    *,
    pending: bool,
    not_before: float,
    now_monotonic: float,
) -> bool:
    return bool(pending and now_monotonic >= not_before)


def _renderer_event_idle_wait_enabled(
    file_events: "RendererFileEventSource | None",
    snapshot: "ParsedSession",
    update_state: Mapping[str, object],
    delay: float,
    *,
    force_fast: bool,
) -> bool:
    del snapshot, delay
    if file_events is None or not file_events.event_driven or force_fast:
        return False
    update_phase = str(update_state.get("phase") or "")
    return update_phase not in {"checking", "downloading"}


__all__ = [
    "AUTO_RENDERER_TIMEOUT_FAILURE_LIMIT",
    "RENDERER_ACTIVE_WORK_RESCAN_SECONDS",
    "RENDERER_IDLE_POLL_MS",
    "RENDERER_UPDATE_FAILURE_LIMIT",
    "_json_signature",
    "_path_stat_signature",
    "_paths_only_current_session",
    "_renderer_active_session_observation_should_refresh",
    "_renderer_budget_refresh_paths",
    "_renderer_budget_signature",
    "_renderer_budget_window_keys",
    "_renderer_deferred_active_work_refresh_due",
    "_renderer_event_idle_wait_enabled",
    "_renderer_initial_failure_can_be_fixed_by_restart",
    "_renderer_initial_failure_should_recover_cdp_port",
    "_renderer_refresh_delay_seconds",
    "_renderer_runtime_signature",
    "_renderer_should_refresh_active_work_items",
    "_renderer_should_refresh_budget_aggregate",
    "_renderer_should_use_visible_first_active_session",
    "_renderer_snapshot_selection_is_stale",
    "_renderer_update_failure_limit",
    "_remote_debugging_ports_from_command_line",
    "_valid_renderer_cdp_port",
]
