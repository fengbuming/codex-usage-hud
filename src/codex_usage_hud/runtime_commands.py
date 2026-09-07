"""Renderer runtime command handlers with explicitly supplied services."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Event
from typing import Any
import uuid

from . import runtime_settings
from . import __version__
from .codex_provider_config import (
    fetch_provider_models,
    fetch_provider_models_for_cli,
    save_provider_configs,
    send_cli_chat_probe,
    send_provider_chat_probe,
)
from .codex_cli_launcher import (
    build_codex_cli_command,
    discover_codex_cli_options,
    launch_codex_cli,
)
from .config import (
    DEFAULT_WORK_OVERLAY_MAX_ITEMS,
    ModelPrice,
    ProviderSettings,
    UserConfig,
    default_model_prices,
    dismiss_warning_for_today,
    extract_model_prices,
    fetch_model_prices,
    write_json_object,
)
from .core.background_usage import valid_background_event_id
from .provider_cleanup import delete_provider_for_context
from .process_environment import (
    external_environment_scope,
    external_process_environment,
)
from .desktop_overlay import DesktopWorkOverlay
from .desktop_overlay_setup import (
    _desktop_overlay_dependency_status,
    _pyside6_version,
    _set_force_desktop_overlay_missing,
    _start_desktop_overlay_install,
)
from .overlay_runtime import _handle_work_overlay_command
from .overlay_projection import _work_overlay_screen_max_items
from .platforms import SessionSwitchController
from .platforms.desktop_thread_navigator import (
    DesktopThreadNavigationError,
    DesktopThreadNavigator,
)
from .runtime_context import RuntimeContext
from .runtime_snapshot_service import _apply_pre_send_pricing
from .runtime_policies import budget_warning_messages
from .runtime_paths import hud_program_root
from .updater import AutoUpdateManager, check_for_update, download_update_asset, launch_installer


def _renderer_settings_status(
    message: str,
    *,
    kind: str = "info",
) -> dict[str, object]:
    return runtime_settings.settings_status(message, kind=kind)


def refresh_latest_snapshot_for_partial_settings_command(
    command: Mapping[str, Any],
    *,
    snapshot: object,
    context: object,
    previous_config: UserConfig,
    current_config: UserConfig,
) -> None:
    """Update only fields whose settings-domain payload is being pushed."""
    action = str(command.get("action") or "").strip()
    changed_keys = runtime_settings.changed_config_keys(
        previous_config, current_config
    )
    if action in {"fetchPrices", "savePricing", "pricingImportCommit"} or (
        action == "save"
        and changed_keys
        and changed_keys.issubset(runtime_settings.PRICING_KEYS)
    ):
        snapshot.estimate_base = _apply_pre_send_pricing(
            context, snapshot, snapshot.estimate_base
        )
    if action == "save" and changed_keys & runtime_settings.BUDGET_KEYS:
        raw_week_cost_usd = (
            max(
                0.0,
                float(snapshot.week_cost_usd)
                - float(snapshot.week_adjustment_usd or 0.0),
            )
            if snapshot.week_cost_usd is not None
            else None
        )
        week_adjustment_usd = max(
            0.0, float(current_config.weekly_adjustment_usd)
        )
        snapshot.week_adjustment_usd = week_adjustment_usd
        snapshot.week_cost_usd = (
            None
            if raw_week_cost_usd is None
            else round(raw_week_cost_usd + week_adjustment_usd, 6)
        )
        snapshot.daily_limit_usd = max(0.0, float(current_config.daily_budget_usd))
        snapshot.weekly_limit_usd = max(0.0, float(current_config.weekly_budget_usd))
        snapshot.budget_warnings = (
            budget_warning_messages(
                snapshot.today_cost_usd,
                snapshot.week_cost_usd,
                snapshot.daily_limit_usd,
                snapshot.weekly_limit_usd,
                list(current_config.budget_thresholds),
            )
            if snapshot.today_cost_usd is not None and snapshot.week_cost_usd is not None
            else []
        )

_LOGGER = logging.getLogger(__name__)
UNHANDLED = object()
PRICING_EXPORT_FILENAME = "codex-usage-hud-pricing.json"


@dataclass(frozen=True, slots=True)
class RuntimeCommandPorts:
    background_usage: object | None = None
    cleanup_worker: object | None = None
    cleanup_manager: object | None = None
    session_index_job: object | None = None
    desktop_thread_navigator: object | None = None
    insights_worker: object | None = None
    insights_payload: Mapping[str, object] | None = None
    activate_session: Callable[[Mapping[str, object]], object | None] | None = None
    resolve_active_session: Callable[[Mapping[str, object]], object | None] | None = None


@dataclass(frozen=True, slots=True)
class GeneralCommandPorts:
    load_config: Callable[[], Any]
    save_config: Callable[[Any], None]
    fetch_prices: Callable[[str], Mapping[str, Any]]
    rest_reminder: object | None
    update_manager: object | None
    work_overlay: object | None
    request_restart: Callable[[], None]
    request_exit: Callable[[], None]
    check_update: Callable[[], object]
    install_update: Callable[[object], None]
    overlay_status: Callable[[], Mapping[str, object]]
    start_overlay_install: Callable[[], bool]
    clear_forced_missing: Callable[[], None]
    forced_missing_with_real_install: Callable[[], bool]
    pyside_version: Callable[[], str]
    default_overlay_limit: Callable[[], int]
    dismiss_warnings_today: Callable[[], bool]
    request_restart_codex: Callable[[], None] | None = None
    pricing_open_path: Callable[[Path], None] | None = None
    save_codex_providers: Callable[[object], Mapping[str, object]] | None = None
    delete_provider: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None
    fetch_provider_models: Callable[[str, str], list[str]] | None = None
    fetch_cli_provider_models: Callable[[str], Mapping[str, object]] | None = None
    send_provider_chat_probe: Callable[[str, str, str, str], Mapping[str, object]] | None = None
    send_cli_chat_probe: Callable[[str, str, str], Mapping[str, object]] | None = None
    codex_cli_discover: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None
    codex_cli_launch: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None


def _status(message: str, *, kind: str = "") -> dict[str, object]:
    return runtime_settings.settings_status(message, kind=kind)


_ERROR_MESSAGE_MAX = 160


def _exc_detail_log(exc: BaseException, *, tag: str) -> str:
    """把完整异常写入日志，并返回适合放进状态栏的短文本。

    状态栏只展示异常文本的压缩单行（超长截断，空文本退回异常类型名），避免把
    完整 traceback 或 OSError 细节塞进界面；完整堆栈通过日志保留用于诊断。
    """
    _LOGGER.warning(
        "%s exc=%s",
        tag,
        type(exc).__name__,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    detail = " ".join(str(exc).strip().split())
    if not detail:
        detail = type(exc).__name__
    elif len(detail) > _ERROR_MESSAGE_MAX:
        detail = detail[:_ERROR_MESSAGE_MAX].rstrip() + "…"
    return detail


def _price_updates(
    previous: UserConfig, current: UserConfig
) -> list[tuple[str, dict[str, ModelPrice]]]:
    updates: list[tuple[str, dict[str, ModelPrice]]] = []
    global_updates = {
        key: price
        for key, price in current.model_prices.items()
        if previous.model_prices.get(key) != price
    }
    if global_updates:
        updates.append(("", global_updates))
    for provider in sorted(
        previous.provider_settings.keys() | current.provider_settings.keys()
    ):
        previous_prices = previous.provider_settings.get(
            provider, ProviderSettings()
        ).model_prices
        current_prices = current.provider_settings.get(
            provider, ProviderSettings()
        ).model_prices
        changed = {
            key: price
            for key, price in current_prices.items()
            if previous_prices.get(key) != price
        }
        if changed:
            updates.append((provider, changed))
    return updates


def _pricing_version_state_changed(previous: UserConfig, current: UserConfig) -> bool:
    """Reject direct version/audit writes outside the pricing workflows."""
    return (
        previous.pricing_versions != current.pricing_versions
        or previous.pricing_audit != current.pricing_audit
    )


def _merge_versioned_prices(
    candidate: UserConfig,
    versioned: UserConfig,
) -> UserConfig:
    provider_settings: dict[str, ProviderSettings] = {}
    for provider in sorted(
        candidate.provider_settings.keys() | versioned.provider_settings.keys()
    ):
        candidate_settings = candidate.provider_settings.get(provider, ProviderSettings())
        versioned_settings = versioned.provider_settings.get(provider, ProviderSettings())
        provider_settings[provider] = replace(
            candidate_settings,
            model_prices=versioned_settings.model_prices,
        )
    return replace(
        candidate,
        model_prices=versioned.model_prices,
        provider_settings=provider_settings,
        pricing_versions=versioned.pricing_versions,
        pricing_audit=versioned.pricing_audit,
    )


def _prepare_versioned_price_changes(
    ports: GeneralCommandPorts,
    settings_payload: object,
    effective_at: object,
) -> tuple[UserConfig, UserConfig, int]:
    """Build a versioned candidate without writing it.

    Keeping this operation pure lets import/validation flows inspect a
    candidate without publishing a partial price table.
    """
    previous = ports.load_config()
    candidate = runtime_settings.config_from_payload(previous, settings_payload)
    if _pricing_version_state_changed(previous, candidate):
        raise ValueError("价格版本只能通过保存价格或导入预览流程写入。")
    updates = _price_updates(previous, candidate)
    if not updates:
        return previous, candidate, 0
    if not str(effective_at or "").strip():
        raise ValueError("保存价格前必须设置新价格的生效时间。")
    versioned = previous
    changed_count = 0
    for provider, prices in updates:
        versioned, result = versioned.apply_price_updates(
            prices,
            effective_at=str(effective_at),
            provider=provider,
        )
        changed_count += int(result.added_count) + int(result.updated_count)
    final = _merge_versioned_prices(candidate, versioned)
    return previous, final, changed_count


def _save_versioned_price_changes(
    ports: GeneralCommandPorts,
    settings_payload: object,
    effective_at: object,
) -> tuple[UserConfig, int]:
    _previous, final, changed_count = _prepare_versioned_price_changes(
        ports,
        settings_payload,
        effective_at,
    )
    ports.save_config(final)
    return final, changed_count


def _pricing_payload_with_default_effective_at(
    payload: object,
    effective_at: object,
) -> dict[str, object]:
    default_effective_at = str(effective_at or "").strip()
    if isinstance(payload, Mapping) and isinstance(payload.get("prices"), list):
        normalized = dict(payload)
        normalized["schema_version"] = normalized.get("schema_version", 1)
        normalized["unit"] = normalized.get("unit", "USD_per_1M_tokens")
        if any(not isinstance(row, Mapping) for row in payload["prices"]):
            raise ValueError("prices entries must be objects")
        missing_effective_at = any(
            isinstance(row, Mapping) and not str(row.get("effective_at") or "").strip()
            for row in payload["prices"]
        )
        if missing_effective_at and not default_effective_at:
            raise ValueError("导入价格前必须设置新价格的生效时间。")
        normalized["prices"] = [
            {
                **dict(row),
                "effective_at": row.get("effective_at") or default_effective_at,
            }
            for row in payload["prices"]
        ]
        return normalized
    extracted = extract_model_prices(payload)
    if not extracted:
        raise ValueError("价格 JSON 中没有可导入的模型价格。")
    if not default_effective_at:
        raise ValueError("导入价格前必须设置新价格的生效时间。")
    return {
        "schema_version": 1,
        "unit": "USD_per_1M_tokens",
        "prices": [
            {
                **price.to_dict(),
                "model": price.model or key,
                "effective_at": default_effective_at,
                "created_by": "user_import",
                "source": "import",
            }
            for key, price in sorted(extracted.items())
        ],
    }


def _pricing_template_payload(*, now: datetime) -> dict[str, object]:
    fallback = default_model_prices().get("gpt-5.6-sol")
    if fallback is None:
        raise ValueError("内置模型价格中缺少 gpt-5.6-sol。")
    timestamp = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    row = fallback.to_dict()
    row.update(
        {
            "model": "gpt-5.6-sol",
            "effective_at": timestamp,
            "created_at": timestamp,
            "created_by": "builtin_migration",
            "source": "builtin",
        }
    )
    payload = UserConfig.empty_pricing_template()
    payload["prices"] = [row]
    return payload


def _pricing_export_payload(
    config: UserConfig,
    *,
    now: datetime,
) -> tuple[dict[str, object], bool]:
    payload = config.export_pricing_payload()
    prices = payload.get("prices")
    if isinstance(prices, list) and prices:
        return payload, False
    return _pricing_template_payload(now=now), True


def _pricing_export_path(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return root / PRICING_EXPORT_FILENAME


def _export_pricing_to_program_root(
    config: UserConfig,
) -> tuple[Path, bool]:
    now = datetime.now(timezone.utc)
    payload, used_template = _pricing_export_payload(config, now=now)
    output_path = _pricing_export_path(hud_program_root())
    write_json_object(output_path, payload)
    return output_path.resolve(), used_template


def _pricing_file_path(filename: object) -> Path:
    raw_name = str(filename or "").strip()
    name = Path(raw_name).name
    if (
        not raw_name
        or name != raw_name
        or name != PRICING_EXPORT_FILENAME
    ):
        raise ValueError("价格文件名无效。")
    root = hud_program_root().resolve()
    path = (root / name).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("价格文件必须位于 HUD 程序根目录。") from exc
    if not path.is_file():
        raise ValueError("价格文件不存在，请重新导出。")
    return path


def _open_system_path(path: Path) -> None:
    if sys.platform.startswith("win"):
        with external_environment_scope():
            os.startfile(str(path))  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], env=external_process_environment())
        return
    subprocess.Popen(["xdg-open", str(path)], env=external_process_environment())


def _sync_imported_current_prices(
    config: UserConfig,
    preview: object,
    *,
    now: datetime | None = None,
) -> UserConfig:
    """Keep the editable current table aligned with the newest imported scope.

    A portable import may deliberately contain older historical versions.  The
    legacy ``model_prices`` table is still rendered as the editable current
    price, so it must be derived from the newest effective version after the
    merge rather than blindly from the last imported row.
    """
    current = now or datetime.now(timezone.utc)

    def scope_key(version: object) -> tuple[str, str, str]:
        return (
            str(getattr(version, "provider", "") or "").strip().lower(),
            str(getattr(version, "base_url", "") or "").strip().lower(),
            str(
                getattr(version, "model", "")
                or getattr(version, "model_pattern", "")
                or ""
            )
            .strip()
            .lower(),
        )

    touched_scopes = {
        scope_key(version)
        for version in getattr(preview, "versions", ())
        if scope_key(version)[2]
    }
    latest_by_scope: dict[tuple[str, str, str], object] = {}
    for version in getattr(config, "pricing_versions", ()):
        key = scope_key(version)
        effective_at = getattr(version, "effective_at", None)
        if key not in touched_scopes or not isinstance(effective_at, datetime):
            continue
        if effective_at > current:
            continue
        previous = latest_by_scope.get(key)
        if previous is None or (
            effective_at,
            getattr(version, "created_at", datetime.min.replace(tzinfo=timezone.utc)),
            str(getattr(version, "version_id", "")),
        ) > (
            getattr(previous, "effective_at", datetime.min.replace(tzinfo=timezone.utc)),
            getattr(previous, "created_at", datetime.min.replace(tzinfo=timezone.utc)),
            str(getattr(previous, "version_id", "")),
        ):
            latest_by_scope[key] = version

    result = config
    versions = sorted(
        latest_by_scope.values(),
        key=lambda version: (
            getattr(version, "effective_at", datetime.min.replace(tzinfo=timezone.utc)),
            getattr(version, "created_at", datetime.min.replace(tzinfo=timezone.utc)),
            str(getattr(version, "version_id", "")),
        ),
    )
    for version in versions:
        key = str(getattr(version, "model", "") or getattr(version, "model_pattern", ""))
        price = ModelPrice.from_mapping(version.to_dict(), key)
        if key and price is not None:
            result = result.with_price_updates(
                {key: price},
                provider=str(getattr(version, "provider", "") or "") or None,
            )
    return result


def _current_pricing_effective_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def correlate_status(
    status: dict[str, object], command: Mapping[str, object]
) -> dict[str, object]:
    status.setdefault("requestId", str(command.get("requestId") or command.get("id") or ""))
    status.setdefault("action", str(command.get("action") or ""))
    return status


def dispatch_command(
    command: Mapping[str, Any],
    runtime_ports: RuntimeCommandPorts,
    general_ports: GeneralCommandPorts,
) -> dict[str, object]:
    for handler in (
        handle_active_session_command,
        handle_session_index_command,
        handle_cleanup_command,
        handle_insights_command,
        handle_background_command,
    ):
        handled = handler(command, runtime_ports)
        if handled is not UNHANDLED:
            return correlate_status(handled, command)
    return correlate_status(handle_general_command(command, general_ports), command)


def handle_active_session_command(
    command: Mapping[str, Any], ports: RuntimeCommandPorts
) -> dict[str, object] | object:
    """Handle a user-confirmed renderer candidate binding."""
    action = str(command.get("action") or "").strip()
    if action != "resolveActiveSession":
        return UNHANDLED
    resolver = ports.resolve_active_session
    if resolver is None:
        return _status("当前会话候选匹配不可用。", kind="error")
    try:
        accepted = resolver(command)
    except Exception as exc:
        error_detail = _exc_detail_log(
            exc, tag="renderer_active_session_candidate_failed"
        )
        return _status(
            f"会话候选匹配失败：{error_detail}",
            kind="error",
        )
    if not bool(accepted):
        return _status(
            "会话候选已失效，请重新选择当前列表中的未归档会话。",
            kind="error",
        )
    return _status("已按你的选择匹配当前未归档会话。")


def _query_with_preview(
    runtime: object,
    *,
    range_key: str,
    feature: str,
    model: str,
    event_id: str,
) -> dict[str, object]:
    query = getattr(runtime, "query", None)
    if not callable(query):
        raise RuntimeError("用量总览当前不可用。")
    raw_payload = query(
        range_key=range_key,
        feature=feature,
        model=model,
        event_id=event_id,
    )
    if not isinstance(raw_payload, Mapping):
        raise RuntimeError("后台用量查询返回了无效数据。")
    payload = dict(raw_payload)
    selected_event_id = str(payload.get("selectedEventId") or "").strip()
    selected_detail: dict[str, object] | None = None
    detail = getattr(runtime, "detail", None)
    if selected_event_id and callable(detail):
        try:
            raw_detail = detail(selected_event_id)
        except Exception as exc:
            _LOGGER.debug(
                "background_usage_preview_failed event_id=%s error=%s",
                selected_event_id,
                exc,
            )
        else:
            if isinstance(raw_detail, Mapping):
                selected_detail = dict(raw_detail)
                prompt = str(selected_detail.pop("prompt", "") or "")
                selected_detail["hasPrompt"] = bool(prompt)
    payload["selectedDetail"] = selected_detail
    return payload


def background_usage_workdir(runtime: object | None, event_id: object) -> Path | None:
    """Resolve one audited background event to an existing absolute directory."""
    normalized_event_id = valid_background_event_id(event_id)
    if not normalized_event_id:
        return None
    detail = getattr(runtime, "detail", None)
    if not callable(detail):
        return None
    payload = detail(normalized_event_id)
    if not isinstance(payload, Mapping):
        return None
    payload_event_id = valid_background_event_id(payload.get("eventId"))
    if payload_event_id.casefold() != normalized_event_id.casefold():
        return None
    raw_path = str(payload.get("cwd") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    return path if path.is_absolute() and path.is_dir() else None


def handle_background_command(
    command: Mapping[str, Any], ports: RuntimeCommandPorts
) -> dict[str, object] | object:
    action = str(command.get("action") or "").strip()
    if action not in {
        "backgroundUsageQuery",
        "backgroundUsageDetail",
        "openBackgroundUsage",
        "openBackgroundUsageFromInsights",
        "openBackgroundUsageWorkdir",
        "backgroundUsagePolicyQuery",
        "backgroundUsagePolicySet",
    }:
        return UNHANDLED
    request_id = str(command.get("requestId") or command.get("id") or "").strip()
    runtime = ports.background_usage
    try:
        if action == "backgroundUsagePolicyQuery":
            query = getattr(runtime, "policy_query", None)
            if not callable(query):
                return runtime_settings.background_usage_response_status("policyQuery", request_id, error="后台任务控制当前不可用。")
            payload = query(command.get("featureKey"), command.get("eventId", ""))
            return runtime_settings.background_usage_response_status("policyQuery", request_id, payload=payload, event_id=str(command.get("eventId") or ""))
        if action == "backgroundUsagePolicySet":
            apply = getattr(runtime, "policy_set", None)
            if not callable(apply):
                return runtime_settings.background_usage_response_status("policyApply", request_id, error="后台任务控制当前不可用。")
            payload = apply(
                command.get("featureKey"),
                command.get("desiredState"),
                command.get("expectedPolicyRevision"),
                command.get("eventId", ""),
                command.get("source", "usage_detail"),
            )
            return runtime_settings.background_usage_response_status("policyApply", request_id, payload=payload, event_id=str(command.get("eventId") or ""), error=str(payload.get("message") or "") if isinstance(payload, Mapping) and payload.get("error") else "")
        if action == "backgroundUsageQuery":
            raw_filters = command.get("filters")
            filters = raw_filters if isinstance(raw_filters, Mapping) else {}
            payload = _query_with_preview(
                runtime,
                range_key=str(filters.get("range") or "today"),
                feature=str(filters.get("feature") or ""),
                model=str(filters.get("model") or ""),
                event_id=str(filters.get("eventId") or ""),
            )
            return runtime_settings.background_usage_response_status(
                "query", request_id, payload=payload
            )
        event_id = str(command.get("eventId") or "").strip()
        if action == "openBackgroundUsageWorkdir":
            try:
                workdir = background_usage_workdir(runtime, event_id)
            except Exception as exc:
                error_detail = _exc_detail_log(
                    exc, tag="background_usage_workdir_read_failed"
                )
                return _status(
                    f"无法读取后台任务工作目录：{error_detail}",
                    kind="error",
                )
            if workdir is None:
                return _status("该后台任务没有可打开的工作目录。", kind="error")
            try:
                _open_system_path(workdir)
            except OSError as exc:
                error_detail = _exc_detail_log(
                    exc, tag="background_usage_workdir_open_failed"
                )
                return _status(
                    f"无法打开工作目录：{error_detail}",
                    kind="error",
                )
            return _status("已打开工作目录。")
        if action == "backgroundUsageDetail":
            detail = getattr(runtime, "detail", None)
            if not callable(detail):
                return runtime_settings.background_usage_response_status(
                    "detail",
                    request_id,
                    event_id=event_id,
                    error="用量总览当前不可用。",
                )
            if command.get("markViewed") is True:
                confirm = getattr(runtime, "confirm", None)
                if callable(confirm):
                    confirm(event_id)
            payload = detail(event_id) if event_id else None
            return runtime_settings.background_usage_response_status(
                "detail",
                request_id,
                payload=payload,
                event_id=event_id,
                error="" if payload is not None else "后台用量事件不存在。",
            )
        if event_id:
            confirm = getattr(runtime, "confirm", None)
            if callable(confirm):
                confirm(event_id)
        range_key = "today"
        range_for_event = getattr(runtime, "range_for_event", None)
        if event_id and callable(range_for_event):
            candidate = str(range_for_event(event_id) or "today").strip().lower()
            if candidate in {"today", "7d", "30d", "all"}:
                range_key = candidate
        payload = _query_with_preview(
            runtime,
            range_key=range_key,
            feature="",
            model="",
            event_id=event_id,
        )
        return runtime_settings.background_usage_response_status(
            "open", request_id, payload=payload, event_id=event_id
        )
    except Exception as exc:
        kind = {
            "backgroundUsageQuery": "query",
            "backgroundUsageDetail": "detail",
            "backgroundUsagePolicyQuery": "policyQuery",
            "backgroundUsagePolicySet": "policyApply",
        }.get(action, "open")
        error_detail = _exc_detail_log(
            exc, tag="background_usage_read_failed"
        )
        return runtime_settings.background_usage_response_status(
            kind,
            request_id,
            event_id=str(command.get("eventId") or "").strip(),
            error=f"用量总览读取失败：{error_detail}"
        )


def handle_session_index_command(
    command: Mapping[str, Any], ports: RuntimeCommandPorts
) -> dict[str, object] | object:
    """Handle ``sessionIndexStatus`` / ``sessionIndexControl``.

    Both read the single warm job; control actions are validated against the
    action set from the PRD (§9.2): ``start | extend | pause | resume |
    cancel_ui`` plus local index maintenance controls. ``cancel_ui`` only
    detaches the UI, never stops the job.
    """
    action = str(command.get("action") or "").strip()
    if action not in runtime_settings.SESSION_INDEX_COMMANDS:
        return UNHANDLED
    job = getattr(ports, "session_index_job", None)
    if job is None:
        status = _status("会话搜索索引进度当前不可用。", kind="error")
        status["sessionIndex"] = {
            "available": False,
            "error": "unavailable",
        }
        return status
    request_id = str(command.get("requestId") or command.get("id") or "")
    if action == "sessionIndexStatus":
        status = _status("")
        _session_index_attach(job)
        payload = job.status() if callable(getattr(job, "status", None)) else {}
        payload["requestId"] = request_id
        status["sessionIndex"] = payload
        return status
    control_action = str(command.get("control") or "").strip().casefold()
    valid = {
        "start",
        "extend",
        "pause",
        "resume",
        "cancel_ui",
        "enable",
        "disable",
        "clear",
    }
    if control_action not in valid:
        status = _status("未知的索引控制动作。", kind="error")
        status["sessionIndex"] = {"accepted": False, "error": "unknown_action"}
        return status
    range_key = str(command.get("range") or "").strip().casefold()
    clear_result: Mapping[str, object] = {}
    control_error = ""
    try:
        if control_action == "start":
            accepted = bool(job.start(range_key))
        elif control_action == "extend":
            accepted = bool(job.extend(range_key))
        elif control_action == "pause":
            accepted = bool(job.pause())
        elif control_action == "resume":
            accepted = bool(job.resume())
        elif control_action == "enable":
            setter = getattr(job, "set_enabled", None)
            accepted = bool(setter(True, range_key)) if callable(setter) else False
            if not callable(setter):
                control_error = "enable_unavailable"
        elif control_action == "disable":
            setter = getattr(job, "set_enabled", None)
            accepted = bool(setter(False, range_key)) if callable(setter) else False
            if not callable(setter):
                control_error = "disable_unavailable"
        elif control_action == "clear":
            clearer = getattr(job, "clear_index", None)
            if callable(clearer):
                value = clearer()
                clear_result = value if isinstance(value, Mapping) else {}
                accepted = bool(clear_result.get("accepted"))
                control_error = str(clear_result.get("error") or "")
            else:
                accepted = False
                control_error = "clear_unavailable"
        else:
            accepted = bool(job.cancel_ui())
    except Exception as exc:
        status = _status(
            _exc_detail_log(exc, tag="session_index_control_failed"),
            kind="error",
        )
        status["sessionIndex"] = {
            "accepted": False,
            "error": str(exc) or type(exc).__name__,
        }
        return status
    if accepted and control_action in ("start", "extend", "resume", "enable"):
        _session_index_attach(job)
    if accepted and control_action == "clear":
        # Clearing also invalidates the manager's cached search matches. Push
        # that reset through the existing session-cleanup domain so a visible
        # query cannot keep showing ids from the removed index.
        manager = getattr(ports, "cleanup_manager", None)
        publisher = getattr(ports.cleanup_worker, "_publish", None)
        snapshot_fn = getattr(manager, "snapshot", None)
        if callable(publisher) and callable(snapshot_fn):
            try:
                publisher(snapshot_fn())
            except Exception:
                pass
    status = _status(
        "" if accepted else "索引控制未生效。",
        kind="" if accepted else "warning",
    )
    payload = dict(job.status()) if callable(getattr(job, "status", None)) else {}
    payload["accepted"] = bool(accepted)
    payload["error"] = control_error
    payload["requestId"] = request_id
    payload.update(
        {
            key: value
            for key, value in clear_result.items()
            if key not in {"accepted", "error"}
        }
    )
    status["sessionIndex"] = payload
    return status


def _session_index_attach(job: object) -> None:
    """Mark the UI as subscribed to the shared warm job (PRD §4.1).

    Entering session management only attaches to the existing task; it never
    creates or stops a job.  ``cancel_ui`` (background run) detaches without
    stopping the worker, so attach here only flips a subscription flag.
    """
    attach = getattr(job, "attach", None)
    if callable(attach):
        try:
            attach()
        except Exception:
            pass


_SESSION_JUMP_ERROR_MESSAGES = {
    "thread-not-owned": "Codex Desktop 未保留该会话，无法跳转。",
    "desktop-bridge-unavailable": "Codex Desktop 会话通道不可用，无法跳转。",
    "navigation-unverified": "已发送跳转请求，但未能确认 Codex Desktop 已切换会话。",
}

_PROVIDER_ID_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _desktop_thread_navigator(context: object) -> DesktopThreadNavigator:
    """Prefer an injected navigator; otherwise build one from the environment.

    Construction only reads the CDP port from the environment, and the port can
    change when the HUD (re)launches Codex Desktop, so it is re-read per call
    instead of being cached.
    """
    existing = getattr(context, "desktop_thread_navigator", None)
    if existing is not None:
        return existing
    return DesktopThreadNavigator()


def _session_cleanup_route_target(
    command: Mapping[str, Any], ports: RuntimeCommandPorts
) -> Mapping[str, str] | None:
    target_for_item = getattr(
        ports.cleanup_manager, "session_route_target_for_item", None
    )
    if not callable(target_for_item):
        return None
    try:
        target = target_for_item(
            str(command.get("itemId") or "").strip(),
            str(command.get("inventoryRevision") or "").strip(),
        )
    except Exception as exc:
        _exc_detail_log(exc, tag="session_cleanup_route_target_failed")
        return None
    if not isinstance(target, Mapping):
        return None
    if not str(target.get("sessionId") or "").strip():
        return None
    return target


def _session_jump_status(
    message: str,
    *,
    kind: str = "",
    item_id: str = "",
    error: str = "",
    verified: bool = False,
    can_resume: bool = False,
) -> dict[str, object]:
    status = _status(message, kind=kind)
    status["sessionCleanupSessionJump"] = {
        "itemId": item_id,
        "verified": verified,
        "error": error,
        "canResume": can_resume,
    }
    return status


def _open_session_cleanup_session(
    command: Mapping[str, Any], ports: RuntimeCommandPorts
) -> dict[str, object]:
    """Route Codex Desktop to one inventory session, even a long-lost one."""
    item_id = str(command.get("itemId") or "").strip()
    target = _session_cleanup_route_target(command, ports)
    if target is None:
        return _session_jump_status(
            "该会话当前不可跳转。",
            kind="error",
            item_id=item_id,
            error="target-unresolved",
        )
    navigator = ports.desktop_thread_navigator
    if not callable(getattr(navigator, "navigate", None)):
        try:
            navigator = DesktopThreadNavigator()
        except Exception:  # pragma: no cover - construction only reads env
            navigator = None
    navigate = getattr(navigator, "navigate", None)
    if not callable(navigate):
        return _session_jump_status(
            "Codex Desktop 会话跳转当前不可用。",
            kind="error",
            item_id=item_id,
            error="navigator-unavailable",
            can_resume=True,
        )
    try:
        report = navigate(
            str(target.get("sessionId") or "").strip(),
            client_kind=str(target.get("clientKind") or ""),
        )
    except DesktopThreadNavigationError as exc:
        return _session_jump_status(
            f"无法跳转到此会话：{exc}",
            kind="error",
            item_id=item_id,
            error="navigation-unavailable",
            can_resume=True,
        )
    except Exception as exc:
        error_detail = _exc_detail_log(
            exc, tag="session_cleanup_session_jump_failed"
        )
        return _session_jump_status(
            f"跳转失败：{error_detail}",
            kind="error",
            item_id=item_id,
            error=error_detail,
            can_resume=True,
        )
    if report.verified:
        status = _session_jump_status(
            "已跳转到 Codex Desktop 会话。",
            item_id=item_id,
            verified=True,
        )
        query = str(command.get("searchQuery") or "").strip()[:500]
        thread_find = getattr(ports.cleanup_manager, "thread_find_for_item", None)
        if query and callable(thread_find):
            try:
                status["sessionCleanupSessionJump"]["search"] = thread_find(
                    item_id, str(command.get("inventoryRevision") or ""), query
                )
            except Exception as exc:
                _exc_detail_log(exc, tag="session_thread_find_failed")
                status["sessionCleanupSessionJump"]["search"] = {
                    "query": query, "error": "thread-find-unavailable"
                }
        return status
    error = str(report.error or "navigation-unverified").strip()
    message = _SESSION_JUMP_ERROR_MESSAGES.get(error) or "未能跳转到该会话。"
    return _session_jump_status(
        message,
        kind="error",
        item_id=item_id,
        error=error,
        can_resume=True,
    )


def _resume_session_cleanup_session(
    command: Mapping[str, Any], ports: RuntimeCommandPorts
) -> dict[str, object]:
    """Fallback: reopen a session the Desktop no longer owns via ``codex resume``."""
    item_id = str(command.get("itemId") or "").strip()
    revision = str(command.get("inventoryRevision") or "").strip()
    target = _session_cleanup_route_target(command, ports)
    if target is None:
        return _status("该会话当前不可恢复。", kind="error")
    session_id = str(target.get("sessionId") or "").strip()
    workdir_text = str(target.get("cwd") or "").strip()
    if not workdir_text:
        workdir_for_item = getattr(ports.cleanup_manager, "workdir_for_item", None)
        if callable(workdir_for_item):
            try:
                resolved = workdir_for_item(item_id, revision)
            except Exception as exc:
                _exc_detail_log(
                    exc, tag="session_cleanup_resume_workdir_failed"
                )
                resolved = None
            if isinstance(resolved, Path):
                workdir_text = str(resolved)
    if not workdir_text:
        return _status("该会话没有可用于恢复的工作目录。", kind="error")
    try:
        options = discover_codex_cli_options()
    except Exception as exc:
        error_detail = _exc_detail_log(
            exc, tag="session_cleanup_resume_options_failed"
        )
        return _status(f"无法读取终端选项：{error_detail}", kind="error")
    options_map = options if isinstance(options, Mapping) else {}
    terminal_id = str(options_map.get("defaultTerminal") or "").strip()
    terminal_options = options_map.get("terminals")
    selected_terminal = (
        next(
            (
                item
                for item in terminal_options
                if isinstance(item, Mapping)
                and str(item.get("id") or "") == terminal_id
            ),
            {},
        )
        if isinstance(terminal_options, Sequence)
        and not isinstance(terminal_options, (str, bytes, bytearray))
        else {}
    )
    shell = str(selected_terminal.get("shell") or "powershell").strip().lower()
    if shell not in {"powershell", "cmd", "bash", "zsh"}:
        shell = "powershell"
    provider = str(target.get("modelProvider") or "").strip()
    if not _PROVIDER_ID_TEXT.fullmatch(provider) or provider == "unknown":
        provider = ""
    try:
        launch_command = build_codex_cli_command(
            provider=provider,
            default_provider=str(options_map.get("defaultProvider") or ""),
            permission="workspace-write",
            resume=True,
            resume_session_id=session_id,
            workdir=workdir_text,
            shell=shell,
        )
        launch_codex_cli(
            terminal_id=terminal_id,
            command=launch_command,
            workdir=workdir_text,
            provider=provider,
        )
    except Exception as exc:
        error_detail = _exc_detail_log(
            exc, tag="session_cleanup_resume_launch_failed"
        )
        return _status(f"无法在终端恢复该会话：{error_detail}", kind="error")
    return _status("已在终端执行 codex resume。")


def handle_cleanup_command(
    command: Mapping[str, Any], ports: RuntimeCommandPorts
) -> dict[str, object] | object:
    action = str(command.get("action") or "").strip()
    if action == "sessionCleanupWorkdirOptions":
        manager = ports.cleanup_manager
        options = getattr(manager, "workdir_options_detailed", None)
        detailed = callable(options)
        if not callable(options):
            options = getattr(manager, "workdir_options", None)
        request_id = str(command.get("requestId") or command.get("id") or "")
        if not callable(options):
            status = _status("会话工作目录筛选当前不可用。", kind="error")
            status["sessionCleanupWorkdirOptions"] = {
                "requestId": request_id,
                "options": [],
                "error": "unavailable",
                "detailed": False,
            }
            return status
        try:
            values = options()
        except Exception as exc:
            error_detail = _exc_detail_log(exc, tag="session_cleanup_workdir_options_failed")
            status = _status(
                f"无法读取会话工作目录选项：{error_detail}", kind="error"
            )
            status["sessionCleanupWorkdirOptions"] = {
                "requestId": request_id,
                "options": [],
                "error": error_detail,
                "detailed": detailed,
            }
            return status
        status = _status("")
        status["sessionCleanupWorkdirOptions"] = {
            "requestId": request_id,
            "options": list(values) if isinstance(values, Sequence) else [],
            "detailed": detailed,
        }
        return status
    if action == "openSessionCleanupSession":
        return _open_session_cleanup_session(command, ports)
    if action == "resumeSessionCleanupSession":
        return _resume_session_cleanup_session(command, ports)
    if action == "openSessionCleanupWorkdir":
        workdir_for_item = getattr(ports.cleanup_manager, "workdir_for_item", None)
        if not callable(workdir_for_item):
            return _status("会话管理当前不可用。", kind="error")
        try:
            workdir = workdir_for_item(
                str(command.get("itemId") or "").strip(),
                str(command.get("inventoryRevision") or "").strip(),
            )
        except Exception as exc:
            error_detail = _exc_detail_log(
                exc, tag="session_cleanup_workdir_read_failed"
            )
            return _status(
                f"无法读取会话工作目录：{error_detail}",
                kind="error",
            )
        try:
            valid_workdir = (
                isinstance(workdir, Path)
                and workdir.is_absolute()
                and workdir.is_dir()
            )
        except OSError:
            valid_workdir = False
        if not valid_workdir:
            return _status("该会话没有可打开的工作目录。", kind="error")
        try:
            _open_system_path(workdir)
        except OSError as exc:
            error_detail = _exc_detail_log(
                exc, tag="session_cleanup_workdir_open_failed"
            )
            return _status(
                f"无法打开工作目录：{error_detail}",
                kind="error",
            )
        return _status("已打开工作目录。")
    if action not in runtime_settings.SESSION_CLEANUP_COMMANDS:
        return UNHANDLED
    request_id = str(command.get("requestId") or command.get("id") or "")
    enqueue = getattr(ports.cleanup_worker, "enqueue", None)
    if not callable(enqueue):
        status = _status("会话永久删除当前不可用。", kind="error")
        status["sessionCleanupRequestId"] = request_id
        status["sessionCleanupAction"] = action
        return status
    try:
        accepted = enqueue(command)
    except Exception as exc:
        status = _status(
            _exc_detail_log(exc, tag='session_cleanup_enqueue_failed'),
            kind="error",
        )
        status["sessionCleanupRequestId"] = request_id
        status["sessionCleanupAction"] = action
        return status
    request_id = str(accepted.get("requestId") or request_id)
    labels = {
        "sessionCleanupScan": "会话清单扫描已开始。",
        "sessionCleanupSearch": "正在搜索会话内容...",
        "sessionCleanupPreview": "正在生成永久删除确认。",
        "sessionCleanupExecute": "永久删除请求已进入本地事务门禁。",
        "sessionCleanupCancel": "已取消会话删除确认。",
        "sessionTransfer": "会话 Provider 复制/迁移请求已提交。",
    }
    status = _status(labels.get(action, "会话清理命令已提交。"))
    status["sessionCleanupRequestId"] = request_id
    status["sessionCleanupAction"] = action
    return status


def actionable_session_ids(payload: Mapping[str, object] | None) -> set[str]:
    if not isinstance(payload, Mapping):
        return set()
    result: set[str] = set()
    for window_name in ("today", "week", "month"):
        window = payload.get(window_name)
        if not isinstance(window, Mapping):
            continue
        for collection_name in ("sessions", "topSessionsByUsage", "topSessionsByCost"):
            sessions = window.get(collection_name)
            if not isinstance(sessions, list):
                continue
            for item in sessions:
                if not isinstance(item, Mapping) or not bool(
                    item.get("actionable", item.get("canActivate", False))
                ):
                    continue
                session_id = str(item.get("id") or item.get("sessionId") or "").strip()
                try:
                    canonical = str(uuid.UUID(session_id))
                except (ValueError, AttributeError, TypeError):
                    continue
                if canonical == session_id.casefold():
                    result.add(canonical)
    return result


def usage_insights_workdir(
    payload: Mapping[str, object] | None, session_id: str
) -> Path | None:
    if not isinstance(payload, Mapping):
        return None
    for window_name in ("today", "week", "month"):
        window = payload.get(window_name)
        if not isinstance(window, Mapping):
            continue
        for collection_name in ("sessions", "topSessionsByUsage", "topSessionsByCost"):
            sessions = window.get(collection_name)
            if not isinstance(sessions, list):
                continue
            for item in sessions:
                if not isinstance(item, Mapping):
                    continue
                candidate_id = str(item.get("id") or item.get("sessionId") or "").strip().casefold()
                if candidate_id != session_id:
                    continue
                raw_path = str(item.get("workdir") or "").strip()
                if not raw_path:
                    continue
                path = Path(raw_path)
                if path.is_absolute() and path.is_dir():
                    return path
    return None


def handle_insights_command(
    command: Mapping[str, Any], ports: RuntimeCommandPorts
) -> dict[str, object] | object:
    action = str(command.get("action") or "").strip()
    if action not in {"usageInsightsRefresh", "openUsageInsightsSession", "openUsageInsightsWorkdir"}:
        return UNHANDLED
    request_id = str(command.get("requestId") or command.get("id") or "")
    if action == "usageInsightsRefresh":
        refresh = getattr(ports.insights_worker, "request_refresh", None)
        if not callable(refresh) or not refresh(request_id=request_id):
            status = _status("用量洞察刷新器当前不可用。", kind="error")
        else:
            status = _status("用量洞察刷新已开始。")
        status["usageInsightsRequestId"] = request_id
        return status
    session_id = str(command.get("sessionId") or "").strip().casefold()
    if action == "openUsageInsightsWorkdir":
        workdir = usage_insights_workdir(ports.insights_payload, session_id)
        if workdir is None:
            return _status("该会话没有可打开的工作目录。", kind="error")
        try:
            _open_system_path(workdir)
        except OSError as exc:
            error_detail = _exc_detail_log(
                exc, tag="usage_insights_workdir_open_failed"
            )
            return _status(
                f"无法打开工作目录：{error_detail}",
                kind="error",
            )
        return _status("已打开工作目录。")
    if session_id not in actionable_session_ids(ports.insights_payload):
        return _status(
            "该会话已归档、标识不完整或不在当前洞察结果中，未执行跳转。",
            kind="error",
        )
    if ports.activate_session is None:
        return _status("当前 Renderer 会话切换器不可用。", kind="error")
    result = ports.activate_session(
        {
            "action": "activateSession",
            "sessionId": session_id,
            "targetTitle": str(command.get("targetTitle") or "").strip(),
            "workdir": str(command.get("workdir") or "").strip(),
        }
    )
    if result is None or not (
        bool(getattr(result, "ok", False))
        or str(getattr(result, "status", "")) == "already-active"
    ):
        return _status(
            str(getattr(result, "message", "") or "无法打开该会话。"), kind="error"
        )
    return _status("已切换到所选会话。")


def _update_status(state: object, fallback: str) -> dict[str, object]:
    return _status(
        str(getattr(state, "message", "") or getattr(state, "title", "") or fallback),
        kind="error" if getattr(state, "error", "") else "",
    )


def handle_general_command(
    command: Mapping[str, Any], ports: GeneralCommandPorts
) -> dict[str, object]:
    action = str(command.get("action") or "").strip()
    try:
        if action == "codexCliDiscover":
            if ports.codex_cli_discover is None:
                return _status("Codex CLI 启动器当前不可用。", kind="error")
            payload = ports.codex_cli_discover(command)
            status = _status("终端和 Codex Desktop 工作目录已刷新。")
            status["codexCli"] = dict(payload)
            return status
        if action == "codexCliLaunch":
            if ports.codex_cli_launch is None:
                return _status("Codex CLI 启动器当前不可用。", kind="error")
            result = ports.codex_cli_launch(command)
            if bool(result.get("cancelled")):
                status = _status("已停止 Codex CLI 启动，未创建终端。")
                status["codexCliLaunchCancelled"] = True
                return status
            launch_mode = "新标签页" if bool(result.get("openedAsTab")) else "新终端窗口"
            status = _status(
                f"已在 {str(result.get('terminal') or '所选终端')} 的{launch_mode}中启动 Codex CLI。"
            )
            status["codexCliLaunch"] = dict(result)
            return status
        if action == "deleteProvider":
            if ports.delete_provider is None:
                return _status("供应商删除当前不可用。", kind="error")
            result = ports.delete_provider(command)
            return dict(result)
        if action == "fetchProviderModels":
            if ports.fetch_provider_models is None:
                return _status("模型列表获取当前不可用。", kind="error")
            base_url = str(command.get("baseUrl") or command.get("base_url") or "").strip()
            api_key = str(command.get("apiKey") or command.get("api_key") or "").strip()
            try:
                models = ports.fetch_provider_models(base_url, api_key)
            except ValueError as exc:
                return _status(
                    _exc_detail_log(exc, tag='fetch_provider_models_failed'),
                    kind="error",
                )
            status = _status(f"已获取 {len(models)} 个模型。")
            status["providerConnected"] = True
            status["models"] = list(models)
            return status
        if action == "codexCliFetchModels":
            if ports.fetch_cli_provider_models is None:
                return _status("模型列表获取当前不可用。", kind="error")
            provider = str(command.get("provider") or "").strip()
            try:
                payload = ports.fetch_cli_provider_models(provider)
            except ValueError as exc:
                return _status(
                    _exc_detail_log(exc, tag='fetch_cli_provider_models_failed'),
                    kind="error",
                )
            status = _status(
                f"已获取 {len([item for item in payload.get('models', [])])} 个模型。"
            )
            status["codexCliModels"] = dict(payload)
            return status
        if action == "providerChatTest":
            if ports.send_provider_chat_probe is None:
                return _status("聊天测试当前不可用。", kind="error")
            base_url = str(command.get("baseUrl") or command.get("base_url") or "").strip()
            api_key = str(command.get("apiKey") or command.get("api_key") or "").strip()
            model = str(command.get("model") or "").strip()
            message = str(command.get("message") or "hi").strip() or "hi"
            result = ports.send_provider_chat_probe(base_url, api_key, model, message)
            status = _status(
                str(result.get("reply") or result.get("error") or "聊天测试无结果。"),
                kind="" if result.get("ok") else "error",
            )
            status["providerChatTest"] = dict(result)
            return status
        if action == "codexCliChatTest":
            if ports.send_cli_chat_probe is None:
                return _status("聊天测试当前不可用。", kind="error")
            provider = str(command.get("provider") or "").strip()
            model = str(command.get("model") or "").strip()
            message = str(command.get("message") or "hi").strip() or "hi"
            result = ports.send_cli_chat_probe(provider, model, message)
            status = _status(
                str(result.get("reply") or result.get("error") or "聊天测试无结果。"),
                kind="" if result.get("ok") else "error",
            )
            status["codexCliChatTest"] = dict(result)
            return status
        if action == "savePricing":
            codex_provider_result: Mapping[str, object] | None = None
            codex_updates = command.get("codexProviders")
            if codex_updates:
                if ports.save_codex_providers is None:
                    raise RuntimeError("Codex provider 配置写入当前不可用。")
                codex_provider_result = ports.save_codex_providers(codex_updates)
            _config, changed_count = _save_versioned_price_changes(
                ports,
                command.get("settings"),
                _current_pricing_effective_at(),
            )
            provider_suffix = ""
            if codex_provider_result:
                provider_ids = [
                    str(item or "")
                    for item in codex_provider_result.get("providerIds", [])
                    if str(item or "")
                ]
                if provider_ids:
                    provider_suffix = f"；Codex provider 已更新：{', '.join(provider_ids)}"
            return _status(
                f"已保存 {changed_count} 个新价格版本；已有记录不变{provider_suffix}。"
            )
        if action in {"pricingImportPreview", "pricingImportCommit"}:
            default_effective_at = _current_pricing_effective_at()
            payload = _pricing_payload_with_default_effective_at(
                command.get("payload"),
                default_effective_at,
            )
            config = ports.load_config()
            preview = config.preview_pricing_import(payload)
            if action == "pricingImportPreview":
                status = _status(
                    "导入预览已生成；确认冲突处理后才会写入价格配置。"
                )
                preview_payload = preview.to_dict()
                preview_payload.update(
                    {
                        "addedCount": preview.added_count,
                        "updatedCount": preview.updated_count,
                        "skippedCount": preview.skipped_count,
                    }
                )
                status["pricingPreview"] = preview_payload
                status["pricingPayload"] = payload
                return status
            conflict_policy = str(command.get("conflictPolicy") or "cancel")
            updated, result = config.apply_pricing_import(
                preview,
                conflict_policy=conflict_policy,
            )
            updated = _sync_imported_current_prices(updated, preview)
            ports.save_config(updated)
            status = _status(
                "价格导入完成："
                f"新增 {result.added_count}，更新 {result.updated_count}，"
                f"跳过 {result.skipped_count}。"
            )
            status["pricingImportResult"] = {
                "addedCount": result.added_count,
                "updatedCount": result.updated_count,
                "skippedCount": result.skipped_count,
                "importedAt": datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                ),
            }
            return status
        if action == "fetchPricesPreview":
            config = ports.load_config()
            provider = str(command.get("provider") or "").strip().lower()
            provider_url = (
                config.provider_settings[provider].pricing_url
                if provider and provider in config.provider_settings
                else config.pricing_url
            )
            url = str(command.get("url") or provider_url or "").strip()
            fetched = ports.fetch_prices(url)
            legacy_payload = {
                "model_prices": {
                    key: (
                        value.to_dict()
                        if isinstance(value, ModelPrice)
                        else dict(value)
                    )
                    for key, value in fetched.items()
                }
            }
            payload = _pricing_payload_with_default_effective_at(
                legacy_payload,
                _current_pricing_effective_at(),
            )
            if provider:
                payload["prices"] = [
                    {**row, "provider": provider}
                    for row in payload["prices"]
                    if isinstance(row, Mapping)
                ]
            preview = config.preview_pricing_import(payload)
            status = _status(
                f"已拉取 {len(fetched)} 个模型价格；确认后才会保存。"
            )
            preview_payload = preview.to_dict()
            preview_payload.update(
                {
                    "addedCount": preview.added_count,
                    "updatedCount": preview.updated_count,
                    "skippedCount": preview.skipped_count,
                }
            )
            status["pricingPreview"] = preview_payload
            status["pricingPayload"] = payload
            status["pricingUrl"] = url
            return status
        if action in {"pricingExport", "pricingTemplate"}:
            try:
                output_path, used_template = _export_pricing_to_program_root(
                    ports.load_config()
                )
            except (OSError, ValueError) as exc:
                error_detail = _exc_detail_log(exc, tag="pricing_export_failed")
                return _status(
                    f"价格 JSON 生成失败：{error_detail}",
                    kind="error",
                )
            if used_template:
                message = (
                    "模型价格表为空，已使用 gpt-5.6-sol 内置价格模板生成："
                    f"{output_path}"
                )
            else:
                message = f"当前价格 JSON 已生成：{output_path}"
            status = _status(message)
            status["filename"] = output_path.name
            status["pricingPath"] = str(output_path)
            status["pricingDirectory"] = str(output_path.parent)
            status["pricingUsedTemplate"] = used_template
            status["mimeType"] = "application/json"
            return status
        if action == "pricingOpen":
            if ports.pricing_open_path is None:
                return _status("当前系统不支持打开价格文件。", kind="error")
            try:
                path = _pricing_file_path(command.get("filename"))
                ports.pricing_open_path(path)
            except (OSError, ValueError) as exc:
                error_detail = _exc_detail_log(exc, tag="pricing_open_failed")
                return _status(
                    f"价格文件打开失败：{error_detail}",
                    kind="error",
                )
            status = _status(f"已请求打开价格 JSON：{path}")
            status["pricingPath"] = str(path)
            return status
        if action in {"save", "applyDisplayMode"}:
            settings_payload = command.get("settings")
            previous_config = ports.load_config()
            config = runtime_settings.config_from_payload(previous_config, settings_payload)
            if _pricing_version_state_changed(previous_config, config):
                raise ValueError("价格版本只能通过保存价格或导入预览流程写入。")
            if action == "save" and _price_updates(previous_config, config):
                raise ValueError("价格有变更，请使用价格保存流程。")
            codex_provider_result: Mapping[str, object] | None = None
            codex_updates = command.get("codexProviders")
            if action == "save" and codex_updates:
                if ports.save_codex_providers is None:
                    raise RuntimeError("Codex provider 配置写入当前不可用。")
                codex_provider_result = ports.save_codex_providers(codex_updates)
            ports.save_config(config)
            if action == "applyDisplayMode":
                return _status(
                    "Renderer 方案已保存；当前会话已处于内嵌显示，无需重启。"
                )
            if str(command.get("section") or "") == "restReminder":
                started_at_ms = (
                    settings_payload.get("rest_reminder_timer_started_at_ms")
                    if isinstance(settings_payload, Mapping)
                    else None
                )
                if ports.rest_reminder is not None and started_at_ms is not None:
                    ports.rest_reminder.adjust_cycle_started_at_ms(started_at_ms)
                    status = _status("提醒设置已保存，已按指定时间校正本轮计时。")
                else:
                    status = _status("提醒设置已保存；休息结束后会自动开始下一轮。")
                status["restReminderSaved"] = True
                status["restReminderSaveRequestId"] = str(
                    command.get("requestId") or command.get("id") or ""
                )
                return status
            if codex_provider_result:
                provider_ids = [
                    str(item or "")
                    for item in codex_provider_result.get("providerIds", [])
                    if str(item or "")
                ]
                # 修改了默认 Codex App Provider 时，Codex Desktop 在启动时才加载
                # config.toml 与用户环境变量，必须重启 Codex Desktop 才能生效，
                # 因此返回带 restartVisible + restartCodex 的状态，前端据此弹出
                # 「立即重启 Codex Desktop / 稍后重启」提示。
                if codex_provider_result.get("defaultProviderEdited"):
                    return runtime_settings.settings_status(
                        "默认 Codex App Provider 配置已保存，需要重启 Codex Desktop 才能生效。",
                        restart_visible=True,
                        restart_codex=True,
                    )
                suffix = f"；Codex provider 已更新：{', '.join(provider_ids)}" if provider_ids else ""
                return _status(f"设置已保存，相关显示会自动刷新{suffix}。")
            return _status("设置已保存，相关显示会自动刷新。")
        if action.startswith("restReminder"):
            reminder = ports.rest_reminder
            if action == "restReminderAck":
                if reminder is not None:
                    reminder.acknowledge()
                return _status("休息提醒状态已更新。")
            if action == "restReminderStart":
                ok = bool(reminder.start_rest()) if reminder is not None else False
                return _status(
                    "已开始休息计时。" if ok else "当前状态不能开始休息。",
                    kind="" if ok else "error",
                )
            if action == "restReminderFinish":
                ok = bool(reminder.finish_rest()) if reminder is not None else False
                return _status(
                    "本次休息已结束，新一轮专注计时已开始。"
                    if ok
                    else "当前没有正在进行的休息。",
                    kind="" if ok else "error",
                )
            if action == "restReminderCredit":
                ok = bool(
                    reminder.credit_early_rest(command.get("minutes"))
                    if reminder is not None
                    else False
                )
                try:
                    minutes = int(command.get("minutes") or 0)
                except (TypeError, ValueError):
                    minutes = 0
                return _status(
                    f"已记录休息 {minutes} 分钟，新一轮专注计时已开始。"
                    if ok
                    else "休息时长无效或当前状态不能记录。",
                    kind="" if ok else "error",
                )
            if action == "restReminderPostpone":
                ok = bool(reminder.postpone()) if reminder is not None else False
                return _status(
                    "已安排稍后提醒。" if ok else "这次提醒已经延后过了。",
                    kind="" if ok else "error",
                )
            result = (
                reminder.test_notification()
                if reminder is not None
                else {"status": "failed", "error": "提醒服务未启动"}
            )
            sent = str(result.get("status") or "") == "sent"
            if bool(result.get("preview")):
                return _status(
                    "已发送系统通知，并弹出实际休息提醒预览。关闭预览不会改变当前计时。"
                    if sent
                    else f"已弹出实际休息提醒预览；系统通知失败：{result.get('error') or '未知错误'}",
                    kind="" if sent else "error",
                )
            return _status(
                "系统通知测试已发送。"
                if sent
                else f"系统通知发送失败：{result.get('error') or '未知错误'}",
                kind="" if sent else "error",
            )
        if action == "fetchPrices":
            return _status(
                "旧版直接拉取已停用，请先预览价格后确认导入。",
                kind="error",
            )
        if action == "restart":
            ports.request_restart()
            return _status("已请求重启 HUD；daemon 模式会自动恢复。")
        if action == "restartCodex":
            if ports.request_restart_codex is None:
                return _status("当前运行模式不支持立即重启 Codex Desktop。", kind="error")
            ports.request_restart_codex()
            return _status("已请求立即重启 Codex Desktop；HUD 将在重启后重新连接。")
        if action == "exit":
            ports.request_exit()
            return _status("已请求退出 HUD；后台守护进程也会一并停止。")
        if action == "checkUpdate":
            if ports.update_manager is not None:
                return _update_status(
                    ports.update_manager.request_check(auto_download=False),
                    "正在检查更新...",
                )
            info = ports.check_update()
            if getattr(info, "error", ""):
                return _status(f"检查更新失败：{info.error}", kind="error")
            if getattr(info, "available", False):
                return _status(
                    f"发现新版本 {info.latest_version}，安装包：{info.asset_name}"
                )
            return _status(f"当前已是最新版本（{info.current_version}）。")
        if action == "installUpdate":
            if ports.update_manager is not None:
                return _update_status(
                    ports.update_manager.request_install(), "正在准备安装更新..."
                )
            info = ports.check_update()
            if getattr(info, "error", ""):
                return _status(f"检查更新失败：{info.error}", kind="error")
            if not getattr(info, "available", False):
                return _status(f"当前已是最新版本（{info.current_version}）。")
            ports.install_update(info)
            ports.request_restart()
            return _status(f"已启动 {info.asset_name}，安装器会先关闭当前 HUD。")
        if action == "installDesktopOverlay":
            status = ports.overlay_status()
            version = str(status.get("version") or "").strip()
            if bool(status.get("installed")):
                return _status(
                    f"气泡组件已可用{f'（PySide6 {version}）' if version else ''}；可直接启用会话进度气泡。"
                )
            if bool(status.get("installing")):
                return _status("气泡组件正在安装；完成后点击“启用气泡”。")
            if not bool(status.get("canInstall")):
                return runtime_settings.settings_status(
                    "当前运行环境不能在线安装气泡组件；请安装带会话进度气泡的版本后重启 HUD。",
                    kind="error",
                    restart_visible=bool(status.get("requiresRestart")),
                )
            if ports.forced_missing_with_real_install():
                ports.clear_forced_missing()
                version = ports.pyside_version()
                return _status(
                    f"已检测到本机已安装气泡组件{f'（PySide6 {version}）' if version else ''}；可直接启用会话进度气泡。"
                )
            if ports.start_overlay_install():
                refreshed = ports.overlay_status()
                if bool(refreshed.get("installed")):
                    version = str(refreshed.get("version") or "").strip()
                    return _status(
                        f"已检测到本机已安装气泡组件{f'（PySide6 {version}）' if version else ''}；可直接启用会话进度气泡。"
                    )
                return _status("已开始安装气泡组件；完成后点击“启用气泡”。")
            return _status(
                "无法启动 PySide6 安装；请在终端运行 pip install PySide6>=6.8。",
                kind="error",
            )
        if action == "enableDesktopOverlay":
            if ports.forced_missing_with_real_install():
                ports.clear_forced_missing()
            status = ports.overlay_status()
            if not bool(status.get("installed")):
                return runtime_settings.settings_status(
                    "还没检测到气泡组件；安装完成后再点一次“启用气泡”。",
                    kind="error",
                    restart_visible=bool(status.get("requiresRestart")),
                )
            config = ports.load_config()
            if int(config.work_overlay_max_items or 0) <= 0:
                config = replace(
                    config, work_overlay_max_items=ports.default_overlay_limit()
                )
                ports.save_config(config)
            if ports.work_overlay is not None:
                ports.work_overlay.reset_runtime_availability()
            version = str(status.get("version") or "").strip()
            return _status(
                f"会话进度气泡已启用{f'（PySide6 {version}）' if version else ''}。"
            )
        if action == "updateAction":
            if ports.update_manager is None:
                return _status("当前会话未启用自动更新控制器。", kind="error")
            return _update_status(
                ports.update_manager.handle_click(), "更新操作已提交。"
            )
        if action == "dismissWarningsToday":
            if not ports.dismiss_warnings_today():
                return _status("无法保存预警关闭状态：配置路径不可用。", kind="error")
            return _status("今天不再显示预算预警。")
        return _status(f"无法处理未知设置命令：{action or 'empty'}", kind="error")
    except Exception as exc:
        error_detail = _exc_detail_log(
            exc, tag="renderer_settings_command_failed"
        )
        return _status(
            f"设置命令执行失败：{error_detail}",
            kind="error",
        )



def _handle_renderer_session_cleanup_command(
    command: Mapping[str, Any],
    context: RuntimeContext,
) -> dict[str, object]:
    result = handle_cleanup_command(
        command,
        RuntimeCommandPorts(
            cleanup_worker=getattr(context, "session_cleanup_worker", None),
            cleanup_manager=getattr(context, "session_cleanup_manager", None),
            session_index_job=getattr(context, "session_index_warm_job", None),
            desktop_thread_navigator=_desktop_thread_navigator(context),
        ),
    )
    if result is UNHANDLED:
        return _renderer_settings_status("无法处理未知会话清理命令。", kind="error")
    return result


def _usage_insights_actionable_session_ids(context: object) -> set[str]:
    return actionable_session_ids(
        getattr(context, "usage_insights_payload", {})
    )


def _handle_renderer_usage_insights_command(
    command: Mapping[str, Any],
    context: RuntimeContext,
    *,
    session_controller: SessionSwitchController | None,
) -> dict[str, object]:
    ports = RuntimeCommandPorts(
        insights_worker=getattr(context, "usage_insights_worker", None),
        insights_payload=getattr(context, "usage_insights_payload", None),
        activate_session=(
            lambda activation: _handle_work_overlay_command(
                activation,
                session_controller,
                prepare_window=True,
                backend_names=("cdp",),
            )
            if session_controller is not None
            else None
        ),
    )
    result = handle_insights_command(command, ports)
    if result is UNHANDLED:
        return _renderer_settings_status("无法处理未知用量洞察命令。", kind="error")
    return result


def _resolve_renderer_active_session_candidate(
    context: object,
    command: Mapping[str, object],
) -> bool:
    tracker = getattr(context, "active_session_tracker", None)
    resolver = getattr(tracker, "resolve_renderer_candidate", None)
    if not callable(resolver):
        return False
    try:
        selection_seq = int(command.get("selectionSeq") or 0)
    except (TypeError, ValueError):
        selection_seq = 0
    return bool(
        resolver(
            str(command.get("sessionId") or "").strip(),
            selection_seq=selection_seq,
        )
    )


def _handle_renderer_settings_command(
    command: Mapping[str, Any],
    context: RuntimeContext,
    restart_requested: Event,
    exit_requested: Event,
    update_manager: AutoUpdateManager | None = None,
    work_overlay: DesktopWorkOverlay | None = None,
    session_controller: SessionSwitchController | None = None,
    request_restart_codex: Callable[[], None] | None = None,
) -> dict[str, object]:
    command_ports = RuntimeCommandPorts(
        background_usage=getattr(context, "background_usage_runtime", None),
        cleanup_worker=getattr(context, "session_cleanup_worker", None),
        cleanup_manager=getattr(context, "session_cleanup_manager", None),
        session_index_job=getattr(context, "session_index_warm_job", None),
        desktop_thread_navigator=_desktop_thread_navigator(context),
        insights_worker=getattr(context, "usage_insights_worker", None),
        insights_payload=getattr(context, "usage_insights_payload", None),
        resolve_active_session=lambda candidate: _resolve_renderer_active_session_candidate(
            context, candidate
        ),
        activate_session=(
            lambda activation: _handle_work_overlay_command(
                activation,
                session_controller,
                prepare_window=True,
                backend_names=("cdp",),
            )
            if session_controller is not None
            else None
        ),
    )
    settings_store = getattr(context, "settings_store", None)
    settings_path = getattr(settings_store, "path", None)

    def load_config() -> UserConfig:
        load = getattr(settings_store, "load", None)
        return load() if callable(load) else UserConfig.defaults()

    def save_config(config: UserConfig) -> None:
        if settings_store is None:
            raise RuntimeError("配置存储当前不可用。")
        settings_store.save(config)
        context.settings_mtime = None
        context.reload_user_config()

    def install_update(info: object) -> None:
        installer = download_update_asset(info)
        launch_installer(installer)

    def discover_cli(command: Mapping[str, object]) -> Mapping[str, object]:
        provider = str(
            command.get("provider")
            or getattr(context, "app_provider", "")
            or ""
        ).strip().lower()
        return discover_codex_cli_options(
            provider=provider,
            sessions_root=getattr(context, "sessions_root", None),
            state_db_path=getattr(context, "state_db_path", None),
            current_workdir=Path.cwd(),
        )

    def launch_cli(command: Mapping[str, object]) -> Mapping[str, object]:
        provider = str(
            command.get("provider")
            or getattr(context, "app_provider", "")
            or ""
        ).strip().lower()
        transfer_session_id = str(
            command.get("sessionTransferResumeId")
            or command.get("session_transfer_resume_id")
            or ""
        ).strip()
        no_project = bool(command.get("noProject")) and not transfer_session_id
        workdir = str(Path.home()) if no_project else str(command.get("workdir") or "").strip()
        launch_command = str(command.get("command") or "")
        terminal_id = str(command.get("terminalId") or "").strip()
        codex_home: Path | None = None
        if transfer_session_id:
            manager = getattr(context, "session_cleanup_manager", None)
            resolve_target = getattr(manager, "workdir_for_transfer_target", None)
            if not callable(resolve_target):
                raise ValueError("目标 Provider 会话续聊校验当前不可用。")
            target_path = resolve_target(transfer_session_id, provider)
            if not isinstance(target_path, Path):
                raise ValueError(
                    "目标 Provider 会话未能重新验证，未启动续聊命令。"
                )
            sessions_root = getattr(context, "sessions_root", None)
            state_db_path = getattr(context, "state_db_path", None)
            codex_home = (
                Path(sessions_root).parent
                if sessions_root is not None
                else None
            )
            options = discover_codex_cli_options(
                provider=provider,
                sessions_root=sessions_root,
                state_db_path=state_db_path,
                current_workdir=target_path,
                codex_home=codex_home,
            )
            proxy = options.get("proxy")
            proxy_values = proxy if isinstance(proxy, Mapping) else {}
            terminal_options = options.get("terminals")
            selected_terminal = next(
                (
                    item
                    for item in terminal_options
                    if isinstance(item, Mapping)
                    and str(item.get("id") or "") == str(
                        options.get("defaultTerminal") or ""
                    ).strip()
                ),
                {},
            ) if isinstance(terminal_options, Sequence) and not isinstance(
                terminal_options, (str, bytes, bytearray)
            ) else {}
            shell = str(selected_terminal.get("shell") or "powershell").strip().lower()
            if shell not in {"powershell", "cmd", "bash", "zsh"}:
                shell = "powershell"
            launch_command = build_codex_cli_command(
                provider=provider,
                profile=str(options.get("profile") or ""),
                default_provider=str(options.get("defaultProvider") or ""),
                permission="workspace-write",
                resume=True,
                resume_session_id=transfer_session_id,
                use_proxy=bool(proxy_values.get("enabled")),
                proxy_port=proxy_values.get("port", 7897),
                workdir=str(target_path),
                shell=shell,
            )
            workdir = str(target_path)
            terminal_id = str(options.get("defaultTerminal") or "").strip()
        result = dict(
            launch_codex_cli(
                terminal_id=terminal_id,
                command=launch_command,
                workdir=workdir,
                provider=provider,
                codex_home=codex_home,
                cancel_requested=(
                    command.get("_codexCliCancelRequested")
                    if callable(command.get("_codexCliCancelRequested"))
                    else None
                ),
                commit_spawn=(
                    command.get("_codexCliCommitSpawn")
                    if callable(command.get("_codexCliCommitSpawn"))
                    else None
                ),
            )
        )
        if transfer_session_id:
            result["sessionTransferResumeId"] = transfer_session_id
            result["provider"] = provider
        return result

    def fetch_cli_provider_models(provider: str) -> Mapping[str, object]:
        return fetch_provider_models_for_cli(provider)

    def chat_probe_for_provider(
        base_url: str, api_key: str, model: str, message: str
    ) -> Mapping[str, object]:
        return send_provider_chat_probe(base_url, api_key, model, message)

    def chat_probe_for_cli(provider: str, model: str, message: str) -> Mapping[str, object]:
        return send_cli_chat_probe(provider, model, message)

    def delete_provider(command: Mapping[str, object]) -> Mapping[str, object]:
        # config.toml / 单价删除始终在同步请求阶段完成，立即生效并让前端关闭
        # loading 遮罩、移除供应商列表项；历史会话清理（若勾选）才进入后台 worker，
        # 由 providerDelete 任务在后台线程执行，不影响删除动作的速度。
        result = delete_provider_for_context(context, command)
        if bool(command.get("deleteSessionHistory")):
            worker = getattr(context, "session_cleanup_worker", None)
            enqueue = getattr(worker, "enqueue", None)
            if not callable(enqueue):
                return {
                    **result,
                    **_status(
                        "供应商配置已删除，但会话历史后台清理未能启动，请在存储页重试。",
                        kind="warning",
                    ),
                    "providerDeleteHistoryEnqueueFailed": True,
                }
            provider_delete_command = dict(command)
            provider_delete_command["action"] = "providerDelete"
            try:
                accepted = enqueue(provider_delete_command)
            except Exception as exc:
                error_detail = _exc_detail_log(
                    exc, tag="provider_delete_history_enqueue_failed"
                )
                return {
                    **result,
                    **_status(
                        f"供应商配置已删除，但会话历史后台清理未能启动：{error_detail}",
                        kind="warning",
                    ),
                    "providerDeleteHistoryEnqueueFailed": True,
                }
            request_id = str(
                accepted.get("requestId")
                if isinstance(accepted, Mapping)
                else command.get("requestId") or command.get("id") or ""
            )
            return {
                **result,
                **_status(
                    "供应商配置已删除；正在后台清理该供应商的会话历史，可继续操作。"
                ),
                "providerDeleteRequestId": request_id,
            }
        return result

    general_ports = GeneralCommandPorts(
        load_config=load_config,
        save_config=save_config,
        fetch_prices=fetch_model_prices,
        rest_reminder=getattr(context, "rest_reminder", None),
        update_manager=update_manager,
        work_overlay=work_overlay,
        request_restart=restart_requested.set,
        request_restart_codex=request_restart_codex,
        request_exit=exit_requested.set,
        check_update=lambda: check_for_update(current_version=__version__),
        install_update=install_update,
        overlay_status=_desktop_overlay_dependency_status,
        start_overlay_install=_start_desktop_overlay_install,
        clear_forced_missing=lambda: _set_force_desktop_overlay_missing(False),
        forced_missing_with_real_install=lambda: bool(
            _desktop_overlay_dependency_status().get("forcedMissing")
            and _desktop_overlay_dependency_status().get("realInstalled")
        ),
        pyside_version=_pyside6_version,
        default_overlay_limit=lambda: min(
            DEFAULT_WORK_OVERLAY_MAX_ITEMS, _work_overlay_screen_max_items()
        ),
        dismiss_warnings_today=lambda: bool(
            settings_path is not None and not dismiss_warning_for_today(settings_path)
        ),
        pricing_open_path=_open_system_path,
        save_codex_providers=lambda updates: save_provider_configs(updates),
        delete_provider=delete_provider,
        fetch_provider_models=lambda base_url, api_key: fetch_provider_models(base_url, api_key),
        fetch_cli_provider_models=fetch_cli_provider_models,
        send_provider_chat_probe=chat_probe_for_provider,
        send_cli_chat_probe=chat_probe_for_cli,
        codex_cli_discover=discover_cli,
        codex_cli_launch=launch_cli,
    )
    result = dispatch_command(command, command_ports, general_ports)
    session_index = result.get("sessionIndex") if isinstance(result, Mapping) else None
    if isinstance(session_index, Mapping):
        try:
            remembered = dict(session_index)
            remembered.pop("accepted", None)
            remembered.pop("requestId", None)
            context.session_index_payload = remembered
        except (AttributeError, TypeError):
            pass
    return result

__all__ = [
    "RuntimeCommandPorts",
    "GeneralCommandPorts",
    "PRICING_EXPORT_FILENAME",
    "UNHANDLED",
    "actionable_session_ids",
    "correlate_status",
    "dispatch_command",
    "handle_background_command",
    "handle_active_session_command",
    "handle_cleanup_command",
    "handle_insights_command",
    "handle_session_index_command",
    "handle_general_command",
]
