from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import socket
import ssl
from threading import Event, Thread
from urllib.parse import urlsplit
from unittest.mock import patch

import pytest

from codex_usage_hud import codex_provider_config as config
from codex_usage_hud import codex_cli_launcher as cli
from codex_usage_hud.provider_proxy import ProviderProxyRelay, proxy_metadata


@pytest.fixture
def relay():
    instance = ProviderProxyRelay()
    yield instance
    instance.close()


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
                                     "proxy_port": 7898}, config_path=path)
        definition = config.read_provider_definitions(path)["other"]
        assert definition.base_url == "https://example.test/v2"
        assert definition.use_proxy and definition.proxy_port == 7898
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
