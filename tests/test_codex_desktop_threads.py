from __future__ import annotations

from unittest.mock import MagicMock
import json
import shutil
import subprocess

import pytest

from codex_usage_hud.platforms.codex_desktop_threads import (
    CodexDesktopThreadLifecycle,
    CodexDesktopThreadLifecycleError,
    DesktopThreadLifecycleReport,
    _desktop_thread_lifecycle_script,
    _desktop_thread_preflight_script,
)


SOURCE_ID = "10000000-0000-4000-8000-000000000001"


def _cdp_value(value: object) -> dict[str, object]:
    return {"result": {"result": {"value": value}}}


def _lifecycle(command_sender: object) -> CodexDesktopThreadLifecycle:
    return CodexDesktopThreadLifecycle(
        port=55545,
        enabled=True,
        target_lister=lambda _port, _timeout: [
            {"webSocketDebuggerUrl": "ws://127.0.0.1:55545/devtools/page/main"}
        ],
        target_picker=lambda targets: targets[0],
        command_sender=command_sender,  # type: ignore[arg-type]
    )


def test_lifecycle_script_uses_desktop_app_server_and_requires_notifications() -> None:
    script = _desktop_thread_lifecycle_script(
        SOURCE_ID,
        "E:/project",
        timeout_ms=2500,
    )

    assert 'type: "mcp-request"' in script
    assert 'type: "archive-thread"' in script
    assert '"thread/archive"' in script
    assert '"thread/delete"' in script
    assert '"thread/archived"' in script
    assert '"thread/deleted"' in script
    assert 'window.addEventListener("message", onMessage)' in script
    assert 'window.removeEventListener("message", onMessage)' in script
    assert "persisted-atom-update" not in script
    assert "state_5.sqlite" not in script
    assert SOURCE_ID in script


@pytest.mark.parametrize("mode", ["active", "orphan", "subscribed-elsewhere", "still-active", "turn-completed", "already-archived", "changed-provider"])
def test_forced_lifecycle_stops_writers_before_deletion(mode: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed to exercise the Desktop protocol")
    script = _desktop_thread_lifecycle_script(
        SOURCE_ID,
        "E:/project",
        timeout_ms=500,
        force=True,
        missing_rollout=mode == "orphan",
        already_archived=mode == "already-archived",
        provider="token-x",
    )
    probe = r"""
const vm = require("node:vm");
const fs = require("node:fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const calls = [];
const listeners = new Set();
let status = ["orphan", "already-archived"].includes(input.mode) ? "notLoaded" : "active";
const emit = (data) => listeners.forEach((listener) => listener({data: {hostId: "local", ...data}}));
const notify = (method) => emit({type: "mcp-notification", method, params: {threadId: input.id}});
const window = {
  setTimeout, clearTimeout,
  addEventListener: (_name, listener) => listeners.add(listener),
  removeEventListener: (_name, listener) => listeners.delete(listener),
  electronBridge: {sendMessageFromView: (message) => {
    if (message.type !== "mcp-request") return Promise.resolve();
    const {id, method} = message.request;
    calls.push(method);
    let result = {};
    let error;
    if (method === "thread/read") {
      if (input.mode === "orphan") error = {message: "rollout not found"};
      else result = {thread: {id: input.id, status: {type: status}, modelProvider: input.mode === "changed-provider" ? "other" : "token-x"}};
    }
    if (method === "thread/turns/list") result = {data: [{id: "turn-1", status: input.mode === "turn-completed" ? "completed" : "inProgress"}]};
    if (method === "turn/interrupt") status = input.mode === "still-active" ? "active" : "idle";
    if (method === "thread/unsubscribe") {
      result = {status: ["orphan", "already-archived"].includes(input.mode) ? "notLoaded" : ["subscribed-elsewhere", "still-active"].includes(input.mode) ? "notSubscribed" : "unsubscribed"};
      if (result.status === "unsubscribed") { status = "notLoaded"; notify("thread/closed"); }
    }
    if (method === "thread/archive") {
      if (input.mode === "orphan") error = {message: "rollout not found"};
      else notify("thread/archived");
    }
    if (method === "thread/delete") notify("thread/deleted");
    emit({type: "mcp-response", message: {id, result, error}});
    return Promise.resolve();
  }},
};
vm.runInNewContext(input.script, {window, Date, Map, Promise, crypto: require("node:crypto").webcrypto})
  .then(result => console.log(JSON.stringify({result, calls, listeners: listeners.size})));
"""
    completed = subprocess.run(
        [node, "-e", probe],
        input=json.dumps({"script": script, "mode": mode, "id": SOURCE_ID}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=5,
    )
    observed = json.loads(completed.stdout)
    assert observed["listeners"] == 0
    if mode == "active":
        assert observed["result"]["deleted"] is True
        assert observed["calls"] == [
            "thread/read",
            "thread/turns/list",
            "turn/interrupt",
            "thread/unsubscribe",
            "thread/archive",
            "thread/delete",
        ]
    elif mode == "orphan":
        assert observed["result"]["quiesced"] is True
        assert observed["result"]["localOnly"] is True
    elif mode == "still-active":
        assert observed["result"]["error"] == "thread-still-loaded"
        assert "thread/archive" not in observed["calls"]
    elif mode == "already-archived":
        assert observed["result"]["deleted"] is True
        assert observed["calls"] == ["thread/read", "thread/unsubscribe", "thread/delete"]
    elif mode == "changed-provider":
        assert observed["result"]["error"] == "thread-provider-changed"
        assert observed["calls"] == ["thread/read"]
    else:
        assert observed["result"]["deleted"] is True


def test_forced_lifecycle_accepts_deleted_notification_for_previously_archived_thread() -> None:
    sender = MagicMock(return_value=_cdp_value({
        "threadId": SOURCE_ID, "archived": True, "deleted": True,
        "archiveNotification": False, "deleteNotification": True,
    }))
    report = _lifecycle(sender).archive_then_delete(SOURCE_ID, force=True, already_archived=True)
    assert report.verified
    assert not report.archive_notification


def test_preflight_script_reads_all_source_rollouts_without_mutation() -> None:
    script = _desktop_thread_preflight_script(
        [SOURCE_ID],
        "E:/project",
        timeout_ms=2500,
    )

    assert '"thread/read"' in script
    assert 'includeTurns: false' in script
    assert 'source: "hud-session-migration-preflight"' in script
    assert '"thread/archive"' not in script
    assert '"thread/delete"' not in script
    assert SOURCE_ID in script


def test_preflight_reports_a_verified_source_family() -> None:
    sender = MagicMock(
        return_value=_cdp_value(
            {
                "ok": True,
                "verified": True,
                "threadIds": [SOURCE_ID],
                "error": "",
            }
        )
    )

    result = _lifecycle(sender).preflight([SOURCE_ID], cwd="E:/project")

    assert result["verified"] is True
    assert result["threadIds"] == [SOURCE_ID]
    params = sender.call_args.args[2]
    assert params["awaitPromise"] is True
    assert '"thread/read"' in params["expression"]


def test_lifecycle_reports_a_fully_verified_desktop_delete() -> None:
    sender = MagicMock(
        return_value=_cdp_value(
            {
                "ok": True,
                "threadId": SOURCE_ID,
                "archived": True,
                "deleted": True,
                "archiveNotification": True,
                "deleteNotification": True,
                "error": "",
            }
        )
    )

    report = _lifecycle(sender).archive_then_delete(SOURCE_ID, cwd="E:/project")

    assert report == DesktopThreadLifecycleReport(
        thread_id=SOURCE_ID,
        archived=True,
        deleted=True,
        archive_notification=True,
        delete_notification=True,
    )
    assert report.verified
    params = sender.call_args.args[2]
    assert params["awaitPromise"] is True
    assert 'type: "mcp-request"' in params["expression"]


def test_lifecycle_preserves_an_archived_source_when_delete_is_not_confirmed() -> None:
    sender = MagicMock(
        return_value=_cdp_value(
            {
                "ok": False,
                "threadId": SOURCE_ID,
                "archived": True,
                "deleted": False,
                "archiveNotification": True,
                "deleteNotification": False,
                "error": "desktop-delete-failed",
            }
        )
    )

    report = _lifecycle(sender).archive_then_delete(SOURCE_ID)

    assert report.archived
    assert not report.deleted
    assert not report.verified
    assert report.error == "desktop-delete-failed"


def test_lifecycle_rejects_a_mismatched_desktop_result() -> None:
    sender = MagicMock(
        return_value=_cdp_value(
            {
                "threadId": "10000000-0000-4000-8000-000000000002",
                "archived": True,
                "deleted": True,
                "archiveNotification": True,
                "deleteNotification": True,
            }
        )
    )

    with pytest.raises(CodexDesktopThreadLifecycleError, match="不匹配"):
        _lifecycle(sender).archive_then_delete(SOURCE_ID)
