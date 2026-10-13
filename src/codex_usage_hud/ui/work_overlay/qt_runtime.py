"""PySide6 work-overlay runtime owner."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

from ... import overlay_ipc
from .constants import (
    WORK_OVERLAY_HELPER_HEARTBEAT_MS,
    WORK_OVERLAY_POINTER_SYNC_MS,
    WORK_OVERLAY_STATE_READ_RETRY_MS,
)


def _work_overlay_command_path(state_path: Path) -> Path:
    return overlay_ipc.command_path(state_path)


def _work_overlay_heartbeat_path(state_path: Path) -> Path:
    return overlay_ipc.heartbeat_path(state_path)


def _refresh_overlay_state_from_event(
    *,
    read_state: Callable[[], bool],
    retry_active: Callable[[], bool],
    start_retry: Callable[[int], None],
    stop_retry: Callable[[], None],
    heartbeat_active: Callable[[], bool],
    start_heartbeat: Callable[[], None],
    schedule_stale: Callable[[], None],
) -> bool:
    """Apply the deterministic retry/heartbeat policy after a watcher wake."""
    if not read_state():
        if not retry_active():
            start_retry(WORK_OVERLAY_STATE_READ_RETRY_MS)
        return False
    stop_retry()
    if not heartbeat_active():
        start_heartbeat()
    schedule_stale()
    return True

def run_work_overlay_helper_qt(
    state_file: str | Path,
    *,
    process_exists: Callable[[int], bool],
    owner_pid_from_path: Callable[[Path], int | None],
    item_limit: int,
    stale_seconds: float,
    overlay_alpha: float,
    hover_alpha: float,
    header_title_limit: int,
) -> int:
    try:
        from PySide6.QtCore import QFileSystemWatcher, QTimer
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover - depends on local runtime
        raise RuntimeError(
            f"PySide6 desktop work overlay import failed: {type(exc).__name__}: {exc}"
        ) from exc

    from .qt_window import OverlayWindow

    path = Path(str(state_file)).expanduser()
    def read_state() -> dict[str, object] | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    app = QApplication.instance() or QApplication([Path(sys.argv[0]).name or "codex-hud-overlay"])
    app.setQuitOnLastWindowClosed(False)
    owner_pid = owner_pid_from_path(path)
    heartbeat_path = _work_overlay_heartbeat_path(path)

    overlay = OverlayWindow(
        app=app,
        path=path,
        read_state=read_state,
        owner_pid=owner_pid,
        process_exists=process_exists,
        stale_seconds=stale_seconds,
        item_limit=item_limit,
        overlay_alpha=overlay_alpha,
        hover_alpha=hover_alpha,
        heartbeat_path=heartbeat_path,
        header_title_limit=header_title_limit,
    )
    state_watcher = QFileSystemWatcher()
    state_stale_timer = QTimer()
    state_stale_timer.setSingleShot(True)
    state_read_retry_timer = QTimer()
    state_read_retry_timer.setSingleShot(True)
    helper_heartbeat_timer = QTimer()
    helper_heartbeat_timer.timeout.connect(overlay.emit_helper_heartbeat)

    def watch_state_path() -> None:
        parent = str(path.parent)
        if parent and parent not in state_watcher.directories():
            try:
                state_watcher.addPath(parent)
            except RuntimeError:
                return
        file_path = str(path)
        if path.exists() and file_path not in state_watcher.files():
            try:
                state_watcher.addPath(file_path)
            except RuntimeError:
                return

    def schedule_stale_check() -> None:
        state_stale_timer.start(
            max(
                1000,
                int((max(0.1, float(stale_seconds)) + 0.25) * 1000),
            )
        )

    def refresh_state_from_watcher(*_args: object) -> None:
        watch_state_path()
        _refresh_overlay_state_from_event(
            read_state=overlay.poll_state,
            retry_active=state_read_retry_timer.isActive,
            start_retry=state_read_retry_timer.start,
            stop_retry=state_read_retry_timer.stop,
            heartbeat_active=helper_heartbeat_timer.isActive,
            start_heartbeat=lambda: (
                overlay.emit_helper_heartbeat(),
                helper_heartbeat_timer.start(WORK_OVERLAY_HELPER_HEARTBEAT_MS),
            ),
            schedule_stale=schedule_stale_check,
        )
        watch_state_path()

    state_watcher.fileChanged.connect(refresh_state_from_watcher)
    state_watcher.directoryChanged.connect(refresh_state_from_watcher)
    state_stale_timer.timeout.connect(refresh_state_from_watcher)
    state_read_retry_timer.timeout.connect(refresh_state_from_watcher)
    watch_state_path()

    pointer_timer = QTimer()
    pointer_timer.timeout.connect(overlay.sync_pointer_state)
    pointer_timer.start(WORK_OVERLAY_POINTER_SYNC_MS)

    refresh_state_from_watcher()
    overlay.sync_pointer_state()
    app.exec()
    pointer_timer.stop()
    helper_heartbeat_timer.stop()
    state_read_retry_timer.stop()
    state_stale_timer.stop()
    watched_files = state_watcher.files()
    if watched_files:
        state_watcher.removePaths(watched_files)
    watched_directories = state_watcher.directories()
    if watched_directories:
        state_watcher.removePaths(watched_directories)
    return 0

__all__ = [
    "run_work_overlay_helper_qt",
    "_refresh_overlay_state_from_event",
    "_work_overlay_command_path",
    "_work_overlay_heartbeat_path",
]
