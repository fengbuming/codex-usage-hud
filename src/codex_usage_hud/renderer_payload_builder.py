"""Renderer full and partial payload projection owner.

This module contains the formatting and snapshot-to-domain projection logic.
The UI renderer module exposes a small compatibility facade for existing
imports while callers migrate to this owner.
"""

from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any

from . import __version__
from .config import DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED
from .config import UserConfig, default_model_prices, warning_dismissed_today
from .core.connection_health import ConnectionHealth
from .core.parser import (
    CostEstimator,
    ParsedSession,
    RequestRound,
    TaskHistory,
    seconds_between,
)
from .core.runtime_errors import RuntimeErrorEvent
from .platforms.active_session import is_new_session_source, is_pending_session_source
from .platforms.codex_theme import CodexThemeSnapshot
from . import renderer_catalog
from . import renderer_activity_projection
from . import renderer_request_projection
from .renderer_payloads import RendererHudPayload, payload_domains
from .renderer_presenters import budget as renderer_budget
from .renderer_presenters import common as renderer_common
from .renderer_presenters import session as renderer_session

TOKEN_LEGEND_TEXT = "↑ 输入  ↻ 缓存  ↓ 输出\n◇ 推理  ∑ 合计  $ 金额\n◎ 缓存率  ~ 估算"
TOP_EXPANDED_HEADER_FALLBACK = "Codex 会话 / 预算"
_COST_ESTIMATOR = CostEstimator()
COMPOSER_TIKTOKEN_BADGE_ENABLED = DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED


def set_cost_estimator(estimator: CostEstimator) -> None:
    """Use the current user-configured price table for renderer formatting."""
    global _COST_ESTIMATOR
    _COST_ESTIMATOR = estimator


def _renderer_theme_payload(snapshot: CodexThemeSnapshot | None) -> dict[str, object]:
    if snapshot is None or snapshot.source not in {"cdp", "persisted"}:
        return {}
    return {
        "variant": snapshot.effective_variant,
        "source": snapshot.source,
        "tokens": snapshot.hud_tokens.to_dict(),
        "effectiveTheme": snapshot.effective_theme.to_dict(),
    }

def _configured_model_catalog_path() -> Path | None:
    return renderer_catalog.configured_model_catalog_path()


def _model_catalog_candidate_paths() -> list[Path]:
    return renderer_catalog.model_catalog_candidate_paths()


def _normalize_catalog_model(model: object) -> dict[str, object] | None:
    return renderer_catalog.normalize_catalog_model(model)


def _renderer_model_catalog_payload() -> list[dict[str, object]]:
    return renderer_catalog.model_catalog_payload()


def _renderer_hud_script_with_model_catalog(
    catalog: list[dict[str, object]] | None = None,
) -> str:
    if catalog is None:
        catalog = _renderer_model_catalog_payload()
    return renderer_catalog.renderer_hud_script_with_model_catalog(catalog)


RENDERER_HUD_SCRIPT = renderer_catalog.RENDERER_HUD_SCRIPT


def _payload_domains(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    return payload_domains(payload)


def _active_session_provider(snapshot: ParsedSession) -> str:
    """Normalize the followed session's rollout provider for the renderer.

    ``unknown`` comes from the parser when a rollout has no provider yet
    (new/pending sessions); the renderer falls back to the config default.
    """
    provider = str(getattr(snapshot, "model_provider", "") or "").strip().lower()
    return "" if provider in {"", "unknown"} else provider


def payload_from_snapshot(
    snapshot: ParsedSession,
    *,
    settings: UserConfig | None = None,
    active_display_mode: str = "renderer",
    settings_path: Path | str | None = None,
    settings_bridge_url: str = "",
    background_usage_bridge_url: str = "",
    session_index_url: str = "",
    session_index: dict[str, object] | None = None,
    background_usage_revision: int = 0,
    background_usage_notification: dict[str, object] | None = None,
    rest_reminder: dict[str, object] | None = None,
    settings_command_status: dict[str, object] | None = None,
    support_images: list[dict[str, str]] | None = None,
    theme: dict[str, object] | None = None,
    update_state: dict[str, object] | None = None,
    debug: bool = False,
    runtime_errors: list[RuntimeErrorEvent | dict[str, object]] | None = None,
    work_overlay_selectable_max: int = 6,
    desktop_overlay_dependency: dict[str, object] | None = None,
    provider_registry: dict[str, object] | None = None,
    app_provider: str = "",
    default_provider: str = "",
    usage_insights: dict[str, object] | None = None,
    session_cleanup: dict[str, object] | None = None,
    connection_health: dict[str, object] | ConnectionHealth | None = None,
    request_rows_limit: int = renderer_request_projection.REQUEST_ROWS_PAGE_SIZE,
) -> RendererHudPayload:
    new_session = _is_new_session_snapshot(snapshot)
    pending_session = _is_pending_session_snapshot(snapshot)
    session_cost = _session_cost(snapshot)
    warnings_dismissed = (
        warning_dismissed_today(settings_path) if settings_path is not None else False
    )
    top_details = _top_details(snapshot, session_cost)
    if warnings_dismissed:
        top_details["warnings"] = _format_notice(
            snapshot,
            include_budget_warnings=False,
        )
    top_line = (
        f"{_top_session_usage_summary(snapshot, session_cost)} | "
        f"今日 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)} | "
        f"本周 {_format_usage_money(snapshot.week_tokens, snapshot.week_cost_usd)} | "
        f"状态 {_budget_status(snapshot)}"
    )
    if snapshot.error and snapshot.status in {"missing", "error"}:
        top_line = f"{_status_label(snapshot.status)} | {_compact(snapshot.error, 120)}"
    top_progress = _top_progress(snapshot)
    if snapshot.error and snapshot.status in {"missing", "error"}:
        top_progress = {
            "collapsed": [_top_progress_metric(top_line, 1.0, "error")],
            "budget": [],
        }
    request_line = _request_total_line(snapshot)
    if pending_session:
        request_line = _follow_feedback(snapshot)
    elif snapshot.follow_reason == "renderer-channel-unavailable":
        request_line = _follow_feedback(snapshot)
    if snapshot.request.error:
        request_line = f"本次 Token 出错 | {_compact(snapshot.request.error, 120)}"
    pre_send_estimate = ""
    pre_send_base_tokens = 0
    pre_send_breakdown: list[dict[str, object]] = []
    pre_send_input_price = 0.0
    pre_send_total_cost: float | None = None
    pre_send_has_prices = False
    activity_warning = False
    activity_reading_file = ""
    if COMPOSER_TIKTOKEN_BADGE_ENABLED:
        pre_send_estimate = snapshot.estimate_base.short_label()
        pre_send_base_tokens = int(snapshot.estimate_base.total_tokens or 0)
        pre_send_breakdown = snapshot.estimate_base.breakdown_rows()
        pre_send_input_price = float(snapshot.estimate_base.input_price_per_token or 0.0)
        pre_send_total_cost = snapshot.estimate_base.total_cost()
        pre_send_has_prices = bool(snapshot.estimate_base.has_prices)
        activity_warning = bool(snapshot.reading_activity.active)
        activity_reading_file = snapshot.reading_activity.warning_label()
    settings_payload = (settings or UserConfig.defaults()).to_dict()
    settings_payload["default_model_prices"] = {
        name: price.to_dict() for name, price in default_model_prices().items()
    }
    settings_payload["provider_registry"] = dict(provider_registry or {})
    settings_payload["app_provider"] = str(app_provider or "")
    # config.toml 顶层 model_provider（新会话默认供应商）的稳定值：与会被活跃
    # 会话供应商观察覆盖的 app_provider 不同，它只随 config 重载变化，供渲染器
    # 菜单栏「默认」徽标与供应商编辑器「默认 Codex App Provider」判定使用。
    settings_payload["default_provider"] = str(default_provider or "")
    return RendererHudPayload(
        top_line=top_line,
        request_line=request_line,
        session=_session_label(snapshot),
        model=snapshot.request.model or "n/a",
        active_session_provider=_active_session_provider(snapshot),
        source=snapshot.selection_source or "activity",
        request_status=snapshot.request.status or "waiting",
        last_event=_format_time(snapshot.last_event_time),
        refreshed_at=_format_time(snapshot.refreshed_at),
        new_session=new_session,
        pending_session=pending_session,
        selection_seq=int(snapshot.selection_seq or 0),
        session_id=str(snapshot.session_id or ""),
        renderer_session_id=str(snapshot.renderer_session_id or ""),
        selection_observed_at_ms=int(snapshot.selection_observed_at_ms or 0),
        follow_state=str(snapshot.follow_state or ""),
        follow_reason=str(snapshot.follow_reason or ""),
        follow_elapsed_ms=_follow_elapsed_ms(snapshot),
        follow_timing=dict(snapshot.follow_timing or {}),
        match_candidates=[
            dict(item) for item in getattr(snapshot, "match_candidates", []) or []
        ],
        warning=bool(
            snapshot.error
            or snapshot.request.error
            or snapshot.budget_error
            or (snapshot.budget_warnings and not warnings_dismissed)
        ),
        top_details=top_details,
        top_progress=top_progress,
        top_copies=_top_copy_texts(snapshot),
        request_rows=_request_rows(snapshot, limit=request_rows_limit),
        request_row_details=_request_row_details(snapshot, limit=request_rows_limit),
        request_rows_total=_request_rows_total(snapshot),
        observed_models=_observed_models(snapshot),
        settings=settings_payload,
        active_display_mode=str(active_display_mode or "renderer"),
        settings_path=str(settings_path or ""),
        settings_bridge_url=settings_bridge_url,
        background_usage_bridge_url=background_usage_bridge_url,
        session_index_url=session_index_url,
        session_index=dict(session_index or {}),
        background_usage_revision=max(0, int(background_usage_revision or 0)),
        background_usage_notification=dict(background_usage_notification or {}),
        rest_reminder=dict(rest_reminder or {}),
        settings_command_status=settings_command_status or {},
        usage_insights=usage_insights or {},
        session_cleanup=session_cleanup or {},
        work_overlay_selectable_max=max(1, int(work_overlay_selectable_max or 1)),
        desktop_overlay_dependency=desktop_overlay_dependency or {},
        support_images=support_images or [],
        theme=theme or {},
        update_state=update_state or {},
        app_version=__version__,
        pre_send_estimate=pre_send_estimate,
        pre_send_base_tokens=pre_send_base_tokens,
        pre_send_breakdown=pre_send_breakdown,
        pre_send_input_price=pre_send_input_price,
        pre_send_total_cost=pre_send_total_cost,
        pre_send_has_prices=pre_send_has_prices,
        activity_warning=activity_warning,
        activity_reading_file=activity_reading_file,
        debug=bool(debug),
        runtime_errors=_runtime_errors_payload(runtime_errors or []),
        connection_health=_connection_health_payload(connection_health),
    )


def session_switch_payload_from_snapshot(
    snapshot: ParsedSession,
    *,
    settings_path: Path | str | None = None,
    background_usage_notification: dict[str, object] | None = None,
    connection_health: dict[str, object] | ConnectionHealth | None = None,
) -> dict[str, object]:
    session_cost = _session_cost(snapshot)
    warnings_dismissed = (
        warning_dismissed_today(settings_path) if settings_path is not None else False
    )
    top_line = (
        f"{_top_session_usage_summary(snapshot, session_cost)} | "
        f"今日 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)} | "
        f"本周 {_format_usage_money(snapshot.week_tokens, snapshot.week_cost_usd)} | "
        f"状态 {_budget_status(snapshot)}"
    )
    if snapshot.error and snapshot.status in {"missing", "error"}:
        top_line = f"{_status_label(snapshot.status)} | {_compact(snapshot.error, 120)}"
    request_line = _request_total_line(snapshot)
    if _is_pending_session_snapshot(snapshot):
        request_line = _follow_feedback(snapshot)
    elif snapshot.follow_reason == "renderer-channel-unavailable":
        request_line = _follow_feedback(snapshot)
    if snapshot.request.error:
        request_line = f"本次 Token 出错 | {_compact(snapshot.request.error, 120)}"
    domain = {
        "topLine": top_line,
        "requestLine": request_line,
        "session": _session_label(snapshot),
        "model": snapshot.request.model or "n/a",
        "source": snapshot.selection_source or "activity",
        "requestStatus": snapshot.request.status or "waiting",
        "lastEvent": _format_time(snapshot.last_event_time),
        "refreshedAt": _format_time(snapshot.refreshed_at),
        "warning": bool(
            snapshot.error
            or snapshot.request.error
            or snapshot.budget_error
            or (snapshot.budget_warnings and not warnings_dismissed)
        ),
        "newSession": bool(_is_new_session_snapshot(snapshot)),
        "pendingSession": bool(_is_pending_session_snapshot(snapshot)),
        "selectionSeq": int(snapshot.selection_seq or 0),
        "sessionId": str(snapshot.session_id or ""),
        "rendererSessionId": str(snapshot.renderer_session_id or ""),
        "cachedPreview": False,
        "selectionObservedAt": int(snapshot.selection_observed_at_ms or 0),
        "followState": str(snapshot.follow_state or ""),
        "followReason": str(snapshot.follow_reason or ""),
        "followElapsedMs": _follow_elapsed_ms(snapshot),
        "followTiming": dict(snapshot.follow_timing or {}),
        "matchCandidates": [
            dict(item) for item in getattr(snapshot, "match_candidates", []) or []
        ],
        "backgroundUsageNotification": dict(background_usage_notification or {}),
        "connectionHealth": _connection_health_payload(connection_health),
        "activeSessionProvider": _active_session_provider(snapshot),
    }
    payload = dict(domain)
    payload["payloadDomains"] = {"sessionSwitch": dict(domain)}
    return payload


def _runtime_errors_payload(
    errors: list[RuntimeErrorEvent | dict[str, object]],
) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for error in errors:
        if isinstance(error, RuntimeErrorEvent):
            payload.append(error.to_payload())
        elif isinstance(error, dict):
            payload.append(dict(error))
    return payload


def _connection_health_payload(
    value: dict[str, object] | ConnectionHealth | None,
) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, ConnectionHealth):
        return value.to_payload()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _observed_models(snapshot: ParsedSession) -> list[str]:
    seen: set[str] = set()
    models: list[str] = []
    candidates = [snapshot.request.model]
    candidates.extend(item.model for item in _task_rows(snapshot))
    candidates.extend(
        item.model for item in getattr(snapshot, "session_request_history", []) or []
    )
    for model in candidates:
        text = str(model or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        models.append(text)
    return models


def _runtime_expression_params(expression: str) -> dict[str, object]:
    return {
        "expression": expression,
        "returnByValue": True,
        "allowUnsafeEvalBlockedByCSP": True,
    }


def _session_label(snapshot: ParsedSession) -> str:
    return renderer_session.session_label(
        snapshot,
        is_new_session=_is_new_session_snapshot,
        is_pending_session=_is_pending_session_snapshot,
        compact=_compact,
    )


def _follow_elapsed_ms(snapshot: ParsedSession) -> int:
    return renderer_session.follow_elapsed_ms(
        snapshot,
        int(time.time() * 1000),
    )


def _follow_feedback(snapshot: ParsedSession) -> str:
    return renderer_session.follow_feedback(snapshot)


def _is_new_session_snapshot(snapshot: ParsedSession) -> bool:
    return is_new_session_source(str(snapshot.selection_source or ""))


def _is_pending_session_snapshot(snapshot: ParsedSession) -> bool:
    return is_pending_session_source(str(snapshot.selection_source or ""))


def _status_label(value: str) -> str:
    return renderer_session.status_label(value)


def _request_status_label(value: str) -> str:
    return renderer_session.request_status_label(value)


def _activity_label(value: str) -> str:
    return renderer_session.activity_label(value)


def _short_num(value: int | None) -> str:
    return renderer_common.short_num(value)


def _format_money(value: float | None) -> str:
    return renderer_common.format_money(value)


def _format_realtime_money(value: float | None, estimated: bool) -> str:
    return renderer_common.format_realtime_money(
        value,
        estimated,
        money_formatter=_format_money,
    )


def _format_fixed_money(value: float | None, estimated: bool) -> str:
    return renderer_common.format_fixed_money(value, estimated)


def _fixed_token_total(value: int | None) -> str:
    return renderer_common.fixed_token_total(value, short_formatter=_short_num)


def _format_usage_money(tokens: int | None, cost: float | None) -> str:
    return renderer_common.format_usage_money(
        tokens,
        cost,
        short_formatter=_short_num,
        money_formatter=_format_money,
    )


def _format_time(value: datetime | None) -> str:
    return renderer_common.format_time(value)


def _format_start(value: datetime | None) -> str:
    return renderer_common.format_start(value)


def _gap_label(value: str) -> str:
    return renderer_session.gap_label(value)


def _copyable_tool_command(snapshot: ParsedSession) -> str | None:
    call = snapshot.slow.slowest_tool_call
    if call is None:
        return None
    raw_args = (call.args or "").strip()
    if not raw_args:
        return None
    try:
        payload = json.loads(raw_args)
    except json.JSONDecodeError:
        return raw_args
    if isinstance(payload, dict):
        command = payload.get("command")
        if command:
            return str(command)
    return raw_args


def _copyable_gap_detail(snapshot: ParsedSession) -> str | None:
    detail = snapshot.slow.longest_gap_detail
    if detail is None:
        return None
    return "\n".join(
        [
            f"类型: {_gap_label(detail.category)}",
            f"时长: {detail.duration_seconds:.1f}s",
            f"开始事件: {detail.from_event}",
            f"结束事件: {detail.to_event}",
            f"行号: {detail.start_line} -> {detail.end_line}",
        ]
    )


def _top_copy_texts(snapshot: ParsedSession) -> dict[str, str]:
    copies: dict[str, str] = {}
    tool_command = _copyable_tool_command(snapshot)
    if tool_command:
        copies["slow"] = tool_command
    gap_detail = _copyable_gap_detail(snapshot)
    if gap_detail:
        copies["gap"] = gap_detail
    return copies


def _task_snapshot(snapshot: ParsedSession, task: TaskHistory) -> ParsedSession:
    """Project one historical task through the existing activity presenters."""
    projected = copy.copy(snapshot)
    projected.task_prompt = task.prompt
    projected.task_turn_id = task.turn_id
    projected.task_index = task.index
    projected.task_count = task.count
    projected.task_started_at = task.started_at
    projected.task_completed_at = task.completed_at
    projected.task_aborted_at = task.aborted_at
    projected.final_answer_at = task.final_answer_at
    projected.last_event_time = task.last_event_time
    projected.request = task.request
    projected.request_history = list(task.request_history)
    projected.activity = task.activity
    projected.activity_steps = [
        copy.copy(step) for step in getattr(task, "activity_steps", [])
    ]
    projected.last_output = task.last_output
    projected.slow = task.slow
    projected.error = task.error
    return projected


def _activity_task_payload(
    snapshot: ParsedSession,
    task: TaskHistory,
) -> dict[str, object]:
    projected = _task_snapshot(snapshot, task)
    labels = _top_activity_labels(projected)
    return {
        "index": task.index,
        "count": task.count,
        "turnId": task.turn_id,
        "taskOrdinal": f"Req {task.index}/{task.count}",
        "currentTask": _top_current_task(projected),
        "executing": _top_executing_text(projected),
        "executingLabel": labels["executingLabel"],
        "currentTaskLabel": labels["currentTaskLabel"],
        "activityState": _top_activity_state(projected),
        "activityElapsed": _top_activity_elapsed(projected),
        "activityElapsedLabel": labels["activityElapsedLabel"],
        "activityGap": _top_activity_gap_value(projected),
        "activityGapLabel": labels["activityGapLabel"],
        "activityLast": _top_activity_last(projected),
        "activityLastLabel": labels["activityLastLabel"],
        "activityLastTooltip": _top_activity_last_tooltip(projected),
        "activityTrail": _top_activity_trail(projected),
        "slow": _top_slow_chip(projected),
        "gap": _top_gap_chip(projected),
        "copies": _top_copy_texts(projected),
    }


def _activity_tasks(snapshot: ParsedSession) -> list[dict[str, object]]:
    tasks = list(getattr(snapshot, "activity_tasks", []) or [])
    if not tasks:
        return []
    return [_activity_task_payload(snapshot, task) for task in tasks]


def _compact(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _top_expanded_header_title(snapshot: ParsedSession) -> str:
    return renderer_session.expanded_header_title(
        snapshot,
        is_new_session=_is_new_session_snapshot,
        is_pending_session=_is_pending_session_snapshot,
        compact=_compact,
        fallback=TOP_EXPANDED_HEADER_FALLBACK,
    )


def _display_tokens(
    snapshot: ParsedSession,
) -> tuple[int | None, bool, int | None, bool, int | None, bool, int | None, bool]:
    return renderer_session.display_tokens(snapshot)


def _display_cached_tokens(
    snapshot: ParsedSession,
    input_tokens: int | None,
    input_estimated: bool,
) -> tuple[int | None, bool]:
    return renderer_session.display_cached_tokens(
        snapshot,
        input_tokens,
        input_estimated,
    )


def _format_rate_marker(value: float | None, estimated: bool) -> str:
    return renderer_common.format_rate_marker(
        value,
        estimated,
        value_formatter=_format_rate_value,
    )


def _format_rate_value(value: float | None, estimated: bool) -> str:
    return renderer_common.format_rate_value(value, estimated)


def _session_cache_hit_rate(snapshot: ParsedSession) -> tuple[float | None, bool]:
    return renderer_session.session_cache_hit_rate(
        snapshot,
        display_tokens_fn=_display_tokens,
        display_cached_tokens_fn=_display_cached_tokens,
    )


def _session_cache_hit_rate_label(snapshot: ParsedSession) -> str:
    return renderer_session.session_cache_hit_rate_label(
        snapshot,
        cache_hit_rate=_session_cache_hit_rate,
        rate_marker=_format_rate_marker,
    )


def _top_session_cache_hit_rate_label(snapshot: ParsedSession) -> str:
    return renderer_session.top_session_cache_hit_rate_label(
        snapshot,
        cache_hit_rate_label=_session_cache_hit_rate_label,
    )


def _top_session_usage_summary(snapshot: ParsedSession, session_cost: float | None = None) -> str:
    resolved_cost = _session_cost(snapshot) if session_cost is None else session_cost
    return renderer_session.top_session_usage_summary(
        snapshot,
        resolved_cost,
        is_new_session=_is_new_session_snapshot,
        is_pending_session=_is_pending_session_snapshot,
        display_tokens_fn=_display_tokens,
        usage_money=_format_usage_money,
        top_cache_hit_rate_label=_top_session_cache_hit_rate_label,
    )


def _top_cache_progress_label(snapshot: ParsedSession) -> str:
    return renderer_session.top_cache_progress_label(
        snapshot,
        cache_hit_rate_label=_session_cache_hit_rate_label,
    )


def _budget_progress_total_ratio(cost: float | None, limit: float | None) -> float:
    return renderer_budget.progress_total_ratio(cost, limit)


def _budget_progress_ratio(cost: float | None, limit: float | None) -> float:
    return renderer_budget.progress_ratio(
        cost,
        limit,
        total_ratio=_budget_progress_total_ratio,
    )


def _budget_progress_total_text(cost: float | None, limit: float | None) -> str:
    return renderer_budget.progress_total_text(
        cost,
        limit,
        total_ratio=_budget_progress_total_ratio,
    )


def _budget_progress_overflow_ratio(cost: float | None, limit: float | None) -> float:
    return renderer_budget.progress_overflow_ratio(
        cost,
        limit,
        total_ratio=_budget_progress_total_ratio,
    )


def _budget_progress_overflow_parts(
    cost: float | None,
    limit: float | None,
) -> tuple[str, str]:
    return renderer_budget.progress_overflow_parts(
        cost,
        limit,
        total_ratio=_budget_progress_total_ratio,
        money_formatter=_format_money,
    )


def _budget_progress_overflow_badge(cost: float | None, limit: float | None) -> str:
    return renderer_budget.progress_overflow_badge(
        cost,
        limit,
        overflow_parts=_budget_progress_overflow_parts,
    )


def _budget_progress_overflow_badge_compact(cost: float | None, limit: float | None) -> str:
    return renderer_budget.progress_overflow_badge_compact(
        cost,
        limit,
        overflow_parts=_budget_progress_overflow_parts,
    )


def _budget_limit_text(limit: float | None) -> str:
    return renderer_budget.limit_text(limit, money_formatter=_format_money)


def _top_progress_metric(
    label: str,
    ratio: float | None,
    tone: str,
    *,
    right_text: str = "",
    overflow_ratio: float | None = None,
    overflow_badge: str = "",
    overflow_badge_compact: str = "",
    overflow_badge_icon: str = "",
) -> dict[str, object]:
    return renderer_budget.progress_metric(
        label,
        ratio,
        tone,
        right_text=right_text,
        overflow_ratio=overflow_ratio,
        overflow_badge=overflow_badge,
        overflow_badge_compact=overflow_badge_compact,
        overflow_badge_icon=overflow_badge_icon,
    )


def _top_progress(snapshot: ParsedSession) -> dict[str, object]:
    cache_ratio, _cache_estimated = _session_cache_hit_rate(snapshot)
    budget_ready = bool(getattr(snapshot, "budget_ready", True))
    # A deferred cold-start scan leaves placeholder zeros behind. Treat them as
    # unmeasured so the HUD never claims the budget is $0 before it is scanned.
    day_complete = snapshot.today_cost_usd is not None and budget_ready
    week_complete = snapshot.week_cost_usd is not None and budget_ready
    day_label = (
        f"今日 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)}"
        if budget_ready
        else "今日 计算中…"
    )
    week_label = (
        f"本周 {_format_usage_money(snapshot.week_tokens, snapshot.week_cost_usd)}"
        if budget_ready
        else "本周 计算中…"
    )
    day_overflow = (
        _budget_progress_overflow_ratio(snapshot.today_cost_usd, snapshot.daily_limit_usd)
        if day_complete
        else 0.0
    )
    week_overflow = (
        _budget_progress_overflow_ratio(snapshot.week_cost_usd, snapshot.weekly_limit_usd)
        if week_complete
        else 0.0
    )
    day_badge, day_badge_compact = (
        _budget_progress_overflow_parts(snapshot.today_cost_usd, snapshot.daily_limit_usd)
        if day_complete
        else ("", "")
    )
    week_badge, week_badge_compact = (
        _budget_progress_overflow_parts(snapshot.week_cost_usd, snapshot.weekly_limit_usd)
        if week_complete
        else ("", "")
    )
    session = _top_progress_metric(
        _top_session_usage_summary(snapshot),
        0.0,
        "session",
    )
    cache = _top_progress_metric(
        _top_cache_progress_label(snapshot),
        cache_ratio if cache_ratio is not None else 0.0,
        "cache",
    )
    day = _top_progress_metric(
        day_label,
        _budget_progress_ratio(snapshot.today_cost_usd, snapshot.daily_limit_usd),
        "day",
        # Keep full usage/amount on the left; only the badge shrinks under pressure.
        right_text=(
            ""
            if not day_complete or day_overflow > 0.0
            else _budget_limit_text(snapshot.daily_limit_usd)
        ),
        overflow_ratio=day_overflow,
        overflow_badge=day_badge,
        overflow_badge_compact=day_badge_compact,
        overflow_badge_icon="🚨" if day_badge else "",
    )
    week = _top_progress_metric(
        week_label,
        _budget_progress_ratio(snapshot.week_cost_usd, snapshot.weekly_limit_usd),
        "week",
        right_text=(
            ""
            if not week_complete or week_overflow > 0.0
            else _budget_limit_text(snapshot.weekly_limit_usd)
        ),
        overflow_ratio=week_overflow,
        overflow_badge=week_badge,
        overflow_badge_compact=week_badge_compact,
    )
    budget_day = _top_progress_metric(
        day_label,
        _budget_progress_ratio(snapshot.today_cost_usd, snapshot.daily_limit_usd),
        "day",
        right_text=(
            ""
            if not day_complete or day_overflow > 0.0
            else _budget_limit_text(snapshot.daily_limit_usd)
        ),
        overflow_ratio=day_overflow,
        overflow_badge=day_badge,
        overflow_badge_compact=day_badge_compact,
        overflow_badge_icon="🚨" if day_badge else "",
    )
    budget_week = _top_progress_metric(
        week_label,
        _budget_progress_ratio(snapshot.week_cost_usd, snapshot.weekly_limit_usd),
        "week",
        right_text=(
            ""
            if not week_complete or week_overflow > 0.0
            else _budget_limit_text(snapshot.weekly_limit_usd)
        ),
        overflow_ratio=week_overflow,
        overflow_badge=week_badge,
        overflow_badge_compact=week_badge_compact,
    )
    return {
        "collapsed": [session, day, week],
        "cache": cache,
        "budget": [budget_day, budget_week],
    }


def _request_projection_context() -> renderer_request_projection.RequestProjectionContext:
    return renderer_request_projection.RequestProjectionContext(
        cost_estimator=_COST_ESTIMATOR,
        is_new_session=_is_new_session_snapshot,
        is_pending_session=_is_pending_session_snapshot,
        display_tokens=_display_tokens,
        format_rate_value=_format_rate_value,
        format_fixed_money=_format_fixed_money,
        fixed_token_total=_fixed_token_total,
        short_number=_short_num,
    )


def _round_cache_hit_rate_value(item: RequestRound) -> str:
    return renderer_request_projection.round_cache_hit_rate_value(
        item,
        context=_request_projection_context(),
    )


def _request_cost(snapshot: ParsedSession) -> tuple[float | None, bool]:
    return renderer_request_projection.request_cost(
        snapshot,
        context=_request_projection_context(),
    )


def _round_from_snapshot(snapshot: ParsedSession) -> RequestRound:
    return renderer_request_projection.round_from_snapshot(
        snapshot,
        context=_request_projection_context(),
    )


def _task_rows(snapshot: ParsedSession) -> list[RequestRound]:
    return renderer_request_projection.task_rows(
        snapshot,
        context=_request_projection_context(),
    )


def _task_total(snapshot: ParsedSession) -> tuple[int, int, int, int, int, float | None, bool]:
    return renderer_request_projection.task_total(
        snapshot,
        context=_request_projection_context(),
    )


def _session_cost(snapshot: ParsedSession) -> float | None:
    family_cost = getattr(snapshot, "family_cost_usd", None)
    if family_cost is not None and float(family_cost) > 0:
        thread_cost = snapshot.confirmed.cumulative_cost_usd
        if thread_cost is None or float(family_cost) >= float(thread_cost):
            return float(family_cost)
    if snapshot.confirmed.cumulative_cost_usd is not None:
        return snapshot.confirmed.cumulative_cost_usd
    return _COST_ESTIMATOR.calculate(
        snapshot.request.model,
        snapshot.confirmed.cumulative_input,
        snapshot.confirmed.cumulative_cached,
        snapshot.confirmed.cumulative_output,
        snapshot.confirmed.cumulative_reasoning,
        cache_write_tokens=snapshot.confirmed.cumulative_cache_write,
    )


def _session_tokens(snapshot: ParsedSession) -> int:
    family_tokens = int(getattr(snapshot, "family_tokens", 0) or 0)
    thread_tokens = int(snapshot.confirmed.cumulative_total or 0)
    return max(family_tokens, thread_tokens)


def _budget_status(snapshot: ParsedSession) -> str:
    if snapshot.budget_error:
        return "预算不可用"
    if snapshot.today_cost_usd is None or snapshot.week_cost_usd is None:
        return "价格不可用"
    if snapshot.budget_warnings:
        tags: list[str] = []
        for warning in snapshot.budget_warnings:
            if warning.startswith("日") and "超过 " in warning:
                tags.append("日>" + warning.split("超过 ", 1)[1].split("%", 1)[0] + "%")
            elif warning.startswith("周") and "超过 " in warning:
                tags.append("周>" + warning.split("超过 ", 1)[1].split("%", 1)[0] + "%")
            else:
                tags.append("额度")
        return "提醒 " + "/".join(tags)
    return _status_label(snapshot.status)


def _request_counter(snapshot: ParsedSession) -> str:
    (
        input_tokens,
        input_estimated,
        output_tokens,
        output_estimated,
        reasoning_tokens,
        reasoning_estimated,
        total_tokens,
        total_estimated,
    ) = _display_tokens(snapshot)
    cost, cost_estimated = _request_cost(snapshot)
    cached_tokens, cached_estimated = _display_cached_tokens(
        snapshot,
        input_tokens,
        input_estimated,
    )
    return " ".join(
        [
            f"↑{'~' if input_estimated else ''}{_short_num(input_tokens)}",
            f"↻{'~' if cached_estimated else ''}{_short_num(cached_tokens)}",
            f"↓{'~' if output_estimated else ''}{_short_num(output_tokens)}",
            f"◇{'~' if reasoning_estimated else ''}{_short_num(reasoning_tokens)}",
            f"∑{'~' if total_estimated else ''}{_short_num(total_tokens)}",
            _format_realtime_money(cost, cost_estimated),
        ]
    )


def _request_total_line(snapshot: ParsedSession) -> str:
    (
        input_tokens,
        cached_tokens,
        output_tokens,
        reasoning_tokens,
        total_tokens,
        cost,
        estimated,
    ) = _task_total(snapshot)
    return " ".join(
        [
            _format_fixed_money(cost, estimated),
            f"↑{'~' if estimated else ''}{_short_num(input_tokens)}",
            _session_cache_hit_rate_label(snapshot),
            f"↓{'~' if estimated else ''}{_short_num(output_tokens)}",
            f"◇{'~' if estimated else ''}{_short_num(reasoning_tokens)}",
            f"↻{'~' if estimated else ''}{_short_num(cached_tokens)}",
            f"∑{_fixed_token_total(total_tokens)}",
        ]
    )


def _round_is_running(item: RequestRound) -> bool:
    return renderer_request_projection.round_is_running(item)


def _round_elapsed_text(
    started_at: datetime | None,
    *,
    now: datetime | None = None,
) -> str:
    return renderer_request_projection.round_elapsed_text(started_at, now=now)


def _round_time_text(
    item: RequestRound,
    *,
    now: datetime | None = None,
) -> str:
    return renderer_request_projection.round_time_text(
        item,
        now=now,
    )


def _round_time_iso(value: datetime | None) -> str:
    return renderer_request_projection.round_time_iso(value)


def _round_entry(
    item: RequestRound,
    fallback_model: str,
    *,
    widths: "_RoundColumnWidths | None" = None,
    now: datetime | None = None,
) -> str:
    return renderer_request_projection.round_entry(
        item,
        fallback_model,
        context=_request_projection_context(),
        widths=widths,
        now=now,
    )


def _round_entry_parts(
    item: RequestRound,
    fallback_model: str,
    *,
    widths: "_RoundColumnWidths | None" = None,
    now: datetime | None = None,
) -> dict[str, str]:
    return renderer_request_projection.round_entry_parts(
        item,
        fallback_model,
        context=_request_projection_context(),
        widths=widths,
        now=now,
    )


_RoundColumnWidths = renderer_request_projection.RoundColumnWidths


def _round_entry_widths(
    rows: list[RequestRound],
    fallback_model: str,
) -> _RoundColumnWidths:
    return renderer_request_projection.round_entry_widths(
        rows,
        fallback_model,
        context=_request_projection_context(),
    )


def _request_rows(
    snapshot: ParsedSession,
    *,
    limit: int = renderer_request_projection.REQUEST_ROWS_PAGE_SIZE,
) -> list[str]:
    return renderer_request_projection.request_rows(
        snapshot,
        context=_request_projection_context(),
        limit=limit,
    )


def _display_request_rows(
    snapshot: ParsedSession,
    *,
    limit: int = renderer_request_projection.REQUEST_ROWS_PAGE_SIZE,
) -> tuple[list[RequestRound], _RoundColumnWidths]:
    return renderer_request_projection.display_request_rows(
        snapshot,
        context=_request_projection_context(),
        limit=limit,
    )


def _request_row_details(
    snapshot: ParsedSession,
    *,
    limit: int = renderer_request_projection.REQUEST_ROWS_PAGE_SIZE,
) -> list[dict[str, object]]:
    return renderer_request_projection.request_row_details(
        snapshot,
        context=_request_projection_context(),
        limit=limit,
    )


def _request_rows_total(snapshot: ParsedSession) -> int:
    return len(
        renderer_request_projection.task_rows(
            snapshot,
            context=_request_projection_context(),
        )
    )


def _budget_warning_summary(
    snapshot: ParsedSession,
    *,
    include_budget_warnings: bool = True,
) -> str:
    if snapshot.budget_error:
        return snapshot.budget_error
    if not include_budget_warnings or not snapshot.budget_warnings:
        return ""
    messages: list[str] = []
    for warning in snapshot.budget_warnings:
        if (
            warning.startswith("日额度已用")
            and "超过 " in warning
            and snapshot.today_cost_usd is not None
            and snapshot.daily_limit_usd > 0
        ):
            threshold = warning.split("超过 ", 1)[1].split("%", 1)[0].strip()
            messages.append(
                f"日已用 {snapshot.today_cost_usd / snapshot.daily_limit_usd:.0%}，超过 {threshold}% 阈值"
            )
            continue
        if (
            warning.startswith("周额度已用")
            and "超过 " in warning
            and snapshot.week_cost_usd is not None
            and snapshot.weekly_limit_usd > 0
        ):
            threshold = warning.split("超过 ", 1)[1].split("%", 1)[0].strip()
            messages.append(
                f"周已用 {snapshot.week_cost_usd / snapshot.weekly_limit_usd:.0%}，超过 {threshold}% 阈值"
            )
            continue
        messages.append(warning)
    return "预警  " + "；".join(messages)


def _format_warnings(
    snapshot: ParsedSession,
    *,
    include_budget_warnings: bool = True,
) -> str:
    return _budget_warning_summary(
        snapshot,
        include_budget_warnings=include_budget_warnings,
    )


def _format_notice(
    snapshot: ParsedSession,
    *,
    include_budget_warnings: bool = True,
) -> str:
    parts: list[str] = []
    notice = _format_warnings(
        snapshot,
        include_budget_warnings=include_budget_warnings,
    )
    if notice:
        parts.append(notice)
    if snapshot.error:
        parts.append(f"错误 {_compact(snapshot.error, 80)}")
    if snapshot.request.error:
        parts.append(f"请求 {_compact(snapshot.request.error, 80)}")
    return "  |  ".join(parts)


def _format_slow_panel(snapshot: ParsedSession) -> str:
    return "\n".join(
        [
            f"最慢工具  {snapshot.slow.slowest_tool}",
            f"最慢等待  {snapshot.slow.slowest_user_wait}",
        ]
    )


def _current_gap_text(snapshot: ParsedSession) -> str:
    prefix = "进行中" if snapshot.slow.current_gap_active else "当前"
    return f"{prefix}  {snapshot.slow.current_gap}"


def _format_gap_panel(snapshot: ParsedSession) -> str:
    return (
        f"最长响应等待  {snapshot.slow.longest_gap}\n"
        f"{_current_gap_text(snapshot)}"
    )


def _token_value_text(value: int | None, estimated: bool = False) -> str:
    return f"{'~' if estimated else ''}{_short_num(value)}"


def _cache_percent_text(snapshot: ParsedSession) -> str:
    label = _top_session_cache_hit_rate_label(snapshot)
    return label if label != "-" else "--"


def _component_cost(
    snapshot: ParsedSession,
    *,
    input_tokens: int = 0,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
    output_tokens: int = 0,
) -> float | None:
    return _COST_ESTIMATOR.calculate(
        snapshot.request.model,
        input_tokens,
        cached_tokens,
        output_tokens,
        0,
        cache_write_tokens=cache_write_tokens,
    )


def _top_session_composition(snapshot: ParsedSession) -> str:
    confirmed = snapshot.confirmed
    input_tokens = int(confirmed.cumulative_input or 0)
    cached_tokens = max(0, min(int(confirmed.cumulative_cached or 0), input_tokens))
    cache_write_tokens = max(
        0,
        min(
            int(confirmed.cumulative_cache_write or 0),
            input_tokens - cached_tokens,
        ),
    )
    uncached_tokens = max(0, input_tokens - cached_tokens - cache_write_tokens)
    output_tokens = int(confirmed.cumulative_output or 0)
    components = [
        (
            "↑↻",
            cached_tokens,
            _component_cost(snapshot, input_tokens=cached_tokens, cached_tokens=cached_tokens),
        ),
        (
            "↑+",
            cache_write_tokens,
            _component_cost(
                snapshot,
                input_tokens=cache_write_tokens,
                cache_write_tokens=cache_write_tokens,
            ),
        ),
        (
            "↑",
            uncached_tokens,
            _component_cost(snapshot, input_tokens=uncached_tokens),
        ),
        (
            "↓",
            output_tokens,
            _component_cost(snapshot, output_tokens=output_tokens),
        ),
    ]
    components = [item for item in components if item[1] > 0]
    if not components:
        return "暂无可分析的 token 构成"
    cost_components = [(label, cost) for label, _tokens, cost in components if cost is not None]
    if len(cost_components) == len(components):
        return " + ".join(
            f"{label} {_format_money(cost)}"
            for label, cost in cost_components
        )
    return " + ".join(f"{label} {_short_num(tokens)}" for label, tokens, _cost in components)


def _round_duration_text(item: RequestRound) -> str:
    if item.started_at is None:
        return "--"
    finish = item.completed_at
    if finish is None:
        return _round_elapsed_text(item.started_at).strip()
    return _duration_text(seconds_between(item.started_at, finish))


def _round_cost_value(item: RequestRound, fallback_model: str) -> tuple[float | None, bool]:
    cost = item.cost_usd
    estimated = item.estimated or item.status == "running"
    if cost is None:
        cost = _COST_ESTIMATOR.calculate(
            item.model or fallback_model,
            item.input_tokens or 0,
            item.cached_tokens or 0,
            item.output_tokens or 0,
            item.reasoning_tokens or 0,
            cache_write_tokens=item.cache_write_tokens or 0,
        )
        estimated = True
    return cost, estimated


def _session_round_rows(snapshot: ParsedSession) -> list[RequestRound]:
    if _is_new_session_snapshot(snapshot) or _is_pending_session_snapshot(snapshot):
        return []
    rows = list(getattr(snapshot, "session_request_history", []) or [])
    if rows:
        return rows
    return _task_rows(snapshot)


def _top_heavy_rounds(snapshot: ParsedSession) -> list[dict[str, object]]:
    task_rows: list[tuple[int, RequestRound]] = []
    for task in getattr(snapshot, "activity_tasks", []) or []:
        task_index = max(1, int(getattr(task, "index", 0) or 1))
        task_rows.extend(
            (task_index, item)
            for item in (getattr(task, "request_history", []) or [])
        )
    if not task_rows:
        fallback_task_index = max(1, int(getattr(snapshot, "task_index", 0) or 1))
        task_rows = [
            (fallback_task_index, item) for item in _session_round_rows(snapshot)
        ]
    ranked: list[tuple[float, int, int, RequestRound, float | None, bool]] = []
    task_prompts = {
        max(1, int(getattr(task, "index", 0) or 1)): str(
            getattr(task, "prompt", "") or ""
        ).strip()
        for task in (getattr(snapshot, "activity_tasks", []) or [])
    }
    task_turn_ids = {
        max(1, int(getattr(task, "index", 0) or 1)): str(
            getattr(task, "turn_id", "") or ""
        ).strip()
        for task in (getattr(snapshot, "activity_tasks", []) or [])
    }
    task_rolled_back = {
        max(1, int(getattr(task, "index", 0) or 1)): bool(
            getattr(task, "rolled_back", False)
        )
        for task in (getattr(snapshot, "activity_tasks", []) or [])
    }
    fallback_prompt = str(getattr(snapshot, "task_prompt", "") or "").strip()
    fallback_turn_id = str(getattr(snapshot, "task_turn_id", "") or "").strip()
    for task_index, item in task_rows:
        if not (
            item.status != "waiting"
            or item.total_tokens
            or item.input_tokens
            or item.output_tokens
            or item.reasoning_tokens
            or item.cost_usd
        ):
            continue
        cost, estimated = _round_cost_value(item, snapshot.request.model)
        total = int(item.total_tokens or 0)
        if total <= 0:
            total = int(item.input_tokens or 0) + int(item.output_tokens or 0)
        ranked.append(
            (float(cost if cost is not None else -1.0), total, task_index, item, cost, estimated)
        )
    ranked.sort(key=lambda value: (value[0], value[1]), reverse=True)

    details: list[dict[str, object]] = []
    for _score_cost, total, task_index, item, cost, estimated in ranked[:3]:
        duration = _round_duration_text(item)
        breakdown = (
            f"↑{_short_num(item.input_tokens)} "
            f"↻{_short_num(item.cached_tokens)} "
            f"↓{_short_num(item.output_tokens)} "
            f"◇{_short_num(item.reasoning_tokens)}"
        )
        rolled_back = bool(task_rolled_back.get(task_index, False))
        title = f"Req{task_index}-#{item.index} {_format_fixed_money(cost, estimated)} · ∑{_short_num(total)}"
        if rolled_back:
            title = f"{title} · 已回滚"
        detail = _compact(item.activity_summary or f"消耗构成：{breakdown}", 112)
        copy_text = item.copy_text or (
            f"Req{task_index}-#{item.index}\n"
            f"金额 {_format_fixed_money(cost, estimated)}\n"
            f"Tokens {total:,}\n"
            f"{breakdown}"
        )
        tooltip = (
            f"轮次 #{item.index} · {duration}\n"
            f"金额 {_format_fixed_money(cost, estimated)} · Tokens {total:,}\n"
            f"{detail}"
        )
        if rolled_back:
            tooltip = f"该轮次已被回滚，聊天中不可见；点击仅复制，不滚动定位\n{tooltip}"
        locate_texts = [
            text[:600]
            for text in (getattr(item, "activity_texts", ()) or ())
            if str(text).strip()
        ][:6]
        details.append(
            {
                "title": title,
                "detail": detail,
                "copyText": copy_text,
                "taskIndex": task_index,
                "roundIndex": int(item.index),
                "taskPrompt": task_prompts.get(task_index, "") or fallback_prompt,
                "taskTurnId": task_turn_ids.get(task_index, "") or fallback_turn_id,
                "rolledBack": rolled_back,
                "tooltip": tooltip,
                "locateTexts": locate_texts,
            }
        )
    return details


def _activity_projection_context() -> renderer_activity_projection.ActivityProjectionContext:
    return renderer_activity_projection.ActivityProjectionContext(
        is_new_session=_is_new_session_snapshot,
        is_pending_session=_is_pending_session_snapshot,
        task_rows=_task_rows,
        task_total=_task_total,
        round_cost=_round_cost_value,
        compact=_compact,
        activity_label=_activity_label,
        request_status_label=_request_status_label,
        gap_label=_gap_label,
        short_number=_short_num,
        format_rate_marker=_format_rate_marker,
        format_fixed_money=_format_fixed_money,
        duration_text=_duration_text,
        timeline_time=_timeline_time,
        round_elapsed_text=_round_elapsed_text,
    )


def _top_task_ordinal(snapshot: ParsedSession) -> str:
    return renderer_activity_projection.task_ordinal(
        snapshot,
        context=_activity_projection_context(),
    )


def _top_task_ordinal_parts(snapshot: ParsedSession) -> dict[str, str]:
    return renderer_activity_projection.task_ordinal_parts(
        snapshot,
        context=_activity_projection_context(),
    )


def _top_session_parts(snapshot: ParsedSession) -> dict[str, str]:
    confirmed = snapshot.confirmed
    if snapshot.token_events > 0:
        average = confirmed.cumulative_total // max(1, snapshot.token_events)
        session_average = f"均值 {_short_num(average)} /轮"
    else:
        session_average = "均值 n/a"
    parts = {
        "sessionMix": _top_cache_progress_label(snapshot),
        "sessionAverage": session_average,
        "sessionComposition": _top_session_composition(snapshot),
        "heavyRoundsSummary": "Top 3",
        "heavyRounds": _top_heavy_rounds(snapshot),
        "sessionInputTokens": _token_value_text(confirmed.cumulative_input),
        "sessionCachedTokens": _token_value_text(confirmed.cumulative_cached),
        "sessionOutputTokens": _token_value_text(confirmed.cumulative_output),
        "sessionReasoningTokens": _token_value_text(confirmed.cumulative_reasoning),
    }
    parts.update(_top_task_ordinal_parts(snapshot))
    return parts


def _top_current_work_item(snapshot: ParsedSession) -> Any | None:
    return renderer_activity_projection.current_work_item(snapshot)


def _top_task_finished(snapshot: ParsedSession) -> bool:
    return renderer_activity_projection.task_finished(snapshot)


def _top_task_aborted(snapshot: ParsedSession) -> bool:
    return renderer_activity_projection.task_aborted(snapshot)


def _top_activity_state(snapshot: ParsedSession) -> str:
    return renderer_activity_projection.activity_state(
        snapshot,
        context=_activity_projection_context(),
    )


def _top_activity_main(snapshot: ParsedSession, *, limit: int = 118) -> str:
    return renderer_activity_projection.activity_main(
        snapshot,
        context=_activity_projection_context(),
        limit=limit,
    )


def _top_executing_text(snapshot: ParsedSession) -> str:
    return renderer_activity_projection.executing_text(
        snapshot,
        context=_activity_projection_context(),
    )


def _top_current_task(snapshot: ParsedSession) -> str:
    return renderer_activity_projection.current_task(
        snapshot,
        context=_activity_projection_context(),
    )


def _top_activity_labels(snapshot: ParsedSession) -> dict[str, str]:
    return renderer_activity_projection.activity_labels(
        snapshot,
        context=_activity_projection_context(),
    )


def _task_finished_at(snapshot: ParsedSession) -> datetime | None:
    return renderer_activity_projection.task_finished_at(snapshot)


def _top_activity_elapsed(snapshot: ParsedSession) -> str:
    return renderer_activity_projection.activity_elapsed(
        snapshot,
        context=_activity_projection_context(),
    )


def _task_round_count(snapshot: ParsedSession) -> int:
    return renderer_activity_projection.task_round_count(
        snapshot,
        context=_activity_projection_context(),
    )


def _task_cache_hit_rate_label(snapshot: ParsedSession, rows: list[RequestRound]) -> str:
    return renderer_activity_projection.task_cache_hit_rate_label(
        snapshot,
        rows,
        context=_activity_projection_context(),
    )


def _top_task_spend_text(snapshot: ParsedSession) -> str:
    return renderer_activity_projection.task_spend_text(
        snapshot,
        context=_activity_projection_context(),
    )


def _top_task_spend_money_text(snapshot: ParsedSession) -> str:
    return renderer_activity_projection.task_spend_money_text(
        snapshot,
        context=_activity_projection_context(),
    )


def _top_activity_gap_value(snapshot: ParsedSession) -> str:
    return renderer_activity_projection.activity_gap_value(
        snapshot,
        context=_activity_projection_context(),
    )


def _top_activity_last(snapshot: ParsedSession) -> str:
    return renderer_activity_projection.activity_last(
        snapshot,
        context=_activity_projection_context(),
    )


def _top_activity_last_tooltip(snapshot: ParsedSession) -> str:
    return renderer_activity_projection.activity_last_tooltip(
        snapshot,
        context=_activity_projection_context(),
    )


def _duration_text(seconds: float | None) -> str:
    return renderer_common.duration_text(seconds)


def _running_duration(start: datetime | None, end: datetime | None, now: datetime) -> float | None:
    return renderer_activity_projection.running_duration(start, end, now)


def _first_duration_fragment(value: str) -> str:
    return renderer_activity_projection.first_duration_fragment(value)


def _top_slow_chip(snapshot: ParsedSession) -> str:
    return renderer_activity_projection.slow_chip(
        snapshot,
        context=_activity_projection_context(),
    )


def _top_gap_chip(snapshot: ParsedSession) -> str:
    return renderer_activity_projection.gap_chip(
        snapshot,
        context=_activity_projection_context(),
    )


def _timeline_time(value: datetime | None) -> str:
    return renderer_common.timeline_time(value)


def _top_activity_trail(snapshot: ParsedSession) -> list[dict[str, object]]:
    return renderer_activity_projection.activity_trail(
        snapshot,
        context=_activity_projection_context(),
    )


def _top_details(snapshot: ParsedSession, session_cost: float | None) -> dict[str, object]:
    session_parts = _top_session_parts(snapshot)
    activity_labels = _top_activity_labels(snapshot)
    session_label = (
        "新会话"
        if _is_new_session_snapshot(snapshot)
        else (
            "会话加载中"
            if _is_pending_session_snapshot(snapshot)
            else f"会话 {snapshot.session_id[-12:]}"
        )
    )
    details = {
        "title": _top_expanded_header_title(snapshot),
        "session": (
            f"{session_label} | "
            f"行 {snapshot.line_count} | 确认 {snapshot.token_events}"
        ),
        "sessionCost": _format_money(session_cost),
        "sessionTokens": _short_num(_session_tokens(snapshot)),
        "sessionRounds": f"{snapshot.token_events} 轮确认",
        "cacheText": _top_cache_progress_label(snapshot),
        "warnings": _format_notice(snapshot),
        "executing": _top_executing_text(snapshot),
        "currentTask": _top_current_task(snapshot),
        "activityState": _top_activity_state(snapshot),
        "activityElapsed": _top_activity_elapsed(snapshot),
        "activityGap": _top_activity_gap_value(snapshot),
        "activityLast": _top_activity_last(snapshot),
        "activityLastTooltip": _top_activity_last_tooltip(snapshot),
        "activityTrail": _top_activity_trail(snapshot),
        "slow": _top_slow_chip(snapshot),
        "gap": _top_gap_chip(snapshot),
    }
    activity_tasks = _activity_tasks(snapshot)
    if activity_tasks:
        details["activityTasks"] = activity_tasks
        details["activityTaskIndex"] = int(snapshot.task_index or 0)
        details["activityTaskCount"] = len(activity_tasks)
        details["activityTaskNavigable"] = len(activity_tasks) > 1
    details.update(session_parts)
    details.update(activity_labels)
    return details


__all__ = [
    "RendererHudPayload",
    "payload_domains",
    "payload_from_snapshot",
    "session_switch_payload_from_snapshot",
    "set_cost_estimator",
]
