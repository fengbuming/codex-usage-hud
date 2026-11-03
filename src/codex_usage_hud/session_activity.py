"""Event-driven Windows session-lock notifications for the HUD daemon."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes


_LOGGER = logging.getLogger(__name__)
_DESKTOP_SWITCHDESKTOP = 0x0100
_UOI_NAME = 2
_WM_QUIT = 0x0012
_WM_WTSSESSION_CHANGE = 0x02B1
_WTS_SESSION_LOCK = 0x7
_WTS_SESSION_UNLOCK = 0x8
_NOTIFY_FOR_THIS_SESSION = 0
_HWND_MESSAGE = -3
# Power-broadcast messages: Windows delivers WM_POWERBROADCAST on sleep and
# resume. A machine that sleeps or hibernates does not always emit a WTS
# session-lock event, so the HUD must treat suspend as "away" too, or it keeps
# CDP traffic alive through the entire sleep and the Codex renderer can wedge.
_WM_POWERBROADCAST = 0x0218
_PBT_APMSUSPEND = 0x0004
_PBT_APMRESUMEAUTOMATIC = 0x0012
_PBT_APMRESUMESUSPEND = 0x0007
_PBT_APMRESUMECRITICAL = 0x0006
# Query events are part of the suspend handshake and do NOT mean the system
# is actually going away: PBT_APMQUERYSUSPEND can be vetoed by any app, and
# PBT_APMQUERYSUSPENDFAILED means the suspend was cancelled (still awake).
# Only the definitive PBT_APMSUSPEND (which always follows a successful
# query) maps to "away", so a cancelled suspend can never stop the HUD.
_PBT_APMQUERYSUSPEND = 0x0000
_PBT_APMQUERYSUSPENDFAILED = 0x0002

_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_WNDPROC = _WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    ctypes.c_size_t,
    ctypes.c_ssize_t,
)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
    ]


def windows_session_locked() -> bool:
    """Return whether the interactive Windows desktop is unavailable."""
    if not sys.platform.startswith("win"):
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        close_desktop = user32.CloseDesktop
        close_desktop.argtypes = (ctypes.c_void_p,)
        close_desktop.restype = ctypes.c_bool
        open_input_desktop = user32.OpenInputDesktop
        open_input_desktop.argtypes = (
            ctypes.c_uint32,
            ctypes.c_bool,
            ctypes.c_uint32,
        )
        open_input_desktop.restype = ctypes.c_void_p
        desktop = open_input_desktop(0, False, _DESKTOP_SWITCHDESKTOP)
        if not desktop:
            return True
        try:
            get_user_object_information = user32.GetUserObjectInformationW
            get_user_object_information.argtypes = (
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_uint32),
            )
            get_user_object_information.restype = ctypes.c_bool
            required = ctypes.c_uint32(0)
            get_user_object_information(
                desktop, _UOI_NAME, None, 0, ctypes.byref(required)
            )
            buffer = ctypes.create_unicode_buffer(max(2, required.value + 1))
            if not get_user_object_information(
                desktop,
                _UOI_NAME,
                buffer,
                ctypes.sizeof(buffer),
                ctypes.byref(required),
            ):
                return False
            return buffer.value.casefold() == "winlogon"
        finally:
            close_desktop(desktop)
    except Exception:
        return False


class WindowsSessionLockMonitor:
    """Report Windows session lock transitions through a hidden message window."""

    def __init__(
        self,
        *,
        on_lock: Callable[[], None],
        on_unlock: Callable[[], None],
        locked_probe: Callable[[], bool] = windows_session_locked,
    ) -> None:
        self._on_lock = on_lock
        self._on_unlock = on_unlock
        self._locked_probe = locked_probe
        self._stop_event = threading.Event()
        self._ready = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hwnd: wintypes.HWND | None = None
        self._locked: bool | None = None
        self._startup_error: BaseException | None = None

    def start(self, *, initial_locked: bool | None = None) -> None:
        if not sys.platform.startswith("win"):
            return
        with self._state_lock:
            if self._thread is not None:
                return
            self._stop_event.clear()
            self._ready.clear()
            self._startup_error = None
            self._locked = initial_locked
            thread = threading.Thread(
                target=self._run,
                name="codex-hud-session-lock-monitor",
                daemon=True,
            )
            self._thread = thread
        if self._locked:
            self._on_lock()
        thread.start()
        self._ready.wait(timeout=2.0)
        _LOGGER.info(
            "session_lock_monitor_start ready=%s alive=%s hwnd=%s thread_id=%s initial_locked=%s",
            self._ready.is_set(), thread.is_alive(), self._hwnd,
            self._thread_id, initial_locked,
        )
        error = self._startup_error
        if error is not None:
            _LOGGER.error("session_lock_monitor_start_failed error=%s", error)
            self.close()

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.start()
        else:
            self.close()

    def close(self) -> None:
        _LOGGER.info("session_lock_monitor_stop_requested thread_id=%s", self._thread_id)
        self._stop_event.set()
        with self._state_lock:
            thread = self._thread
            thread_id = self._thread_id
        if thread_id:
            try:
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                post_thread_message = user32.PostThreadMessageW
                post_thread_message.argtypes = (
                    wintypes.DWORD,
                    wintypes.UINT,
                    wintypes.WPARAM,
                    wintypes.LPARAM,
                )
                post_thread_message.restype = wintypes.BOOL
                post_thread_message(thread_id, _WM_QUIT, 0, 0)
            except Exception:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._state_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None
            self._thread_id = 0
            self._hwnd = None
            self._locked = None

    def _handle_power_broadcast(self, wparam: int) -> None:
        """Map Windows power events to away/back transitions.

        A machine that sleeps or hibernates does not always emit a WTS
        session-lock event, so the HUD must treat suspend as "away" too, or it
        keeps CDP traffic alive through the entire sleep and the Codex renderer
        can wedge (blank UI after unlock). Only the definitive suspend event is
        handled; the query handshake (QUERYSUSPEND / QUERYSUSPENDFAILED) never
        triggers "away" because the suspend may still be vetoed.
        """
        _LOGGER.info("session_power_notification wparam=%s", wparam)
        if wparam == _PBT_APMSUSPEND:
            self._emit_transition(True)
        elif wparam in {
            _PBT_APMRESUMEAUTOMATIC,
            _PBT_APMRESUMESUSPEND,
            _PBT_APMRESUMECRITICAL,
        }:
            self._emit_transition(False)

    def _emit_transition(self, locked: bool) -> None:
        """Record a lock-state observation and fire a transition callback.

        The first observation is not swallowed: a WTS/power event that arrives
        between ``start()`` and the initial-state probe would otherwise be lost
        forever (the probe then dedups against the recorded state and no
        callback ever fires). Emitting on the first event closes that race.
        """
        with self._state_lock:
            previous = self._locked
            self._locked = bool(locked)
        if previous is None or bool(locked) != previous:
            self._notify_transition(previous, locked)

    def _notify_transition(self, previous: bool | None, locked: bool) -> None:
        started = time.monotonic()
        _LOGGER.info("session_lock_callback_begin previous=%s locked=%s", previous, locked)
        try:
            (self._on_lock if locked else self._on_unlock)()
        except Exception:
            _LOGGER.exception("session_lock_callback_failed locked=%s", locked)
            raise
        _LOGGER.info(
            "session_lock_callback_complete locked=%s elapsed_ms=%.1f",
            locked, (time.monotonic() - started) * 1000.0,
        )

    def _run(self) -> None:
        user32 = None
        wtsapi32 = None
        class_name = f"CodexUsageHudSessionLock_{id(self):x}"
        registered = False
        hwnd: wintypes.HWND | None = None
        wnd_proc = None
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            def_window_proc = user32.DefWindowProcW
            def_window_proc.argtypes = (
                wintypes.HWND,
                wintypes.UINT,
                ctypes.c_size_t,
                ctypes.c_ssize_t,
            )
            def_window_proc.restype = ctypes.c_ssize_t
            get_current_thread_id = kernel32.GetCurrentThreadId
            get_current_thread_id.argtypes = ()
            get_current_thread_id.restype = wintypes.DWORD
            with self._state_lock:
                self._thread_id = int(get_current_thread_id())

            def window_proc(
                current_hwnd: wintypes.HWND,
                message: int,
                wparam: int,
                lparam: int,
            ) -> int:
                if message == _WM_WTSSESSION_CHANGE:
                    _LOGGER.info("session_wts_notification wparam=%s session_id=%s", wparam, lparam)
                    if int(wparam) == _WTS_SESSION_LOCK:
                        self._emit_transition(True)
                    elif int(wparam) == _WTS_SESSION_UNLOCK:
                        self._emit_transition(False)
                elif message == _WM_POWERBROADCAST:
                    self._handle_power_broadcast(int(wparam))
                return int(def_window_proc(current_hwnd, message, wparam, lparam))

            wnd_proc = _WNDPROC(window_proc)
            get_module_handle = kernel32.GetModuleHandleW
            get_module_handle.argtypes = (wintypes.LPCWSTR,)
            get_module_handle.restype = ctypes.c_void_p
            hinstance = get_module_handle(None)
            register_class = user32.RegisterClassW
            register_class.argtypes = (ctypes.POINTER(_WNDCLASSW),)
            register_class.restype = wintypes.ATOM
            unregister_class = user32.UnregisterClassW
            unregister_class.argtypes = (wintypes.LPCWSTR, ctypes.c_void_p)
            unregister_class.restype = wintypes.BOOL
            window_class = _WNDCLASSW(
                style=0,
                lpfnWndProc=wnd_proc,
                cbClsExtra=0,
                cbWndExtra=0,
                hInstance=hinstance,
                hIcon=None,
                hCursor=None,
                hbrBackground=None,
                lpszMenuName=None,
                lpszClassName=class_name,
            )
            if not register_class(ctypes.byref(window_class)):
                raise ctypes.WinError(ctypes.get_last_error())
            registered = True
            create_window = user32.CreateWindowExW
            create_window.argtypes = (
                wintypes.DWORD,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                wintypes.HMENU,
                ctypes.c_void_p,
                ctypes.c_void_p,
            )
            create_window.restype = wintypes.HWND
            hwnd = create_window(
                0,
                class_name,
                class_name,
                0,
                0,
                0,
                0,
                0,
                wintypes.HWND(_HWND_MESSAGE),
                None,
                hinstance,
                None,
            )
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            with self._state_lock:
                self._hwnd = hwnd
            register_session = wtsapi32.WTSRegisterSessionNotification
            register_session.argtypes = (wintypes.HWND, wintypes.DWORD)
            register_session.restype = wintypes.BOOL
            if not register_session(hwnd, _NOTIFY_FOR_THIS_SESSION):
                raise ctypes.WinError(ctypes.get_last_error())

            _LOGGER.info("session_lock_monitor_registered hwnd=%s thread_id=%s", hwnd, self._thread_id)

            self._synchronize_initial_state()

            get_message = user32.GetMessageW
            get_message.argtypes = (
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
            )
            get_message.restype = ctypes.c_int
            translate_message = user32.TranslateMessage
            translate_message.argtypes = (ctypes.POINTER(wintypes.MSG),)
            translate_message.restype = wintypes.BOOL
            dispatch_message = user32.DispatchMessageW
            dispatch_message.argtypes = (ctypes.POINTER(wintypes.MSG),)
            dispatch_message.restype = ctypes.c_ssize_t
            self._ready.set()
            message = wintypes.MSG()
            while not self._stop_event.is_set():
                result = int(get_message(ctypes.byref(message), None, 0, 0))
                if result <= 0:
                    break
                translate_message(ctypes.byref(message))
                dispatch_message(ctypes.byref(message))
            unregister_session = wtsapi32.WTSUnRegisterSessionNotification
            unregister_session.argtypes = (wintypes.HWND,)
            unregister_session.restype = wintypes.BOOL
            unregister_session(hwnd)
        except BaseException as exc:
            with self._state_lock:
                self._startup_error = exc
            _LOGGER.exception("session_lock_monitor_failed")
        finally:
            _LOGGER.info("session_lock_monitor_exiting stop_requested=%s", self._stop_event.is_set())
            if hwnd:
                try:
                    user32.DestroyWindow(hwnd)
                except Exception:
                    pass
            if registered:
                try:
                    user32.UnregisterClassW(class_name, hinstance)
                except Exception:
                    pass
            self._ready.set()
            with self._state_lock:
                self._hwnd = None
                self._thread_id = 0

    def _synchronize_initial_state(self) -> None:
        current = bool(self._locked_probe())
        with self._state_lock:
            previous = self._locked
            self._locked = current
        _LOGGER.info("session_lock_initial_probe previous=%s locked=%s", previous, current)
        if previous is None:
            if current:
                self._notify_transition(previous, current)
            return
        if previous == current:
            return
        self._notify_transition(previous, current)


__all__ = [
    "WindowsSessionLockMonitor",
    "windows_session_locked",
]
