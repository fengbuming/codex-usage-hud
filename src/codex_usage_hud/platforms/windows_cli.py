"""On-demand Windows CLI identity and terminal activation, without dependencies."""

from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from dataclasses import dataclass
from pathlib import Path
import re
import struct

from .windows_tracker import CodexWindowTracker, _UiaProbe


def _terminal_title_key(title: str) -> str:
    """Ignore Codex's animated Braille prefix, preserving the full title."""
    return re.sub(r"^[\u2800-\u28ff]\s+", "", title.strip())


@dataclass(frozen=True)
class CliProcess:
    pid: int
    parent_pid: int
    executable: str
    generation: int
    command_line: str = ""
    launch_id: str = ""
    terminal_session: str = ""


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("size", wt.DWORD), ("usage", wt.DWORD), ("pid", wt.DWORD),
                ("heap", ctypes.c_size_t), ("module", wt.DWORD), ("threads", wt.DWORD),
                ("parent", wt.DWORD), ("priority", wt.LONG), ("flags", wt.DWORD),
                ("name", wt.WCHAR * 260)]


class _HandleEntry(ctypes.Structure):
    _fields_ = [("object", ctypes.c_void_p), ("pid", ctypes.c_size_t),
                ("handle", ctypes.c_size_t), ("access", wt.ULONG),
                ("trace", wt.USHORT), ("type", wt.USHORT),
                ("attributes", wt.ULONG), ("reserved", wt.ULONG)]


class WindowsCliApi:
    def __init__(self) -> None:
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.n = ctypes.WinDLL("ntdll")
        self.u = ctypes.WinDLL("user32")
        signatures = {
            "OpenProcess": ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            "CloseHandle": ([wt.HANDLE], wt.BOOL),
            "CreateToolhelp32Snapshot": ([wt.DWORD, wt.DWORD], wt.HANDLE),
            "Process32FirstW": ([wt.HANDLE, ctypes.POINTER(_ProcessEntry)], wt.BOOL),
            "Process32NextW": ([wt.HANDLE, ctypes.POINTER(_ProcessEntry)], wt.BOOL),
            "QueryFullProcessImageNameW": ([wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)], wt.BOOL),
            "GetProcessTimes": ([wt.HANDLE, *([ctypes.POINTER(wt.FILETIME)] * 4)], wt.BOOL),
            "ReadProcessMemory": ([wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)], wt.BOOL),
            "DuplicateHandle": ([wt.HANDLE, wt.HANDLE, wt.HANDLE, ctypes.POINTER(wt.HANDLE),
                                 wt.DWORD, wt.BOOL, wt.DWORD], wt.BOOL),
            "GetCurrentProcess": ([], wt.HANDLE),
            "GetFileType": ([wt.HANDLE], wt.DWORD),
            "GetFinalPathNameByHandleW": ([wt.HANDLE, wt.LPWSTR, wt.DWORD, wt.DWORD], wt.DWORD),
            "GetConsoleWindow": ([], wt.HWND),
            "AttachConsole": ([wt.DWORD], wt.BOOL),
            "FreeConsole": ([], wt.BOOL),
            "GetConsoleTitleW": ([wt.LPWSTR, wt.DWORD], wt.DWORD),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.k, name)
            fn.argtypes, fn.restype = args, result
        self.n.NtQueryInformationProcess.argtypes = [wt.HANDLE, wt.ULONG, ctypes.c_void_p,
                                                    wt.ULONG, ctypes.POINTER(wt.ULONG)]
        self.n.NtQuerySystemInformation.argtypes = [wt.ULONG, ctypes.c_void_p, wt.ULONG,
                                                   ctypes.POINTER(wt.ULONG)]
        self.n.NtQueryInformationProcess.restype = self.n.NtQuerySystemInformation.restype = ctypes.c_long
        self.u.GetForegroundWindow.restype = wt.HWND

    def _read(self, handle: int, address: int, size: int) -> bytes:
        if not address or size <= 0 or size > 256 * 1024:
            return b""
        data = ctypes.create_string_buffer(size)
        count = ctypes.c_size_t()
        self.k.ReadProcessMemory(handle, address, data, size, ctypes.byref(count))
        return data.raw[:count.value]

    def _context(self, handle: int) -> tuple[str, str, str]:
        # ProcessBasicInformation: the second native pointer is the PEB.
        basic = (ctypes.c_size_t * 6)()
        if self.n.NtQueryInformationProcess(handle, 0, basic, ctypes.sizeof(basic), None) < 0:
            return "", "", ""
        width = ctypes.sizeof(ctypes.c_void_p)
        unpack = "<Q" if width == 8 else "<I"
        peb = int(basic[1])
        # WOW64 processes use a 32-bit PEB even in a 64-bit HUD.
        wow = ctypes.c_size_t()
        if width == 8 and self.n.NtQueryInformationProcess(handle, 26, ctypes.byref(wow),
                                                         ctypes.sizeof(wow), None) >= 0 and wow.value:
            width, unpack, peb = 4, "<I", wow.value
        raw = self._read(handle, peb + (0x20 if width == 8 else 0x10), width)
        if len(raw) != width:
            return "", "", ""
        parameters = struct.unpack(unpack, raw)[0]
        data = self._read(handle, parameters, 0x90 if width == 8 else 0x50)
        command_offset, environment_offset = ((0x70, 0x80) if width == 8 else (0x40, 0x48))
        if len(data) < environment_offset + width:
            return "", "", ""
        length = struct.unpack_from("<H", data, command_offset)[0]
        command_ptr = struct.unpack_from(unpack, data, command_offset + (8 if width == 8 else 4))[0]
        command = self._read(handle, command_ptr, length).decode("utf-16-le", "replace")
        environment_ptr = struct.unpack_from(unpack, data, environment_offset)[0]
        environment = self._read(handle, environment_ptr, 65536).decode("utf-16-le", "replace")
        selected = {}
        for entry in environment.split("\0\0", 1)[0].split("\0"):
            name, sep, value = entry.partition("=")
            if sep and name.casefold() in {"codex_usage_hud_cli_launch_id", "wt_session"}:
                selected[name.casefold()] = value
        # Never expose the remainder of the process environment.
        return command, selected.get("codex_usage_hud_cli_launch_id", ""), selected.get("wt_session", "")

    def processes(self) -> list[CliProcess]:
        snapshot = self.k.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise OSError("Cannot enumerate terminal processes")
        rows = []
        try:
            entry = _ProcessEntry(size=ctypes.sizeof(_ProcessEntry))
            live = self.k.Process32FirstW(snapshot, ctypes.byref(entry))
            while live:
                rows.append((int(entry.pid), int(entry.parent), entry.name))
                live = self.k.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.k.CloseHandle(snapshot)
        result = []
        relevant = {"codex.exe", "windowsterminal.exe", "openconsole.exe", "conhost.exe",
                    "pwsh.exe", "powershell.exe", "cmd.exe", "node.exe", "code.exe"}
        for pid, parent, name in rows:
            if name.casefold() not in relevant:
                continue
            handle = self.k.OpenProcess(0x1010, False, pid)
            if not handle:
                continue
            try:
                path = ctypes.create_unicode_buffer(32768)
                length = wt.DWORD(len(path))
                if not self.k.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                    continue
                times = [wt.FILETIME() for _ in range(4)]
                if not self.k.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                    continue
                generation = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
                command, launch_id, terminal_session = self._context(handle) if name.casefold() == "codex.exe" else ("", "", "")
                result.append(CliProcess(pid, parent, path.value, generation, command, launch_id, terminal_session))
            finally:
                self.k.CloseHandle(handle)
        return result

    def open_rollouts(self, pids: set[int]) -> dict[int, set[str]]:
        if not pids:
            return {}
        size = 1024 * 1024
        for _ in range(7):
            data = ctypes.create_string_buffer(size)
            needed = wt.ULONG()
            status = self.n.NtQuerySystemInformation(64, data, size, ctypes.byref(needed))
            if status >= 0:
                break
            if status & 0xFFFFFFFF != 0xC0000004:
                raise OSError("Cannot inspect CLI rollout ownership")
            size = max(size * 2, needed.value + 4096)
            if size > 64 * 1024 * 1024:
                raise OSError("Process handle snapshot exceeds limit")
        else:
            raise OSError("Process handle snapshot changed repeatedly")
        width = ctypes.sizeof(ctypes.c_size_t)
        count = ctypes.c_size_t.from_buffer(data).value
        count = min(count, (size - width * 2) // ctypes.sizeof(_HandleEntry))
        entries = (_HandleEntry * count).from_buffer(data, width * 2)
        sources = {pid: self.k.OpenProcess(0x40, False, pid) for pid in pids}
        result: dict[int, set[str]] = {}
        try:
            for entry in entries:
                source = sources.get(entry.pid)
                # Resume pickers can briefly READ many historical rollouts.
                # Only a writable recorder handle establishes live ownership.
                if not source or not entry.access & 0x6:  # FILE_WRITE_DATA | FILE_APPEND_DATA
                    continue
                copied = wt.HANDLE()
                if not self.k.DuplicateHandle(source, entry.handle, self.k.GetCurrentProcess(),
                                               ctypes.byref(copied), 0, False, 2):
                    continue
                try:
                    # Query disk files only; pipe/device name queries may block.
                    if self.k.GetFileType(copied) != 1:
                        continue
                    path = ctypes.create_unicode_buffer(32768)
                    length = self.k.GetFinalPathNameByHandleW(copied, path, len(path), 0)
                    value = path.value
                    if 0 < length < len(path) and Path(value).name.startswith("rollout-") and value.endswith(".jsonl"):
                        normalized = "\\\\" + value[8:] if value.startswith("\\\\?\\UNC\\") else value.removeprefix("\\\\?\\")
                        result.setdefault(entry.pid, set()).add(normalized)
                finally:
                    self.k.CloseHandle(copied)
        finally:
            for source in sources.values():
                if source:
                    self.k.CloseHandle(source)
        return result

    def console_info(self, pid: int) -> dict[str, object]:
        # Called only in a disposable helper: never detach the HUD/user console.
        # CREATE_NO_WINDOW may still attach a windowless console. Release the
        # helper's own console before attaching to the target CLI's console.
        self.k.FreeConsole()
        if not self.k.AttachConsole(pid):
            return {}
        try:
            title = ctypes.create_unicode_buffer(4096)
            self.k.GetConsoleTitleW(title, len(title))
            hwnd = int(self.k.GetConsoleWindow() or 0)
            name = ctypes.create_unicode_buffer(256)
            self.u.GetClassNameW(wt.HWND(hwnd), name, len(name))
            classic = name.value == "ConsoleWindowClass" and bool(self.u.IsWindowVisible(wt.HWND(hwnd)))
            owner = wt.DWORD()
            self.u.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(owner))
            return {"hwnd": hwnd, "title": title.value, "classic": classic, "ownerPid": owner.value}
        finally:
            self.k.FreeConsole()

    def windows(self, pids: set[int]) -> list[int]:
        callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        result = []
        def callback(hwnd, _):
            pid = wt.DWORD()
            self.u.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
            name = ctypes.create_unicode_buffer(256)
            self.u.GetClassNameW(wt.HWND(hwnd), name, len(name))
            if pid.value in pids and name.value == "CASCADIA_HOSTING_WINDOW_CLASS":
                result.append(int(hwnd))
            return True
        self.u.EnumWindows(callback_type(callback), 0)
        return result

    def restore(self, hwnd: int) -> bool:
        tracker = CodexWindowTracker(enable_uia=False)
        tracker._activate_window(hwnd)
        return tracker.is_main_window_foreground(hwnd)

    def focus_terminal(self, hwnds: list[int], title: str) -> tuple[bool, str]:
        probe = _UiaProbe()
        initialized = int(probe._ole32.CoInitializeEx(None, 2))
        if initialized < 0 and initialized != -2147417850:  # RPC_E_CHANGED_MODE
            return False, "terminal-unavailable"
        probe._local.com_ready = True
        probe._ole32.CoUninitialize.argtypes = []
        probe._ole32.CoUninitialize.restype = None
        automation = probe._automation_for_thread()
        if not automation:
            if initialized >= 0:
                probe._ole32.CoUninitialize()
            return False, "terminal-unavailable"
        walker = probe._raw_view_walker(automation)
        if not walker:
            probe._release(automation)
            if initialized >= 0:
                probe._ole32.CoUninitialize()
            return False, "terminal-unavailable"
        tabs: list[tuple[int, int]] = []
        try:
            for hwnd in hwnds:
                root = probe._element_from_handle(automation, hwnd)
                if not root:
                    continue
                stack, visited = [root], 0
                try:
                    while stack and visited < 256:
                        element = stack.pop()
                        visited += 1
                        try:
                            if (probe._element_int(element, 21) == 50019
                                    and _terminal_title_key(probe._element_bstr(element, 23)) == _terminal_title_key(title)):
                                tabs.append((hwnd, probe._retain(element)))
                            stack.extend(probe._children(walker, element))
                        finally:
                            probe._release(element)
                finally:
                    for element in stack:
                        probe._release(element)
            if len(tabs) != 1:
                return False, "terminal-ambiguous" if tabs else "terminal-tab-not-found"
            hwnd, tab = tabs[0]
            if not self.restore(hwnd):
                return False, "terminal-not-foreground"
            pattern = ctypes.c_void_p()
            get = probe._method(tab, 16, ctypes.c_long, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
            if get(tab, 10010, ctypes.byref(pattern)) < 0 or not pattern.value:
                return False, "terminal-tab-unavailable"
            try:
                select = probe._method(pattern.value, 3, ctypes.c_long)
                selected = select(pattern.value) >= 0 and probe._element_bool(pattern.value, 6)
            finally:
                probe._release(pattern.value)
            if not selected:
                return False, "terminal-tab-unverified"
            root = probe._element_from_handle(automation, hwnd)
            stack, panes, visited = ([root] if root else []), [], 0
            try:
                while stack and visited < 256:
                    element = stack.pop()
                    visited += 1
                    try:
                        if probe._element_bstr(element, 30) == "TermControl":
                            panes.append((probe._element_bstr(element, 23), probe._retain(element)))
                        stack.extend(probe._children(walker, element))
                    finally:
                        probe._release(element)
                candidates = panes if len(panes) == 1 else [
                    p for p in panes if _terminal_title_key(p[0]) == _terminal_title_key(title)
                ]
                if len(candidates) != 1:
                    return False, "terminal-pane-ambiguous"
                pane = candidates[0][1]
                focus = probe._method(pane, 3, ctypes.c_long)
                focused = focus(pane) >= 0 and probe._element_bool(pane, 26)
                return bool(focused and self.u.GetForegroundWindow() == hwnd), "switched" if focused else "terminal-pane-unverified"
            finally:
                for element in stack:
                    probe._release(element)
                for _, pane in panes:
                    probe._release(pane)
        finally:
            for _, tab in tabs:
                probe._release(tab)
            probe._release(walker)
            probe._release(automation)
            if initialized >= 0:
                probe._ole32.CoUninitialize()
