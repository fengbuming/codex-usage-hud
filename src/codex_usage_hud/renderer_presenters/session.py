"""Session selection, status, and cache presentation helpers."""

from __future__ import annotations

from typing import Any, Callable

from ..core.parser import ParsedSession


DisplayTokens = tuple[int | None, bool, int | None, bool, int | None, bool, int | None, bool]
DisplayTokensFn = Callable[[ParsedSession], DisplayTokens]
DisplayCachedTokensFn = Callable[
    [ParsedSession, int | None, bool], tuple[int | None, bool]
]
CompactFn = Callable[[Any, int], str]
SessionFlagFn = Callable[[ParsedSession], bool]
RateMarkerFn = Callable[[float | None, bool], str]
RateFn = Callable[[ParsedSession], tuple[float | None, bool]]
UsageMoneyFn = Callable[[int | None, float | None], str]


def session_label(
    snapshot: ParsedSession,
    *,
    is_new_session: SessionFlagFn,
    is_pending_session: SessionFlagFn,
    compact: CompactFn,
) -> str:
    if is_new_session(snapshot):
        return "新会话"
    if is_pending_session(snapshot):
        return "会话加载中"
    title = compact(snapshot.session_title, 36)
    if title:
        return title
    session_id = str(snapshot.session_id or "n/a")
    return session_id[-12:] if len(session_id) > 12 else session_id


def follow_elapsed_ms(snapshot: ParsedSession, now_ms: int) -> int:
    observed_at_ms = int(snapshot.selection_observed_at_ms or 0)
    if observed_at_ms <= 0:
        return 0
    return max(0, int(now_ms) - observed_at_ms)


def follow_feedback(snapshot: ParsedSession) -> str:
    reason = str(snapshot.follow_reason or "").strip()
    labels = {
        "awaiting-canonical-id": "会话切换中：等待 Codex 提供正式会话 ID",
        "awaiting-persistence": "会话切换中：等待 Codex 写入会话映射",
        "awaiting-exact-mapping": "会话切换中：正式 ID 已收到，等待本地映射",
        "ambiguous-persisted-identity": "会话切换暂停：存在多个未归档同名会话，请展开请求面板选择",
        "no-unarchived-candidate": "会话切换暂停：当前列表没有可匹配的未归档会话",
        "renderer-channel-unavailable": "会话切换暂停：renderer 事件通道不可用",
    }
    return labels.get(reason, "会话切换中：正在确认当前会话")


def status_label(value: str) -> str:
    labels = {
        "starting": "启动中",
        "loading": "加载中",
        "waiting": "等待日志",
        "missing": "未找到",
        "error": "出错",
        "parsed": "实时",
        "live": "实时",
        "idle": "空闲",
        "stale": "历史",
    }
    return labels.get(value, value)


def request_status_label(value: str) -> str:
    labels = {
        "waiting": "等待",
        "running": "运行中",
        "confirmed": "已确认",
        "disabled": "已关闭",
        "error": "出错",
    }
    return labels.get(value, value)


def activity_label(value: str) -> str:
    labels = {
        "idle": "空闲",
        "user": "用户输入",
        "agent": "助手消息",
        "tool call": "调用工具",
        "tool output": "工具返回",
        "assistant": "助手输出",
        "confirmed": "Token确认",
        "reasoning": "思考中",
    }
    return labels.get(value, value)


def gap_label(value: str) -> str:
    labels = {
        "user_wait": "等用户",
        "tool_wait": "等工具",
        "model_or_idle": "模型思考",
        "model_startup": "模型启动",
        "other_gap": "执行等待",
    }
    return labels.get(value, value)


def expanded_header_title(
    snapshot: ParsedSession,
    *,
    is_new_session: SessionFlagFn,
    is_pending_session: SessionFlagFn,
    compact: CompactFn,
    fallback: str,
) -> str:
    if is_new_session(snapshot):
        return "新会话"
    if is_pending_session(snapshot):
        return "会话加载中"
    title = compact(snapshot.session_title, 72)
    return title or fallback


def display_tokens(snapshot: ParsedSession) -> DisplayTokens:
    request = snapshot.request
    input_tokens = request.input_tokens
    input_estimated = False
    if input_tokens is None and request.estimated:
        input_tokens = (
            snapshot.confirmed.last_input
            + snapshot.estimate.input_tokens
            + snapshot.estimate.tool_tokens
        )
        input_estimated = input_tokens > 0

    output_tokens = request.output_tokens
    output_estimated = request.estimated and output_tokens is not None
    reasoning_tokens = request.reasoning_tokens
    total_tokens = request.total_tokens
    total_estimated = request.estimated or input_estimated
    if input_tokens is not None and (request.estimated or not total_tokens):
        total_tokens = input_tokens + int(output_tokens or 0)
        total_estimated = True

    return (
        input_tokens,
        input_estimated,
        output_tokens,
        output_estimated,
        reasoning_tokens,
        False,
        total_tokens,
        total_estimated,
    )


def display_cached_tokens(
    snapshot: ParsedSession,
    input_tokens: int | None,
    input_estimated: bool,
) -> tuple[int | None, bool]:
    cached_tokens = snapshot.request.cached_tokens
    cached_estimated = snapshot.request.estimated and cached_tokens is not None
    if cached_tokens is None and input_tokens is not None:
        cached_tokens = min(snapshot.confirmed.last_cached, int(input_tokens))
        cached_estimated = input_estimated or snapshot.request.estimated
    return cached_tokens, cached_estimated


def session_cache_hit_rate(
    snapshot: ParsedSession,
    *,
    display_tokens_fn: DisplayTokensFn = display_tokens,
    display_cached_tokens_fn: DisplayCachedTokensFn = display_cached_tokens,
) -> tuple[float | None, bool]:
    input_tokens = int(snapshot.confirmed.cumulative_input or 0)
    cached_tokens = int(snapshot.confirmed.cumulative_cached or 0)
    estimated = False
    if snapshot.request.status == "running" or input_tokens <= 0:
        (
            request_input_tokens,
            input_estimated,
            _output_tokens,
            _output_estimated,
            _reasoning_tokens,
            _reasoning_estimated,
            _total_tokens,
            _total_estimated,
        ) = display_tokens_fn(snapshot)
        request_cached_tokens, cached_estimated = display_cached_tokens_fn(
            snapshot,
            request_input_tokens,
            input_estimated,
        )
        if request_input_tokens is not None and int(request_input_tokens) > 0:
            request_input = int(request_input_tokens)
            request_cached = int(request_cached_tokens or 0)
            if snapshot.request.status == "running":
                input_tokens += request_input
                cached_tokens += request_cached
            else:
                input_tokens = request_input
                cached_tokens = request_cached
            estimated = input_estimated or cached_estimated or snapshot.request.estimated
    if input_tokens <= 0:
        return None, estimated
    cached_tokens = max(0, min(cached_tokens, input_tokens))
    return cached_tokens / max(1, input_tokens), estimated


def session_cache_hit_rate_label(
    snapshot: ParsedSession,
    *,
    cache_hit_rate: RateFn,
    rate_marker: RateMarkerFn,
) -> str:
    ratio, estimated = cache_hit_rate(snapshot)
    return rate_marker(ratio, estimated)


def top_session_cache_hit_rate_label(
    snapshot: ParsedSession,
    *,
    cache_hit_rate_label: Callable[[ParsedSession], str],
) -> str:
    # The top HUD presents the value as a live dashboard metric; keep the
    # request-level estimated marker out of this compact session summary.
    return cache_hit_rate_label(snapshot).removeprefix("◎").removeprefix("~")


def top_cache_progress_label(
    snapshot: ParsedSession,
    *,
    cache_hit_rate_label: Callable[[ParsedSession], str],
) -> str:
    label = top_session_cache_hit_rate_label(
        snapshot, cache_hit_rate_label=cache_hit_rate_label,
    )
    return f"缓存命中 {label}"


def top_session_usage_summary(
    snapshot: ParsedSession,
    session_cost: float | None,
    *,
    is_new_session: SessionFlagFn,
    is_pending_session: SessionFlagFn,
    display_tokens_fn: DisplayTokensFn,
    usage_money: UsageMoneyFn,
    session_speed_label: Callable[[ParsedSession], str],
) -> str:
    if is_new_session(snapshot):
        return "新会话 等待首个会话事件"
    if is_pending_session(snapshot):
        return "本会话 加载精确会话映射"
    family_tokens = int(getattr(snapshot, "family_tokens", 0) or 0)
    family_cost = getattr(snapshot, "family_cost_usd", None)
    thread_tokens = int(snapshot.confirmed.cumulative_total or 0)
    thread_cost = session_cost
    if snapshot.request.status == "running":
        request_total_tokens = display_tokens_fn(snapshot)[6]
        thread_tokens += int(request_total_tokens or 0)
    # Prefer family (root + live subagents) so the top bar matches usage top10.
    if family_tokens > thread_tokens or (
        family_cost is not None
        and thread_cost is not None
        and float(family_cost) > float(thread_cost)
    ):
        total_tokens = max(family_tokens, thread_tokens)
        total_cost = family_cost if family_cost is not None else thread_cost
    else:
        total_tokens = thread_tokens
        total_cost = thread_cost if thread_cost is not None else family_cost
    return (
        f"本会话 {usage_money(total_tokens, total_cost)}"
        f"/{session_speed_label(snapshot)}"
    )


__all__ = [
    "activity_label",
    "display_cached_tokens",
    "display_tokens",
    "expanded_header_title",
    "follow_elapsed_ms",
    "follow_feedback",
    "gap_label",
    "request_status_label",
    "session_cache_hit_rate",
    "session_cache_hit_rate_label",
    "session_label",
    "status_label",
    "top_cache_progress_label",
    "top_session_cache_hit_rate_label",
    "top_session_usage_summary",
]
