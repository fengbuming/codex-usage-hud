"""Desktop work-overlay helper supervision and state publication."""

from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from threading import Event
from typing import Any, Protocol

from . import (
    overlay_command_channel,
    overlay_ipc,
    overlay_projection,
    overlay_state,
    overlay_supervision,
    overlay_transition_audit,
)
from .config import (
    DEFAULT_WORK_OVERLAY_MAX_ITEMS,
    DEFAULT_WORK_OVERLAY_SIDE,
    normalize_work_overlay_max_items,
    normalize_work_overlay_side,
    write_json_object,
)
from .core import WorkStatusItem
from .platforms.codex_theme import CodexThemeProbe
from .platforms.file_watcher import FileChangeWatcher, FileWatchSpec
from .process_environment import internal_process_environment

WORK_OVERLAY_COMMAND_FALLBACK_POLL_SECONDS = 5.0
WORK_OVERLAY_SWITCH_COMPLETED_HOLD_SECONDS = 1.4
WORK_OVERLAY_KEEPALIVE_SECONDS = 15.0
WORK_OVERLAY_RESTART_BACKOFF_SECONDS = 60.0
WORK_OVERLAY_HELPER_HEARTBEAT_TIMEOUT_SECONDS = 35.0
WORK_OVERLAY_HELPER_MAX_USER_OBJECTS = 2_000
WORK_OVERLAY_HELPER_READY_TIMEOUT_SECONDS = 5.0
WORK_OVERLAY_HELPER_READY_POLL_SECONDS = 0.02
WORK_OVERLAY_RESTART_ACTION = "restartCodex"
WORK_OVERLAY_SYSTEM_ACTION_READY = "systemActionReady"
WORK_OVERLAY_RESTART_ACTION_ID = "restart-codex-for-renderer"
WORK_OVERLAY_SYSTEM_NOTICE_ID = "renderer-recovery-notice"
WORK_OVERLAY_SYSTEM_ACTION_READY_TIMEOUT_SECONDS = 2.0
# A helper that dies faster than this never rendered a single frame. Overlay
# entry points are driven by the renderer tick (and by the rest-reminder
# payload, which is republished on every tick), so without a breaker a helper
# that cannot stay up gets respawned at tick speed -- measured at ~150
# processes/minute, which starves the Codex renderer into a blank window.
WORK_OVERLAY_HELPER_RAPID_EXIT_SECONDS = 5.0
WORK_OVERLAY_HELPER_RAPID_EXIT_LIMIT = 3
WORK_OVERLAY_HELPER_RAPID_EXIT_WINDOW_SECONDS = 30.0
WORK_OVERLAY_HELPER_BREAKER_BACKOFF_SECONDS = 120.0

_LOGGER = logging.getLogger("codex_usage_hud.desktop_overlay")
_SUBPROCESS_POPEN_TYPE = subprocess.Popen


def _append_renderer_diagnostic(_stage: str, **_fields: object) -> None:
    """Default no-op; production composition injects the diagnostic owner."""


class OverlayClock(Protocol):
    def monotonic(self) -> float: ...
    def time(self) -> float: ...


class _SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def time(self) -> float:
        return time.time()


def _default_runtime_dir() -> Path:
    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(
            os.environ.get("XDG_RUNTIME_DIR")
            or os.environ.get("XDG_STATE_HOME")
            or Path.home() / ".local" / "state"
        )
    return base / "codex-usage-hud"


def _pyside6_runtime_available() -> bool:
    try:
        importlib.invalidate_caches()
        return importlib.util.find_spec("PySide6") is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _work_overlay_command(state_path: Path) -> list[str]:
    state_arg = str(state_path)
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable)), "--work-overlay-helper", "--work-overlay-state-file", state_arg]
    helper_python = Path(sys.executable)
    if sys.platform.startswith("win") and helper_python.name.lower() == "python.exe":
        candidate = helper_python.with_name("pythonw.exe")
        if candidate.exists():
            helper_python = candidate
    return [str(helper_python), "-m", "codex_usage_hud", "--work-overlay-helper", "--work-overlay-state-file", state_arg]


def _windows_user_object_count(process: subprocess.Popen[Any]) -> int | None:
    if os.name != "nt":
        return None
    handle = getattr(process, "_handle", None)
    if not handle:
        return None
    try:
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_gui_resources = user32.GetGuiResources
        get_gui_resources.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        get_gui_resources.restype = ctypes.c_uint
        return int(get_gui_resources(ctypes.c_void_p(handle), 1))
    except Exception:
        return None

class DesktopWorkOverlay:
    """Optional PySide6 primary-screen desktop overlay for work bubbles."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        item_limit: int = DEFAULT_WORK_OVERLAY_MAX_ITEMS,
        side: str = DEFAULT_WORK_OVERLAY_SIDE,
        clock: OverlayClock | None = None,
        runtime_dir: Callable[[], Path] | None = None,
        diagnostic_sink: Callable[..., None] | None = None,
        runtime_available: Callable[[], bool] | None = None,
        state_path: Path | None = None,
    ) -> None:
        self._clock = clock or _SystemClock()
        self._runtime_dir = runtime_dir or _default_runtime_dir
        self._diagnostic_sink = diagnostic_sink or (
            lambda stage, **fields: _append_renderer_diagnostic(stage, **fields)
        )
        self._runtime_available_probe = runtime_available or _pyside6_runtime_available
        self._producer_instance_id = str(uuid.uuid4())
        self._state_revision = 0
        self.item_limit = normalize_work_overlay_max_items(item_limit)
        self.side = normalize_work_overlay_side(side)
        self.enabled = bool(enabled) and self.item_limit > 0
        self._state_path = state_path or (
            self._runtime_dir() / f"work-overlay-{os.getpid()}-{int(self._clock.time() * 1000)}.json"
        )
        self._command_path = overlay_ipc.command_path(self._state_path)
        self._transition_audit_path = overlay_ipc.transition_audit_path(self._runtime_dir())
        self._command_reader = overlay_command_channel.OverlayCommandReader()
        self._deferred_commands: deque[dict[str, object]] = deque()
        self._process: subprocess.Popen[str] | None = None
        self._closed = False
        self._available: bool | None = None
        self._unavailable_reason = ""
        self._unavailable_reported = False
        self._restart_blocked_until = 0.0
        self._last_helper_exit_code: int | None = None
        self._helper_started_at = 0.0
        self._last_helper_heartbeat_at = 0.0
        # Rapid-exit circuit breaker: monotonic start stamp, recent rapid exits,
        # and the deadline before which no restart is attempted at all.
        self._helper_started_monotonic = 0.0
        self._helper_rapid_exits: deque[float] = deque()
        self._helper_breaker_until = 0.0
        self._last_payload_items: list[dict[str, object]] | None = None
        self._last_theme_payload: dict[str, object] = {}
        self._system_action: dict[str, object] | None = None
        self._system_notice: dict[str, object] | None = None
        self._rest_reminder: dict[str, object] = {}
        self._keepalive_stop = Event()
        self._keepalive_thread: threading.Thread | None = None
        self._system_action_unavailable_reason = ""
        self._last_state_signature: str | None = None
        self._last_state_write_at = 0.0
        self._switch_completed_command: dict[str, object] | None = None
        self._switch_completed_until = 0.0
        self._theme_probe = CodexThemeProbe(
            timeout_seconds=0.08,
            # The renderer binding pushes theme changes.  This cache is only
            # a fallback for the desktop work overlay, so do not re-run the
            # expensive DOM probe on every session refresh.
            cache_seconds=60.0,
            failure_cooldown_seconds=5.0,
        )

    def configure(
        self,
        *,
        enabled: bool | None = None,
        item_limit: int | None = None,
        side: str | None = None,
    ) -> None:
        next_enabled = self.enabled if enabled is None else bool(enabled)
        if item_limit is not None:
            self.item_limit = normalize_work_overlay_max_items(item_limit)
        if side is not None:
            self.side = normalize_work_overlay_side(side)
        self.enabled = next_enabled and self.item_limit > 0
        if (
            not self.enabled
            and self._system_action is None
            and self._system_notice is None
            and not self._rest_reminder
            and not self._closed
        ):
            self._stop_runtime(permanent=False)

    def update(self, items: Sequence[WorkStatusItem]) -> None:
        if self._closed:
            return
        if (
            not self.enabled
            and self._system_action is None
            and self._system_notice is None
            and not self._rest_reminder
        ):
            self._stop_runtime(permanent=False)
            return
        if not self.enabled:
            return
        if not self._runtime_available():
            self._stop_runtime(permanent=False)
            self._report_unavailable_once(self._unavailable_reason)
            return
        self._ensure_helper_healthy(self._clock.monotonic())
        # Active items are already filtered for terminal and stale work before
        # reaching the overlay. Retaining them on the first update keeps a HUD
        # restart from hiding a still-running CLI conversation until its next
        # filesystem event.
        payload_items = [
            overlay_projection.work_item_to_overlay_dict(item) for item in items
        ]
        payload_items = self._apply_switch_completed_override(payload_items)
        theme_payload = self._theme_payload()
        if (
            not payload_items
            and self._system_action is None
            and self._system_notice is None
            and not self._rest_reminder
            and self._last_payload_items is None
        ):
            self._last_payload_items = []
            self._last_theme_payload = dict(theme_payload)
            return
        next_signature = self._state_signature(
            payload_items,
            theme=theme_payload,
            close=False,
        )
        if next_signature != self._last_state_signature:
            self._write_state(payload_items, theme=theme_payload, close=False)
        self._last_payload_items = [dict(item) for item in payload_items]
        self._last_theme_payload = dict(theme_payload)
        self._maybe_start_helper(self._clock.monotonic())
        self._ensure_keepalive_worker_if_runtime_started()

    def update_rest_reminder(self, payload: Mapping[str, object] | None) -> bool:
        """Publish one non-session rest bubble, independently of session limits.

        The renderer republishes the same payload on every tick, so the restart
        gate (``_maybe_start_helper``) is the only thing standing between a
        helper that cannot stay up and an unbounded respawn storm.
        """
        if self._closed:
            return False
        next_payload = (
            dict(payload)
            if isinstance(payload, Mapping) and bool(payload.get("bubbleVisible"))
            else {}
        )
        now = self._clock.monotonic()
        if next_payload == self._rest_reminder:
            if not next_payload:
                return False
            if not self._runtime_available():
                self._report_unavailable_once(self._unavailable_reason)
                return False
            self._ensure_helper_healthy(now)
            process = self._process
            if process is not None and process.poll() is not None:
                self._last_helper_exit_code = int(process.returncode or 0)
                self._note_helper_exit(self._last_helper_exit_code, now)
                self._process = None
            self._maybe_start_helper(now)
            self._ensure_keepalive_worker_if_runtime_started()
            return self._process is not None
        self._rest_reminder = next_payload
        if (
            not self._rest_reminder
            and not self.enabled
            and self._system_action is None
            and self._system_notice is None
        ):
            self._stop_runtime(permanent=False)
            return False
        if not self._runtime_available():
            self._report_unavailable_once(self._unavailable_reason)
            return False
        self._ensure_helper_healthy(now)
        payload_items = list(self._last_payload_items or [])
        theme_payload = self._last_theme_payload or self._theme_payload()
        self._write_state(payload_items, theme=theme_payload, close=False)
        self._last_payload_items = [dict(item) for item in payload_items]
        self._last_theme_payload = dict(theme_payload)
        process = self._process
        if process is not None and process.poll() is not None:
            self._last_helper_exit_code = int(process.returncode or 0)
            self._note_helper_exit(self._last_helper_exit_code, now)
            self._process = None
        self._maybe_start_helper(now)
        self._ensure_keepalive_worker_if_runtime_started()
        return self._process is not None

    def show_system_notice(self, *, title: str, message: str) -> bool:
        """Publish a non-interactive notice without replacing session bubbles."""
        if self._closed:
            return False
        self._system_action = None
        self._system_notice = {
            "id": WORK_OVERLAY_SYSTEM_NOTICE_ID,
            "title": str(title or "Codex HUD"),
            "message": str(message or ""),
            "status": "warning",
            "persistent": True,
        }
        if not self._runtime_available():
            self._report_unavailable_once(self._unavailable_reason)
            return False
        now = self._clock.monotonic()
        self._ensure_helper_healthy(now)
        payload_items = list(self._last_payload_items or [])
        theme_payload = self._theme_payload()
        self._write_state(payload_items, theme=theme_payload, close=False)
        self._last_payload_items = [dict(item) for item in payload_items]
        self._last_theme_payload = dict(theme_payload)
        process = self._process
        if process is not None and process.poll() is not None:
            self._last_helper_exit_code = int(process.returncode or 0)
            self._note_helper_exit(self._last_helper_exit_code, now)
            self._process = None
        started_now = self._process is None and self._maybe_start_helper(now)
        if self._process is None:
            return False
        if started_now and not self._wait_for_helper_ready():
            self._system_action_unavailable_reason = (
                "PySide6 desktop overlay helper did not become ready"
            )
            self._stop_runtime(permanent=False)
            return False
        self._ensure_keepalive_worker_if_runtime_started()
        return True

    def clear_system_notice(self) -> bool:
        """Remove the recovery notice while retaining the current session items."""
        if self._closed or self._system_notice is None:
            return False
        self._system_notice = None
        payload_items = list(self._last_payload_items or [])
        if (
            not payload_items
            and not self.enabled
            and self._system_action is None
            and not self._rest_reminder
        ):
            self._stop_runtime(permanent=False)
            return True
        if not self._runtime_available():
            self._report_unavailable_once(self._unavailable_reason)
            return False
        theme_payload = self._last_theme_payload or self._theme_payload()
        self._write_state(payload_items, theme=theme_payload, close=False)
        self._last_payload_items = [dict(item) for item in payload_items]
        self._last_theme_payload = dict(theme_payload)
        self._maybe_start_helper(self._clock.monotonic())
        self._ensure_keepalive_worker_if_runtime_started()
        return self._process is not None

    def clear_system_action(self) -> bool:
        """Remove a restart action after the replacement renderer is attached."""
        if self._closed or self._system_action is None:
            return False
        self._system_action = None
        payload_items = list(self._last_payload_items or [])
        if (
            not payload_items
            and not self.enabled
            and self._system_notice is None
            and not self._rest_reminder
        ):
            self._stop_runtime(permanent=False)
            return True
        if not self._runtime_available():
            self._report_unavailable_once(self._unavailable_reason)
            return False
        theme_payload = self._last_theme_payload or self._theme_payload()
        self._write_state(payload_items, theme=theme_payload, close=False)
        self._last_payload_items = [dict(item) for item in payload_items]
        self._last_theme_payload = dict(theme_payload)
        self._maybe_start_helper(self._clock.monotonic())
        self._ensure_keepalive_worker_if_runtime_started()
        return self._process is not None

    def offer_codex_restart(self, *, title: str, message: str) -> bool:
        """Show a persistent system action independently of session-bubble settings."""
        if self._closed:
            return False
        self._system_action_unavailable_reason = ""
        notice_id = str((self._system_notice or {}).get("id") or "").strip()
        self._system_notice = None
        self._system_action = {
            "id": notice_id or WORK_OVERLAY_RESTART_ACTION_ID,
            "action": WORK_OVERLAY_RESTART_ACTION,
            "title": str(title or "需要重启 Codex"),
            "message": str(message or ""),
            "label": "重启 Codex",
            "persistent": True,
        }
        if not self._runtime_available():
            self._system_action_unavailable_reason = self._unavailable_reason
            self._report_unavailable_once(self._unavailable_reason)
            self._system_action = None
            return False
        now = self._clock.monotonic()
        self._ensure_helper_healthy(now)
        payload_items = list(self._last_payload_items or [])
        theme_payload = self._theme_payload()
        self._write_state(payload_items, theme=theme_payload, close=False)
        self._last_payload_items = [dict(item) for item in payload_items]
        self._last_theme_payload = dict(theme_payload)
        process = self._process
        if process is not None and process.poll() is not None:
            self._last_helper_exit_code = int(process.returncode or 0)
            self._note_helper_exit(self._last_helper_exit_code, now)
            self._process = None
        self._maybe_start_helper(now)
        if self._process is None:
            self._system_action_unavailable_reason = (
                self._unavailable_reason or "unable to start PySide6 desktop overlay helper"
            )
            self._system_action = None
            return False
        self._ensure_keepalive_worker_if_runtime_started()
        ready = self._wait_for_system_action_command(
            {WORK_OVERLAY_SYSTEM_ACTION_READY},
            timeout_seconds=WORK_OVERLAY_SYSTEM_ACTION_READY_TIMEOUT_SECONDS,
        )
        if ready is None:
            if not self._system_action_unavailable_reason:
                self._system_action_unavailable_reason = (
                    "PySide6 desktop overlay helper did not acknowledge the restart action"
                )
            self._stop_runtime(permanent=False)
            return False
        return True

    def wait_for_codex_restart_request(self) -> bool:
        """Wait on file/process events until the system action is clicked or fails."""
        if self._closed or self._system_action is None:
            return False
        command = self._wait_for_system_action_command(
            {WORK_OVERLAY_RESTART_ACTION},
            timeout_seconds=None,
        )
        if command is None:
            if not self._system_action_unavailable_reason:
                self._system_action_unavailable_reason = (
                    "PySide6 desktop overlay helper exited before restart was requested"
                )
            return False
        return True

    @property
    def system_action_unavailable_reason(self) -> str:
        return self._system_action_unavailable_reason

    def _wait_for_system_action_command(
        self,
        accepted_actions: set[str],
        *,
        timeout_seconds: float | None,
    ) -> dict[str, object] | None:
        process = self._process
        if process is None:
            return None
        wake = Event()
        matched: list[dict[str, object]] = []
        result_lock = threading.Lock()
        drain_lock = threading.Lock()
        expected_action_id = str(
            (self._system_action or {}).get("id") or ""
        ).strip()

        def drain_commands() -> None:
            with drain_lock:
                commands = list(self._deferred_commands)
                self._deferred_commands.clear()
                commands.extend(self.take_commands())
                selected, deferred, runtime_error = (
                    overlay_supervision.route_system_action_commands(
                        commands,
                        accepted_actions=accepted_actions,
                        expected_action_id=expected_action_id,
                    )
                )
                self._deferred_commands.extend(deferred)
                if runtime_error is not None:
                    self._system_action_unavailable_reason = runtime_error
                    wake.set()
                if selected is not None:
                    with result_lock:
                        if not matched:
                            matched.append(selected)
                    wake.set()

        watcher = FileChangeWatcher(
            lambda _reasons, _paths: drain_commands(),
            fallback_poll_seconds=WORK_OVERLAY_COMMAND_FALLBACK_POLL_SECONDS,
        )
        try:
            watcher.update(
                [FileWatchSpec.file(self._command_path, "work-overlay-system-action")]
            )
            drain_commands()

            def wait_for_helper_exit() -> None:
                try:
                    process.wait()
                except Exception:
                    pass
                wake.set()

            threading.Thread(
                target=wait_for_helper_exit,
                name="codex-hud-overlay-action-exit",
                daemon=True,
            ).start()
            wake.wait(timeout_seconds)
            drain_commands()
        finally:
            watcher.close()
        with result_lock:
            return dict(matched[0]) if matched else None

    def keep_alive(self) -> None:
        """Refresh the helper state file while renderer snapshots are unchanged."""
        if (
            self._closed
            or (
                not self.enabled
                and self._system_action is None
                and self._system_notice is None
                and not self._rest_reminder
            )
        ):
            return
        if (
            not self._last_payload_items
            and self._system_action is None
            and self._system_notice is None
            and not self._rest_reminder
        ):
            return
        now = self._clock.monotonic()
        self._ensure_helper_healthy(now)
        if now - self._last_state_write_at < WORK_OVERLAY_KEEPALIVE_SECONDS:
            return
        self._write_state(
            self._last_payload_items,
            theme=self._last_theme_payload,
            close=False,
        )
        self._maybe_start_helper(now)

    def _ensure_keepalive_worker(self) -> None:
        """Keep visible bubbles alive even if the renderer loop is blocked.

        The renderer normally schedules ``keep_alive`` as an event-loop
        deadline.  A slow CDP call or a daemon handoff can temporarily prevent
        that loop from reaching its deadline, while the desktop helper still
        expires an unchanged state file after a short grace period.  Keep this
        fallback limited to the lifetime of a published bubble; an idle HUD
        does not acquire a timer thread.
        """
        if self._closed:
            return
        thread = self._keepalive_thread
        if thread is not None and thread.is_alive():
            return
        self._keepalive_stop.clear()
        thread = threading.Thread(
            target=self._run_keepalive_worker,
            name="codex-usage-hud-overlay-keepalive",
            daemon=True,
        )
        self._keepalive_thread = thread
        thread.start()

    def _ensure_keepalive_worker_if_runtime_started(self) -> None:
        if isinstance(self._process, _SUBPROCESS_POPEN_TYPE):
            self._ensure_keepalive_worker()

    def _run_keepalive_worker(self) -> None:
        interval = max(1.0, min(5.0, WORK_OVERLAY_KEEPALIVE_SECONDS / 3.0))
        try:
            while not self._keepalive_stop.wait(interval):
                if self._closed:
                    return
                if (
                    (
                        not self.enabled
                        and self._system_action is None
                        and self._system_notice is None
                        and not self._rest_reminder
                    )
                    or (
                        not self._last_payload_items
                        and self._system_action is None
                        and self._system_notice is None
                        and not self._rest_reminder
                    )
                ):
                    return
                try:
                    self.keep_alive()
                except Exception:
                    _LOGGER.exception("work_overlay_keepalive_failed")
        finally:
            if self._keepalive_thread is threading.current_thread():
                self._keepalive_thread = None

    def _stop_keepalive_worker(self) -> None:
        self._keepalive_stop.set()
        thread = self._keepalive_thread
        self._keepalive_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.2)

    def mark_switch_completed(self, command: Mapping[str, object]) -> bool:
        """Tell the helper that a clicked bubble has become the active session."""
        if self._closed or not self.enabled or not self._last_payload_items:
            return False
        match_index = next(
            (
                index
                for index, item in enumerate(self._last_payload_items)
                if overlay_projection.command_matches_item(command, item)
            ),
            -1,
        )
        if match_index < 0:
            return False
        self._switch_completed_command = dict(command)
        self._switch_completed_until = (
            self._clock.monotonic() + WORK_OVERLAY_SWITCH_COMPLETED_HOLD_SECONDS
        )
        payload_items: list[dict[str, object]] = []
        for index, item in enumerate(self._last_payload_items):
            next_item = dict(item)
            next_item["current"] = index == match_index
            payload_items.append(next_item)
        self._last_payload_items = payload_items
        self._write_state(
            payload_items,
            theme=self._last_theme_payload,
            close=False,
        )
        self._ensure_keepalive_worker_if_runtime_started()
        return True

    def _apply_switch_completed_override(
        self,
        payload_items: Sequence[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        items = [dict(item) for item in payload_items]
        command = self._switch_completed_command
        if command is None:
            return items
        if self._clock.monotonic() > self._switch_completed_until:
            self._switch_completed_command = None
            self._switch_completed_until = 0.0
            return items
        match_index = next(
            (
                index
                for index, item in enumerate(items)
                if overlay_projection.command_matches_item(command, item)
            ),
            -1,
        )
        if match_index < 0:
            return items
        for index, item in enumerate(items):
            item["current"] = index == match_index
        return items

    def next_keep_alive_seconds(self) -> float | None:
        """Return when the helper state file needs its next refresh."""
        return overlay_supervision.next_keep_alive_seconds(
            closed=self._closed,
            enabled=self.enabled,
            has_payload=bool(self._last_payload_items),
            has_system_notice=self._system_notice is not None,
            has_rest_reminder=bool(self._rest_reminder),
            now_monotonic=self._clock.monotonic(),
            last_state_write_at=self._last_state_write_at,
            keepalive_seconds=WORK_OVERLAY_KEEPALIVE_SECONDS,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._stop_runtime(permanent=True)

    def take_commands(self) -> list[dict[str, object]]:
        if self._closed:
            return []
        return self._command_reader.read(self._command_path)

    def acknowledge_command(
        self,
        command: Mapping[str, object],
        *,
        status: str,
        result: Mapping[str, object] | None = None,
        error: Mapping[str, object] | None = None,
    ) -> bool:
        return overlay_command_channel.append_acknowledgement(
            self._state_path,
            command,
            producer_instance_id=self._producer_instance_id,
            status=status,
            result=result,
            error=error,
        )

    @property
    def _command_offset(self) -> int:
        return self._command_reader.offset

    @_command_offset.setter
    def _command_offset(self, value: int) -> None:
        self._command_reader.offset = max(0, int(value))

    @property
    def _seen_request_ids(self) -> set[str]:
        return self._command_reader.seen_request_ids

    @_seen_request_ids.setter
    def _seen_request_ids(self, value: set[str]) -> None:
        self._command_reader.seen_request_ids = set(value)

    @property
    def command_path(self) -> Path:
        return self._command_path

    def _stop_runtime(self, *, permanent: bool) -> None:
        if permanent:
            self._closed = True
        self._stop_keepalive_worker()
        process = self._process
        self._process = None
        if process is not None:
            # A teardown of a just-started helper ("did not become ready") is a
            # rapid failure too, and must feed the breaker.
            self._note_helper_exit(
                int(getattr(process, "returncode", 0) or 0),
                self._clock.monotonic(),
            )
        try:
            state_exists = self._state_path.exists()
        except OSError:
            state_exists = False
        if process is not None or state_exists:
            self._write_state([], close=True)
        if process is not None:
            try:
                process.wait(timeout=1.0)
            except Exception:
                try:
                    process.terminate()
                except Exception:
                    pass
        try:
            self._state_path.unlink()
        except OSError:
            pass
        try:
            self._command_path.unlink()
        except OSError:
            pass
        try:
            overlay_ipc.ack_path(self._state_path).unlink()
        except OSError:
            pass
        try:
            overlay_ipc.heartbeat_path(self._state_path).unlink()
        except OSError:
            pass
        self._command_offset = 0
        self._seen_request_ids.clear()
        self._deferred_commands.clear()
        self._last_payload_items = None
        self._last_theme_payload = {}
        self._system_action = None
        self._system_notice = None
        self._rest_reminder = {}
        self._last_state_signature = None
        self._last_state_write_at = 0.0
        self._switch_completed_command = None
        self._switch_completed_until = 0.0
        self._helper_started_at = 0.0
        self._helper_started_monotonic = 0.0
        self._last_helper_heartbeat_at = 0.0

    def _maybe_start_helper(self, now: float) -> bool:
        """Start the helper only when backoff and the breaker allow it.

        Every overlay entry point funnels through here. The renderer republishes
        an unchanged rest-reminder payload on every tick, so a helper that keeps
        exiting immediately must never be respawned at caller speed: that is the
        respawn storm which starves the Codex renderer into a blank window.
        """
        if self._process is not None:
            return True
        if now < float(self._restart_blocked_until or 0.0):
            return False
        if now < float(self._helper_breaker_until or 0.0):
            return False
        self._start()
        return self._process is not None

    def _note_helper_exit(self, exit_code: int, now: float) -> None:
        """Feed one observed helper exit into the rapid-exit circuit breaker."""
        started_at = float(self._helper_started_monotonic or 0.0)
        if started_at <= 0.0 or (
            now - started_at
        ) >= WORK_OVERLAY_HELPER_RAPID_EXIT_SECONDS:
            # The helper lived long enough to count as a fresh lifecycle.
            self._helper_rapid_exits.clear()
            return
        self._helper_rapid_exits.append(now)
        window = WORK_OVERLAY_HELPER_RAPID_EXIT_WINDOW_SECONDS
        while self._helper_rapid_exits and (
            now - self._helper_rapid_exits[0]
        ) > window:
            self._helper_rapid_exits.popleft()
        if len(self._helper_rapid_exits) < WORK_OVERLAY_HELPER_RAPID_EXIT_LIMIT:
            return
        self._helper_rapid_exits.clear()
        self._helper_breaker_until = max(
            self._helper_breaker_until,
            now + WORK_OVERLAY_HELPER_BREAKER_BACKOFF_SECONDS,
        )
        _LOGGER.warning(
            "work_overlay_helper_rapid_exit_breaker tripped exit_code=%s "
            "backoff_seconds=%s",
            exit_code,
            WORK_OVERLAY_HELPER_BREAKER_BACKOFF_SECONDS,
        )

    def _start(self) -> None:
        try:
            self._process = subprocess.Popen(
                _work_overlay_command(self._state_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # This is the same frozen executable, so its PyInstaller
                # parent/child environment must remain intact.
                env=internal_process_environment(),
            )
            started_at = self._clock.time()
            self._helper_started_at = started_at
            self._last_helper_heartbeat_at = started_at
            self._helper_started_monotonic = self._clock.monotonic()
        except Exception:
            self._process = None
            self._helper_started_monotonic = 0.0
            self._restart_blocked_until = (
                self._clock.monotonic() + WORK_OVERLAY_RESTART_BACKOFF_SECONDS
            )
            self._report_unavailable_once("unable to start PySide6 desktop overlay helper")

    def _wait_for_helper_ready(self) -> bool:
        """Wait until the freshly spawned helper has rendered one state."""
        process = self._process
        if process is None:
            return False
        # Unit-test fakes and alternate embedders do not own the real helper
        # heartbeat.  The production path always stores a subprocess.Popen.
        if not isinstance(process, _SUBPROCESS_POPEN_TYPE):
            return True
        heartbeat_path = overlay_ipc.heartbeat_path(self._state_path)
        deadline = (
            self._clock.monotonic() + WORK_OVERLAY_HELPER_READY_TIMEOUT_SECONDS
        )
        while True:
            try:
                if process.poll() is not None:
                    return False
            except Exception:
                return False
            if heartbeat_path.exists():
                return True
            if self._clock.monotonic() >= deadline:
                return False
            time.sleep(WORK_OVERLAY_HELPER_READY_POLL_SECONDS)

    def _ensure_helper_healthy(self, now: float) -> None:
        """Restart a live-but-stuck helper before it can retain stale bubbles."""
        process = self._process
        if process is None:
            return
        process_exit_code = process.poll()
        if process_exit_code is not None:
            process_exit_code = getattr(process, "returncode", process_exit_code)
        self._refresh_helper_heartbeat()
        decision = overlay_supervision.evaluate_helper_health(
            process_exit_code=process_exit_code,
            user_object_count=_windows_user_object_count(process),
            helper_started_at=self._helper_started_at,
            last_heartbeat_at=self._last_helper_heartbeat_at,
            now_monotonic=now,
            now_wall=self._clock.time(),
            heartbeat_timeout_seconds=WORK_OVERLAY_HELPER_HEARTBEAT_TIMEOUT_SECONDS,
            max_user_objects=WORK_OVERLAY_HELPER_MAX_USER_OBJECTS,
            restart_backoff_seconds=WORK_OVERLAY_RESTART_BACKOFF_SECONDS,
        )
        if decision.action == overlay_supervision.EXITED:
            self._last_helper_exit_code = int(decision.exit_code or 0)
            self._note_helper_exit(self._last_helper_exit_code, now)
            self._process = None
            self._restart_blocked_until = max(
                float(decision.restart_blocked_until or 0.0),
                float(self._helper_breaker_until or 0.0),
            )
            if decision.reason:
                self._report_unavailable_once(decision.reason)
            return
        if decision.action == overlay_supervision.RESTART:
            _LOGGER.warning(
                "work_overlay_helper_unresponsive reason=%s",
                decision.reason,
            )
            self._restart_unresponsive_helper(
                process,
                now,
                reason=decision.reason,
            )

    def _restart_unresponsive_helper(
        self,
        process: subprocess.Popen[str],
        now: float,
        *,
        reason: str,
    ) -> None:
        _LOGGER.warning("work_overlay_helper_restart reason=%s", reason)
        self._note_helper_exit(
            int(getattr(process, "returncode", 0) or 0),
            now,
        )
        try:
            process.terminate()
            process.wait(timeout=1.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        self._process = None
        self._helper_started_at = 0.0
        self._helper_started_monotonic = 0.0
        self._last_helper_heartbeat_at = 0.0
        self._restart_blocked_until = now
        self._maybe_start_helper(now)

    def _refresh_helper_heartbeat(self) -> None:
        try:
            heartbeat_at = overlay_ipc.heartbeat_path(self._state_path).stat().st_mtime
        except OSError:
            return
        self._last_helper_heartbeat_at = max(
            self._last_helper_heartbeat_at,
            float(heartbeat_at),
        )

    def reset_runtime_availability(self) -> bool:
        self._available = None
        self._unavailable_reason = ""
        self._unavailable_reported = False
        self._restart_blocked_until = 0.0
        # An explicit user action ("enable bubbles") is a legitimate retry and
        # clears the rapid-exit breaker, otherwise a past storm would keep the
        # overlay suppressed for its full backoff window.
        self._helper_rapid_exits.clear()
        self._helper_breaker_until = 0.0
        return self._runtime_available()

    def _runtime_available(self) -> bool:
        decision = overlay_supervision.probe_runtime_availability(
            cached=self._available,
            probe=self._runtime_available_probe,
            unavailable_reason=self._unavailable_reason,
        )
        self._available = decision.available
        self._unavailable_reason = decision.reason
        return decision.available

    def _report_unavailable_once(self, reason: str) -> None:
        if self._unavailable_reported:
            return
        self._unavailable_reported = True
        message = str(reason or "PySide6 desktop overlay is unavailable")
        _LOGGER.warning("work_overlay_unavailable reason=%s", message)
        try:
            self._diagnostic_sink("work_overlay_unavailable", reason=message)
        except Exception:
            return

    def _append_transition_audit(
        self,
        items: Sequence[Mapping[str, object]],
        *,
        close: bool,
        state_revision: int,
    ) -> None:
        overlay_transition_audit.append_transition_audit(
            items,
            previous_items=self._last_payload_items,
            close=close,
            state_revision=state_revision,
            audit_path=self._transition_audit_path,
            state_path=self._state_path,
            producer_instance_id=self._producer_instance_id,
            owner_pid=os.getpid(),
        )

    def _write_state(
        self,
        items: Sequence[Mapping[str, object]],
        *,
        theme: Mapping[str, object] | None = None,
        close: bool,
    ) -> None:
        try:
            payload_items = list(items)
            payload_signature = self._state_signature(
                payload_items,
                theme=theme,
                close=close,
            )
            next_revision = self._state_revision + 1
            state_payload = overlay_state.build_state_message(
                owner_pid=os.getpid(),
                item_limit=int(self.item_limit),
                side=self.side,
                command_path=self._command_path,
                state_path=self._state_path,
                revision=next_revision,
                producer_instance_id=self._producer_instance_id,
                items=payload_items,
                system_action=self._system_action,
                system_notice=self._system_notice,
                rest_reminder=self._rest_reminder,
                theme=theme,
                updated_at=self._clock.time(),
                close=close,
            )
            overlay_state.write_state(
                self._state_path,
                state_payload,
                writer=write_json_object,
            )
            self._append_transition_audit(
                payload_items,
                close=close,
                state_revision=next_revision,
            )
            self._state_revision = next_revision
            self._last_state_signature = payload_signature
            self._last_state_write_at = self._clock.monotonic()
        except OSError:
            return

    def _state_signature(
        self,
        items: Sequence[Mapping[str, object]],
        *,
        theme: Mapping[str, object] | None = None,
        close: bool,
    ) -> str:
        return overlay_state.state_signature(
            item_limit=int(self.item_limit),
            side=self.side,
            command_path=self._command_path,
            items=items,
            system_action=self._system_action,
            system_notice=self._system_notice,
            rest_reminder=self._rest_reminder,
            theme=theme,
            close=close,
        )

    def _theme_payload(self) -> dict[str, object]:
        snapshot = self._theme_probe.snapshot()
        if snapshot.source not in {"cdp", "persisted"}:
            return {}
        return snapshot.hud_tokens.to_dict()


__all__ = ["DesktopWorkOverlay", "OverlayClock"]
