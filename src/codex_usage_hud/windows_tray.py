"""Native Windows system-tray lifecycle for the renderer HUD daemon."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

from .runtime_paths import hud_program_root


_LOGGER = logging.getLogger(__name__)

_HWND_MESSAGE = -3
_WM_NULL = 0x0000
_WM_DESTROY = 0x0002
_WM_RBUTTONUP = 0x0205
_WM_QUIT = 0x0012
_WM_APP = 0x8000
_WM_TRAY_ICON = _WM_APP + 1
_NIM_ADD = 0x00000000
_NIM_DELETE = 0x00000002
_NIF_MESSAGE = 0x00000001
_NIF_ICON = 0x00000002
_NIF_TIP = 0x00000004
_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x00000010
_LR_DEFAULTSIZE = 0x00000040
_MF_STRING = 0x00000000
_TPM_LEFTALIGN = 0x0000
_TPM_BOTTOMALIGN = 0x0020
_TPM_RIGHTBUTTON = 0x0002
_TPM_RETURNCMD = 0x0100
_TRAY_EXIT_COMMAND = 1001
_TRAY_TOOLTIP = "Codex Usage HUD"
_TRAY_EXIT_LABEL = "退出"

_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_WNDPROC = _WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    ctypes.c_size_t,
    ctypes.c_ssize_t,
)


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _GUID(ctypes.Structure):
    _fields_ = [
        ("data1", wintypes.DWORD),
        ("data2", wintypes.WORD),
        ("data3", wintypes.WORD),
        ("data4", ctypes.c_ubyte * 8),
    ]


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
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class _NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", ctypes.c_void_p),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", _GUID),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


class ShutdownCoordinator:
    """Fan out one idempotent shutdown request to the active runtime."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callback: Callable[[], object] | None = None

    def __call__(self) -> bool:
        return self.is_requested()

    def is_requested(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def request(self) -> bool:
        """Set the request once and notify the current renderer session."""
        with self._lock:
            if self._event.is_set():
                return False
            self._event.set()
            callback = self._callback
        if callback is not None:
            try:
                callback()
            except Exception:
                _LOGGER.exception("hud_shutdown_callback_failed")
        return True

    def bind(self, callback: Callable[[], object]) -> None:
        with self._lock:
            self._callback = callback
            requested = self._event.is_set()
        if requested:
            callback()

    def unbind(self, callback: Callable[[], object]) -> None:
        with self._lock:
            if self._callback is callback:
                self._callback = None


def resolve_tray_icon_path() -> Path | None:
    """Find the packaged ICO, then the repository's designed icon asset."""
    candidates: list[Path] = [
        Path(__file__).resolve().parent / "assets" / "hud-app-icon.ico",
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "icon" / "hud-app-icon.ico")
    candidates.append(hud_program_root() / "icon" / "hud-app-icon.ico")
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


class WindowsTrayIcon:
    """Own a native Windows tray icon and its right-click menu thread."""

    def __init__(
        self,
        *,
        on_exit: Callable[[], object],
        icon_path: Path | None = None,
        tooltip: str = _TRAY_TOOLTIP,
    ) -> None:
        self._on_exit = on_exit
        self._icon_path = Path(icon_path) if icon_path is not None else None
        self._tooltip = str(tooltip)[:127]
        self._stop_event = threading.Event()
        self._ready = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hwnd: wintypes.HWND | None = None
        self._startup_error: BaseException | None = None
        self._exit_dispatched = False

    @property
    def icon_path(self) -> Path | None:
        return self._icon_path or resolve_tray_icon_path()

    def start(self) -> bool:
        if not sys.platform.startswith("win"):
            return False
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop_event.clear()
            self._ready.clear()
            self._startup_error = None
            self._exit_dispatched = False
            thread = threading.Thread(
                target=self._run,
                name="codex-hud-windows-tray",
                daemon=True,
            )
            self._thread = thread
        thread.start()
        self._ready.wait(timeout=2.0)
        error = self._startup_error
        if error is not None or not self._ready.is_set():
            if error is not None:
                _LOGGER.warning("windows_tray_start_failed error=%s", error)
            else:
                _LOGGER.warning("windows_tray_start_timed_out")
            self.close()
            return False
        return True

    def close(self) -> None:
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
                _LOGGER.debug("windows_tray_post_quit_failed", exc_info=True)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._state_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None
            self._thread_id = 0
            self._hwnd = None

    def __enter__(self) -> WindowsTrayIcon:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb
        self.close()

    def _run(self) -> None:
        user32 = None
        kernel32 = None
        shell32 = None
        hinstance = None
        hwnd: wintypes.HWND | None = None
        hicon: ctypes.c_void_p | None = None
        class_name = f"CodexUsageHudTray_{id(self):x}"
        registered = False
        notify_added = False
        wnd_proc = None
        try:
            icon_path = self.icon_path
            if icon_path is None:
                raise FileNotFoundError("hud-app-icon.ico was not found")
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)

            get_current_thread_id = kernel32.GetCurrentThreadId
            get_current_thread_id.argtypes = ()
            get_current_thread_id.restype = wintypes.DWORD
            with self._state_lock:
                self._thread_id = int(get_current_thread_id())

            load_image = user32.LoadImageW
            load_image.argtypes = [
                ctypes.c_void_p,
                wintypes.LPCWSTR,
                wintypes.UINT,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            load_image.restype = ctypes.c_void_p
            hicon = load_image(
                None,
                str(icon_path),
                _IMAGE_ICON,
                32,
                32,
                _LR_LOADFROMFILE | _LR_DEFAULTSIZE,
            )
            if not hicon:
                raise ctypes.WinError(ctypes.get_last_error())

            def_window_proc = user32.DefWindowProcW
            def_window_proc.argtypes = (
                wintypes.HWND,
                wintypes.UINT,
                ctypes.c_size_t,
                ctypes.c_ssize_t,
            )
            def_window_proc.restype = ctypes.c_ssize_t

            def window_proc(
                current_hwnd: wintypes.HWND,
                message: int,
                wparam: int,
                lparam: int,
            ) -> int:
                if message == _WM_TRAY_ICON and int(lparam) == _WM_RBUTTONUP:
                    self._show_context_menu(user32, current_hwnd)
                    return 0
                if message == _WM_DESTROY:
                    return 0
                return int(def_window_proc(current_hwnd, message, wparam, lparam))

            wnd_proc = _WNDPROC(window_proc)
            get_module_handle = kernel32.GetModuleHandleW
            get_module_handle.argtypes = (wintypes.LPCWSTR,)
            get_module_handle.restype = ctypes.c_void_p
            hinstance = get_module_handle(None)

            register_class = user32.RegisterClassW
            register_class.argtypes = (ctypes.POINTER(_WNDCLASSW),)
            register_class.restype = wintypes.ATOM
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

            notify = _NOTIFYICONDATAW()
            notify.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
            notify.hWnd = hwnd
            notify.uID = 1
            notify.uFlags = _NIF_MESSAGE | _NIF_ICON | _NIF_TIP
            notify.uCallbackMessage = _WM_TRAY_ICON
            notify.hIcon = hicon
            notify.szTip = self._tooltip
            shell_notify = shell32.Shell_NotifyIconW
            shell_notify.argtypes = (
                wintypes.DWORD,
                ctypes.POINTER(_NOTIFYICONDATAW),
            )
            shell_notify.restype = wintypes.BOOL
            if not shell_notify(_NIM_ADD, ctypes.byref(notify)):
                raise ctypes.WinError(ctypes.get_last_error())
            notify_added = True
            self._ready.set()

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
            dispatch_message = user32.DispatchMessageW
            dispatch_message.argtypes = (ctypes.POINTER(wintypes.MSG),)
            dispatch_message.restype = ctypes.c_ssize_t
            message = wintypes.MSG()
            while not self._stop_event.is_set():
                result = int(get_message(ctypes.byref(message), None, 0, 0))
                if result <= 0:
                    break
                translate_message(ctypes.byref(message))
                dispatch_message(ctypes.byref(message))
        except BaseException as exc:
            with self._state_lock:
                self._startup_error = exc
            _LOGGER.debug("windows_tray_thread_failed", exc_info=True)
        finally:
            if notify_added and shell32 is not None and hwnd:
                try:
                    shell_notify = shell32.Shell_NotifyIconW
                    shell_notify.argtypes = (
                        wintypes.DWORD,
                        ctypes.POINTER(_NOTIFYICONDATAW),
                    )
                    shell_notify.restype = wintypes.BOOL
                    notify = _NOTIFYICONDATAW()
                    notify.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
                    notify.hWnd = hwnd
                    notify.uID = 1
                    shell_notify(_NIM_DELETE, ctypes.byref(notify))
                except Exception:
                    _LOGGER.debug("windows_tray_delete_failed", exc_info=True)
            if hwnd and user32 is not None:
                try:
                    user32.DestroyWindow(hwnd)
                except Exception:
                    pass
            if registered and user32 is not None:
                try:
                    user32.UnregisterClassW(class_name, hinstance)
                except Exception:
                    pass
            if hicon and user32 is not None:
                try:
                    user32.DestroyIcon(hicon)
                except Exception:
                    pass
            self._ready.set()
            with self._state_lock:
                if self._hwnd == hwnd:
                    self._hwnd = None

    def _show_context_menu(self, user32: object, hwnd: wintypes.HWND) -> None:
        create_menu = user32.CreatePopupMenu
        create_menu.argtypes = ()
        create_menu.restype = wintypes.HMENU
        menu = create_menu()
        if not menu:
            return
        try:
            append_menu = user32.AppendMenuW
            append_menu.argtypes = (
                wintypes.HMENU,
                wintypes.UINT,
                ctypes.c_size_t,
                wintypes.LPCWSTR,
            )
            append_menu.restype = wintypes.BOOL
            if not append_menu(
                menu,
                _MF_STRING,
                _TRAY_EXIT_COMMAND,
                _TRAY_EXIT_LABEL,
            ):
                return
            point = _POINT()
            get_cursor_pos = user32.GetCursorPos
            get_cursor_pos.argtypes = (ctypes.POINTER(_POINT),)
            get_cursor_pos.restype = wintypes.BOOL
            if not get_cursor_pos(ctypes.byref(point)):
                return
            user32.SetForegroundWindow(hwnd)
            track_menu = user32.TrackPopupMenu
            track_menu.argtypes = (
                wintypes.HMENU,
                wintypes.UINT,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                ctypes.c_void_p,
            )
            track_menu.restype = wintypes.UINT
            command = track_menu(
                menu,
                _TPM_LEFTALIGN
                | _TPM_BOTTOMALIGN
                | _TPM_RIGHTBUTTON
                | _TPM_RETURNCMD,
                point.x,
                point.y,
                0,
                hwnd,
                None,
            )
            if int(command) == _TRAY_EXIT_COMMAND:
                self._request_exit()
            post_message = user32.PostMessageW
            post_message.argtypes = (
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            post_message.restype = wintypes.BOOL
            post_message(hwnd, _WM_NULL, 0, 0)
        finally:
            destroy_menu = user32.DestroyMenu
            destroy_menu.argtypes = (wintypes.HMENU,)
            destroy_menu.restype = wintypes.BOOL
            destroy_menu(menu)

    def _request_exit(self) -> None:
        with self._state_lock:
            if self._exit_dispatched:
                return
            self._exit_dispatched = True
        try:
            if self._on_exit() is False:
                with self._state_lock:
                    self._exit_dispatched = False
        except Exception:
            with self._state_lock:
                self._exit_dispatched = False
            _LOGGER.exception("windows_tray_exit_callback_failed")


def create_windows_tray(*, on_exit: Callable[[], object]) -> WindowsTrayIcon:
    return WindowsTrayIcon(on_exit=on_exit)


__all__ = [
    "ShutdownCoordinator",
    "WindowsTrayIcon",
    "create_windows_tray",
    "resolve_tray_icon_path",
]
