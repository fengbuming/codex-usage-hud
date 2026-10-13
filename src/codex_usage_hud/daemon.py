"""Desktop process helpers for waking the HUD with the Codex client.

The implementation intentionally stays in the Python standard library. Windows
uses Toolhelp32 through ``ctypes``; macOS uses a conservative ``ps`` snapshot
fallback until a dependency-free native launch notification is available.
"""

from __future__ import annotations

import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol

DEFAULT_DAEMON_POLL_MS = 1000
DEFAULT_DAEMON_REPLACEMENT_WAIT_SECONDS = 30.0
MAX_DAEMON_POLL_MS = 5000
MIN_DAEMON_POLL_MS = 100

_TH32CS_SNAPPROCESS = 0x00000002
_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_MAX_PATH = 260
_MAX_PROCESS_IMAGE_PATH = 32768
_SW_HIDE = 0
_CODEX_APP_PATH_ENV = "CODEX_USAGE_HUD_CODEX_APP"
_LOGGER_NAME = "codex_usage_hud.daemon"
_logger = logging.getLogger(_LOGGER_NAME)
_logger.addHandler(logging.NullHandler())
_logging_configured = False


class ProcessListenerError(RuntimeError):
    """Raised when the daemon cannot inspect system processes safely."""


class DaemonState(str, Enum):
    """High-level daemon state for logging and tests."""

    WAITING_FOR_CODEX = "waiting_for_codex"
    HUD_RUNNING = "hud_running"
    FALLBACK = "fallback"
    EXITING = "exiting"


@dataclass(frozen=True)
class ProcessSnapshot:
    """Result of one Codex process scan."""

    found: bool
    pids: tuple[int, ...] = ()
    names: tuple[str, ...] = ()
    checked_at: float = 0.0
    error: str = ""

    @property
    def primary_pid(self) -> int | None:
        return self.pids[0] if self.pids else None


class ProcessListener(Protocol):
    """Small protocol used by the daemon state machine."""

    def snapshot(self) -> ProcessSnapshot:
        """Return a current process snapshot or raise ``ProcessListenerError``."""


class ProcessExitMonitor(Protocol):
    """Event-source style monitor for one already-detected process."""

    def is_running(self) -> bool | None:
        """Return process liveness, or None when the monitor cannot tell."""

    def close(self) -> None:
        """Release monitor resources."""


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * _MAX_PATH),
    ]


def daemon_log_path() -> Path:
    """Return the per-user daemon diagnostics path."""
    explicit = os.environ.get("CODEX_USAGE_HUD_DAEMON_LOG")
    if explicit:
        return Path(explicit).expanduser()
    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "codex-usage-hud" / "daemon.log"


def configure_daemon_logging() -> Path | None:
    """Configure a small rolling log for daemon lifecycle diagnostics."""
    global _logging_configured
    if _logging_configured:
        return daemon_log_path()
    path = daemon_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=512 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        return None
    level_name = os.environ.get("CODEX_USAGE_HUD_DAEMON_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _logger.handlers = [
        item for item in _logger.handlers if not isinstance(item, logging.NullHandler)
    ]
    _logger.addHandler(handler)
    _logger.setLevel(level)
    _logger.propagate = False
    _logging_configured = True
    return path


def hide_console_window() -> None:
    """Hide the current Windows console when daemon mode is launched manually."""
    if not sys.platform.startswith("win"):
        return
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, _SW_HIDE)
    except Exception:
        return


def _normalized_windows_path(value: str) -> str:
    return str(value or "").strip().replace("/", "\\").rstrip("\\").lower()


def _is_known_codex_desktop_path(executable_path: str) -> bool:
    """Return whether a Codex.exe path belongs to a desktop installation."""
    path = _normalized_windows_path(executable_path)
    if not path or not path.endswith("\\codex.exe"):
        return False

    configured = _normalized_windows_path(os.environ.get(_CODEX_APP_PATH_ENV, ""))
    if configured and path == configured:
        return True

    if "\\windowsapps\\openai.codex_" in path and "\\app\\" in path:
        return True

    desktop_install_markers = (
        "\\appdata\\local\\programs\\codexrelocated\\",
        "\\appdata\\local\\programs\\codex\\",
        "\\appdata\\local\\programs\\openai codex\\",
        "\\program files\\codex\\",
        "\\program files\\openai codex\\",
        "\\program files (x86)\\codex\\",
        "\\program files (x86)\\openai codex\\",
    )
    return any(marker in path for marker in desktop_install_markers)


def is_codex_client_process(
    process_name: str,
    executable_path: str = "",
) -> bool:
    """Return whether a process belongs to the Codex desktop app.

    ``codex.exe`` by name alone is deliberately ambiguous: npm and standalone
    CLI installations use exactly that executable name.  Current desktop
    builds use ``ChatGPT.exe`` for the Electron process family, while their
    ``resources\\codex.exe`` app-server is accepted only when its full path can
    be verified as part of a desktop installation.
    """
    name = Path(str(process_name or "")).name.strip().lower()
    if not name:
        return False
    stem = name[:-4] if name.endswith(".exe") else name
    if stem == "chatgpt":
        return True
    if stem == "codex":
        return _is_known_codex_desktop_path(executable_path)
    return False


class WindowsProcessListener:
    """Low-overhead Windows process listener based on Toolhelp snapshots."""

    def __init__(self, *, exclude_pid: int | None = None) -> None:
        self.exclude_pid = exclude_pid if exclude_pid is not None else os.getpid()
        self.enabled = False
        self.error = ""
        self._main_process_cache: dict[int, bool] = {}
        if not sys.platform.startswith("win"):
            self.error = f"Windows process listener is unsupported on {sys.platform}"
            return
        try:
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._configure_api()
            self.enabled = True
        except Exception as exc:
            self.error = str(exc)

    def _configure_api(self) -> None:
        self.kernel32.CreateToolhelp32Snapshot.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self.kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessEntry32W),
        ]
        self.kernel32.Process32FirstW.restype = wintypes.BOOL
        self.kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessEntry32W),
        ]
        self.kernel32.Process32NextW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        if hasattr(self.kernel32, "QueryFullProcessImageNameW"):
            self.kernel32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            ]
            self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self.kernel32.WaitForSingleObject.restype = wintypes.DWORD

    def _process_image_path(self, pid: int) -> str:
        query = getattr(self.kernel32, "QueryFullProcessImageNameW", None)
        if not callable(query):
            return ""
        process = self.kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            int(pid),
        )
        if not process:
            return ""
        try:
            size = wintypes.DWORD(_MAX_PROCESS_IMAGE_PATH)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not query(process, 0, buffer, ctypes.byref(size)):
                return ""
            return str(buffer.value or "")
        finally:
            self.kernel32.CloseHandle(process)

    def snapshot(self) -> ProcessSnapshot:
        """Return whether any Codex client process is currently alive."""
        checked_at = time.monotonic()
        if not self.enabled:
            raise ProcessListenerError(self.error or "Windows process listener unavailable")

        invalid_handle = ctypes.c_void_p(-1).value
        handle = self.kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not handle or int(handle) == int(invalid_handle or -1):
            error = ctypes.get_last_error()
            raise ProcessListenerError(f"CreateToolhelp32Snapshot failed: {error}")

        pids: list[int] = []
        names: list[str] = []
        parents: dict[int, int] = {}
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        try:
            has_entry = bool(self.kernel32.Process32FirstW(handle, ctypes.byref(entry)))
            while has_entry:
                pid = int(entry.th32ProcessID)
                name = str(entry.szExeFile or "")
                executable_path = ""
                if Path(name).stem.lower() == "codex":
                    executable_path = self._process_image_path(pid)
                if pid != self.exclude_pid and is_codex_client_process(
                    name,
                    executable_path,
                ):
                    pids.append(pid)
                    names.append(name)
                    parents[pid] = int(entry.th32ParentProcessID)
                has_entry = bool(self.kernel32.Process32NextW(handle, ctypes.byref(entry)))
        finally:
            self.kernel32.CloseHandle(handle)

        # Toolhelp enumeration order is not process-tree order. Watch only the
        # audited roots; GPU/renderer/crashpad helpers can survive a tray quit.
        roots = {pid for pid in pids if parents[pid] not in parents}
        self._main_process_cache = {
            pid: main for pid, main in self._main_process_cache.items()
            if pid in parents
        }
        if any(pid not in self._main_process_cache for pid in roots):
            from .codex_app_runtime import (
                audited_running_codex_desktop_processes,
                is_codex_desktop_main_process,
            )

            try:
                audited = audited_running_codex_desktop_processes()
            except RuntimeError as exc:
                raise ProcessListenerError(str(exc)) from exc
            for process in audited:
                if process.pid in roots and process.command_line:
                    self._main_process_cache[process.pid] = (
                        is_codex_desktop_main_process(process)
                    )
        main_rows = [
            (pid, name) for pid, name in zip(pids, names)
            if pid in roots and self._main_process_cache.get(pid, False)
        ]
        return ProcessSnapshot(
            found=bool(main_rows),
            pids=tuple(pid for pid, _name in main_rows),
            names=tuple(name for _pid, name in main_rows),
            checked_at=checked_at,
        )

    def exit_monitor_for_snapshot(
        self,
        snapshot: ProcessSnapshot,
    ) -> ProcessExitMonitor | None:
        pid = snapshot.primary_pid
        if not self.enabled or pid is None:
            return None
        handle = self.kernel32.OpenProcess(_SYNCHRONIZE, False, int(pid))
        if not handle:
            return None
        return _WindowsProcessExitMonitor(self.kernel32, int(handle))


def _is_macos_codex_desktop_command(command_line: str) -> bool:
    normalized = str(command_line or "").replace("\\", "/").casefold()
    if re.search(r"/[^/]*codex\.app/contents/macos/", normalized):
        return True
    configured = os.environ.get(_CODEX_APP_PATH_ENV, "").strip()
    if not configured:
        return False
    configured_path = configured.replace("\\", "/").rstrip("/").casefold()
    return bool(
        configured_path
        and f"{configured_path}/contents/macos/" in normalized
    )


class MacOSProcessListener:
    """Conservative macOS Codex Desktop listener based on audited snapshots."""

    def __init__(self, *, exclude_pid: int | None = None) -> None:
        self.exclude_pid = exclude_pid if exclude_pid is not None else os.getpid()
        self.enabled = sys.platform == "darwin"
        self.error = "" if self.enabled else (
            f"macOS process listener is unsupported on {sys.platform}"
        )

    def snapshot(self) -> ProcessSnapshot:
        checked_at = time.monotonic()
        if not self.enabled:
            raise ProcessListenerError(self.error or "macOS process listener unavailable")
        try:
            result = subprocess.run(
                ["ps", "-axo", "pid=,command="],
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProcessListenerError("macOS Codex process query failed") from exc
        if result.returncode != 0:
            raise ProcessListenerError(
                f"macOS Codex process query returned {result.returncode}"
            )

        pids: list[int] = []
        names: list[str] = []
        for line in result.stdout.splitlines():
            row = line.strip()
            if not row:
                continue
            pid_text, separator, command_line = row.partition(" ")
            if not separator or not _is_macos_codex_desktop_command(command_line):
                continue
            try:
                pid = int(pid_text)
            except (TypeError, ValueError):
                continue
            if pid <= 0 or pid == self.exclude_pid:
                continue
            pids.append(pid)
            names.append("Codex")
        return ProcessSnapshot(
            found=bool(pids),
            pids=tuple(pids),
            names=tuple(names),
            checked_at=checked_at,
        )


def current_process_listener() -> ProcessListener:
    if sys.platform == "darwin":
        _logger.info(
            "daemon_process_listener_selected platform=macos mode=ps-snapshot-fallback"
        )
        return MacOSProcessListener()
    _logger.info("daemon_process_listener_selected platform=windows mode=toolhelp")
    return WindowsProcessListener()


class _WindowsProcessExitMonitor:
    def __init__(self, kernel32: object, handle: int) -> None:
        self._kernel32 = kernel32
        self._handle: int | None = handle

    def is_running(self) -> bool | None:
        handle = self._handle
        if handle is None:
            return None
        try:
            result = self._kernel32.WaitForSingleObject(handle, 0)
        except Exception:
            return None
        if result == _WAIT_TIMEOUT:
            return True
        if result == _WAIT_OBJECT_0:
            return False
        return None

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            self._kernel32.CloseHandle(handle)
        except Exception:
            pass


class CodexDaemonManager:
    """Small polling state machine that owns process-level daemon decisions."""

    def __init__(
        self,
        listener: ProcessListener | None = None,
        *,
        poll_ms: int = DEFAULT_DAEMON_POLL_MS,
        exit_monitor_factory: Callable[[ProcessSnapshot], ProcessExitMonitor | None]
        | None = None,
    ) -> None:
        self.listener = listener if listener is not None else current_process_listener()
        self.poll_ms = max(MIN_DAEMON_POLL_MS, min(MAX_DAEMON_POLL_MS, int(poll_ms)))
        self.state = DaemonState.WAITING_FOR_CODEX
        self.last_snapshot = ProcessSnapshot(found=False, checked_at=time.monotonic())
        self._exit_monitor_factory = exit_monitor_factory
        self._exit_monitor: ProcessExitMonitor | None = None
        self._exit_monitor_pid: int | None = None
        self._stop_requested: Callable[[], bool] | None = None

    @property
    def poll_seconds(self) -> float:
        return self.poll_ms / 1000.0

    def snapshot(self) -> ProcessSnapshot:
        """Return a guarded process snapshot and move to fallback on failure."""
        try:
            snapshot = self.listener.snapshot()
        except ProcessListenerError:
            self.state = DaemonState.FALLBACK
            raise
        except Exception as exc:
            self.state = DaemonState.FALLBACK
            raise ProcessListenerError(str(exc)) from exc
        self.last_snapshot = snapshot
        self._refresh_exit_monitor(snapshot)
        return snapshot

    def set_stop_requested(self, callback: Callable[[], bool] | None) -> None:
        """Allow an external event to interrupt daemon wait states."""
        self._stop_requested = callback

    def wait_for_codex(self) -> ProcessSnapshot:
        """Block in a low-frequency loop until a Codex process appears."""
        self.state = DaemonState.WAITING_FOR_CODEX
        _logger.info("daemon_waiting poll_ms=%s", self.poll_ms)
        while True:
            snapshot = self.snapshot()
            if self._stop_requested is not None and self._stop_requested():
                return snapshot
            if snapshot.found:
                self.state = DaemonState.HUD_RUNNING
                _logger.info(
                    "daemon_codex_detected pids=%s names=%s",
                    ",".join(str(pid) for pid in snapshot.pids),
                    ",".join(snapshot.names),
                )
                return snapshot
            wait = getattr(self._stop_requested, "wait", None)
            if callable(wait):
                wait(self.poll_seconds)
            else:
                time.sleep(self.poll_seconds)

    def wait_for_codex_replacement(
        self,
        previous_pid: int | None,
        *,
        timeout_seconds: float = DEFAULT_DAEMON_REPLACEMENT_WAIT_SECONDS,
    ) -> bool:
        """Wait until an explicit restart has produced a different process.

        ``wait_for_codex`` only waits for *any* Codex process.  During a
        desktop restart the old process can remain alive briefly after the new
        CDP process is already launchable, so using that generic wait would
        let the renderer attach to the old process and then make the daemon
        report the expected replacement as an unexpected exit.  This bounded
        wait establishes the new process as the daemon baseline before the
        next renderer session starts.
        """
        try:
            old_pid = int(previous_pid) if previous_pid is not None else None
        except (TypeError, ValueError, OverflowError):
            old_pid = None
        timeout = max(0.0, float(timeout_seconds))
        deadline = time.monotonic() + timeout
        self.state = DaemonState.WAITING_FOR_CODEX
        _logger.info(
            "daemon_waiting_for_codex_replacement old_pid=%s poll_ms=%s",
            old_pid,
            self.poll_ms,
        )
        while True:
            snapshot = self.snapshot()
            if self._stop_requested is not None and self._stop_requested():
                return False
            if snapshot.found and (
                old_pid is None or old_pid not in snapshot.pids
            ):
                self.state = DaemonState.HUD_RUNNING
                _logger.info(
                    "daemon_codex_replacement_ready old_pid=%s new_pid=%s",
                    old_pid,
                    snapshot.primary_pid,
                )
                return True
            if time.monotonic() >= deadline:
                _logger.warning(
                    "daemon_codex_replacement_timeout old_pid=%s pids=%s",
                    old_pid,
                    ",".join(str(pid) for pid in snapshot.pids),
                )
                return False
            wait = getattr(self._stop_requested, "wait", None)
            if callable(wait):
                wait(self.poll_seconds)
            else:
                time.sleep(self.poll_seconds)

    def codex_is_running(self) -> bool:
        """Return whether the watched Codex process family is still alive."""
        previous_pid = self.last_snapshot.primary_pid
        monitored_exited = False
        if self._exit_monitor is not None:
            monitored_pid = self._exit_monitor_pid
            running = self._exit_monitor.is_running()
            if running is True:
                return True
            monitored_exited = running is False
            self._clear_exit_monitor()
            previous_pid = monitored_pid or previous_pid
        snapshot = self.snapshot()
        if (
            monitored_exited
            or (
                snapshot.found
                and previous_pid is not None
                and previous_pid not in snapshot.pids
            )
        ):
            self.state = DaemonState.EXITING
            _logger.info(
                "daemon_codex_replaced old_pid=%s new_pid=%s",
                previous_pid,
                snapshot.primary_pid,
            )
            return False
        if not snapshot.found:
            self.state = DaemonState.EXITING
        return snapshot.found

    def close(self) -> None:
        self._clear_exit_monitor()

    def _refresh_exit_monitor(self, snapshot: ProcessSnapshot) -> None:
        pid = snapshot.primary_pid
        if pid is None:
            self._clear_exit_monitor()
            return
        if self._exit_monitor is not None and self._exit_monitor_pid == pid:
            return
        self._clear_exit_monitor()
        factory = self._exit_monitor_factory
        if factory is None:
            factory = getattr(self.listener, "exit_monitor_for_snapshot", None)
        if not callable(factory):
            return
        try:
            monitor = factory(snapshot)
        except Exception as exc:
            _logger.info("daemon_exit_monitor_unavailable pid=%s error=%s", pid, exc)
            return
        if monitor is None:
            return
        self._exit_monitor = monitor
        self._exit_monitor_pid = pid
        _logger.info("daemon_exit_monitor_started pid=%s", pid)

    def _clear_exit_monitor(self) -> None:
        monitor = self._exit_monitor
        self._exit_monitor = None
        self._exit_monitor_pid = None
        if monitor is None:
            return
        try:
            monitor.close()
        except Exception:
            pass


__all__ = [
    "CodexDaemonManager",
    "DaemonState",
    "DEFAULT_DAEMON_POLL_MS",
    "DEFAULT_DAEMON_REPLACEMENT_WAIT_SECONDS",
    "MAX_DAEMON_POLL_MS",
    "MacOSProcessListener",
    "ProcessListenerError",
    "ProcessExitMonitor",
    "ProcessSnapshot",
    "WindowsProcessListener",
    "configure_daemon_logging",
    "current_process_listener",
    "daemon_log_path",
    "hide_console_window",
    "is_codex_client_process",
]
