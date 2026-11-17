"""One-shot opt-in memory snapshot for the running HUD process."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import tracemalloc
from typing import Mapping


MEMORY_PROFILE_ENV = "CODEX_USAGE_HUD_MEMORY_PROFILE"
MEMORY_PROFILE_FILENAME = "hud_memory_profile.json"
_active = False
_captured = False
_owns_tracing = False
_trace_started_stage = ""
_LOGGER = logging.getLogger("codex_usage_hud.memory_profile")


def start_if_enabled(
    environ: Mapping[str, str] | None = None,
    *,
    stage: str = "process-entrypoint",
) -> bool:
    global _active, _owns_tracing, _trace_started_stage

    values = os.environ if environ is None else environ
    enabled = str(values.get(MEMORY_PROFILE_ENV, "")).strip().lower()
    if enabled not in {"1", "true", "yes", "on"} or _captured:
        return False
    if _active:
        return True
    _owns_tracing = not tracemalloc.is_tracing()
    if _owns_tracing:
        tracemalloc.start(1)
    _trace_started_stage = str(stage)
    _active = True
    return True


def capture_profile_once(
    *,
    reason: str = "first-session-renderer-update",
    output_path: Path | str | None = None,
) -> bool:
    global _active, _captured

    if not _active or _captured:
        return False
    _captured = True
    try:
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        snapshot = tracemalloc.take_snapshot()
        top_lines = []
        by_file: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for stat in snapshot.statistics("lineno")[:40]:
            frame = stat.traceback[0]
            filename = _source_label(frame.filename)
            top_lines.append(
                {
                    "source": filename,
                    "line": frame.lineno,
                    "bytes": stat.size,
                    "allocations": stat.count,
                }
            )
            by_file[filename][0] += stat.size
            by_file[filename][1] += stat.count
        try:
            process = _process_memory()
        except Exception as exc:
            process = {
                "source": "unavailable",
                "error_type": type(exc).__name__,
                "private_bytes": None,
                "working_set_bytes": None,
                "peak_working_set_bytes": None,
            }
        private_bytes = process.get("private_bytes")
        report = {
            "schema_version": 1,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "reason": str(reason),
            "pid": os.getpid(),
            "profiling": {
                "tracemalloc_frames": 1,
                "tracemalloc_started_here": _owns_tracing,
                "trace_started_stage": _trace_started_stage,
                "current_traced_bytes": current_bytes,
                "peak_traced_bytes": peak_bytes,
                "private_minus_traced_bytes": (
                    private_bytes - current_bytes
                    if isinstance(private_bytes, int)
                    else None
                ),
            },
            "process": process,
            "runtime": {
                "loaded_modules": len(sys.modules),
                "active_threads": threading.active_count(),
            },
            "top_allocation_lines": top_lines,
            "top_allocation_files": [
                {"source": name, "bytes": values[0], "allocations": values[1]}
                for name, values in sorted(
                    by_file.items(), key=lambda item: item[1][0], reverse=True
                )[:30]
            ],
        }
        _write_report(report, output_path)
        return True
    except Exception as exc:
        _LOGGER.warning(
            "memory_profile_capture_failed error_type=%s", type(exc).__name__
        )
        return False
    finally:
        _active = False
        if _owns_tracing and tracemalloc.is_tracing():
            tracemalloc.stop()


def _source_label(filename: str) -> str:
    if filename.startswith("<"):
        return filename
    path = Path(filename)
    project_root = Path(__file__).resolve().parents[2]
    try:
        return path.resolve().relative_to(project_root).as_posix()
    except (OSError, ValueError):
        return path.name


def _process_memory() -> dict[str, int | str | None]:
    if os.name != "nt":
        return {
            "source": "unavailable",
            "private_bytes": None,
            "working_set_bytes": None,
            "peak_working_set_bytes": None,
        }

    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "peak_working",
                "working",
                "peak_paged",
                "paged",
                "peak_nonpaged",
                "nonpaged",
                "pagefile",
                "peak_pagefile",
                "private",
            )
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return {
        "source": "GetProcessMemoryInfo",
        "private_bytes": counters.private,
        "working_set_bytes": counters.working,
        "peak_working_set_bytes": counters.peak_working,
    }


def _write_report(report: dict[str, object], output_path: Path | str | None) -> None:
    if output_path is None:
        from .runtime_paths import runtime_file_path

        target = runtime_file_path(MEMORY_PROFILE_FILENAME)
    else:
        target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, target)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
