"""Lossless temporary history storage with one shared decoded-object budget.

Owned by the selected-session cache only. Files are anonymous/temporary and
stay alive while a snapshot or slice references them; eviction never invalidates
an in-flight reader. Desktop work/bubble parsing does not use this storage.
"""

from __future__ import annotations

from array import array
from collections import OrderedDict
from collections.abc import Mapping, Sequence
import copy
from dataclasses import fields, is_dataclass
import pickle
import sys
import tempfile
from threading import RLock
import weakref


def retained_size(value: object) -> int:
    """Count unique owned containers/strings, without traversing modules/classes."""
    seen: set[int] = set()
    pending = [value]
    size = 0
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        size += sys.getsizeof(item)
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)):
            pending.extend(item)
        elif is_dataclass(item) and not isinstance(item, type):
            pending.extend(getattr(item, field.name) for field in fields(item))
    return size


class DecodedHistoryCache:
    """LRU shared by every raw/history sequence of one selected-session owner."""

    def __init__(self, max_bytes: int = 2 * 1024 * 1024):
        self.max_bytes = max(0, int(max_bytes))
        self.retained_bytes = 0
        self._values = OrderedDict()
        self._lock = RLock()

    def get(self, key):
        with self._lock:
            entry = self._values.get(key)
            if entry is not None:
                self._values.move_to_end(key)
                return entry[0]
        return None

    def put(self, key, value, *, size=None):
        size = retained_size(value) if size is None else size
        with self._lock:
            if key in self._values:
                self.retained_bytes -= self._values.pop(key)[1]
            if size > self.max_bytes:
                return
            while self._values and self.retained_bytes + size > self.max_bytes:
                self.retained_bytes -= self._values.popitem(last=False)[1][1]
            self._values[key] = (value, size)
            self.retained_bytes += size

    def release(self, owner):
        with self._lock:
            for key in tuple(self._values):
                if key[0] is owner:
                    self.retained_bytes -= self._values.pop(key)[1]

    def clear(self):
        with self._lock:
            self._values.clear()
            self.retained_bytes = 0


class DiskSequence(Sequence):
    """Appendable root and lazy slices; decoding never materializes a full log."""

    def __init__(self, values=(), *, cache: DecodedHistoryCache | None = None):
        self._file = tempfile.TemporaryFile(prefix="codex-hud-history-")
        self._offsets = array("Q", [0])
        self._sizes = array("Q")
        self._cache = cache if cache is not None else DecodedHistoryCache()
        self._token = object()
        self._lock = RLock()
        self._finalizer = weakref.finalize(self, _close_storage, self._file, self._cache, self._token)
        for value in values:
            self.append(value)

    def append(self, value):
        size = retained_size(value)
        with self._lock:
            start = self._offsets[-1]
            self._file.seek(start)
            try:
                pickle.dump(value, self._file, protocol=pickle.HIGHEST_PROTOCOL)
                self._file.flush()
            except BaseException:
                self._file.seek(start)
                self._file.truncate()
                raise
            self._offsets.append(self._file.tell())
            # This immutable value's size is independent of how many parser
            # passes read it. Avoid walking the same object graph on every miss.
            self._sizes.append(size)

    def __len__(self):
        return len(self._offsets) - 1

    def __getitem__(self, index):
        if isinstance(index, slice):
            return _DiskSlice(self, range(len(self))[index])
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        key = (self._token, index)
        with self._lock:
            value = self._cache.get(key)
            if value is None:
                self._file.seek(self._offsets[index])
                value = pickle.loads(self._file.read(self._offsets[index + 1] - self._offsets[index]))
                self._cache.put(key, value, size=self._sizes[index])
            return value

    def __iter__(self):
        return (self[index] for index in range(len(self)))

    def __eq__(self, other):
        return isinstance(other, Sequence) and len(self) == len(other) and all(
            a == b for a, b in zip(self, other)
        )

    def __deepcopy__(self, memo):
        return [copy.deepcopy(value, memo) for value in self]


class _DiskSlice(Sequence):
    def __init__(self, owner, indexes):
        self._owner = owner
        self._indexes = indexes

    def __len__(self):
        return len(self._indexes)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return _DiskSlice(self._owner, self._indexes[index])
        return self._owner[self._indexes[index]]

    def __iter__(self):
        return (self._owner[index] for index in self._indexes)


class DiskRecordSequence(DiskSequence):
    """Keep small record headers hot; fetch large payload fields on demand.

The parser makes several metadata passes. Decoding an entire tool output on
each type/timestamp check would turn a memory improvement into an I/O/CPU cost.
Payload descriptors preserve every field and share the same total LRU budget.
"""

    def __init__(self, *, cache):
        self._headers = DiskSequence(cache=cache)
        self._payloads = DiskSequence(cache=cache)

    def append(self, record):
        header = {key: value for key, value in record.items() if key != 'payload'}
        payload = record.get('payload')
        if not isinstance(payload, Mapping):
            self._headers.append((header, payload, None))
            return
        small, offsets = {}, {}
        for key, value in payload.items():
            if isinstance(value, (str, int, float, bool, type(None))) and sys.getsizeof(value) <= 256:
                small[key] = value
            else:
                offsets[key] = len(self._payloads)
                self._payloads.append(value)
        self._headers.append((header, small, offsets))

    def __len__(self):
        return len(self._headers)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return _DiskSlice(self, range(len(self))[index])
        header, small, offsets = self._headers[index]
        return {**header, 'payload': small if offsets is None else _RecordPayload(small, offsets, self._payloads)}


class _RecordPayload(Mapping):
    def __init__(self, small, offsets, values):
        self._small = small
        self._offsets = offsets
        self._values = values

    def __len__(self):
        return len(self._small) + len(self._offsets)

    def __iter__(self):
        yield from self._small
        yield from self._offsets

    def __getitem__(self, key):
        if key in self._small:
            return self._small[key]
        return self._values[self._offsets[key]]


def spill_snapshot_history(snapshot, cache):
    """Keep full task/request history accessible, without pinning its text."""
    for name in ("session_request_history", "request_history", "activity_tasks"):
        values = getattr(snapshot, name, None)
        if values and not isinstance(values, DiskSequence):
            setattr(snapshot, name, DiskSequence(values, cache=cache))


def _close_storage(file, cache, token):
    try:
        file.close()
    finally:
        cache.release(token)
