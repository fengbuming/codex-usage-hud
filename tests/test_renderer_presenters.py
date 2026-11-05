from __future__ import annotations

from datetime import datetime, timezone
from functools import partial
from unittest.mock import patch

from codex_usage_hud.core.parser import (
    ConfirmedTokens,
    CostEstimator,
    ParsedSession,
    RequestRound,
    RequestTokens,
    ToolCallTiming,
)
from codex_usage_hud.renderer_presenters import activity
from codex_usage_hud.renderer_presenters import budget, common
from codex_usage_hud.renderer_presenters import request
from codex_usage_hud.renderer_presenters import session
from codex_usage_hud.ui import renderer_domains


def test_common_formatters_preserve_renderer_boundaries() -> None:
    assert common.short_num(-12_000) == "-12k"
    assert common.short_num(1_234) == "1,234"
    assert common.short_num(2_500_000) == "2.5M"
    assert common.format_money(None) == "$0.00"
    assert common.format_money(0.01) == "$0.01"
    assert common.format_money(0.009) == "$0.01"
    assert common.format_money(0.1234) == "$0.12"
    assert common.format_money(1.0) == "$1.00"
    assert common.format_realtime_money(0.5, True) == "~$0.50"
    assert common.format_fixed_money(0.009, True) == "~$0.01"
    assert common.format_fixed_money(125.0, True) == "~$125.0"
    assert common.format_usage_money(12_345, 1.25) == "12k/$1.25"
    assert common.fixed_token_total(295_000) == "295k"


def test_common_rate_and_time_formatters_are_pure() -> None:
    value = datetime(2026, 7, 31, 12, 34, 56, tzinfo=timezone.utc)

    assert common.format_rate_value(None, False) == "-"
    assert common.format_rate_value(1.5, True) == "~100%"
    assert common.format_rate_marker(0.5, False) == "◎50%"
    assert common.format_time(None) == "n/a"
    assert common.format_start(None) == "n/a"
    assert common.format_time(value) == value.astimezone().strftime("%m-%d %H:%M:%S")
    assert common.format_start(value) == value.astimezone().strftime("%m-%d %H:%M")
    assert common.timeline_time(None) == "--:--"
    assert common.timeline_time(value) == value.astimezone().strftime("%H:%M:%S")
    assert common.duration_text(None) == "--"
    assert common.duration_text(59.9) == "59.9s"
    assert common.duration_text(61.0) == "1m1s"
    assert common.duration_text(3_661.0) == "1h1m"


def test_budget_progress_calculates_capped_fill_and_overflow() -> None:
    assert budget.progress_total_ratio(112.0, 100.0) == 1.12
    assert budget.progress_ratio(112.0, 100.0) == 1.0
    assert budget.progress_total_text(112.0, 100.0) == "112%"
    assert abs(budget.progress_overflow_ratio(112.0, 100.0) - 0.12) < 1e-9
    assert budget.progress_overflow_parts(112.0, 100.0) == (
        "+12% / +$12.00",
        "+$12.00",
    )
    assert budget.progress_overflow_parts(100.0, 100.0) == ("", "")
    assert budget.progress_total_ratio(1.0, 0.0) == 0.0
    assert budget.limit_text(100.0) == "总 $100.00"


def test_budget_metric_keeps_optional_overflow_fields_sparse() -> None:
    assert budget.progress_metric("today", 1.2, "day") == {
        "label": "today",
        "ratio": 1.0,
        "tone": "day",
    }
    assert budget.progress_metric(
        "today",
        1.2,
        "day",
        right_text="总 $100.00",
        overflow_ratio=0.2,
        overflow_badge="+20% / +$20.00",
        overflow_badge_compact="+$20.00",
        overflow_badge_icon="!",
    ) == {
        "label": "today",
        "ratio": 1.0,
        "tone": "day",
        "rightText": "总 $100.00",
        "overflowRatio": 0.2,
        "overflowBadge": "+20% / +$20.00",
        "overflowBadgeCompact": "+$20.00",
        "overflowBadgeIcon": "!",
    }


def test_renderer_facade_wrappers_keep_old_patch_points_dynamic() -> None:
    with patch.object(renderer_domains, "_format_money", return_value="$patched"):
        assert renderer_domains._format_usage_money(1_234, 2.0) == "1,234/$patched"
        assert renderer_domains._budget_progress_overflow_parts(112.0, 100.0) == (
            "+12% / +$patched",
            "+$patched",
        )
        assert renderer_domains._budget_limit_text(100.0) == "总 $patched"


def test_session_presenter_keeps_selection_and_status_copy() -> None:
    snapshot = ParsedSession(
        session_id="session-1234567890abcdef",
        session_title="Renderer session title",
        selection_observed_at_ms=1_000,
        follow_reason="awaiting-exact-mapping",
        confirmed=ConfirmedTokens(cumulative_input=100, cumulative_cached=40),
        request=RequestTokens(status="running", input_tokens=50, total_tokens=75),
    )
    def is_new(_value: ParsedSession) -> bool:
        return False

    def is_pending(_value: ParsedSession) -> bool:
        return False

    def compact(value: object, limit: int) -> str:
        return str(value)[:limit]

    assert session.session_label(
        snapshot,
        is_new_session=is_new,
        is_pending_session=is_pending,
        compact=compact,
    ) == "Renderer session title"
    assert session.follow_elapsed_ms(snapshot, 1_250) == 250
    assert session.follow_feedback(snapshot) == "会话切换中：正式 ID 已收到，等待本地映射"
    assert session.status_label("parsed") == "实时"
    assert session.request_status_label("running") == "运行中"
    assert session.activity_label("tool call") == "调用工具"
    assert session.gap_label("model_startup") == "模型启动"
    assert session.expanded_header_title(
        snapshot,
        is_new_session=is_new,
        is_pending_session=is_pending,
        compact=compact,
        fallback="fallback",
    ) == "Renderer session title"


def test_session_presenter_preserves_running_cache_projection() -> None:
    snapshot = ParsedSession(
        confirmed=ConfirmedTokens(cumulative_input=100, cumulative_cached=40),
        request=RequestTokens(
            status="running",
            input_tokens=50,
            cached_tokens=20,
            output_tokens=25,
            total_tokens=75,
            estimated=False,
        ),
    )

    assert session.display_tokens(snapshot) == (50, False, 25, False, None, False, 75, False)
    assert session.display_cached_tokens(snapshot, 50, False) == (20, False)
    ratio, estimated = session.session_cache_hit_rate(snapshot)
    assert abs(ratio - 0.4) < 1e-9
    assert estimated is False
    assert session.session_cache_hit_rate_label(
        snapshot,
        cache_hit_rate=session.session_cache_hit_rate,
        rate_marker=common.format_rate_marker,
    ) == "◎40%"
    assert session.top_session_cache_hit_rate_label(
        snapshot,
        cache_hit_rate_label=lambda value: "◎40%",
    ) == "40%"
    assert session.top_cache_progress_label(
        snapshot,
        cache_hit_rate_label=lambda value: "◎40%",
    ) == "缓存命中 40%"


def test_request_presenter_preserves_round_order_and_dynamic_widths() -> None:
    item = RequestRound(
        index=33,
        status="confirmed",
        model="gpt-5.4",
        input_tokens=194_000,
        cached_tokens=93_000,
        output_tokens=852,
        reasoning_tokens=516,
        total_tokens=295_000,
        estimated=False,
        cost_usd=0.094,
        started_at=datetime(2026, 5, 28, 20, 36, 26).astimezone(),
    )
    estimator = CostEstimator()
    entry_parts = partial(request.round_entry_parts, cost_estimator=estimator)

    entry = request.round_entry(
        item,
        "gpt-5.4",
        entry_parts=entry_parts,
    )
    widths = request.round_entry_widths(
        [item],
        "gpt-5.4",
        cost_estimator=estimator,
    )

    assert entry.startswith("#33 $0.09 20:36:26 ↑101k")
    assert "◎48%" in entry
    assert entry.endswith("↻93k ∑295k")
    assert widths.index == 2
    assert request.round_is_running(item) is False
    assert request.round_time_iso(item.started_at) == item.started_at.isoformat()


def test_activity_presenter_extracts_tool_details_and_merges_events() -> None:
    moment = datetime(2026, 7, 31, 12, 34, 56, tzinfo=timezone.utc)
    call = ToolCallTiming(
        call_id="call-1",
        name="shell_command",
        args='{"command":"git status"}',
        start=moment,
        start_line=1,
    )
    def compact(value: object, limit: int) -> str:
        return str(value)[:limit]

    assert activity.tool_call_arguments_summary(call, compact=compact) == "git status"
    assert activity.tool_call_timeline_detail(
        call,
        "1.0s",
        arguments_summary=lambda value: "git status",
    ) == "1.0s shell_command · git status"
    assert activity.is_token_confirm_event("Token确认", "") is True
    assert activity.is_token_confirm_event("事件", "received token_count") is True

    merged = activity.merge_activity_events(
        [
            (
                moment,
                1,
                {
                    "title": "请求完成",
                    "detail": "model",
                    "taskIndex": 2,
                    "roundIndex": 7,
                },
            ),
            (
                moment,
                2,
                {
                    "title": "任务完成",
                    "detail": "done",
                    "taskIndex": 2,
                    "roundIndexes": [7, 8],
                },
            ),
            (
                moment,
                3,
                {
                    "title": "Token确认",
                    "detail": "received token_count",
                    "taskIndex": 2,
                    "roundIndex": 8,
                },
            ),
        ],
        compact=compact,
    )
    assert len(merged) == 1
    assert merged[0]["title"] == "任务完成，Token确认"
    assert "请求完成" not in str(merged[0]["title"])
    assert merged[0]["taskIndex"] == 2
    assert merged[0]["roundIndex"] == 8
    assert merged[0]["roundIndexes"] == [7, 8]


def test_activity_round_detail_keeps_cost_and_token_order() -> None:
    item = RequestRound(
        index=1,
        status="confirmed",
        model="gpt-5.4",
        input_tokens=1_000,
        cached_tokens=200,
        output_tokens=80,
        reasoning_tokens=20,
        total_tokens=1_100,
        estimated=False,
        cost_usd=0.25,
    )

    detail = activity.activity_round_detail(
        item,
        "gpt-5.4",
        round_cost=lambda value, fallback: (value.cost_usd, value.estimated),
        compact=lambda value, limit: str(value)[:limit],
    )

    assert detail == "$0.25 · ∑1,100 · ↑1,000 ↻200 ↓80 ◇20"
