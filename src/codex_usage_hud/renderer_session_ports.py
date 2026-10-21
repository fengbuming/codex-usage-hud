"""Explicit composition ports for one renderer HUD session.

The port contract is deliberately data-only. Resource construction and
runtime side effects stay in the composition root and lifecycle owners.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Mapping


@dataclass(frozen=True, slots=True)
class RendererSessionPorts:
    """Callbacks and constants injected into one renderer session."""

    DAEMON_RENDERER_CDP_TIMEOUT_SECONDS: object
    DAEMON_RENDERER_INITIAL_TIMEOUT_SECONDS: object
    DAEMON_RENDERER_WINDOW_READY_TIMEOUT_SECONDS: object
    DAEMON_RESTART_REQUESTED: object
    HUD_AUTO_RESTART_CODEX: object
    HUD_SUSPEND_FOR_SESSION_LOCK: object
    HUD_SWITCH_TO_RENDERER_RESTART_CODEX: object
    HudAlreadyRunningError: object
    HudInstanceLock: object
    HudLoadingFeedback: object
    RENDERER_ACTIVE_SESSION_BOOTSTRAP_WAIT_SECONDS: object
    RENDERER_ACTIVE_WORK_AFTER_SESSION_DELAY_SECONDS: object
    RENDERER_ACTIVE_WORK_RESCAN_SECONDS: object
    RENDERER_CDP_TIMEOUT_SECONDS: object
    RENDERER_EVENT_IDLE_WAIT_SECONDS: object
    RENDERER_HUD_UNAVAILABLE: object
    RENDERER_INITIAL_TIMEOUT_SECONDS: object
    RENDERER_RESTART_CDP_TIMEOUT_SECONDS: object
    RENDERER_RESTART_INITIAL_TIMEOUT_SECONDS: object
    RENDERER_SLOW_OPERATION_LOG_MS: object
    RENDERER_STARTUP_ATTACH: object
    RENDERER_STARTUP_ATTACH_OBSERVED: object
    RENDERER_STARTUP_LAUNCH: object
    RENDERER_STARTUP_RELAUNCH_OBSERVED: object
    RENDERER_STARTUP_UPDATE_RESTARTED: object
    RENDERER_STARTUP_RESTART_REQUIRED: object
    RENDERER_STARTUP_STEP_MIN_VISIBLE_SECONDS: object
    RENDERER_WINDOW_PREPARE_TIMEOUT_SECONDS: object
    _LOGGER: object
    _RendererCdpPortCandidate: object
    _RendererFileEventSource: object
    _append_renderer_diagnostic: object
    _apply_user_config_to_runtime_context: object
    _background_usage_notification_for_session: object
    _background_usage_response_retry_delay_seconds: object
    _build_session_switch_controller: object
    _changed_user_config_keys: object
    _desktop_overlay_dependency_status: object
    _eprint: object
    _handle_renderer_settings_command: object
    _handle_work_overlay_commands: object
    _has_pending_background_usage_response: object
    _invalidate_active_session_mapping_cache: object
    _json_signature: object
    _partial_domains_for_changed_user_config: object
    _partial_domains_for_settings_command: object
    _prepare_codex_window_for_renderer: object
    _production_runtime_services: object
    _provider_registry_payload: object
    _record_cdp_update_failure: object
    _refresh_latest_snapshot_for_partial_settings_command: object
    _refresh_usage_insights_payload: object
    _refresh_visible_current_work_item: object
    _remember_successful_renderer_cdp_port: object
    _renderer_budget_signature: object
    _renderer_budget_window_keys: object
    _renderer_event_idle_wait_enabled: object
    _renderer_initial_failure_can_be_fixed_by_restart: object
    _renderer_initial_failure_should_recover_cdp_port: object
    _renderer_refresh_delay_seconds: object
    _renderer_settings_status: object
    _renderer_snapshot_selection_is_stale: object
    _renderer_startup_plan: object
    _renderer_update_failure_limit: object
    _resolve_cdp_update_failure: object
    _runtime_debug_enabled: object
    _runtime_errors_payload_for_context: object
    _session_path_key: object
    _stabilize_published_work_overlay_items: object
    _update_session_cleanup_activity: object
    _valid_renderer_cdp_port: object
    _validate_renderer_cdp_candidate: object
    _wait_for_renderer_restart_request: object
    _wait_for_visible_codex_window: object
    _work_overlay_item_limit_for_context: object
    _work_overlay_items_with_background_usage: object
    _work_overlay_screen_max_items: object
    active_work_items_for_snapshot: object
    launch_codex_app: object

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "RendererSessionPorts":
        """Build ports and fail early when composition bindings are missing."""
        missing = [field.name for field in fields(cls) if field.name not in values]
        if missing:
            raise RuntimeError(
                "renderer session composition missing ports: " + ", ".join(missing)
            )
        return cls(**{field.name: values[field.name] for field in fields(cls)})


__all__ = ["RendererSessionPorts"]
