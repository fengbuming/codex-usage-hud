"""Bootstrap and one-shot resource assembly for a renderer session.

This owner constructs the resources that form one renderer session.  Startup
classification, window/CDP attachment, event reduction, refresh execution,
and shutdown remain in the runtime composition root and their dedicated
owners.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event

from . import (
    overlay_commands,
    renderer_bridge,
    renderer_connection,
    runtime_policies,
    snapshot_builder as snapshot_builder_owner,
)
from .config import normalize_display_mode
from .core.connection_health import ConnectionHealth
from .renderer_session_lifecycle import (
    RendererSessionResources,
    RendererStartupFeedback,
)
from .renderer_session_ports import RendererSessionPorts
from .runtime_ports import RuntimeServices


@dataclass(slots=True)
class RendererSessionBase:
    """Context and overlay resources created before renderer attachment."""

    resources: RendererSessionResources
    context: object
    work_overlay: object
    display_mode: str


@dataclass(slots=True)
class RendererSessionAssembly:
    """Resources and runtime adapters required by the renderer loop."""

    base: RendererSessionBase
    update_manager: object
    client: object
    restart_requested: Event
    restart_codex_requested: Event
    exit_requested: Event
    runtime_signals: runtime_policies.RendererRuntimeSignals
    command_refresh_requested: Event
    active_session_refresh_requested: Event
    runtime_event_bus: object | None
    runtime_event_publish: Callable[..., object] | None
    runtime_event_drain: Callable[[], list[object]]
    connection_health: ConnectionHealth
    connection_managers: dict[str, renderer_connection.RendererConnectionManager]
    background_usage_runtime: object | None
    overlay_runtime_commands: overlay_commands.OverlayRuntimeCommandCallbacks
    bridge_url: object
    background_usage_bridge_url: str
    session_index_url: str
    startup_feedback: RendererStartupFeedback
    snapshot_or_error: snapshot_builder_owner.RuntimeSnapshotBuilder

    @property
    def resources(self) -> RendererSessionResources:
        return self.base.resources

    @property
    def context(self) -> object:
        return self.base.context

    @property
    def work_overlay(self) -> object:
        return self.base.work_overlay

    @property
    def display_mode(self) -> str:
        return self.base.display_mode


def create_renderer_session_base(
    args: object,
    *,
    services: RuntimeServices,
    work_overlay: object | None = None,
) -> RendererSessionBase:
    """Create context and overlay while retaining partial-close ownership."""

    context = services.context_factory(args)
    resources = RendererSessionResources(context=context)
    try:
        display_mode = normalize_display_mode(
            getattr(args, "hud_mode", None)
            or context.user_config.display_mode
        )
        if work_overlay is None:
            work_overlay = services.overlay_factory(context)
        resources.overlay = work_overlay
    except BaseException:
        resources.close()
        raise
    return RendererSessionBase(
        resources=resources,
        context=context,
        work_overlay=work_overlay,
        display_mode=display_mode,
    )


def assemble_renderer_session(
    base: RendererSessionBase,
    *,
    startup_plan: object,
    renderer_cdp_timeout: float,
    services: RuntimeServices,
    ports: RendererSessionPorts,
) -> RendererSessionAssembly:
    """Construct renderer/client/bridge adapters in dependency order.

    The caller already owns ``base.resources``.  Every resource is registered
    immediately after construction so a later failure is closed by the root's
    normal reverse-order lifecycle.
    """

    resources = base.resources
    context = base.context
    work_overlay = base.work_overlay

    update_manager = services.update_manager_factory()
    resources.update_manager = update_manager
    client = services.renderer_factory(
        getattr(startup_plan, "port"), renderer_cdp_timeout
    )
    resources.client = client

    restart_requested = Event()
    restart_codex_requested = Event()
    exit_requested = Event()
    runtime_signals = runtime_policies.RendererRuntimeSignals()
    command_refresh_requested = runtime_signals.command_refresh
    active_session_refresh_requested = runtime_signals.active_session_refresh

    # AutoUpdateManager 的后台线程（检查更新/下载）完成后，主动唤醒事件循环，
    # 否则 payload 不会刷新，前端 loading 弹窗会永远停留在 checking 状态。
    update_manager._on_state_change = lambda _state: command_refresh_requested.set()

    pre_send_estimator = getattr(context, "pre_send_estimator", None)
    if pre_send_estimator is not None:
        pre_send_estimator.update_callback = (
            lambda _estimate: runtime_signals.request_active_session_refresh()
        )

    runtime_event_bus = getattr(context, "runtime_events", None)
    runtime_event_subscribe = getattr(runtime_event_bus, "subscribe", None)
    runtime_event_publish = getattr(runtime_event_bus, "publish", None)
    runtime_event_drain = getattr(runtime_event_bus, "drain", None)
    if not callable(runtime_event_drain):
        def _empty_runtime_event_drain() -> list[object]:
            return []

        runtime_event_drain = _empty_runtime_event_drain
    if callable(runtime_event_subscribe):
        resources.runtime_event_unsubscribe = runtime_event_subscribe(
            runtime_signals.wake_for_runtime_event
        )

    connection_health = ConnectionHealth()
    connection_health.note_success("ok")
    connection_managers: dict[
        str, renderer_connection.RendererConnectionManager
    ] = {}

    def request_connection_health_light() -> None:
        manager = connection_managers.get("manager")
        if manager is not None:
            manager.request_light()
            return
        command_refresh_requested.set()

    bridge_callbacks = renderer_bridge.RendererBridgeCallbacks(
        signals=runtime_signals,
        active_session_tracker=getattr(context, "active_session_tracker", None),
        attachment_estimator=getattr(context, "pre_send_estimator", None),
        connection_health=connection_health,
        request_connection_health_light=request_connection_health_light,
        request_active_session_refresh=runtime_signals.request_active_session_refresh,
        publish_event=runtime_event_publish
        if callable(runtime_event_publish)
        else None,
    )
    resources.bridge_callbacks = bridge_callbacks
    bridge_callbacks.connect_tracker()
    bridge_callbacks.install(client)

    def request_renderer_restart() -> None:
        """Request a renderer restart and wake the event loop immediately."""
        restart_requested.set()
        command_refresh_requested.set()

    background_usage_runtime = getattr(context, "background_usage_runtime", None)
    overlay_runtime_commands = overlay_commands.OverlayRuntimeCommandCallbacks(
        background_runtime=background_usage_runtime,
        rest_reminder=getattr(context, "rest_reminder", None),
        work_overlay=work_overlay,
        enqueue_renderer_command=bridge_callbacks.enqueue_command,
    )
    bridge = services.bridge_factory(
        context.settings_store,
        restart_callback=request_renderer_restart,
        command_callback=bridge_callbacks.enqueue_command,
        active_session_callback=bridge_callbacks.observe_active_session,
        attachments_callback=bridge_callbacks.observe_attachments,
        background_usage_query_callback=(
            lambda filters: background_usage_runtime.query(**filters)
            if background_usage_runtime is not None
            else None
        ),
        background_usage_detail_callback=(
            background_usage_runtime.detail
            if background_usage_runtime is not None
            else None
        ),
        background_usage_confirm_callback=(
            background_usage_runtime.confirm
            if background_usage_runtime is not None
            else None
        ),
        background_usage_policy_query_callback=(
            getattr(background_usage_runtime, "policy_query", None)
        ),
        background_usage_policy_set_callback=(
            getattr(background_usage_runtime, "policy_set", None)
        ),
        session_index_status_callback=(
            (lambda: context.session_index_warm_job.status())
            if getattr(context, "session_index_warm_job", None) is not None
            else None
        ),
        session_index_control_callback=(
            (lambda body: context.session_index_warm_job.control(body))
            if getattr(context, "session_index_warm_job", None) is not None
            else None
        ),
    )
    resources.bridge = bridge
    # The HTTP listener starts before Renderer attachment. Source reload must
    # not mistake a live listener (or the old page's bridge URL) for a ready HUD.
    bridge.renderer_ready_callback = lambda: bool(
        getattr(client, "last_attach_metrics", {}).get("attached", False)
    )
    bridge_url = bridge.start()
    background_usage_bridge_url = (
        bridge.background_usage_url if background_usage_runtime is not None else ""
    )
    session_index_url = (
        bridge.session_index_url
        if getattr(context, "session_index_warm_job", None) is not None
        else ""
    )
    startup_feedback = RendererStartupFeedback(
        client,
        command_refresh_requested,
        bootstrap_wait_seconds=ports.RENDERER_ACTIVE_SESSION_BOOTSTRAP_WAIT_SECONDS,
    )
    snapshot_or_error = snapshot_builder_owner.RuntimeSnapshotBuilder(
        context=context,
        builder=services.snapshot_builder,
    )
    return RendererSessionAssembly(
        base=base,
        update_manager=update_manager,
        client=client,
        restart_requested=restart_requested,
        restart_codex_requested=restart_codex_requested,
        exit_requested=exit_requested,
        runtime_signals=runtime_signals,
        command_refresh_requested=command_refresh_requested,
        active_session_refresh_requested=active_session_refresh_requested,
        runtime_event_bus=runtime_event_bus,
        runtime_event_publish=runtime_event_publish
        if callable(runtime_event_publish)
        else None,
        runtime_event_drain=runtime_event_drain,
        connection_health=connection_health,
        connection_managers=connection_managers,
        background_usage_runtime=background_usage_runtime,
        overlay_runtime_commands=overlay_runtime_commands,
        bridge_url=bridge_url,
        background_usage_bridge_url=background_usage_bridge_url,
        session_index_url=session_index_url,
        startup_feedback=startup_feedback,
        snapshot_or_error=snapshot_or_error,
    )


__all__ = [
    "RendererSessionAssembly",
    "RendererSessionBase",
    "assemble_renderer_session",
    "create_renderer_session_base",
]
