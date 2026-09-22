"""Local Codex JSONL and SQLite SSE parsing primitives."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .calculator import UsageCalculator, estimate_tokens
from .pre_send_estimator import BaseEstimate
from .activity_monitor import ReadingActivity

DEFAULT_MODEL = "gpt-5.5"
MAX_REQUEST_HISTORY = 500
# Forked/subagent JSONL dumps parent history in a same-timestamp cluster right
# after the structural boundary. Skip that cluster so only live child usage is
# billed (matches cc-switch codex_session parsing).
HISTORY_REPLAY_CLUSTER_SECONDS = 0.5


def parse_timestamp(value: Any) -> datetime | None:
    """Parse Codex ISO timestamps without raising for partial log lines."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def short_session_id(path: Path) -> str:
    """Return a stable session id derived from a JSONL filename."""
    match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        path.stem,
        re.IGNORECASE,
    )
    return match.group(1) if match else path.stem


def compact_text(value: Any, limit: int = 140) -> str:
    """Collapse whitespace and trim text for local display metadata."""
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def message_text(payload: Mapping[str, Any]) -> str:
    """Extract assistant text from a Codex response_item message payload."""
    direct = payload.get("text") or payload.get("message")
    if direct:
        return str(direct)

    parts: list[str] = []
    for item in payload.get("content") or []:
        if isinstance(item, Mapping):
            text = item.get("text")
            if text is None:
                text = item.get("content")
            if text is not None:
                parts.append(str(text))
        elif item is not None:
            parts.append(str(item))
    return " ".join(part for part in parts if part)


def reasoning_text(payload: Mapping[str, Any]) -> str:
    """Extract readable text from a reasoning response item when available."""
    direct = (
        payload.get("text") or payload.get("reasoning") or payload.get("summary_text")
    )
    if direct:
        return str(direct)

    parts: list[str] = []
    for key in ("summary", "content"):
        items = payload.get(key) or []
        if not isinstance(items, Sequence):
            continue
        for item in items:
            if isinstance(item, Mapping):
                text = item.get("text")
                if text is None:
                    text = item.get("content")
                if text is not None:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
    return " ".join(part for part in parts if part)


def reasoning_title_text(value: Any) -> str:
    """Strip markdown emphasis from a reasoning summary heading."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"^#+\s*", "", text)
    return compact_text(text, 90)


def reasoning_summary_title(payload: Mapping[str, Any]) -> str:
    """Return the first de-markdowned reasoning summary heading, if any."""
    summary = payload.get("summary_text")
    if isinstance(summary, Sequence) and not isinstance(summary, (str, bytes, bytearray)):
        for entry in summary:
            title = reasoning_title_text(entry)
            if title:
                return title
    return reasoning_title_text(reasoning_text(payload))


def _tool_argument_value(payload: Mapping[str, Any]) -> Any:
    """Return tool arguments across the legacy and current JSONL shapes."""
    for key in ("arguments", "input", "parameters"):
        if key in payload and payload.get(key) is not None:
            return payload.get(key)
    return None


def _mapping_from_tool_arguments(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def tool_call_detail(payload: Mapping[str, Any]) -> str:
    """Normalize function/custom tool calls for activity and status display."""
    name = str(payload.get("name") or payload.get("tool_name") or "工具调用").strip()
    value = _tool_argument_value(payload)
    if value is None:
        return name
    if isinstance(value, str):
        argument_text = value.strip()
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        argument_text = json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))
    else:
        try:
            argument_text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            argument_text = str(value)
    return f"{name} {compact_text(argument_text, 140)}".strip()


def _tool_call_name(payload: Mapping[str, Any]) -> str:
    return str(payload.get("name") or payload.get("tool_name") or "").strip()


def _mcp_tool_name(item: Mapping[str, Any]) -> str:
    """Normalize an event-level McpToolCall to the activity tool namespace."""
    server = (
        str(item.get("server") or "")
        .strip()
        .replace(".", "_")
        .replace("-", "_")
    )
    tool = str(item.get("tool") or item.get("name") or "").strip()
    if server and tool:
        return f"mcp__{server}__{tool}"
    return tool or server


def _mcp_tool_detail(item: Mapping[str, Any]) -> str:
    """Return a compact MCP action detail without embedding its result payload."""
    tool_name = str(item.get("tool") or item.get("name") or "MCP 工具").strip()
    arguments = item.get("arguments")
    if arguments in (None, "", {}, []):
        return tool_name
    if isinstance(arguments, str):
        text = arguments.strip()
    else:
        try:
            text = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(arguments)
    return f"{tool_name} {compact_text(text, 160)}".strip()


def _command_parts(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(part) for part in value if part]


_SHELL_LAUNCHERS = frozenset(
    {
        "pwsh",
        "powershell",
        "bash",
        "sh",
        "zsh",
        "dash",
        "fish",
        "cmd",
        "nu",
    }
)


def _shell_launcher(value: str) -> bool:
    name = str(value or "").strip().casefold()
    if name.endswith(".exe"):
        name = name[: -len(".exe")]
    # Launcher tokens arrive as absolute paths on Windows
    # (D:\Program Files\PowerShell\7\pwsh), so compare the final path
    # segment. Splitting on both separators keeps /-style paths working.
    name = re.split(r"[/\\]", name)[-1]
    return name in _SHELL_LAUNCHERS


def _normalise_command_parts(parts: Sequence[str]) -> str:
    values = [str(part) for part in parts if part]
    if not values:
        return ""
    command_flags = {
        "-command",
        "--command",
        "-c",
        "/c",
        "/k",
        "-lc",
    }
    # Only strip a shell launcher wrapper (pwsh -Command ...) when the first
    # token is a known shell and the flag appears near the front. A bare
    # command array like `curl -c cookies.txt` or `git log -c key=val` must
    # never lose its leading executable.
    if _shell_launcher(values[0]):
        flag_index = next(
            (
                index
                for index, value in enumerate(values[:4])
                if value.casefold() in command_flags
            ),
            None,
        )
        if flag_index is not None and flag_index + 1 < len(values):
            values = values[flag_index + 1 :]
    return " ".join(values)


def _parsed_cmd_segments(item: Mapping[str, Any]) -> list[str]:
    """Return non-empty ``parsed_cmd[].cmd`` segments of a CommandExecution item.

    Codex records the shell-stripped command here for every execution, which
    is cleaner than the raw ``command`` argv that still carries the full
    pwsh/bash launcher path.
    """
    parsed = item.get("parsed_cmd")
    if not isinstance(parsed, Sequence) or isinstance(parsed, (str, bytes, bytearray)):
        return []
    segments: list[str] = []
    for entry in parsed:
        if isinstance(entry, Mapping):
            cmd = str(entry.get("cmd") or "").strip()
            if cmd:
                segments.append(cmd)
    return segments


def command_execution_text(item: Mapping[str, Any], limit: int = 200) -> str:
    """Extract a readable command from a CommandExecution item."""
    segments = _parsed_cmd_segments(item)
    if segments:
        return compact_text(" ".join(segments), limit)
    value = item.get("command") or item.get("cmd") or ""
    parts = _command_parts(value)
    if parts:
        value = _normalise_command_parts(parts)
    return compact_text(value, limit)


def command_execution_raw_text(item: Mapping[str, Any], limit: int = 2000) -> str:
    """Return the un-truncated command text for tooltip and clipboard use."""
    segments = _parsed_cmd_segments(item)
    if segments:
        text = " ".join(segments)
    else:
        value = item.get("command") or item.get("cmd") or ""
        parts = _command_parts(value)
        text = _normalise_command_parts(parts) if parts else str(value)
    return text[:limit] if text else ""


_JS_CMD_ARGUMENT_RE = re.compile(r"\bcmd\s*:\s*(['\"])(.*?)\1", re.DOTALL)
_PATCH_BLOCK_RE = re.compile(r"\*\*\* Begin Patch.*?(?:\*\*\* End Patch|$)", re.DOTALL)
_PATCH_TARGET_RE = re.compile(r"(?:Add|Update|Delete) File:\s*([^\r\n\"]+)")


def _patch_tool_texts(text: str) -> tuple[str, str] | None:
    """Split an apply_patch JS input into (compact detail, raw patch text)."""
    match = _PATCH_BLOCK_RE.search(text)
    if not match:
        return None
    block = match.group(0)
    target = _PATCH_TARGET_RE.search(block)
    name = _file_change_basename(target.group(1).strip()) if target else ""
    detail = f"apply_patch {name}".strip()
    return detail, block[:2000]


def tool_command_text(payload: Mapping[str, Any], limit: int = 200) -> str:
    """Extract a command from function/custom tool arguments when present."""
    value = _tool_argument_value(payload)
    mapped = _mapping_from_tool_arguments(value)
    if mapped is not None:
        value = mapped.get("command") or mapped.get("cmd") or value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        value = " ".join(str(part) for part in value if part)
    text = str(value or "")
    if mapped is None and text:
        # JS glue inputs (`const r = await tools.exec_command({cmd: '...'})`)
        # are not JSON; pull the wrapped command out so running steps show the
        # user-facing command instead of the harness source line.
        match = _JS_CMD_ARGUMENT_RE.search(text)
        if match:
            text = match.group(2).replace("\\'", "'").replace('\\"', '"')
        else:
            patch_texts = _patch_tool_texts(text)
            if patch_texts is not None:
                return compact_text(patch_texts[0], limit)
    return compact_text(text, limit)


def tool_command_raw_text(payload: Mapping[str, Any], limit: int = 2000) -> str:
    """Return the un-truncated tool command for tooltip and clipboard use."""
    value = _tool_argument_value(payload)
    mapped = _mapping_from_tool_arguments(value)
    if mapped is not None:
        value = mapped.get("command") or mapped.get("cmd") or value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        value = " ".join(str(part) for part in value if part)
    text = str(value or "")
    if mapped is None and text:
        match = _JS_CMD_ARGUMENT_RE.search(text)
        if match:
            text = match.group(2).replace("\\'", "'").replace('\\"', '"')
        else:
            patch_texts = _patch_tool_texts(text)
            if patch_texts is not None:
                return patch_texts[1][:limit]
    return text[:limit] if text else ""


def command_execution_output(item: Mapping[str, Any]) -> str:
    """Pick the best completed-command output, preferring structured fields.

    ``formatted_output`` keeps the line structure Codex already rendered; on
    failure the stderr stream is the payload the user actually needs.
    """
    failed = str(item.get("status") or "").casefold() in {"failed", "error"}
    if failed:
        candidates = (
            item.get("stderr"),
            item.get("formatted_output"),
            item.get("stdout"),
            item.get("aggregated_output"),
        )
    else:
        candidates = (
            item.get("formatted_output"),
            item.get("stdout"),
            item.get("aggregated_output"),
        )
    for candidate in candidates:
        text = str(candidate or "")
        if text.strip():
            return text
    return ""


def active_output_tail(
    text: str,
    *,
    lines: int = 3,
    width: int = 120,
) -> str:
    """Return the last command output lines joined by real newlines."""
    if not text:
        return ""
    raw_lines = re.split(r"\r?\n", str(text))
    while raw_lines and not raw_lines[-1].strip():
        raw_lines.pop()
    if not raw_lines:
        return ""
    tail = [compact_text(line, width) for line in raw_lines[-lines:]]
    return "\n".join(line for line in tail if line)


def _file_change_basename(path: Any) -> str:
    return str(path or "").replace("\\", "/").rsplit("/", 1)[-1]


def _exit_code_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def file_change_output(changes: Mapping[str, Any]) -> str:
    """Summarize one FileChange item as per-file +/- line counts."""
    parts: list[str] = []
    for path, change in changes.items():
        name = _file_change_basename(path)
        if not isinstance(change, Mapping):
            parts.append(name)
            continue
        diff = str(change.get("unified_diff") or "")
        if diff:
            added = sum(
                1
                for line in diff.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
            removed = sum(
                1
                for line in diff.splitlines()
                if line.startswith("-") and not line.startswith("---")
            )
            parts.append(f"{name} +{added}/-{removed}")
            continue
        kind = str(change.get("type") or "").casefold()
        content = change.get("content")
        if kind == "add" and content:
            parts.append(f"{name} +{len(str(content).splitlines())}")
        elif kind == "delete":
            parts.append(f"{name} 删除")
        else:
            parts.append(name)
    return compact_text(" · ".join(parts), 220)


def _command_duration_text(duration: Any) -> str:
    seconds: float | None = None
    if isinstance(duration, Mapping):
        try:
            seconds = float(duration.get("secs") or 0) + float(
                duration.get("nanos") or 0
            ) / 1_000_000_000
        except (TypeError, ValueError):
            seconds = None
    elif isinstance(duration, (int, float)):
        seconds = float(duration)
    if seconds is None or seconds < 0:
        return ""
    total = max(0, int(round(seconds)))
    if total < 60:
        return f"{total}s"
    minutes, rest = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m{rest:02d}s"


def _activity_from_record(record: Mapping[str, Any]) -> "Activity | None":
    payload = record.get("payload") or {}
    if not isinstance(payload, Mapping):
        return None
    record_type = record.get("type")
    payload_type = payload.get("type")
    timestamp = record.get("_dt")

    user_text = _user_message_text(record)
    if user_text:
        return Activity("user", compact_text(user_text, 160), timestamp)
    if record_type == "event_msg" and payload_type == "agent_message":
        return Activity("agent", compact_text(payload.get("message"), 160), timestamp)
    if record_type == "response_item" and payload_type in {
        "function_call",
        "custom_tool_call",
    }:
        return Activity("tool call", tool_call_detail(payload), timestamp)
    if record_type == "response_item" and payload_type in {
        "function_call_output",
        "custom_tool_call_output",
    }:
        call_id = str(payload.get("call_id") or "").strip()
        output = compact_text(payload.get("output"), 140)
        return Activity("tool output", f"{call_id} {output}".strip(), timestamp)
    if record_type == "event_msg" and payload_type in {
        "item_started",
        "item_updated",
        "item_completed",
    }:
        item = payload.get("item") or {}
        if isinstance(item, Mapping):
            item_type = str(item.get("type") or "").replace("_", "").casefold()
            if item_type == "commandexecution":
                command = command_execution_text(item)
                status = str(item.get("status") or "").casefold()
                if payload_type == "item_completed":
                    prefix = "执行命令" if status == "completed" else "命令失败"
                else:
                    prefix = "执行命令"
                return Activity(
                    "tool call",
                    f"{prefix}: {command}" if command else prefix,
                    timestamp,
                )
            if item_type == "reasoning":
                if payload_type == "item_completed":
                    return Activity(
                        "reasoning",
                        reasoning_summary_title(item) or "正在思考",
                        timestamp,
                    )
                return Activity("reasoning", "正在思考", timestamp)
    if record_type == "response_item" and payload_type == "message":
        if is_turn_aborted_message(payload):
            return None
        role = response_message_role(payload)
        if role and role != "assistant":
            return None
        return Activity("assistant", compact_text(message_text(payload), 160), timestamp)
    if record_type == "response_item" and payload_type == "reasoning":
        return Activity(
            "reasoning", reasoning_summary_title(payload) or "正在思考", timestamp
        )
    if record_type == "event_msg" and payload_type == "task_started":
        return Activity("reasoning", "正在思考", timestamp)
    return None


def response_message_role(payload: Mapping[str, Any]) -> str:
    """Return a normalized role for response_item message payloads."""
    return str(payload.get("role") or "").strip().lower()


def is_turn_aborted_message(payload: Mapping[str, Any]) -> bool:
    """Return whether a response_item message is Codex's synthetic turn_aborted marker."""
    if payload.get("type") != "message":
        return False
    if response_message_role(payload) != "user":
        return False
    text = " ".join(message_text(payload).split()).lower()
    return text.startswith("<turn_aborted>") or "<turn_aborted>" in text


def _user_message_text(record: Mapping[str, Any]) -> str:
    """Read user input from both Codex rollout formats."""
    payload = record.get("payload") or {}
    if not isinstance(payload, Mapping):
        return ""
    if record.get("type") == "event_msg" and payload.get("type") == "user_message":
        return str(payload.get("message") or "").strip()
    # 新版继续输入只有 response_item；中止标记是合成消息，不能重开任务。
    if (
        record.get("type") == "response_item"
        and payload.get("type") == "message"
        and response_message_role(payload) == "user"
        and not is_turn_aborted_message(payload)
    ):
        return message_text(payload).strip()
    return ""


def extract_log_field(body: str, name: str) -> str:
    """Extract a key=value field from an OTel feedback log line."""
    match = re.search(rf"\b{re.escape(name)}=(\"[^\"]*\"|[^\s]+)", body)
    if not match:
        return ""
    value = match.group(1)
    return value[1:-1] if value.startswith('"') and value.endswith('"') else value


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _row_value(
    row: Mapping[str, Any] | sqlite3.Row, name: str, default: Any = None
) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    try:
        return row[name]
    except (IndexError, KeyError, TypeError):
        return default


def seconds_between(start: datetime, end: datetime) -> float:
    """Return elapsed seconds between two timestamps."""
    return (end - start).total_seconds()


def event_label(record: Mapping[str, Any]) -> str:
    """Return a compact label for one JSONL event."""
    payload = record.get("payload") or {}
    record_type = record.get("type")
    payload_type = payload.get("type") if isinstance(payload, Mapping) else None

    if record_type == "response_item":
        if payload_type == "function_call":
            return "call:" + str(payload.get("name") or "?")
        if payload_type == "function_call_output":
            return "output:" + str(payload.get("call_id") or "?")
        if payload_type == "message":
            if is_turn_aborted_message(payload):
                return "turn_aborted"
            return "assistant:" + compact_text(message_text(payload), 70)
        if payload_type == "reasoning":
            detail = compact_text(reasoning_text(payload), 70)
            return "reasoning:" + detail if detail else "reasoning"

    if record_type == "event_msg":
        if payload_type == "user_message":
            return "user:" + compact_text(payload.get("message"), 70)
        if payload_type == "agent_message":
            return "agent:" + compact_text(payload.get("message"), 70)
        if payload_type == "token_count":
            return "token_count"
        if payload_type == "task_started":
            return "task_started"
        if payload_type == "task_complete":
            return "task_complete"

    return f"{record_type}:{payload_type}"


def classify_gap(previous: Mapping[str, Any], current: Mapping[str, Any]) -> str:
    """Classify a wait gap between adjacent JSONL records."""
    previous_payload = previous.get("payload") or {}
    current_payload = current.get("payload") or {}

    if (
        previous.get("type") == "response_item"
        and isinstance(previous_payload, Mapping)
        and previous_payload.get("type") == "function_call"
        and current.get("type") == "response_item"
        and isinstance(current_payload, Mapping)
        and current_payload.get("type") == "function_call_output"
    ):
        if previous_payload.get("call_id") == current_payload.get("call_id"):
            if previous_payload.get("name") == "request_user_input":
                return "user_wait"
            return "tool_wait"

    if (
        previous.get("type") == "event_msg"
        and isinstance(previous_payload, Mapping)
        and previous_payload.get("type") == "task_complete"
        and current.get("type") == "event_msg"
        and isinstance(current_payload, Mapping)
        and current_payload.get("type") == "task_started"
    ):
        return "idle_between_tasks"

    if (
        previous.get("type") == "event_msg"
        and isinstance(previous_payload, Mapping)
        and previous_payload.get("type") in {"user_message", "task_started"}
    ):
        return "model_startup"

    if (
        previous.get("type") == "event_msg"
        and isinstance(previous_payload, Mapping)
        and previous_payload.get("type") == "token_count"
        and current.get("type") == "response_item"
        and isinstance(current_payload, Mapping)
        and current_payload.get("type") == "reasoning"
    ):
        return "model_or_idle"

    return "other_gap"


@dataclass
class ConfirmedTokens:
    """Confirmed token counters from JSONL token_count events."""

    last_total: int = 0
    last_input: int = 0
    last_cached: int = 0
    last_cache_write: int = 0
    last_output: int = 0
    last_reasoning: int = 0
    cumulative_total: int = 0
    cumulative_input: int = 0
    cumulative_cached: int = 0
    cumulative_cache_write: int = 0
    cumulative_output: int = 0
    cumulative_reasoning: int = 0
    cumulative_cost_usd: float | None = None
    timestamp: datetime | None = None
    line: int = 0


@dataclass
class EstimateTokens:
    """Unconfirmed local estimates after the latest token_count event."""

    input_tokens: int = 0
    output_tokens: int = 0
    tool_tokens: int = 0
    total_tokens: int = 0
    source: str = ""
    started_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class RequestTokens:
    """Current request state, either confirmed or still being estimated."""

    status: str = "waiting"
    round_index: int = 0
    model: str = ""
    input_tokens: int | None = None
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = 0
    reasoning_tokens: int | None = None
    total_tokens: int | None = 0
    estimated: bool = True
    source: str = "jsonl"
    response_id: str = ""
    updated_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cost_usd: float | None = None
    error: str = ""


@dataclass
class RequestRound:
    """One model request round within the current Codex task."""

    index: int
    status: str
    model: str
    input_tokens: int | None
    cached_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    estimated: bool
    cache_write_tokens: int | None = 0
    cost_usd: float | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    activity_summary: str = ""
    copy_text: str = ""
    # All activity entry texts of the round (输入/输出/工具调用/工具返回/推理).
    # The locate flow matches these against the chat DOM; copy_text alone only
    # carries the preferred entry, whose text may never render verbatim.
    activity_texts: tuple[str, ...] = ()


@dataclass
class UsageEvent:
    """One confirmed token_count event with a computed cost."""

    timestamp: datetime | None
    model: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    cost_usd: float | None = None
    model_provider: str = "unknown"
    cache_write_tokens: int = 0
    line: int = 0


@dataclass
class UsageSummary:
    """Aggregated usage totals for a rolling time window."""

    tokens: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    # Event coverage is kept beside the aggregate so callers never mistake a
    # partial sum (where unavailable prices contribute zero) for a complete
    # monetary total.  Zero/zero preserves compatibility for synthetic
    # summaries created by older integrations.
    priced_event_count: int = 0
    total_event_count: int = 0


@dataclass
class Activity:
    """Latest user-visible activity discovered in a session log."""

    kind: str = "idle"
    detail: str = ""
    timestamp: datetime | None = None


@dataclass
class ActivityStep:
    """One command/tool step in the current task's execution timeline."""

    timestamp: datetime | None = None
    title: str = "执行命令"
    detail: str = ""
    status: str = "running"
    call_id: str = ""
    line: int = 0
    tool_name: str = ""
    output: str = ""
    # Backfilled the instant the command completes: local display needs the
    # exit code, the newline-preserving output tail, and the rounded duration
    # because Codex emits zero events between start and completion.
    exit_code: int | None = None
    active_tail: str = ""
    duration_text: str = ""
    command_raw: str = ""


@dataclass
class WorkStatusItem:
    """One active Codex work item shown in the HUD activity stack."""

    id: str
    title: str
    status: str
    status_label: str
    detail: str
    session_id: str = ""
    target_title: str = ""
    round_index: int = 0
    task_index: int = 0
    model_name: str = ""
    status_text: str = ""
    last_text: str = ""
    elapsed_text: str = ""
    progress: str = ""
    tokens_text: str = ""
    cost_text: str = ""
    cache_hit_text: str = ""
    workdir_name: str = ""
    source: str = ""
    workdir: str = ""
    model_provider: str = "unknown"
    profile_name: str = ""
    client_kind: str = "unknown"
    is_subagent: bool = False
    agent_nickname: str = ""
    parent_thread_id: str = ""
    session_started_at: datetime | None = None
    task_started_at: datetime | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    # Timestamp of the newest assistant output; the bubble feed compares it
    # against task_started_at to detect "thinking, no fresh answer yet".
    last_output_at: datetime | None = None
    current: bool = False
    pending_accounting: bool = False
    draft_text: str = ""
    # Native Codex disclosure title observed for the currently selected
    # renderer session. It is not reconstructed from JSONL activity steps.
    collapsed_title: str = ""
    collapsed_disclosure_ambiguous: bool = False
    activity_steps: tuple[Mapping[str, object], ...] = ()
    kind: str = "session"
    event_id: str = ""


@dataclass
class ToolCallTiming:
    """One completed tool or user-wait round-trip."""

    call_id: str
    name: str
    args: str
    start: datetime
    start_line: int
    end: datetime | None = None
    end_line: int | None = None
    output: str = ""

    @property
    def duration_seconds(self) -> float | None:
        if self.end is None:
            return None
        return seconds_between(self.start, self.end)

    @property
    def category(self) -> str:
        return "user_wait" if self.name == "request_user_input" else "tool"


@dataclass
class GapTiming:
    """One classified wait gap between adjacent JSONL records."""

    start: datetime
    end: datetime
    duration_seconds: float
    category: str
    from_event: str
    to_event: str
    start_line: int
    end_line: int


@dataclass
class SlowSummary:
    """Slow wait summaries for the currently visible task."""

    slowest_user_wait: str = "无（本任务无用户确认）"
    slowest_user_wait_call: ToolCallTiming | None = None
    slowest_tool: str = "无（本任务无工具调用）"
    slowest_tool_call: ToolCallTiming | None = None
    longest_gap: str = "无（本任务无长响应等待）"
    longest_gap_detail: GapTiming | None = None
    current_gap: str = "任务已结束"
    current_gap_active: bool = False


@dataclass
class TaskHistory:
    """The parsed activity and request state for one task in a session."""

    index: int
    count: int
    prompt: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    aborted_at: datetime | None = None
    # Codex keeps rolled-back attempts in the JSONL even though the Desktop
    # conversation no longer shows them; their token usage is still real.
    rolled_back: bool = False
    final_answer_at: datetime | None = None
    last_event_time: datetime | None = None
    request: RequestTokens = field(default_factory=RequestTokens)
    request_history: list[RequestRound] = field(default_factory=list)
    activity: Activity = field(default_factory=Activity)
    activity_steps: list[ActivityStep] = field(default_factory=list)
    last_output: Activity = field(default_factory=Activity)
    slow: SlowSummary = field(default_factory=SlowSummary)
    error: str = ""
    turn_id: str = ""


@dataclass
class ParsedSession:
    """Parsed view of a Codex session JSONL file."""

    session_path: Path | None = None
    session_id: str = "n/a"
    session_title: str = ""
    task_prompt: str = ""
    task_index: int = 0
    task_count: int = 0
    cwd: str = ""
    model_provider: str = "unknown"
    profile_name: str = ""
    originator: str = ""
    session_source: str = ""
    client_kind: str = "unknown"
    thread_source: str = ""
    parent_thread_id: str = ""
    agent_nickname: str = ""
    is_subagent: bool = False
    status: str = "starting"
    error: str = ""
    refreshed_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    session_started_at: datetime | None = None
    last_event_time: datetime | None = None
    last_file_mtime: float | None = None
    confirmed: ConfirmedTokens = field(default_factory=ConfirmedTokens)
    estimate: EstimateTokens = field(default_factory=EstimateTokens)
    request: RequestTokens = field(default_factory=RequestTokens)
    request_history: list[RequestRound] = field(default_factory=list)
    session_request_history: list[RequestRound] = field(default_factory=list)
    activity_tasks: list[TaskHistory] = field(default_factory=list)
    activity: Activity = field(default_factory=Activity)
    activity_steps: list[ActivityStep] = field(default_factory=list)
    last_output: Activity = field(default_factory=Activity)
    slow: SlowSummary = field(default_factory=SlowSummary)
    line_count: int = 0
    token_events: int = 0
    task_started_at: datetime | None = None
    task_completed_at: datetime | None = None
    task_aborted_at: datetime | None = None
    final_answer_at: datetime | None = None
    selection_source: str = "activity"
    renderer_session_id: str = ""
    selection_seq: int = 0
    selection_observed_at_ms: int = 0
    # Renderer-only composer state.  It exists before a new-session JSONL file
    # is persisted and is intentionally kept separate from task_prompt, which
    # is sourced from durable session records.
    composer_draft: str = ""
    composer_draft_updated_at_ms: int = 0
    composer_send_requested: bool = False
    # Renderer-observed label of the current Codex work disclosure.  This is
    # deliberately separate from the durable JSONL timeline because it mirrors
    # the title the user can currently see in the Codex UI.
    renderer_collapsed_title: str = ""
    # Multiple native work disclosures in the current materialized turn are
    # ambiguous; consumers must not mix their title and activity rows.
    renderer_collapsed_disclosure_ambiguous: bool = False
    follow_state: str = ""
    follow_reason: str = ""
    follow_timing: dict[str, int] = field(default_factory=dict)
    match_candidates: list[dict[str, object]] = field(default_factory=list)
    today_tokens: int = 0
    today_cost_usd: float | None = 0.0
    week_tokens: int = 0
    week_cost_usd: float | None = 0.0
    week_before_today_tokens: int = 0
    week_before_today_cost_usd: float | None = 0.0
    week_adjustment_usd: float = 0.0
    # Lifetime usage for this session root plus forked subagents (local JSONL).
    family_tokens: int = 0
    family_cost_usd: float | None = None
    family_member_count: int = 0
    daily_limit_usd: float = 100.0
    weekly_limit_usd: float = 400.0
    day_start: datetime | None = None
    week_start: datetime | None = None
    budget_warnings: list[str] = field(default_factory=list)
    budget_error: str = ""
    # False while the renderer cold-start path defers the historical budget
    # scan: today/week amounts are still placeholder zeros and must not be
    # rendered as if they were measured. True everywhere else.
    budget_ready: bool = True
    active_work_items: list[WorkStatusItem] = field(default_factory=list)
    estimate_base: BaseEstimate = field(default_factory=BaseEstimate)
    reading_activity: ReadingActivity = field(default_factory=ReadingActivity)
    task_turn_id: str = ""


@dataclass
class JsonlTailState:
    """Incremental read state for one Codex session JSONL file."""

    path: Path | None = None
    file_id: tuple[str, int, int] | None = None
    offset: int = 0
    line_count: int = 0
    file_size: int | None = None
    last_file_mtime: float | None = None
    last_complete_line: str = ""
    records: list[dict[str, Any]] = field(default_factory=list)
    snapshot: ParsedSession = field(default_factory=ParsedSession)


class CostEstimator:
    """Small adapter that keeps parsing resilient to unknown model names."""

    def __init__(
        self,
        calculator: UsageCalculator | None = None,
        default_model: str = DEFAULT_MODEL,
    ) -> None:
        self._calculator = calculator or UsageCalculator()
        self._default_model = default_model

    def calculate(
        self,
        model: str,
        input_tokens: int | None,
        cached_tokens: int | None,
        output_tokens: int | None,
        reasoning_tokens: int | None = 0,
        *,
        cache_write_tokens: int | None = 0,
        provider: str = "",
        base_url: str = "",
        occurred_at: datetime | None = None,
    ) -> float | None:
        if input_tokens is None or output_tokens is None:
            return None
        try:
            return self._calculator.calculate_cost_usd(
                model_name=model or self._default_model,
                input_tokens=input_tokens,
                cached_input_tokens=cached_tokens or 0,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens or 0,
                cache_write_tokens=cache_write_tokens or 0,
                provider=provider,
                base_url=base_url,
                occurred_at=occurred_at,
            )
        except (TypeError, ValueError):
            return None


def classify_session_client(originator: object, source: object) -> str:
    """Classify session ownership once at the JSONL boundary for all UI consumers."""
    originator_text = str(originator or "").strip().lower()
    if "codex desktop" in originator_text:
        return "app"
    if (
        "codex-tui" in originator_text
        or "codex cli" in originator_text
        or originator_text == "codex_exec"
    ):
        return "cli"
    if isinstance(source, str):
        source_text = source.strip().lower()
        if source_text == "cli":
            return "cli"
        if source_text == "vscode":
            return "app"
    return "unknown"


def extract_session_thread_identity(
    payload: Mapping[str, Any] | None,
) -> tuple[str, str, str, bool]:
    """Return thread_source, parent_thread_id, agent_nickname, is_subagent from session_meta."""
    if not isinstance(payload, Mapping):
        return "", "", "", False
    thread_source = str(payload.get("thread_source") or "").strip()
    parent_thread_id = str(payload.get("parent_thread_id") or "").strip()
    agent_nickname = str(payload.get("agent_nickname") or "").strip()
    source = payload.get("source")
    if not parent_thread_id and isinstance(source, Mapping):
        subagent = source.get("subagent")
        if isinstance(subagent, Mapping):
            spawn = subagent.get("thread_spawn")
            if isinstance(spawn, Mapping):
                parent_thread_id = str(spawn.get("parent_thread_id") or "").strip()
                if not agent_nickname:
                    agent_nickname = str(spawn.get("agent_nickname") or "").strip()
    is_subagent = thread_source.lower() == "subagent" or bool(parent_thread_id)
    return thread_source, parent_thread_id, agent_nickname, is_subagent


class JsonlSessionParser:
    """Parse Codex session JSONL records into token and request state."""

    def __init__(
        self,
        estimate_enabled: bool = True,
        cost_estimator: CostEstimator | None = None,
    ) -> None:
        self.estimate_enabled = estimate_enabled
        self.cost_estimator = cost_estimator or CostEstimator()

    def parse_file(
        self,
        path: Path,
        session_id: str | None = None,
        sse_tracker: SseRequestStateMachine | None = None,
    ) -> ParsedSession:
        """Parse a session file, skipping incomplete trailing JSONL rows."""
        snapshot = ParsedSession(session_path=path)
        if not path.exists():
            snapshot.status = "missing"
            snapshot.error = f"Session file not found: {path}"
            return snapshot

        records = self.load_records_lenient(path)
        try:
            snapshot.last_file_mtime = path.stat().st_mtime
        except OSError:
            snapshot.last_file_mtime = None
        return self.parse_records(records, path, session_id, sse_tracker, snapshot)

    def parse_file_incremental(
        self,
        path: Path,
        state: JsonlTailState | None = None,
        *,
        session_id: str | None = None,
        sse_tracker: SseRequestStateMachine | None = None,
    ) -> tuple[ParsedSession, JsonlTailState]:
        """Parse a session file by reading only bytes appended since ``state``."""
        snapshot = ParsedSession(session_path=path)
        if not path.exists():
            snapshot.status = "missing"
            snapshot.error = f"Session file not found: {path}"
            return snapshot, JsonlTailState(path=path, snapshot=snapshot)

        try:
            tail = self.load_records_incremental(path, state)
        except OSError as exc:
            snapshot.status = "missing"
            snapshot.error = f"Session file unavailable: {exc}"
            tail = state or JsonlTailState(path=path)
            tail.snapshot = snapshot
            return snapshot, tail

        snapshot.last_file_mtime = tail.last_file_mtime
        parsed = self.parse_records(
            tail.records, path, session_id, sse_tracker, snapshot
        )
        tail.snapshot = parsed
        return parsed, tail

    def load_records_incremental(
        self,
        path: Path,
        state: JsonlTailState | None = None,
    ) -> JsonlTailState:
        """Read appended JSONL records without building a full session snapshot.

        Aggregate usage scans need the decoded records and a reusable byte offset,
        but not request, activity, gap, or renderer state. Keeping that lower-level
        operation separate avoids fully parsing every historical session during a
        cold budget scan while preserving incremental refreshes for changed files.
        """
        stat = path.stat()
        file_id = self._file_id(path, stat)
        if self._tail_state_matches(path, stat, file_id, state):
            tail = state
        else:
            tail = JsonlTailState(path=path, file_id=file_id)
        self._read_tail_records(path, tail)
        tail.file_id = file_id
        tail.file_size = stat.st_size
        tail.last_file_mtime = stat.st_mtime
        return tail

    def parse_file_tail_preview(
        self,
        path: Path,
        *,
        session_id: str | None = None,
        max_bytes: int = 256 * 1024,
    ) -> ParsedSession:
        """Build a cheap, recent-state preview without decoding a whole log.

        This is intentionally only for a cold renderer session switch.  The
        complete incremental state is hydrated in the background afterwards;
        reading the tail keeps the renderer's click path independent of the
        size of an old conversation log.
        """
        snapshot = ParsedSession(session_path=path)
        try:
            stat = path.stat()
            file_size = int(stat.st_size)
            start = max(0, file_size - max(1, int(max_bytes)))
            with path.open("rb") as handle:
                handle.seek(start)
                chunk = handle.read()
        except OSError as exc:
            snapshot.status = "missing"
            snapshot.error = f"Session file unavailable: {exc}"
            return snapshot

        if start:
            first_newline = chunk.find(b"\n")
            chunk = chunk[first_newline + 1 :] if first_newline >= 0 else b""
        complete_length = (
            len(chunk) if chunk.endswith(b"\n") else chunk.rfind(b"\n") + 1
        )
        records: list[dict[str, Any]] = []
        for line_number, raw_line in enumerate(chunk[:complete_length].splitlines(), 1):
            try:
                record = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict):
                continue
            record["_line"] = line_number
            record["_dt"] = parse_timestamp(record.get("timestamp"))
            records.append(record)

        snapshot.last_file_mtime = stat.st_mtime
        preview = self.parse_records(
            records,
            path,
            session_id,
            snapshot=snapshot,
        )
        if start:
            # A bounded tail can price only the rounds inside its byte window,
            # not the whole cumulative token total reported by Codex.
            preview.confirmed.cumulative_cost_usd = None
        if preview.status == "parsed":
            preview.status = "loading"
        return preview

    def _file_id(self, path: Path, stat: os.stat_result) -> tuple[str, int, int]:
        try:
            resolved = str(path.resolve(strict=False))
        except OSError:
            resolved = str(path.absolute())
        return (
            resolved,
            int(getattr(stat, "st_dev", 0) or 0),
            int(getattr(stat, "st_ino", 0) or 0),
        )

    def _tail_state_matches(
        self,
        path: Path,
        stat: os.stat_result,
        file_id: tuple[str, int, int],
        state: JsonlTailState | None,
    ) -> bool:
        if state is None:
            return False
        if state.path is None:
            return False
        try:
            same_path = state.path.resolve(strict=False) == path.resolve(strict=False)
        except OSError:
            same_path = state.path == path
        if not same_path:
            return False
        if state.file_id is not None and state.file_id != file_id:
            return False
        return int(stat.st_size) >= int(state.offset)

    def _read_tail_records(self, path: Path, state: JsonlTailState) -> None:
        with path.open("rb") as handle:
            handle.seek(max(0, int(state.offset)))
            chunk = handle.read()
        if not chunk:
            return

        complete_length = (
            len(chunk) if chunk.endswith(b"\n") else chunk.rfind(b"\n") + 1
        )
        if complete_length <= 0:
            return
        complete = chunk[:complete_length]
        state.offset += complete_length
        for raw_line in complete.splitlines(keepends=True):
            state.line_count += 1
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                text = stripped.decode("utf-8")
            except UnicodeDecodeError:
                continue
            state.last_complete_line = text
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            record["_line"] = state.line_count
            record["_dt"] = parse_timestamp(record.get("timestamp"))
            state.records.append(record)

    def parse_records(
        self,
        records: Sequence[dict[str, Any]],
        path: Path | None = None,
        session_id: str | None = None,
        sse_tracker: SseRequestStateMachine | None = None,
        snapshot: ParsedSession | None = None,
    ) -> ParsedSession:
        """Parse already loaded Codex JSONL records."""
        parsed = snapshot or ParsedSession(session_path=path)
        parsed.line_count = len(records)
        if not records:
            parsed.status = "waiting"
            parsed.error = "Session file has no complete JSONL records"
            if path is not None:
                parsed.session_id = session_id or short_session_id(path)
            return parsed

        parsed.session_id = session_id or self.session_id_from_records(records, path)
        parsed.session_started_at = self.session_started_at(records)
        parsed.cwd = self.session_cwd(records)
        parsed.model_provider = self.session_model_provider(records)
        parsed.profile_name = self.session_profile_name(records)
        parsed.originator = self.session_originator(records)
        raw_session_source = self.session_source(records)
        parsed.session_source = (
            raw_session_source.strip() if isinstance(raw_session_source, str) else ""
        )
        parsed.client_kind = classify_session_client(
            parsed.originator, raw_session_source
        )
        (
            parsed.thread_source,
            parsed.parent_thread_id,
            parsed.agent_nickname,
            parsed.is_subagent,
        ) = extract_session_thread_identity(self.session_meta_payload(records))
        parsed.last_event_time = records[-1].get("_dt")
        parsed.activity = self.latest_activity(records)
        parsed.last_output = self.latest_output(records)
        parsed.slow = self.slow_summary(records, parsed.last_event_time)
        token_index = self.apply_confirmed_tokens(parsed, records)
        if self.estimate_enabled:
            parsed.estimate = self.estimate_since_last_token(records, token_index)

        task_started_index, task_started_at = self.latest_task_started(records)
        parsed.task_started_at = task_started_at
        parsed.task_turn_id = self.task_turn_id(records, task_started_index)
        parsed.task_prompt = self.latest_task_prompt(records, task_started_index)
        parsed.task_index, parsed.task_count = self.task_ordinal(
            records,
            task_started_index,
        )
        parsed.activity_steps = self.activity_steps(records, task_started_index)
        parsed.task_completed_at = self.latest_task_completed_after(
            records,
            task_started_index,
        )
        parsed.task_aborted_at = self.latest_task_aborted_after(
            records,
            task_started_index,
        )
        parsed.final_answer_at = self.latest_final_answer_after(
            records,
            task_started_index,
        )
        parsed.session_request_history = self.reindex_rounds(
            self.token_rounds_since_task(records, None)
        )
        jsonl_rounds = self.token_rounds_since_task(records, task_started_index)
        parsed.request = self.build_request_tokens(
            parsed,
            task_started_at,
            self.latest_model(records),
            jsonl_rounds,
            sse_tracker,
        )
        parsed.activity_tasks = self.build_task_history(
            records,
            parsed,
            task_started_index,
        )
        parsed.status = "parsed"
        return parsed

    def load_records_lenient(self, path: Path) -> list[dict[str, Any]]:
        """Read JSONL records while tolerating partially written lines."""
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                text = line.strip()
                if not text:
                    continue
                try:
                    record = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                record["_line"] = line_number
                record["_dt"] = parse_timestamp(record.get("timestamp"))
                records.append(record)
        return records

    def session_id_from_records(
        self, records: Sequence[Mapping[str, Any]], path: Path | None = None
    ) -> str:
        for record in records:
            if record.get("type") != "session_meta":
                continue
            payload = record.get("payload") or {}
            if isinstance(payload, Mapping) and payload.get("id"):
                return str(payload["id"])
        return short_session_id(path) if path is not None else "n/a"

    def session_started_at(
        self, records: Sequence[Mapping[str, Any]]
    ) -> datetime | None:
        for record in records:
            if record.get("type") != "session_meta":
                continue
            timestamp = record.get("_dt")
            if isinstance(timestamp, datetime):
                return timestamp
        first_timestamp = records[0].get("_dt") if records else None
        return first_timestamp if isinstance(first_timestamp, datetime) else None

    def session_cwd(self, records: Sequence[Mapping[str, Any]]) -> str:
        for record in records:
            if record.get("type") != "session_meta":
                continue
            payload = record.get("payload") or {}
            if isinstance(payload, Mapping) and payload.get("cwd"):
                return str(payload.get("cwd") or "").strip()
        return ""

    def session_model_provider(self, records: Sequence[Mapping[str, Any]]) -> str:
        """Return the stable billing channel from session metadata."""
        payload = self.session_meta_payload(records)
        return str(payload.get("model_provider") or "").strip().lower() or "unknown"

    def session_profile_name(self, records: Sequence[Mapping[str, Any]]) -> str:
        """Return the CLI profile label recorded for this session.

        Older session metadata does not persist the command-line profile name
        separately. In that format the selected model provider is the only
        stable session-level label available, so retain it as a compatibility
        fallback for the overlay.
        """
        payload = self.session_meta_payload(records)
        for key in ("profile", "profile_name", "profileName"):
            profile = str(payload.get(key) or "").strip()
            if profile:
                return profile
        if payload.get("model_provider"):
            return self.session_model_provider(records)
        return ""

    def session_base_url(self, records: Sequence[Mapping[str, Any]]) -> str:
        """Return an explicit API base URL when Codex recorded one in metadata."""
        payload = self.session_meta_payload(records)
        return str(
            payload.get("base_url")
            or payload.get("baseUrl")
            or payload.get("api_base")
            or ""
        ).strip()

    def session_originator(self, records: Sequence[Mapping[str, Any]]) -> str:
        return str(self.session_meta_payload(records).get("originator") or "").strip()

    def session_source(self, records: Sequence[Mapping[str, Any]]) -> object:
        return self.session_meta_payload(records).get("source")

    def session_meta_payload(
        self, records: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Any]:
        for record in records:
            if record.get("type") != "session_meta":
                continue
            payload = record.get("payload") or {}
            if isinstance(payload, Mapping):
                return payload
        return {}

    def latest_task_started(
        self, records: Sequence[Mapping[str, Any]]
    ) -> tuple[int | None, datetime | None]:
        for index in range(len(records) - 1, -1, -1):
            record = records[index]
            payload = record.get("payload") or {}
            if (
                record.get("type") == "event_msg"
                and isinstance(payload, Mapping)
                and payload.get("type") == "task_started"
            ):
                return index, record.get("_dt")
        return None, None

    def latest_task_prompt(
        self,
        records: Sequence[Mapping[str, Any]],
        task_started_index: int | None,
    ) -> str:
        """Return the user message that started the currently visible task."""
        if not records:
            return ""

        start_index = 0 if task_started_index is None else task_started_index

        for record in reversed(records[start_index:]):
            text = compact_text(_user_message_text(record), 260)
            if text:
                return text

        if task_started_index is None:
            return ""

        for record in reversed(records[:task_started_index]):
            text = compact_text(_user_message_text(record), 260)
            if text:
                return text
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            if record.get("type") == "event_msg" and payload.get("type") in {
                "task_complete",
                "turn_aborted",
                "task_started",
            }:
                break
        return ""

    def task_turn_id(
        self,
        records: Sequence[Mapping[str, Any]],
        task_started_index: int | None,
    ) -> str:
        """Return the stable Codex turn id for one task-start marker."""
        if task_started_index is None or not (0 <= task_started_index < len(records)):
            return ""
        payload = records[task_started_index].get("payload") or {}
        if not isinstance(payload, Mapping):
            return ""
        return str(payload.get("turn_id") or "").strip()

    def task_ordinal(
        self,
        records: Sequence[Mapping[str, Any]],
        task_started_index: int | None,
    ) -> tuple[int, int]:
        """Return the current task number and total known tasks in the session."""
        task_start_indices: list[int] = []
        user_message_indices: list[int] = []
        for index, record in enumerate(records):
            payload = record.get("payload") or {}
            if record.get("type") != "event_msg" or not isinstance(payload, Mapping):
                continue
            payload_type = payload.get("type")
            if payload_type == "task_started":
                task_start_indices.append(index)
            elif payload_type == "user_message" and compact_text(
                payload.get("message"), 8
            ):
                user_message_indices.append(index)

        if task_start_indices:
            if task_started_index is None:
                return len(task_start_indices), len(task_start_indices)
            current = sum(
                1 for index in task_start_indices if index <= task_started_index
            )
            return current, len(task_start_indices)

        if user_message_indices:
            return len(user_message_indices), len(user_message_indices)
        return 0, 0

    def task_start_indices(
        self, records: Sequence[Mapping[str, Any]]
    ) -> list[int]:
        """Return task markers in file order for activity history grouping."""
        indices: list[int] = []
        for index, record in enumerate(records):
            payload = record.get("payload") or {}
            if (
                record.get("type") == "event_msg"
                and isinstance(payload, Mapping)
                and payload.get("type") == "task_started"
            ):
                indices.append(index)
        return indices

    def task_rolled_back_ordinals(
        self, records: Sequence[Mapping[str, Any]]
    ) -> set[int]:
        """Replay thread rollbacks to find task ordinals no longer visible.

        Every user submission (including retries and edited resends) appends a
        task_started record, while thread_rolled_back drops trailing turns from
        the live thread shown by Codex Desktop.  Tasks removed by any rollback
        have no chat anchor to locate, though their token usage stays billed.
        """
        rolled: set[int] = set()
        live: list[int] = []
        ordinal = 0
        for record in records:
            payload = record.get("payload") or {}
            if record.get("type") != "event_msg" or not isinstance(payload, Mapping):
                continue
            payload_type = payload.get("type")
            if payload_type == "task_started":
                ordinal += 1
                live.append(ordinal)
            elif payload_type == "thread_rolled_back":
                try:
                    num_turns = int(payload.get("num_turns") or 0)
                except (TypeError, ValueError):
                    num_turns = 0
                for _ in range(max(0, min(num_turns, len(live)))):
                    rolled.add(live.pop())
        return rolled

    def task_prompt_in_range(
        self,
        records: Sequence[Mapping[str, Any]],
        segment_start: int,
        task_started_index: int,
        segment_end: int,
    ) -> str:
        """Find the user message belonging to one task segment."""
        for record in reversed(records[task_started_index:segment_end]):
            payload = record.get("payload") or {}
            if (
                record.get("type") == "event_msg"
                and isinstance(payload, Mapping)
                and payload.get("type") == "user_message"
            ):
                text = compact_text(payload.get("message"), 260)
                if text:
                    return text

        for record in reversed(records[segment_start:task_started_index]):
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            if (
                record.get("type") == "event_msg"
                and payload.get("type") == "user_message"
            ):
                text = compact_text(payload.get("message"), 260)
                if text:
                    return text
            if (
                record.get("type") == "event_msg"
                and payload.get("type") == "task_started"
            ):
                break
        return ""

    def task_terminal_times(
        self,
        records: Sequence[Mapping[str, Any]],
        segment_start: int,
        segment_end: int,
    ) -> tuple[datetime | None, datetime | None]:
        completed_at: datetime | None = None
        aborted_at: datetime | None = None
        for record in records[segment_start:segment_end]:
            payload = record.get("payload") or {}
            if record.get("type") != "event_msg" or not isinstance(payload, Mapping):
                continue
            payload_type = payload.get("type")
            if payload_type == "task_complete":
                completed_at = record.get("_dt")
            elif payload_type == "turn_aborted":
                aborted_at = record.get("_dt")
        return completed_at, aborted_at

    def final_answer_in_range(
        self,
        records: Sequence[Mapping[str, Any]],
        segment_start: int,
        segment_end: int,
    ) -> datetime | None:
        for record in reversed(records[segment_start:segment_end]):
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            if str(payload.get("phase") or "") != "final_answer":
                continue
            record_type = record.get("type")
            payload_type = payload.get("type")
            if record_type == "event_msg" and payload_type == "agent_message":
                if compact_text(payload.get("message"), 8):
                    return record.get("_dt")
            if record_type == "response_item" and payload_type == "message":
                role = response_message_role(payload)
                if role and role != "assistant":
                    continue
                if compact_text(message_text(payload), 8):
                    return record.get("_dt")
        return None

    def request_from_task_rows(
        self,
        rows: Sequence[RequestRound],
        model: str,
        started_at: datetime | None,
        completed_at: datetime | None,
        aborted_at: datetime | None,
    ) -> RequestTokens:
        last = rows[-1] if rows else None
        if last is None:
            return RequestTokens(
                status="waiting",
                model=model,
                source="jsonl",
                started_at=started_at,
                completed_at=aborted_at or completed_at,
                updated_at=aborted_at or completed_at,
            )
        return RequestTokens(
            status="confirmed" if completed_at or aborted_at else last.status,
            round_index=len(rows),
            model=last.model or model,
            input_tokens=last.input_tokens,
            cached_tokens=last.cached_tokens,
            cache_write_tokens=last.cache_write_tokens,
            output_tokens=last.output_tokens,
            reasoning_tokens=last.reasoning_tokens,
            total_tokens=last.total_tokens,
            estimated=last.estimated,
            source="jsonl",
            updated_at=last.completed_at or last.started_at,
            started_at=started_at or last.started_at,
            completed_at=aborted_at or completed_at or last.completed_at,
            cost_usd=last.cost_usd,
        )

    def build_task_history(
        self,
        records: Sequence[Mapping[str, Any]],
        parsed: ParsedSession,
        latest_task_started_index: int | None,
    ) -> list[TaskHistory]:
        starts = self.task_start_indices(records)
        if not starts:
            return []
        count = len(starts)
        rolled_back_ordinals = self.task_rolled_back_ordinals(records)
        history: list[TaskHistory] = []
        for ordinal, start in enumerate(starts, 1):
            end = starts[ordinal] if ordinal < count else len(records)
            segment_start = starts[ordinal - 2] if ordinal > 1 else 0
            started_at = records[start].get("_dt")
            completed_at, aborted_at = self.task_terminal_times(records, start, end)
            rows = self.reindex_rounds(
                self.token_rounds_since_task(records, start, end)
            )
            is_latest = start == latest_task_started_index
            if is_latest:
                rows = list(parsed.request_history)
                request = parsed.request
                activity = parsed.activity
                activity_steps = list(parsed.activity_steps)
                last_output = parsed.last_output
                slow = parsed.slow
                last_event_time = parsed.last_event_time
            else:
                model = self.latest_model(records[start:end]) or parsed.request.model
                request = self.request_from_task_rows(
                    rows,
                    model,
                    started_at,
                    completed_at,
                    aborted_at,
                )
                activity = self.latest_activity(records[start:end])
                activity_steps = self.activity_steps(records[start:end])
                last_output = self.latest_output(records[start:end])
                slow = self.slow_summary(
                    records[start:end],
                    records[end - 1].get("_dt") if end > start else started_at,
                )
                last_event_time = (
                    records[end - 1].get("_dt") if end > start else started_at
                )
            history.append(
                TaskHistory(
                    index=ordinal,
                    count=count,
                    turn_id=self.task_turn_id(records, start),
                    rolled_back=ordinal in rolled_back_ordinals,
                    prompt=self.task_prompt_in_range(
                        records,
                        segment_start,
                        start,
                        end,
                    ),
                    started_at=started_at,
                    completed_at=completed_at,
                    aborted_at=aborted_at,
                    final_answer_at=self.final_answer_in_range(records, start, end),
                    last_event_time=last_event_time,
                    request=request,
                    request_history=rows,
                    activity=activity,
                    activity_steps=activity_steps,
                    last_output=last_output,
                    slow=slow,
                    error=parsed.error if is_latest else "",
                )
            )
        return history

    def latest_task_completed_after(
        self,
        records: Sequence[Mapping[str, Any]],
        task_started_index: int | None,
    ) -> datetime | None:
        start_index = self.latest_task_segment_start(records, task_started_index)
        for record in reversed(records[start_index:]):
            payload = record.get("payload") or {}
            if (
                record.get("type") == "event_msg"
                and isinstance(payload, Mapping)
                and payload.get("type") == "task_complete"
            ):
                return record.get("_dt")
        return None

    def latest_task_aborted_after(
        self,
        records: Sequence[Mapping[str, Any]],
        task_started_index: int | None,
    ) -> datetime | None:
        start_index = self.latest_task_segment_start(records, task_started_index)
        for record in reversed(records[start_index:]):
            payload = record.get("payload") or {}
            if (
                record.get("type") == "event_msg"
                and isinstance(payload, Mapping)
                and payload.get("type") == "turn_aborted"
            ):
                return record.get("_dt")
        return None

    def latest_final_answer_after(
        self,
        records: Sequence[Mapping[str, Any]],
        task_started_index: int | None,
    ) -> datetime | None:
        start_index = self.latest_task_segment_start(records, task_started_index)
        for record in reversed(records[start_index:]):
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            if str(payload.get("phase") or "") != "final_answer":
                continue
            record_type = record.get("type")
            payload_type = payload.get("type")
            if (
                record_type == "event_msg"
                and payload_type == "agent_message"
                and compact_text(payload.get("message"), 8)
            ):
                return record.get("_dt")
            if record_type == "response_item" and payload_type == "message":
                role = response_message_role(payload)
                if role and role != "assistant":
                    continue
                if compact_text(message_text(payload), 8):
                    return record.get("_dt")
        return None

    def latest_task_segment_start(
        self,
        records: Sequence[Mapping[str, Any]],
        task_started_index: int | None,
    ) -> int:
        """Return the current continuation segment within the latest task."""
        task_start = 0 if task_started_index is None else task_started_index + 1
        for index in range(len(records) - 1, task_start - 1, -1):
            record = records[index]
            payload = record.get("payload") or {}
            if record.get("type") == "compacted":
                return index + 1
            if _user_message_text(record):
                return index + 1
            if record.get("type") == "event_msg" and isinstance(payload, Mapping):
                payload_type = payload.get("type")
                if payload_type == "context_compacted":
                    return index + 1
        return task_start

    def latest_model(self, records: Sequence[Mapping[str, Any]]) -> str:
        for record in reversed(records):
            payload = record.get("payload") or {}
            if (
                record.get("type") == "turn_context"
                and isinstance(payload, Mapping)
                and payload.get("model")
            ):
                return str(payload.get("model") or "")
        return ""

    @staticmethod
    def _history_replay_boundary(
        records: Sequence[Mapping[str, Any]],
    ) -> int | None:
        """Return the structural marker that ends a forked history snapshot."""
        carries_history_snapshot = False
        for record in records:
            if record.get("type") != "session_meta":
                continue
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                break
            thread_id = str(
                payload.get("id")
                or payload.get("thread_id")
                or payload.get("threadId")
                or payload.get("session_id")
                or payload.get("sessionId")
                or ""
            )
            session_id = str(
                payload.get("session_id") or payload.get("sessionId") or ""
            )
            source = payload.get("source")
            carries_history_snapshot = (
                bool(payload.get("forked_from_id"))
                or (isinstance(source, Mapping) and "subagent" in source)
                or bool(session_id and thread_id and session_id != thread_id)
            )
            break
        if not carries_history_snapshot:
            return None

        for index, record in enumerate(records):
            record_type = str(record.get("type") or "")
            payload = record.get("payload") or {}
            if record_type.startswith("inter_agent_communication"):
                return index
            if (
                record_type == "event_msg"
                and isinstance(payload, Mapping)
                and payload.get("type") == "thread_settings_applied"
            ):
                return index
        return None

    @classmethod
    def _history_replay_usage_start(
        cls,
        records: Sequence[Mapping[str, Any]],
        *,
        cluster_seconds: float = HISTORY_REPLAY_CLUSTER_SECONDS,
    ) -> int | None:
        """Return the first record index that is live child usage, not forked history.

        Codex writes parent-thread history into subagent JSONL as a dense
        same-timestamp cluster immediately after the structural boundary. Live
        child turns resume after a real time gap. Skip the dense cluster so
        parent history is not billed again under the child session id.
        """
        boundary = cls._history_replay_boundary(records)
        if boundary is None:
            return None

        cluster_limit = max(0.0, float(cluster_seconds))
        cluster_start: datetime | None = None
        last_in_cluster = boundary - 1
        for index in range(boundary, len(records)):
            record = records[index]
            timestamp = record.get("_dt")
            if not isinstance(timestamp, datetime):
                timestamp = parse_timestamp(record.get("timestamp"))
            if not isinstance(timestamp, datetime):
                continue
            if cluster_start is None:
                cluster_start = timestamp
                last_in_cluster = index
                continue
            gap = abs((timestamp - cluster_start).total_seconds())
            if gap <= cluster_limit:
                last_in_cluster = index
                continue
            break
        if cluster_start is None:
            return boundary
        return last_in_cluster + 1

    def _usage_event_records(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> list[tuple[int, UsageEvent]]:
        """Return per-request deltas, excluding replayed parent-thread history."""
        events: list[tuple[int, UsageEvent]] = []
        current_model = ""
        model_provider = self.session_model_provider(records)
        base_url = self.session_base_url(records)
        replay_start = self._history_replay_usage_start(records)
        previous_total: tuple[int, int, int, int, int] | None = None

        for index, record in enumerate(records):
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            if record.get("type") == "turn_context":
                info = payload.get("info") or {}
                current_model = str(
                    payload.get("model")
                    or (info.get("model") if isinstance(info, Mapping) else "")
                    or current_model
                )
                continue
            if (
                record.get("type") != "event_msg"
                or payload.get("type") != "token_count"
            ):
                continue

            info = payload.get("info") or {}
            if not isinstance(info, Mapping):
                continue
            current_model = str(
                info.get("model")
                or info.get("model_name")
                or payload.get("model")
                or current_model
            )
            cumulative = info.get("total_token_usage")
            usage = (
                cumulative
                if isinstance(cumulative, Mapping)
                else info.get("last_token_usage")
            )
            if not isinstance(usage, Mapping):
                continue

            current = (
                _as_int(usage.get("input_tokens")),
                _as_int(
                    usage.get("cached_input_tokens"),
                    _as_int(usage.get("cache_read_input_tokens")),
                ),
                _as_int(
                    usage.get("cache_write_input_tokens"),
                    _as_int(usage.get("cache_creation_input_tokens")),
                ),
                _as_int(usage.get("output_tokens")),
                _as_int(usage.get("reasoning_output_tokens")),
            )
            if isinstance(cumulative, Mapping):
                if previous_total is None:
                    delta = current
                else:
                    delta = tuple(
                        max(0, current[position] - previous_total[position])
                        for position in range(len(current))
                    )
                previous_total = current
            else:
                delta = current

            (
                input_tokens,
                cached_tokens,
                cache_write_tokens,
                output_tokens,
                reasoning_tokens,
            ) = delta
            cached_tokens = min(cached_tokens, input_tokens)
            cache_write_tokens = min(
                cache_write_tokens,
                max(0, input_tokens - cached_tokens),
            )
            if not (input_tokens or output_tokens):
                continue
            if replay_start is not None and index < replay_start:
                continue

            occurred_at = record.get("_dt")
            event_line = _as_int(record.get("_line"))
            cost_usd = self.cost_estimator.calculate(
                current_model,
                input_tokens,
                cached_tokens,
                output_tokens,
                reasoning_tokens,
                cache_write_tokens=cache_write_tokens,
                provider=model_provider,
                base_url=base_url,
                occurred_at=occurred_at
                if isinstance(occurred_at, datetime)
                else None,
            )

            events.append(
                (
                    index,
                    UsageEvent(
                        timestamp=record.get("_dt"),
                        model=current_model,
                        input_tokens=input_tokens,
                        cached_tokens=cached_tokens,
                        output_tokens=output_tokens,
                        reasoning_tokens=min(reasoning_tokens, output_tokens),
                        total_tokens=input_tokens + output_tokens,
                        cost_usd=cost_usd,
                        model_provider=model_provider,
                        cache_write_tokens=cache_write_tokens,
                        line=event_line,
                    ),
                )
            )
        return events

    def apply_confirmed_tokens(
        self,
        snapshot: ParsedSession,
        records: Sequence[Mapping[str, Any]],
    ) -> int:
        """Apply confirmed per-request deltas and return the last event index."""
        indexed_events = self._usage_event_records(records)
        if not indexed_events:
            return -1

        session_cost = sum(
            float(event.cost_usd or 0.0) for _index, event in indexed_events
        )
        has_session_cost = any(
            event.cost_usd is not None for _index, event in indexed_events
        )
        last_token_index, last = indexed_events[-1]
        events = [event for _index, event in indexed_events]
        snapshot.token_events = len(events)
        snapshot.confirmed = ConfirmedTokens(
            last_total=last.total_tokens,
            last_input=last.input_tokens,
            last_cached=last.cached_tokens,
            last_cache_write=last.cache_write_tokens,
            last_output=last.output_tokens,
            last_reasoning=last.reasoning_tokens,
            cumulative_total=sum(event.total_tokens for event in events),
            cumulative_input=sum(event.input_tokens for event in events),
            cumulative_cached=sum(event.cached_tokens for event in events),
            cumulative_cache_write=sum(event.cache_write_tokens for event in events),
            cumulative_output=sum(event.output_tokens for event in events),
            cumulative_reasoning=sum(event.reasoning_tokens for event in events),
            cumulative_cost_usd=round(session_cost, 6) if has_session_cost else None,
            timestamp=last.timestamp,
            line=last.line,
        )
        return last_token_index

    def usage_events(self, records: Sequence[Mapping[str, Any]]) -> list[UsageEvent]:
        """Return confirmed per-request deltas across a session."""
        return [event for _index, event in self._usage_event_records(records)]

    def summarize_usage_events(
        self,
        events: Sequence[UsageEvent],
        start_at: datetime,
    ) -> UsageSummary:
        """Aggregate confirmed usage events from ``start_at`` onward."""
        summary = UsageSummary()
        for event in events:
            if event.timestamp is None:
                continue
            event_time = event.timestamp.astimezone(start_at.tzinfo)
            if event_time < start_at:
                continue
            summary.total_event_count += 1
            if event.cost_usd is not None:
                summary.priced_event_count += 1
            summary.tokens += event.total_tokens
            summary.input_tokens += event.input_tokens
            summary.cached_tokens += event.cached_tokens
            summary.cache_write_tokens += event.cache_write_tokens
            summary.output_tokens += event.output_tokens
            summary.reasoning_tokens += event.reasoning_tokens
            summary.cost_usd += float(event.cost_usd or 0.0)
        summary.cost_usd = round(summary.cost_usd, 6)
        return summary

    def estimate_since_last_token(
        self, records: Sequence[Mapping[str, Any]], token_index: int
    ) -> EstimateTokens:
        estimate = EstimateTokens()
        sources: list[str] = []
        for record in records[token_index + 1 :]:
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            record_type = record.get("type")
            payload_type = payload.get("type")
            timestamp = record.get("_dt")
            contributed = False

            if record_type == "event_msg" and payload_type == "user_message":
                value = estimate_tokens(payload.get("message"))
                estimate.input_tokens += value
                if value:
                    sources.append(f"user~{value}")
                    contributed = True
            elif record_type == "response_item" and payload_type == "message":
                if is_turn_aborted_message(payload):
                    continue
                role = response_message_role(payload)
                if role and role != "assistant":
                    continue
                value = estimate_tokens(message_text(payload))
                estimate.output_tokens += value
                if value:
                    sources.append(f"assistant~{value}")
                    contributed = True
            elif record_type == "response_item" and payload_type == "function_call":
                value = estimate_tokens(payload.get("arguments"))
                estimate.output_tokens += value
                if value:
                    sources.append(f"tool-call~{value}")
                    contributed = True
            elif (
                record_type == "response_item"
                and payload_type == "function_call_output"
            ):
                value = estimate_tokens(payload.get("output"))
                estimate.tool_tokens += value
                if value:
                    sources.append(f"tool-output~{value}")
                    contributed = True

            if contributed and isinstance(timestamp, datetime):
                if estimate.started_at is None:
                    estimate.started_at = timestamp
                estimate.updated_at = timestamp

        estimate.total_tokens = (
            estimate.input_tokens + estimate.output_tokens + estimate.tool_tokens
        )
        estimate.source = ", ".join(sources[-4:]) if sources else "no pending estimate"
        return estimate

    def round_activity_entry(
        self,
        record_type: Any,
        payload_type: Any,
        payload: Mapping[str, Any],
    ) -> tuple[str, str, str] | None:
        """Return (side, label, text) for content that explains one request round."""
        if record_type == "event_msg" and payload_type == "user_message":
            text = str(payload.get("message") or "").strip()
            return ("input", "输入", text) if text else None
        if record_type == "event_msg" and payload_type == "agent_message":
            text = str(payload.get("message") or "").strip()
            return ("output", "输出", text) if text else None
        if record_type == "response_item" and payload_type == "message":
            if is_turn_aborted_message(payload):
                return None
            role = response_message_role(payload)
            if role and role != "assistant":
                return None
            text = message_text(payload).strip()
            return ("output", "输出", text) if text else None
        if record_type == "response_item" and payload_type == "function_call":
            name = str(payload.get("name") or "工具调用").strip()
            args = str(payload.get("arguments") or "").strip()
            text = f"{name} {args}".strip()
            return ("output", "工具调用", text) if text else None
        if record_type == "response_item" and payload_type == "custom_tool_call":
            text = tool_call_detail(payload)
            return ("output", "工具调用", text) if text else None
        if record_type == "response_item" and payload_type == "function_call_output":
            text = str(payload.get("output") or "").strip()
            return ("input", "工具返回", text) if text else None
        if (
            record_type == "response_item"
            and payload_type == "custom_tool_call_output"
        ):
            text = str(payload.get("output") or "").strip()
            return ("input", "工具返回", text) if text else None
        if record_type == "response_item" and payload_type == "reasoning":
            text = reasoning_text(payload).strip()
            return ("output", "推理", text) if text else None
        if record_type == "event_msg" and payload_type in {
            "item_started",
            "item_updated",
            "item_completed",
        }:
            item = payload.get("item") or {}
            if isinstance(item, Mapping):
                item_type = str(item.get("type") or "").replace("_", "").casefold()
                if item_type == "commandexecution":
                    text = command_execution_text(item)
                    return ("output", "命令", text) if text else None
        return None

    def round_activity_summary(
        self,
        entries: Sequence[tuple[str, str, str]],
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
    ) -> tuple[str, str]:
        """Choose display and copy text for a request round."""
        input_entries = [item for item in entries if item[0] == "input"]
        output_entries = [item for item in entries if item[0] == "output"]
        output_weight = int(output_tokens or 0) + int(reasoning_tokens or 0)
        input_weight = max(0, int(input_tokens or 0) - int(cached_tokens or 0))
        preferred: tuple[str, str, str] | None = None
        if output_weight >= input_weight and output_entries:
            preferred = output_entries[-1]
        elif input_entries:
            preferred = input_entries[-1]
        elif output_entries:
            preferred = output_entries[-1]

        if preferred is None:
            breakdown = (
                f"输入 {int(input_tokens or 0):,}，缓存 {int(cached_tokens or 0):,}，"
                f"输出 {int(output_tokens or 0):,}，推理 {int(reasoning_tokens or 0):,}"
            )
            return f"消耗集中在 {breakdown}", breakdown

        _side, label, text = preferred
        display = f"{label}：{compact_text(text, 92)}"
        # Keep the clipboard source complete; display formatting remains compact.
        copy_text = text
        return display, f"{label}：\n{copy_text}"

    def token_rounds_since_task(
        self,
        records: Sequence[Mapping[str, Any]],
        task_started_index: int | None,
        end_index: int | None = None,
    ) -> list[RequestRound]:
        rounds: list[RequestRound] = []
        model_provider = self.session_model_provider(records)
        current_model = ""
        start_index = 0 if task_started_index is None else task_started_index + 1
        for record in records[:start_index]:
            payload = record.get("payload") or {}
            if (
                record.get("type") == "turn_context"
                and isinstance(payload, Mapping)
                and payload.get("model")
            ):
                current_model = str(payload.get("model") or current_model)

        last_cumulative_total: int | None = None
        seen_usage_keys: set[tuple[Any, ...]] = set()
        round_activity_entries: list[tuple[str, str, str]] = []
        round_started_at = (
            records[task_started_index].get("_dt")
            if task_started_index is not None
            else None
        )
        for record in records[start_index:end_index]:
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            record_type = record.get("type")
            if record_type == "turn_context":
                current_model = str(payload.get("model") or current_model)
                continue

            timestamp = record.get("_dt")
            if round_started_at is None:
                round_started_at = timestamp
            if record_type != "event_msg" or payload.get("type") != "token_count":
                entry = self.round_activity_entry(
                    record_type, payload.get("type"), payload
                )
                if entry is not None:
                    round_activity_entries.append(entry)
                continue

            info = payload.get("info") or {}
            if not isinstance(info, Mapping):
                continue
            usage = info.get("last_token_usage") or {}
            cumulative = info.get("total_token_usage") or {}
            if not isinstance(usage, Mapping) or not isinstance(cumulative, Mapping):
                continue

            input_tokens = _as_int(usage.get("input_tokens"))
            cached_tokens = _as_int(usage.get("cached_input_tokens"))
            cache_write_tokens = _as_int(usage.get("cache_write_input_tokens"))
            output_tokens = _as_int(usage.get("output_tokens"))
            reasoning_tokens = _as_int(usage.get("reasoning_output_tokens"))
            total_tokens = _as_int(
                usage.get("total_tokens"), input_tokens + output_tokens
            )
            cumulative_total = _as_int(cumulative.get("total_tokens"))
            if not (input_tokens or output_tokens or reasoning_tokens):
                continue
            activity_summary, copy_text = self.round_activity_summary(
                round_activity_entries,
                input_tokens,
                cached_tokens,
                output_tokens,
                reasoning_tokens,
            )
            if cumulative_total:
                if (
                    last_cumulative_total is not None
                    and cumulative_total <= last_cumulative_total
                ):
                    round_activity_entries = []
                    continue
                last_cumulative_total = cumulative_total
            else:
                usage_key = (
                    current_model,
                    input_tokens,
                    cached_tokens,
                    output_tokens,
                    reasoning_tokens,
                    total_tokens,
                    timestamp,
                )
                if usage_key in seen_usage_keys:
                    round_activity_entries = []
                    continue
                seen_usage_keys.add(usage_key)

            round_activity_texts: list[str] = []
            for _side, _label, entry_text in round_activity_entries:
                cleaned = str(entry_text or "").strip()
                if cleaned and cleaned not in round_activity_texts:
                    round_activity_texts.append(cleaned)
            rounds.append(
                RequestRound(
                    index=len(rounds) + 1,
                    status="confirmed",
                    model=current_model,
                    input_tokens=input_tokens,
                    cached_tokens=cached_tokens,
                    cache_write_tokens=cache_write_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                    total_tokens=total_tokens,
                    estimated=False,
                    cost_usd=self.cost_estimator.calculate(
                        current_model,
                        input_tokens,
                        cached_tokens,
                        output_tokens,
                        reasoning_tokens,
                        cache_write_tokens=cache_write_tokens,
                        provider=model_provider,
                    ),
                    started_at=round_started_at,
                    completed_at=timestamp,
                    activity_summary=activity_summary,
                    copy_text=copy_text,
                    activity_texts=tuple(round_activity_texts[:8]),
                )
            )
            round_started_at = timestamp
            round_activity_entries = []
        return rounds

    def _legacy_latest_activity(self, records: Sequence[Mapping[str, Any]]) -> Activity:
        latest_token_count: Activity | None = None
        for record in reversed(records):
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            record_type = record.get("type")
            payload_type = payload.get("type")
            timestamp = record.get("_dt")

            if record_type == "event_msg" and payload_type == "user_message":
                return Activity(
                    "user", compact_text(payload.get("message"), 160), timestamp
                )
            if record_type == "event_msg" and payload_type == "agent_message":
                return Activity(
                    "agent", compact_text(payload.get("message"), 160), timestamp
                )
            if record_type == "response_item" and payload_type == "function_call":
                return Activity(
                    "tool call",
                    f"{payload.get('name')} {compact_text(payload.get('arguments'), 140)}",
                    timestamp,
                )
            if record_type == "response_item" and payload_type == "custom_tool_call":
                return Activity(
                    "tool call",
                    f"{payload.get('name')} {compact_text(payload.get('arguments'), 140)}",
                    timestamp,
                )
            if (
                record_type == "response_item"
                and payload_type == "function_call_output"
            ):
                return Activity(
                    "tool output",
                    f"{payload.get('call_id')} {compact_text(payload.get('output'), 140)}",
                    timestamp,
                )
            if (
                record_type == "response_item"
                and payload_type == "custom_tool_call_output"
            ):
                return Activity(
                    "tool output",
                    f"{payload.get('call_id')} {compact_text(payload.get('output'), 140)}",
                    timestamp,
                )
            # Newer Codex builds emit command execution as
            # event_msg/item_completed with item.type=CommandExecution (no
            # preceding response_item/custom_tool_call). Recognise it so the
            # bubble shows "执行命令" instead of falling through to an older
            # "正在思考" reasoning event while commands are running.
            if (
                record_type == "event_msg"
                and payload_type == "item_completed"
            ):
                item = payload.get("item") or {}
                if isinstance(item, Mapping) and item.get("type") == "CommandExecution":
                    cmd = item.get("command") or []
                    if isinstance(cmd, list) and cmd:
                        cmd_parts = [str(c) for c in cmd if c]
                        # Skip shell wrapper (pwsh.exe -Command / bash -c)
                        if (
                            len(cmd_parts) >= 3
                            and cmd_parts[1].lower() in ("-command", "-c", "--command")
                        ):
                            cmd_text = " ".join(cmd_parts[2:])
                        else:
                            cmd_text = " ".join(cmd_parts)
                    else:
                        cmd_text = str(cmd)
                    status = str(item.get("status") or "")
                    prefix = "执行命令" if status == "completed" else "命令失败"
                    return Activity(
                        "tool call",
                        f"{prefix}: {compact_text(cmd_text, 140)}",
                        timestamp,
                    )
            if record_type == "response_item" and payload_type == "message":
                if is_turn_aborted_message(payload):
                    continue
                role = response_message_role(payload)
                if role and role != "assistant":
                    continue
                return Activity(
                    "assistant", compact_text(message_text(payload), 160), timestamp
                )
            if record_type == "response_item" and payload_type == "reasoning":
                return Activity("reasoning", "正在思考", timestamp)
            if record_type == "event_msg" and payload_type == "task_started":
                return Activity("reasoning", "正在思考", timestamp)
            if record_type == "event_msg" and payload_type == "token_count":
                # Token accounting is frequently appended after a command or
                # streamed assistant message. Keep it as a fallback so it does
                # not overwrite the user-visible work that preceded it.
                latest_token_count = Activity(
                    "confirmed", "received token_count", timestamp
                )
        return latest_token_count or Activity("idle", "no activity", None)

    def latest_activity(self, records: Sequence[Mapping[str, Any]]) -> Activity:
        """Return the latest meaningful activity, skipping token bookkeeping."""
        latest_token_count: Activity | None = None
        for record in reversed(records):
            activity = _activity_from_record(record)
            if activity is not None:
                return activity
            payload = record.get("payload") or {}
            if (
                isinstance(payload, Mapping)
                and record.get("type") == "event_msg"
                and payload.get("type") == "token_count"
            ):
                # Token accounting is frequently appended after a command or
                # streamed assistant message. Keep it only as a fallback.
                latest_token_count = Activity(
                    "confirmed",
                    "received token_count",
                    record.get("_dt"),
                )
        return latest_token_count or Activity("idle", "no activity", None)

    def activity_steps(
        self,
        records: Sequence[Mapping[str, Any]],
        task_started_index: int | None = None,
    ) -> list[ActivityStep]:
        """Return every command/tool step in chronological order.

        Codex writes the custom tool call before its CommandExecution completion.
        Keep the call as a running step and update that same step when a matching
        completion/output arrives, so refreshes never lose intermediate commands.
        """
        start_index = 0 if task_started_index is None else task_started_index + 1
        steps: list[ActivityStep] = []
        open_indexes: list[int] = []

        # Desktop builds report reasoning titles through event_msg
        # item_completed Reasoning records; raw response_item reasoning entries
        # are the fallback for sessions that never emit them. Prefer the
        # completed form so a single reasoning round never yields two steps.
        prefer_completed_reasoning = any(
            record.get("type") == "event_msg"
            and isinstance(record.get("payload"), Mapping)
            and str(
                (record["payload"].get("item") or {}).get("type") or ""
            ).casefold()
            == "reasoning"
            for record in records[start_index:]
        )

        def add_open_step(
            record: Mapping[str, Any],
            *,
            title: str,
            detail: str,
            call_id: str,
            tool_name: str = "",
            command_raw: str = "",
        ) -> None:
            steps.append(
                ActivityStep(
                    timestamp=record.get("_dt"),
                    title=title,
                    detail=detail,
                    status="running",
                    call_id=call_id,
                    line=_as_int(record.get("_line")),
                    tool_name=tool_name,
                    command_raw=command_raw,
                )
            )
            open_indexes.append(len(steps) - 1)

        def find_open_step(
            call_id: str,
            command: str,
            *,
            allow_unique_command_fallback: bool = False,
        ) -> int | None:
            normalized_id = str(call_id or "").strip()
            normalized_command = " ".join(str(command or "").split())
            matching_commands: list[int] = []
            for index in reversed(open_indexes):
                step = steps[index]
                if normalized_id and step.call_id == normalized_id:
                    return index
                if (
                    normalized_command
                    and " ".join(step.detail.split()) == normalized_command
                ):
                    matching_commands.append(index)
            if len(matching_commands) == 1:
                return matching_commands[0]
            if matching_commands:
                return None
            if not allow_unique_command_fallback:
                return None
            command_indexes = [
                index for index in open_indexes if steps[index].title == "执行命令"
            ]
            return command_indexes[-1] if len(command_indexes) == 1 else None

        def close_open_step(index: int) -> None:
            try:
                open_indexes.remove(index)
            except ValueError:
                pass

        def find_step_by_call_id(call_id: str) -> int | None:
            normalized_id = str(call_id or "").strip()
            if not normalized_id:
                return None
            return next(
                (
                    index
                    for index in range(len(steps) - 1, -1, -1)
                    if steps[index].call_id == normalized_id
                ),
                None,
            )

        for record in records[start_index:]:
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            record_type = record.get("type")
            payload_type = payload.get("type")
            if record_type == "response_item" and payload_type in {
                "function_call",
                "custom_tool_call",
            }:
                raw_name = _tool_call_name(payload)
                name = raw_name.casefold()
                command = tool_command_text(payload)
                command_raw = tool_command_raw_text(payload)
                tool_call_id = str(
                    payload.get("call_id") or payload.get("id") or ""
                ).strip()
                existing_index = find_open_step(
                    tool_call_id,
                    # Match by call id only when one exists; fall back to the
                    # command text only for id-less calls so two concurrent
                    # calls with identical text are never merged.
                    command if not tool_call_id else "",
                )
                if existing_index is not None:
                    step = steps[existing_index]
                    if raw_name and not step.tool_name:
                        step.tool_name = raw_name
                    if command and not step.detail:
                        step.detail = command
                    if command_raw and not step.command_raw:
                        step.command_raw = command_raw
                    continue
                title = (
                    "执行命令"
                    if name in {"exec", "shell", "shell_command"}
                    else "调用工具"
                )
                add_open_step(
                    record,
                    title=title,
                    detail=command or tool_call_detail(payload),
                    call_id=str(
                        payload.get("call_id") or payload.get("id") or ""
                    ).strip(),
                    tool_name=raw_name,
                    command_raw=command_raw,
                )
                continue
            if record_type == "response_item" and payload_type == "reasoning":
                if prefer_completed_reasoning:
                    continue
                title = reasoning_summary_title(payload)
                if title:
                    steps.append(
                        ActivityStep(
                            timestamp=record.get("_dt"),
                            title="思考",
                            detail=title,
                            status="completed",
                            line=_as_int(record.get("_line")),
                        )
                    )
                continue
            if record_type == "event_msg" and payload_type in {
                "item_started",
                "item_updated",
                "item_completed",
            }:
                item = payload.get("item") or {}
                if not isinstance(item, Mapping):
                    continue
                item_type = str(item.get("type") or "").replace("_", "").casefold()
                if item_type == "reasoning":
                    if payload_type == "item_completed":
                        title = reasoning_summary_title(item)
                        if title:
                            steps.append(
                                ActivityStep(
                                    timestamp=record.get("_dt"),
                                    title="思考",
                                    detail=title,
                                    status="completed",
                                    line=_as_int(record.get("_line")),
                                )
                            )
                    continue
                if item_type == "mcptoolcall":
                    tool_name = _mcp_tool_name(item)
                    detail = _mcp_tool_detail(item)
                    call_id = str(item.get("id") or "").strip()
                    failed = str(item.get("status") or "").casefold() in {
                        "failed",
                        "error",
                    }
                    existing_index = find_step_by_call_id(call_id)
                    if payload_type != "item_completed":
                        if existing_index is None:
                            add_open_step(
                                record,
                                title="调用工具",
                                detail=detail,
                                call_id=call_id,
                                tool_name=tool_name,
                            )
                        else:
                            steps[existing_index].detail = detail or steps[existing_index].detail
                            steps[existing_index].timestamp = record.get("_dt") or steps[existing_index].timestamp
                        continue
                    if existing_index is not None:
                        step = steps[existing_index]
                        step.timestamp = record.get("_dt") or step.timestamp
                        step.detail = detail or step.detail
                        step.status = "failed" if failed else "completed"
                        step.title = "工具失败" if failed else "工具完成"
                        step.line = _as_int(record.get("_line"), step.line)
                        close_open_step(existing_index)
                        continue
                    steps.append(
                        ActivityStep(
                            timestamp=record.get("_dt"),
                            title="工具失败" if failed else "工具完成",
                            detail=detail,
                            status="failed" if failed else "completed",
                            call_id=call_id,
                            line=_as_int(record.get("_line")),
                            tool_name=tool_name,
                        )
                    )
                    continue
                if item_type == "filechange":
                    if payload_type == "item_completed":
                        changes = item.get("changes") or {}
                        if isinstance(changes, Mapping) and changes:
                            failed = str(item.get("status") or "").casefold() in {
                                "failed",
                                "error",
                            }
                            names = " · ".join(
                                _file_change_basename(path) for path in changes
                            )
                            steps.append(
                                ActivityStep(
                                    timestamp=record.get("_dt"),
                                    title="编辑文件",
                                    detail=compact_text(names, 200),
                                    status="failed" if failed else "completed",
                                    call_id=str(item.get("id") or "").strip(),
                                    line=_as_int(record.get("_line")),
                                    tool_name="apply_patch",
                                    output=file_change_output(changes),
                                )
                            )
                    continue
                if item_type != "commandexecution":
                    continue
                command = command_execution_text(item)
                command_raw = command_execution_raw_text(item)
                completed = payload_type == "item_completed"
                failed = str(item.get("status") or "").casefold() in {
                    "failed",
                    "error",
                }
                index = find_open_step(
                    str(item.get("id") or ""),
                    command,
                    allow_unique_command_fallback=True,
                )
                if index is not None and not completed:
                    step = steps[index]
                    step.timestamp = record.get("_dt") or step.timestamp
                    step.detail = command or step.detail
                    if command_raw:
                        step.command_raw = command_raw
                    step.status = "running"
                    step.line = _as_int(record.get("_line"), step.line)
                    continue
                if index is None or not completed:
                    if completed:
                        output_text = command_execution_output(item)
                        steps.append(
                            ActivityStep(
                                timestamp=record.get("_dt"),
                                title="命令失败" if failed else "命令完成",
                                detail=command,
                                status="failed" if failed else "completed",
                                call_id=str(item.get("id") or "").strip(),
                                line=_as_int(record.get("_line")),
                                output=compact_text(output_text, 220),
                                exit_code=_exit_code_or_none(item.get("exit_code")),
                                active_tail=active_output_tail(output_text),
                                duration_text=_command_duration_text(
                                    item.get("duration")
                                ),
                                command_raw=command_raw,
                            )
                        )
                    else:
                        add_open_step(
                            record,
                            title="执行命令",
                            detail=command,
                            call_id=str(item.get("id") or "").strip(),
                            command_raw=command_raw,
                        )
                    continue
                step = steps[index]
                step.timestamp = record.get("_dt") or step.timestamp
                step.title = "命令失败" if failed else "命令完成"
                step.detail = command or step.detail
                step.status = "failed" if failed else "completed"
                if not step.call_id:
                    step.call_id = str(item.get("id") or "").strip()
                step.line = _as_int(record.get("_line"), step.line)
                if command_raw:
                    step.command_raw = command_raw
                output_text = command_execution_output(item)
                step.output = compact_text(output_text, 220)
                step.exit_code = _exit_code_or_none(item.get("exit_code"))
                step.active_tail = active_output_tail(output_text)
                step.duration_text = _command_duration_text(item.get("duration"))
                close_open_step(index)
                continue
            if record_type == "response_item" and payload_type in {
                "function_call_output",
                "custom_tool_call_output",
            }:
                call_id = str(payload.get("call_id") or "").strip()
                index = find_step_by_call_id(call_id)
                if index is None and not call_id and len(open_indexes) == 1:
                    index = open_indexes[0]
                if index is not None:
                    if steps[index].status not in {"failed", "completed"}:
                        steps[index].status = "completed"
                    steps[index].timestamp = record.get("_dt") or steps[index].timestamp
                    if not steps[index].output:
                        # Structured CommandExecution output is preferred; the
                        # raw tool-output string is only a fallback for
                        # sessions that never emit the completed item.
                        steps[index].output = compact_text(
                            payload.get("output") or payload.get("result") or "",
                            220,
                        )
                    close_open_step(index)

        if any(
            record.get("type") == "event_msg"
            and isinstance(record.get("payload"), Mapping)
            and record["payload"].get("type") in {"task_complete", "turn_aborted"}
            for record in records[start_index:]
        ):
            for index in open_indexes:
                steps[index].status = "completed"
            open_indexes.clear()
        return steps[-128:]

    def latest_output(self, records: Sequence[Mapping[str, Any]]) -> Activity:
        """Return the latest assistant-visible text, ignoring bookkeeping events."""
        for record in reversed(records):
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            record_type = record.get("type")
            payload_type = payload.get("type")
            timestamp = record.get("_dt")

            if record_type == "event_msg" and payload_type == "agent_message":
                text = compact_text(payload.get("message"), 220)
                if text:
                    return Activity("agent", text, timestamp)
            if record_type == "response_item" and payload_type == "message":
                if is_turn_aborted_message(payload):
                    continue
                role = response_message_role(payload)
                if role and role != "assistant":
                    continue
                text = compact_text(message_text(payload), 220)
                if text:
                    return Activity("assistant", text, timestamp)
        return Activity("idle", "", None)

    def slow_summary(
        self,
        records: Sequence[Mapping[str, Any]],
        last_event_time: datetime | None,
    ) -> SlowSummary:
        """Summarize tool waits and model-response gaps for the latest task."""
        calls_by_id: dict[str, ToolCallTiming] = {}
        completed_calls: list[ToolCallTiming] = []
        gaps: list[GapTiming] = []
        active_after_record: list[bool] = []
        task_active = False
        latest_task_start_index = 0

        for record in records:
            payload = record.get("payload") or {}
            if (
                record.get("type") == "event_msg"
                and isinstance(payload, Mapping)
                and payload.get("type") == "task_started"
            ):
                task_active = True
                latest_task_start_index = len(active_after_record)
            # 继续可能沿用 turn_id 且没有 task_started；收到真实输入即恢复等待态。
            if _user_message_text(record):
                task_active = True
            is_task_terminal = (
                record.get("type") == "event_msg"
                and isinstance(payload, Mapping)
                and payload.get("type") in {"task_complete", "turn_aborted"}
            )
            active_after_record.append(task_active and not is_task_terminal)
            if is_task_terminal:
                task_active = False

        active_gap_categories = {
            "model_startup",
            "model_or_idle",
            "tool_wait",
            "other_gap",
        }
        for index, (previous, current) in enumerate(zip(records, records[1:])):
            previous_dt = previous.get("_dt")
            current_dt = current.get("_dt")
            if not isinstance(previous_dt, datetime) or not isinstance(
                current_dt, datetime
            ):
                continue
            duration = seconds_between(previous_dt, current_dt)
            category = classify_gap(previous, current)
            previous_is_active = (
                active_after_record[index]
                if index < len(active_after_record)
                else False
            )
            if (
                duration >= 5.0
                and index >= latest_task_start_index
                and previous_is_active
                and category in active_gap_categories
            ):
                gaps.append(
                    GapTiming(
                        start=previous_dt,
                        end=current_dt,
                        duration_seconds=duration,
                        category=category,
                        from_event=event_label(previous),
                        to_event=event_label(current),
                        start_line=_as_int(previous.get("_line")),
                        end_line=_as_int(current.get("_line")),
                    )
                )

        for index, record in enumerate(records):
            if index < latest_task_start_index:
                continue
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            record_type = record.get("type")
            payload_type = payload.get("type")
            timestamp = record.get("_dt")
            if not isinstance(timestamp, datetime):
                continue

            if record_type == "response_item" and payload_type == "function_call":
                call_id = str(payload.get("call_id") or "")
                calls_by_id[call_id] = ToolCallTiming(
                    call_id=call_id,
                    name=str(payload.get("name") or "?"),
                    args=str(payload.get("arguments") or ""),
                    start=timestamp,
                    start_line=_as_int(record.get("_line")),
                )
            elif (
                record_type == "response_item"
                and payload_type == "function_call_output"
            ):
                call_id = str(payload.get("call_id") or "")
                call = calls_by_id.get(call_id)
                if call is None:
                    continue
                call.end = timestamp
                call.end_line = _as_int(record.get("_line"))
                call.output = str(payload.get("output") or "")
                completed_calls.append(call)

        waits = [
            call
            for call in completed_calls
            if call.category == "user_wait" and call.duration_seconds is not None
        ]
        tools = [
            call
            for call in completed_calls
            if call.category == "tool" and call.duration_seconds is not None
        ]
        waits.sort(key=lambda call: call.duration_seconds or 0.0, reverse=True)
        tools.sort(key=lambda call: call.duration_seconds or 0.0, reverse=True)
        gaps.sort(key=lambda item: item.duration_seconds, reverse=True)

        current_gap = "任务已结束"
        currently_active = (
            bool(active_after_record[-1]) if active_after_record else False
        )
        if last_event_time is not None and currently_active:
            current_gap = f"{seconds_between(last_event_time, datetime.now(last_event_time.tzinfo)):.1f}s"

        return SlowSummary(
            slowest_user_wait=(
                self.format_call(waits[0]) if waits else "无（本任务无用户确认）"
            ),
            slowest_user_wait_call=waits[0] if waits else None,
            slowest_tool=(
                self.format_call(tools[0]) if tools else "无（本任务无工具调用）"
            ),
            slowest_tool_call=tools[0] if tools else None,
            longest_gap=(
                self.format_gap(gaps[0]) if gaps else "无（本任务无长响应等待）"
            ),
            longest_gap_detail=gaps[0] if gaps else None,
            current_gap=current_gap,
            current_gap_active=currently_active,
        )

    def format_call(self, call: ToolCallTiming) -> str:
        """Format one tool or user-wait duration."""
        duration = call.duration_seconds or 0.0
        name = "用户确认" if call.name == "request_user_input" else call.name
        return f"{duration:.1f}s {name}"

    def format_gap(self, gap: GapTiming) -> str:
        """Format one model / response wait gap."""
        return f"{gap.duration_seconds:.1f}s {gap.category}"

    def build_request_tokens(
        self,
        snapshot: ParsedSession,
        task_started_at: datetime | None,
        latest_model: str,
        jsonl_rounds: list[RequestRound],
        sse_tracker: SseRequestStateMachine | None = None,
    ) -> RequestTokens:
        history = list(jsonl_rounds)
        if sse_tracker is not None:
            request, sse_history = sse_tracker.build(
                snapshot.session_id, task_started_at
            )
            if self.running_request_already_confirmed(request, history):
                request = RequestTokens(status="waiting", source="sse")
            if not history:
                history = list(sse_history)
                if request.status == "running" and request.round_index and history:
                    history[-1] = self.round_from_request(
                        request, snapshot, latest_model
                    )
            elif request.status == "running" and request.round_index:
                history.append(self.round_from_request(request, snapshot, latest_model))
            elif request.status == "confirmed" and request.input_tokens is not None:
                latest = history[-1] if history else None
                same_as_latest = (
                    latest is not None
                    and latest.input_tokens == request.input_tokens
                    and latest.cached_tokens == request.cached_tokens
                    and latest.output_tokens == request.output_tokens
                    and latest.reasoning_tokens == request.reasoning_tokens
                    and latest.total_tokens == request.total_tokens
                )
                if not same_as_latest:
                    history.append(
                        self.round_from_request(request, snapshot, latest_model)
                    )
            history = self._history_after_task_abort(history, snapshot.task_aborted_at)
            snapshot.request_history = self.reindex_rounds(history)
            request = self.request_after_task_abort(snapshot, request, latest_model)
            if request.error:
                if not snapshot.error:
                    snapshot.error = request.error
            elif request.status != "waiting":
                return request

        request = self.fallback_request_tokens(snapshot, latest_model)
        if history:
            if request.status == "running":
                history.append(self.round_from_request(request, snapshot, latest_model))
            history = self._history_after_task_abort(history, snapshot.task_aborted_at)
            snapshot.request_history = self.reindex_rounds(history)
        elif request.status != "waiting":
            snapshot.request_history = self.reindex_rounds(
                [self.round_from_request(request, snapshot, latest_model)]
            )
        return request

    def running_request_already_confirmed(
        self, request: RequestTokens, history: Sequence[RequestRound]
    ) -> bool:
        if request.status != "running" or request.started_at is None:
            return False
        for item in reversed(history):
            if item.estimated or item.completed_at is None:
                continue
            return (item.completed_at - request.started_at).total_seconds() >= -1.0
        return False

    def fallback_request_tokens(
        self, snapshot: ParsedSession, latest_model: str
    ) -> RequestTokens:
        if (
            snapshot.task_completed_at is None
            and snapshot.task_aborted_at is None
            and snapshot.estimate.total_tokens > 0
        ):
            return RequestTokens(
                status="running",
                model=latest_model,
                input_tokens=snapshot.estimate.input_tokens or None,
                output_tokens=snapshot.estimate.output_tokens
                + snapshot.estimate.tool_tokens,
                reasoning_tokens=None,
                total_tokens=snapshot.estimate.total_tokens,
                estimated=True,
                source="jsonl",
                updated_at=snapshot.estimate.updated_at or snapshot.last_event_time,
                started_at=snapshot.estimate.started_at or snapshot.last_event_time,
            )
        confirmed = snapshot.confirmed
        cost = self.cost_estimator.calculate(
            latest_model,
            confirmed.last_input,
            confirmed.last_cached,
            confirmed.last_output,
            confirmed.last_reasoning,
            cache_write_tokens=confirmed.last_cache_write,
            provider=snapshot.model_provider,
        )
        return RequestTokens(
            status="confirmed" if confirmed.last_total else "waiting",
            model=latest_model,
            input_tokens=confirmed.last_input or None,
            cached_tokens=confirmed.last_cached or None,
            cache_write_tokens=confirmed.last_cache_write or None,
            output_tokens=confirmed.last_output,
            reasoning_tokens=confirmed.last_reasoning,
            total_tokens=confirmed.last_total,
            estimated=False,
            source="jsonl",
            updated_at=snapshot.task_aborted_at or confirmed.timestamp,
            completed_at=snapshot.task_aborted_at,
            cost_usd=cost,
        )

    def _history_after_task_abort(
        self,
        history: Sequence[RequestRound],
        task_aborted_at: datetime | None,
    ) -> list[RequestRound]:
        items = list(history)
        if task_aborted_at is None:
            return items
        while items and items[-1].status == "running":
            items.pop()
        return items

    def request_after_task_abort(
        self,
        snapshot: ParsedSession,
        request: RequestTokens,
        latest_model: str,
    ) -> RequestTokens:
        if snapshot.task_aborted_at is None or request.status != "running":
            return request
        next_request = self.fallback_request_tokens(
            snapshot, request.model or latest_model
        )
        next_request.source = request.source or next_request.source
        if next_request.started_at is None:
            next_request.started_at = snapshot.task_started_at or request.started_at
        if next_request.updated_at is None:
            next_request.updated_at = snapshot.task_aborted_at
        if next_request.completed_at is None:
            next_request.completed_at = snapshot.task_aborted_at
        return next_request

    def round_from_request(
        self,
        request: RequestTokens,
        snapshot: ParsedSession | None = None,
        fallback_model: str = "",
    ) -> RequestRound:
        model = request.model or fallback_model
        input_tokens = request.input_tokens
        cached_tokens = request.cached_tokens
        cache_write_tokens = request.cache_write_tokens
        output_tokens = request.output_tokens
        total_tokens = request.total_tokens
        cost_usd = request.cost_usd
        activity_summary = ""
        copy_text = ""

        if snapshot is not None and request.status == "running" and request.estimated:
            if input_tokens is None:
                input_tokens = (
                    snapshot.confirmed.last_input
                    + snapshot.estimate.input_tokens
                    + snapshot.estimate.tool_tokens
                ) or None
            if input_tokens is not None and (
                total_tokens is None or total_tokens <= int(output_tokens or 0)
            ):
                total_tokens = int(input_tokens) + int(output_tokens or 0)
            if cached_tokens is None:
                cached_tokens = (
                    min(int(snapshot.confirmed.last_cached or 0), int(input_tokens))
                    if input_tokens is not None
                    else None
                )
            if cache_write_tokens is None:
                cache_write_tokens = (
                    min(
                        int(snapshot.confirmed.last_cache_write or 0),
                        max(0, int(input_tokens) - int(cached_tokens or 0)),
                    )
                    if input_tokens is not None
                    else None
                )
            if cost_usd is None:
                cost_usd = self.cost_estimator.calculate(
                    model,
                    input_tokens,
                    cached_tokens,
                    output_tokens or 0,
                    request.reasoning_tokens or 0,
                    cache_write_tokens=cache_write_tokens or 0,
                    provider=snapshot.model_provider,
                )
            if snapshot.activity.detail:
                activity_label = {
                    "user": "输入",
                    "agent": "输出",
                    "assistant": "输出",
                    "tool call": "工具调用",
                    "tool output": "工具返回",
                }.get(snapshot.activity.kind, "活动")
                activity_summary = (
                    f"{activity_label}：{compact_text(snapshot.activity.detail, 92)}"
                )
                copy_text = f"{activity_label}：\n{snapshot.activity.detail}"

        return RequestRound(
            index=request.round_index,
            status=request.status,
            model=model,
            input_tokens=input_tokens,
            cached_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=request.reasoning_tokens,
            total_tokens=total_tokens,
            estimated=request.estimated,
            cost_usd=cost_usd,
            started_at=request.started_at,
            completed_at=request.completed_at,
            activity_summary=activity_summary,
            copy_text=copy_text,
        )

    def reindex_rounds(self, rounds: Sequence[RequestRound]) -> list[RequestRound]:
        items = list(rounds)
        for index, item in enumerate(items, 1):
            item.index = index
        return items


class SseRequestStateMachine:
    """Track live request usage from Codex SQLite OTel and SSE log rows."""

    def __init__(
        self,
        db_path: Path | None = None,
        estimate_enabled: bool = True,
        cost_estimator: CostEstimator | None = None,
    ) -> None:
        self.db_path = db_path
        self.estimate_enabled = estimate_enabled
        self.cost_estimator = cost_estimator or CostEstimator()
        self.session_id: str | None = None
        self.task_started_at: datetime | None = None
        self.active_start_id: int | None = None
        self.last_seen_id = 0
        self.round_index = 0
        self.history: list[RequestRound] = []
        self.pending_sse_events: list[str] = []
        self.current = RequestTokens(status="waiting", source="sse")

    def reset_session(
        self, session_id: str, task_started_at: datetime | None = None
    ) -> None:
        self.session_id = session_id
        self.task_started_at = task_started_at
        self.active_start_id = None
        self.last_seen_id = 0
        self.round_index = 0
        self.history = []
        self.pending_sse_events = []
        self.current = RequestTokens(status="waiting", source="sse")

    def reset_task(self, task_started_at: datetime | None) -> None:
        self.archive_current()
        self.task_started_at = task_started_at
        self.active_start_id = None
        self.last_seen_id = 0
        self.round_index = 0
        self.history = []
        self.pending_sse_events = []
        self.current = RequestTokens(status="waiting", source="sse")

    def build(
        self, session_id: str, task_started_at: datetime | None = None
    ) -> tuple[RequestTokens, list[RequestRound]]:
        """Read SQLite rows and return the current request plus recent rounds."""
        if session_id == "n/a":
            return RequestTokens(status="waiting", source="sse"), []
        if self.db_path is None:
            return self.current, self.rounds()
        if not self.db_path.exists():
            return (
                RequestTokens(
                    status="error",
                    source="sse",
                    error=f"SSE log database not found: {self.db_path}",
                ),
                [],
            )
        if self.session_id != session_id:
            self.reset_session(session_id, task_started_at)
        elif self.task_started_at != task_started_at:
            self.reset_task(task_started_at)

        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            try:
                con.row_factory = sqlite3.Row
                if self.active_start_id is None:
                    self.bootstrap(con, session_id, task_started_at)
                if self.active_start_id is None:
                    return RequestTokens(status="waiting", source="sse"), []
                self.consume_new_rows(con, session_id)
                return self.current, self.recent_rounds(con, session_id)
            finally:
                con.close()
        except sqlite3.Error as exc:
            return (
                RequestTokens(status="error", source="sse", error=f"SQLite: {exc}"),
                self.rounds(),
            )

    def bootstrap(
        self,
        con: sqlite3.Connection,
        session_id: str,
        task_started_at: datetime | None,
    ) -> None:
        if task_started_at is None:
            row = con.execute(
                """
                select id
                from logs
                where target='codex_otel.log_only'
                  and feedback_log_body like ?
                  and feedback_log_body like '%event.name="codex.sse_event"%'
                  and feedback_log_body like '%event.kind=response.created%'
                order by id desc
                limit 1
                """,
                (f"%conversation.id={session_id}%",),
            ).fetchone()
        else:
            row = con.execute(
                """
                select id
                from logs
                where target='codex_otel.log_only'
                  and ts >= ?
                  and feedback_log_body like ?
                  and feedback_log_body like '%event.name="codex.sse_event"%'
                  and feedback_log_body like '%event.kind=response.created%'
                order by id asc
                limit 1
                """,
                (
                    int(task_started_at.timestamp()) - 2,
                    f"%conversation.id={session_id}%",
                ),
            ).fetchone()
        if row is None:
            return
        self.active_start_id = int(row["id"])
        self.last_seen_id = max(0, self.active_start_id - 1)
        self.current = RequestTokens(status="waiting", source="sse")

    def consume_new_rows(self, con: sqlite3.Connection, session_id: str) -> None:
        rows = con.execute(
            """
            select id, ts, target, feedback_log_body
            from logs
            where id > ?
              and (target='codex_api::sse::responses' or target='codex_otel.log_only')
            order by id
            """,
            (self.last_seen_id,),
        ).fetchall()
        self.consume_log_rows(rows, session_id)

    def consume_log_rows(
        self, rows: Iterable[Mapping[str, Any]], session_id: str
    ) -> None:
        """Consume already fetched SQLite log rows; useful for tests and adapters."""
        if self.session_id != session_id:
            self.reset_session(session_id)
        for row in rows:
            row_id = _as_int(_row_value(row, "id"))
            target = str(_row_value(row, "target", "") or "")
            body = str(_row_value(row, "feedback_log_body", "") or "")
            if target == "codex_otel.log_only":
                self.consume_otel(row_id, body, session_id)
            elif target == "codex_api::sse::responses":
                self.pending_sse_events.append(body)
                self.pending_sse_events = self.pending_sse_events[-200:]
            self.last_seen_id = max(self.last_seen_id, row_id)

    def consume_otel(self, row_id: int, body: str, session_id: str) -> None:
        del row_id
        if 'event.name="codex.sse_event"' not in body:
            return
        kind = self.extract_text(body, r"\bevent\.kind=([^\s]+)")
        timestamp = self.extract_timestamp(body)
        model = extract_log_field(body, "slug") or extract_log_field(body, "model")
        is_current_session = f"conversation.id={session_id}" in body

        if kind in {
            "response.output_text.delta",
            "response.function_call_arguments.delta",
        }:
            pending = self.pop_pending_sse_event(kind)
            if is_current_session and pending:
                self.consume_sse(pending)
            return

        if not is_current_session:
            return

        if kind == "response.created":
            self.start_round(timestamp, model)
            return

        if kind == "response.in_progress" and self.current.status == "waiting":
            self.current.status = "running"
            self.current.updated_at = timestamp
            return

        if kind == "response.completed":
            exact = self.request_from_otel_counts(body, timestamp, model)
            if exact is not None:
                self.finish_round(exact)

    def pop_pending_sse_event(self, event_type: str) -> str | None:
        for index, body in enumerate(self.pending_sse_events):
            event = self.parse_sse_event(body)
            if event and event.get("type") == event_type:
                item = self.pending_sse_events.pop(index)
                if index:
                    del self.pending_sse_events[:index]
                return item
        return None

    def start_round(self, timestamp: datetime | None, model: str) -> None:
        self.archive_current()
        self.round_index += 1
        self.current = RequestTokens(
            status="running",
            round_index=self.round_index,
            model=model,
            output_tokens=0,
            total_tokens=0,
            estimated=True,
            source="sse",
            updated_at=timestamp,
            started_at=timestamp,
        )

    def finish_round(self, exact: RequestTokens) -> None:
        exact.round_index = self.current.round_index or self.round_index
        exact.model = exact.model or self.current.model
        exact.started_at = self.current.started_at
        exact.completed_at = exact.updated_at
        exact.cost_usd = self.calculate_request_cost(exact)
        self.current = exact

    def archive_current(self) -> None:
        if self.current.status == "waiting" or not self.current.round_index:
            return
        self.history.append(self.to_round(self.current))
        self.history = self.history[-MAX_REQUEST_HISTORY:]

    def rounds(self) -> list[RequestRound]:
        items = list(self.history)
        if self.current.status != "waiting" and self.current.round_index:
            items.append(self.to_round(self.current))
        return items[-MAX_REQUEST_HISTORY:]

    def recent_rounds(
        self, con: sqlite3.Connection, session_id: str
    ) -> list[RequestRound]:
        rows = con.execute(
            """
            select id, feedback_log_body
            from logs
            where target='codex_otel.log_only'
              and id >= ?
              and feedback_log_body like ?
              and feedback_log_body like 'event.name="codex.sse_event" event.kind=response.completed input_token_count=%'
            order by id
            """,
            (int(self.active_start_id or 0), f"%conversation.id={session_id}%"),
        ).fetchall()
        items: list[RequestRound] = []
        seen_usage_keys: set[tuple[Any, ...]] = set()
        for index, row in enumerate(rows, 1):
            body = str(row["feedback_log_body"] or "")
            model = extract_log_field(body, "slug") or extract_log_field(body, "model")
            input_tokens = self.extract_int(body, "input_token_count")
            output_tokens = self.extract_int(body, "output_token_count")
            cached_tokens = self.extract_int(body, "cached_token_count")
            cache_write_tokens = self.extract_int(body, "cache_write_token_count")
            reasoning_tokens = self.extract_int(body, "reasoning_token_count")
            timestamp = self.extract_timestamp(body)
            usage_key = (
                model,
                input_tokens,
                cached_tokens,
                cache_write_tokens,
                output_tokens,
                reasoning_tokens,
                timestamp,
            )
            if usage_key in seen_usage_keys:
                continue
            seen_usage_keys.add(usage_key)
            items.append(
                RequestRound(
                    index=index,
                    status="confirmed",
                    model=model,
                    input_tokens=input_tokens,
                    cached_tokens=cached_tokens,
                    cache_write_tokens=cache_write_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                    total_tokens=(
                        input_tokens + output_tokens
                        if input_tokens is not None and output_tokens is not None
                        else None
                    ),
                    estimated=False,
                    cost_usd=self.cost_estimator.calculate(
                        model,
                        input_tokens,
                        cached_tokens,
                        output_tokens,
                        reasoning_tokens,
                        cache_write_tokens=cache_write_tokens or 0,
                    ),
                    completed_at=timestamp,
                )
            )
        if self.current.status == "running" and self.current.round_index:
            items.append(self.to_round(self.current))
        items = items[-MAX_REQUEST_HISTORY:]
        for index, item in enumerate(items, 1):
            item.index = index
        return items

    def to_round(self, request: RequestTokens) -> RequestRound:
        return RequestRound(
            index=request.round_index,
            status=request.status,
            model=request.model,
            input_tokens=request.input_tokens,
            cached_tokens=request.cached_tokens,
            cache_write_tokens=request.cache_write_tokens,
            output_tokens=request.output_tokens,
            reasoning_tokens=request.reasoning_tokens,
            total_tokens=request.total_tokens,
            estimated=request.estimated,
            cost_usd=request.cost_usd,
            started_at=request.started_at,
            completed_at=request.completed_at,
        )

    def consume_sse(self, body: str) -> None:
        event = self.parse_sse_event(body)
        if not event:
            return
        event_type = str(event.get("type") or "")
        if event_type in {
            "response.output_text.delta",
            "response.function_call_arguments.delta",
        }:
            if self.current.status != "running" or not self.current.round_index:
                return
            if not self.estimate_enabled:
                return
            delta = str(event.get("delta") or "")
            if not delta:
                return
            output = (self.current.output_tokens or 0) + estimate_tokens(delta)
            self.current.status = "running"
            self.current.output_tokens = output
            self.current.total_tokens = output
            self.current.estimated = True
            self.current.source = "sse"
            return

        if event_type == "response.completed":
            exact = self.request_from_sse_completed(event)
            if exact is not None:
                self.finish_round(exact)

    def request_from_otel_counts(
        self, body: str, timestamp: datetime | None, model: str
    ) -> RequestTokens | None:
        input_tokens = self.extract_int(body, "input_token_count")
        output_tokens = self.extract_int(body, "output_token_count")
        cached_tokens = self.extract_int(body, "cached_token_count")
        cache_write_tokens = self.extract_int(body, "cache_write_token_count")
        reasoning_tokens = self.extract_int(body, "reasoning_token_count")
        if input_tokens is None or output_tokens is None:
            return None
        return RequestTokens(
            status="confirmed",
            model=model,
            input_tokens=input_tokens,
            cached_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens or 0,
            total_tokens=input_tokens + output_tokens,
            estimated=False,
            source="sse",
            updated_at=timestamp,
        )

    def request_from_sse_completed(
        self, event: Mapping[str, Any]
    ) -> RequestTokens | None:
        response = event.get("response") or {}
        if not isinstance(response, Mapping):
            return None
        usage = response.get("usage") or {}
        if not isinstance(usage, Mapping) or not usage:
            return None
        details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        if not isinstance(details, Mapping):
            details = {}
        if not isinstance(output_details, Mapping):
            output_details = {}
        input_tokens = _as_int(usage.get("input_tokens"))
        output_tokens = _as_int(usage.get("output_tokens"))
        total_tokens = _as_int(usage.get("total_tokens"), input_tokens + output_tokens)
        return RequestTokens(
            status="confirmed",
            model=str(response.get("model") or ""),
            input_tokens=input_tokens,
            cached_tokens=_as_int(details.get("cached_tokens")),
            cache_write_tokens=_as_int(details.get("cache_write_tokens")),
            output_tokens=output_tokens,
            reasoning_tokens=_as_int(output_details.get("reasoning_tokens")),
            total_tokens=total_tokens,
            estimated=False,
            source="sse",
            response_id=str(response.get("id") or ""),
        )

    def calculate_request_cost(self, request: RequestTokens) -> float | None:
        return self.cost_estimator.calculate(
            request.model,
            request.input_tokens,
            request.cached_tokens,
            request.output_tokens,
            request.reasoning_tokens or 0,
            cache_write_tokens=request.cache_write_tokens or 0,
        )

    def parse_sse_event(self, body: str) -> dict[str, Any] | None:
        prefix = "SSE event:"
        if not body.startswith(prefix):
            return None
        try:
            event = json.loads(body[len(prefix) :].strip())
        except json.JSONDecodeError:
            return None
        return event if isinstance(event, dict) else None

    def extract_int(self, body: str, name: str) -> int | None:
        match = re.search(rf"\b{re.escape(name)}=(\d+)", body)
        return int(match.group(1)) if match else None

    def extract_text(self, body: str, pattern: str) -> str:
        match = re.search(pattern, body)
        return match.group(1) if match else ""

    def extract_timestamp(self, body: str) -> datetime | None:
        return parse_timestamp(self.extract_text(body, r"\bevent\.timestamp=([^\s]+)"))
