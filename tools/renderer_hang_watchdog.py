#!/usr/bin/env python3
"""Renderer-hang watchdog: snapshot the local machine when the HUD observes a
CDP update failure or a wedged Codex renderer.

Background
----------
The blank-UI failure after a long session lock is a wedged renderer main
thread (see renderer_connection.maybe_escalate_renderer_hung). When that
happens, ``renderer_fallback.log`` records ``cdp.update_failed`` /
``renderer_hung_escalation`` and the HUD requests a Codex restart. This
watchdog tails that log and, on any matching event, captures a machine
snapshot (Codex/Electron process tree, window state, lock state, overlay
state file) so the next white screen has forensic data instead of guesses.

Usage
-----
    python tools/renderer_hang_watchdog.py --once            # one snapshot now
    python tools/renderer_hang_watchdog.py --watch           # tail forever
    python tools/renderer_hang_watchdog.py --watch --interval 2

Output
------
Appends JSON lines to %LOCALAPPDATA%\\codex-usage-hud\\renderer-hang-snapshots.jsonl
(stdlib + ctypes only; no third-party deps).
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HUD_DIRNAME = "codex-usage-hud"
SNAPSHOT_FILENAME = "renderer-hang-snapshots.jsonl"
SINGLETON_MUTEX_NAME = "CodexHudRendererHangWatchdog"
_KERNEL32 = None
_SINGLETON_HANDLE = None
TRIGGER_PATTERNS = (
    re.compile(r"cdp\.update_failed"),
    re.compile(r"renderer_hung_escalation"),
    re.compile(r"renderer_restart_requested"),
    re.compile(r"initial_connect_failed"),
    re.compile(r"work_overlay_helper\.state_read_failed"),
)
WINDOW_TITLE_PATTERNS = ("codex", "chatgpt")
PROCESS_NAME_PATTERNS = ("codex", "chatgpt", "python", "pythonw")


def hud_runtime_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / HUD_DIRNAME


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _say(msg: str) -> None:
    """Print unless running under pythonw (no console attached)."""
    try:
        print(msg, flush=True)
    except Exception:
        pass


def _singleton_acquired(name: str) -> bool:
    """Take a named mutex so multiple launchers cannot double-watch.

    The handle is held for the process lifetime; Windows releases it
    automatically when the process exits (including crashes).
    """
    global _KERNEL32, _SINGLETON_HANDLE
    if sys.platform != "win32":
        return True  # single-instance not enforced off-Windows
    if _KERNEL32 is None:
        _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _SINGLETON_HANDLE = _KERNEL32.CreateMutexW(None, False, name)
    if not _SINGLETON_HANDLE:
        return True  # mutex unavailable; do not block monitoring
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def _run_powershell(script: str, timeout: float = 15.0) -> list[object] | None:
    """Run one PowerShell expression and parse its JSON output (best effort)."""
    if sys.platform != "win32":
        return None
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def _codex_processes() -> list[dict[str, object]]:
    """Codex/Electron/Python process tree with CPU, memory, threads, cmdline."""
    rows = _run_powershell(
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -match 'codex|chatgpt|python' } | "
        "Select-Object Name, ProcessId, ParentProcessId, CreationDate, CommandLine | "
        "ConvertTo-Json -Compress"
    )
    if not isinstance(rows, list):
        rows = [rows] if isinstance(rows, dict) else []
    perf = _run_powershell(
        "Get-Process | Where-Object { $_.ProcessName -match 'codex|chatgpt|python' } | "
        "Select-Object Id, CPU, WorkingSet64, Threads | ConvertTo-Json -Compress"
    )
    perf_by_pid: dict[int, dict[str, object]] = {}
    if isinstance(perf, list):
        for row in perf:
            try:
                pid = int(row.get("Id") or 0)
            except (TypeError, ValueError):
                continue
            perf_by_pid[pid] = row
    elif isinstance(perf, dict):
        try:
            perf_by_pid[int(perf.get("Id") or 0)] = perf
        except (TypeError, ValueError):
            pass
    result: list[dict[str, object]] = []
    for row in rows:
        try:
            pid = int(row.get("ProcessId") or 0)
        except (TypeError, ValueError):
            continue
        p = perf_by_pid.get(pid, {})
        result.append(
            {
                "name": row.get("Name"),
                "pid": pid,
                "ppid": row.get("ParentProcessId"),
                "created": row.get("CreationDate"),
                "commandLine": row.get("CommandLine"),
                "cpuSeconds": p.get("CPU"),
                "workingSetMb": (
                    round(int(p["WorkingSet64"]) / 1048576.0, 1)
                    if p.get("WorkingSet64") is not None
                    else None
                ),
                "threads": (
                    len(p["Threads"]) if isinstance(p.get("Threads"), list) else None
                ),
            }
        )
    return result


def _windows_info() -> list[dict[str, object]]:
    """Visible Codex/ChatGPT windows with rect and minimized state."""
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    found: list[dict[str, object]] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _enum_proc(hwnd: int, _lparam: int) -> bool:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if not any(p in title.lower() for p in WINDOW_TITLE_PATTERNS):
            return True
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        found.append(
            {
                "hwnd": hwnd,
                "title": title,
                "visible": bool(user32.IsWindowVisible(hwnd)),
                "minimized": bool(user32.IsIconic(hwnd)),
                "foreground": hwnd == user32.GetForegroundWindow(),
                "rect": [
                    rect.left,
                    rect.top,
                    rect.right,
                    rect.bottom,
                ],
            }
        )
        return True

    user32.EnumWindows(_enum_proc, 0)
    return found


def _lock_state() -> str:
    """Heuristic lock detection: LogonUI.exe running or WTS session locked."""
    if sys.platform != "win32":
        return "unknown"
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-Process -Name LogonUI -ErrorAction SilentlyContinue | "
             "Measure-Object | Select-Object -ExpandProperty Count"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if proc.returncode == 0 and proc.stdout.strip().isdigit():
            if int(proc.stdout.strip()) > 0:
                return "locked"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unlocked"


def _overlay_state() -> dict[str, object] | None:
    runtime = hud_runtime_dir()
    try:
        files = sorted(
            runtime.glob("work-overlay-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None
    if not files:
        return None
    path = files[0]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        age = None
    items = raw.get("items")
    return {
        "file": path.name,
        "mtimeAgeS": round(age, 1) if age is not None else None,
        "revision": raw.get("revision"),
        "ownerPid": raw.get("ownerPid"),
        "items": len(items) if isinstance(items, list) else None,
        "restReminder": bool(raw.get("restReminder")),
        "systemNotice": bool(raw.get("systemNotice")),
        "close": raw.get("close"),
    }


def _renderer_cdp_state() -> dict[str, object] | None:
    path = hud_runtime_dir() / "renderer_cdp_state.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _recent_events(log_path: Path, limit: int = 8) -> list[str]:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    hits = [
        line
        for line in lines
        if any(pattern.search(line) for pattern in TRIGGER_PATTERNS)
    ]
    return hits[-limit:]


def capture_snapshot() -> dict[str, object]:
    runtime = hud_runtime_dir()
    log_path = runtime / "renderer_fallback.log"
    return {
        "t": time.time(),
        "wall": _now_iso(),
        "desktop_locked": _lock_state(),
        "codex_processes": _codex_processes(),
        "windows": _windows_info(),
        "overlay": _overlay_state(),
        "renderer_cdp_state": _renderer_cdp_state(),
        "events": _recent_events(log_path),
    }


def append_snapshot(snapshot: dict[str, object], out_path: Path | None = None) -> Path:
    path = out_path or (hud_runtime_dir() / SNAPSHOT_FILENAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    return path


def _log_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def watch(
    log_path: Path,
    *,
    interval: float,
    dedup_seconds: float,
    out_path: Path,
) -> None:
    _say(f"watching {log_path}")
    offset = _log_size(log_path)
    last_trigger = 0.0
    append_snapshot(
        {
            "event": "watchdog_start",
            "pid": os.getpid(),
            "log_size": offset,
            "log_path": str(log_path),
        },
        out_path,
    )
    while True:
        try:
            size = _log_size(log_path)
            if size > offset:
                with log_path.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    new_lines = fh.read().splitlines()
                offset = size
                for line in new_lines:
                    if any(p.search(line) for p in TRIGGER_PATTERNS):
                        now = time.monotonic()
                        if now - last_trigger < dedup_seconds:
                            continue
                        last_trigger = now
                        snapshot = capture_snapshot()
                        snapshot["triggerLine"] = line
                        path = append_snapshot(snapshot, out_path)
                        _say(f"[{_now_iso()}] trigger -> {path}")
        except KeyboardInterrupt:
            _say("stopped")
            return
        except Exception as exc:  # keep the tail alive
            _say(f"watch error: {exc}")
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="capture one snapshot and exit")
    parser.add_argument("--watch", action="store_true", help="tail the log and snapshot on triggers")
    parser.add_argument("--interval", type=float, default=1.0, help="tail poll interval seconds")
    parser.add_argument("--dedup", type=float, default=5.0, help="min seconds between snapshots for the same storm")
    parser.add_argument("--out", type=Path, default=None, help="snapshot output path")
    args = parser.parse_args(argv)

    runtime = hud_runtime_dir()
    log_path = runtime / "renderer_fallback.log"
    out_path = args.out or (runtime / SNAPSHOT_FILENAME)

    if args.once:
        snapshot = capture_snapshot()
        path = append_snapshot(snapshot, out_path)
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        print(f"\nsnapshot appended to {path}")
        return 0
    if not _singleton_acquired(SINGLETON_MUTEX_NAME):
        _say("another watchdog instance is already watching; exiting")
        return 0
    watch(log_path, interval=args.interval, dedup_seconds=args.dedup, out_path=out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
