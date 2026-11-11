"""Compare search parity, latency and retained allocations on a safe DB copy.

Example: python tools/benchmark_session_search_memory.py PATH/TO/session-search.sqlite
The live database is opened read-only and backed up to a temporary directory.
No HUD reload, source migration, configuration change or corpus pruning occurs.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

QUERIES = (
    "renderer",
    "active_session",
    "session_search.py",
    "E:/Project",
    "model provider",
    "session cleanup",
    "token",
    "扫描",
    "索引",
    "优化方案",
    "中文",
    "工具",
    "a",
    "ab",
    "中",
    "0002398",
    "renderer active session",
    "搜索",
    "workdir",
    "pytest",
    "配置",
    "codex_usage_hud",
    "SQLite",
    "no_such_needle_f14d20d9",
)


def _process_memory():
    if sys.platform != "win32":
        return None
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

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    if not psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        return None
    return {"private_bytes": counters.private, "working_set_bytes": counters.working}


def _worker(engine, path):
    from codex_usage_hud.core.session_search import SessionSearchIndex
    from codex_usage_hud.core.session_search_disk import OnDemandSessionSearchIndex

    index = (
        SessionSearchIndex if engine == "resident" else OnDemandSessionSearchIndex
    )(path)
    start = time.perf_counter()
    loaded = index.load()
    prepare_ms = (time.perf_counter() - start) * 1000
    if engine == "disk":
        index.close()
        index = OnDemandSessionSearchIndex(path)
    else:
        del index
        gc.collect()
        index = SessionSearchIndex(path)
    gc.collect()
    process_baseline = _process_memory()
    tracemalloc.start()
    start = time.perf_counter()
    index.load()
    restart_ms_traced = (time.perf_counter() - start) * 1000
    gc.collect()
    ready_bytes = tracemalloc.get_traced_memory()[0]
    digests = []
    results = []
    for query in QUERIES:
        result = index.search(query)
        digests.append(
            hashlib.sha256(
                json.dumps(result, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
        )
        results.append(result)
    path.with_name(engine + "-results.json").write_text(
        json.dumps(results, ensure_ascii=False), encoding="utf-8"
    )
    del results
    del result
    gc.collect()
    active_bytes = tracemalloc.get_traced_memory()[0]
    mapped_bytes = index._stream_path().stat().st_size if engine == "disk" else 0
    if engine == "disk":
        index.unload_memory()
    gc.collect()
    released_bytes = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    # Time independently: allocation tracing materially changes both engines.
    cold = []
    for query in QUERIES:
        if engine == "disk":
            index.unload_memory()
        start = time.perf_counter()
        index.search(query)
        cold.append((time.perf_counter() - start) * 1000)
    warm = []
    for _ in range(5):
        for query in QUERIES:
            start = time.perf_counter()
            index.search(query)
            warm.append((time.perf_counter() - start) * 1000)
    process_active = _process_memory()
    if engine == "disk":
        index.unload_memory()
        gc.collect()
        process_released = _process_memory()
        index.close()
    else:
        process_released = _process_memory()
    del index
    gc.collect()
    index = (
        SessionSearchIndex if engine == "resident" else OnDemandSessionSearchIndex
    )(path)
    start = time.perf_counter()
    index.load()
    restart_ms = (time.perf_counter() - start) * 1000
    if engine == "disk":
        index.close()
    return {
        "indexed": loaded["indexed"],
        "prepare_ms": round(prepare_ms, 2),
        "restart_ms_traced": round(restart_ms_traced, 2),
        "restart_ms": round(restart_ms, 2),
        "ready_bytes": ready_bytes,
        "active_bytes": active_bytes,
        "mapped_file_bytes": mapped_bytes,
        "released_bytes": released_bytes,
        "cold_query_ms": [round(value, 3) for value in cold],
        "warm_p50_ms": round(statistics.median(warm), 3),
        "warm_p95_ms": round(sorted(warm)[int(len(warm) * 0.95) - 1], 3),
        "warm_max_ms": round(max(warm), 3),
        "digests": digests,
        "process_memory": {
            "baseline": process_baseline,
            "active": process_active,
            "released": process_released,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--worker", choices=("resident", "disk"), help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(_worker(args.worker, args.database)))
        return 0
    with tempfile.TemporaryDirectory(prefix="hud-search-memory-") as directory:
        database = Path(directory) / "search.sqlite"
        source = sqlite3.connect(
            args.database.resolve().as_uri() + "?mode=ro", uri=True
        )
        target = sqlite3.connect(database)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        snapshot = args.database.with_name(args.database.name + ".memory")
        if snapshot.exists():
            shutil.copyfile(snapshot, database.with_name(database.name + ".memory"))
        report = {}
        for engine in ("resident", "disk"):
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    str(database),
                    "--worker",
                    engine,
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            report[engine] = json.loads(completed.stdout)
        report["identical_results"] = report["resident"].pop("digests") == report[
            "disk"
        ].pop("digests")
        if not report["identical_results"]:
            before = json.loads(
                database.with_name("resident-results.json").read_text(encoding="utf-8")
            )
            after = json.loads(
                database.with_name("disk-results.json").read_text(encoding="utf-8")
            )
            report["differences"] = [
                {"query": query, "resident": a, "disk": b}
                for query, a, b in zip(QUERIES, before, after)
                if a != b
            ]
        report["query_count"] = len(QUERIES)
        report["measurement"] = (
            "Python retained allocations; cold queries release application caches, not OS disk caches"
        )
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 0 if report["identical_results"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
