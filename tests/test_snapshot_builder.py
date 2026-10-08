from pathlib import Path
from types import SimpleNamespace

from codex_usage_hud.core import ParsedSession, UsageSummary
from codex_usage_hud.config import UserConfig
from codex_usage_hud.snapshot_builder import (
    RuntimeSnapshotBuilder,
    SnapshotBuilderPorts,
    build_snapshot,
)


def test_snapshot_keeps_selection_sequence_captured_before_parse() -> None:
    tracker = SimpleNamespace(
        selection_seq=10,
        selection_observed_at_ms=100,
        renderer_session_id="renderer-old",
        follow_state="following",
        follow_reason="",
        selection_received_at_ms=101,
        selection_resolved_at_ms=102,
        follow_stuck_since_ms=0,
        follow_stuck_elapsed_ms=0,
        title_for_session=lambda path, session_id: "old title",
    )

    def snapshot_for(path: Path, *, session_id: str) -> ParsedSession:
        del path, session_id
        tracker.selection_seq = 11
        tracker.selection_observed_at_ms = 200
        return ParsedSession(session_id="old-session", status="parsed")

    context = SimpleNamespace(
        reload_user_config=lambda: None,
        active_session_tracker=tracker,
        session_resolver=SimpleNamespace(
            resolve=lambda: (Path("old.jsonl"), "renderer:old"),
            session_id="old-session",
        ),
        session_snapshot_cache=SimpleNamespace(snapshot_for=snapshot_for),
        visible_app_error_cache=SimpleNamespace(resolve=lambda snapshot, error: ""),
        platform=SimpleNamespace(get_active_app_error=lambda: ""),
        user_config=UserConfig.defaults(),
        usage_cache=SimpleNamespace(
            summarize=lambda *args, **kwargs: (UsageSummary(), UsageSummary())
        ),
        sessions_root=Path("."),
        daily_budget_usd=0.0,
        weekly_budget_usd=0.0,
        budget_thresholds=[],
        pre_send_estimator=None,
        parser=SimpleNamespace(),
        session_management_current_session_id="",
        session_management_active_session_ids=set(),
    )
    ports = SnapshotBuilderPorts(
        record_active_session_error=lambda *args: None,
        provider_scope=lambda *args: None,
        refresh_usage_insights=lambda *args: None,
        active_work_items=lambda *args: [],
        apply_family_usage=lambda *args: None,
    )

    snapshot = build_snapshot(context, ports)

    assert tracker.selection_seq == 11
    assert snapshot.selection_seq == 10
    assert snapshot.selection_observed_at_ms == 100


def test_renderer_snapshot_defers_cold_budget_aggregation() -> None:
    tracker = SimpleNamespace(
        selection_seq=1,
        selection_observed_at_ms=1,
        title_for_session=lambda path, session_id: "current title",
    )
    summarize_calls: list[object] = []
    active_work_calls: list[object] = []

    class ColdCache:
        def is_warm_for(self, *_args: object) -> bool:
            return False

        def summarize(self, *_args: object, **_kwargs: object) -> tuple[UsageSummary, UsageSummary]:
            summarize_calls.append(True)
            return UsageSummary(), UsageSummary()

    context = SimpleNamespace(
        reload_user_config=lambda: None,
        renderer_mode=True,
        active_session_tracker=tracker,
        session_resolver=SimpleNamespace(
            resolve=lambda: (Path("current.jsonl"), "renderer:current"),
            session_id="current",
        ),
        session_snapshot_cache=SimpleNamespace(
            snapshot_for=lambda path, session_id: ParsedSession(
                session_id="current", status="parsed"
            )
        ),
        visible_app_error_cache=SimpleNamespace(resolve=lambda snapshot, error: ""),
        platform=SimpleNamespace(get_active_app_error=lambda: ""),
        user_config=UserConfig.defaults(),
        usage_cache=ColdCache(),
        sessions_root=Path("."),
        daily_budget_usd=1.0,
        weekly_budget_usd=2.0,
        budget_thresholds=[],
        pre_send_estimator=None,
        parser=SimpleNamespace(),
        session_management_current_session_id="",
        session_management_active_session_ids=set(),
    )
    ports = SnapshotBuilderPorts(
        record_active_session_error=lambda *args: None,
        provider_scope=lambda *args: None,
        refresh_usage_insights=lambda *args: None,
        active_work_items=lambda *args: active_work_calls.append(True) or [],
        apply_family_usage=lambda *args: None,
    )

    snapshot = build_snapshot(context, ports)

    assert snapshot.session_id == "current"
    assert snapshot.daily_limit_usd == 1.0
    assert snapshot.weekly_limit_usd == 2.0
    assert snapshot.budget_ready is False
    assert not summarize_calls
    assert active_work_calls == [True]


def _reuse_context() -> tuple[SimpleNamespace, SnapshotBuilderPorts]:
    tracker = SimpleNamespace(
        selection_seq=1,
        selection_observed_at_ms=1,
        title_for_session=lambda path, session_id: "current title",
    )
    context = SimpleNamespace(
        reload_user_config=lambda: None,
        renderer_mode=True,
        active_session_tracker=tracker,
        session_resolver=SimpleNamespace(
            resolve=lambda: (Path("current.jsonl"), "renderer:current"),
            session_id="current",
        ),
        session_snapshot_cache=SimpleNamespace(
            snapshot_for=lambda path, session_id: ParsedSession(
                session_id="current", status="parsed"
            )
        ),
        visible_app_error_cache=SimpleNamespace(resolve=lambda snapshot, error: ""),
        platform=SimpleNamespace(get_active_app_error=lambda: ""),
        user_config=UserConfig.defaults(),
        usage_cache=SimpleNamespace(),
        sessions_root=Path("."),
        daily_budget_usd=1.0,
        weekly_budget_usd=2.0,
        budget_thresholds=[],
        pre_send_estimator=None,
        parser=SimpleNamespace(),
        session_management_current_session_id="",
        session_management_active_session_ids=set(),
    )
    ports = SnapshotBuilderPorts(
        record_active_session_error=lambda *args: None,
        provider_scope=lambda *args: None,
        refresh_usage_insights=lambda *args: None,
        active_work_items=lambda *args: [],
        apply_family_usage=lambda *args: None,
    )
    return context, ports


def test_reused_deferred_budget_stays_unmeasured() -> None:
    """Reusing a cold-start placeholder must not republish zeros as real usage."""
    context, ports = _reuse_context()
    source = ParsedSession(session_id="current", status="parsed")
    source.budget_ready = False
    source.today_tokens = 0
    source.today_cost_usd = 0.0

    snapshot = build_snapshot(context, ports, reuse_budget_from=source)

    assert snapshot.budget_ready is False


def test_reused_ready_budget_stays_measured() -> None:
    context, ports = _reuse_context()
    source = ParsedSession(session_id="current", status="parsed")
    source.budget_ready = True
    source.today_tokens = 1234
    source.today_cost_usd = 1.25

    snapshot = build_snapshot(context, ports, reuse_budget_from=source)

    assert snapshot.budget_ready is True
    assert snapshot.today_tokens == 1234
    assert snapshot.today_cost_usd == 1.25


def test_once_snapshot_does_not_defer_cold_budget_aggregation() -> None:
    tracker = SimpleNamespace(
        selection_seq=1,
        selection_observed_at_ms=1,
        title_for_session=lambda path, session_id: "current title",
    )
    summarize_calls: list[object] = []

    class ColdCache:
        def is_warm_for(self, *_args: object) -> bool:
            return False

        def summarize(
            self, *_args: object, **_kwargs: object
        ) -> tuple[UsageSummary, UsageSummary]:
            summarize_calls.append(True)
            summary = UsageSummary(tokens=10, cost_usd=1.0)
            return summary, summary

    context = SimpleNamespace(
        reload_user_config=lambda: None,
        renderer_mode=True,
        defer_cold_renderer_budget=False,
        active_session_tracker=tracker,
        session_resolver=SimpleNamespace(
            resolve=lambda: (Path("current.jsonl"), "renderer:current"),
            session_id="current",
        ),
        session_snapshot_cache=SimpleNamespace(
            snapshot_for=lambda path, session_id: ParsedSession(
                session_id="current", status="parsed"
            )
        ),
        visible_app_error_cache=SimpleNamespace(resolve=lambda snapshot, error: ""),
        platform=SimpleNamespace(get_active_app_error=lambda: ""),
        user_config=UserConfig.defaults(),
        usage_cache=ColdCache(),
        sessions_root=Path("."),
        daily_budget_usd=1.0,
        weekly_budget_usd=2.0,
        budget_thresholds=[],
        pre_send_estimator=None,
        parser=SimpleNamespace(),
        session_management_current_session_id="",
        session_management_active_session_ids=set(),
    )
    ports = SnapshotBuilderPorts(
        record_active_session_error=lambda *args: None,
        provider_scope=lambda *args: None,
        refresh_usage_insights=lambda *args: None,
        active_work_items=lambda *args: [],
        apply_family_usage=lambda *args: None,
    )

    snapshot = build_snapshot(context, ports)

    assert summarize_calls == [True]
    assert snapshot.today_tokens == 10
    assert snapshot.week_tokens == 10


def test_runtime_snapshot_builder_preserves_refresh_options() -> None:
    calls: list[tuple[object, dict[str, object]]] = []

    def builder(context: object, **kwargs: object) -> ParsedSession:
        calls.append((context, dict(kwargs)))
        return ParsedSession(status="ok")

    context = object()
    runtime_builder = RuntimeSnapshotBuilder(context=context, builder=builder)
    reused = ParsedSession(status="old")

    runtime_builder(
        refresh_budget_aggregate=False,
        refresh_budget_paths=(Path("changed.jsonl"),),
        refresh_active_work_items=False,
        refresh_current_session_usage=False,
        reuse_budget_from=reused,
        refresh_visible_app_error=False,
    )

    assert calls == [
        (
            context,
            {
                "refresh_budget_aggregate": False,
                "refresh_budget_paths": (Path("changed.jsonl"),),
                "reuse_budget_from": reused,
                "refresh_visible_app_error": False,
                "refresh_current_session_usage": False,
                "refresh_active_work_items": False,
            },
        )
    ]


def test_runtime_snapshot_builder_returns_error_snapshot_on_failure() -> None:
    def fail(_context: object) -> ParsedSession:
        raise RuntimeError("snapshot failed")

    snapshot = RuntimeSnapshotBuilder(context=object(), builder=fail)()

    assert snapshot.status == "error"
    assert snapshot.error == "snapshot failed"


def test_cold_deferred_budget_publishes_recent_window_totals() -> None:
    """A cold-start frame must show real day/week numbers when they are knowable.

    The full historical scan is still pending, but the day/week windows only
    depend on recent files. Publishing the recent-window totals keeps the top
    bar off "今日 计算中…" instead of parking it for the whole re-parse.
    """
    tracker = SimpleNamespace(
        selection_seq=1,
        selection_observed_at_ms=1,
        title_for_session=lambda path, session_id: "current title",
    )
    summarize_calls: list[object] = []
    insights_calls: list[object] = []
    recent_day = UsageSummary(tokens=4_242)
    recent_week = UsageSummary(tokens=9_999)

    class ColdCache:
        def is_warm_for(self, *_args: object) -> bool:
            return False

        def summarize(self, *_args: object, **_kwargs: object) -> tuple[UsageSummary, UsageSummary]:
            summarize_calls.append(True)
            return UsageSummary(), UsageSummary()

        def recent_window_totals(self, *_args: object, **_kwargs: object):
            return recent_day, recent_week

    context = SimpleNamespace(
        reload_user_config=lambda: None,
        renderer_mode=True,
        active_session_tracker=tracker,
        session_resolver=SimpleNamespace(
            resolve=lambda: (Path("current.jsonl"), "renderer:current"),
            session_id="current",
        ),
        session_snapshot_cache=SimpleNamespace(
            snapshot_for=lambda path, session_id: ParsedSession(
                session_id="current", status="parsed"
            )
        ),
        visible_app_error_cache=SimpleNamespace(resolve=lambda snapshot, error: ""),
        platform=SimpleNamespace(get_active_app_error=lambda: ""),
        user_config=UserConfig.defaults(),
        usage_cache=ColdCache(),
        sessions_root=Path("."),
        daily_budget_usd=1.0,
        weekly_budget_usd=2.0,
        budget_thresholds=[],
        pre_send_estimator=None,
        parser=SimpleNamespace(),
        session_management_current_session_id="",
        session_management_active_session_ids=set(),
    )
    ports = SnapshotBuilderPorts(
        record_active_session_error=lambda *args: None,
        provider_scope=lambda *args: None,
        refresh_usage_insights=lambda *args: insights_calls.append(True),
        active_work_items=lambda *args: [],
        apply_family_usage=lambda *args: None,
    )

    snapshot = build_snapshot(context, ports)

    # Real window numbers, not placeholders -> the HUD must not say 计算中.
    assert snapshot.budget_ready is True
    assert snapshot.today_tokens == 4_242
    assert snapshot.week_tokens == 9_999
    # The full scan and the insights rebuild still have to happen later.
    assert not summarize_calls
    assert not insights_calls
