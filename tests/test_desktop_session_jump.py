"""Tests for the per-row Codex Desktop session jump command.

Covers the navigation adapter, the runtime command handler that wires it into
the existing ``openSessionCleanup*`` action namespace, and the inventory
manager's opaque-id resolver via its already-defined public API.

The tests never touch a real Codex Desktop process.  All scripts returned by
the navigator are inspected for ``electronBridge`` usage so future edits cannot
silently regress the protocol.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codex_usage_hud.platforms.desktop_thread_navigator import (
    DesktopThreadNavigationError,
    DesktopThreadNavigationReport,
    DesktopThreadNavigator,
    _canonical_uuid,
    _navigation_script,
    _route_candidates,
)
from codex_usage_hud.runtime_commands import (
    RuntimeCommandPorts,
    handle_cleanup_command,
)


SESSION_ID = "10000000-0000-4000-8000-000000000abc"
SESSION_ID_UPPER = SESSION_ID.upper()
OTHER_SESSION_ID = "20000000-0000-4000-8000-000000000def"
REVISION = "rev-1"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_canonical_uuid_round_trip_lowercases_and_strips_whitespace() -> None:
    assert _canonical_uuid(f"  {SESSION_ID_UPPER}  ") == SESSION_ID
    assert _canonical_uuid("") == ""
    assert _canonical_uuid(None) == ""
    assert _canonical_uuid("not-a-uuid") == ""


def test_route_candidates_orders_caller_kind_first() -> None:
    ordered = _route_candidates("local-thread")
    assert ordered[0] == "/local/"
    assert "/c/" in ordered
    assert "/remote/" in ordered
    # Unknown kind falls back to the canonical order.
    fallback = _route_candidates("")
    assert fallback[0] == "/local/"


def test_navigation_script_uses_electron_bridge_and_local_host() -> None:
    script = _navigation_script(
        SESSION_ID,
        _route_candidates("local"),
        preflight_timeout_ms=2000,
        verify_timeout_ms=1500,
    )
    assert "window.electronBridge" in script
    assert "sendMessageFromView" in script
    assert "thread/read" in script
    assert "open-in-main-window" in script
    assert "/local/" in script
    # Sidebar activation is the only signal we trust, so the script must read
    # the data-app-action attribute the existing CDP probe already targets.
    assert "data-app-action-sidebar-thread-active" in script
    # The message listener must always be removed even on the happy path.
    assert "removeEventListener" in script


# ---------------------------------------------------------------------------
# Navigator adapter
# ---------------------------------------------------------------------------

def _make_navigator(
    *,
    enabled: bool = True,
    evaluate: object = None,
    target_lister=None,
    target_picker=None,
    command_sender=None,
) -> DesktopThreadNavigator:
    return DesktopThreadNavigator(
        port=50697,
        enabled=enabled,
        target_lister=target_lister or (lambda port, timeout: [{"webSocketDebuggerUrl": "ws://localhost/x"}]),
        target_picker=target_picker or (lambda targets: targets[0]),
        command_sender=command_sender
        or (lambda url, method, params, timeout: {"result": {"result": {"value": evaluate}}}),
    )


def test_navigator_rejects_invalid_uuid_before_cdp_call() -> None:
    sender = MagicMock()
    navigator = _make_navigator(command_sender=sender)
    with pytest.raises(DesktopThreadNavigationError):
        navigator.navigate("not-a-uuid")
    sender.assert_not_called()


def test_navigator_reports_unverified_when_route_does_not_activate_sidebar() -> None:
    evaluate = {
        "ok": False,
        "threadId": SESSION_ID,
        "verified": False,
        "error": "navigation-unverified",
        "detail": "/local/:not-active",
    }
    navigator = _make_navigator(evaluate=evaluate)
    report = navigator.navigate(SESSION_ID, client_kind="local-desktop")
    assert isinstance(report, DesktopThreadNavigationReport)
    assert report.thread_id == SESSION_ID
    assert report.verified is False
    assert report.not_owned is False
    assert report.error == "navigation-unverified"
    assert report.detail.endswith("not-active")


def test_navigator_reports_thread_not_owned_when_preflight_fails() -> None:
    evaluate = {
        "ok": False,
        "threadId": SESSION_ID,
        "verified": False,
        "error": "thread-not-owned",
        "detail": "thread-id-mismatch",
    }
    navigator = _make_navigator(evaluate=evaluate)
    report = navigator.navigate(SESSION_ID)
    assert report.not_owned is True
    assert report.error == "thread-not-owned"
    assert report.detail == "thread-id-mismatch"


def test_navigator_marks_verified_when_attempt_returns_true() -> None:
    evaluate = {
        "ok": True,
        "threadId": SESSION_ID,
        "verified": True,
        "route": "/local/",
        "error": "",
    }
    navigator = _make_navigator(evaluate=evaluate)
    report = navigator.navigate(SESSION_ID, client_kind="local")
    assert report.verified is True
    assert report.route == "/local/"
    assert report.error == ""


def test_navigator_short_circuits_when_disabled() -> None:
    sender = MagicMock()
    navigator = _make_navigator(enabled=False, command_sender=sender)
    with pytest.raises(DesktopThreadNavigationError):
        navigator.navigate(SESSION_ID)
    sender.assert_not_called()


def test_navigator_surfaces_cdp_failure() -> None:
    navigator = DesktopThreadNavigator(
        port=50697,
        enabled=True,
        target_lister=lambda port, timeout: [{"webSocketDebuggerUrl": "ws://localhost/x"}],
        target_picker=lambda targets: targets[0],
        command_sender=lambda *args, **kw: (_ for _ in ()).throw(OSError("cdp closed")),
    )
    with pytest.raises(DesktopThreadNavigationError) as exc:
        navigator.navigate(SESSION_ID)
    assert "会话跳转通道不可用" in str(exc.value)


# ---------------------------------------------------------------------------
# Inventory resolver stub
# ---------------------------------------------------------------------------

def _inventory_stub(
    *,
    session_id: str = SESSION_ID,
    client_kind: str = "codex-cli",
    provider: str = "custom",
    cwd: str = r"C:\\Project\\hud-test",
    revision: str = REVISION,
) -> SimpleNamespace:
    return SimpleNamespace(
        session_route_target_for_item=MagicMock(
            return_value={
                "sessionId": session_id,
                "clientKind": client_kind,
                "modelProvider": provider,
                "cwd": cwd,
            }
            if revision
            else None
        ),
        workdir_for_item=MagicMock(return_value=Path(cwd)),
    )


def _build_navigator(report: DesktopThreadNavigationReport | Exception) -> SimpleNamespace:
    fake = SimpleNamespace()
    if isinstance(report, Exception):
        fake.navigate = MagicMock(side_effect=report)
    else:
        fake.navigate = MagicMock(return_value=report)
    return fake


# ---------------------------------------------------------------------------
# Runtime command wiring
# ---------------------------------------------------------------------------

def test_open_session_jump_reports_unresolved_target() -> None:
    from pathlib import Path

    manager = SimpleNamespace(
        session_route_target_for_item=MagicMock(return_value=None),
    )
    navigator = _build_navigator(
        DesktopThreadNavigationReport(thread_id=SESSION_ID, verified=True)
    )
    status = handle_cleanup_command(
        {
            "action": "openSessionCleanupSession",
            "itemId": "opaque-1",
            "inventoryRevision": REVISION,
        },
        RuntimeCommandPorts(
            cleanup_manager=manager,
            desktop_thread_navigator=navigator,
        ),
    )
    assert status["kind"] == "error"
    assert status["sessionCleanupSessionJump"]["error"] == "target-unresolved"
    navigator.navigate.assert_not_called()


def test_open_session_jump_succeeds_when_navigator_verifies() -> None:
    from pathlib import Path

    manager = _inventory_stub()
    navigator = _build_navigator(
        DesktopThreadNavigationReport(
            thread_id=SESSION_ID,
            verified=True,
            route="/local/",
            error="",
            detail="",
        )
    )
    status = handle_cleanup_command(
        {
            "action": "openSessionCleanupSession",
            "itemId": SESSION_ID,
            "inventoryRevision": REVISION,
        },
        RuntimeCommandPorts(
            cleanup_manager=manager,
            desktop_thread_navigator=navigator,
        ),
    )
    assert status["kind"] == ""
    payload = status["sessionCleanupSessionJump"]
    assert payload["verified"] is True
    assert payload["error"] == ""
    assert payload["canResume"] is False
    manager.session_route_target_for_item.assert_called_once_with(SESSION_ID, REVISION)
    navigator.navigate.assert_called_once()
    args, kwargs = navigator.navigate.call_args
    assert args == (SESSION_ID,)
    assert kwargs["client_kind"] == "codex-cli"


def test_open_session_jump_returns_not_owned_error_and_marks_resumable() -> None:
    from pathlib import Path

    manager = _inventory_stub()
    navigator = _build_navigator(
        DesktopThreadNavigationReport(
            thread_id=SESSION_ID,
            verified=False,
            error="thread-not-owned",
            detail="",
        )
    )
    status = handle_cleanup_command(
        {
            "action": "openSessionCleanupSession",
            "itemId": SESSION_ID,
            "inventoryRevision": REVISION,
        },
        RuntimeCommandPorts(
            cleanup_manager=manager,
            desktop_thread_navigator=navigator,
        ),
    )
    assert status["kind"] == "error"
    payload = status["sessionCleanupSessionJump"]
    assert payload["error"] == "thread-not-owned"
    assert payload["canResume"] is True


def test_open_session_jump_translates_navigator_failure() -> None:
    from pathlib import Path

    manager = _inventory_stub()
    navigator = _build_navigator(
        DesktopThreadNavigationError("desktop missing")
    )
    status = handle_cleanup_command(
        {
            "action": "openSessionCleanupSession",
            "itemId": SESSION_ID,
            "inventoryRevision": REVISION,
        },
        RuntimeCommandPorts(
            cleanup_manager=manager,
            desktop_thread_navigator=navigator,
        ),
    )
    assert status["kind"] == "error"
    payload = status["sessionCleanupSessionJump"]
    assert payload["error"] == "navigation-unavailable"
    assert payload["canResume"] is True


@patch("codex_usage_hud.runtime_commands.DesktopThreadNavigator")
def test_open_session_jump_without_injected_navigator_falls_back_to_constructor(constructor) -> None:
    from pathlib import Path

    manager = _inventory_stub()
    constructor.return_value.navigate.side_effect = DesktopThreadNavigationError("desktop missing")
    status = handle_cleanup_command(
        {
            "action": "openSessionCleanupSession",
            "itemId": SESSION_ID,
            "inventoryRevision": REVISION,
        },
        RuntimeCommandPorts(
            cleanup_manager=manager,
            desktop_thread_navigator=None,
        ),
    )
    assert status["kind"] == "error"
    payload = status["sessionCleanupSessionJump"]
    assert payload["error"] in {"navigator-unavailable", "navigation-unavailable"}
    assert payload["canResume"] is True


# ---------------------------------------------------------------------------
# Resume fallback
# ---------------------------------------------------------------------------

def test_resume_session_cleanup_launches_terminal_command() -> None:
    from pathlib import Path

    manager = _inventory_stub()
    ports = RuntimeCommandPorts(cleanup_manager=manager)
    with (
        patch(
            "codex_usage_hud.runtime_commands.discover_codex_cli_options",
            return_value={
                "defaultTerminal": "windows-terminal",
                "defaultProvider": "custom",
                "terminals": [{"id": "windows-terminal", "shell": "cmd"}],
            },
        ),
        patch(
            "codex_usage_hud.runtime_commands.build_codex_cli_command",
            return_value=f"codex resume {SESSION_ID}",
        ) as build,
        patch(
            "codex_usage_hud.runtime_commands.launch_codex_cli",
        ) as launch,
    ):
        status = handle_cleanup_command(
            {
                "action": "resumeSessionCleanupSession",
                "itemId": SESSION_ID,
                "inventoryRevision": REVISION,
            },
            ports,
        )
    assert status["kind"] == ""
    build.assert_called_once()
    kwargs = build.call_args.kwargs
    assert kwargs["resume"] is True
    assert kwargs["resume_session_id"] == SESSION_ID
    assert launch.call_count == 1


def test_resume_session_cleanup_reports_missing_target() -> None:
    manager = SimpleNamespace(
        session_route_target_for_item=MagicMock(return_value=None),
        workdir_for_item=MagicMock(return_value=None),
    )
    status = handle_cleanup_command(
        {
            "action": "resumeSessionCleanupSession",
            "itemId": "opaque-1",
            "inventoryRevision": REVISION,
        },
        RuntimeCommandPorts(cleanup_manager=manager),
    )
    assert status["kind"] == "error"
    assert status["message"].endswith("不可恢复。")
