"""Renderer CDP client lifecycle and transport helpers."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

from .config import UserConfig
from .core.connection_health import ConnectionHealth, PROBE_TIMEOUT_SECONDS
from .core.parser import ParsedSession
from .core.runtime_errors import RuntimeErrorEvent
from .platforms.cdp_probe import (
    cdp_port_from_env,
    install_new_document_script,
    list_targets,
    pick_page_target,
    remove_new_document_script,
    send_cdp_command,
)
from .platforms.codex_theme import CodexThemeProbe, CodexThemeSnapshot
from .renderer_catalog import (
    RENDERER_HUD_SCRIPT,  # noqa: F401 - compatibility export
    model_catalog_payload as _renderer_model_catalog_payload,  # noqa: F401 - compatibility export
    renderer_hud_script_with_model_catalog as _renderer_hud_script_with_model_catalog,
)
from .renderer_cdp import RendererTargetDiscovery as _RendererTargetDiscovery
from .renderer_cdp import _RendererBinding
from .renderer_payload_builder import (
    _renderer_theme_payload,
    _runtime_expression_params,
    payload_from_snapshot,
)
from .renderer_metrics import RendererMetricsWindow
from .support_assets import support_qr_payload


DEFAULT_RENDERER_TIMEOUT_SECONDS = 0.45
DEFAULT_RENDERER_TARGET_CACHE_SECONDS = 2.0
SLOW_RENDERER_UPDATE_LOG_MS = 250.0
RENDERER_STARTUP_RETRY_INTERVAL_SECONDS = 0.5
WARMUP_ACK_TARGET = 3
WARMUP_MAX_PROBES = 6
WARMUP_MAX_SECONDS = 8.0
ACTIVE_SESSION_BINDING_NAME = "codexUsageHudActiveSession"
SETTINGS_COMMAND_BINDING_NAME = "codexUsageHudSettingsCommand"
COMPOSER_ATTACHMENTS_BINDING_NAME = "codexUsageHudComposerAttachments"
LAYOUT_BINDING_NAME = "codexUsageHudLayout"
THEME_BINDING_NAME = "codexUsageHudTheme"
RENDERER_HUD_ENV = "CODEX_USAGE_HUD_RENDERER"
_LOGGER = logging.getLogger("codex_usage_hud.ui.renderer_hud")
REMOVE_RENDERER_HUD_SCRIPT = (
    "(() => {"
    "let existed = false;"
    "try {"
    "const remove = window.__codexUsageHudRemove;"
    "existed = typeof remove === 'function' || !!document.getElementById('codex-usage-hud-root');"
    "if (typeof remove === 'function') remove();"
    "else {"
    "document.getElementById('codex-usage-hud-root')?.remove();"
    "document.getElementById('codex-usage-hud-style')?.remove();"
    "}"
    "} catch (_) {"
    "document.getElementById('codex-usage-hud-root')?.remove();"
    "document.getElementById('codex-usage-hud-style')?.remove();"
    "existed = true;"
    "}"
    "return existed;"
    "})()"
)


def renderer_enabled_from_env(default: bool = True) -> bool:
    value = os.environ.get(RENDERER_HUD_ENV)
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized not in {"0", "false", "no", "off"}


class RendererHudClient:
    """Install and update the in-renderer HUD through a local CDP target."""

    def __init__(
        self,
        *,
        port: int | None = None,
        timeout_seconds: float = DEFAULT_RENDERER_TIMEOUT_SECONDS,
        update_timeout_seconds: float = 1.5,
        target_cache_seconds: float = DEFAULT_RENDERER_TARGET_CACHE_SECONDS,
        enabled: bool | None = None,
    ) -> None:
        self.port = int(port or cdp_port_from_env())
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.update_timeout_seconds = max(0.2, float(update_timeout_seconds))
        self.target_cache_seconds = max(0.0, float(target_cache_seconds))
        self.enabled = renderer_enabled_from_env() if enabled is None else bool(enabled)
        self.last_status = "idle" if self.enabled else "disabled"
        self.last_error = ""
        self.last_update_metrics: dict[str, object] = {}
        self.last_bootstrap_metrics: dict[str, object] = {}
        self.last_attach_metrics: dict[str, object] = {}
        self._target_id = ""
        self._script_identifier = ""
        self._websocket_url = ""
        self._cached_target_id = ""
        self._cached_websocket_url = ""
        self._target_cache_at = 0.0
        self._support_images_sent = False
        self._update_lock = threading.Lock()
        self._update_failure_count = 0
        self._update_retry_not_before = 0.0
        self._update_cooldown_until = 0.0
        self._metrics_window = RendererMetricsWindow()
        self._payload_domain_digests: dict[str, str] = {}
        self._payload_extras_digest: str | None = None
        self._payload_digest_target_id = ""
        self._target_discovery = _RendererTargetDiscovery(
            port=self.port,
            timeout_seconds=self.timeout_seconds,
            list_targets_fn=lambda port, timeout: list_targets(port, timeout),
            pick_target_fn=lambda targets: pick_page_target(targets),
        )
        self._active_session_binding: _RendererBinding | None = None
        self._active_session_callback: Any = None
        self._settings_command_binding: _RendererBinding | None = None
        self._settings_command_callback: Any = None
        self._attachments_binding: _RendererBinding | None = None
        self._attachments_callback: Any = None
        self._layout_binding: _RendererBinding | None = None
        self._layout_callback: Any = None
        self._theme_binding: _RendererBinding | None = None
        self._theme_callback: Any = None
        self._theme_bootstrap_target_id = ""
        # Theme changes are pushed by the renderer binding.  Keep the last
        # snapshot here so a normal HUD refresh never has to synchronously
        # walk the Codex DOM again.  That probe can block the renderer for
        # hundreds of milliseconds while Codex is busy.
        self._theme_snapshot: CodexThemeSnapshot | None = None
        self._theme_probe = CodexThemeProbe(
            port=self.port,
            timeout_seconds=max(0.08, min(self.timeout_seconds, 0.25)),
            cache_seconds=max(0.35, self.target_cache_seconds),
            failure_cooldown_seconds=4.0,
        )
        # When True, the HUD issues no CDP traffic at all (session lock / sleep).
        self._quiesced = False
        # --- renderer white-screen cure state (2026-09-17) ---
        # Unlock warmup gate: after resume only 1+1 probes run until the
        # renderer acks three probes <100ms or the bounded recovery fires.
        self._warming = False
        self._warmup_acks = 0
        self._warmup_probes = 0
        self._warmup_started_at = 0.0
        self._last_warmup_probe_at: float | None = None
        self._recovery_refresh_pending = False
        # Degraded state: on a hung renderer the HUD removes its injection
        # and probes at a low cadence so Codex self-heals. Never restarts
        # Codex from this path.
        self._degraded = False
        self._degraded_reason = ""
        self._degraded_at = 0.0
        self._last_degrade_probe_at: float | None = None
        # First full update after warmup: send small domains first, heavy
        # domains immediately after (never one ~1MB Runtime.evaluate).
        self._split_next_full = False

    def quiesce(self) -> None:
        """Stop all CDP activity toward the Codex renderer without exiting.

        Session-lock/sleep: the renderer is about to enter a power-saving or
        suspended state. Keeping persistent CDP sessions and periodic
        ``Runtime.evaluate`` traffic alive through it pins the page and is what
        wedges the renderer main thread after a long lock (blank Codex UI).
        Quiescing disconnects locally (no CDP round-trip, safe while the
        renderer is suspending) so the page can freeze/resume cleanly. The HUD
        process, its DOM injection, the binding objects, and their callbacks
        all stay in place; ``resume`` + the next ``update`` re-attach them.
        """
        self._quiesced = True
        for attr in (
            "_active_session_binding",
            "_settings_command_binding",
            "_attachments_binding",
            "_layout_binding",
            "_theme_binding",
        ):
            binding = getattr(self, attr, None)
            if binding is not None:
                try:
                    # Local socket close only; the object and its callback are
                    # retained so the normal update path re-ensures it.
                    binding.close()
                except Exception:
                    pass
        # Keep the script identifier and target across the lock: the page realm
        # survives, so the first post-resume update must NOT reinstall the whole
        # ~1.3MB bundle into a thawing renderer. resume() enters the warmup gate
        # instead; a genuinely lost context is re-installed by the existing
        # in-place recovery in _update_payload_once.
        self._clear_target_cache(clear_script=False)
        self._warming = False
        self._warmup_acks = 0
        self._warmup_probes = 0
        self._warmup_started_at = 0.0
        self._last_warmup_probe_at = None

    def resume(self) -> None:
        """Re-enable CDP activity after unlock/wake through the warmup gate.

        The page realm survives a session lock, so the script identifier and
        target cache are retained: the first post-unlock update must not
        reinstall the whole ~1.3MB HUD bundle while the renderer is thawing.
        Instead, updates become probe-only until the renderer acknowledges
        three consecutive 1+1 probes under 100ms.  Slow but responsive
        renderers leave this gate through the bounded recovery path.
        """
        self._quiesced = False
        self._warming = True
        self._warmup_acks = 0
        self._warmup_probes = 0
        self._warmup_started_at = time.monotonic()
        self._last_warmup_probe_at = None
        self._recovery_refresh_pending = True
        self._split_next_full = True

    def recovery_seconds_until_probe(self) -> float | None:
        """Return the next recovery probe deadline without touching CDP."""
        if self._quiesced or not self.enabled:
            return None
        now = time.monotonic()
        if self._degraded:
            last = self._last_degrade_probe_at
            return 0.0 if last is None else max(0.0, last + 10.0 - now)
        if self._warming:
            last = self._last_warmup_probe_at
            return 0.0 if last is None else max(0.0, last + 0.25 - now)
        return None

    def advance_recovery(self) -> bool:
        """Run one due recovery probe; return whether CDP was contacted."""
        remaining = self.recovery_seconds_until_probe()
        if remaining is None or remaining > 0.0:
            return False
        if self._degraded:
            self._degraded_tick()
        elif self._warming:
            self._warmup_tick()
        return True

    def recovery_refresh_pending(self) -> bool:
        """Whether recovery has completed and needs one normal payload push."""
        return bool(
            self._recovery_refresh_pending
            and not self._quiesced
            and not self._warming
            and not self._degraded
        )

    def _probe_renderer_alive(self, *, timeout_seconds: float) -> tuple[bool, float]:
        """Best-effort 1+1 Runtime.evaluate against the current page target."""
        started = time.perf_counter()
        try:
            target = self._page_target()
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            target_id = str(target.get("id") or websocket_url)
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            send_persistent = getattr(self._active_session_binding, "send_command", None)
            if callable(send_persistent):
                try:
                    if self._active_session_binding is not None:
                        self._active_session_binding.ensure(websocket_url, target_id)
                    result = send_persistent(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params("1+1"),
                        timeout_seconds,
                    )
                except Exception:
                    result = send_cdp_command(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params("1+1"),
                        timeout_seconds,
                    )
            else:
                result = send_cdp_command(
                    websocket_url,
                    "Runtime.evaluate",
                    _runtime_expression_params("1+1"),
                    timeout_seconds,
                )
            probe_ok, _apply_ms = self._update_acknowledgement(result)
            return bool(probe_ok), (time.perf_counter() - started) * 1000.0
        except Exception:
            return False, (time.perf_counter() - started) * 1000.0

    def _warmup_tick(self) -> bool:
        """Unlock warmup gate: probe-only until responsive or bounded recovery.

        Returns True (successful no-op) so the event loop never counts a
        warmup probe as a payload failure.
        """
        self._last_warmup_probe_at = time.monotonic()
        ok, probe_ms = self._probe_renderer_alive(timeout_seconds=2.0)
        self._warmup_probes += 1
        if ok and probe_ms < 100.0:
            self._warmup_acks += 1
        else:
            self._warmup_acks = 0
        self.last_update_metrics = {
            **dict(self.last_update_metrics),
            "rendererAliveProbe": "warmup-alive" if ok else "warmup-pending",
            "rendererAliveProbeMs": probe_ms,
            "warmupAcks": self._warmup_acks,
            "warmupProbes": self._warmup_probes,
            "payloadBytes": 0,
            "payloadDomains": [],
        }
        if self._warmup_acks >= WARMUP_ACK_TARGET:
            self._warming = False
            self._split_next_full = True
            self.last_update_metrics["rendererAliveProbe"] = "warmup-complete"
        elif (
            self._warmup_probes >= WARMUP_MAX_PROBES
            or time.monotonic() - self._warmup_started_at >= WARMUP_MAX_SECONDS
        ):
            # Do not strand the HUD behind a strict latency gate.  A renderer
            # that responds consistently but takes >100ms is usable; force a
            # clean reinstall on the next update so payload delivery resumes.
            self._warming = False
            self._split_next_full = True
            self._clear_target_cache(clear_script=True)
            self.last_update_metrics["rendererAliveProbe"] = "warmup-timeout-recover"
        return True

    def degrade(self, reason: str = "") -> None:
        """Enter the degraded state: remove the HUD from the page so Codex
        regains the main thread and can self-heal, then probe at a low
        cadence. Never restarts Codex from this path."""
        if self._degraded:
            return
        self._degraded = True
        self._degraded_reason = str(reason or "")
        self._degraded_at = time.monotonic()
        self._last_degrade_probe_at = None
        self._recovery_refresh_pending = True
        self._warming = False
        # Remove the page injection and stop sockets, but retain callback
        # ownership so recovery can recreate every event channel.
        self.close(remove_from_page=True, preserve_callbacks=True)

    def _degraded_tick(self) -> bool:
        """Low-cadence probe while degraded; self-heal back to normal on ack."""
        now = time.monotonic()
        if (
            self._last_degrade_probe_at is not None
            and now - self._last_degrade_probe_at < 10.0
        ):
            return True
        self._last_degrade_probe_at = now
        ok, probe_ms = self._probe_renderer_alive(timeout_seconds=2.0)
        self.last_update_metrics = {
            **dict(self.last_update_metrics),
            "rendererAliveProbe": "degraded-alive" if ok else "degraded-pending",
            "rendererAliveProbeMs": probe_ms,
            "payloadBytes": 0,
            "payloadDomains": [],
        }
        if ok:
            self._degraded = False
            self._degraded_reason = ""
            self._rebuild_bindings()
            # The HUD script was removed on degrade; force a clean reinstall.
            self._clear_target_cache(clear_script=True)
            self._reset_update_retry_state()
            self.last_update_metrics["rendererAliveProbe"] = "degraded-recovered"
        return True

    _HEAVY_UPDATE_DOMAINS = frozenset({
        "usageInsights",
        "supportImages",
        "sessionIndex",
        "backgroundUsage",
        "backgroundUsageNotification",
    })

    def _update_payload_split(
        self,
        payload: dict[str, object],
        *,
        startup_retry: bool,
    ) -> bool:
        """First full update after unlock: send small domains first, heavy
        domains immediately after, so a thawing renderer never parses a
        ~1MB expression in one synchronous Runtime.evaluate."""
        heavy_keys = {key for key in self._HEAVY_UPDATE_DOMAINS if key in payload}
        if not heavy_keys:
            return self._update_payload_once(payload, startup_retry=startup_retry)
        light_payload = {
            key: value for key, value in payload.items() if key not in heavy_keys
        }
        heavy_payload = {
            key: value for key, value in payload.items() if key in heavy_keys
        }
        light_domains = payload.get("payloadDomains")
        if isinstance(light_domains, dict):
            light_payload["payloadDomains"] = {
                key: value
                for key, value in light_domains.items()
                if key not in heavy_keys
            }
            heavy_payload["payloadDomains"] = {
                key: value
                for key, value in light_domains.items()
                if key in heavy_keys
            }
        light_ok = self._update_payload_once(
            light_payload,
            startup_retry=startup_retry,
        )
        if not light_ok:
            return False
        if not heavy_payload.get("payloadDomains"):
            return True
        return self._update_payload_once(
            heavy_payload,
            startup_retry=startup_retry,
        )

    @property
    def quiesced(self) -> bool:
        return bool(self._quiesced)

    def set_active_session_callback(self, callback: Any) -> None:
        """Receive renderer active-session events over CDP instead of HTTP fetch."""
        if self._active_session_binding is not None:
            self._active_session_binding.close()
            self._active_session_binding = None
        self._active_session_callback = callback if callable(callback) else None
        if callable(callback):
            self._active_session_binding = _RendererBinding(
                ACTIVE_SESSION_BINDING_NAME,
                callback,
                timeout_seconds=self.timeout_seconds,
                disconnect_callback=self._handle_active_session_binding_disconnect,
            )
            self._active_session_binding.retry_same_target = True

    def _handle_active_session_binding_disconnect(self, reason: str) -> None:
        callback = self._active_session_callback
        if not callable(callback):
            return
        try:
            callback(
                {
                    "channelUnavailable": True,
                    "reason": str(reason or "renderer binding disconnected"),
                    "observedAt": int(time.time() * 1000),
                }
            )
        except Exception:
            return

    def set_settings_command_callback(self, callback: Any) -> None:
        """Receive renderer settings commands over CDP instead of HTTP fetch."""
        if self._settings_command_binding is not None:
            self._settings_command_binding.close()
            self._settings_command_binding = None
        self._settings_command_callback = callback if callable(callback) else None
        if callable(self._settings_command_callback):
            self._settings_command_binding = _RendererBinding(
                SETTINGS_COMMAND_BINDING_NAME,
                self._settings_command_callback,
                timeout_seconds=self.timeout_seconds,
            )
            # A renderer reinjection can close the CDP listener without
            # changing the page target.  Keep this command channel eligible
            # for same-target recovery; otherwise the page still exposes the
            # old binding function while commands disappear silently.
            self._settings_command_binding.retry_same_target = True

    def set_attachments_callback(self, callback: Any) -> None:
        """Receive renderer composer-attachment events over CDP instead of HTTP fetch.

        The page CSP blocks in-page fetch to the local bridge, so this binding
        is the reliable channel for delivering attachment token estimates.
        """
        if self._attachments_binding is not None:
            self._attachments_binding.close()
            self._attachments_binding = None
        self._attachments_callback = callback if callable(callback) else None
        if callable(self._attachments_callback):
            self._attachments_binding = _RendererBinding(
                COMPOSER_ATTACHMENTS_BINDING_NAME,
                self._attachments_callback,
                timeout_seconds=self.timeout_seconds,
            )

    def set_layout_callback(self, callback: Any) -> None:
        """Receive renderer HUD layout events (drag/resize/toggle) over CDP.

        The renderer JS reports panel geometry changes through a dedicated
        binding so the Python loop can emit ``renderer_layout_changed`` events
        without polling localStorage or waiting for the next refresh tick.
        """
        if self._layout_binding is not None:
            self._layout_binding.close()
            self._layout_binding = None
        self._layout_callback = callback if callable(callback) else None
        if callable(self._layout_callback):
            self._layout_binding = _RendererBinding(
                LAYOUT_BINDING_NAME,
                self._layout_callback,
                timeout_seconds=self.timeout_seconds,
            )

    def set_theme_callback(self, callback: Any) -> None:
        """Receive live Codex renderer theme changes over CDP."""
        if self._theme_binding is not None:
            self._theme_binding.close()
            self._theme_binding = None
        self._theme_callback = callback if callable(callback) else None
        self._theme_bootstrap_target_id = ""
        if callable(callback):
            self._theme_binding = _RendererBinding(
                THEME_BINDING_NAME,
                self._handle_theme_binding_payload,
                timeout_seconds=self.timeout_seconds,
            )

    def _rebuild_bindings(self) -> None:
        """Recreate event channels after a degraded full close."""
        self.set_active_session_callback(self._active_session_callback)
        self.set_settings_command_callback(self._settings_command_callback)
        self.set_attachments_callback(self._attachments_callback)
        self.set_layout_callback(self._layout_callback)
        self.set_theme_callback(self._theme_callback)

    def _handle_theme_binding_payload(self, payload: dict[str, object]) -> None:
        callback = self._theme_callback
        if not callable(callback):
            return
        snapshot = CodexThemeSnapshot.from_probe_result(payload, source="cdp")
        if snapshot is None:
            return
        self._theme_snapshot = snapshot
        try:
            callback(_renderer_theme_payload(snapshot))
        except Exception:
            return

    def set_audit_callback(self, callback: Any) -> None:
        """Deprecated: request/response audit capture has been removed.

        Kept as a no-op so callers set up before wiring is torn down don't
        crash. Any callback passed here is discarded.
        """
        del callback

    def bootstrap_active_session(
        self,
        *,
        startup_payload: dict[str, object] | None = None,
    ) -> bool:
        """Install the renderer controller and ask it to report the selected session."""
        if not self.enabled:
            self.last_status = "disabled"
            return False
        started = time.perf_counter()
        stage = "target_discovery"
        try:
            target = self._page_target()
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            target_id = str(target.get("id") or websocket_url)
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            if target_id != self._target_id or not self._script_identifier:
                stage = "script_install"
                self._install(websocket_url, target_id)
                self.record_renderer_metric("script_installs")
                self.record_renderer_metric("binding_rebuilds")
            if self._active_session_binding is not None:
                stage = "active_session_binding"
                self._active_session_binding.ensure(websocket_url, target_id)
                wait_ready = getattr(self._active_session_binding, "wait_ready", None)
                if callable(wait_ready) and not wait_ready(self.timeout_seconds):
                    raise RuntimeError("renderer active-session binding was not ready")
            active_expression = (
                "typeof window.__codexUsageHudReportActiveSession === 'function' && "
                "window.__codexUsageHudReportActiveSession('bootstrap')"
            )
            stage = "active_session_report"
            expression = active_expression
            if startup_payload:
                startup_json = json.dumps(startup_payload, ensure_ascii=False)
                expression = (
                    "(() => {"
                    "const startup = typeof window.__codexUsageHudUpdate === 'function' && "
                    f"window.__codexUsageHudUpdate({startup_json});"
                    f"const active = {active_expression};"
                    "return { startup: !!startup, active: active || {} };"
                    "})()"
                )
            result = send_cdp_command(
                websocket_url,
                "Runtime.evaluate",
                _runtime_expression_params(expression),
                self.timeout_seconds,
            )
            value = result.get("result", {}).get("result", {}).get("value", False)
            active_value = (
                value.get("active", False)
                if startup_payload and isinstance(value, dict)
                else value
            )
            acknowledged = True if isinstance(active_value, dict) else bool(active_value)
            if not acknowledged:
                raise RuntimeError(
                    "renderer active session bootstrap did not acknowledge request"
                )
            self._deliver_bootstrap_active_session(active_value)
        except Exception as exc:
            self.last_bootstrap_metrics = {
                "totalMs": (time.perf_counter() - started) * 1000.0,
                "failureStage": stage,
                "startupBubble": bool(startup_payload),
            }
            self._clear_target_cache(clear_script=True)
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
        self.last_bootstrap_metrics = {
            "totalMs": (time.perf_counter() - started) * 1000.0,
            "failureStage": "",
            "startupBubble": bool(startup_payload),
        }
        self.last_status = "ok"
        self.last_error = ""
        return True

    def _deliver_bootstrap_active_session(self, value: object) -> None:
        """Synchronously publish the page-active session returned by bootstrap."""
        callback = self._active_session_callback
        if not callable(callback) or not isinstance(value, dict):
            return
        payload = dict(value)
        if not (
            str(payload.get("sessionId") or payload.get("session_id") or "").strip()
            or str(payload.get("title") or "").strip()
            or bool(payload.get("newSession") or payload.get("new_session"))
            or bool(payload.get("pendingSession") or payload.get("pending_session"))
        ):
            return
        try:
            callback(payload)
        except Exception:
            return

    def update(
        self,
        snapshot: ParsedSession,
        *,
        settings: UserConfig | None = None,
        active_display_mode: str = "renderer",
        settings_path: Path | str | None = None,
        settings_bridge_url: str = "",
        background_usage_bridge_url: str = "",
        session_index_url: str = "",
        session_index: dict[str, object] | None = None,
        background_usage_revision: int = 0,
        background_usage_notification: dict[str, object] | None = None,
        rest_reminder: dict[str, object] | None = None,
        settings_command_status: dict[str, object] | None = None,
        update_state: dict[str, object] | None = None,
        debug: bool = False,
        runtime_errors: list[RuntimeErrorEvent | dict[str, object]] | None = None,
        work_overlay_selectable_max: int = 6,
        desktop_overlay_dependency: dict[str, object] | None = None,
        provider_registry: dict[str, object] | None = None,
        app_provider: str = "",
        default_provider: str = "",
        usage_insights: dict[str, object] | None = None,
        session_cleanup: dict[str, object] | None = None,
        connection_health: dict[str, object] | ConnectionHealth | None = None,
        request_rows_limit: int = 30,
        startup_retry: bool = False,
    ) -> bool:
        if self._quiesced:
            return False
        if self._degraded:
            return self._degraded_tick()
        if self._warming:
            return self._warmup_tick()
        started = time.perf_counter()
        if not startup_retry:
            deferred = self._update_gate_state()
            if deferred is not None:
                self._defer_update(*deferred)
                return False
        support_images = [] if self._support_images_sent else support_qr_payload()
        theme_started = time.perf_counter()
        theme_snapshot = self._theme_snapshot
        if theme_snapshot is None:
            theme_snapshot = self._theme_probe.snapshot()
            self._theme_snapshot = theme_snapshot
        theme_probe_ms = (time.perf_counter() - theme_started) * 1000.0
        payload = payload_from_snapshot(
            snapshot,
            settings=settings,
            active_display_mode=active_display_mode,
            settings_path=settings_path,
            settings_bridge_url=settings_bridge_url,
            background_usage_bridge_url=background_usage_bridge_url,
            session_index_url=session_index_url,
            session_index=session_index,
            background_usage_revision=background_usage_revision,
            background_usage_notification=background_usage_notification,
            rest_reminder=rest_reminder,
            settings_command_status=settings_command_status,
            support_images=support_images,
            theme=_renderer_theme_payload(theme_snapshot),
            update_state=update_state,
            debug=debug,
            runtime_errors=runtime_errors,
            work_overlay_selectable_max=work_overlay_selectable_max,
            desktop_overlay_dependency=desktop_overlay_dependency,
            provider_registry=provider_registry,
            app_provider=app_provider,
            default_provider=default_provider,
            usage_insights=usage_insights,
            session_cleanup=session_cleanup,
            connection_health=connection_health,
            request_rows_limit=request_rows_limit,
        ).to_json()
        update_ok = (
            self.update_payload(payload, startup_retry=True)
            if startup_retry
            else self.update_payload(payload)
        )
        metrics = dict(self.last_update_metrics)
        metrics.update(
            {
                "themeProbeMs": theme_probe_ms,
                "payloadBuildMs": (time.perf_counter() - theme_started) * 1000.0,
                "totalMs": (time.perf_counter() - started) * 1000.0,
            }
        )
        self.last_update_metrics = metrics
        if update_ok:
            if support_images:
                self._support_images_sent = True
            return True
        return False

    def update_startup(self, snapshot: ParsedSession) -> bool:
        """Try a cold-start payload without poisoning the normal retry gate."""
        return self.update(snapshot, startup_retry=True)

    def show_startup(self, payload: dict[str, object]) -> bool:
        """Paint a startup-only payload before normal HUD domain updates begin."""
        return self.update_payload(payload)

    def update_payload(
        self,
        payload: dict[str, object],
        *,
        startup_retry: bool = False,
    ) -> bool:
        if self._quiesced:
            return False
        if self._degraded:
            return self._degraded_tick()
        if self._warming:
            return self._warmup_tick()
        if not self.enabled:
            self.last_status = "disabled"
            return False
        if not startup_retry:
            deferred = self._update_gate_state()
            if deferred is not None:
                self._defer_update(*deferred)
                return False
        lock = getattr(self, "_update_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._update_lock = lock
        if not lock.acquire(blocking=False):
            self._defer_update("busy", 0.0)
            return False
        try:
            self.record_renderer_metric("payload_updates")
            if self._split_next_full:
                self._split_next_full = False
                return self._update_payload_split(
                    payload,
                    startup_retry=startup_retry,
                )
            return self._update_payload_once(payload, startup_retry=startup_retry)
        finally:
            lock.release()

    def _update_payload_once(
        self,
        payload: dict[str, object],
        *,
        startup_retry: bool = False,
    ) -> bool:
        if not self.enabled:
            self.last_status = "disabled"
            return False
        started = time.perf_counter()
        stage = "target_discovery"
        try:
            target_started = time.perf_counter()
            target = self._page_target()
            target_discovery_ms = (time.perf_counter() - target_started) * 1000.0
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            target_id = str(target.get("id") or websocket_url)
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            target_changed = target_id != str(
                getattr(self, "_payload_digest_target_id", "") or ""
            )
            script_reinstall_needed = bool(
                target_id != self._target_id or not self._script_identifier
            )
            if target_changed or script_reinstall_needed:
                self._payload_domain_digests.clear()
                self._payload_extras_digest = None
                self._payload_digest_target_id = target_id
            if script_reinstall_needed:
                stage = "script_install"
                self._install(websocket_url, target_id)
                self.record_renderer_metric("script_installs")
                self.record_renderer_metric("binding_rebuilds")
            if self._theme_binding is not None:
                stage = "theme_binding"
                self._theme_binding.ensure(websocket_url, target_id)
            (
                prepared_payload,
                pending_domain_digests,
                skipped_domain_count,
                pending_extras_digest,
            ) = self._prepare_payload_domain_delta(payload)
            if prepared_payload is None:
                self._record_update_success()
                metrics = dict(self.last_update_metrics)
                metrics.update(
                    {
                        "targetDiscoveryMs": target_discovery_ms,
                        "totalMs": (time.perf_counter() - started) * 1000.0,
                        "failureStage": "",
                        "payloadBytes": 0,
                        "payloadDomains": [],
                        "duplicateDomainUpdateSkipped": True,
                        "skippedDuplicateDomains": skipped_domain_count,
                        "changedDomains": [],
                    }
                )
                self.last_update_metrics = metrics
                self.last_status = "unchanged"
                self.last_error = ""
                return True
            active_session_binding_primed = False
            if (
                self._active_session_binding is not None
                and (startup_retry or script_reinstall_needed)
            ):
                stage = "active_session_binding"
                self._active_session_binding.ensure(websocket_url, target_id)
                active_session_binding_primed = True
                wait_ready = getattr(self._active_session_binding, "wait_ready", None)
                if callable(wait_ready) and not wait_ready(self.timeout_seconds):
                    raise RuntimeError(
                        "renderer active-session binding was not ready"
                    )
            stage = "payload_apply"
            if not self._send_update(websocket_url, prepared_payload):
                # Both the persistent binding and its fresh-websocket
                # verification reported that the page cannot apply a payload.
                # Reinstall and rehydrate immediately: waiting for the normal
                # one-second retry makes the top and bottom HUD visibly blink.
                first_update_metrics = dict(self.last_update_metrics)
                recovery_started = time.perf_counter()
                stage = "payload_recovery"
                self._install(websocket_url, target_id, force=True)
                self.record_renderer_metric("script_installs")
                self.record_renderer_metric("binding_rebuilds")
                recovered = self._send_update(websocket_url, payload)
                recovery_metrics = dict(self.last_update_metrics)
                recovery_metrics.update(
                    {
                        "inPlaceRecovery": True,
                        "recoveryMs": (time.perf_counter() - recovery_started)
                        * 1000.0,
                        "initialPersistentVerification": first_update_metrics.get(
                            "persistentVerification", ""
                        ),
                        "initialRendererApplyMs": first_update_metrics.get(
                            "rendererApplyMs"
                        ),
                    }
                )
                self.last_update_metrics = recovery_metrics
                if not recovered:
                    raise RuntimeError(
                        "renderer update function did not acknowledge payload"
                    )
            if self._active_session_binding is not None and not active_session_binding_primed:
                stage = "active_session_binding"
                self._active_session_binding.ensure(websocket_url, target_id)
            if self._settings_command_binding is not None:
                stage = "settings_binding"
                self._settings_command_binding.ensure(websocket_url, target_id)
            if self._attachments_binding is not None:
                stage = "attachments_binding"
                self._attachments_binding.ensure(websocket_url, target_id)
            if self._layout_binding is not None:
                stage = "layout_binding"
                self._layout_binding.ensure(websocket_url, target_id)
            if self._theme_binding is not None and self._theme_bootstrap_target_id != target_id:
                try:
                    stage = "theme_bootstrap"
                    send_cdp_command(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params(
                            "typeof window.__codexUsageHudReportTheme === 'function' "
                            "&& window.__codexUsageHudReportTheme('binding-ready')"
                        ),
                        self.timeout_seconds,
                    )
                    self._theme_bootstrap_target_id = target_id
                except Exception:
                    pass
        except Exception as exc:
            metrics = dict(self.last_update_metrics)
            metrics.update(
                {
                    "totalMs": (time.perf_counter() - started) * 1000.0,
                    "failureStage": stage,
                }
            )
            self.last_update_metrics = metrics
            failure_text = f"{type(exc).__name__}: {exc}".lower()
            target_lost = stage == "target_discovery" or any(
                marker in failure_text
                for marker in (
                    "target closed",
                    "no such target",
                    "websocket",
                    "connection reset",
                    "broken pipe",
                    "connection refused",
                )
            )
            # A live page can replace its document without closing the CDP
            # target.  In that case the cached target still looks healthy, but
            # the injected HUD function is gone and no payload can be applied.
            # Treat a missing acknowledgement as a document loss so the next
            # retry reinstalls and immediately evaluates the renderer script.
            renderer_context_lost = stage in {"payload_apply", "payload_recovery"}
            if target_lost or renderer_context_lost:
                self._clear_target_cache(clear_script=True)
                self._payload_domain_digests.clear()
                self._payload_extras_digest = None
                self._payload_digest_target_id = ""
            elif startup_retry:
                # A launched Desktop app can replace its startup page with the
                # real main renderer without closing the original target. Do
                # not pin every retry to the first page selected during splash.
                self._clear_target_cache(clear_script=False)
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            if startup_retry:
                # A splash page is an expected transient during a launched
                # Desktop cold start. Keep each probe eligible for the next
                # attempt instead of entering the normal 1/2/30 second gate.
                self._reset_update_retry_state()
            else:
                self._record_update_failure(stage=stage, error=exc)
            return False
        metrics = dict(self.last_update_metrics)
        metrics.update(
            {
                "targetDiscoveryMs": target_discovery_ms,
                "totalMs": (time.perf_counter() - started) * 1000.0,
                "failureStage": "",
                "duplicateDomainUpdateSkipped": False,
                "skippedDuplicateDomains": skipped_domain_count,
                "changedDomains": sorted(pending_domain_digests),
            }
        )
        self.last_update_metrics = metrics
        self.last_status = "ok"
        self.last_error = ""
        self._record_update_success()
        self._recovery_refresh_pending = False
        self._payload_domain_digests.update(pending_domain_digests)
        if pending_extras_digest is not None:
            self._payload_extras_digest = pending_extras_digest
        return True

    @staticmethod
    def _payload_domain_digest(value: object) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _prepare_payload_domain_delta(
        self,
        payload: dict[str, object],
    ) -> tuple[dict[str, object] | None, dict[str, str], int, str | None]:
        """Return a compatible payload containing only changed domains.

        Digests are committed by the caller only after the enclosing CDP
        update succeeds, so a failed send always retries the full changed
        payload.
        """
        raw_domains = payload.get("payloadDomains")
        if not isinstance(raw_domains, Mapping) or not raw_domains:
            return payload, {}, 0, None

        domains = dict(raw_domains)
        all_alias_keys: set[str] = set()
        alias_keys_by_domain: dict[object, set[str]] = {}
        all_digests: dict[str, str] = {}
        for name, value in domains.items():
            aliases = (
                {str(key) for key in value}
                if isinstance(value, Mapping)
                else set()
            )
            alias_keys_by_domain[name] = aliases
            all_alias_keys.update(aliases)
            all_digests[str(name)] = self._payload_domain_digest(value)

        extras = {
            key: value
            for key, value in payload.items()
            if key != "payloadDomains" and str(key) not in all_alias_keys
        }
        extras_digest = self._payload_domain_digest(extras)
        previous_extras_digest = getattr(self, "_payload_extras_digest", None)
        if previous_extras_digest is None or extras_digest != previous_extras_digest:
            return payload, all_digests, 0, extras_digest

        previous_domains = getattr(self, "_payload_domain_digests", {}) or {}
        changed: dict[object, object] = {}
        pending: dict[str, str] = {}
        changed_alias_keys: set[str] = set()
        for name, value in domains.items():
            domain_name = str(name)
            digest = all_digests[domain_name]
            if previous_domains.get(domain_name) == digest:
                continue
            changed[name] = value
            pending[domain_name] = digest
            changed_alias_keys.update(alias_keys_by_domain[name])

        if not changed:
            return None, {}, len(domains), extras_digest
        if len(changed) == len(domains):
            return payload, pending, 0, extras_digest

        reduced = dict(payload)
        reduced["payloadDomains"] = changed
        reduced = {
            key: value
            for key, value in reduced.items()
            if key == "payloadDomains"
            or str(key) not in all_alias_keys
            or str(key) in changed_alias_keys
        }
        return reduced, pending, len(domains) - len(changed), extras_digest

    def _update_gate_state(self) -> tuple[str, float] | None:
        now = time.monotonic()
        cooldown_until = float(getattr(self, "_update_cooldown_until", 0.0) or 0.0)
        if now < cooldown_until:
            return ("cooldown", cooldown_until - now)
        retry_not_before = float(
            getattr(self, "_update_retry_not_before", 0.0) or 0.0
        )
        if now < retry_not_before:
            return ("backoff", retry_not_before - now)
        return None

    def update_gate_state(self) -> tuple[bool, str, float]:
        """Inspect the local update gate without issuing a CDP command."""
        if not self.enabled:
            return False, "disabled", 5.0
        if self._quiesced:
            return False, "quiesced", 5.0
        recovery_in = self.recovery_seconds_until_probe()
        if recovery_in is not None:
            return False, "degraded" if self._degraded else "warming", recovery_in
        lock = getattr(self, "_update_lock", None)
        if lock is not None and lock.locked():
            return False, "busy", 0.05
        deferred = self._update_gate_state()
        if deferred is None:
            return True, "", 0.0
        reason, remaining = deferred
        return False, str(reason), max(0.0, float(remaining))

    def record_renderer_metric(self, name: str, amount: float = 1.0) -> None:
        window = getattr(self, "_metrics_window", None)
        if not isinstance(window, RendererMetricsWindow):
            return
        summary = window.record(name, amount)
        if summary is not None:
            _LOGGER.info("renderer_metrics_window %s", json.dumps(summary, sort_keys=True))

    def _start_renderer_cooldown_metric(self, duration: float) -> None:
        window = getattr(self, "_metrics_window", None)
        if not isinstance(window, RendererMetricsWindow):
            return
        summary = window.start_cooldown(duration)
        if summary is not None:
            _LOGGER.info("renderer_metrics_window %s", json.dumps(summary, sort_keys=True))



    def _defer_update(self, status: str, remaining: float) -> None:
        self.last_status = status
        self.last_error = ""
        metrics = dict(getattr(self, "last_update_metrics", {}) or {})
        metrics.update(
            {
                "deferred": True,
                "deferredReason": status,
                "cooldownRemaining": (
                    max(0.0, float(remaining)) if status == "cooldown" else 0.0
                ),
                "retryNotBefore": time.monotonic() + max(0.0, float(remaining)),
            }
        )
        self.last_update_metrics = metrics

    def _record_update_failure(self, *, stage: str, error: Exception) -> None:
        now = time.monotonic()
        attempt = int(getattr(self, "_update_failure_count", 0) or 0) + 1
        self._update_failure_count = attempt
        if attempt >= 3:
            delay = 30.0
            self._update_cooldown_until = now + delay
            self._start_renderer_cooldown_metric(delay)
        else:
            delay = (1.0, 2.0, 4.0)[min(attempt - 1, 2)]
            self._update_cooldown_until = 0.0
        self._update_retry_not_before = now + delay
        metrics = dict(getattr(self, "last_update_metrics", {}) or {})
        metrics.update(
            {
                "retryAttempt": attempt,
                "retryNotBefore": self._update_retry_not_before,
                "cooldownRemaining": delay if attempt >= 3 else 0.0,
                "failureStage": stage,
                "failureKind": type(error).__name__,
            }
        )
        self.last_update_metrics = metrics

    def _reset_update_retry_state(self) -> None:
        self._update_failure_count = 0
        self._update_retry_not_before = 0.0
        self._update_cooldown_until = 0.0

    def _record_update_success(self) -> None:
        self._update_failure_count = 0
        self._update_retry_not_before = 0.0
        self._update_cooldown_until = 0.0
        window = getattr(self, "_metrics_window", None)
        if isinstance(window, RendererMetricsWindow):
            window.clear_cooldown()
        metrics = dict(getattr(self, "last_update_metrics", {}) or {})
        metrics.update(
            {
                "retryAttempt": 0,
                "retryNotBefore": 0.0,
                "cooldownRemaining": 0.0,
            }
        )
        self.last_update_metrics = metrics

    def probe_connection(self, *, timeout_seconds: float | None = None) -> bool:
        """Cheap CDP liveness check used by conditional heartbeat.

        Prefers the already-open active-session binding socket so healthy idle
        ticks avoid a fresh WebSocket handshake. Falls back to one ephemeral
        Runtime.evaluate against the current page target.
        """
        if self._quiesced:
            return False
        if not self.enabled:
            self.last_status = "disabled"
            return False
        timeout = max(
            0.05,
            float(
                PROBE_TIMEOUT_SECONDS
                if timeout_seconds is None
                else timeout_seconds
            ),
        )
        expression = (
            "typeof window.__codexUsageHudReportActiveSession === 'function' "
            "|| typeof window.__codexUsageHudUpdate === 'function'"
        )
        try:
            target = self._page_target()
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            target_id = str(target.get("id") or websocket_url)
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            send_persistent = getattr(self._active_session_binding, "send_command", None)
            if callable(send_persistent):
                try:
                    if self._active_session_binding is not None:
                        self._active_session_binding.ensure(websocket_url, target_id)
                    result = send_persistent(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params(expression),
                        timeout,
                    )
                except Exception:
                    result = send_cdp_command(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params(expression),
                        timeout,
                    )
            else:
                result = send_cdp_command(
                    websocket_url,
                    "Runtime.evaluate",
                    _runtime_expression_params(expression),
                    timeout,
                )
            value = result.get("result", {}).get("result", {}).get("value", False)
            ok = bool(value)
            self.last_status = "ok" if ok else "failed"
            self.last_error = "" if ok else "renderer probe expression returned false"
            return ok
        except Exception as exc:
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def report_active_session(self, reason: str = "self-heal") -> bool:
        """Force the injected controller to re-read and publish the active session.

        Used by session-follow self-heal after sticky new-session or binding loss.
        """
        if self._quiesced:
            return False
        if not self.enabled:
            self.last_status = "disabled"
            return False
        report_reason = str(reason or "self-heal").replace("\\", "\\\\").replace("'", "\\'")
        expression = (
            "typeof window.__codexUsageHudReportActiveSession === 'function' && "
            f"window.__codexUsageHudReportActiveSession('{report_reason}')"
        )
        try:
            target = self._page_target()
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            target_id = str(target.get("id") or websocket_url)
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            if self._active_session_binding is not None:
                self._active_session_binding.ensure(websocket_url, target_id)
            send_persistent = getattr(self._active_session_binding, "send_command", None)
            if callable(send_persistent):
                try:
                    result = send_persistent(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params(expression),
                        self.timeout_seconds,
                    )
                except Exception:
                    result = send_cdp_command(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params(expression),
                        self.timeout_seconds,
                    )
            else:
                result = send_cdp_command(
                    websocket_url,
                    "Runtime.evaluate",
                    _runtime_expression_params(expression),
                    self.timeout_seconds,
                )
            value = result.get("result", {}).get("result", {}).get("value", False)
            if isinstance(value, dict):
                self._deliver_bootstrap_active_session(value)
                self.last_status = "ok"
                self.last_error = ""
                return True
            ok = bool(value)
            self.last_status = "ok" if ok else "failed"
            self.last_error = "" if ok else "active session report was not acknowledged"
            return ok
        except Exception as exc:
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def rebind_active_session_channel(self) -> bool:
        """Restart the active-session CDP binding for the current page target."""
        if not self.enabled or self._active_session_binding is None:
            return False
        try:
            target = self._page_target(force=True)
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            target_id = str(target.get("id") or websocket_url)
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            # Force a fresh listener even for the same target id.
            self._active_session_binding.close(join_timeout=0.2)
            self._active_session_binding.ensure(websocket_url, target_id)
            wait_ready = getattr(self._active_session_binding, "wait_ready", None)
            if callable(wait_ready) and not wait_ready(self.timeout_seconds):
                raise RuntimeError("active-session binding was not ready after rebind")
            self.last_status = "ok"
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def close(
        self,
        *,
        remove_from_page: bool = True,
        preserve_callbacks: bool = False,
    ) -> None:
        """Release local sockets and best-effort remove the HUD from the page.

        ``remove_from_page=False`` skips the CDP round-trips that uninstall the
        injected HUD. The session-lock exit path uses it: against a renderer
        that is suspending/frozen the cleanup cannot succeed anyway, and the
        attempt only delays the stop. The fresh session after unlock performs
        the real cleanup before it reinstalls.
        """
        if self._active_session_binding is not None:
            self._active_session_binding.close()
            self._active_session_binding = None
        if not preserve_callbacks:
            self._active_session_callback = None
        if self._settings_command_binding is not None:
            self._settings_command_binding.close()
            self._settings_command_binding = None
        if self._attachments_binding is not None:
            self._attachments_binding.close()
            self._attachments_binding = None
        if self._layout_binding is not None:
            self._layout_binding.close()
            self._layout_binding = None
        if self._theme_binding is not None:
            self._theme_binding.close()
            self._theme_binding = None
        if not preserve_callbacks:
            self._settings_command_callback = None
            self._attachments_callback = None
            self._layout_callback = None
            self._theme_callback = None
        self._theme_bootstrap_target_id = ""
        if not self.enabled:
            return
        if not remove_from_page:
            self._clear_target_cache(clear_script=True)
            return
        try:
            for websocket_url in self._close_websocket_candidates():
                try:
                    send_cdp_command(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params(
                            REMOVE_RENDERER_HUD_SCRIPT
                        ),
                        self.timeout_seconds,
                    )
                except Exception:
                    pass
                if not self._script_identifier:
                    continue
                try:
                    remove_new_document_script(
                        websocket_url,
                        self._script_identifier,
                        self.timeout_seconds,
                    )
                except Exception:
                    pass
        except Exception:
            return
        finally:
            self._clear_target_cache(clear_script=True)

    def _close_websocket_candidates(self) -> list[str]:
        urls: list[str] = []
        for websocket_url in (self._websocket_url, self._cached_websocket_url):
            if websocket_url and websocket_url not in urls:
                urls.append(websocket_url)
        try:
            target = self._page_target(force=True)
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            if websocket_url and websocket_url not in urls:
                urls.append(websocket_url)
        except Exception:
            pass
        return urls

    def _page_target(self, *, force: bool = False) -> dict[str, Any]:
        target = self._target_discovery.target(force=force)
        self._cached_target_id = str(
            target.get("id") or target.get("webSocketDebuggerUrl") or ""
        )
        self._cached_websocket_url = str(target.get("webSocketDebuggerUrl") or "")
        self._target_cache_at = time.monotonic()
        return target

    def _clear_target_cache(self, *, clear_script: bool) -> None:
        self._cached_target_id = ""
        self._cached_websocket_url = ""
        self._target_cache_at = 0.0
        self._target_discovery.clear()
        if clear_script:
            self._target_id = ""
            self._websocket_url = ""
            self._script_identifier = ""
            self._theme_bootstrap_target_id = ""
            self._theme_snapshot = None

    def _install(self, websocket_url: str, target_id: str, *, force: bool = False) -> None:
        if target_id != self._target_id:
            self._theme_snapshot = None
        if force and self._script_identifier:
            try:
                remove_new_document_script(
                    websocket_url,
                    self._script_identifier,
                    self.timeout_seconds,
                )
            except Exception:
                pass
        self._script_identifier = install_new_document_script(
            websocket_url,
            _renderer_hud_script_with_model_catalog(),
            self.timeout_seconds,
        )
        self._target_id = target_id
        self._websocket_url = websocket_url
        self._support_images_sent = False

    def _send_update(self, websocket_url: str, payload: dict[str, object]) -> bool:
        payload_json = json.dumps(payload, ensure_ascii=False)
        expression = (
            "(() => {"
            "const started = performance.now();"
            "const ok = typeof window.__codexUsageHudUpdate === 'function' && "
            f"window.__codexUsageHudUpdate({payload_json});"
            "return { ok: !!ok, applyMs: performance.now() - started };"
            "})()"
        )
        started = time.perf_counter()
        transport = "ephemeral"
        persistent_ms: float | None = None
        persistent_fallback_reason = ""
        fallback_ms: float | None = None
        persistent_verification = "not-needed"
        persistent_verification_error = ""
        self.record_renderer_metric("cdp_commands")
        self.record_renderer_metric("payload_bytes", len(payload_json.encode("utf-8")))
        send_persistent = getattr(self._active_session_binding, "send_command", None)
        if callable(send_persistent):
            persistent_started = time.perf_counter()
            try:
                result = send_persistent(
                    websocket_url,
                    "Runtime.evaluate",
                    _runtime_expression_params(expression),
                    self.update_timeout_seconds,
                )
                persistent_ms = (time.perf_counter() - persistent_started) * 1000.0
                transport = "active-session-binding"
            except Exception as exc:
                persistent_ms = (time.perf_counter() - persistent_started) * 1000.0
                persistent_fallback_reason = f"{type(exc).__name__}: {exc}"
                transport = "active-session-binding"
                result = {"result": {"result": {"value": {"ok": False}}}}
            else:
                persistent_ok, _persistent_apply_ms = self._update_acknowledgement(
                    result
                )
                if not persistent_ok:
                    # The persistent binding is useful for low-latency updates,
                    # but a valid response without an acknowledgement can also
                    # come from a stale CDP execution context.  Verify once on
                    # a fresh websocket before declaring the HUD document lost
                    # and reinstalling the entire surface.
                    persistent_verification = "ephemeral-unacknowledged"
                    verification_started = time.perf_counter()
                    self.record_renderer_metric("cdp_commands")
                    try:
                        result = send_cdp_command(
                            websocket_url,
                            "Runtime.evaluate",
                            _runtime_expression_params(expression),
                            self.update_timeout_seconds,
                        )
                        fallback_ms = (
                            time.perf_counter() - verification_started
                        ) * 1000.0
                        verified_ok, _verified_apply_ms = (
                            self._update_acknowledgement(result)
                        )
                        if verified_ok:
                            transport = "active-session-binding-verified"
                            persistent_verification = "ephemeral-acknowledged"
                    except Exception as exc:
                        fallback_ms = (
                            time.perf_counter() - verification_started
                        ) * 1000.0
                        persistent_verification = "ephemeral-error"
                        persistent_verification_error = (
                            f"{type(exc).__name__}: {exc}"
                        )
        else:
            result = send_cdp_command(
                websocket_url,
                "Runtime.evaluate",
                _runtime_expression_params(expression),
                self.update_timeout_seconds,
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        ok, renderer_apply_ms = self._update_acknowledgement(result)
        domains_value = payload.get("payloadDomains")
        payload_domains = (
            sorted(str(key) for key in domains_value)
            if isinstance(domains_value, dict)
            else []
        )
        log_threshold = float(SLOW_RENDERER_UPDATE_LOG_MS)
        renderer_alive_probe: str | None = None
        renderer_alive_probe_ms: float | None = None
        if not ok:
            # The payload update failed. Probe whether the renderer main
            # thread is still responsive with a trivial Runtime.evaluate.
            # "alive" means the renderer is running and the HUD payload JS
            # itself is what is stuck; "no-ack"/"error:*" means the renderer
            # main thread is wedged -- the long-lock blank-UI failure mode
            # documented in renderer_connection.maybe_escalate_renderer_hung.
            probe_started = time.perf_counter()
            try:
                probe_result = send_cdp_command(
                    websocket_url,
                    "Runtime.evaluate",
                    _runtime_expression_params("1+1"),
                    max(0.35, min(1.0, self.update_timeout_seconds)),
                )
                renderer_alive_probe_ms = (
                    time.perf_counter() - probe_started
                ) * 1000.0
                probe_ok, _probe_apply_ms = self._update_acknowledgement(
                    probe_result
                )
                renderer_alive_probe = "alive" if probe_ok else "no-ack"
            except Exception as exc:
                renderer_alive_probe_ms = (
                    time.perf_counter() - probe_started
                ) * 1000.0
                renderer_alive_probe = f"error:{type(exc).__name__}"
        self.last_update_metrics = {
            "cdpMs": elapsed_ms,
            "rendererApplyMs": renderer_apply_ms,
            "rendererAliveProbe": renderer_alive_probe,
            "rendererAliveProbeMs": renderer_alive_probe_ms,
            "payloadBytes": len(payload_json.encode("utf-8")),
            "payloadDomains": payload_domains,
            "transport": transport,
            "persistentMs": persistent_ms,
            "persistentFallbackReason": persistent_fallback_reason,
            "fallbackMs": fallback_ms,
            "persistentVerification": persistent_verification,
            "persistentVerificationError": persistent_verification_error,
            "attribution": (
                "hud_dom"
                if renderer_apply_ms is not None and renderer_apply_ms >= log_threshold
                else (
                    "codex_renderer_or_cdp"
                    if elapsed_ms >= log_threshold
                    else "normal"
                )
            ),
        }
        slow_session_switch = (
            "sessionSwitch" in payload_domains and elapsed_ms >= 150.0
        )
        if slow_session_switch or elapsed_ms >= log_threshold or (
            renderer_apply_ms is not None and renderer_apply_ms >= log_threshold
        ):
            _LOGGER.info(
                "renderer_update_timing attribution=%s transport=%s cdp_ms=%.1f persistent_ms=%s fallback_ms=%s fallback_reason=%s verification=%s verification_error=%s renderer_apply_ms=%s payload_bytes=%s domains=%s ok=%s",
                self.last_update_metrics["attribution"],
                transport,
                elapsed_ms,
                f"{persistent_ms:.1f}" if persistent_ms is not None else "-",
                f"{fallback_ms:.1f}" if fallback_ms is not None else "-",
                persistent_fallback_reason or "-",
                persistent_verification,
                persistent_verification_error or "-",
                f"{renderer_apply_ms:.1f}" if renderer_apply_ms is not None else "-",
                self.last_update_metrics["payloadBytes"],
                ",".join(payload_domains),
                ok,
            )
        return ok

    @staticmethod
    def _update_acknowledgement(result: object) -> tuple[bool, float | None]:
        if not isinstance(result, dict):
            return False, None
        result_payload = result.get("result")
        if not isinstance(result_payload, dict):
            return False, None
        evaluation_result = result_payload.get("result")
        if not isinstance(evaluation_result, dict):
            return False, None
        value = evaluation_result.get("value", False)
        if not isinstance(value, dict):
            return bool(value), None
        try:
            renderer_apply_ms = float(value.get("applyMs"))
        except (TypeError, ValueError):
            renderer_apply_ms = None
        return bool(value.get("ok", False)), renderer_apply_ms


def wait_for_renderer(
    client: RendererHudClient,
    snapshot_factory: Any,
    *,
    timeout_seconds: float,
    progress_callback: Any = None,
    retry_until_ready: bool = False,
    retry_interval_seconds: float = RENDERER_STARTUP_RETRY_INTERVAL_SECONDS,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> bool:
    """Attach once, or use a bounded cold-start retry when explicitly asked."""
    clock = monotonic or time.monotonic
    wait = sleep or time.sleep
    if callable(progress_callback) and not retry_until_ready:
        try:
            progress_callback("reading_session")
        except Exception:
            pass
    started = time.perf_counter()
    snapshot_started = time.perf_counter()
    snapshot = snapshot_factory()
    snapshot_ms = (time.perf_counter() - snapshot_started) * 1000.0
    if callable(progress_callback) and not retry_until_ready:
        try:
            progress_callback("showing_hud")
        except Exception:
            pass
    update_ms = 0.0
    attempts = 0
    attached = False
    deadline = clock() + max(0.0, float(timeout_seconds))
    while True:
        attempts += 1
        attempt_started = time.perf_counter()
        if retry_until_ready:
            update_startup = getattr(client, "update_startup", None)
            if callable(update_startup):
                attached = bool(update_startup(snapshot))
            else:
                attached = bool(client.update(snapshot))
        else:
            attached = bool(client.update(snapshot))
        update_ms += (time.perf_counter() - attempt_started) * 1000.0
        if attached or not retry_until_ready:
            break
        if str(getattr(client, "last_status", "") or "") == "disabled":
            break
        remaining = deadline - clock()
        if remaining <= 0.0:
            break
        wait(min(max(0.01, float(retry_interval_seconds)), remaining))
    metrics = {
        "totalMs": (time.perf_counter() - started) * 1000.0,
        "snapshotBuildMs": snapshot_ms,
        "hudUpdateMs": update_ms,
        "attempts": attempts,
        "retryUntilReady": bool(retry_until_ready),
        "update": dict(getattr(client, "last_update_metrics", {}) or {}),
    }
    client.last_attach_metrics = metrics
    if metrics["totalMs"] >= float(SLOW_RENDERER_UPDATE_LOG_MS):
        update_metrics = metrics["update"]
        attribution = (
            "python_snapshot"
            if snapshot_ms >= update_ms
            else str(update_metrics.get("attribution") or "hud_or_cdp")
        )
        _LOGGER.info(
            "renderer_attach_timing attribution=%s total_ms=%.1f snapshot_ms=%.1f hud_update_ms=%.1f cdp_ms=%s renderer_apply_ms=%s",
            attribution,
            metrics["totalMs"],
            snapshot_ms,
            update_ms,
            update_metrics.get("cdpMs", "-"),
            update_metrics.get("rendererApplyMs", "-"),
        )
    return attached


def remove_renderer_hud_from_pages(
    *,
    port: int | None = None,
    timeout_seconds: float = DEFAULT_RENDERER_TIMEOUT_SECONDS,
) -> int:
    """Best-effort cleanup for renderer HUD DOM left in any Codex page target."""
    removed = 0
    try:
        targets = list_targets(int(port or cdp_port_from_env()), timeout_seconds)
    except Exception:
        return 0
    for target in targets:
        if target.get("type") != "page":
            continue
        websocket_url = str(target.get("webSocketDebuggerUrl") or "")
        if not websocket_url:
            continue
        try:
            result = send_cdp_command(
                websocket_url,
                "Runtime.evaluate",
                _runtime_expression_params(
                    REMOVE_RENDERER_HUD_SCRIPT
                ),
                timeout_seconds,
            )
        except Exception:
            continue
        if bool(
            result.get("result", {})
            .get("result", {})
            .get("value", False)
        ):
            removed += 1
    return removed


__all__ = [
    "RendererHudClient",
    "remove_renderer_hud_from_pages",
    "wait_for_renderer",
]
