"""Windows Codex window tracking and UI Automation landmark probing.

This module intentionally uses only the Python standard library.  The Win32,
DWM, COM, and UI Automation calls are bound with ``ctypes`` so the HUD can dock
to the live Codex window without pywinauto, pyautogui, or comtypes.
"""

from __future__ import annotations

import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)

DockTarget = Literal["title", "input"]


STATUS_VISIBLE = "visible"
STATUS_NOT_FOUND = "not_found"
STATUS_HIDDEN = "hidden"
STATUS_MINIMIZED = "minimized"
STATUS_CLOAKED = "cloaked"
STATUS_UNSUPPORTED = "unsupported"

_COINIT_APARTMENTTHREADED = 0x2
_CLSCTX_INPROC_SERVER = 0x1
_CLSCTX_LOCAL_SERVER = 0x4
_RPC_E_CHANGED_MODE = -2147417850
_S_OK = 0
_S_FALSE = 1
_E_NOINTERFACE = -2147467262
_DWMWA_CLOAKED = 14
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TREE_SCOPE_ELEMENT = 0x1
_TREE_SCOPE_DESCENDANTS = 0x4
_TREE_SCOPE_SUBTREE = _TREE_SCOPE_ELEMENT | _TREE_SCOPE_DESCENDANTS
_UIA_STRUCTURE_CHANGED_EVENT_ID = 20002
_UIA_LAYOUT_INVALIDATED_EVENT_ID = 20008
_UIA_BOUNDING_RECTANGLE_PROPERTY_ID = 30001
_UIA_IS_OFFSCREEN_PROPERTY_ID = 30022
_UIA_HEADER_EVENT_DEBOUNCE_SECONDS = 0.08
_PM_REMOVE = 0x0001
_QS_ALLINPUT = 0x04FF
_MWMO_INPUTAVAILABLE = 0x0004
_MWMO_ALERTABLE = 0x0002

_UIA_BUTTON_CONTROL_TYPE_ID = 50000
_UIA_COMBO_BOX_CONTROL_TYPE_ID = 50003
_UIA_EDIT_CONTROL_TYPE_ID = 50004
_UIA_HYPERLINK_CONTROL_TYPE_ID = 50005
_UIA_IMAGE_CONTROL_TYPE_ID = 50006
_UIA_MENU_ITEM_CONTROL_TYPE_ID = 50011
_UIA_TEXT_CONTROL_TYPE_ID = 50020
_UIA_GROUP_CONTROL_TYPE_ID = 50026
_UIA_SPLIT_BUTTON_CONTROL_TYPE_ID = 50031
_UIA_PANE_CONTROL_TYPE_ID = 50033
_UIA_TITLE_BAR_CONTROL_TYPE_ID = 50037

_UIA_HEADER_CANDIDATE_TYPES = {
    _UIA_BUTTON_CONTROL_TYPE_ID,
    _UIA_COMBO_BOX_CONTROL_TYPE_ID,
    _UIA_GROUP_CONTROL_TYPE_ID,
    _UIA_HYPERLINK_CONTROL_TYPE_ID,
    _UIA_IMAGE_CONTROL_TYPE_ID,
    _UIA_MENU_ITEM_CONTROL_TYPE_ID,
    _UIA_SPLIT_BUTTON_CONTROL_TYPE_ID,
}
_UIA_HEADER_CONTAINER_TYPES = {
    _UIA_PANE_CONTROL_TYPE_ID,
    _UIA_TITLE_BAR_CONTROL_TYPE_ID,
}
_UIA_BOTTOM_CANDIDATE_TYPES = _UIA_HEADER_CANDIDATE_TYPES | {
    _UIA_TEXT_CONTROL_TYPE_ID,
}

_MAX_UIA_NODES = 900
_MAX_HEADER_ROI_UIA_NODES = 1600
_MAX_BOTTOM_UIA_NODES = 1600
_HWND_REVERIFY_SECONDS = 2.0
_WINDOW_SNAPSHOT_CACHE_SECONDS = 0.02
_UIA_REFRESH_SECONDS = 0.75
_UIA_SLOW_SECONDS = 0.05
_MIN_CODEX_WINDOW_WIDTH = 300
_MIN_CODEX_WINDOW_HEIGHT = 200

_TITLE_BAR_HEIGHT = 45
_TITLE_SAFE_LEFT_RATIO = 0.155
_TITLE_SAFE_RIGHT_RATIO = 0.14
_TITLE_SAFE_LEFT_MIN = 154
_TITLE_SAFE_RIGHT_MIN = 172
_TITLE_SAFE_MIN_WIDTH = 320
_HEADER_ROI_MIN_TOP_OFFSET = 20
_HEADER_ROI_MAX_TOP_OFFSET = 125
_HEADER_ROI_MAX_HEIGHT = 56
_HEADER_ROI_MAX_CANDIDATE_HEIGHT = 56
_HEADER_MAIN_TITLEBAR_LEFT_FALLBACK = 305
_HEADER_MAIN_TITLEBAR_RIGHT_FALLBACK = 148
_HEADER_MAIN_TITLEBAR_TOP_INSET = 0
_HEADER_MAIN_TITLEBAR_BOTTOM_INSET = 7
_RIGHT_SIDEBAR_MARKERS = (
    "omx notepad",
    "priority context",
    "working memory",
    "manual",
    "审查",
    "终端",
    "浏览器",
    "侧边聊天",
    "ctrl+shift+g",
    "ctrl+t",
    "ctrl+p",
    "ctrl+alt+s",
)

_INPUT_BOTTOM_MARGIN = 36
_INPUT_FALLBACK_HEIGHT = 56
_INPUT_SAFE_LEFT_RATIO = 0.30
_INPUT_SAFE_RIGHT_RATIO = 0.28
_INPUT_SAFE_LEFT_MIN = 298
_INPUT_SAFE_RIGHT_MIN = 345
_INPUT_SAFE_MIN_WIDTH = 260
_BOTTOM_CONTROL_SCAN_TOP_RATIO = 0.32
_BOTTOM_CONTROL_SCAN_TOP_MIN = 180
_BOTTOM_ROW_MIN_ABOVE_INPUT = 44
_BOTTOM_ROW_MIN_BELOW_INPUT = 84

_LOGGER_NAME = "codex_usage_hud.windows_tracker"
_LOG_ENV_PATH = "CODEX_USAGE_HUD_WINDOW_LOG"
_LOG_ENV_LEVEL = "CODEX_USAGE_HUD_WINDOW_LOG_LEVEL"
#
# Codex Desktop 26.707+ renamed the Electron GUI executable from ``Codex.exe``
# to ``ChatGPT.exe`` (the Rust ``codex.exe`` under ``resources/`` is now the
# app-server backend, not the visible window).  We accept both so the HUD keeps
# tracking users on old installs *and* on the current release.
_CODEX_PROCESS_NAMES = {
    "codex.exe",
    "chatgpt.exe",
    "openai codex.exe",
}
_logger = logging.getLogger(_LOGGER_NAME)
_logger.addHandler(logging.NullHandler())
_logging_configured = False


def _is_codex_process_name(process: str) -> bool:
    process_lower = Path(str(process or "")).name.strip().lower()
    if process_lower in _CODEX_PROCESS_NAMES:
        return True
    # Some legacy builds shipped as "Codex Preview.exe" / "codex-desktop.exe";
    # anything whose stem starts with "codex" is still worth honouring.
    return process_lower.startswith("codex")


def window_tracker_log_path() -> Path:
    """Return the diagnostics log path used by the Windows tracker."""
    explicit = os.environ.get(_LOG_ENV_PATH)
    if explicit:
        return Path(explicit).expanduser()
    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "codex-usage-hud" / "window_tracker.log"


def configure_window_tracker_logging() -> Path | None:
    """Configure lightweight rolling diagnostics for slow Windows tracking paths."""
    global _logging_configured
    if _logging_configured:
        return window_tracker_log_path()
    path = window_tracker_log_path()
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
    level_name = os.environ.get(_LOG_ENV_LEVEL, "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    _logger.handlers = [
        item for item in _logger.handlers if not isinstance(item, logging.NullHandler)
    ]
    _logger.addHandler(handler)
    _logger.setLevel(level)
    _logger.propagate = False
    _logging_configured = True
    return path


@dataclass(frozen=True)
class PhysicalRect:
    """A physical screen-space rectangle in device pixels."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def as_xywh(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.width, self.height

    def intersects(self, other: "PhysicalRect") -> bool:
        return (
            self.left < other.right
            and self.right > other.left
            and self.top < other.bottom
            and self.bottom > other.top
        )

    def intersection(self, other: "PhysicalRect") -> "PhysicalRect | None":
        rect = PhysicalRect(
            max(self.left, other.left),
            max(self.top, other.top),
            min(self.right, other.right),
            min(self.bottom, other.bottom),
        )
        return None if rect.is_empty else rect

    @classmethod
    def from_win_rect(cls, rect: wintypes.RECT) -> "PhysicalRect":
        return cls(
            int(rect.left),
            int(rect.top),
            int(rect.right),
            int(rect.bottom),
        )


def _offset_rect(rect: PhysicalRect, dx: int, dy: int) -> PhysicalRect:
    return PhysicalRect(
        rect.left + dx,
        rect.top + dy,
        rect.right + dx,
        rect.bottom + dy,
    )


@dataclass(frozen=True)
class DockSnapshot:
    """Current Codex window and landmark state for HUD docking."""

    status: str
    hwnd: int = 0
    source: str = "none"
    window_rect: PhysicalRect | None = None
    title_bar: PhysicalRect | None = None
    input_box: PhysicalRect | None = None
    dock: tuple[int, int, int] | None = None
    reason: str = ""

    @property
    def visible(self) -> bool:
        return self.status == STATUS_VISIBLE and self.dock is not None


@dataclass(frozen=True)
class _Landmarks:
    title_bar: PhysicalRect
    input_box: PhysicalRect
    source: str
    nodes: int = 0
    duration_ms: float = 0.0


@dataclass(frozen=True)
class _UiNode:
    rect: PhysicalRect | None
    control_type: int
    name: str
    automation_id: str
    class_name: str
    offscreen: bool

    @property
    def search_text(self) -> str:
        return " ".join([self.name, self.automation_id, self.class_name]).lower()


@dataclass(frozen=True)
class _HeaderButtonCandidate:
    rect: PhysicalRect
    control_type: int
    name: str
    automation_id: str
    class_name: str
    depth: int

    @property
    def label(self) -> str:
        return " ".join(
            value.strip()
            for value in (self.name, self.automation_id, self.class_name)
            if value.strip()
        )


@dataclass(frozen=True)
class _HeaderButtonCollection:
    ordered: tuple[_HeaderButtonCandidate, ...]
    right_cluster: tuple[_HeaderButtonCandidate, ...]
    left_title_actions: tuple[_HeaderButtonCandidate, ...]


@dataclass(frozen=True)
class _HeaderEventTargetCandidate:
    candidate: _HeaderButtonCandidate
    element: int


@dataclass(frozen=True)
class HeaderRoiSnapshot:
    status: str
    hwnd: int = 0
    source: str = "none"
    window_rect: PhysicalRect | None = None
    header_rect: PhysicalRect | None = None
    roi: PhysicalRect | None = None
    nodes: int = 0
    duration_ms: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class BottomRoiSnapshot:
    status: str
    hwnd: int = 0
    source: str = "none"
    window_rect: PhysicalRect | None = None
    input_rect: PhysicalRect | None = None
    roi: PhysicalRect | None = None
    left_control: PhysicalRect | None = None
    right_control: PhysicalRect | None = None
    nodes: int = 0
    duration_ms: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class _HeaderRoiScan:
    header_rect: PhysicalRect
    collection: _HeaderButtonCollection
    roi: PhysicalRect | None
    nodes: int = 0
    right_sidebar_markers: int = 0
    reason: str = ""


@dataclass(frozen=True)
class _BottomRoiScan:
    input_rect: PhysicalRect
    left_control: _HeaderButtonCandidate | None
    left_blockers: tuple[_HeaderButtonCandidate, ...]
    right_control: _HeaderButtonCandidate | None
    roi: PhysicalRect | None
    nodes: int = 0
    candidates: int = 0
    row_candidates: int = 0
    reason: str = ""


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> "_GUID":
        parsed = uuid.UUID(value)
        fields = parsed.fields
        node = int(fields[5]).to_bytes(6, "big")
        data4 = (ctypes.c_ubyte * 8)(fields[3], fields[4], *node)
        return cls(fields[0], fields[1], fields[2], data4)


def _same_guid(left: _GUID, right: _GUID) -> bool:
    return bytes(ctypes.string_at(ctypes.byref(left), ctypes.sizeof(_GUID))) == bytes(
        ctypes.string_at(ctypes.byref(right), ctypes.sizeof(_GUID))
    )


_IID_IUNKNOWN = _GUID.from_string("{00000000-0000-0000-c000-000000000046}")
_IID_IUIAUTOMATION_EVENT_HANDLER = _GUID.from_string(
    "{146c3c17-f12e-4e22-8c27-f894b9b79c69}"
)
_IID_IUIAUTOMATION_PROPERTY_CHANGED_EVENT_HANDLER = _GUID.from_string(
    "{40cd37d4-c756-4b0c-8c6f-bddfeeb13b50}"
)


class _VariantValue(ctypes.Union):
    _fields_ = [
        ("lVal", ctypes.c_long),
        ("boolVal", ctypes.c_short),
        ("bstrVal", ctypes.c_void_p),
        ("pdispVal", ctypes.c_void_p),
    ]


class _Variant(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("value", _VariantValue),
    ]


_UiaQueryInterfaceProc = _WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_void_p,
    ctypes.POINTER(_GUID),
    ctypes.POINTER(ctypes.c_void_p),
)
_UiaAddRefProc = _WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_UiaReleaseProc = _WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_UiaHandleEventProc = _WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_int,
)


class _UiaAutomationEventHandlerVTable(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", _UiaQueryInterfaceProc),
        ("AddRef", _UiaAddRefProc),
        ("Release", _UiaReleaseProc),
        ("HandleAutomationEvent", _UiaHandleEventProc),
    ]


class _UiaAutomationEventHandlerObject(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_UiaAutomationEventHandlerVTable))]


class _UiaAutomationEventHandler:
    """Small COM object implementing IUIAutomationEventHandler."""

    def __init__(self, callback: Callable[[int, int], None]) -> None:
        self.callback = callback
        self._ref_count = 1
        self._query_interface = _UiaQueryInterfaceProc(self._query_interface_impl)
        self._add_ref = _UiaAddRefProc(self._add_ref_impl)
        self._release = _UiaReleaseProc(self._release_impl)
        self._handle_event = _UiaHandleEventProc(self._handle_event_impl)
        self._vtable = _UiaAutomationEventHandlerVTable(
            self._query_interface,
            self._add_ref,
            self._release,
            self._handle_event,
        )
        self._object = _UiaAutomationEventHandlerObject()
        self._object.lpVtbl = ctypes.pointer(self._vtable)
        self.ptr = ctypes.cast(ctypes.pointer(self._object), ctypes.c_void_p)

    def _query_interface_impl(
        self,
        this: int,
        riid: ctypes.POINTER(_GUID),
        out: ctypes.POINTER(ctypes.c_void_p),
    ) -> int:
        if not out:
            return _E_NOINTERFACE
        requested = riid.contents
        if _same_guid(requested, _IID_IUNKNOWN) or _same_guid(
            requested,
            _IID_IUIAUTOMATION_EVENT_HANDLER,
        ):
            out[0] = ctypes.c_void_p(this)
            self._add_ref_impl(this)
            return _S_OK
        out[0] = ctypes.c_void_p()
        return _E_NOINTERFACE

    def _add_ref_impl(self, _this: int) -> int:
        self._ref_count += 1
        return self._ref_count

    def _release_impl(self, _this: int) -> int:
        self._ref_count = max(1, self._ref_count - 1)
        return self._ref_count

    def _handle_event_impl(self, _this: int, sender: int, event_id: int) -> int:
        try:
            self.callback(int(sender or 0), int(event_id))
        except Exception:
            pass
        return _S_OK


_UiaHandlePropertyChangedProc = _WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_int,
    _Variant,
)


class _UiaPropertyChangedEventHandlerVTable(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", _UiaQueryInterfaceProc),
        ("AddRef", _UiaAddRefProc),
        ("Release", _UiaReleaseProc),
        ("HandlePropertyChangedEvent", _UiaHandlePropertyChangedProc),
    ]


class _UiaPropertyChangedEventHandlerObject(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_UiaPropertyChangedEventHandlerVTable))]


class _UiaPropertyChangedEventHandler:
    """Small COM object implementing IUIAutomationPropertyChangedEventHandler."""

    def __init__(self, callback: Callable[[int, int], None]) -> None:
        self.callback = callback
        self._ref_count = 1
        self._query_interface = _UiaQueryInterfaceProc(self._query_interface_impl)
        self._add_ref = _UiaAddRefProc(self._add_ref_impl)
        self._release = _UiaReleaseProc(self._release_impl)
        self._handle_event = _UiaHandlePropertyChangedProc(self._handle_event_impl)
        self._vtable = _UiaPropertyChangedEventHandlerVTable(
            self._query_interface,
            self._add_ref,
            self._release,
            self._handle_event,
        )
        self._object = _UiaPropertyChangedEventHandlerObject()
        self._object.lpVtbl = ctypes.pointer(self._vtable)
        self.ptr = ctypes.cast(ctypes.pointer(self._object), ctypes.c_void_p)

    def _query_interface_impl(
        self,
        this: int,
        riid: ctypes.POINTER(_GUID),
        out: ctypes.POINTER(ctypes.c_void_p),
    ) -> int:
        if not out:
            return _E_NOINTERFACE
        requested = riid.contents
        if _same_guid(requested, _IID_IUNKNOWN) or _same_guid(
            requested,
            _IID_IUIAUTOMATION_PROPERTY_CHANGED_EVENT_HANDLER,
        ):
            out[0] = ctypes.c_void_p(this)
            self._add_ref_impl(this)
            return _S_OK
        out[0] = ctypes.c_void_p()
        return _E_NOINTERFACE

    def _add_ref_impl(self, _this: int) -> int:
        self._ref_count += 1
        return self._ref_count

    def _release_impl(self, _this: int) -> int:
        self._ref_count = max(1, self._ref_count - 1)
        return self._ref_count

    def _handle_event_impl(
        self,
        _this: int,
        sender: int,
        property_id: int,
        _new_value: _Variant,
    ) -> int:
        try:
            self.callback(int(sender or 0), int(property_id))
        except Exception:
            pass
        return _S_OK


class _UiaProbe:
    """Minimal IUIAutomation wrapper for Edit and TitleBar landmarks."""

    _clsid_cuiautomation = _GUID.from_string("{ff48dba4-60ef-4201-aa87-54103eef594e}")
    _iid_iuiautomation = _GUID.from_string("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}")

    def __init__(self) -> None:
        self._local = threading.local()
        self._ole32 = ctypes.windll.ole32
        self._oleaut32 = ctypes.windll.oleaut32
        self._configure_api()

    def _configure_api(self) -> None:
        self._ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        self._ole32.CoInitializeEx.restype = ctypes.c_long
        self._ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(_GUID),
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._ole32.CoCreateInstance.restype = ctypes.c_long
        self._oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
        self._oleaut32.SysFreeString.restype = None

    def find_landmarks(self, hwnd: int, window_rect: PhysicalRect) -> _Landmarks | None:
        automation = self._automation_for_thread()
        if not automation:
            return None

        root = self._element_from_handle(automation, hwnd)
        if not root:
            return None

        walker = self._control_view_walker(automation) or self._raw_view_walker(automation)
        if not walker:
            self._release(root)
            return None

        try:
            return self._scan_tree(root, walker, window_rect)
        finally:
            self._release(walker)
            self._release(root)

    def find_header_roi(self, hwnd: int, window_rect: PhysicalRect) -> _HeaderRoiScan | None:
        automation = self._automation_for_thread()
        if not automation:
            return None

        root = self._element_from_handle(automation, hwnd)
        if not root:
            return None

        walker = self._control_view_walker(automation) or self._raw_view_walker(automation)
        if not walker:
            self._release(root)
            return None

        try:
            return self._scan_header_roi(root, walker, window_rect)
        finally:
            self._release(walker)
            self._release(root)

    def find_header_event_targets(
        self,
        hwnd: int,
        window_rect: PhysicalRect,
    ) -> tuple[int, ...]:
        automation = self._automation_for_thread()
        if not automation:
            return ()

        root = self._element_from_handle(automation, hwnd)
        if not root:
            return ()

        walker = self._control_view_walker(automation) or self._raw_view_walker(automation)
        if not walker:
            self._release(root)
            return ()

        try:
            return self._scan_header_event_targets(root, walker, window_rect)
        finally:
            self._release(walker)
            self._release(root)

    def find_header_roi_from_event_targets(
        self,
        elements: tuple[int, ...],
        window_rect: PhysicalRect,
    ) -> _HeaderRoiScan | None:
        if not elements:
            main_titlebar = CodexWindowTracker._main_titlebar_rect(window_rect)
            main_titlebar_roi = CodexWindowTracker._main_titlebar_roi_rect(
                [],
                window_rect,
                main_titlebar,
            )
            if main_titlebar_roi is None:
                return None
            return _HeaderRoiScan(
                header_rect=main_titlebar,
                collection=_HeaderButtonCollection(
                    ordered=(),
                    right_cluster=(),
                    left_title_actions=(),
                ),
                roi=main_titlebar_roi,
                nodes=0,
                reason="event-target-main-titlebar",
            )

        return self._scan_header_roi_from_event_targets(elements, window_rect)

    def find_bottom_roi(self, hwnd: int, window_rect: PhysicalRect) -> _BottomRoiScan | None:
        automation = self._automation_for_thread()
        if not automation:
            return None

        root = self._element_from_handle(automation, hwnd)
        if not root:
            return None

        control_walker = self._control_view_walker(automation)
        raw_walker = 0
        fallback_scan: _BottomRoiScan | None = None
        try:
            if control_walker:
                fallback_scan = self._scan_bottom_roi(root, control_walker, window_rect)
                if fallback_scan is not None and fallback_scan.roi is not None:
                    return fallback_scan

            raw_walker = self._raw_view_walker(automation)
            if raw_walker and raw_walker != control_walker:
                raw_scan = self._scan_bottom_roi(root, raw_walker, window_rect)
                if raw_scan is not None:
                    return raw_scan
            return fallback_scan
        finally:
            if control_walker:
                self._release(control_walker)
            if raw_walker and raw_walker != control_walker:
                self._release(raw_walker)
            self._release(root)

    def add_automation_event_handler(
        self,
        automation: int,
        event_id: int,
        element: int,
        handler: int,
    ) -> bool:
        func = self._method(
            automation,
            32,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        hr = int(
            func(
                automation,
                int(event_id),
                ctypes.c_void_p(element),
                _TREE_SCOPE_SUBTREE,
                None,
                ctypes.c_void_p(handler),
            )
        )
        return hr >= 0

    def remove_automation_event_handler(
        self,
        automation: int,
        event_id: int,
        element: int,
        handler: int,
    ) -> None:
        func = self._method(
            automation,
            33,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        try:
            func(
                automation,
                int(event_id),
                ctypes.c_void_p(element),
                ctypes.c_void_p(handler),
            )
        except Exception:
            pass

    def add_property_changed_event_handler(
        self,
        automation: int,
        element: int,
        handler: int,
        property_ids: tuple[int, ...],
    ) -> bool:
        if not property_ids:
            return False
        properties = (ctypes.c_int * len(property_ids))(*[int(item) for item in property_ids])
        func = self._method(
            automation,
            34,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_int,
        )
        hr = int(
            func(
                automation,
                ctypes.c_void_p(element),
                _TREE_SCOPE_SUBTREE,
                None,
                ctypes.c_void_p(handler),
                properties,
                len(property_ids),
            )
        )
        return hr >= 0

    def remove_property_changed_event_handler(
        self,
        automation: int,
        element: int,
        handler: int,
    ) -> None:
        func = self._method(
            automation,
            35,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        try:
            func(automation, ctypes.c_void_p(element), ctypes.c_void_p(handler))
        except Exception:
            pass

    def _automation_for_thread(self) -> int:
        automation = int(getattr(self._local, "automation", 0) or 0)
        if automation:
            return automation
        if not self._init_com_for_thread():
            return 0

        created = ctypes.c_void_p()
        hr = int(
            self._ole32.CoCreateInstance(
                ctypes.byref(self._clsid_cuiautomation),
                None,
                _CLSCTX_INPROC_SERVER | _CLSCTX_LOCAL_SERVER,
                ctypes.byref(self._iid_iuiautomation),
                ctypes.byref(created),
            )
        )
        if hr < 0 or not created.value:
            return 0
        self._local.automation = int(created.value)
        return int(created.value)

    def _init_com_for_thread(self) -> bool:
        if getattr(self._local, "com_ready", False):
            return True
        hr = int(self._ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED))
        if hr in {_S_OK, _S_FALSE, _RPC_E_CHANGED_MODE} or hr >= 0:
            self._local.com_ready = True
            return True
        return False

    def _element_from_handle(self, automation: int, hwnd: int) -> int:
        func = self._method(
            automation,
            6,
            ctypes.c_long,
            wintypes.HWND,
            ctypes.POINTER(ctypes.c_void_p),
        )
        element = ctypes.c_void_p()
        hr = int(func(automation, wintypes.HWND(hwnd), ctypes.byref(element)))
        if hr < 0 or not element.value:
            return 0
        return int(element.value)

    def _control_view_walker(self, automation: int) -> int:
        return self._automation_walker(automation, 14)

    def _raw_view_walker(self, automation: int) -> int:
        return self._automation_walker(automation, 16)

    def _automation_walker(self, automation: int, index: int) -> int:
        func = self._method(
            automation,
            index,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_void_p),
        )
        walker = ctypes.c_void_p()
        hr = int(func(automation, ctypes.byref(walker)))
        if hr < 0 or not walker.value:
            return 0
        return int(walker.value)

    def _scan_tree(
        self,
        root: int,
        walker: int,
        window_rect: PhysicalRect,
    ) -> _Landmarks | None:
        best_title: tuple[int, PhysicalRect] | None = None
        best_input: tuple[int, PhysicalRect] | None = None
        header_candidates: list[_HeaderButtonCandidate] = []
        stack: list[tuple[int, bool, int]] = [(root, False, 0)]
        visited = 0

        while stack and visited < _MAX_UIA_NODES:
            ptr, release_after, depth = stack.pop()
            visited += 1
            try:
                node = self._node(ptr)
                if node is not None:
                    title_score = self._score_title_bar(node, window_rect)
                    if title_score > 0 and node.rect is not None:
                        if best_title is None or title_score > best_title[0]:
                            best_title = (title_score, node.rect)

                    input_score = self._score_input_box(node, window_rect)
                    if input_score > 0 and node.rect is not None:
                        if best_input is None or input_score > best_input[0]:
                            best_input = (input_score, node.rect)

                    candidate = self._header_button_candidate(node, window_rect, depth)
                    if candidate is not None:
                        header_candidates.append(candidate)

                children = self._children(walker, ptr)
                for child in reversed(children):
                    stack.append((child, True, depth + 1))
            finally:
                if release_after:
                    self._release(ptr)

        if best_title is None and best_input is None:
            return None

        fallback = CodexWindowTracker.geometry_fallback(window_rect)
        title_bar = best_title[1] if best_title is not None else fallback.title_bar
        input_box = best_input[1] if best_input is not None else fallback.input_box
        self._log_header_button_candidates(header_candidates, window_rect, title_bar)
        source = "uia" if best_title is not None and best_input is not None else "uia+geometry"
        return _Landmarks(
            title_bar=title_bar,
            input_box=input_box,
            source=source,
            nodes=visited,
        )

    def _scan_header_roi(
        self,
        root: int,
        walker: int,
        window_rect: PhysicalRect,
    ) -> _HeaderRoiScan | None:
        best_title: tuple[int, PhysicalRect] | None = None
        header_candidates: list[_HeaderButtonCandidate] = []
        right_sidebar_markers = 0
        stack: list[tuple[int, bool, int]] = [(root, False, 0)]
        visited = 0

        while stack and visited < _MAX_HEADER_ROI_UIA_NODES:
            ptr, release_after, depth = stack.pop()
            visited += 1
            try:
                node = self._node(ptr)
                if node is not None:
                    if self._right_sidebar_marker(node, window_rect):
                        right_sidebar_markers += 1

                    title_candidate = self._header_roi_title_candidate(
                        node,
                        window_rect,
                    )
                    if title_candidate is not None:
                        title_score, title_rect = title_candidate
                        if best_title is None or title_score > best_title[0]:
                            best_title = (title_score, title_rect)

                    candidate = self._header_button_candidate(node, window_rect, depth)
                    if candidate is not None:
                        header_candidates.append(candidate)

                children = self._children(walker, ptr)
                for child in reversed(children):
                    stack.append((child, True, depth + 1))
            finally:
                if release_after:
                    self._release(ptr)

        if best_title is None and not header_candidates:
            return None

        if right_sidebar_markers >= 2:
            main_titlebar = self._main_titlebar_rect(window_rect)
            main_titlebar_roi = self._main_titlebar_roi_rect(
                header_candidates,
                window_rect,
                main_titlebar,
            )
            if main_titlebar_roi is not None:
                return _HeaderRoiScan(
                    header_rect=main_titlebar,
                    collection=_HeaderButtonCollection(
                        ordered=(),
                        right_cluster=(),
                        left_title_actions=(),
                    ),
                    roi=main_titlebar_roi,
                    nodes=visited,
                    right_sidebar_markers=right_sidebar_markers,
                    reason="right-sidebar-main-titlebar",
                )

        header_rect = (
            best_title[1]
            if best_title is not None
            else CodexWindowTracker.geometry_fallback(window_rect).title_bar
        )
        header_rects = [header_rect]
        fallback_header = CodexWindowTracker.geometry_fallback(window_rect).title_bar
        if not self._same_rect(fallback_header, header_rect):
            header_rects.append(fallback_header)
        inferred_header = self._candidate_header_rect(header_candidates, window_rect)
        if inferred_header is not None and not any(
            self._same_rect(inferred_header, item) for item in header_rects
        ):
            header_rects.append(inferred_header)

        fallback_scan: _HeaderRoiScan | None = None
        for candidate_header in header_rects:
            collection = self._collect_header_button_candidates(
                header_candidates,
                candidate_header,
            )
            roi, reason = self._header_roi_rect(collection, candidate_header)
            scan = _HeaderRoiScan(
                header_rect=candidate_header,
                collection=collection,
                roi=roi,
                nodes=visited,
                right_sidebar_markers=right_sidebar_markers,
                reason=reason,
            )
            if roi is not None:
                return scan
            if fallback_scan is None:
                fallback_scan = scan
        return fallback_scan

    def _scan_header_event_targets(
        self,
        root: int,
        walker: int,
        window_rect: PhysicalRect,
    ) -> tuple[int, ...]:
        best_title: tuple[int, PhysicalRect] | None = None
        target_candidates: list[_HeaderEventTargetCandidate] = []
        stack: list[tuple[int, bool, int]] = [(root, False, 0)]
        visited = 0

        while stack and visited < _MAX_HEADER_ROI_UIA_NODES:
            ptr, release_after, depth = stack.pop()
            visited += 1
            try:
                node = self._node(ptr)
                if node is not None:
                    title_candidate = self._header_roi_title_candidate(node, window_rect)
                    if title_candidate is not None:
                        title_score, title_rect = title_candidate
                        if best_title is None or title_score > best_title[0]:
                            best_title = (title_score, title_rect)

                    candidate = self._header_button_candidate(node, window_rect, depth)
                    if candidate is not None:
                        retained = self._retain(ptr)
                        if retained:
                            target_candidates.append(
                                _HeaderEventTargetCandidate(
                                    candidate=candidate,
                                    element=retained,
                                )
                            )

                children = self._children(walker, ptr)
                for child in reversed(children):
                    stack.append((child, True, depth + 1))
            finally:
                if release_after:
                    self._release(ptr)

        if not target_candidates:
            return ()

        header_candidates = [item.candidate for item in target_candidates]
        header_rect = (
            best_title[1]
            if best_title is not None
            else CodexWindowTracker.geometry_fallback(window_rect).title_bar
        )
        header_rects = [header_rect]
        fallback_header = CodexWindowTracker.geometry_fallback(window_rect).title_bar
        if not self._same_rect(fallback_header, header_rect):
            header_rects.append(fallback_header)
        inferred_header = self._candidate_header_rect(header_candidates, window_rect)
        if inferred_header is not None and not any(
            self._same_rect(inferred_header, item) for item in header_rects
        ):
            header_rects.append(inferred_header)

        candidate_elements = {
            item.candidate: item.element
            for item in target_candidates
        }
        selected: list[_HeaderButtonCandidate] = []
        for candidate_header in header_rects:
            collection = self._collect_header_button_candidates(
                header_candidates,
                candidate_header,
            )
            selected = list(self._header_event_candidates(collection))
            if selected:
                break
        if not selected:
            for item in target_candidates:
                self._release(item.element)
            return ()

        seen_elements: set[int] = set()
        elements: list[int] = []
        for candidate in selected:
            element = candidate_elements.get(candidate, 0)
            if not element or element in seen_elements:
                continue
            seen_elements.add(element)
            elements.append(element)

        selected_elements = set(elements)
        for item in target_candidates:
            if item.element not in selected_elements:
                self._release(item.element)
        return tuple(elements)

    def _scan_header_roi_from_event_targets(
        self,
        elements: tuple[int, ...],
        window_rect: PhysicalRect,
    ) -> _HeaderRoiScan | None:
        header_candidates: list[_HeaderButtonCandidate] = []
        for depth, element in enumerate(elements):
            node = self._node(element)
            if node is None or node.offscreen:
                continue
            candidate = self._header_button_candidate(node, window_rect, depth)
            if candidate is not None:
                header_candidates.append(candidate)

        fallback_header = CodexWindowTracker.geometry_fallback(window_rect).title_bar
        header_rects: list[PhysicalRect] = [fallback_header]
        inferred_header = self._candidate_header_rect(header_candidates, window_rect)
        if inferred_header is not None and not self._same_rect(inferred_header, fallback_header):
            header_rects.insert(0, inferred_header)

        fallback_scan: _HeaderRoiScan | None = None
        for candidate_header in header_rects:
            collection = self._collect_header_button_candidates(
                header_candidates,
                candidate_header,
            )
            roi, reason = self._header_roi_rect(collection, candidate_header)
            scan = _HeaderRoiScan(
                header_rect=candidate_header,
                collection=collection,
                roi=roi,
                nodes=len(elements),
                reason=f"event-target-{reason}",
            )
            if roi is not None:
                return scan
            if fallback_scan is None:
                fallback_scan = scan

        main_titlebar = CodexWindowTracker._main_titlebar_rect(window_rect)
        main_titlebar_roi = CodexWindowTracker._main_titlebar_roi_rect(
            header_candidates,
            window_rect,
            main_titlebar,
        )
        if main_titlebar_roi is not None:
            return _HeaderRoiScan(
                header_rect=main_titlebar,
                collection=_HeaderButtonCollection(
                    ordered=tuple(header_candidates),
                    right_cluster=(),
                    left_title_actions=(),
                ),
                roi=main_titlebar_roi,
                nodes=len(elements),
                reason="event-target-main-titlebar",
            )
        return fallback_scan

    @classmethod
    def _header_event_candidates(
        cls,
        collection: _HeaderButtonCollection,
    ) -> tuple[_HeaderButtonCandidate, ...]:
        # Event anchors must be discoverable in the always-collapsed header row.
        # Popup menus and expanded-state affordances are intentionally excluded.
        selected: list[_HeaderButtonCandidate] = []
        if collection.left_title_actions:
            selected.append(
                max(
                    collection.left_title_actions,
                    key=cls._left_header_event_score,
                )
            )

        selected.extend(collection.right_cluster)
        return tuple(selected)

    def _scan_bottom_roi(
        self,
        root: int,
        walker: int,
        window_rect: PhysicalRect,
    ) -> _BottomRoiScan | None:
        best_input: tuple[int, PhysicalRect] | None = None
        bottom_candidates: list[_HeaderButtonCandidate] = []
        stack: list[tuple[int, bool, int]] = [(root, False, 0)]
        visited = 0

        while stack and visited < _MAX_BOTTOM_UIA_NODES:
            ptr, release_after, depth = stack.pop()
            visited += 1
            try:
                node = self._node(ptr)
                if node is not None:
                    input_score = self._score_bottom_roi_input_box(node, window_rect)
                    if input_score > 0 and node.rect is not None:
                        if best_input is None or input_score > best_input[0]:
                            best_input = (input_score, node.rect)

                    candidate = self._bottom_control_candidate(node, window_rect, depth)
                    if candidate is not None:
                        bottom_candidates.append(candidate)

                children = self._children(walker, ptr)
                for child in reversed(children):
                    stack.append((child, True, depth + 1))
            finally:
                if release_after:
                    self._release(ptr)

        if best_input is None and not bottom_candidates:
            return None

        input_rect = (
            best_input[1]
            if best_input is not None
            else CodexWindowTracker.geometry_fallback(window_rect).input_box
        )
        row_candidates = self._bottom_roi_row_candidates(
            bottom_candidates,
            input_rect,
            window_rect,
        )
        left_control, right_control = self._bottom_roi_controls(row_candidates)
        left_blockers = self._bottom_left_blockers(
            row_candidates,
            left_control,
            right_control,
        )
        roi, reason = self._bottom_roi_rect(
            left_control,
            left_blockers,
            right_control,
            window_rect,
        )
        return _BottomRoiScan(
            input_rect=input_rect,
            left_control=left_control,
            left_blockers=left_blockers,
            right_control=right_control,
            roi=roi,
            nodes=visited,
            candidates=len(bottom_candidates),
            row_candidates=len(row_candidates),
            reason=reason,
        )

    @staticmethod
    def _header_button_candidate(
        node: _UiNode,
        window_rect: PhysicalRect,
        depth: int,
    ) -> _HeaderButtonCandidate | None:
        rect = node.rect
        if rect is None:
            return None
        clipped = rect.intersection(window_rect)
        if clipped is None:
            return None
        header_bottom = window_rect.top + 170
        if (
            clipped.top < window_rect.top + _HEADER_ROI_MIN_TOP_OFFSET
            or clipped.top > header_bottom
            or clipped.bottom < window_rect.top
        ):
            return None
        if clipped.height < 8 or clipped.width < 8:
            return None
        if clipped.height > _HEADER_ROI_MAX_CANDIDATE_HEIGHT:
            return None
        if node.control_type not in _UIA_HEADER_CANDIDATE_TYPES:
            text = node.search_text
            if node.control_type not in _UIA_HEADER_CONTAINER_TYPES:
                return None
            if not any(
                token in text
                for token in ("button", "menu", "action", "toolbar", "header", "caption")
            ):
                return None
        return _HeaderButtonCandidate(
            rect=clipped,
            control_type=node.control_type,
            name=node.name,
            automation_id=node.automation_id,
            class_name=node.class_name,
            depth=depth,
        )

    @staticmethod
    def _header_roi_title_candidate(
        node: _UiNode,
        window_rect: PhysicalRect,
    ) -> tuple[int, PhysicalRect] | None:
        rect = node.rect
        if rect is None or node.offscreen:
            return None
        clipped = rect.intersection(window_rect)
        if clipped is None:
            return None
        if clipped.width < max(160, int(window_rect.width * 0.45)):
            return None
        if (
            clipped.top < window_rect.top + _HEADER_ROI_MIN_TOP_OFFSET
            or clipped.top > window_rect.top + _HEADER_ROI_MAX_TOP_OFFSET
            or clipped.height > _HEADER_ROI_MAX_HEIGHT
        ):
            return None
        if clipped.bottom > window_rect.top + 170:
            return None
        text = node.search_text
        has_header_cue = (
            node.control_type == _UIA_TITLE_BAR_CONTROL_TYPE_ID
            or "title" in text
            or "caption" in text
            or "header" in text
            or "toolbar" in text
            or "codex" in text
        )
        if not has_header_cue:
            return None

        score = clipped.width
        if node.control_type == _UIA_TITLE_BAR_CONTROL_TYPE_ID:
            score += 5000
        if "title" in text or "caption" in text:
            score += 700
        if "header" in text or "toolbar" in text:
            score += 900
        if "codex" in text:
            score += 500
        preferred_top = window_rect.top + _TITLE_BAR_HEIGHT
        score += max(0, 500 - abs(clipped.top - preferred_top) * 8)
        score -= max(0, clipped.height - _TITLE_BAR_HEIGHT) * 8
        return max(0, int(score)), clipped

    @staticmethod
    def _right_sidebar_marker(node: _UiNode, window_rect: PhysicalRect) -> bool:
        rect = node.rect
        if rect is None or node.offscreen:
            return False
        clipped = rect.intersection(window_rect)
        if clipped is None:
            return False
        center_x = (clipped.left + clipped.right) / 2
        if center_x < window_rect.left + (window_rect.width * 0.50):
            return False
        if clipped.top < window_rect.top + 80:
            return False
        if clipped.height < 8 or clipped.height > 96:
            return False
        text = node.search_text
        if not text:
            return False
        return any(marker in text for marker in _RIGHT_SIDEBAR_MARKERS)

    @staticmethod
    def _window_frame_inset(window_rect: PhysicalRect) -> int:
        if window_rect.top >= 0:
            return 0
        return max(0, min(12, -window_rect.top))

    @classmethod
    def _main_titlebar_rect(cls, window_rect: PhysicalRect) -> PhysicalRect:
        inset = cls._window_frame_inset(window_rect)
        return PhysicalRect(
            window_rect.left + inset,
            window_rect.top + inset,
            window_rect.right - inset,
            window_rect.top + inset + _TITLE_BAR_HEIGHT,
        )

    @classmethod
    def _main_titlebar_roi_rect(
        cls,
        candidates: list[_HeaderButtonCandidate],
        window_rect: PhysicalRect,
        titlebar_rect: PhysicalRect,
    ) -> PhysicalRect | None:
        left = titlebar_rect.left + _HEADER_MAIN_TITLEBAR_LEFT_FALLBACK
        right = titlebar_rect.right - _HEADER_MAIN_TITLEBAR_RIGHT_FALLBACK
        for candidate in candidates:
            text = candidate.label.lower()
            if (
                ("帮助" in text or "help" in text)
                and candidate.rect.left < titlebar_rect.left + 380
                and candidate.rect.intersection(titlebar_rect) is not None
            ):
                left = max(left, candidate.rect.right + 14)
            if (
                ("最小化" in text or "minimize" in text)
                and candidate.rect.right > titlebar_rect.right - 220
                and candidate.rect.intersection(titlebar_rect) is not None
            ):
                right = min(right, candidate.rect.left - 14)

        if right - left < max(240, int(window_rect.width * 0.25)):
            return None
        top = titlebar_rect.top + _HEADER_MAIN_TITLEBAR_TOP_INSET
        bottom = titlebar_rect.bottom - _HEADER_MAIN_TITLEBAR_BOTTOM_INSET
        if bottom <= top:
            return None
        return PhysicalRect(left, top, right, bottom)

    @staticmethod
    def _bottom_control_candidate(
        node: _UiNode,
        window_rect: PhysicalRect,
        depth: int,
    ) -> _HeaderButtonCandidate | None:
        rect = node.rect
        if rect is None or node.offscreen:
            return None
        clipped = rect.intersection(window_rect)
        if clipped is None:
            return None
        scan_top = window_rect.top + max(
            _BOTTOM_CONTROL_SCAN_TOP_MIN,
            int(window_rect.height * _BOTTOM_CONTROL_SCAN_TOP_RATIO),
        )
        if clipped.bottom < scan_top:
            return None
        if clipped.height < 8 or clipped.width < 8:
            return None
        if node.control_type not in _UIA_BOTTOM_CANDIDATE_TYPES:
            return None
        return _HeaderButtonCandidate(
            rect=clipped,
            control_type=node.control_type,
            name=node.name,
            automation_id=node.automation_id,
            class_name=node.class_name,
            depth=depth,
        )

    @classmethod
    def _bottom_roi_row_candidates(
        cls,
        candidates: list[_HeaderButtonCandidate],
        input_rect: PhysicalRect,
        window_rect: PhysicalRect,
    ) -> list[_HeaderButtonCandidate]:
        if not candidates:
            return []

        band_above = max(
            _BOTTOM_ROW_MIN_ABOVE_INPUT,
            min(96, int(input_rect.height * 0.55)),
        )
        band_below = max(
            _BOTTOM_ROW_MIN_BELOW_INPUT,
            min(132, int(input_rect.height * 0.85)),
        )
        band_top = max(window_rect.top, input_rect.bottom - band_above)
        band_bottom = min(window_rect.bottom, input_rect.bottom + band_below)
        horizontal_left_margin = max(32, min(48, int(input_rect.width * 0.12)))
        horizontal_right_margin = max(96, min(180, int(input_rect.width * 0.30)))
        horizontal_min = max(window_rect.left, input_rect.left - horizontal_left_margin)
        horizontal_max = min(window_rect.right, input_rect.right + horizontal_right_margin)
        max_height = max(56, min(128, int(input_rect.height * 0.95)))
        eligible = [
            item
            for item in candidates
            if horizontal_min <= ((item.rect.left + item.rect.right) / 2) <= horizontal_max
            if item.rect.bottom >= band_top
            and item.rect.top <= band_bottom
            and item.rect.height <= max_height
        ]
        if len(eligible) <= 2:
            return eligible

        tolerance = max(14, min(30, int(input_rect.height * 0.20)))
        rows: list[list[_HeaderButtonCandidate]] = []
        for item in sorted(
            eligible,
            key=lambda candidate: (
                candidate.rect.top + candidate.rect.bottom,
                candidate.rect.left,
                candidate.depth,
            ),
        ):
            center_y = (item.rect.top + item.rect.bottom) / 2
            for row in rows:
                row_center = sum(
                    (candidate.rect.top + candidate.rect.bottom) / 2
                    for candidate in row
                ) / len(row)
                if abs(center_y - row_center) <= tolerance:
                    row.append(item)
                    break
            else:
                rows.append([item])

        def row_score(row: list[_HeaderButtonCandidate]) -> tuple[int, int, int, int]:
            center_y = int(
                sum(
                    (candidate.rect.top + candidate.rect.bottom) / 2
                    for candidate in row
                )
                / len(row)
            )
            distance = abs(center_y - input_rect.bottom)
            semantic = sum(
                1
                for candidate in row
                if cls._is_bottom_left_control(candidate)
                or cls._is_bottom_right_control(candidate)
                or cls._is_bottom_left_blocker(candidate)
            )
            width = max(candidate.rect.right for candidate in row) - min(
                candidate.rect.left for candidate in row
            )
            return (
                semantic,
                max(0, 120 - distance),
                min(width, window_rect.width),
                len(row),
            )

        selected = max(rows, key=row_score)
        return sorted(
            selected,
            key=lambda item: (item.rect.left, item.rect.top, item.depth),
        )

    @classmethod
    def _bottom_roi_controls(
        cls,
        candidates: list[_HeaderButtonCandidate],
    ) -> tuple[_HeaderButtonCandidate | None, _HeaderButtonCandidate | None]:
        ordered = sorted(
            candidates,
            key=lambda item: (
                -item.rect.bottom,
                item.rect.left,
                item.depth,
                item.control_type,
            ),
        )

        left_matches = [
            item
            for item in ordered
            if cls._is_bottom_left_control(item)
        ]
        left_control = left_matches[0] if left_matches else None

        right_matches = [
            item
            for item in ordered
            if cls._is_bottom_right_control(item)
            and (left_control is None or item.rect.left > left_control.rect.right)
        ]
        right_control = right_matches[0] if right_matches else None
        if right_control is None:
            right_control = cls._bottom_geometry_right_control(ordered, left_control)
        if left_control is None:
            left_control = cls._bottom_geometry_left_control(ordered, right_control)
        return left_control, right_control

    @classmethod
    def _bottom_geometry_left_control(
        cls,
        candidates: list[_HeaderButtonCandidate],
        right_control: _HeaderButtonCandidate | None,
    ) -> _HeaderButtonCandidate | None:
        if not candidates:
            return None
        right_limit = right_control.rect.left if right_control is not None else None
        eligible = [
            item
            for item in candidates
            if (right_limit is None or item.rect.right <= right_limit)
            and not cls._is_bottom_left_blocker(item)
            and not cls._is_bottom_right_control(item)
            and not cls._is_bottom_send_control(item)
            and item.control_type != _UIA_TEXT_CONTROL_TYPE_ID
        ]
        if not eligible:
            return None

        ordered = sorted(eligible, key=lambda item: (item.rect.left, item.rect.top))
        row_left = ordered[0].rect.left
        limit = row_left + max(96, int((ordered[-1].rect.right - row_left) * 0.30))
        max_gap = max(22, min(36, max(item.rect.height for item in ordered)))
        cluster: list[_HeaderButtonCandidate] = []
        cluster_right = row_left
        for item in ordered:
            if cluster and item.rect.left - cluster_right > max_gap:
                break
            if cluster and item.rect.left > limit:
                break
            cluster.append(item)
            cluster_right = max(cluster_right, item.rect.right)
        return cls._combined_bottom_candidate(cluster, "geometry-left-cluster")

    @classmethod
    def _bottom_geometry_right_control(
        cls,
        candidates: list[_HeaderButtonCandidate],
        left_control: _HeaderButtonCandidate | None,
    ) -> _HeaderButtonCandidate | None:
        if not candidates:
            return None
        eligible = [
            item
            for item in candidates
            if (left_control is None or item.rect.left >= left_control.rect.right)
            and not cls._is_bottom_left_blocker(item)
            and not cls._is_bottom_left_control(item)
        ]
        if not eligible:
            return None

        ordered = sorted(eligible, key=lambda item: (item.rect.left, item.rect.top))
        send_control = cls._bottom_send_control(ordered)
        right_limit = send_control.rect.left if send_control is not None else None
        pool = [
            item
            for item in ordered
            if (right_limit is None or item.rect.right <= right_limit)
            and not cls._is_bottom_send_control(item)
        ]
        if not pool:
            return None

        row_left = min(item.rect.left for item in ordered)
        row_right = max(item.rect.right for item in ordered)
        right_zone_start = row_left + int((row_right - row_left) * 0.42)
        semantic_pool = [item for item in pool if cls._is_bottom_right_control(item)]
        zone_pool = semantic_pool or [
            item for item in pool if item.rect.left >= right_zone_start
        ]
        if not zone_pool:
            return None

        anchor = max(zone_pool, key=lambda item: (item.rect.right, item.rect.left))
        max_gap = max(18, min(32, anchor.rect.height))
        cluster = [anchor]
        cluster_left = anchor.rect.left
        for item in reversed(pool[: pool.index(anchor)]):
            if item.rect.left < right_zone_start and not cls._is_bottom_right_control(item):
                break
            if cluster_left - item.rect.right > max_gap:
                break
            cluster.append(item)
            cluster_left = min(cluster_left, item.rect.left)
        return cls._combined_bottom_candidate(cluster, "geometry-right-cluster")

    @staticmethod
    def _combined_bottom_candidate(
        candidates: list[_HeaderButtonCandidate],
        name: str,
    ) -> _HeaderButtonCandidate | None:
        if not candidates:
            return None
        return _HeaderButtonCandidate(
            rect=PhysicalRect(
                min(item.rect.left for item in candidates),
                min(item.rect.top for item in candidates),
                max(item.rect.right for item in candidates),
                max(item.rect.bottom for item in candidates),
            ),
            control_type=_UIA_GROUP_CONTROL_TYPE_ID,
            name=name,
            automation_id="",
            class_name="",
            depth=min(item.depth for item in candidates),
        )

    @classmethod
    def _bottom_left_blockers(
        cls,
        candidates: list[_HeaderButtonCandidate],
        left_control: _HeaderButtonCandidate | None,
        right_control: _HeaderButtonCandidate | None,
    ) -> tuple[_HeaderButtonCandidate, ...]:
        if left_control is None or right_control is None:
            return ()
        row_top = min(left_control.rect.top, right_control.rect.top) - 8
        row_bottom = max(left_control.rect.bottom, right_control.rect.bottom) + 8
        blockers = [
            item
            for item in candidates
            if item is not left_control
            and item is not right_control
            and item.rect.left >= left_control.rect.right
            and item.rect.right <= right_control.rect.left
            and item.rect.bottom >= row_top
            and item.rect.top <= row_bottom
            and cls._is_bottom_left_blocker(item)
        ]
        return tuple(
            sorted(
                blockers,
                key=lambda item: (item.rect.left, item.rect.top, item.depth),
            )
        )

    @staticmethod
    def _is_bottom_left_control(candidate: _HeaderButtonCandidate) -> bool:
        if candidate.control_type == _UIA_TEXT_CONTROL_TYPE_ID:
            return False
        text = candidate.label.lower()
        return (
            "完全访问" in text
            or "更改权限" in text
            or "permission" in text
            or "access" in text
        )

    @staticmethod
    def _is_bottom_right_control(candidate: _HeaderButtonCandidate) -> bool:
        text = candidate.label.lower()
        return (
            "选择模型" in text
            or "超高" in text
            or "5.5" in text
            or "model" in text
            or "ctrl+shift" in text
        )

    @staticmethod
    def _is_bottom_left_blocker(candidate: _HeaderButtonCandidate) -> bool:
        text = candidate.label.lower()
        return (
            "目标" in text
            or "计划" in text
            or "goal" in text
            or "plan" in text
        )

    @staticmethod
    def _is_bottom_send_control(candidate: _HeaderButtonCandidate) -> bool:
        text = candidate.label.lower()
        return (
            "发送" in text
            or "send" in text
            or "submit" in text
            or "arrow up" in text
            or "arrow-up" in text
        )

    @classmethod
    def _bottom_send_control(
        cls,
        candidates: list[_HeaderButtonCandidate],
    ) -> _HeaderButtonCandidate | None:
        if not candidates:
            return None
        semantic = [item for item in candidates if cls._is_bottom_send_control(item)]
        if semantic:
            return max(semantic, key=lambda item: (item.rect.right, item.rect.left))
        if len(candidates) < 2:
            return None

        ordered = sorted(candidates, key=lambda item: (item.rect.left, item.rect.top))
        rightmost = ordered[-1]
        previous = ordered[-2]
        if (
            rightmost.rect.width <= 56
            and rightmost.rect.height <= 56
            and rightmost.rect.width <= max(12, rightmost.rect.height * 2)
            and rightmost.rect.left - previous.rect.right <= max(
                72,
                rightmost.rect.height * 3,
            )
        ):
            return rightmost
        return None

    @staticmethod
    def _bottom_roi_rect(
        left_control: _HeaderButtonCandidate | None,
        left_blockers: tuple[_HeaderButtonCandidate, ...],
        right_control: _HeaderButtonCandidate | None,
        window_rect: PhysicalRect,
    ) -> tuple[PhysicalRect | None, str]:
        if left_control is None:
            return None, "missing-left-permission"
        if right_control is None:
            return None, "missing-right-model"

        controls_height = max(left_control.rect.height, right_control.rect.height)
        padding = max(8, min(16, int(controls_height * 0.45)))
        left_edge = left_control.rect.right
        if left_blockers:
            left_edge = max(left_edge, max(item.rect.right for item in left_blockers))
        left = left_edge + padding
        right = right_control.rect.left - padding
        min_width = max(120, int(window_rect.width * 0.12))
        if right - left < min_width:
            return None, "roi-too-narrow"

        inset_y = max(0, min(4, int(controls_height * 0.10)))
        top = max(window_rect.top, min(left_control.rect.top, right_control.rect.top) + inset_y)
        bottom = min(
            window_rect.bottom,
            max(left_control.rect.bottom, right_control.rect.bottom) - inset_y,
        )
        if bottom <= top:
            return None, "roi-too-short"
        return PhysicalRect(left, top, right, bottom), "ok"

    @staticmethod
    def _same_rect(left: PhysicalRect, right: PhysicalRect) -> bool:
        return (
            left.left == right.left
            and left.top == right.top
            and left.right == right.right
            and left.bottom == right.bottom
        )

    @staticmethod
    def _candidate_header_rect(
        candidates: list[_HeaderButtonCandidate],
        window_rect: PhysicalRect,
    ) -> PhysicalRect | None:
        top_min = window_rect.top + _HEADER_ROI_MIN_TOP_OFFSET
        top_limit = window_rect.top + _HEADER_ROI_MAX_TOP_OFFSET
        top_candidates = sorted(
            (
                item
                for item in candidates
                if top_min <= item.rect.top <= top_limit
                and item.rect.height <= _HEADER_ROI_MAX_CANDIDATE_HEIGHT
            ),
            key=lambda item: (
                item.rect.top + item.rect.bottom,
                item.rect.left,
                item.depth,
            ),
        )
        if len(top_candidates) < 2:
            return None

        rows: list[list[_HeaderButtonCandidate]] = []
        for item in top_candidates:
            center_y = (item.rect.top + item.rect.bottom) / 2
            for row in rows:
                row_center = sum(
                    (candidate.rect.top + candidate.rect.bottom) / 2
                    for candidate in row
                ) / len(row)
                if abs(center_y - row_center) <= 12:
                    row.append(item)
                    break
            else:
                rows.append([item])

        top_candidates = max(
            rows,
            key=lambda row: (
                len(row),
                max(item.rect.right for item in row) - min(item.rect.left for item in row),
                -abs(
                    (
                        min(item.rect.top for item in row)
                        + max(item.rect.bottom for item in row)
                    )
                    / 2
                    - (window_rect.top + _TITLE_BAR_HEIGHT)
                ),
            ),
        )
        if len(top_candidates) < 2:
            return None
        left = min(item.rect.left for item in top_candidates)
        top = min(item.rect.top for item in top_candidates)
        right = max(item.rect.right for item in top_candidates)
        bottom = max(item.rect.bottom for item in top_candidates)
        if right - left < max(180, int(window_rect.width * 0.25)):
            return None
        pad_y = max(6, min(14, int((bottom - top) * 0.35)))
        return PhysicalRect(
            max(window_rect.left, left - 24),
            max(window_rect.top, top - pad_y),
            min(window_rect.right, right + 24),
            min(window_rect.top + 170, bottom + pad_y),
        )

    @staticmethod
    def _format_header_candidate(candidate: _HeaderButtonCandidate) -> str:
        label = " ".join(candidate.label.split())[:80]
        return (
            f"type={candidate.control_type} depth={candidate.depth} "
            f"rect=({candidate.rect.left},{candidate.rect.top},{candidate.rect.right},{candidate.rect.bottom}) "
            f"label={label!r}"
        )

    @classmethod
    def _left_header_event_score(
        cls,
        candidate: _HeaderButtonCandidate,
    ) -> int:
        return (
            candidate.rect.right
            - candidate.rect.width
            - (candidate.depth * 30)
            + cls._left_header_action_bonus(candidate)
        )

    @staticmethod
    def _left_header_action_bonus(candidate: _HeaderButtonCandidate) -> int:
        text = candidate.label.lower()
        return int(
            "对话操作" in text
            or "conversation action" in text
            or "conversation menu" in text
        ) * 20

    @staticmethod
    def _is_compact_header_candidate(
        candidate: _HeaderButtonCandidate,
        header_rect: PhysicalRect,
    ) -> bool:
        max_width = max(96, int(header_rect.height * 4), int(header_rect.width * 0.22))
        return candidate.rect.width <= max_width

    @staticmethod
    def _header_candidate_bounds(
        candidates: tuple[_HeaderButtonCandidate, ...],
    ) -> str:
        if not candidates:
            return "none"
        left = min(item.rect.left for item in candidates)
        top = min(item.rect.top for item in candidates)
        right = max(item.rect.right for item in candidates)
        bottom = max(item.rect.bottom for item in candidates)
        return f"({left},{top},{right},{bottom})"

    @classmethod
    def _header_roi_rect(
        cls,
        collection: _HeaderButtonCollection,
        header_rect: PhysicalRect,
    ) -> tuple[PhysicalRect | None, str]:
        if not collection.right_cluster:
            return None, "missing-right-cluster"

        padding = max(10, min(18, int(header_rect.height * 0.35)))
        left = header_rect.left + padding
        if collection.left_title_actions:
            left = max(
                left,
                max(item.rect.right for item in collection.left_title_actions) + padding,
            )
        right = min(item.rect.left for item in collection.right_cluster) - padding
        min_width = max(120, int(header_rect.width * 0.18))
        if right - left < min_width:
            return None, "roi-too-narrow"

        inset_y = max(2, min(8, int(header_rect.height * 0.16)))
        return (
            PhysicalRect(
                left,
                header_rect.top + inset_y,
                right,
                header_rect.bottom - inset_y,
            ),
            "ok",
        )

    @classmethod
    def _collect_header_button_candidates(
        cls,
        candidates: list[_HeaderButtonCandidate],
        header_rect: PhysicalRect,
    ) -> _HeaderButtonCollection:
        ordered = tuple(
            sorted(
                (
                    item
                    for item in candidates
                    if item.rect.intersection(header_rect) is not None
                    and item.rect.height <= max(48, int(header_rect.height * 1.5))
                ),
                key=lambda item: (
                    item.rect.left,
                    item.rect.top,
                    item.depth,
                    item.control_type,
                ),
            )
        )
        right_start = header_rect.left + int(header_rect.width * 0.55)
        right_edge_start = header_rect.right - max(180, int(header_rect.width * 0.24))
        right_start = max(right_start, right_edge_start)
        right_candidates = [
            item
            for item in ordered
            if item.rect.left >= right_start
            and cls._is_compact_header_candidate(item, header_rect)
        ]
        right_cluster: list[_HeaderButtonCandidate] = []
        if right_candidates:
            max_gap = max(24, int(header_rect.height * 1.25))
            right_cluster.append(right_candidates[-1])
            for item in reversed(right_candidates[:-1]):
                leftmost = right_cluster[-1]
                gap = leftmost.rect.left - item.rect.right
                if gap > max_gap:
                    break
                shallowest_depth = min(candidate.depth for candidate in right_cluster)
                if item.depth > shallowest_depth + 2:
                    break
                right_cluster.append(item)
            right_cluster.reverse()

        left_limit = header_rect.left + int(header_rect.width * 0.45)
        if right_cluster:
            left_limit = min(left_limit, right_cluster[0].rect.left)
        left_candidates = [
            item
            for item in ordered
            if item.rect.left < left_limit
            and cls._is_compact_header_candidate(item, header_rect)
        ]
        left_title_actions = (
            (max(left_candidates, key=cls._left_header_event_score),)
            if left_candidates
            else ()
        )
        return _HeaderButtonCollection(
            ordered=ordered,
            right_cluster=tuple(right_cluster),
            left_title_actions=left_title_actions,
        )

    @classmethod
    def _log_header_button_candidates(
        cls,
        candidates: list[_HeaderButtonCandidate],
        window_rect: PhysicalRect,
        header_rect: PhysicalRect,
    ) -> None:
        collection = cls._collect_header_button_candidates(candidates, header_rect)
        if not collection.ordered:
            _logger.info(
                "uia_header_buttons count=0 window=(%s,%s,%s,%s) header=(%s,%s,%s,%s)",
                window_rect.left,
                window_rect.top,
                window_rect.right,
                window_rect.bottom,
                header_rect.left,
                header_rect.top,
                header_rect.right,
                header_rect.bottom,
            )
            return
        sample = " | ".join(
            cls._format_header_candidate(item) for item in collection.ordered[:24]
        )
        right_sample = " | ".join(
            cls._format_header_candidate(item) for item in collection.right_cluster[:12]
        )
        left_sample = " | ".join(
            cls._format_header_candidate(item)
            for item in collection.left_title_actions[-12:]
        )
        _logger.info(
            "uia_header_buttons count=%s right_count=%s left_count=%s "
            "window=(%s,%s,%s,%s) header=(%s,%s,%s,%s) "
            "right_bounds=%s left_bounds=%s sample=%s right_cluster=%s left_title=%s",
            len(collection.ordered),
            len(collection.right_cluster),
            len(collection.left_title_actions),
            window_rect.left,
            window_rect.top,
            window_rect.right,
            window_rect.bottom,
            header_rect.left,
            header_rect.top,
            header_rect.right,
            header_rect.bottom,
            cls._header_candidate_bounds(collection.right_cluster),
            cls._header_candidate_bounds(collection.left_title_actions),
            sample,
            right_sample,
            left_sample,
        )

    @classmethod
    def _log_header_roi_scan(
        cls,
        scan: _HeaderRoiScan,
        window_rect: PhysicalRect,
    ) -> None:
        roi = scan.roi
        roi_text = (
            f"({roi.left},{roi.top},{roi.right},{roi.bottom})"
            if roi is not None
            else "none"
        )
        _logger.info(
            "uia_header_roi_demo status=%s reason=%s window=(%s,%s,%s,%s) "
            "header=(%s,%s,%s,%s) roi=%s right_count=%s left_count=%s "
            "right_bounds=%s left_bounds=%s nodes=%s right_sidebar_markers=%s",
            "visible" if roi is not None else "not_found",
            scan.reason,
            window_rect.left,
            window_rect.top,
            window_rect.right,
            window_rect.bottom,
            scan.header_rect.left,
            scan.header_rect.top,
            scan.header_rect.right,
            scan.header_rect.bottom,
            roi_text,
            len(scan.collection.right_cluster),
            len(scan.collection.left_title_actions),
            cls._header_candidate_bounds(scan.collection.right_cluster),
            cls._header_candidate_bounds(scan.collection.left_title_actions),
            scan.nodes,
            scan.right_sidebar_markers,
        )

    @classmethod
    def _log_bottom_roi_scan(
        cls,
        scan: _BottomRoiScan,
        window_rect: PhysicalRect,
    ) -> None:
        roi = scan.roi
        roi_text = (
            f"({roi.left},{roi.top},{roi.right},{roi.bottom})"
            if roi is not None
            else "none"
        )
        left_text = (
            cls._format_header_candidate(scan.left_control)
            if scan.left_control is not None
            else "none"
        )
        right_text = (
            cls._format_header_candidate(scan.right_control)
            if scan.right_control is not None
            else "none"
        )
        blockers_text = " | ".join(
            cls._format_header_candidate(item) for item in scan.left_blockers
        )
        _logger.info(
            "uia_bottom_roi_demo status=%s reason=%s window=(%s,%s,%s,%s) "
            "input=(%s,%s,%s,%s) roi=%s left=%s blockers=%s right=%s "
            "nodes=%s candidates=%s row_candidates=%s",
            "visible" if roi is not None else "not_found",
            scan.reason,
            window_rect.left,
            window_rect.top,
            window_rect.right,
            window_rect.bottom,
            scan.input_rect.left,
            scan.input_rect.top,
            scan.input_rect.right,
            scan.input_rect.bottom,
            roi_text,
            left_text,
            blockers_text or "none",
            right_text,
            scan.nodes,
            scan.candidates,
            scan.row_candidates,
        )

    def _children(self, walker: int, element: int) -> list[int]:
        first_child = self._walker_first_child(walker, element)
        if not first_child:
            return []
        children: list[int] = []
        child = first_child
        while child and len(children) < _MAX_UIA_NODES:
            children.append(child)
            child = self._walker_next_sibling(walker, child)
        return children

    def _walker_first_child(self, walker: int, element: int) -> int:
        func = self._method(
            walker,
            4,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        child = ctypes.c_void_p()
        hr = int(func(walker, element, ctypes.byref(child)))
        if hr < 0 or not child.value:
            return 0
        return int(child.value)

    def _walker_next_sibling(self, walker: int, element: int) -> int:
        func = self._method(
            walker,
            6,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        sibling = ctypes.c_void_p()
        hr = int(func(walker, element, ctypes.byref(sibling)))
        if hr < 0 or not sibling.value:
            return 0
        return int(sibling.value)

    def _node(self, element: int) -> _UiNode | None:
        try:
            rect = self._element_rect(element)
            return _UiNode(
                rect=rect,
                control_type=self._element_int(element, 21),
                name=self._element_bstr(element, 23),
                automation_id=self._element_bstr(element, 29),
                class_name=self._element_bstr(element, 30),
                offscreen=self._element_bool(element, 38),
            )
        except Exception:
            return None

    def _element_rect(self, element: int) -> PhysicalRect | None:
        func = self._method(
            element,
            43,
            ctypes.c_long,
            ctypes.POINTER(wintypes.RECT),
        )
        rect = wintypes.RECT()
        hr = int(func(element, ctypes.byref(rect)))
        if hr < 0:
            return None
        physical = PhysicalRect.from_win_rect(rect)
        return None if physical.is_empty else physical

    def _element_int(self, element: int, index: int) -> int:
        func = self._method(element, index, ctypes.c_long, ctypes.POINTER(ctypes.c_int))
        value = ctypes.c_int()
        hr = int(func(element, ctypes.byref(value)))
        return int(value.value) if hr >= 0 else 0

    def _element_bool(self, element: int, index: int) -> bool:
        func = self._method(element, index, ctypes.c_long, ctypes.POINTER(wintypes.BOOL))
        value = wintypes.BOOL()
        hr = int(func(element, ctypes.byref(value)))
        return bool(value.value) if hr >= 0 else False

    def _element_bstr(self, element: int, index: int) -> str:
        func = self._method(element, index, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))
        bstr = ctypes.c_void_p()
        hr = int(func(element, ctypes.byref(bstr)))
        if hr < 0 or not bstr.value:
            return ""
        try:
            return ctypes.wstring_at(bstr.value)
        finally:
            self._oleaut32.SysFreeString(bstr)

    @staticmethod
    def _score_title_bar(node: _UiNode, window_rect: PhysicalRect) -> int:
        rect = node.rect
        if rect is None or node.offscreen:
            return 0
        clipped = rect.intersection(window_rect)
        if clipped is None:
            return 0
        if clipped.width < max(160, int(window_rect.width * 0.45)):
            return 0
        if clipped.top > window_rect.top + 90 or clipped.height > 120:
            return 0

        score = clipped.width
        if node.control_type == _UIA_TITLE_BAR_CONTROL_TYPE_ID:
            score += 5000
        if "title" in node.search_text or "caption" in node.search_text:
            score += 700
        if "codex" in node.search_text:
            score += 500
        score += max(0, 600 - abs(clipped.top - window_rect.top) * 10)
        return score

    @staticmethod
    def _score_input_box(node: _UiNode, window_rect: PhysicalRect) -> int:
        rect = node.rect
        if rect is None:
            return 0
        clipped = rect.intersection(window_rect)
        if clipped is None:
            return 0
        if clipped.width < max(240, int(window_rect.width * 0.20)) or clipped.height < 18:
            return 0
        if clipped.height > max(180, int(window_rect.height * 0.30)):
            return 0

        text = node.search_text
        cue_words = (
            "prompt",
            "message",
            "chat",
            "composer",
            "input",
            "textarea",
            "ask",
            "type",
            "edit",
            "输入",
            "消息",
            "编辑",
        )
        has_cue = any(word in text for word in cue_words)
        is_edit = node.control_type == _UIA_EDIT_CONTROL_TYPE_ID
        if not is_edit and not has_cue:
            return 0

        center_y = clipped.top + (clipped.height / 2)
        if center_y < window_rect.top + (window_rect.height * 0.70):
            return 0
        score = clipped.width
        if is_edit:
            score += 3000
        if has_cue:
            score += 1200
        if center_y > window_rect.top + (window_rect.height * 0.75):
            score += 3000
        if clipped.bottom > window_rect.bottom - 180:
            score += 1800
        if node.offscreen:
            score -= 2000
        if "search" in text or "find" in text:
            score -= 600
        return max(0, int(score))

    @staticmethod
    def _score_bottom_roi_input_box(node: _UiNode, window_rect: PhysicalRect) -> int:
        rect = node.rect
        if rect is None:
            return 0
        clipped = rect.intersection(window_rect)
        if clipped is None:
            return 0
        if clipped.width < max(240, int(window_rect.width * 0.20)) or clipped.height < 18:
            return 0
        if clipped.height > max(240, int(window_rect.height * 0.45)):
            return 0

        text = node.search_text
        cue_words = (
            "prompt",
            "message",
            "chat",
            "composer",
            "input",
            "textarea",
            "ask",
            "type",
            "edit",
            "输入",
            "消息",
            "编辑",
        )
        has_cue = any(word in text for word in cue_words)
        is_edit = node.control_type == _UIA_EDIT_CONTROL_TYPE_ID
        if not is_edit and not has_cue:
            return 0

        center_y = clipped.top + (clipped.height / 2)
        if center_y < window_rect.top + (window_rect.height * 0.35):
            return 0
        score = clipped.width
        if is_edit:
            score += 3000
        if has_cue:
            score += 1200
        if center_y > window_rect.top + (window_rect.height * 0.55):
            score += 1300
        if center_y > window_rect.top + (window_rect.height * 0.70):
            score += 2200
        if clipped.bottom > window_rect.bottom - 180:
            score += 1800
        if node.offscreen:
            score -= 2000
        if "search" in text or "find" in text:
            score -= 600
        return max(0, int(score))

    def _retain(self, ptr: int) -> int:
        if not ptr:
            return 0
        try:
            func = self._method(ptr, 1, ctypes.c_ulong)
            func(ptr)
        except Exception:
            return 0
        return ptr

    def _release(self, ptr: int) -> None:
        if not ptr:
            return
        try:
            func = self._method(ptr, 2, ctypes.c_ulong)
            func(ptr)
        except Exception:
            return

    @staticmethod
    def _method(ptr: int, index: int, restype: object, *argtypes: object) -> object:
        vtable = ctypes.cast(
            ptr,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        return _WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


class _UiaHeaderRoiEventWatcher:
    """Invalidate cached header ROI when Codex header UIA layout changes."""

    def __init__(self, tracker: "CodexWindowTracker") -> None:
        self.tracker = tracker
        self.probe = tracker._uia_probe
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._hwnd = 0
        self._window_rect: PhysicalRect | None = None
        self._last_event_at = 0.0
        self._last_invalidated_at = 0.0
        self._registered_hwnd = 0
        self._event_handler: _UiaAutomationEventHandler | None = None
        self._property_handler: _UiaPropertyChangedEventHandler | None = None
        self._owned_target_elements: tuple[int, ...] = ()

    def ensure(self, hwnd: int, window_rect: PhysicalRect | None = None) -> None:
        if self.probe is None or not hwnd:
            return
        if self._thread is not None and self._thread.is_alive() and self._hwnd == hwnd:
            if window_rect is not None:
                self._window_rect = window_rect
            return
        self.stop()
        self._hwnd = int(hwnd)
        self._window_rect = window_rect
        stop_event = threading.Event()
        self._stop_event = stop_event
        self._thread = threading.Thread(
            target=self._run,
            args=(stop_event,),
            name="codex-hud-header-roi-uia",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        thread = self._thread
        self._stop_event.set()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        if thread is self._thread:
            self._thread = None

    def _run(self, stop_event: threading.Event) -> None:
        probe = self.probe
        hwnd = self._hwnd
        if probe is None or not hwnd:
            return
        automation = probe._automation_for_thread()
        if not automation:
            return
        root = probe._element_from_handle(automation, hwnd)
        if not root:
            return
        target_elements: tuple[int, ...] = ()
        owned_target_elements: tuple[int, ...] = ()
        registered_automation_targets: list[tuple[int, int]] = []
        registered_property_targets: list[int] = []
        watch_scope = "root"
        self._event_handler = _UiaAutomationEventHandler(self._handle_event)
        self._property_handler = _UiaPropertyChangedEventHandler(self._handle_event)
        event_handler_ptr = int(self._event_handler.ptr.value or 0)
        property_handler_ptr = int(self._property_handler.ptr.value or 0)
        try:
            if self._window_rect is not None:
                try:
                    target_elements = probe.find_header_event_targets(
                        hwnd,
                        self._window_rect,
                    )
                except Exception as exc:
                    _logger.debug(
                        "uia_header_roi_event_targets_failed hwnd=%s error=%s",
                        hwnd,
                        exc,
                    )
            if target_elements:
                owned_target_elements = target_elements
                target_elements = (*target_elements, root)
                watch_scope = "anchors+root-layout"
            else:
                target_elements = (root,)
            (
                registered_automation_targets,
                registered_property_targets,
            ) = self._register_targets(
                probe,
                automation,
                target_elements,
                event_handler_ptr,
                property_handler_ptr,
                register_property_handlers=watch_scope == "root",
            )
            if (
                not registered_automation_targets
                and not registered_property_targets
                and owned_target_elements
            ):
                for element in owned_target_elements:
                    probe._release(element)
                owned_target_elements = ()
                target_elements = (root,)
                watch_scope = "root-fallback"
                (
                    registered_automation_targets,
                    registered_property_targets,
                ) = self._register_targets(
                    probe,
                    automation,
                    target_elements,
                    event_handler_ptr,
                    property_handler_ptr,
                    register_property_handlers=True,
                )
            if not registered_automation_targets and not registered_property_targets:
                return
            event_ids = sorted({event_id for event_id, _element in registered_automation_targets})
            _logger.info(
                "uia_header_roi_event_watcher_started hwnd=%s events=%s property=%s "
                "scope=%s target_count=%s",
                hwnd,
                ",".join(str(item) for item in event_ids) or "none",
                bool(registered_property_targets),
                watch_scope,
                len(target_elements),
            )
            self._registered_hwnd = hwnd
            self._owned_target_elements = owned_target_elements
            self._message_loop(stop_event)
        finally:
            self._registered_hwnd = 0
            self._owned_target_elements = ()
            for element in registered_property_targets:
                probe.remove_property_changed_event_handler(
                    automation,
                    element,
                    property_handler_ptr,
                )
            for event_id, element in registered_automation_targets:
                probe.remove_automation_event_handler(
                    automation,
                    event_id,
                    element,
                    event_handler_ptr,
                )
            for element in owned_target_elements:
                probe._release(element)
            self._event_handler = None
            self._property_handler = None
            probe._release(root)

    def _register_targets(
        self,
        probe: _UiaProbe,
        automation: int,
        elements: tuple[int, ...],
        event_handler_ptr: int,
        property_handler_ptr: int,
        *,
        register_property_handlers: bool,
    ) -> tuple[list[tuple[int, int]], list[int]]:
        registered_automation_targets: list[tuple[int, int]] = []
        registered_property_targets: list[int] = []
        for element in elements:
            for event_id in (
                _UIA_STRUCTURE_CHANGED_EVENT_ID,
                _UIA_LAYOUT_INVALIDATED_EVENT_ID,
            ):
                if probe.add_automation_event_handler(
                    automation,
                    event_id,
                    element,
                    event_handler_ptr,
                ):
                    registered_automation_targets.append((event_id, element))
            if register_property_handlers and probe.add_property_changed_event_handler(
                automation,
                element,
                property_handler_ptr,
                (_UIA_BOUNDING_RECTANGLE_PROPERTY_ID, _UIA_IS_OFFSCREEN_PROPERTY_ID),
            ):
                registered_property_targets.append(element)
        return registered_automation_targets, registered_property_targets

    def _message_loop(self, stop_event: threading.Event) -> None:
        user32 = ctypes.windll.user32
        user32.MsgWaitForMultipleObjectsEx.argtypes = [
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        user32.MsgWaitForMultipleObjectsEx.restype = wintypes.DWORD
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = wintypes.LPARAM
        msg = wintypes.MSG()
        while not stop_event.is_set():
            user32.MsgWaitForMultipleObjectsEx(
                0,
                None,
                100,
                _QS_ALLINPUT,
                _MWMO_INPUTAVAILABLE | _MWMO_ALERTABLE,
            )
            while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, _PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            self._drain_debounced_event()

    def _handle_event(self, _sender: int, event_id: int) -> None:
        if event_id not in {
            _UIA_STRUCTURE_CHANGED_EVENT_ID,
            _UIA_LAYOUT_INVALIDATED_EVENT_ID,
            _UIA_BOUNDING_RECTANGLE_PROPERTY_ID,
            _UIA_IS_OFFSCREEN_PROPERTY_ID,
        }:
            return
        self._last_event_at = time.monotonic()

    def _drain_debounced_event(self) -> None:
        event_at = self._last_event_at
        if not event_at or event_at == self._last_invalidated_at:
            return
        now = time.monotonic()
        if now - event_at < _UIA_HEADER_EVENT_DEBOUNCE_SECONDS:
            return
        self._last_invalidated_at = event_at
        _logger.info(
            "uia_header_roi_event_invalidated hwnd=%s debounce_ms=%.1f",
            self._hwnd,
            (now - event_at) * 1000,
        )
        snapshot = self._fast_header_roi_snapshot()
        if snapshot is not None:
            self.tracker.publish_header_roi_snapshot(snapshot, reason="uia-event")
            return
        self.tracker.invalidate_header_roi_cache("uia-event")

    def _fast_header_roi_snapshot(self) -> HeaderRoiSnapshot | None:
        probe = self.probe
        window_rect = self._window_rect
        if probe is None or window_rect is None:
            return None
        started = time.perf_counter()
        try:
            scan = probe.find_header_roi_from_event_targets(
                self._owned_target_elements,
                window_rect,
            )
        except Exception as exc:
            _logger.debug("uia_header_roi_event_fast_scan_failed hwnd=%s error=%s", self._hwnd, exc)
            return None
        duration_ms = (time.perf_counter() - started) * 1000
        if scan is None:
            return None
        return HeaderRoiSnapshot(
            status=STATUS_VISIBLE if scan.roi is not None else STATUS_NOT_FOUND,
            hwnd=self._hwnd,
            source="uia-event",
            window_rect=window_rect,
            header_rect=scan.header_rect,
            roi=scan.roi,
            nodes=scan.nodes,
            duration_ms=duration_ms,
            reason=scan.reason,
        )

    def is_event_driven_for(self, hwnd: int) -> bool:
        thread = self._thread
        return (
            bool(hwnd)
            and self._registered_hwnd == int(hwnd)
            and thread is not None
            and thread.is_alive()
        )


class CodexWindowTracker:
    """Track the Codex main window and optional UIA docking landmarks on Windows."""

    def __init__(self, *, blocking_uia: bool = False, enable_uia: bool = True) -> None:
        self.enabled = False
        self.blocking_uia = blocking_uia
        self.enable_uia = enable_uia
        self.log_path = configure_window_tracker_logging()
        self.last_status = STATUS_UNSUPPORTED
        self.last_snapshot = DockSnapshot(status=STATUS_UNSUPPORTED, reason="not Windows")
        self._last_hwnd = 0
        self._last_hwnd_verified_at = 0.0
        self._window_snapshot_cache_at = 0.0
        self._window_snapshot_cache: DockSnapshot | None = None
        self._landmark_cache_at = 0.0
        self._landmark_cache: _Landmarks | None = None
        self._landmark_cache_hwnd = 0
        self._landmark_cache_window_rect: PhysicalRect | None = None
        self._header_roi_cache_at = 0.0
        self._header_roi_cache: HeaderRoiSnapshot | None = None
        self._header_roi_cache_hwnd = 0
        self._header_roi_cache_window_rect: PhysicalRect | None = None
        self._bottom_roi_cache_at = 0.0
        self._bottom_roi_cache: BottomRoiSnapshot | None = None
        self._bottom_roi_cache_hwnd = 0
        self._bottom_roi_cache_window_rect: PhysicalRect | None = None
        self._header_roi_change_callback: Callable[[str], None] | None = None
        self._last_uia_attempt_at = 0.0
        self._uia_lock = threading.Lock()
        self._uia_scan_running = False
        self._uia_probe: _UiaProbe | None = None
        self._header_roi_event_watcher: _UiaHeaderRoiEventWatcher | None = None

        if not sys.platform.startswith("win"):
            _logger.info("windows_tracker_unsupported platform=%s", sys.platform)
            return

        try:
            self.user32 = ctypes.windll.user32
            self.kernel32 = ctypes.windll.kernel32
            self.dwmapi = getattr(ctypes.windll, "dwmapi", None)
            self._configure_api()
            if self.enable_uia:
                self._uia_probe = _UiaProbe()
                self._header_roi_event_watcher = _UiaHeaderRoiEventWatcher(self)
            self.enabled = True
            self.last_status = STATUS_NOT_FOUND
            self.last_snapshot = DockSnapshot(status=STATUS_NOT_FOUND)
            _logger.info(
                "windows_tracker_started log_path=%s uia_refresh_seconds=%.2f "
                "blocking_uia=%s enable_uia=%s",
                self.log_path,
                _UIA_REFRESH_SECONDS,
                self.blocking_uia,
                self.enable_uia,
            )
        except Exception as exc:
            self.last_snapshot = DockSnapshot(status=STATUS_UNSUPPORTED, reason=str(exc))
            self.last_status = STATUS_UNSUPPORTED
            _logger.exception("windows_tracker_start_failed")

    def set_dpi_aware(self) -> None:
        """Ask Windows for physical pixels so Tk and Win32 coordinates agree."""
        if not self.enabled:
            return
        try:
            self.user32.SetProcessDPIAware()
        except Exception:
            return

    def find_main_window(self, *, allow_inactive: bool = False) -> int | None:
        """Return the best live Codex top-level HWND, if one exists."""
        if not self.enabled:
            return None

        now = time.monotonic()
        cached = self._candidate_from_hwnd(self._last_hwnd, verify_codex=True)
        if (
            cached is not None
            and self._is_visible_candidate(cached)
            and self._last_hwnd
            and self.user32.IsWindow(wintypes.HWND(self._last_hwnd))
            and now - self._last_hwnd_verified_at <= _HWND_REVERIFY_SECONDS
        ):
            self._last_hwnd_verified_at = now
            return cached.hwnd

        candidates: dict[int, _WindowCandidate] = {}
        if cached is not None:
            candidates[cached.hwnd] = cached
        for hwnd in self._findwindow_candidates():
            candidate = self._candidate_from_hwnd(hwnd, verify_codex=True)
            if candidate is not None:
                candidates[candidate.hwnd] = candidate

        for candidate in self._enum_window_candidates():
            candidates[candidate.hwnd] = candidate
        if not candidates:
            self._last_hwnd = 0
            return None

        # Prefer the real main surface over transient Codex popups so tray
        # menus and other temporary overlays do not steal the dock anchor.
        stable_candidates = [
            candidate
            for candidate in candidates.values()
            if self._is_stable_candidate(candidate)
        ]
        if not stable_candidates and allow_inactive:
            stable_candidates = [
                candidate
                for candidate in candidates.values()
                if self._is_restore_candidate(candidate)
            ]
        if not stable_candidates:
            if cached is not None and self.user32.IsWindow(wintypes.HWND(cached.hwnd)):
                self._last_hwnd_verified_at = now
                return cached.hwnd
            self._last_hwnd = 0
            return None

        best = sorted(stable_candidates, key=self._score_candidate, reverse=True)[0]
        self._last_hwnd = best.hwnd
        self._last_hwnd_verified_at = now
        _logger.info(
            "codex_hwnd_selected hwnd=%s process=%s title=%r class=%r visible=%s minimized=%s cloaked=%s",
            best.hwnd,
            best.process,
            best.title,
            best.class_name,
            best.visible,
            best.minimized,
            best.cloaked,
        )
        return best.hwnd

    @staticmethod
    def _is_visible_candidate(candidate: "_WindowCandidate") -> bool:
        return candidate.visible and not candidate.minimized and not candidate.cloaked

    @staticmethod
    def _is_stable_candidate(candidate: "_WindowCandidate") -> bool:
        if not CodexWindowTracker._is_visible_candidate(candidate):
            return False
        title = candidate.title.strip().lower()
        class_name = candidate.class_name.strip().lower()
        if class_name.startswith("codexcomputeruse"):
            return False
        if not _is_codex_process_name(candidate.process):
            return False
        if title == "codex":
            return True
        if not title:
            return False
        rect = candidate.rect
        if rect is None:
            return False
        if rect.width * rect.height < 300_000:
            return False
        return class_name == "chrome_widgetwin_1" or "codex" in class_name

    @staticmethod
    def _is_restore_candidate(candidate: "_WindowCandidate") -> bool:
        title = candidate.title.strip().lower()
        class_name = candidate.class_name.strip().lower()
        if class_name.startswith("codexcomputeruse"):
            return False
        if not _is_codex_process_name(candidate.process):
            return False
        if title == "codex":
            return True
        # Current Windows Desktop titles its Electron window "ChatGPT".
        # A minimized window reports a tiny icon rectangle, not its restored
        # size, so the usual main-window area threshold cannot identify it.
        if title == "chatgpt" and class_name == "chrome_widgetwin_1":
            return True
        if not title:
            return False
        rect = candidate.rect
        if rect is None or rect.width * rect.height < 300_000:
            return False
        return class_name == "chrome_widgetwin_1" or "codex" in class_name

    def get_window_rect(self) -> tuple[int, int, int, int] | None:
        """Return the live Codex main window rectangle as ``left, top, right, bottom``."""
        snapshot = self.get_window_snapshot()
        if snapshot.window_rect is None:
            return None
        rect = snapshot.window_rect
        return rect.left, rect.top, rect.right, rect.bottom

    def get_window_snapshot(self) -> DockSnapshot:
        """Return current Codex top-level window status without UIA tree scanning."""
        if not self.enabled:
            snapshot = DockSnapshot(status=STATUS_UNSUPPORTED, reason="Windows APIs unavailable")
            self._remember(snapshot)
            return snapshot
        now = time.monotonic()
        if (
            self._window_snapshot_cache is not None
            and now - self._window_snapshot_cache_at <= _WINDOW_SNAPSHOT_CACHE_SECONDS
        ):
            self._remember(self._window_snapshot_cache)
            return self._window_snapshot_cache

        hwnd = self.find_main_window()
        if hwnd is None:
            snapshot = DockSnapshot(status=STATUS_NOT_FOUND, reason="Codex HWND not found")
            self._remember(snapshot)
            self._window_snapshot_cache = snapshot
            self._window_snapshot_cache_at = now
            return snapshot
        snapshot = self._window_visibility_snapshot(hwnd)
        self._remember(snapshot)
        self._window_snapshot_cache = snapshot
        self._window_snapshot_cache_at = now
        return snapshot

    def activate_main_window(self) -> int | None:
        """Restore and foreground the best Codex main window when possible."""
        if not self.enabled:
            return None
        hwnd = self.find_main_window(allow_inactive=True)
        if hwnd is None:
            return None
        self._activate_window(hwnd)
        self._window_snapshot_cache = None
        self._window_snapshot_cache_at = 0.0
        return hwnd

    def get_dock_coordinates(
        self,
        target: DockTarget = "input",
        *,
        hud_height: int = 32,
    ) -> tuple[int, int, int] | None:
        """Return ideal ``(x, y, available_width)`` for a HUD dock, or ``None``.

        Inspect ``last_status`` or call ``get_dock_snapshot`` when the caller
        needs the explicit hide reason (minimized, hidden, virtual-desktop
        cloaked, not found, etc.).
        """
        snapshot = self.get_dock_snapshot(target=target, hud_height=hud_height)
        return snapshot.dock

    def get_dock_snapshot(
        self,
        target: DockTarget = "input",
        *,
        hud_height: int = 32,
    ) -> DockSnapshot:
        """Return window, landmark, status, and docking details for the HUD."""
        if not self.enabled:
            snapshot = DockSnapshot(status=STATUS_UNSUPPORTED, reason="Windows APIs unavailable")
            self._remember(snapshot)
            return snapshot

        base = self.get_window_snapshot()
        if base.status != STATUS_VISIBLE or base.window_rect is None:
            return base
        hwnd = base.hwnd

        landmarks = self._landmarks(hwnd, base.window_rect)
        dock = self.dock_coordinates_from_landmarks(
            landmarks.title_bar,
            landmarks.input_box,
            target=target,
            hud_height=hud_height,
        )
        snapshot = DockSnapshot(
            status=STATUS_VISIBLE,
            hwnd=hwnd,
            source=landmarks.source,
            window_rect=base.window_rect,
            title_bar=landmarks.title_bar,
            input_box=landmarks.input_box,
            dock=dock,
        )
        self._remember(snapshot)
        return snapshot

    def get_header_roi_snapshot(self) -> HeaderRoiSnapshot:
        """Return a debug-only UIA header safe area without affecting dock state."""
        if not self.enabled:
            return HeaderRoiSnapshot(
                status=STATUS_UNSUPPORTED,
                reason="Windows APIs unavailable",
            )
        if not self.enable_uia or self._uia_probe is None:
            return HeaderRoiSnapshot(
                status=STATUS_UNSUPPORTED,
                reason="UIA disabled",
            )

        base = self.get_window_snapshot()
        if base.status != STATUS_VISIBLE or base.window_rect is None:
            return HeaderRoiSnapshot(
                status=base.status,
                hwnd=base.hwnd,
                window_rect=base.window_rect,
                reason=base.reason,
            )

        hwnd = base.hwnd
        window_rect = base.window_rect
        self._ensure_header_roi_event_watcher(hwnd, window_rect)
        event_driven_cache = (
            self._header_roi_event_watcher is not None
            and self._header_roi_event_watcher.is_event_driven_for(hwnd)
        )
        now = time.monotonic()
        with self._uia_lock:
            cached = self._header_roi_cache
            cached_rect = self._header_roi_cache_window_rect
            cached_hwnd = self._header_roi_cache_hwnd
            cache_age = now - self._header_roi_cache_at

        if cached is not None and cached_rect is not None and cached_hwnd == hwnd:
            if (
                window_rect.width == cached_rect.width
                and window_rect.height == cached_rect.height
                and (event_driven_cache or cache_age <= _UIA_REFRESH_SECONDS)
            ):
                return self._translate_header_roi_snapshot(cached, cached_rect, window_rect)

        started = time.perf_counter()
        try:
            scan = self._uia_probe.find_header_roi(hwnd, window_rect)
        except Exception as exc:
            _logger.exception("uia_header_roi_scan_failed hwnd=%s error=%s", hwnd, exc)
            scan = None
        duration_ms = (time.perf_counter() - started) * 1000
        if scan is None:
            snapshot = HeaderRoiSnapshot(
                status=STATUS_NOT_FOUND,
                hwnd=hwnd,
                source="uia",
                window_rect=window_rect,
                duration_ms=duration_ms,
                reason="no-header-controls",
            )
            _logger.info(
                "uia_header_roi_demo status=%s reason=%s window=(%s,%s,%s,%s) duration_ms=%.1f",
                snapshot.status,
                snapshot.reason,
                window_rect.left,
                window_rect.top,
                window_rect.right,
                window_rect.bottom,
                duration_ms,
            )
        else:
            self._uia_probe._log_header_roi_scan(scan, window_rect)
            _logger.info(
                "uia_header_roi_scan_complete hwnd=%s duration_ms=%.1f nodes=%s reason=%s",
                hwnd,
                duration_ms,
                scan.nodes,
                scan.reason,
            )
            snapshot = HeaderRoiSnapshot(
                status=STATUS_VISIBLE if scan.roi is not None else STATUS_NOT_FOUND,
                hwnd=hwnd,
                source="uia",
                window_rect=window_rect,
                header_rect=scan.header_rect,
                roi=scan.roi,
                nodes=scan.nodes,
                duration_ms=duration_ms,
                reason=scan.reason,
            )

        with self._uia_lock:
            self._header_roi_cache_at = time.monotonic()
            self._header_roi_cache = snapshot
            self._header_roi_cache_hwnd = hwnd
            self._header_roi_cache_window_rect = window_rect
        return snapshot

    def invalidate_header_roi_cache(self, reason: str = "manual") -> None:
        with self._uia_lock:
            self._header_roi_cache_at = 0.0
            self._header_roi_cache = None
            self._header_roi_cache_hwnd = 0
            self._header_roi_cache_window_rect = None
        _logger.info("uia_header_roi_cache_invalidated reason=%s", reason)
        callback = self._header_roi_change_callback
        if callback is not None:
            try:
                callback(reason)
            except Exception as exc:
                _logger.debug("uia_header_roi_change_callback_failed error=%s", exc)

    def publish_header_roi_snapshot(
        self,
        snapshot: HeaderRoiSnapshot,
        *,
        reason: str = "manual",
    ) -> None:
        with self._uia_lock:
            self._header_roi_cache_at = time.monotonic()
            self._header_roi_cache = snapshot
            self._header_roi_cache_hwnd = snapshot.hwnd
            self._header_roi_cache_window_rect = snapshot.window_rect
        _logger.info(
            "uia_header_roi_cache_published reason=%s status=%s duration_ms=%.1f roi=%s",
            reason,
            snapshot.status,
            snapshot.duration_ms,
            (
                f"({snapshot.roi.left},{snapshot.roi.top},{snapshot.roi.right},{snapshot.roi.bottom})"
                if snapshot.roi is not None
                else "none"
            ),
        )
        callback = self._header_roi_change_callback
        if callback is not None:
            try:
                callback(reason)
            except Exception as exc:
                _logger.debug("uia_header_roi_change_callback_failed error=%s", exc)

    def set_header_roi_change_callback(
        self,
        callback: Callable[[str], None] | None,
    ) -> None:
        self._header_roi_change_callback = callback

    def _ensure_header_roi_event_watcher(
        self,
        hwnd: int,
        window_rect: PhysicalRect,
    ) -> None:
        watcher = self._header_roi_event_watcher
        if watcher is None:
            return
        try:
            watcher.ensure(hwnd, window_rect)
        except Exception as exc:
            _logger.debug("uia_header_roi_event_watcher_failed hwnd=%s error=%s", hwnd, exc)

    def get_bottom_roi_snapshot(self) -> BottomRoiSnapshot:
        """Return a debug-only UIA bottom safe area without affecting dock state."""
        if not self.enabled:
            return BottomRoiSnapshot(
                status=STATUS_UNSUPPORTED,
                reason="Windows APIs unavailable",
            )
        if not self.enable_uia or self._uia_probe is None:
            return BottomRoiSnapshot(
                status=STATUS_UNSUPPORTED,
                reason="UIA disabled",
            )

        base = self.get_window_snapshot()
        if base.status != STATUS_VISIBLE or base.window_rect is None:
            return BottomRoiSnapshot(
                status=base.status,
                hwnd=base.hwnd,
                window_rect=base.window_rect,
                reason=base.reason,
            )

        hwnd = base.hwnd
        window_rect = base.window_rect
        now = time.monotonic()
        with self._uia_lock:
            cached = self._bottom_roi_cache
            cached_rect = self._bottom_roi_cache_window_rect
            cached_hwnd = self._bottom_roi_cache_hwnd
            cache_age = now - self._bottom_roi_cache_at

        if cached is not None and cached_rect is not None and cached_hwnd == hwnd:
            if (
                window_rect.width == cached_rect.width
                and window_rect.height == cached_rect.height
                and cache_age <= _UIA_REFRESH_SECONDS
            ):
                return self._translate_bottom_roi_snapshot(
                    cached,
                    cached_rect,
                    window_rect,
                )

        started = time.perf_counter()
        try:
            scan = self._uia_probe.find_bottom_roi(hwnd, window_rect)
        except Exception as exc:
            _logger.exception("uia_bottom_roi_scan_failed hwnd=%s error=%s", hwnd, exc)
            scan = None
        duration_ms = (time.perf_counter() - started) * 1000
        if scan is None:
            snapshot = BottomRoiSnapshot(
                status=STATUS_NOT_FOUND,
                hwnd=hwnd,
                source="uia",
                window_rect=window_rect,
                duration_ms=duration_ms,
                reason="no-bottom-controls",
            )
            _logger.info(
                "uia_bottom_roi_demo status=%s reason=%s window=(%s,%s,%s,%s) duration_ms=%.1f",
                snapshot.status,
                snapshot.reason,
                window_rect.left,
                window_rect.top,
                window_rect.right,
                window_rect.bottom,
                duration_ms,
            )
        else:
            self._uia_probe._log_bottom_roi_scan(scan, window_rect)
            snapshot = BottomRoiSnapshot(
                status=STATUS_VISIBLE if scan.roi is not None else STATUS_NOT_FOUND,
                hwnd=hwnd,
                source="uia",
                window_rect=window_rect,
                input_rect=scan.input_rect,
                roi=scan.roi,
                left_control=(
                    scan.left_control.rect if scan.left_control is not None else None
                ),
                right_control=(
                    scan.right_control.rect if scan.right_control is not None else None
                ),
                nodes=scan.nodes,
                duration_ms=duration_ms,
                reason=scan.reason,
            )

        with self._uia_lock:
            self._bottom_roi_cache_at = time.monotonic()
            self._bottom_roi_cache = snapshot
            self._bottom_roi_cache_hwnd = hwnd
            self._bottom_roi_cache_window_rect = window_rect
        return snapshot

    def is_main_window_foreground(self, hwnd: int) -> bool:
        """Verify the main window itself, excluding HUD helpers and other chats."""
        if not self.enabled or not hwnd:
            return False
        try:
            handle = wintypes.HWND(hwnd)
            return bool(
                self.user32.GetForegroundWindow() == hwnd
                and self.user32.IsWindowVisible(handle)
                and not self.user32.IsIconic(handle)
                and not self._is_cloaked(hwnd)
            )
        except Exception:
            return False

    def is_active(self, hwnd: int, allowed_hwnds: set[int] | None = None) -> bool:
        """Return whether Codex or one of its own windows is foreground."""
        if not self.enabled or not hwnd:
            return True
        allowed_hwnds = allowed_hwnds or set()
        try:
            if self.user32.IsIconic(wintypes.HWND(hwnd)) or self._is_cloaked(hwnd):
                return False
            foreground = int(self.user32.GetForegroundWindow() or 0)
            if not foreground:
                return False
            foreground_handle = wintypes.HWND(foreground)
            if (
                self.user32.IsIconic(foreground_handle)
                or not self.user32.IsWindowVisible(foreground_handle)
                or self._is_cloaked(foreground)
            ):
                return False
            if foreground == hwnd or foreground in allowed_hwnds:
                return True
            hwnd_pid = wintypes.DWORD()
            foreground_pid = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(
                wintypes.HWND(hwnd),
                ctypes.byref(hwnd_pid),
            )
            self.user32.GetWindowThreadProcessId(
                wintypes.HWND(foreground),
                ctypes.byref(foreground_pid),
            )
            if int(foreground_pid.value or 0) == int(hwnd_pid.value or 0):
                return True
            if int(foreground_pid.value or 0) == os.getpid():
                return True
            return "codex" in self._process_name(int(foreground_pid.value or 0)).lower()
        except Exception:
            return True

    @classmethod
    def geometry_fallback(cls, window_rect: PhysicalRect) -> _Landmarks:
        """Return deterministic Electron-style title and input fallback landmarks."""
        title_bar = PhysicalRect(
            window_rect.left,
            window_rect.top,
            window_rect.right,
            min(window_rect.bottom, window_rect.top + _TITLE_BAR_HEIGHT),
        )

        left_margin = max(
            _INPUT_SAFE_LEFT_MIN,
            int(round(window_rect.width * _INPUT_SAFE_LEFT_RATIO)),
        )
        right_margin = max(
            _INPUT_SAFE_RIGHT_MIN,
            int(round(window_rect.width * _INPUT_SAFE_RIGHT_RATIO)),
        )
        left_margin = cls._fit_anchor_left(
            window_rect.width,
            left_margin,
            right_margin,
            _INPUT_SAFE_MIN_WIDTH,
        )
        input_left = window_rect.left + left_margin
        input_right = max(input_left + 1, window_rect.right - right_margin)
        input_top = max(
            title_bar.bottom,
            window_rect.bottom - _INPUT_BOTTOM_MARGIN - _INPUT_FALLBACK_HEIGHT,
        )
        input_bottom = min(window_rect.bottom, input_top + _INPUT_FALLBACK_HEIGHT)
        return _Landmarks(
            title_bar=title_bar,
            input_box=PhysicalRect(input_left, input_top, input_right, input_bottom),
            source="geometry",
        )

    @classmethod
    def dock_coordinates_from_landmarks(
        cls,
        title_bar: PhysicalRect,
        input_box: PhysicalRect,
        *,
        target: DockTarget,
        hud_height: int,
    ) -> tuple[int, int, int]:
        """Convert detected landmarks into a concrete HUD ``(x, y, width)``."""
        height = max(1, int(hud_height))
        if target == "title":
            left_margin = max(
                _TITLE_SAFE_LEFT_MIN,
                int(round(title_bar.width * _TITLE_SAFE_LEFT_RATIO)),
            )
            right_margin = max(
                _TITLE_SAFE_RIGHT_MIN,
                int(round(title_bar.width * _TITLE_SAFE_RIGHT_RATIO)),
            )
            left_margin = cls._fit_anchor_left(
                title_bar.width,
                left_margin,
                right_margin,
                _TITLE_SAFE_MIN_WIDTH,
            )
            x = title_bar.left + left_margin
            y = title_bar.top + max(0, (title_bar.height - height) // 2)
            width = max(1, title_bar.width - left_margin - right_margin)
            return x, y, width

        x = input_box.left
        y = max(0, input_box.top - height)
        return x, y, max(1, input_box.width)

    def _configure_api(self) -> None:
        enum_proc_type = _WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )
        self._enum_proc_type = enum_proc_type
        self.user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        self.user32.FindWindowW.restype = wintypes.HWND
        self.user32.EnumWindows.argtypes = [enum_proc_type, wintypes.LPARAM]
        self.user32.EnumWindows.restype = wintypes.BOOL
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.IsIconic.argtypes = [wintypes.HWND]
        self.user32.IsIconic.restype = wintypes.BOOL
        self.user32.GetWindowRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL
        self.user32.BringWindowToTop.argtypes = [wintypes.HWND]
        self.user32.BringWindowToTop.restype = wintypes.BOOL
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.SetForegroundWindow.restype = wintypes.BOOL
        self.user32.SetActiveWindow.argtypes = [wintypes.HWND]
        self.user32.SetActiveWindow.restype = wintypes.HWND
        self.user32.SetFocus.argtypes = [wintypes.HWND]
        self.user32.SetFocus.restype = wintypes.HWND
        self.user32.AttachThreadInput.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.BOOL,
        ]
        self.user32.AttachThreadInput.restype = wintypes.BOOL
        self.user32.GetClassNameW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        if self.dwmapi is not None:
            self.dwmapi.DwmGetWindowAttribute.argtypes = [
                wintypes.HWND,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            self.dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

    def _findwindow_candidates(self) -> list[int]:
        candidates: list[int] = []
        for class_name, title in [
            (None, "Codex"),
            ("Codex", None),
            ("Chrome_WidgetWin_1", None),
            ("Chrome_WidgetWin_0", None),
        ]:
            try:
                hwnd = int(self.user32.FindWindowW(class_name, title) or 0)
            except Exception:
                hwnd = 0
            if hwnd and hwnd not in candidates:
                candidates.append(hwnd)
        return candidates

    def _enum_window_candidates(self) -> list["_WindowCandidate"]:
        candidates: list[_WindowCandidate] = []

        def callback(hwnd: int, _: int) -> bool:
            candidate = self._candidate_from_hwnd(int(hwnd), verify_codex=True)
            if candidate is not None:
                candidates.append(candidate)
            return True

        try:
            self.user32.EnumWindows(self._enum_proc_type(callback), 0)
        except Exception:
            return []
        return candidates

    def _candidate_from_hwnd(
        self,
        hwnd: int,
        *,
        verify_codex: bool = False,
    ) -> "_WindowCandidate | None":
        if not hwnd:
            return None
        try:
            if not self.user32.IsWindow(wintypes.HWND(hwnd)):
                return None
            title = self._window_text(hwnd)
            class_name = self._class_name(hwnd)
            pid = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            process = self._process_name(int(pid.value or 0))
            if verify_codex and not self._looks_like_codex(title, class_name, process):
                return None
            rect = self._rect_for_hwnd(hwnd)
            if rect is None and not self.user32.IsIconic(wintypes.HWND(hwnd)):
                return None
            return _WindowCandidate(
                hwnd=hwnd,
                title=title,
                class_name=class_name,
                process=process,
                rect=rect,
                visible=bool(self.user32.IsWindowVisible(wintypes.HWND(hwnd))),
                minimized=bool(self.user32.IsIconic(wintypes.HWND(hwnd))),
                cloaked=self._is_cloaked(hwnd),
            )
        except Exception:
            return None

    def _window_visibility_snapshot(self, hwnd: int) -> DockSnapshot:
        if not self.user32.IsWindow(wintypes.HWND(hwnd)):
            return DockSnapshot(status=STATUS_NOT_FOUND, hwnd=hwnd, reason="HWND is stale")
        rect = self._rect_for_hwnd(hwnd)
        if self.user32.IsIconic(wintypes.HWND(hwnd)):
            return DockSnapshot(
                status=STATUS_MINIMIZED,
                hwnd=hwnd,
                window_rect=rect,
                reason="Codex is minimized",
            )
        if not self.user32.IsWindowVisible(wintypes.HWND(hwnd)):
            return DockSnapshot(
                status=STATUS_HIDDEN,
                hwnd=hwnd,
                window_rect=rect,
                reason="Codex is hidden",
            )
        if self._is_cloaked(hwnd):
            return DockSnapshot(
                status=STATUS_CLOAKED,
                hwnd=hwnd,
                window_rect=rect,
                reason="Codex is cloaked by DWM, often because it is on another virtual desktop",
            )
        if rect is None:
            return DockSnapshot(status=STATUS_HIDDEN, hwnd=hwnd, reason="GetWindowRect failed")
        return DockSnapshot(status=STATUS_VISIBLE, hwnd=hwnd, window_rect=rect)

    def _rect_for_hwnd(self, hwnd: int) -> PhysicalRect | None:
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        physical = PhysicalRect.from_win_rect(rect)
        if physical.width < _MIN_CODEX_WINDOW_WIDTH or physical.height < _MIN_CODEX_WINDOW_HEIGHT:
            return None
        return physical

    def _activate_window(self, hwnd: int) -> None:
        sw_restore = 9
        hwnd_handle = wintypes.HWND(hwnd)
        current_thread_id = 0
        foreground_thread_id = 0
        target_thread_id = 0
        attached_threads: list[int] = []
        try:
            current_thread_id = int(self.kernel32.GetCurrentThreadId() or 0)
            foreground = int(self.user32.GetForegroundWindow() or 0)
            if foreground:
                foreground_thread_id = int(
                    self.user32.GetWindowThreadProcessId(
                        wintypes.HWND(foreground),
                        None,
                    )
                    or 0
                )
            target_thread_id = int(
                self.user32.GetWindowThreadProcessId(hwnd_handle, None) or 0
            )
            for thread_id in (foreground_thread_id, target_thread_id):
                if not thread_id or thread_id == current_thread_id or thread_id in attached_threads:
                    continue
                if self.user32.AttachThreadInput(
                    wintypes.DWORD(current_thread_id),
                    wintypes.DWORD(thread_id),
                    True,
                ):
                    attached_threads.append(thread_id)
            self.user32.ShowWindow(hwnd_handle, sw_restore)
            self.user32.BringWindowToTop(hwnd_handle)
            self.user32.SetActiveWindow(hwnd_handle)
            self.user32.SetForegroundWindow(hwnd_handle)
            self.user32.SetFocus(hwnd_handle)
        except Exception:
            return
        finally:
            for thread_id in reversed(attached_threads):
                try:
                    self.user32.AttachThreadInput(
                        wintypes.DWORD(current_thread_id),
                        wintypes.DWORD(thread_id),
                        False,
                    )
                except Exception:
                    continue

    def _landmarks(self, hwnd: int, window_rect: PhysicalRect) -> _Landmarks:
        if not self.enable_uia:
            return self.geometry_fallback(window_rect)

        now = time.monotonic()
        with self._uia_lock:
            cached = self._landmark_cache
            cached_rect = self._landmark_cache_window_rect
            cached_hwnd = self._landmark_cache_hwnd
            cache_age = now - self._landmark_cache_at

        if cached is not None and cached_rect is not None and cached_hwnd == hwnd:
            if window_rect.width == cached_rect.width and window_rect.height == cached_rect.height:
                if cache_age >= _UIA_REFRESH_SECONDS:
                    self._schedule_uia_refresh(hwnd, window_rect)
                return self._translate_landmarks(cached, cached_rect, window_rect)
            if now - self._last_uia_attempt_at <= _UIA_REFRESH_SECONDS:
                return self.geometry_fallback(window_rect)

        if self.blocking_uia:
            return self._scan_uia_landmarks(hwnd, window_rect)

        self._schedule_uia_refresh(hwnd, window_rect)
        return self.geometry_fallback(window_rect)

    def _schedule_uia_refresh(self, hwnd: int, window_rect: PhysicalRect) -> None:
        if not self.enable_uia or self._uia_probe is None:
            return
        now = time.monotonic()
        with self._uia_lock:
            if self._uia_scan_running:
                return
            if now - self._last_uia_attempt_at < _UIA_REFRESH_SECONDS:
                return
            self._last_uia_attempt_at = now
            self._uia_scan_running = True

        _logger.debug(
            "uia_scan_scheduled hwnd=%s window=(%s,%s,%s,%s)",
            hwnd,
            window_rect.left,
            window_rect.top,
            window_rect.right,
            window_rect.bottom,
        )
        thread = threading.Thread(
            target=self._run_uia_refresh,
            args=(hwnd, window_rect),
            name="codex-hud-uia",
            daemon=True,
        )
        thread.start()

    def _run_uia_refresh(self, hwnd: int, window_rect: PhysicalRect) -> None:
        try:
            self._scan_uia_landmarks(hwnd, window_rect)
        finally:
            with self._uia_lock:
                self._uia_scan_running = False

    def _scan_uia_landmarks(self, hwnd: int, window_rect: PhysicalRect) -> _Landmarks:
        landmarks = None
        with self._uia_lock:
            self._last_uia_attempt_at = time.monotonic()
        if self._uia_probe is not None:
            started = time.perf_counter()
            try:
                landmarks = self._uia_probe.find_landmarks(hwnd, window_rect)
            except Exception as exc:
                _logger.exception("uia_scan_failed hwnd=%s error=%s", hwnd, exc)
                landmarks = None
            duration = time.perf_counter() - started
            if landmarks is not None:
                landmarks = _Landmarks(
                    title_bar=landmarks.title_bar,
                    input_box=landmarks.input_box,
                    source=landmarks.source,
                    nodes=landmarks.nodes,
                    duration_ms=duration * 1000,
                )
            if duration >= _UIA_SLOW_SECONDS:
                _logger.warning(
                    "uia_scan_slow hwnd=%s duration_ms=%.1f nodes=%s source=%s window=(%s,%s,%s,%s)",
                    hwnd,
                    duration * 1000,
                    landmarks.nodes if landmarks is not None else 0,
                    landmarks.source if landmarks is not None else "none",
                    window_rect.left,
                    window_rect.top,
                    window_rect.right,
                    window_rect.bottom,
                )
        if landmarks is None:
            landmarks = self.geometry_fallback(window_rect)
            _logger.info(
                "uia_fallback_geometry hwnd=%s window=(%s,%s,%s,%s)",
                hwnd,
                window_rect.left,
                window_rect.top,
                window_rect.right,
                window_rect.bottom,
            )
        elif landmarks.source != "uia":
            _logger.info(
                "uia_partial_fallback hwnd=%s source=%s nodes=%s duration_ms=%.1f",
                hwnd,
                landmarks.source,
                landmarks.nodes,
                landmarks.duration_ms,
            )

        with self._uia_lock:
            self._landmark_cache_at = time.monotonic()
            self._landmark_cache = landmarks
            self._landmark_cache_hwnd = hwnd
            self._landmark_cache_window_rect = window_rect
        return landmarks

    @staticmethod
    def _translate_landmarks(
        landmarks: _Landmarks,
        from_rect: PhysicalRect,
        to_rect: PhysicalRect,
    ) -> _Landmarks:
        if from_rect.left == to_rect.left and from_rect.top == to_rect.top:
            return landmarks
        dx = to_rect.left - from_rect.left
        dy = to_rect.top - from_rect.top
        return _Landmarks(
            title_bar=_offset_rect(landmarks.title_bar, dx, dy),
            input_box=_offset_rect(landmarks.input_box, dx, dy),
            source=landmarks.source,
            nodes=landmarks.nodes,
            duration_ms=landmarks.duration_ms,
        )

    @staticmethod
    def _translate_header_roi_snapshot(
        snapshot: HeaderRoiSnapshot,
        from_rect: PhysicalRect,
        to_rect: PhysicalRect,
    ) -> HeaderRoiSnapshot:
        if from_rect.left == to_rect.left and from_rect.top == to_rect.top:
            return snapshot
        dx = to_rect.left - from_rect.left
        dy = to_rect.top - from_rect.top
        return HeaderRoiSnapshot(
            status=snapshot.status,
            hwnd=snapshot.hwnd,
            source=snapshot.source,
            window_rect=to_rect,
            header_rect=(
                _offset_rect(snapshot.header_rect, dx, dy)
                if snapshot.header_rect is not None
                else None
            ),
            roi=_offset_rect(snapshot.roi, dx, dy) if snapshot.roi is not None else None,
            nodes=snapshot.nodes,
            duration_ms=snapshot.duration_ms,
            reason=snapshot.reason,
        )

    @staticmethod
    def _translate_bottom_roi_snapshot(
        snapshot: BottomRoiSnapshot,
        from_rect: PhysicalRect,
        to_rect: PhysicalRect,
    ) -> BottomRoiSnapshot:
        if from_rect.left == to_rect.left and from_rect.top == to_rect.top:
            return snapshot
        dx = to_rect.left - from_rect.left
        dy = to_rect.top - from_rect.top
        return BottomRoiSnapshot(
            status=snapshot.status,
            hwnd=snapshot.hwnd,
            source=snapshot.source,
            window_rect=to_rect,
            input_rect=(
                _offset_rect(snapshot.input_rect, dx, dy)
                if snapshot.input_rect is not None
                else None
            ),
            roi=_offset_rect(snapshot.roi, dx, dy) if snapshot.roi is not None else None,
            left_control=(
                _offset_rect(snapshot.left_control, dx, dy)
                if snapshot.left_control is not None
                else None
            ),
            right_control=(
                _offset_rect(snapshot.right_control, dx, dy)
                if snapshot.right_control is not None
                else None
            ),
            nodes=snapshot.nodes,
            duration_ms=snapshot.duration_ms,
            reason=snapshot.reason,
        )

    def _window_text(self, hwnd: int) -> str:
        length = int(self.user32.GetWindowTextLengthW(wintypes.HWND(hwnd)) or 0)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, length + 1)
        return buffer.value

    def _class_name(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(wintypes.HWND(hwnd), buffer, 256)
        return buffer.value

    def _process_name(self, pid: int) -> str:
        if not pid:
            return ""
        handle = self.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            ok = self.kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                buffer,
                ctypes.byref(size),
            )
            return Path(buffer.value).name if ok else ""
        finally:
            self.kernel32.CloseHandle(handle)

    def _is_cloaked(self, hwnd: int) -> bool:
        if self.dwmapi is None:
            return False
        value = wintypes.DWORD()
        try:
            hr = int(
                self.dwmapi.DwmGetWindowAttribute(
                    wintypes.HWND(hwnd),
                    _DWMWA_CLOAKED,
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                )
            )
        except Exception:
            return False
        return hr >= 0 and bool(value.value)

    @staticmethod
    def _looks_like_codex(title: str, class_name: str, process: str) -> bool:
        title_lower = title.strip().lower()
        class_lower = class_name.strip().lower()
        process_lower = process.strip().lower()
        # This is a full-screen computer-use indicator, not a chat window.
        # Restoring it when Desktop is minimized traps the user under a cover.
        if class_lower.startswith("codexcomputeruse"):
            return False
        if not title_lower and class_lower == "chrome_widgetwin_0":
            return False
        if not _is_codex_process_name(process_lower):
            return False
        if class_lower == "chrome_widgetwin_1":
            return bool(title_lower) or process_lower in _CODEX_PROCESS_NAMES
        return True

    @staticmethod
    def _score_candidate(candidate: "_WindowCandidate") -> tuple[int, int]:
        title = candidate.title.strip().lower()
        class_name = candidate.class_name.strip().lower()
        process = candidate.process.strip().lower()
        score = 0
        if process in _CODEX_PROCESS_NAMES:
            score += 100
        elif process.startswith("codex"):
            score += 70
        if title == "codex":
            score += 45
        elif title:
            score += 12
        else:
            score -= 35
        if "codex" in class_name:
            score += 20
        elif class_name == "chrome_widgetwin_1":
            score += 14
        elif class_name == "chrome_widgetwin_0":
            score -= 45
        if candidate.visible:
            score += 10
        if not candidate.minimized:
            score += 5
        if candidate.cloaked:
            score -= 50
        area = candidate.rect.width * candidate.rect.height if candidate.rect else 0
        return score, area

    @staticmethod
    def _fit_anchor_left(
        window_width: int,
        left_margin: int,
        right_margin: int,
        min_width: int,
    ) -> int:
        if window_width - left_margin - right_margin >= min_width:
            return left_margin
        return max(8, min(left_margin, window_width - right_margin - min_width))

    def _remember(self, snapshot: DockSnapshot) -> None:
        previous = self.last_snapshot
        if previous.status != snapshot.status or previous.hwnd != snapshot.hwnd:
            _logger.info(
                "codex_window_status status=%s hwnd=%s source=%s reason=%r",
                snapshot.status,
                snapshot.hwnd or 0,
                snapshot.source,
                snapshot.reason,
            )
        self.last_snapshot = snapshot
        self.last_status = snapshot.status


@dataclass(frozen=True)
class _WindowCandidate:
    hwnd: int
    title: str
    class_name: str
    process: str
    rect: PhysicalRect | None
    visible: bool
    minimized: bool
    cloaked: bool


__all__ = [
    "CodexWindowTracker",
    "DockSnapshot",
    "PhysicalRect",
    "STATUS_CLOAKED",
    "STATUS_HIDDEN",
    "STATUS_MINIMIZED",
    "STATUS_NOT_FOUND",
    "STATUS_UNSUPPORTED",
    "STATUS_VISIBLE",
    "configure_window_tracker_logging",
    "window_tracker_log_path",
]
