"""Small native file-change watcher used by renderer mode."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import ctypes
import logging
import os
from pathlib import Path
import select
import sys
import threading
from typing import Any

from ..usage_contributions import iter_usage_jsonl_files, usage_jsonl_stat_tokens


FileChangeCallback = Callable[[set[str], set[Path]], None]
_LOGGER = logging.getLogger("codex_usage_hud.file_watcher")
_LOGGER.addHandler(logging.NullHandler())
_OVERFLOW_REASON = "file_watcher.overflow"


@dataclass(frozen=True)
class FileWatchSpec:
    """A path watched for renderer invalidation."""

    path: Path
    reason: str
    kind: str = "file"
    recursive: bool = False
    suffixes: tuple[str, ...] = ()

    @classmethod
    def file(cls, path: Path, reason: str) -> "FileWatchSpec":
        return cls(path=Path(path), reason=reason, kind="file")

    @classmethod
    def tree(
        cls,
        path: Path,
        reason: str,
        *,
        suffixes: Iterable[str] = (),
    ) -> "FileWatchSpec":
        return cls(
            path=Path(path),
            reason=reason,
            kind="tree",
            recursive=True,
            suffixes=tuple(str(item).lower() for item in suffixes),
        )


class FileChangeWatcher:
    """Watch a compact set of files/directories with native events when possible."""

    def __init__(
        self,
        callback: FileChangeCallback,
        *,
        fallback_poll_seconds: float = 5.0,
        force_polling: bool = False,
    ) -> None:
        self._callback = callback
        self._fallback_poll_seconds = max(0.05, float(fallback_poll_seconds))
        self._force_polling = bool(force_polling)
        self._lock = threading.Lock()
        self._specs: tuple[FileWatchSpec, ...] = ()
        self._stop_event = threading.Event()
        self._workers: list[_BaseWatchWorker] = []
        self._event_driven = False
        self._polling_cause = ""

    @property
    def event_driven(self) -> bool:
        return self._event_driven

    @property
    def polling_cause(self) -> str:
        return self._polling_cause

    def update(self, specs: Iterable[FileWatchSpec]) -> None:
        next_specs = _normalize_specs(specs)
        with self._lock:
            if next_specs == self._specs:
                return
            self._stop_locked()
            self._specs = next_specs
            if not self._specs:
                return
            self._stop_event = threading.Event()
            self._workers, self._event_driven, self._polling_cause = self._build_workers_locked(
                self._specs,
                self._stop_event,
            )
            _LOGGER.info(
                "file_watcher_started mode=%s cause=%s workers=%s specs=%s reasons=%s",
                "native" if self._event_driven else "polling",
                self._polling_cause or "native",
                ",".join(type(worker).__name__ for worker in self._workers),
                len(self._specs),
                ",".join(sorted({spec.reason for spec in self._specs})),
            )
            for worker in self._workers:
                worker.start()

    def close(self) -> None:
        with self._lock:
            self._stop_locked()
            self._specs = ()

    def _stop_locked(self) -> None:
        self._event_driven = False
        self._polling_cause = ""
        self._stop_event.set()
        workers = self._workers
        self._workers = []
        for worker in workers:
            worker.close()
        for worker in workers:
            worker.join(timeout=1.0)

    def _build_workers_locked(
        self,
        specs: tuple[FileWatchSpec, ...],
        stop_event: threading.Event,
    ) -> tuple[list["_BaseWatchWorker"], bool, str]:
        polling_cause = "platform_unsupported"
        if self._force_polling:
            polling_cause = "force_polling"
        else:
            if sys.platform.startswith("win"):
                workers = _build_windows_workers(specs, stop_event, self._callback)
                if workers:
                    return workers, True, ""
                polling_cause = "native_unavailable"
            elif sys.platform == "darwin":
                if _needs_recursive_tree_polling(specs):
                    polling_cause = "platform_recursive_tree_unsupported"
                else:
                    worker = _build_kqueue_worker(specs, stop_event, self._callback)
                    if worker is not None:
                        return [worker], True, ""
                    polling_cause = "native_unavailable"
        return [
            _PollingWorker(
                specs,
                stop_event,
                self._callback,
                self._fallback_poll_seconds,
            )
        ], False, polling_cause


class _BaseWatchWorker:
    def start(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def join(self, timeout: float | None = None) -> None:
        raise NotImplementedError


class _ThreadWorker(_BaseWatchWorker):
    def __init__(self, name: str) -> None:
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        return

    def join(self, timeout: float | None = None) -> None:
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        raise NotImplementedError


class _PollingWorker(_ThreadWorker):
    def __init__(
        self,
        specs: tuple[FileWatchSpec, ...],
        stop_event: threading.Event,
        callback: FileChangeCallback,
        poll_seconds: float,
    ) -> None:
        super().__init__("codex-hud-file-poll")
        self._specs = specs
        self._stop_event = stop_event
        self._callback = callback
        self._poll_seconds = poll_seconds
        self._last_signature = _poll_signature(specs)

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_seconds):
            signature = _poll_signature(self._specs)
            if signature == self._last_signature:
                continue
            previous = self._last_signature
            self._last_signature = signature
            reasons = {
                reason
                for reason, token in signature.items()
                if previous.get(reason) != token
            }
            if reasons:
                self._callback(
                    reasons,
                    _changed_paths_from_poll_signatures(previous, signature),
                )


class _WindowsDirectoryWorker(_ThreadWorker):
    def __init__(
        self,
        directory: Path,
        recursive: bool,
        specs: tuple[FileWatchSpec, ...],
        stop_event: threading.Event,
        callback: FileChangeCallback,
    ) -> None:
        super().__init__("codex-hud-file-watch-win")
        self._directory = directory
        self._recursive = recursive
        self._specs = specs
        self._stop_event = stop_event
        self._callback = callback
        self._handle: int | None = None

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            ctypes.windll.kernel32.CancelIoEx(ctypes.c_void_p(handle), None)
        except Exception:
            pass
        try:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
        except Exception:
            pass

    def _run(self) -> None:
        handle = _open_windows_directory(self._directory)
        if handle is None:
            _LOGGER.warning("file_watcher_windows_open_failed directory=%s", self._directory)
            return
        self._handle = handle
        buffer = ctypes.create_string_buffer(64 * 1024)
        bytes_returned = ctypes.c_uint32(0)
        notify_filter = (
            _FILE_NOTIFY_CHANGE_FILE_NAME
            | _FILE_NOTIFY_CHANGE_DIR_NAME
            | _FILE_NOTIFY_CHANGE_LAST_WRITE
            | _FILE_NOTIFY_CHANGE_SIZE
            | _FILE_NOTIFY_CHANGE_CREATION
        )
        try:
            while not self._stop_event.is_set():
                ok = ctypes.windll.kernel32.ReadDirectoryChangesW(
                    ctypes.c_void_p(handle),
                    ctypes.byref(buffer),
                    ctypes.sizeof(buffer),
                    bool(self._recursive),
                    notify_filter,
                    ctypes.byref(bytes_returned),
                    None,
                    None,
                )
                if not ok or self._stop_event.is_set():
                    if not ok and not self._stop_event.is_set():
                        _LOGGER.warning(
                            "file_watcher_windows_read_failed directory=%s winerror=%s",
                            self._directory, ctypes.windll.kernel32.GetLastError(),
                        )
                    break
                if bytes_returned.value == 0:
                    _LOGGER.warning(
                        "file_watcher_windows_overflow directory=%s recursive=%s",
                        self._directory,
                        self._recursive,
                    )
                    self._emit_overflow_reconciliation()
                    continue
                changed = _parse_windows_notifications(self._directory, buffer)
                self._emit_matching(changed)
        finally:
            _LOGGER.info(
                "file_watcher_windows_exiting directory=%s stop_requested=%s",
                self._directory, self._stop_event.is_set(),
            )
            self.close()

    def _emit_overflow_reconciliation(self) -> None:
        reasons = {spec.reason for spec in self._specs}
        paths: set[Path] = set()
        for spec in self._specs:
            paths.update(_reconciliation_paths_for_spec(spec))
        if reasons:
            self._callback(reasons | {_OVERFLOW_REASON}, paths)

    def _emit_matching(self, changed_paths: set[Path]) -> None:
        reasons: set[str] = set()
        matched: set[Path] = set()
        for path in changed_paths:
            for spec in self._specs:
                if _spec_matches_path(spec, path):
                    reasons.add(spec.reason)
                    matched.add(path)
        if reasons:
            self._callback(reasons, matched)


class _KqueueWorker(_ThreadWorker):
    def __init__(
        self,
        specs: tuple[FileWatchSpec, ...],
        stop_event: threading.Event,
        callback: FileChangeCallback,
    ) -> None:
        super().__init__("codex-hud-file-watch-kqueue")
        self._specs = specs
        self._stop_event = stop_event
        self._callback = callback
        self._kqueue: Any | None = None
        self._fds: dict[int, FileWatchSpec] = {}

    def close(self) -> None:
        self._stop_event.set()
        kqueue = self._kqueue
        if kqueue is not None:
            try:
                kqueue.close()
            except OSError:
                pass
        for fd in list(self._fds):
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds.clear()

    def _run(self) -> None:
        if not hasattr(select, "kqueue") or not hasattr(select, "kevent"):
            return
        try:
            self._kqueue = select.kqueue()
        except OSError:
            return
        changes = []
        for spec in self._specs:
            watch_path = spec.path
            if spec.kind == "file" and not watch_path.exists():
                watch_path = spec.path.parent
            try:
                fd = os.open(watch_path, os.O_RDONLY)
            except OSError:
                continue
            self._fds[fd] = spec
            changes.append(
                select.kevent(
                    fd,
                    filter=select.KQ_FILTER_VNODE,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
                    fflags=(
                        select.KQ_NOTE_WRITE
                        | select.KQ_NOTE_EXTEND
                        | select.KQ_NOTE_ATTRIB
                        | select.KQ_NOTE_RENAME
                        | select.KQ_NOTE_DELETE
                    ),
                )
            )
        if not changes:
            self.close()
            return
        try:
            self._kqueue.control(changes, 0, 0)
            while not self._stop_event.is_set():
                events = self._kqueue.control(None, 16, 0.5)
                reasons = {
                    self._fds[event.ident].reason
                    for event in events
                    if event.ident in self._fds
                }
                if reasons:
                    self._callback(reasons, set())
        except OSError:
            return
        finally:
            self.close()


def _normalize_specs(specs: Iterable[FileWatchSpec]) -> tuple[FileWatchSpec, ...]:
    normalized: dict[tuple[object, ...], FileWatchSpec] = {}
    for spec in specs:
        path = Path(spec.path).expanduser()
        reason = str(spec.reason or "").strip()
        if not reason:
            continue
        kind = spec.kind if spec.kind in {"file", "tree"} else "file"
        item = FileWatchSpec(
            path=path,
            reason=reason,
            kind=kind,
            recursive=bool(spec.recursive or kind == "tree"),
            suffixes=tuple(str(suffix).lower() for suffix in spec.suffixes),
        )
        key = (
            _path_key(item.path),
            item.reason,
            item.kind,
            item.recursive,
            item.suffixes,
        )
        normalized[key] = item
    return tuple(normalized[key] for key in sorted(normalized))


def _poll_signature(specs: tuple[FileWatchSpec, ...]) -> dict[str, tuple[object, ...]]:
    tokens: dict[str, list[object]] = {}
    for spec in specs:
        tokens.setdefault(spec.reason, []).append(_poll_token_for_spec(spec))
    return {reason: tuple(value) for reason, value in tokens.items()}


def _iter_stat_tokens(value: object) -> Iterable[tuple[str, int, int]]:
    if (
        isinstance(value, tuple)
        and len(value) == 3
        and isinstance(value[0], str)
        and isinstance(value[1], int)
        and isinstance(value[2], int)
    ):
        yield value
        return
    if isinstance(value, tuple):
        for item in value:
            yield from _iter_stat_tokens(item)


def _stat_token_map(value: object) -> dict[str, tuple[str, int, int]]:
    return {token[0]: token for token in _iter_stat_tokens(value)}


def _changed_paths_from_poll_signatures(
    previous: dict[str, tuple[object, ...]],
    current: dict[str, tuple[object, ...]],
) -> set[Path]:
    changed: set[Path] = set()
    for reason in set(previous) | set(current):
        previous_tokens = _stat_token_map(previous.get(reason, ()))
        current_tokens = _stat_token_map(current.get(reason, ()))
        for key in set(previous_tokens) | set(current_tokens):
            if previous_tokens.get(key) != current_tokens.get(key):
                changed.add(Path(key))
    return changed


def _poll_token_for_spec(spec: FileWatchSpec) -> object:
    if spec.kind == "tree":
        return _tree_token(spec.path, spec.suffixes)
    paths = [spec.path]
    if spec.path.suffix.lower() in {".sqlite", ".db"}:
        paths.extend(
            [
                spec.path.with_name(spec.path.name + "-wal"),
                spec.path.with_name(spec.path.name + "-shm"),
            ]
        )
    return tuple(_stat_token(path) for path in paths)


def _reconciliation_paths_for_spec(spec: FileWatchSpec) -> set[Path]:
    if spec.kind == "tree":
        return _tree_paths(spec.path, spec.suffixes)
    paths = {spec.path}
    if spec.path.suffix.lower() in {".sqlite", ".db"}:
        paths.update(
            {
                spec.path.with_name(spec.path.name + "-wal"),
                spec.path.with_name(spec.path.name + "-shm"),
            }
        )
    return paths


def _tree_paths(path: Path, suffixes: tuple[str, ...]) -> set[Path]:
    if not path.exists():
        return set()
    values: set[Path] = set()
    try:
        candidates = (
            iter_usage_jsonl_files(path)
            if suffixes == (".jsonl",)
            else path.rglob("*")
        )
        for candidate in candidates:
            if suffixes and candidate.suffix.lower() not in suffixes:
                continue
            if candidate.is_file():
                values.add(candidate)
    except OSError:
        return set()
    return values


def _tree_token(path: Path, suffixes: tuple[str, ...]) -> tuple[tuple[str, int, int], ...]:
    if not path.exists():
        return ()
    values: list[tuple[str, int, int]] = []
    try:
        if suffixes == (".jsonl",):
            return tuple(
                sorted(
                    (
                        os.path.normcase(os.path.normpath(str(candidate))),
                        mtime_ns,
                        size,
                    )
                    for candidate, mtime_ns, size in usage_jsonl_stat_tokens(path)
                )
            )
        iterator = (
            iter_usage_jsonl_files(path)
            if suffixes == (".jsonl",)
            else path.rglob("*")
        )
        for candidate in iterator:
            if suffixes and candidate.suffix.lower() not in suffixes:
                continue
            if not candidate.is_file():
                continue
            stat = candidate.stat()
            values.append(
                (
                    _path_key(candidate),
                    int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
                    int(stat.st_size),
                )
            )
    except OSError:
        return ()
    values.sort()
    return tuple(values)


def _stat_token(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (_path_key(path), 0, 0)
    return (
        _path_key(path),
        int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
        int(stat.st_size),
    )


def _path_key(path: Path) -> str:
    try:
        # Keep the lexical spelling supplied by the watcher.  Resolving here
        # turns macOS's /var symlink into /private/var (and can similarly
        # canonicalize Windows junctions), so a changed path reconstructed
        # from the token no longer compares equal to the caller's Path.
        text = os.path.abspath(os.path.normpath(str(path)))
    except OSError:
        text = str(path)
    return os.path.normcase(text)


def _spec_matches_path(spec: FileWatchSpec, changed_path: Path) -> bool:
    if spec.kind == "tree":
        if spec.suffixes and changed_path.suffix.lower() not in spec.suffixes:
            return False
        try:
            changed_path.resolve(strict=False).relative_to(spec.path.resolve(strict=False))
            return True
        except ValueError:
            return False
        except OSError:
            return False
    if _path_key(changed_path) == _path_key(spec.path):
        return True
    if spec.path.suffix.lower() in {".sqlite", ".db"}:
        return changed_path.parent == spec.path.parent and changed_path.name in {
            spec.path.name + "-wal",
            spec.path.name + "-shm",
        }
    return False


def _build_windows_workers(
    specs: tuple[FileWatchSpec, ...],
    stop_event: threading.Event,
    callback: FileChangeCallback,
) -> list[_WindowsDirectoryWorker]:
    if not hasattr(ctypes, "windll"):
        return []
    grouped: dict[tuple[str, bool], tuple[Path, bool, list[FileWatchSpec]]] = {}
    for spec in specs:
        directory = spec.path if spec.kind == "tree" else spec.path.parent
        if not directory.exists() or not directory.is_dir():
            continue
        key = (_path_key(directory), bool(spec.recursive))
        if key not in grouped:
            grouped[key] = (directory, bool(spec.recursive), [])
        grouped[key][2].append(spec)
    return [
        _WindowsDirectoryWorker(
            directory,
            recursive,
            tuple(directory_specs),
            stop_event,
            callback,
        )
        for directory, recursive, directory_specs in grouped.values()
    ]


def _build_kqueue_worker(
    specs: tuple[FileWatchSpec, ...],
    stop_event: threading.Event,
    callback: FileChangeCallback,
) -> _KqueueWorker | None:
    if not hasattr(select, "kqueue") or not hasattr(select, "kevent"):
        return None
    return _KqueueWorker(specs, stop_event, callback)


def _needs_recursive_tree_polling(specs: tuple[FileWatchSpec, ...]) -> bool:
    """Return whether native kqueue would miss recursive tree changes."""
    return any(spec.kind == "tree" and spec.recursive for spec in specs)


if sys.platform.startswith("win"):
    _FILE_LIST_DIRECTORY = 0x0001
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _FILE_SHARE_DELETE = 0x00000004
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_NOTIFY_CHANGE_FILE_NAME = 0x00000001
    _FILE_NOTIFY_CHANGE_DIR_NAME = 0x00000002
    _FILE_NOTIFY_CHANGE_LAST_WRITE = 0x00000010
    _FILE_NOTIFY_CHANGE_SIZE = 0x00000008
    _FILE_NOTIFY_CHANGE_CREATION = 0x00000040
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
else:
    _FILE_LIST_DIRECTORY = 0
    _FILE_SHARE_READ = 0
    _FILE_SHARE_WRITE = 0
    _FILE_SHARE_DELETE = 0
    _OPEN_EXISTING = 0
    _FILE_FLAG_BACKUP_SEMANTICS = 0
    _FILE_NOTIFY_CHANGE_FILE_NAME = 0
    _FILE_NOTIFY_CHANGE_DIR_NAME = 0
    _FILE_NOTIFY_CHANGE_LAST_WRITE = 0
    _FILE_NOTIFY_CHANGE_SIZE = 0
    _FILE_NOTIFY_CHANGE_CREATION = 0
    _INVALID_HANDLE_VALUE = None


def _open_windows_directory(path: Path) -> int | None:
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = kernel32.CreateFileW(
        str(path),
        _FILE_LIST_DIRECTORY,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if not handle or handle == _INVALID_HANDLE_VALUE:
        return None
    return int(handle)


def _parse_windows_notifications(directory: Path, buffer: Any) -> set[Path]:
    changed: set[Path] = set()
    offset = 0
    raw = buffer.raw
    while offset + 12 <= len(raw):
        next_offset = int.from_bytes(raw[offset : offset + 4], "little")
        name_length = int.from_bytes(raw[offset + 8 : offset + 12], "little")
        name_start = offset + 12
        name_end = name_start + name_length
        if name_end > len(raw):
            break
        try:
            name = raw[name_start:name_end].decode("utf-16-le", errors="ignore")
        except UnicodeDecodeError:
            name = ""
        if name:
            changed.add(directory / name)
        if next_offset <= 0:
            break
        offset += next_offset
    return changed


__all__ = ["FileChangeWatcher", "FileWatchSpec"]
