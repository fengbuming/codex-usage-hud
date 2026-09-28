"""Activate a registered Windows application without losing package identity."""

from __future__ import annotations

import ctypes
import logging
import sys
from uuid import UUID

_LOGGER = logging.getLogger(__name__)


def activate_packaged_app(app_id: str, arguments: str = "") -> bool:
    """Pass arguments through IApplicationActivationManager, not CreateProcess."""
    if not sys.platform.startswith("win"):
        return False
    ole32 = ctypes.OleDLL("ole32")
    guid_type = ctypes.c_ubyte * 16
    clsid = guid_type.from_buffer_copy(
        UUID("45BA127D-10A8-46EA-8AB7-56EA9078943C").bytes_le
    )
    iid = guid_type.from_buffer_copy(
        UUID("2e941141-7f97-4756-ba1d-9decde894a3d").bytes_le
    )
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    manager = ctypes.c_void_p()
    initialized = False
    release = None
    try:
        try:
            ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
            initialized = True
        except OSError as exc:
            # A UI or worker thread may already use the other apartment model.
            if getattr(exc, "winerror", 0) & 0xFFFFFFFF != 0x80010106:
                raise
        ole32.CoCreateInstance(
            ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(manager)
        )
        vtable = ctypes.cast(
            manager, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        activate = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p,
            ctypes.c_wchar_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
        )(vtable[3])
        process_id = ctypes.c_ulong()
        result = activate(manager, app_id, arguments, 0, ctypes.byref(process_id))
        if result < 0:
            raise OSError(f"ActivateApplication HRESULT=0x{result & 0xFFFFFFFF:08X}")
        _LOGGER.info("codex_app_package_activated app_id=%s pid=%s", app_id, process_id.value)
        return True
    except (OSError, ValueError) as exc:
        _LOGGER.info("codex_app_package_activation_failed app_id=%s error=%s", app_id, exc)
        return False
    finally:
        if release is not None:
            release(manager)
        if initialized:
            ole32.CoUninitialize()
