from __future__ import annotations

import pytest

from codex_usage_hud.core.parser import ParsedSession
from codex_usage_hud import renderer_client, renderer_payload_builder
from codex_usage_hud.ui import renderer_domains


def test_renderer_domains_exports_the_client_owner() -> None:
    assert renderer_domains.RendererHudClient is renderer_client.RendererHudClient


def test_renderer_domains_exports_the_payload_builder_owner() -> None:
    assert (
        renderer_domains.payload_from_snapshot
        is renderer_payload_builder.payload_from_snapshot
    )


def test_renderer_domains_rejects_unknown_dynamic_owner_name() -> None:
    with pytest.raises(AttributeError):
        getattr(renderer_domains, "_unsupported_payload_builder_symbol")


def test_client_resolves_transport_from_its_owner(monkeypatch) -> None:
    client = renderer_client.RendererHudClient(
        port=9229,
        timeout_seconds=0.05,
        enabled=True,
    )
    client._active_session_binding = None
    calls: list[tuple[str, str]] = []

    def fake_send(
        websocket_url: str,
        method: str,
        params: dict[str, object],
        timeout_seconds: float,
    ) -> dict[str, object]:
        del params, timeout_seconds
        calls.append((websocket_url, method))
        return {"result": {"result": {"value": {"ok": True, "applyMs": 1.0}}}}

    monkeypatch.setattr(renderer_client, "send_cdp_command", fake_send)

    assert client._send_update(  # noqa: SLF001
        "ws://127.0.0.1:9229/devtools/page/1",
        {"topLine": "A"},
    )
    assert calls == [
        ("ws://127.0.0.1:9229/devtools/page/1", "Runtime.evaluate")
    ]


def test_client_uses_its_payload_and_support_owners(monkeypatch) -> None:
    client = renderer_client.RendererHudClient(enabled=True)
    client._theme_snapshot = object()  # noqa: SLF001
    client.update_payload = lambda payload: payload == {"requestLine": "owner"}  # type: ignore[method-assign]
    captured: dict[str, object] = {}

    class FakePayload:
        def to_json(self) -> dict[str, object]:
            return {"requestLine": "owner"}

    def fake_builder(snapshot: ParsedSession, **kwargs: object) -> FakePayload:
        captured["snapshot"] = snapshot
        captured.update(kwargs)
        return FakePayload()

    monkeypatch.setattr(renderer_client, "support_qr_payload", lambda: [])
    monkeypatch.setattr(renderer_client, "_renderer_theme_payload", lambda _: {})
    monkeypatch.setattr(renderer_client, "payload_from_snapshot", fake_builder)

    snapshot = ParsedSession(status="waiting")
    assert client.update(snapshot)
    assert captured["snapshot"] is snapshot
    assert captured["support_images"] == []
    assert captured["settings_path"] is None


def test_client_passes_session_index_to_payload_builder(monkeypatch) -> None:
    client = renderer_client.RendererHudClient(enabled=True)
    client._theme_snapshot = object()  # noqa: SLF001
    client.update_payload = lambda _payload: True  # type: ignore[method-assign]
    captured: dict[str, object] = {}

    class FakePayload:
        def to_json(self) -> dict[str, object]:
            return {"requestLine": "owner"}

    def fake_builder(snapshot: ParsedSession, **kwargs: object) -> FakePayload:
        del snapshot
        captured.update(kwargs)
        return FakePayload()

    monkeypatch.setattr(renderer_client, "support_qr_payload", lambda: [])
    monkeypatch.setattr(renderer_client, "_renderer_theme_payload", lambda _: {})
    monkeypatch.setattr(renderer_client, "payload_from_snapshot", fake_builder)

    assert client.update(
        ParsedSession(status="waiting"),
        session_index={"jobState": "attached", "builtCount": 2},
    )
    assert captured["session_index"] == {
        "jobState": "attached",
        "builtCount": 2,
    }


def test_client_quiesce_blocks_all_cdp_entrypoints_and_resume_reenables() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    client = renderer_client.RendererHudClient(enabled=True)
    # 塞一个假 binding，验证 quiesce 断开 socket 但保留对象供恢复重连。
    fake_binding = MagicMock()
    client._active_session_binding = fake_binding  # noqa: SLF001
    client._script_identifier = "sid"  # noqa: SLF001

    client.quiesce()

    assert client.quiesced
    fake_binding.close.assert_called_once_with()
    # 对象保留：恢复后 update 流程会 ensure 重连，通道不丢。
    assert client._active_session_binding is fake_binding  # noqa: SLF001
    # script id 保留：页面 realm 在锁屏期间存续，恢复后无需重装整个 bundle
    # （白屏根治 P0-A：resume 走苏醒门而不是强制全量重装）。
    assert client._script_identifier == "sid"  # noqa: SLF001
    assert client.update(SimpleNamespace()) is False
    assert client.update_payload({}) is False
    assert client.probe_connection() is False
    assert client.report_active_session() is False

    client.resume()

    assert not client.quiesced
    # resume 后进入苏醒门（warmup），只允许 1+1 探活，不再清 target cache
    # 强制全量重装。
    assert client._warming  # noqa: SLF001
    assert client._warmup_acks == 0  # noqa: SLF001


def test_client_update_and_probe_short_circuit_before_cdp_when_quiesced() -> None:
    client = renderer_client.RendererHudClient(enabled=True)
    client.quiesce()

    # 短路必须发生在任何网络/CDP 调用之前（不触网）。
    assert client.update_payload({"k": "v"}) is False
    assert client.probe_connection() is False
    client.resume()


def test_resume_warmup_has_bounded_recovery_for_slow_healthy_renderer(monkeypatch) -> None:
    client = renderer_client.RendererHudClient(enabled=True)
    client.resume()
    probes = 0

    def slow_probe(*, timeout_seconds: float) -> tuple[bool, float]:
        del timeout_seconds
        nonlocal probes
        probes += 1
        return True, 120.0

    monkeypatch.setattr(client, "_probe_renderer_alive", slow_probe)
    for _ in range(renderer_client.WARMUP_MAX_PROBES):
        assert client.update_payload({"topLine": "still current"})

    assert probes == renderer_client.WARMUP_MAX_PROBES
    assert not client._warming  # noqa: SLF001
    assert client.last_update_metrics["rendererAliveProbe"] == "warmup-timeout-recover"
    assert client._script_identifier == ""  # noqa: SLF001

    applied: list[dict[str, object]] = []
    monkeypatch.setattr(
        client,
        "_update_payload_once",
        lambda payload, *, startup_retry: applied.append(payload) or True,
    )
    assert client.update_payload({"topLine": "after recovery"})
    assert applied == [{"topLine": "after recovery"}]


def test_degraded_renderer_probe_recovers_while_idle(monkeypatch) -> None:
    client = renderer_client.RendererHudClient(enabled=True)
    monkeypatch.setattr(client, "close", lambda **kwargs: None)
    monkeypatch.setattr(client, "_rebuild_bindings", lambda: None)
    monkeypatch.setattr(client, "_clear_target_cache", lambda **kwargs: None)
    monkeypatch.setattr(
        client,
        "_probe_renderer_alive",
        lambda *, timeout_seconds: (True, 12.0),
    )

    client.degrade("renderer hung")

    assert client.recovery_seconds_until_probe() == 0.0
    assert client.advance_recovery()
    assert not client._degraded  # noqa: SLF001
    assert client.recovery_refresh_pending()
    assert client.recovery_seconds_until_probe() is None
