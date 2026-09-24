"""Usage summary query and invalidation facade."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging
from pathlib import Path
import re
import sqlite3
import time
from typing import Any
import uuid

from .core import (
    JsonlSessionParser,
    UsageSummary,
    extract_session_thread_identity,
    message_text,
)
from .core.deleted_usage import (
    DeletedUsageEvent,
    DeletedUsageLedger,
    DeletedUsageLedgerError,
)
from .session_cleanup_runtime import DeletedUsageTransactions
from .runtime_usage import (
    merge_usage as _merge_usage,
    workdir_leaf as _workdir_leaf,
)
from .usage_insights import UsageInsightsProjector
from .usage_summary_store import UsageSummaryStore
from .usage_contributions import (
    DailyUsageContribution,
    FileUsageContribution as _UsageCacheEntry,
    UsageInsightAggregate as _UsageInsightAggregate,
    canonical_usage_path,
    iter_usage_jsonl_files,
    path_under_usage_roots,
    usage_parser_version,
    usage_scan_roots,
)

DEFAULT_USAGE_SUMMARY_RESCAN_SECONDS = 2.0
DEFAULT_USAGE_SUMMARY_TAIL_STATE_BYTES = 32 * 1024 * 1024
USAGE_INSIGHTS_TOP_SESSION_LIMIT = 10
_LOGGER = logging.getLogger("codex_usage_hud.usage_cache")


@dataclass
class _UsageInsightSessionAggregate:
    session_id: str
    session_key: str
    title: str
    provider: str
    workdir_name: str
    archived: bool
    can_activate: bool
    models: dict[tuple[str, str], _UsageInsightAggregate] = field(default_factory=dict)
    usage: _UsageInsightAggregate = field(default_factory=_UsageInsightAggregate)


class UsageSummaryCache:
    """Cache rolling day/week usage summaries per JSONL session file."""

    def __init__(
        self,
        parser: JsonlSessionParser,
        *,
        min_rescan_seconds: float = DEFAULT_USAGE_SUMMARY_RESCAN_SECONDS,
        max_tail_state_bytes: int = DEFAULT_USAGE_SUMMARY_TAIL_STATE_BYTES,
        deleted_usage_ledger: DeletedUsageLedger | None = None,
        summary_store: UsageSummaryStore | None = None,
    ) -> None:
        self._parser = parser
        self._min_rescan_seconds = max(0.0, float(min_rescan_seconds))
        self._max_tail_state_bytes = max(0, int(max_tail_state_bytes))
        self._deleted_usage_ledger = deleted_usage_ledger
        self._summary_store = summary_store
        self._deleted_usage_transactions = DeletedUsageTransactions(
            deleted_usage_ledger,
            parser,
            on_commit=self._touch_insights,
        )
        self._last_parser_version = usage_parser_version(parser)
        self._entries: dict[Path, _UsageCacheEntry] = {}
        self._dirty_entries: set[Path] = set()
        self._hydrated_scan_key: (
            tuple[tuple[Path, ...], datetime, datetime, str] | None
        ) = None
        self._deleted_entries: list[_UsageCacheEntry] = []
        self._last_scan_key: tuple[tuple[Path, ...], datetime, datetime] | None = None
        self._last_scan_at = 0.0
        self._last_day_total = UsageSummary()
        self._last_week_total = UsageSummary()
        self._insights_revision = 0
        self._insights_generated_at: datetime | None = None
        self._insights_projector = UsageInsightsProjector(self)

    def _hydrate_persisted_entries(
        self,
        scan_roots: tuple[Path, ...],
        day_start: datetime,
        week_start: datetime,
    ) -> None:
        store = self._summary_store
        parser_version = usage_parser_version(self._parser)
        hydration_key = (scan_roots, day_start, week_start, parser_version)
        if store is None or self._hydrated_scan_key == hydration_key:
            return
        try:
            self._entries = store.load(
                scan_roots,
                day_start,
                week_start,
                parser_version,
            )
        except (OSError, sqlite3.Error, ValueError):
            _LOGGER.exception("usage_summary_cache_hydration_failed")
            self._entries = {}
        self._dirty_entries.clear()
        self._hydrated_scan_key = hydration_key

    def _flush_persisted_entries(self) -> None:
        store = self._summary_store
        if store is None or not self._dirty_entries:
            return
        paths = tuple(self._dirty_entries)
        try:
            store.save_many(
                (path, self._entries[path]) for path in paths if path in self._entries
            )
        except (OSError, sqlite3.Error, ValueError, TypeError):
            _LOGGER.exception("usage_summary_cache_persist_failed")
            return
        self._dirty_entries.difference_update(paths)

    def _delete_persisted_entries(self, paths: Iterable[Path]) -> None:
        store = self._summary_store
        path_tuple = tuple(paths)
        if store is None or not path_tuple:
            return
        try:
            store.delete_many(path_tuple)
        except (OSError, sqlite3.Error):
            _LOGGER.exception("usage_summary_cache_delete_failed")

    @staticmethod
    def _cache_path(path: Path) -> Path:
        return canonical_usage_path(path)

    @staticmethod
    def _scan_roots(sessions_root: Path) -> tuple[Path, ...]:
        return usage_scan_roots(sessions_root)

    def _touch_insights(self) -> None:
        self._insights_revision += 1
        self._insights_generated_at = datetime.now().astimezone()

    def _refresh_parser_version(self) -> None:
        parser_version = usage_parser_version(self._parser)
        if parser_version == self._last_parser_version:
            return
        self._last_parser_version = parser_version
        self._entries.clear()
        self._dirty_entries.clear()
        self._hydrated_scan_key = None
        self._deleted_entries.clear()
        self._last_scan_key = None
        self._last_scan_at = 0.0
        self._last_day_total = UsageSummary()
        self._last_week_total = UsageSummary()
        self._touch_insights()

    def is_warm_for(
        self,
        sessions_root: Path,
        day_start: datetime,
        week_start: datetime,
    ) -> bool:
        """Return whether this cache has scanned the requested budget windows."""
        sessions_root = self._cache_path(sessions_root)
        scan_key = (self._scan_roots(sessions_root), day_start, week_start)
        return (
            self._last_scan_key == scan_key
            and self._last_parser_version == usage_parser_version(self._parser)
        )

    def _trim_tail_states(self) -> None:
        """Bound retained raw JSONL records while keeping recent files incremental."""
        retained_bytes = 0
        entries = sorted(
            self._entries.items(),
            key=lambda item: (float(item[1].mtime or 0.0), str(item[0])),
            reverse=True,
        )
        for _path, entry in entries:
            if entry.tail_state is None:
                continue
            file_size = max(0, int(entry.file_size or 0))
            if (
                file_size > self._max_tail_state_bytes
                or retained_bytes + file_size > self._max_tail_state_bytes
            ):
                entry.tail_state = None
                continue
            retained_bytes += file_size

    def prepare_deleted_session_usage(self, item: object) -> str:
        return self._deleted_usage_transactions.prepare(item)

    def commit_deleted_session_usage(self, receipt: object) -> None:
        self._deleted_usage_transactions.commit(receipt)

    def discard_deleted_session_usage(self, receipt: object) -> None:
        self._deleted_usage_transactions.discard(receipt)

    def _deleted_usage_entries(
        self,
        day_start: datetime,
        week_start: datetime,
        live_session_ids: set[str],
    ) -> list[_UsageCacheEntry]:
        ledger = self._deleted_usage_ledger
        if ledger is None:
            return []
        try:
            sessions = ledger.sessions()
        except DeletedUsageLedgerError as exc:
            _LOGGER.warning("deleted_session_usage_load_failed error=%s", exc)
            return []
        estimator = getattr(self._parser, "cost_estimator", None)
        price_snapshot = getattr(estimator, "price_snapshot", None)
        calculate = getattr(estimator, "calculate", None)
        month_start = day_start - timedelta(days=29)
        entries: list[_UsageCacheEntry] = []
        for session in sessions:
            if live_session_ids.intersection(session.family_session_ids):
                continue
            providers: dict[str, list[DeletedUsageEvent]] = {}
            for event in session.events:
                if (
                    event.cost_usd is None
                    and callable(price_snapshot)
                    and callable(calculate)
                ):
                    selected = price_snapshot(
                        event.model,
                        provider=event.provider,
                        occurred_at=event.timestamp,
                    )
                    if selected["status"] != "unavailable" and not selected["baseUrl"]:
                        cost = calculate(
                            event.model,
                            event.input_tokens,
                            event.cached_tokens,
                            event.output_tokens,
                            event.reasoning_tokens,
                            cache_write_tokens=event.cache_write_tokens,
                            provider=event.provider,
                            occurred_at=event.timestamp,
                        )
                        if cost is not None:
                            event = replace(event, cost_usd=cost)
                providers.setdefault(event.provider, []).append(event)
            for provider, events in providers.items():
                daily_usage = self._daily_usage_contributions(events, day_start)
                summary_day = self._parser.summarize_usage_events(events, day_start)
                summary_week = self._parser.summarize_usage_events(events, week_start)
                summary_month = self._parser.summarize_usage_events(events, month_start)
                (
                    models_day,
                    day_priced_event_count,
                    day_total_event_count,
                    day_latest_event_at,
                ) = self._model_insights_for_window(events, day_start)
                (
                    models_week,
                    week_priced_event_count,
                    week_total_event_count,
                    week_latest_event_at,
                ) = self._model_insights_for_window(events, week_start)
                (
                    models_month,
                    month_priced_event_count,
                    month_total_event_count,
                    month_latest_event_at,
                ) = self._model_insights_for_window(events, month_start)
                entries.append(
                    _UsageCacheEntry(
                        mtime=None,
                        file_size=None,
                        day_start=day_start,
                        week_start=week_start,
                        month_start=month_start,
                        model_provider=provider,
                        summary_day=summary_day,
                        summary_week=summary_week,
                        summary_month=summary_month,
                        session_id=session.session_id,
                        session_key=(
                            "deleted-session-"
                            + uuid.uuid5(uuid.NAMESPACE_URL, session.session_id).hex[
                                :16
                            ]
                        ),
                        session_title=session.title,
                        workdir_name=session.workdir_name,
                        archived=True,
                        can_activate=False,
                        models_day=models_day,
                        models_week=models_week,
                        models_month=models_month,
                        day_priced_event_count=day_priced_event_count,
                        day_total_event_count=day_total_event_count,
                        week_priced_event_count=week_priced_event_count,
                        week_total_event_count=week_total_event_count,
                        month_priced_event_count=month_priced_event_count,
                        month_total_event_count=month_total_event_count,
                        day_latest_event_at=day_latest_event_at,
                        week_latest_event_at=week_latest_event_at,
                        month_latest_event_at=month_latest_event_at,
                        daily_usage=daily_usage,
                        daily_usage_complete=True,
                    )
                )
        return entries

    def _session_meta_payload(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        payload: Mapping[str, Any] = {}
        payload_reader = getattr(self._parser, "session_meta_payload", None)
        if callable(payload_reader):
            raw_payload = payload_reader(records)
            if isinstance(raw_payload, Mapping):
                payload = raw_payload
        if not payload:
            for record in records:
                if record.get("type") != "session_meta":
                    continue
                raw_payload = record.get("payload")
                if isinstance(raw_payload, Mapping):
                    payload = raw_payload
                    break
        return payload

    @staticmethod
    def _canonical_session_id(value: object) -> str:
        candidate = str(value or "").strip()
        try:
            canonical = str(uuid.UUID(candidate))
        except (ValueError, AttributeError, TypeError):
            return ""
        return canonical if candidate.casefold() == canonical else ""

    @staticmethod
    def _entry_lifetime_rank(
        key: object,
        entry: _UsageCacheEntry,
    ) -> tuple[int, int, int, int, int, float, str]:
        summary = entry.summary_all
        return (
            int(summary.tokens or 0),
            int(summary.input_tokens or 0),
            int(summary.cached_tokens or 0),
            int(summary.output_tokens or 0),
            int(summary.total_event_count or 0),
            float(entry.mtime or 0.0),
            str(key),
        )

    def _deduplicated_entries(
        self,
        items: Iterable[tuple[object, _UsageCacheEntry]],
    ) -> list[tuple[object, _UsageCacheEntry]]:
        """Keep one cumulative snapshot per logical session ID.

        Codex Desktop can write several continuation JSONL files for one
        thread. Their cumulative counters include the preceding files, so
        aggregating every path would charge the same history repeatedly.
        Files without a session ID remain independent contributions.
        """
        selected: dict[tuple[str, str], tuple[object, _UsageCacheEntry]] = {}
        for key, entry in items:
            session_id = str(entry.session_id or "").strip()
            group_key = ("session", session_id) if session_id else ("path", str(key))
            previous = selected.get(group_key)
            if previous is None or self._entry_lifetime_rank(
                key, entry
            ) > self._entry_lifetime_rank(*previous):
                selected[group_key] = (key, entry)
        return list(selected.values())

    @classmethod
    def _delegated_source_session_id(
        cls,
        records: Sequence[Mapping[str, Any]],
    ) -> str:
        """Recover the parent ID from legacy desktop subagent prompts."""
        for record in records:
            if record.get("type") != "response_item":
                continue
            payload = record.get("payload")
            if not isinstance(payload, Mapping):
                continue
            if payload.get("type") != "message" or payload.get("role") != "user":
                continue
            text = message_text(payload)
            if "<codex_delegation" not in text:
                continue
            match = re.search(
                r"<source_thread_id>\s*([^<]+?)\s*</source_thread_id>",
                text,
                re.IGNORECASE,
            )
            if match:
                parent_id = cls._canonical_session_id(match.group(1))
                if parent_id:
                    return parent_id
        return ""

    def _is_archived_path(
        self,
        path: Path,
        scan_roots: Sequence[Path],
    ) -> bool:
        resolved = self._cache_path(path)
        for root in scan_roots:
            if root.name.casefold() != "archived_sessions":
                continue
            try:
                resolved.relative_to(self._cache_path(root))
            except ValueError:
                continue
            return True
        return False

    def _model_insights_for_window(
        self, events: Sequence[object], start: datetime
    ) -> tuple[dict[str, _UsageInsightAggregate], int, int, datetime | None]:
        return self._insights_projector._model_insights_for_window(events, start)

    def _daily_usage_contributions(
        self,
        events: Sequence[object],
        reference: datetime,
    ) -> dict[str, DailyUsageContribution]:
        grouped: dict[str, list[object]] = {}
        for event in events:
            timestamp = getattr(event, "timestamp", None)
            if not isinstance(timestamp, datetime):
                continue
            if reference.tzinfo is None:
                local_time = (
                    timestamp.astimezone().replace(tzinfo=None)
                    if timestamp.tzinfo is not None
                    else timestamp
                )
            elif timestamp.tzinfo is None:
                local_time = timestamp.replace(tzinfo=reference.tzinfo)
            else:
                local_time = timestamp.astimezone(reference.tzinfo)
            grouped.setdefault(local_time.date().isoformat(), []).append(event)

        contributions: dict[str, DailyUsageContribution] = {}
        for date_key, daily_events in grouped.items():
            year, month, day = (int(value) for value in date_key.split("-"))
            bucket_start = reference.replace(
                year=year,
                month=month,
                day=day,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            models, priced_count, total_count, latest_at = (
                self._model_insights_for_window(daily_events, bucket_start)
            )
            contributions[date_key] = DailyUsageContribution(
                summary=self._parser.summarize_usage_events(
                    daily_events,
                    bucket_start,
                ),
                models=models,
                priced_event_count=priced_count,
                total_event_count=total_count,
                latest_event_at=latest_at,
            )
        return contributions

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
        return self._insights_projector.insights(
            sessions_root,
            day_start,
            week_start,
            rolling_week_start=rolling_week_start,
            included_providers=included_providers,
            limit=limit,
        )

    def summarize(
        self,
        sessions_root: Path,
        day_start: datetime,
        week_start: datetime,
        *,
        allow_stale: bool = False,
        force_rescan: bool = False,
        refresh_paths: Iterable[Path] = (),
        included_providers: Iterable[str] | None = None,
    ) -> tuple[UsageSummary, UsageSummary]:
        now = time.monotonic()
        self._refresh_parser_version()
        sessions_root = self._cache_path(sessions_root)
        scan_roots = self._scan_roots(sessions_root)
        self._hydrate_persisted_entries(scan_roots, day_start, week_start)
        scan_key = (scan_roots, day_start, week_start)
        refresh_path_tuple = tuple(
            dict.fromkeys(self._cache_path(path) for path in refresh_paths)
        )
        if not force_rescan and self._last_scan_key == scan_key and refresh_path_tuple:
            self._refresh_paths(
                refresh_path_tuple,
                scan_roots,
                day_start,
                week_start,
            )
            self._last_scan_at = now
        if allow_stale and self._last_scan_key == scan_key:
            return self._totals_for_providers(
                scan_roots,
                day_start,
                week_start,
                included_providers,
            )
        if (
            not force_rescan
            and self._last_scan_key == scan_key
            and now - self._last_scan_at < self._min_rescan_seconds
        ):
            return self._totals_for_providers(
                scan_roots,
                day_start,
                week_start,
                included_providers,
            )

        day_total = UsageSummary()
        week_total = UsageSummary()
        previous_scan_key = self._last_scan_key
        revision_before_scan = self._insights_revision
        previous_deleted_entries = list(self._deleted_entries)

        existing_roots = [root for root in scan_roots if root.exists()]
        if not existing_roots:
            had_entries = bool(self._entries)
            removed_paths = tuple(self._entries)
            self._entries.clear()
            self._dirty_entries.clear()
            self._delete_persisted_entries(removed_paths)
            deleted_entries = self._deleted_usage_entries(day_start, week_start, set())
            self._deleted_entries = deleted_entries
            if deleted_entries != previous_deleted_entries:
                self._touch_insights()
            for entry in deleted_entries:
                _merge_usage(day_total, entry.summary_day)
                _merge_usage(week_total, entry.summary_week)
            self._last_scan_key = scan_key
            self._last_scan_at = now
            self._last_day_total = day_total
            self._last_week_total = week_total
            if had_entries or previous_scan_key != scan_key:
                self._touch_insights()
            return self._totals_for_providers(
                scan_roots,
                day_start,
                week_start,
                included_providers,
            )

        seen_paths: set[Path] = set()
        scan_items: list[tuple[Path, bool]] = []
        for root in existing_roots:
            archived = root.name.casefold() == "archived_sessions"
            for path in iter_usage_jsonl_files(root):
                path = self._cache_path(path)
                seen_paths.add(path)
                scan_items.append((path, archived))
        for path, archived in scan_items:
            self._summaries_for_file(
                path,
                day_start,
                week_start,
                archived=archived,
            )

        removed_paths: list[Path] = []
        for cached_path in list(self._entries):
            if cached_path not in seen_paths:
                del self._entries[cached_path]
                self._dirty_entries.discard(cached_path)
                removed_paths.append(cached_path)
                self._touch_insights()
        self._delete_persisted_entries(removed_paths)
        self._flush_persisted_entries()

        for _path, entry in self._deduplicated_entries(self._entries.items()):
            if entry.day_start != day_start or entry.week_start != week_start:
                continue
            _merge_usage(day_total, entry.summary_day)
            _merge_usage(week_total, entry.summary_week)

        live_session_ids = {
            entry.session_id for entry in self._entries.values() if entry.session_id
        }
        self._deleted_entries = self._deleted_usage_entries(
            day_start,
            week_start,
            live_session_ids,
        )
        if self._deleted_entries != previous_deleted_entries:
            self._touch_insights()
        for entry in self._deleted_entries:
            _merge_usage(day_total, entry.summary_day)
            _merge_usage(week_total, entry.summary_week)

        self._last_scan_key = scan_key
        self._last_scan_at = now
        self._last_day_total = replace(day_total)
        self._last_week_total = replace(week_total)
        if (
            previous_scan_key != scan_key
            and revision_before_scan == self._insights_revision
        ):
            self._touch_insights()
        return self._totals_for_providers(
            scan_roots,
            day_start,
            week_start,
            included_providers,
        )

    def _totals_for_providers(
        self,
        scan_roots: Sequence[Path],
        day_start: datetime,
        week_start: datetime,
        included_providers: Iterable[str] | None,
    ) -> tuple[UsageSummary, UsageSummary]:
        if included_providers is None:
            return replace(self._last_day_total), replace(self._last_week_total)
        providers = {
            str(provider or "").strip().lower()
            for provider in included_providers
            if str(provider or "").strip()
        }
        day_total = UsageSummary()
        week_total = UsageSummary()
        for path, entry in self._deduplicated_entries(self._entries.items()):
            if entry.day_start != day_start or entry.week_start != week_start:
                continue
            if entry.model_provider not in providers:
                continue
            if not self._path_under_scan_roots(path, scan_roots):
                continue
            _merge_usage(day_total, entry.summary_day)
            _merge_usage(week_total, entry.summary_week)
        for entry in self._deleted_entries:
            if entry.model_provider not in providers:
                continue
            _merge_usage(day_total, entry.summary_day)
            _merge_usage(week_total, entry.summary_week)
        return day_total, week_total

    def family_lifetime_usage(
        self,
        session_id: str,
        *,
        included_providers: Iterable[str] | None = None,
    ) -> UsageSummary:
        """Sum lifetime usage for a root session and its subagent children."""
        root_id = self._canonical_session_id(session_id)
        total = UsageSummary()
        if not root_id:
            return total
        providers = None
        if included_providers is not None:
            providers = {
                str(provider or "").strip().lower()
                for provider in included_providers
                if str(provider or "").strip()
            }
        all_items = self._deduplicated_entries(
            list(self._entries.items())
            + [
                (f"deleted:{index}:{entry.session_key}", entry)
                for index, entry in enumerate(self._deleted_entries)
            ]
        )
        session_entries = {
            entry.session_id: entry
            for _key, entry in all_items
            if entry.session_id
        }

        def root_of(entry: _UsageCacheEntry) -> str:
            if not entry.session_id:
                return ""
            current = entry
            chain = [entry.session_id]
            while current.parent_session_id:
                parent_id = current.parent_session_id
                if parent_id in chain:
                    return min(chain[chain.index(parent_id) :])
                chain.append(parent_id)
                parent = session_entries.get(parent_id)
                if parent is None:
                    return parent_id
                current = parent
            return current.session_id

        for _key, entry in all_items:
            if providers is not None and entry.model_provider not in providers:
                continue
            member_id = entry.session_id or ""
            if member_id != root_id and root_of(entry) != root_id:
                continue
            summary = entry.summary_all
            if summary.tokens <= 0 and float(summary.cost_usd or 0.0) <= 0.0:
                # Older cache entries or deleted ledger rows without lifetime.
                summary = entry.summary_month
            _merge_usage(total, summary)
        return total

    def _path_under_scan_roots(self, path: Path, scan_roots: Sequence[Path]) -> bool:
        return path_under_usage_roots(path, scan_roots)

    def _refresh_paths(
        self,
        paths: Sequence[Path],
        scan_roots: Sequence[Path],
        day_start: datetime,
        week_start: datetime,
    ) -> None:
        for path in paths:
            if not self._path_under_scan_roots(path, scan_roots):
                continue

            if path.exists():
                self._summaries_for_file(
                    path,
                    day_start,
                    week_start,
                    force=True,
                    archived=self._is_archived_path(path, scan_roots),
                )
            else:
                if self._entries.pop(path, None) is not None:
                    self._touch_insights()
                self._dirty_entries.discard(path)
                self._delete_persisted_entries((path,))
        day_total = UsageSummary()
        week_total = UsageSummary()
        for path, entry in self._deduplicated_entries(self._entries.items()):
            if (
                entry.day_start != day_start
                or entry.week_start != week_start
                or not self._path_under_scan_roots(path, scan_roots)
            ):
                continue
            _merge_usage(day_total, entry.summary_day)
            _merge_usage(week_total, entry.summary_week)
        for entry in self._deleted_entries:
            _merge_usage(day_total, entry.summary_day)
            _merge_usage(week_total, entry.summary_week)

        self._last_day_total = day_total
        self._last_week_total = week_total
        self._flush_persisted_entries()

    def _summaries_for_file(
        self,
        path: Path,
        day_start: datetime,
        week_start: datetime,
        *,
        force: bool = False,
        archived: bool | None = None,
    ) -> tuple[UsageSummary, UsageSummary, UsageSummary]:
        try:
            stat = path.stat()
        except OSError:
            if self._entries.pop(path, None) is not None:
                self._touch_insights()
            return UsageSummary(), UsageSummary(), UsageSummary()

        entry = self._entries.get(path)
        if (
            not force
            and entry is not None
            and entry.parser_version == usage_parser_version(self._parser)
            and (
                entry.mtime_ns == int(stat.st_mtime_ns)
                if entry.mtime_ns is not None
                else entry.mtime == stat.st_mtime
            )
            and entry.file_size == stat.st_size
            and entry.day_start == day_start
            and entry.week_start == week_start
            and entry.month_start == day_start - timedelta(days=29)
            and (archived is None or entry.archived == archived)
        ):
            return entry.summary_day, entry.summary_week, entry.summary_month

        parser_version = usage_parser_version(self._parser)
        incremental_record_reader = getattr(
            self._parser,
            "load_records_incremental",
            None,
        )
        incremental_reader = getattr(self._parser, "parse_file_incremental", None)
        tail_state = None
        try:
            previous_state = (
                entry.tail_state
                if entry is not None and entry.parser_version == parser_version
                else None
            )
            if callable(incremental_record_reader):
                tail_state = incremental_record_reader(path, previous_state)
                records = tail_state.records
            elif callable(incremental_reader):
                _snapshot, tail_state = incremental_reader(path, previous_state)
                records = tail_state.records
            else:
                records = self._parser.load_records_lenient(path)
        except OSError:
            if self._entries.pop(path, None) is not None:
                self._touch_insights()
            return UsageSummary(), UsageSummary(), UsageSummary()

        events = self._parser.usage_events(records)
        daily_usage = self._daily_usage_contributions(events, day_start)
        provider_reader = getattr(self._parser, "session_model_provider", None)
        model_provider = (
            str(provider_reader(records) or "").strip().lower()
            if callable(provider_reader)
            else "unknown"
        ) or "unknown"
        summary_day = self._parser.summarize_usage_events(events, day_start)
        summary_week = self._parser.summarize_usage_events(events, week_start)
        month_start = day_start - timedelta(days=29)
        summary_month = self._parser.summarize_usage_events(events, month_start)
        lifetime_start = day_start - timedelta(days=36500)
        summary_all = self._parser.summarize_usage_events(events, lifetime_start)
        (
            models_day,
            day_priced_event_count,
            day_total_event_count,
            day_latest_event_at,
        ) = self._model_insights_for_window(events, day_start)
        (
            models_week,
            week_priced_event_count,
            week_total_event_count,
            week_latest_event_at,
        ) = self._model_insights_for_window(events, week_start)
        (
            models_month,
            month_priced_event_count,
            month_total_event_count,
            month_latest_event_at,
        ) = self._model_insights_for_window(events, month_start)
        session_meta = self._session_meta_payload(records)
        session_id = self._canonical_session_id(session_meta.get("id"))
        session_title = " ".join(
            str(
                session_meta.get("title")
                or session_meta.get("session_title")
                or session_meta.get("name")
                or ""
            ).split()
        )
        _thread_source, raw_parent_id, _agent_nickname, is_subagent = (
            extract_session_thread_identity(session_meta)
        )
        if is_subagent and not raw_parent_id:
            raw_parent_id = self._delegated_source_session_id(records)
        parent_session_id = (
            self._canonical_session_id(raw_parent_id) if is_subagent else ""
        )
        if parent_session_id == session_id:
            parent_session_id = ""
        is_archived = (
            bool(archived)
            if archived is not None
            else ("archived_sessions" in {part.casefold() for part in path.parts})
        )
        self._entries[path] = _UsageCacheEntry(
            mtime=stat.st_mtime,
            file_size=stat.st_size,
            day_start=day_start,
            week_start=week_start,
            month_start=month_start,
            model_provider=model_provider,
            summary_day=summary_day,
            summary_week=summary_week,
            summary_month=summary_month,
            summary_all=summary_all,
            session_id=session_id,
            parent_session_id=parent_session_id,
            session_key=path.stem,
            session_title=session_title,
            workdir_name=_workdir_leaf(session_meta.get("cwd")),
            workdir=str(session_meta.get("cwd") or "").strip(),
            archived=is_archived,
            can_activate=bool(session_id) and not is_archived,
            models_day=models_day,
            models_week=models_week,
            models_month=models_month,
            day_priced_event_count=day_priced_event_count,
            day_total_event_count=day_total_event_count,
            week_priced_event_count=week_priced_event_count,
            week_total_event_count=week_total_event_count,
            month_priced_event_count=month_priced_event_count,
            month_total_event_count=month_total_event_count,
            day_latest_event_at=day_latest_event_at,
            week_latest_event_at=week_latest_event_at,
            month_latest_event_at=month_latest_event_at,
            parser_version=parser_version,
            tail_state=tail_state,
            mtime_ns=int(stat.st_mtime_ns),
            daily_usage=daily_usage,
            daily_usage_complete=True,
        )
        self._dirty_entries.add(path)
        self._trim_tail_states()
        self._touch_insights()
        return summary_day, summary_week, summary_month
