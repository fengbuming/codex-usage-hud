"""Compare selected-history appends and renderer peaks on isolated file copies.

The running HUD, proxy, bubble helper and original rollouts are never changed.
Timing is untraced; allocation peaks are measured in separate operations.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
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

from benchmark_hud_cache_memory import memory


class BenchmarkClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 11, 12, 9, 0, tzinfo=timezone.utc)
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)


def digest(value):
    """Canonical streaming digest, without materializing disk-backed histories."""
    target = hashlib.sha256()

    def visit(item):
        if is_dataclass(item):
            visit({f.name: getattr(item, f.name) for f in fields(item)})
        elif isinstance(item, Mapping):
            target.update(b'{')
            for key in sorted(item):
                visit(key)
                visit(item[key])
            target.update(b'}')
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            target.update(b'[')
            for child in item:
                visit(child)
            target.update(b']')
        else:
            if isinstance(item, Path):
                item = item.name
            elif isinstance(item, datetime):
                item = item.isoformat()
            target.update(json.dumps(item, ensure_ascii=False, default=str).encode('utf-8'))
            target.update(b'\0')

    visit(value)
    return target.hexdigest()


def worker(source, corpus):
    sys.path.insert(0, str(source.resolve()))
    from codex_usage_hud.core import JsonlSessionParser
    from codex_usage_hud.disk_sequence import DecodedHistoryCache, spill_snapshot_history
    from codex_usage_hud import renderer_payload_builder as builder
    # Freeze module clocks before parsing so timing-dependent text compares at
    # the same instant. No production clock or live process is patched.
    for name, module in tuple(sys.modules.items()):
        if name.startswith('codex_usage_hud') and getattr(module, 'datetime', None) is datetime:
            module.datetime = BenchmarkClock

    results = []
    for path in sorted(corpus.glob('*.jsonl')):
        parser = JsonlSessionParser()
        cache = DecodedHistoryCache()
        start = time.perf_counter()
        snapshot, state = parser.parse_file_incremental(path, _record_cache=cache)
        spill_snapshot_history(snapshot, cache)
        cold_ms = (time.perf_counter() - start) * 1000
        snapshot_hashes = [digest(snapshot)]
        payload_hashes = [digest(builder.payload_from_snapshot(snapshot, support_images=[]))]
        update_times = []
        for index in range(6):
            if index % 2:
                payload = {'type': 'token_count', 'info': {
                    'last_token_usage': {'input_tokens': 100, 'cached_input_tokens': 20,
                                         'output_tokens': 10, 'total_tokens': 110},
                    'total_token_usage': {'total_tokens': 900000000 + index * 110}}}
            else:
                payload = {'type': 'agent_message', 'message': 'fixed append' * 50}
            record = {'type': 'event_msg', 'timestamp': f'2026-11-12T08:01:{index:02d}Z', 'payload': payload}
            with path.open('a', encoding='utf-8') as file:
                file.write(json.dumps(record) + '\n')
            # last_file_mtime is part of ParsedSession. Give both independent
            # copies the same logical file timestamp after each append.
            os.utime(path, ns=(1794466800000000000 + index * 1000000000,) * 2)
            start = time.perf_counter()
            snapshot, state = parser.parse_file_incremental(path, state, _record_cache=cache)
            spill_snapshot_history(snapshot, cache)
            update_times.append((time.perf_counter() - start) * 1000)
            snapshot_hashes.append(digest(snapshot))
            payload_hashes.append(digest(builder.payload_from_snapshot(snapshot, support_images=[])))
        gc.collect()
        process = memory()
        tracemalloc.start()
        projected = builder._top_heavy_rounds(snapshot)
        _current, renderer_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        del projected
        # Count unchanged hydration separately from real append timings.
        tracemalloc.start()
        refreshed, state = parser.parse_file_incremental(path, state, _record_cache=cache)
        spill_snapshot_history(refreshed, cache)
        _current, parse_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results.append({'file': path.name, 'source_bytes': path.stat().st_size,
                        'cold_ms': round(cold_ms, 2), 'append_ms': [round(x, 2) for x in update_times],
                        'append_median_ms': round(statistics.median(update_times), 2),
                        'private_after_updates': process, 'top_three_traced_peak_bytes': renderer_peak,
                        'unchanged_parse_traced_peak_bytes': parse_peak,
                        'snapshot_digests': snapshot_hashes, 'payload_digests': payload_hashes,
                        'decoded_bytes': cache.retained_bytes})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-src', type=Path)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker-source', type=Path)
    args = parser.parse_args()
    if args.worker_source:
        print(json.dumps(worker(args.worker_source, args.corpus)))
        return 0
    if not args.baseline_src:
        parser.error('--baseline-src is required')
    report = {}
    with tempfile.TemporaryDirectory(prefix='hud-selected-compare-') as directory:
        for label, source in [('before', args.baseline_src), ('after', Path(__file__).resolve().parents[1] / 'src')]:
            copied = Path(directory) / label
            shutil.copytree(args.corpus, copied)
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                                     '--worker-source', str(source.resolve()), '--corpus', str(copied)],
                                    capture_output=True, text=True, encoding='utf-8', check=True, timeout=120)
            report[label] = json.loads(result.stdout)
    report['snapshot_parity'] = all(a['snapshot_digests'] == b['snapshot_digests'] for a, b in zip(report['before'], report['after']))
    report['renderer_payload_parity'] = all(a['payload_digests'] == b['payload_digests'] for a, b in zip(report['before'], report['after']))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'snapshot_parity': report['snapshot_parity'], 'renderer_payload_parity': report['renderer_payload_parity'],
                      'files': len(report['after']), 'output': str(args.output)}))
    return 0 if report['snapshot_parity'] and report['renderer_payload_parity'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
