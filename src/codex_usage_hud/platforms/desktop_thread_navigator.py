"""Route-level navigation to arbitrary Codex Desktop threads.

Codex Desktop keeps its own local thread catalog, but the sidebar only renders
a small window of it.  Clicking sidebar rows therefore cannot reach an old
thread.  This adapter instead sends the same ``electronBridge`` message the
product already uses for its Desktop-owned lifecycle
(:mod:`codex_usage_hud.platforms.codex_desktop_threads`) and asks the main
process to navigate to ``/local/<threadId>`` directly.

The flow is deliberately:

1. ``thread/read`` preflight — proves Desktop still owns the rollout.  Without
   it an unknown id navigates to a blank conversation instead of failing.
2. ``open-in-main-window`` with the route path.
3. Verification against the conversation composer's exact thread identity or
   the sidebar's active row. Archived threads may have no sidebar row.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import uuid

from .cdp_probe import (
    cdp_enabled_from_env,
    cdp_port_from_env,
    list_targets,
    pick_page_target,
    runtime_evaluate_params,
    send_cdp_command,
)


_DEFAULT_PREFLIGHT_TIMEOUT_SECONDS = 6.0
_DEFAULT_VERIFY_TIMEOUT_SECONDS = 4.0

class DesktopThreadNavigationError(RuntimeError):
    """Raised when Desktop cannot be asked to open a thread at all."""


def _canonical_uuid(value: object) -> str:
    candidate = str(value or "").strip()
    try:
        canonical = str(uuid.UUID(candidate))
    except (AttributeError, TypeError, ValueError):
        return ""
    return canonical if candidate.casefold() == canonical else ""


def _route_candidates(client_kind: object) -> list[str]:
    """The local thread/read preflight authorizes only the local route."""
    return ["/local/"]


def _navigation_script(
    thread_id: str,
    routes: Sequence[str],
    *,
    preflight_timeout_ms: int,
    verify_timeout_ms: int,
) -> str:
    """Build one preflight-then-navigate-then-verify transaction."""
    payload = json.dumps(
        {
            "threadId": thread_id,
            "routes": [str(route) for route in routes],
        },
        ensure_ascii=False,
    )
    return f"""
(async () => {{
  const input = {payload};
  const preflightTimeoutMs = {max(500, int(preflight_timeout_ms))};
  const verifyTimeoutMs = {max(500, int(verify_timeout_ms))};
  const bridge = window.electronBridge;
  if (!bridge || typeof bridge.sendMessageFromView !== "function") {{
    return {{ ok: false, threadId: input.threadId, error: "desktop-bridge-unavailable" }};
  }}
  const send = bridge.sendMessageFromView.bind(bridge);
  const pending = new Map();
  const messageText = (value) => {{
    if (typeof value === "string") return value;
    if (value && typeof value === "object") {{
      return String(value.message || value.detail || value.error || "");
    }}
    return "";
  }};
  const onMessage = (event) => {{
    const data = event?.data;
    if (data?.hostId !== "local" || data.type !== "mcp-response") return;
    const id = data.message?.id;
    const waiter = pending.get(id);
    if (!waiter) return;
    pending.delete(id);
    window.clearTimeout(waiter.timer);
    const error = messageText(data.message?.error);
    waiter.resolve({{ ok: !error, error, result: data.message?.result }});
  }};
  window.addEventListener("message", onMessage);
  const request = (method, params, timeoutMs) => new Promise((resolve) => {{
    const id = `hud-desktop-nav-${{crypto.randomUUID()}}`;
    const timer = window.setTimeout(() => {{
      if (pending.delete(id)) resolve({{ ok: false, error: "desktop-response-timeout" }});
    }}, timeoutMs);
    pending.set(id, {{ resolve, timer }});
    Promise.resolve(send({{
      type: "mcp-request",
      hostId: "local",
      request: {{ id, method, params }},
      priority: "critical",
      source: "hud-session-navigation",
      timeoutMs,
      expiresAtMs: Date.now() + timeoutMs,
    }})).catch((error) => {{
      const waiter = pending.get(id);
      if (!waiter) return;
      pending.delete(id);
      window.clearTimeout(waiter.timer);
      resolve({{ ok: false, error: `desktop-send-failed: ${{String(error)}}` }});
    }});
  }});
  const rowIsActive = (row) => (
    row.getAttribute("data-app-action-sidebar-thread-active") === "true"
    || row.getAttribute("aria-current") === "page"
    || row.getAttribute("aria-current") === "true"
    || row.getAttribute("aria-selected") === "true"
  );
  const activeRowFor = (hostId) => {{
    if (hostId === "local") {{
      const composers = document.querySelectorAll("[data-above-composer-conversation-id]");
      for (const composer of composers) {{
        if (composer.closest("#codex-usage-hud-root")) continue;
        if (composer.getAttribute("data-above-composer-conversation-id") === input.threadId) {{
          return true;
        }}
      }}
    }}
    const rows = document.querySelectorAll(
      `[data-app-action-sidebar-thread-id="${{hostId}}:${{input.threadId}}"]`
    );
    for (const row of rows) {{
      if (rowIsActive(row)) return true;
    }}
    return false;
  }};
  const waitForActive = (hostId) => new Promise((resolve) => {{
    if (activeRowFor(hostId)) return resolve(true);
    const observer = new MutationObserver(() => {{
      if (activeRowFor(hostId)) finish(true);
    }});
    const finish = (verified) => {{
      observer.disconnect();
      window.clearTimeout(timer);
      resolve(verified);
    }};
    const timer = window.setTimeout(() => finish(activeRowFor(hostId)), verifyTimeoutMs);
    observer.observe(document.documentElement, {{
      subtree: true, childList: true, attributes: true,
      attributeFilter: ['data-above-composer-conversation-id', 'data-app-action-sidebar-thread-active', 'aria-current', 'aria-selected'],
    }});
    if (activeRowFor(hostId)) finish(true);
  }});
  try {{
    // 1. Prove Desktop still owns this rollout.  Navigating to an id it has
    // never loaded lands on a blank conversation instead of erroring.
    const read = await request(
      "thread/read",
      {{ threadId: input.threadId, includeTurns: false }},
      preflightTimeoutMs,
    );
    if (!read.ok) {{
      return {{
        ok: false,
        threadId: input.threadId,
        verified: false,
        error: /desktop-response-timeout|desktop-send-failed/.test(read.error)
          ? "desktop-response-timeout" : "thread-not-owned",
        detail: read.error,
      }};
    }}
    const thread = read.result?.thread;
    if (!thread || String(thread.id || "").toLowerCase() !== input.threadId.toLowerCase()) {{
      return {{
        ok: false,
        threadId: input.threadId,
        verified: false,
        error: "thread-not-owned",
        detail: "thread-id-mismatch",
      }};
    }}
    // 2. Route only on the host verified by thread/read. A slow sidebar is
    // not evidence that the thread belongs to a different host.
    const attempted = [];
    for (const route of input.routes) {{
      const hostId = route.replace(/^\\//, "").replace(/\\/$/, "");
      const path = `${{route}}${{input.threadId}}?hostId=${{hostId}}`;
      let sendError = "";
      try {{
        await send({{
          type: "open-in-main-window",
          path,
          appEntryAttribution: {{ channel: "hud", source: "hud" }},
        }});
      }} catch (error) {{
        sendError = String(error);
      }}
      const verified = sendError ? false : await waitForActive(hostId);
      attempted.push({{ route, hostId, verified, sendError }});
      if (verified) {{
        return {{
          ok: true,
          threadId: input.threadId,
          verified: true,
          route,
          attempted,
          error: "",
        }};
      }}
    }}
    return {{
      ok: false,
      threadId: input.threadId,
      verified: false,
      error: "navigation-unverified",
      detail: attempted.map((item) => item.sendError || `${{item.route}}:not-active`).join("; "),
      attempted,
    }};
  }} finally {{
    for (const waiter of pending.values()) {{
      window.clearTimeout(waiter.timer);
      waiter.resolve({{ ok: false, error: "desktop-navigation-cancelled" }});
    }}
    pending.clear();
    window.removeEventListener("message", onMessage);
  }}
}})()
"""


@dataclass(frozen=True)
class DesktopThreadNavigationReport:
    """Result of one Desktop route navigation attempt."""

    thread_id: str
    verified: bool = False
    route: str = ""
    error: str = ""
    detail: str = ""

    @property
    def not_owned(self) -> bool:
        """Desktop no longer holds this thread (the caller should grey it out)."""
        return self.error == "thread-not-owned"

    def to_payload(self) -> dict[str, object]:
        return {
            "threadId": self.thread_id,
            "verified": self.verified,
            "route": self.route,
            "error": self.error,
            "detail": self.detail,
        }


class DesktopThreadNavigator:
    """Open an arbitrary Desktop thread through its main-process router."""

    def __init__(
        self,
        *,
        port: int | None = None,
        preflight_timeout_seconds: float = _DEFAULT_PREFLIGHT_TIMEOUT_SECONDS,
        verify_timeout_seconds: float = _DEFAULT_VERIFY_TIMEOUT_SECONDS,
        enabled: bool | None = None,
        target_lister: Callable[[int, float], list[dict[str, object]]] = list_targets,
        target_picker: Callable[
            [list[dict[str, object]]], dict[str, object]
        ] = pick_page_target,
        command_sender: Callable[
            [str, str, Mapping[str, object], float], dict[str, object]
        ] = send_cdp_command,
    ) -> None:
        self.port = int(port if port is not None else cdp_port_from_env())
        self.preflight_timeout_seconds = max(1.0, float(preflight_timeout_seconds))
        self.verify_timeout_seconds = max(0.5, float(verify_timeout_seconds))
        self.enabled = cdp_enabled_from_env() if enabled is None else bool(enabled)
        self._target_lister = target_lister
        self._target_picker = target_picker
        self._command_sender = command_sender

    def _evaluate(self, expression: str) -> Mapping[str, object]:
        timeout = (
            self.preflight_timeout_seconds
            + self.verify_timeout_seconds * 3.0
            + 2.0
        )
        try:
            target = self._target_picker(
                self._target_lister(self.port, self.preflight_timeout_seconds)
            )
            websocket_url = str(target.get("webSocketDebuggerUrl") or "").strip()
            if not websocket_url:
                raise RuntimeError("Codex Desktop 没有可用的 CDP 页面目标。")
            result = self._command_sender(
                websocket_url,
                "Runtime.evaluate",
                runtime_evaluate_params(
                    expression,
                    await_promise=True,
                ),
                timeout,
            )
        except DesktopThreadNavigationError:
            raise
        except Exception as exc:
            raise DesktopThreadNavigationError(
                f"Codex Desktop 会话跳转通道不可用：{type(exc).__name__}。"
            ) from exc
        value = (
            result.get("result", {}).get("result", {}).get("value")
            if isinstance(result, Mapping)
            else None
        )
        if not isinstance(value, Mapping):
            raise DesktopThreadNavigationError(
                "Codex Desktop 会话跳转通道返回了无效结果。"
            )
        return value

    def navigate(
        self,
        thread_id: object,
        *,
        client_kind: object = "",
    ) -> DesktopThreadNavigationReport:
        """Open one Desktop thread, verifying the sidebar followed the route."""
        normalized_id = _canonical_uuid(thread_id)
        if not normalized_id:
            raise DesktopThreadNavigationError("Codex Desktop 会话标识无效。")
        if not self.enabled:
            raise DesktopThreadNavigationError("Codex Desktop CDP 当前不可用。")
        value = self._evaluate(
            _navigation_script(
                normalized_id,
                _route_candidates(client_kind),
                preflight_timeout_ms=max(
                    500, int(self.preflight_timeout_seconds * 1000)
                ),
                verify_timeout_ms=max(500, int(self.verify_timeout_seconds * 1000)),
            )
        )
        reported_id = _canonical_uuid(value.get("threadId"))
        if reported_id != normalized_id:
            raise DesktopThreadNavigationError(
                "Codex Desktop 会话跳转通道返回了不匹配的会话标识。"
            )
        return DesktopThreadNavigationReport(
            thread_id=reported_id,
            verified=bool(value.get("verified")),
            route=str(value.get("route") or "").strip(),
            error=str(value.get("error") or "").strip(),
            detail=str(value.get("detail") or "").strip(),
        )


__all__ = [
    "DesktopThreadNavigationError",
    "DesktopThreadNavigationReport",
    "DesktopThreadNavigator",
]
