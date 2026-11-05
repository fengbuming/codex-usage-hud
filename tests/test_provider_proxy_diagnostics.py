import gzip
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.parse import urlsplit

import pytest

from codex_usage_hud.provider_proxy import ProviderProxyRelay
from codex_usage_hud.provider_proxy_diagnostics import (
    HUD_REQUEST_HEADER, SseDiagnostics, annotate_sse_frame, safe_base_url,
    upstream_request_id,
)


def test_request_ids_are_distinct_from_response_ids_and_urls_hide_credentials():
    assert upstream_request_id({"X-Request-ID": "req-header"}, b'{"reqId":"req-body"}') == "req-header"
    assert upstream_request_id({}, b'{"error":{"requestId":"req-body"}}') == "req-body"
    assert upstream_request_id({}, b'{"id":"resp_not_a_request_id"}') == ""
    assert upstream_request_id({}, b'{"reqId":"req-large", "unfinished":') == "req-large"
    assert safe_base_url("https://user:secret@example.com:8443/api/v2?api_key=secret#token") == "https://example.com:8443/api/v2"
    assert safe_base_url("http://[::1]:1234/v1") == "http://[::1]:1234/v1"


@pytest.mark.parametrize("encoding", ["identity", "gzip", "zstd", "deflate"])
@pytest.mark.parametrize("id_source", ["header", "body", "none"])
def test_http_errors_keep_status_and_complete_body_with_displayable_context(tmp_path, encoding, id_source):
    original = json.dumps({"error": {"message": "original upstream failure", "code": "quota", **({"reqId": "req-body"} if id_source == "body" else {})}, "padding": "x" * 140000}).encode()
    wire = original
    if encoding == "gzip":
        wire = gzip.compress(original)
    elif encoding == "zstd":
        import zstandard

        wire = zstandard.ZstdCompressor().compress(original)
    elif encoding == "deflate":
        import zlib

        wire = zlib.compress(original)

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(429, "Upstream Quota")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(wire)))
            if encoding != "identity":
                self.send_header("Content-Encoding", encoding)
            if id_source == "header":
                self.send_header("X-Request-ID", "req-header")
            self.end_headers()
            self.wfile.write(wire)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    relay = ProviderProxyRelay()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/api/v2?api_key=private"
        endpoint = urlsplit(relay.route({"base_url": base_url, "provider_id": "yeta"}, tmp_path / "config.toml"))
        client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
        try:
            client.request("GET", endpoint.path + "/responses")
            response = client.getresponse()
            data = response.read()
            hud_id = response.getheader(HUD_REQUEST_HEADER)
            assert response.status == 429 and response.reason == "Upstream Quota"
            assert response.getheader("Content-Type") == "text/plain; charset=utf-8"
            assert response.getheader("Content-Encoding") is None
            assert data.endswith(original), "the diagnostic sample must not truncate the error body"
            prefix = data[:-len(original)].decode()
            assert "provider=yeta" in prefix
            assert f"baseUrl=http://127.0.0.1:{server.server_port}/api/v2" in prefix
            assert "private" not in prefix
            expected = {"header": "req-header", "body": "req-body", "none": "not provided"}[id_source]
            assert f"upstreamReqId={expected}" in prefix
            assert hud_id.startswith("hud_") and f"hudReqId={hud_id}" in prefix
        finally:
            client.close()
    finally:
        relay.close()
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_local_connection_failures_show_real_provider_base_and_unique_local_id(tmp_path):
    relay = ProviderProxyRelay()
    try:
        endpoint = urlsplit(relay.route({"base_url": "http://127.0.0.1:1/v1?key=hidden", "provider_id": "alpha"}, tmp_path / "config.toml"))
        ids = []
        for _ in range(2):
            client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
            try:
                client.request("GET", endpoint.path + "/responses")
                response = client.getresponse()
                body = response.read().decode()
                assert response.status == 502
                assert "provider=alpha" in body and "baseUrl=http://127.0.0.1:1/v1" in body
                assert "upstreamReqId=not provided" in body
                assert "ConnectionRefusedError" in body and "hidden" not in body
                ids.append(response.getheader(HUD_REQUEST_HEADER))
                assert ids[-1] in body
            finally:
                client.close()
        assert len(set(ids)) == 2
    finally:
        relay.close()


@pytest.mark.parametrize("kind", ["error", "response.failed"])
def test_sse_errors_keep_original_fields_and_append_provider_url_and_ids(kind):
    payload = {"type": kind, "request_id": "req-frame", "error": {"message": "original", "code": "quota"}}
    if kind == "response.failed":
        payload["response"] = {"error": payload.pop("error")}
    frame = b"event: " + kind.encode() + b"\r\nid: event-1\r\ndata: " + json.dumps(payload).encode() + b"\r\n\r\n"
    route = {"provider": "alpha", "base_url": "https://api.example/v1?token=hidden"}
    result = annotate_sse_frame(frame, route, "hud_test", {})
    assert b"id: event-1" in result and result.endswith(b"\r\n\r\n")
    data = json.loads(next(line[6:] for line in result.splitlines() if line.startswith(b"data: ")))
    error = data.get("error") or data["response"]["error"]
    assert error["code"] == "quota" and error["message"].startswith("original\n")
    assert "provider=alpha" in error["message"] and "baseUrl=https://api.example/v1" in error["message"]
    assert "upstreamReqId=req-frame" in error["message"] and "hudReqId=hud_test" in error["message"]
    assert "hidden" not in error["message"]
    stream = SseDiagnostics(route, "hud_test", {})
    pieces = [stream.feed(frame[index:index + 7]) for index in range(0, len(frame), 7)]
    assert b"".join(pieces) + stream.finish() == result


def test_successful_sse_and_large_events_are_byte_for_byte_unchanged():
    original = b'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
    large = b"data: " + b"x" * 200000 + b"\r\n\r\n"
    stream = SseDiagnostics({}, "hud_test", {})
    source = original + large + original
    result = b"".join(stream.feed(source[i:i + 8192]) for i in range(0, len(source), 8192)) + stream.finish()
    assert result == source
