"""Disk postings and bounded candidate buffers for the renderer's search.

The durable document table remains authoritative. This disposable sidecar
stores the *same* token streams and gram bitmaps as SessionSearchIndex, without
rehydrating millions of Python strings, tuples and sets at process startup.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from contextlib import nullcontext
import pickle
import mmap
import os
import secrets
import sqlite3
import sys
from pathlib import Path
from threading import Timer

from .session_search import (
    SessionSearchIndex,
    _MemoryDocument,
    _memory_fields,
    _memory_field,
    _SNAPSHOT_VERSION,
)
from ..memory_policy import SEARCH_CANDIDATE_BYTES, SEARCH_EXTRACTION_BYTES
from .search_tail_cache import SearchTailCache


_QUERY_VERSION = _SNAPSHOT_VERSION * 100 + 5


class _Text:
    """UTF-8 token stream; substring checks stay in C without decoding it."""

    __slots__ = ("buffer", "start", "size")

    def __init__(self, buffer, start, size) -> None:
        self.buffer = buffer
        self.start = start
        self.size = size

    def find(self, needle, start=0):
        pos = self.buffer.find(needle, self.start + start, self.start + self.size)
        return pos - self.start if pos >= 0 else -1

    def startswith(self, needle):
        return (
            len(needle) <= self.size
            and self.buffer[self.start : self.start + len(needle)] == needle
        )

    def byte_at(self, index):
        return self.buffer[self.start + index]

    def __contains__(self, term: str) -> bool:
        return self.find(term.encode("utf-8")) >= 0

    def split(self, separator: str):
        return (
            part.decode("utf-8")
            for part in self.buffer[self.start : self.start + self.size].split(
                separator.encode("utf-8")
            )
        )

    def first_matching_token(self, terms):
        # Streams are already casefolded. Find the earliest matching token
        # directly rather than allocating/decoding every preceding file token.
        hits = [self.find(term.encode("utf-8")) for term in terms]
        position = min((hit for hit in hits if hit >= 0), default=-1)
        if position < 0:
            return ""
        absolute = self.start + position
        start = self.buffer.rfind(b"\0", self.start, absolute) + 1
        start = max(start, self.start)
        end = self.buffer.find(b"\0", absolute, self.start + self.size)
        if end < 0:
            end = self.start + self.size
        return self.buffer[start:end].decode("utf-8")


class _Tokens:
    __slots__ = ("text",)

    def __init__(self, text: _Text) -> None:
        self.text = text

    def __contains__(self, term: str) -> bool:
        value = self.text
        needle = term.encode("utf-8")
        start = 0
        while True:
            pos = value.find(needle, start)
            if pos < 0:
                return False
            end = pos + len(needle)
            if (pos == 0 or value.byte_at(pos - 1) == 0) and (
                end == value.size or value.byte_at(end) == 0
            ):
                return True
            start = pos + 1

    def has_prefix(self, term: str) -> bool:
        needle = term.encode("utf-8")
        return self.text.startswith(needle) or self.text.find(b"\0" + needle) >= 0

    def match_score(self, term):
        value = self.text
        needle = term.encode("utf-8")
        pos = value.find(needle)
        if pos < 0:
            return 0.0
        score = 1.0
        while pos >= 0:
            end = pos + len(needle)
            if pos == 0 or value.byte_at(pos - 1) == 0:
                if end == value.size or value.byte_at(end) == 0:
                    return 2.0
                score = 1.4
            pos = value.find(needle, pos + 1)
        return score

    def __iter__(self):
        return self.text.split("\0")


class _AllFields:
    __slots__ = ("fields", "tokens")

    def __init__(self, fields, *, tokens=False) -> None:
        self.fields = fields
        self.tokens = tokens

    def __contains__(self, term: str) -> bool:
        return any(term in field[1 if self.tokens else 3] for field in self.fields)


class _Documents(Mapping):
    __slots__ = ("owner",)

    def __init__(self, owner) -> None:
        self.owner = owner

    def __iter__(self):
        return iter(self.owner._session_slots)

    def __len__(self):
        return len(self.owner._session_slots)

    def __getitem__(self, key):
        return self.owner._candidate(key)


class _Postings:
    __slots__ = ("owner",)

    def __init__(self, owner) -> None:
        self.owner = owner

    def get(self, gram, default=0):
        owner = self.owner
        cached = owner._gram_cache.get(gram)
        if cached is not None:
            owner._gram_cache.move_to_end(gram)
            return cached
        connection = owner._query_connection()
        if not owner._memory_loaded:
            raise _ReloadIndex()
        row = connection.execute(
            "SELECT mask FROM postings WHERE gram=?",
            (gram,),
        ).fetchone()
        mask = int.from_bytes(row[0], "little") if row else default
        owner._gram_cache[gram] = mask
        if len(owner._gram_cache) > 4096:
            owner._gram_cache.popitem(last=False)
        return mask


class _ReloadIndex(RuntimeError):
    """Restart candidate selection after recovering a disposable sidecar."""


class OnDemandSessionSearchIndex(SessionSearchIndex):
    """Same extractor/ranker, disk bitmaps and a bounded candidate partition.

    `memory_loaded` means the small catalog is ready, independently of buffer
    residency. Releasing buffers never changes coverage or makes search wait
    for a corpus-wide unpickle. All access is serialized by the parent's RLock.
    """

    def __init__(
        self, *args, cache_bytes=SEARCH_CANDIDATE_BYTES, idle_seconds=60.0, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self._tail_cache = SearchTailCache(max_bytes=SEARCH_EXTRACTION_BYTES)
        self._documents = _Documents(self)
        self._postings = _Postings(self)
        self._query_db = None
        self._schema_ready = False
        self._query_depth = 0
        self._streams = None
        self._candidate_cache = OrderedDict()
        self._candidate_bytes = 0
        self._cache_limit = max(0, cache_bytes)
        self._gram_cache = OrderedDict()
        self._stamps = {}
        self._descriptors = {}
        self._idle_seconds = idle_seconds
        self._idle_timer = None
        self._idle_generation = 0
        self._retain_queries = False
        self._bulk_masks = None

    def _query_path(self):
        return self.path.with_name(self.path.name + ".query")

    def _stream_path(self):
        return self.path.with_name(self.path.name + ".streams")

    def _stream_buffer(self):
        if self._streams is None:
            with self._stream_path().open("rb") as handle:
                self._streams = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        return self._streams

    def _invalidate_buffers(self):
        self._candidate_cache.clear()
        self._candidate_bytes = 0
        self._preview_cache = {}
        # Views keep their immutable mapping alive until the last in-flight
        # navigation has finished. Dropping ownership releases unused mappings.
        self._streams = None

    def _query_connection(self):
        try:
            return self._initialize_query_connection()
        except sqlite3.DatabaseError as error:
            self._close_query_connection()
            self._schema_ready = False
            code = getattr(error, "sqlite_errorcode", 0) & 255
            if code not in (11, 26) and str(error) not in (
                "file is not a database",
                "database disk image is malformed",
            ):
                raise
            path = self._query_path()
            for artifact in (
                path,
                path.with_name(path.name + "-wal"),
                path.with_name(path.name + "-shm"),
            ):
                artifact.unlink(missing_ok=True)
            self._memory_loaded = False
            self._invalidate_buffers()
            return self._initialize_query_connection()

    def _initialize_query_connection(self):
        if self._query_db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self._query_path(), timeout=2.0, check_same_thread=False
            )
            self._query_db = connection
            if self._schema_ready:
                connection.execute("PRAGMA synchronous=NORMAL")
                connection.execute("PRAGMA cache_size=-1024")
                return connection
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA cache_size=-1024")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, _QUERY_VERSION):
                connection.execute("DROP TABLE IF EXISTS documents")
                connection.execute("DROP TABLE IF EXISTS postings")
                connection.execute("DROP TABLE IF EXISTS document_grams")
            # Keep query rows small: paths after a huge gram blob would force
            # SQLite to walk overflow pages on every candidate fetch.
            connection.execute(
                "CREATE TABLE IF NOT EXISTS documents (session_id TEXT PRIMARY KEY, slot INTEGER UNIQUE NOT NULL, stamp REAL NOT NULL, fields BLOB NOT NULL, paths BLOB NOT NULL, stream_end INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS document_grams (session_id TEXT PRIMARY KEY, grams BLOB NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS postings (gram TEXT PRIMARY KEY, mask BLOB NOT NULL) WITHOUT ROWID"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS document_stamps ON documents(session_id, slot, stamp)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS stream_generation (value BLOB NOT NULL)"
            )
            generation = connection.execute(
                "SELECT value FROM stream_generation"
            ).fetchone()
            try:
                with self._stream_path().open("rb") as stream:
                    header = stream.read(32)
            except FileNotFoundError:
                header = b""
            if (
                generation is None
                or header != generation[0]
                or version not in (0, _QUERY_VERSION)
            ):
                # Detect interrupted compaction before serving any offset.
                connection.execute("DELETE FROM documents")
                connection.execute("DELETE FROM postings")
                connection.execute("DELETE FROM document_grams")
                connection.execute("DELETE FROM stream_generation")
                header = secrets.token_bytes(32)
                self._stream_path().write_bytes(header)
                connection.execute(
                    "INSERT INTO stream_generation VALUES (?)", (header,)
                )
                self._memory_loaded = False
                self._invalidate_buffers()
            connection.execute(f"PRAGMA user_version={_QUERY_VERSION}")
            connection.commit()
            self._schema_ready = True
            self._query_db = connection
        return self._query_db

    def _candidate(self, session_id):
        with self._lock:
            if session_id not in self._session_slots:
                raise KeyError(session_id)
            cached = self._candidate_cache.get(session_id)
            if cached is not None:
                self._candidate_cache.move_to_end(session_id)
                return cached[0]
            row = self._descriptors.get(session_id)
            if row is None:
                raise KeyError(session_id)
            packed = pickle.loads(row[0])
            buffer = self._stream_buffer()
            if any(
                start < 32 or size < 0 or start + size > len(buffer)
                for _name, start, size in packed
            ):
                raise ValueError("Search token stream is incomplete")
            path_start, path_size = pickle.loads(row[1])
            if path_start < 32 or path_start + path_size > len(buffer):
                raise ValueError("Search path stream is incomplete")
            paths, basenames = pickle.loads(buffer[path_start : path_start + path_size])
            path_bytes = sum(
                sys.getsizeof(values) + sum(sys.getsizeof(value) for value in values)
                for values in (paths, basenames)
            )
            copy_buffers = (
                sum(size for _name, _start, size in packed) + path_bytes + 2048
                <= self._cache_limit
            )
            base_offset = 0
            if copy_buffers and packed:
                base_offset = min(start for _name, start, _size in packed)
                end_offset = max(start + size for _name, start, size in packed)
                buffer = buffer[base_offset:end_offset]
            fields = tuple(
                (
                    name,
                    _Tokens(text := _Text(buffer, start - base_offset, size)),
                    (),
                    text,
                )
                for name, start, size in packed
            )
            document = _MemoryDocument(
                session_id,
                fields,
                _AllFields(fields),
                _AllFields(fields, tokens=True),
                paths,
                basenames,
            )
            # Payload + bounded object overhead. Oversized documents are used
            # only for this query; they must not evict/overrun the whole cache.
            size = sum(length for _name, _start, length in packed) + path_bytes + 2048
            if size <= self._cache_limit:
                while (
                    self._candidate_cache
                    and self._candidate_bytes + size > self._cache_limit
                ):
                    _key, (_document, old_size) = self._candidate_cache.popitem(
                        last=False
                    )
                    self._candidate_bytes -= old_size
                self._candidate_cache[session_id] = (document, size)
                self._candidate_bytes += size
            if not self._query_depth:
                self._close_query_connection()
            return document

    def _evict(self, session_id):
        cached = self._candidate_cache.pop(session_id, None)
        if cached:
            self._candidate_bytes -= cached[1]
        self._preview_cache = {}

    def _store(self, session_id, fields, paths, stamp):
        connection = self._query_connection()
        previous = connection.execute(
            "SELECT slot, grams FROM documents JOIN document_grams USING(session_id) WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if previous:
            slot, old_encoded = previous
            old_grams = set(pickle.loads(old_encoded))
        else:
            slot = (
                self._free_slots.pop() if self._free_slots else len(self._slot_sessions)
            )
            old_grams = set()
        grams = {
            gram
            for _name, _tokens, field_grams, _text in fields
            for gram in field_grams
        }
        bit = 1 << slot
        changed = old_grams ^ grams
        updates = []
        deletes = []
        masks = self._masks(changed) if self._bulk_masks is None else self._bulk_masks
        for gram in changed:
            mask = masks.get(gram, 0)
            mask = mask | bit if gram in grams else mask & ~bit
            if self._bulk_masks is not None:
                self._bulk_masks[gram] = mask
            elif mask:
                updates.append(
                    (gram, mask.to_bytes((mask.bit_length() + 7) // 8, "little"))
                )
            else:
                deletes.append((gram,))
        packed = []
        with self._stream_path().open("ab") as stream:
            for name, _tokens, _grams, text in fields:
                data = text.encode("utf-8")
                packed.append((name, stream.tell(), len(data)))
                stream.write(data)
            path_data = pickle.dumps(
                (tuple(paths), tuple(Path(path).name for path in paths)), protocol=4
            )
            path_descriptor = pickle.dumps((stream.tell(), len(path_data)), protocol=4)
            stream.write(path_data)
            stream_end = stream.tell()
        field_descriptor = pickle.dumps(tuple(packed), protocol=4)
        with connection if self._bulk_masks is None else nullcontext():
            if self._bulk_masks is None:
                connection.executemany(
                    "INSERT INTO postings VALUES (?,?) ON CONFLICT(gram) DO UPDATE SET mask=excluded.mask",
                    updates,
                )
                connection.executemany("DELETE FROM postings WHERE gram=?", deletes)
            connection.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET stamp=excluded.stamp, fields=excluded.fields, paths=excluded.paths, stream_end=excluded.stream_end",
                (
                    session_id,
                    slot,
                    stamp,
                    field_descriptor,
                    path_descriptor,
                    stream_end,
                ),
            )
            connection.execute(
                "INSERT INTO document_grams VALUES (?,?) ON CONFLICT(session_id) DO UPDATE SET grams=excluded.grams",
                (session_id, pickle.dumps(tuple(grams), protocol=4)),
            )
        self._gram_cache.clear()
        self._invalidate_buffers()
        self._session_slots[session_id] = slot
        if slot == len(self._slot_sessions):
            self._slot_sessions.append(session_id)
        else:
            self._slot_sessions[slot] = session_id
        self._stamps[session_id] = stamp
        self._descriptors[session_id] = (field_descriptor, path_descriptor)

    def _masks(self, grams):
        values = list(grams)
        result = {}
        connection = self._query_connection()
        for start in range(0, len(values), 500):
            chunk = values[start : start + 500]
            rows = connection.execute(
                "SELECT gram, mask FROM postings WHERE gram IN ("
                + ",".join("?" for _ in chunk)
                + ")",
                chunk,
            )
            result.update((gram, int.from_bytes(mask, "little")) for gram, mask in rows)
        return result

    @staticmethod
    def _legacy_fields(legacy, session_id, stamp):
        if not isinstance(legacy, dict):
            return None
        try:
            if legacy["stamps"].get(session_id) != stamp:
                return None
            fields = legacy["documents"][session_id]
            values = []
            for name, tokens, grams in fields:
                if not isinstance(name, str) or not isinstance(tokens, (list, tuple)):
                    return None
                if not all(isinstance(token, str) for token in tokens):
                    return None
                if not isinstance(grams, (list, tuple, set, frozenset)) or not all(
                    isinstance(gram, str) for gram in grams
                ):
                    return None
                values.append((name, tokens, grams, "\0".join(tokens)))
            return tuple(values) or None
        except (KeyError, TypeError, ValueError, AttributeError):
            return None

    def _load_memory(self):
        with self._lock:
            self._last_load_reconciled = 0
            if self._memory_loaded:
                return len(self._session_slots)
            self.ensure_scan_index()
            source = self._connect()
            try:
                current = dict(
                    source.execute(
                        "SELECT session_id, indexed_at FROM session_search_documents"
                    )
                )
                rows = (
                    self._query_connection()
                    .execute(
                        "SELECT session_id, slot, stamp, stream_end, fields, paths FROM documents"
                    )
                    .fetchall()
                )
                self._session_slots = {
                    sid: slot for sid, slot, _stamp, _end, _fields, _paths in rows
                }
                self._slot_sessions = [None] * (
                    max(self._session_slots.values(), default=-1) + 1
                )
                stream_size = self._stream_path().stat().st_size
                self._stamps = {
                    sid: stamp
                    for sid, _slot, stamp, end, _fields, _paths in rows
                    if end <= stream_size
                }
                self._descriptors = {
                    sid: (fields, paths)
                    for sid, _slot, _stamp, _end, fields, paths in rows
                }
                for sid, slot in self._session_slots.items():
                    self._slot_sessions[slot] = sid
                self._free_slots = [
                    slot for slot, sid in enumerate(self._slot_sessions) if sid is None
                ]
                for sid in set(self._session_slots) - current.keys():
                    self._remove_memory_document_locked(sid)
                stale = [
                    sid
                    for sid, stamp in current.items()
                    if self._stamps.get(sid) != stamp
                ]
                self._last_load_reconciled = len(stale)
                if not rows and stale:
                    self._bulk_masks = {}
                    self._query_connection().execute("BEGIN")
                legacy = None
                # One-time conversion preserves even older snapshots' exact
                # find-bar hints. Do not rebuild all Python resident indexes.
                if stale and not rows and self._snapshot_path().exists():
                    try:
                        with self._snapshot_path().open("rb") as handle:
                            legacy = pickle.load(handle)
                        if not isinstance(legacy, dict) or legacy.get(
                            "version"
                        ) not in (5, 6):
                            legacy = None
                    except (
                        OSError,
                        ValueError,
                        EOFError,
                        pickle.UnpicklingError,
                        AttributeError,
                        ImportError,
                    ):
                        legacy = None
                for sid in stale:
                    fields = self._legacy_fields(legacy, sid, current[sid])
                    if fields is not None:
                        paths_by_session = legacy.get("changed_paths", {})
                        if not isinstance(paths_by_session, dict):
                            paths_by_session = {}
                        self._store(
                            sid,
                            fields,
                            paths_by_session.get(sid, ()),
                            current[sid],
                        )
                        continue
                    row = source.execute(
                        "SELECT session_id, fingerprints, user_text, assistant_text, tool_text, changed_paths, title, workdir, model_provider, client_kind FROM session_search_documents WHERE session_id=?",
                        (sid,),
                    ).fetchone()
                    document = self._document_from_row(row)
                    self._store(
                        sid,
                        _memory_fields(document),
                        document.changed_paths,
                        current[sid],
                    )
                if self._bulk_masks is not None:
                    connection = self._query_connection()
                    connection.executemany(
                        "INSERT INTO postings VALUES (?,?)",
                        (
                            (
                                gram,
                                mask.to_bytes((mask.bit_length() + 7) // 8, "little"),
                            )
                            for gram, mask in self._bulk_masks.items()
                            if mask
                        ),
                    )
                    connection.commit()
                self._memory_loaded = True
                return len(current)
            finally:
                self._bulk_masks = None
                if self._query_db is not None and self._query_db.in_transaction:
                    self._query_db.rollback()
                source.close()
                # Background maintenance leaves no query buffers/connection.
                self.unload_memory()

    def _apply_memory_documents(self, documents, *, precomputed=None):
        with self._lock:
            source = self._connect()
            try:
                for document in documents:
                    stamp = source.execute(
                        "SELECT indexed_at FROM session_search_documents WHERE session_id=?",
                        (document.session_id,),
                    ).fetchone()[0]
                    fields = (
                        precomputed.get(document.session_id) if precomputed else None
                    )
                    if fields:
                        fields = tuple(fields) + (
                            _memory_field(
                                "metadata",
                                " ".join(
                                    (
                                        document.title,
                                        document.workdir,
                                        document.model_provider,
                                        document.client_kind,
                                    )
                                ),
                            ),
                        )
                    else:
                        fields = _memory_fields(document)
                    self._store(
                        document.session_id, fields, document.changed_paths, stamp
                    )
                self._memory_loaded = True
            except Exception:
                self._memory_loaded = False
                raise
            finally:
                source.close()
                self._compact_streams()
                if not self._query_depth:
                    self._close_query_connection()
                self.release_when_idle()

    def _remove_memory_document_locked(self, session_id):
        if session_id not in self._session_slots:
            return
        connection = self._query_connection()
        slot = self._session_slots[session_id]
        row = connection.execute(
            "SELECT grams FROM document_grams WHERE session_id=?", (session_id,)
        ).fetchone()
        if row:
            updates, deletes = [], []
            grams = pickle.loads(row[0])
            masks = self._masks(grams)
            for gram in grams:
                mask = masks.get(gram, 0) & ~(1 << slot)
                if mask:
                    updates.append(
                        (mask.to_bytes((mask.bit_length() + 7) // 8, "little"), gram)
                    )
                else:
                    deletes.append((gram,))
        else:
            updates, deletes = [], []
        with connection:
            connection.executemany("UPDATE postings SET mask=? WHERE gram=?", updates)
            connection.executemany("DELETE FROM postings WHERE gram=?", deletes)
            connection.execute(
                "DELETE FROM documents WHERE session_id=?", (session_id,)
            )
            connection.execute(
                "DELETE FROM document_grams WHERE session_id=?", (session_id,)
            )
        self._gram_cache.clear()
        self._evict(session_id)
        self._session_slots.pop(session_id)
        self._stamps.pop(session_id, None)
        self._descriptors.pop(session_id, None)
        self._slot_sessions[slot] = None
        self._free_slots.append(slot)

    def remove_missing(self, session_ids):
        with self._lock:
            try:
                return super().remove_missing(session_ids)
            finally:
                if not self._query_depth:
                    self._close_query_connection()

    def _write_snapshot(self, connection=None, *, force=True):
        # Every changed document and its postings commit together. A stale
        # sidecar after a crash is repaired by stamp on the next load.
        with self._lock:
            try:
                self._compact_streams()
                return True
            finally:
                if not self._query_depth:
                    self._close_query_connection()

    def _compact_streams(self):
        """Bound append garbage; a generation header makes crashes detectable."""
        path = self._stream_path()
        if not path.exists() or path.stat().st_size < 32 * 1024 * 1024:
            return
        connection = self._query_connection()
        rows = connection.execute(
            "SELECT session_id, fields, paths FROM documents"
        ).fetchall()
        decoded = [
            (sid, pickle.loads(fields), pickle.loads(paths))
            for sid, fields, paths in rows
        ]
        live = sum(
            size for _sid, fields, _paths in decoded for _name, _offset, size in fields
        ) + sum(paths[1] for _sid, _fields, paths in decoded)
        if path.stat().st_size <= live * 2 + 32:
            return
        self._invalidate_buffers()
        temporary = path.with_name(path.name + ".tmp")
        generation = secrets.token_bytes(32)
        updates = []
        try:
            with path.open("rb") as source, temporary.open("wb") as target:
                target.write(generation)
                for sid, fields, paths in decoded:
                    packed = []
                    for name, offset, size in fields:
                        packed.append((name, target.tell(), size))
                        source.seek(offset)
                        remaining = size
                        while remaining:
                            chunk = source.read(min(remaining, 1024 * 1024))
                            if not chunk:
                                raise OSError(
                                    "Incomplete search stream during compaction"
                                )
                            target.write(chunk)
                            remaining -= len(chunk)
                    source.seek(paths[0])
                    path_descriptor = pickle.dumps(
                        (target.tell(), paths[1]), protocol=4
                    )
                    target.write(source.read(paths[1]))
                    updates.append(
                        (
                            pickle.dumps(tuple(packed), protocol=4),
                            path_descriptor,
                            target.tell(),
                            sid,
                        )
                    )
                target.flush()
                os.fsync(target.fileno())
            with connection:
                connection.executemany(
                    "UPDATE documents SET fields=?, paths=?, stream_end=? WHERE session_id=?",
                    updates,
                )
                connection.execute(
                    "UPDATE stream_generation SET value=?", (generation,)
                )
                os.replace(temporary, path)
            self._descriptors = {
                sid: (fields, paths) for fields, paths, _end, sid in updates
            }
        except OSError:
            # A navigation may still hold an old mapping on Windows. Leave
            # its immutable generation in place and retry on a later update.
            pass
        except sqlite3.Error:
            self._memory_loaded = False
            self._schema_ready = False
            self._invalidate_buffers()
            self._close_query_connection()
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def _candidate_may_match(self, document, terms):
        # The shared ranker verifies every term in each field immediately
        # afterwards. Avoid scanning mapped streams twice just to pre-filter.
        return True

    def _field_match_score(self, tokens, field_text, term):
        return tokens.match_score(term)

    def search(self, query, **kwargs):
        with self._lock:
            interactive = kwargs.get("load", True)
            if interactive and str(query or "").strip():
                self._retain_queries = True
            retaining = self._retain_queries
            self._query_depth += 1
            try:
                if (
                    str(query or "").strip()
                    and self._query_db is None
                    and not self._gram_cache
                ):
                    self._query_connection()
                if str(query or "").strip() and not self._memory_loaded:
                    self._load_memory()
                    self._retain_queries = retaining
                    if retaining and self._idle_timer is None:
                        self._schedule_release()
                try:
                    return super().search(query, **kwargs)
                except _ReloadIndex:
                    self._load_memory()
                    self._retain_queries = retaining
                    if retaining and self._idle_timer is None:
                        self._schedule_release()
                    return super().search(query, **kwargs)
            finally:
                self._query_depth -= 1
                if not self._retain_queries:
                    self._invalidate_buffers()
                    self._gram_cache.clear()
                    self._close_query_connection()
                elif interactive and (self._candidate_cache or self._gram_cache):
                    self._schedule_release()

    def preview_matches(self, session_ids, query):
        with self._lock:
            self._retain_queries = True
            self._query_depth += 1
            try:
                return super().preview_matches(session_ids, query)
            finally:
                self._query_depth -= 1
                if self._candidate_cache or self._query_db is not None:
                    self._schedule_release()

    def _schedule_release(self):
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        self._idle_generation += 1
        generation = self._idle_generation

        def release():
            with self._lock:
                if generation == self._idle_generation:
                    self.unload_memory()

        self._idle_timer = Timer(self._idle_seconds, release)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def release_when_idle(self):
        with self._lock:
            if self._idle_timer is None:
                self._schedule_release()

    def begin_query(self):
        """Only an explicit query/navigation extends the residency window."""
        with self._lock:
            self._retain_queries = True
            self._schedule_release()

    def unload_memory(self):
        """Release buffers, preserving the tiny ready catalog and disk index."""
        with self._lock:
            self._retain_queries = False
            self._idle_generation += 1
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            self._invalidate_buffers()
            self._gram_cache.clear()
            self._preview_cache = {}
            self._tail_cache.clear()
            self._close_query_connection()

    def _close_query_connection(self):
        if self._query_db is not None:
            self._query_db.close()
            self._query_db = None

    def _reset_memory_locked(self):
        self.unload_memory()
        self._session_slots.clear()
        self._slot_sessions.clear()
        self._free_slots.clear()
        self._stamps.clear()
        self._descriptors.clear()
        self._schema_ready = False

    def index_artifact_paths(self):
        path = self._query_path()
        stream = self._stream_path()
        return super().index_artifact_paths() + (
            path,
            path.with_name(path.name + "-wal"),
            path.with_name(path.name + "-shm"),
            path.with_name(path.name + "-journal"),
            stream,
            stream.with_name(stream.name + ".tmp"),
        )

    def clear_index(self):
        with self._lock:
            self.unload_memory()
            return super().clear_index()

    close = unload_memory
