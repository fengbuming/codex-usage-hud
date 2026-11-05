"""Focused gateway routing/configuration tests with an isolated CODEX_HOME."""

from concurrent.futures import ThreadPoolExecutor
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
from threading import Event, Thread
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import MagicMock, patch

import pytest

from codex_usage_hud import codex_provider_config as config
from codex_usage_hud import provider_gateway as gateway
from codex_usage_hud.provider_proxy import ProviderProxyRelay, DetachedProviderProxyRelay
from codex_usage_hud.provider_proxy import _decode_gateway_body
from codex_usage_hud.provider_registry import discover_provider_registry
from codex_usage_hud.core.parser import JsonlSessionParser
from codex_usage_hud.config import UserConfig

A = "10000000-0000-4000-8000-000000000001"
B = "10000000-0000-4000-8000-000000000002"
C = "10000000-0000-4000-8000-000000000003"


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('''model_provider = "alpha"
model = "native-model"
[model_providers.alpha]
name = "Alpha"
base_url = "https://alpha.example/v1"
wire_api = "responses"
experimental_bearer_token = "alpha-test-only"
[model_providers.beta]
name = "Beta"
base_url = "https://beta.example/api/v2"
wire_api = "responses"
experimental_bearer_token = "beta-test-only"
''', encoding="utf-8")
    monkeypatch.setattr(config, "default_codex_config_path", lambda: path)
    monkeypatch.setattr(config, "_user_environment_value", lambda _name: "")
    return path


@pytest.fixture
def active(home):
    relay = ProviderProxyRelay()
    original = home.read_bytes()
    gateway.activate_gateway(home, relay)
    try:
        yield home, relay, original
    finally:
        relay.close()


def headers(thread_id):
    return {"x-codex-turn-metadata": json.dumps({"thread_id": thread_id})}


def state_thread(path, thread_id, provider, created_at=1):
    with sqlite3.connect(path.parent / "state_5.sqlite") as db:
        db.execute("CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY, model_provider TEXT, created_at INTEGER)")
        db.execute("INSERT INTO threads VALUES (?, ?, ?)", (thread_id, provider, created_at))


def test_takeover_switch_is_internal_and_exit_restores_direct_identity(active):
    path, relay, original = active
    info = gateway.gateway_info(path)
    parsed = config._parse_toml_mapping(path.read_text())
    assert parsed["model_provider"] == "codex_hud"
    assert all(item["base_url"] == info["endpoint"] for item in parsed["model_providers"].values())
    assert config.read_provider_definitions(path)["alpha"].base_url == "https://alpha.example/v1"
    assert path.with_name(path.name + ".hud-gateway-backup").read_bytes() == original
    config_before = path.read_bytes()
    first = gateway.resolve_route(path, headers(A), generation=True)
    assert first["provider"] == "alpha"
    result = config.set_default_codex_provider("beta", config_path=path)
    assert result["gateway"] is True
    assert path.read_bytes() == config_before
    assert gateway.resolve_route(path, headers(A), generation=True)["provider"] == "alpha"
    assert gateway.resolve_route(path, headers(B), generation=True)["provider"] == "beta"
    gateway.activate_gateway(path, relay)
    assert gateway.gateway_info(path)["default_provider"] == "beta"
    assert path.with_name(path.name + ".hud-gateway-backup").read_bytes() == original
    with pytest.raises(ValueError, match="退出"):
        gateway.validate_exit("", config_path=path)
    result = gateway.prepare_exit("beta", config_path=path)
    gateway.validate_exit(result["exitToken"], config_path=path)
    assert gateway.gateway_info(path) == {}
    direct = config._parse_toml_mapping(path.read_text())
    assert direct["model_provider"] == "beta"
    assert direct["model_providers"]["alpha"]["base_url"] == "https://alpha.example/v1"
    assert direct["model_providers"]["codex_hud"]["base_url"] == "https://beta.example/api/v2"
    assert "openai_base_url" not in direct
    path.write_text(path.read_text() + "\n# concurrent edit\n", encoding="utf-8")
    with pytest.raises(ValueError, match="变化"):
        gateway.validate_exit(result["exitToken"], config_path=path)
    gateway.activate_gateway(path, relay)
    assert gateway.gateway_info(path)["default_provider"] == "beta"


def test_old_sessions_use_original_and_copy_uses_target_without_rewriting_source(active):
    path, _, _ = active
    state_thread(path, A, "alpha")
    gateway.select_provider("beta", config_path=path)
    assert gateway.resolve_route(path, headers(A), generation=True)["provider"] == "alpha"
    with pytest.raises(ValueError, match="复制"):
        gateway.select_provider("beta", config_path=path, thread_id=A)
    created = gateway.gateway_info(path)["activated_at"] + 1
    state_thread(path, B, "alpha", created)
    gateway.select_provider("beta", config_path=path, thread_id=B, copied_from=A)
    route = gateway.resolve_route(path, headers(B), generation=True)
    assert route["provider"] == "beta" and route["foreign_history"] is True
    assert gateway.session_provider(B, "alpha", config_path=path) == "beta"
    parser = JsonlSessionParser()
    assert parser.session_model_provider([{"type": "session_meta", "payload": {"id": B, "model_provider": "alpha"}}]) == "beta"
    with sqlite3.connect(path.parent / "state_5.sqlite") as db:
        assert db.execute("SELECT model_provider FROM threads WHERE id = ?", (A,)).fetchone()[0] == "alpha"


def test_concurrent_first_requests_and_later_default_change_keep_one_binding(active):
    path, _, _ = active
    with ThreadPoolExecutor(max_workers=6) as pool:
        routes = list(pool.map(lambda _: gateway.resolve_route(path, headers(A), generation=True), range(12)))
    assert {route["provider"] for route in routes} == {"alpha"}
    gateway.select_provider("beta", config_path=path)
    assert gateway.resolve_route(path, headers(A), generation=True)["provider"] == "alpha"


def test_missing_conflicting_ids_credentials_and_deleted_provider_do_not_fallback(active, monkeypatch):
    path, _, _ = active
    for request_headers in ({}, {**headers(A), "session_id": B}):
        with pytest.raises(ValueError, match="会话"):
            gateway.resolve_route(path, request_headers, generation=True)
    gateway.resolve_route(path, headers(A), generation=True)
    original = path.read_text()
    path.write_text(original.replace('experimental_bearer_token = "beta-test-only"', ""), encoding="utf-8")
    with pytest.raises(ValueError, match="凭据"):
        gateway.select_provider("beta", config_path=path)
    assert gateway.gateway_info(path)["default_provider"] == "alpha"
    with pytest.raises(ValueError, match="凭据"):
        gateway.prepare_exit("beta", config_path=path)
    assert gateway.gateway_info(path)
    path.write_text(original, encoding="utf-8")
    changed, _ = config._remove_provider_tables(original, "alpha")
    path.write_text(changed, encoding="utf-8")
    with pytest.raises(ValueError, match="已删除"):
        gateway.resolve_route(path, headers(A), generation=True)


def test_failed_takeover_and_exit_leave_configuration_untouched(home, monkeypatch):
    relay = ProviderProxyRelay()
    original = home.read_bytes()
    real_write = config._write_text_atomically
    monkeypatch.setattr(config, "_write_text_atomically", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("readonly")))
    try:
        with pytest.raises(OSError):
            gateway.activate_gateway(home, relay)
        assert home.read_bytes() == original
        monkeypatch.setattr(config, "_write_text_atomically", real_write)
        gateway.activate_gateway(home, relay)
        active_text = home.read_bytes()
        monkeypatch.setattr(config, "_write_text_atomically", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("readonly")))
        with pytest.raises(OSError):
            gateway.prepare_exit("beta", config_path=home)
        assert home.read_bytes() == active_text
    finally:
        monkeypatch.setattr(config, "_write_text_atomically", real_write)
        relay.close()


def test_registry_hides_native_gateway_but_keeps_real_default_and_credentials(active):
    path, _, _ = active
    gateway.select_provider("beta", config_path=path)
    registry = discover_provider_registry(user_config=UserConfig.defaults(), config_path=path, include_history=False)
    assert registry.app_provider == "beta"
    assert "codex_hud" not in registry.entries
    assert registry.entries["beta"].base_url == "https://beta.example/api/v2"
    with pytest.raises(ValueError, match="默认"):
        config.delete_provider_config("beta", config_path=path)


def test_copy_scrubs_only_source_reasoning_seals_and_rejects_opaque_context(active):
    path, _, _ = active
    gateway.resolve_route(path, headers(A), generation=True)
    gateway.select_provider("beta", config_path=path, thread_id=B, copied_from=A)
    route = gateway.resolve_route(path, headers(B), generation=True)
    text = {"type": "message", "role": "user", "content": "continue"}
    source = {"type": "reasoning", "summary": [], "encrypted_content": "source-sealed"}
    body = json.dumps({"input": [source, text]}).encode()
    assert json.loads(gateway.prepare_request_body(path, route, body))["input"] == [text]
    own = {**source, "encrypted_content": "target-sealed"}
    body = json.dumps({"input": [source, own, text]}).encode()
    assert json.loads(gateway.prepare_request_body(path, route, body))["input"] == [own, text]
    gateway.select_provider("beta", config_path=path, thread_id=C, copied_from=A)
    route = gateway.resolve_route(path, headers(C), generation=True)
    body = json.dumps({"input": [{"type": "compaction", "encrypted_content": "opaque"}, text]}).encode()
    with pytest.raises(ValueError, match="加密压缩"):
        gateway.prepare_request_body(path, route, body)


def test_gateway_streams_replaces_credentials_and_preserves_status(home):
    calls, started, release = [], Event(), Event()

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append((self.path, dict(self.headers), body))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: first\n\n")
            self.wfile.flush()
            started.set()
            release.wait(3)
            self.wfile.write(b"data: last\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    home.write_text(home.read_text().replace("https://alpha.example/v1", f"http://127.0.0.1:{server.server_port}/api/custom"), encoding="utf-8")
    relay = ProviderProxyRelay()
    client = None
    try:
        gateway.activate_gateway(home, relay)
        endpoint = urlsplit(gateway.gateway_info(home)["endpoint"])
        client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
        client.request("POST", endpoint.path + "/responses?custom=1", b'{"input":[]}',
                       {**headers(A), "Authorization": "Bearer caller", "X-Api-Key": "caller", "Cookie": "secret", "OpenAI-Project": "foreign"})
        response = client.getresponse()
        assert started.wait(1)
        assert response.read(len(b"data: first\n\n")) == b"data: first\n\n"
        assert not release.is_set(), "first SSE frame must reach the client before completion"
        gateway.select_provider("beta", config_path=home)
        assert calls[0][0] == "/api/custom/responses?custom=1"
        sent = {k.lower(): v for k, v in calls[0][1].items()}
        assert sent["authorization"] == "Bearer alpha-test-only"
        assert not {"x-api-key", "cookie", "openai-project"} & sent.keys()
        release.set()
        assert response.read() == b"data: last\n\n"
    finally:
        release.set()
        if client:
            client.close()
        relay.close()
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_external_config_manager_is_not_reported_as_active_gateway(active):
    path, relay, _ = active
    path.write_text(path.read_text().replace('model_provider = "codex_hud"', 'model_provider = "magpie"', 1), encoding="utf-8")
    assert gateway.gateway_info(path) == {}
    with pytest.raises(ValueError, match="magpie"):
        gateway.activate_gateway(path, relay)


@pytest.mark.parametrize("encoding", ["gzip", "zstd"])
def test_gateway_decodes_codex_requests_before_model_rewrite(encoding):
    import gzip
    import zstandard

    body = b'{"model":"native","input":[]}'
    compressed = gzip.compress(body) if encoding == "gzip" else zstandard.ZstdCompressor().compress(body)
    assert _decode_gateway_body(compressed, encoding) == body
    with pytest.raises(ValueError, match="解压"):
        _decode_gateway_body(b"invalid", encoding)


def test_subagent_inherits_parent_binding_after_default_switch(active):
    path, _, _ = active
    gateway.resolve_route(path, headers(A), generation=True)
    gateway.select_provider("beta", config_path=path)
    state_thread(path, B, "codex_hud", gateway.gateway_info(path)["activated_at"] + 1)
    with sqlite3.connect(path.parent / "state_5.sqlite") as db:
        db.execute("ALTER TABLE threads ADD COLUMN source TEXT")
        db.execute("UPDATE threads SET source = ? WHERE id = ?", (json.dumps({"subAgent":{"thread_spawn":{"parent_thread_id":A}}}), B))
    assert gateway.resolve_route(path, headers(B), generation=True)["provider"] == "alpha"


def test_clone_key_keeps_native_gateway_and_selects_new_upstream(active, monkeypatch):
    path, relay, _ = active
    environment = {}
    monkeypatch.setattr(config, "_user_environment_value", lambda key: environment.get(key, ""))
    monkeypatch.setattr(config, "_set_user_environment_value", environment.__setitem__)
    monkeypatch.setattr(config, "provider_proxy_relay", relay)
    result = config.clone_provider_with_bearer_key("alpha", "new-test-only", config_path=path)
    assert config._parse_toml_mapping(path.read_text(encoding="utf-8"))["model_provider"] == "codex_hud"
    route = gateway.resolve_route(path, headers(C), generation=True)
    assert route["provider"] == result["newProviderId"]
    assert route["base_url"] == "https://alpha.example/v1"
    assert route["headers"]["Authorization"] == "Bearer new-test-only"


def test_edit_provider_keeps_gateway_and_hot_reads_new_url_and_model(active, monkeypatch):
    path, relay, _ = active
    monkeypatch.setattr(config, "provider_proxy_relay", relay)
    config.save_provider_configs({"provider_id":"alpha", "base_url":"https://alpha.example/v3",
                                  "desktop_model":"custom-model", "desktop_models":["custom-model"]}, config_path=path)
    parsed = config._parse_toml_mapping(path.read_text(encoding="utf-8"))
    assert parsed["model_provider"] == "codex_hud"
    assert parsed["model_providers"]["alpha"]["base_url"] == gateway.gateway_info(path)["endpoint"]
    route = gateway.resolve_route(path, headers(A), generation=True)
    assert route["base_url"] == "https://alpha.example/v3"
    assert route["model"] == "custom-model"


def test_official_account_refresh_and_exit_keep_native_login(home):
    auth = home.with_name("auth.json")
    auth.write_text(json.dumps({"auth_mode":"chatgpt", "tokens":{"access_token":"official-test-only",
                                                              "refresh_token":"refresh-test-only", "account_id":"account-test"}}), encoding="utf-8")
    relay = ProviderProxyRelay()
    try:
        gateway.activate_gateway(home, relay)
        native = config._parse_toml_mapping(home.read_text(encoding="utf-8"))["model_providers"]["codex_hud"]
        assert native["requires_openai_auth"] is True
        assert "experimental_bearer_token" not in native
        gateway.select_provider("openai", config_path=home)
        route = gateway.resolve_route(home, headers(A), generation=True)
        assert route["base_url"] == "https://chatgpt.com/backend-api/codex"
        assert route["headers"]["Authorization"] == "Bearer official-test-only"
        assert route["headers"]["ChatGPT-Account-Id"] == "account-test"
        result = gateway.prepare_exit("openai", config_path=home)
        gateway.validate_exit(result["exitToken"], config_path=home)
        direct = config._parse_toml_mapping(home.read_text(encoding="utf-8"))
        assert direct["model_provider"] == "openai"
        assert direct["model_providers"]["codex_hud"]["base_url"] == "https://chatgpt.com/backend-api/codex"
    finally:
        relay.close()


def test_renderer_commands_bind_prepare_ack_and_authorize_exit(active):
    from codex_usage_hud.runtime_commands import _handle_renderer_settings_command

    path, _, _ = active
    context = SimpleNamespace(user_config=UserConfig.defaults())
    restart, exit_requested = MagicMock(), MagicMock()
    gateway.resolve_route(path, headers(A), generation=True)
    bound = _handle_renderer_settings_command({"action":"providerBindSession", "provider":"beta",
                                               "sessionId":B, "copiedFrom":A, "requestId":"bind-1"}, context, restart, exit_requested)
    assert bound["kind"] == "" and bound["requestId"] == "bind-1"
    assert bound["providerGateway"]["providerId"] == "beta"
    prepared = _handle_renderer_settings_command({"action":"providerPrepareExit", "provider":"beta",
                                                 "requestId":"exit-1"}, context, restart, exit_requested)
    assert prepared["kind"] == ""
    token = prepared["providerGateway"]["exitToken"]
    exit_requested.set.assert_not_called()
    result = _handle_renderer_settings_command({"action":"exit", "exitToken":token}, context, restart, exit_requested)
    assert result["kind"] == ""
    exit_requested.set.assert_called_once()
    with pytest.raises(ValueError, match="未接管"):
        gateway.select_provider("alpha", config_path=path, thread_id=C)
    assert config._parse_toml_mapping(path.read_text(encoding="utf-8"))["model_provider"] == "beta"


def test_detached_takeover_uses_separate_stable_helper_and_close_keeps_worker(home):
    from codex_usage_hud import provider_proxy_service

    relay = DetachedProviderProxyRelay(state_path=home.with_name("legacy-state.json"))
    with patch.object(provider_proxy_service, "ProviderProxyServiceClient") as factory:
        worker = factory.return_value
        worker.route.return_value = "http://127.0.0.1:57324/test-local-token/v1"
        relay.activate(home)
        assert factory.call_args.args == (57324,)
        assert factory.call_args.kwargs["state_path"].name == "gateway-legacy-state.json"
        metadata = worker.route.call_args.args[0]
        assert metadata["gateway_config"] == str(home.resolve())
        assert "headers" not in metadata and "Authorization" not in json.dumps(metadata)
        assert relay._service is None, "takeover must not restart the legacy helper"
        relay.close()
        worker.close.assert_called_once()
        worker.process.terminate.assert_not_called()
