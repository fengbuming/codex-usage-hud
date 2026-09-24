"""Runtime path discovery and user-configuration application."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .config import UserConfig, normalize_display_mode, parse_thresholds

if TYPE_CHECKING:
    from .runtime_context import RuntimeContext


@dataclass(frozen=True, slots=True)
class ConfigApplyPorts:
    discover_providers: Callable[..., Any]
    cost_estimator_from_config: Callable[[UserConfig], Any]
    usage_cache_factory: Callable[[Any], Any]
    configure_ui_cost_estimators: Callable[[Any], None]


def candidate_data_dirs(platform: object) -> list[Path]:
    candidates = [platform.get_codex_data_dir(), Path.home() / ".codex"]
    ordered: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.expanduser()
        if resolved not in seen:
            seen.add(resolved)
            ordered.append(resolved)
    return ordered


def discover_path(
    platform: object,
    explicit_path: str | None,
    relative_name: str,
) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser()
    roots = candidate_data_dirs(platform)
    for root in roots:
        candidate = root / relative_name
        if candidate.exists():
            return candidate
    return roots[0] / relative_name


def discover_sessions_root(platform: object, explicit_root: str | None) -> Path:
    if explicit_root:
        return Path(explicit_root).expanduser()
    roots = candidate_data_dirs(platform)
    for root in roots:
        candidate = root / "sessions"
        if candidate.exists():
            return candidate
    return roots[0] / "sessions"


def cli_overrides(args: argparse.Namespace) -> dict[str, object]:
    patch: dict[str, object] = {}
    if getattr(args, "daily_budget_usd", None) is not None:
        patch["daily_budget_usd"] = max(0.0, float(args.daily_budget_usd))
    if getattr(args, "weekly_budget_usd", None) is not None:
        patch["weekly_budget_usd"] = max(0.0, float(args.weekly_budget_usd))
    if getattr(args, "budget_thresholds", None) is not None:
        patch["budget_thresholds"] = parse_thresholds(args.budget_thresholds)
    if getattr(args, "hud_mode", None):
        patch["display_mode"] = normalize_display_mode(args.hud_mode)
    return patch


def apply_cli_overrides(
    config: UserConfig,
    args_or_patch: argparse.Namespace | dict[str, object],
) -> UserConfig:
    patch = (
        dict(args_or_patch)
        if isinstance(args_or_patch, dict)
        else cli_overrides(args_or_patch)
    )
    return replace(config, **patch) if patch else config


def apply_to_context(
    context: object,
    next_config: UserConfig,
    *,
    mtime: float | None,
    ports: ConfigApplyPorts,
    include_history: bool = True,
) -> UserConfig:
    overrides = dict(getattr(context, "config_overrides", {}) or {})
    next_config = apply_cli_overrides(next_config, overrides)
    previous_config = getattr(context, "user_config", UserConfig.defaults())
    sessions_root = getattr(context, "sessions_root", None)
    registry = None
    if isinstance(sessions_root, Path):
        registry = ports.discover_providers(
            user_config=next_config,
            sessions_root=sessions_root,
            include_history=include_history,
        )
        next_config = next_config.migrate_legacy_provider_settings(
            registry.providers(), app_provider=registry.app_provider
        )
        registry = ports.discover_providers(
            user_config=next_config,
            sessions_root=sessions_root,
            include_history=include_history,
        )
    prices_changed = (
        next_config.price_table() != previous_config.price_table()
        or next_config.pricing_versions != previous_config.pricing_versions
    )
    context.user_config = next_config
    context.settings_mtime = mtime
    context.daily_budget_usd = max(0.0, float(next_config.daily_budget_usd))
    context.weekly_budget_usd = max(0.0, float(next_config.weekly_budget_usd))
    context.weekly_adjustment_usd = max(
        0.0, float(next_config.weekly_adjustment_usd)
    )
    context.budget_thresholds = list(next_config.budget_thresholds)
    if registry is not None:
        context.provider_registry = registry
        context.app_provider = registry.app_provider
    if prices_changed:
        estimator = ports.cost_estimator_from_config(next_config)
        parser = getattr(context, "parser", None)
        if parser is not None:
            parser.cost_estimator = estimator
        tracker = getattr(context, "sse_tracker", None)
        if tracker is not None:
            tracker.cost_estimator = estimator
        ports.configure_ui_cost_estimators(estimator)
        worker = getattr(context, "usage_insights_worker", None)
        request_refresh = getattr(worker, "request_refresh", None)
        if callable(request_refresh):
            request_refresh(request_id="pricing-change")
    session_lock_monitor = getattr(context, "session_lock_monitor", None)
    set_enabled = getattr(session_lock_monitor, "set_enabled", None)
    if callable(set_enabled):
        # The monitor always listens now: lock/sleep quiesces the renderer by
        # default, and stop_hud_on_lock_screen only changes on_lock to a full
        # HUD exit instead of a quiesce. It must never be switched off here.
        set_enabled(True)
    background = getattr(context, "background_usage_runtime", None)
    reconfigure = getattr(background, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(
            provider=str(getattr(context, "app_provider", "") or ""),
            price_table=next_config.price_table(),
            pricing_versions=getattr(next_config, "pricing_versions", ()),
        )
    reminder = getattr(context, "rest_reminder", None)
    if reminder is not None:
        reminder.configure(next_config)
    return next_config


def reload_if_changed(
    context: object,
    ports: ConfigApplyPorts,
    *,
    include_history: bool = True,
) -> bool:
    mtime = context.settings_store.mtime()
    if mtime == context.settings_mtime:
        return False
    apply_to_context(
        context,
        context.settings_store.load(),
        mtime=mtime,
        ports=ports,
        include_history=include_history,
    )
    return True



def _save_renderer_user_config(context: RuntimeContext, config: UserConfig) -> None:
    context.settings_store.save(config)
    context.settings_mtime = None
    context.reload_user_config()


def _apply_user_config_to_runtime_context(
    context: RuntimeContext | object,
    next_config: UserConfig,
    *,
    mtime: float | None,
) -> None:
    from .runtime_context import _runtime_config_ports

    apply_to_context(
        context,
        next_config,
        mtime=mtime,
        ports=_runtime_config_ports(),
    )

__all__ = [
    "ConfigApplyPorts",
    "apply_cli_overrides",
    "apply_to_context",
    "candidate_data_dirs",
    "cli_overrides",
    "discover_path",
    "discover_sessions_root",
    "reload_if_changed",
]
