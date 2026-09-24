"""Provider-neutral per-file usage contribution state and path reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
from collections.abc import Sequence
from typing import Generic, TypeVar

from .core import JsonlTailState, UsageSummary


USAGE_CONTRIBUTION_PARSER_VERSION = "usage-events-v2"


@dataclass
class UsageInsightAggregate:
    summary: UsageSummary = field(default_factory=UsageSummary)
    priced_event_count: int = 0
    total_event_count: int = 0
    latest_event_at: datetime | None = None


@dataclass
class DailyUsageContribution:
    summary: UsageSummary = field(default_factory=UsageSummary)
    models: dict[str, UsageInsightAggregate] = field(default_factory=dict)
    priced_event_count: int = 0
    total_event_count: int = 0
    latest_event_at: datetime | None = None


@dataclass
class FileUsageContribution:
    mtime: float | None
    file_size: int | None
    day_start: datetime
    week_start: datetime
    month_start: datetime
    model_provider: str
    summary_day: UsageSummary
    summary_week: UsageSummary
    summary_month: UsageSummary
    summary_all: UsageSummary = field(default_factory=UsageSummary)
    session_id: str = ""
    parent_session_id: str = ""
    session_key: str = ""
    session_title: str = ""
    workdir_name: str = ""
    workdir: str = ""
    archived: bool = False
    can_activate: bool = False
    models_day: dict[str, UsageInsightAggregate] = field(default_factory=dict)
    models_week: dict[str, UsageInsightAggregate] = field(default_factory=dict)
    models_month: dict[str, UsageInsightAggregate] = field(default_factory=dict)
    day_priced_event_count: int = 0
    day_total_event_count: int = 0
    week_priced_event_count: int = 0
    week_total_event_count: int = 0
    month_priced_event_count: int = 0
    month_total_event_count: int = 0
    day_latest_event_at: datetime | None = None
    week_latest_event_at: datetime | None = None
    month_latest_event_at: datetime | None = None
    parser_version: str = ""
    tail_state: JsonlTailState | None = None
    mtime_ns: int | None = None
    daily_usage: dict[str, DailyUsageContribution] = field(default_factory=dict)
    daily_usage_complete: bool = False


def canonical_usage_path(path: Path) -> Path:
    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        return Path(os.path.normpath(expanded))
    try:
        return expanded.resolve(strict=False)
    except OSError:
        return expanded.absolute()


def usage_scan_roots(sessions_root: Path) -> tuple[Path, ...]:
    roots = [sessions_root]
    if sessions_root.name == "sessions":
        roots.append(sessions_root.parent / "archived_sessions")
    return tuple(dict.fromkeys(roots))


def iter_usage_jsonl_files(root: Path) -> list[Path]:
    """Enumerate JSONL files without Path.rglob's repeated Windows stat calls."""
    found: list[Path] = []
    pending = [Path(root)]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(
                            follow_symlinks=False
                        ) and entry.name.casefold().endswith(".jsonl"):
                            found.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def usage_jsonl_stat_tokens(root: Path) -> list[tuple[Path, int, int]]:
    found: list[tuple[Path, int, int]] = []
    pending = [Path(root)]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(
                            follow_symlinks=False
                        ) and entry.name.casefold().endswith(".jsonl"):
                            stat = entry.stat(follow_symlinks=False)
                            found.append(
                                (
                                    Path(entry.path),
                                    int(stat.st_mtime_ns),
                                    int(stat.st_size),
                                )
                            )
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def path_under_usage_roots(path: Path, scan_roots: Sequence[Path]) -> bool:
    resolved = canonical_usage_path(path)
    for root in scan_roots:
        try:
            resolved.relative_to(canonical_usage_path(root))
        except ValueError:
            continue
        return True
    return False


def usage_parser_version(parser: object) -> str:
    custom = str(getattr(parser, "usage_contribution_version", "") or "").strip()
    if custom:
        return custom
    estimator = getattr(parser, "cost_estimator", None)
    pricing_fingerprint = str(
        getattr(estimator, "pricing_fingerprint", "") or ""
    ).strip()
    if pricing_fingerprint:
        return f"{USAGE_CONTRIBUTION_PARSER_VERSION}:{pricing_fingerprint}"
    return USAGE_CONTRIBUTION_PARSER_VERSION


T = TypeVar("T")


class ContributionIndex(Generic[T]):
    """Own canonical path keys so query facades do not duplicate path policy."""

    def __init__(self) -> None:
        self._entries: dict[Path, T] = {}

    def get(self, path: Path) -> T | None:
        return self._entries.get(canonical_usage_path(path))

    def replace(self, path: Path, contribution: T) -> T | None:
        key = canonical_usage_path(path)
        previous = self._entries.get(key)
        self._entries[key] = contribution
        return previous

    def remove(self, path: Path) -> T | None:
        return self._entries.pop(canonical_usage_path(path), None)
