"""Bounded parsed snapshots for background work discovery (no raw JSONL cache)."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from threading import RLock

from .core import ParsedSession
from .usage_contributions import usage_parser_version


class ActiveWorkSnapshotCache:
    """Reuse unchanged rollouts while still re-evaluating time-dependent status."""

    def __init__(
        self, max_entries: int = 16,
        *, project: Callable[[ParsedSession], ParsedSession] | None = None,
    ) -> None:
        self._max_entries = max(1, max_entries)
        self._entries: OrderedDict[Path, tuple[tuple, ParsedSession]] = OrderedDict()
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
                self._parser, self._version = parser, version
            signature = self._signature(key)
            entry = self._entries.get(key)
            if signature is not None and entry is not None and entry[0] == signature:
                self._entries.move_to_end(key)
                return entry[1]
            self._entries.pop(key, None)
            work_parser = getattr(type(parser), "parse_work_file", None)
            snapshot = work_parser(parser, path) if callable(work_parser) else parser.parse_file(path)
            if self._project is not None:
                snapshot = self._project(snapshot)
            # A writer may append/replace the file during parsing. Serve this
            # observation, but never label a partial observation as up to date.
            if signature is not None and self._signature(key) == signature:
                self._entries[key] = (signature, snapshot)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
            return snapshot

    def close(self) -> None:
        with self._lock:
            self._entries.clear()
            self._parser = None
