"""Current-session snapshot construction and text projection."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
import json
import logging
import time
from pathlib import Path
from typing import Any
import uuid

from .config import DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED
from .core import (
    BaseEstimate,
    ParsedSession,
    ReadingActivity,
    UsageSummary,
    detect_reading_activity,
)
from . import runtime_policies, runtime_usage
from . import session_snapshots

VISIBLE_APP_ERROR_HOLD_SECONDS = 60.0

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SnapshotBuilderPorts:
    """Coordinator-owned effects used while enriching a snapshot."""

    record_active_session_error: Callable[[Any, str, Path | None], None]
    provider_scope: Callable[[Any, ParsedSession | None], frozenset[str] | None]
    refresh_usage_insights: Callable[[Any], object]
    active_work_items: Callable[[Any, ParsedSession, Path | None], list[Any]]
    apply_family_usage: Callable[[Any, ParsedSession, frozenset[str] | None], None]


@dataclass(frozen=True, slots=True)
class RuntimeSnapshotBuilder:
    """Adapt the injected snapshot builder to runtime refresh options safely."""

    context: object
    builder: Callable[..., ParsedSession]

    def __call__(
        self,
        *,
        refresh_budget_aggregate: bool | None = None,
        refresh_budget_paths: Iterable[Path] = (),
        refresh_active_work_items: bool = True,
        scan_active_work_candidates: bool = True,
        refresh_current_session_usage: bool = True,
        reuse_budget_from: ParsedSession | None = None,
        refresh_visible_app_error: bool = True,
    ) -> ParsedSession:
        try:
            if refresh_budget_aggregate is None and refresh_active_work_items:
                return self.builder(self.context)
            if refresh_budget_aggregate is None:
                return self.builder(
                    self.context,
                    refresh_active_work_items=False,
                )
            kwargs: dict[str, object] = {
                "refresh_budget_aggregate": refresh_budget_aggregate,
                "refresh_budget_paths": refresh_budget_paths,
            }
            if reuse_budget_from is not None:
                kwargs["reuse_budget_from"] = reuse_budget_from
            if not refresh_visible_app_error:
                kwargs["refresh_visible_app_error"] = False
            if not refresh_current_session_usage:
                kwargs["refresh_current_session_usage"] = False
            if not refresh_active_work_items:
                kwargs["refresh_active_work_items"] = False
            elif not scan_active_work_candidates:
                kwargs["scan_active_work_candidates"] = False
            return self.builder(self.context, **kwargs)
        except Exception as exc:
            return ParsedSession(status="error", error=str(exc))


def selection_is_stale(snapshot: ParsedSession, tracker: object | None) -> bool:
    snapshot_seq = int(getattr(snapshot, "selection_seq", 0) or 0)
    current_seq = int(getattr(tracker, "selection_seq", 0) or 0)
    return bool(snapshot_seq and current_seq and snapshot_seq != current_seq)


def _visible_app_error(platform: object) -> str:
    try:
        text = platform.get_active_app_error()
    except Exception:
        return ""
    return " ".join(str(text or "").split())


def _apply_visible_app_error(snapshot: ParsedSession, message: str) -> None:
    if not message:
        return
    snapshot.request.status = "error"
    snapshot.request.error = message
    snapshot.request.source = "app"
    snapshot.request.updated_at = snapshot.refreshed_at
    if snapshot.request.started_at is None:
        snapshot.request.started_at = snapshot.task_started_at


def _visible_app_error_task_key(snapshot: ParsedSession) -> str:
    started_at = snapshot.task_started_at or snapshot.request.started_at
    return json.dumps(
        {
            "session": snapshot.session_id,
            "path": str(snapshot.session_path or ""),
            "startedAt": started_at.isoformat() if started_at is not None else "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


@dataclass
class VisibleAppErrorCache:
    """Hold a transient renderer-side visible error for its active task."""

    message: str = ""
    task_key: str = ""
    updated_at: datetime | None = None

    def clear(self) -> None:
        self.message = ""
        self.task_key = ""
        self.updated_at = None

    def resolve(self, snapshot: ParsedSession, visible_message: str) -> str:
        message = " ".join(str(visible_message or "").split())
        now = snapshot.refreshed_at or datetime.now().astimezone()
        task_key = _visible_app_error_task_key(snapshot)
        if message:
            self.message = message
            self.task_key = task_key
            self.updated_at = now
            return message
        if not self.message or self.updated_at is None:
            return ""
        if self.task_key != task_key:
            self.clear()
            return ""
        try:
            age_seconds = (now - self.updated_at).total_seconds()
        except TypeError:
            age_seconds = (
                now.replace(tzinfo=None) - self.updated_at.replace(tzinfo=None)
            ).total_seconds()
        if age_seconds <= VISIBLE_APP_ERROR_HOLD_SECONDS:
            return self.message
        self.clear()
        return ""


def _enrich_tracker(
    context: object,
    snapshot: ParsedSession,
    session_path: Path | None,
    *,
    selection_seq: int,
    selection_observed_at_ms: int,
) -> None:
    tracker = context.active_session_tracker
    if tracker is None:
        return
    snapshot.renderer_session_id = str(getattr(tracker, "renderer_session_id", "") or "")
    snapshot.selection_seq = selection_seq
    snapshot.selection_observed_at_ms = selection_observed_at_ms
    snapshot.composer_draft = str(
        getattr(tracker, "renderer_draft_text", "") or ""
    )
    snapshot.composer_draft_updated_at_ms = int(
        getattr(tracker, "renderer_draft_updated_at_ms", 0) or 0
    )
    snapshot.composer_send_requested = bool(
        getattr(tracker, "renderer_send_requested", False)
    )
    snapshot.renderer_collapsed_title = str(
        getattr(tracker, "renderer_collapsed_title", "") or ""
    )
    snapshot.renderer_collapsed_disclosure_ambiguous = bool(
        getattr(tracker, "renderer_collapsed_disclosure_ambiguous", False)
    )
    # The page can briefly lose its header/sidebar nodes while a blank chat is
    # being mounted. Reassert the tracker's sticky provisional state so that a
    # transient renderer-waiting source cannot remove the draft bubble.
    if session_path is None:
        if bool(getattr(tracker, "renderer_new_session", False)):
            snapshot.selection_source = "renderer-new-session"
        elif bool(getattr(tracker, "renderer_pending_session", False)):
            snapshot.selection_source = "renderer-pending-session"
    snapshot.follow_state = str(getattr(tracker, "follow_state", "") or "")
    snapshot.follow_reason = str(getattr(tracker, "follow_reason", "") or "")
    candidates = getattr(tracker, "match_candidates", [])
    snapshot.match_candidates = [
        dict(item) for item in candidates if isinstance(item, dict)
    ]
    snapshot.follow_timing = {
        "observedAt": int(getattr(tracker, "selection_observed_at_ms", 0) or 0),
        "receivedAt": int(getattr(tracker, "selection_received_at_ms", 0) or 0),
        "resolvedAt": int(getattr(tracker, "selection_resolved_at_ms", 0) or 0),
        "stuckSince": int(getattr(tracker, "follow_stuck_since_ms", 0) or 0),
        "stuckElapsedMs": int(getattr(tracker, "follow_stuck_elapsed_ms", 0) or 0),
    }
    if session_path is not None:
        snapshot.session_title = tracker.title_for_session(session_path, snapshot.session_id)


_BUDGET_FIELDS = (
    "today_tokens",
    "today_cost_usd",
    "week_tokens",
    "week_cost_usd",
    "week_before_today_tokens",
    "week_before_today_cost_usd",
    "week_adjustment_usd",
    "family_tokens",
    "family_cost_usd",
    "family_member_count",
    "daily_limit_usd",
    "weekly_limit_usd",
    "day_start",
    "week_start",
    "budget_error",
)


def _should_defer_cold_renderer_budget(
    context: object,
    day_start: datetime,
    week_start: datetime,
) -> bool:
    if not bool(getattr(context, "renderer_mode", False)):
        return False
    if not bool(getattr(context, "defer_cold_renderer_budget", True)):
        return False
    sessions_root = Path(getattr(context, "sessions_root"))
    if not sessions_root.exists():
        return False
    is_warm_for = getattr(getattr(context, "usage_cache", None), "is_warm_for", None)
    if not callable(is_warm_for):
        return False
    try:
        return not bool(is_warm_for(sessions_root, day_start, week_start))
    except Exception:
        return False


def _complete_summary_cost(summary: UsageSummary) -> float | None:
    """Return a budget amount only when every known event has a price."""
    total = max(0, int(getattr(summary, "total_event_count", 0) or 0))
    priced = min(total, max(0, int(getattr(summary, "priced_event_count", 0) or 0)))
    if total > 0 and priced < total:
        return None
    return round(float(summary.cost_usd or 0.0), 6)


def _reuse_budget(
    context: object,
    snapshot: ParsedSession,
    source: ParsedSession,
    ports: SnapshotBuilderPorts,
) -> None:
    for field_name in _BUDGET_FIELDS:
        setattr(snapshot, field_name, getattr(source, field_name))
    snapshot.budget_warnings = list(source.budget_warnings)
    if int(getattr(snapshot, "family_tokens", 0) or 0) <= 0:
        scope = ports.provider_scope(context, snapshot)
        ports.apply_family_usage(context.usage_cache, snapshot, scope)


def _recent_window_totals(
    context: object,
    day_start: datetime,
    week_start: datetime,
    scope: Iterable[str] | None,
) -> tuple[UsageSummary, UsageSummary] | None:
    """Day/week totals from the recent-only fast path, or ``None``.

    Used while the cold-start budget is deferred: the full historical scan is
    still pending, but the day/week windows only depend on files written since
    the window opened, so real numbers are already knowable.
    """
    recent = getattr(
        getattr(context, "usage_cache", None), "recent_window_totals", None
    )
    if not callable(recent):
        return None
    try:
        totals = recent(
            context.sessions_root,
            day_start,
            week_start,
            included_providers=scope,
        )
    except Exception:
        _LOGGER.exception("usage_recent_window_totals_failed")
        return None
    if totals is None:
        return None
    return totals


def _summarize_budget(
    context: object,
    snapshot: ParsedSession,
    session_path: Path | None,
    ports: SnapshotBuilderPorts,
    *,
    refresh_budget_aggregate: bool | None,
    refresh_budget_paths: Iterable[Path],
    refresh_current_session_usage: bool,
) -> bool:
    day_start, week_start = runtime_policies.budget_windows(context.user_config)
    paths = tuple(Path(path) for path in refresh_budget_paths)
    if (
        refresh_budget_aggregate is False
        and not paths
        and session_path is not None
        and refresh_current_session_usage
    ):
        paths = (session_path,)
    scope = ports.provider_scope(context, snapshot)
    deferred = _should_defer_cold_renderer_budget(context, day_start, week_start)
    window_measured = not deferred
    if deferred:
        # The first renderer frame must not parse every historical session.
        # The day/week windows, however, only depend on files written since the
        # window opened, so publish those real totals immediately instead of
        # leaving the top bar on "今日 计算中…" until the full scan lands.
        recent_totals = _recent_window_totals(context, day_start, week_start, scope)
        if recent_totals is None:
            today_total, week_total = UsageSummary(), UsageSummary()
        else:
            today_total, week_total = recent_totals
            window_measured = True
    else:
        today_total, week_total = context.usage_cache.summarize(
            context.sessions_root,
            day_start,
            week_start,
            allow_stale=refresh_budget_aggregate is False,
            force_rescan=refresh_budget_aggregate is True,
            refresh_paths=paths,
            included_providers=scope,
        )
    adjustment = context.user_config.weekly_adjustment_for_scope(scope)
    snapshot.today_tokens = today_total.tokens
    today_cost = _complete_summary_cost(today_total)
    week_cost = _complete_summary_cost(week_total)
    snapshot.today_cost_usd = today_cost
    snapshot.week_tokens = week_total.tokens
    snapshot.week_cost_usd = (
        None if week_cost is None else round(week_cost + adjustment, 6)
    )
    prior = runtime_usage.usage_before_today_in_week(
        week_total, today_total, day_start, week_start
    )
    snapshot.week_before_today_tokens = prior.tokens
    snapshot.week_before_today_cost_usd = _complete_summary_cost(prior)
    snapshot.week_adjustment_usd = adjustment
    snapshot.daily_limit_usd = context.daily_budget_usd
    snapshot.weekly_limit_usd = context.weekly_budget_usd
    snapshot.day_start = day_start
    snapshot.week_start = week_start
    snapshot.budget_warnings = runtime_policies.budget_warning_messages(
        today_cost,
        snapshot.week_cost_usd,
        context.daily_budget_usd,
        context.weekly_budget_usd,
        context.budget_thresholds,
    )
    snapshot.budget_error = "" if context.sessions_root.exists() else snapshot.error
    if not deferred:
        # Insights still need the full scan (they cover month/lifetime), so do
        # not publish them from the recent-only window tier.
        ports.refresh_usage_insights(context)
    ports.apply_family_usage(context.usage_cache, snapshot, scope)
    return window_measured


def apply_pre_send_pricing(
    context: object, snapshot: ParsedSession, base: BaseEstimate
) -> BaseEstimate:
    model = snapshot.request.model or ""
    estimator = context.parser.cost_estimator
    million = 1_000_000
    input_cost = estimator.calculate(model, million, 0, 0, 0)
    cached_cost = estimator.calculate(model, million, million, 0, 0)
    if input_cost is None or cached_cost is None:
        return base
    confirmed = snapshot.confirmed
    cache_rate = 0.0
    if confirmed.last_input > 0:
        cache_rate = min(1.0, max(0.0, confirmed.last_cached / confirmed.last_input))
    return base.with_pricing(
        input_price_per_token=input_cost / million,
        cached_price_per_token=cached_cost / million,
        cache_hit_rate=cache_rate,
        model_name=model,
    )


def apply_pre_send_and_activity(context: object, snapshot: ParsedSession) -> None:
    if not DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED:
        snapshot.estimate_base = BaseEstimate()
        snapshot.reading_activity = ReadingActivity()
        return
    estimator = getattr(context, "pre_send_estimator", None)
    if estimator is not None:
        if snapshot.cwd:
            estimator.set_project_roots([snapshot.cwd])
        base = estimator.latest()
        confirmed = snapshot.confirmed
        if confirmed.last_input > 0:
            base = base.with_confirmed_context(
                cached_tokens=confirmed.last_cached,
                uncached_tokens=max(0, confirmed.last_input - confirmed.last_cached),
            )
        else:
            base = base.with_session_history(int(confirmed.cumulative_input or 0))
        snapshot.estimate_base = apply_pre_send_pricing(context, snapshot, base)
    snapshot.reading_activity = detect_reading_activity(snapshot)


def update_session_cleanup_activity(context: object, snapshot: ParsedSession) -> None:
    session_id = str(snapshot.session_id or "").strip()
    try:
        canonical = str(uuid.UUID(session_id))
    except (AttributeError, TypeError, ValueError):
        canonical = ""
    context.session_management_current_session_id = (
        canonical if canonical == session_id.casefold() else ""
    )
    active: set[str] = set()
    if context.session_management_current_session_id and (
        snapshot.request.status == "running"
        or snapshot.slow.current_gap_active
        or snapshot.activity.kind in {"tool call", "agent", "assistant"}
    ):
        active.add(context.session_management_current_session_id)
    for item in snapshot.active_work_items:
        value = str(item.session_id or "").strip()
        try:
            canonical = str(uuid.UUID(value))
        except (AttributeError, TypeError, ValueError):
            continue
        if canonical == value.casefold() and str(item.status or "") != "recent":
            active.add(canonical)
    context.session_management_active_session_ids = active


def build_snapshot(
    context: object,
    ports: SnapshotBuilderPorts,
    *,
    refresh_budget_aggregate: bool | None = None,
    refresh_budget_paths: Iterable[Path] = (),
    refresh_active_work_items: bool = True,
    scan_active_work_candidates: bool = True,
    refresh_current_session_usage: bool = True,
    reuse_budget_from: ParsedSession | None = None,
    refresh_visible_app_error: bool = True,
) -> ParsedSession:
    def now_ms() -> int:
        return int(time.time() * 1000)

    build_started_at_ms = now_ms()
    context.reload_user_config()
    tracker = context.active_session_tracker
    selection_seq = int(getattr(tracker, "selection_seq", 0) or 0)
    selection_observed_at_ms = int(
        getattr(tracker, "selection_observed_at_ms", 0) or 0
    )
    selected = session_snapshots.resolve_selected_snapshot(context)
    session_path = selected.path
    selection_source = selected.selection_source
    session_resolved_at_ms = now_ms()
    ports.record_active_session_error(context, selection_source, session_path)
    snapshot = selected.snapshot
    session_parsed_at_ms = now_ms()
    _enrich_tracker(
        context,
        snapshot,
        session_path,
        selection_seq=selection_seq,
        selection_observed_at_ms=selection_observed_at_ms,
    )
    tracker_enriched_at_ms = now_ms()
    app_error = context.visible_app_error_cache.resolve(
        snapshot,
        _visible_app_error(context.platform) if refresh_visible_app_error else "",
    )
    _apply_visible_app_error(snapshot, app_error)
    app_error_checked_at_ms = now_ms()
    budget_ready = True
    if reuse_budget_from is not None:
        _reuse_budget(context, snapshot, reuse_budget_from, ports)
        # A reused budget is only as trustworthy as its source. A deferred
        # cold-start frame carries placeholder zeros, and re-publishing those
        # as measured usage would flash "今日 ¥0.00" instead of the honest
        # "今日 计算中…" label.
        budget_ready = bool(getattr(reuse_budget_from, "budget_ready", True))
    else:
        budget_ready = _summarize_budget(
            context,
            snapshot,
            session_path,
            ports,
            refresh_budget_aggregate=refresh_budget_aggregate,
            refresh_budget_paths=refresh_budget_paths,
            refresh_current_session_usage=refresh_current_session_usage,
        )
    # Renderers must not show the deferred placeholder zeros as measured usage.
    snapshot.budget_ready = bool(budget_ready)
    usage_summarized_at_ms = now_ms()
    # Active-work discovery has a small bounded candidate set and must not be
    # coupled to the cold budget scan. Otherwise a HUD restart leaves every
    # already-running CLI conversation without a bubble until budgets warm.
    if refresh_active_work_items:
        if scan_active_work_candidates:
            snapshot.active_work_items = ports.active_work_items(
                context, snapshot, session_path
            )
        else:
            snapshot.active_work_items = ports.active_work_items(
                context, snapshot, session_path, scan_candidates=False
            )
    apply_pre_send_and_activity(context, snapshot)
    snapshot.follow_timing = {
        **dict(snapshot.follow_timing or {}),
        "buildStartedAt": build_started_at_ms,
        "sessionResolvedAt": session_resolved_at_ms,
        "sessionParsedAt": session_parsed_at_ms,
        "trackerEnrichedAt": tracker_enriched_at_ms,
        "appErrorCheckedAt": app_error_checked_at_ms,
        "usageSummarizedAt": usage_summarized_at_ms,
        "runtimeEnrichedAt": now_ms(),
    }
    update_session_cleanup_activity(context, snapshot)
    return snapshot


def snapshot_to_text(snapshot: ParsedSession, compact: bool = False) -> str:
    model_name = snapshot.request.model or "n/a"
    task_tokens, _ = runtime_usage.current_task_usage(snapshot)
    session_cost = runtime_usage.current_session_cost(snapshot)
    money = runtime_usage.format_money
    tokens = runtime_usage.format_tokens
    if compact:
        return (
            f"session={snapshot.session_id} status={snapshot.status} source={snapshot.selection_source} model={model_name} "
            f"task_tokens={tokens(task_tokens)} session_cost={money(session_cost)} "
            f"today={tokens(snapshot.today_tokens)}/{money(snapshot.today_cost_usd)} "
            f"week={tokens(snapshot.week_tokens)}/{money(snapshot.week_cost_usd)}"
        )
    lines = [
        f"Session: {snapshot.session_id}",
        f"Status: {snapshot.status}",
        f"Source: {snapshot.selection_source}",
        f"Model: {model_name}",
        f"Current Task: {tokens(task_tokens)} tokens",
        f"Current Session Cost: {money(session_cost)}",
        f"Today: {tokens(snapshot.today_tokens)} tokens | {money(snapshot.today_cost_usd)} / {money(snapshot.daily_limit_usd)}",
        f"This Week: {tokens(snapshot.week_tokens)} tokens | {money(snapshot.week_cost_usd)} / {money(snapshot.weekly_limit_usd)}",
        f"This Week Breakdown: before today reset {money(snapshot.week_before_today_cost_usd)} + today {money(snapshot.today_cost_usd)}",
        f"Activity: {snapshot.activity.kind} | {snapshot.activity.detail or 'n/a'}",
        f"Path: {snapshot.session_path or 'n/a'}",
    ]
    if snapshot.week_adjustment_usd > 0:
        lines.append(f"This Week Manual Adjustment: {money(snapshot.week_adjustment_usd)}")
    if snapshot.budget_warnings:
        lines.append("Budget Warnings: " + " | ".join(snapshot.budget_warnings))
    if snapshot.error:
        lines.append(f"Error: {snapshot.error}")
    return "\n".join(lines)


__all__ = [
    "RuntimeSnapshotBuilder",
    "SnapshotBuilderPorts",
    "VisibleAppErrorCache",
    "apply_pre_send_and_activity",
    "apply_pre_send_pricing",
    "build_snapshot",
    "selection_is_stale",
    "snapshot_to_text",
    "update_session_cleanup_activity",
]
