"""Persistent daemon lifecycle and renderer-only compatibility sessions."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
import logging
import sys
import threading
from typing import Any

from . import loading_feedback
from .codex_app_runtime import launch_codex_app, restart_codex_app
from .daemon import (
    CodexDaemonManager,
    ProcessListenerError,
    configure_daemon_logging,
    hide_console_window,
)
from .instance_lock import HudAlreadyRunningError, HudInstanceLock
from .runtime_diagnostics import append_renderer_diagnostic, attach_cli_logger_to_daemon_log
from .session_activity import WindowsSessionLockMonitor
from .windows_tray import ShutdownCoordinator, create_windows_tray


DEFAULT_DAEMON_STARTUP_WAIT = "wait"
DEFAULT_DAEMON_STARTUP_RENDERER = "renderer"
DEFAULT_DAEMON_STARTUP_CANCEL = "cancel"
DAEMON_RESTART_REQUESTED = 10
RENDERER_HUD_UNAVAILABLE = 20
HUD_SWITCH_TO_RENDERER = 31
HUD_SWITCH_TO_RENDERER_RESTART_CODEX = 32
HUD_AUTO_RESTART_CODEX = 33
HUD_SUSPEND_FOR_SESSION_LOCK = 34
WORK_OVERLAY_RESTART_ACTION_ID = "restart-codex-for-renderer"
DAEMON_RENDERER_RECOVERY_FAILURE_LIMIT = 3
RENDERER_RECOVERY_NOTICE_TITLE = "检测到 Codex App 未启用 CDP"
RENDERER_RECOVERY_NOTICE_MESSAGE = "正在以 CDP 模式重新启动，请稍候。"
RENDERER_RESTART_NOTICE_TITLE = "正在重启 Codex"
RENDERER_RESTART_NOTICE_MESSAGE = "正在以 CDP 模式重启 Codex App，请稍候。"

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DaemonStartupDecision:
    mode: str
    launch_codex: bool = False
    codex_was_running: bool = False


@dataclass(frozen=True)
class DaemonServices:
    manager_factory: Callable[..., object] = CodexDaemonManager
    lock_factory: Callable[[], object] = HudInstanceLock
    configure_logging: Callable[[], object] = configure_daemon_logging
    attach_logger: Callable[[], object] = attach_cli_logger_to_daemon_log
    hide_console: Callable[[], object] = hide_console_window
    startup_decision: Callable[[argparse.Namespace, object], DaemonStartupDecision] | None = None
    create_loading: Callable[..., object] = loading_feedback._create_loading_feedback
    run_renderer: Callable[..., int] | None = None
    run_hud: Callable[..., int] | None = None
    restart_codex: Callable[[], bool] | None = None
    automatic_restart_codex: Callable[[], bool] | None = None
    select_launch_port: Callable[[], int] | None = None
    launch: Callable[..., bool] = launch_codex_app
    append_diagnostic: Callable[..., object] = append_renderer_diagnostic
    create_tray: Callable[..., object] | None = None


@contextmanager
def _tray_lifetime(
    factory: Callable[..., object],
    shutdown: ShutdownCoordinator,
):
    """Keep one tray instance alive for the whole daemon lifetime."""
    tray: object | None = None
    try:
        tray = factory(on_exit=shutdown.request)
        start = getattr(tray, "start", None)
        if callable(start):
            start()
    except Exception as exc:
        _LOGGER.warning("windows_tray_unavailable error=%s", exc)
    try:
        yield
    finally:
        close = getattr(tray, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                _LOGGER.debug("windows_tray_close_failed", exc_info=True)


def _configure_manager_shutdown(
    manager: object,
    shutdown: ShutdownCoordinator,
) -> None:
    if isinstance(manager, CodexDaemonManager):
        manager.set_stop_requested(shutdown)


def _clone_args(args: argparse.Namespace, **changes: object) -> argparse.Namespace:
    """Copy argparse state before a daemon changes renderer preferences."""
    try:
        return replace(args, **changes)
    except TypeError:
        namespace = argparse.Namespace(**vars(args))
        for key, value in changes.items():
            setattr(namespace, key, value)
        return namespace


def clone_args_with_renderer_preference(
    args: argparse.Namespace,
    enabled: bool = True,
) -> argparse.Namespace:
    return _clone_args(
        args,
        renderer_hud=bool(enabled),
        hud_mode="renderer",
        runtime_hud_mode="renderer",
    )


def clone_args_with_display_mode(
    args: argparse.Namespace,
    mode: str,
) -> argparse.Namespace:
    return _clone_args(
        args,
        hud_mode=mode,
        runtime_hud_mode=mode,
        renderer_hud=mode == "renderer",
    )


def daemon_startup_decision(
    args: argparse.Namespace,
    manager: object,
) -> DaemonStartupDecision:
    del args
    snapshot = manager.snapshot()
    if snapshot.found:
        return DaemonStartupDecision(
            DEFAULT_DAEMON_STARTUP_WAIT,
            codex_was_running=True,
        )
    return DaemonStartupDecision(
        DEFAULT_DAEMON_STARTUP_RENDERER,
        launch_codex=True,
    )


def _default_restart_codex() -> bool:
    return restart_codex_app(debugger=True)


def _default_services() -> DaemonServices:
    return DaemonServices(
        startup_decision=daemon_startup_decision,
        restart_codex=_default_restart_codex,
    )


def _manager_primary_pid(manager: object) -> int | None:
    """Read the daemon's current process baseline without assuming its type."""
    snapshot = getattr(manager, "last_snapshot", None)
    pid = getattr(snapshot, "primary_pid", None)
    try:
        value = int(pid)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value > 0 else None


def _wait_for_codex_after_explicit_restart(
    manager: object,
    previous_pid: int | None,
) -> bool:
    """Make the daemon baseline the replacement process before rendering."""
    wait_for_replacement = getattr(
        manager,
        "wait_for_codex_replacement",
        None,
    )
    if callable(wait_for_replacement) and previous_pid is not None:
        result = wait_for_replacement(previous_pid)
        return result is not False
    wait_for_codex = getattr(manager, "wait_for_codex", None)
    if callable(wait_for_codex):
        wait_for_codex()
    return True


def run_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    hide_until_attached: bool = True,
    daemon_manager: object | None = None,
    loading_feedback_instance: object | None = None,
    launched_codex: bool = False,
    observed_codex_launch: bool = False,
    seamless_recovery: bool = False,
    overlay_handoff: dict[str, object] | None = None,
    run_renderer: Callable[..., int] | None = None,
    restart_codex: Callable[[], bool] | None = None,
    shutdown_coordinator: object | None = None,
) -> int:
    """Run one renderer session and handle the bounded restart transition."""
    del hide_until_attached
    renderer = run_renderer
    if renderer is None:
        raise RuntimeError("daemon runtime requires a renderer session callback")
    if isinstance(daemon_manager, CodexDaemonManager) and isinstance(
        shutdown_coordinator, ShutdownCoordinator
    ):
        daemon_manager.set_stop_requested(shutdown_coordinator)
    restart = restart_codex or _default_restart_codex
    session_args = clone_args_with_display_mode(args, "renderer")
    renderer_exit = renderer(
        session_args,
        lock_already_held=lock_already_held,
        daemon_manager=daemon_manager,
        launched_codex=launched_codex,
        observed_codex_launch=observed_codex_launch,
        seamless_recovery=seamless_recovery,
        overlay_handoff=overlay_handoff,
        loading_feedback=loading_feedback_instance,
        restart_codex=restart,
        shutdown_coordinator=shutdown_coordinator,
    )
    if renderer_exit == HUD_SWITCH_TO_RENDERER_RESTART_CODEX:
        previous_pid = _manager_primary_pid(daemon_manager)
        if not restart():
            return RENDERER_HUD_UNAVAILABLE
        if (
            daemon_manager is not None
            and previous_pid is not None
            and not _wait_for_codex_after_explicit_restart(
                daemon_manager,
                previous_pid,
            )
        ):
            return RENDERER_HUD_UNAVAILABLE
        return renderer(
            clone_args_with_renderer_preference(args, True),
            lock_already_held=lock_already_held,
            daemon_manager=daemon_manager,
            launched_codex=True,
            observed_codex_launch=False,
            seamless_recovery=seamless_recovery,
            overlay_handoff=overlay_handoff,
            loading_feedback=loading_feedback_instance,
            restart_codex=restart,
            shutdown_coordinator=shutdown_coordinator,
        )
    if renderer_exit == HUD_SWITCH_TO_RENDERER:
        _LOGGER.info("renderer_hud_legacy_switch_ignored code=%s", renderer_exit)
        return RENDERER_HUD_UNAVAILABLE
    return renderer_exit


def _close_overlay_handoff(overlay_handoff: dict[str, object]) -> None:
    overlay = overlay_handoff.pop("overlay", None)
    if overlay is None:
        return
    close = getattr(overlay, "close", None)
    if callable(close):
        close()


def _show_renderer_system_notice(
    overlay_handoff: dict[str, object],
    *,
    title: str,
    message: str,
) -> bool:
    overlay = overlay_handoff.get("overlay")
    show_notice = getattr(overlay, "show_system_notice", None)
    if not callable(show_notice):
        return False
    try:
        return bool(
            show_notice(
                title=title,
                message=message,
            )
        )
    except Exception:
        _LOGGER.exception("renderer_system_notice_failed title=%s", title)
        return False


def _show_renderer_recovery_notice(overlay_handoff: dict[str, object]) -> bool:
    return _show_renderer_system_notice(
        overlay_handoff,
        title=RENDERER_RECOVERY_NOTICE_TITLE,
        message=RENDERER_RECOVERY_NOTICE_MESSAGE,
    )


def _wait_for_renderer_recovery_retry(
    args: argparse.Namespace,
    services: DaemonServices,
    current_loading: object | None,
) -> bool:
    """Show a bounded recovery action after automatic attach has failed."""
    if current_loading is not None:
        close = getattr(current_loading, "close", None)
        if callable(close):
            close()
    title = "Renderer HUD 恢复失败"
    message = "Codex 主窗口或 CDP 调试端口未就绪。保存好当前工作后，点击重试。"
    try:
        card = services.create_loading(args, title=title, message=message).start()
    except Exception as exc:
        services.append_diagnostic(
            "renderer_recovery_retry_unavailable",
            reason=str(exc),
        )
        return False
    try:
        offer = getattr(card, "offer_codex_restart", None)
        wait = getattr(card, "wait_for_codex_restart_request", None)
        if not callable(offer) or not bool(
            offer(title=title, message=message)
        ):
            services.append_diagnostic(
                "renderer_recovery_retry_unavailable",
                reason="loading feedback action unavailable",
            )
            return False
        if not callable(wait) or not bool(wait()):
            services.append_diagnostic(
                "renderer_recovery_retry_cancelled",
                reason="restart action was not requested",
            )
            return False
        services.append_diagnostic(
            "renderer_recovery_retry_requested",
            action_id=WORK_OVERLAY_RESTART_ACTION_ID,
        )
        return True
    finally:
        close = getattr(card, "close", None)
        if callable(close):
            close()


def _wait_for_session_unlock() -> None:
    """Block on the native unlock notification while the daemon stays alive."""
    if not sys.platform.startswith("win"):
        return
    unlocked = threading.Event()
    monitor = WindowsSessionLockMonitor(
        on_lock=lambda: None,
        on_unlock=unlocked.set,
    )
    monitor.start(initial_locked=True)
    try:
        unlocked.wait()
    finally:
        monitor.close()


def legacy_hud_session_unavailable(surface: str) -> int:
    _LOGGER.info("legacy_hud_session_unavailable surface=%s renderer_only=true", surface)
    return RENDERER_HUD_UNAVAILABLE


def run_tk_window_session(
    context: object,
    args: argparse.Namespace,
    *,
    daemon_manager: object | None = None,
    existing_window: object | None = None,
    close_context: bool = True,
    update_manager: object | None = None,
) -> int:
    del args, daemon_manager, existing_window, update_manager
    if close_context:
        context.close()
    return legacy_hud_session_unavailable("tk")


def run_qt_window_session(
    context: object,
    args: argparse.Namespace,
    *,
    daemon_manager: object | None = None,
    existing_window: object | None = None,
    close_context: bool = True,
    update_manager: object | None = None,
) -> int:
    del args, daemon_manager, existing_window, update_manager
    if close_context:
        context.close()
    return legacy_hud_session_unavailable("qt")


def run_qt_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    hide_until_attached: bool = True,
    daemon_manager: object | None = None,
    loading_feedback_instance: object | None = None,
) -> int:
    del args, lock_already_held, hide_until_attached, daemon_manager
    if loading_feedback_instance is not None:
        loading_feedback_instance.close()
    return legacy_hud_session_unavailable("qt")


def run_tk_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    hide_until_attached: bool = True,
    daemon_manager: object | None = None,
    loading_feedback_instance: object | None = None,
) -> int:
    del args, lock_already_held, hide_until_attached, daemon_manager
    if loading_feedback_instance is not None:
        loading_feedback_instance.close()
    return legacy_hud_session_unavailable("tk")


def run_daemon(
    args: argparse.Namespace,
    *,
    services: DaemonServices | None = None,
) -> int:
    """Run the hidden daemon until the renderer session exits."""
    services = services or _default_services()
    services.configure_logging()
    services.attach_logger()
    services.hide_console()
    manager = services.manager_factory(poll_ms=args.daemon_poll_ms)
    shutdown = ShutdownCoordinator()
    _configure_manager_shutdown(manager, shutdown)
    startup_decision = services.startup_decision or daemon_startup_decision
    renderer = services.run_renderer
    hud = services.run_hud
    if renderer is None:
        raise RuntimeError("daemon runtime requires a renderer session callback")
    if hud is None:
        def default_hud(current_args: argparse.Namespace, **kwargs: object) -> int:
            if "loading_feedback" in kwargs:
                kwargs["loading_feedback_instance"] = kwargs.pop("loading_feedback")
            return run_hud_session(
                current_args,
                run_renderer=renderer,
                restart_codex=services.restart_codex,
                **kwargs,
            )

        hud = default_hud
    restart = services.restart_codex or _default_restart_codex
    automatic_restart = services.automatic_restart_codex or restart
    overlay_handoff: dict[str, object] = {}
    tray_factory = services.create_tray or create_windows_tray
    try:
        with services.lock_factory(), _tray_lifetime(tray_factory, shutdown):
            try:
                startup = startup_decision(args, manager)
            except KeyboardInterrupt:
                return 130
            except ProcessListenerError as exc:
                _LOGGER.exception("daemon_listener_failed fallback=%s", exc)
                return hud(
                    args,
                    lock_already_held=True,
                    hide_until_attached=True,
                    overlay_handoff=overlay_handoff,
                    shutdown_coordinator=shutdown,
                )

            if shutdown.is_requested():
                return 0
            if startup.mode == DEFAULT_DAEMON_STARTUP_CANCEL:
                return 0
            startup_loading: Any | None = None
            launched_codex_for_renderer = False
            observed_codex_launch = False
            renderer_recovery_failures = 0
            seamless_recovery = False
            pending_codex_replacement_pid: int | None = None
            if startup.mode == DEFAULT_DAEMON_STARTUP_WAIT:
                startup_loading = services.create_loading(
                    args,
                    title="正在启动 Renderer HUD",
                    message="正在检查 Codex 的 CDP 连接…",
                ).start()
            if (
                startup.mode == DEFAULT_DAEMON_STARTUP_RENDERER
                and startup.launch_codex
            ):
                startup_loading = services.create_loading(
                    args,
                    title="正在启动 Renderer HUD",
                    message="正在以调试模式启动 Codex App...",
                ).start()
                try:
                    launch_port = (
                        services.select_launch_port() if services.select_launch_port else None
                    )
                except (OSError, RuntimeError) as exc:
                    startup_loading.close()
                    services.append_diagnostic(
                        "renderer_cdp_launch_failed",
                        reason=str(exc),
                        source="daemon-startup",
                    )
                    return RENDERER_HUD_UNAVAILABLE
                if not services.launch(debugger=True):
                    startup_loading.close()
                    services.append_diagnostic(
                        "renderer_cdp_launch_failed",
                        **({"port": launch_port} if launch_port is not None else {}),
                        source="daemon-startup",
                    )
                    return RENDERER_HUD_UNAVAILABLE
                launched_codex_for_renderer = True

            force_renderer_retry = startup.mode == DEFAULT_DAEMON_STARTUP_RENDERER
            while True:
                try:
                    if pending_codex_replacement_pid is not None:
                        if shutdown.is_requested():
                            return 0
                        replacement_ready = _wait_for_codex_after_explicit_restart(
                            manager,
                            pending_codex_replacement_pid,
                        )
                        pending_codex_replacement_pid = None
                        if shutdown.is_requested():
                            return 0
                        if not replacement_ready:
                            _LOGGER.warning(
                                "daemon_codex_replacement_not_ready"
                            )
                            _close_overlay_handoff(overlay_handoff)
                            return RENDERER_HUD_UNAVAILABLE
                    else:
                        manager.wait_for_codex()
                        if shutdown.is_requested():
                            return 0
                except KeyboardInterrupt:
                    return 130
                except ProcessListenerError as exc:
                    _LOGGER.exception("daemon_listener_failed fallback=%s", exc)
                    return hud(
                        args,
                        lock_already_held=True,
                        hide_until_attached=True,
                        overlay_handoff=overlay_handoff,
                        shutdown_coordinator=shutdown,
                    )
                if force_renderer_retry:
                    exit_code = renderer(
                        clone_args_with_renderer_preference(args, True),
                        lock_already_held=True,
                        daemon_manager=manager,
                        loading_feedback=startup_loading,
                        launched_codex=launched_codex_for_renderer,
                        observed_codex_launch=observed_codex_launch,
                        overlay_handoff=overlay_handoff,
                        seamless_recovery=seamless_recovery,
                        shutdown_coordinator=shutdown,
                    )
                else:
                    exit_code = hud(
                        clone_args_with_display_mode(args, "renderer"),
                        lock_already_held=True,
                        hide_until_attached=True,
                        daemon_manager=manager,
                        loading_feedback=startup_loading,
                        observed_codex_launch=observed_codex_launch,
                        overlay_handoff=overlay_handoff,
                        seamless_recovery=seamless_recovery,
                        shutdown_coordinator=shutdown,
                    )
                session_loading = startup_loading
                startup_loading = None
                observed_codex_launch = False
                if shutdown.is_requested():
                    _close_overlay_handoff(overlay_handoff)
                    return 0
                if exit_code == HUD_AUTO_RESTART_CODEX:
                    if session_loading is not None:
                        session_loading.close()
                    startup_loading = None
                    if _show_renderer_recovery_notice(overlay_handoff):
                        services.append_diagnostic(
                            "renderer_recovery_notice_shown",
                            notice_id="renderer-recovery-notice",
                        )
                    else:
                        services.append_diagnostic(
                            "renderer_recovery_notice_unavailable",
                            reason="overlay handoff unavailable",
                        )
                    seamless_recovery = True
                    previous_pid = _manager_primary_pid(manager)
                    if not automatic_restart():
                        if not _wait_for_renderer_recovery_retry(
                            args,
                            services,
                            session_loading,
                        ):
                            _close_overlay_handoff(overlay_handoff)
                            return RENDERER_HUD_UNAVAILABLE
                        startup_loading = services.create_loading(
                            args,
                            title="正在重启 Codex",
                            message="正在以调试/CDP 模式重启 Codex App，并重新尝试注入 HUD...",
                        ).start()
                        previous_pid = _manager_primary_pid(manager)
                        if not restart():
                            startup_loading.close()
                            _close_overlay_handoff(overlay_handoff)
                            return RENDERER_HUD_UNAVAILABLE
                        seamless_recovery = False
                    launched_codex_for_renderer = True
                    pending_codex_replacement_pid = previous_pid
                    force_renderer_retry = True
                    renderer_recovery_failures = 0
                    continue
                if exit_code == HUD_SUSPEND_FOR_SESSION_LOCK:
                    if session_loading is not None:
                        session_loading.close()
                    startup_loading = None
                    _close_overlay_handoff(overlay_handoff)
                    _LOGGER.info("daemon_renderer_suspended_for_session_lock")
                    try:
                        _wait_for_session_unlock()
                    except KeyboardInterrupt:
                        return 130
                    _LOGGER.info("daemon_session_unlock_detected renderer_restart=scheduled")
                    # The renderer may have wedged during the long lock (blank
                    # Codex UI). Keep the recovery retry path armed so a failed
                    # re-attach escalates to a Codex restart instead of the
                    # daemon silently giving up and leaving the blank window.
                    force_renderer_retry = True
                    launched_codex_for_renderer = False
                    observed_codex_launch = False
                    renderer_recovery_failures = 0
                    seamless_recovery = False
                    pending_codex_replacement_pid = None
                    continue
                if exit_code == HUD_SWITCH_TO_RENDERER_RESTART_CODEX:
                    seamless_recovery = False
                    if _show_renderer_system_notice(
                        overlay_handoff,
                        title=RENDERER_RESTART_NOTICE_TITLE,
                        message=RENDERER_RESTART_NOTICE_MESSAGE,
                    ):
                        startup_loading = None
                    else:
                        startup_loading = services.create_loading(
                            args,
                            title=RENDERER_RESTART_NOTICE_TITLE,
                            message="正在以调试/CDP 模式重启 Codex App，并重新尝试注入 HUD...",
                        ).start()
                    # This branch now serves only user-initiated restarts
                    # (the restart card / settings command). Renderer-hung
                    # escalation no longer requests a Codex restart: it
                    # degrades the HUD instead (white-screen cure, 2026-09-17).
                    previous_pid = _manager_primary_pid(manager)
                    if not automatic_restart():
                        if startup_loading is not None:
                            startup_loading.close()
                        _close_overlay_handoff(overlay_handoff)
                        return RENDERER_HUD_UNAVAILABLE
                    launched_codex_for_renderer = True
                    pending_codex_replacement_pid = previous_pid
                    force_renderer_retry = True
                    renderer_recovery_failures = 0
                    continue
                if exit_code == DAEMON_RESTART_REQUESTED:
                    launched_codex_for_renderer = False
                    renderer_recovery_failures = 0
                    # The renderer session has already been running when this
                    # watchdog signal is emitted. A replacement Codex must be
                    # classified as an observed launch so a plain (non-CDP)
                    # process is stopped and relaunched with CDP automatically.
                    # Initial startup of an already-running Codex never emits
                    # this signal and keeps its explicit confirmation flow.
                    observed_codex_launch = True
                    continue
                if force_renderer_retry and exit_code == RENDERER_HUD_UNAVAILABLE:
                    renderer_recovery_failures += 1
                    if renderer_recovery_failures >= DAEMON_RENDERER_RECOVERY_FAILURE_LIMIT:
                        if not _wait_for_renderer_recovery_retry(
                            args,
                            services,
                            session_loading,
                        ):
                            _close_overlay_handoff(overlay_handoff)
                            return RENDERER_HUD_UNAVAILABLE
                        startup_loading = services.create_loading(
                            args,
                            title="正在重启 Codex",
                            message="正在以调试/CDP 模式重启 Codex App，并重新尝试注入 HUD...",
                        ).start()
                        previous_pid = _manager_primary_pid(manager)
                        if not restart():
                            startup_loading.close()
                            _close_overlay_handoff(overlay_handoff)
                            return RENDERER_HUD_UNAVAILABLE
                        seamless_recovery = False
                        launched_codex_for_renderer = True
                        pending_codex_replacement_pid = previous_pid
                        renderer_recovery_failures = 0
                    if shutdown.wait(manager.poll_seconds):
                        return 0
                    continue
                _close_overlay_handoff(overlay_handoff)
                return exit_code
    except HudAlreadyRunningError as exc:
        print(f"codex-usage-hud: {exc}")
        return 2


__all__ = [
    "DAEMON_RESTART_REQUESTED", "DEFAULT_DAEMON_STARTUP_CANCEL",
    "DEFAULT_DAEMON_STARTUP_RENDERER", "DEFAULT_DAEMON_STARTUP_WAIT",
    "HUD_AUTO_RESTART_CODEX", "HUD_SUSPEND_FOR_SESSION_LOCK",
    "HUD_SWITCH_TO_RENDERER",
    "HUD_SWITCH_TO_RENDERER_RESTART_CODEX", "RENDERER_HUD_UNAVAILABLE",
    "DaemonServices", "DaemonStartupDecision", "clone_args_with_display_mode",
    "clone_args_with_renderer_preference", "daemon_startup_decision",
    "legacy_hud_session_unavailable", "run_daemon", "run_hud_session",
    "run_qt_hud_session", "run_qt_window_session", "run_tk_hud_session",
    "run_tk_window_session",
]
