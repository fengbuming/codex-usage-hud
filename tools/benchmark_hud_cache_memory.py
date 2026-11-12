"""Compare cache retention and helper imports in isolated child processes.

Pass --baseline-src pointing at an unchanged src/ snapshot. Inputs are copied
to a temporary corpus; the live HUD and its caches are never reloaded/modified.
"""

from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc


def memory():
    if sys.platform != "win32":
        return None
    import ctypes as c
    from ctypes import wintypes as w

    class Counters(c.Structure):
        _fields_ = [("cb", w.DWORD), ("faults", w.DWORD)] + [
            (name, c.c_size_t)
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

    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    psapi = c.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = (w.HANDLE, c.c_void_p, w.DWORD)
    counters = Counters()
    counters.cb = c.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), c.byref(counters), counters.cb
    ):
        raise c.WinError(c.get_last_error())
    return {
        "private_bytes": counters.private,
        "working_set_bytes": counters.working,
        "peak_working_set_bytes": counters.peak_working,
    }


def deep_size(root):
    seen = set()
    stack = [root]
    total = 0
    while stack:
        value = stack.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        total += sys.getsizeof(value)
        if isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, (list, tuple, set, frozenset)):
            stack.extend(value)
        elif dataclasses.is_dataclass(value) and not isinstance(value, type):
            stack.extend(
                getattr(value, field.name) for field in dataclasses.fields(value)
            )
    return total


def helper_worker():
    import importlib
    from unittest.mock import patch
    from codex_usage_hud import desktop_overlay_setup as setup

    try:
        from codex_usage_hud.helper_entrypoint import main

        helper_owner = setup
    except ImportError:
        helper_owner = importlib.import_module("codex_usage_hud.runtime_orchestration")
        main = helper_owner.main

    def show_helper(_state):
        # Import the production widget classes/native libraries, without
        # starting windows or the event loop in this offline benchmark.
        from codex_usage_hud.ui.work_overlay.qt_window import OverlayWindow

        del OverlayWindow
        return 0

    with patch.object(helper_owner, "run_work_overlay_helper", side_effect=show_helper):
        assert (
            main(
                ["--work-overlay-helper", "--work-overlay-state-file", "benchmark.json"]
            )
            == 0
        )
    gc.collect()
    return {
        "process": memory(),
        "renderer_manifest_loaded": "codex_usage_hud.renderer_assets.manifest"
        in sys.modules,
        "module_count": len(sys.modules),
    }


def caches_worker(corpus):
    from codex_usage_hud.core import JsonlSessionParser
    from codex_usage_hud.core import parser as parser_module
    from codex_usage_hud.usage_cache import UsageSummaryCache

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 11, 11, 8, 0, 0, tzinfo=timezone.utc)
            return (
                value.astimezone(tz) if tz else value.astimezone().replace(tzinfo=None)
            )

    # Both child processes parse the exact same logical instant; UI elapsed
    # text and refreshed_at would otherwise differ solely by benchmark order.
    parser_module.datetime = FixedDatetime

    files = sorted(corpus.glob("*.jsonl"))
    parser = JsonlSessionParser()
    cache = UsageSummaryCache(parser, min_rescan_seconds=0)
    day = datetime(2026, 11, 11, tzinfo=timezone.utc)
    week = datetime(2026, 11, 9, tzinfo=timezone.utc)
    start = time.perf_counter()
    totals = cache.summarize(corpus, day, week)
    cold_ms = (time.perf_counter() - start) * 1000
    gc.collect()
    usage_memory = memory()
    retained = deep_size([entry.tail_state for entry in cache._entries.values()])
    snapshots = []
    states = []
    start = time.perf_counter()
    for path in files:
        snapshot, state = parser.parse_file_incremental(path)
        snapshots.append(snapshot)
        states.append(state)
    snapshot_ms = (time.perf_counter() - start) * 1000
    gc.collect()
    total_memory = memory()
    snapshot_bytes = deep_size(snapshots)
    combined_bytes = deep_size(
        [snapshots, states, [entry.tail_state for entry in cache._entries.values()]]
    )
    start = time.perf_counter()
    for index, path in enumerate(files):
        snapshot, state = parser.parse_file_incremental(path, states[index])
        snapshots[index], states[index] = snapshot, state
    reparse_ms = (time.perf_counter() - start) * 1000
    reparse_samples = [reparse_ms]
    for _ in range(4):
        start = time.perf_counter()
        for index, path in enumerate(files):
            snapshot, state = parser.parse_file_incremental(path, states[index])
            snapshots[index], states[index] = snapshot, state
        reparse_samples.append((time.perf_counter() - start) * 1000)
    contributions = []
    for path, entry in sorted(cache._entries.items()):
        # Do not deepcopy raw tails to form a comparison payload.
        values = {
            field.name: getattr(entry, field.name)
            for field in dataclasses.fields(entry)
            if field.name != "tail_state"
        }
        contributions.append((path.name, values))
    payload = {"totals": totals, "contributions": contributions, "snapshots": snapshots}

    def encode(value):
        if dataclasses.is_dataclass(value):
            return {
                field.name: getattr(value, field.name)
                for field in dataclasses.fields(value)
            }
        if isinstance(value, Path):
            return value.name
        return str(value)

    encoded = json.dumps(payload, default=encode, sort_keys=True)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    (corpus / (sys.argv[-1] + "-parity.json")).write_text(encoded, encoding="utf-8")
    # Measure streaming reader peak separately from snapshot construction.
    largest = max(files, key=lambda path: path.stat().st_size)
    tracemalloc.start()
    state = parser.load_records_incremental(largest)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del state
    return {
        "files": len(files),
        "source_bytes": sum(path.stat().st_size for path in files),
        "usage_cold_ms": round(cold_ms, 2),
        "usage_retained_bytes": retained,
        "snapshot_retained_bytes": snapshot_bytes,
        "combined_object_bytes": combined_bytes,
        "snapshot_cold_ms": round(snapshot_ms, 2),
        "snapshot_reparse_ms": round(reparse_ms, 2),
        "snapshot_reparse_median_ms": round(statistics.median(reparse_samples), 2),
        "snapshot_reparse_samples_ms": [round(value, 2) for value in reparse_samples],
        "usage_process": usage_memory,
        "combined_process": total_memory,
        "reader_traced_retained_bytes": current,
        "reader_traced_peak_bytes": peak,
        "reader_transient_bytes": peak - current,
        "parity_digest": digest,
    }


def renderer_worker():
    from codex_usage_hud import renderer_catalog, renderer_client

    del renderer_client
    gc.collect()
    process = memory()
    seen = set()
    strings = 0
    for name, module in list(sys.modules.items()):
        if not name.startswith("codex_usage_hud") or module is None:
            continue
        for value in vars(module).values():
            if isinstance(value, str) and len(value) >= 1024 and id(value) not in seen:
                seen.add(id(value))
                strings += sys.getsizeof(value)
    return {
        "process": process,
        "large_module_string_bytes": strings,
        "default_script_materialized": "RENDERER_HUD_SCRIPT" in vars(renderer_catalog),
        "script_digest": hashlib.sha256(
            renderer_catalog.renderer_hud_script_with_model_catalog([]).encode("utf-8")
        ).hexdigest(),
    }


class _SelectedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 11, 12, 2, 0, 0, tzinfo=timezone.utc)
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)


def selected_worker(corpus):
    """Measure the selected-session storage, without starting any HUD owner."""
    from collections.abc import Sequence
    from codex_usage_hud.core import JsonlSessionParser
    from codex_usage_hud.core import parser as parser_module

    parser_module.datetime = _SelectedClock
    try:
        from codex_usage_hud.disk_sequence import DecodedHistoryCache, spill_snapshot_history
    except ImportError:
        history = None
    else:
        history = DecodedHistoryCache()
    parser = JsonlSessionParser()
    snapshots, states = [], []
    files = sorted(corpus.glob('*.jsonl'))
    start = time.perf_counter()
    for path in files:
        kwargs = {'_record_cache': history} if history is not None else {}
        snapshot, state = parser.parse_file_incremental(path, **kwargs)
        if history is not None:
            spill_snapshot_history(snapshot, history)
        snapshots.append(snapshot)
        states.append(state)
    cold_ms = (time.perf_counter() - start) * 1000
    gc.collect()
    process = memory()

    def plain(value):
        if dataclasses.is_dataclass(value):
            return {field.name: plain(getattr(value, field.name)) for field in dataclasses.fields(value)}
        if isinstance(value, dict):
            return {key: plain(item) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [plain(item) for item in value]
        if isinstance(value, Path):
            return value.name
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    digest = hashlib.sha256(json.dumps(plain(snapshots), sort_keys=True, default=str).encode()).hexdigest()
    # The protected bubble entry uses its original parser/storage path.
    bubble_digest = hashlib.sha256(json.dumps(
        [plain(parser.parse_work_file(path)) for path in files], sort_keys=True, default=str
    ).encode()).hexdigest()
    return {'files': len(files), 'source_bytes': sum(path.stat().st_size for path in files),
            'cold_parse_ms': round(cold_ms, 2), 'process_after_hydration': process,
            'decoded_cache_bytes': history.retained_bytes if history is not None else None,
            'snapshot_digest': digest, 'bubble_snapshot_digest': bubble_digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-src", type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", choices=("helper", "caches", "renderer", "selected"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--label", default="worker")
    args = parser.parse_args()
    if args.worker:
        sys.path.insert(0, str(args.source.resolve()))
        result = {
            "helper": helper_worker,
            "caches": lambda: caches_worker(args.corpus),
            "selected": lambda: selected_worker(args.corpus),
            "renderer": renderer_worker,
        }[args.worker]()
        print(json.dumps(result))
        return 0
    if not args.baseline_src or not args.corpus:
        parser.error("--baseline-src and --corpus are required")
    report = {
        "method": "isolated processes, fixed corpus copy; private commit differs from resident RAM; Qt widgets imported without showing windows"
    }
    with tempfile.TemporaryDirectory(prefix="hud-caches-compare-") as directory:
        copied = Path(directory) / "sessions"
        shutil.copytree(
            args.corpus, copied, ignore=shutil.ignore_patterns("*.json", "*.sqlite*")
        )
        for label, source in (
            ("before", args.baseline_src),
            ("after", Path(__file__).resolve().parents[1] / "src"),
        ):
            report[label] = {}
            for worker in ("helper", "caches", "renderer"):
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    worker,
                    "--source",
                    str(source.resolve()),
                    "--corpus",
                    str(copied),
                    "--label",
                    label,
                ]
                env = dict(os.environ, PYTHONPATH=str(source.resolve()))
                result = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    env=env,
                )
                report[label][worker] = json.loads(result.stdout)
        report["identical_results"] = (
            report["before"]["caches"]["parity_digest"]
            == report["after"]["caches"]["parity_digest"]
        )
        report["identical_renderer_script"] = (
            report["before"]["renderer"]["script_digest"]
            == report["after"]["renderer"]["script_digest"]
        )
        if not report["identical_results"]:
            a = json.loads((copied / "before-parity.json").read_text(encoding="utf-8"))
            b = json.loads((copied / "after-parity.json").read_text(encoding="utf-8"))
            report["different_top_level"] = [key for key in a if a[key] != b[key]]
            differences = []

            def compare(a, b, path):
                if a == b or len(differences) >= 25:
                    return
                if isinstance(a, dict) and isinstance(b, dict):
                    for key in a.keys() | b.keys():
                        compare(a.get(key), b.get(key), path + "." + str(key))
                elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
                    for index, (x, y) in enumerate(zip(a, b)):
                        compare(x, y, path + f"[{index}]")
                else:
                    differences.append(path)

            compare(a, b, "result")
            report["different_fields"] = differences
        text = json.dumps(report, indent=2)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
    return (
        0 if report["identical_results"] and report["identical_renderer_script"] else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
