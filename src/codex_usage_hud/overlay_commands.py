"""Desktop-overlay command routing with explicit activation ports."""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
import uuid

from .codex_app_runtime import (
    activate_running_codex_app,
    codex_processes_running,
    launch_codex_app,
    prepare_codex_window_for_renderer,
)
from .platforms import (
    CdpSessionSwitchBackend,
    CodexWindowTracker,
    SessionSwitchController,
    SessionSwitchResult,
    WindowsSearchSessionSwitchBackend,
)
from .platforms.base import BasePlatform
from .platforms.cdp_probe import (
    cdp_port_from_env, list_targets, pick_page_target,
    runtime_evaluate_params, send_cdp_command,
)
from . import overlay_window


NATIVE_SEARCH_SESSION_SWITCH_ENV = "CODEX_USAGE_HUD_NATIVE_SEARCH_SWITCH"
WORK_OVERLAY_CDP_SWITCH_TIMEOUT_SECONDS = 3.0
WORK_OVERLAY_WINDOW_PREPARE_TIMEOUT_SECONDS = 15.0
WORK_OVERLAY_UI_READY_TIMEOUT_SECONDS = 15.0
WORK_OVERLAY_SWITCH_REFOCUS_TIMEOUT_SECONDS = 0.8
WORK_OVERLAY_SWITCH_REFOCUS_DELAY_SECONDS = 0.0

_LOGGER = logging.getLogger("codex_usage_hud.overlay_commands")
WindowAction = Callable[[], tuple[bool, str, str, int]]


def _running_cdp_target() -> dict[str, Any] | None:
    try:
        return pick_page_target(list_targets(cdp_port_from_env(), 0.2))
    except Exception:
        return None


def _open_running_codex_ui() -> bool:
    """Reopen a tray instance through its own main process without shell discovery."""
    target = _running_cdp_target()
    if target is not None:
        try:
            response = send_cdp_command(
                str(target["webSocketDebuggerUrl"]), "Runtime.evaluate",
                runtime_evaluate_params("""(async () => {
                  const bridge = window.electronBridge;
                  if (!bridge?.sendMessageFromView) return false;
                  const row = document.querySelector('[data-app-action-sidebar-thread-active="true"]');
                  const ref = row?.getAttribute('data-app-action-sidebar-thread-id') || '';
                  const match = ref.match(/^(local|chatgpt|remote):(.+)$/);
                  // A provisional new-thread ID is not a routable chat.
                  // Use the platform reopen action until it is persisted.
                  if (!match || !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(match[2])) return false;
                  const prefix = match?.[1] === 'chatgpt' ? 'c' : match?.[1];
                  const path = `/${prefix}/${encodeURIComponent(match[2])}?hostId=${match[1]}`;
                  await bridge.sendMessageFromView({type: 'open-in-main-window', path});
                  return true;
                })()""", await_promise=True), 0.5,
            )
            if response.get("result", {}).get("result", {}).get("value") is True:
                return True
        except Exception:
            pass
    return activate_running_codex_app()


def _prepare_codex_window_for_standalone(
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.25,
    launch_if_missing: bool = False,
) -> tuple[bool, str, str, int]:
    return overlay_window.prepare_codex_window_for_standalone(
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        launch_if_missing=launch_if_missing,
        prepare_window_for_renderer=prepare_codex_window_for_renderer,
        tracker_factory=lambda: CodexWindowTracker(enable_uia=False),
        processes_running=lambda: _running_cdp_target() is not None or codex_processes_running(),
        activate=_open_running_codex_ui,
        launch=launch_codex_app,
        monotonic=time.monotonic,
        sleep=time.sleep,
    )


@dataclass(frozen=True, slots=True)
class OverlayRuntimeCommandCallbacks:
    """Route overlay-only background and reminder commands to their owners."""

    background_runtime: object | None
    rest_reminder: object | None
    work_overlay: object
    enqueue_renderer_command: Callable[[dict[str, object]], None]
    request_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex

    def handle_background(self, command: dict[str, object]) -> bool:
        action = str(command.get("action") or "").strip()
        event_id = str(command.get("eventId") or "").strip()
        if self.background_runtime is None or not event_id:
            return False
        if action == "dismissBackgroundUsage":
            confirm = getattr(self.background_runtime, "confirm", None)
            return bool(callable(confirm) and confirm(event_id))
        if action == "openBackgroundUsage":
            request_id = f"background-overlay-{self.request_id_factory()}"
            self.enqueue_renderer_command(
                {
                    "id": request_id,
                    "requestId": request_id,
                    "action": "openBackgroundUsage",
                    "eventId": event_id,
                }
            )
            return True
        return False

    def handle_rest_reminder(self, command: dict[str, object]) -> bool:
        presenter = self.rest_reminder
        if presenter is None:
            return False
        action = str(command.get("action") or "").strip()
        if action == "restReminderAck":
            presenter.acknowledge()
            ok = True
        elif action == "restReminderPostpone":
            ok = bool(presenter.postpone())
        elif action == "restReminderStart":
            ok = bool(presenter.start_rest())
        elif action == "restReminderFinish":
            ok = bool(presenter.finish_rest())
        elif action == "restReminderCredit":
            ok = bool(presenter.credit_early_rest(command.get("minutes")))
        else:
            return False
        self.work_overlay.update_rest_reminder(
            presenter.desktop_bubble_payload()
        )
        return ok

def _handle_work_overlay_command(
    command: Mapping[str, object],
    session_controller: object,
    *,
    prepare_window: bool = True,
    activation_meta: dict[str, object] | None = None,
    backend_names: tuple[str, ...] | None = None,
    prepare_window_callback: WindowAction | None = None,
    refocus_window_callback: WindowAction | None = None,
) -> Any | None:
    action = str(command.get("action") or "").strip()
    if action != "activateSession":
        return None
    if str(command.get("clientKind") or "").strip().lower() == "cli":
        from .platforms.cli_session_switch import CliSessionNavigator
        return CliSessionNavigator().activate(str(command.get("sessionId") or ""),
                                             str(command.get("workdir") or ""))
    session_id = str(command.get("sessionId") or "").strip()
    target_title = str(command.get("targetTitle") or command.get("title") or "").strip()
    if not session_id and not target_title:
        _LOGGER.info("work_overlay_command_ignored reason=missing_target")
        return None

    def activate_session() -> Any:
        workdir = str(command.get("workdir") or "").strip()
        if backend_names is None:
            return session_controller.activate_session(
                session_id=session_id,
                title=target_title,
                workdir=workdir,
            )
        return session_controller.activate_session(
            session_id=session_id,
            title=target_title,
            workdir=workdir,
            backend_names=backend_names,
        )

    window_prepared = False
    if prepare_window and prepare_window_callback is not None:
        window_ready, window_status, window_reason, window_hwnd = prepare_window_callback()
        window_prepared = True
        if activation_meta is not None:
            activation_meta.update(windowPrepared=True, windowReady=window_ready,
                                   windowStatus=window_status, windowHwnd=window_hwnd)
        if not window_ready:
            return SessionSwitchResult(
                ok=False, status="window-unavailable", requested_session_id=session_id,
                requested_title=target_title, message=window_reason or window_status,
            )
    # Window creation precedes renderer readiness on a cold start. Retry only
    # readiness/transport failures; a missing thread is a terminal result.
    deadline = time.monotonic() + WORK_OVERLAY_UI_READY_TIMEOUT_SECONDS
    result = activate_session()
    while (
        window_prepared and not result.ok
        and result.status in {"cdp-error", "backend-error", "no-backend",
                              "desktop-bridge-unavailable", "desktop-response-timeout",
                              "navigation-unverified", "sidebar-unavailable"}
        and time.monotonic() < deadline
    ):
        time.sleep(0.05)
        result = activate_session()
    if activation_meta is not None:
        activation_meta["windowPrepared"] = window_prepared
    _LOGGER.info(
        "work_overlay_command_processed ok=%s status=%s backend=%s requested_session=%s active_session=%s matched_by=%s message=%s",
        result.ok,
        result.status,
        result.backend or "-",
        result.requested_session_id or "-",
        result.active_session_id or "-",
        result.matched_by or "-",
        result.message or "-",
    )
    if prepare_window and result.ok and refocus_window_callback is not None:
        window_ready, window_status, window_reason, window_hwnd = refocus_window_callback()
        if activation_meta is not None:
            activation_meta.update(windowRefocused=window_ready, windowStatus=window_status,
                                   windowHwnd=window_hwnd)
        _LOGGER.info(
            "work_overlay_command_session_refocus ok=%s status=%s hwnd=%s reason=%s",
            window_ready,
            window_status,
            window_hwnd,
            window_reason or "-",
        )
        if not window_ready:
            return SessionSwitchResult(
                ok=False, status="window-not-foreground", backend=result.backend,
                requested_session_id=session_id, requested_title=target_title,
                active_session_id=result.active_session_id, active_title=result.active_title,
                matched_by=result.matched_by, message=window_reason or window_status,
            )
    return result


def _handle_work_overlay_commands(
    work_overlay: object,
    session_controller: object,
    *,
    prepare_window: bool = True,
    runtime_events: object | None = None,
    runtime_errors: object | None = None,
    background_command_callback: Callable[[dict[str, object]], bool] | None = None,
    rest_reminder_command_callback: Callable[[dict[str, object]], bool] | None = None,
    prepare_window_callback: WindowAction | None = None,
    refocus_window_callback: WindowAction | None = None,
    background_refocus_callback: WindowAction | None = None,
    clock: Callable[[], float] = time.time,
) -> int:
    take_commands = getattr(work_overlay, "take_commands", None)
    if not callable(take_commands):
        return 0
    handled = 0
    for command in take_commands():
        ack_status = "rejected"
        ack_result: dict[str, object] = {}
        try:
            ack_status, ack_result = _route_work_overlay_command(
                command,
                work_overlay,
                session_controller,
                prepare_window=prepare_window,
                runtime_events=runtime_events,
                runtime_errors=runtime_errors,
                background_command_callback=background_command_callback,
                rest_reminder_command_callback=rest_reminder_command_callback,
                prepare_window_callback=prepare_window_callback,
                refocus_window_callback=refocus_window_callback,
                background_refocus_callback=background_refocus_callback,
                clock=clock,
            )
        except Exception as exc:
            ack_status = "error"
            ack_result = {"message": str(exc)}
            raise
        finally:
            acknowledge = getattr(work_overlay, "acknowledge_command", None)
            if callable(acknowledge):
                acknowledge(command, status=ack_status, result=ack_result)
        handled += 1
    return handled


def _route_work_overlay_command(
    command: Mapping[str, object],
    work_overlay: object,
    session_controller: object,
    *,
    prepare_window: bool,
    runtime_events: object | None,
    runtime_errors: object | None,
    background_command_callback: Callable[[dict[str, object]], bool] | None,
    rest_reminder_command_callback: Callable[[dict[str, object]], bool] | None,
    prepare_window_callback: WindowAction | None,
    refocus_window_callback: WindowAction | None,
    background_refocus_callback: WindowAction | None,
    clock: Callable[[], float],
) -> tuple[str, dict[str, object]]:
    if _handle_work_overlay_runtime_error_command(command, runtime_events, runtime_errors):
        return "completed", {"handled": True}
    action = str(command.get("action") or "").strip()
    if action in {
        "restReminderAck",
        "restReminderPostpone",
        "restReminderStart",
        "restReminderFinish",
        "restReminderCredit",
    }:
        callback_handled = rest_reminder_command_callback is not None
        callback_ok = bool(rest_reminder_command_callback(dict(command))) if rest_reminder_command_callback else False
        meta = {"handled": callback_handled, "ok": callback_ok, "restReminder": True}
        _publish_work_overlay_command_event(runtime_events, command, None, activation_context=meta, clock=clock)
        return ("completed" if callback_ok else "rejected"), meta
    if action in {"dismissBackgroundUsage", "openBackgroundUsage"}:
        callback_handled = background_command_callback is not None
        callback_ok = bool(background_command_callback(dict(command))) if background_command_callback else False
        meta: dict[str, object] = {"handled": callback_handled, "ok": callback_ok}
        if action == "openBackgroundUsage":
            meta["backgroundCommandQueued"] = callback_ok
            if callback_ok and prepare_window:
                try:
                    ready, status, reason, hwnd = background_refocus_callback() if background_refocus_callback else (False, "unavailable", "", 0)
                except Exception as exc:
                    ready, status, reason, hwnd = False, "refocus-error", str(exc), 0
                meta.update({"windowRefocused": ready, "windowStatus": status, "windowReason": reason, "windowHwnd": hwnd})
        _publish_work_overlay_command_event(runtime_events, command, None, activation_context=meta, clock=clock)
        return ("completed" if callback_ok else "rejected"), meta
    activation_meta: dict[str, object] = {}
    result = _handle_work_overlay_command(
        command,
        session_controller,
        prepare_window=prepare_window,
        activation_meta=activation_meta,
        prepare_window_callback=prepare_window_callback,
        refocus_window_callback=refocus_window_callback,
    )
    _publish_work_overlay_command_event(runtime_events, command, result, activation_context=activation_meta, clock=clock)
    is_cli = str(command.get("clientKind") or "").lower() == "cli"
    if is_cli and result is not None and not result.ok:
        show_notice = getattr(work_overlay, "show_system_notice", None)
        if callable(show_notice):
            show_notice(title="CLI 会话跳转", dismissible=True,
                        message=result.message or "未能定位此 CLI 的终端。",
                        related_request_id=str(command.get("requestId") or ""))
    elif is_cli and result is not None and result.ok:
        notice = getattr(work_overlay, "_system_notice", None)
        if isinstance(notice, Mapping) and notice.get("relatedRequestId"):
            clear_notice = getattr(work_overlay, "clear_system_notice", None)
            if callable(clear_notice):
                clear_notice()
    if result is not None and result.ok:
        _publish_work_overlay_active_session_changed(runtime_events, command, result, activation_context=activation_meta, clock=clock)
    if result is not None and result.ok:
        mark_completed = getattr(work_overlay, "mark_switch_completed", None)
        if callable(mark_completed):
            mark_completed(command)
    meta = {**activation_meta, "handled": result is not None, "ok": bool(getattr(result, "ok", False)),
            "status": str(getattr(result, "status", "")), "message": str(getattr(result, "message", ""))}
    return ("completed" if meta["ok"] else "rejected"), meta


def _handle_work_overlay_runtime_error_command(
    command: Mapping[str, object],
    runtime_events: object | None,
    runtime_errors: object | None = None,
) -> bool:
    action = str(command.get("action") or "").strip()
    if action != "runtimeError":
        return False
    context_value = command.get("context")
    context = dict(context_value) if isinstance(context_value, Mapping) else {}
    severity = str(command.get("severity") or "error").strip() or "error"
    code = str(command.get("code") or "helper_error").strip() or "helper_error"
    message = str(command.get("message") or "Desktop work overlay helper error.").strip()
    source = str(command.get("source") or "work_overlay_helper").strip()
    if runtime_errors is not None:
        if runtime_errors.event_bus is None and runtime_events is not None:
            runtime_errors.event_bus = runtime_events
        runtime_errors.record(
            source=source,
            severity=severity,
            code=code,
            message=message,
            context=context,
        )
        return True
    publish = getattr(runtime_events, "publish", None)
    if not callable(publish):
        return True
    publish(
        "runtime_error",
        source=source,
        session=str(command.get("sessionId") or "") or None,
        context=context,
        error={
            "source": source,
            "severity": severity,
            "code": code,
            "message": message,
            "context": context,
        },
    )
    return True


def _publish_work_overlay_command_event(
    runtime_events: object | None,
    command: Mapping[str, object],
    result: Any | None,
    *,
    activation_context: Mapping[str, object] | None = None,
    clock: Callable[[], float] = time.time,
) -> None:
    publish = getattr(runtime_events, "publish", None)
    if not callable(publish):
        return
    context = _work_overlay_activation_context(command, result, clock=clock)
    context.update(dict(activation_context or {}))
    publish(
        "overlay_command_received",
        source="work_overlay",
        session=str(command.get("sessionId") or "") or None,
        context=context,
    )


def _work_overlay_activation_context(
    command: Mapping[str, object],
    result: Any | None,
    *,
    clock: Callable[[], float] = time.time,
) -> dict[str, object]:
    """Project one structured local activation result for runtime events."""
    context: dict[str, object] = {
        "action": str(command.get("action") or ""),
        "sessionId": str(command.get("sessionId") or ""),
        "requestedSessionId": str(command.get("sessionId") or ""),
        "activeSessionId": str(getattr(result, "active_session_id", "") or ""),
        "requestedTitle": str(
            command.get("targetTitle") or command.get("title") or ""
        ),
        "activeTitle": str(getattr(result, "active_title", "") or ""),
        "current": bool(command.get("current")),
        "handled": result is not None,
        "ok": bool(getattr(result, "ok", False)) if result is not None else False,
        "backend": str(getattr(result, "backend", "") or "") if result is not None else "",
        "status": str(getattr(result, "status", "") or "") if result is not None else "",
        "matchedBy": str(getattr(result, "matched_by", "") or "") if result is not None else "",
        "message": str(getattr(result, "message", "") or "") if result is not None else "",
    }
    requested_at = command.get("requestedAt")
    try:
        requested_timestamp = float(requested_at)
    except (TypeError, ValueError):
        requested_timestamp = 0.0
    if requested_timestamp > 0:
        context["latencyMs"] = round(
            max(0.0, (clock() - requested_timestamp) * 1000.0),
            1,
        )
    return context


def _publish_work_overlay_active_session_changed(
    runtime_events: object | None,
    command: Mapping[str, object],
    result: Any,
    *,
    activation_context: Mapping[str, object] | None = None,
    clock: Callable[[], float] = time.time,
) -> None:
    publish = getattr(runtime_events, "publish", None)
    if not callable(publish):
        return
    context = _work_overlay_activation_context(command, result, clock=clock)
    context.update(dict(activation_context or {}))
    publish(
        "active_session_changed",
        source="work_overlay",
        session=(
            str(result.active_session_id or "").strip()
            or str(command.get("sessionId") or "").strip()
            or None
        ),
        context={"reason": "overlay_session_activation", **context},
    )


handle_command = _handle_work_overlay_command
handle_commands = _handle_work_overlay_commands
activation_context = _work_overlay_activation_context


def _build_session_switch_controller(
    platform: BasePlatform,
    *,
    prefer_native_search: bool,
    cdp_port: int | None = None,
) -> SessionSwitchController:
    cdp = CdpSessionSwitchBackend(
        timeout_seconds=WORK_OVERLAY_CDP_SWITCH_TIMEOUT_SECONDS,
        port=cdp_port,
    )
    native_setting = os.environ.get(NATIVE_SEARCH_SESSION_SWITCH_ENV, "").strip().lower()
    native_enabled = native_setting not in {"0", "false", "no", "off"}
    backends: list[object] = [cdp]
    if native_enabled:
        native = WindowsSearchSessionSwitchBackend(platform)
        backends = [native, cdp] if prefer_native_search else [cdp, native]
    return SessionSwitchController(backends)


def _prepare_codex_window_for_work_overlay_switch() -> tuple[bool, str, str, int]:
    return overlay_window.prepare_codex_window_for_work_overlay_switch(
        platform=sys.platform,
        launch_codex_app=launch_codex_app,
        prepare_standalone=_prepare_codex_window_for_standalone,
        timeout_seconds=WORK_OVERLAY_WINDOW_PREPARE_TIMEOUT_SECONDS,
        poll_seconds=0.08,
        launch_if_missing=True,
    )


def _refocus_codex_window_after_work_overlay_switch() -> tuple[bool, str, str, int]:
    return overlay_window.refocus_codex_window_after_work_overlay_switch(
        platform=sys.platform,
        launch_codex_app=launch_codex_app,
        prepare_standalone=_prepare_codex_window_for_standalone,
        sleep=time.sleep,
        delay_seconds=WORK_OVERLAY_SWITCH_REFOCUS_DELAY_SECONDS,
        timeout_seconds=WORK_OVERLAY_SWITCH_REFOCUS_TIMEOUT_SECONDS,
        poll_seconds=0.08,
        launch_if_missing=True,
    )


def _refocus_codex_window_after_current_session_click() -> tuple[bool, str, str, int]:
    return overlay_window.refocus_codex_window_after_current_session_click(
        _refocus_codex_window_after_work_overlay_switch
    )

__all__ = [
    "OverlayRuntimeCommandCallbacks",
    "activation_context",
    "handle_command",
    "handle_commands",
]
