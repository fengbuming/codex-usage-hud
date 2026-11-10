"""Fast is an explicit outbound request choice, independent of account catalogs."""

import json
import sqlite3
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codex_usage_hud import codex_provider_config as config
from codex_usage_hud import provider_gateway as gateway
from codex_usage_hud.provider_gateway_auth import GATEWAY_AUTH_HEADER, GATEWAY_PATH_ID
from codex_usage_hud.provider_proxy import ProviderProxyRelay

A = "10000000-0000-4000-8000-000000000001"
B = "10000000-0000-4000-8000-000000000002"
C = "10000000-0000-4000-8000-000000000003"


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('''model_provider = "alpha"
[model_providers.alpha]
base_url = "https://alpha.example/v1"
wire_api = "responses"
experimental_bearer_token = "test-only"
''', encoding="utf-8")
    monkeypatch.setattr(config, "default_codex_config_path", lambda: path)
    monkeypatch.setattr(config, "_user_environment_value", lambda _: "")
    with gateway._state_db(path) as db:
        db.execute("INSERT INTO settings VALUES ('default_provider', 'alpha')")
    return path


def route(home, session):
    return gateway.resolve_route(home, {"session_id": session}, generation=True)


def test_fast_and_standard_override_only_service_tier_and_unset_is_byte_identical():
    payload = {"model": "custom-model", "input": [{"role": "user", "content": "原文"}],
               "stream": True, "service_tier": "ultrafast", "reasoning": {"effort": "high"}}
    body = json.dumps(payload, ensure_ascii=False).encode()
    assert gateway.apply_request_service_tier(body, None) is body
    for tier in ("fast", "default"):
        changed = json.loads(gateway.apply_request_service_tier(body, tier))
        assert changed == {**payload, "service_tier": tier}
    assert gateway.apply_request_service_tier(None, "fast") is None


def test_preferences_persist_per_chat_without_editing_codex_config(home):
    original = home.read_bytes()
    route(home, A)
    route(home, B)
    result = gateway.set_fast_request(A, True)
    assert result == {"sessionId": A, "enabled": True, "serviceTier": "fast", "gateway": False}
    assert gateway.fast_request_preferences() == {A: "fast"}
    assert route(home, A)["service_tier_override"] == "fast"
    assert route(home, B)["service_tier_override"] is None
    gateway.set_fast_request(A, False)
    assert route(home, A)["service_tier_override"] == "default"
    assert home.read_bytes() == original


def test_new_chat_default_is_bound_once_and_never_changes_existing_chats(home):
    route(home, A)
    gateway.set_fast_request("", True)
    assert route(home, A)["service_tier_override"] is None
    assert route(home, B)["service_tier_override"] == "fast"
    gateway.set_fast_request("", False)
    assert route(home, B)["service_tier_override"] == "fast"
    assert route(home, C)["service_tier_override"] == "default"


def test_forced_fast_overrides_every_chat_until_disabled(home):
    route(home, A)
    gateway.set_fast_request(A, False)
    gateway.set_fast_requests_forced(True)
    assert route(home, A)["service_tier_override"] == "fast"
    assert route(home, B)["service_tier_override"] == "fast"
    assert route(home, C)["service_tier_override"] == "fast"

    gateway.set_fast_requests_forced(False)
    assert route(home, A)["service_tier_override"] == "default"
    assert route(home, B)["service_tier_override"] is None


def test_historical_chat_does_not_inherit_new_chat_default(home):
    with sqlite3.connect(home.parent / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, model_provider TEXT)")
        db.execute("INSERT INTO threads VALUES (?, 'alpha')", (A,))
    gateway.set_fast_request("", True)
    assert route(home, A)["service_tier_override"] is None


@pytest.mark.parametrize("session,enabled", [("wrong-id", True), (A, "true"), (A, None)])
def test_invalid_setting_does_not_save(home, session, enabled):
    with pytest.raises(ValueError):
        gateway.set_fast_request(session, enabled)
    assert gateway.fast_request_preferences() == {}


@pytest.mark.parametrize("endpoint,tier", [("responses", "fast"), ("responses", "default"), ("responses/compact", None)])
def test_relay_sends_choice_only_to_responses_and_preserves_upstream_reply(home, endpoint, tier):
    relay = ProviderProxyRelay()
    relay.routes[GATEWAY_PATH_ID] = {"gateway_config": str(home), "token": "test-token"}
    gateway.set_fast_request(A, tier != "default")
    body = b'{"model":"custom-model","input":[],"service_tier":"priority"}'
    handler = object.__new__(relay._handler_type())
    handler.command, handler.path = "POST", f"/{GATEWAY_PATH_ID}/v1/{endpoint}"
    handler.rfile, handler.wfile = BytesIO(body), BytesIO()
    handler.headers = {"session_id": A, GATEWAY_AUTH_HEADER: "test-token", "Content-Length": str(len(body))}
    handler.send_response_only, handler.send_header, handler.end_headers = MagicMock(), MagicMock(), MagicMock()
    upstream_reply = b'{"service_tier":"default","id":"actual-upstream-response"}'
    with patch("http.client.HTTPSConnection") as connection:
        response = connection.return_value.getresponse.return_value
        response.status, response.headers = 200, {}
        response.getheaders.return_value, response.getheader.return_value = [], ""
        response.read1.side_effect = [upstream_reply, b""]
        handler._forward()
    sent = connection.return_value.request.call_args
    assert sent is not None
    assert json.loads(sent.kwargs["body"])["service_tier"] == (tier or "priority")
    assert handler.wfile.getvalue() == upstream_reply


def test_renderer_command_ack_and_settings_projection(home):
    from codex_usage_hud.config import UserConfig
    from codex_usage_hud.runtime_commands import _handle_renderer_settings_command
    from codex_usage_hud.runtime_settings import partial_domains_for_command

    current = UserConfig.defaults()
    command = {"action": "fastRequestSet", "sessionId": A, "enabled": True, "requestId": "fast-1"}
    result = _handle_renderer_settings_command(command, SimpleNamespace(user_config=current), MagicMock(), MagicMock())
    assert result["kind"] == ""
    assert result["requestId"] == "fast-1"
    assert result["providerGateway"]["serviceTier"] == "fast"
    assert partial_domains_for_command(command, previous_config=current, current_config=current) == {"settings"}
