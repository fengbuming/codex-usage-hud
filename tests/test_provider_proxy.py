from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import socket
import ssl
from threading import Event, Thread
from urllib.parse import urlsplit
from unittest.mock import MagicMock, patch

import pytest

from codex_usage_hud import codex_provider_config as config
from codex_usage_hud import codex_cli_launcher as cli
from codex_usage_hud import provider_proxy_service
from codex_usage_hud.provider_proxy import (
    DetachedProviderProxyRelay,
    ProviderProxyRelay,
    _rewrite_model,
    proxy_metadata,
    proxy_route_required,
)


@pytest.fixture
def relay():
    instance = ProviderProxyRelay()
    yield instance
    instance.close()


@pytest.fixture
def detached_relay(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    instance = DetachedProviderProxyRelay(port, state_path=tmp_path / "relay-state.json")
    service = None
    try:
        yield instance
    finally:
        service = instance._service
        instance.close()
        if service is not None and service.process is not None:
            service.process.terminate()
            service.process.wait(timeout=10)


def test_detached_relay_preserves_stream_and_endpoint_across_hud_close(detached_relay, upstream, tmp_path):
    server, calls, first_frame, release = upstream
    metadata = {"base_url": f"http://127.0.0.1:{server.server_port}/v1", "enabled": False}
    endpoint = detached_relay.route(metadata, tmp_path / "config.toml")
    service = detached_relay._service
    worker_pid = service._call("health")["pid"]
    target = urlsplit(endpoint)
    client = http.client.HTTPConnection(target.hostname, target.port, timeout=3)
    client.request("POST", target.path + "/responses", b'{"stream":true}')
    response = client.getresponse()
    assert first_frame.wait(1)
    assert response.read(len(b"data: first\n\n")) == b"data: first\n\n"
    detached_relay.close()
    replacement = DetachedProviderProxyRelay(detached_relay.port, state_path=detached_relay.state_path)
    try:
        assert replacement.route(metadata, tmp_path / "config.toml") == endpoint
        assert replacement._service._call("health")["pid"] == worker_pid
        assert replacement._service.process is None
        release.set()
        assert response.read() == b""
        assert response.status == 200
        assert len(calls) == 1
    finally:
        client.close()
        replacement.close()
        service.process.terminate()
        service.process.wait(timeout=10)


def test_detached_relay_pushes_source_evidence_and_rejects_control_without_token(detached_relay, upstream, tmp_path):
    server, _, _, release = upstream
    received = []
    evidence_ready = Event()

    def evidence(thread_id, feature_key):
        received.append((thread_id, feature_key))
        evidence_ready.set()

    detached_relay.add_source_evidence_listener(evidence)
    endpoint = urlsplit(detached_relay.route({"base_url": f"http://127.0.0.1:{server.server_port}/v1"}, tmp_path / "config.toml"))
    client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
    body = json.dumps({"instructions": "Memory Writing Agent: Phase 2 (Consolidation)"})
    client.request("POST", endpoint.path + "/responses", body, {
        "x-codex-turn-metadata": json.dumps({"thread_id": "01a1870b-7d0e-7c81-a578-6625e4a23103"}),
    })
    response = client.getresponse()
    assert evidence_ready.wait(2)
    assert received == [("01a1870b-7d0e-7c81-a578-6625e4a23103", "memory_consolidation")]
    release.set()
    response.read()
    client.close()
    client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
    client.request("POST", "/__codex_hud_proxy__/route", b'{}')
    assert client.getresponse().status == 403
    client.close()


def test_windows_proxy_helper_requests_breakaway_from_hud_job(monkeypatch, tmp_path):
    popen = MagicMock()
    monkeypatch.setattr(provider_proxy_service.os, "name", "nt")
    monkeypatch.setattr(provider_proxy_service.subprocess, "Popen", popen)

    provider_proxy_service.launch_helper(tmp_path / "relay-state.json", 57323)

    flags = popen.call_args.kwargs["creationflags"]
    assert flags & provider_proxy_service._CREATE_BREAKAWAY_FROM_JOB


@pytest.mark.parametrize(
    "metadata,required",
    [
        ({"enabled": False, "model": "", "models": []}, False),
        ({"enabled": True, "model": "", "models": []}, True),
        ({"enabled": False, "model": "custom-model", "models": []}, True),
        ({"enabled": False, "model": "", "models": ["custom-model"]}, True),
    ],
)
def test_proxy_route_required_only_for_proxy_or_model_rewrite(metadata, required):
    assert proxy_route_required(metadata) is required


def test_activate_migrates_direct_provider_but_warms_existing_route(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'model_provider = "demo"\n'
        "[model_providers.demo]\n"
        'name = "Demo"\n'
        'base_url = "http://127.0.0.1:57323/old-token/v1"\n'
        'env_key = "DEMO_KEY"\n'
        '# codex-hud-proxy: {"base_url":"https://example.test/v1",'
        '"enabled":false,"port":7897,"token":"old-token",'
        '"model":"","models":[]}\n',
        encoding="utf-8",
    )
    relay = ProviderProxyRelay()
    with patch.object(relay, "route", return_value="http://127.0.0.1:57323/old-token/v1") as route:
        relay.activate(path)

    text = path.read_text(encoding="utf-8")
    assert 'base_url = "https://example.test/v1"' in text
    assert 'base_url = "http://127.0.0.1:57323/old-token/v1"' not in text
    route.assert_called_once()


def test_direct_provider_save_switch_clone_and_activation_need_no_helper(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'model_provider = "demo"\n'
        '[model_providers.other]\nbase_url = "https://other.test/v1"\n'
        '[model_providers.demo]\nbase_url = "https://example.test/v1"\n'
        'env_key = "DEMO_KEY"\nrequest_timeout_ms = 321\n',
        encoding="utf-8",
    )
    relay = ProviderProxyRelay()
    with (
        patch.object(config, "provider_proxy_relay", relay),
        patch.object(config, "_user_environment_value", return_value="test-only"),
        patch.object(config, "_set_user_environment_value"),
        patch.object(relay, "route", side_effect=AssertionError("direct provider started a relay")) as route,
    ):
        update = {"provider_id": "demo", "base_url": "https://example.test/v1",
                  "env_key": "DEMO_KEY", "use_proxy": False, "proxy_port": 7897}
        config.save_provider_configs(update, config_path=path)
        committed = path.read_bytes()
        config.save_provider_configs(update, config_path=path)
        assert path.read_bytes() == committed
        config.set_default_codex_provider("other", config_path=path)
        config.set_default_codex_provider("demo", config_path=path)
        cloned = config.clone_provider_with_bearer_key("demo", "replacement", config_path=path)
        relay.activate(path)
        route.assert_not_called()

    parsed = config._parse_toml_mapping(path.read_text(encoding="utf-8"))
    assert parsed["model_provider"] == cloned["newProviderId"]
    for provider in ("demo", cloned["newProviderId"]):
        assert parsed["model_providers"][provider]["base_url"] == "https://example.test/v1"
        assert parsed["model_providers"][provider]["request_timeout_ms"] == 321
        assert not proxy_metadata(config.read_provider_definitions(path)[provider].section_text).get("token")


@pytest.mark.parametrize("operation", ["activate", "save", "switch"])
def test_direct_migration_finishes_when_another_provider_cannot_start_helper(tmp_path, operation):
    path = tmp_path / "config.toml"
    text = 'model_provider = "demo"\n'
    for provider, enabled in (("proxied", True), ("demo", False)):
        metadata = {"base_url": f"https://{provider}.test/v1", "enabled": enabled,
                    "port": 7897, "model": "", "models": [], "token": provider}
        text += (f'[model_providers.{provider}]\n'
                 f'base_url = "http://127.0.0.1:57323/{provider}/v1"\n'
                 f'experimental_bearer_token = "test-only"\n'
                 f'# codex-hud-proxy: {json.dumps(metadata)}\n')
    path.write_text(text, encoding="utf-8")
    relay = ProviderProxyRelay()
    with (
        patch.object(relay, "route", side_effect=RuntimeError("helper unavailable")),
        patch.object(config, "provider_proxy_relay", relay),
    ):
        if operation == "activate":
            with pytest.raises(RuntimeError, match="helper unavailable"):
                relay.activate(path)
        elif operation == "save":
            config.save_provider_configs({"provider_id": "demo", "base_url": "https://demo.test/v1",
                                          "use_proxy": False}, config_path=path)
        else:
            config.set_default_codex_provider("demo", config_path=path)
        parsed = config._parse_toml_mapping(path.read_text(encoding="utf-8"))
        assert parsed["model_providers"]["demo"]["base_url"] == "https://demo.test/v1"
        assert parsed["model_providers"]["proxied"]["base_url"] == "http://127.0.0.1:57323/proxied/v1"
    assert proxy_metadata(config.read_provider_definitions(path)["demo"].section_text)["token"] == "demo"


def test_migrated_default_sends_requests_after_detached_helper_is_stopped(detached_relay, upstream, tmp_path):
    server, calls, _, release = upstream
    release.set()
    path = tmp_path / "config.toml"
    metadata = {"base_url": f"http://127.0.0.1:{server.server_port}/v1", "enabled": False,
                "port": 7897, "model": "", "models": []}
    cached_url = detached_relay.route(metadata, path)
    service = detached_relay._service
    path.write_text(
        f'model_provider = "demo"\n[model_providers.demo]\nbase_url = "{cached_url}"\n'
        f'# codex-hud-proxy: {json.dumps(metadata)}\n', encoding="utf-8",
    )
    try:
        detached_relay.activate(path)
        direct_url = config._parse_toml_mapping(path.read_text())["model_providers"]["demo"]["base_url"]
        assert direct_url == metadata["base_url"]
        assert metadata["token"] in json.loads(detached_relay.state_path.read_text())["routes"]

        def send(url):
            target = urlsplit(url)
            client = http.client.HTTPConnection(target.hostname, target.port, timeout=3)
            try:
                client.request("POST", target.path + "/responses", b'{"model":"native"}',
                               {"Authorization": "Bearer test-only"})
                response = client.getresponse()
                assert response.status == 200
                assert b"data: first" in response.read()
            finally:
                client.close()

        send(cached_url)
        detached_relay.close()
        service.process.terminate()
        service.process.wait(timeout=10)
        send(direct_url)
        assert calls == [("/v1/responses", "Bearer test-only", b'{"model":"native"}')] * 2
    finally:
        detached_relay.close()
        if service.process.poll() is None:
            service.process.terminate()
            service.process.wait(timeout=10)


@pytest.mark.parametrize("required_by", ["proxy", "model"])
def test_disabling_relay_preserves_cached_route_and_does_not_create_new_token(relay, tmp_path, required_by):
    path = tmp_path / "config.toml"
    path.write_text('model_provider = "demo"\n[model_providers.demo]\n'
                    'base_url = "https://example.test/v1"\n'
                    'experimental_bearer_token = "test-only"\n', encoding="utf-8")
    with patch.object(config, "provider_proxy_relay", relay):
        update = {"provider_id": "demo", "base_url": "https://example.test/v1",
                  "use_proxy": required_by == "proxy", "desktop_model": "custom" if required_by == "model" else ""}
        config.save_provider_configs(update, config_path=path)
        prior = proxy_metadata(config.read_provider_definitions(path)["demo"].section_text)
        config.save_provider_configs({**update, "use_proxy": False, "desktop_model": "", "desktop_models": []}, config_path=path)
        direct = proxy_metadata(config.read_provider_definitions(path)["demo"].section_text)
        assert not direct.get("token")
        assert direct["previous_routes"][0]["token"] == prior["token"]
        assert relay.routes[prior["token"]]["enabled"] == prior["enabled"]
        assert relay.routes[prior["token"]]["model"] == prior["model"]
        parsed = config._parse_toml_mapping(path.read_text())
        assert parsed["model_providers"]["demo"]["base_url"] == "https://example.test/v1"


@pytest.fixture
def upstream():
    calls = []
    first_frame = Event()
    release = Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append((self.path, self.headers.get("Authorization"), body))
            if self.path.endswith("upstream-502"):
                error_body = b'{"error":{"message":"upstream overloaded","type":"server_error"}}'
                self.send_response(502, "Upstream Busy")
                self.send_header("Content-Type", "application/json")
                self.send_header("x-request-id", "req-upstream-123")
                self.send_header("Content-Length", str(len(error_body)))
                self.end_headers()
                self.wfile.write(error_body)
                return
            self.send_response(429 if self.path.endswith("error") else 200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            frame = b"data: first\n\n"
            self.wfile.write(f"{len(frame):x}\r\n".encode() + frame + b"\r\n")
            self.wfile.flush()
            first_frame.set()
            release.wait(3)
            self.wfile.write(b"0\r\n\r\n")

        def do_GET(self):
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
            self.wfile.write(b"\x81\x02hi")
            self.wfile.flush()
            incoming = self.connection.recv(4)
            self.connection.sendall(incoming)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, calls, first_frame, release
    release.set()
    server.shutdown()
    server.server_close()
    thread.join(2)


@pytest.mark.parametrize("enabled", [False, True])
def test_routing_and_streaming_preserve_body_auth_query_and_errors(relay, upstream, tmp_path, enabled):
    server, calls, first_frame, release = upstream
    # When enabled this server is an HTTP proxy receiving an absolute URI.
    base = "http://provider.invalid/v1" if enabled else f"http://127.0.0.1:{server.server_port}/v1"
    endpoint = urlsplit(relay.route({"base_url": base, "enabled": enabled,
                                    "port": server.server_port}, tmp_path / "config.toml"))
    client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
    client.request("POST", endpoint.path + "/responses?x=1", b'{"stream":true}',
                   {"Authorization": "Bearer test-only"})
    response = client.getresponse()
    assert first_frame.wait(1)
    assert response.read(len(b"data: first\n\n")) == b"data: first\n\n"
    # Read the first event while upstream is still waiting: no response buffering.
    assert not release.is_set()
    assert response.status == 200
    assert calls == [(base + "/responses?x=1" if enabled else "/v1/responses?x=1",
                      "Bearer test-only", b'{"stream":true}')]
    release.set()
    response.read()
    client.close()
    client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
    client.request("POST", endpoint.path + "/error", b"")
    assert client.getresponse().status == 429
    client.close()
    client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
    client.request("POST", endpoint.path + "/upstream-502", b"")
    response = client.getresponse()
    assert response.status == 502
    assert response.reason == "Upstream Busy"
    assert response.getheader("Content-Type") == "application/json"
    assert response.getheader("x-request-id") == "req-upstream-123"
    assert response.read() == b'{"error":{"message":"upstream overloaded","type":"server_error"}}'
    client.close()


def test_connection_failure_identifies_local_relay_and_preserves_exception(relay, tmp_path):
    endpoint = urlsplit(relay.route({
        "base_url": "http://127.0.0.1:1/v1",
        "enabled": False,
    }, tmp_path / "config.toml"))
    client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
    client.request("POST", endpoint.path + "/responses", b"{}")
    response = client.getresponse()
    body = response.read().decode("utf-8")
    client.close()

    assert response.status == 502
    assert response.getheader("Content-Type") == "text/plain; charset=utf-8"
    assert "Codex HUD provider relay did not receive an upstream HTTP response." in body
    assert "ConnectionRefusedError" in body


def test_relay_reports_known_source_signature_by_codex_thread_metadata(relay, tmp_path):
    received = []
    source_evidence = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append((self.headers.get("x-codex-turn-metadata"), body))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    thread_id = "30000000-0000-4000-8000-000000000001"
    listener = relay.add_source_evidence_listener(
        lambda observed_thread, feature: source_evidence.append(
            (observed_thread, feature)
        )
    )
    try:
        endpoint = urlsplit(
            relay.route(
                {"base_url": f"http://127.0.0.1:{server.server_port}/v1"},
                tmp_path / "config.toml",
            )
        )
        body = json.dumps(
            {
                "instructions": "Memory Writing Agent: Phase 2 (Consolidation)",
                "input": [{"role": "user", "content": "Private task text"}],
            }
        ).encode("utf-8")
        client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
        client.request(
            "POST",
            endpoint.path + "/responses",
            body,
            {"x-codex-turn-metadata": json.dumps({"thread_id": thread_id})},
        )
        response = client.getresponse()
        assert response.status == 200
        response.read()
        client.close()

        assert source_evidence == [(thread_id, "memory_consolidation")]
        assert received == [
            (
                json.dumps({"thread_id": thread_id}),
                body,
            )
        ]
    finally:
        relay.remove_source_evidence_listener(listener)
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_rewrite_model_changes_unknown_fallback_but_preserves_configured_models():
    assert _rewrite_model(
        b'{"model":"gpt-6.1-sol","stream":true}',
        "deepseek-v4.1-flash-uncensored",
        ["deepseek-v4.1-flash-uncensored", "deepseek-v4.1-flash"],
    ) == b'{"model":"deepseek-v4.1-flash-uncensored","stream":true}'
    assert _rewrite_model(
        b'{"model":"deepseek-v4.1-flash","stream":true}',
        "deepseek-v4.1-flash-uncensored",
        ["deepseek-v4.1-flash-uncensored", "deepseek-v4.1-flash"],
    ) == b'{"model":"deepseek-v4.1-flash","stream":true}'
    assert _rewrite_model(b"not-json", "deepseek-v4.1-flash-uncensored") == b"not-json"


def test_relay_rewrites_unknown_model_in_responses_request(relay, upstream, tmp_path):
    server, calls, _, release = upstream
    release.set()
    endpoint = urlsplit(relay.route({
        "base_url": f"http://127.0.0.1:{server.server_port}/v1",
        "enabled": False,
        "model": "deepseek-v4.1-flash-uncensored",
        "models": ["deepseek-v4.1-flash-uncensored", "deepseek-v4.1-flash"],
    }, tmp_path / "config.toml"))
    client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
    client.request("POST", endpoint.path + "/responses", b'{"model":"gpt-6.1-sol","stream":true}')
    response = client.getresponse()
    assert response.status == 200
    response.read()
    client.close()
    assert calls[-1][2] == b'{"model":"deepseek-v4.1-flash-uncensored","stream":true}'
def test_websocket_handshake_and_bidirectional_frames(relay, upstream, tmp_path):
    server = upstream[0]
    endpoint = urlsplit(relay.route({"base_url": f"http://127.0.0.1:{server.server_port}/v1",
                                    "enabled": False}, tmp_path / "config.toml"))
    with socket.create_connection((endpoint.hostname, endpoint.port), timeout=3) as client:
        client.sendall(f"GET {endpoint.path}/responses HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n".encode())
        received = bytearray()
        while b"\r\n\r\n" not in received:
            received.extend(client.recv(1))
        assert b"101" in received
        assert client.recv(4) == b"\x81\x02hi"
        client.sendall(b"\x81\x02ok")
        assert client.recv(4) == b"\x81\x02ok"


def test_https_connect_uses_upstream_hostname_and_verified_tls(relay, upstream, tmp_path):
    # Temporary CA and server certificate exercise real TLS through a CONNECT proxy.
    pytest.importorskip("cryptography")
    from datetime import datetime, timedelta, timezone
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "test.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    server, calls, _, release = upstream
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    tunnels = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_CONNECT(self):
            import select
            tunnels.append(self.path)
            with socket.create_connection(("127.0.0.1", server.server_port), timeout=3) as remote:
                self.send_response(200)
                self.end_headers()
                sockets = [remote, self.connection]
                while True:
                    readable = select.select(sockets, [], [], 3)[0]
                    if not readable:
                        return
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        (remote if source is self.connection else self.connection).sendall(data)

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    thread = Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    release.set()
    try:
        base = f"https://127.0.0.1:{server.server_port}/v1"
        endpoint = urlsplit(relay.route({"base_url": base, "enabled": True,
                                        "port": proxy.server_port}, tmp_path / "config.toml"))
        context = ssl.create_default_context(cafile=str(cert_path))
        with patch.object(http.client.ssl, "_create_default_https_context", return_value=context):
            client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
            client.request("POST", endpoint.path + "/responses", b"test", {"Authorization": "Bearer test-only"})
            response = client.getresponse()
            assert response.status == 200
            assert b"data: first" in response.read()
            client.close()
        assert tunnels == [f"127.0.0.1:{server.server_port}"]
        assert calls == [("/v1/responses", "Bearer test-only", b"test")]
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join(2)


def test_config_roundtrip_restart_clone_and_cli_share_one_setting(relay, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model_provider = "other"\n\n[model_providers.other]\nname = "Other"\nbase_url = "https://example.test/v1"\nenv_key = "OTHER_API_KEY"\n', encoding="utf-8")
    environment = {"OTHER_API_KEY": "test-only"}
    with patch.object(config, "provider_proxy_relay", relay), patch.object(
        config, "_user_environment_value", side_effect=environment.get
    ), patch.object(
        config, "_set_user_environment_value", side_effect=environment.__setitem__
    ):
        config.save_provider_configs({"provider_id": "other", "base_url": "https://example.test/v2",
                                     "env_key": "OTHER_API_KEY", "use_proxy": True,
                                     "proxy_port": 7898, "desktop_model": "deepseek-flash",
                                     "desktop_models": ["deepseek-flash", "deepseek-fast"]}, config_path=path)
        definition = config.read_provider_definitions(path)["other"]
        assert definition.base_url == "https://example.test/v2"
        assert definition.use_proxy and definition.proxy_port == 7898
        route = proxy_metadata(definition.section_text)
        assert route["model"] == "deepseek-flash"
        assert route["models"] == ["deepseek-flash", "deepseek-fast"]
        assert 'base_url = "https://example.test/v2"' in definition.section_text
        options = cli.discover_codex_cli_options(provider="other", codex_home=tmp_path)
        assert options["proxy"]["enabled"] is True
        assert options["proxy"]["port"] == 7898
        command = cli.build_codex_cli_command(provider="other", use_proxy=True, proxy_port=7898,
                                              provider_base_url=definition.base_url)
        assert "http://127.0.0.1:7898" in command
        assert "model_providers.other.base_url" in command
        assert "https://example.test/v2" in command
        token = proxy_metadata(definition.section_text)["token"]
        clone = config.clone_provider_with_bearer_key("other", "test-only-new", config_path=path)
        assert environment[clone["environmentKey"]] == "test-only-new"
        cloned = config.read_provider_definitions(path)[clone["newProviderId"]]
        assert cloned.use_proxy and cloned.proxy_port == 7898
        assert proxy_metadata(cloned.section_text)["token"] != token
        changed_clone = config.clone_provider_with_bearer_key(
            clone["newProviderId"], "test-only-third", config_path=path,
            use_proxy=False, proxy_port=7899
        )
        changed_definition = config.read_provider_definitions(path)[changed_clone["newProviderId"]]
        assert not changed_definition.use_proxy and changed_definition.proxy_port == 7899
        relay.close()
        assert 'base_url = "https://example.test/v2"' in path.read_text(encoding="utf-8")
        assert "http://127.0.0.1:" not in path.read_text(encoding="utf-8")
        relay.activate(path)
        assert "http://127.0.0.1:" in path.read_text(encoding="utf-8")
        config.save_provider_configs({"provider_id": "other", "base_url": definition.base_url,
                                     "env_key": "OTHER_API_KEY", "use_proxy": False,
                                     "proxy_port": 7898}, config_path=path)
        assert not config.read_provider_definitions(path)["other"].use_proxy


def test_failed_save_preserves_active_route_and_external_url_edits_survive_close(relay, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model_provider = "other"\n\n[model_providers.other]\nname = "Other"\nbase_url = "https://old.example/v1"\nenv_key = "OTHER_API_KEY"\n', encoding="utf-8")
    with patch.object(config, "provider_proxy_relay", relay), patch.object(config, "_user_environment_value", return_value="test-only"):
        update = {"provider_id": "other", "base_url": "https://old.example/v1",
                  "env_key": "OTHER_API_KEY", "use_proxy": True, "proxy_port": 7897}
        config.save_provider_configs(update, config_path=path)
        old_text = path.read_text(encoding="utf-8")
        old_token = proxy_metadata(old_text)["token"]
        with patch.object(config, "_write_text_atomically", side_effect=OSError("test write failed")):
            with pytest.raises(OSError):
                config.save_provider_configs({**update, "base_url": "https://new.example/v1",
                                              "proxy_port": 7899}, config_path=path)
        assert path.read_text(encoding="utf-8") == old_text
        assert relay.routes[old_token]["base_url"] == "https://old.example/v1"
        assert relay.routes[old_token]["port"] == 7897
        section = config._section_range(old_text, "other")[2]
        body = config._set_quoted_value(section, "base_url", "https://external.example/v1", "\n")
        path.write_text(config._replace_section_body(old_text, "other", body), encoding="utf-8")
        relay.close()
        definition = config.read_provider_definitions(path)["other"]
        assert definition.base_url == "https://external.example/v1"


@pytest.mark.parametrize("shell,clear", [("powershell", "Remove-Item Env:HTTP_PROXY"),
                                         ("cmd", 'set "ALL_PROXY="'), ("bash", "unset HTTP_PROXY")])
def test_cli_clears_inherited_proxy_when_disabled(shell, clear):
    command = cli.build_codex_cli_command(provider="other", use_proxy=False, shell=shell)
    assert clear in command
    assert "127.0.0.1" not in command


def test_saved_routes_survive_fresh_relay_restart_and_failed_save(tmp_path, upstream):
    server, calls, _, release = upstream
    release.set()
    path = tmp_path / "config.toml"
    base = f"http://127.0.0.1:{server.server_port}/v1"
    path.write_text(f'model_provider = "demo"\n[model_providers.demo]\n'
                    f'name = "Demo"\nbase_url = "{base}"\nenv_key = "DEMO_KEY"\n', encoding="utf-8")
    relay = ProviderProxyRelay()
    try:
        with patch.object(config, "provider_proxy_relay", relay), patch.object(
            config, "_user_environment_value", return_value="test-only"
        ):
            update = {"provider_id": "demo", "base_url": base, "env_key": "DEMO_KEY",
                      "use_proxy": False, "proxy_port": 7897, "desktop_model": "custom-model"}
            def endpoint():
                return config._parse_toml_mapping(path.read_text())["model_providers"]["demo"]["base_url"]
            config.save_provider_configs(update, config_path=path)
            original = endpoint()
            config.save_provider_configs(update, config_path=path)
            assert endpoint() == original
            config.save_provider_configs({**update, "base_url": base + "/new"}, config_path=path)
            changed = endpoint()
            assert changed != original
            committed = path.read_text()
            with patch.object(config, "_write_text_atomically", side_effect=OSError("failed save")):
                with pytest.raises(OSError):
                    config.save_provider_configs({**update, "base_url": base + "/failed"}, config_path=path)
            assert path.read_text() == committed
        port = relay.port
        relay.close()
        relay = ProviderProxyRelay(port=port)
        relay.activate(path)
        for url, expected in ((original, "/v1/responses"), (changed, "/v1/new/responses")):
            target = urlsplit(url)
            client = http.client.HTTPConnection(target.hostname, target.port, timeout=3)
            try:
                client.request("POST", target.path + "/responses", b"test")
                response = client.getresponse()
                assert response.status == 200
                response.read()
                assert calls[-1][0] == expected
            finally:
                client.close()
    finally:
        relay.close()
