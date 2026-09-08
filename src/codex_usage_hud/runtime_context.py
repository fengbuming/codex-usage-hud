"""Owned renderer runtime resources with explicit lifecycle management."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import logging
from pathlib import Path
import argparse

from .background_usage_runtime import (
    BACKGROUND_USAGE_DATABASE_FILENAME,
    BackgroundUsageRuntime,
)
from .config import (
    DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED,
    UserConfig,
    UserConfigStore,
    normalize_display_mode,
)
from .core import (
    CostEstimator,
    JsonlSessionParser,
    PreSendEstimator,
    SseRequestStateMachine,
    UsageCalculator,
)
from .core.deleted_usage import DeletedUsageLedger
from .core.rest_reminder import RestReminderPresenter
from .core.runtime_errors import RuntimeErrorRegistry
from .core.runtime_events import RuntimeEventBus
from .core.session_search import DEFAULT_RANGE, wider_range_key
from .platforms import ActiveSessionTracker, SessionPathResolver, get_current_platform
from .platforms.base import BasePlatform
from .provider_registry import ProviderRegistry, discover_provider_registry
from .runtime_diagnostics import ensure_runtime_error_diagnostics
from .runtime_paths import hud_runtime_dir
from . import runtime_config
from .session_cleanup_runtime import _build_session_cleanup_manager
from .session_cleanup_runtime import SessionCleanupWorker
from .session_snapshots import SessionSnapshotCache
from .snapshot_builder import VisibleAppErrorCache
from .usage_cache import UsageSummaryCache
from .usage_summary_store import UsageSummaryStore
from .usage_insights import (
    UsageInsightsWorker,
    _refresh_usage_after_session_delete,
    _refresh_usage_insights_payload,
    _run_usage_insights_refresh,
)


DEFAULT_SQLITE_LOG = "logs_2.sqlite"
DEFAULT_STATE_DB = "state_5.sqlite"
DEFAULT_SESSION_INDEX = "session_index.jsonl"
DELETED_SESSION_USAGE_FILENAME = "deleted_session_usage.json"
USAGE_SUMMARY_DATABASE_FILENAME = "usage-summary.sqlite3"

_LOGGER = logging.getLogger(__name__)

# Cold-start interactive-window guard for the warm job's finalize thread.
# Its work (117 MB snapshot unpickle ~3 s + doc-table scan ~8.5 s on a 2.89 GB
# database) is GIL/IO heavy; run at startup it starved the authoritative HUD
# payload behind the "step 4" bubble by 10-20 s and left zeroed panels. The
# finalize thread waits this many seconds before starting so the interactive
# startup finishes first (PRD §11: yield while the UI is busy).
_SESSION_INDEX_PRELOAD_GRACE_SECONDS = 20.0


@dataclass
class RuntimeContext:
    """Resources and configuration owned by one renderer runtime invocation.

    Construction intentionally does not start workers. The composition root owns
    that work so tests and alternate entry points can construct a context without
    hidden threads or filesystem activity.
    """

    platform: BasePlatform
    sessions_root: Path
    session_file: Path | None
    sqlite_log_path: Path | None
    state_db_path: Path
    session_index_path: Path
    poll_ms: int
    daily_budget_usd: float
    weekly_budget_usd: float
    budget_thresholds: list[float]
    user_config: UserConfig
    settings_store: UserConfigStore
    settings_mtime: float | None
    parser: JsonlSessionParser
    sse_tracker: SseRequestStateMachine | None
    active_session_tracker: object | None
    session_resolver: object
    usage_cache: UsageSummaryCache
    app_provider: str = ""
    provider_registry: ProviderRegistry | None = None
    pre_send_estimator: PreSendEstimator | None = None
    runtime_events: RuntimeEventBus = field(default_factory=RuntimeEventBus)
    runtime_errors: RuntimeErrorRegistry = field(default_factory=RuntimeErrorRegistry)
    visible_app_error_cache: object | None = None
    current_session_tail_state: object | None = None
    session_snapshot_cache: object | None = None
    renderer_mode: bool = True
    defer_cold_renderer_budget: bool = True
    background_usage_runtime: object | None = None
    usage_insights_payload: dict[str, object] = field(default_factory=dict)
    usage_insights_worker: object | None = None
    session_cleanup_manager: object | None = None
    session_cleanup_worker: object | None = None
    session_index_warm_job: object | None = None
    session_index_payload: dict[str, object] = field(default_factory=dict)
    session_cleanup_payload: dict[str, object] = field(default_factory=dict)
    session_cleanup_delta: dict[str, object] = field(default_factory=dict)
    session_management_current_session_id: str = ""
    session_management_active_session_ids: set[str] = field(default_factory=set)
    rest_reminder: object | None = None
    session_lock_monitor: object | None = None
    config_overrides: dict[str, object] = field(default_factory=dict)
    work_overlay_started_at: datetime = field(
        default_factory=lambda: datetime.now().astimezone(),
        init=False,
        repr=False,
    )
    config_reload: Callable[..., object] | None = field(
        default=None,
        repr=False,
    )
    _closed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.runtime_errors.event_bus is None:
            self.runtime_errors.event_bus = self.runtime_events

    def reload_user_config(self, *, include_history: bool = True) -> object | None:
        """Delegate reload mechanics to the injected configuration owner."""
        if self.config_reload is None:
            return None
        return self.config_reload(self, include_history=include_history)

    def close(self) -> None:
        """Release owned helpers in reverse dependency order, once."""
        if self._closed:
            return
        self._closed = True
        tracker = self.active_session_tracker
        resolver = self.session_resolver
        if resolver is not None and hasattr(resolver, "active_session_tracker"):
            try:
                resolver.active_session_tracker = None
            except Exception:
                pass
        if tracker is not None:
            try:
                tracker.close()
            except Exception:
                _LOGGER.exception(
                    "runtime_context_close_failed resource=active_session_tracker"
                )
            finally:
                self.active_session_tracker = None
        for field_name in (
            "session_cleanup_worker",
            "session_index_warm_job",
            "usage_insights_worker",
            "background_usage_runtime",
            "session_snapshot_cache",
            "pre_send_estimator",
            "rest_reminder",
        ):
            resource = getattr(self, field_name, None)
            if resource is None:
                continue
            try:
                resource.close()
            except Exception:
                _LOGGER.exception(
                    "runtime_context_close_failed resource=%s", field_name
                )
            finally:
                setattr(self, field_name, None)


def _build_usage_summary_cache(parser: JsonlSessionParser) -> UsageSummaryCache:
    return UsageSummaryCache(
        parser,
        deleted_usage_ledger=DeletedUsageLedger(
            hud_runtime_dir() / DELETED_SESSION_USAGE_FILENAME
        ),
        summary_store=UsageSummaryStore(
            hud_runtime_dir() / USAGE_SUMMARY_DATABASE_FILENAME
        ),
    )


def _initialize_runtime_context_resources(context: RuntimeContext) -> None:
    """Attach optional runtime helpers after the context itself is fully built."""
    if context.rest_reminder is None:
        settings_path = getattr(context.settings_store, "path", None)
        context.rest_reminder = RestReminderPresenter(state_path=settings_path)
        context.rest_reminder.configure(
            context.user_config,
            force_reset=True,
            restore_persisted=True,
        )
    ensure_runtime_error_diagnostics(context)
    if context.session_snapshot_cache is None:
        context.session_snapshot_cache = SessionSnapshotCache(
            context.parser,
            event_bus=context.runtime_events,
            sse_tracker=context.sse_tracker,
        )
    if not context.renderer_mode:
        return
    context.usage_insights_payload = _refresh_usage_insights_payload(context)
    if context.usage_insights_worker is None:
        context.usage_insights_worker = UsageInsightsWorker(
            context,
            refresh=_run_usage_insights_refresh,
        )
    if context.session_cleanup_manager is None:
        context.session_cleanup_manager = _build_session_cleanup_manager(context)
    context.session_cleanup_payload = context.session_cleanup_manager.snapshot()
    if context.session_cleanup_worker is None:
        context.session_cleanup_worker = SessionCleanupWorker(
            context,
            context.session_cleanup_manager,
            on_deleted=_refresh_usage_after_session_delete,
        )
    if _session_index_warm_job_available(context.session_cleanup_manager):
        warm_state_path = hud_runtime_dir() / "session-index-warm.json"
        fresh_install = not warm_state_path.exists()
        context.session_index_warm_job = _build_session_index_warm_job(context)
        # New installs default to the search index OFF (session-management UX):
        # the feature switch starts closed and no background build runs until
        # the user enables it. Existing installs keep whatever they chose
        # because their persisted state file already exists, so we never
        # overwrite their preference.
        if fresh_install:
            context.session_index_warm_job.set_enabled(False)
        context.session_index_payload = dict(
            context.session_index_warm_job.status()
        )
        warm_job = context.session_index_warm_job
        # The warm job needs the manager's candidate inventory.  Build that
        # inventory in the background at startup so first-use indexing does
        # not wait for the user to open session management and click Scan.
        import threading

        def _prime_session_index() -> None:
            try:
                snapshot = context.session_cleanup_manager.scan()
                publisher = getattr(context.session_cleanup_worker, "_publish", None)
                if callable(publisher):
                    publisher(snapshot)
                else:
                    context.session_cleanup_payload = snapshot
                watcher_start = getattr(context.session_cleanup_worker, "_start_search_watcher", None)
                if callable(watcher_start):
                    watcher_start()
                warm_job.start(_session_index_startup_range(context))
            except Exception as exc:
                _LOGGER.exception("session_index_warm_start_failed error=%s", exc)

        threading.Thread(
            target=_prime_session_index,
            name="codex-usage-hud-session-index-prime",
            daemon=True,
        ).start()


def _session_index_warm_job_available(manager: object) -> bool:
    return bool(
        manager is not None
        and hasattr(manager, "search_index_entries")
        and hasattr(manager, "_search_index")
    )


def _session_index_default_range(context: RuntimeContext) -> str:
    config = getattr(context, "user_config", None)
    raw = getattr(config, "session_search_range", "") if config is not None else ""
    candidate = str(raw or "").strip().casefold()
    return candidate if candidate in {"1m", "3m", "6m", "1y", "all"} else "1m"


def _session_index_startup_range(context: RuntimeContext) -> str:
    """Resolve the startup warm range: persisted boundary vs configured default.

    The warm job has been constructed but not started, so its status reflects
    the persisted state file. The wider of the two wins: a user extension must
    survive restarts (PRD §5.1 remembers it as the new coverage boundary), and
    a wider configured default still applies on top. Without this, restarting
    the HUD re-ran the configured default, overwrote the persisted extension
    and trimmed the already-built index back down to one month.
    """
    configured = _session_index_default_range(context)
    status_fn = getattr(
        getattr(context, "session_index_warm_job", None), "status", None
    )
    persisted = ""
    if callable(status_fn):
        try:
            persisted = str(status_fn().get("selectedRange") or "")
        except Exception:
            persisted = ""
    return wider_range_key(configured, persisted)


def _build_session_index_warm_job(context: RuntimeContext) -> object:
    from .core.session_index_job import SessionIndexWarmJob

    manager = context.session_cleanup_manager
    state_path = (
        hud_runtime_dir() / "session-index-warm.json"
    )
    job: SessionIndexWarmJob

    def publish_progress(snapshot: object) -> None:
        payload = _session_index_snapshot_payload(snapshot)
        context.session_index_payload = payload
        # Coverage can be complete before the delayed resident-memory load.
        # Refresh the submitted query on progress (including that final load),
        # even while detached, so reopening cannot inherit a stale empty result.
        worker = getattr(context, "session_cleanup_worker", None)
        refresh = getattr(worker, "refresh_warm_search", None)
        if callable(refresh) and payload["enabled"]:
            refresh()
        current_job = getattr(context, "session_index_warm_job", None)
        attached = bool(
            callable(getattr(current_job, "is_attached", None))
            and current_job.is_attached()
        )
        if not attached:
            return
        event_bus = getattr(context, "runtime_events", None)
        publish = getattr(event_bus, "publish", None)
        if callable(publish):
            publish(
                "session_index_progress",
                source="session_index_warm_job",
                context=payload,
            )

    def prepare_search_index_clear() -> bool:
        worker = getattr(context, "session_cleanup_worker", None)
        prepare = getattr(worker, "prepare_search_index_clear", None)
        return bool(prepare()) if callable(prepare) else True

    def finish_search_index_clear() -> None:
        worker = getattr(context, "session_cleanup_worker", None)
        finish = getattr(worker, "finish_search_index_clear", None)
        if callable(finish):
            finish()

    job = SessionIndexWarmJob(
        manager,
        state_path=state_path,
        progress_callback=publish_progress,
        preload_grace_seconds=_SESSION_INDEX_PRELOAD_GRACE_SECONDS,
        before_clear=prepare_search_index_clear,
        after_clear=finish_search_index_clear,
    )
    return job


def _session_index_snapshot_payload(snapshot: object) -> dict[str, object]:
    """Convert a warm-job snapshot to the renderer-safe progress shape."""
    return {
        "coverage": str(getattr(snapshot, "coverage", "empty") or "empty"),
        "coverageBoundary": float(
            getattr(snapshot, "coverage_boundary", 0.0) or 0.0
        ),
        "jobState": str(getattr(snapshot, "job_state", "idle") or "idle"),
        "builtCount": max(0, int(getattr(snapshot, "built_count", 0) or 0)),
        "totalCount": max(0, int(getattr(snapshot, "total_count", 0) or 0)),
        "phase": str(getattr(snapshot, "phase", "") or ""),
        "startedAt": float(getattr(snapshot, "started_at", 0.0) or 0.0) * 1000.0,
        "estimatedRemainingSec": float(
            getattr(snapshot, "estimated_remaining_sec", 0.0) or 0.0
        ),
        "selectedRange": str(
            getattr(snapshot, "selected_range", DEFAULT_RANGE) or DEFAULT_RANGE
        ),
        "canExtend": bool(getattr(snapshot, "can_extend", False)),
        "error": str(getattr(snapshot, "error", "") or ""),
        "enabled": bool(getattr(snapshot, "enabled", True)),
        "diskBytes": max(0, int(getattr(snapshot, "disk_bytes", 0) or 0)),
    }


def _suspend_native_active_title(context: "RuntimeContext") -> None:
    try:
        context.platform.suspend_native_active_title(True)
    except Exception:
        return


def _stop_active_session_tracker(context: "RuntimeContext") -> None:
    tracker = getattr(context, "active_session_tracker", None)
    resolver = getattr(context, "session_resolver", None)
    if resolver is not None and hasattr(resolver, "active_session_tracker"):
        try:
            resolver.active_session_tracker = None
        except Exception:
            pass
    if tracker is None:
        return
    try:
        tracker.close()
    finally:
        context.active_session_tracker = None


def _cost_estimator_from_config(config: UserConfig) -> CostEstimator:
    return CostEstimator(
        UsageCalculator(
            config.price_table(),
            pricing_versions=getattr(config, "pricing_versions", ()),
        )
    )


def _configure_ui_cost_estimators(estimator: CostEstimator) -> None:
    try:
        from .ui import renderer_domains

        renderer_domains.set_cost_estimator(estimator)
    except Exception:
        return


def _runtime_config_ports() -> runtime_config.ConfigApplyPorts:
    return runtime_config.ConfigApplyPorts(
        discover_providers=discover_provider_registry,
        cost_estimator_from_config=_cost_estimator_from_config,
        usage_cache_factory=_build_usage_summary_cache,
        configure_ui_cost_estimators=_configure_ui_cost_estimators,
    )


def _apply_cli_config_overrides(
    config: UserConfig,
    args: argparse.Namespace,
) -> UserConfig:
    return runtime_config.apply_cli_overrides(config, args)


def build_runtime_context(args: argparse.Namespace) -> RuntimeContext:
    platform = get_current_platform()
    settings_store = UserConfigStore()
    user_config = _apply_cli_config_overrides(settings_store.load(), args)
    estimator = _cost_estimator_from_config(user_config)
    _configure_ui_cost_estimators(estimator)
    parser = JsonlSessionParser(estimate_enabled=True, cost_estimator=estimator)
    sessions_root = runtime_config.discover_sessions_root(platform, args.sessions_root)
    provider_registry = discover_provider_registry(
        user_config=user_config,
        sessions_root=sessions_root,
    )
    user_config = user_config.migrate_legacy_provider_settings(
        provider_registry.providers(), app_provider=provider_registry.app_provider
    )
    provider_registry = discover_provider_registry(
        user_config=user_config,
        sessions_root=sessions_root,
    )
    sqlite_log_path = runtime_config.discover_path(
        platform, args.sse_db, DEFAULT_SQLITE_LOG
    )
    state_db_path = runtime_config.discover_path(
        platform, args.state_db, DEFAULT_STATE_DB
    )
    session_index_path = runtime_config.discover_path(
        platform, None, DEFAULT_SESSION_INDEX
    )
    runtime_display_mode = normalize_display_mode(
        getattr(args, "runtime_hud_mode", None)
        or getattr(args, "hud_mode", None)
        or user_config.display_mode
    )
    renderer_active_session_bridge = runtime_display_mode == "renderer"
    if renderer_active_session_bridge:
        try:
            platform.suspend_native_active_title(True)
        except Exception:
            pass
    active_session_tracker = ActiveSessionTracker(
        platform=platform,
        state_db=state_db_path,
        sessions_root=sessions_root,
        session_index_path=session_index_path,
        poll_ms=args.active_session_poll_ms,
        enabled=(
            not args.no_follow_active_session
            and not args.session_id
            and not args.session_file
        ),
        start_background_watcher=not renderer_active_session_bridge,
    )
    active_session_tracker.start()
    session_resolver = SessionPathResolver(
        platform=platform,
        sessions_root=sessions_root,
        session_id=args.session_id,
        session_file=Path(args.session_file).expanduser()
        if args.session_file
        else None,
        active_session_tracker=active_session_tracker,
        auto_switch_idle_seconds=args.auto_switch_idle_seconds,
    )
    sse_tracker = (
        None
        if args.no_sse
        else SseRequestStateMachine(db_path=sqlite_log_path, cost_estimator=estimator)
    )
    pre_send_estimator = None
    if DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED:
        pre_send_estimator = PreSendEstimator(
            project_roots=[str(sessions_root.parent)],
        )
        pre_send_estimator.start()
    context = RuntimeContext(
        platform=platform,
        sessions_root=sessions_root,
        session_file=Path(args.session_file).expanduser()
        if args.session_file
        else None,
        sqlite_log_path=sqlite_log_path,
        state_db_path=state_db_path,
        session_index_path=session_index_path,
        poll_ms=max(100, int(args.poll_ms)),
        daily_budget_usd=max(0.0, float(user_config.daily_budget_usd)),
        weekly_budget_usd=max(0.0, float(user_config.weekly_budget_usd)),
        budget_thresholds=list(user_config.budget_thresholds),
        user_config=user_config,
        settings_store=settings_store,
        settings_mtime=settings_store.mtime(),
        parser=parser,
        sse_tracker=sse_tracker,
        active_session_tracker=active_session_tracker,
        session_resolver=session_resolver,
        usage_cache=_build_usage_summary_cache(parser),
        app_provider=provider_registry.app_provider,
        provider_registry=provider_registry,
        pre_send_estimator=pre_send_estimator,
        runtime_errors=RuntimeErrorRegistry(),
        visible_app_error_cache=VisibleAppErrorCache(),
        renderer_mode=renderer_active_session_bridge,
        defer_cold_renderer_budget=not bool(getattr(args, "once", False)),
        config_overrides=runtime_config.cli_overrides(args),
        config_reload=lambda runtime, **kwargs: runtime_config.reload_if_changed(
            runtime, _runtime_config_ports(), **kwargs
        ),
    )
    try:
        _initialize_runtime_context_resources(context)
        if renderer_active_session_bridge and sqlite_log_path.is_file():
            try:
                context.background_usage_runtime = BackgroundUsageRuntime(
                    logs_path=sqlite_log_path,
                    state_path=state_db_path,
                    database_path=(
                        hud_runtime_dir() / BACKGROUND_USAGE_DATABASE_FILENAME
                    ),
                    provider=provider_registry.app_provider,
                    price_table=user_config.price_table(),
                    pricing_versions=getattr(user_config, "pricing_versions", ()),
                    event_bus=context.runtime_events,
                    runtime_errors=context.runtime_errors,
                ).start()
            except Exception as exc:
                context.runtime_errors.record(
                    source="background_usage",
                    code="startup_failed",
                    message="Background usage audit could not start; the renderer HUD remains available.",
                    severity="warning",
                    context={"errorType": type(exc).__name__},
                )
    except Exception:
        context.close()
        raise
    return context


__all__ = ["RuntimeContext"]
