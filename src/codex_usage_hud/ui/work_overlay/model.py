"""Qt-free work-overlay item normalization and projection rules."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, MutableMapping, Sequence
from datetime import datetime
from typing import Any

from ... import overlay_projection
from ...core import parse_timestamp
from .constants import (
    WORK_OVERLAY_BODY_MAX_LINES,
    WORK_OVERLAY_FEED_SPINNER_ENABLED,
    WORK_OVERLAY_HOTSPOT_HOVER_ALPHA,
)

def _compact_work_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."

def _compact_workdir_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return "..." + text[-max(0, limit - 3) :]

def _normalized_system_action(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    action_id = str(value.get("id") or "").strip()
    action = str(value.get("action") or "").strip()
    title = str(value.get("title") or "").strip()
    label = str(value.get("label") or "").strip()
    if not action_id or not action or not title or not label:
        return None
    return {
        "id": action_id,
        "action": action,
        "title": title,
        "message": str(value.get("message") or "").strip(),
        "label": label,
        "persistent": bool(value.get("persistent")),
    }

def _system_action_overlay_item(action: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": str(action.get("id") or ""),
        "title": str(action.get("title") or ""),
        "status": "warning",
        "statusLabel": str(action.get("label") or ""),
        "statusText": str(action.get("label") or ""),
        "lastText": str(action.get("message") or ""),
        "elapsedText": "",
        "systemAction": True,
        "action": str(action.get("action") or ""),
    }

def _item_is_system_action(item: Mapping[str, object]) -> bool:
    return bool(item.get("systemAction")) and bool(str(item.get("action") or "").strip())

def _normalized_system_notice(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    notice_id = str(value.get("id") or "").strip()
    title = str(value.get("title") or "").strip()
    message = str(value.get("message") or "").strip()
    if not notice_id or not title or not message:
        return None
    status = str(value.get("status") or "warning").strip() or "warning"
    return {
        "id": notice_id,
        "title": title,
        "message": message,
        "status": status,
        "persistent": bool(value.get("persistent")),
        "dismissible": bool(value.get("dismissible")),
        "instanceId": str(value.get("instanceId") or ""),
    }

def _system_notice_overlay_item(notice: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": str(notice.get("id") or ""),
        "title": str(notice.get("title") or ""),
        "status": str(notice.get("status") or "warning"),
        "statusLabel": "等待恢复" if notice.get("dismissible") else "请稍候",
        "statusText": "等待恢复" if notice.get("dismissible") else "请稍候",
        "lastText": str(notice.get("message") or ""),
        "elapsedText": "",
        "systemNotice": True,
        "dismissible": bool(notice.get("dismissible")),
        "taskStartedAt": str(notice.get("instanceId") or ""),
    }

def _item_is_system_notice(item: Mapping[str, object]) -> bool:
    return bool(item.get("systemNotice"))

def _normalized_rest_reminder(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping) or not bool(value.get("bubbleVisible")):
        return None
    phase = str(value.get("phase") or "").strip().lower()
    if phase not in {"prompt", "postponed", "resting", "completed", "preview"}:
        return None
    return {
        "bubbleVisible": True,
        "phase": phase,
        "message": str(value.get("message") or "").strip(),
        "canPostpone": bool(value.get("canPostpone")),
        "intervalMinutes": max(1, int(value.get("intervalMinutes") or 45)),
        "breakMinutes": max(1, int(value.get("breakMinutes") or 2)),
        "postponeMinutes": max(1, int(value.get("postponeMinutes") or 10)),
        "promptEndsAtMs": max(0, int(value.get("promptEndsAtMs") or 0)),
        "promptStartedAtMs": max(0, int(value.get("promptStartedAtMs") or 0)),
        "promptWaitInfinite": bool(value.get("promptWaitInfinite")),
        "earlyRestOptionsMinutes": [3, 5, 10],
        "postponeEndsAtMs": max(0, int(value.get("postponeEndsAtMs") or 0)),
        "restStartedAtMs": max(0, int(value.get("restStartedAtMs") or 0)),
        "restEndsAtMs": max(0, int(value.get("restEndsAtMs") or 0)),
        "todayRestedSeconds": max(0, int(value.get("todayRestedSeconds") or 0)),
        "completedTodaySeconds": max(
            0, int(value.get("completedTodaySeconds") or 0)
        ),
        "lastRestDurationSeconds": max(
            0, int(value.get("lastRestDurationSeconds") or 0)
        ),
        "completionEndsAtMs": max(0, int(value.get("completionEndsAtMs") or 0)),
    }

def _item_is_rest_reminder(item: Mapping[str, object]) -> bool:
    return str(item.get("kind") or "").strip() == "rest_reminder"

def _format_rest_duration(value: object) -> str:
    try:
        total = max(0, int(round(float(value))))
    except (TypeError, ValueError, OverflowError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"

def _rest_credit_actions() -> list[dict[str, object]]:
    return [
        *(
            {
                "action": "restReminderCredit",
                "label": f"{minutes}分钟",
                "minutes": minutes,
                "primary": False,
            }
            for minutes in (3, 5, 10)
        ),
        {"action": "restReminderCreditMore", "label": "更多", "primary": False},
    ]


def _rest_reminder_card_copy(
    item: Mapping[str, object],
    *,
    now_ms: int | None = None,
) -> dict[str, object]:
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    phase = str(item.get("phase") or "").strip().lower()
    completed_today = max(0, int(item.get("completedTodaySeconds") or 0))
    message = str(item.get("message") or "").strip() or "该休息一下了。"
    title = "☕ 休息提醒"
    detail = message
    hint = ""
    status = ""
    header_meta = ""
    header_elapsed = ""
    color_status = "waiting_user"
    actions: list[dict[str, object]] = []
    if phase == "prompt":
        title = "☕ 该休息一下了"
        hint = "如果您已提前休息过了，可以点击下方分钟数按钮标记已休息"
        header_meta = f"今日已休息 {_format_rest_duration(completed_today)}"
        prompt_started_ms = int(item.get("promptStartedAtMs") or 0)
        prompt_elapsed = (
            max(0.0, (current_ms - prompt_started_ms) / 1000.0)
            if prompt_started_ms > 0
            else 0.0
        )
        header_elapsed = f"已等待 {_format_rest_duration(prompt_elapsed)}"
        status = "等待你的选择 · 不会自动跳过"
        if bool(item.get("canPostpone")):
            minutes = max(1, int(item.get("postponeMinutes") or 10))
            actions.append(
                {
                    "action": "restReminderPostpone",
                    "label": f"延迟 {minutes} 分钟",
                    "primary": False,
                }
            )
        actions.extend(_rest_credit_actions())
        actions.append({"action": "restReminderStart", "label": "开始休息", "primary": True})
    elif phase == "postponed":
        remaining = max(
            0.0, (int(item.get("postponeEndsAtMs") or 0) - current_ms) / 1000.0
        )
        title = "☕ 休息已延迟"
        header_meta = f"今日已休息 {_format_rest_duration(completed_today)}"
        detail = f"{_format_rest_duration(remaining)} 后再次提醒"
        hint = "如果您已经休息过了，可以点击分钟数按钮标记已休息"
        status = "延迟不计入休息"
        color_status = "tool"
        actions.extend(_rest_credit_actions())
        actions.append(
            {"action": "restReminderStart", "label": "开始休息", "primary": True}
        )
    elif phase == "resting":
        started_ms = int(item.get("restStartedAtMs") or 0)
        ends_ms = int(item.get("restEndsAtMs") or 0)
        elapsed = max(0.0, (min(current_ms, ends_ms or current_ms) - started_ms) / 1000.0)
        today = max(
            completed_today + elapsed,
            int(item.get("todayRestedSeconds") or 0),
        )
        target = max(1, int(item.get("breakMinutes") or 2)) * 60
        title = "☕ 正在休息"
        header_meta = f"今日已休息 {_format_rest_duration(today)}"
        detail = f"本次已休息 {_format_rest_duration(elapsed)}"
        hint = "实际休息比计时更长？点击分钟数按钮按实际时长记录并结束"
        status = f"目标 {_format_rest_duration(target)}"
        color_status = "running"
        actions.extend(_rest_credit_actions())
        actions.append(
            {"action": "restReminderFinish", "label": "提前结束", "primary": True}
        )
    elif phase == "completed":
        duration = max(0, int(item.get("lastRestDurationSeconds") or 0))
        title = "✓ 休息完成"
        header_meta = f"今日已休息 {_format_rest_duration(completed_today)}"
        detail = f"本次休息 {_format_rest_duration(duration)}"
        status = "新一轮专注已开始"
        color_status = "rest_completed"
    elif phase == "preview":
        title = "测试预览"
        detail = message
        status = "不会改变当前计时，也不会计入今日休息"
        color_status = "tool"
        actions.append(
            {"action": "restReminderAck", "label": "关闭预览", "primary": True}
        )
    return {
        "title": title,
        "headerMeta": header_meta,
        "headerElapsed": header_elapsed,
        "detail": detail,
        "hint": hint,
        "statusText": status,
        "status": color_status,
        "actions": actions,
    }

def _rest_reminder_overlay_item(reminder: Mapping[str, object]) -> dict[str, object]:
    normalized = _normalized_rest_reminder(reminder)
    if normalized is None:
        return {}
    copy = _rest_reminder_card_copy(normalized)
    return {
        "id": "rest-reminder",
        "kind": "rest_reminder",
        "phase": normalized["phase"],
        "message": normalized["message"],
        "canPostpone": normalized["canPostpone"],
        "intervalMinutes": normalized["intervalMinutes"],
        "breakMinutes": normalized["breakMinutes"],
        "postponeMinutes": normalized["postponeMinutes"],
        "promptEndsAtMs": normalized["promptEndsAtMs"],
        "promptStartedAtMs": normalized["promptStartedAtMs"],
        "promptWaitInfinite": normalized["promptWaitInfinite"],
        "earlyRestOptionsMinutes": normalized["earlyRestOptionsMinutes"],
        "postponeEndsAtMs": normalized["postponeEndsAtMs"],
        "restStartedAtMs": normalized["restStartedAtMs"],
        "restEndsAtMs": normalized["restEndsAtMs"],
        "todayRestedSeconds": normalized["todayRestedSeconds"],
        "completedTodaySeconds": normalized["completedTodaySeconds"],
        "lastRestDurationSeconds": normalized["lastRestDurationSeconds"],
        "completionEndsAtMs": normalized["completionEndsAtMs"],
        "title": copy["title"],
        "headerMeta": copy["headerMeta"],
        "headerElapsed": copy["headerElapsed"],
        "status": copy["status"],
        "statusLabel": copy["statusText"],
        "statusText": copy["statusText"],
        "lastText": copy["detail"],
        "restHint": copy["hint"],
        "elapsedText": "",
        "restActions": copy["actions"],
    }

def _workdir_parts(value: object) -> list[str]:
    text = str(value or "").strip().rstrip("\\/")
    if not text:
        return []
    return [part for part in text.replace("/", "\\").split("\\") if part]

def _workdir_leaf(value: object) -> str:
    parts = _workdir_parts(value)
    return parts[-1] if parts else ""

def _item_is_background_usage(item: Mapping[str, object]) -> bool:
    return (
        str(item.get("kind") or "").strip() == "background_usage"
        or str(item.get("status") or "").strip() == "background_usage"
    )

def _workdir_display_name(item: Mapping[str, object]) -> str:
    if _item_is_background_usage(item):
        return str(item.get("workdirName") or "查看后台用量记录").strip()
    return _workdir_leaf(item.get("workdir")) or _workdir_leaf(item.get("workdirName"))

def _item_is_completed(item: Mapping[str, object]) -> bool:
    if _item_is_background_usage(item):
        return False
    return str(item.get("status") or "") == "recent"


OverlayRect = tuple[float, float, float, float]

def _item_id(item: Mapping[str, object]) -> str:
    return str(item.get("id") or "").strip()


def _overlay_session_identity(item: Mapping[str, object]) -> str:
    """Return the stable identity used by the current-session treatment."""
    if (
        _item_is_background_usage(item)
        or _item_is_rest_reminder(item)
        or _item_is_system_action(item)
        or _item_is_system_notice(item)
    ):
        return ""
    return str(item.get("sessionId") or item.get("id") or "").strip()


def _next_stable_current_session_id(
    items: Sequence[Mapping[str, object]],
    previous: object = "",
) -> str:
    """Keep the selected card marked through partial same-session refreshes."""
    for item in items:
        identity = _overlay_session_identity(item)
        if identity and bool(item.get("current")) and not _item_is_completed(item):
            return identity
    prior = str(previous or "").strip()
    if prior and any(_overlay_session_identity(item) == prior for item in items):
        return prior
    return ""


def _overlay_item_is_stably_current(
    item: Mapping[str, object],
    current_session_id: object = "",
) -> bool:
    identity = _overlay_session_identity(item)
    stable = str(current_session_id or "").strip()
    if stable:
        return bool(identity and identity == stable and not _item_is_completed(item))
    return bool(item.get("current")) and bool(identity) and not _item_is_completed(item)

def _switch_item_key(item: Mapping[str, object]) -> str:
    session_id = str(item.get("sessionId") or item.get("id") or "").strip()
    title = str(item.get("targetTitle") or item.get("title") or "").strip()
    workdir = str(item.get("workdir") or "").strip()
    if not session_id and not title and not workdir:
        return ""
    return json.dumps(
        {
            "sessionId": session_id,
            "title": title,
            "workdir": workdir,
        },
        ensure_ascii=False,
        sort_keys=True,
    )

def _item_kind(item: Mapping[str, object]) -> str:
    return "completed" if _item_is_completed(item) else "card"

def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))

def _interactive_hotspot_opacity(
    base_opacity: float,
    hovered: bool,
    *,
    invisible_hit_surface: bool = False,
) -> float:
    if hovered:
        return WORK_OVERLAY_HOTSPOT_HOVER_ALPHA
    if invisible_hit_surface:
        # Windows performs per-pixel hit testing for these layered top-level
        # windows. Keep their global opacity at 1 so the intentionally faint
        # painted pixels never round down to fully transparent while the parent
        # bubble fades under the pointer.
        return 1.0
    return _clamp01(base_opacity)

def _matched_overlay_item_records(
    records: Sequence[dict[str, Any]],
    items: Sequence[Mapping[str, object]],
) -> list[tuple[dict[str, Any], Mapping[str, object]]]:
    """Match grouped widget records to payload items by shape and stable ID."""
    unused_records = list(records)
    matched: list[tuple[dict[str, Any], Mapping[str, object]]] = []
    for item in items:
        item_id = _item_id(item)
        kind = _item_kind(item)
        record = next(
            (
                candidate
                for candidate in unused_records
                if str(candidate.get("kind") or "") == kind
                and (
                    not item_id
                    or str(candidate.get("item_id") or "") == item_id
                )
            ),
            None,
        )
        if record is None:
            continue
        unused_records.remove(record)
        matched.append((record, item))
    return matched

def _workdir_link_pending_for_item(
    item: Mapping[str, object],
    pending: bool,
) -> bool:
    del item, pending
    return False

def _item_is_cli(item: Mapping[str, object]) -> bool:
    return str(item.get("clientKind") or "").strip().lower() == "cli"

def _profile_display_name(item: Mapping[str, object]) -> str:
    """Return the profile label only for CLI session bubbles."""
    if not _item_is_cli(item):
        return ""
    profile = _compact_work_text(
        item.get("profileName")
        or item.get("profile_name")
        or item.get("modelProvider"),
        24,
    )
    if profile.lower() in {"unknown", "n/a"}:
        return ""
    return profile

def _workdir_footer_display_name(
    item: Mapping[str, object],
    *,
    limit: int = 40,
) -> str:
    """Return the existing footer workdir text with a CLI profile prefix."""
    workdir = _workdir_display_name(item)
    profile = _profile_display_name(item)
    if not profile:
        return _compact_workdir_text(workdir, limit)
    profile_label = f"[{profile}]"
    if not workdir:
        return profile_label
    remaining = max(8, limit - len(profile_label) - 1)
    return f"{profile_label} {_compact_workdir_text(workdir, remaining)}"


def _overlay_activity_steps(item: Mapping[str, object]) -> list[Mapping[str, object]]:
    value = item.get("activitySteps") or item.get("activity_steps") or ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [step for step in value if isinstance(step, Mapping)]


def _overlay_activity_step_prefix(step: Mapping[str, object]) -> str:
    title = str(step.get("title") or "").strip()
    status = str(step.get("status") or "").strip().lower()
    tool_name = str(step.get("toolName") or step.get("tool_name") or "").strip().lower()
    normalized_name = tool_name.replace(".", "_").replace("-", "_")
    if status in {"failed", "error"} or title == "命令失败":
        return "失败"
    if (
        title in {"执行命令", "命令完成"}
        or normalized_name.endswith(("exec", "shell", "shell_command"))
        or normalized_name in {"functions_exec", "unified_exec"}
    ):
        return "命令"
    if any(token in normalized_name for token in ("read", "open", "cat", "view_file")):
        return "读文件"
    if any(token in normalized_name for token in ("edit", "write", "patch", "apply")):
        return "编辑"
    if "request_user_input" in normalized_name:
        return "等确认"
    return title or "调用工具"


def _overlay_activity_step_text(step: Mapping[str, object]) -> str:
    prefix = _overlay_activity_step_prefix(step)
    detail = " ".join(str(step.get("detail") or "").split())
    output = " ".join(str(step.get("output") or "").split())
    if output and output != detail:
        detail = f"{detail} · {output}" if detail else output
    return f"{prefix}：{detail}" if detail else prefix


def _overlay_activity_step_texts(item: Mapping[str, object]) -> list[str]:
    return [
        text
        for text in (_overlay_activity_step_text(step) for step in _overlay_activity_steps(item))
        if text
    ]


def _overlay_activity_tooltip(item: Mapping[str, object]) -> str:
    return "\n".join(_overlay_activity_step_texts(item))


_FEED_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_FEED_ACTIVE_STATUSES = frozenset({"running", "active", "tool"})


def _overlay_feed_spinner_frame(tick: int) -> str:
    """Return the braille spinner glyph for the running feed rows."""
    if not WORK_OVERLAY_FEED_SPINNER_ENABLED:
        return ""
    return _FEED_SPINNER_FRAMES[max(0, int(tick)) % len(_FEED_SPINNER_FRAMES)]


def _overlay_step_elapsed_seconds(
    step: Mapping[str, object],
    now: datetime,
) -> int:
    """Local seconds since a step started; execution windows are event-silent."""
    started_at = parse_timestamp(str(step.get("timestamp") or "").strip())
    if started_at is None:
        return 0
    if started_at.tzinfo is None:
        current = now.replace(tzinfo=None)
    else:
        current = now.astimezone(started_at.tzinfo)
    return max(0, int((current - started_at).total_seconds()))


def _overlay_output_is_stale(item: Mapping[str, object]) -> bool:
    """Whether no fresh agent output arrived after the current task started."""
    task_started_at = parse_timestamp(
        str(item.get("taskStartedAt") or "").strip()
    )
    if task_started_at is None:
        return False
    last_output_at = parse_timestamp(str(item.get("lastOutputAt") or "").strip())
    if last_output_at is None:
        return True
    return last_output_at < task_started_at


def _overlay_feed_active(item: Mapping[str, object]) -> bool:
    """Whether the bubble body should show the live activity feed.

    Execution windows emit zero rollout events, so the feed is the only
    surface that can show local progress; the output view returns as soon as
    a fresh agent message lands and no step is left open.

    Rest-reminder cards never participate in the session activity feed: their
    ``waiting_user`` status must not be mistaken for a Codex turn waiting on
    the user, which would otherwise inject a "思考中" row and an "展开"
    affordance into the reminder bubble.
    """
    if _item_is_rest_reminder(item):
        return False
    status = str(item.get("status") or "").strip().lower()
    steps = _overlay_activity_steps(item)
    if any(
        str(step.get("status") or "").strip().lower() == "running" for step in steps
    ):
        return True
    if status == "waiting_user":
        return True
    if status in _FEED_ACTIVE_STATUSES:
        if steps and str(steps[-1].get("status") or "").strip().lower() == "failed":
            # 失败滞留：hold the feed so the failure stays visible until the
            # next output arrives.
            return True
        if not str(item.get("lastText") or "").strip():
            return True
        # A collapsed Codex tool group remains the active body even during the
        # short `tool -> active` gaps between commands.  Without this bridge,
        # the bubble alternates between the purple execution view and the
        # stale assistant-output view while the group is still running.
        if _overlay_execution_body_active(item):
            return True
        return _overlay_output_is_stale(item)
    return False


def _overlay_feed_tail_line(step: Mapping[str, object]) -> str:
    tail = str(step.get("activeTail") or "")
    line = ""
    for candidate in tail.splitlines():
        candidate = candidate.strip()
        if candidate:
            line = candidate
    return line


def _overlay_feed_summary_row(rows: Sequence[Mapping[str, str]]) -> Mapping[str, str] | None:
    """Return the one-line execution summary used beside persistent output."""
    for row in reversed(list(rows)):
        if str(row.get("kind") or "") in {"live", "wait", "fail", "failed", "done"}:
            return row
    return rows[-1] if rows else None


def _overlay_execution_group_steps(
    item: Mapping[str, object],
) -> list[Mapping[str, object]]:
    """Return action steps belonging to the current collapsed execution block.

    The Codex desktop groups the tool calls that follow an assistant output into
    one disclosure.  The JSONL feed does not expose that disclosure node, so the
    HUD reconstructs the same boundary from the newest output timestamp and the
    ordered action steps.  Reasoning-only rows are intentionally excluded: the
    group contains the commands/tools a user can act on.
    """
    steps = _overlay_activity_steps(item)
    if not steps:
        return []

    def is_action(step: Mapping[str, object]) -> bool:
        title = str(step.get("title") or "").strip()
        tool_name = str(step.get("toolName") or step.get("tool_name") or "").strip()
        detail = " ".join(str(step.get("detail") or "").split())
        return bool(detail or tool_name) and not (title == "思考" and not tool_name)

    if bool(item.get("collapsedDisclosureAmbiguous")):
        # The JSONL timeline cannot identify which historical actions belong to
        # one of multiple native disclosures. A unique latest running action
        # is still safe to expose for the live title/body; never mix completed
        # rows from the ambiguous history into that minimal view.
        running_steps = [
            step
            for step in steps
            if is_action(step)
            and str(step.get("status") or "").strip().lower() == "running"
        ]
        return running_steps[-1:]

    last_output_at = parse_timestamp(str(item.get("lastOutputAt") or "").strip())

    candidates: list[Mapping[str, object]] = []
    for step in steps:
        if not is_action(step):
            continue
        timestamp = parse_timestamp(str(step.get("timestamp") or "").strip())
        if last_output_at is not None and timestamp is not None:
            try:
                if timestamp <= last_output_at:
                    continue
            except TypeError:
                if timestamp.replace(tzinfo=None) <= last_output_at.replace(tzinfo=None):
                    continue
        candidates.append(step)

    # Some legacy records omit timestamps. Keep the newest action window in
    # that case instead of accidentally presenting the entire task history.
    if not candidates:
        candidates = [step for step in steps if is_action(step)][-16:]
    return candidates[-16:]


def _overlay_execution_body_active(item: Mapping[str, object]) -> bool:
    """Whether the body should show commands instead of stale assistant text."""
    steps = _overlay_execution_group_steps(item)
    if not steps:
        return False
    if any(str(step.get("status") or "").strip().lower() == "running" for step in steps):
        return True
    first_timestamp = next(
        (
            timestamp
            for timestamp in (
                parse_timestamp(str(step.get("timestamp") or "").strip())
                for step in steps
            )
            if timestamp is not None
        ),
        None,
    )
    last_output_at = parse_timestamp(str(item.get("lastOutputAt") or "").strip())
    if first_timestamp is None:
        return False
    if last_output_at is None:
        return True
    try:
        return last_output_at < first_timestamp
    except TypeError:
        return last_output_at.replace(tzinfo=None) < first_timestamp.replace(tzinfo=None)


def _overlay_mcp_provider_label(tool_name: str) -> str:
    normalized = str(tool_name or "").strip().lower().replace("-", "_")
    parts = normalized.split("__")
    if len(parts) < 3 or parts[0] != "mcp":
        return ""
    words = []
    for word in parts[1].split("_"):
        if not word:
            continue
        words.append("MCP" if word == "mcp" else word.capitalize())
    return " ".join(words)


def _overlay_execution_group_title(item: Mapping[str, object]) -> str:
    """Build the short title shown in the bubble footer for a tool group."""
    steps = _overlay_execution_group_steps(item)
    if bool(item.get("collapsedDisclosureAmbiguous")):
        return _overlay_running_execution_title(item)
    execution_active = bool(steps) and _overlay_execution_body_active(item)
    elapsed_title_seen = False
    for key in (
        "nativeCollapsedTitle",
        "executionGroupTitle",
        "collapsedTitle",
        "activityGroupTitle",
        "groupTitle",
    ):
        explicit = " ".join(str(item.get(key) or "").split())
        if explicit and not _overlay_title_is_elapsed(explicit):
            return explicit
        if explicit:
            elapsed_title_seen = True
    if elapsed_title_seen and execution_active:
        # Codex exposes the user-message duration button as the collapsed
        # title while the actual tool group is active. Prefer the current
        # action kind in that narrow case so "用时 ..." cannot mask editing.
        state_title = _overlay_running_execution_title(item)
        if state_title:
            return state_title
    if len(steps) < 2:
        return ""
    command_steps: list[Mapping[str, object]] = []
    edit_steps: list[Mapping[str, object]] = []
    read_steps: list[Mapping[str, object]] = []
    wait_steps: list[Mapping[str, object]] = []
    providers: list[str] = []
    for step in steps:
        title = str(step.get("title") or "").strip()
        tool_name = str(step.get("toolName") or step.get("tool_name") or "").strip()
        normalized = tool_name.lower().replace(".", "_").replace("-", "_")
        prefix = _overlay_activity_step_prefix(step)
        provider = _overlay_mcp_provider_label(tool_name)
        if provider and provider not in providers:
            providers.append(provider)
        if "request_user_input" in normalized or prefix == "等确认":
            wait_steps.append(step)
        if (
            prefix == "命令"
            or title in {"执行命令", "命令完成", "命令失败"}
            or normalized.endswith(("exec", "shell", "shell_command"))
        ):
            command_steps.append(step)
        if (
            prefix == "编辑"
            or title == "编辑文件"
            or any(token in normalized for token in ("edit", "write", "patch", "apply"))
        ):
            edit_steps.append(step)
        if (
            prefix == "读文件"
            or any(token in normalized for token in ("read", "open", "cat", "view_file"))
        ):
            read_steps.append(step)

    fragments: list[str] = [f"已使用 {provider}" for provider in providers]
    if edit_steps:
        fragments.append("集成或编辑了多个文件" if len(edit_steps) > 1 else "编辑了文件")
    if read_steps:
        fragments.append("读取了文件")
    if command_steps:
        fragments.append("运行了命令")
    if wait_steps:
        fragments.append("等待确认")
    if fragments:
        # The native Codex disclosure uses a sentence-like heading without
        # separators. Spaces keep the title readable while allowing the
        # footer's existing ellipsis to preserve its beginning.
        return " ".join(fragments)
    return "执行了一组工具"


def _overlay_title_is_elapsed(value: object) -> bool:
    """Whether a native disclosure value is only a duration summary."""
    text = " ".join(str(value or "").split()).casefold()
    return text.startswith(
        (
            "已处理",
            "处理了",
            "用时",
            "耗时",
            "历时",
            "worked for",
            "worked",
            "duration",
            "processed",
            "took",
        )
    )


def _overlay_title_is_active(value: object) -> bool:
    """Whether a native disclosure value describes live execution."""
    text = " ".join(str(value or "").split()).casefold()
    return text.startswith(
        (
            "正在",
            "等待",
            "working",
            "editing",
            "running",
            "reading",
            "executing",
            "calling",
        )
    )


def _overlay_running_execution_title(item: Mapping[str, object]) -> str:
    """Return the current action-kind title for a running tool step."""
    for step in reversed(_overlay_execution_group_steps(item)):
        if str(step.get("status") or "").strip().lower() != "running":
            continue
        prefix = _overlay_activity_step_prefix(step)
        if prefix == "编辑":
            return "正在编辑文件"
        if prefix == "命令":
            return "正在执行命令"
        if prefix == "读文件":
            return "正在读取文件"
        if prefix == "等确认":
            return "等待确认"
        if prefix:
            return "正在执行操作"
    return ""


def _overlay_execution_rows(
    item: Mapping[str, object],
    *,
    max_rows: int = WORK_OVERLAY_BODY_MAX_LINES,
) -> list[dict[str, str]]:
    """Render the latest commands/tools inside the current execution group."""
    rows: list[dict[str, str]] = []
    for step in _overlay_execution_group_steps(item):
        title = str(step.get("title") or "").strip()
        status = str(step.get("status") or "").strip().lower()
        tool_name = str(step.get("toolName") or step.get("tool_name") or "").strip()
        detail = " ".join(str(step.get("detail") or "").split()) or tool_name
        prefix = _overlay_activity_step_prefix(step)
        output = " ".join(str(step.get("output") or "").split())
        command_raw = str(step.get("commandRaw") or "").strip() or detail
        if prefix == "命令":
            if status == "running":
                text = f"$ {detail}".strip()
                action = "copy_command"
            elif status in {"failed", "error"}:
                text = f"✗ {detail}".strip()
                action = "peek_output"
            else:
                text = f"✓ {detail}".strip()
                action = "peek_output"
            duration = str(step.get("durationText") or "").strip()
            if duration and status != "running":
                text = f"{text} · {duration}"
        elif prefix == "编辑":
            text = f"✎ {detail}".strip()
            if output and output != detail:
                suffix = output.removeprefix(detail).strip(" ·")
                if suffix:
                    text = f"{text} · {suffix}"
            action = "peek_output"
        elif prefix == "读文件":
            text = f"▣ {detail}".strip()
            action = "peek_output"
        elif prefix == "等确认":
            text = f"⏸ {detail}".strip()
            action = "peek_output"
        else:
            text = f"• {detail}".strip()
            action = "peek_output"
        if status in {"failed", "error"} and step.get("exitCode") not in (None, ""):
            text = f"{text} · 退出码 {step.get('exitCode')}"
        rows.append(
            {
                "kind": "execution",
                "mode": "execution",
                "text": text,
                "tooltip": (
                    f"完整命令（点击复制）：{command_raw}"
                    if action == "copy_command"
                    else output
                ),
                "action": action,
                "command": command_raw if action == "copy_command" else "",
            }
        )
    return rows[-max(1, int(max_rows)) :]


def _overlay_execution_live_summary(item: Mapping[str, object]) -> str:
    """Return the single live line shown beside the scheme-B expand button."""
    steps = _overlay_execution_group_steps(item)
    if not steps:
        return "执行中"
    running = next(
        (
            step
            for step in reversed(steps)
            if str(step.get("status") or "").strip().lower() == "running"
        ),
        None,
    )
    if running is not None:
        detail = " ".join(str(running.get("detail") or "").split())
        elapsed = _overlay_step_elapsed_seconds(running, datetime.now().astimezone())
        return f"运行中 {elapsed}s" + (f" · {detail}" if detail else "")
    latest_rows = _overlay_execution_rows(item, max_rows=1)
    if latest_rows:
        return str(latest_rows[-1].get("text") or "执行中")
    failed = sum(
        1
        for step in steps
        if str(step.get("status") or "").strip().lower() in {"failed", "error"}
    )
    completed = sum(
        1
        for step in steps
        if str(step.get("status") or "").strip().lower() == "completed"
    )
    if failed:
        return f"执行结果 · 完成 {completed} · 失败 {failed}"
    return f"执行中 · 已完成 {completed} 项"


def _overlay_feed_rows(
    item: Mapping[str, object],
    *,
    spinner_frame: str = "⠙",
    now: datetime | None = None,
    max_rows: int = WORK_OVERLAY_BODY_MAX_LINES,
) -> list[dict[str, str]]:
    """Build the last few activity feed rows for an active bubble body.

    Row shapes: ``✓/✗`` finished actions, ``▸`` reasoning titles, ``$``
    commands, ``└`` completed-output tail lines, ``⠙`` live local timing.
    """
    if not _overlay_feed_active(item):
        return []
    steps = _overlay_activity_steps(item)
    status = str(item.get("status") or "").strip().lower()
    current = now or datetime.now().astimezone()
    spinner = f"{spinner_frame} " if spinner_frame else ""
    rows: list[dict[str, str]] = []
    running_seen = False
    for step in steps:
        step_status = str(step.get("status") or "").strip().lower()
        title = str(step.get("title") or "").strip()
        detail = " ".join(str(step.get("detail") or "").split())
        prefix = _overlay_activity_step_prefix(step)
        command_raw = str(step.get("commandRaw") or "").strip() or detail
        if step_status == "running":
            running_seen = True
            if prefix == "等确认":
                rows.append(
                    {
                        "kind": "wait",
                        "text": f"⏸ 等确认 · {detail}".strip(" ·"),
                        "tooltip": command_raw,
                        "action": "peek_output",
                        "command": "",
                    }
                )
                continue
            elapsed = _overlay_step_elapsed_seconds(step, current)
            rows.append(
                {
                    "kind": "cmd",
                    "text": f"$ {detail}".strip(),
                    "tooltip": f"完整命令（点击复制）：{command_raw}" if command_raw else "",
                    "action": "copy_command",
                    "command": command_raw,
                }
            )
            rows.append(
                {
                    "kind": "live",
                    "text": f"{spinner}运行中 {elapsed}s · 回输出",
                    "tooltip": "点击回到输出视图",
                    "action": "peek_output",
                    "command": "",
                }
            )
            continue
        if title == "思考":
            rows.append(
                {
                    "kind": "title",
                    "text": f"▸ {detail}".strip(),
                    "tooltip": detail,
                    "action": "peek_output",
                    "command": "",
                }
            )
            continue
        if step_status == "failed":
            exit_code = step.get("exitCode")
            text = f"✗ {prefix}：{detail}".strip("：")
            if exit_code not in (None, ""):
                text = f"{text} · 退出码 {exit_code}"
            rows.append(
                {
                    "kind": "fail",
                    "text": text,
                    "tooltip": " ".join(str(step.get("output") or "").split()),
                    "action": "peek_output",
                    "command": "",
                }
            )
        else:
            duration = str(step.get("durationText") or "").strip()
            text = f"✓ {prefix}：{detail}".strip("：")
            if duration:
                text = f"{text} · {duration}"
            rows.append(
                {
                    "kind": "done",
                    "text": text,
                    "tooltip": " ".join(str(step.get("output") or "").split()),
                    "action": "peek_output",
                    "command": "",
                }
            )
        tail_line = _overlay_feed_tail_line(step)
        if tail_line:
            rows.append(
                {
                    "kind": "out",
                    "text": f"└ {tail_line}",
                    "tooltip": str(step.get("activeTail") or ""),
                    "action": "peek_output",
                    "command": "",
                }
            )
    last_step = steps[-1] if steps else None
    if not running_seen and status in _FEED_ACTIVE_STATUSES | {"waiting_user"}:
        if last_step is None or str(last_step.get("status") or "").lower() != "failed":
            anchor_step = last_step or {}
            anchor_ts = parse_timestamp(
                str(anchor_step.get("timestamp") or "").strip()
            ) or parse_timestamp(
                str(item.get("taskStartedAt") or item.get("startedAt") or "").strip()
            )
            elapsed = 0
            if anchor_ts is not None:
                anchor = (
                    anchor_ts
                    if anchor_ts.tzinfo is not None
                    else anchor_ts
                )
                try:
                    elapsed = max(0, int((current - anchor).total_seconds()))
                except TypeError:
                    elapsed = 0
            rows.append(
                {
                    "kind": "live",
                    "text": f"{spinner}思考中 {elapsed}s…",
                    "tooltip": "",
                    "action": "peek_output",
                    "command": "",
                }
            )
    return rows[-max(1, int(max_rows)) :]


def _overlay_footer_selection(item: Mapping[str, object]) -> tuple[str, str]:
    """Pick the footer text by priority: running > 等确认 > 失败 > 思考 > 末条.

    Returns ``(kind, base_text)`` where kind drives the spinner/seconds suffix
    and the footer color; the base text carries no spinner or elapsed yet.
    """
    texts = _overlay_activity_step_texts(item)
    if not texts:
        return "", ""
    status = str(item.get("status") or "").strip().lower()
    if status not in _FEED_ACTIVE_STATUSES | {"waiting_user"}:
        return "plain", texts[-1]
    steps = _overlay_activity_steps(item)
    running = [
        step
        for step in steps
        if str(step.get("status") or "").strip().lower() == "running"
    ]
    if running:
        step = running[-1]
        if _overlay_activity_step_prefix(step) == "等确认":
            detail = " ".join(str(step.get("detail") or "").split())
            return "wait", f"等确认 · {detail}".strip(" ·")
        return "running", _overlay_activity_step_text(step)
    last = steps[-1] if steps else None
    if last is not None:
        if str(last.get("status") or "").strip().lower() == "failed":
            text = f"✗ {_overlay_activity_step_text(last)}"
            exit_code = last.get("exitCode")
            if exit_code not in (None, ""):
                text = f"{text} · 退出码 {exit_code}"
            return "failed", text
        if str(last.get("title") or "") == "思考":
            detail = " ".join(str(last.get("detail") or "").split())
            return "thinking", f"思考中 · {detail}".strip(" ·")
    return "plain", texts[-1]


def _workdir_clickable_for_item(item: Mapping[str, object]) -> bool:
    if _item_is_cli(item):
        return False
    if _item_is_background_usage(item):
        return bool(str(item.get("eventId") or item.get("id") or "").strip())
    workdir = str(item.get("workdir") or "").strip()
    session_id = str(item.get("sessionId") or item.get("id") or "").strip()
    target_title = str(item.get("targetTitle") or item.get("title") or "").strip()
    return bool(workdir and (session_id or target_title))

def _workdir_link_hover_visible_for_item(item: Mapping[str, object]) -> bool:
    return not _item_is_cli(item) and not _item_is_completed(item)

def _workdir_external_link_for_item(item: Mapping[str, object]) -> bool:
    return _workdir_clickable_for_item(item)

def _workdir_link_opacity_for_item(
    item: Mapping[str, object],
    base_opacity: float,
    hovered: bool,
) -> float:
    return _interactive_hotspot_opacity(
        base_opacity,
        hovered and _workdir_link_hover_visible_for_item(item),
        invisible_hit_surface=True,
    )

def _overlay_item_timestamp_seconds(
    item: Mapping[str, object],
    *keys: str,
) -> float:
    return overlay_projection.payload_timestamp_seconds(
        item,
        *keys,
        parse_timestamp=parse_timestamp,
    )

def _ordered_overlay_items(items: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return overlay_projection.order_payload_items(
        items,
        is_background=_item_is_background_usage,
        is_completed=_item_is_completed,
        parse_timestamp=parse_timestamp,
    )

def _work_overlay_header_text(
    started_at: datetime | str | None,
    elapsed_text: str,
    title: str,
    *,
    title_limit: int = 28,
) -> str:
    del started_at
    elapsed = str(elapsed_text or "").strip()
    # The Chinese label is the normal local UI copy, but the renderer can
    # surface the equivalent English label while Codex is in an English locale.
    # Keep the header to the elapsed value in either case.
    for prefix in ("已处理", "Worked for", "Worked", "Processed"):
        if elapsed.casefold().startswith(prefix.casefold()):
            elapsed = elapsed[len(prefix) :].lstrip(" ：:")
            break
    title_text = _compact_work_text(title, title_limit)
    parts = [part for part in (elapsed, title_text) if part]
    return " | ".join(parts) if parts else "Codex 工作"

def _work_overlay_live_elapsed_text(
    item: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> str | None:
    """Return local elapsed copy for an active session card, never terminal items."""
    status = str(item.get("status") or "").strip().lower()
    if (
        status not in {"running", "active", "tool"}
        or _item_is_completed(item)
        or _item_is_background_usage(item)
        or _item_is_rest_reminder(item)
        or _item_is_system_action(item)
        or _item_is_system_notice(item)
    ):
        return None
    started_at = parse_timestamp(
        str(item.get("taskStartedAt") or item.get("startedAt") or "").strip()
    )
    if started_at is None:
        return None
    current = now or datetime.now().astimezone()
    if started_at.tzinfo is None:
        current = current.replace(tzinfo=None)
    else:
        current = current.astimezone(started_at.tzinfo)
    seconds = max(0, int((current - started_at).total_seconds()))
    if seconds < 60:
        elapsed = f"{seconds}s"
    else:
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            elapsed = f"{minutes}m{seconds:02d}s"
        else:
            hours, minutes = divmod(minutes, 60)
            elapsed = f"{hours}h{minutes:02d}m{seconds:02d}s"
    return f"已处理 {elapsed}"

def _work_overlay_item_with_live_elapsed_text(
    item: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Normalize incoming active-card elapsed copy before each render."""
    normalized = dict(item)
    elapsed_text = _work_overlay_live_elapsed_text(normalized, now=now)
    if elapsed_text is not None:
        normalized["elapsedText"] = elapsed_text
    return normalized

def _item_dismiss_key(item: Mapping[str, object]) -> str:
    status = str(item.get("status") or "")
    error_text = str(item.get("statusText") or item.get("detail") or "") if status == "error" else ""
    dismissal_key: dict[str, object] = {
        "id": item.get("id"),
        "errorText": error_text,
        "status": "error" if status == "error" else "work",
        "taskStartedAt": item.get("taskStartedAt") or item.get("startedAt") or "",
    }
    user_steer_at = str(item.get("userSteerAt") or "").strip()
    if user_steer_at:
        dismissal_key["userSteerAt"] = user_steer_at
    return json.dumps(dismissal_key, ensure_ascii=False, sort_keys=True)

def _mark_item_dismissed(
    dismissed_instances: MutableMapping[str, str],
    item: Mapping[str, object],
) -> None:
    item_id = _item_id(item)
    if item_id:
        dismissed_instances[item_id] = _item_dismiss_key(item)

def _visible_overlay_items(
    items: Sequence[Mapping[str, object]],
    dismissed_instances: MutableMapping[str, str],
    *,
    item_limit: int,
) -> list[Mapping[str, object]]:
    return overlay_projection.visible_payload_items(
        items,
        dismissed_instances,
        item_limit=item_limit,
        dismiss_key=_item_dismiss_key,
    )

__all__ = [
    "_compact_work_text",
    "_compact_workdir_text",
    "_normalized_system_action",
    "_system_action_overlay_item",
    "_item_is_system_action",
    "_normalized_system_notice",
    "_system_notice_overlay_item",
    "_item_is_system_notice",
    "_normalized_rest_reminder",
    "_item_is_rest_reminder",
    "_format_rest_duration",
    "_rest_reminder_card_copy",
    "_rest_reminder_overlay_item",
    "_workdir_parts",
    "_workdir_leaf",
    "_item_is_background_usage",
    "_workdir_display_name",
    "_item_is_completed",
    "_item_id",
    "_overlay_session_identity",
    "_next_stable_current_session_id",
    "_overlay_item_is_stably_current",
    "_switch_item_key",
    "_item_kind",
    "_clamp01",
    "_interactive_hotspot_opacity",
    "_workdir_link_pending_for_item",
    "_item_is_cli",
    "_profile_display_name",
    "_workdir_footer_display_name",
    "_overlay_activity_steps",
    "_overlay_activity_step_texts",
    "_overlay_activity_tooltip",
    "_overlay_feed_active",
    "_overlay_feed_rows",
    "_overlay_execution_group_steps",
    "_overlay_execution_body_active",
    "_overlay_execution_group_title",
    "_overlay_execution_live_summary",
    "_overlay_execution_rows",
    "_overlay_feed_spinner_frame",
    "_overlay_feed_tail_line",
    "_overlay_footer_selection",
    "_overlay_output_is_stale",
    "_overlay_step_elapsed_seconds",
    "_workdir_clickable_for_item",
    "_workdir_link_hover_visible_for_item",
    "_workdir_external_link_for_item",
    "_workdir_link_opacity_for_item",
    "_matched_overlay_item_records",
    "_overlay_item_timestamp_seconds",
    "_ordered_overlay_items",
    "_work_overlay_header_text",
    "_work_overlay_live_elapsed_text",
    "_work_overlay_item_with_live_elapsed_text",
    "_item_dismiss_key",
    "_mark_item_dismissed",
    "_visible_overlay_items",
]
