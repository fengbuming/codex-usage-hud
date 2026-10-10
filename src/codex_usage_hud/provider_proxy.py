"""Loopback-only provider relay, with explicit direct/proxy routing.

Routes carry no credentials and are restored to upstream URLs on clean shutdown.
The relay is idle until Codex sends a request; it has no polling worker.
"""

from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import select
import socket
from threading import RLock, Thread
from urllib.parse import urlsplit

MARKER = "# codex-hud-proxy: "
_METADATA = re.compile(r"(?m)^# codex-hud-proxy: (.+)\r?$")
_HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                "te", "trailer", "transfer-encoding", "upgrade"}


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


class ProviderProxyRelay:
    def __init__(self, port: int = 0) -> None:
        self.port = port
        self.server: ThreadingHTTPServer | None = None
        self.thread: Thread | None = None
        self.routes: dict[str, dict] = {}
        self.config_paths: set[Path] = set()
        self.lock = RLock()
        self.stop_reader = None
        self.stop_writer = None

    def route(self, metadata: dict, config_path: Path) -> str:
        upstream = urlsplit(str(metadata.get("base_url") or ""))
        if upstream.scheme not in {"http", "https"} or not upstream.hostname or upstream.username:
            raise ValueError("使用代理配置时 Base URL 必须是有效的 HTTP / HTTPS 地址。")
        try:
            port = int(metadata.get("port", 7897))
        except (TypeError, ValueError):
            raise ValueError("代理端口必须为 1–65535。") from None
        if not 1 <= port <= 65535:
            raise ValueError("代理端口必须为 1–65535。")
        with self.lock:
            if self.server is None:
                self.server = ThreadingHTTPServer(("127.0.0.1", self.port), self._handler_type())
                self.port = self.server.server_port
                self.stop_reader, self.stop_writer = socket.socketpair()
                self.thread = Thread(target=self._serve, name="provider-proxy-relay", daemon=True)
                self.thread.start()
            token = str(metadata.get("token") or secrets.token_urlsafe(24))
            metadata["token"] = token
            self.routes[token] = dict(metadata)
            self.config_paths.add(config_path)
            return f"http://127.0.0.1:{self.server.server_port}/{token}{upstream.path.rstrip('/')}"

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

            def _forward(self):
                path = urlsplit(self.path)
                token, _, suffix = path.path.lstrip("/").partition("/")
                with relay.lock:
                    route = relay.routes.get(token)
                if not route:
                    self.send_error(404)
                    return
                upstream = urlsplit(route["base_url"])
                target = "/" + suffix if suffix else upstream.path or "/"
                query = "&".join(filter(None, (upstream.query, path.query)))
                if query:
                    target += "?" + query
                connection = None
                started = False
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
                    connection.request(self.command, target or "/", body=body, headers=headers)
                    response = connection.getresponse()
                    self.send_response_only(response.status, response.reason)
                    for key, value in response.getheaders():
                        if key.lower() not in _HOP_HEADERS | {"content-length"}:
                            self.send_header(key, value)
                    started = True
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
                            while data := response.read1(65536):
                                self.wfile.write(data)
                                self.wfile.flush()
                except (OSError, ValueError, http.client.HTTPException):
                    if not started:
                        self.send_error(502, "Provider connection failed")
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
        for provider, definition in config.read_provider_definitions(path).items():
            metadata = proxy_metadata(definition.section_text)
            if not metadata:
                continue
            section = config._section_range(candidate, provider)
            if section is None:
                continue
            base_url = self.route(metadata, path) if activate else str(metadata["base_url"])
            newline = config._preferred_newline(candidate)
            body = config._set_quoted_value(section[2], "base_url", base_url, newline)
            body = with_proxy_metadata(body, metadata, newline)
            candidate = config._replace_section_body(candidate, provider, body)
        if candidate != original:
            config._validate_toml(candidate)
            config._write_text_atomically(path, candidate, original)

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


# Stable endpoint across HUD restarts; never silently redirect to an occupied port.
provider_proxy_relay = ProviderProxyRelay(port=57323)
