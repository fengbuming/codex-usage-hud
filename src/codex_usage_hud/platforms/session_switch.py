"""Session-switch backends for Codex conversation navigation."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import logging
import sys
import time
from typing import Protocol

from .base import BasePlatform
from .cdp_probe import CodexCdpSessionController
from .windows_tracker import CodexWindowTracker

_VK_CONTROL = 0x11
_VK_A = 0x41
_VK_K = 0x4B
_VK_V = 0x56
_VK_1 = 0x31
_VK_ESCAPE = 0x1B
_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002
_KEYEVENTF_KEYUP = 0x0002
_INPUT_KEYBOARD = 1
_LOGGER = logging.getLogger(__name__)

if sys.platform.startswith("win"):
    _USER32 = ctypes.windll.user32
    _KERNEL32 = ctypes.windll.kernel32
    _USER32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
    _USER32.SendInput.restype = wintypes.UINT
    _USER32.OpenClipboard.argtypes = [wintypes.HWND]
    _USER32.OpenClipboard.restype = wintypes.BOOL
    _USER32.CloseClipboard.argtypes = []
    _USER32.CloseClipboard.restype = wintypes.BOOL
    _USER32.EmptyClipboard.argtypes = []
    _USER32.EmptyClipboard.restype = wintypes.BOOL
    _USER32.GetClipboardData.argtypes = [wintypes.UINT]
    _USER32.GetClipboardData.restype = wintypes.HANDLE
    _USER32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    _USER32.SetClipboardData.restype = wintypes.HANDLE
    _USER32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    _USER32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    _USER32.keybd_event.argtypes = [
        wintypes.BYTE,
        wintypes.BYTE,
        wintypes.DWORD,
        ctypes.c_size_t,
    ]
    _USER32.keybd_event.restype = None
    _KERNEL32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _KERNEL32.GlobalAlloc.restype = wintypes.HGLOBAL
    _KERNEL32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _KERNEL32.GlobalLock.restype = ctypes.c_void_p
    _KERNEL32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _KERNEL32.GlobalUnlock.restype = wintypes.BOOL


@dataclass(frozen=True)
class SessionSwitchRequest:
    session_id: str = ""
    title: str = ""
    workdir: str = ""


@dataclass(frozen=True)
class SessionSwitchResult:
    ok: bool
    status: str
    backend: str = ""
    requested_session_id: str = ""
    requested_title: str = ""
    active_session_id: str = ""
    active_title: str = ""
    matched_by: str = ""
    message: str = ""


class SessionSwitchBackend(Protocol):
    name: str

    def activate(self, request: SessionSwitchRequest) -> SessionSwitchResult:
        """Try to switch to the requested session."""


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _title_matches(candidate: str, requested: str) -> bool:
    left = _normalize_text(candidate).lower()
    right = _normalize_text(requested).lower()
    if not left or not right:
        return False
    return left == right or left.startswith(right) or right.startswith(left)


class CdpSessionSwitchBackend:
    name = "cdp"

    def __init__(self, *, timeout_seconds: float, port: int | None = None) -> None:
        self._controller = CodexCdpSessionController(
            port=port,
            timeout_seconds=timeout_seconds,
        )

    def activate(self, request: SessionSwitchRequest) -> SessionSwitchResult:
        result = self._controller.activate_thread(
            session_id=request.session_id,
            title=request.title,
            workdir=request.workdir,
        )
        return SessionSwitchResult(
            ok=result.ok,
            status=result.status,
            backend=self.name,
            requested_session_id=result.requested_session_id,
            requested_title=result.requested_title,
            active_session_id=result.active_session_id,
            active_title=result.active_title,
            matched_by=result.matched_by,
            message=result.message,
        )


class _KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeyBdInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


def _send_virtual_keys(*virtual_keys: int) -> bool:
    if not sys.platform.startswith("win"):
        return False
    events: list[_Input] = []
    for value in virtual_keys:
        events.append(
            _Input(
                type=_INPUT_KEYBOARD,
                ki=_KeyBdInput(wVk=int(value), wScan=0, dwFlags=0, time=0, dwExtraInfo=None),
            )
        )
    for value in reversed(virtual_keys):
        events.append(
            _Input(
                type=_INPUT_KEYBOARD,
                ki=_KeyBdInput(
                    wVk=int(value),
                    wScan=0,
                    dwFlags=_KEYEVENTF_KEYUP,
                    time=0,
                    dwExtraInfo=None,
                ),
            )
        )
    array_type = _Input * len(events)
    sent = int(_USER32.SendInput(len(events), array_type(*events), ctypes.sizeof(_Input)) or 0)
    if sent == len(events):
        return True
    # Electron windows sometimes ignore SendInput while keybd_event still lands.
    for value in virtual_keys:
        _USER32.keybd_event(int(value), 0, 0, 0)
        time.sleep(0.01)
    for value in reversed(virtual_keys):
        _USER32.keybd_event(int(value), 0, _KEYEVENTF_KEYUP, 0)
        time.sleep(0.01)
    return True


def _open_clipboard() -> bool:
    for _ in range(8):
        if _USER32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def _get_clipboard_text() -> str | None:
    if not sys.platform.startswith("win") or not _open_clipboard():
        return None
    try:
        if not _USER32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
            return None
        data_handle = _USER32.GetClipboardData(_CF_UNICODETEXT)
        if not data_handle:
            return None
        pointer = _KERNEL32.GlobalLock(data_handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            _KERNEL32.GlobalUnlock(data_handle)
    finally:
        _USER32.CloseClipboard()


def _set_clipboard_text(value: str) -> bool:
    if not sys.platform.startswith("win") or not _open_clipboard():
        return False
    text = str(value or "")
    try:
        _USER32.EmptyClipboard()
        payload = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(payload)
        handle = _KERNEL32.GlobalAlloc(_GMEM_MOVEABLE, size)
        if not handle:
            return False
        locked = _KERNEL32.GlobalLock(handle)
        if not locked:
            return False
        try:
            ctypes.memmove(locked, ctypes.addressof(payload), size)
        finally:
            _KERNEL32.GlobalUnlock(handle)
        return bool(_USER32.SetClipboardData(_CF_UNICODETEXT, handle))
    finally:
        _USER32.CloseClipboard()


@contextmanager
def _temporary_clipboard_text(value: str):
    previous = _get_clipboard_text()
    if not _set_clipboard_text(value):
        yield False
        return
    try:
        yield True
    finally:
        if previous is not None:
            _set_clipboard_text(previous)


class WindowsSearchSessionSwitchBackend:
    """Use Codex's own Ctrl+K search flow without CDP."""

    name = "windows-search"

    def __init__(
        self,
        platform: BasePlatform,
        *,
        timeout_seconds: float = 3.0,
        settle_seconds: float = 0.45,
        poll_seconds: float = 0.12,
    ) -> None:
        self._platform = platform
        self._timeout_seconds = max(0.5, float(timeout_seconds))
        self._settle_seconds = max(0.1, float(settle_seconds))
        self._poll_seconds = max(0.05, float(poll_seconds))

    @staticmethod
    def _dismiss_search_dialog(tracker: object, hwnd: int) -> None:
        try:
            # A failed shortcut leaves Codex's modal search layer open. Only
            # dismiss it while Codex still owns the foreground input.
            if tracker.is_active(hwnd):
                _send_virtual_keys(_VK_ESCAPE)
        except Exception:
            _LOGGER.debug("session_search_dialog_cleanup_failed", exc_info=True)

    def activate(self, request: SessionSwitchRequest) -> SessionSwitchResult:
        if not sys.platform.startswith("win"):
            return SessionSwitchResult(
                ok=False,
                status="unsupported",
                backend=self.name,
                requested_session_id=request.session_id,
                requested_title=request.title,
                message="Windows backend is unavailable on this platform",
            )
        title = _normalize_text(request.title)
        if not title:
            return SessionSwitchResult(
                ok=False,
                status="missing-target",
                backend=self.name,
                requested_session_id=request.session_id,
                requested_title=request.title,
                message="title is required for search backend",
            )

        try:
            tracker = CodexWindowTracker(enable_uia=False)
        except Exception as exc:
            return SessionSwitchResult(
                ok=False,
                status="tracker-error",
                backend=self.name,
                requested_session_id=request.session_id,
                requested_title=title,
                message=str(exc),
            )
        if not getattr(tracker, "enabled", False):
            return SessionSwitchResult(
                ok=False,
                status="tracker-disabled",
                backend=self.name,
                requested_session_id=request.session_id,
                requested_title=title,
                message="window tracker unavailable",
            )

        hwnd = int(tracker.activate_main_window() or 0)
        if not hwnd:
            return SessionSwitchResult(
                ok=False,
                status="no-window",
                backend=self.name,
                requested_session_id=request.session_id,
                requested_title=title,
                message="Codex window not found",
            )

        active_title_before = _normalize_text(
            self._platform.get_active_conversation_title() or ""
        )
        with _temporary_clipboard_text(title) as clipboard_ready:
            if not clipboard_ready:
                return SessionSwitchResult(
                    ok=False,
                    status="clipboard-error",
                    backend=self.name,
                    requested_session_id=request.session_id,
                    requested_title=title,
                    active_title=active_title_before,
                    message="clipboard access failed",
                )
            if not _send_virtual_keys(_VK_CONTROL, _VK_K):
                return SessionSwitchResult(
                    ok=False,
                    status="sendinput-error",
                    backend=self.name,
                    requested_session_id=request.session_id,
                    requested_title=title,
                    active_title=active_title_before,
                    message="unable to open Codex search",
                )
            time.sleep(0.16)
            _send_virtual_keys(_VK_CONTROL, _VK_A)
            time.sleep(0.05)
            if not _send_virtual_keys(_VK_CONTROL, _VK_V):
                self._dismiss_search_dialog(tracker, hwnd)
                return SessionSwitchResult(
                    ok=False,
                    status="sendinput-error",
                    backend=self.name,
                    requested_session_id=request.session_id,
                    requested_title=title,
                    active_title=active_title_before,
                    message="unable to paste search text",
                )
            time.sleep(self._settle_seconds)
            if not _send_virtual_keys(_VK_CONTROL, _VK_1):
                self._dismiss_search_dialog(tracker, hwnd)
                return SessionSwitchResult(
                    ok=False,
                    status="sendinput-error",
                    backend=self.name,
                    requested_session_id=request.session_id,
                    requested_title=title,
                    active_title=active_title_before,
                    message="unable to trigger search shortcut",
                )

        deadline = time.monotonic() + self._timeout_seconds
        last_title = active_title_before
        while time.monotonic() < deadline:
            active_title = _normalize_text(
                self._platform.get_active_conversation_title() or ""
            )
            if active_title:
                last_title = active_title
            if _title_matches(active_title, title):
                return SessionSwitchResult(
                    ok=True,
                    status="switched",
                    backend=self.name,
                    requested_session_id=request.session_id,
                    requested_title=title,
                    active_title=active_title,
                    matched_by="search-shortcut",
                )
            time.sleep(self._poll_seconds)

        self._dismiss_search_dialog(tracker, hwnd)
        return SessionSwitchResult(
            ok=False,
            status="search-shortcut-timeout",
            backend=self.name,
            requested_session_id=request.session_id,
            requested_title=title,
            active_title=last_title,
            matched_by="search-shortcut",
            message="active title did not switch in time",
        )


class SessionSwitchController:
    """Try one or more session-switch backends in order."""

    def __init__(self, backends: Sequence[SessionSwitchBackend]) -> None:
        self._backends = tuple(backends)

    def activate_session(
        self,
        *,
        session_id: str = "",
        title: str = "",
        workdir: str = "",
        backend_names: Sequence[str] | None = None,
    ) -> SessionSwitchResult:
        request = SessionSwitchRequest(
            session_id=_normalize_text(session_id),
            title=_normalize_text(title),
            workdir=_normalize_text(workdir),
        )
        last_result = SessionSwitchResult(
            ok=False,
            status="no-backend",
            requested_session_id=request.session_id,
            requested_title=request.title,
        )
        allowed_backends = (
            {str(name or "").strip().casefold() for name in backend_names}
            if backend_names is not None
            else None
        )
        for backend in self._backends:
            backend_name = str(
                getattr(backend, "name", "") or type(backend).__name__
            )
            if (
                allowed_backends is not None
                and backend_name.casefold() not in allowed_backends
            ):
                continue
            try:
                result = backend.activate(request)
            except Exception as exc:
                _LOGGER.exception(
                    "session_switch_backend_failed backend=%s requested_session=%s requested_title=%s",
                    backend_name,
                    request.session_id or "-",
                    request.title or "-",
                )
                last_result = SessionSwitchResult(
                    ok=False,
                    status="backend-error",
                    backend=backend_name,
                    requested_session_id=request.session_id,
                    requested_title=request.title,
                    message=f"{type(exc).__name__}: {exc}",
                )
                continue
            if result.ok:
                return result
            last_result = result
            if result.status == "missing-target":
                break
        return last_result


__all__ = [
    "CdpSessionSwitchBackend",
    "SessionSwitchController",
    "SessionSwitchRequest",
    "SessionSwitchResult",
    "WindowsSearchSessionSwitchBackend",
]
