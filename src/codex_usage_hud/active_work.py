"""Background refresh lifecycle for renderer active-work items."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
import json
import logging
from pathlib import Path
import re
import threading
from typing import Any, TYPE_CHECKING

from .core import ParsedSession, WorkStatusItem
from .overlay_projection import (
    _select_runtime_work_overlay_items,
    _work_overlay_item_limit_for_context,
    _work_overlay_item_sort_key,
    _work_overlay_runtime_task_key,
    _work_overlay_seen_task_keys,
    _merge_stable_item_metadata,
    _work_overlay_terminal_item_tasks,
    _work_overlay_visible_item_cache,
)
from .runtime_usage import (
    _current_task_cache_hit_text,
    _current_task_cost,
    _current_task_model_name,
    _current_task_round_index,
    _current_task_tokens,
    format_cost_compact as _format_cost_compact,
    format_tokens as _format_tokens,
    workdir_leaf as _workdir_leaf,
)
from .session_snapshots import session_path_key as _session_path_key
from .usage_contributions import usage_scan_roots

if TYPE_CHECKING:
    from .runtime_context import RuntimeContext


_LOGGER = logging.getLogger(__name__)

ActiveWorkBuilder = Callable[[object, object, Path | None, tuple[Path, ...]], list[Any]]

ACTIVE_WORK_CANDIDATE_LIMIT = 16
ACTIVE_WORK_STALE_SECONDS = 4 * 60 * 60
ACTIVE_WORK_MODEL_STARTUP_STALE_SECONDS = 90.0
# The renderer owns visible three-line truncation.  Keep enough source text
# here for that layout decision while still bounding the state-file payload.
ACTIVE_WORK_LAST_OUTPUT_MAX_CHARS = 4096
FINAL_ANSWER_COMPLETION_GRACE_SECONDS = 1.0


def _iso_or_empty(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _datetime_age_seconds(value: datetime, now: datetime) -> float:
    try:
        return (now - value).total_seconds()
    except TypeError:
        return (now.replace(tzinfo=None) - value.replace(tzinfo=None)).total_seconds()


class RendererActiveWorkPump:
    """Build the latest requested active-work projection off the renderer loop."""

    def __init__(
        self,
        context: object,
        wake_event: threading.Event,
        *,
        build_items: ActiveWorkBuilder,
    ) -> None:
        self._context = context
        self._wake_event = wake_event
        self._build_items = build_items
        self._lock = threading.Lock()
        self._closed = False
        self._pending: tuple[object, Path | None, tuple[Path, ...], int] | None = None
        self._latest: tuple[int, list[Any]] | None = None
        self._worker: threading.Thread | None = None

    def request(
        self,
        snapshot: object,
        session_path: Path | None,
        priority_paths: Sequence[Path] = (),
    ) -> bool:
        with self._lock:
            if self._closed:
                return False
            normalized_paths = tuple(dict.fromkeys(Path(path) for path in priority_paths))
            if self._pending is not None:
                normalized_paths = tuple(
                    dict.fromkeys((*self._pending[2], *normalized_paths))
                )
            self._pending = (
                copy.copy(snapshot),
                session_path,
                normalized_paths,
                int(getattr(snapshot, "selection_seq", 0) or 0),
            )
            if self._worker is not None and self._worker.is_alive():
                return True
            self._worker = threading.Thread(
                target=self._run,
                name="codex-usage-hud-renderer-active-work",
                daemon=True,
            )
            self._worker.start()
        return True

    def take_latest(self) -> tuple[int, list[Any]] | None:
        with self._lock:
            latest = self._latest
            self._latest = None
            return latest

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending = None
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=0.2)

    def _run(self) -> None:
        while True:
            with self._lock:
                if self._closed:
                    self._worker = None
                    return
                request = self._pending
                self._pending = None
            if request is None:
                with self._lock:
                    self._worker = None
                return
            snapshot, session_path, priority_paths, selection_seq = request
            try:
                items = self._build_items(
                    self._context,
                    snapshot,
                    session_path,
                    priority_paths,
                )
            except Exception as exc:
                _LOGGER.info(
                    "renderer_active_work_refresh_failed error=%s",
                    f"{type(exc).__name__}: {exc}",
                )
                items = []
            with self._lock:
                if self._closed:
                    self._worker = None
                    return
                if self._pending is not None:
                    continue
                self._latest = (selection_seq, items)
                self._worker = None
            self._wake_event.set()
            return



def _active_work_scan_roots(sessions_root: Path) -> tuple[Path, ...]:
    return usage_scan_roots(sessions_root)


def _recent_session_files(
    sessions_root: Path,
    *,
    current_path: Path | None = None,
    limit: int = ACTIVE_WORK_CANDIDATE_LIMIT,
    priority_paths: Sequence[Path] = (),
) -> list[Path]:
    priority_keys = {
        _session_path_key(Path(path))
        for path in priority_paths
        if Path(path).suffix.lower() == ".jsonl"
    }
    paths: dict[str, tuple[Path, float, bool]] = {}
    if current_path is not None:
        try:
            stat = current_path.stat()
            paths[_session_path_key(current_path)] = (
                current_path,
                stat.st_mtime,
                _session_path_key(current_path) in priority_keys,
            )
        except OSError:
            paths[_session_path_key(current_path)] = (
                current_path,
                0.0,
                _session_path_key(current_path) in priority_keys,
            )
    for root in _active_work_scan_roots(sessions_root):
        if not root.exists():
            continue
        try:
            iterator = root.rglob("*.jsonl")
            for path in iterator:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                path_key = _session_path_key(path)
                paths[path_key] = (
                    path,
                    stat.st_mtime,
                    path_key in priority_keys,
                )
        except OSError:
            continue
    ordered = sorted(paths.values(), key=lambda item: (item[2], item[1]), reverse=True)
    return [path for path, _mtime, _priority in ordered[: max(1, int(limit))]]


def _work_activity_label(value: str) -> str:
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


def _tool_invocation_parts(detail: str) -> tuple[str, str]:
    text = " ".join(str(detail or "").split())
    name, _space, args = text.partition(" ")
    return name, args


def _tool_display_name(name: str) -> str:
    value = str(name or "").strip()
    if not value:
        return "工具"
    value = value.rsplit(".", 1)[-1]
    return value.replace("_", " ")


def _extract_tool_file_target(text: str) -> str:
    patterns = [
        r"(?:Update|Add|Delete) File:\s*([^\r\n]+)",
        r'"(?:file|path)"\s*:\s*"([^"]+)"',
        r"'(?:file|path)'\s*:\s*'([^']+)'",
        r"([A-Za-z]:\\[^\s\"']+\.[A-Za-z0-9_]+)",
        r"([\w./\\-]+\.(?:py|ts|tsx|js|jsx|json|md|css|html|yaml|yml|toml|txt))",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        target = match.group(1).strip().strip("`'\".,")
        if target:
            normalized = target.replace("/", "\\")
            return _compact_work_text(normalized, 48)
    return ""


def _tool_status_text(detail: str) -> str:
    text = " ".join(str(detail or "").split())
    if text.startswith("执行命令:"):
        command = text.removeprefix("执行命令:").strip()
        return f"已执行 {_compact_work_text(command, 52)}" if command else "已执行命令"
    if text.startswith("命令失败:"):
        command = text.removeprefix("命令失败:").strip()
        return f"命令失败 {_compact_work_text(command, 52)}" if command else "命令失败"
    name, args = _tool_invocation_parts(text)
    lower_name = name.lower()
    target = _extract_tool_file_target(args)
    if lower_name in {"exec", "unified_exec", "functions.exec"}:
        command = ""
        try:
            parsed_args = json.loads(args) if args else {}
        except json.JSONDecodeError:
            parsed_args = {}
        if isinstance(parsed_args, Mapping):
            command_value = parsed_args.get("command") or parsed_args.get("cmd")
            if isinstance(command_value, (list, tuple)):
                command = " ".join(str(part) for part in command_value if part)
            else:
                command = str(command_value or "").strip()
        return (
            f"正在运行 {_compact_work_text(command, 52)}"
            if command
            else "正在执行命令"
        )
    if "apply_patch" in lower_name or "edit" in lower_name:
        return f"正在编辑 {target}" if target else "正在编辑文件"
    if "shell" in lower_name:
        command = ""
        try:
            parsed_args = json.loads(args) if args else {}
        except json.JSONDecodeError:
            parsed_args = {}
        if isinstance(parsed_args, Mapping):
            command = str(parsed_args.get("command") or "").strip()
        if command:
            file_target = _extract_tool_file_target(command)
            if file_target and re.search(r"\b(apply_patch|Set-Content|Add-Content)\b", command):
                return f"正在编辑 {file_target}"
            return f"正在运行 {_compact_work_text(command, 52)}"
        return "正在运行命令"
    if "view_image" in lower_name or "screenshot" in lower_name:
        return "正在查看界面"
    if "puppeteer" in lower_name:
        return "正在操作浏览器"
    if "read" in lower_name or "open" in lower_name:
        return f"正在读取 {target}" if target else "正在读取内容"
    if target:
        return f"正在处理 {target}"
    return f"正在调用 {_tool_display_name(name)}"


def _work_status_text(
    snapshot: ParsedSession,
    status_value: str,
    status_label: str,
) -> str:
    activity = snapshot.activity
    if _is_renderer_provisional_selection(snapshot):
        return "正在发送请求" if snapshot.composer_send_requested else "等待发送"
    if status_value == "recent":
        return "已完成"
    if status_value == "error":
        return _compact_work_text(
            snapshot.request.error or snapshot.error or status_label,
            80,
        )
    if status_value == "waiting_user":
        return "等待用户输入"
    if activity.kind == "tool call":
        return _tool_status_text(activity.detail)
    if activity.kind == "tool output":
        return "正在读取工具结果"
    if status_value == "tool":
        return _tool_status_text(activity.detail)
    if status_value in {"running", "active"}:
        if activity.kind in {"agent", "assistant"}:
            return "正在输出"
        model_name = _current_task_model_name(snapshot)
        return f"{model_name} 正在思考" if model_name else "正在思考"
    return status_label


def _activity_step_payloads(
    snapshot: ParsedSession,
    *,
    limit: int = 32,
) -> tuple[Mapping[str, object], ...]:
    """Carry the current task's real tool timeline into the desktop bubble."""
    payloads: list[Mapping[str, object]] = []
    for step in list(getattr(snapshot, "activity_steps", []) or [])[-max(1, limit) :]:
        title = str(getattr(step, "title", "") or "").strip()
        detail = _compact_work_text(getattr(step, "detail", ""), 220)
        output = _compact_work_text(getattr(step, "output", ""), 220)
        if not title and not detail and not output:
            continue
        timestamp = getattr(step, "timestamp", None)
        # activeTail keeps its real newlines (up to three output tail lines);
        # flattening it here would undo the parser's line-preserving tail.
        active_tail = str(getattr(step, "active_tail", "") or "")
        exit_code = getattr(step, "exit_code", None)
        payloads.append(
            {
                "timestamp": _iso_or_empty(timestamp),
                "title": title or "执行命令",
                "detail": detail,
                "status": str(getattr(step, "status", "") or "running").strip()
                or "running",
                "callId": str(getattr(step, "call_id", "") or "").strip(),
                "line": int(getattr(step, "line", 0) or 0),
                "toolName": str(getattr(step, "tool_name", "") or "").strip(),
                "output": output,
                "activeTail": active_tail,
                "exitCode": exit_code,
                "durationText": str(getattr(step, "duration_text", "") or "").strip(),
                "commandRaw": str(getattr(step, "command_raw", "") or "")[:2000],
            }
        )
    return tuple(payloads)


def _is_renderer_provisional_selection(snapshot: ParsedSession) -> bool:
    source = str(snapshot.selection_source or "").strip().lower()
    if source.startswith(("renderer-new-session", "renderer-pending")):
        return True
    return bool(
        not _work_session_id(snapshot)
        and snapshot.session_path is None
        and (
            str(getattr(snapshot, "composer_draft", "") or "").strip()
            or bool(getattr(snapshot, "composer_send_requested", False))
        )
    )


def _work_session_id(snapshot: ParsedSession) -> str:
    value = str(snapshot.session_id or "").strip()
    return "" if value.lower() in {"", "n/a"} else value


def _elapsed_compact(
    started_at: datetime | None,
    *,
    now: datetime | None = None,
) -> str:
    if started_at is None:
        return ""
    if started_at.tzinfo is None:
        current = now.replace(tzinfo=None) if now is not None else datetime.now()
    else:
        current = (now or datetime.now().astimezone()).astimezone(started_at.tzinfo)
    seconds = max(0, int((current - started_at).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def is_subagent_session(snapshot: ParsedSession | object) -> bool:
    """True when a parsed session is a Codex multi-agent subagent thread."""
    flag = getattr(snapshot, "is_subagent", False)
    if flag:
        return True
    thread_source = str(getattr(snapshot, "thread_source", "") or "").strip().lower()
    if thread_source == "subagent":
        return True
    parent_thread_id = str(getattr(snapshot, "parent_thread_id", "") or "").strip()
    return bool(parent_thread_id)


def is_independent_desktop_delegation(snapshot: ParsedSession | object) -> bool:
    """True when Desktop promoted a subagent into its own visible thread."""
    if not is_subagent_session(snapshot):
        return False
    if str(getattr(snapshot, "client_kind", "") or "").strip().lower() != "app":
        return False
    if str(getattr(snapshot, "parent_thread_id", "") or "").strip():
        return False
    if str(getattr(snapshot, "agent_nickname", "") or "").strip():
        return False
    # Internal collaboration agents carry a structural parent or agent identity.
    # Desktop handoff threads currently retain only thread_source=subagent; their
    # latest task prompt changes over time, so prompt text is not an identity key.
    return True


def _hide_from_work_overlay(snapshot: ParsedSession | object) -> bool:
    return is_subagent_session(snapshot) and not is_independent_desktop_delegation(snapshot)


def _work_status_from_snapshot(
    snapshot: ParsedSession,
    *,
    now: datetime,
) -> tuple[str, str, bool] | None:
    if snapshot.task_aborted_at is not None:
        return None
    activity_detail = snapshot.activity.detail.lower()
    request_status = snapshot.request.status
    if request_status == "error" or snapshot.request.error:
        return "error", "出错", False
    if _is_renderer_provisional_selection(snapshot):
        if snapshot.composer_send_requested:
            # A composer send is an active task even before Codex has persisted
            # the JSONL file or exposed its canonical thread id.
            return "running", "发送中", False
        return "draft", "待发送", False
    if snapshot.task_completed_at is not None:
        return "recent", "刚完成", False
    if snapshot.final_answer_at is not None and (
        _datetime_age_seconds(snapshot.final_answer_at, now)
        >= FINAL_ANSWER_COMPLETION_GRACE_SECONDS
    ):
        return "recent", "刚完成", True
    if request_status == "running":
        return "running", "运行中", False
    if snapshot.activity.kind == "tool call" and activity_detail.startswith(
        "request_user_input"
    ):
        return "waiting_user", "等待用户", False
    if snapshot.activity.kind == "tool call":
        return "tool", "工具执行", False
    if snapshot.slow.current_gap_active:
        return "active", "处理中", False
    return None


def _is_inherited_completion(snapshot: ParsedSession | object) -> bool:
    """Return whether the terminal event predates the target session metadata."""
    completion_at = getattr(snapshot, "task_completed_at", None) or getattr(
        snapshot, "final_answer_at", None
    )
    session_started_at = getattr(snapshot, "session_started_at", None)
    return bool(
        completion_at is not None
        and session_started_at is not None
        and _datetime_age_seconds(completion_at, session_started_at) > 0
    )


def _transfer_inherited_session_ids(context: object | None) -> set[str]:
    if context is None:
        return set()
    values = getattr(context, "_work_overlay_transfer_inherited_session_ids", None)
    if isinstance(values, set):
        return values
    values = set()
    try:
        setattr(context, "_work_overlay_transfer_inherited_session_ids", values)
    except Exception:
        pass
    return values


def _is_transfer_inherited_completion(
    context: object | None,
    snapshot: ParsedSession | object,
) -> bool:
    session_id = str(getattr(snapshot, "session_id", "") or "").strip()
    if not session_id or session_id not in _transfer_inherited_session_ids(context):
        return False
    completion_at = getattr(snapshot, "task_completed_at", None) or getattr(
        snapshot, "final_answer_at", None
    )
    session_started_at = getattr(snapshot, "session_started_at", None)
    if not isinstance(completion_at, datetime) or not isinstance(
        session_started_at, datetime
    ):
        return False
    if _datetime_age_seconds(completion_at, session_started_at) >= 0:
        return True
    _transfer_inherited_session_ids(context).discard(session_id)
    return False


def _should_suppress_inherited_completion(
    context: object | None,
    snapshot: ParsedSession | object,
) -> bool:
    return _is_inherited_completion(snapshot) or _is_transfer_inherited_completion(
        context,
        snapshot,
    )


def _completion_was_seen_active(
    context: object | None,
    item: WorkStatusItem,
    prior_items: Sequence[WorkStatusItem] = (),
) -> bool:
    task_key = _work_overlay_runtime_task_key(item)
    if not task_key:
        return False
    if task_key in _work_overlay_seen_task_keys(context):
        return True
    return any(
        existing.status != "recent"
        and _work_overlay_runtime_task_key(existing) == task_key
        for existing in prior_items
    )


def _terminal_task_marker(item: WorkStatusItem) -> str:
    return _iso_or_empty(item.task_started_at or item.started_at)


def _segment_started_after_runtime_start(
    context: object | None,
    snapshot: ParsedSession,
) -> bool:
    runtime_started_at = getattr(context, "work_overlay_started_at", None)
    if not isinstance(runtime_started_at, datetime):
        return True
    segment_started_at = snapshot.request.started_at or (
        snapshot.task_completed_at or snapshot.final_answer_at
    )
    if segment_started_at is None:
        return False
    return _datetime_age_seconds(segment_started_at, runtime_started_at) >= 0


def _terminal_completion_prompts(context: object) -> dict[str, str]:
    values = getattr(context, "_work_overlay_terminal_completion_prompts", None)
    if isinstance(values, dict):
        return values
    values = {}
    try:
        setattr(context, "_work_overlay_terminal_completion_prompts", values)
    except Exception:
        pass
    return values


def _remember_suppressed_completion(
    context: object,
    session_id: str,
    task_key: str,
    snapshot: ParsedSession,
) -> None:
    if not session_id or not task_key:
        return
    _work_overlay_terminal_item_tasks(context)[session_id] = task_key
    prompt = str(getattr(snapshot, "task_prompt", "") or "").strip()
    if prompt:
        _terminal_completion_prompts(context)[session_id] = prompt


def _remember_released_segment(
    context: object | None,
    snapshot: ParsedSession,
    item: WorkStatusItem | None,
    *,
    released: bool,
) -> None:
    if not released or item is None or not _segment_started_after_runtime_start(
        context,
        snapshot,
    ):
        return
    task_key = _work_overlay_runtime_task_key(item)
    if task_key:
        _work_overlay_seen_task_keys(context).add(task_key)


def _clear_terminal_item_task_for_new_segment(
    context: object,
    snapshot: ParsedSession,
) -> bool:
    """Release inherited suppression once a later user-steer starts."""
    session_id = str(snapshot.session_id or "").strip()
    if not session_id:
        return False
    terminal_tasks = _work_overlay_terminal_item_tasks(context)
    prompt = str(getattr(snapshot, "task_prompt", "") or "").strip()
    previous_prompt = _terminal_completion_prompts(context).get(session_id, "")
    if prompt and previous_prompt and prompt != previous_prompt:
        terminal_tasks.pop(session_id, None)
        _terminal_completion_prompts(context).pop(session_id, None)
        return True
    return False


def _work_item_model_startup_timed_out(
    snapshot: ParsedSession,
    *,
    now: datetime,
) -> bool:
    """Whether a CLI task never progressed past its initial user message."""
    if (
        snapshot.task_completed_at is not None
        or snapshot.task_aborted_at is not None
        or snapshot.final_answer_at is not None
        or snapshot.request.status != "running"
        or snapshot.activity.kind != "user"
    ):
        return False
    draft_updated_at_ms = int(
        getattr(snapshot, "composer_draft_updated_at_ms", 0) or 0
    )
    draft_updated_at = None
    if draft_updated_at_ms > 0:
        try:
            draft_updated_at = datetime.fromtimestamp(
                draft_updated_at_ms / 1000.0,
                tz=now.tzinfo,
            )
        except (OverflowError, OSError, ValueError):
            draft_updated_at = None
    updated_at = (
        draft_updated_at
        if _is_renderer_provisional_selection(snapshot) and draft_updated_at is not None
        else None
    ) or (
        snapshot.request.updated_at
        or snapshot.activity.timestamp
        or snapshot.last_event_time
        or snapshot.refreshed_at
    )
    return bool(
        updated_at is not None
        and _datetime_age_seconds(updated_at, now)
        > ACTIVE_WORK_MODEL_STARTUP_STALE_SECONDS
    )


def _work_item_from_snapshot(
    snapshot: ParsedSession,
    *,
    current: bool,
    title: str = "",
    source: str = "",
    context: object | None = None,
    now: datetime | None = None,
) -> WorkStatusItem | None:
    current_time = now or datetime.now().astimezone()
    status = _work_status_from_snapshot(snapshot, now=current_time)
    if status is None:
        return None
    status_value, status_label, pending_accounting = status
    if status_value == "recent" and _should_suppress_inherited_completion(
        context,
        snapshot,
    ):
        # Fork/copy materialization keeps the source transcript's terminal
        # event but gives the target a newer session metadata timestamp.
        # That inherited completion is history, not a newly finished task.
        return None
    if _work_item_model_startup_timed_out(snapshot, now=current_time):
        # A task_started/user_message pair can be left behind when a CLI resume
        # exits before model work begins. It has no terminal event, but must not
        # keep an active bubble (and its live elapsed clock) for four hours.
        return None

    draft_text = str(getattr(snapshot, "composer_draft", "") or "").strip()
    draft_updated_at_ms = int(
        getattr(snapshot, "composer_draft_updated_at_ms", 0) or 0
    )
    draft_updated_at = None
    if draft_updated_at_ms > 0:
        try:
            draft_updated_at = datetime.fromtimestamp(
                draft_updated_at_ms / 1000.0,
                tz=current_time.tzinfo,
            )
        except (OverflowError, OSError, ValueError):
            draft_updated_at = None
    updated_at = (
        draft_updated_at
        if _is_renderer_provisional_selection(snapshot) and draft_updated_at is not None
        else None
    ) or (
        snapshot.request.updated_at
        or snapshot.activity.timestamp
        or snapshot.last_event_time
        or snapshot.refreshed_at
    )
    if updated_at is not None:
        current_for_age = (
            current_time.astimezone(updated_at.tzinfo)
            if updated_at.tzinfo is not None
            else current_time.replace(tzinfo=None)
        )
        age_seconds = (current_for_age - updated_at).total_seconds()
        if (
            status_value != "recent"
            and age_seconds > ACTIVE_WORK_STALE_SECONDS
            and not _is_renderer_provisional_selection(snapshot)
        ):
            return None

    session_id = _work_session_id(snapshot)
    display_title = (
        title.strip()
        or snapshot.session_title.strip()
        or str(getattr(snapshot, "agent_nickname", "") or "").strip()
        or session_id
        or ("新会话" if _is_renderer_provisional_selection(snapshot) else "Codex 工作")
    )
    detail = (
        snapshot.request.error
        or snapshot.activity.detail
        or snapshot.error
        or "等待更多活动日志"
    )
    if _is_renderer_provisional_selection(snapshot) and draft_text:
        detail = draft_text
    if snapshot.activity.kind and not (
        _is_renderer_provisional_selection(snapshot) and draft_text
    ):
        detail = f"{_work_activity_label(snapshot.activity.kind)}：{detail}"
    round_index = _current_task_round_index(snapshot)
    model_name = _current_task_model_name(snapshot)
    status_text = _work_status_text(snapshot, status_value, status_label)
    last_text = (
        draft_text
        if _is_renderer_provisional_selection(snapshot) and draft_text
        else snapshot.last_output.detail.strip()
    )
    started_at = (
        snapshot.task_started_at
        or snapshot.request.started_at
        or snapshot.session_started_at
    )
    elapsed_reference = (
        snapshot.task_completed_at
        if status_value == "recent" and snapshot.task_completed_at is not None
        else current_time
    )
    elapsed = _elapsed_compact(started_at, now=elapsed_reference)
    elapsed_text = f"已处理 {elapsed}" if elapsed else ""
    tokens = _current_task_tokens(snapshot)
    progress_parts = []
    if tokens:
        progress_parts.append(f"{_format_tokens(tokens)} tokens")
    if source or snapshot.selection_source:
        progress_parts.append(source or snapshot.selection_source)
    progress = " | ".join(progress_parts)
    item_id = session_id or _session_path_key(snapshot.session_path) or display_title
    return WorkStatusItem(
        id=str(item_id),
        title=_compact_work_text(display_title, 56),
        session_id=session_id,
        target_title=display_title.strip(),
        round_index=round_index,
        task_index=max(0, int(getattr(snapshot, "task_index", 0) or 0)),
        model_name=model_name,
        status=status_value,
        status_label=status_label,
        detail=_compact_work_text(detail, 120),
        status_text=_compact_work_text(status_text, 80),
        last_text=_compact_work_text(last_text, ACTIVE_WORK_LAST_OUTPUT_MAX_CHARS),
        elapsed_text=elapsed_text,
        progress=progress,
        tokens_text=_format_tokens(tokens),
        cost_text=_format_cost_compact(_current_task_cost(snapshot)),
        cache_hit_text=_current_task_cache_hit_text(snapshot),
        source=source or snapshot.selection_source,
        workdir=str(snapshot.cwd or "").strip(),
        workdir_name=_compact_work_text(_workdir_leaf(snapshot.cwd), 32),
        model_provider=snapshot.model_provider,
        profile_name=str(getattr(snapshot, "profile_name", "") or "").strip(),
        client_kind=snapshot.client_kind,
        # The overlay treats a promoted Desktop delegation as a normal visible
        # session. Keep the internal-subagent marker only for folded agents so
        # both visible-item caches can retain promoted work across refreshes.
        is_subagent=_hide_from_work_overlay(snapshot),
        agent_nickname=str(getattr(snapshot, "agent_nickname", "") or "").strip(),
        parent_thread_id=str(getattr(snapshot, "parent_thread_id", "") or "").strip(),
        session_started_at=snapshot.session_started_at,
        task_started_at=snapshot.task_started_at,
        started_at=started_at,
        updated_at=updated_at,
        last_output_at=getattr(snapshot.last_output, "timestamp", None),
        current=current,
        pending_accounting=pending_accounting,
        draft_text=draft_text,
        collapsed_title=_compact_work_text(
            getattr(snapshot, "renderer_collapsed_title", ""),
            512,
        ),
        collapsed_disclosure_ambiguous=bool(
            getattr(snapshot, "renderer_collapsed_disclosure_ambiguous", False)
        ),
        activity_steps=_activity_step_payloads(snapshot),
    )


def _compact_work_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _refresh_visible_current_work_item(
    context: object,
    items: Sequence[WorkStatusItem],
    snapshot: ParsedSession,
) -> list[WorkStatusItem]:
    """Apply current-session state without waiting for the recent-work scan."""
    if _hide_from_work_overlay(snapshot):
        return list(items)
    session_id = _work_session_id(snapshot)
    refreshed = _work_item_from_snapshot(
        snapshot,
        current=True,
        title=snapshot.session_title,
        source=snapshot.selection_source,
        context=context,
    )
    if not session_id:
        if refreshed is None:
            return list(items)
        provisional_index = next(
            (
                index
                for index, item in enumerate(items)
                if not str(item.session_id or "").strip()
                and str(item.source or "").strip()
                == str(snapshot.selection_source or "").strip()
            ),
            None,
        )
        if provisional_index is None:
            return [*items, refreshed]
        updated = list(items)
        updated[provisional_index] = refreshed
        return updated
    existing_index = next(
        (
            index
            for index, item in enumerate(items)
            if str(item.session_id or item.id).strip() == session_id
        ),
        None,
    )
    segment_released = bool(
        refreshed is not None
        and refreshed.status == "recent"
        and _clear_terminal_item_task_for_new_segment(context, snapshot)
    )
    _remember_released_segment(
        context,
        snapshot,
        refreshed,
        released=segment_released,
    )
    if (
        refreshed is not None
        and refreshed.status == "recent"
        and not (
            segment_released
            or _completion_was_seen_active(context, refreshed, items)
        )
    ):
        if session_id:
            task_key = _terminal_task_marker(refreshed)
            _remember_suppressed_completion(
                context,
                session_id,
                task_key,
                snapshot,
            )
        if existing_index is None:
            return list(items)
        return [item for index, item in enumerate(items) if index != existing_index]
    if existing_index is None:
        # Replace the immediate send bubble when Codex later provides the
        # canonical session id instead of briefly showing both entries.
        provisional_index = next(
            (
                index
                for index, item in enumerate(items)
                if not str(item.session_id or "").strip()
                and str(item.source or "").lower().startswith("renderer-")
            ),
            None,
        )
        if provisional_index is not None and refreshed is not None:
            updated = list(items)
            updated[provisional_index] = refreshed
            return updated
        if refreshed is not None and refreshed.status == "recent":
            # The inherited terminal may already have removed the current item.
            # Replace it directly when a later real completion arrives, rather
            # than clearing the tombstone and allowing the old cache to return.
            _work_overlay_terminal_item_tasks(context).pop(session_id, None)
            _terminal_completion_prompts(context).pop(session_id, None)
            return [*items, refreshed]
        if _should_suppress_inherited_completion(context, snapshot):
            task_key = _iso_or_empty(
                snapshot.task_started_at or snapshot.request.started_at
            )
            _remember_suppressed_completion(
                context,
                session_id,
                task_key,
                snapshot,
            )
        return list(items)
    if refreshed is None:
        if _should_suppress_inherited_completion(context, snapshot):
            task_key = _iso_or_empty(
                snapshot.task_started_at or snapshot.request.started_at
            )
            _remember_suppressed_completion(
                context,
                session_id,
                task_key,
                snapshot,
            )
            return [item for index, item in enumerate(items) if index != existing_index]
        if snapshot.task_aborted_at is None:
            return list(items)
        task_key = _iso_or_empty(snapshot.task_started_at or snapshot.request.started_at)
        if task_key:
            _work_overlay_terminal_item_tasks(context)[session_id] = task_key
        return [item for index, item in enumerate(items) if index != existing_index]
    if refreshed.status == "recent":
        # A later completion is a new terminal event even when the parser
        # reuses the inherited task_started marker after a user steer.
        _work_overlay_terminal_item_tasks(context).pop(session_id, None)
        _terminal_completion_prompts(context).pop(session_id, None)
    refreshed = _merge_stable_item_metadata(items[existing_index], refreshed)
    # Drop any leftover renderer provisional bubble now that the canonical
    # session item exists, so the same conversation is not shown twice.
    updated = [
        item
        for index, item in enumerate(items)
        if index == existing_index
        or str(item.session_id or "").strip()
        or not str(item.source or "").lower().startswith("renderer-")
    ]
    for index, item in enumerate(updated):
        if str(item.session_id or item.id).strip() == session_id:
            updated[index] = refreshed
            break
    return updated


def _effective_provider_scope(
    context: "RuntimeContext | object",
    snapshot: ParsedSession | None = None,
) -> frozenset[str] | None:
    """Resolve the provider scope used for usage, budgets, and adjustments."""
    if snapshot is not None and snapshot.client_kind == "app":
        observed_provider = str(snapshot.model_provider or "").strip().lower()
        if observed_provider and observed_provider != "unknown":
            setattr(context, "app_provider", observed_provider)
    app_provider = str(getattr(context, "app_provider", "") or "").strip().lower()
    config = getattr(context, "user_config", None)
    resolver = getattr(config, "effective_provider_scope", None)
    if callable(resolver):
        return resolver(app_provider)
    return None


def _effective_notification_provider_scope(
    context: "RuntimeContext | object",
    snapshot: ParsedSession | None = None,
) -> frozenset[str] | None:
    """Resolve providers that may produce active-work notification bubbles."""
    included = _effective_provider_scope(context, snapshot)
    app_provider = str(getattr(context, "app_provider", "") or "").strip().lower()
    config = getattr(context, "user_config", None)
    resolver = getattr(config, "effective_notification_provider_scope", None)
    if callable(resolver):
        return resolver(app_provider)
    return included


def active_work_items_for_snapshot(
    context: "RuntimeContext",
    snapshot: ParsedSession,
    session_path: Path | None,
    priority_paths: Sequence[Path] = (),
    *,
    scan_candidates: bool = True,
) -> list[WorkStatusItem]:
    """Build primary-screen work bubble items from recently active Codex sessions.

    ``scan_candidates=False`` skips the recent-session discovery loop (the costly
    ``parse_file`` pass over ``ACTIVE_WORK_CANDIDATE_LIMIT`` historical logs) and
    emits only the current-session bubble plus any already-cached items. The
    renderer cold-start first frame uses this so the primary bubble appears
    immediately while the candidate scan is deferred to the async pump.
    """
    item_limit = _work_overlay_item_limit_for_context(context)
    if item_limit <= 0:
        _work_overlay_visible_item_cache(context).clear()
        return []
    now = datetime.now().astimezone()
    items: dict[str, WorkStatusItem] = {}
    visible_item_cache = _work_overlay_visible_item_cache(context)
    terminal_item_tasks = _work_overlay_terminal_item_tasks(context)
    terminal_item_ids: dict[str, str] = {}
    expired_startup_item_ids: set[str] = set()
    current_key = _session_path_key(session_path)
    # Internal collaboration agents stay folded into their parent. Desktop can
    # promote a delegation to an independent visible thread; those do bubble.
    if not _hide_from_work_overlay(snapshot):
        current_segment_released = _clear_terminal_item_task_for_new_segment(
            context,
            snapshot,
        )
        current_item = _work_item_from_snapshot(
            snapshot,
            current=True,
            title=snapshot.session_title,
            source=snapshot.selection_source,
            context=context,
            now=now,
        )
        _remember_released_segment(
            context,
            snapshot,
            current_item,
            released=current_segment_released,
        )
        if current_item is not None:
            cached_current = visible_item_cache.get(str(current_item.id))
            if cached_current is not None:
                current_item = _merge_stable_item_metadata(
                    cached_current,
                    current_item,
                )
            if current_item.status == "recent" and not _completion_was_seen_active(
                context,
                current_item,
            ):
                if snapshot.session_id:
                    task_key = _terminal_task_marker(current_item)
                    _remember_suppressed_completion(
                        context,
                        str(snapshot.session_id),
                        task_key,
                        snapshot,
                    )
                    if task_key:
                        terminal_item_ids[str(snapshot.session_id)] = task_key
            else:
                items[str(current_item.id)] = current_item
                if current_item.status == "recent" and snapshot.session_id:
                    terminal_item_tasks.pop(str(snapshot.session_id), None)
                    _terminal_completion_prompts(context).pop(
                        str(snapshot.session_id),
                        None,
                    )
        elif _should_suppress_inherited_completion(context, snapshot) and snapshot.session_id:
            task_key = _iso_or_empty(
                snapshot.task_started_at or snapshot.request.started_at
            )
            _remember_suppressed_completion(
                context,
                str(snapshot.session_id),
                task_key,
                snapshot,
            )
            terminal_item_ids[str(snapshot.session_id)] = task_key
        elif snapshot.task_aborted_at is not None and snapshot.session_id:
            terminal_item_ids[str(snapshot.session_id)] = _iso_or_empty(
                snapshot.task_started_at
            )
        elif _work_item_model_startup_timed_out(snapshot, now=now) and snapshot.session_id:
            expired_startup_item_ids.add(str(snapshot.session_id))

    if scan_candidates:
        for path in _recent_session_files(
            context.sessions_root,
            current_path=session_path,
            limit=ACTIVE_WORK_CANDIDATE_LIMIT,
            priority_paths=priority_paths,
        ):
            if _session_path_key(path) == current_key:
                continue
            try:
                parsed = context.parser.parse_file(path)
            except Exception:
                continue
            if _hide_from_work_overlay(parsed):
                continue
            parsed_segment_released = _clear_terminal_item_task_for_new_segment(
                context,
                parsed,
            )
            title = ""
            if context.active_session_tracker is not None:
                title = context.active_session_tracker.title_for_session(
                    path,
                    parsed.session_id,
                )
            item = _work_item_from_snapshot(
                parsed,
                current=False,
                title=title,
                source="activity",
                context=context,
                now=now,
            )
            _remember_released_segment(
                context,
                parsed,
                item,
                released=parsed_segment_released,
            )
            if item is not None:
                cached_item = visible_item_cache.get(str(item.id))
                if cached_item is not None:
                    item = _merge_stable_item_metadata(cached_item, item)
                if item.status == "recent" and not _completion_was_seen_active(
                    context,
                    item,
                ):
                    if parsed.session_id:
                        task_key = _terminal_task_marker(item)
                        _remember_suppressed_completion(
                            context,
                            str(parsed.session_id),
                            task_key,
                            parsed,
                        )
                        if task_key:
                            terminal_item_ids[str(parsed.session_id)] = task_key
                else:
                    items[str(item.id)] = item
                    if item.status == "recent" and parsed.session_id:
                        terminal_item_tasks.pop(str(parsed.session_id), None)
                        _terminal_completion_prompts(context).pop(
                            str(parsed.session_id),
                            None,
                        )
            elif _should_suppress_inherited_completion(context, parsed) and parsed.session_id:
                task_key = _iso_or_empty(
                    parsed.task_started_at or parsed.request.started_at
                )
                _remember_suppressed_completion(
                    context,
                    str(parsed.session_id),
                    task_key,
                    parsed,
                )
                terminal_item_ids[str(parsed.session_id)] = task_key
            elif parsed.task_aborted_at is not None and parsed.session_id:
                terminal_item_ids[str(parsed.session_id)] = _iso_or_empty(
                    parsed.task_started_at
                )
            elif _work_item_model_startup_timed_out(parsed, now=now) and parsed.session_id:
                expired_startup_item_ids.add(str(parsed.session_id))

    terminal_item_tasks.update(terminal_item_ids)
    for item_id in terminal_item_ids:
        visible_item_cache.pop(item_id, None)
    for item_id, cached_item in list(visible_item_cache.items()):
        if item_id in items:
            continue
        if item_id in expired_startup_item_ids:
            visible_item_cache.pop(item_id, None)
            continue
        if bool(getattr(cached_item, "is_subagent", False)):
            visible_item_cache.pop(item_id, None)
            continue
        updated_at = (
            cached_item.updated_at
            or cached_item.started_at
            or cached_item.task_started_at
            or cached_item.session_started_at
        )
        if cached_item.status != "recent" and (
            updated_at is None
            or _datetime_age_seconds(updated_at, now) > ACTIVE_WORK_STALE_SECONDS
        ):
            visible_item_cache.pop(item_id, None)
            continue
        items[item_id] = replace(cached_item, current=False)

    ordered = sorted(items.values(), key=_work_overlay_item_sort_key, reverse=True)
    provider_scope = _effective_notification_provider_scope(context, snapshot)
    if provider_scope is not None:
        # The selected current session remains visible even while its provider
        # metadata is temporarily unavailable during a Renderer handoff.
        ordered = [
            item
            for item in ordered
            if item.current or item.model_provider in provider_scope
        ]
    selected = _select_runtime_work_overlay_items(
        context,
        ordered,
        item_limit=item_limit,
    )
    selected_ids = {str(item.id) for item in selected if str(item.id or "").strip()}
    retained_ids = {
        item_id
        for item_id, cached_item in visible_item_cache.items()
        if item_id in items
        and item_id not in terminal_item_ids
        and _work_overlay_runtime_task_key(items[item_id])
        == _work_overlay_runtime_task_key(cached_item)
    }
    visible_ids = selected_ids | retained_ids
    selected = [
        item
        for item in ordered
        if str(item.id or "").strip() in visible_ids
    ][:item_limit]
    current_item = next(
        (
            item
            for item in ordered
            if bool(item.current) and item.status != "recent"
        ),
        None,
    )
    if current_item is not None and all(
        str(item.id or "") != str(current_item.id or "") for item in selected
    ):
        if selected:
            selected[-1] = current_item
        else:
            selected = [current_item]
    visible_item_cache.clear()
    visible_item_cache.update(
        {
            str(item.id): replace(item, current=False)
            for item in selected
            if str(item.id or "").strip()
        }
    )
    return selected

__all__ = ["ActiveWorkBuilder", "RendererActiveWorkPump"]
