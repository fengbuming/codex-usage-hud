"""Local HTTP bridge for renderer-injected settings UI."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from threading import Thread, current_thread
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse

from .config import ProviderSettings, UserConfig, UserConfigStore

DEFAULT_SETTINGS_BRIDGE_PORT = 57322


class SettingsBridgeServer:
    """Tiny localhost JSON API used by the renderer settings panel."""

    def __init__(
        self,
        store: UserConfigStore,
        host: str = "127.0.0.1",
        port: int = DEFAULT_SETTINGS_BRIDGE_PORT,
        restart_callback: Callable[[], None] | None = None,
        command_callback: Callable[[dict[str, Any]], None] | None = None,
        active_session_callback: Callable[[dict[str, Any]], None] | None = None,
        attachments_callback: Callable[[dict[str, Any]], None] | None = None,
        background_usage_query_callback: (
            Callable[[dict[str, str]], dict[str, object]] | None
        ) = None,
        background_usage_detail_callback: (
            Callable[[str], dict[str, object] | None] | None
        ) = None,
        background_usage_confirm_callback: Callable[[str], bool] | None = None,
        background_usage_policy_query_callback: Callable[[str, str], dict[str, object]] | None = None,
        background_usage_policy_set_callback: Callable[..., dict[str, object]] | None = None,
        session_index_status_callback: Callable[[], dict[str, object]] | None = None,
        session_index_control_callback: Callable[[dict[str, Any]], dict[str, object]] | None = None,
        manage_provider_proxy: bool = False,
    ) -> None:
        self.store = store
        self.manage_provider_proxy = manage_provider_proxy
        self.host = host
        self.port = max(0, int(port))
        self.restart_callback = restart_callback
        self.command_callback = command_callback
        self.active_session_callback = active_session_callback
        self.attachments_callback = attachments_callback
        self.background_usage_query_callback = background_usage_query_callback
        self.background_usage_detail_callback = background_usage_detail_callback
        self.background_usage_confirm_callback = background_usage_confirm_callback
        self.background_usage_policy_query_callback = background_usage_policy_query_callback
        self.background_usage_policy_set_callback = background_usage_policy_set_callback
        self.session_index_status_callback = session_index_status_callback
        self.session_index_control_callback = session_index_control_callback
        self.background_usage_access_token = secrets.token_urlsafe(24)
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None
        self.url = ""

    def start(self) -> str:
        if self._server is not None:
            return self.url
        from .codex_provider_config import default_codex_config_path
        from .provider_proxy import provider_proxy_relay

        if self.manage_provider_proxy:
            provider_proxy_relay.activate(default_codex_config_path())
        handler = self._handler_type()
        try:
            self._server = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError:
            if self.port <= 0:
                raise
            self._server = ThreadingHTTPServer((self.host, 0), handler)
        port = int(self._server.server_address[1])
        self.url = f"http://{self.host}:{port}"
        self._thread = Thread(
            target=self._server.serve_forever,
            name="codex-usage-hud-settings-bridge",
            daemon=True,
        )
        self._thread.start()
        return self.url

    @property
    def background_usage_url(self) -> str:
        if not self.url:
            return ""
        return (
            f"{self.url}/background-usage?access_token="
            f"{quote(self.background_usage_access_token, safe='')}"
        )

    @property
    def session_index_url(self) -> str:
        if not self.url:
            return ""
        return (
            f"{self.url}/session-index/status?access_token="
            f"{quote(self.background_usage_access_token, safe='')}"
        )

    def close(self) -> None:
        from .provider_proxy import provider_proxy_relay

        try:
            self._close_bridge()
        finally:
            if self.manage_provider_proxy:
                provider_proxy_relay.close()

    def _close_bridge(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        self.url = ""
        if server is None:
            if thread is not None and thread is not current_thread():
                thread.join(timeout=2.0)
            return
        try:
            server.shutdown()
        finally:
            if thread is not None and thread is not current_thread():
                thread.join(timeout=2.0)
            server.server_close()

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        store = self.store
        restart_callback = self.restart_callback
        command_callback = self.command_callback
        active_session_callback = self.active_session_callback
        attachments_callback = self.attachments_callback
        background_usage_query_callback = self.background_usage_query_callback
        background_usage_detail_callback = self.background_usage_detail_callback
        background_usage_confirm_callback = self.background_usage_confirm_callback
        background_usage_policy_query_callback = self.background_usage_policy_query_callback
        background_usage_policy_set_callback = self.background_usage_policy_set_callback
        session_index_status_callback = self.session_index_status_callback
        session_index_control_callback = self.session_index_control_callback
        background_usage_access_token = self.background_usage_access_token

        class Handler(BaseHTTPRequestHandler):
            server_version = "codex-usage-hud-settings"

            def log_message(self, format: str, *args: object) -> None:
                del format, args
                return

            def do_OPTIONS(self) -> None:
                self._send_json({"ok": True})

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/settings":
                    self._send_config(store.load(), "ok", "settings loaded")
                    return
                if parsed.path == "/background-usage":
                    self._background_usage_query(parsed)
                    return
                if parsed.path == "/background-usage/detail":
                    self._background_usage_detail(parsed)
                    return
                if parsed.path == "/background-usage/policy":
                    self._background_usage_policy_query(parsed)
                    return
                if parsed.path == "/session-index/status":
                    self._session_index_status(parsed)
                    return
                if parsed.path != "/settings":
                    self._send_json({"status": "failed", "message": "not found"}, 404)
                    return

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                if path == "/settings":
                    self._save_settings()
                    return
                if path == "/prices/fetch":
                    self._fetch_prices()
                    return
                if path == "/restart":
                    self._request_restart()
                    return
                if path == "/command":
                    self._receive_command()
                    return
                if path == "/active-session":
                    self._receive_active_session()
                    return
                if path == "/composer-attachments":
                    self._receive_attachments()
                    return
                if path == "/background-usage/confirm":
                    self._background_usage_confirm(urlparse(self.path))
                    return
                if path == "/background-usage/policy":
                    self._background_usage_policy_set(urlparse(self.path))
                    return
                if path == "/session-index/control":
                    self._session_index_control(urlparse(self.path))
                    return
                self._send_json({"status": "failed", "message": "not found"}, 404)

            def _background_usage_authorized(self, parsed: Any) -> bool:
                supplied = str(
                    parse_qs(str(parsed.query or "")).get("access_token", [""])[0]
                )
                if secrets.compare_digest(supplied, background_usage_access_token):
                    return True
                self._send_json(
                    {"status": "failed", "message": "background usage access denied"},
                    403,
                )
                return False

            def _background_usage_query(self, parsed: Any) -> None:
                if not self._background_usage_authorized(parsed):
                    return
                if background_usage_query_callback is None:
                    self._send_json(
                        {"status": "failed", "message": "background usage is unavailable"},
                        503,
                    )
                    return
                query = parse_qs(str(parsed.query or ""))
                filters = {
                    "range_key": str(query.get("range", ["today"])[0]),
                    "feature": str(query.get("feature", [""])[0]),
                    "model": str(query.get("model", [""])[0]),
                    "event_id": str(query.get("eventId", [""])[0]),
                }
                try:
                    payload = background_usage_query_callback(filters)
                except Exception as exc:
                    self._send_json(
                        {
                            "status": "failed",
                            "message": f"background usage query failed: {exc}",
                        },
                        500,
                    )
                    return
                self._send_json({"status": "ok", "backgroundUsage": payload})

            def _background_usage_detail(self, parsed: Any) -> None:
                if not self._background_usage_authorized(parsed):
                    return
                if background_usage_detail_callback is None:
                    self._send_json(
                        {"status": "failed", "message": "background usage is unavailable"},
                        503,
                    )
                    return
                query = parse_qs(str(parsed.query or ""))
                event_id = str(query.get("eventId", [""])[0]).strip()
                try:
                    payload = background_usage_detail_callback(event_id)
                except Exception as exc:
                    self._send_json(
                        {
                            "status": "failed",
                            "message": f"background usage detail failed: {exc}",
                        },
                        500,
                    )
                    return
                if payload is None:
                    self._send_json(
                        {"status": "failed", "message": "background usage event not found"},
                        404,
                    )
                    return
                self._send_json({"status": "ok", "backgroundUsageDetail": payload})

            def _background_usage_confirm(self, parsed: Any) -> None:
                if not self._background_usage_authorized(parsed):
                    return
                if background_usage_confirm_callback is None:
                    self._send_json(
                        {"status": "failed", "message": "background usage is unavailable"},
                        503,
                    )
                    return
                event_id = str(self._read_json().get("eventId") or "").strip()
                try:
                    changed = bool(background_usage_confirm_callback(event_id))
                except Exception as exc:
                    self._send_json(
                        {
                            "status": "failed",
                            "message": f"background usage confirmation failed: {exc}",
                        },
                        500,
                    )
                    return
                self._send_json(
                    {
                        "status": "ok",
                        "message": "background usage confirmed" if changed else "unchanged",
                        "changed": changed,
                    }
                )

            def _background_usage_policy_query(self, parsed: Any) -> None:
                if not self._background_usage_authorized(parsed):
                    return
                if background_usage_policy_query_callback is None:
                    self._send_json({"status": "failed", "message": "background policy is unavailable"}, 503)
                    return
                query = parse_qs(str(parsed.query or ""))
                try:
                    payload = background_usage_policy_query_callback(str(query.get("feature", [""])[0]), str(query.get("eventId", [""])[0]))
                except Exception as exc:
                    self._send_json({"status": "failed", "message": f"background policy query failed: {exc}"}, 500)
                    return
                self._send_json({"status": "ok", "backgroundUsagePolicy": payload})

            def _background_usage_policy_set(self, parsed: Any) -> None:
                if not self._background_usage_authorized(parsed):
                    return
                if background_usage_policy_set_callback is None:
                    self._send_json({"status": "failed", "message": "background policy is unavailable"}, 503)
                    return
                body = self._read_json()
                try:
                    args = (
                        str(body.get("featureKey") or ""),
                        str(body.get("desiredState") or ""),
                        body.get("expectedPolicyRevision"),
                        str(body.get("eventId") or ""),
                        str(body.get("source") or "usage_detail"),
                    )
                    payload = background_usage_policy_set_callback(*args)
                except Exception as exc:
                    self._send_json({"status": "failed", "message": f"background policy update failed: {exc}"}, 500)
                    return
                self._send_json({"status": "ok", "backgroundUsagePolicy": payload})

            def _session_index_status(self, parsed: Any) -> None:
                if not self._background_usage_authorized(parsed):
                    return
                if session_index_status_callback is None:
                    self._send_json(
                        {"status": "failed", "message": "session index is unavailable"},
                        503,
                    )
                    return
                try:
                    payload = session_index_status_callback()
                except Exception as exc:
                    self._send_json(
                        {"status": "failed", "message": f"session index status failed: {exc}"},
                        500,
                    )
                    return
                if not isinstance(payload, dict):
                    payload = {}
                self._send_json({"status": "ok", "sessionIndex": payload})

            def _session_index_control(self, parsed: Any) -> None:
                if not self._background_usage_authorized(parsed):
                    return
                if session_index_control_callback is None:
                    self._send_json(
                        {"status": "failed", "message": "session index is unavailable"},
                        503,
                    )
                    return
                body = self._read_json()
                body = body if isinstance(body, dict) else {}
                body["accessTokenCheck"] = True
                try:
                    payload = session_index_control_callback(body)
                except Exception as exc:
                    self._send_json(
                        {"status": "failed", "message": f"session index control failed: {exc}"},
                        500,
                    )
                    return
                if not isinstance(payload, dict):
                    payload = {}
                self._send_json({"status": "ok", "sessionIndex": payload})

            def _save_settings(self) -> None:
                body = self._read_json()
                payload = body.get("settings", body)
                current = store.load()
                merged = current.to_dict()
                if isinstance(payload, dict):
                    merged.update(payload)
                config = UserConfig.from_dict(merged)
                if self._pricing_state_changed(current, config):
                    self._send_json(
                        {
                            "status": "failed",
                            "message": (
                                "价格有变更，请通过 Renderer 设置新价格的生效时间并先预览。"
                            ),
                        },
                        409,
                    )
                    return
                try:
                    store.save(config)
                except OSError as exc:
                    self._send_json(
                        {"status": "failed", "message": f"save failed: {exc}"},
                        500,
                    )
                    return
                self._send_config(config, "ok", "settings saved")

            def _fetch_prices(self) -> None:
                self._read_json()
                self._send_json(
                    {
                        "status": "failed",
                        "message": (
                            "旧版直接拉取已停用，请在 Renderer 中设置生效时间并先预览。"
                        ),
                    },
                    409,
                )

            @staticmethod
            def _pricing_state_changed(
                current: UserConfig, candidate: UserConfig
            ) -> bool:
                if (
                    current.pricing_versions != candidate.pricing_versions
                    or current.pricing_audit != candidate.pricing_audit
                ):
                    return True
                if current.model_prices != candidate.model_prices:
                    return True
                providers = set(current.provider_settings) | set(
                    candidate.provider_settings
                )
                return any(
                    current.provider_settings.get(name, ProviderSettings()).model_prices
                    != candidate.provider_settings.get(name, ProviderSettings()).model_prices
                    for name in providers
                )

            def _request_restart(self) -> None:
                # Consume the POST body before replying; otherwise Windows can
                # reset the connection while the client is still uploading it.
                self._read_json()
                if restart_callback is None:
                    self._send_json(
                        {
                            "status": "failed",
                            "message": "restart is not available for this HUD session",
                        },
                        503,
                    )
                    return
                try:
                    restart_callback()
                except Exception as exc:
                    self._send_json(
                        {"status": "failed", "message": f"restart failed: {exc}"},
                        500,
                    )
                    return
                self._send_json(
                    {
                        "status": "ok",
                        "message": "HUD restart requested; daemon mode will relaunch it shortly",
                    }
                )

            def _receive_command(self) -> None:
                if command_callback is None:
                    self._send_json(
                        {
                            "status": "failed",
                            "message": "renderer command callback is not available",
                        },
                        503,
                    )
                    return
                body = self._read_json()
                if not body:
                    self._send_json(
                        {"status": "failed", "message": "empty command"},
                        400,
                    )
                    return
                try:
                    command_callback(body)
                except Exception as exc:
                    self._send_json(
                        {"status": "failed", "message": f"command failed: {exc}"},
                        500,
                    )
                    return
                self._send_json({"status": "ok", "message": "command accepted"})

            def _receive_active_session(self) -> None:
                if active_session_callback is None:
                    self._send_json(
                        {
                            "status": "failed",
                            "message": "active session callback is not available",
                        },
                        503,
                    )
                    return
                body = self._read_json()
                if not body:
                    self._send_json(
                        {"status": "failed", "message": "empty active session payload"},
                        400,
                    )
                    return
                try:
                    active_session_callback(body)
                except Exception as exc:
                    self._send_json(
                        {"status": "failed", "message": f"active session failed: {exc}"},
                        500,
                    )
                    return
                self._send_json({"status": "ok", "message": "active session accepted"})

            def _receive_attachments(self) -> None:
                if attachments_callback is None:
                    self._send_json(
                        {
                            "status": "failed",
                            "message": "attachments callback is not available",
                        },
                        503,
                    )
                    return
                body = self._read_json()
                # 空附件也需要上报（用户清空了输入框附件），故不拒绝空 body。
                try:
                    attachments_callback(body)
                except Exception as exc:
                    self._send_json(
                        {"status": "failed", "message": f"attachments failed: {exc}"},
                        500,
                    )
                    return
                self._send_json({"status": "ok", "message": "attachments accepted"})

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}
                try:
                    raw = self.rfile.read(length).decode("utf-8")
                    value = json.loads(raw)
                except (OSError, json.JSONDecodeError):
                    return {}
                return value if isinstance(value, dict) else {}

            def _send_config(
                self,
                config: UserConfig,
                status: str,
                message: str,
                *,
                extra: dict[str, object] | None = None,
            ) -> None:
                payload: dict[str, object] = {
                    "status": status,
                    "message": message,
                    "settings": config.to_dict(),
                    "settingsPath": str(store.path),
                }
                if extra:
                    payload.update(extra)
                self._send_json(payload)

            def _send_json(self, payload: dict[str, object], status_code: int = 200) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Content-Type,X-Codex-Usage-Hud-Token",
                )
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.end_headers()
                self.wfile.write(data)

        return Handler


__all__ = ["DEFAULT_SETTINGS_BRIDGE_PORT", "SettingsBridgeServer"]
