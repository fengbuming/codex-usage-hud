"""Desktop-owned archive/delete lifecycle for migrated Codex threads.

Codex Desktop keeps a local thread catalog in addition to the CLI rollout and
state-db files.  Deleting those files from another process can therefore leave
an unresumable sidebar entry.  This adapter sends the same App Server requests
through the already-running Desktop process and requires the corresponding
Desktop notifications before reporting a source as deleted.
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


_DEFAULT_TIMEOUT_SECONDS = 8.0


class CodexDesktopThreadLifecycleError(RuntimeError):
    """Raised when Desktop cannot prove a thread lifecycle transition."""


def _canonical_uuid(value: object) -> str:
    candidate = str(value or "").strip()
    try:
        canonical = str(uuid.UUID(candidate))
    except (AttributeError, TypeError, ValueError):
        return ""
    return canonical if candidate.casefold() == canonical else ""


def _desktop_thread_lifecycle_script(
    thread_id: str,
    cwd: str,
    *,
    timeout_ms: int,
    force: bool = False,
    missing_rollout: bool = False,
    already_archived: bool = False,
    provider: str = "",
) -> str:
    """Build one Desktop-owned archive-then-delete transaction.

    ``mcp-request`` is the renderer-to-main-process route used by Desktop's
    own App Server client.  The script deliberately waits for
    ``thread/archived`` and ``thread/deleted`` notifications, because those
    are what make Desktop update its separate local thread catalog.
    """
    payload = json.dumps(
        {
            "threadId": thread_id,
            "cwd": str(cwd or "").strip() or "/",
            "force": bool(force),
            "missingRollout": bool(missing_rollout),
            "alreadyArchived": bool(already_archived),
            "provider": str(provider or "").strip().casefold(),
        },
        ensure_ascii=False,
    )
    return f"""
(async () => {{
  const input = {payload};
  const timeoutMs = {max(500, int(timeout_ms))};
  const bridge = window.electronBridge;
  if (!bridge || typeof bridge.sendMessageFromView !== "function") {{
    return {{ ok: false, threadId: input.threadId, error: "desktop-bridge-unavailable" }};
  }}
  const notifications = {{ archived: false, deleted: false, closed: false }};
  let quiesced = false;
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
    if (data?.hostId !== "local") return;
    if (data.type === "mcp-notification") {{
      const threadId = data.params?.threadId;
      if (threadId !== input.threadId) return;
      if (data.method === "thread/archived") notifications.archived = true;
      if (data.method === "thread/deleted") notifications.deleted = true;
      if (data.method === "thread/closed") notifications.closed = true;
      return;
    }}
    if (data.type !== "mcp-response") return;
    const id = data.message?.id;
    const waiter = pending.get(id);
    if (!waiter) return;
    pending.delete(id);
    window.clearTimeout(waiter.timer);
    const error = messageText(data.message?.error);
    waiter.resolve({{ ok: !error, error, result: data.message?.result }});
  }};
  window.addEventListener("message", onMessage);
  const waitForNotification = async (name) => {{
    const deadline = Date.now() + timeoutMs;
    while (!notifications[name] && Date.now() < deadline) {{
      await new Promise((resolve) => window.setTimeout(resolve, 25));
    }}
    return notifications[name];
  }};
  const request = (method, params) => new Promise((resolve) => {{
    const id = `hud-desktop-thread-${{method.replace(/[^a-z]/g, "-")}}-${{crypto.randomUUID()}}`;
    const timer = window.setTimeout(() => {{
      if (pending.delete(id)) resolve({{ ok: false, error: "desktop-response-timeout" }});
    }}, timeoutMs);
    pending.set(id, {{ resolve, timer }});
    Promise.resolve(bridge.sendMessageFromView({{
      type: "mcp-request",
      hostId: "local",
      request: {{ id, method, params }},
      priority: "critical",
      source: "hud-session-migration",
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
  try {{
    if (input.force) {{
      // 供应商强制删除先中断当前轮次并卸载会话，确认写入方退出后才删除文件。
      const read = await request("thread/read", {{ threadId: input.threadId, includeTurns: false }});
      if (!read.ok && !input.missingRollout && !input.alreadyArchived) {{
        return {{ ok: false, threadId: input.threadId, error: read.error }};
      }}
      if (read.ok && input.provider && read.result?.thread?.modelProvider
        && String(read.result.thread.modelProvider).trim().toLowerCase() !== input.provider) {{
        return {{ ok: false, threadId: input.threadId, error: "thread-provider-changed" }};
      }}
      if (read.result?.thread?.status?.type === "active") {{
        const turns = await request("thread/turns/list", {{
          threadId: input.threadId, sortDirection: "desc", limit: 1, itemsView: "notLoaded",
        }});
        const turn = turns.result?.data?.find((value) => value.status === "inProgress");
        // 轮次可能在查询期间结束；仍继续卸载，由关闭通知确认写入已停止。
        if (turns.ok && turn?.id) {{
          await request("turn/interrupt", {{ threadId: input.threadId, turnId: turn.id }});
        }}
      }}
      const unload = await request("thread/unsubscribe", {{ threadId: input.threadId }});
      if (!unload.ok) {{
        return {{ ok: false, threadId: input.threadId, error: unload.error }};
      }}
      quiesced = unload.result?.status === "notLoaded";
      if (!quiesced && unload.result?.status === "unsubscribed") {{
        quiesced = await waitForNotification("closed");
      }}
      if (!quiesced) {{
        const unloaded = await request("thread/read", {{ threadId: input.threadId, includeTurns: false }});
        quiesced = unloaded.ok && unloaded.result?.thread?.status?.type === "notLoaded";
        // 其他连接仍订阅但已空闲时，交给官方归档/删除收尾；活动写入不可本地强删。
        if (!unloaded.ok || unloaded.result?.thread?.status?.type === "active") {{
          return {{ ok: false, threadId: input.threadId, error: "thread-still-loaded" }};
        }}
      }}
    }}
    // This is the companion registration sent by Desktop's archive command.
    // It lets the same App Server connection apply its normal archive side
    // effects, while explicitly avoiding any worktree cleanup.
    await Promise.resolve(bridge.sendMessageFromView({{
      type: "archive-thread",
      hostId: "local",
      conversationId: input.threadId,
      cwd: input.cwd,
      cleanupWorktree: false,
      replacementOwnerThreadId: null,
      replacementOwnerCwd: null,
    }}));
    // 数据库已确认归档的会话直接删除，不能等待不会再次发送的归档通知。
    const archive = input.alreadyArchived
      ? {{ ok: true }}
      : await request("thread/archive", {{ threadId: input.threadId }});
    if (!archive.ok) {{
      // 文件已丢失的孤立记录无法归档；确认卸载后交给调用方清理数据库与索引。
      if (input.force && input.missingRollout && quiesced) {{
        return {{ ok: true, threadId: input.threadId, quiesced: true, localOnly: true, error: "" }};
      }}
      return {{
        ok: false,
        threadId: input.threadId,
        archived: false,
        deleted: false,
        archiveNotification: notifications.archived,
        deleteNotification: notifications.deleted,
        error: archive.error || "desktop-archive-failed",
      }};
    }}
    if (!input.alreadyArchived && !await waitForNotification("archived")) {{
      return {{
        ok: false,
        threadId: input.threadId,
        archived: true,
        deleted: false,
        archiveNotification: false,
        deleteNotification: notifications.deleted,
        error: "desktop-archive-notification-timeout",
      }};
    }}
    const deletion = await request("thread/delete", {{ threadId: input.threadId }});
    if (!deletion.ok) {{
      if (input.force && input.missingRollout && quiesced) {{
        return {{ ok: true, threadId: input.threadId, quiesced: true, localOnly: true, error: "" }};
      }}
      return {{
        ok: false,
        threadId: input.threadId,
        archived: true,
        deleted: false,
        archiveNotification: notifications.archived,
        deleteNotification: notifications.deleted,
        error: deletion.error || "desktop-delete-failed",
      }};
    }}
    if (!await waitForNotification("deleted")) {{
      return {{
        ok: false,
        threadId: input.threadId,
        archived: true,
        deleted: true,
        archiveNotification: notifications.archived,
        deleteNotification: false,
        error: "desktop-delete-notification-timeout",
      }};
    }}
    return {{
      ok: true,
      threadId: input.threadId,
      archived: true,
      deleted: true,
      archiveNotification: notifications.archived,
      deleteNotification: true,
      error: "",
      quiesced,
    }};
  }} finally {{
    for (const waiter of pending.values()) {{
      window.clearTimeout(waiter.timer);
      waiter.resolve({{ ok: false, error: "desktop-lifecycle-cancelled" }});
    }}
    pending.clear();
    window.removeEventListener("message", onMessage);
  }}
}})()
"""


def _desktop_thread_preflight_script(
    thread_ids: Sequence[str],
    cwd: str,
    *,
    timeout_ms: int,
) -> str:
    """Build a read-only Desktop rollout preflight for a complete source tree."""
    payload = json.dumps(
        {
            "threadIds": [str(value) for value in thread_ids],
            "cwd": str(cwd or "").strip() or "/",
        },
        ensure_ascii=False,
    )
    return f"""
(async () => {{
  const input = {payload};
  const timeoutMs = {max(500, int(timeout_ms))};
  const bridge = window.electronBridge;
  if (!bridge || typeof bridge.sendMessageFromView !== "function") {{
    return {{ ok: false, verified: false, error: "desktop-bridge-unavailable" }};
  }}
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
  const request = (method, params) => new Promise((resolve) => {{
    const id = `hud-desktop-thread-preflight-${{crypto.randomUUID()}}`;
    const timer = window.setTimeout(() => {{
      if (pending.delete(id)) resolve({{ ok: false, error: "desktop-response-timeout" }});
    }}, timeoutMs);
    pending.set(id, {{ resolve, timer }});
    Promise.resolve(bridge.sendMessageFromView({{
      type: "mcp-request",
      hostId: "local",
      request: {{ id, method, params }},
      priority: "critical",
      source: "hud-session-migration-preflight",
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
  window.addEventListener("message", onMessage);
  try {{
    for (const threadId of input.threadIds) {{
      const response = await request("thread/read", {{
        threadId,
        includeTurns: false,
      }});
      if (!response.ok) {{
        return {{
          ok: false,
          verified: false,
          threadId,
          error: response.error || "desktop-thread-read-failed",
        }};
      }}
      const thread = response.result?.thread;
      if (!thread || String(thread.id || "").toLowerCase() !== String(threadId).toLowerCase()) {{
        return {{
          ok: false,
          verified: false,
          threadId,
          error: "desktop-thread-rollout-unavailable",
        }};
      }}
    }}
    return {{
      ok: true,
      verified: true,
      threadIds: input.threadIds,
      error: "",
    }};
  }} finally {{
    for (const waiter of pending.values()) {{
      window.clearTimeout(waiter.timer);
      waiter.resolve({{ ok: false, error: "desktop-preflight-cancelled" }});
    }}
    pending.clear();
    window.removeEventListener("message", onMessage);
  }}
}})()
"""


@dataclass(frozen=True)
class DesktopThreadLifecycleReport:
    """Verified result for one source thread."""

    thread_id: str
    archived: bool
    deleted: bool
    archive_notification: bool
    delete_notification: bool
    error: str = ""
    quiesced: bool = False
    local_only: bool = False
    already_archived: bool = False

    @property
    def verified(self) -> bool:
        return (
            self.archived
            and self.deleted
            and (self.archive_notification or self.already_archived)
            and self.delete_notification
            and not self.error
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "threadId": self.thread_id,
            "archived": self.archived,
            "deleted": self.deleted,
            "archiveNotification": self.archive_notification,
            "deleteNotification": self.delete_notification,
            "verified": self.verified,
            "error": self.error,
            "quiesced": self.quiesced,
            "localOnly": self.local_only,
            "alreadyArchived": self.already_archived,
        }


class CodexDesktopThreadLifecycle:
    """Invoke the running Desktop's official thread lifecycle through CDP."""

    def __init__(
        self,
        *,
        port: int | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        enabled: bool | None = None,
        target_lister: Callable[[int, float], list[dict[str, object]]] = list_targets,
        target_picker: Callable[
            [list[dict[str, object]]], dict[str, object]
        ] = pick_page_target,
        command_sender: Callable[
            [str, str, Mapping[str, object], float], dict[str, object]
        ] = send_cdp_command,
    ) -> None:
        self.port = int(port or cdp_port_from_env())
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.enabled = cdp_enabled_from_env() if enabled is None else bool(enabled)
        self._target_lister = target_lister
        self._target_picker = target_picker
        self._command_sender = command_sender

    def _evaluate(self, expression: str) -> Mapping[str, object]:
        try:
            target = self._target_picker(
                self._target_lister(self.port, self.timeout_seconds)
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
                self.timeout_seconds * 4 + 0.5,
            )
        except CodexDesktopThreadLifecycleError:
            raise
        except Exception as exc:
            raise CodexDesktopThreadLifecycleError(
                f"Codex Desktop 归档/删除通道不可用：{type(exc).__name__}。"
            ) from exc
        value = (
            result.get("result", {}).get("result", {}).get("value")
            if isinstance(result, Mapping)
            else None
        )
        if not isinstance(value, Mapping):
            raise CodexDesktopThreadLifecycleError(
                "Codex Desktop 归档/删除通道返回了无效结果。"
            )
        return value

    def preflight(
        self,
        thread_ids: Sequence[str],
        *,
        cwd: str = "",
    ) -> Mapping[str, object]:
        """Read each source rollout through Desktop before any deletion."""
        normalized_ids = tuple(
            normalized
            for value in thread_ids
            if (normalized := _canonical_uuid(value))
        )
        if not normalized_ids or len(normalized_ids) != len(thread_ids):
            raise CodexDesktopThreadLifecycleError("Codex Desktop 源会话标识无效。")
        if not self.enabled:
            raise CodexDesktopThreadLifecycleError("Codex Desktop CDP 当前不可用。")
        return self._evaluate(
            _desktop_thread_preflight_script(
                normalized_ids,
                cwd,
                timeout_ms=max(500, int(self.timeout_seconds * 1000) - 250),
            )
        )

    def archive_then_delete(
        self,
        thread_id: str,
        *,
        cwd: str = "",
        force: bool = False,
        missing_rollout: bool = False,
        already_archived: bool = False,
        provider: str = "",
    ) -> DesktopThreadLifecycleReport:
        normalized_id = _canonical_uuid(thread_id)
        if not normalized_id:
            raise CodexDesktopThreadLifecycleError("Codex Desktop 源会话标识无效。")
        if not self.enabled:
            raise CodexDesktopThreadLifecycleError("Codex Desktop CDP 当前不可用。")
        value = self._evaluate(
            _desktop_thread_lifecycle_script(
                normalized_id,
                cwd,
                timeout_ms=max(500, int(self.timeout_seconds * 1000) - 250),
                force=force,
                missing_rollout=missing_rollout,
                already_archived=already_archived,
                provider=provider,
            )
        )
        reported_id = _canonical_uuid(value.get("threadId"))
        if reported_id != normalized_id:
            raise CodexDesktopThreadLifecycleError(
                "Codex Desktop 归档/删除通道返回了不匹配的会话标识。"
            )
        return DesktopThreadLifecycleReport(
            thread_id=reported_id,
            archived=bool(value.get("archived")),
            deleted=bool(value.get("deleted")),
            archive_notification=bool(value.get("archiveNotification")),
            delete_notification=bool(value.get("deleteNotification")),
            error=str(value.get("error") or "").strip(),
            quiesced=bool(value.get("quiesced")),
            local_only=bool(value.get("localOnly")),
            already_archived=already_archived,
        )


__all__ = [
    "CodexDesktopThreadLifecycle",
    "CodexDesktopThreadLifecycleError",
    "DesktopThreadLifecycleReport",
]
