"""Tests for the pure top-level task/activity payload owner."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from codex_usage_hud import renderer_activity_projection as projection
from codex_usage_hud import renderer_activity_trail as trail_owner
from codex_usage_hud.core.parser import (
    Activity,
    ActivityStep,
    ParsedSession,
    RequestRound,
    RequestTokens,
)
from codex_usage_hud.renderer_presenters import common
from codex_usage_hud.renderer_presenters import request


def _compact(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _context(rows: list[RequestRound]) -> projection.ActivityProjectionContext:
    return projection.ActivityProjectionContext(
        is_new_session=lambda _snapshot: False,
        is_pending_session=lambda _snapshot: False,
        task_rows=lambda _snapshot: rows,
        task_total=lambda _snapshot: (
            100,
            20,
            10,
            0,
            110,
            0.25,
            False,
        ),
        round_cost=lambda item, _fallback: (item.cost_usd, item.estimated),
        compact=_compact,
        activity_label=lambda value: {
            "tool call": "调用工具",
            "assistant": "助手输出",
        }.get(value, value),
        request_status_label=lambda value: {
            "running": "运行中",
            "confirmed": "已确认",
        }.get(value, value),
        gap_label=lambda value: {"tool_wait": "等工具"}.get(value, value),
        short_number=common.short_num,
        format_rate_marker=common.format_rate_marker,
        format_fixed_money=common.format_fixed_money,
        duration_text=common.duration_text,
        timeline_time=common.timeline_time,
        round_elapsed_text=lambda started_at: request.round_elapsed_text(
            started_at,
            now=datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc),
        ),
    )


def test_activity_owner_projects_current_task_and_trail_without_builder_import() -> None:
    moment = datetime(2026, 8, 1, 11, 59, 0, tzinfo=timezone.utc)
    row = RequestRound(
        index=2,
        status="confirmed",
        model="gpt-5.5",
        input_tokens=100,
        cached_tokens=20,
        output_tokens=10,
        reasoning_tokens=0,
        total_tokens=110,
        estimated=False,
        cost_usd=0.25,
        started_at=moment,
        completed_at=moment,
        activity_summary="完成当前需求",
    )
    snapshot = ParsedSession(
        session_id="session-1234567890",
        session_title="Renderer session",
        request=RequestTokens(status="confirmed", model="gpt-5.5"),
        activity=Activity("assistant", "已完成当前需求", moment),
        task_started_at=moment,
        refreshed_at=moment,
    )
    snapshot.task_prompt = "保留当前 payload ABI"
    context = _context([row])

    assert projection.current_task(snapshot, context=context) == "保留当前 payload ABI"
    trail = projection.activity_trail(snapshot, context=context)
    direct_trail = trail_owner.activity_trail(
        snapshot,
        context=context,
        current_task=lambda value: projection.current_task(value, context=context),
        activity_main=lambda value, limit: projection.activity_main(
            value,
            context=context,
            limit=limit,
        ),
    )
    assert direct_trail == trail
    assert trail
    assert "轮次 #2" in str(trail)
    assert "已完成当前需求" in str(trail)


def test_activity_owner_has_no_runtime_or_overlay_dependency() -> None:
    path = Path(projection.__file__)
    source = path.read_text(encoding="utf-8")
    assert "renderer_payload_builder" not in source
    assert "renderer_runtime" not in source
    assert "desktop_overlay" not in source
    assert "renderer_client" not in source

    trail_source = Path(trail_owner.__file__).read_text(encoding="utf-8")
    assert "renderer_activity_projection" not in trail_source
    assert "renderer_payload_builder" not in trail_source
    assert "renderer_runtime" not in trail_source
    assert "desktop_overlay" not in trail_source


def test_renderer_work_feed_projects_latest_output_and_three_tools() -> None:
    moment = datetime(2026, 8, 1, 11, 59, 0, tzinfo=timezone.utc)
    snapshot = ParsedSession(
        session_id="session-work-feed",
        request=RequestTokens(status="running", model="gpt-5.5"),
        activity=Activity("tool call", "正在执行", moment),
        refreshed_at=moment,
    )
    snapshot.last_output = Activity("assistant", "最新助手正文", moment)
    snapshot.activity_steps = [
        ActivityStep(title="执行命令", detail=f"命令 {index}", status="completed")
        for index in range(1, 5)
    ]
    context = _context([])

    assert projection.work_output(snapshot, context=context) == "最新助手正文"
    rows = projection.work_tool_rows(snapshot, context=context)
    assert [row["text"] for row in rows] == ["执行命令：命令 2", "执行命令：命令 3", "执行命令：命令 4"]
