"""Pure top-level task and activity projection for Renderer payloads.

The payload builder owns the final ``RendererHudPayload`` assembly.  This
module owns the snapshot-to-``topDetails`` activity values so task status,
elapsed time, chips, and the activity trail can evolve independently of the
payload envelope.  All formatting and snapshot access are explicit through
``ActivityProjectionContext``; the owner has no runtime, CDP, or UI imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from datetime import datetime
import re
from typing import Any, Callable

from .core.parser import ParsedSession, RequestRound
from . import renderer_activity_trail


CompactFn = Callable[[Any, int], str]
SessionFlagFn = Callable[[ParsedSession], bool]
TaskRowsFn = Callable[[ParsedSession], list[RequestRound]]
TaskTotalFn = Callable[
    [ParsedSession], tuple[int, int, int, int, int, float | None, bool]
]
RoundCostFn = Callable[[RequestRound, str], tuple[float | None, bool]]
StringFn = Callable[[str], str]
RateMarkerFn = Callable[[float | None, bool], str]
MoneyFn = Callable[[float | None, bool], str]
ShortNumberFn = Callable[[int | None], str]
DurationFn = Callable[[float | None], str]
TimelineTimeFn = Callable[[datetime | None], str]
RoundElapsedFn = Callable[[datetime | None], str]


@dataclass(frozen=True)
class ActivityProjectionContext:
    """Formatting and snapshot-domain callbacks required by this owner."""

    is_new_session: SessionFlagFn
    is_pending_session: SessionFlagFn
    task_rows: TaskRowsFn
    task_total: TaskTotalFn
    round_cost: RoundCostFn
    compact: CompactFn
    activity_label: StringFn
    request_status_label: StringFn
    gap_label: StringFn
    short_number: ShortNumberFn
    format_rate_marker: RateMarkerFn
    format_fixed_money: MoneyFn
    duration_text: DurationFn
    timeline_time: TimelineTimeFn
    round_elapsed_text: RoundElapsedFn


def current_work_item(snapshot: ParsedSession) -> Any | None:
    """Return the selected active work item, preferring the marked current one."""
    for item in snapshot.active_work_items:
        if getattr(item, "current", False):
            return item
    return snapshot.active_work_items[0] if snapshot.active_work_items else None


def task_finished(snapshot: ParsedSession) -> bool:
    return (
        (snapshot.task_completed_at is not None or snapshot.task_aborted_at is not None)
        and snapshot.request.status != "running"
        and not snapshot.slow.current_gap_active
    )


def task_aborted(snapshot: ParsedSession) -> bool:
    return (
        snapshot.task_aborted_at is not None
        and (
            snapshot.task_completed_at is None
            or snapshot.task_aborted_at >= snapshot.task_completed_at
        )
    )


def _step_field(step: Any, name: str, alt: str = "") -> Any:
    """Read a field from an ActivityStep object or its payload Mapping form."""
    value = getattr(step, name, None)
    if value is None and isinstance(step, Mapping):
        value = step.get(alt or name)
    return value


def _running_step_detail(steps: Any) -> str:
    """Return the active step's shell-stripped command, if one is running."""
    for step in reversed(list(steps or ())):
        status = str(_step_field(step, "status") or "").strip().lower()
        if status != "running":
            continue
        tool = str(_step_field(step, "tool_name", "toolName") or "").lower()
        text = " ".join(str(_step_field(step, "detail") or "").split())
        if "request_user_input" in tool:
            return f"等确认 · {text}".strip(" ·")
        return text
    return ""


def _latest_thinking_title(steps: Any) -> str:
    """Return the newest reasoning summary heading, de-markdowned upstream."""
    for step in reversed(list(steps or ())):
        if str(_step_field(step, "title") or "").strip() != "思考":
            continue
        title = " ".join(str(_step_field(step, "detail") or "").split())
        if title:
            return title
    return ""


def activity_state(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> str:
    if task_finished(snapshot):
        return "已中止" if task_aborted(snapshot) else "已完成"
    item = current_work_item(snapshot)
    if item is not None:
        label = (
            getattr(item, "status_label", "")
            or getattr(item, "status_text", "")
            or getattr(item, "status", "")
        )
        if label:
            return context.compact(label, 18)
    if snapshot.request.error or snapshot.error:
        return "异常"
    if snapshot.slow.current_gap_active:
        return "等待中"
    if snapshot.request.status == "running":
        return "请求中"
    activity = context.activity_label(snapshot.activity.kind)
    if activity not in {"空闲", "Token确认"}:
        return activity
    return context.request_status_label(snapshot.request.status or snapshot.status)


def activity_main(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
    limit: int = 118,
) -> str:
    activity = context.activity_label(snapshot.activity.kind)
    detail = context.compact(snapshot.activity.detail, limit)
    # 活跃步优先: prefer the running step's shell-stripped command and the
    # de-markdowned reasoning heading over the raw activity record text, so
    # the renderer copy matches the desktop bubble footer.
    running_detail = _running_step_detail(snapshot.activity_steps)
    if running_detail:
        activity = context.activity_label("tool call")
        detail = context.compact(running_detail, limit)
    elif snapshot.activity.kind == "reasoning":
        title = _latest_thinking_title(snapshot.activity_steps)
        if title:
            detail = context.compact(title, limit)
    if not detail:
        detail = context.request_status_label(snapshot.request.status or snapshot.status)
    # Safety net: when the last JSONL record is an unrecognized bookkeeping
    # event but the SSE tracker confirms the request is running, avoid showing
    # "空闲：no activity" and surface the running state instead.
    if snapshot.activity.kind == "idle" and snapshot.request.status == "running":
        activity = "运行中"
        detail = "正在处理"
    return f"{activity}：{detail}"


def current_task(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> str:
    if context.is_new_session(snapshot):
        return "新会话"
    if context.is_pending_session(snapshot):
        return "等待 Codex 写入精确会话映射"
    prompt = context.compact(getattr(snapshot, "task_prompt", ""), 180)
    if prompt:
        return prompt
    item = current_work_item(snapshot)
    if item is not None:
        title = (
            getattr(item, "title", "")
            or getattr(item, "target_title", "")
            or getattr(item, "workdir_name", "")
        )
        if title:
            return context.compact(title, 128)
    if snapshot.session_title:
        return context.compact(snapshot.session_title, 128)
    return f"会话 {snapshot.session_id[-12:]}"


def executing_text(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> str:
    if task_finished(snapshot):
        summary = context.compact(snapshot.last_output.detail, 160)
        if summary:
            return summary
        if task_aborted(snapshot):
            return "任务已中止"
        return activity_main(snapshot, context=context)
    item = current_work_item(snapshot)
    if item is not None:
        label = getattr(item, "status_label", "") or activity_state(
            snapshot,
            context=context,
        )
        steps = getattr(item, "activity_steps", ())
        detail = (
            _running_step_detail(steps)
            or _latest_thinking_title(steps)
            or getattr(item, "status_text", "")
            or getattr(item, "detail", "")
            or getattr(item, "last_text", "")
            or getattr(item, "progress", "")
        )
        if detail:
            return f"{label}：{context.compact(detail, 108)}"
        return context.compact(label, 108)
    return activity_main(snapshot, context=context)


def work_output(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
    limit: int = 180,
) -> str:
    """Return the latest assistant output for the Renderer work feed."""
    output = getattr(snapshot, "last_output", None)
    kind = str(getattr(output, "kind", "") or "").strip().lower()
    detail = str(getattr(output, "detail", "") or "").strip()
    if kind not in {"agent", "assistant", "output"} or not detail:
        return ""
    return context.compact(detail, limit)


def work_tool_rows(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
    max_rows: int = 3,
) -> list[dict[str, object]]:
    """Return the latest concrete tool rows for the Renderer work feed."""
    rows: list[dict[str, object]] = []
    for step in getattr(snapshot, "activity_steps", []) or []:
        title = str(_step_field(step, "title") or "执行命令").strip()
        tool_name = str(_step_field(step, "tool_name", "toolName") or "").strip()
        detail = " ".join(str(_step_field(step, "detail") or "").split())
        if title == "思考" and not tool_name:
            continue
        if not detail and not tool_name:
            continue
        status = str(_step_field(step, "status") or "").strip().lower()
        text = title if not detail else f"{title}：{detail}"
        rows.append(
            {
                "title": context.compact(title, 32),
                "detail": context.compact(detail or tool_name, 140),
                "text": context.compact(text, 180),
                "status": status,
                "active": status == "running",
                "tooltip": text,
            }
        )
    return rows[-max(1, int(max_rows)) :]


def activity_labels(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> dict[str, str]:
    if task_finished(snapshot):
        return {
            "executingLabel": "任务中止" if task_aborted(snapshot) else "完成任务",
            "currentTaskLabel": "当前需求",
            "activityElapsedLabel": "已处理",
            "activityGapLabel": "处理轮次",
            "activityLastLabel": "处理花费",
        }
    return {
        "executingLabel": "正在执行",
        "currentTaskLabel": "当前需求",
        "activityElapsedLabel": "已运行",
        "activityGapLabel": "当前等待",
        "activityLastLabel": "需求轮次",
    }


def task_finished_at(snapshot: ParsedSession) -> datetime | None:
    if snapshot.task_aborted_at is not None:
        return snapshot.task_aborted_at
    return snapshot.task_completed_at


def running_duration(
    start: datetime | None,
    end: datetime | None,
    now: datetime,
) -> float | None:
    if start is None:
        return None
    finish = (
        end or now.astimezone(start.tzinfo)
        if start.tzinfo is not None
        else end or now.replace(tzinfo=None)
    )
    return max(0.0, (finish - start).total_seconds())


def activity_elapsed(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> str:
    item = current_work_item(snapshot)
    started_at = None
    if item is not None:
        started_at = (
            getattr(item, "started_at", None)
            or getattr(item, "task_started_at", None)
            or getattr(item, "session_started_at", None)
        )
    started_at = (
        started_at
        or snapshot.request.started_at
        or snapshot.task_started_at
        or snapshot.session_started_at
    )
    if task_finished(snapshot):
        duration = running_duration(
            started_at,
            task_finished_at(snapshot),
            snapshot.refreshed_at,
        )
        return context.duration_text(duration)
    return context.round_elapsed_text(started_at).strip()


def task_round_count(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> int:
    rows = context.task_rows(snapshot)
    count = 0
    for item in rows:
        if item.status == "waiting" and not (
            item.total_tokens
            or item.input_tokens
            or item.output_tokens
            or item.reasoning_tokens
            or item.cost_usd
        ):
            continue
        count += 1
    return count


def task_cache_hit_rate_label(
    snapshot: ParsedSession,
    rows: list[RequestRound],
    *,
    context: ActivityProjectionContext,
) -> str:
    input_tokens = sum(int(item.input_tokens or 0) for item in rows)
    if input_tokens <= 0:
        return "--"
    cached_tokens = sum(int(item.cached_tokens or 0) for item in rows)
    cached_tokens = max(0, min(cached_tokens, input_tokens))
    estimated = any(item.estimated or item.status == "running" for item in rows)
    label = context.format_rate_marker(
        cached_tokens / max(1, input_tokens),
        estimated,
    )
    return label[1:] if label.startswith("◎") else label


def task_spend_text(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> str:
    rows = context.task_rows(snapshot)
    (
        _input_tokens,
        _cached_tokens,
        _output_tokens,
        _reasoning_tokens,
        total_tokens,
        cost,
        estimated,
    ) = context.task_total(snapshot)
    return (
        f"{context.short_number(total_tokens)}Tokens/"
        f"{context.format_fixed_money(cost, estimated)}/"
        f"{task_cache_hit_rate_label(snapshot, rows, context=context)}"
    )


def task_spend_money_text(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> str:
    (
        _input_tokens,
        _cached_tokens,
        _output_tokens,
        _reasoning_tokens,
        _total_tokens,
        cost,
        estimated,
    ) = context.task_total(snapshot)
    return context.format_fixed_money(cost, estimated)


def activity_gap_value(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> str:
    if task_finished(snapshot):
        return f"{task_round_count(snapshot, context=context)}轮"
    return snapshot.slow.current_gap


def activity_last(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> str:
    if task_finished(snapshot):
        return task_spend_money_text(snapshot, context=context)
    return f"{task_round_count(snapshot, context=context)}轮"


def activity_last_tooltip(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> str:
    if task_finished(snapshot):
        return task_spend_text(snapshot, context=context)
    return f"本次需求已产生 {task_round_count(snapshot, context=context)} 轮"


def first_duration_fragment(value: str) -> str:
    match = re.search(r"\d+(?:\.\d+)?s|\d+m\d+s|\d+h\d+m", value or "")
    return match.group(0) if match else "--"


def slow_chip(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> str:
    call = snapshot.slow.slowest_tool_call
    if call is not None:
        duration = context.duration_text(
            running_duration(call.start, call.end, snapshot.refreshed_at)
        )
        return context.compact(f"最慢工具:{duration}", 28)
    if snapshot.slow.slowest_tool and not snapshot.slow.slowest_tool.startswith("无"):
        return context.compact(
            f"最慢工具:{first_duration_fragment(snapshot.slow.slowest_tool)}",
            28,
        )
    return "最慢工具:--"


def gap_chip(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> str:
    detail = snapshot.slow.longest_gap_detail
    if detail is not None:
        return context.compact(
            f"最长等待:{context.duration_text(detail.duration_seconds)}",
            28,
        )
    if snapshot.slow.longest_gap and not snapshot.slow.longest_gap.startswith("无"):
        return context.compact(
            f"最长等待:{first_duration_fragment(snapshot.slow.longest_gap)}",
            28,
        )
    return "最长等待:--"


def activity_trail(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> list[dict[str, object]]:
    return renderer_activity_trail.activity_trail(
        snapshot,
        context=context,
        current_task=lambda value: current_task(value, context=context),
        activity_main=lambda value, limit: activity_main(
            value,
            context=context,
            limit=limit,
        ),
    )


def task_ordinal(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> str:
    count = int(getattr(snapshot, "task_count", 0) or 0)
    index = int(getattr(snapshot, "task_index", 0) or 0)
    if count <= 0:
        return ""
    if task_finished(snapshot):
        return f"共{count}次需求"
    if index > 0:
        return f"第{index}次需求"
    return ""


def task_ordinal_parts(
    snapshot: ParsedSession,
    *,
    context: ActivityProjectionContext,
) -> dict[str, str]:
    value = task_ordinal(snapshot, context=context)
    if not value:
        return {
            "taskOrdinal": "",
            "taskOrdinalSession": "",
            "taskOrdinalActivity": "",
        }
    if task_finished(snapshot):
        return {
            "taskOrdinal": value,
            "taskOrdinalSession": value,
            "taskOrdinalActivity": "",
        }
    return {
        "taskOrdinal": value,
        "taskOrdinalSession": "",
        "taskOrdinalActivity": value,
    }


__all__ = [
    "ActivityProjectionContext",
    "activity_elapsed",
    "activity_gap_value",
    "activity_labels",
    "activity_last",
    "activity_last_tooltip",
    "activity_main",
    "activity_state",
    "activity_trail",
    "current_task",
    "current_work_item",
    "executing_text",
    "first_duration_fragment",
    "gap_chip",
    "running_duration",
    "slow_chip",
    "task_aborted",
    "task_cache_hit_rate_label",
    "task_finished",
    "task_finished_at",
    "task_ordinal",
    "task_ordinal_parts",
    "task_round_count",
    "task_spend_money_text",
    "task_spend_text",
    "work_output",
    "work_tool_rows",
]
