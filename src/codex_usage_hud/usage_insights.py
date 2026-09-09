"""Usage-insights worker lifecycle independent of runtime coordination."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging
from pathlib import Path
from threading import Event, Lock, Thread

from .active_work import _effective_provider_scope
from .codex_provider_config import (
    _user_environment_value,
    read_codex_auth_api_key,
)
from .config import UserConfig
from .core import ParsedSession, UsageSummary
from .runtime_policies import budget_windows as current_budget_windows
from .runtime_usage import merge_usage as _merge_usage
from .usage_contributions import (
    FileUsageContribution as _UsageCacheEntry,
    UsageInsightAggregate as _UsageInsightAggregate,
    canonical_usage_path,
    path_under_usage_roots,
    usage_scan_roots,
)


USAGE_INSIGHTS_TOP_SESSION_LIMIT = 10


@dataclass
class _UsageInsightSessionAggregate:
    session_id: str
    session_key: str
    title: str
    provider: str
    workdir_name: str
    workdir: str
    archived: bool
    can_activate: bool
    models: dict[tuple[str, str], _UsageInsightAggregate] = field(default_factory=dict)
    usage: _UsageInsightAggregate = field(default_factory=_UsageInsightAggregate)


_LOGGER = logging.getLogger("codex_usage_hud.usage_insights")


def _background_summary(context: object, range_key: str) -> dict[str, object]:
    runtime = getattr(context, "background_usage_runtime", None)
    query = getattr(runtime, "query", None)
    unavailable = {
        "available": False,
        "requestCount": 0,
        "totalTokens": 0,
        "estimatedCostUsd": None,
        "costComplete": False,
        "pendingCount": 0,
    }
    if not callable(query):
        return unavailable
    try:
        raw = query(range_key=range_key, feature="", model="", event_id="")
        summary = raw.get("summary") if isinstance(raw, Mapping) else None
        values = dict(summary) if isinstance(summary, Mapping) else {}
        pending_count = 0
        if range_key == "today":
            pending_today = getattr(runtime, "pending_today", None)
            if callable(pending_today):
                pending_count = len(pending_today())
        return {
            "available": True,
            "requestCount": max(0, int(values.get("requestCount") or 0)),
            "totalTokens": max(0, int(values.get("totalTokens") or 0)),
            "estimatedCostUsd": values.get("estimatedCostUsd"),
            "costComplete": bool(values.get("costComplete", False)),
            "pendingCount": max(0, int(pending_count)),
            "range": range_key,
            "separateFromSessionTotals": True,
        }
    except Exception as exc:
        _LOGGER.debug(
            "usage_insights_background_summary_failed range=%s error=%s",
            range_key,
            exc,
        )
        return unavailable


def _session_title(context: object, session_id: str) -> str:
    normalized = str(session_id or "").strip()
    if not normalized:
        return ""
    tracker = getattr(context, "active_session_tracker", None)
    for method_name in ("title_from_thread_id", "title_from_session_index_id"):
        resolver = getattr(tracker, method_name, None)
        if not callable(resolver):
            continue
        try:
            title = " ".join(str(resolver(normalized) or "").split())
        except Exception:
            continue
        if title:
            return title
    return f"会话 {normalized[:8]}"


def _usage_insights_windows(
    config: UserConfig,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime, datetime]:
    """Keep the Top10 seven-day range independent of configurable budgets."""
    current = now or datetime.now().astimezone()
    day_start, budget_week_start = current_budget_windows(config, now=current)
    local_today = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start, budget_week_start, local_today - timedelta(days=6)


def build_usage_insights_payload(
    context: object,
    *,
    day_start: datetime,
    week_start: datetime,
    rolling_week_start: datetime | None = None,
    included_providers: Iterable[str] | None,
) -> dict[str, object]:
    usage_cache = getattr(context, "usage_cache")
    payload = dict(
        usage_cache.insights(
            Path(getattr(context, "sessions_root")),
            day_start,
            week_start,
            rolling_week_start=rolling_week_start,
            included_providers=included_providers,
        )
    )
    background_by_window = {
        "today": _background_summary(context, "today"),
        "week": _background_summary(context, "7d"),
        "month": _background_summary(context, "30d"),
    }
    title_cache: dict[str, str] = {}
    for window_name in ("today", "week", "month"):
        raw_window = payload.get(window_name)
        window = dict(raw_window) if isinstance(raw_window, Mapping) else {}
        totals = dict(window.get("totals") or {})
        coverage = dict(totals.get("costCoverage") or {})

        def project_sessions(value: object) -> list[dict[str, object]]:
            sessions: list[dict[str, object]] = []
            if not isinstance(value, list):
                return sessions
            for raw_session in value:
                if not isinstance(raw_session, Mapping):
                    continue
                session = dict(raw_session)
                session_id = str(session.get("sessionId") or "").strip()
                can_activate = bool(session.get("canActivate")) and bool(session_id)
                title = " ".join(str(session.get("title") or "").split())
                if session_id and not title:
                    title = title_cache.get(session_id, "") or _session_title(
                        context, session_id
                    )
                if session_id and title:
                    title_cache[session_id] = title
                session.update(
                    {
                        "id": session_id,
                        "title": title or "未命名会话",
                        "actionable": can_activate,
                    }
                )
                sessions.append(session)
            return sessions

        sessions = project_sessions(window.get("sessions"))
        totals["sessionCount"] = max(
            len(sessions), int(totals.get("sessionCount") or 0)
        )
        window.update(
            {
                "totals": totals,
                "costCoverage": coverage,
                "sessions": sessions,
                "topSessionsByUsage": project_sessions(
                    window.get("topSessionsByUsage")
                ),
                "topSessionsByCost": project_sessions(
                    window.get("topSessionsByCost")
                ),
                "background": background_by_window[window_name],
            }
        )
        payload[window_name] = window
    payload.update(
        {
            "state": "ready" if bool(payload.get("ready")) else "idle",
            "error": "",
            "backgroundSeparate": True,
        }
    )
    return payload


def apply_family_session_usage(
    usage_cache: object,
    snapshot: ParsedSession,
    provider_scope: Iterable[str] | None,
) -> None:
    session_id = str(snapshot.session_id or "").strip()
    parent_id = str(snapshot.parent_thread_id or "").strip()
    root_id = parent_id if snapshot.is_subagent and parent_id else session_id
    fallback_tokens = int(snapshot.confirmed.cumulative_total or 0)
    fallback_cost = snapshot.confirmed.cumulative_cost_usd
    lookup = getattr(usage_cache, "family_lifetime_usage", None)
    if not root_id or root_id == "n/a" or not callable(lookup):
        snapshot.family_tokens = fallback_tokens
        snapshot.family_cost_usd = fallback_cost
        snapshot.family_member_count = 1 if fallback_tokens else 0
        return
    try:
        family = lookup(root_id, included_providers=provider_scope)
    except Exception as exc:
        _LOGGER.debug("family_session_usage_failed session=%s error=%s", root_id, exc)
        family = None
    if family is None or (
        int(getattr(family, "tokens", 0) or 0) <= 0
        and float(getattr(family, "cost_usd", 0.0) or 0.0) <= 0.0
    ):
        snapshot.family_tokens = fallback_tokens
        snapshot.family_cost_usd = fallback_cost
        snapshot.family_member_count = 1 if fallback_tokens else 0
        return
    snapshot.family_tokens = int(getattr(family, "tokens", 0) or 0)
    snapshot.family_cost_usd = round(
        float(getattr(family, "cost_usd", 0.0) or 0.0), 6
    )
    member_ids = {root_id}
    for entry in list(getattr(usage_cache, "_entries", {}).values()):
        member_id = str(getattr(entry, "session_id", "") or "")
        parent = str(getattr(entry, "parent_session_id", "") or "")
        if member_id and (member_id == root_id or parent == root_id):
            member_ids.add(member_id)
    snapshot.family_member_count = len(member_ids)


class UsageInsightsProjector:
    def __init__(self, state: object) -> None:
        self._state = state

    @staticmethod
    def _event_value(event: object, name: str, default: object = None) -> object:
        if isinstance(event, Mapping):
            return event.get(name, default)
        return getattr(event, name, default)

    @staticmethod
    def _window_event_time(
        event: object,
        start_at: datetime,
    ) -> datetime | None:
        timestamp = UsageInsightsProjector._event_value(event, "timestamp")
        if not isinstance(timestamp, datetime):
            return None
        if start_at.tzinfo is None:
            event_time = (
                timestamp.astimezone().replace(tzinfo=None)
                if timestamp.tzinfo is not None
                else timestamp
            )
        elif timestamp.tzinfo is None:
            event_time = timestamp.replace(tzinfo=start_at.tzinfo)
        else:
            event_time = timestamp.astimezone(start_at.tzinfo)
        return event_time if event_time >= start_at else None

    @staticmethod
    def _nonnegative_event_int(event: object, name: str) -> int:
        try:
            return max(0, int(UsageInsightsProjector._event_value(event, name, 0) or 0))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _summary_for_usage_event(cls, event: object) -> tuple[UsageSummary, bool]:
        input_tokens = cls._nonnegative_event_int(event, "input_tokens")
        cached_tokens = min(
            input_tokens,
            cls._nonnegative_event_int(event, "cached_tokens"),
        )
        raw_cost = cls._event_value(event, "cost_usd")
        try:
            cost = None if raw_cost is None else max(0.0, float(raw_cost))
        except (TypeError, ValueError):
            cost = None
        return (
            UsageSummary(
                tokens=cls._nonnegative_event_int(event, "total_tokens"),
                input_tokens=input_tokens,
                cached_tokens=cached_tokens,
                cache_write_tokens=min(
                    input_tokens - cached_tokens,
                    cls._nonnegative_event_int(event, "cache_write_tokens"),
                ),
                output_tokens=cls._nonnegative_event_int(event, "output_tokens"),
                reasoning_tokens=cls._nonnegative_event_int(event, "reasoning_tokens"),
                cost_usd=round(cost or 0.0, 6),
            ),
            cost is not None,
        )

    @staticmethod
    def _merge_insight_aggregate(
        target: _UsageInsightAggregate,
        summary: UsageSummary,
        *,
        priced_event_count: int,
        total_event_count: int,
        latest_event_at: datetime | None,
    ) -> None:
        _merge_usage(target.summary, summary)
        target.priced_event_count += max(0, int(priced_event_count))
        target.total_event_count += max(0, int(total_event_count))
        if latest_event_at is not None and (
            target.latest_event_at is None or latest_event_at > target.latest_event_at
        ):
            target.latest_event_at = latest_event_at

    @classmethod
    def _model_insights_for_window(
        cls,
        events: Sequence[object],
        start_at: datetime,
    ) -> tuple[dict[str, _UsageInsightAggregate], int, int, datetime | None]:
        models: dict[str, _UsageInsightAggregate] = {}
        priced_event_count = 0
        total_event_count = 0
        latest_event_at: datetime | None = None
        for event in events:
            event_time = cls._window_event_time(event, start_at)
            if event_time is None:
                continue
            model = str(cls._event_value(event, "model", "") or "").strip() or "unknown"
            summary, priced = cls._summary_for_usage_event(event)
            aggregate = models.setdefault(model, _UsageInsightAggregate())
            cls._merge_insight_aggregate(
                aggregate,
                summary,
                priced_event_count=int(priced),
                total_event_count=1,
                latest_event_at=event_time,
            )
            priced_event_count += int(priced)
            total_event_count += 1
            if latest_event_at is None or event_time > latest_event_at:
                latest_event_at = event_time
        return models, priced_event_count, total_event_count, latest_event_at

    @staticmethod
    def _insight_aggregate_payload(
        aggregate: _UsageInsightAggregate,
    ) -> dict[str, object]:
        summary = aggregate.summary
        input_tokens = max(0, int(summary.input_tokens or 0))
        cached_tokens = min(input_tokens, max(0, int(summary.cached_tokens or 0)))
        total_event_count = max(0, int(aggregate.total_event_count))
        priced_event_count = min(
            total_event_count,
            max(0, int(aggregate.priced_event_count)),
        )
        cost_usd: float | None = round(
            max(0.0, float(summary.cost_usd or 0.0)),
            6,
        )
        if total_event_count > 0 and priced_event_count == 0:
            cost_usd = None
        return {
            "tokens": max(0, int(summary.tokens or 0)),
            "inputTokens": input_tokens,
            "cachedTokens": cached_tokens,
            "cacheWriteTokens": max(0, int(summary.cache_write_tokens or 0)),
            "outputTokens": max(0, int(summary.output_tokens or 0)),
            "reasoningTokens": max(0, int(summary.reasoning_tokens or 0)),
            "costUsd": cost_usd,
            "cacheRatio": (
                round(cached_tokens / input_tokens, 6)
                if input_tokens > 0
                else None
            ),
            "costCoverage": {
                "pricedEventCount": priced_event_count,
                "totalEventCount": total_event_count,
                "hasCompleteCost": priced_event_count == total_event_count,
            },
            "latestEventAt": (
                aggregate.latest_event_at.isoformat(timespec="seconds")
                if aggregate.latest_event_at is not None
                else ""
            ),
        }

    def _window_insights(
        self,
        entries: Sequence[_UsageCacheEntry],
        *,
        window: str,
        start_at: datetime,
        limit: int,
    ) -> dict[str, object]:
        total = _UsageInsightAggregate()
        provider_totals: dict[str, _UsageInsightAggregate] = {}
        model_totals: dict[tuple[str, str], _UsageInsightAggregate] = {}
        session_entries = {
            entry.session_id: entry
            for entry in entries
            if entry.session_id
        }
        session_groups: dict[str, _UsageInsightSessionAggregate] = {}

        def root_session(
            entry: _UsageCacheEntry,
        ) -> tuple[str, _UsageCacheEntry | None]:
            if not entry.session_id:
                return f"file:{entry.session_key}", entry
            current = entry
            chain = [entry.session_id]
            while current.parent_session_id:
                parent_id = current.parent_session_id
                if parent_id in chain:
                    cycle = chain[chain.index(parent_id) :]
                    root_id = min(cycle)
                    return root_id, session_entries.get(root_id)
                chain.append(parent_id)
                parent = session_entries.get(parent_id)
                if parent is None:
                    return parent_id, None
                current = parent
            return current.session_id, current

        for entry in entries:
            if window == "day":
                summary = entry.summary_day
                models = entry.models_day
                priced_event_count = entry.day_priced_event_count
                total_event_count = entry.day_total_event_count
                latest_event_at = entry.day_latest_event_at
            elif window == "rolling_week":
                (
                    summary,
                    models,
                    priced_event_count,
                    total_event_count,
                    latest_event_at,
                ) = self._rolling_window_values(entry, start_at)
            elif window == "week":
                summary = entry.summary_week
                models = entry.models_week
                priced_event_count = entry.week_priced_event_count
                total_event_count = entry.week_total_event_count
                latest_event_at = entry.week_latest_event_at
            else:
                summary = entry.summary_month
                models = entry.models_month
                priced_event_count = entry.month_priced_event_count
                total_event_count = entry.month_total_event_count
                latest_event_at = entry.month_latest_event_at
            aggregate = _UsageInsightAggregate(
                summary=replace(summary),
                priced_event_count=priced_event_count,
                total_event_count=total_event_count,
                latest_event_at=latest_event_at,
            )
            self._merge_insight_aggregate(
                total,
                aggregate.summary,
                priced_event_count=aggregate.priced_event_count,
                total_event_count=aggregate.total_event_count,
                latest_event_at=aggregate.latest_event_at,
            )
            provider_total = provider_totals.setdefault(
                entry.model_provider,
                _UsageInsightAggregate(),
            )
            self._merge_insight_aggregate(
                provider_total,
                aggregate.summary,
                priced_event_count=aggregate.priced_event_count,
                total_event_count=aggregate.total_event_count,
                latest_event_at=aggregate.latest_event_at,
            )
            for model, model_aggregate in models.items():
                target = model_totals.setdefault(
                    (entry.model_provider, model),
                    _UsageInsightAggregate(),
                )
                self._merge_insight_aggregate(
                    target,
                    model_aggregate.summary,
                    priced_event_count=model_aggregate.priced_event_count,
                    total_event_count=model_aggregate.total_event_count,
                    latest_event_at=model_aggregate.latest_event_at,
                )
            if aggregate.total_event_count or aggregate.summary.tokens:
                root_id, root_entry = root_session(entry)
                metadata_entry = root_entry or entry
                group = session_groups.get(root_id)
                if group is None:
                    group = _UsageInsightSessionAggregate(
                        session_id=(root_id if entry.session_id else ""),
                        session_key=metadata_entry.session_key,
                        title=metadata_entry.session_title,
                        provider=metadata_entry.model_provider,
                        workdir_name=(
                            metadata_entry.workdir_name or entry.workdir_name
                        ),
                        workdir=(metadata_entry.workdir or entry.workdir),
                        archived=bool(metadata_entry.archived),
                        can_activate=bool(
                            root_entry is not None and root_entry.can_activate
                        ),
                    )
                    session_groups[root_id] = group
                for model, model_aggregate in models.items():
                    model_key = (entry.model_provider, model)
                    target_model = group.models.setdefault(
                        model_key,
                        _UsageInsightAggregate(),
                    )
                    self._merge_insight_aggregate(
                        target_model,
                        model_aggregate.summary,
                        priced_event_count=model_aggregate.priced_event_count,
                        total_event_count=model_aggregate.total_event_count,
                        latest_event_at=model_aggregate.latest_event_at,
                    )
                self._merge_insight_aggregate(
                    group.usage,
                    aggregate.summary,
                    priced_event_count=aggregate.priced_event_count,
                    total_event_count=aggregate.total_event_count,
                    latest_event_at=aggregate.latest_event_at,
                )

        sessions = [
            {
                "sessionId": group.session_id,
                "sessionKey": group.session_key,
                "title": group.title,
                "provider": group.provider,
                "workdirName": group.workdir_name,
                "workdir": group.workdir,
                "archived": group.archived,
                "canActivate": group.can_activate,
                "models": [
                    {
                        "model": model,
                        "provider": provider,
                        **self._insight_aggregate_payload(model_aggregate),
                    }
                    for (provider, model), model_aggregate in sorted(
                        group.models.items(),
                        key=lambda item: (
                            -int(item[1].summary.tokens or 0),
                            str(item[0][1]).casefold(),
                            str(item[0][0]).casefold(),
                        ),
                    )
                ],
                **self._insight_aggregate_payload(group.usage),
            }
            for group in session_groups.values()
        ]

        def rank_key(item: Mapping[str, object]) -> tuple[int, int, str]:
            return (
                -int(item.get("tokens") or 0),
                -int(item.get("inputTokens") or 0),
                str(
                    item.get("sessionKey")
                    or item.get("model")
                    or item.get("provider")
                    or ""
                ).casefold(),
            )

        model_rows = [
            {
                "model": model,
                "provider": provider,
                **self._insight_aggregate_payload(aggregate),
            }
            for (provider, model), aggregate in model_totals.items()
        ]
        provider_rows = [
            {
                "provider": provider,
                **self._insight_aggregate_payload(aggregate),
            }
            for provider, aggregate in provider_totals.items()
        ]
        sessions.sort(key=rank_key)
        cost_ranked_sessions = [
            item
            for item in sessions
            if item.get("costUsd") is not None
        ]
        cost_ranked_sessions.sort(
            key=lambda item: (
                -float(item.get("costUsd") or 0.0),
                -int(item.get("tokens") or 0),
                str(item.get("sessionKey") or "").casefold(),
            )
        )
        model_rows.sort(key=rank_key)
        provider_rows.sort(key=rank_key)
        totals_payload = self._insight_aggregate_payload(total)
        totals_payload["sessionCount"] = len(sessions)
        return {
            "startAt": start_at.isoformat(timespec="seconds"),
            "totals": totals_payload,
            "costCoverage": dict(totals_payload["costCoverage"]),
            "sessions": sessions[:limit],
            "topSessionsByUsage": sessions[:USAGE_INSIGHTS_TOP_SESSION_LIMIT],
            "topSessionsByCost": cost_ranked_sessions[
                :USAGE_INSIGHTS_TOP_SESSION_LIMIT
            ],
            "models": model_rows[:limit],
            "providers": provider_rows[:limit],
        }

    def _rolling_window_values(
        self,
        entry: _UsageCacheEntry,
        start_at: datetime,
    ) -> tuple[
        UsageSummary,
        dict[str, _UsageInsightAggregate],
        int,
        int,
        datetime | None,
    ]:
        """Aggregate cached local-day contributions for a rolling UI range."""
        summary = UsageSummary()
        models: dict[str, _UsageInsightAggregate] = {}
        priced_event_count = 0
        total_event_count = 0
        latest_event_at: datetime | None = None
        start_date = start_at.date().isoformat()
        for date_key, contribution in entry.daily_usage.items():
            if date_key < start_date:
                continue
            _merge_usage(summary, contribution.summary)
            priced_event_count += contribution.priced_event_count
            total_event_count += contribution.total_event_count
            if contribution.latest_event_at is not None and (
                latest_event_at is None
                or contribution.latest_event_at > latest_event_at
            ):
                latest_event_at = contribution.latest_event_at
            for model, model_aggregate in contribution.models.items():
                target = models.setdefault(model, _UsageInsightAggregate())
                self._merge_insight_aggregate(
                    target,
                    model_aggregate.summary,
                    priced_event_count=model_aggregate.priced_event_count,
                    total_event_count=model_aggregate.total_event_count,
                    latest_event_at=model_aggregate.latest_event_at,
                )
        return summary, models, priced_event_count, total_event_count, latest_event_at

    def insights(
        self,
        sessions_root: Path,
        day_start: datetime,
        week_start: datetime,
        *,
        rolling_week_start: datetime | None = None,
        included_providers: Iterable[str] | None = None,
        limit: int = 8,
    ) -> dict[str, object]:
        """Project already-cached usage contributions without filesystem work."""
        sessions_root = canonical_usage_path(sessions_root)
        scan_roots = usage_scan_roots(sessions_root)
        scan_key = (scan_roots, day_start, week_start)
        month_start = day_start - timedelta(days=29)
        ready = self._state._last_scan_key == scan_key
        providers = None
        if included_providers is not None:
            providers = {
                str(provider or "").strip().lower()
                for provider in included_providers
                if str(provider or "").strip()
            }
        raw_entries = list(self._state._entries.items())
        deduplicate = getattr(self._state, "_deduplicated_entries", None)
        if callable(deduplicate):
            raw_entries = deduplicate(raw_entries)
        entries = [
            entry
            for path, entry in raw_entries
            if ready
            and entry.day_start == day_start
            and entry.week_start == week_start
            and entry.month_start == month_start
            and path_under_usage_roots(path, scan_roots)
            and (providers is None or entry.model_provider in providers)
        ]
        deleted_entries = list(self._state._deleted_entries) if ready else []
        if providers is not None:
            deleted_entries = [
                entry
                for entry in deleted_entries
                if entry.model_provider in providers
            ]
        entries.extend(deleted_entries)
        row_limit = max(1, min(100, int(limit)))
        insights_week_start = rolling_week_start or week_start
        return {
            "ready": ready,
            "revision": int(self._state._insights_revision),
            "generatedAt": (
                self._state._insights_generated_at.isoformat(timespec="seconds")
                if self._state._insights_generated_at is not None
                else ""
            ),
            "providerScope": sorted(providers) if providers is not None else None,
            "today": self._window_insights(
                entries,
                window="day",
                start_at=day_start,
                limit=row_limit,
            ),
            "week": self._window_insights(
                entries,
                window=("rolling_week" if rolling_week_start is not None else "week"),
                start_at=insights_week_start,
                limit=row_limit,
            ),
            "month": self._window_insights(
                entries,
                window="month",
                start_at=month_start,
                limit=row_limit,
            ),
        }


class UsageInsightsWorker:
    def __init__(
        self,
        context: object,
        *,
        refresh: Callable[[object, str], Mapping[str, object]],
    ) -> None:
        self._context = context
        self._refresh = refresh
        self._lock = Lock()
        self._wake = Event()
        self._closed = Event()
        self._request_id = ""
        self._worker = Thread(
            target=self._run,
            name="codex-usage-hud-insights",
            daemon=True,
        )
        self._worker.start()

    def request_refresh(self, *, request_id: str = "") -> bool:
        if self._closed.is_set():
            return False
        with self._lock:
            self._request_id = str(request_id or "")
        current = dict(getattr(self._context, "usage_insights_payload", {}) or {})
        current.update(
            {"state": "loading", "error": "", "requestId": str(request_id or "")}
        )
        setattr(self._context, "usage_insights_payload", current)
        self._publish(current)
        self._wake.set()
        return True

    def close(self) -> None:
        self._closed.set()
        self._wake.set()
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)

    def _run(self) -> None:
        while not self._closed.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._closed.is_set():
                break
            with self._lock:
                request_id = self._request_id
                self._request_id = ""
            try:
                payload = dict(self._refresh(self._context, request_id))
            except Exception as exc:
                _LOGGER.exception("usage_insights_refresh_failed")
                payload = {
                    "state": "failed",
                    "ready": False,
                    "requestId": request_id,
                    "error": str(exc) or type(exc).__name__,
                }
            setattr(self._context, "usage_insights_payload", payload)
            self._publish(payload)
            if bool(payload.get("ready")):
                self._publish_cache_hydrated(payload)

    def _publish(self, payload: Mapping[str, object]) -> None:
        event_bus = getattr(self._context, "runtime_events", None)
        publish = getattr(event_bus, "publish", None)
        if callable(publish):
            publish(
                "usage_insights_changed",
                source="usage_insights",
                context={
                    "requestId": str(payload.get("requestId") or ""),
                    "revision": int(payload.get("revision") or 0),
                    "state": str(payload.get("state") or ""),
                },
            )

    def _publish_cache_hydrated(self, payload: Mapping[str, object]) -> None:
        event_bus = getattr(self._context, "runtime_events", None)
        publish = getattr(event_bus, "publish", None)
        if callable(publish):
            publish(
                "usage_cache_hydrated",
                source="usage_insights",
                context={
                    "requestId": str(payload.get("requestId") or ""),
                    "revision": int(payload.get("revision") or 0),
                },
            )


def _refresh_usage_insights_payload(context: object) -> dict[str, object]:
    try:
        day_start, week_start, rolling_week_start = _usage_insights_windows(
            getattr(context, "user_config", UserConfig.defaults())
        )
        payload = build_usage_insights_payload(
            context,
            day_start=day_start,
            week_start=week_start,
            rolling_week_start=rolling_week_start,
            included_providers=_effective_provider_scope(context),
        )
    except Exception as exc:
        _LOGGER.debug("usage_insights_projection_failed error=%s", exc)
        payload = {
            "state": "failed",
            "ready": False,
            "error": str(exc) or type(exc).__name__,
        }
    setattr(context, "usage_insights_payload", payload)
    return payload


def _refresh_usage_after_session_delete(context: object, request_id: str) -> None:
    worker = getattr(context, "usage_insights_worker", None)
    request_refresh = getattr(worker, "request_refresh", None)
    if callable(request_refresh):
        try:
            if request_refresh(request_id=f"session-delete:{request_id}"):
                return
        except Exception as exc:
            _LOGGER.debug("deleted_session_usage_queue_failed error=%s", exc)
    try:
        day_start, week_start = current_budget_windows(
            getattr(context, "user_config", UserConfig.defaults())
        )
        usage_cache = getattr(context, "usage_cache")
        usage_cache.summarize(
            Path(getattr(context, "sessions_root")),
            day_start,
            week_start,
            force_rescan=True,
            included_providers=_effective_provider_scope(context),
        )
        payload = _refresh_usage_insights_payload(context)
        event_bus = getattr(context, "runtime_events", None)
        publish = getattr(event_bus, "publish", None)
        if callable(publish):
            publish(
                "usage_insights_changed",
                source="session_cleanup",
                context={
                    "requestId": request_id,
                    "revision": int(payload.get("revision") or 0),
                    "state": str(payload.get("state") or ""),
                },
            )
    except Exception as exc:
        _LOGGER.exception("deleted_session_usage_refresh_failed error=%s", exc)


def _run_usage_insights_refresh(
    context: object,
    request_id: str,
) -> Mapping[str, object]:
    day_start, week_start, rolling_week_start = _usage_insights_windows(
        getattr(context, "user_config", UserConfig.defaults())
    )
    usage_cache = getattr(context, "usage_cache")
    usage_cache.summarize(
        Path(getattr(context, "sessions_root")),
        day_start,
        week_start,
        force_rescan=True,
        included_providers=_effective_provider_scope(context),
    )
    payload = build_usage_insights_payload(
        context,
        day_start=day_start,
        week_start=week_start,
        rolling_week_start=rolling_week_start,
        included_providers=_effective_provider_scope(context),
    )
    payload["state"] = "ready" if payload.get("ready") else "idle"
    payload["requestId"] = request_id
    return payload


def _provider_registry_payload(context: object) -> dict[str, object]:
    registry = getattr(context, "provider_registry", None)
    entries = getattr(registry, "entries", {})
    if not isinstance(entries, Mapping):
        return {}
    app_provider = str(getattr(registry, "app_provider", "") or "").strip().lower()
    config_path = str(getattr(registry, "config_path", "") or "").strip() or None
    return {
        provider: {
            "profiles": list(getattr(entry, "profile_names", ())),
            "historicalOnly": bool(getattr(entry, "historical_only", False)),
            "defined": bool(getattr(entry, "from_provider_definition", False)),
            "name": str(getattr(entry, "name", "") or ""),
            "baseUrl": str(getattr(entry, "base_url", "") or ""),
            "envKey": str(getattr(entry, "env_key", "") or ""),
            "officialAccount": bool(getattr(entry, "official_account", False)),
            "requiresOpenaiAuth": bool(getattr(entry, "requires_openai_auth", False)),
            "wireApi": str(getattr(entry, "wire_api", "responses") or "responses"),
            "hasApiKey": bool(getattr(entry, "has_api_key", False)),
            "apiKey": (
                read_codex_auth_api_key(config_path)
                if str(provider or "").strip().lower() == app_provider
                else _user_environment_value(str(getattr(entry, "env_key", "") or ""))
            ),
            "configText": (
                ""
                if str(provider or "").strip().lower() == app_provider
                else str(getattr(entry, "config_text", "") or "")
            ),
        }
        for provider, entry in entries.items()
    }
