"""Detached, idle-on-demand provider relay and its authenticated loopback control."""

from __future__ import annotations

import argparse
import http.client
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import secrets
import select
import socket
import subprocess
import sys
from threading import RLock, Thread
import time

from .runtime_paths import runtime_file_path
from .provider_proxy import LoopbackHTTPServer

CONTROL_PATH = "/__codex_hud_proxy__/"
CONTROL_HEADER = "X-Codex-Hud-Proxy-Control"
PROTOCOL_VERSION = 1

# A HUD launched from the Windows desktop can itself be placed in a
# kill-on-close Job Object.  The provider relay is deliberately a separate
# lifetime: Codex must keep sending requests after the HUD is stopped.  Ask
# Windows to break the helper out of that job in addition to detaching its
# console and process group.
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(state, handle, separators=(",", ":"))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_state(path: Path, port: int) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("version") == PROTOCOL_VERSION and state.get("port") == port and state.get("control_token"):
            return state
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def _load_or_create_state(path: Path, port: int) -> dict:
    state = _read_state(path, port)
    if state:
        return state
    state = {"version": PROTOCOL_VERSION, "port": port, "control_token": secrets.token_urlsafe(32)}
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Concurrent HUD/CLI startup must share one control token. Replacing
        # this file here could strand an already-starting helper.
        with path.open("x", encoding="utf-8") as handle:
            os.chmod(path, 0o600)
            json.dump(state, handle, separators=(",", ":"))
        return state
    except FileExistsError:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            state = _read_state(path, port)
            if state:
                return state
            time.sleep(.02)
        raise RuntimeError("Provider 代理状态文件无效，未修改已有文件。") from None


def helper_command(state_path: Path, port: int, *, wait_for_port: float = 0) -> list[str]:
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command += ["-m", "codex_usage_hud.provider_proxy_service"]
    return command + [
        "--provider-proxy-helper", "--provider-proxy-state-file", str(state_path),
        "--provider-proxy-port", str(port), "--provider-proxy-wait", str(wait_for_port),
    ]


def launch_helper(state_path: Path, port: int, *, wait_for_port: float = 0) -> subprocess.Popen:
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        options["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | _CREATE_BREAKAWAY_FROM_JOB
        )
    else:
        options["start_new_session"] = True
    log_path = state_path.with_suffix(".startup.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as log:
        os.chmod(log_path, 0o600)
        options["stderr"] = log
        return subprocess.Popen(helper_command(state_path, port, wait_for_port=wait_for_port), **options)


class ProviderProxyServiceClient:
    def __init__(self, port: int, *, state_path: Path | None = None, on_evidence=None) -> None:
        self.port = port
        self.state_path = state_path or runtime_file_path("provider-proxy-service.json")
        self.on_evidence = on_evidence
        self.state: dict = {}
        self.process = None
        self._callback_server = None
        self._callback_thread = None
        self._callback_stop = None
        self._subscription = secrets.token_urlsafe(24)
        self._lock = RLock()

    def _call(self, action: str, payload: dict | None = None) -> dict:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            connection.request("POST", CONTROL_PATH + action, json.dumps(payload or {}), {
                CONTROL_HEADER: self.state["control_token"], "Content-Type": "application/json",
            })
            response = connection.getresponse()
            value = json.loads(response.read())
            if response.status != 200:
                raise RuntimeError(value.get("error") or "Provider relay control request failed")
            return value
        finally:
            connection.close()

    def ensure(self) -> None:
        with self._lock:
            self.state = _read_state(self.state_path, self.port)
            if self.state:
                try:
                    if self._call("health").get("version") == PROTOCOL_VERSION:
                        self._subscribe()
                        return
                except (OSError, ValueError, RuntimeError, http.client.HTTPException):
                    pass
            # An unknown listener may be an old HUD or another application.
            # Never replace it or silently change Codex's stable endpoint.
            with socket.socket() as probe:
                probe.settimeout(.3)
                occupied = probe.connect_ex(("127.0.0.1", self.port)) == 0
            if occupied:
                raise RuntimeError(f"端口 {self.port} 仍由旧版 HUD 代理或其它程序占用；请先完成代理进程迁移。")
            if not self.state:
                self.state = _load_or_create_state(self.state_path, self.port)
            self.process = launch_helper(self.state_path, self.port)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    if self._call("health").get("version") == PROTOCOL_VERSION:
                        self._subscribe()
                        return
                except (OSError, ValueError, RuntimeError, http.client.HTTPException):
                    pass
                if self.process.poll() is not None:
                    break
                time.sleep(.05)
            exit_code = self.process.poll()
            if exit_code is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            raise RuntimeError(
                "独立 provider 代理未能启动；保留原有 provider 配置。"
                f" exit_code={exit_code}; 启动日志：{self.state_path.with_suffix('.startup.log')}"
            )

    def route(self, metadata: dict, config_path: Path) -> str:
        self.ensure()
        result = self._call("route", {"metadata": metadata, "config_path": str(config_path.resolve())})
        metadata.update(result["metadata"])
        return result["endpoint"]

    def _subscribe(self) -> None:
        if self.on_evidence is None:
            return
        if self._callback_server is not None:
            # The callback listener outlives the helper. Re-register it after
            # every health check so a replacement also receives the address.
            self._call("subscribe", {
                "token": self._subscription, "port": self._callback_server.server_port,
            })
            return
        client = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                if not secrets.compare_digest(self.headers.get(CONTROL_HEADER, ""), client._subscription):
                    self.send_error(403)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 4096:
                        raise ValueError("Invalid evidence size")
                    evidence = json.loads(self.rfile.read(size))
                    client.on_evidence(str(evidence["thread_id"]), str(evidence["feature_key"]))
                    self.send_response(204)
                    self.end_headers()
                except (ValueError, KeyError):
                    self.send_error(400)

        server = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
        reader, writer = socket.socketpair()

        def serve():
            try:
                while reader not in select.select([server, reader], [], [])[0]:
                    server._handle_request_noblock()
            finally:
                server.server_close()
                reader.close()

        self._callback_server = server
        self._callback_stop = writer
        self._callback_thread = Thread(target=serve, daemon=True, name="provider-proxy-evidence")
        self._callback_thread.start()
        try:
            self._call("subscribe", {"token": self._subscription, "port": server.server_port})
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._callback_server is None:
            return
        try:
            self._call("unsubscribe", {"token": self._subscription})
        except (OSError, ValueError, RuntimeError, http.client.HTTPException):
            pass
        self._callback_stop.send(b"x")
        self._callback_thread.join(timeout=2)
        self._callback_stop.close()
        self._callback_server = self._callback_thread = self._callback_stop = None


def run_helper(state_path: Path, port: int, *, wait_for_port: float = 0) -> int:
    from .codex_provider_config import default_codex_config_path, read_provider_definitions
    from .provider_proxy import ProviderProxyRelay, proxy_metadata

    state = _load_or_create_state(state_path, port)
    subscriptions: dict[str, int] = {}

    class Relay(ProviderProxyRelay):
        def _handler_type(self):
            relay = self
            base = super()._handler_type()

            class Handler(base):
                def _forward(self):
                    if not self.path.startswith(CONTROL_PATH):
                        return super()._forward()
                    if not secrets.compare_digest(self.headers.get(CONTROL_HEADER, ""), state["control_token"]):
                        self.send_error(403)
                        return
                    try:
                        size = int(self.headers.get("Content-Length", "0"))
                        if not 0 < size <= 1024 * 1024:
                            raise ValueError("Invalid control request size")
                        payload = json.loads(self.rfile.read(size))
                        action = self.path[len(CONTROL_PATH):]
                        if action == "health":
                            result = {"version": PROTOCOL_VERSION, "pid": os.getpid()}
                        elif action == "route":
                            metadata = payload["metadata"]
                            with relay.lock:
                                endpoint = relay.route(metadata, Path(payload["config_path"]))
                                _save_state(state_path, {**state, "routes": relay.routes, "config_paths": [str(p) for p in relay.config_paths]})
                            result = {"endpoint": endpoint, "metadata": metadata}
                        elif action in {"subscribe", "unsubscribe"}:
                            token = str(payload["token"])
                            with relay.lock:
                                if action == "subscribe":
                                    callback_port = int(payload["port"])
                                    if not token or not 1 <= callback_port <= 65535:
                                        raise ValueError("Invalid evidence subscriber")
                                    subscriptions[token] = callback_port
                                else:
                                    subscriptions.pop(token, None)
                            result = {}
                        else:
                            raise ValueError("Unknown control action")
                        body = json.dumps(result).encode()
                        self.send_response_only(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    except (OSError, ValueError, TypeError, KeyError) as error:
                        body = json.dumps({"error": str(error)}).encode()
                        self.send_response_only(400)
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)

                do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _forward

            return Handler

    relay = Relay(port)
    relay.routes.update(state.get("routes") or {})
    paths = {Path(p) for p in state.get("config_paths", [])}
    if wait_for_port > 0:
        paths.add(default_codex_config_path())
    # Warm committed current/previous tokens before accepting a request. This
    # also supports a controlled handover from an older in-process HUD relay.
    for path in paths:
        if not path.is_file():
            continue
        for definition in read_provider_definitions(path).values():
            metadata = proxy_metadata(definition.section_text)
            if not metadata:
                continue
            for route in [*metadata.get("previous_routes", []), {**metadata, "model": definition.desktop_model, "models": list(definition.desktop_models)}]:
                if route.get("token"):
                    relay.routes[str(route["token"])] = dict(route)
            relay.config_paths.add(path)

    def evidence(thread_id: str, feature_key: str) -> None:
        with relay.lock:
            subscribers = tuple(subscriptions.items())

        def deliver():
            for token, callback_port in subscribers:
                connection = http.client.HTTPConnection("127.0.0.1", callback_port, timeout=.5)
                try:
                    connection.request("POST", "/evidence", json.dumps({"thread_id": thread_id, "feature_key": feature_key}), {CONTROL_HEADER: token})
                    response = connection.getresponse()
                    response.read()
                    if response.status != 204:
                        raise OSError("Evidence subscriber unavailable")
                except (OSError, http.client.HTTPException):
                    with relay.lock:
                        subscriptions.pop(token, None)
                finally:
                    connection.close()

        if subscribers:
            Thread(target=deliver, daemon=True, name="provider-proxy-evidence-push").start()

    relay.add_source_evidence_listener(evidence)
    deadline = time.monotonic() + wait_for_port
    while True:
        try:
            relay._start()
            break
        except OSError as exc:
            if time.monotonic() >= deadline:
                print(f"Provider relay bind failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 1
            time.sleep(.05)
    relay.thread.join()
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-proxy-helper", action="store_true")
    parser.add_argument("--provider-proxy-state-file", type=Path, required=True)
    parser.add_argument("--provider-proxy-port", type=int, required=True)
    parser.add_argument("--provider-proxy-wait", type=float, default=0)
    args = parser.parse_args(argv)
    return run_helper(args.provider_proxy_state_file, args.provider_proxy_port, wait_for_port=args.provider_proxy_wait)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
