from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from codex_usage_hud.renderer_client import RendererHudClient
from codex_usage_hud.renderer_payload_delta import RendererPayloadDeltaCache


def envelope(**values):
    return {**values, "payloadDomains": {key: {key: value} for key, value in values.items()}}


def prepare(cache, payload, baseline=None):
    return cache.prepare(payload, baseline or {}, RendererHudClient._payload_domain_digest)


def usage(rows):
    return {"state": "ready", "today": {"sessions": rows, "totals": {"tokens": 99}}}


def test_inventory_is_sent_once_but_resent_on_revision_or_document_reset():
    cache = RendererPayloadDeltaCache()
    data = {"revision": "r1", "sessions": [{"id": "first"}], "workdirs": [{"id": "project"}]}
    payload = envelope(sessionCleanup=data)
    first, pending = prepare(cache, payload)
    assert first == payload
    cache.commit(pending)
    updated = envelope(sessionCleanup={**data, "search": {"processed": 1}})
    reduced, _ = prepare(cache, updated, {"sessionCleanup": "ack"})
    assert "sessions" not in reduced["sessionCleanup"]
    assert "workdirs" not in reduced["sessionCleanup"]
    assert reduced["payloadDomains"]["sessionCleanup"]["sessionCleanup"] == reduced["sessionCleanup"]
    changed = envelope(sessionCleanup={**data, "revision": "r2"})
    assert "sessions" in prepare(cache, changed, {"sessionCleanup": "ack"})[0]["sessionCleanup"]
    assert prepare(cache, payload)[0] == payload


def test_unacknowledged_updates_do_not_advance_the_list_baseline():
    cache = RendererPayloadDeltaCache()
    rows = [{"id": str(i), "tokens": 1} for i in range(8)]
    initial = envelope(usageInsights=usage(rows))
    _, pending = prepare(cache, initial)
    # A failed initial send must retry full lists.
    assert prepare(cache, initial, {"usageInsights": "ack"})[0] == initial
    cache.commit(pending)
    changed = [*rows]
    changed[1] = {**changed[1], "tokens": 2}
    prepare(cache, envelope(usageInsights=usage(changed)), {"usageInsights": "ack"})
    changed[4] = {**changed[4], "tokens": 3}
    retry, _ = prepare(cache, envelope(usageInsights=usage(changed)), {"usageInsights": "ack"})
    assert [item[0] for item in retry["usageInsights"]["_listPatches"][0]["items"]] == [1, 4]


def test_loading_payload_invalidates_obsolete_ranking_baselines():
    cache = RendererPayloadDeltaCache()
    rows = [{"id": str(i)} for i in range(8)]
    _, pending = prepare(cache, envelope(usageInsights=usage(rows)))
    cache.commit(pending)
    _, pending = prepare(cache, envelope(usageInsights={"state": "loading"}), {"usageInsights": "ack"})
    cache.commit(pending)
    payload = envelope(usageInsights=usage(rows))
    assert prepare(cache, payload, {"usageInsights": "ack"})[0] == payload


def test_renderer_reconstructs_reordered_truncated_and_empty_lists_without_loss():
    from codex_usage_hud.renderer_assets.usage_insights import TEXT

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable")
    cache = RendererPayloadDeltaCache()
    rows = [{"id": str(i), "title": f"会话 {i}", "tokens": i} for i in range(12)]
    full, pending = prepare(cache, envelope(usageInsights=usage(rows)))
    cache.commit(pending)
    changed = list(rows)
    changed[1], changed[2] = changed[2], changed[1]
    changed = changed[:-2]
    partial, pending = prepare(cache, envelope(usageInsights=usage(changed)), {"usageInsights": "ack"})
    cache.commit(pending)
    empty, _ = prepare(cache, envelope(usageInsights=usage([])), {"usageInsights": "ack"})
    assert "sessions" not in partial["usageInsights"]["today"]
    start = TEXT.index("function applyUsageInsightsPayload(")
    end = TEXT.index("    function install()", start)
    script = (
        'const usageInsightsState = {data: null}; function rerenderUsageInsightsIfVisible() {}\n'
        + TEXT[start:end]
        + '\nconst observed = [];\n'
        + '\n'.join(
            f'applyUsageInsightsPayload(null, {json.dumps(payload, ensure_ascii=True)});'
            'observed.push(JSON.parse(JSON.stringify(usageInsightsState.data)));'
            for payload in (full, partial, empty)
        )
        + '\nprocess.stdout.write(JSON.stringify(observed));'
    )
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", check=True, timeout=10)
    assert json.loads(completed.stdout) == [usage(rows), usage(changed), usage([])]


def test_single_row_update_avoids_resending_large_unchanged_rankings():
    cache = RendererPayloadDeltaCache()
    rows = [{"id": str(i), "title": "long title " * 100, "tokens": 1} for i in range(100)]
    _, pending = prepare(cache, envelope(usageInsights=usage(rows)))
    cache.commit(pending)
    changed = list(rows)
    changed[0] = {**changed[0], "tokens": 2}
    payload = envelope(usageInsights=usage(changed))
    reduced, _ = prepare(cache, payload, {"usageInsights": "ack"})
    assert len(json.dumps(reduced)) < len(json.dumps(payload)) / 20


def test_client_acknowledges_deltas_only_after_success_and_resends_full_after_page_loss(monkeypatch):
    client = RendererHudClient(enabled=True)
    client._script_identifier = "installed"
    client._target_id = "page"
    client._payload_digest_target_id = "page"
    monkeypatch.setattr(client, "_page_target", lambda: {
        "id": "page", "webSocketDebuggerUrl": "ws://127.0.0.1/test",
    })
    monkeypatch.setattr(client, "_install", lambda *args, **kwargs: None)
    monkeypatch.setattr(client, "_record_update_failure", lambda **kwargs: None)
    sent = []
    succeed = True

    def send(url, payload):
        sent.append(payload)
        return succeed

    monkeypatch.setattr(client, "_send_update", send)
    rows = [{"id": str(i), "tokens": 1} for i in range(10)]
    assert client.update_payload(envelope(usageInsights=usage(rows)))
    changed = list(rows)
    changed[0] = {**rows[0], "tokens": 2}
    succeed = False
    client.last_update_metrics["rendererAliveProbe"] = "error"
    assert not client.update_payload(envelope(usageInsights=usage(changed)))
    assert sent[-1]["usageInsights"]["_listPatches"][0]["items"][0][0] == 0
    changed[2] = {**rows[2], "tokens": 3}
    succeed = True
    assert client.update_payload(envelope(usageInsights=usage(changed)))
    assert [item[0] for item in sent[-1]["usageInsights"]["_listPatches"][0]["items"]] == [0, 2]
    # Page/reinstall resets domain acknowledgement; old list hashes must not
    # permit patches against a newly empty renderer.
    client._payload_domain_digests.clear()
    client._payload_extras_digest = None
    assert client.update_payload(envelope(usageInsights=usage(changed)))
    assert "sessions" in sent[-1]["usageInsights"]["today"]
    assert "_listPatches" not in sent[-1]["usageInsights"]
