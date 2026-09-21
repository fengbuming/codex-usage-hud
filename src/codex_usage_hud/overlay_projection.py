"""Pure desktop-overlay payload and transition projection."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
import json
import sys
from typing import MutableMapping

from . import overlay_ipc
from .config import (
    DEFAULT_WORK_OVERLAY_MAX_ITEMS,
    DEFAULT_WORK_OVERLAY_SIDE,
    normalize_work_overlay_max_items,
    normalize_work_overlay_side,
)
from .core import WorkStatusItem
from .core.background_usage import BACKGROUND_USAGE_KIND


ACTIVE_WORK_ITEM_LIMIT = DEFAULT_WORK_OVERLAY_MAX_ITEMS
ACTIVE_WORK_STALE_SECONDS = 4 * 60 * 60
WORK_OVERLAY_STALE_SECONDS = 20.0
WORK_OVERLAY_TOP_OFFSET = 56
WORK_OVERLAY_MARGIN = 16
WORK_OVERLAY_ESTIMATED_ITEM_HEIGHT = 160


def _iso_or_empty(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def work_item_to_overlay_dict(item: WorkStatusItem) -> dict[str, object]:
    activity_steps = getattr(item, "activity_steps", ())
    return {
        "kind": item.kind,
        "eventId": item.event_id,
        "id": item.id,
        "title": item.title,
        "sessionId": item.session_id,
        "targetTitle": item.target_title,
        "roundIndex": item.round_index,
        "taskIndex": getattr(item, "task_index", 0),
        "modelName": item.model_name,
        "status": item.status,
        "statusLabel": item.status_label,
        "detail": item.detail,
        "statusText": item.status_text,
        "lastText": item.last_text,
        "elapsedText": item.elapsed_text,
        "progress": item.progress,
        "tokensText": item.tokens_text,
        "costText": item.cost_text,
        "cacheHitText": item.cache_hit_text,
        "workdirName": item.workdir_name,
        "source": item.source,
        "workdir": item.workdir,
        "modelProvider": item.model_provider,
        "profileName": getattr(item, "profile_name", ""),
        "clientKind": item.client_kind,
        "sessionStartedAt": _iso_or_empty(item.session_started_at),
        "taskStartedAt": _iso_or_empty(item.task_started_at),
        "startedAt": _iso_or_empty(item.started_at),
        "updatedAt": _iso_or_empty(item.updated_at),
        "lastOutputAt": _iso_or_empty(getattr(item, "last_output_at", None)),
        "current": item.current,
        "pendingAccounting": item.pending_accounting,
        "draftText": getattr(item, "draft_text", ""),
        "collapsedTitle": getattr(item, "collapsed_title", ""),
        "collapsedDisclosureAmbiguous": bool(
            getattr(item, "collapsed_disclosure_ambiguous", False)
        ),
        "activitySteps": [
            dict(step)
            for step in activity_steps
            if isinstance(step, Mapping)
        ],
    }


def background_usage_to_work_item(
    summary: Mapping[str, object],
    *,
    feature_label: Callable[[object, object], str],
    format_tokens: Callable[[int], str],
    format_cost: Callable[[float], str],
) -> WorkStatusItem | None:
    event_id = str(summary.get("eventId") or "").strip()
    if not event_id:
        return None
    models_value = summary.get("models")
    models = (
        [str(value).strip() for value in models_value if str(value).strip()]
        if isinstance(models_value, Sequence) and not isinstance(models_value, str)
        else []
    )
    model_name = " + ".join(models[:2])
    if len(models) > 2:
        model_name = f"{model_name} +{len(models) - 2}"
    request_count = max(0, int(summary.get("requestCount") or 0))
    total_tokens = max(0, int(summary.get("totalTokens") or 0))
    cost_value = summary.get("estimatedCostUsd")
    try:
        estimated_cost = float(cost_value) if cost_value is not None else None
    except (TypeError, ValueError):
        estimated_cost = None
    updated_at: datetime | None = None
    updated_text = str(summary.get("lastSeenAt") or "").strip()
    if updated_text:
        try:
            updated_at = datetime.fromisoformat(updated_text.replace("Z", "+00:00"))
        except ValueError:
            pass
    label = feature_label(summary.get("featureKey"), summary.get("featureLabel"))
    endpoint = str(summary.get("endpoint") or "/responses").strip()
    return WorkStatusItem(
        id=event_id,
        event_id=event_id,
        kind=BACKGROUND_USAGE_KIND,
        title=label,
        status=BACKGROUND_USAGE_KIND,
        status_label=f"Codex App 后台任务：{label}",
        detail=f"{request_count} 次 API 请求",
        model_name=model_name,
        status_text=f"{request_count} 次请求",
        last_text=endpoint,
        progress=f"{request_count} 次 API 请求",
        tokens_text=format_tokens(total_tokens),
        cost_text=(
            f"估算 {format_cost(estimated_cost)}"
            if estimated_cost is not None
            else "估算不可用"
        ),
        workdir_name="查看后台用量记录",
        source=BACKGROUND_USAGE_KIND,
        workdir=str(summary.get("cwd") or "").strip(),
        model_provider=str(summary.get("provider") or "unknown").strip() or "unknown",
        client_kind="app",
        updated_at=updated_at,
    )


def append_background_usage(
    session_items: Sequence[WorkStatusItem],
    background_items: Sequence[WorkStatusItem],
) -> list[WorkStatusItem]:
    return [*session_items, *background_items]


def command_matches_item(
    command: Mapping[str, object], item: Mapping[str, object]
) -> bool:
    return overlay_ipc.command_matches_item(command, item)


def payload_status(item: Mapping[str, object]) -> str:
    return overlay_ipc.payload_status(item)


def payload_pending_accounting(item: Mapping[str, object]) -> bool:
    return bool(item.get("pendingAccounting"))


def payload_kind(item: Mapping[str, object]) -> str:
    return overlay_ipc.payload_kind(item)


def transition_name(
    old_item: Mapping[str, object], new_item: Mapping[str, object]
) -> str:
    return overlay_ipc.transition_name(old_item, new_item)


def runtime_task_key(item: WorkStatusItem) -> str:
    item_id = str(item.id or item.session_id or "").strip()
    started = item.task_started_at or item.started_at
    started_at = started.isoformat() if started is not None else ""
    if not item_id and not started_at:
        return ""
    return json.dumps(
        {"id": item_id, "taskStartedAt": started_at},
        ensure_ascii=False,
        sort_keys=True,
    )


def item_sort_key(item: WorkStatusItem) -> tuple[float, float]:
    session_timestamp = item.session_started_at or item.started_at or item.updated_at
    task_timestamp = item.started_at or item.updated_at or item.session_started_at
    session_seconds = session_timestamp.timestamp() if session_timestamp else 0.0
    task_seconds = task_timestamp.timestamp() if task_timestamp else 0.0
    return session_seconds, task_seconds


def item_updated_seconds(item: WorkStatusItem) -> float:
    updated_at = (
        item.updated_at
        or item.started_at
        or item.task_started_at
        or item.session_started_at
    )
    return updated_at.timestamp() if updated_at is not None else 0.0


def _activity_step_key(step: Mapping[str, object]) -> tuple[str, ...]:
    call_id = str(step.get("callId") or step.get("call_id") or "").strip()
    if call_id:
        return ("call", call_id)
    line = str(step.get("line") or "").strip()
    if line and line != "0":
        return ("line", line)
    return (
        "text",
        " ".join(str(step.get("title") or "").split()),
        " ".join(str(step.get("detail") or "").split()),
        str(step.get("timestamp") or "").strip(),
    )


def _merge_activity_steps(
    cached: object,
    current: object,
) -> tuple[dict[str, object], ...]:
    old_steps = [dict(step) for step in cached or () if isinstance(step, Mapping)]
    new_steps = [dict(step) for step in current or () if isinstance(step, Mapping)]
    if not old_steps:
        return tuple(new_steps[-64:])
    if not new_steps:
        return tuple(old_steps[-64:])
    merged = old_steps
    for new_step in new_steps:
        key = _activity_step_key(new_step)
        match_index = next(
            (
                index
                for index in range(len(merged) - 1, -1, -1)
                if _activity_step_key(merged[index]) == key
            ),
            None,
        )
        if match_index is None:
            merged.append(new_step)
            continue
        previous = merged[match_index]
        merged_fields = {
            field: value
            for field, value in new_step.items()
            if value not in (None, "", [])
        }
        # A fresh partial snapshot may replay a finished step as "running";
        # never regress a terminal status back to running.
        if (
            str(previous.get("status") or "").strip().lower()
            in {"completed", "failed", "error"}
            and str(merged_fields.get("status") or "").strip().lower()
            == "running"
        ):
            merged_fields.pop("status", None)
        merged[match_index] = {**previous, **merged_fields}
    return tuple(merged[-64:])


def _merge_stable_item_metadata(
    cached: WorkStatusItem,
    current: WorkStatusItem,
) -> WorkStatusItem:
    """Keep durable session metadata through partial/fast refresh snapshots."""
    updates: dict[str, object] = {}
    for field in (
        "title",
        "target_title",
        "model_name",
        "workdir",
        "workdir_name",
        "profile_name",
        "session_started_at",
        "updated_at",
    ):
        current_value = getattr(current, field, None)
        cached_value = getattr(cached, field, None)
        if current_value in (None, "") and cached_value not in (None, ""):
            updates[field] = cached_value
    if (
        str(getattr(current, "model_provider", "") or "").strip().lower()
        in {"", "unknown", "n/a"}
        and str(getattr(cached, "model_provider", "") or "").strip()
    ):
        updates["model_provider"] = cached.model_provider
    if (
        str(getattr(current, "client_kind", "") or "").strip().lower()
        in {"", "unknown", "n/a"}
        and str(getattr(cached, "client_kind", "") or "").strip()
    ):
        updates["client_kind"] = cached.client_kind

    cached_task = getattr(cached, "task_started_at", None)
    current_task = getattr(current, "task_started_at", None)
    cached_task_index = int(getattr(cached, "task_index", 0) or 0)
    current_task_index = int(getattr(current, "task_index", 0) or 0)
    same_task = (
        not cached_task_index
        or not current_task_index
        or cached_task_index == current_task_index
    ) and (cached_task is None or current_task is None or cached_task == current_task)
    if same_task:
        updates["round_index"] = max(
            0,
            int(getattr(cached, "round_index", 0) or 0),
            int(getattr(current, "round_index", 0) or 0),
        )
        for field in ("task_started_at", "started_at", "elapsed_text"):
            current_value = getattr(current, field, None)
            cached_value = getattr(cached, field, None)
            if current_value in (None, "") and cached_value not in (None, ""):
                updates[field] = cached_value
        updates["activity_steps"] = _merge_activity_steps(
            getattr(cached, "activity_steps", ()),
            getattr(current, "activity_steps", ()),
        )
    if not updates:
        return current
    return replace(current, **updates)


def _item_started_at(item: WorkStatusItem) -> datetime | None:
    return item.task_started_at or item.started_at or item.session_started_at


def select_visible_items(
    items: list[WorkStatusItem] | tuple[WorkStatusItem, ...],
    *,
    item_limit: int,
    seen_task_keys: set[str],
    now: datetime,
    stale_seconds: float,
    runtime_started_at: datetime | None = None,
) -> list[WorkStatusItem]:
    del now, stale_seconds, runtime_started_at
    previously_seen = set(seen_task_keys)
    visible: list[WorkStatusItem] = []
    for item in items:
        if len(visible) >= item_limit:
            break
        task_key = runtime_task_key(item)
        if item.status == "recent":
            # Completion is a state transition, not a recent-history notice.
            # A session must have been observed as active in this runtime
            # before its terminal state can become a circular completion bubble.
            if task_key and task_key in previously_seen:
                visible.append(item)
                seen_task_keys.add(task_key)
            continue
        visible.append(item)
        # HUD 重启前开始的任务也可能仍在运行；以本次显示过进行态为准，不能按开始时间过滤。
        if task_key:
            seen_task_keys.add(task_key)
    # The followed session is the primary user-facing item. A burst of newer
    # background/CLI sessions must not evict it solely because the overlay has
    # a small item limit.
    current_item = next(
        (
            item
            for item in items
            if bool(item.current) and item.status != "recent"
        ),
        None,
    )
    if current_item is not None and all(
        str(item.id or "") != str(current_item.id or "") for item in visible
    ):
        if len(visible) >= item_limit:
            visible[-1] = current_item
        else:
            visible.append(current_item)
        current_key = runtime_task_key(current_item)
        if current_key:
            seen_task_keys.add(current_key)
    return visible


def stabilize_published_items(
    items: list[WorkStatusItem] | tuple[WorkStatusItem, ...],
    *,
    item_limit: int,
    cache: MutableMapping[str, WorkStatusItem],
    terminal_tasks: MutableMapping[str, str],
    provider_scope: frozenset[str] | None,
    now: datetime,
    stale_seconds: float,
) -> list[WorkStatusItem]:
    if item_limit <= 0:
        cache.clear()
        return []
    merged = {str(item.id): item for item in items if str(item.id or "").strip()}
    for item_id, item in list(merged.items()):
        cached_item = cache.get(item_id)
        if cached_item is not None:
            item = _merge_stable_item_metadata(cached_item, item)
            if item_updated_seconds(item) < item_updated_seconds(cached_item):
                # A renderer-observed disclosure title, including an explicit
                # empty value, is current DOM state rather than durable cache
                # metadata.  Do not resurrect a prior title when an older
                # JSONL refresh arrives after that DOM observation.
                item = replace(
                    cached_item,
                    current=item.current,
                    collapsed_title=item.collapsed_title,
                )
            merged[item_id] = item
        if cached_item is not None and cached_item.session_started_at is not None:
            stable_start = cached_item.session_started_at
            if item.session_started_at is not None:
                stable_start = min(stable_start, item.session_started_at)
            if item.session_started_at != stable_start:
                item = replace(item, session_started_at=stable_start)
                merged[item_id] = item
        terminal_task = terminal_tasks.get(item_id)
        item_task = (item.task_started_at or item.started_at)
        item_task_text = item_task.isoformat() if item_task is not None else ""
        if terminal_task and terminal_task == item_task_text:
            merged.pop(item_id, None)
        elif terminal_task:
            terminal_tasks.pop(item_id, None)
    for item_id, item in list(merged.items()):
        if bool(getattr(item, "is_subagent", False)) and not item.current:
            merged.pop(item_id, None)
    for item_id, cached_item in list(cache.items()):
        if item_id in merged or bool(getattr(cached_item, "is_subagent", False)):
            continue
        cached_task = cached_item.task_started_at or cached_item.started_at
        cached_task_text = cached_task.isoformat() if cached_task is not None else ""
        if terminal_tasks.get(item_id) == cached_task_text:
            continue
        if provider_scope is not None and cached_item.model_provider not in provider_scope:
            continue
        updated_at = (
            cached_item.updated_at
            or cached_item.started_at
            or cached_item.task_started_at
            or cached_item.session_started_at
        )
        if cached_item.status != "recent" and (
            updated_at is None
            or max(0.0, (now - updated_at).total_seconds()) > stale_seconds
        ):
            continue
        merged[item_id] = replace(cached_item, current=False)
    stable = sorted(merged.values(), key=item_sort_key, reverse=True)
    current_item = next(
        (
            item
            for item in stable
            if bool(item.current) and item.status != "recent"
        ),
        None,
    )
    stable = stable[:item_limit]
    if current_item is not None and all(
        str(item.id or "") != str(current_item.id or "") for item in stable
    ):
        if stable:
            stable[-1] = current_item
        else:
            stable = [current_item]
    cache.clear()
    cache.update(
        {
            str(item.id): replace(item, current=False)
            for item in stable
            if str(item.id or "").strip()
        }
    )
    return stable


def payload_timestamp_seconds(
    item: Mapping[str, object],
    *keys: str,
    parse_timestamp: Callable[[str], datetime | None],
) -> float:
    for key in keys:
        value = item.get(key)
        if isinstance(value, datetime):
            return value.timestamp()
        text = str(value or "").strip()
        if text:
            parsed = parse_timestamp(text)
            if parsed is not None:
                return parsed.timestamp()
    return 0.0


def order_payload_items(
    items: Sequence[Mapping[str, object]],
    *,
    is_background: Callable[[Mapping[str, object]], bool],
    is_completed: Callable[[Mapping[str, object]], bool],
    parse_timestamp: Callable[[str], datetime | None],
) -> list[Mapping[str, object]]:
    session_items: list[Mapping[str, object]] = []
    background: list[Mapping[str, object]] = []
    completed: list[Mapping[str, object]] = []
    for item in items:
        if is_background(item):
            background.append(item)
        elif is_completed(item):
            completed.append(item)
        else:
            # ``current`` identifies the renderer-selected session. It is not
            # a stable work-bubble ordering key when CLI and App sessions run
            # concurrently, so keep all session cards in one order group.
            session_items.append(item)
    background.sort(
        key=lambda item: payload_timestamp_seconds(
            item, "updatedAt", parse_timestamp=parse_timestamp
        ),
        reverse=True,
    )
    completed.sort(
        key=lambda item: payload_timestamp_seconds(
            item,
            "updatedAt",
            "taskStartedAt",
            "startedAt",
            parse_timestamp=parse_timestamp,
        )
    )
    session_items.sort(
        key=lambda item: (
            payload_timestamp_seconds(
                item,
                "sessionStartedAt",
                "taskStartedAt",
                "startedAt",
                "updatedAt",
                parse_timestamp=parse_timestamp,
            ),
            str(item.get("id") or item.get("sessionId") or "").strip(),
        ),
        reverse=True,
    )
    return session_items + background + completed


def visible_payload_items(
    items: Sequence[Mapping[str, object]],
    dismissed_instances: MutableMapping[str, str],
    *,
    item_limit: int,
    dismiss_key: Callable[[Mapping[str, object]], str],
) -> list[Mapping[str, object]]:
    """Choose newest completed items without changing their display order.

    ``order_payload_items`` intentionally puts completed items oldest first so
    the newest badge sits at the right edge.  Applying the shared item limit
    directly to that order instead retained the oldest completed sessions when
    active cards consumed the available slots.  Select the newest completed
    tail first, then return it in the original visual order.
    """
    limit = max(0, int(item_limit))
    non_completed_indexes = [
        index
        for index, item in enumerate(items)
        if payload_status(item) != "recent"
    ]
    selected_indexes = set(non_completed_indexes[:limit])
    remaining = max(0, limit - len(selected_indexes))
    if remaining:
        completed_indexes = [
            index
            for index, item in enumerate(items)
            if payload_status(item) == "recent"
        ]
        selected_indexes.update(completed_indexes[-remaining:])

    visible: list[Mapping[str, object]] = []
    for index, item in enumerate(items):
        item_id = str(item.get("id") or "")
        if index not in selected_indexes:
            continue
        key = dismiss_key(item)
        if item_id and dismissed_instances.get(item_id) == key:
            continue
        if item_id and item_id in dismissed_instances:
            dismissed_instances.pop(item_id, None)
        visible.append(item)
    return visible


def _primary_screen_height() -> int:
    if sys.platform.startswith("win"):
        try:
            import ctypes

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            return max(1, int(user32.GetSystemMetrics(1)))
        except Exception:
            pass
    return 1080


def _work_overlay_max_items_for_screen_height(screen_height: int) -> int:
    available_height = max(
        1,
        int(screen_height) - WORK_OVERLAY_TOP_OFFSET - (WORK_OVERLAY_MARGIN * 2),
    )
    return max(1, available_height // WORK_OVERLAY_ESTIMATED_ITEM_HEIGHT)


def _work_overlay_screen_max_items(screen_height: int | None = None) -> int:
    height = _primary_screen_height() if screen_height is None else int(screen_height)
    return _work_overlay_max_items_for_screen_height(height)


def _work_overlay_item_limit_for_context(context: object) -> int:
    config = getattr(context, "user_config", None)
    configured = normalize_work_overlay_max_items(
        getattr(config, "work_overlay_max_items", ACTIVE_WORK_ITEM_LIMIT),
        ACTIVE_WORK_ITEM_LIMIT,
    )
    if configured <= 0:
        return 0
    return min(configured, _work_overlay_screen_max_items())


def _work_overlay_side_for_context(context: object) -> str:
    config = getattr(context, "user_config", None)
    return normalize_work_overlay_side(
        getattr(config, "work_overlay_side", DEFAULT_WORK_OVERLAY_SIDE)
    )


def _work_overlay_runtime_task_key(item: WorkStatusItem) -> str:
    return runtime_task_key(item)


def _work_overlay_seen_task_keys(context: object) -> set[str]:
    seen = getattr(context, "_work_overlay_seen_task_keys", None)
    if isinstance(seen, set):
        return seen
    seen = set()
    try:
        setattr(context, "_work_overlay_seen_task_keys", seen)
    except Exception:
        pass
    return seen


def _work_overlay_visible_item_cache(context: object) -> dict[str, WorkStatusItem]:
    cache = getattr(context, "_work_overlay_visible_item_cache", None)
    if isinstance(cache, dict):
        return cache
    cache = {}
    try:
        setattr(context, "_work_overlay_visible_item_cache", cache)
    except Exception:
        pass
    return cache


def _work_overlay_published_item_cache(context: object) -> dict[str, WorkStatusItem]:
    cache = getattr(context, "_work_overlay_published_item_cache", None)
    if isinstance(cache, dict):
        return cache
    cache = {}
    try:
        setattr(context, "_work_overlay_published_item_cache", cache)
    except Exception:
        pass
    return cache


def _work_overlay_terminal_item_tasks(context: object) -> dict[str, str]:
    terminal = getattr(context, "_work_overlay_terminal_item_tasks", None)
    if isinstance(terminal, dict):
        return terminal
    terminal = {}
    try:
        setattr(context, "_work_overlay_terminal_item_tasks", terminal)
    except Exception:
        pass
    return terminal


def _work_overlay_item_sort_key(item: WorkStatusItem) -> tuple[float, float]:
    return item_sort_key(item)


def _work_overlay_item_updated_seconds(item: WorkStatusItem) -> float:
    return item_updated_seconds(item)


def _stabilize_published_work_overlay_items(
    context: object,
    items: Sequence[WorkStatusItem],
) -> list[WorkStatusItem]:
    item_limit = _work_overlay_item_limit_for_context(context)
    cache = _work_overlay_published_item_cache(context)
    terminal = _work_overlay_terminal_item_tasks(context)
    from .active_work import _effective_notification_provider_scope

    provider_scope = _effective_notification_provider_scope(context, None)
    return stabilize_published_items(
        list(items),
        item_limit=item_limit,
        cache=cache,
        terminal_tasks=terminal,
        provider_scope=provider_scope,
        now=datetime.now().astimezone(),
        stale_seconds=ACTIVE_WORK_STALE_SECONDS,
    )


def _select_runtime_work_overlay_items(
    context: object,
    items: Sequence[WorkStatusItem],
    *,
    item_limit: int,
) -> list[WorkStatusItem]:
    seen_task_keys = _work_overlay_seen_task_keys(context)
    return select_visible_items(
        list(items),
        item_limit=item_limit,
        seen_task_keys=seen_task_keys,
        now=datetime.now().astimezone(),
        stale_seconds=WORK_OVERLAY_STALE_SECONDS,
        runtime_started_at=getattr(context, "work_overlay_started_at", None),
    )
