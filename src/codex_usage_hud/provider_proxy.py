"""Loopback-only provider relay, with explicit direct/proxy routing.

Routes carry no credentials. The shared relay runs independently of the HUD,
so a HUD restart cannot close Codex's cached endpoints or active streams.
The relay is idle until Codex sends a request; it has no polling worker.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import encodings.idna  # noqa: F401 -- preload before frozen relay worker threads resolve hosts.
import http.client
import gzip
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import re
import secrets
import select
import socket
import sqlite3
import uuid
from socketserver import TCPServer
from threading import RLock, Thread
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit

from .provider_gateway_auth import GATEWAY_AUTH_HEADER, GATEWAY_PATH_ID, gateway_authorized
from .provider_proxy_diagnostics import (
    HUD_REQUEST_HEADER, SseDiagnostics, diagnostic_text, error_body_chunks,
    sample_error_body, upstream_request_id,
)

MARKER = "# codex-hud-proxy: "
_METADATA = re.compile(r"(?m)^# codex-hud-proxy: (.+)\r?$")
_HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                "te", "trailer", "transfer-encoding", "upgrade"}
_LOGGER = logging.getLogger(__name__)


def _decode_gateway_body(body: bytes | None, encoding: str) -> bytes | None:
    encoding = encoding.strip().lower()
    if not body or not encoding or encoding == "identity":
        return body
    if encoding not in {"gzip", "zstd"}:
        raise ValueError("HUD 代理不支持此请求压缩格式。")
    try:
        errors = (OSError, EOFError)
        if encoding == "gzip":
            reader = gzip.GzipFile(fileobj=io.BytesIO(body))
        else:
            try:
                import zstandard
            except ImportError as exc:
                raise ValueError("缺少 zstandard 依赖，无法解压 Codex 请求。") from exc
            errors += (zstandard.ZstdError,)
            reader = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(body))
        limit = 128 * 1024 * 1024
        with reader:
            result = reader.read(limit + 1)
    except errors as exc:
        raise ValueError("无法解压 Codex 请求。") from exc
    if len(result) > limit:
        raise ValueError("解压后的 Codex 请求超过 128 MiB。")
    return result


class LoopbackHTTPServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer.server_bind calls getfqdn, which can block startup on
        # hosts with slow/broken reverse DNS (including macOS runners).
        # These servers bind numeric loopback addresses and need no lookup.
        TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


def _rewrite_model(body: bytes | None, target_model: object, allowed_models: object = None) -> bytes | None:
    """Pin provider requests to the model selected for the proxy route.

    Codex may issue an automatic compaction request with its native fallback
    model even after a custom provider model was selected.  The proxy route is
    provider-scoped, so correcting only the JSON model field is sufficient and
    leaves non-JSON or streaming framing untouched.
    """
    model = str(target_model or "").strip()
    if not body or not model:
        return body
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return body
    if not isinstance(payload, dict) or not isinstance(payload.get("model"), str):
        return body
    allowed = {str(item).strip() for item in allowed_models} if isinstance(allowed_models, list) else set()
    if payload["model"] in allowed or payload["model"] == model:
        return body
    payload["model"] = model
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _source_evidence_from_request(
    headers: Mapping[str, object],
    body: bytes | None,
) -> tuple[str, str] | None:
    from .core.background_usage import (
        classify_background_request_body,
        valid_background_event_id,
    )

    raw_metadata = str(headers.get("x-codex-turn-metadata") or "").strip()
    try:
        metadata = json.loads(raw_metadata)
    except (TypeError, ValueError):
        return None
    if not isinstance(metadata, dict):
        return None
    thread_id = valid_background_event_id(metadata.get("thread_id")).lower()
    if not thread_id:
        return None
    feature = classify_background_request_body(body)
    if not feature.key or feature.key == "unknown":
        return None
    return thread_id, feature.key


def proxy_metadata(section: str) -> dict:
    match = _METADATA.search(section)
    if not match:
        return {}
    try:
        value = json.loads(match.group(1))
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def with_proxy_metadata(body: str, metadata: dict, newline: str) -> str:
    body = _METADATA.sub("", body).rstrip("\r\n")
    return body + newline + MARKER + json.dumps(metadata, ensure_ascii=True, separators=(",", ":"))


def updated_proxy_metadata(metadata: dict, **changes) -> dict:
    """Retain immutable committed routes used by already-created sessions."""
    updated = {**metadata, **changes}
    keys = ("base_url", "enabled", "port", "model", "models")
    if metadata.get("token") and any(metadata.get(key) != updated.get(key) for key in keys):
        previous = {key: metadata[key] for key in (*keys, "token") if key in metadata}
        updated["previous_routes"] = [*metadata.get("previous_routes", []), previous]
        updated.pop("token", None)
    return updated


def proxy_route_required(metadata: Mapping[str, object]) -> bool:
    """Return whether new requests need the loopback relay.

    A provider without an upstream HTTP proxy and without a Desktop model
    rewrite can use its upstream URL directly. Existing loopback tokens are
    still warmed during activation so already-created sessions keep working.
    """
    if bool(metadata.get("enabled")):
        return True
    if str(metadata.get("model") or "").strip():
        return True
    models = metadata.get("models")
    return isinstance(models, list) and any(str(item or "").strip() for item in models)


def _validated_proxy_upstream(metadata: Mapping[str, object]) -> SplitResult:
    upstream = urlsplit(str(metadata.get("base_url") or ""))
    if upstream.scheme not in {"http", "https"} or not upstream.hostname or upstream.username:
        raise ValueError("使用代理配置时 Base URL 必须是有效的 HTTP / HTTPS 地址。")
    try:
        port = int(metadata.get("port", 7897))
    except (TypeError, ValueError):
        raise ValueError("代理端口必须为 1–65535。") from None
    if not 1 <= port <= 65535:
        raise ValueError("代理端口必须为 1–65535。")
    return upstream


class ProviderProxyRelay:
    def __init__(self, port: int = 0) -> None:
        self.port = port
        self.server: ThreadingHTTPServer | None = None
        self.thread: Thread | None = None
        self.routes: dict[str, dict] = {}
        self.config_paths: set[Path] = set()
        self.lock = RLock()
        self.source_evidence_listeners: dict[str, Callable[[str, str], None]] = {}
        self.stop_reader = None
        self.stop_writer = None

    def route(self, metadata: dict, config_path: Path) -> str:
        upstream = _validated_proxy_upstream(metadata)
        with self.lock:
            # Restore old immutable endpoints as well as the current one. Only
            # committed metadata survives a restart; failed saves cannot alter
            # the route used by existing sessions.
            for previous in metadata.get("previous_routes", []):
                self.route({key: previous[key] for key in
                            ("base_url", "enabled", "port", "token", "model", "models") if key in previous}, config_path)
            self._start()
            token = str(metadata.get("token") or secrets.token_urlsafe(24))
            if token == GATEWAY_PATH_ID:
                raise ValueError("The HUD gateway path cannot be used as a secret token.")
            metadata["token"] = token
            path_id = metadata.get("path_id")
            if path_id:
                if path_id != GATEWAY_PATH_ID or not metadata.get("gateway_config"):
                    raise ValueError("Readable routes are reserved for the HUD gateway.")
                existing = self.routes.get(path_id)
                if existing and Path(existing["gateway_config"]).resolve() != config_path.resolve():
                    raise ValueError("The readable HUD route already belongs to another config.")
            self.routes[token] = dict(metadata)
            if path_id:
                self.routes[path_id] = dict(metadata)
            self.config_paths.add(config_path)
            return f"http://127.0.0.1:{self.server.server_port}/{path_id or token}{upstream.path.rstrip('/')}"

    def config_base_url(self, metadata: dict, config_path: Path) -> str:
        """Select the persisted URL and retain any existing relay endpoints."""
        _validated_proxy_upstream(metadata)
        if metadata.get("gateway"):
            from .provider_gateway import gateway_info

            info = gateway_info(config_path)
            if info:
                return str(info["endpoint"])
        if proxy_route_required(metadata):
            return self.route(metadata, config_path)
        # Direct providers never need a new loopback endpoint. Warm only
        # already-committed tokens; helper availability must not prevent
        # restoring an independent upstream URL for new sessions.
        for route in [*metadata.get("previous_routes", []), metadata]:
            if not route.get("token"):
                continue
            try:
                self.route({key: value for key, value in route.items()
                            if key != "previous_routes"}, config_path)
            except (OSError, RuntimeError, ValueError, http.client.HTTPException) as exc:
                _LOGGER.warning("provider_previous_route_warm_failed error=%s", type(exc).__name__)
        return str(metadata["base_url"])

    def _start(self) -> None:
        if self.server is not None:
            return
        self.server = LoopbackHTTPServer(("127.0.0.1", self.port), self._handler_type())
        self.port = self.server.server_port
        self.stop_reader, self.stop_writer = socket.socketpair()
        self.thread = Thread(target=self._serve, name="provider-proxy-relay", daemon=True)
        self.thread.start()

    def add_source_evidence_listener(
        self,
        listener: Callable[[str, str], None],
    ) -> str:
        token = secrets.token_urlsafe(16)
        with self.lock:
            self.source_evidence_listeners[token] = listener
        return token

    def remove_source_evidence_listener(self, token: object) -> None:
        with self.lock:
            self.source_evidence_listeners.pop(str(token or ""), None)

    def _report_source_evidence(
        self,
        headers: Mapping[str, object],
        body: bytes | None,
    ) -> None:
        with self.lock:
            listeners = tuple(self.source_evidence_listeners.values())
        if not listeners:
            return
        evidence = _source_evidence_from_request(headers, body)
        if evidence is None:
            return
        self._deliver_source_evidence(*evidence)

    def _deliver_source_evidence(self, thread_id: str, feature_key: str) -> None:
        with self.lock:
            listeners = tuple(self.source_evidence_listeners.values())
        for listener in listeners:
            try:
                listener(thread_id, feature_key)
            except Exception:
                continue

    def _serve(self) -> None:
        server, stop_reader = self.server, self.stop_reader
        while True:
            ready = select.select([server, stop_reader], [], [])[0]
            if stop_reader in ready:
                return
            server._handle_request_noblock()

    def _handler_type(self):
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            rbufsize = 0

            def log_message(self, *_args):
                pass

            def _send_relay_failure(self, error, route=None, upstream_id="", upstream_status=None):
                context = diagnostic_text(route or {}, self.hud_request_id, upstream_id, upstream_status)
                explanation = ("Codex HUD provider relay could not read the upstream HTTP response."
                               if upstream_status is not None else
                               "Codex HUD provider relay did not receive an upstream HTTP response.")
                detail = (f"[Codex HUD] {context}\n"
                          f"{explanation}\n"
                          f"{type(error).__name__}: {error}\n").encode("utf-8", errors="replace")
                _LOGGER.warning("provider_relay_failure %s error=%s", context, type(error).__name__)
                try:
                    self.send_response_only(502, "Bad Gateway")
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(detail)))
                    self.send_header("Connection", "close")
                    self.send_header(HUD_REQUEST_HEADER, self.hud_request_id)
                    self.end_headers()
                    self.wfile.write(detail)
                    self.wfile.flush()
                except OSError:
                    pass

            def _forward(self):
                self.hud_request_id = "hud_" + uuid.uuid4().hex
                path = urlsplit(self.path)
                token, _, suffix = path.path.lstrip("/").partition("/")
                with relay.lock:
                    route = relay.routes.get(token)
                if not route:
                    self.send_error(404)
                    return
                if token == GATEWAY_PATH_ID and not gateway_authorized(self.headers, str(route.get("token") or "")):
                    self.send_error(401, "HUD gateway authentication required")
                    self.close_connection = True
                    return
                gateway = bool(route.get("gateway_config"))
                if gateway:
                    from .provider_gateway import resolve_route

                    gateway_path = Path(route["gateway_config"])
                    try:
                        route = resolve_route(
                            gateway_path, self.headers,
                            generation=(self.command == "POST" or self.headers.get("Upgrade", "").lower() == "websocket")
                            and suffix.rstrip("/").endswith(("/responses", "/responses/compact")),
                        )
                    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
                        self._send_relay_failure(exc, {"provider": "unresolved", "base_url": ""})
                        self.close_connection = True
                        return
                upstream = urlsplit(route["base_url"])
                target = "/" + suffix if suffix else upstream.path or "/"
                if gateway:
                    target = upstream.path.rstrip("/") + "/" + suffix.removeprefix("v1/")
                    # The built-in ChatGPT client includes /codex beneath its
                    # configured backend root; custom Responses clients do not.
                    if suffix.startswith("v1/codex/"):
                        target = upstream.path.rstrip("/") + "/" + suffix[len("v1/codex/"):]
                query = "&".join(filter(None, (upstream.query, path.query)))
                if gateway and route.get("query_params"):
                    # The selected upstream owns configured keys, including
                    # api-version; cached native-provider values cannot win.
                    params = route["query_params"]
                    pairs = [(key, value) for key, value in parse_qsl(query, keep_blank_values=True)
                             if key not in params]
                    query = urlencode([*pairs, *params.items()])
                if query:
                    target += "?" + query
                connection = None
                response = None
                upstream_id = ""
                upstream_response_received = False
                try:
                    if route.get("enabled"):
                        cls = http.client.HTTPSConnection if upstream.scheme == "https" else http.client.HTTPConnection
                        connection = cls("127.0.0.1", int(route["port"]), timeout=120)
                        if upstream.scheme == "https":
                            connection.set_tunnel(upstream.hostname, upstream.port or 443)
                        else:
                            target = f"{upstream.scheme}://{upstream.netloc}{target}"
                    else:
                        cls = http.client.HTTPSConnection if upstream.scheme == "https" else http.client.HTTPConnection
                        connection = cls(upstream.hostname, upstream.port, timeout=120)
                    class UnbufferedResponse(http.client.HTTPResponse):
                        def __init__(self, sock, **kwargs):
                            super().__init__(sock, **kwargs)
                            self.fp.close()
                            self.fp = sock.makefile("rb", buffering=0)
                            self.fp.read1 = self.fp.read

                        def begin(self):
                            super().begin()
                            if self.status == 101:
                                self.will_close = False

                    connection.response_class = UnbufferedResponse
                    upgrade = self.headers.get("Upgrade", "").lower() == "websocket"
                    nominated = {x.strip().lower() for x in self.headers.get("Connection", "").split(",")}
                    headers = {k: v for k, v in self.headers.items()
                               if k.lower() not in _HOP_HEADERS | nominated | {"host", "content-length"}}
                    if gateway:
                        headers = {k: v for k, v in headers.items() if k.lower() not in {
                            "authorization", "api-key", "x-api-key", "chatgpt-account-id",
                            "openai-organization", "openai-project", "cookie", GATEWAY_AUTH_HEADER.lower(),
                        }}
                        headers.update(route.get("headers") or {})
                    headers["Host"] = upstream.netloc
                    if upgrade:
                        headers.update({"Connection": "Upgrade", "Upgrade": "websocket"})
                    body = None
                    def read_exact(size):
                        data = bytearray()
                        while len(data) < size:
                            chunk = self.rfile.read(size - len(data))
                            if not chunk:
                                raise ValueError("Truncated request body")
                            data.extend(chunk)
                        return bytes(data)

                    if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                        chunks = []
                        while True:
                            size = int(self.rfile.readline().split(b";", 1)[0], 16)
                            if size == 0:
                                while self.rfile.readline().strip():
                                    pass
                                break
                            chunks.append(read_exact(size))
                            if read_exact(2) != b"\r\n":
                                raise ValueError("Invalid chunk framing")
                        body = b"".join(chunks)
                    elif self.headers.get("Content-Length"):
                        body = read_exact(int(self.headers["Content-Length"]))
                    if gateway and self.headers.get("Content-Encoding"):
                        body = _decode_gateway_body(body, self.headers["Content-Encoding"])
                        headers = {k: v for k, v in headers.items() if k.lower() != "content-encoding"}
                    request_path = urlsplit(target).path.rstrip("/")
                    if request_path.endswith(("/responses", "/responses/compact")):
                        if gateway and request_path.endswith("/responses") and not upgrade:
                            from .provider_gateway import bind_image_draft

                            body = bind_image_draft(gateway_path, route, body)
                        relay._report_source_evidence(self.headers, body)
                        if gateway and route.get("image_generation"):
                            if upgrade or request_path.endswith("/compact"):
                                raise ValueError("生图模式需要 HTTP Responses 请求，不支持 WebSocket 或上下文压缩。")
                            from .image_generation import serve_image_generation

                            serve_image_generation(self, gateway_path, route, body)
                            return
                        body = _rewrite_model(body, route.get("model"), route.get("models"))
                        if gateway:
                            from .provider_gateway import prepare_request_body

                            if upgrade and route.get("foreign_history"):
                                raise ValueError("跨供应商副本需要 HTTP Responses 传输；请重新打开副本后重试。")
                            body = prepare_request_body(gateway_path, route, body)
                    connection.request(self.command, target or "/", body=body, headers=headers)
                    response = connection.getresponse()
                    upstream_id = upstream_request_id(response.headers)
                    error_chunks = error_body_chunks(response) if response.status >= 400 and self.command != "HEAD" else None
                    sample = sample_error_body(error_chunks) if error_chunks is not None else b""
                    if error_chunks is not None:
                        upstream_id = upstream_request_id(response.headers, sample)
                    upstream_response_received = True
                    self.send_response_only(response.status, response.reason)
                    omitted = _HOP_HEADERS | {"content-length", HUD_REQUEST_HEADER.lower(), "x-codex-hud-upstream-request-id"}
                    if error_chunks is not None:
                        omitted |= {"content-type", "content-encoding", "content-md5", "digest", "etag"}
                    for key, value in response.getheaders():
                        if key.lower() not in omitted:
                            self.send_header(key, value)
                    self.send_header(HUD_REQUEST_HEADER, self.hud_request_id)
                    if upstream_id:
                        self.send_header("X-Codex-Hud-Upstream-Request-Id", upstream_id)
                    if error_chunks is not None:
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                    if upgrade and response.status == 101:
                        self.send_header("Connection", "Upgrade")
                        self.send_header("Upgrade", "websocket")
                        self.end_headers()
                        self.wfile.flush()
                        remote = connection.sock
                        if remote is None:
                            raise OSError("Missing upstream socket")
                        remote.settimeout(None)
                        self.connection.settimeout(None)
                        # Both handshake readers are unbuffered: frames stay on the sockets.
                        while True:
                            pending = getattr(remote, "pending", lambda: 0)()
                            readable = [remote] if pending else select.select([self.connection, remote], [], [], 120)[0]
                            if not readable:
                                break
                            for source in readable:
                                data = source.recv(65536)
                                if not data:
                                    return
                                (remote if source is self.connection else self.connection).sendall(data)
                    else:
                        self.send_header("Connection", "close")
                        self.end_headers()
                        if self.command != "HEAD":
                            if error_chunks is not None:
                                context = diagnostic_text(route, self.hud_request_id, upstream_id, response.status)
                                _LOGGER.warning("provider_upstream_error %s", context)
                                self.wfile.write(f"[Codex HUD] {context}\n\n".encode("utf-8") + sample)
                                self.wfile.flush()
                                for data in error_chunks:
                                    self.wfile.write(data)
                                    self.wfile.flush()
                            else:
                                sse = SseDiagnostics(route, self.hud_request_id, response.headers) if (
                                    "text/event-stream" in str(response.getheader("Content-Type", "")).lower()
                                    and str(response.getheader("Content-Encoding", "")).lower() in {"", "identity"}
                                ) else None
                                while data := response.read1(65536):
                                    self.wfile.write(sse.feed(data) if sse else data)
                                    self.wfile.flush()
                                if sse:
                                    self.wfile.write(sse.finish())
                                    self.wfile.flush()
                except (OSError, ValueError, LookupError, sqlite3.Error, http.client.HTTPException) as exc:
                    if not upstream_response_received and not getattr(self, "hud_image_response_started", False):
                        self._send_relay_failure(exc, route, upstream_id, response.status if response else None)
                    else:
                        _LOGGER.warning("provider_upstream_stream_failure %s error=%s", diagnostic_text(route, self.hud_request_id, upstream_id), type(exc).__name__)
                finally:
                    self.close_connection = True
                    if connection is not None:
                        connection.close()

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _forward

        return Handler

    def activate(self, config_path: Path) -> None:
        self._rewrite(config_path, activate=True)

    def _rewrite(self, path: Path, *, activate: bool) -> None:
        from . import codex_provider_config as config

        if not path.exists():
            return
        original = config._read_text_exact(path)
        candidate = original
        activation_error: Exception | None = None
        for provider, definition in config.read_provider_definitions(path).items():
            metadata = proxy_metadata(config._provider_section_text(original, provider))
            if not metadata:
                continue
            metadata = updated_proxy_metadata(
                metadata, base_url=definition.base_url,
                model=definition.desktop_model, models=list(definition.desktop_models),
            )
            section = config._section_range(candidate, provider)
            if section is None:
                continue
            if activate:
                try:
                    base_url = self.config_base_url(metadata, path)
                except (OSError, RuntimeError, ValueError, http.client.HTTPException) as exc:
                    # Finish direct-provider migration even if an unrelated
                    # provider still requires an unavailable helper.
                    if activation_error is None:
                        activation_error = exc
                    continue
            else:
                base_url = str(metadata["base_url"])
            newline = config._preferred_newline(candidate)
            body = config._set_quoted_value(section[2], "base_url", base_url, newline)
            body = with_proxy_metadata(body, metadata, newline)
            candidate = config._replace_section_body(candidate, provider, body)
        if candidate != original:
            config._validate_toml(candidate)
            config._write_text_atomically(path, candidate, original)
        if activation_error is not None:
            raise activation_error

    def close(self) -> None:
        try:
            for path in list(self.config_paths):
                self._rewrite(path, activate=False)
        finally:
            if self.server is not None:
                self.stop_writer.send(b"x")
                if self.thread is not None:
                    self.thread.join(timeout=2)
                self.server.server_close()
                self.server = None
            self.thread = None
            for sock in (self.stop_reader, self.stop_writer):
                if sock is not None:
                    sock.close()
            self.stop_reader = self.stop_writer = None
            self.routes.clear()
            self.config_paths.clear()


class DetachedProviderProxyRelay(ProviderProxyRelay):
    """HUD-side configuration facade; closing it never owns the network relay."""

    def __init__(self, port: int = 57323, *, state_path: Path | None = None) -> None:
        super().__init__(port)
        self.state_path = state_path
        self._service = None
        self._gateway_service = None

    def route(self, metadata: dict, config_path: Path) -> str:
        from .provider_proxy_service import ProviderProxyServiceClient

        with self.lock:
            if self._service is None:
                self._service = ProviderProxyServiceClient(
                    self.port, state_path=self.state_path,
                    on_evidence=self._deliver_source_evidence,
                )
            endpoint = self._service.route(metadata, config_path)
            self.config_paths.add(config_path)
            return endpoint

    def activate(self, config_path: Path) -> None:
        from .provider_gateway import activate_gateway

        activate_gateway(config_path, self)

    def restore_legacy_routes(self, config_path: Path) -> None:
        """Warm committed private URLs without opting into gateway takeover."""
        from . import codex_provider_config as config

        # Do not rewrite config or mint tokens. Old windows can retain any of
        # these endpoints even when the current provider now uses the gateway.
        for definition in config.read_provider_definitions(config_path).values():
            metadata = proxy_metadata(definition.section_text)
            for route in [*metadata.get("previous_routes", []), metadata]:
                if route.get("token"):
                    try:
                        self.route({key: route[key] for key in
                                    ("base_url", "enabled", "port", "token", "model", "models")
                                    if key in route}, config_path)
                    except (OSError, RuntimeError, ValueError, http.client.HTTPException) as exc:
                        _LOGGER.warning("provider_legacy_route_restore_failed error=%s", type(exc).__name__)

    def gateway_route(self, metadata: dict, config_path: Path) -> str:
        from .provider_proxy_service import ProviderProxyServiceClient
        from .runtime_paths import runtime_file_path

        # The legacy helper can keep servicing cached URLs and active streams.
        # A separate stable endpoint upgrades the protocol without killing it.
        with self.lock:
            if self._gateway_service is None:
                self._gateway_service = ProviderProxyServiceClient(
                    self.port + 1,
                    state_path=(self.state_path.with_name("gateway-" + self.state_path.name)
                                if self.state_path else runtime_file_path("provider-gateway-service.json")),
                    on_evidence=self._deliver_source_evidence,
                )
            return self._gateway_service.route(metadata, config_path)

    def close(self) -> None:
        with self.lock:
            service, self._service = self._service, None
            gateway_service, self._gateway_service = self._gateway_service, None
            self.config_paths.clear()
        if service is not None:
            service.close()
        if gateway_service is not None:
            gateway_service.close()


# Stable endpoint across HUD restarts; never redirect to an arbitrary free port.
provider_proxy_relay = DetachedProviderProxyRelay()
