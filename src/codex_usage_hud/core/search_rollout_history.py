"""Resolve the JSONL segments referenced by Codex paginated history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from uuid import UUID

from .rollout_io import open_rollout_bytes, resolve_rollout_path


_UUID_PATTERN = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_ROLLOUT_NAME = re.compile(
    rf"rollout-.*-({_UUID_PATTERN})(?:_({_UUID_PATTERN}))?\.jsonl(?:\.zst)?",
    re.IGNORECASE,
)
_UUID_RE = re.compile(_UUID_PATTERN, re.IGNORECASE)
_MAX_HEADER_BYTES = 1024 * 1024


def _history_base(path: Path, end: int | None) -> tuple[str, int] | None:
    try:
        with open_rollout_bytes(path) as stream:
            header = stream.readline(min(_MAX_HEADER_BYTES, end or _MAX_HEADER_BYTES))
        if not header.endswith(b"\n") or b'"history_base"' not in header:
            return None
        record = json.loads(header)
    except (OSError, UnicodeError, ValueError):
        return None
    payload = record.get("payload") if isinstance(record, dict) else None
    if not isinstance(payload, dict) or record.get("type") != "session_meta":
        return None
    if payload.get("history_mode") != "paginated":
        return None
    base = payload.get("history_base")
    if not isinstance(base, dict):
        return None
    base_id, offset = base.get("thread_id"), base.get("end_byte_offset")
    if (isinstance(base_id, str) and _UUID_RE.fullmatch(base_id)
            and type(offset) is int and offset > 0):
        return base_id.casefold(), offset
    return None


def _base_directories(path: Path, base_id: str) -> tuple[Path, ...]:
    parent = path.parent
    roots = (parent,)
    if parent.name == "archived_sessions":
        codex_home = parent.parent
    elif len(path.parents) >= 4 and path.parents[3].name == "sessions":
        codex_home = path.parents[3].parent
    else:
        return roots
    roots += (codex_home / "archived_sessions",)
    identity = UUID(base_id)
    # UUIDv7 creation time narrows the dated sessions tree to the possible
    # local calendar dates, even if Codex switched time zones between recordings.
    if identity.version == 7:
        date = datetime.fromtimestamp((identity.int >> 80) / 1000, timezone.utc)
        roots += tuple(
            codex_home / "sessions" / (date + timedelta(days=delta)).strftime("%Y/%m/%d")
            for delta in (0, -1, 1)
        )
    return tuple(dict.fromkeys(roots))


def _base_path(path: Path, base_id: str) -> Path | None:
    for directory in _base_directories(path, base_id):
        matches: set[Path] = set()
        for candidate in directory.glob(f"rollout-*{base_id}.jsonl*"):
            name = _ROLLOUT_NAME.fullmatch(candidate.name)
            if name is None or (name[2] or name[1]).casefold() != base_id:
                continue
            # A plain file and its compressed sibling represent one segment.
            plain = candidate.with_suffix("") if candidate.suffix == ".zst" else candidate
            candidate = resolve_rollout_path(plain)
            if candidate.is_file() and not candidate.is_symlink():
                matches.add(candidate)
        if matches:
            return next(iter(matches)) if len(matches) == 1 else None
    return None


def rollout_history_segments(path: Path) -> tuple[tuple[Path, int | None], ...]:
    """Return reachable ancestors in reading order, then the current segment."""
    current = resolve_rollout_path(path)
    segments: list[tuple[Path, int | None]] = [(current, None)]
    # Legacy and test rollouts with other filenames have no recorder lineage.
    if _ROLLOUT_NAME.fullmatch(current.name) is None:
        return tuple(segments)
    seen = {current}
    while (base := _history_base(*segments[-1])) is not None:
        base_id, byte_limit = base
        source = _base_path(segments[-1][0], base_id)
        if source is None or source in seen:
            break
        segments.append((source, byte_limit))
        seen.add(source)
    return tuple(reversed(segments))
