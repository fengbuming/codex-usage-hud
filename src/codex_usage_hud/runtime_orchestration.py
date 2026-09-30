"""CLI composition root for the renderer HUD runtime."""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import cli_app as _cli_app_owner
from . import codex_app_runtime as _codex_app_runtime_owner
from . import daemon_runtime as _daemon_runtime_owner
from . import instance_lock
from . import loading_feedback
from . import renderer_runtime
from .renderer_session_ports import RendererSessionPorts
from . import renderer_startup as _renderer_startup_owner
from . import runtime_compat as _runtime_compat
from . import runtime_diagnostics as _runtime_diagnostics_owner
from . import runtime_paths as _runtime_paths_owner
from .config import DEFAULT_WORK_OVERLAY_MAX_ITEMS, UserConfigStore, effective_display_mode
from .daemon import CodexDaemonManager, configure_daemon_logging, hide_console_window
from .platforms import CodexWindowTracker
from .platforms.cdp_probe import cdp_port_from_env, cdp_version_info, list_targets, pick_page_target
from .runtime_ports import RuntimeServices
from .ui.renderer_domains import remove_renderer_hud_from_pages
from .usage_cache import UsageSummaryCache  # noqa: F401 - public CLI facade export


_LOGGER = logging.getLogger("codex_usage_hud.runtime_orchestration")
ACTIVE_WORK_ITEM_LIMIT = DEFAULT_WORK_OVERLAY_MAX_ITEMS
WORK_OVERLAY_STALE_SECONDS = 20.0
WORK_OVERLAY_ALPHA = 0.88
WORK_OVERLAY_HOVER_ALPHA = 0.22
WORK_OVERLAY_HEADER_TITLE_LIMIT = 28
WORK_OVERLAY_RESTART_ACTION_ID = "restart-codex-for-renderer"
WORK_OVERLAY_CDP_SWITCH_TIMEOUT_SECONDS = 3.0
WORK_OVERLAY_WINDOW_PREPARE_TIMEOUT_SECONDS = 0.8
WORK_OVERLAY_SWITCH_REFOCUS_TIMEOUT_SECONDS = 0.8
WORK_OVERLAY_SWITCH_REFOCUS_DELAY_SECONDS = 0.08
NATIVE_SEARCH_SESSION_SWITCH_ENV = "CODEX_USAGE_HUD_NATIVE_SEARCH_SWITCH"
RENDERER_IDLE_POLL_MS = 1500
RENDERER_ACTIVE_WORK_RESCAN_SECONDS = 5.0
RENDERER_UPDATE_FAILURE_LIMIT = 6
AUTO_RENDERER_TIMEOUT_FAILURE_LIMIT = 3
RENDERER_HUD_UNAVAILABLE = 20
DAEMON_RESTART_REQUESTED = 10
HUD_SWITCH_TO_RENDERER_RESTART_CODEX = 32
HUD_AUTO_RESTART_CODEX = 33
HUD_SUSPEND_FOR_SESSION_LOCK = 34
RENDERER_CDP_TIMEOUT_SECONDS = 0.35
DAEMON_RENDERER_CDP_TIMEOUT_SECONDS = 1.5
RENDERER_INITIAL_TIMEOUT_SECONDS = 0.75
RENDERER_WINDOW_PREPARE_TIMEOUT_SECONDS = 2.0
DAEMON_RENDERER_INITIAL_TIMEOUT_SECONDS = 10.0
RENDERER_RESTART_CDP_TIMEOUT_SECONDS = 1.5
RENDERER_RESTART_INITIAL_TIMEOUT_SECONDS = 30.0
RENDERER_RESTART_READY_TIMEOUT_SECONDS = 30.0
RENDERER_RESTART_READY_POLL_SECONDS = 0.25
DAEMON_RENDERER_WINDOW_READY_TIMEOUT_SECONDS = 15.0
RENDERER_ACTIVE_SESSION_BOOTSTRAP_WAIT_SECONDS = 0.35
RENDERER_STARTUP_STEP_MIN_VISIBLE_SECONDS = 0.45
RENDERER_SLOW_OPERATION_LOG_MS = 250.0
RENDERER_EVENT_IDLE_WAIT_SECONDS = 30.0
RENDERER_ACTIVE_WORK_AFTER_SESSION_DELAY_SECONDS = 1.2
RENDERER_STARTUP_LAUNCH = "launch"
RENDERER_STARTUP_ATTACH = "attach"
RENDERER_STARTUP_RESTART_REQUIRED = "restart-required"
RENDERER_STARTUP_ATTACH_OBSERVED = "attach-observed"
RENDERER_STARTUP_RELAUNCH_OBSERVED = "relaunch-observed"
_REMOTE_DEBUGGING_PORT_PATTERN = re.compile(r"(?:^|\s)--remote-debugging-port(?:=|\s+)(\d{1,5})(?=\s|$)")


def __getattr__(name: str) -> object:
    """Serve old private test imports without making them coordinator owners."""
    return _runtime_compat.resolve(name)


def _runtime_display_mode(value: object) -> str:
    return effective_display_mode(value)


def _initial_runtime_display_mode(args: argparse.Namespace) -> str:
    del args
    return "renderer"


# Explicit composition dependencies used by the root wrappers below.  Legacy
# test-only imports remain lazily resolved through runtime_compat.
_running_codex_desktop_processes = _codex_app_runtime_owner.running_codex_desktop_processes
_audited_running_codex_desktop_processes = _codex_app_runtime_owner.audited_running_codex_desktop_processes
_codex_processes_running = _codex_app_runtime_owner.codex_processes_running
_activate_running_codex_app = _codex_app_runtime_owner.activate_running_codex_app
_stop_codex_processes = _codex_app_runtime_owner.stop_codex_app
_stop_macos_codex_app = _codex_app_runtime_owner.stop_macos_codex_app
_wait_for_visible_codex_window = _codex_app_runtime_owner.wait_for_visible_codex_window
renderer_cdp_state_path = _runtime_paths_owner.renderer_cdp_state_path
renderer_diagnostic_path = _runtime_paths_owner.renderer_diagnostic_path
_localhost_cdp_port_is_listening = _renderer_startup_owner.localhost_cdp_port_is_listening
_localhost_cdp_port_available = _renderer_startup_owner.localhost_cdp_port_available
_allocate_fresh_renderer_cdp_port = _renderer_startup_owner.allocate_fresh_cdp_port
build_runtime_context = _runtime_compat.resolve("build_runtime_context")
build_snapshot = _runtime_compat.resolve("build_snapshot")
snapshot_to_text = _runtime_compat.resolve("snapshot_to_text")
build_parser = _cli_app_owner.build_parser
run_update_check = _cli_app_owner.run_update_check
run_update_install = _cli_app_owner.run_update_install
run_work_overlay_helper = _runtime_compat.resolve("run_work_overlay_helper")
cleanup_stale_work_overlay_files = _runtime_compat.resolve("cleanup_stale_work_overlay_files")
_init_force_desktop_overlay_missing_from_env = _runtime_compat.resolve("_init_force_desktop_overlay_missing_from_env")
_attach_cli_logger_to_daemon_log = _runtime_diagnostics_owner.attach_cli_logger_to_daemon_log


def _wait_for_renderer_restart_ready(
    port: int,
    *,
    source: str,
    timeout_seconds: float = RENDERER_RESTART_READY_TIMEOUT_SECONDS,
    poll_seconds: float = RENDERER_RESTART_READY_POLL_SECONDS,
) -> bool:
    """Wait until the replacement app exposes both its window and page target."""
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    candidate = _renderer_startup_owner.RendererCdpPortCandidate(
        port=int(port),
        source=f"restart-{source}",
    )
    window_ready = False
    window_status = "not_checked"
    window_reason = ""
    window_hwnd = 0
    cdp_ready = False
    cdp_reason = "not_checked"
    while True:
        if not window_ready:
            (
                window_ready,
                window_status,
                window_reason,
                window_hwnd,
            ) = _wait_for_visible_codex_window(timeout_seconds=0.0)
        cdp_ready, cdp_reason = _validate_renderer_cdp_candidate(candidate)
        if window_ready and cdp_ready:
            _append_renderer_diagnostic(
                "renderer_restart_ready",
                source=source,
                port=port,
                hwnd=window_hwnd,
            )
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(max(0.01, float(poll_seconds)), remaining))
    _append_renderer_diagnostic(
        "renderer_restart_not_ready",
        source=source,
        port=port,
        window_ready=window_ready,
        window_status=window_status,
        window_reason=window_reason,
        hwnd=window_hwnd,
        cdp_ready=cdp_ready,
        cdp_reason=cdp_reason,
        timeout_seconds=timeout_seconds,
    )
    return False


def _restart_codex_for_renderer(*, source: str = "user") -> bool:
    if sys.platform.startswith("win") and not _stop_codex_processes():
        return False
    if sys.platform == "darwin" and not _stop_macos_codex_app():
        return False
    try:
        port = _select_launch_renderer_cdp_port(require_fresh=True)
    except (OSError, RuntimeError) as exc:
        _append_renderer_diagnostic(
            "renderer_cdp_launch_failed", reason=str(exc), source="restart"
        )
        return False
    _append_renderer_diagnostic(
        (
            "renderer_restart_requested_automatically"
            if source == "automatic"
            else "renderer_restart_requested_by_user"
        ),
        action_id="restart-codex-for-renderer", port=port,
        source=source,
    )
    if not launch_codex_app(debugger=True, cdp_port=port):
        _append_renderer_diagnostic(
            "renderer_restart_launch_failed",
            source=source,
            port=port,
        )
        return False
    return _wait_for_renderer_restart_ready(port, source=source)


def _automatic_restart_codex_for_renderer() -> bool:
    return _restart_codex_for_renderer(source="automatic")


def run_renderer_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    daemon_manager: CodexDaemonManager | None = None,
    launched_codex: bool = False,
    observed_codex_launch: bool = False,
    loading_feedback: object | None = None,
    overlay_handoff: dict[str, object] | None = None,
    seamless_recovery: bool = False,
    restart_codex: object | None = None,
    shutdown_coordinator: object | None = None,
    services: RuntimeServices | None = None,
) -> int:
    return renderer_runtime.run_renderer_hud_session(
        args,
        lock_already_held=lock_already_held,
        daemon_manager=daemon_manager,
        launched_codex=launched_codex,
        observed_codex_launch=observed_codex_launch,
        loading_feedback=loading_feedback,
        overlay_handoff=overlay_handoff,
        seamless_recovery=seamless_recovery,
        restart_codex=restart_codex,
        shutdown_coordinator=shutdown_coordinator,
        services=services,
        ports=RendererSessionPorts.from_mapping(globals()),
    )


def launch_codex_app(
    *, debugger: bool = False, cdp_port: int | None = None,
    on_debugger_launch: Any | None = None,
) -> bool:
    port = cdp_port if cdp_port is not None or not debugger else cdp_port_from_env()
    callback = on_debugger_launch or _remember_requested_renderer_cdp_port
    return _codex_app_runtime_owner.launch_codex_app(
        debugger=debugger, cdp_port=port,
        on_debugger_launch=callback if debugger else on_debugger_launch,
    )


def _prepare_codex_window_for_renderer(
    *, timeout_seconds: float, poll_seconds: float = 0.25,
    launch_if_missing: bool = False,
) -> tuple[bool, str, str, int]:
    port = _select_initial_renderer_cdp_port() if launch_if_missing else cdp_port_from_env()
    return _codex_app_runtime_owner.prepare_codex_window_for_renderer(
        timeout_seconds=timeout_seconds, poll_seconds=poll_seconds,
        launch_if_missing=launch_if_missing, cdp_port=port,
        tracker_factory=lambda: CodexWindowTracker(enable_uia=False),
        processes_running=_codex_processes_running,
        activate=_activate_running_codex_app,
        launch=lambda **_kwargs: launch_codex_app(debugger=True),
        monotonic=time.monotonic, sleep=time.sleep,
    )


_runtime_diagnostic_sink = _runtime_diagnostics_owner.JsonlDiagnosticSink(
    _runtime_paths_owner.renderer_diagnostic_path
)


def _append_renderer_diagnostic(stage: str, **fields: object) -> None:
    _runtime_diagnostic_sink.append(stage, **fields)


def _append_runtime_error_diagnostic(action: str, event: object) -> None:
    _runtime_diagnostics_owner.append_runtime_error_diagnostic(
        action, event, append=_append_renderer_diagnostic
    )


def _ensure_runtime_error_diagnostics(context: object) -> None:
    _runtime_diagnostics_owner.ensure_runtime_error_diagnostics(
        context, callback=_append_runtime_error_diagnostic
    )


def stop_running_hud(path: Path | None = None) -> str:
    def cleanup() -> None:
        try:
            remove_renderer_hud_from_pages(port=_read_persisted_renderer_cdp_port())
        except Exception:
            _LOGGER.debug("renderer_hud_shutdown_cleanup_failed", exc_info=True)
    return instance_lock.stop_recorded_instance(path, before_stop=cleanup)


def _startup_ports() -> object:
    return _renderer_startup_owner.RendererStartupPorts(
        desktop_processes=_running_codex_desktop_processes,
        audited_desktop_processes=_audited_running_codex_desktop_processes,
        desktop_running=_codex_processes_running,
        diagnostic=_append_renderer_diagnostic,
        state_path=renderer_cdp_state_path,
    )


def _renderer_startup_plan(*, launched_codex: bool = False, observed_codex_launch: bool = False) -> object:
    return _renderer_startup_owner.startup_plan(
        launched_codex=launched_codex, observed_codex_launch=observed_codex_launch,
        ports=_startup_ports(), select_initial=_select_initial_renderer_cdp_port,
        select_launch=_select_launch_renderer_cdp_port,
        find_existing=_find_existing_renderer_cdp_candidate,
    )


def _renderer_cdp_port_candidates() -> list[object]:
    return _renderer_startup_owner.cdp_port_candidates(ports=_startup_ports())


def _validate_renderer_cdp_candidate(candidate: object) -> tuple[bool, str]:
    return _renderer_startup_owner.validate_cdp_candidate(
        candidate, listening=_localhost_cdp_port_is_listening,
        version_probe=cdp_version_info, target_list=list_targets,
        page_picker=pick_page_target,
    )


def _find_existing_renderer_cdp_candidate() -> object | None:
    return _renderer_startup_owner.find_existing_cdp_candidate(
        ports=_startup_ports(), validate=_validate_renderer_cdp_candidate
    )


def _read_persisted_renderer_cdp_state_port(key: str) -> int | None:
    return _renderer_startup_owner.read_persisted_cdp_state_port(key, state_path=renderer_cdp_state_path)


def _read_persisted_renderer_cdp_port() -> int | None:
    return _renderer_startup_owner.read_persisted_cdp_port(state_path=renderer_cdp_state_path)


def _remember_requested_renderer_cdp_port(port: int | None) -> None:
    _renderer_startup_owner.remember_requested_cdp_port(port, state_path=renderer_cdp_state_path)


def _remember_successful_renderer_cdp_port(port: int | None) -> None:
    _renderer_startup_owner.remember_cdp_port(
        port, requested=True, successful=True, state_path=renderer_cdp_state_path
    )


def _select_initial_renderer_cdp_port() -> int:
    return _renderer_startup_owner.select_initial_cdp_port(
        state_path=renderer_cdp_state_path, listening=_localhost_cdp_port_is_listening
    )


def _select_launch_renderer_cdp_port(*, require_fresh: bool = False) -> int:
    return _renderer_startup_owner.select_launch_cdp_port(
        require_fresh=require_fresh, state_path=renderer_cdp_state_path,
        available=_localhost_cdp_port_available,
        allocate=_allocate_fresh_renderer_cdp_port,
    )


def run_once_snapshot(args: argparse.Namespace) -> int:
    return _cli_app_owner.run_once_snapshot(
        args, context_factory=build_runtime_context, snapshot_builder=build_snapshot,
        snapshot_formatter=snapshot_to_text,
    )


def _daemon_startup_decision(args: argparse.Namespace, manager: object) -> object:
    return _daemon_runtime_owner.daemon_startup_decision(args, manager)


def run_hud_session(args: argparse.Namespace, **kwargs: object) -> int:
    if "loading_feedback" in kwargs:
        kwargs["loading_feedback_instance"] = kwargs.pop("loading_feedback")
    return _daemon_runtime_owner.run_hud_session(
        args, run_renderer=run_renderer_hud_session,
        restart_codex=_restart_codex_for_renderer, **kwargs,
    )


def run_qt_hud_session(args: argparse.Namespace, **kwargs: object) -> int:
    if "loading_feedback" in kwargs:
        kwargs["loading_feedback_instance"] = kwargs.pop("loading_feedback")
    return _daemon_runtime_owner.run_qt_hud_session(args, **kwargs)


def run_tk_hud_session(args: argparse.Namespace, **kwargs: object) -> int:
    if "loading_feedback" in kwargs:
        kwargs["loading_feedback_instance"] = kwargs.pop("loading_feedback")
    return _daemon_runtime_owner.run_tk_hud_session(args, **kwargs)


def run_daemon(args: argparse.Namespace) -> int:
    services = _daemon_runtime_owner.DaemonServices(
        manager_factory=CodexDaemonManager, lock_factory=instance_lock.HudInstanceLock,
        configure_logging=configure_daemon_logging,
        attach_logger=_attach_cli_logger_to_daemon_log, hide_console=hide_console_window,
        startup_decision=_daemon_startup_decision,
        create_loading=loading_feedback._create_loading_feedback,
        run_renderer=run_renderer_hud_session, run_hud=run_hud_session,
        restart_codex=_restart_codex_for_renderer,
        automatic_restart_codex=_automatic_restart_codex_for_renderer,
        select_launch_port=_select_launch_renderer_cdp_port, launch=launch_codex_app,
        append_diagnostic=_append_renderer_diagnostic,
    )
    return _daemon_runtime_owner.run_daemon(args, services=services)


def main(argv: Sequence[str] | None = None) -> int:
    # PyInstaller frozen builds must call freeze_support() in __main__ before any
    # worker process is spawned; otherwise ``spawn`` re-executes this executable
    # and the search index's ProcessPoolExecutor deadlocks. _enable_frozen_process_pool
    # then unlocks process pools for the frozen build so the first index build
    # runs in parallel instead of serialised on one GIL-bound thread.
    import multiprocessing

    multiprocessing.freeze_support()
    from .core import session_search as _session_search

    _session_search._enable_frozen_process_pool()
    from .daemon_launch import detach_daemon_if_needed, resume_brokered_daemon

    services = _cli_app_owner.CliAppServices(
        run_daemon=run_daemon, run_once=run_once_snapshot, stop=stop_running_hud,
        run_loading_helper=loading_feedback.run_loading_feedback_helper,
        run_overlay_helper=run_work_overlay_helper,
        cleanup_loading=loading_feedback.cleanup_stale_loading_feedback_files,
        cleanup_overlay=cleanup_stale_work_overlay_files,
        enable_crash_diagnostics=_runtime_diagnostics_owner.enable_crash_diagnostics,
        init_overlay_dependency_override=_init_force_desktop_overlay_missing_from_env,
        config_store_factory=UserConfigStore, parser_factory=build_parser,
        update_check=run_update_check, update_install=run_update_install,
        detach_daemon=detach_daemon_if_needed,
        resume_broker=lambda address, token: resume_brokered_daemon(address, token, main),
    )
    return _cli_app_owner.main(argv, services=services)


# RendererSessionPorts is intentionally materialized so monkeypatches on this
# composition root remain injection points. Other historical imports use
# __getattr__ and do not recreate ownership here.
globals().update(_runtime_compat.session_port_bindings(globals()))


if __name__ == "__main__":
    raise SystemExit(main())
