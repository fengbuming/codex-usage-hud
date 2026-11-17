"""Measure on-demand renderer history on fixed local rollout copies.

Uses no CDP, running HUD, provider requests or original-file writes. Payload
sizes and full historical-content parity are separate from process memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import sys
import tempfile
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from codex_usage_hud.core.parser import JsonlSessionParser  # noqa: E402
from codex_usage_hud.disk_sequence import DecodedHistoryCache, spill_snapshot_history  # noqa: E402
from codex_usage_hud import renderer_payload_builder as builder  # noqa: E402
from codex_usage_hud.renderer_history import RendererHistoryStore  # noqa: E402
from codex_usage_hud.renderer_payload_delta import RendererPayloadDeltaCache  # noqa: E402
from codex_usage_hud.renderer_client import RendererHudClient  # noqa: E402


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')


def resolve(store, value):
    if isinstance(value, dict):
        if value.get('_hudHistoryRef'):
            result = store.read(value['_hudHistoryRef'])
            if result is None:
                raise RuntimeError('History reference expired during parity check')
            return resolve(store, result['value'])
        return {key: resolve(store, item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(store, item) for item in value]
    return value


def check_parity(store, baseline, compact):
    expected = baseline.to_json()
    actual = resolve(store, compact.to_json())
    tasks = []
    for index, item in enumerate(actual['topDetails'].get('activityTasks', [])):
        if item.get('current'):
            # The current task reads the existing top details directly. Verify
            # the fields used by the selected-task renderer against that view.
            latest = expected['topDetails']['activityTasks'][index]
            for key in ('currentTask', 'executing', 'activityState', 'activityTrail', 'workOutput', 'workTools', 'slow', 'gap'):
                if latest[key] != actual['topDetails'][key]:
                    raise AssertionError('Current task projection differs: ' + key)
            tasks.append(latest)
        else:
            tasks.append(item.get('detailRef', item))
    if tasks:
        actual['topDetails']['activityTasks'] = tasks
        actual['payloadDomains']['currentSession']['topDetails']['activityTasks'] = tasks
    if actual != expected:
        raise AssertionError('Expanded historical payload differs')
    return hashlib.sha256(encode(expected)).hexdigest()


def measure(path):
    parser = JsonlSessionParser()
    cache = DecodedHistoryCache()
    snapshot, _state = parser.parse_file_incremental(path, _record_cache=cache)
    spill_snapshot_history(snapshot, cache)
    store = RendererHistoryStore()
    store.url = 'http://127.0.0.1:57322/renderer-history?access_token=benchmark'
    try:
        baseline = builder.payload_from_snapshot(snapshot, support_images=[])
        scope = store.begin(snapshot)
        compact = builder.payload_from_snapshot(snapshot, support_images=[], history_scope=scope)
        scope.finish()
        digest = check_parity(store, baseline, compact)
        scope.acknowledge()
        delta = RendererPayloadDeltaCache()
        _, pending = delta.prepare(compact.to_json(), {}, RendererHudClient._payload_domain_digest)
        delta.commit(pending)
        times = {'baseline': [], 'on_demand': []}
        next_payload = None
        for label in times:
            for _ in range(5):
                start = time.perf_counter()
                if label == 'baseline':
                    built = builder.payload_from_snapshot(snapshot, support_images=[]).to_json()
                else:
                    current = store.begin(snapshot)
                    built = builder.payload_from_snapshot(snapshot, support_images=[], history_scope=current).to_json()
                    current.finish()
                    next_payload, pending = delta.prepare(built, {'currentSession': 'ack'}, RendererHudClient._payload_domain_digest)
                    current.acknowledge()
                    delta.commit(pending)
                    built = next_payload
                encode(built)
                times[label].append((time.perf_counter() - start) * 1000)
        peaks = {}
        for label in times:
            tracemalloc.start()
            try:
                if label == 'baseline':
                    built = builder.payload_from_snapshot(snapshot, support_images=[]).to_json()
                else:
                    current = store.begin(snapshot)
                    built = builder.payload_from_snapshot(snapshot, support_images=[], history_scope=current).to_json()
                    current.finish()
                    built, pending = delta.prepare(built, {'currentSession': 'ack'}, RendererHudClient._payload_domain_digest)
                    current.acknowledge()
                    delta.commit(pending)
                encode(built)
                peaks[label] = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
        return {'file': path.name, 'source_bytes': path.stat().st_size,
                'tasks': len(snapshot.activity_tasks), 'rounds': len(snapshot.session_request_history),
                'baseline_bytes': len(encode(baseline.to_json())), 'first_on_demand_bytes': len(encode(compact.to_json())),
                'unchanged_history_update_bytes': len(encode(next_payload)),
                'median_build_and_serialize_ms': {key: round(statistics.median(value), 2) for key, value in times.items()},
                'traced_peak_bytes': peaks, 'complete_payload_sha256': digest,
                'historical_payload_parity': True,
                'artifact_count': len(store._tokens)}
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = {'method': 'Fixed copies; no live process or provider calls; untraced timings and separate traced allocation peaks', 'results': []}
    with tempfile.TemporaryDirectory(prefix='hud-renderer-history-compare-') as directory:
        copied = Path(directory) / 'corpus'
        shutil.copytree(args.corpus, copied)
        for path in sorted(copied.glob('*.jsonl')):
            report['results'].append(measure(path))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
