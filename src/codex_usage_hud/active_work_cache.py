"""Bounded parsed snapshots for background work discovery (no raw JSONL cache)."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from threading import RLock

from .core import ParsedSession
from .disk_sequence import retained_size
from .usage_contributions import usage_parser_version


class ActiveWorkSnapshotCache:
    """Reuse unchanged rollouts while still re-evaluating time-dependent status."""

    def __init__(
        self, max_entries: int = 16,
        *,
        max_bytes: int = 4 * 1024 * 1024,
        project: Callable[[ParsedSession], ParsedSession] | None = None,
    ) -> None:
        self._max_entries = max(1, max_entries)
        self._max_bytes = max(0, int(max_bytes))
        self._retained_bytes = 0
        self._entries: OrderedDict[Path, tuple[tuple, ParsedSession, int]] = OrderedDict()
        self._lock = RLock()
        self._parser: object | None = None
        self._version: tuple[str, bool] | None = None
        self._project = project

    @staticmethod
    def _signature(path: Path) -> tuple | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def parse(self, parser: object, path: Path) -> ParsedSession:
        # Keep the parser itself, rather than its id: ids can be reused after
        # settings replace the parser. Pricing changes also invalidate results.
        version = (usage_parser_version(parser), bool(getattr(parser, "estimate_enabled", True)))
        key = Path(path).absolute()
        with self._lock:
            if parser is not self._parser or version != self._version:
                self._entries.clear()
                self._retained_bytes = 0
                self._parser, self._version = parser, version
            signature = self._signature(key)
            entry = self._entries.get(key)
            if signature is not None and entry is not None and entry[0] == signature:
                self._entries.move_to_end(key)
                return entry[1]
            removed = self._entries.pop(key, None)
            if removed is not None:
                self._retained_bytes -= removed[2]
            work_parser = getattr(type(parser), "parse_work_file", None)
            snapshot = work_parser(parser, path) if callable(work_parser) else parser.parse_file(path)
            if self._project is not None:
                snapshot = self._project(snapshot)
            retained_bytes = retained_size(snapshot)
            # A writer may append/replace the file during parsing. Serve this
            # observation, but never label a partial observation as up to date.
            if (
                retained_bytes <= self._max_bytes
                and signature is not None
                and self._signature(key) == signature
            ):
                while self._entries and (
                    len(self._entries) >= self._max_entries
                    or self._retained_bytes + retained_bytes > self._max_bytes
                ):
                    _old_path, (_old_signature, _old_snapshot, old_bytes) = self._entries.popitem(last=False)
                    self._retained_bytes -= old_bytes
                self._entries[key] = (signature, snapshot, retained_bytes)
                self._retained_bytes += retained_bytes
            return snapshot

    def close(self) -> None:
        with self._lock:
            self._entries.clear()
            self._retained_bytes = 0
            self._parser = None
