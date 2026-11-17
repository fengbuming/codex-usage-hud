"""Legacy runtime symbol resolution kept outside the composition root."""

from __future__ import annotations

from dataclasses import fields
import importlib
from typing import Mapping

_OWNER_MODULES = (
    "active_work",
    "codex_app_runtime",
    "daemon_runtime",
    "desktop_overlay",
    "desktop_overlay_setup",
    "loading_feedback",
    "overlay_commands",
    "overlay_ipc",
    "overlay_projection",
    "overlay_runtime",
    "ui.renderer_domains",
    "renderer_file_events",
    "renderer_runtime_policies",
    "renderer_session_ports",
    "renderer_runtime",
    "renderer_startup",
    "runtime_commands",
    "runtime_config",
    "runtime_context",
    "runtime_diagnostics",
    "runtime_paths",
    "runtime_policies",
    "runtime_settings",
    "runtime_snapshot_service",
    "runtime_usage",
    "session_cleanup_runtime",
    "session_snapshots",
    "snapshot_builder",
    "usage_cache",
    "usage_insights",
    "platforms.cdp_probe",
)

_SPECIAL = {
    "CostEstimator": ("core.parser", "CostEstimator"),
    "RuntimeServices": ("runtime_ports", "RuntimeServices"),
    "UsageCalculator": ("core.calculator", "UsageCalculator"),
    "build_runtime_context": ("runtime_context", "build_runtime_context"),
    "build_snapshot": ("runtime_snapshot_service", "build_snapshot"),
    "snapshot_to_text": ("runtime_snapshot_service", "snapshot_to_text"),
    "run_work_overlay_helper": ("desktop_overlay_setup", "run_work_overlay_helper"),
    "cleanup_stale_work_overlay_files": ("desktop_overlay_setup", "cleanup_stale_work_overlay_files"),
    "_init_force_desktop_overlay_missing_from_env": ("desktop_overlay_setup", "_init_force_desktop_overlay_missing_from_env"),
    "_assign_fresh_renderer_cdp_port": ("renderer_startup", "assign_fresh_cdp_port"),
    "_background_usage_query_payload_with_preview": ("runtime_commands", "_query_with_preview"),
    "_background_usage_response_retry_delay_seconds": ("runtime_settings", "background_usage_retry_delay"),
    "_changed_user_config_keys": ("runtime_settings", "changed_config_keys"),
    "_handle_work_overlay_command": ("overlay_runtime", "_handle_work_overlay_command"),
    "_handle_work_overlay_commands": ("overlay_runtime", "_handle_work_overlay_commands"),
    "_partial_domains_for_changed_user_config": ("runtime_settings", "partial_domains_for_changed_config"),
    "_partial_domains_for_settings_command": ("runtime_settings", "partial_domains_for_command"),
    "_refresh_latest_snapshot_for_partial_settings_command": ("runtime_commands", "refresh_latest_snapshot_for_partial_settings_command"),
    "_work_overlay_command_path": ("overlay_ipc", "command_path"),
    "_work_overlay_heartbeat_path": ("overlay_ipc", "heartbeat_path"),
    "budget_warnings": ("runtime_policies", "budget_warning_messages"),
    "current_budget_windows": ("runtime_policies", "budget_windows"),
}


class _LazyCallable:
    """Resolve a renderer callback only when its owning runtime invokes it."""

    __slots__ = ("_name", "_value")

    def __init__(self, name: str) -> None:
        self._name = name
        self._value = None

    def __call__(self, *args, **kwargs):
        value = self._value
        if value is None:
            value = self._value = resolve(self._name)
        return value(*args, **kwargs)

    def __getattr__(self, name: str) -> object:
        value = self._value
        if value is None:
            value = self._value = resolve(self._name)
        return getattr(value, name)


def resolve(name: str) -> object:
    """Resolve a historical coordinator export from its current owner."""
    special = _SPECIAL.get(name)
    if special is not None:
        module_name, attribute = special
        return getattr(importlib.import_module(f".{module_name}", __package__), attribute)
    candidates = (name, name.lstrip("_"))
    for module_name in _OWNER_MODULES:
        owner = importlib.import_module(f".{module_name}", __package__)
        for candidate in candidates:
            try:
                return getattr(owner, candidate)
            except AttributeError:
                continue
    raise AttributeError(f"runtime_orchestration has no attribute {name!r}")


def session_port_bindings(root_values: Mapping[str, object]) -> dict[str, object]:
    """Materialize declared renderer ports while preferring root callbacks."""
    from .renderer_session_ports import RendererSessionPorts

    values: dict[str, object] = {}
    for field in fields(RendererSessionPorts):
        values[field.name] = (
            root_values[field.name]
            if field.name in root_values
            else _LazyCallable(field.name)
        )
    return values
