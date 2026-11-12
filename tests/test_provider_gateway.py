"""Focused gateway routing/configuration tests with an isolated CODEX_HOME."""

from concurrent.futures import ThreadPoolExecutor
import http.client
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
from threading import Event, Thread
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch

import pytest

from codex_usage_hud import codex_provider_config as config
from codex_usage_hud import provider_gateway as gateway
from codex_usage_hud.provider_proxy import ProviderProxyRelay, DetachedProviderProxyRelay
from codex_usage_hud.provider_proxy import _decode_gateway_body
from codex_usage_hud.provider_registry import discover_provider_registry
from codex_usage_hud.provider_gateway_auth import GATEWAY_AUTH_HEADER
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


def test_direct_chat_created_between_takeovers_keeps_native_provider(active):
    path, relay, _ = active
    created = gateway.gateway_info(path)["activated_at"] + 1
    gateway.prepare_exit("beta", config_path=path)
    state_thread(path, B, "beta", created)
    gateway.activate_gateway(path, relay)
    gateway.select_provider("alpha", config_path=path)
    with pytest.raises(ValueError, match="原供应商"):
        gateway.select_provider("alpha", config_path=path, thread_id=B)
    route = gateway.resolve_route(path, headers(B), generation=True)
    assert route["provider"] == "beta"
    assert gateway.session_provider(B, config_path=path) == "beta"


def test_recent_native_provider_is_not_replaced_when_deleted(active):
    path, _, _ = active
    state_thread(path, B, "missing", gateway.gateway_info(path)["activated_at"] + 1)
    with pytest.raises(ValueError, match="已删除"):
        gateway.resolve_route(path, headers(B), generation=True)
    assert gateway.session_provider(B, config_path=path) == ""


def test_subagent_of_cross_provider_copy_inherits_real_binding(active):
    path, _, _ = active
    state_thread(path, A, "alpha")
    created = gateway.gateway_info(path)["activated_at"] + 1
    state_thread(path, B, "alpha", created)
    gateway.select_provider("beta", config_path=path, thread_id=B, copied_from=A)
    state_thread(path, C, "alpha", created)
    with sqlite3.connect(path.parent / "state_5.sqlite") as db:
        db.execute("ALTER TABLE threads ADD COLUMN source TEXT")
        db.execute("UPDATE threads SET source = ? WHERE id = ?", (json.dumps({"subAgent":{"thread_spawn":{"parent_thread_id":B}}}), C))
    assert gateway.resolve_route(path, headers(C), generation=True)["provider"] == "beta"


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


def test_spooled_copy_preserves_seal_rules_and_transaction_rollback(active):
    from codex_usage_hud.request_body import BodyFiles

    path, _, _ = active
    gateway.resolve_route(path, headers(A), generation=True)
    gateway.select_provider("beta", config_path=path, thread_id=B, copied_from=A)
    route = gateway.resolve_route(path, headers(B), generation=True)
    text = {"type": "message", "role": "user", "content": "完整内容" * 10000}
    source = {"type": "reasoning", "summary": [], "encrypted_content": "source-sealed"}
    files = BodyFiles()
    def body(items):
        value = files.new()
        value.write(json.dumps({"input": items}).encode())
        return value.rewind()
    try:
        assert gateway.prepare_request_body(path, route, body([source, text])).load()['input'] == [text]
        own = {**source, 'encrypted_content': 'target-sealed'}
        assert gateway.prepare_request_body(path, route, body([source, own, text])).load()['input'] == [own, text]
        gateway.select_provider('beta', config_path=path, thread_id=C, copied_from=A)
        fresh = gateway.resolve_route(path, headers(C), generation=True)
        with pytest.raises(ValueError, match='加密压缩'):
            gateway.prepare_request_body(path, fresh, body([source, {'type': 'compaction', 'encrypted_content': 'opaque'}]))
        with gateway._state_db(path) as db:
            assert db.execute('SELECT prepared FROM copies WHERE id = ?', (C,)).fetchone()[0] == 0
            assert not db.execute('SELECT 1 FROM foreign_seals WHERE id = ?', (C,)).fetchone()
    finally:
        files.close()


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
    home.write_text(home.read_text().replace("https://alpha.example/v1", f"http://127.0.0.1:{server.server_port}/api/custom?base=keep&api-version=base")
                    .replace('name = "Alpha"', 'name = "Alpha"\nquery_params = { "api-version" = "target", "tenant" = "space & slash/" }'), encoding="utf-8")
    relay = ProviderProxyRelay()
    client = None
    try:
        gateway.activate_gateway(home, relay)
        endpoint = urlsplit(gateway.gateway_info(home)["endpoint"])
        client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
        client.request("POST", endpoint.path + "/responses?custom=1&api-version=stale", b'{"input":[]}',
                       {**headers(A), GATEWAY_AUTH_HEADER: gateway.gateway_info(home)["token"], "Authorization": "Bearer caller", "X-Api-Key": "caller", "Cookie": "secret", "OpenAI-Project": "foreign"})
        response = client.getresponse()
        assert started.wait(1)
        assert response.read(len(b"data: first\n\n")) == b"data: first\n\n"
        assert not release.is_set(), "first SSE frame must reach the client before completion"
        gateway.select_provider("beta", config_path=home)
        assert urlsplit(calls[0][0]).path == "/api/custom/responses"
        assert parse_qs(urlsplit(calls[0][0]).query) == {
            "base":["keep"], "api-version":["target"], "tenant":["space & slash/"],
        }
        sent = {k.lower(): v for k, v in calls[0][1].items()}
        assert sent["authorization"] == "Bearer alpha-test-only"
        assert not {"x-api-key", "cookie", "openai-project", GATEWAY_AUTH_HEADER.lower()} & sent.keys()
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
    assert parsed["model_providers"]["alpha"]["http_headers"][GATEWAY_AUTH_HEADER] == gateway.gateway_info(path)["token"]
    route = gateway.resolve_route(path, headers(A), generation=True)
    assert route["base_url"] == "https://alpha.example/v3"
    assert route["model"] == "custom-model"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
@pytest.mark.parametrize("advanced", [False, True])
def test_save_rejects_gateway_as_upstream_without_changing_config(active, monkeypatch, host, advanced):
    path, relay, _ = active
    monkeypatch.setattr(config, "provider_proxy_relay", relay)
    endpoint = urlsplit(gateway.gateway_info(path)["endpoint"])
    url = f"http://{host}:{endpoint.port}/different/path"
    update = {"provider_id": "alpha", "base_url": url}
    if advanced:
        update["base_url"] = "https://alpha.example/v1"
        update["section_text"] = f'[model_providers.alpha]\nbase_url = "{url}"\n'
    before = path.read_bytes()
    with pytest.raises(ValueError, match="真实上游"):
        config.save_provider_configs(update, config_path=path)
    assert path.read_bytes() == before
    assert gateway.resolve_route(path, headers(A), generation=True)["base_url"] == "https://alpha.example/v1"


def test_reactivation_rejects_corrupt_nondefault_upstream_without_writing(active):
    path, relay, _ = active
    endpoint = gateway.gateway_info(path)["endpoint"]
    original = path.read_text(encoding="utf-8")
    section = config._section_range(original, "beta")
    metadata = config.proxy_metadata(section[2])
    metadata["base_url"] = endpoint
    from codex_usage_hud.provider_proxy import with_proxy_metadata

    path.write_text(config._replace_section_body(original, "beta", with_proxy_metadata(section[2], metadata, "\n")), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="真实上游"):
        gateway.activate_gateway(path, relay)
    assert path.read_bytes() == before


def test_save_allows_independent_local_upstream(active, monkeypatch):
    path, relay, _ = active
    monkeypatch.setattr(config, "provider_proxy_relay", relay)
    endpoint = urlsplit(gateway.gateway_info(path)["endpoint"])
    port = 18080 if endpoint.port != 18080 else 18081
    url = f"http://127.0.0.1:{port}/v1"
    config.save_provider_configs({"provider_id": "alpha", "base_url": url}, config_path=path)
    assert gateway.resolve_route(path, headers(A), generation=True)["base_url"] == url


@pytest.mark.parametrize("style", ["inline", "multiline", "table", "dotted"])
def test_gateway_auth_headers_preserve_custom_headers_and_are_removed_on_exit(home, style):
    source = home.read_text(encoding="utf-8")
    extra = {
        "inline": 'http_headers = { "X-Custom" = "keep" }\n',
        "multiline": 'http_headers = { "X-Custom" = """\\\nkeep""" }\n',
        "dotted": 'http_headers."X-Custom" = "keep"\n',
        "table": '',
    }[style]
    source = source.replace('name = "Alpha"\n', 'name = "Alpha"\n' + extra)
    if style == "table":
        source += '\n[model_providers.alpha.http_headers]\n"X-Custom" = "keep"\n'
    home.write_text(source, encoding="utf-8")
    relay = ProviderProxyRelay()
    try:
        gateway.activate_gateway(home, relay)
        info = gateway.gateway_info(home)
        assert urlsplit(info["endpoint"]).path == "/codex_hud/v1"
        parsed = config._parse_toml_mapping(home.read_text(encoding="utf-8"))
        for entry in parsed["model_providers"].values():
            assert entry["http_headers"][GATEWAY_AUTH_HEADER] == info["token"]
        assert parsed["model_providers"]["alpha"]["http_headers"]["X-Custom"] == "keep"
        route = gateway.resolve_route(home, headers(A), generation=True)
        assert route["headers"]["X-Custom"] == "keep"
        assert GATEWAY_AUTH_HEADER not in route["headers"]
        active_text = home.read_bytes()
        gateway.activate_gateway(home, relay)
        assert home.read_bytes() == active_text
        gateway.prepare_exit("alpha", config_path=home)
        parsed = config._parse_toml_mapping(home.read_text(encoding="utf-8"))
        assert parsed["model_providers"]["alpha"]["http_headers"] == {"X-Custom": "keep"}
        assert parsed["model_providers"]["codex_hud"]["http_headers"] == {"X-Custom": "keep"}
        assert all(GATEWAY_AUTH_HEADER not in entry.get("http_headers", {}) for entry in parsed["model_providers"].values())
    finally:
        relay.close()


@pytest.mark.parametrize("proxy_enabled,model", [(True, ""), (False, "private-model"), (True, "private-model")])
def test_exit_preserves_independent_proxy_and_model_relay(home, monkeypatch, proxy_enabled, model):
    from codex_usage_hud.provider_proxy import with_proxy_metadata

    original = home.read_text(encoding="utf-8")
    body = config._section_range(original, "alpha")[2]
    if model:
        body = config._with_hud_comment(body, config.DESKTOP_MODEL_MARKER, model, "\n")
    body = with_proxy_metadata(body, {"base_url":"https://alpha.example/v1", "enabled":proxy_enabled, "port":7897}, "\n")
    home.write_text(config._replace_section_body(original, "alpha", body), encoding="utf-8")
    gateway_relay, ordinary_relay = ProviderProxyRelay(), ProviderProxyRelay()
    monkeypatch.setattr(config, "provider_proxy_relay", ordinary_relay)
    try:
        gateway.activate_gateway(home, gateway_relay)
        gateway.prepare_exit("alpha", config_path=home)
        parsed = config._parse_toml_mapping(home.read_text(encoding="utf-8"))
        alpha = parsed["model_providers"]["alpha"]
        assert alpha["base_url"] == parsed["model_providers"]["codex_hud"]["base_url"]
        endpoint = urlsplit(alpha["base_url"])
        assert endpoint.port == ordinary_relay.server.server_port
        assert endpoint.port != gateway_relay.server.server_port
        route = ordinary_relay.routes[endpoint.path.split("/")[1]]
        assert route["enabled"] is proxy_enabled
        assert route["model"] == model
        assert route["port"] == 7897
        assert config.read_provider_definitions(home)["alpha"].base_url == "https://alpha.example/v1"
        assert not gateway.gateway_info(home)
    finally:
        ordinary_relay.close()
        gateway_relay.close()


@pytest.mark.parametrize("quoted", [False, True])
def test_exit_copies_full_provider_tree_and_reactivation_clears_stale_compatibility_tables(home, quoted):
    name = '"alpha"' if quoted else 'alpha'
    text = home.read_text(encoding="utf-8") + f'''
[model_providers.{name}.http_headers]
"X-Tenant" = "required"
[model_providers.{name}.env_http_headers]
"X-Environment" = "TENANT_ENV"
[model_providers.{name}.query_params]
api-version = "2026-01-01"
[profiles.keep]
model_provider = "codex_hud"
'''
    home.write_text(text, encoding="utf-8")
    relay = ProviderProxyRelay()
    try:
        gateway.activate_gateway(home, relay)
        gateway.prepare_exit("alpha", config_path=home)
        direct = config._parse_toml_mapping(home.read_text(encoding="utf-8"))
        assert direct["model_providers"]["codex_hud"] == direct["model_providers"]["alpha"]
        assert direct["profiles"]["keep"]["model_provider"] == "codex_hud"
        gateway.activate_gateway(home, relay)
        active = config._parse_toml_mapping(home.read_text(encoding="utf-8"))
        native = active["model_providers"]["codex_hud"]
        assert set(native["http_headers"]) == {GATEWAY_AUTH_HEADER}
        assert "env_http_headers" not in native and "query_params" not in native
        assert active["profiles"]["keep"]["model_provider"] == "codex_hud"
        stable = home.read_bytes()
        gateway.activate_gateway(home, relay)
        assert home.read_bytes() == stable
    finally:
        relay.close()


def test_named_gateway_requires_auth_before_binding_and_legacy_url_still_works(home):
    calls = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    home.write_text(home.read_text().replace("https://alpha.example/v1", f"http://127.0.0.1:{server.server_port}/v1"), encoding="utf-8")
    relay = ProviderProxyRelay()
    try:
        gateway.activate_gateway(home, relay)
        info = gateway.gateway_info(home)
        endpoint = urlsplit(info["endpoint"])

        def request(path, auth):
            client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
            try:
                client.request("POST", path, b"{}", {**headers(A), **auth})
                response = client.getresponse()
                body = response.read()
                return response.status, body
            finally:
                client.close()

        for auth in ({}, {GATEWAY_AUTH_HEADER: "wrong"}, {"Authorization": "Bearer upstream-not-the-gateway"}):
            assert request(endpoint.path + "/responses", auth)[0] == 401
        assert not calls
        assert gateway.session_provider(A, config_path=home) == ""
        assert request(endpoint.path + "/responses", {GATEWAY_AUTH_HEADER: info["token"]}) == (200, b"ok")
        assert request(endpoint.path + "/responses", {"Authorization": "Bearer " + info["token"]}) == (200, b"ok")
        assert request(f'/{info["token"]}/v1/responses', {}) == (200, b"ok")
        assert all(GATEWAY_AUTH_HEADER.lower() not in {k.lower() for k in call} for call in calls)
        # Re-adopting a saved random URL migrates config without changing the
        # token, the durable session route, or compatibility with cached URLs.
        text = home.read_text().replace("/codex_hud/v1", f'/{info["token"]}/v1')
        home.write_text(text, encoding="utf-8")
        gateway.activate_gateway(home, relay)
        assert gateway.gateway_info(home)["endpoint"] == info["endpoint"]
        assert gateway.session_provider(A, config_path=home) == "alpha"
    finally:
        relay.close()
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_gateway_header_preserves_case_insensitive_provider_identity(home):
    text = home.read_text().replace('[model_providers.alpha]', '[model_providers.ALPHA]').replace('name = "Alpha"', 'name = "Alpha"\nhttp_headers = { "X-Custom" = "keep" }')
    home.write_text(text, encoding="utf-8")
    relay = ProviderProxyRelay()
    try:
        gateway.activate_gateway(home, relay)
        route = gateway.resolve_route(home, headers(A), generation=True)
        assert route["headers"]["X-Custom"] == "keep"
        assert GATEWAY_AUTH_HEADER not in route["headers"]
        gateway.prepare_exit("alpha", config_path=home)
        assert GATEWAY_AUTH_HEADER not in home.read_text()
    finally:
        relay.close()


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
        worker.route.return_value = "http://127.0.0.1:57324/codex_hud/v1"
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


@pytest.mark.parametrize("remove_source", [False, True])
def test_copy_rebuilds_custom_credentials_and_query_only_from_target(active, remove_source):
    path, relay, _ = active
    text = path.read_text(encoding="utf-8")
    text = text.replace('name = "Alpha"', 'name = "Alpha"\nquery_params = { source_key = "alpha-secret", client_version = "alpha-version" }\nenv_http_headers = { "X-Source-Secret" = "ALPHA_SECRET" }')
    text = text.replace('name = "Beta"', 'name = "Beta"\nquery_params = { target_key = "beta-secret", tenant = "target-tenant" }\nenv_http_headers = { "x-target-secret" = "BETA_SECRET" }')
    path.write_text(text, encoding="utf-8")
    gateway.resolve_route(path, headers(A), generation=True)
    gateway.select_provider("beta", config_path=path, thread_id=B, copied_from=A)
    if remove_source:
        gateway.select_provider("beta", config_path=path)
        config.delete_provider_config("alpha", config_path=path)
    body = b'{"model":"text-model","input":[]}'
    handler = object.__new__(relay._handler_type())
    handler.command, handler.path = "POST", "/codex_hud/v1/responses?source_key=alpha-secret&client_version=client&target_key=stale&tenant=source-tenant&unknown_secret=deleted-secret&limit=5"
    handler.rfile, handler.wfile = BytesIO(body), BytesIO()
    handler.headers = {**headers(B), GATEWAY_AUTH_HEADER: gateway.gateway_info(path)["token"],
                       "Content-Length": str(len(body)), "Authorization": "Bearer source-key",
                       "X-Source-Secret": "alpha-secret", "X-Deleted-Secret": "deleted-secret",
                       "X-TARGET-SECRET": "stale-target-secret", "OpenAI-Beta": "responses=experimental"}
    handler.send_response_only, handler.send_header, handler.end_headers = MagicMock(), MagicMock(), MagicMock()
    with patch.object(config, "_user_environment_value", lambda key: {"ALPHA_SECRET": "alpha-secret", "BETA_SECRET": "beta-secret"}.get(key, "")), patch("http.client.HTTPSConnection") as connection:
        response = connection.return_value.getresponse.return_value
        response.status, response.headers = 200, {}
        response.getheaders.return_value, response.getheader.return_value, response.read1.return_value = [], "", b""
        handler._forward()
    request = connection.return_value.request.call_args
    sent = {key.lower(): value for key, value in request.kwargs["headers"].items()}
    assert sent["authorization"] == "Bearer beta-test-only"
    assert sent["x-target-secret"] == "beta-secret"
    assert not {"x-source-secret", "x-deleted-secret", GATEWAY_AUTH_HEADER.lower()} & sent.keys()
    assert sent["openai-beta"] == "responses=experimental"
    assert sent["x-codex-turn-metadata"] == headers(B)["x-codex-turn-metadata"]
    params = parse_qs(urlsplit(request.args[1]).query)
    assert params == {"target_key": ["beta-secret"], "tenant": ["target-tenant"], "limit": ["5"],
                      **({"client_version": ["client"]} if remove_source else {})}


@pytest.mark.parametrize("parent_prepared", [False, True])
@pytest.mark.parametrize("existing_binding", [False, True])
def test_subagent_inherits_foreign_seals_without_scrubbing_target_history(active, parent_prepared, existing_binding):
    path, _, _ = active
    gateway.resolve_route(path, headers(A), generation=True)
    gateway.select_provider("beta", config_path=path, thread_id=B, copied_from=A)
    parent = gateway.resolve_route(path, headers(B), generation=True)
    source = {"type": "reasoning", "summary": [], "encrypted_content": "source-seal"}
    own = {**source, "encrypted_content": "target-seal"}
    text = {"role": "user", "content": "continue"}
    if parent_prepared:
        gateway.prepare_request_body(path, parent, json.dumps({"input": [source, text]}).encode())
    state_thread(path, C, "alpha", gateway.gateway_info(path)["activated_at"] + 1)
    with sqlite3.connect(path.parent / "state_5.sqlite") as db:
        db.execute("ALTER TABLE threads ADD COLUMN source TEXT")
        db.execute("UPDATE threads SET source = ? WHERE id = ?", (json.dumps({"subAgent": {"thread_spawn": {"parent_thread_id": B}}}), C))
    db.close()
    if existing_binding:
        with gateway._state_db(path) as db:
            db.execute("INSERT INTO sessions VALUES (?, ?)", (C, "beta"))
    child = gateway.resolve_route(path, headers(C), generation=True)
    assert child["provider"] == "beta" and child["foreign_history"] is True
    items = [source, own, text] if parent_prepared else [source, text]
    cleaned = json.loads(gateway.prepare_request_body(path, child, json.dumps({"input": items}).encode()))["input"]
    assert cleaned == ([own, text] if parent_prepared else [text])
    cleaned = json.loads(gateway.prepare_request_body(path, child, json.dumps({"input": [source, own, text]}).encode()))["input"]
    assert cleaned == [own, text]


def test_descendant_repairs_missing_foreign_state_through_an_unvisited_parent(active):
    path, _, _ = active
    gateway.resolve_route(path, headers(A), generation=True)
    gateway.select_provider("beta", config_path=path, thread_id=B, copied_from=A)
    source = {"type": "reasoning", "summary": [], "encrypted_content": "old-seal"}
    gateway.prepare_request_body(path, gateway.resolve_route(path, headers(B), generation=True),
                                 json.dumps({"input": [source]}).encode())
    child_id = "10000000-0000-4000-8000-000000000004"
    for thread_id in (C, child_id):
        state_thread(path, thread_id, "alpha", gateway.gateway_info(path)["activated_at"] + 1)
    with sqlite3.connect(path.parent / "state_5.sqlite") as db:
        db.execute("ALTER TABLE threads ADD COLUMN source TEXT")
        for thread_id, parent in ((C, B), (child_id, C)):
            db.execute("UPDATE threads SET source = ? WHERE id = ?", (json.dumps({"parent_thread_id": parent}), thread_id))
    db.close()
    with gateway._state_db(path) as db:
        db.executemany("INSERT INTO sessions VALUES (?, 'beta')", [(C,), (child_id,)])
    route = gateway.resolve_route(path, headers(child_id), generation=True)
    assert route["foreign_history"] is True
    assert json.loads(gateway.prepare_request_body(path, route, json.dumps({"input": [source]}).encode()))["input"] == []


@pytest.mark.parametrize("setting", [None, False, True])
def test_takeover_forces_http_for_legacy_identities_and_restores_transport_on_exit(home, monkeypatch, setting):
    original = home.read_text(encoding="utf-8")
    if setting is not None:
        original = original.replace('name = "Alpha"', f'name = "Alpha"\nsupports_websockets = {str(setting).lower()} # keep comment')
        home.write_text(original, encoding="utf-8")
    relay = ProviderProxyRelay()
    monkeypatch.setattr(config, "provider_proxy_relay", relay)
    try:
        gateway.activate_gateway(home, relay)
        assert all(entry["supports_websockets"] is False for entry in config._parse_toml_mapping(home.read_text())["model_providers"].values())
        active_text = home.read_bytes()
        gateway.activate_gateway(home, relay)
        assert home.read_bytes() == active_text
        gateway.prepare_exit("alpha", config_path=home)
        restored = config._parse_toml_mapping(home.read_text())["model_providers"]
        for provider in ("alpha", "codex_hud"):
            assert restored[provider].get("supports_websockets") is setting
            assert ("supports_websockets" in restored[provider]) == (setting is not None)
        assert "supports_websockets" not in restored["beta"]
        if setting is not None:
            assert "# keep comment" in home.read_text()
        gateway.activate_gateway(home, relay)
        assert config._parse_toml_mapping(home.read_text())["model_providers"]["alpha"]["supports_websockets"] is False
    finally:
        relay.close()


def test_provider_added_during_takeover_uses_http_then_restores_requested_websockets(active, monkeypatch):
    path, relay, _ = active
    monkeypatch.setattr(config, "provider_proxy_relay", relay)
    environment = {}
    monkeypatch.setattr(config, "_user_environment_value", lambda key: environment.get(key, ""))
    monkeypatch.setattr(config, "_set_user_environment_value", environment.__setitem__)
    config.save_provider_configs({"provider_id": "gamma", "is_new": True, "api_key": "gamma-test", "section_text": '''[model_providers.gamma]
name = "Gamma"
base_url = "https://gamma.example/v1"
experimental_bearer_token = "gamma-test"
env_key = "GAMMA_TEST_API_KEY"
supports_websockets = true
'''}, config_path=path)
    assert config._parse_toml_mapping(path.read_text())["model_providers"]["gamma"]["supports_websockets"] is False
    gateway.prepare_exit("gamma", config_path=path)
    assert config._parse_toml_mapping(path.read_text())["model_providers"]["gamma"]["supports_websockets"] is True
