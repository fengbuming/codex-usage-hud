"""Safe local-store orchestration for permanent session deletion."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PureWindowsPath
import secrets
import shutil
import sqlite3
from threading import RLock
import time
import uuid

from ..codex_cli_launcher import discover_workdirs
from .parser import classify_session_client
from .session_search import (
    SessionSearchIndex,
    normalise_workdir,
    search_terms,
    workdir_identity,
)
from .session_materializer import (
    SessionMaterializationError,
    _first_user_message_text,
    materialize_forked_rollout,
)


DEFAULT_CONFIRMATION_TTL_SECONDS = 300.0
_ACTIVE_EDGE_STATES = {"active", "in_progress", "pending", "running", "starting"}
_SESSION_METADATA_SCAN_LIMIT = 512 * 1024
# Fork writers can take a short moment to create both the rollout and the
# state-db record.  Transfer-only retries are bounded and never run as HUD
# background polling.
_TRANSFER_READY_RETRY_DELAYS_SECONDS = (0.0, 0.1, 0.25, 0.5, 0.75, 1.25)


def _search_path_key(value: object) -> str:
    try:
        path = Path(str(value or "")).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        path = Path(str(value or ""))
    return os.path.normcase(os.path.normpath(str(path)))


class SessionCleanupError(RuntimeError):
    """Raised when a permanent-delete request cannot be proven safe."""


@dataclass(frozen=True)
class SessionDeleteCapability:
    available: bool
    reason: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "available": bool(self.available),
            "command": "local SQLite transaction" if self.available else "",
            "reason": str(self.reason or ""),
        }


@dataclass(frozen=True)
class _ThreadRecord:
    session_id: str
    rollout_path: Path | None
    title: str
    cwd: str
    archived: bool
    updated_at_ms: int


@dataclass(frozen=True)
class _SessionMetadata:
    model_provider: str = "unknown"
    client_kind: str = "unknown"


@dataclass(frozen=True)
class SessionCleanupItem:
    id: str
    title: str
    workdir_name: str
    updated_at: str
    status: str
    archived: bool
    size: int
    descendant_count: int
    selectable: bool
    blocked_reason: str
    transferable: bool = True
    transfer_blocked_reason: str = ""
    model_provider: str = "unknown"
    client_kind: str = "unknown"
    _session_id: str = field(default="", repr=False, compare=False)
    _cwd: str = field(default="", repr=False, compare=False)
    _descendant_ids: tuple[str, ...] = field(default=(), repr=False, compare=False)
    _rollout_paths: tuple[Path, ...] = field(default=(), repr=False, compare=False)

    def to_payload(self, *, pending_source_cleanup: bool = False) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "workdirName": self.workdir_name,
            # The renderer gets only an opaque key.  Absolute cwd values stay
            # behind the manager boundary, preserving the existing payload
            # privacy contract while still allowing workdir filtering.
            "workdirId": workdir_identity(self._cwd),
            "updatedAt": self.updated_at,
            "status": self.status,
            "archived": bool(self.archived),
            "bytes": max(0, int(self.size)),
            "descendantCount": max(0, int(self.descendant_count)),
            "selectable": bool(self.selectable),
            "blockedReason": self.blocked_reason,
            "transferable": bool(self.transferable),
            "transferBlockedReason": self.transfer_blocked_reason,
            "modelProvider": self.model_provider,
            "clientKind": self.client_kind,
            "pendingSourceCleanup": bool(pending_source_cleanup),
        }


@dataclass(frozen=True)
class _Confirmation:
    revision: str
    item_ids: tuple[str, ...]
    expires_at: float


UsageSnapshotPrepare = Callable[[SessionCleanupItem], object]
UsageSnapshotCommit = Callable[[object], None]
UsageSnapshotDiscard = Callable[[object], None]
DesktopSourceLifecycle = Callable[[str, str], Mapping[str, object]]
DesktopSourcePreflight = Callable[[Sequence[str], str], Mapping[str, object]]
TransferProgressCallback = Callable[[str, str, bool, str], None]
TransferBatchOperation = Callable[
    [Sequence[tuple[str, str]], TransferProgressCallback | None],
    Mapping[str, object],
]


def _canonical_uuid(value: object) -> str:
    candidate = str(value or "").strip()
    try:
        canonical = str(uuid.UUID(candidate))
    except (AttributeError, TypeError, ValueError):
        return ""
    return canonical if candidate.casefold() == canonical else ""


def _normalise_fork_target_uuid(value: object) -> str:
    """Canonicalise an App Server-generated target id before collision checks.

    Renderer-provided ids remain strict via :func:`_canonical_uuid`, but a
    successful App Server may serialize a UUID with uppercase hex.  Treat that
    trusted response as the same opaque id so it cannot evade either the
    existing-store or same-batch collision gates.
    """
    candidate = str(value or "").strip()
    try:
        return str(uuid.UUID(candidate))
    except (AttributeError, TypeError, ValueError):
        return ""


_MIGRATED_SOURCE_STORE_KEY = "migratedSourceIds"
_PENDING_CLEANUP_SOURCE_STORE_KEY = "pendingCleanupSourceIds"


def _load_migrated_source_store(path: Path | None) -> dict[str, set[str]]:
    """Read persisted transfer bookkeeping, tolerating any damage.

    Returns ``{"migrated": ..., "pendingCleanup": ...}`` where ``migrated``
    holds ids whose source deletion was confirmed and ``pendingCleanup``
    holds ids whose target copy is verified but the source is retained.
    """
    empty = {"migrated": set(), "pendingCleanup": set()}
    if path is None:
        return {
            key: set(value) for key, value in empty.items()
        }
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {key: set(value) for key, value in empty.items()}
    if not isinstance(raw, Mapping):
        return {key: set(value) for key, value in empty.items()}
    loaded: dict[str, set[str]] = {}
    for store_key, name in (
        (_MIGRATED_SOURCE_STORE_KEY, "migrated"),
        (_PENDING_CLEANUP_SOURCE_STORE_KEY, "pendingCleanup"),
    ):
        values = raw.get(store_key)
        if not isinstance(values, Sequence) or isinstance(
            values,
            (str, bytes, bytearray),
        ):
            values = []
        loaded[name] = {
            canonical
            for value in values
            if (canonical := _canonical_uuid(value))
        }
    return loaded


def _write_migrated_source_store(
    path: Path | None,
    migrated: Iterable[str],
    pending_cleanup: Iterable[str],
) -> bool:
    """Persist transfer bookkeeping atomically; best effort only.

    A failed write must never fail a transfer whose Desktop deletion already
    committed: the in-memory sets keep protecting the current HUD process and
    only restart resilience is lost.
    """
    if path is None:
        return False
    path = Path(path)
    payload = json.dumps(
        {
            _MIGRATED_SOURCE_STORE_KEY: sorted(
                canonical
                for value in migrated
                if (canonical := _canonical_uuid(value))
            ),
            _PENDING_CLEANUP_SOURCE_STORE_KEY: sorted(
                canonical
                for value in pending_cleanup
                if (canonical := _canonical_uuid(value))
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except (OSError, UnicodeError):
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def _read_only_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _read_write_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=rw", uri=True, timeout=5.0
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _normalized_rollout_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text or "\x00" in text:
        return None
    if os.name == "nt" and text.startswith("\\\\?\\"):
        text = text[4:]
    try:
        return Path(text).expanduser().resolve(strict=False)
    except OSError:
        return None


def _path_under(path: Path, roots: Sequence[Path]) -> bool:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve(strict=False))
        except (OSError, ValueError):
            continue
        return True
    return False


def _workdir_leaf(value: object) -> str:
    text = str(value or "").strip().rstrip("/\\")
    if not text:
        return ""
    if "\\" in text:
        return PureWindowsPath(text).name
    return Path(text).name


def _updated_at_iso(value: int) -> str:
    if value <= 0:
        return ""
    try:
        return (
            datetime.fromtimestamp(value / 1000.0)
            .astimezone()
            .isoformat(timespec="seconds")
        )
    except (OSError, OverflowError, ValueError):
        return ""


_BASE_URL_PROVIDER_HINTS: tuple[tuple[str, str], ...] = (
    ("api.openai.com", "openai"),
    ("openai.azure.com", "azure-openai"),
    ("api.anthropic.com", "anthropic"),
    ("generativelanguage.googleapis.com", "google"),
    ("api.deepseek.com", "deepseek"),
    ("dashscope.aliyuncs.com", "qwen"),
    ("ark.cn-beijing.volces.com", "doubao"),
    ("api.x.ai", "xai"),
    ("api.mistral.ai", "mistral"),
    ("api.cohere.com", "cohere"),
)


def _provider_from_payload(payload: Mapping[str, object]) -> str:
    """Extract a normalized provider string from any record payload.

    Checks ``model_provider`` first, then ``provider``, then a
    ``provider/model``-style ``model`` field.  Returns ``""`` when no
    provider signal is present.
    """
    for key in ("model_provider", "provider"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    model = payload.get("model")
    if isinstance(model, str) and "/" in model:
        prefix = model.split("/", 1)[0].strip().lower()
        if prefix:
            return prefix
    return ""


def _session_metadata(path: Path | None) -> _SessionMetadata:
    """Read only the session metadata record needed for inventory filters.

    When the canonical ``session_meta`` record is absent, a bounded prefix is
    enough for the inventory fast path. Provider-scoped deletion performs its
    own targeted deep scan for the remaining ``unknown`` cases.
    """
    if path is None:
        return _SessionMetadata()
    fallback_provider = ""
    fallback_originator: object = None
    fallback_source: object = None
    scanned = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if scanned >= _SESSION_METADATA_SCAN_LIMIT:
                    break
                scanned += len(line)
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, Mapping):
                    continue
                payload = record.get("payload")
                if isinstance(payload, Mapping):
                    if not fallback_provider:
                        fallback_provider = _provider_from_payload(payload)
                    if fallback_originator is None and "originator" in payload:
                        fallback_originator = payload.get("originator")
                    if fallback_source is None and "source" in payload:
                        fallback_source = payload.get("source")
                if record.get("type") != "session_meta":
                    continue
                if not isinstance(payload, Mapping):
                    return _SessionMetadata(
                        model_provider=fallback_provider or "unknown",
                        client_kind=classify_session_client(
                            fallback_originator, fallback_source
                        ),
                    )
                explicit_provider = (
                    str(payload.get("model_provider") or "").strip().lower()
                )
                return _SessionMetadata(
                    model_provider=explicit_provider
                    or fallback_provider
                    or "unknown",
                    client_kind=classify_session_client(
                        payload.get("originator") or fallback_originator,
                        payload.get("source")
                        if "source" in payload
                        else fallback_source,
                    ),
                )
    except (OSError, UnicodeError):
        pass
    return _SessionMetadata(
        model_provider=fallback_provider or "unknown",
        client_kind=classify_session_client(fallback_originator, fallback_source),
    )


def _missing_paginated_history_source(
    path: Path | None,
    session_id: str,
    records: Mapping[str, _ThreadRecord],
    allowed_roots: Sequence[Path],
) -> bool:
    """Return whether a paginated rollout points at an unavailable source."""
    if path is None:
        return False
    try:
        with path.open("r", encoding="utf-8") as handle:
            first_line = handle.readline()
        record = json.loads(first_line)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(record, Mapping):
        return False
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        return False
    if str(payload.get("history_mode") or "").strip().casefold() != "paginated":
        return False
    history_base = payload.get("history_base")
    # A paginated root starts a new history and legitimately has no base.
    if history_base is None:
        return False
    base_id = (
        _canonical_uuid(history_base.get("thread_id"))
        if isinstance(history_base, Mapping)
        else ""
    )
    if not base_id or base_id == session_id:
        return True
    source = records.get(base_id)
    source_path = source.rollout_path if source is not None else None
    return (
        source_path is None
        or not _path_under(source_path, allowed_roots)
        or not source_path.is_file()
    )


class SessionCleanupManager:
    """Build a root-session inventory and delete verified local session trees."""

    def __init__(
        self,
        *,
        state_db_path: Path,
        sessions_root: Path,
        session_index_path: Path,
        thread_history_db_path: Path | None = None,
        current_session_ids: Callable[[], Iterable[str]] | None = None,
        active_session_ids: Callable[[], Iterable[str]] | None = None,
        usage_snapshot_prepare: UsageSnapshotPrepare | None = None,
        usage_snapshot_commit: UsageSnapshotCommit | None = None,
        usage_snapshot_discard: UsageSnapshotDiscard | None = None,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] | None = None,
        confirmation_ttl_seconds: float = DEFAULT_CONFIRMATION_TTL_SECONDS,
        transfer_ready_retry_delays: Sequence[
            float
        ] = _TRANSFER_READY_RETRY_DELAYS_SECONDS,
        transfer_max_workers: int = 1,
        sleep: Callable[[float], None] = time.sleep,
        migrated_source_store: Path | None = None,
        search_index_path: Path | None = None,
    ) -> None:
        self.state_db_path = Path(state_db_path)
        self.sessions_root = Path(sessions_root)
        self.session_index_path = Path(session_index_path)
        self.thread_history_db_path = (
            Path(thread_history_db_path)
            if thread_history_db_path is not None
            else None
        )
        self.current_session_ids = current_session_ids or (lambda: ())
        self.active_session_ids = active_session_ids or (lambda: ())
        self.usage_snapshot_prepare = usage_snapshot_prepare
        self.usage_snapshot_commit = usage_snapshot_commit
        self.usage_snapshot_discard = usage_snapshot_discard
        self.clock = clock
        self.token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self.confirmation_ttl_seconds = max(1.0, float(confirmation_ttl_seconds))
        self.transfer_ready_retry_delays = tuple(
            max(0.0, float(delay)) for delay in transfer_ready_retry_delays
        ) or (0.0,)
        try:
            configured_workers = int(transfer_max_workers)
        except (TypeError, ValueError):
            configured_workers = 1
        self.transfer_max_workers = max(1, min(8, configured_workers))
        self._sleep = sleep
        self._revision_counter = 0
        self._revision = ""
        self._generated_at = 0.0
        self._capability = SessionDeleteCapability(False, "Not scanned yet.")
        self._items: dict[str, SessionCleanupItem] = {}
        self._search_state_lock = RLock()
        self._search_index = SessionSearchIndex(
            search_index_path
            if search_index_path is not None
            else self.sessions_root.parent / "session-search.sqlite"
        )
        self._search_state: dict[str, object] = {
            "query": "",
            "workdirId": "",
            "state": "idle",
            "matches": [],
            "matchKinds": [],
            "indexed": 0,
            "indexTotal": 0,
            "indexState": "idle",
            "generation": 0,
            "indexAvailable": True,
            "requestId": "",
            "revision": "",
            "error": "",
        }
        self._confirmations: dict[str, _Confirmation] = {}
        self._operation: dict[str, object] = self._idle_operation()
        self._unresolved_count = 0
        # Real (thread) ids whose sources were migrated away in a completed
        # transfer.  Excluded from every inventory so a dialog reopen or a
        # fresh scan cannot resurrect a session that already belongs to
        # another provider. Keyed by canonical thread id (stable across
        # scans and opaque-id regeneration), never by the ephemeral item id.
        # Desktop's state-db deletion can lag behind its confirmed delete
        # notification, so both sets are also persisted across HUD restarts.
        # ``_pending_cleanup_source_ids`` records sources whose target copy
        # is verified but whose deletion is not confirmed yet; they stay in
        # the inventory (still deletable) but are flagged so the transfer
        # dialog cannot offer a duplicate copy.
        self._migrated_source_store = (
            Path(migrated_source_store)
            if migrated_source_store is not None
            else None
        )
        _stored_sources = _load_migrated_source_store(self._migrated_source_store)
        self._migrated_source_ids: set[str] = _stored_sources["migrated"]
        self._pending_cleanup_source_ids: set[str] = _stored_sources["pendingCleanup"]

    @staticmethod
    def _idle_operation() -> dict[str, object]:
        return {
            "id": "",
            "requestId": "",
            "action": "idle",
            "state": "idle",
            "progress": 0,
            "error": "",
        }

    def snapshot(self, *, include_sessions: bool = True) -> dict[str, object]:
        with self._search_state_lock:
            sessions = sorted(
                (
                    item
                    for item in self._items.values()
                    if item._session_id not in self._migrated_source_ids
                ),
                key=lambda item: (item.updated_at, item.title.casefold()),
                reverse=True,
            )
            selectable = [item for item in sessions if item.selectable]
            payload: dict[str, object] = {
                "revision": self._revision,
                "generatedAt": _updated_at_iso(int(self._generated_at * 1000)),
                "capability": self._capability.to_payload(),
                "totals": {
                    "sessions": len(sessions),
                    "selectable": len(selectable),
                    "blocked": len(sessions) - len(selectable),
                    "unresolved": self._unresolved_count,
                    "bytes": sum(item.size for item in sessions),
                    "descendants": sum(item.descendant_count for item in sessions),
                },
                "operation": dict(self._operation),
                "search": dict(self._search_state),
            }
            if include_sessions:
                payload["sessions"] = [
                    item.to_payload(
                        pending_source_cleanup=(
                            item._session_id in self._pending_cleanup_source_ids
                        ),
                    )
                    for item in sessions
                ]
                payload["workdirs"] = self.workdir_options()
            return payload

    def workdir_options(self, *, include_paths: bool = False) -> list[dict[str, object]]:
        """Return renderer-safe workdir choices with opaque identities.

        The labels/order follow the same local-project discovery used by the
        CLI launcher. Historical cwd values from the scanned inventory are
        merged in as well, including directories that no longer exist. Full
        paths remain private to the manager and are never serialized here.
        """

        discovered = discover_workdirs(
            sessions_root=self.sessions_root,
            current_workdir=Path.cwd(),
        )
        values: dict[str, dict[str, object]] = {}
        order: list[str] = []

        def add(raw_path: object, label: object = "", source: object = "") -> None:
            path = str(raw_path or "").strip()
            identity = workdir_identity(path)
            if not identity:
                return
            normalised = normalise_workdir(path)
            key = normalised.casefold()
            if key in values:
                return
            try:
                path_obj = Path(path)
                available = path_obj.is_dir()
            except (OSError, ValueError):
                path_obj = Path(".")
                available = False
            entry: dict[str, object] = {
                "id": identity,
                "label": str(label or path_obj.name or path).strip(),
                "available": available,
                "source": str(source or "Codex Desktop"),
                "sessionCount": 0,
            }
            if include_paths:
                entry["path"] = path
            values[key] = entry
            order.append(key)

        for entry in discovered:
            if isinstance(entry, Mapping):
                add(entry.get("path"), entry.get("label"), entry.get("source"))
        for item in self._items.values():
            raw_path = str(item._cwd or "").strip()
            if not raw_path:
                continue
            add(raw_path, item.workdir_name, "session history")
        for item in self._items.values():
            identity = workdir_identity(item._cwd)
            if not identity:
                continue
            entry = values.get(normalise_workdir(item._cwd).casefold())
            if entry is not None:
                entry["sessionCount"] = int(entry.get("sessionCount") or 0) + 1
        options = [values[key] for key in order]
        options.sort(key=lambda item: (str(item.get("label") or "").casefold(), str(item.get("id") or "")))
        return options

    def workdir_options_detailed(self) -> list[dict[str, object]]:
        """Return CLI-style labels plus paths for the local settings command.

        This explicit command response is the only path-bearing workdir
        surface. The normal session inventory remains opaque for privacy and
        transfer/delete payload compatibility.
        """

        return self.workdir_options(include_paths=True)

    def search_index_entries(
        self,
        changed_paths: Iterable[Path] = (),
    ) -> tuple[tuple[str, tuple[Path, ...], str, str, str, str], ...]:
        """Return current index work without exposing session identifiers."""

        changed = {
            _search_path_key(path)
            for path in changed_paths
            if str(path or "").strip()
        }
        with self._search_state_lock:
            entries: list[tuple[str, tuple[Path, ...], str, str, str, str]] = []
            for item in self._items.values():
                if item._session_id in self._migrated_source_ids:
                    continue
                if changed and not any(
                    _search_path_key(path) in changed for path in item._rollout_paths
                ):
                    continue
                entries.append(
                    (
                        item._session_id,
                        item._rollout_paths,
                        item.title,
                        item._cwd,
                        item.model_provider,
                        item.client_kind,
                    )
                )
            return tuple(entries)

    def begin_search_index(self, revision: str, total: int) -> dict[str, object]:
        """Mark an event-triggered index generation as running."""

        with self._search_state_lock:
            if str(revision or "") != self._revision:
                return self.snapshot(include_sessions=False)
            self._search_state = {
                **self._search_state,
                "state": "indexing",
                "indexState": "indexing",
                "processed": 0,
                "indexed": max(0, int(self._search_state.get("indexed") or 0)),
                "indexTotal": max(0, int(total)),
                "generation": int(self._search_state.get("generation") or 0) + 1,
                "indexAvailable": True,
                "error": "",
                "requestId": "",
                "revision": self._revision,
            }
            return self.snapshot(include_sessions=False)

    def _visible_search_items(
        self,
        workdir_id: str,
    ) -> list[SessionCleanupItem]:
        selected = str(workdir_id or "").strip()
        return [
            item
            for item in self._items.values()
            if item._session_id not in self._migrated_source_ids
            and (
                not selected
                or workdir_identity(item._cwd) == selected
            )
        ]

    @staticmethod
    def _metadata_matches(item: SessionCleanupItem, query: str) -> bool:
        terms = search_terms(query)
        if not terms:
            return True
        haystack = " ".join(
            (
                item.title,
                item.workdir_name,
                item._cwd,
                item.model_provider,
                item.client_kind,
            )
        ).casefold().replace("\\", "/")
        return all(term in haystack for term in terms)

    def _search_matches_locked(
        self,
        query: str,
        workdir_id: str,
    ) -> tuple[list[str], list[dict[str, object]], dict[str, object]]:
        visible_items = self._visible_search_items(workdir_id)
        by_session = {item._session_id: item for item in visible_items}
        result = self._search_index.search(
            query,
            session_ids=by_session,
            load=False,
        )
        details: dict[str, dict[str, object]] = {}
        order: list[str] = []
        raw_matches = result.get("matches") if isinstance(result, Mapping) else []
        if isinstance(raw_matches, Sequence) and not isinstance(
            raw_matches, (str, bytes, bytearray)
        ):
            for raw in raw_matches:
                if not isinstance(raw, Mapping):
                    continue
                item = by_session.get(str(raw.get("sessionId") or ""))
                if item is None:
                    continue
                item_id = item.id
                if item_id not in details:
                    order.append(item_id)
                    details[item_id] = {
                        "id": item_id,
                        "kinds": [
                            str(kind)
                            for kind in raw.get("kinds", ())
                            if str(kind or "").strip()
                        ]
                        if isinstance(raw.get("kinds"), Sequence)
                        and not isinstance(raw.get("kinds"), (str, bytes, bytearray))
                        else [],
                        "score": float(raw.get("score") or 0),
                        "exactPhrase": bool(raw.get("exact_phrase") or False),
                    }
        if query.strip():
            for item in visible_items:
                if not self._metadata_matches(item, query):
                    continue
                item_id = item.id
                if item_id not in details:
                    order.append(item_id)
                    details[item_id] = {
                        "id": item_id,
                        "kinds": ["metadata"],
                        "score": 3.0,
                    }
                elif "metadata" not in details[item_id]["kinds"]:
                    details[item_id]["kinds"].append("metadata")
        else:
            order = [item.id for item in visible_items]
            details = {}
        if details:
            order.sort(
                key=lambda item_id: (
                    0 if details[item_id].get("exactPhrase") else 1,
                    -float(details[item_id].get("score") or 0),
                    item_id,
                )
            )
        return (
            order,
            [details[item_id] for item_id in order if item_id in details],
            result,
        )

    def update_search_index(
        self,
        revision: str,
        processed: int,
        total: int,
        indexed: int,
        *,
        state: str = "indexing",
        error: str = "",
        generation: int | None = None,
    ) -> dict[str, object]:
        """Publish resident-index progress and refresh the active query."""

        with self._search_state_lock:
            if str(revision or "") != self._revision:
                return self.snapshot(include_sessions=False)
            if (
                generation is not None
                and int(generation) != int(self._search_state.get("generation") or 0)
            ):
                return self.snapshot(include_sessions=False)
            query = str(self._search_state.get("query") or "")
            workdir_id = str(self._search_state.get("workdirId") or "")
            matches, match_kinds, result = self._search_matches_locked(
                query,
                workdir_id,
            )
            index_state = str(state or "indexing")
            self._search_state = {
                **self._search_state,
                "query": query,
                "workdirId": workdir_id,
                "state": "indexing" if index_state == "indexing" else "completed",
                "indexState": index_state,
                "processed": max(0, int(processed)),
                "indexTotal": max(0, int(total)),
                "indexed": max(0, int(indexed)),
                "matches": matches,
                "matchKinds": match_kinds,
                "indexAvailable": bool(result.get("indexAvailable", True))
                and index_state != "failed",
                "error": str(error or ""),
                "requestId": "",
                "revision": self._revision,
            }
            return self.snapshot(include_sessions=False)

    def reset_search_index_state(self) -> None:
        """Forget cached search results after the resident index is cleared."""
        with self._search_state_lock:
            self._search_state = {
                **self._search_state,
                "state": "idle",
                "processed": 0,
                "indexTotal": 0,
                "indexState": "idle",
                "indexed": 0,
                "matches": [],
                "matchKinds": [],
                "indexAvailable": True,
                "requestId": "",
                "revision": self._revision,
                "generation": int(self._search_state.get("generation") or 0) + 1,
                "error": "",
            }

    def search(
        self,
        query: str,
        *,
        workdir_id: str = "",
        request_id: str = "",
        include_sessions: bool = True,
    ) -> dict[str, object]:
        """Search only the resident index and current inventory metadata."""

        text = str(query or "").strip()
        selected_workdir = str(workdir_id or "").strip()
        with self._search_state_lock:
            try:
                matches, match_kinds, result = self._search_matches_locked(
                    text,
                    selected_workdir,
                )
                index_state = str(self._search_state.get("indexState") or "idle")
                indexed = int(result.get("indexed") or 0)
                if (
                    index_state == "indexing"
                    and indexed > 0
                    and indexed >= int(self._search_state.get("indexTotal") or 0)
                ):
                    index_state = "ready"
                self._search_state = {
                    **self._search_state,
                    "query": text,
                    "workdirId": selected_workdir,
                    "state": "completed",
                    "indexState": index_state,
                    "matches": matches,
                    "matchKinds": match_kinds,
                    "searchTokens": list(result.get("tokens") or []),
                    "indexed": indexed,
                    "indexAvailable": bool(result.get("indexAvailable", True)),
                    "requestId": str(request_id or ""),
                    "revision": self._revision,
                    "error": "",
                }
                self._operation = {
                    "id": str(request_id or self.token_factory()),
                    "requestId": str(request_id or ""),
                    "action": "search",
                    "state": "completed",
                    "progress": 100,
                    "error": "",
                    "query": text,
                    "workdirId": selected_workdir,
                    "matchedCount": len(matches),
                }
            except Exception as exc:
                self._search_state = {
                    **self._search_state,
                    "query": text,
                    "workdirId": selected_workdir,
                    "state": "failed",
                    "indexState": "failed",
                    "matches": [],
                    "matchKinds": [],
                    "indexed": 0,
                    "indexAvailable": False,
                    "requestId": str(request_id or ""),
                    "revision": self._revision,
                    "error": type(exc).__name__,
                }
                self._operation = {
                    "id": str(request_id or self.token_factory()),
                    "requestId": str(request_id or ""),
                    "action": "search",
                    "state": "failed",
                    "progress": 100,
                    "error": type(exc).__name__,
                    "query": text,
                    "workdirId": selected_workdir,
                }
        return self.snapshot(include_sessions=bool(include_sessions))

    def mark_migrated_sources(self, session_ids: Iterable[str]) -> None:
        """Record sources that were successfully migrated to another provider.

        These thread ids are excluded from every subsequent inventory so a
        dialog reopen or fresh scan cannot resurrect sessions that now belong
        to a different provider.  Only committed deletions (the Desktop
        lifecycle confirmed archive/delete) are recorded.
        """
        for session_id in session_ids:
            canonical = _canonical_uuid(session_id)
            if canonical:
                self._migrated_source_ids.add(canonical)
                self._pending_cleanup_source_ids.discard(canonical)
        # Persist so a HUD restart still hides sources whose Desktop state-db
        # deletion lags behind its confirmed delete notification.
        self._persist_source_transfer_store()

    def mark_pending_cleanup_sources(self, session_ids: Iterable[str]) -> None:
        """Record sources with a verified target copy but a retained source.

        The transfer is usable from the target provider, yet the source
        remains locally.  These stay in the inventory (they are still real,
        deletable sessions) but carry ``pendingSourceCleanup`` so the
        transfer dialog will not offer a duplicate copy while the Desktop
        deletion is unresolved.
        """
        changed = False
        for session_id in session_ids:
            canonical = _canonical_uuid(session_id)
            if canonical and canonical not in self._pending_cleanup_source_ids:
                self._pending_cleanup_source_ids.add(canonical)
                changed = True
        if changed:
            self._persist_source_transfer_store()

    def _persist_source_transfer_store(self) -> None:
        _write_migrated_source_store(
            self._migrated_source_store,
            self._migrated_source_ids,
            self._pending_cleanup_source_ids,
        )

    def _prune_migrated_source_ids(
        self,
        records: Mapping[str, "_ThreadRecord"],
    ) -> None:
        """Drop persisted bookkeeping ids whose state rows are finally gone.

        Once Desktop's deletion has landed in the state DB the id can no
        longer resurrect from a scan, so pruning only keeps the persisted
        store bounded across long-lived installations.
        """
        changed = False
        for attribute in ("_migrated_source_ids", "_pending_cleanup_source_ids"):
            current = getattr(self, attribute)
            if not current:
                continue
            lingering = {
                session_id for session_id in current if session_id in records
            }
            if len(lingering) != len(current):
                setattr(self, attribute, lingering)
                changed = True
        if changed:
            self._persist_source_transfer_store()

    def workdir_for_item(self, item_id: object, revision: object) -> Path | None:
        """Resolve one current inventory item without exposing its path in the payload."""
        normalized_id = str(item_id or "").strip()
        normalized_revision = str(revision or "").strip()
        if (
            not normalized_id
            or not normalized_revision
            or normalized_revision != self._revision
        ):
            return None
        item = self._items.get(normalized_id)
        if item is None or not item._session_id:
            return None
        records, _parents, _edge_states, _unsafe_ids, _unresolved = self._load_state()
        # A completed scan can replace the opaque inventory while the state DB is read.
        if (
            normalized_revision != self._revision
            or self._items.get(normalized_id) is not item
        ):
            return None
        record = records.get(item._session_id)
        raw_path = str(record.cwd or "").strip() if record is not None else ""
        if not raw_path or "\x00" in raw_path:
            return None
        try:
            path = Path(raw_path)
            return path if path.is_absolute() and path.is_dir() else None
        except OSError:
            return None

    def session_route_target_for_item(
        self, item_id: object, revision: object
    ) -> dict[str, str] | None:
        """Resolve one inventory item to its Desktop route target.

        Mirrors :meth:`workdir_for_item`: the renderer only holds an opaque id,
        so the real thread id, host kind and working directory are re-read here
        behind the manager boundary instead of being published in the payload.
        """
        normalized_id = str(item_id or "").strip()
        normalized_revision = str(revision or "").strip()
        if (
            not normalized_id
            or not normalized_revision
            or normalized_revision != self._revision
        ):
            return None
        item = self._items.get(normalized_id)
        if item is None or not item._session_id:
            return None
        if (
            normalized_revision != self._revision
            or self._items.get(normalized_id) is not item
        ):
            return None
        return {
            "sessionId": str(item._session_id),
            "clientKind": str(item.client_kind or "unknown"),
            "modelProvider": str(item.model_provider or "unknown"),
            "cwd": str(item._cwd or ""),
        }

    def thread_find_for_item(
        self, item_id: object, revision: object, query: str
    ) -> dict[str, object]:
        """Payload for driving Codex's native in-thread find after a jump.

        Highlighting, scrolling and prev/next navigation are owned by the
        native find bar; the HUD only needs the query plus a target key so the
        renderer can confirm the landed session before opening it.  The payload
        also ships the ranked match list and query tokens so the in-session
        search float can list results, surface token chips, and walk prev/next.
        """
        import hashlib

        target = self.session_route_target_for_item(item_id, revision)
        item = self._items.get(str(item_id or "").strip())
        if target is None or item is None:
            return {"query": query, "error": "inventory-changed"}
        if str(revision) != self._revision or self._items.get(str(item_id)) is not item:
            return {"query": query, "error": "inventory-changed"}
        titles = self._session_index_titles()
        ordered_ids = [str(value) for value in self._search_state.get("matches") or []]
        kind_map = {
            str(entry.get("id")): entry
            for entry in (self._search_state.get("matchKinds") or [])
            if isinstance(entry, Mapping)
        }
        ranked: list[dict[str, object]] = []
        for match_id in ordered_ids:
            entry = kind_map.get(match_id) or {}
            match_item = self._items.get(match_id)
            ranked.append(
                {
                    "id": match_id,
                    "title": (
                        titles.get(match_item._session_id)
                        if match_item is not None
                        else (entry.get("title") or match_id)
                    ),
                    "kinds": list(entry.get("kinds") or []),
                    "score": float(entry.get("score") or 0),
                    "exactPhrase": bool(entry.get("exactPhrase") or False),
                }
            )
        return {
            "query": query,
            "targetKey": hashlib.sha256(item._session_id.encode()).hexdigest(),
            "tokens": list(search_terms(query)),
            "revision": self._revision,
            "matches": ranked,
        }

    def workdir_for_transfer_target(
        self,
        session_id: object,
        target_provider: object,
    ) -> Path | None:
        """Resolve a verified transfer target to an existing launch directory.

        The renderer only receives the opaque target id.  Re-read its current
        local rollout and metadata before launching ``codex resume`` so a stale
        result card cannot resume an unrelated Provider or arbitrary path.
        """
        canonical = _canonical_uuid(session_id)
        provider = str(target_provider or "").strip().casefold()
        if not canonical or not provider:
            return None
        records, _parents, _edge_states, _unsafe_ids, _unresolved = self._load_state()
        record = records.get(canonical)
        if (
            record is None
            or record.rollout_path is None
            or not _path_under(record.rollout_path, self._allowed_rollout_roots())
            or not record.rollout_path.is_file()
            or _session_metadata(record.rollout_path).model_provider.casefold()
            != provider
        ):
            return None
        raw_path = str(record.cwd or "").strip()
        if not raw_path or "\x00" in raw_path:
            return None
        try:
            path = Path(raw_path)
            return path if path.is_absolute() and path.is_dir() else None
        except OSError:
            return None

    def mark_operation(
        self,
        *,
        request_id: str,
        action: str,
        state: str,
        progress: int = 0,
        error: str = "",
        include_sessions: bool = True,
        **values: object,
    ) -> dict[str, object]:
        operation = {
            "id": str(values.pop("id", "") or request_id or self.token_factory()),
            "requestId": str(request_id or ""),
            "action": str(action or ""),
            "state": str(state or ""),
            "progress": max(0, min(100, int(progress))),
            "error": str(error or ""),
        }
        operation.update(values)
        self._operation = operation
        return self.snapshot(include_sessions=bool(include_sessions))

    def cancel(self, *, request_id: str = "") -> dict[str, object]:
        self._confirmations.clear()
        return self.mark_operation(
            request_id=request_id,
            action="cancel",
            state="cancelled",
            progress=100,
        )

    def probe_capability(self) -> SessionDeleteCapability:
        if not self.state_db_path.is_file():
            return SessionDeleteCapability(
                False,
                "Codex local session store is unavailable.",
            )
        try:
            with closing(_read_write_connection(self.state_db_path)) as connection:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(threads)")
                }
        except (OSError, sqlite3.Error) as exc:
            return SessionDeleteCapability(
                False,
                f"Codex local session store could not be opened ({type(exc).__name__}).",
            )
        if "threads" not in tables or not {"id", "rollout_path"}.issubset(columns):
            return SessionDeleteCapability(
                False,
                "Codex local session store schema is not recognized.",
            )
        return SessionDeleteCapability(True)

    def scan(self, *, request_id: str = "") -> dict[str, object]:
        request = str(request_id or "")
        publisher = getattr(self, "progress_publisher", None)
        # Recover disk space from deletions interrupted by crash or power loss.
        # Staging directories older than the current process are always safe to
        # remove: an in-progress deletion uses a fresh random subdirectory and
        # commits the database before rmtree, so any leftover staging belongs
        # to a previous attempt.
        self._cleanup_orphaned_staging()

        def report(
            phase: str, phase_label: str, phase_index: int, progress: int
        ) -> None:
            self.mark_operation(
                request_id=request,
                action="scan",
                state="scanning",
                progress=min(99, max(0, int(progress))),
                phase=phase,
                phaseLabel=phase_label,
                phaseIndex=phase_index,
                phaseCount=3,
            )
            if callable(publisher):
                publisher(self.snapshot(include_sessions=False))

        report("sessions", "Reading session index", 1, 10)
        capability = self.probe_capability()
        records, parents, edge_states, unsafe_ids, unresolved = self._load_state()
        self._prune_migrated_source_ids(records)
        report("merge", "Merging root sessions and subagents", 2, 55)
        # State rows normally carry the visible title. Avoid rereading the
        # legacy session index on every scan; consult it only for missing titles.
        titles = (
            self._session_index_titles()
            if any(not record.title for record in records.values())
            else {}
        )
        current_ids = self._protected_ids(self.current_session_ids)
        active_ids = self._protected_ids(self.active_session_ids)
        roots, unsafe_roots, unresolved_roots = self._root_ids(
            records,
            parents,
            unsafe_ids,
        )
        unresolved += unresolved_roots
        allowed_roots = self._allowed_rollout_roots()
        report("protect", "Checking deletion protection", 3, 60)
        items: list[SessionCleanupItem] = []
        total_roots = len(roots)
        progress_interval = max(1, total_roots // 32)
        for root_index, root_id in enumerate(roots, 1):
            descendants = self._descendants(root_id, records, parents)
            family = (root_id, *descendants)
            if root_id in self._migrated_source_ids or any(
                session_id in self._migrated_source_ids for session_id in descendants
            ):
                # Sources successfully migrated to another provider must never
                # re-enter the transferable inventory, even if the Desktop
                # lifecycle deletion lags behind the fork (or a scan races a
                # deleted state row).
                continue
            family_records = [records[session_id] for session_id in family]
            rollout_paths = tuple(
                record.rollout_path
                for record in family_records
                if record.rollout_path is not None
            )
            missing_rollout = any(
                record.rollout_path is None
                or not _path_under(record.rollout_path, allowed_roots)
                or not record.rollout_path.is_file()
                for record in family_records
            )
            missing_history_source = any(
                _missing_paginated_history_source(
                    record.rollout_path,
                    record.session_id,
                    records,
                    allowed_roots,
                )
                for record in family_records
            )
            current = bool(set(family) & current_ids)
            active = bool(set(family) & active_ids) or any(
                edge_states.get(session_id, "").casefold() in _ACTIVE_EDGE_STATES
                for session_id in descendants
            )
            blocked_reason = ""
            status = "archived" if records[root_id].archived else "idle"
            if not capability.available:
                blocked_reason = capability.reason
                status = "unavailable"
            elif current:
                blocked_reason = "The current session cannot be permanently deleted."
                status = "current"
            elif active:
                blocked_reason = "This session tree still has active work."
                status = "running"
            elif root_id in unsafe_roots:
                blocked_reason = "The session spawn relation could not be verified."
                status = "unresolved"
            elif missing_rollout:
                blocked_reason = "The session rollout mapping could not be verified."
                status = "unresolved"
            root = records[root_id]
            metadata = _session_metadata(root.rollout_path)
            size = 0
            for path in rollout_paths:
                try:
                    size += max(0, int(path.stat().st_size))
                except OSError:
                    pass
            items.append(
                SessionCleanupItem(
                    id=f"session-{self.token_factory()}",
                    title=(
                        root.title or titles.get(root_id) or "Untitled session"
                    ).strip(),
                    workdir_name=_workdir_leaf(root.cwd),
                    updated_at=_updated_at_iso(
                        max(record.updated_at_ms for record in family_records)
                    ),
                    status=status,
                    archived=root.archived,
                    size=size,
                    descendant_count=len(descendants),
                    selectable=not blocked_reason,
                    blocked_reason=blocked_reason,
                    transferable=not missing_history_source,
                    transfer_blocked_reason=(
                        "The paginated history source rollout could not be verified."
                        if missing_history_source
                        else ""
                    ),
                    model_provider=metadata.model_provider,
                    client_kind=metadata.client_kind,
                    _session_id=root_id,
                    _cwd=root.cwd,
                    _descendant_ids=tuple(descendants),
                    _rollout_paths=rollout_paths,
                )
            )
            if root_index == total_roots or root_index % progress_interval == 0:
                report(
                    "protect",
                    "Checking deletion protection",
                    3,
                    60 + round(39 * root_index / max(1, total_roots)),
                )
        self._revision_counter += 1
        self._revision = f"{self._revision_counter}-{self.token_factory()}"
        self._generated_at = float(self.clock())
        self._capability = capability
        self._items = {item.id: item for item in items}
        previous_query = str(self._search_state.get("query") or "")
        previous_workdir_id = str(self._search_state.get("workdirId") or "")
        self._search_state = {
            "query": previous_query,
            "workdirId": previous_workdir_id,
            "state": "idle",
            "indexState": "stale",
            "processed": 0,
            "indexTotal": len(items),
            "indexed": 0,
            "generation": int(self._search_state.get("generation") or 0) + 1,
            "matches": [],
            "matchKinds": [],
            "indexAvailable": True,
            "requestId": "",
            "revision": self._revision,
            "error": "",
        }
        self._unresolved_count = unresolved
        self._confirmations.clear()
        self._operation = {
            "id": str(request_id or self.token_factory()),
            "requestId": request_id,
            "action": "scan",
            "state": "completed",
            "progress": 100,
            "error": "",
        }
        return self.snapshot()

    def _deep_provider_for_item(self, item: SessionCleanupItem) -> str:
        """Thoroughly scan a session root rollout for provider signals.

        Used only when the inventory metadata reports ``unknown``.  This reads
        the full root rollout and checks nested structures (``info`` objects,
        ``base_url``) that the lightweight inventory scan intentionally skips.
        Returns the normalized provider or ``"unknown"`` when no signal is
        found — never raises, because a corrupt rollout must not block
        provider-scoped deletion of other sessions.
        """
        root_path = item._rollout_paths[0] if item._rollout_paths else None
        if root_path is None:
            return "unknown"
        try:
            with root_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, Mapping):
                        continue
                    payload = record.get("payload")
                    if isinstance(payload, Mapping):
                        provider = _provider_from_payload(payload)
                        if provider:
                            return provider
                        # token_count events nest provider under info.
                        info = payload.get("info")
                        if isinstance(info, Mapping):
                            provider = _provider_from_payload(info)
                            if provider:
                                return provider
                        # session_meta base_url can identify the provider.
                        if record.get("type") == "session_meta":
                            base_url = str(payload.get("base_url") or "").strip().lower()
                            if base_url:
                                for known, name in _BASE_URL_PROVIDER_HINTS:
                                    if known in base_url:
                                        return name
        except (OSError, UnicodeError):
            pass
        return "unknown"

    def delete_provider_history(
        self,
        provider: str,
        *,
        request_id: str = "",
    ) -> dict[str, object]:
        """Delete every safe session tree whose root belongs to one provider.

        The provider-delete dialog is already an explicit user confirmation, so
        this method performs the same scan/preview/execute safety gates as the
        session-management screen without exposing session paths to the
        renderer.
        """
        normalized_provider = str(provider or "").strip().casefold()
        if not normalized_provider:
            raise SessionCleanupError("Provider history deletion requires a provider.")
        inventory = self.scan(request_id=request_id)
        capability = inventory.get("capability")
        if not isinstance(capability, Mapping) or not bool(capability.get("available")):
            reason = str(
                capability.get("reason")
                if isinstance(capability, Mapping)
                else "Codex local session store is unavailable."
            )
            raise SessionCleanupError(
                f"无法删除 Provider {normalized_provider} 的会话历史：{reason}"
            )
        matching: list[SessionCleanupItem] = []
        unresolved_unknown = 0
        for item in self._items.values():
            if item.model_provider.casefold() == normalized_provider:
                matching.append(item)
            elif item.model_provider.casefold() == "unknown":
                # Sessions whose rollout has no session_meta (or no provider
                # field anywhere) cannot be matched by the inventory filter.
                # Run a targeted deep scan against the target provider so a
                # legacy/truncated rollout is not silently left behind.
                deep_provider = self._deep_provider_for_item(item)
                if deep_provider == normalized_provider:
                    matching.append(item)
                elif deep_provider == "unknown":
                    unresolved_unknown += 1
        blocked = [item for item in matching if not item.selectable]
        if blocked:
            reasons = sorted(
                {
                    str(item.blocked_reason or "").strip()
                    for item in blocked
                    if str(item.blocked_reason or "").strip()
                }
            )
            detail = "；".join(reasons[:2]) or "存在受保护的会话"
            raise SessionCleanupError(
                f"Provider {normalized_provider} 仍有 {len(blocked)} 个受保护会话，未执行历史删除：{detail}"
            )
        if not matching:
            return self.mark_operation(
                request_id=request_id,
                action="providerHistoryDelete",
                state="completed",
                progress=100,
                provider=normalized_provider,
                selectedIds=[],
                sessionCount=0,
                deletedCount=0,
                failedCount=0,
                unresolvedUnknownCount=unresolved_unknown,
            )
        item_ids = [item.id for item in matching]
        preview = self.preview(
            item_ids,
            self._revision,
            request_id=request_id,
        )
        operation = preview.get("operation") if isinstance(preview, Mapping) else {}
        confirmation_token = str(
            operation.get("confirmationToken") if isinstance(operation, Mapping) else ""
        )
        if not confirmation_token:
            raise SessionCleanupError("Provider 会话删除确认令牌生成失败。")
        result = self.execute(
            item_ids,
            self._revision,
            confirmation_token,
            request_id=request_id,
        )
        executed = result.get("operation") if isinstance(result, Mapping) else {}
        deleted_count = int(
            executed.get("deletedCount") or 0 if isinstance(executed, Mapping) else 0
        )
        actual_bytes = int(
            executed.get("actualBytes") or 0 if isinstance(executed, Mapping) else 0
        )
        if (
            not isinstance(executed, Mapping)
            or str(executed.get("state") or "") != "completed"
            or deleted_count != len(matching)
        ):
            raise SessionCleanupError(
                f"Provider {normalized_provider} 会话历史删除未完成："
                f"成功 {deleted_count}/{len(matching)}。"
            )
        return self.mark_operation(
            request_id=request_id,
            action="providerHistoryDelete",
            state="completed",
            progress=100,
            provider=normalized_provider,
            selectedIds=item_ids,
            sessionCount=len(matching),
            deletedCount=deleted_count,
            failedCount=0,
            actualBytes=actual_bytes,
            unresolvedUnknownCount=unresolved_unknown,
        )

    def materialize_target_rollouts(
        self,
        targets: Sequence[tuple[str, str]],
        progress_callback: TransferProgressCallback | None = None,
    ) -> dict[str, object]:
        """Flatten several forked targets with one state/path snapshot per retry."""
        normalized: list[tuple[str, str]] = []
        seen_targets: set[str] = set()
        for target_id, source_id in targets:
            canonical_target = _canonical_uuid(target_id)
            canonical_source = _canonical_uuid(source_id)
            if not canonical_target or not canonical_source:
                raise SessionCleanupError("目标或源会话标识无效。")
            if canonical_target in seen_targets:
                raise SessionCleanupError("目标会话标识重复。")
            seen_targets.add(canonical_target)
            normalized.append((canonical_target, canonical_source))
        if not normalized:
            raise SessionCleanupError("目标会话列表为空。")

        pending = dict(normalized)
        source_by_target = dict(normalized)
        outcomes: dict[str, object] = {}
        last_errors: dict[str, str] = {}
        reported_ids: set[str] = set()
        materialized_ids: set[str] = set()
        materialized_paths: dict[str, Path] = {}

        def report_result(target_id: str, success: bool, error: str = "") -> None:
            if not callable(progress_callback) or target_id in reported_ids:
                return
            reported_ids.add(target_id)
            try:
                progress_callback(
                    target_id,
                    "materialize",
                    success,
                    "" if success else str(error or "Codex 目标会话历史物化未完成。"),
                )
            except Exception:
                pass

        for index, delay in enumerate(self.transfer_ready_retry_delays):
            if not pending:
                break
            if index and delay:
                self._sleep(delay)
            try:
                records, _parents, _edge_states, _unsafe_ids, _unresolved = (
                    self._load_state()
                )
                allowed_roots = self._allowed_rollout_roots()
                rollout_paths = {
                    session_id: record.rollout_path
                    for session_id, record in records.items()
                    if (
                        record.rollout_path is not None
                        and _path_under(record.rollout_path, allowed_roots)
                        and record.rollout_path.is_file()
                    )
                }
            except (SessionCleanupError, SessionMaterializationError) as exc:
                detail = str(exc) or type(exc).__name__
                for target_id in pending:
                    last_errors[target_id] = detail
                continue

            def materialize_one(
                target_id: str,
                source_id: str,
            ) -> tuple[str, bool, str]:
                target = records.get(target_id)
                try:
                    if (
                        target is None
                        or target.rollout_path is None
                        or not _path_under(target.rollout_path, allowed_roots)
                        or not target.rollout_path.is_file()
                    ):
                        raise SessionCleanupError(
                            "Codex 目标会话 rollout 尚不可用。"
                        )
                    materialize_forked_rollout(
                        target_id=target_id,
                        source_id=source_id,
                        target_path=target.rollout_path,
                        rollout_paths=rollout_paths,
                    )
                except Exception as exc:
                    # A missing freshly-forked target and a transient Windows
                    # file handle are both expected just after fork.  The source
                    # remains untouched until this bounded materialization succeeds.
                    return target_id, False, str(exc) or type(exc).__name__
                return target_id, True, ""

            pending_items = tuple(pending.items())
            worker_count = min(self.transfer_max_workers, len(pending_items))

            def apply_materialize_result(
                completed: tuple[str, bool, str],
            ) -> None:
                target_id, success, error = completed
                if success:
                    outcomes[target_id] = True
                    pending.pop(target_id, None)
                    materialized_ids.add(target_id)
                    target_record = records.get(target_id)
                    if target_record is not None and target_record.rollout_path is not None:
                        materialized_paths[target_id] = target_record.rollout_path
                    report_result(target_id, True)
                else:
                    last_errors[target_id] = error
                    if index == len(self.transfer_ready_retry_delays) - 1:
                        report_result(target_id, False, error)

            if worker_count <= 1:
                for target_id, source_id in pending_items:
                    apply_materialize_result(
                        materialize_one(target_id, source_id)
                    )
            else:
                with ThreadPoolExecutor(
                    max_workers=worker_count,
                    thread_name_prefix="codex-hud-session-materialize",
                ) as executor:
                    futures = {
                        executor.submit(materialize_one, target_id, source_id): target_id
                        for target_id, source_id in pending_items
                    }
                    for future in as_completed(futures):
                        apply_materialize_result(future.result())

        if materialized_ids:
            try:
                self._reset_thread_history_projection(materialized_ids)
                self._sync_thread_state_metadata(materialized_paths)
            except SessionCleanupError as exc:
                detail = str(exc) or type(exc).__name__
                for target_id in tuple(materialized_ids):
                    outcomes.pop(target_id, None)
                    pending[target_id] = source_by_target[target_id]
                    last_errors[target_id] = detail
                    if target_id in reported_ids:
                        reported_ids.remove(target_id)
                    report_result(target_id, False, detail)

        for target_id in pending:
            outcomes[target_id] = last_errors.get(
                target_id,
                "Codex 目标会话历史物化未完成。",
            )
            report_result(target_id, False, str(outcomes[target_id] or ""))
        return outcomes

    def materialize_target_rollout(self, target_id: str, source_id: str) -> None:
        """Flatten one forked target before any optional source deletion."""
        canonical_target = _canonical_uuid(target_id)
        outcomes = self.materialize_target_rollouts(((canonical_target, source_id),))
        outcome = outcomes.get(canonical_target)
        if outcome is True:
            return
        raise SessionCleanupError(
            str(outcome or "Codex 目标会话历史物化未完成。")
        )

    def _register_session_index_batch(
        self,
        entries: Sequence[tuple[str, str]],
    ) -> None:
        """Append newly forked threads with one index scan and one fsync."""
        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        for session_id, title in entries:
            canonical = _canonical_uuid(session_id)
            if not canonical:
                raise SessionCleanupError(
                    "目标会话标识无效，无法更新 CLI 会话索引。"
                )
            if canonical in seen:
                continue
            seen.add(canonical)
            normalized.append((canonical, title))
        if not normalized:
            return

        existing_ids: set[str] = set()
        existing_lines: list[str] = []
        if self.session_index_path.is_file():
            try:
                with self.session_index_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        existing_lines.append(line)
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(payload, Mapping):
                            existing = _canonical_uuid(payload.get("id"))
                            if existing:
                                existing_ids.add(existing)
            except (OSError, UnicodeError) as exc:
                raise SessionCleanupError("Codex CLI 会话索引无法读取。") from exc
        pending = [
            (session_id, title)
            for session_id, title in normalized
            if session_id not in existing_ids
        ]
        if not pending:
            return
        lines = []
        for session_id, title in pending:
            payload = {
                "id": session_id,
                "thread_name": str(title or "Untitled session").strip()
                or "Untitled session",
                "updated_at": datetime.now(timezone.utc)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
            }
            lines.append(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
        temporary = self.session_index_path.with_name(
            f".{self.session_index_path.name}.hud-batch-{secrets.token_hex(10)}"
        )
        existing_content = "".join(existing_lines)
        if existing_content and not existing_content.endswith(("\n", "\r")):
            existing_content += "\n"
        try:
            self.session_index_path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(existing_content)
                handle.write("".join(lines))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.session_index_path)
        except (OSError, UnicodeError) as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise SessionCleanupError("Codex CLI 会话索引无法更新。") from exc

    def _register_session_index(self, session_id: str, title: str) -> None:
        """Make a newly forked thread discoverable by ``codex resume``."""
        self._register_session_index_batch(((session_id, title),))

    def _reset_thread_history_projection(self, session_ids: Iterable[str]) -> None:
        """Reset cursors invalidated by replacing forked rollout files."""
        path = self.thread_history_db_path
        if path is None or not path.is_file():
            return
        canonical_ids = tuple(
            dict.fromkeys(
                canonical
                for value in session_ids
                if (canonical := _canonical_uuid(value))
            )
        )
        if not canonical_ids:
            return
        placeholders = ", ".join("?" for _ in canonical_ids)
        try:
            with closing(sqlite3.connect(str(path), timeout=5.0)) as connection:
                connection.execute("PRAGMA busy_timeout = 5000")
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "thread_history_projection_state" not in tables:
                    return
                connection.execute(
                    "DELETE FROM thread_history_projection_state "
                    f"WHERE thread_id IN ({placeholders})",
                    canonical_ids,
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise SessionCleanupError(
                "Codex 目标会话历史投影无法重建。"
            ) from exc

    def _sync_thread_state_metadata(self, targets: Mapping[str, Path]) -> None:
        """Seed CLI-facing metadata for targets rebuilt from source records."""
        if not self.state_db_path.is_file() or not targets:
            return
        normalized = {
            canonical: Path(path)
            for session_id, path in targets.items()
            if (canonical := _canonical_uuid(session_id))
        }
        if not normalized:
            return
        try:
            with closing(sqlite3.connect(str(self.state_db_path), timeout=5.0)) as connection:
                connection.execute("PRAGMA busy_timeout = 5000")
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(threads)")
                }
                if "id" not in columns:
                    return
                for session_id, path in normalized.items():
                    text = _first_user_message_text(path)
                    if not text:
                        continue
                    updates: dict[str, object] = {}
                    if "source" in columns:
                        # A user fork is intended to be resumable from the CLI
                        # picker even when Codex inherited the source client kind.
                        updates["source"] = "cli"
                    if "thread_source" in columns:
                        updates["thread_source"] = "user"
                    if "has_user_event" in columns:
                        updates["has_user_event"] = 1
                    for name in ("title", "name", "first_user_message", "preview"):
                        if name in columns:
                            updates[name] = text
                    if not updates:
                        continue
                    assignments = ", ".join(f"{name} = ?" for name in updates)
                    connection.execute(
                        f"UPDATE threads SET {assignments} WHERE id = ?",
                        [*updates.values(), session_id],
                    )
                connection.commit()
        except sqlite3.Error as exc:
            raise SessionCleanupError(
                "Codex 目标会话状态元数据无法同步。"
            ) from exc

    def transfer(
        self,
        item_ids: Sequence[str],
        revision: str,
        source_provider: str,
        target_provider: str,
        mode: str,
        *,
        fork: Callable[[str, str, str], str],
        materialize: Callable[[str, str], object] | None = None,
        verify: Callable[[str, str], bool] | None = None,
        materialize_batch: TransferBatchOperation | None = None,
        verify_batch: TransferBatchOperation | None = None,
        desktop_source_lifecycle: DesktopSourceLifecycle | None = None,
        desktop_source_preflight: DesktopSourcePreflight | None = None,
        started_at_ms: int | float | None = None,
        request_id: str = "",
    ) -> dict[str, object]:
        """Fork selected sessions to another Provider, optionally removing sources.

        ``fork`` is injected by the runtime worker so this manager remains
        responsible for inventory/revalidation.  Migration source deletion is
        intentionally delegated to the running Desktop lifecycle so the
        Desktop-owned local thread catalog receives the archive/delete events.
        """
        normalized_source = str(source_provider or "").strip().casefold()
        normalized_target = str(target_provider or "").strip().casefold()
        normalized_mode = str(mode or "copy").strip().casefold()
        if not normalized_source or not normalized_target:
            raise SessionCleanupError("会话迁移需要源和目标 Provider。")
        if normalized_source == normalized_target:
            raise SessionCleanupError("源 Provider 与目标 Provider 不能相同。")
        if normalized_mode not in {"copy", "migrate"}:
            raise SessionCleanupError("不支持的会话迁移模式。")
        if not callable(fork):
            raise SessionCleanupError("Codex fork 服务当前不可用。")
        if not callable(materialize) and not callable(materialize_batch):
            raise SessionCleanupError("Codex 目标会话物化服务当前不可用。")
        if not callable(verify) and not callable(verify_batch):
            raise SessionCleanupError("Codex 目标会话持久化验证当前不可用。")
        items = self._selected_items(item_ids, revision)
        if any(item.model_provider.casefold() != normalized_source for item in items):
            raise SessionCleanupError(
                "所选会话已不属于当前源 Provider，请重新扫描后再试。"
            )
        if any(not item.transferable for item in items):
            raise SessionCleanupError("所选会话的分页历史源无法验证，不能复制或迁移。")
        if any(item.archived for item in items):
            raise SessionCleanupError(
                "所选会话包含已归档会话，请先解除归档后再复制或迁移。"
            )
        results: list[dict[str, object]] = []
        migration_candidates: list[tuple[SessionCleanupItem, dict[str, object]]] = []
        current_records = self._load_state()[0]
        for item in items:
            current_record = current_records.get(item._session_id)
            if current_record is None:
                raise SessionCleanupError(
                    "所选会话在提交前已改变或不再可用，请重新扫描后再试。"
                )
            if current_record.archived:
                raise SessionCleanupError(
                    "所选会话在提交前已归档，请先解除归档后再复制或迁移。"
                )
        existing_session_ids = set(current_records)
        created_target_ids: set[str] = set()
        publisher = getattr(self, "progress_publisher", None)
        total = len(items)
        last_progress_publish = 0.0
        try:
            transfer_started_at = int(started_at_ms or self.clock() * 1000)
        except (TypeError, ValueError, OverflowError):
            transfer_started_at = int(time.time() * 1000)
        target_progress: dict[str, tuple[str, bool, str]] = {}
        target_progress_events: set[tuple[str, str]] = set()
        source_cleanup_completed = 0
        source_cleanup_total = 0

        def publish_progress(
            *,
            force: bool = False,
            phase: str = "targets",
            source_cleanup_completed: int = 0,
            source_cleanup_total: int = 0,
        ) -> None:
            nonlocal last_progress_publish
            now = time.monotonic()
            # App-server notifications and renderer payloads are relatively
            # expensive for large selections. Keep progress responsive while
            # coalescing bursts from fast local forks.
            if (
                not force
                and last_progress_publish
                and now - last_progress_publish < 0.075
            ):
                return
            target_completed_count = sum(
                status[0] in {"materialize", "verify"}
                or (status[0] == "fork" and not status[1])
                for status in target_progress.values()
            )
            target_ready_count = sum(
                status[0] == "verify" and status[1]
                for status in target_progress.values()
            )
            target_failed_count = sum(
                status[0] in {"fork", "materialize", "verify"} and not status[1]
                for status in target_progress.values()
            )
            migrated_count = sum(
                bool(result.get("sourceDeleted")) for result in results
            )
            if phase in {"sourcePreflight", "sourceCleanup"}:
                progress = 60 + round(
                    39
                    * source_cleanup_completed
                    / max(1, source_cleanup_total),
                )
                phase_label = (
                    "Desktop 源会话只读预检"
                    if phase == "sourcePreflight"
                    else "Desktop 源会话归档与删除"
                )
                phase_index = 2
                phase_count = 2
            else:
                progress = round(60 * target_completed_count / max(1, total))
                phase_label = "目标会话物化与可见性验证"
                phase_index = 1
                phase_count = 2 if normalized_mode == "migrate" else 1
            self.mark_operation(
                request_id=request_id,
                action="sessionTransfer",
                state="running",
                progress=min(99, progress),
                sourceProvider=normalized_source,
                targetProvider=normalized_target,
                mode=normalized_mode,
                selectedIds=[item.id for item in items],
                sessionCount=total,
                completedCount=target_completed_count,
                copiedCount=target_ready_count,
                targetReadyCount=target_ready_count,
                migratedCount=migrated_count,
                sourceRetainedCount=sum(
                    bool(result.get("sourceRetained")) for result in results
                ),
                sourceArchivedCount=sum(
                    bool(result.get("sourceArchived")) for result in results
                ),
                sourceCleanupPendingCount=sum(
                    bool(result.get("sourceCleanupPending")) for result in results
                ),
                targetFailedCount=target_failed_count,
                unmigratedCount=(
                    total - target_ready_count
                    if normalized_mode == "migrate"
                    else 0
                ),
                sourceCleanupCompletedCount=source_cleanup_completed,
                sourceCleanupTotalCount=source_cleanup_total,
                transferPhase=phase,
                phaseLabel=phase_label,
                phaseIndex=phase_index,
                phaseCount=phase_count,
                startedAt=transfer_started_at,
                # Source cleanup is best effort after target readiness; only
                # target visibility/resumability contributes to transfer failure.
                failedCount=target_failed_count,
            )
            if callable(publisher):
                publisher(self.snapshot(include_sessions=False))
            last_progress_publish = now

        def report_target_progress(
            target_id: str,
            phase: str,
            success: bool,
            error: str = "",
        ) -> None:
            target_progress[target_id] = (phase, bool(success), str(error or ""))
            target_progress_events.add((phase, target_id))
            publish_progress(phase="targets")

        publish_progress(force=True, phase="targets")

        transfer_work: list[dict[str, object]] = []
        for item in items:
            work: dict[str, object] = {
                "item": item,
                "targetId": "",
                "forked": False,
                "historyMaterialized": False,
                "targetReady": False,
                "indexWarning": "",
                "error": "",
            }
            try:
                new_session_id = _normalise_fork_target_uuid(
                    fork(item._session_id, normalized_target, item._cwd)
                )
                if not new_session_id or new_session_id == item._session_id:
                    raise SessionCleanupError("Codex fork 未返回新的目标会话。")
                if (
                    new_session_id in existing_session_ids
                    or new_session_id in created_target_ids
                ):
                    raise SessionCleanupError(
                        "Codex fork 返回了与现有目标会话冲突的标识。"
                    )
                created_target_ids.add(new_session_id)
                work["targetId"] = new_session_id
                work["forked"] = True
                report_target_progress(
                    f"fork:{item._session_id}",
                    "fork",
                    True,
                )
            except Exception as exc:
                work["error"] = str(exc) or type(exc).__name__
                report_target_progress(
                    f"fork:{item._session_id}",
                    "fork",
                    False,
                    str(work["error"]),
                )
            transfer_work.append(work)

        publish_progress(force=True, phase="targets")

        def batch_error(
            outcomes: object,
            key: str,
            fallback: str,
            *,
            allow_none: bool = False,
        ) -> str:
            if not isinstance(outcomes, Mapping) or key not in outcomes:
                return fallback
            value = outcomes[key]
            if value is True or (allow_none and value is None):
                return ""
            if value is False:
                return fallback
            return str(value).strip() or fallback

        forked_work = [
            work for work in transfer_work if bool(work.get("forked"))
        ]
        if forked_work:
            materialize_targets = [
                (
                    str(work["targetId"]),
                    str(getattr(work["item"], "_session_id", "")),
                )
                for work in forked_work
            ]
            if callable(materialize_batch):
                try:
                    materialized_outcomes: object = materialize_batch(
                        materialize_targets,
                        report_target_progress,
                    )
                except Exception as exc:
                    materialized_outcomes = {
                        target_id: str(exc) or type(exc).__name__
                        for target_id, _source_id in materialize_targets
                    }
                for work in forked_work:
                    target_id = str(work["targetId"])
                    error = batch_error(
                        materialized_outcomes,
                        target_id,
                        "Codex 目标会话历史物化未完成。",
                        allow_none=True,
                    )
                    if error:
                        work["error"] = error
                    else:
                        work["historyMaterialized"] = True
                    if ("materialize", target_id) not in target_progress_events:
                        report_target_progress(
                            target_id,
                            "materialize",
                            not bool(error),
                            error,
                        )
            else:
                assert callable(materialize)
                for work in forked_work:
                    target_id = str(work["targetId"])
                    source_id = str(getattr(work["item"], "_session_id", ""))
                    try:
                        materialized = materialize(target_id, source_id)
                        if materialized is False:
                            raise SessionCleanupError(
                                "Codex 目标会话历史物化未完成。"
                            )
                        work["historyMaterialized"] = True
                    except Exception as exc:
                        work["error"] = str(exc) or type(exc).__name__
                    report_target_progress(
                        target_id,
                        "materialize",
                        not bool(work.get("error")),
                        str(work.get("error") or ""),
                    )

        materialized_work = [
            work for work in transfer_work if bool(work.get("historyMaterialized"))
        ]
        if materialized_work:
            verify_targets = [
                (str(work["targetId"]), normalized_target)
                for work in materialized_work
            ]
            if callable(verify_batch):
                try:
                    verified_outcomes: object = verify_batch(
                        verify_targets,
                        report_target_progress,
                    )
                except Exception as exc:
                    verified_outcomes = {
                        target_id: str(exc) or type(exc).__name__
                        for target_id, _provider in verify_targets
                    }
                for work in materialized_work:
                    target_id = str(work["targetId"])
                    error = batch_error(
                        verified_outcomes,
                        target_id,
                        "目标会话已创建，但未通过目标 Provider 可见和续聊验证。",
                    )
                    if error:
                        work["error"] = error
                    else:
                        work["targetReady"] = True
                    if ("verify", target_id) not in target_progress_events:
                        report_target_progress(
                            target_id,
                            "verify",
                            not bool(error),
                            error,
                        )
            else:
                assert callable(verify)
                for work in materialized_work:
                    target_id = str(work["targetId"])
                    try:
                        if verify(target_id, normalized_target) is not True:
                            raise SessionCleanupError(
                                "目标会话已创建，但未通过目标 Provider 可见和续聊验证。"
                            )
                        work["targetReady"] = True
                    except Exception as exc:
                        work["error"] = str(exc) or type(exc).__name__
                    report_target_progress(
                        target_id,
                        "verify",
                        bool(work.get("targetReady")),
                        str(work.get("error") or ""),
                    )

        ready_work = [
            work for work in transfer_work if bool(work.get("targetReady"))
        ]
        if ready_work:
            try:
                self._register_session_index_batch(
                    [
                        (
                            str(work["targetId"]),
                            str(getattr(work["item"], "title", "")),
                        )
                        for work in ready_work
                    ]
                )
            except SessionCleanupError as exc:
                warning = str(exc) or type(exc).__name__
                for work in ready_work:
                    work["indexWarning"] = warning

        for work in transfer_work:
            item = work["item"]
            forked = bool(work.get("forked"))
            target_ready = bool(work.get("targetReady"))
            new_session_id = str(work.get("targetId") or "")
            result = {
                "id": item.id,
                "title": item.title,
                "workdir": item._cwd,
                "targetSessionId": new_session_id,
                "state": "copied"
                if target_ready
                else ("targetCreated" if forked else "failed"),
                "forked": forked,
                "targetCreated": forked,
                "targetVisible": target_ready,
                "targetResumable": target_ready,
                "historyMaterialized": bool(work.get("historyMaterialized")),
                "sourceDeleted": False,
                "sourceRetained": normalized_mode == "migrate",
                "sourceArchived": False,
                "sourceCleanupPending": normalized_mode == "migrate",
                "sourceCleanupPreflighted": False,
                "sourceCleanupError": "",
                "desktopLifecycleVerified": target_ready
                and normalized_mode != "migrate",
                "indexWarning": str(work.get("indexWarning") or ""),
                "error": str(work.get("error") or ""),
            }
            results.append(result)
            if normalized_mode == "migrate" and target_ready:
                migration_candidates.append((item, result))
        publish_progress(force=True)

        if normalized_mode == "migrate":
            if len(migration_candidates) != total:
                # Multi-select migration is all-or-none for source deletion.
                # A usable target remains available, but retaining every source
                # avoids silently producing a mixed migration.
                for _item, result in migration_candidates:
                    if not str(result.get("error") or "").strip():
                        result["sourceCleanupPending"] = True
                        result["sourceCleanupError"] = (
                            "至少一个目标会话尚未通过迁移就绪验证。"
                        )
            elif migration_candidates:
                fresh_candidates: list[
                    tuple[SessionCleanupItem, dict[str, object]]
                ] = []
                blockers: dict[str, str] = {}
                target_session_ids = {
                    _canonical_uuid(result.get("targetSessionId"))
                    for _original, result in migration_candidates
                    if _canonical_uuid(result.get("targetSessionId"))
                }
                inventory_items = self._items
                inventory_revision = self._revision
                try:
                    # The pre-fork opaque inventory is stale after App Server
                    # work. Re-scan before preview/execute and map by the
                    # private source id, never by the old renderer id. Restore
                    # the pre-transfer opaque inventory afterward so the final
                    # payload can remove successful rows by their known IDs
                    # without requiring another renderer scan.
                    self.scan(request_id=request_id)
                    current_by_session = {
                        candidate._session_id: candidate
                        for candidate in self._items.values()
                    }
                    for original, result in migration_candidates:
                        current = current_by_session.get(original._session_id)
                        if current is None:
                            blockers[original.id] = "源会话在删除前已改变或不再可用。"
                        elif set(current._descendant_ids) & target_session_ids:
                            # A future App Server may model ``thread/fork`` as
                            # a spawn edge. The normal source-tree delete would
                            # then stage the freshly-created target alongside
                            # its source, so retain every source instead of
                            # risking deletion of a usable target conversation.
                            blockers[original.id] = (
                                "目标会话仍属于源会话子树，无法安全删除源会话。"
                            )
                        elif current.archived:
                            blockers[original.id] = (
                                "源会话在删除前已归档，请先解除归档后再试。"
                            )
                        elif current.model_provider.casefold() != normalized_source:
                            blockers[original.id] = "源会话的 Provider 在删除前已改变。"
                        elif not current.selectable:
                            blockers[original.id] = (
                                current.blocked_reason or "源会话当前不允许安全删除。"
                            )
                        elif not current.transferable:
                            blockers[original.id] = (
                                current.transfer_blocked_reason
                                or "源会话的分页历史当前无法验证。"
                            )
                        else:
                            fresh_candidates.append((current, result))
                except Exception as exc:
                    detail = str(exc) or type(exc).__name__
                    blockers = {
                        original.id: f"删除前刷新源会话失败：{detail}"
                        for original, _result in migration_candidates
                    }
                finally:
                    self._items = inventory_items
                    self._revision = inventory_revision

                if blockers:
                    for original, result in migration_candidates:
                        result["sourceCleanupPending"] = True
                        result["sourceCleanupError"] = blockers.get(
                            original.id,
                            "源会话已保留：同一批次中另一个源会话未满足安全删除条件。",
                        )
                        if not str(result.get("error") or "").strip():
                            result["error"] = ""
                elif len(fresh_candidates) != total:
                    for _original, result in migration_candidates:
                        result["sourceCleanupPending"] = True
                        result["sourceCleanupError"] = "删除前会话清单不完整。"
                        if not str(result.get("error") or "").strip():
                            result["error"] = ""
                else:
                    source_families = [
                        (
                            current,
                            result,
                            tuple(
                                dict.fromkeys(
                                    (
                                        current._session_id,
                                        *current._descendant_ids,
                                    )
                                )
                            ),
                        )
                        for current, result in fresh_candidates
                    ]
                    source_cleanup_total = len(source_families)
                    publish_progress(
                        force=True,
                        phase="sourcePreflight",
                        source_cleanup_completed=0,
                        source_cleanup_total=source_cleanup_total,
                    )
                    preflight_errors: dict[str, str] = {}
                    if callable(desktop_source_preflight):
                        # This is a batch barrier: do not send archive/delete
                        # for an earlier root while a later root can still fail
                        # the Desktop rollout check.
                        worker_count = min(
                            self.transfer_max_workers,
                            len(source_families),
                        )

                        def run_preflight(
                            current: SessionCleanupItem,
                            source_family_ids: Sequence[str],
                        ) -> Mapping[str, object]:
                            return desktop_source_preflight(
                                source_family_ids,
                                current._cwd,
                            )

                        with ThreadPoolExecutor(
                            max_workers=max(1, worker_count),
                            thread_name_prefix="codex-hud-session-preflight",
                        ) as executor:
                            futures = {
                                executor.submit(
                                    run_preflight,
                                    current,
                                    source_family_ids,
                                ): result
                                for current, result, source_family_ids in source_families
                            }
                            for future in as_completed(futures):
                                result = futures[future]
                                try:
                                    preflight = future.result()
                                    if not isinstance(preflight, Mapping):
                                        raise SessionCleanupError(
                                            "Codex Desktop 源会话预检返回了无效结果。"
                                        )
                                    if preflight.get("verified") is not True:
                                        raise SessionCleanupError(
                                            str(
                                                preflight.get("error")
                                                or "Codex Desktop 源会话预检未通过。"
                                            ).strip()
                                        )
                                except Exception as exc:
                                    preflight_errors[result["id"]] = (
                                        str(exc) or type(exc).__name__
                                    )

                    publish_progress(
                        force=True,
                        phase=(
                            "sourcePreflight"
                            if preflight_errors
                            or not callable(desktop_source_preflight)
                            or not callable(desktop_source_lifecycle)
                            else "sourceCleanup"
                        ),
                        source_cleanup_completed=0,
                        source_cleanup_total=source_cleanup_total,
                    )

                    if not callable(desktop_source_lifecycle):
                        for _current, result, _source_family_ids in source_families:
                            result["sourceCleanupPending"] = True
                            result["sourceCleanupError"] = (
                                "Codex Desktop 归档/删除通道当前不可用，源会话已保留。"
                            )
                            result["error"] = ""
                    elif not callable(desktop_source_preflight):
                        # Source deletion is never permitted without a
                        # completed batch preflight.  Keep the verified target
                        # usable and retain every source until Desktop can
                        # read the full source tree.
                        for _current, result, _source_family_ids in source_families:
                            result["sourceCleanupPending"] = True
                            result["sourceCleanupError"] = (
                                "Codex Desktop 源会话预检通道当前不可用，源会话已保留。"
                            )
                            result["error"] = ""
                    elif preflight_errors:
                        for _current, result, _source_family_ids in source_families:
                            result["sourceCleanupPending"] = True
                            result["sourceCleanupPreflighted"] = False
                            result["sourceCleanupError"] = preflight_errors.get(
                                result["id"],
                                "同一批次中另一个源会话未通过 Codex Desktop 预检。",
                            )
                            result["error"] = ""
                    else:
                        source_cleanup_completed = 0

                        def run_lifecycle(
                            current: SessionCleanupItem,
                            source_family_ids: Sequence[str],
                        ) -> tuple[list[Mapping[str, object]], str]:
                            reports: list[Mapping[str, object]] = []
                            lifecycle_error = ""
                            try:
                                for source_id in source_family_ids:
                                    report = desktop_source_lifecycle(
                                        source_id,
                                        current._cwd,
                                    )
                                    if not isinstance(report, Mapping):
                                        raise SessionCleanupError(
                                            "Codex Desktop 归档/删除通道返回了无效结果。"
                                        )
                                    if (
                                        _canonical_uuid(report.get("threadId"))
                                        != source_id
                                    ):
                                        raise SessionCleanupError(
                                            "Codex Desktop 归档/删除通道返回了不匹配的会话标识。"
                                        )
                                    reports.append(report)
                            except Exception as exc:
                                lifecycle_error = str(exc) or type(exc).__name__
                            return reports, lifecycle_error

                        def apply_lifecycle_result(
                            current: SessionCleanupItem,
                            result: dict[str, object],
                            source_family_ids: Sequence[str],
                            usage_receipt: object | None,
                            reports: Sequence[Mapping[str, object]],
                            lifecycle_error: str,
                        ) -> None:
                            nonlocal source_cleanup_completed
                            archived = (
                                bool(reports)
                                and len(reports) == len(source_family_ids)
                                and all(
                                    bool(report.get("archived"))
                                    and bool(report.get("archiveNotification"))
                                    for report in reports
                                )
                            )
                            deleted = archived and all(
                                bool(report.get("deleted"))
                                and bool(report.get("deleteNotification"))
                                and bool(report.get("verified"))
                                for report in reports
                            )
                            report_errors = [
                                str(report.get("error") or "").strip()
                                for report in reports
                                if str(report.get("error") or "").strip()
                            ]
                            detail = lifecycle_error or "; ".join(report_errors)

                            if deleted:
                                result["state"] = "migrated"
                                result["sourceDeleted"] = True
                                result["sourceRetained"] = False
                                result["sourceArchived"] = True
                                result["sourceCleanupPending"] = False
                                result["sourceCleanupError"] = ""
                                result["desktopLifecycleVerified"] = True
                                result["error"] = ""
                                self.mark_migrated_sources(source_family_ids)
                                if (
                                    usage_receipt
                                    and self.usage_snapshot_commit is not None
                                ):
                                    try:
                                        self.usage_snapshot_commit(usage_receipt)
                                    except Exception as exc:
                                        result["usageWarning"] = (
                                            "源会话已由 Codex Desktop 删除，"
                                            "但使用量归档失败："
                                            f"{str(exc) or type(exc).__name__}"
                                        )
                            else:
                                if (
                                    usage_receipt
                                    and self.usage_snapshot_discard is not None
                                ):
                                    try:
                                        self.usage_snapshot_discard(usage_receipt)
                                    except Exception:
                                        pass
                                result["sourceArchived"] = archived
                                result["desktopLifecycleVerified"] = False
                                result["sourceRetained"] = True
                                result["sourceCleanupPending"] = True
                                result["sourceCleanupError"] = detail
                                # The destination has already passed the durable
                                # visibility and resume checks. Source cleanup is
                                # best effort and must not turn a usable migration
                                # into a user-visible transfer error.
                                result["error"] = ""
                            source_cleanup_completed += 1
                            publish_progress(
                                force=True,
                                phase="sourceCleanup",
                                source_cleanup_completed=source_cleanup_completed,
                                source_cleanup_total=source_cleanup_total,
                            )

                        lifecycle_jobs: list[
                            tuple[
                                SessionCleanupItem,
                                dict[str, object],
                                tuple[str, ...],
                                object | None,
                            ]
                        ] = []
                        for current, result, source_family_ids in source_families:
                            result["sourceCleanupPreflighted"] = True
                            usage_receipt: object | None = None
                            try:
                                if self.usage_snapshot_prepare is not None:
                                    usage_receipt = self.usage_snapshot_prepare(current)
                            except Exception as exc:
                                apply_lifecycle_result(
                                    current,
                                    result,
                                    source_family_ids,
                                    None,
                                    (),
                                    str(exc) or type(exc).__name__,
                                )
                            else:
                                lifecycle_jobs.append(
                                    (
                                        current,
                                        result,
                                        source_family_ids,
                                        usage_receipt,
                                    )
                                )

                        worker_count = min(
                            self.transfer_max_workers,
                            len(lifecycle_jobs),
                        )
                        with ThreadPoolExecutor(
                            max_workers=max(1, worker_count),
                            thread_name_prefix="codex-hud-session-lifecycle",
                        ) as executor:
                            futures = {
                                executor.submit(
                                    run_lifecycle,
                                    current,
                                    source_family_ids,
                                ): (current, result, source_family_ids, usage_receipt)
                                for (
                                    current,
                                    result,
                                    source_family_ids,
                                    usage_receipt,
                                ) in lifecycle_jobs
                            }
                            for future in as_completed(futures):
                                current, result, source_family_ids, usage_receipt = (
                                    futures[future]
                                )
                                try:
                                    reports, lifecycle_error = future.result()
                                except Exception as exc:
                                    reports = []
                                    lifecycle_error = str(exc) or type(exc).__name__
                                apply_lifecycle_result(
                                    current,
                                    result,
                                    source_family_ids,
                                    usage_receipt,
                                    reports,
                                    lifecycle_error,
                                )
                publish_progress(
                    force=True,
                    phase=("sourceCleanup" if normalized_mode == "migrate" else "targets"),
                    source_cleanup_completed=source_cleanup_completed,
                    source_cleanup_total=source_cleanup_total,
                )

        copied_count = sum(
            bool(result.get("targetVisible")) and bool(result.get("targetResumable"))
            for result in results
        )
        migrated_count = sum(result.get("state") == "migrated" for result in results)
        source_cleanup_pending_count = sum(
            bool(result.get("sourceCleanupPending"))
            for result in results
        )
        source_retained_count = sum(
            bool(result.get("sourceRetained")) for result in results
        )
        failed_count = total - copied_count
        target_failed_count = total - copied_count
        unmigrated_count = total - copied_count if normalized_mode == "migrate" else 0
        if normalized_mode == "migrate":
            # Verified targets with a retained source must not be offered for a
            # duplicate copy when the dialog reopens (the operation-results
            # filter only survives while the payload operation still matches
            # the dialog's current provider pair).  They stay listed for the
            # storage surface, flagged via ``pendingSourceCleanup``.
            items_by_id = {item.id: item for item in items}
            pending_family_ids: list[str] = []
            for result in results:
                if (
                    not (
                        result.get("targetVisible")
                        and result.get("targetResumable")
                    )
                    or result.get("sourceDeleted")
                ):
                    continue
                source_item = items_by_id.get(str(result.get("id")))
                if source_item is None:
                    continue
                pending_family_ids.append(source_item._session_id)
                pending_family_ids.extend(source_item._descendant_ids)
            if pending_family_ids:
                self.mark_pending_cleanup_sources(pending_family_ids)
        # A transfer is successful once every destination is durable, visible,
        # and resumable.  Source cleanup is deliberately best effort: a stale
        # Desktop rollout must not invalidate a usable cross-provider copy.
        completed = copied_count == total
        has_forked_result = any(bool(result.get("forked")) for result in results)
        state = (
            "completed" if completed else ("partial" if has_forked_result else "failed")
        )
        return self.mark_operation(
            request_id=request_id,
            action="sessionTransfer",
            state=state,
            progress=100,
            sourceProvider=normalized_source,
            targetProvider=normalized_target,
            mode=normalized_mode,
            selectedIds=[item.id for item in items],
            results=results,
            sessionCount=total,
            copiedCount=copied_count,
            migratedCount=migrated_count,
            sourceRetainedCount=source_retained_count,
            sourceArchivedCount=sum(
                bool(result.get("sourceArchived")) for result in results
            ),
            desktopLifecycleFailureCount=source_cleanup_pending_count,
            sourceCleanupPendingCount=source_cleanup_pending_count,
            sourceCleanupCompletedCount=source_cleanup_completed,
            sourceCleanupTotalCount=source_cleanup_total,
            targetFailedCount=max(0, target_failed_count),
            unmigratedCount=max(0, unmigrated_count),
            failedCount=max(0, failed_count),
            startedAt=transfer_started_at,
        )

    def preview(
        self,
        item_ids: Sequence[str],
        revision: str,
        *,
        request_id: str = "",
    ) -> dict[str, object]:
        items = self._selected_items(item_ids, revision)
        token = self.token_factory()
        self._confirmations[token] = _Confirmation(
            revision=revision,
            item_ids=tuple(item.id for item in items),
            expires_at=float(self.clock()) + self.confirmation_ttl_seconds,
        )
        return self.mark_operation(
            id=token,
            request_id=request_id,
            action="preview",
            state="preview",
            progress=100,
            confirmationToken=token,
            inventoryRevision=revision,
            selectedIds=[item.id for item in items],
            sessionCount=len(items),
            descendantCount=sum(item.descendant_count for item in items),
            estimatedBytes=sum(item.size for item in items),
        )

    def execute(
        self,
        item_ids: Sequence[str],
        revision: str,
        confirmation_token: str,
        *,
        request_id: str = "",
    ) -> dict[str, object]:
        items = self._consume_confirmation(item_ids, revision, confirmation_token)
        capability = self.probe_capability()
        self._capability = capability
        usage_receipts: list[object] = []
        deleted = False
        try:
            self._revalidate_batch(items, capability=capability)
            for item in items:
                if self.usage_snapshot_prepare is not None:
                    receipt = self.usage_snapshot_prepare(item)
                    if receipt:
                        usage_receipts.append(receipt)
            self._revalidate_pending_activity(items)
            self._delete_local_batch(items)
            deleted = True
            self._verify_deleted_batch(items)
            if self.usage_snapshot_commit is not None:
                for receipt in usage_receipts:
                    self.usage_snapshot_commit(receipt)
        except SessionCleanupError as exc:
            if not deleted and self.usage_snapshot_discard is not None:
                for receipt in usage_receipts:
                    try:
                        self.usage_snapshot_discard(receipt)
                    except Exception:
                        pass
            results = [self._failed_result(item, str(exc)) for item in items]
        except Exception as exc:
            if not deleted and self.usage_snapshot_discard is not None:
                for receipt in usage_receipts:
                    try:
                        self.usage_snapshot_discard(receipt)
                    except Exception:
                        pass
            results = [self._failed_result(item, type(exc).__name__) for item in items]
        else:
            results = [
                {
                    "id": item.id,
                    "title": item.title,
                    "state": "deleted",
                    "descendantCount": item.descendant_count,
                    "actualBytes": item.size,
                    "error": "",
                }
                for item in items
            ]
        return self._finish_execute(
            items,
            results,
            request_id=request_id,
        )

    @staticmethod
    def _failed_result(item: SessionCleanupItem, error: str = "") -> dict[str, object]:
        return {
            "id": item.id,
            "title": item.title,
            "state": "failed",
            "descendantCount": item.descendant_count,
            "actualBytes": 0,
            "error": error,
        }

    def _finish_execute(
        self,
        items: Sequence[SessionCleanupItem],
        results: Sequence[Mapping[str, object]],
        *,
        request_id: str,
        interrupted: bool = False,
    ) -> dict[str, object]:
        deleted = sum(row.get("state") == "deleted" for row in results)
        state = (
            "completed"
            if deleted == len(results)
            else ("partial" if deleted else "failed")
        )
        self._reload_after_execute()
        return self.mark_operation(
            request_id=request_id,
            action="execute",
            state=state,
            progress=100,
            results=results,
            selectedIds=[item.id for item in items],
            sessionCount=len(items),
            descendantCount=sum(item.descendant_count for item in items),
            deletedCount=deleted,
            failedCount=len(results) - deleted,
            actualBytes=sum(int(row["actualBytes"]) for row in results),
            interrupted=interrupted,
        )

    def _selected_items(
        self,
        item_ids: Sequence[str],
        revision: str,
    ) -> list[SessionCleanupItem]:
        normalized = tuple(dict.fromkeys(str(item_id or "") for item_id in item_ids))
        if not normalized or any(not item_id for item_id in normalized):
            raise SessionCleanupError("Session selection is empty or invalid.")
        if not self._revision or revision != self._revision:
            raise SessionCleanupError("Session inventory revision is stale.")
        try:
            items = [self._items[item_id] for item_id in normalized]
        except KeyError as exc:
            raise SessionCleanupError("Unknown session inventory item.") from exc
        if any(not item.selectable for item in items):
            raise SessionCleanupError("Session selection contains a protected item.")
        return items

    def _consume_confirmation(
        self,
        item_ids: Sequence[str],
        revision: str,
        token: str,
    ) -> list[SessionCleanupItem]:
        items = self._selected_items(item_ids, revision)
        confirmation = self._confirmations.pop(str(token or ""), None)
        if confirmation is None or confirmation.expires_at < float(self.clock()):
            raise SessionCleanupError("Confirmation token is missing or expired.")
        if confirmation.revision != revision or confirmation.item_ids != tuple(
            item.id for item in items
        ):
            raise SessionCleanupError(
                "Confirmation does not match the session selection."
            )
        return items

    def _protected_ids(
        self,
        provider: Callable[[], Iterable[str]],
    ) -> set[str]:
        try:
            values = provider()
        except Exception as exc:
            raise SessionCleanupError(
                f"Active session state could not be verified ({type(exc).__name__})."
            ) from exc
        return {canonical for value in values if (canonical := _canonical_uuid(value))}

    def _allowed_rollout_roots(self) -> tuple[Path, ...]:
        roots = [self.sessions_root]
        if self.sessions_root.name.casefold() == "sessions":
            roots.append(self.sessions_root.parent / "archived_sessions")
        return tuple(roots)

    def _load_state(
        self,
    ) -> tuple[
        dict[str, _ThreadRecord],
        dict[str, set[str]],
        dict[str, str],
        set[str],
        int,
    ]:
        if not self.state_db_path.is_file():
            raise SessionCleanupError("Codex state database is unavailable.")
        try:
            with closing(_read_only_connection(self.state_db_path)) as connection:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "threads" not in tables:
                    raise SessionCleanupError(
                        "Codex state database has no threads table."
                    )
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(threads)")
                }
                if not {"id", "rollout_path"}.issubset(columns):
                    raise SessionCleanupError("Codex thread schema is not recognized.")
                selected = [
                    name
                    for name in (
                        "id",
                        "rollout_path",
                        "title",
                        "cwd",
                        "archived",
                        "updated_at_ms",
                        "updated_at",
                        "recency_at_ms",
                    )
                    if name in columns
                ]
                rows = connection.execute(
                    f"SELECT {', '.join(selected)} FROM threads"
                ).fetchall()
                edge_rows = (
                    connection.execute(
                        "SELECT parent_thread_id, child_thread_id, status "
                        "FROM thread_spawn_edges"
                    ).fetchall()
                    if "thread_spawn_edges" in tables
                    else []
                )
        except sqlite3.Error as exc:
            raise SessionCleanupError(
                "Codex state database could not be read."
            ) from exc
        records: dict[str, _ThreadRecord] = {}
        unresolved = 0
        for row in rows:
            session_id = _canonical_uuid(row["id"])
            if not session_id:
                unresolved += 1
                continue
            values = {name: row[name] for name in selected}
            updated_at_ms = int(values.get("updated_at_ms") or 0)
            if updated_at_ms <= 0:
                updated_at_ms = int(values.get("recency_at_ms") or 0)
            if updated_at_ms <= 0:
                updated_at_ms = int(values.get("updated_at") or 0) * 1000
            records[session_id] = _ThreadRecord(
                session_id=session_id,
                rollout_path=_normalized_rollout_path(values.get("rollout_path")),
                title=str(values.get("title") or "").strip(),
                cwd=str(values.get("cwd") or "").strip(),
                archived=bool(values.get("archived")),
                updated_at_ms=max(0, updated_at_ms),
            )
        parents: dict[str, set[str]] = defaultdict(set)
        edge_states: dict[str, str] = {}
        unsafe_ids: set[str] = set()
        for row in edge_rows:
            parent = _canonical_uuid(row[0])
            child = _canonical_uuid(row[1])
            if not parent or not child:
                unresolved += 1
                if parent:
                    unsafe_ids.add(parent)
                if child:
                    unsafe_ids.add(child)
                continue
            parents[child].add(parent)
            edge_states[child] = str(row[2] or "").strip()
            if parent == child:
                unsafe_ids.add(child)
            if parent not in records:
                unsafe_ids.add(child)
            if child not in records:
                unsafe_ids.add(parent)
        return records, parents, edge_states, unsafe_ids, unresolved

    @staticmethod
    def _root_ids(
        records: Mapping[str, _ThreadRecord],
        parents: Mapping[str, set[str]],
        unsafe_ids: Iterable[str] = (),
    ) -> tuple[list[str], set[str], int]:
        roots = [session_id for session_id in records if not parents.get(session_id)]
        unsafe = set(unsafe_ids)
        unsafe_roots: set[str] = set()
        unresolved_nodes: set[str] = set()

        for start_id in records:
            pending = [start_id]
            visited: set[str] = set()
            lineage_roots: set[str] = set()
            valid = True
            while pending:
                session_id = pending.pop()
                if session_id in visited:
                    valid = False
                    continue
                visited.add(session_id)
                linked = parents.get(session_id, set())
                if not linked:
                    lineage_roots.add(session_id)
                    continue
                if len(linked) != 1:
                    valid = False
                for parent in linked:
                    if parent not in records:
                        valid = False
                        continue
                    pending.append(parent)
            if len(lineage_roots) != 1:
                valid = False
            if not valid or start_id in unsafe:
                unresolved_nodes.add(start_id)
                unsafe_roots.update(lineage_roots)

        return roots, unsafe_roots, len(unresolved_nodes)

    @staticmethod
    def _descendants(
        root_id: str,
        records: Mapping[str, _ThreadRecord],
        parents: Mapping[str, set[str]],
    ) -> list[str]:
        children: dict[str, list[str]] = defaultdict(list)
        for child, linked in parents.items():
            if len(linked) != 1 or child not in records:
                continue
            parent = next(iter(linked))
            if parent in records:
                children[parent].append(child)
        result: list[str] = []
        pending = list(children.get(root_id, ()))
        seen = {root_id}
        while pending:
            session_id = pending.pop()
            if session_id in seen:
                continue
            seen.add(session_id)
            result.append(session_id)
            pending.extend(children.get(session_id, ()))
        return sorted(result)

    def _session_index_metadata(
        self,
        *,
        strict: bool = False,
    ) -> tuple[dict[str, str], set[str]]:
        if not self.session_index_path.is_file():
            return {}, set()
        titles: dict[str, str] = {}
        session_ids: set[str] = set()
        try:
            with self.session_index_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        if strict:
                            raise SessionCleanupError(
                                "Codex session index could not be verified."
                            )
                        continue
                    if not isinstance(payload, Mapping):
                        if strict:
                            raise SessionCleanupError(
                                "Codex session index could not be verified."
                            )
                        continue
                    session_id = _canonical_uuid(payload.get("id"))
                    title = str(payload.get("thread_name") or "").strip()
                    if session_id:
                        session_ids.add(session_id)
                        if title:
                            titles[session_id] = title
        except OSError as exc:
            if strict:
                raise SessionCleanupError(
                    "Codex session index could not be verified."
                ) from exc
            return {}, set()
        return titles, session_ids

    def _session_index_titles(self) -> dict[str, str]:
        return self._session_index_metadata()[0]

    def _session_index_ids(self, *, strict: bool = False) -> set[str]:
        return self._session_index_metadata(strict=strict)[1]

    def _staging_parent(self) -> Path:
        return self.sessions_root.parent / ".hud-session-delete-staging"

    def _cleanup_orphaned_staging(self) -> None:
        """Remove staging directories left by interrupted deletions.

        Each deletion uses a fresh random subdirectory, so any existing
        staging belongs to a previous attempt that was interrupted by crash
        or power loss.  This is safe to call at any time and never raises.
        """
        parent = self._staging_parent()
        if not parent.is_dir():
            return
        for child in parent.iterdir():
            if child.is_dir():
                self._remove_staging_best_effort(child, parent)
        try:
            parent.rmdir()
        except OSError:
            pass

    def _remove_staging_best_effort(
        self,
        staging: Path,
        staging_parent: Path,
    ) -> None:
        """Remove a staging directory with retries, then best-effort cleanup.

        After the database transaction is committed the rollout files in
        staging are already logically deleted.  A transient Windows file
        lock (antivirus, search indexer) must not turn that into a
        user-visible failure, so we retry rmtree with short backoff and
        then fall back to file-by-file removal.  Any residue is reclaimed
        by ``_cleanup_orphaned_staging`` on the next scan.
        """
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                shutil.rmtree(staging)
                last_error = None
                break
            except OSError as exc:
                last_error = exc
                if attempt < 2:
                    self._sleep(0.2 * (attempt + 1))
        if last_error is not None:
            # Fall back to removing individual files to minimize residue.
            try:
                for child in staging.iterdir():
                    try:
                        if child.is_file():
                            child.unlink()
                        elif child.is_dir():
                            shutil.rmtree(child)
                    except OSError:
                        pass
            except OSError:
                pass
            # An emptied directory can often be removed via rmdir even when
            # rmtree failed due to a transient Windows file handle.
            try:
                staging.rmdir()
            except OSError:
                pass
        try:
            staging_parent.rmdir()
        except OSError:
            pass

    def _delete_local_batch(self, items: Sequence[SessionCleanupItem]) -> None:
        session_ids = tuple(
            dict.fromkeys(
                session_id
                for item in items
                for session_id in (item._session_id, *item._descendant_ids)
            )
        )
        rollout_paths = tuple(
            dict.fromkeys(path for item in items for path in item._rollout_paths)
        )
        if not session_ids or not rollout_paths:
            raise SessionCleanupError("Session deletion target is incomplete.")
        retained_index_lines: list[str] | None = None
        if self.session_index_path.is_file():
            try:
                retained_index_lines = []
                with self.session_index_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            retained_index_lines.append(line)
                            continue
                        payload = json.loads(line)
                        if not isinstance(payload, Mapping):
                            raise SessionCleanupError(
                                "Codex session index could not be verified."
                            )
                        if _canonical_uuid(payload.get("id")) not in session_ids:
                            retained_index_lines.append(line)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise SessionCleanupError(
                    "Codex session index could not be verified."
                ) from exc
        staging_parent = self.sessions_root.parent / ".hud-session-delete-staging"
        # Remove staging left by a previous interrupted deletion before creating
        # a fresh one.  This bounds disk residue from crashes or power loss.
        self._cleanup_orphaned_staging()
        staging = staging_parent / secrets.token_hex(12)
        moved: list[tuple[Path, Path]] = []
        staged_index: Path | None = None
        database_committed = False
        try:
            staging.mkdir(parents=True, exist_ok=False)
            for index, path in enumerate(rollout_paths, start=1):
                staged_path = staging / f"{index:04d}-{path.name}"
                os.replace(path, staged_path)
                moved.append((path, staged_path))
            if retained_index_lines is not None:
                staged_index = staging / "session_index.jsonl"
                os.replace(self.session_index_path, staged_index)
                self.session_index_path.write_text(
                    "".join(retained_index_lines),
                    encoding="utf-8",
                )
            with closing(_read_write_connection(self.state_db_path)) as connection:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                columns = {
                    table: {
                        str(row[1])
                        for row in connection.execute(
                            f'PRAGMA table_info("{table.replace(chr(34), chr(34) * 2)}")'
                        )
                    }
                    for table in tables
                }
                placeholders = ",".join("?" for _ in session_ids)
                with connection:
                    for table, column in (
                        ("thread_dynamic_tools", "thread_id"),
                        ("thread_goals", "thread_id"),
                        ("stage1_outputs", "thread_id"),
                    ):
                        if table in tables and column in columns[table]:
                            connection.execute(
                                f"DELETE FROM {table} WHERE {column} IN ({placeholders})",
                                session_ids,
                            )
                    if (
                        "thread_spawn_edges" in tables
                        and {"parent_thread_id", "child_thread_id"}
                        <= columns["thread_spawn_edges"]
                    ):
                        connection.execute(
                            "DELETE FROM thread_spawn_edges "
                            f"WHERE parent_thread_id IN ({placeholders}) "
                            f"OR child_thread_id IN ({placeholders})",
                            (*session_ids, *session_ids),
                        )
                    if (
                        "agent_job_items" in tables
                        and "assigned_thread_id" in columns["agent_job_items"]
                    ):
                        connection.execute(
                            "UPDATE agent_job_items SET assigned_thread_id = NULL "
                            f"WHERE assigned_thread_id IN ({placeholders})",
                            session_ids,
                        )
                    connection.execute(
                        f"DELETE FROM threads WHERE id IN ({placeholders})",
                        session_ids,
                    )
            database_committed = True
            # The database transaction is committed.  Staging cleanup is
            # best-effort: a transient file lock (Windows AV, indexer) must
            # not turn a successful deletion into a user-visible failure.
            # Retry with backoff, then fall back to file-by-file removal.
            self._remove_staging_best_effort(staging, staging_parent)
        except (OSError, sqlite3.Error) as exc:
            if not database_committed:
                if staged_index is not None:
                    try:
                        self.session_index_path.unlink(missing_ok=True)
                        if staged_index.exists():
                            os.replace(staged_index, self.session_index_path)
                    except OSError:
                        pass
                for original_path, staged_path in reversed(moved):
                    try:
                        if staged_path.exists() and not original_path.exists():
                            os.replace(staged_path, original_path)
                    except OSError:
                        pass
                try:
                    staging.rmdir()
                    staging_parent.rmdir()
                except OSError:
                    pass
                raise SessionCleanupError(
                    "Local session deletion transaction failed."
                ) from exc
            # database_committed is True: the deletion already succeeded.
            # Any remaining staging will be reclaimed by the next scan.
            pass

    def _revalidate_batch(
        self,
        items: Sequence[SessionCleanupItem],
        *,
        capability: SessionDeleteCapability,
    ) -> None:
        if not capability.available:
            raise SessionCleanupError(
                capability.reason or "Codex local session store is unavailable."
            )
        records, parents, edge_states, unsafe_ids, _unresolved = self._load_state()
        current_ids = self._protected_ids(self.current_session_ids)
        active_ids = self._protected_ids(self.active_session_ids)
        roots, unsafe_roots, _graph_unresolved = self._root_ids(
            records,
            parents,
            unsafe_ids,
        )
        allowed_roots = self._allowed_rollout_roots()
        for item in items:
            family = (item._session_id, *item._descendant_ids)
            if any(session_id not in records for session_id in family):
                raise SessionCleanupError("Session state changed after scanning.")
            if set(family) & current_ids:
                raise SessionCleanupError("The current session cannot be deleted.")
            if set(family) & active_ids or any(
                edge_states.get(session_id, "").casefold() in _ACTIVE_EDGE_STATES
                for session_id in item._descendant_ids
            ):
                raise SessionCleanupError("The session tree still has active work.")
            if item._session_id not in roots or item._session_id in unsafe_roots:
                raise SessionCleanupError(
                    "The session spawn relation changed after scanning."
                )
            current_descendants = tuple(
                self._descendants(item._session_id, records, parents)
            )
            if current_descendants != item._descendant_ids:
                raise SessionCleanupError("Session spawn tree changed after scanning.")
            current_paths = tuple(
                records[session_id].rollout_path
                for session_id in family
                if records[session_id].rollout_path is not None
            )
            if current_paths != item._rollout_paths:
                raise SessionCleanupError(
                    "Session rollout mapping changed after scanning."
                )
            if any(
                not _path_under(path, allowed_roots) or not path.is_file()
                for path in current_paths
            ):
                raise SessionCleanupError("Session rollout mapping is no longer valid.")

    def _revalidate_pending_activity(
        self,
        items: Sequence[SessionCleanupItem],
    ) -> None:
        current_ids = self._protected_ids(self.current_session_ids)
        active_ids = self._protected_ids(self.active_session_ids)
        for item in items:
            family = {item._session_id, *item._descendant_ids}
            if family & current_ids:
                raise SessionCleanupError("The current session cannot be deleted.")
            if family & active_ids:
                raise SessionCleanupError("The session tree still has active work.")

    def _verify_deleted_batch(self, items: Sequence[SessionCleanupItem]) -> None:
        family = {
            session_id
            for item in items
            for session_id in (item._session_id, *item._descendant_ids)
        }
        rollout_paths = [path for item in items for path in item._rollout_paths]
        try:
            records, _parents, _states, _unsafe_ids, _unresolved = self._load_state()
        except SessionCleanupError as exc:
            if not self.state_db_path.exists():
                raise SessionCleanupError(
                    "Codex state database disappeared after deletion."
                ) from exc
            raise
        if family & set(records):
            raise SessionCleanupError("Codex state still contains the deleted session.")
        if any(path.exists() for path in rollout_paths):
            raise SessionCleanupError("A deleted session rollout still exists.")
        remaining_index_ids = self._session_index_ids(strict=True)
        if family & remaining_index_ids:
            raise SessionCleanupError("Codex session index still contains the session.")

    def _reload_after_execute(self) -> None:
        try:
            records, parents, edge_states, unsafe_ids, unresolved = self._load_state()
        except SessionCleanupError:
            return
        remaining_session_ids = set(records)
        self._items = {
            item_id: item
            for item_id, item in self._items.items()
            if item._session_id in remaining_session_ids
        }
        remaining_ids = tuple(item._session_id for item in self._items.values())
        try:
            self._search_index.remove_missing(remaining_ids)
        except (OSError, sqlite3.Error):
            pass
        self._search_state = {
            **self._search_state,
            "state": "idle",
            "matches": [],
            "matchKinds": [],
            "revision": self._revision,
        }
        _roots, _unsafe_roots, graph_unresolved = self._root_ids(
            records,
            parents,
            unsafe_ids,
        )
        self._unresolved_count = unresolved + graph_unresolved
        del edge_states


__all__ = [
    "SessionCleanupError",
    "SessionCleanupItem",
    "SessionCleanupManager",
    "SessionDeleteCapability",
]
