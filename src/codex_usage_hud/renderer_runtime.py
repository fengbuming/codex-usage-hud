"""Lifecycle assembly for one renderer HUD runtime session."""

from __future__ import annotations

from .renderer_session_lifecycle import (
    RendererSessionLoopControls,
    RendererSessionResources,
)
import argparse
from collections.abc import Callable
from contextlib import nullcontext
import logging
import sys
import time
from functools import partial
from threading import Event
from . import __version__
from . import codex_app_runtime as _codex_app_runtime_owner
from . import renderer_runtime_assembly
from . import renderer_startup as _renderer_startup_owner
from . import runtime_diagnostics as _runtime_diagnostics_owner
from . import runtime_paths as _runtime_paths_owner
from .core import ParsedSession
from .daemon import CodexDaemonManager, ProcessListenerError
from .desktop_overlay import DesktopWorkOverlay
from . import loading_feedback
from .overlay_command_pump import WorkOverlayCommandPump
from .platforms import get_current_platform
from . import runtime_context as runtime_context_owner
from . import renderer_file_events
from . import renderer_connection
from .renderer_event_loop import (
    RendererEventLoop,
    RendererLoopExecutorPorts,
    RendererLoopState,
    RendererRefreshExecutor,
    RendererRefreshPorts,
    RendererTickInputs,
    RendererTickSampler,
    RendererTickSamplerPorts,
)
from .renderer_wait import RendererWaitPlanner, RendererWaitPorts
from .renderer_runtime_policies import (  # noqa: F401
    AUTO_RENDERER_TIMEOUT_FAILURE_LIMIT,
    RENDERER_ACTIVE_WORK_RESCAN_SECONDS,
    RENDERER_IDLE_POLL_MS,
    RENDERER_UPDATE_FAILURE_LIMIT,
    _json_signature,
    _path_stat_signature,
    _paths_only_current_session,
    _renderer_active_session_observation_should_refresh,
    _renderer_budget_refresh_paths,
    _renderer_budget_signature,
    _renderer_budget_window_keys,
    _renderer_deferred_active_work_refresh_due,
    _renderer_event_idle_wait_enabled,
    _renderer_initial_failure_can_be_fixed_by_restart,
    _renderer_initial_failure_should_recover_cdp_port,
    _renderer_refresh_delay_seconds,
    _renderer_runtime_signature,
    _renderer_should_refresh_active_work_items,
    _renderer_should_refresh_budget_aggregate,
    _renderer_should_use_visible_first_active_session,
    _renderer_snapshot_selection_is_stale,
    _renderer_update_failure_limit,
    _remote_debugging_ports_from_command_line,
    _valid_renderer_cdp_port,
)
from .renderer_pre_refresh import (
    RendererPreRefreshExecutor,
    RendererPreRefreshPorts,
)
from .session_activity import WindowsSessionLockMonitor
from . import active_work
from .active_work import RendererActiveWorkPump
from . import overlay_projection
from .runtime_ports import RuntimeServices
from .runtime_context import RuntimeContext
from .renderer_session_ports import RendererSessionPorts
from .settings_bridge import SettingsBridgeServer
from .ui.renderer_domains import RendererHudClient, payload_from_snapshot, remove_renderer_hud_from_pages, session_switch_payload_from_snapshot, wait_for_renderer
from .updater import AutoUpdateManager


_LOGGER = logging.getLogger(__name__)

RENDERER_STARTUP_NOTICE_TITLE = "正在启动 Renderer"
RENDERER_STARTUP_NOTICE_MESSAGE = "HUD 正在检查 Codex 的 CDP 连接，请稍候。"


def _ensure_loading_feedback_started(
    args: argparse.Namespace,
    card: object | None,
    *,
    title: str,
    message: str,
) -> object:
    if card is None:
        card = loading_feedback._create_loading_feedback(
            args,
            title=title,
            message=message,
        )
    start = getattr(card, "start", None)
    if callable(start):
        start()
    update = getattr(card, "update", None)
    if callable(update):
        update(title=title, message=message)
    return card


def _prepare_renderer_startup_feedback(
    args: argparse.Namespace,
    work_overlay: DesktopWorkOverlay,
    loading_card: object | None,
) -> object | None:
    """Prefer the PySide bubble for an already-running Codex startup check."""
    show_notice = getattr(work_overlay, "show_system_notice", None)
    if callable(show_notice):
        try:
            if bool(
                show_notice(
                    title=RENDERER_STARTUP_NOTICE_TITLE,
                    message=RENDERER_STARTUP_NOTICE_MESSAGE,
                )
            ):
                if loading_card is not None:
                    close = getattr(loading_card, "close", None)
                    if callable(close):
                        close()
                return None
        except Exception:
            _LOGGER.exception("renderer_startup_notice_failed")
    return _ensure_loading_feedback_started(
        args,
        loading_card,
        title="正在启动 Renderer HUD",
        message="正在检查 Codex 的 CDP 连接…",
    )


def _wait_for_renderer_restart_request(
    args: argparse.Namespace,
    work_overlay: DesktopWorkOverlay,
    loading_card: object | None,
) -> bool:
    title = "需要重启 Codex"
    message = "当前 Codex 未开启 HUD 所需的 CDP。保存好当前工作后，点击重启继续。"
    if work_overlay.offer_codex_restart(title=title, message=message):
        if loading_card is not None:
            loading_card.close()
            loading_card = None
        if work_overlay.wait_for_codex_restart_request():
            _LOGGER.info("renderer_restart_requested_by_user surface=work-overlay")
            return True
        fallback_reason = work_overlay.system_action_unavailable_reason
    else:
        fallback_reason = work_overlay.system_action_unavailable_reason
    _runtime_diagnostics_owner.append_renderer_diagnostic(
        "renderer_restart_overlay_fallback",
        reason=str(fallback_reason or "PySide6 desktop restart action unavailable"),
    )
    work_overlay.close()
    card = loading_card
    card = _ensure_loading_feedback_started(
        args,
        card,
        title="正在启动 Renderer HUD",
        message="正在检查 Codex 的 CDP 连接…",
    )
    if not card.offer_codex_restart(title=title, message=message):
        card.close()
        return False
    if not card.wait_for_codex_restart_request():
        card.close()
        return False
    card.close()
    _LOGGER.info("renderer_restart_requested_by_user surface=loading-card")
    return True


def _restart_codex_for_renderer() -> bool:
    if sys.platform.startswith("win") and not _codex_app_runtime_owner.stop_codex_app():
        return False
    if sys.platform == "darwin" and not _codex_app_runtime_owner.stop_macos_codex_app():
        return False
    try:
        port = _renderer_startup_owner.select_launch_cdp_port(require_fresh=True)
    except (OSError, RuntimeError) as exc:
        _runtime_diagnostics_owner.append_renderer_diagnostic(
            "renderer_cdp_launch_failed", reason=str(exc), source="restart"
        )
        return False
    _runtime_diagnostics_owner.append_renderer_diagnostic(
        "renderer_restart_requested_by_user",
        action_id="restart-codex-for-renderer",
        port=port,
    )
    return _codex_app_runtime_owner.launch_codex_app(
        debugger=True,
        cdp_port=port,
        on_debugger_launch=_renderer_startup_owner.remember_requested_cdp_port,
    )


class SystemRuntimeClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def time(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def production_runtime_services() -> RuntimeServices:
    from . import runtime_snapshot_service as snapshot_service_owner

    return RuntimeServices(
        clock=SystemRuntimeClock(),
        context_factory=runtime_context_owner.build_runtime_context,
        renderer_factory=lambda port, timeout: RendererHudClient(
            port=port, timeout_seconds=timeout
        ),
        overlay_factory=lambda context: DesktopWorkOverlay(
            item_limit=overlay_projection._work_overlay_item_limit_for_context(context),
            side=overlay_projection._work_overlay_side_for_context(context),
            runtime_dir=_runtime_paths_owner.hud_runtime_dir,
            diagnostic_sink=_runtime_diagnostics_owner.append_renderer_diagnostic,
        ),
        update_manager_factory=lambda: AutoUpdateManager(current_version=__version__),
        bridge_factory=SettingsBridgeServer,
        snapshot_builder=snapshot_service_owner.build_snapshot,
        command_pump_factory=WorkOverlayCommandPump,
        file_event_source_factory=lambda context, wake_event: (
            renderer_file_events.RendererFileEventSource(
                context,
                wake_event,
                diagnostic_setup=_runtime_diagnostics_owner.ensure_runtime_error_diagnostics,
            )
        ),
        active_work_pump_factory=lambda context, wake_event: RendererActiveWorkPump(
            context,
            wake_event,
            build_items=active_work.active_work_items_for_snapshot,
        ),
    )


def run_renderer_hud_session(args: argparse.Namespace, *, lock_already_held: bool = False, daemon_manager: CodexDaemonManager | None = None, launched_codex: bool = False, observed_codex_launch: bool = False, loading_feedback: object | None = None, overlay_handoff: dict[str, object] | None = None, seamless_recovery: bool = False, restart_codex: object | None = None, shutdown_coordinator: object | None = None, services: RuntimeServices | None = None, ports: RendererSessionPorts) -> int:
    """Run the in-renderer HUD over CDP, or report that it is unavailable."""
    if services is None:
        services = ports._production_runtime_services()
    shutdown_probe = getattr(shutdown_coordinator, "is_requested", None)
    if callable(shutdown_probe) and shutdown_probe():
        return 0
    lock_context = nullcontext() if lock_already_held else ports.HudInstanceLock()
    resources: RendererSessionResources | None = None
    session_exit_code: int | None = None
    session_lock_requested = Event()
    shutdown_unbind: Callable[[], object] | None = None
    try:
        with lock_context:
            local_loading = loading_feedback
            try:
                startup_plan = ports._renderer_startup_plan(launched_codex=launched_codex, observed_codex_launch=observed_codex_launch)
            except (OSError, RuntimeError) as exc:
                if local_loading is not None:
                    local_loading.close()
                ports._append_renderer_diagnostic('renderer_cdp_launch_failed', reason=str(exc), source='startup-classification')
                return ports.RENDERER_HUD_UNAVAILABLE
            if startup_plan.scenario == ports.RENDERER_STARTUP_RELAUNCH_OBSERVED:
                if local_loading is not None:
                    local_loading.close()
                ports._append_renderer_diagnostic('renderer_observed_plain_launch_takeover', reason=startup_plan.reason)
                return ports.HUD_AUTO_RESTART_CODEX
            codex_was_running = startup_plan.scenario in {ports.RENDERER_STARTUP_ATTACH, ports.RENDERER_STARTUP_RESTART_REQUIRED}
            if startup_plan.scenario == ports.RENDERER_STARTUP_LAUNCH:
                if local_loading is not None and not seamless_recovery:
                    local_loading.update(title='正在启动 Renderer HUD', message='正在以调试/CDP 模式启动 Codex App...')
                if not ports.launch_codex_app(debugger=True):
                    if local_loading is not None:
                        local_loading.close()
                    ports._append_renderer_diagnostic('renderer_cdp_launch_failed', port=startup_plan.port, source=startup_plan.port_source)
                    return ports.RENDERER_HUD_UNAVAILABLE
                launched_codex = True
            handoff_overlay = (
                overlay_handoff.pop("overlay", None)
                if overlay_handoff is not None
                else None
            )
            base = renderer_runtime_assembly.create_renderer_session_base(
                args,
                services=services,
                work_overlay=handoff_overlay,
            )
            resources = base.resources
            context = base.context
            display_mode = base.display_mode
            work_overlay = base.work_overlay

            if (
                local_loading is not None
                and startup_plan.scenario == ports.RENDERER_STARTUP_RESTART_REQUIRED
            ):
                local_loading = _prepare_renderer_startup_feedback(
                    args,
                    work_overlay,
                    local_loading,
                )

            def release_overlay_for_handoff() -> None:
                if overlay_handoff is None or session_lock_requested.is_set():
                    return
                retained_overlay = resources.release_overlay_for_handoff()
                if retained_overlay is not None:
                    overlay_handoff["overlay"] = retained_overlay

            if startup_plan.scenario == ports.RENDERER_STARTUP_RESTART_REQUIRED:
                try:
                    requested = ports._wait_for_renderer_restart_request(args, work_overlay, local_loading)
                    return ports.HUD_SWITCH_TO_RENDERER_RESTART_CODEX if requested else ports.RENDERER_HUD_UNAVAILABLE
                except KeyboardInterrupt:
                    if local_loading is not None:
                        local_loading.close()
                    return 130
                finally:
                    release_overlay_for_handoff()
                    resources.close()
            cold_start_attach = bool(launched_codex or startup_plan.scenario == ports.RENDERER_STARTUP_ATTACH_OBSERVED)
            renderer_cdp_timeout = ports.RENDERER_RESTART_CDP_TIMEOUT_SECONDS if cold_start_attach else ports.DAEMON_RENDERER_CDP_TIMEOUT_SECONDS if daemon_manager is not None and (not codex_was_running) else ports.RENDERER_CDP_TIMEOUT_SECONDS
            assembly = renderer_runtime_assembly.assemble_renderer_session(
                base,
                startup_plan=startup_plan,
                renderer_cdp_timeout=renderer_cdp_timeout,
                services=services,
                ports=ports,
            )
            update_manager = assembly.update_manager
            client = assembly.client
            restart_requested = assembly.restart_requested
            restart_codex_requested = assembly.restart_codex_requested
            exit_requested = assembly.exit_requested
            runtime_signals = assembly.runtime_signals
            command_refresh_requested = assembly.command_refresh_requested
            active_session_refresh_requested = assembly.active_session_refresh_requested
            command_pump: WorkOverlayCommandPump | None = None
            runtime_event_bus = assembly.runtime_event_bus
            runtime_event_publish = assembly.runtime_event_publish
            runtime_event_drain = assembly.runtime_event_drain
            connection_health = assembly.connection_health
            connection_managers = assembly.connection_managers
            background_usage_runtime = assembly.background_usage_runtime
            overlay_runtime_commands = assembly.overlay_runtime_commands
            bridge_url = assembly.bridge_url
            background_usage_bridge_url = assembly.background_usage_bridge_url
            session_index_url = assembly.session_index_url
            startup_feedback = assembly.startup_feedback
            snapshot_or_error = assembly.snapshot_or_error
            bridge_callbacks = assembly.resources.bridge_callbacks

            def request_external_shutdown() -> None:
                if exit_requested.is_set():
                    return
                exit_requested.set()
                command_refresh_requested.set()
                if local_loading is not None:
                    local_loading.close()
                ports._LOGGER.info("renderer_hud_tray_exit_requested")

            bind_shutdown = getattr(shutdown_coordinator, "bind", None)
            unbind_shutdown = getattr(shutdown_coordinator, "unbind", None)
            if callable(bind_shutdown):
                bind_shutdown(request_external_shutdown)
                if callable(unbind_shutdown):
                    def unbind_external_shutdown() -> None:
                        unbind_shutdown(request_external_shutdown)

                    shutdown_unbind = unbind_external_shutdown
            if exit_requested.is_set():
                return 0

            def request_session_lock_exit() -> None:
                if exit_requested.is_set():
                    return
                session_lock_requested.set()
                exit_requested.set()
                command_refresh_requested.set()
                ports._LOGGER.info("renderer_hud_session_lock_exit_requested")

            # Quiesce (session-lock silent) is the default: the HUD keeps
            # running but issues zero CDP traffic while the machine is locked
            # or asleep, so the Codex renderer can freeze/resume cleanly and is
            # never wedged by a pinned debug session. The opt-out setting
            # (stop_hud_on_lock_screen) still fully exits the HUD process.
            quiesce_event = Event()

            def on_session_lock() -> None:
                if bool(getattr(context.user_config, "stop_hud_on_lock_screen", False)):
                    request_session_lock_exit()
                    return
                quiesce_event.set()
                client.quiesce()
                ports._LOGGER.info("renderer_hud_quiesced_for_session_lock")

            def on_session_unlock() -> None:
                if bool(getattr(context.user_config, "stop_hud_on_lock_screen", False)):
                    return  # 兜底模式：守护进程负责重启 HUD
                client.resume()
                quiesce_event.clear()
                reminder = getattr(context, "rest_reminder", None)
                if reminder is not None:
                    try:
                        reminder.rearm_after_session_unlock()
                    except Exception:
                        ports._LOGGER.debug(
                            "renderer_hud_rest_reminder_rearm_failed",
                            exc_info=True,
                        )
                manager = connection_managers.get("manager") if isinstance(connection_managers, dict) else None
                note = getattr(manager, "note_session_resumed", None)
                if callable(note):
                    try:
                        note()
                    except Exception:
                        ports._LOGGER.debug("renderer_hud_resume_note_failed", exc_info=True)
                # No unconditional refresh here: resume() puts the client in
                # the warmup gate, so the first post-unlock ticks only probe
                # (1+1) until the thawing renderer acks three times under
                # 100ms. Forcing a refresh now would push a full ~1MB payload
                # into the renderer at its most fragile moment.
                ports._LOGGER.info("renderer_hud_resumed_for_session_unlock")

            session_lock_monitor = WindowsSessionLockMonitor(
                on_lock=on_session_lock,
                on_unlock=on_session_unlock,
            )
            resources.session_lock_monitor = session_lock_monitor
            context.session_lock_monitor = session_lock_monitor
            session_lock_monitor.set_enabled(True)
            if session_lock_requested.is_set():
                return ports.HUD_SUSPEND_FOR_SESSION_LOCK
            try:
                wait_for_window = cold_start_attach or (sys.platform.startswith('win') and (not codex_was_running))
                launch_if_missing = False
                if local_loading is not None and not seamless_recovery:
                    local_loading.update(title='正在切换到 Renderer HUD' if cold_start_attach else '正在启动 Renderer HUD', message='正在拉起 Codex 主窗口并切到前台，确保 Renderer 注入目标正确...')
                window_prepared, window_status, window_reason, window_hwnd = ports._prepare_codex_window_for_renderer(timeout_seconds=ports.RENDERER_WINDOW_PREPARE_TIMEOUT_SECONDS, launch_if_missing=launch_if_missing)
                if not window_prepared:
                    ports._LOGGER.info('renderer_hud_window_prepare_best_effort_failed status=%s hwnd=%s reason=%s', window_status, window_hwnd, window_reason)
                if wait_for_window:
                    if local_loading is not None and not seamless_recovery:
                        local_loading.update(title='正在切换到 Renderer HUD' if cold_start_attach else '正在启动 Renderer HUD', message='正在等待 Codex 主窗口和调试端口准备完成...')
                    window_ready, window_status, window_reason, window_hwnd = ports._wait_for_visible_codex_window(timeout_seconds=ports.DAEMON_RENDERER_WINDOW_READY_TIMEOUT_SECONDS)
                    if not window_ready:
                        if local_loading is not None:
                            local_loading.close()
                        ports._LOGGER.info('renderer_hud_window_not_ready status=%s hwnd=%s reason=%s', window_status, window_hwnd, window_reason)
                        ports._append_renderer_diagnostic('window_not_ready', status=window_status, reason=window_reason, hwnd=window_hwnd, display_mode=display_mode, daemon_mode=True, window_ready_timeout_seconds=ports.DAEMON_RENDERER_WINDOW_READY_TIMEOUT_SECONDS)
                        return ports.RENDERER_HUD_UNAVAILABLE
                initial_timeout = ports.RENDERER_RESTART_INITIAL_TIMEOUT_SECONDS if cold_start_attach else ports.DAEMON_RENDERER_INITIAL_TIMEOUT_SECONDS if wait_for_window else ports.RENDERER_INITIAL_TIMEOUT_SECONDS
                if local_loading is not None and not seamless_recovery:
                    local_loading.update(
                        title='正在切换到 Renderer HUD' if cold_start_attach else '正在启动 Renderer HUD',
                        message=(
                            '正在等待 Codex 完成启动并准备 HUD 注入...'
                            if cold_start_attach
                            else '正在把 HUD 注入 Codex 界面，通常只需 1 到 3 秒...'
                        ),
                    )
                renderer_startup_visible = False
                # A visible Desktop splash window is not an injectable Codex
                # renderer. Keep the desktop loading bubble alive until a real
                # HUD payload is acknowledged; sending startup payloads here
                # would otherwise consume the normal client retry gate.
                if not cold_start_attach and not seamless_recovery:
                    renderer_startup_visible = startup_feedback.update(step='第 1 步，共 4 步', detail='已连接 Codex，正在准备 HUD…', progress=18)
                if renderer_startup_visible and local_loading is not None:
                    local_loading.close()
                    local_loading = None
                initial_bootstrapped = False
                if renderer_startup_visible:
                    initial_bootstrapped = startup_feedback.bootstrap(step='第 1 步，共 4 步', detail='已连接 Codex，正在准备 HUD…', progress=18)
                if renderer_startup_visible or initial_bootstrapped:
                    if getattr(client, 'enabled', False) is True:
                        time.sleep(ports.RENDERER_STARTUP_STEP_MIN_VISIBLE_SECONDS)
                    startup_feedback.update(step='第 2 步，共 4 步', detail='正在建立安全的 HUD 通道…', progress=35)
                initial_wait_kwargs: dict[str, object] = {'timeout_seconds': initial_timeout}
                if cold_start_attach:
                    initial_wait_kwargs['retry_until_ready'] = True
                elif renderer_startup_visible or initial_bootstrapped:
                    initial_wait_kwargs['progress_callback'] = startup_feedback.progress
                renderer_attached = False
                # The native loading bubble only reports startup progress; it
                # is not an attach acknowledgement. Existing Codex + valid CDP
                # must still perform the real Renderer probe before the
                # loading/overlay resources are handed off or closed.
                renderer_attached = wait_for_renderer(client, snapshot_or_error, **initial_wait_kwargs)
                if session_lock_requested.is_set():
                    return ports.HUD_SUSPEND_FOR_SESSION_LOCK
                if not renderer_attached:
                    original_error = client.last_error
                    restart_can_help = bool(startup_plan.scenario in {ports.RENDERER_STARTUP_ATTACH, ports.RENDERER_STARTUP_ATTACH_OBSERVED} and (ports._renderer_initial_failure_should_recover_cdp_port(original_error) or ports._renderer_initial_failure_can_be_fixed_by_restart(original_error)))
                    if restart_can_help:
                        current_port = ports._valid_renderer_cdp_port(getattr(client, 'port', startup_plan.port))
                        if current_port is not None:
                            target_still_valid, _reason = ports._validate_renderer_cdp_candidate(ports._RendererCdpPortCandidate(port=current_port, source=startup_plan.port_source or 'startup-plan'))
                            restart_can_help = not target_still_valid
                    if restart_can_help:
                        ports._append_renderer_diagnostic('initial_connect_restart_waiting_for_user', status=client.last_status, error=original_error, old_port=getattr(client, 'port', None), display_mode=display_mode, daemon_mode=daemon_manager is not None)
                        if ports._wait_for_renderer_restart_request(args, work_overlay, local_loading):
                            return ports.HUD_SWITCH_TO_RENDERER_RESTART_CODEX
                        ports._LOGGER.info('renderer_cdp_port_restart_card_unavailable')
                    if local_loading is not None:
                        local_loading.close()
                    ports._LOGGER.info('renderer_hud_initial_connect_failed status=%s error=%s', client.last_status, client.last_error)
                    ports._append_renderer_diagnostic('initial_connect_failed', status=client.last_status, error=client.last_error, display_mode=display_mode, daemon_mode=daemon_manager is not None, initial_timeout_seconds=initial_timeout, cdp_timeout_seconds=getattr(client, 'timeout_seconds', None), cold_start_attach=cold_start_attach, renderer_attach_attempts=getattr(client, 'last_attach_metrics', {}).get('attempts'))
                    return ports.RENDERER_HUD_UNAVAILABLE
                else:
                    ports._remember_successful_renderer_cdp_port(getattr(client, 'port', None))
                    clear_notice = getattr(work_overlay, 'clear_system_notice', None)
                    if callable(clear_notice):
                        clear_notice()
                    clear_action = getattr(work_overlay, 'clear_system_action', None)
                    if callable(clear_action):
                        clear_action()
                usage_insights_worker = getattr(context, 'usage_insights_worker', None)
                request_usage_refresh = getattr(usage_insights_worker, 'request_refresh', None)
                if callable(request_usage_refresh):
                    request_usage_refresh(request_id='startup')
                session_controller = ports._build_session_switch_controller(getattr(context, 'platform', get_current_platform()), prefer_native_search=False, cdp_port=getattr(client, 'port', None))
                command_pump_factory = services.command_pump_factory or WorkOverlayCommandPump
                file_event_source_factory = services.file_event_source_factory or ports._RendererFileEventSource
                active_work_pump_factory = services.active_work_pump_factory or (lambda context, wake_event: RendererActiveWorkPump(context, wake_event, build_items=ports.active_work_items_for_snapshot))
                handle_overlay_commands = partial(ports._handle_work_overlay_commands, work_overlay, session_controller, prepare_window=True, runtime_events=getattr(context, 'runtime_events', None), runtime_errors=getattr(context, 'runtime_errors', None), background_command_callback=overlay_runtime_commands.handle_background, rest_reminder_command_callback=overlay_runtime_commands.handle_rest_reminder)
                command_pump = command_pump_factory(work_overlay, handle_overlay_commands, command_event=command_refresh_requested)
                resources.command_pump = command_pump
                file_events = file_event_source_factory(context, command_refresh_requested)
                resources.file_events = file_events
                active_work_pump = active_work_pump_factory(context, command_refresh_requested)
                resources.active_work_pump = active_work_pump
                if local_loading is not None:
                    local_loading.close()
                command_pump.start()
                loop_state = RendererLoopState()
                loop_controls = RendererSessionLoopControls(state=loop_state, monotonic=services.clock.monotonic, response_pending=ports._has_pending_background_usage_response, response_retry_delay=ports._background_usage_response_retry_delay_seconds, exit_event=exit_requested, restart_event=restart_requested, overlay=work_overlay, daemon_restart_result=ports.DAEMON_RESTART_REQUESTED if daemon_manager is not None else 0, restart_codex_event=restart_codex_requested, restart_codex_result=ports.HUD_SWITCH_TO_RENDERER_RESTART_CODEX, daemon_manager=daemon_manager, daemon_failure_exception=ProcessListenerError, unavailable_result=ports.RENDERER_HUD_UNAVAILABLE)
                def escalate_renderer_hung(reason: str) -> None:
                    if restart_codex_requested.is_set():
                        return
                    ports._append_renderer_diagnostic(
                        'renderer_hung_escalation',
                        reason=reason,
                        status=str(client.last_status),
                        error=str(client.last_error),
                    )
                    ports._LOGGER.warning(
                        'renderer_hung_escalation_requested reason=%s',
                        reason,
                    )
                    # White-screen cure: never auto-restart Codex from a hung
                    # renderer. Degrade instead -- remove the HUD injection so
                    # Codex owns the main thread and can self-heal, then the
                    # client probes at a low cadence and re-attaches on ack.
                    try:
                        client.degrade(reason)
                    except Exception:
                        ports._LOGGER.exception('renderer_hung_degrade_failed')
                    try:
                        show_notice = getattr(work_overlay, 'show_system_notice', None)
                        if callable(show_notice):
                            show_notice(
                                title="Codex 界面无响应",
                                message="HUD 已暂停注入，让 Codex 自行恢复；若长时间白屏，请手动重启 Codex。",
                            )
                    except Exception:
                        ports._LOGGER.debug('renderer_hung_notice_failed', exc_info=True)
                    command_refresh_requested.set()

                connection_manager = renderer_connection.RendererConnectionManager(client=client, tracker_provider=lambda: getattr(context, 'active_session_tracker', None), wake=command_refresh_requested.set, schedule_soft_reinstall=loop_controls.schedule_soft_reinstall, debug_enabled=ports._runtime_debug_enabled, runtime_errors=lambda: ports._runtime_errors_payload_for_context(context), health=connection_health, escalate_renderer_hung=escalate_renderer_hung)
                connection_managers['manager'] = connection_manager
                loop_controls.connection_manager = connection_manager
                publish_rest_reminder = getattr(work_overlay, 'update_rest_reminder', None)
                if not callable(publish_rest_reminder):

                    def _noop_rest_reminder(_payload: dict[str, object]) -> None:
                        return None
                    publish_rest_reminder = _noop_rest_reminder
                tick_sampler = RendererTickSampler(loop_state, RendererTickSamplerPorts(monotonic=services.clock.monotonic, wall_time=services.clock.time, take_active_work=active_work_pump.take_latest, tracker=lambda: getattr(context, 'active_session_tracker', None), stabilize_active_work=lambda items: list(ports._stabilize_published_work_overlay_items(context, items)), publish_active_work=lambda items: work_overlay.update(ports._work_overlay_items_with_background_usage(context, items)), update_state=lambda: update_manager.tick().to_dict(), update_state_signature=ports._json_signature, rest_reminder=lambda: getattr(context, 'rest_reminder', None), publish_rest_reminder=publish_rest_reminder, publish_event=runtime_event_publish if callable(runtime_event_publish) else None, current_session=lambda: loop_controls.current_session(ports._session_path_key), budget_window_keys=lambda: ports._renderer_budget_window_keys(context), bridge_wake_event=command_refresh_requested, active_session_wake_event=active_session_refresh_requested, take_file_changes=file_events.take_changes, invalidate_mapping=lambda: ports._invalidate_active_session_mapping_cache(context), take_command=runtime_signals.take_command, drain_events=runtime_event_drain if callable(runtime_event_drain) else lambda: [], event_bus=runtime_event_bus, path_key=ports._session_path_key, background_response_pending=ports._has_pending_background_usage_response, refresh_current_work=lambda items, fresh: list(ports._refresh_visible_current_work_item(context, items, fresh))))
                def request_usage_insights_refresh() -> None:
                    worker = getattr(context, "usage_insights_worker", None)
                    request = getattr(worker, "request_refresh", None)
                    if callable(request):
                        request(request_id="budget-window")

                pre_refresh_executor = RendererPreRefreshExecutor(loop_state, RendererPreRefreshPorts(current_config=lambda: context.user_config, execute_command=lambda command: ports._handle_renderer_settings_command(command, context, restart_requested, exit_requested, update_manager, work_overlay, session_controller, restart_codex_requested.set), update_status=lambda: update_manager.status().to_dict(), reset_background_retry=loop_controls.reset_background_retry, renderer_only_status=ports._renderer_settings_status, partial_domains_for_command=lambda command, previous, current: ports._partial_domains_for_settings_command(command, previous_config=previous, current_config=current), request_usage_insights_refresh=request_usage_insights_refresh, refresh_latest_snapshot=lambda command, snapshot, previous, current: ports._refresh_latest_snapshot_for_partial_settings_command(command, snapshot=snapshot, context=context, previous_config=previous, current_config=current), refresh_usage_insights=lambda: ports._refresh_usage_insights_payload(context), overlay_configure=lambda: work_overlay.configure(item_limit=ports._work_overlay_item_limit_for_context(context), side=overlay_projection._work_overlay_side_for_context(context)), overlay_update=work_overlay.update, items_with_background_usage=lambda items: list(ports._work_overlay_items_with_background_usage(context, items)), settings_store=getattr(context, 'settings_store', None), apply_config=lambda config, mtime: ports._apply_user_config_to_runtime_context(context, config, mtime=mtime), changed_config_keys=ports._changed_user_config_keys, partial_domains_for_changes=ports._partial_domains_for_changed_user_config, wake=command_refresh_requested.set))
                resources.pre_refresh_executor = pre_refresh_executor

                def push_lightweight_snapshot(fresh: ParsedSession) -> bool:
                    update_payload = getattr(client, 'update_payload', None)
                    return bool(callable(update_payload) and update_payload(session_switch_payload_from_snapshot(fresh, settings_path=context.settings_store.path, background_usage_notification=ports._background_usage_notification_for_session(context, fresh.session_id), connection_health=connection_health)))

                def push_full_snapshot(fresh: ParsedSession, inputs: RendererTickInputs) -> bool:
                    rest_reminder = getattr(context, 'rest_reminder', None)
                    return bool(client.update(fresh, settings=context.user_config, active_display_mode='renderer', settings_path=context.settings_store.path, settings_bridge_url=bridge_url, background_usage_bridge_url=background_usage_bridge_url, session_index_url=session_index_url, session_index=dict(getattr(context, 'session_index_payload', {}) or {}), background_usage_revision=background_usage_runtime.store.revision() if background_usage_runtime is not None else 0, background_usage_notification=ports._background_usage_notification_for_session(context, fresh.session_id), rest_reminder=rest_reminder.renderer_payload() if rest_reminder is not None else {'visible': False}, settings_command_status=loop_state.settings_command_status, update_state=inputs.update_state, debug=ports._runtime_debug_enabled(), runtime_errors=ports._runtime_errors_payload_for_context(context), work_overlay_selectable_max=ports._work_overlay_screen_max_items(), desktop_overlay_dependency=ports._desktop_overlay_dependency_status(), provider_registry=ports._provider_registry_payload(context), app_provider=str(getattr(context, 'app_provider', '') or ''), default_provider=str(getattr(getattr(context, 'provider_registry', None), 'app_provider', '') or ''), usage_insights=dict(getattr(context, 'usage_insights_payload', {}) or {}), session_cleanup=dict(getattr(context, 'session_cleanup_payload', {}) or {}), connection_health=connection_health, request_rows_limit=loop_state.request_rows_limit))

                def publish_snapshot_overlay(fresh: ParsedSession) -> None:
                    stable_items = ports._stabilize_published_work_overlay_items(context, fresh.active_work_items)
                    fresh.active_work_items = list(stable_items)
                    work_overlay.configure(item_limit=ports._work_overlay_item_limit_for_context(context), side=overlay_projection._work_overlay_side_for_context(context))
                    work_overlay.update(ports._work_overlay_items_with_background_usage(context, stable_items))
                    file_events.update_session_path(fresh.session_path)
                refresh_executor = RendererRefreshExecutor(loop_state, RendererRefreshPorts(monotonic=services.clock.monotonic, wall_time=services.clock.time, perf_counter=time.perf_counter, budget_signature=lambda: ports._renderer_budget_signature(context), build_snapshot=lambda kwargs: snapshot_or_error(**kwargs), selection_is_stale=lambda fresh: ports._renderer_snapshot_selection_is_stale(fresh, getattr(context, 'active_session_tracker', None)), current_selection_seq=lambda: int(getattr(getattr(context, 'active_session_tracker', None), 'selection_seq', 0) or 0), refresh_current_work=lambda items, fresh: list(ports._refresh_visible_current_work_item(context, items, fresh)), request_active_work=lambda fresh, paths: active_work_pump.request(fresh, fresh.session_path, paths), update_snapshot_activity=lambda fresh: ports._update_session_cleanup_activity(context, fresh), push_lightweight=push_lightweight_snapshot, push_full=push_full_snapshot, build_domain_payload=lambda snapshot, inputs: build_domain_payload(snapshot, inputs), push_domain_payload=lambda payload: bool(callable(getattr(client, 'update_payload', None)) and client.update_payload(payload)), publish_overlay=publish_snapshot_overlay, update_metrics=lambda: dict(getattr(client, 'last_update_metrics', {}) or {}), connection_success=lambda: connection_health.note_success('update-ok'), connection_failure=lambda: connection_health.note_failure('update-failed'), sync_connection=connection_manager.sync_follow, wake=command_refresh_requested.set, background_response_pending=ports._has_pending_background_usage_response, reset_background_retry=loop_controls.reset_background_retry, schedule_background_retry=loop_controls.schedule_background_retry, resolve_update_failure=lambda: ports._resolve_cdp_update_failure(context), record_update_failure=lambda failures: ports._record_cdp_update_failure(context, client, failures=failures), client_status=lambda: str(client.last_status), client_error=lambda: str(client.last_error), path_key=ports._session_path_key, active_work_rescan_seconds=ports.RENDERER_ACTIVE_WORK_RESCAN_SECONDS, active_work_after_session_seconds=ports.RENDERER_ACTIVE_WORK_AFTER_SESSION_DELAY_SECONDS, slow_operation_ms=ports.RENDERER_SLOW_OPERATION_LOG_MS, capture_active_session_observation=bridge_callbacks.capture_active_session_observation, acknowledge_active_session_update=lambda selection_seq, observation_key, succeeded: bridge_callbacks.complete_active_session_update(selection_seq, succeeded=succeeded, observation_key=observation_key), retry_active_session_update=bridge_callbacks.retry_active_session_update))

                def session_cleanup_payload_for_refresh(inputs: RendererTickInputs) -> dict[str, object]:
                    full = dict(getattr(context, 'session_cleanup_payload', {}) or {})
                    if 'sessionCleanup' in inputs.event_refresh_request.domains:
                        delta = getattr(context, 'session_cleanup_delta', {})
                        if isinstance(delta, dict) and delta:
                            return dict(delta)
                    return full

                def build_domain_payload(snapshot: ParsedSession, inputs: RendererTickInputs) -> dict[str, object]:
                    rest_reminder = getattr(context, 'rest_reminder', None)
                    return payload_from_snapshot(snapshot, settings=context.user_config, active_display_mode='renderer', settings_path=context.settings_store.path, settings_bridge_url=bridge_url, background_usage_bridge_url=background_usage_bridge_url, session_index_url=session_index_url, session_index=dict(getattr(context, 'session_index_payload', {}) or {}), background_usage_revision=background_usage_runtime.store.revision() if background_usage_runtime is not None else 0, background_usage_notification=ports._background_usage_notification_for_session(context, snapshot.session_id), rest_reminder=rest_reminder.renderer_payload() if rest_reminder is not None else {'visible': False}, settings_command_status=loop_state.settings_command_status, theme=inputs.event_refresh_request.theme_payload, update_state=inputs.update_state, debug=ports._runtime_debug_enabled(), runtime_errors=ports._runtime_errors_payload_for_context(context), work_overlay_selectable_max=ports._work_overlay_screen_max_items(), desktop_overlay_dependency=ports._desktop_overlay_dependency_status(), provider_registry=ports._provider_registry_payload(context), app_provider=str(getattr(context, 'app_provider', '') or ''), default_provider=str(getattr(getattr(context, 'provider_registry', None), 'app_provider', '') or ''), usage_insights=dict(getattr(context, 'usage_insights_payload', {}) or {}), session_cleanup=session_cleanup_payload_for_refresh(inputs), connection_health=connection_health, request_rows_limit=loop_state.request_rows_limit).to_domain_json(*sorted(inputs.event_refresh_request.domains))
                connection_manager.enable_light_push()
                wait_planner = RendererWaitPlanner(loop_state, RendererWaitPorts(monotonic=services.clock.monotonic, base_delay=lambda snapshot, elapsed, force_fast: ports._renderer_refresh_delay_seconds(context, snapshot, elapsed, force_fast=force_fast), idle_wait_enabled=lambda snapshot, update_state, delay, force_fast: ports._renderer_event_idle_wait_enabled(file_events, snapshot, update_state, delay, force_fast=force_fast), reminder_in=lambda: getattr(getattr(context, 'rest_reminder', None), 'seconds_until_wake', lambda: None)(), keepalive_in=lambda: getattr(work_overlay, 'next_keep_alive_seconds', lambda: None)(), daemon_at=lambda: loop_state.next_daemon_check_at if daemon_manager is not None else None, failure_limit=lambda: ports._renderer_update_failure_limit(display_mode, client.last_error), background_response_pending=ports._has_pending_background_usage_response, probe_in=connection_health.seconds_until_probe, heal_in=connection_health.seconds_until_heal, idle_wait_seconds=ports.RENDERER_EVENT_IDLE_WAIT_SECONDS))
                def _quiesce_wait(delay: float) -> object:
                    # The quiesce branch never samples (and therefore never
                    # clears) the shared wake event. Clear it before waiting so
                    # a stale "set" state cannot busy-spin the whole lock; a new
                    # set (unlock, settings, exit) still wakes the wait early.
                    command_refresh_requested.clear()
                    return command_refresh_requested.wait(delay)

                def _event_wait(delay: float) -> object:
                    # The sampler clears this shared latch at the beginning of
                    # the next iteration. Clearing it here would lose a wake
                    # that arrives between the current iteration and this
                    # wait, leaving active-session/command updates asleep
                    # until an unrelated deadline.
                    return command_refresh_requested.wait(delay)

                event_loop = RendererEventLoop(loop_state, RendererLoopExecutorPorts(sample_inputs=tick_sampler.sample, apply_inputs=pre_refresh_executor.apply, exit_requested=loop_controls.exit_requested, restart_requested=loop_controls.restart_requested, restart_result=loop_controls.restart_result, daemon_tick=loop_controls.daemon_tick, compute_force_fast=lambda inputs: bool(loop_state.latest_snapshot is None or inputs.event_refresh_request.force_fast), apply_refresh=refresh_executor.apply, current_snapshot=lambda: loop_state.latest_snapshot, apply_domain_update=refresh_executor.apply_domains, keep_alive=loop_controls.keep_overlay_alive, after_iteration=loop_controls.after_iteration, compute_wait_delay=wait_planner.compute, wait=_event_wait, update_gate=lambda: getattr(client, 'update_gate_state', lambda: (True, '', 0.0))(), record_refresh_merge=lambda: getattr(client, 'record_renderer_metric', lambda *_args: None)('merged_refreshes'), quiesce_active=quiesce_event.is_set, quiesce_wait_delay=lambda: 5.0, quiesce_wait=_quiesce_wait, apply_local_refresh=refresh_executor.apply_local))
                session_exit_code = event_loop.run()
                if session_lock_requested.is_set():
                    return ports.HUD_SUSPEND_FOR_SESSION_LOCK
                return session_exit_code
            except KeyboardInterrupt:
                if local_loading is not None:
                    local_loading.close()
                return 130
            finally:
                if exit_requested.is_set() and not session_lock_requested.is_set():
                    try:
                        remove_renderer_hud_from_pages(port=startup_plan.port)
                    except Exception:
                        ports._LOGGER.debug('renderer_hud_exit_cleanup_failed', exc_info=True)
                release_overlay_for_handoff()
                resources.close(
                    remove_renderer=not session_lock_requested.is_set()
                )
    except ports.HudAlreadyRunningError as exc:
        ports._eprint(f'codex-usage-hud: {exc}')
        return 2
    finally:
        if shutdown_unbind is not None:
            try:
                shutdown_unbind()
            except Exception:
                ports._LOGGER.debug('renderer_hud_shutdown_unbind_failed', exc_info=True)
            shutdown_unbind = None
        if resources is not None:
            if overlay_handoff is not None and not session_lock_requested.is_set():
                retained_overlay = resources.release_overlay_for_handoff()
                if retained_overlay is not None:
                    overlay_handoff["overlay"] = retained_overlay
            resources.close(
                remove_renderer=not session_lock_requested.is_set()
            )
def _refresh_renderer_cdp_dependents(context: object) -> None:
    platform = getattr(context, "platform", None)
    refresh = getattr(platform, "refresh_cdp_probe", None)
    if callable(refresh):
        try:
            refresh()
        except Exception as exc:
            _LOGGER.info("renderer_cdp_probe_refresh_failed error=%s", exc)


def _invalidate_active_session_mapping_cache(context: "RuntimeContext") -> None:
    tracker = getattr(context, "active_session_tracker", None)
    invalidate = getattr(tracker, "invalidate_mapping_cache", None)
    if callable(invalidate):
        invalidate()
