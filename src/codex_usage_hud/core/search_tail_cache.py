"""Bounded extraction state for append-only, uncompressed search rollouts."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Protocol


class ExtractionState(Protocol):
    @property
    def exhausted(self) -> bool: ...
    @property
    def retained_bytes(self) -> int: ...
    def consume(self, line: str) -> None: ...
    def result(self) -> tuple[str, str, str, tuple[str, ...]]: ...


@dataclass
class _Entry:
    state: ExtractionState
    identity: tuple[int, int]
    size: int
    mtime_ns: int
    ctime_ns: int
    offset: int
    prefix: bytes
    tail: bytes
    prefix_hash: Any


class SearchTailCache:
    """Keep extraction fragments, never raw records or full historical files.

    Growing files are checked against the entire previously consumed byte
    prefix before reusing extraction state, so an in-place middle edit plus
    an append cannot silently preserve old search terms. Hashing cached file
    bytes is cheaper than re-decoding/cleaning/re-indexing historical records.
    Compressed/multi-file families use the full streaming reader instead.
    """

    def __init__(self, *, max_entries: int = 4, max_bytes: int = 8 * 1024 * 1024) -> None:
        self._entries: OrderedDict[Path, _Entry] = OrderedDict()
        self._max_entries = max(0, max_entries)
        self._max_bytes = max(0, max_bytes)
        self._lock = RLock()

    @staticmethod
    def _witnesses(path: Path, offset: int) -> tuple[bytes, bytes]:
        # BufferedReader can prefetch hundreds of KiB for each 4 KiB witness.
        # These are deliberate small random reads, not a streaming scan.
        with path.open("rb", buffering=0) as handle:
            prefix = handle.read(min(4096, offset))
            handle.seek(max(0, offset - 4096))
            tail = handle.read(min(4096, offset))
        return prefix, tail

    @staticmethod
    def _extend_hash(path: Path, digest: Any, start: int, end: int) -> Any:
        with path.open("rb", buffering=0) as handle:
            handle.seek(start)
            remaining = end - start
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise OSError("Rollout truncated while checking its indexed prefix")
                digest.update(chunk)
                remaining -= len(chunk)
        return digest

    def parse(self, path: Path, factory: Callable[[], ExtractionState]) -> tuple[str, str, str, tuple[str, ...]]:
        path = Path(path).absolute()
        with self._lock:
            before = path.stat()
            identity = (before.st_dev, before.st_ino)
            entry = self._entries.pop(path, None)
            prefix_hash = hashlib.sha256()
            hashed_offset = 0
            if entry is not None:
                same = (
                    entry.identity == identity
                    and entry.size == before.st_size
                    and entry.mtime_ns == before.st_mtime_ns
                    and entry.ctime_ns == before.st_ctime_ns
                )
                append = entry.identity == identity and before.st_size > entry.size
                if append and self._witnesses(path, entry.offset) == (entry.prefix, entry.tail):
                    verified_hash = self._extend_hash(path, hashlib.sha256(), 0, entry.offset)
                    append = verified_hash.digest() == entry.prefix_hash.digest()
                else:
                    append = False
                if same or append:
                    state, offset = entry.state, entry.offset
                    prefix_hash, hashed_offset = entry.prefix_hash.copy(), entry.offset
                else:
                    state, offset = factory(), 0
            else:
                state, offset = factory(), 0
            complete = True
            if not state.exhausted:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    while not state.exhausted:
                        line = handle.readline()
                        if not line:
                            break
                        state.consume(line)
                        offset = handle.tell()
                        # Match the full reader's handling of a valid final
                        # record without a newline, but do not cache a state
                        # that might absorb a subsequently completed record.
                        if not line.endswith("\n"):
                            complete = False
                            break
            after = path.stat()
            result = state.result()
            if (
                complete
                and 0 <= offset <= after.st_size
                and (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                and state.retained_bytes <= self._max_bytes
            ):
                prefix, tail = self._witnesses(path, offset)
                prefix_hash = self._extend_hash(path, prefix_hash, hashed_offset, offset)
                verified = path.stat()
                if (verified.st_ino, verified.st_size, verified.st_mtime_ns, verified.st_ctime_ns) != (
                    after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
                ):
                    return result
                self._entries[path] = _Entry(
                    state, identity, after.st_size, after.st_mtime_ns, after.st_ctime_ns,
                    offset, prefix, tail, prefix_hash,
                )
                while self._entries and (
                    len(self._entries) > self._max_entries
                    or sum(value.state.retained_bytes + 8192 for value in self._entries.values()) > self._max_bytes
                ):
                    self._entries.popitem(last=False)
            return result

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
