from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codex_usage_hud import daemon_runtime, runtime_orchestration
from codex_usage_hud.desktop_overlay import DesktopWorkOverlay
from codex_usage_hud.renderer_session_lifecycle import RendererSessionResources
from codex_usage_hud.ui.work_overlay.model import (
    _item_is_system_action,
    _item_is_system_notice,
    _normalized_system_notice,
    _system_notice_overlay_item,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.001, seconds)


def test_renderer_restart_ready_waits_for_delayed_cdp_target(monkeypatch) -> None:
    clock = _Clock()
    window_calls = 0
    cdp_results = iter([(False, "target-not-ready"), (True, "")])
    diagnostics: list[tuple[str, dict[str, object]]] = []

    def window_probe(*, timeout_seconds: float):
        nonlocal window_calls
        window_calls += 1
        return (
            window_calls >= 2,
            "visible" if window_calls >= 2 else "not_found",
            "",
            123 if window_calls >= 2 else 0,
        )

    monkeypatch.setattr(
        runtime_orchestration,
        "_wait_for_visible_codex_window",
        window_probe,
    )
    monkeypatch.setattr(
        runtime_orchestration,
        "_validate_renderer_cdp_candidate",
        lambda _candidate: next(cdp_results),
    )
    monkeypatch.setattr(runtime_orchestration.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(runtime_orchestration.time, "sleep", clock.sleep)
    monkeypatch.setattr(
        runtime_orchestration,
        "_append_renderer_diagnostic",
        lambda stage, **fields: diagnostics.append((stage, fields)),
    )

    assert runtime_orchestration._wait_for_renderer_restart_ready(
        61234,
        source="automatic",
        timeout_seconds=1.0,
        poll_seconds=0.1,
    )
    assert window_calls == 2
    assert diagnostics == [
        (
            "renderer_restart_ready",
            {"source": "automatic", "port": 61234, "hwnd": 123},
        )
    ]


def test_renderer_restart_ready_reports_bounded_failure(monkeypatch) -> None:
    clock = _Clock()
    diagnostics: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        runtime_orchestration,
        "_wait_for_visible_codex_window",
        lambda *, timeout_seconds: (True, "visible", "", 456),
    )
    monkeypatch.setattr(
        runtime_orchestration,
        "_validate_renderer_cdp_candidate",
        lambda _candidate: (False, "page-target-missing"),
    )
    monkeypatch.setattr(runtime_orchestration.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(runtime_orchestration.time, "sleep", clock.sleep)
    monkeypatch.setattr(
        runtime_orchestration,
        "_append_renderer_diagnostic",
        lambda stage, **fields: diagnostics.append((stage, fields)),
    )

    assert not runtime_orchestration._wait_for_renderer_restart_ready(
        61234,
        source="automatic",
        timeout_seconds=0.2,
        poll_seconds=0.1,
    )
    assert diagnostics == [
        (
            "renderer_restart_not_ready",
            {
                "source": "automatic",
                "port": 61234,
                "window_ready": True,
                "window_status": "visible",
                "window_reason": "",
                "hwnd": 456,
                "cdp_ready": False,
                "cdp_reason": "page-target-missing",
                "timeout_seconds": 0.2,
            },
        )
    ]


def test_automatic_restart_uses_automatic_diagnostic_and_readiness_gate(
    monkeypatch,
) -> None:
    diagnostics: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(runtime_orchestration.sys, "platform", "win32")
    monkeypatch.setattr(
        runtime_orchestration,
        "_stop_codex_processes",
        lambda: True,
    )
    monkeypatch.setattr(
        runtime_orchestration,
        "_select_launch_renderer_cdp_port",
        lambda *, require_fresh: 61234,
    )
    monkeypatch.setattr(
        runtime_orchestration,
        "launch_codex_app",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        runtime_orchestration,
        "_wait_for_renderer_restart_ready",
        lambda port, *, source: source == "automatic" and port == 61234,
    )
    monkeypatch.setattr(
        runtime_orchestration,
        "_append_renderer_diagnostic",
        lambda stage, **fields: diagnostics.append((stage, fields)),
    )

    assert runtime_orchestration._automatic_restart_codex_for_renderer()
    assert diagnostics == [
        (
            "renderer_restart_requested_automatically",
            {
                "action_id": "restart-codex-for-renderer",
                "port": 61234,
                "source": "automatic",
            },
        )
    ]


def test_daemon_uses_automatic_restart_callback_for_observed_relaunch() -> None:
    manager = SimpleNamespace(wait_for_codex=MagicMock(), poll_seconds=0.0)
    loading = MagicMock()
    loading.start.return_value = loading
    run_hud = MagicMock(
        side_effect=[
            daemon_runtime.DAEMON_RESTART_REQUESTED,
            daemon_runtime.HUD_AUTO_RESTART_CODEX,
        ]
    )
    run_renderer = MagicMock(return_value=0)
    manual_restart = MagicMock(return_value=True)
    automatic_restart = MagicMock(return_value=True)
    services = daemon_runtime.DaemonServices(
        manager_factory=lambda **_kwargs: manager,
        lock_factory=MagicMock(return_value=MagicMock()),
        configure_logging=MagicMock(),
        attach_logger=MagicMock(),
        hide_console=MagicMock(),
        startup_decision=MagicMock(
            return_value=daemon_runtime.DaemonStartupDecision(
                daemon_runtime.DEFAULT_DAEMON_STARTUP_WAIT,
            )
        ),
        create_loading=MagicMock(return_value=loading),
        run_renderer=run_renderer,
        run_hud=run_hud,
        restart_codex=manual_restart,
        automatic_restart_codex=automatic_restart,
    )

    assert daemon_runtime.run_daemon(
        SimpleNamespace(daemon_poll_ms=500),
        services=services,
    ) == 0
    automatic_restart.assert_called_once_with()
    manual_restart.assert_not_called()


def test_observed_relaunch_reuses_overlay_and_skips_new_loading_card() -> None:
    manager = SimpleNamespace(wait_for_codex=MagicMock(), poll_seconds=0.0)
    loading = MagicMock()
    loading.start.return_value = loading
    overlay = SimpleNamespace(
        show_system_notice=MagicMock(return_value=True),
        close=MagicMock(),
    )
    hud_calls: list[dict[str, object]] = []

    def run_hud(_args, **kwargs):
        hud_calls.append(kwargs)
        if len(hud_calls) == 1:
            kwargs["overlay_handoff"]["overlay"] = overlay
            return daemon_runtime.DAEMON_RESTART_REQUESTED
        return daemon_runtime.HUD_AUTO_RESTART_CODEX

    renderer = MagicMock(return_value=0)
    automatic_restart = MagicMock(return_value=True)
    services = daemon_runtime.DaemonServices(
        manager_factory=lambda **_kwargs: manager,
        lock_factory=MagicMock(return_value=MagicMock()),
        configure_logging=MagicMock(),
        attach_logger=MagicMock(),
        hide_console=MagicMock(),
        startup_decision=MagicMock(
            return_value=daemon_runtime.DaemonStartupDecision(
                daemon_runtime.DEFAULT_DAEMON_STARTUP_WAIT,
            )
        ),
        create_loading=MagicMock(return_value=loading),
        run_renderer=renderer,
        run_hud=run_hud,
        automatic_restart_codex=automatic_restart,
    )

    assert daemon_runtime.run_daemon(
        SimpleNamespace(daemon_poll_ms=500),
        services=services,
    ) == 0

    overlay.show_system_notice.assert_called_once_with(
        title=daemon_runtime.RENDERER_RECOVERY_NOTICE_TITLE,
        message=daemon_runtime.RENDERER_RECOVERY_NOTICE_MESSAGE,
    )
    automatic_restart.assert_called_once_with()
    assert services.create_loading.call_count == 1
    assert renderer.call_args.kwargs["seamless_recovery"] is True


def test_manual_renderer_restart_reuses_overlay_for_restart_progress() -> None:
    manager = SimpleNamespace(wait_for_codex=MagicMock(), poll_seconds=0.0)
    loading = MagicMock()
    overlay = SimpleNamespace(
        show_system_notice=MagicMock(return_value=True),
        close=MagicMock(),
    )
    hud_calls: list[dict[str, object]] = []

    def run_hud(_args, **kwargs):
        hud_calls.append(kwargs)
        kwargs["overlay_handoff"]["overlay"] = overlay
        return daemon_runtime.HUD_SWITCH_TO_RENDERER_RESTART_CODEX

    renderer = MagicMock(return_value=0)
    restart = MagicMock(return_value=True)
    services = daemon_runtime.DaemonServices(
        manager_factory=lambda **_kwargs: manager,
        lock_factory=MagicMock(return_value=MagicMock()),
        configure_logging=MagicMock(),
        attach_logger=MagicMock(),
        hide_console=MagicMock(),
        startup_decision=MagicMock(
            return_value=daemon_runtime.DaemonStartupDecision(
                daemon_runtime.DEFAULT_DAEMON_STARTUP_WAIT,
            )
        ),
        create_loading=MagicMock(return_value=loading),
        run_renderer=renderer,
        run_hud=run_hud,
        restart_codex=restart,
    )

    assert daemon_runtime.run_daemon(
        SimpleNamespace(daemon_poll_ms=500),
        services=services,
    ) == 0

    overlay.show_system_notice.assert_called_once_with(
        title=daemon_runtime.RENDERER_RESTART_NOTICE_TITLE,
        message=daemon_runtime.RENDERER_RESTART_NOTICE_MESSAGE,
    )
    restart.assert_called_once_with()
    assert services.create_loading.call_count == 1
    loading.start.assert_called_once_with()
    overlay.close.assert_called_once_with()


def test_manual_renderer_restart_waits_for_replacement_before_new_renderer() -> None:
    manager = SimpleNamespace(
        last_snapshot=SimpleNamespace(primary_pid=27172),
        wait_for_codex=MagicMock(),
        wait_for_codex_replacement=MagicMock(return_value=True),
        poll_seconds=0.0,
    )
    loading = MagicMock()
    overlay = SimpleNamespace(
        show_system_notice=MagicMock(return_value=True),
        close=MagicMock(),
    )
    run_hud = MagicMock(
        side_effect=[
            daemon_runtime.HUD_SWITCH_TO_RENDERER_RESTART_CODEX,
        ]
    )
    renderer = MagicMock(return_value=0)
    restart = MagicMock(return_value=True)
    services = daemon_runtime.DaemonServices(
        manager_factory=lambda **_kwargs: manager,
        lock_factory=MagicMock(return_value=MagicMock()),
        configure_logging=MagicMock(),
        attach_logger=MagicMock(),
        hide_console=MagicMock(),
        startup_decision=MagicMock(
            return_value=daemon_runtime.DaemonStartupDecision(
                daemon_runtime.DEFAULT_DAEMON_STARTUP_WAIT,
            )
        ),
        create_loading=MagicMock(return_value=loading),
        run_renderer=renderer,
        run_hud=run_hud,
        restart_codex=restart,
    )

    assert daemon_runtime.run_daemon(
        SimpleNamespace(daemon_poll_ms=500),
        services=services,
    ) == 0

    manager.wait_for_codex_replacement.assert_called_once_with(27172)
    assert renderer.call_count == 1


def test_system_notice_is_non_interactive_and_has_its_own_projection() -> None:
    notice = _normalized_system_notice(
        {
            "id": "renderer-recovery-notice",
            "title": "检测到 Codex App 未启用 CDP",
            "message": "正在以 CDP 模式重新启动，请稍候。",
            "status": "warning",
            "persistent": True,
        }
    )

    assert notice is not None
    item = _system_notice_overlay_item(notice)
    assert item["systemNotice"] is True
    assert not _item_is_system_action(item)
    assert _item_is_system_notice(item)
    assert "action" not in item


def test_system_notice_preserves_existing_items_and_overlay_process() -> None:
    overlay = DesktopWorkOverlay(item_limit=2)
    existing_items = [{"id": "cli-session", "status": "running"}]
    overlay._last_payload_items = existing_items
    helper = SimpleNamespace(poll=MagicMock(return_value=None))

    with (
        patch.object(overlay, "_runtime_available", return_value=True),
        patch.object(overlay, "_ensure_helper_healthy"),
        patch.object(
            overlay,
            "_start",
            side_effect=lambda: setattr(overlay, "_process", helper),
        ),
        patch.object(overlay, "_append_transition_audit"),
        patch(
            "codex_usage_hud.desktop_overlay.write_json_object"
        ) as write_json,
    ):
        assert overlay.show_system_notice(
            title="检测到 Codex App 未启用 CDP",
            message="正在以 CDP 模式重新启动，请稍候。",
        )

    payload = write_json.call_args.args[1]
    assert payload["items"] == existing_items
    assert payload["systemNotice"]["id"] == "renderer-recovery-notice"
    assert overlay._process is helper


@pytest.mark.qt_ui
def test_hung_notice_can_be_dismissed_without_hiding_next_incident(tmp_path, monkeypatch) -> None:
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from codex_usage_hud.ui.work_overlay.qt_window import OverlayWindow

    app = QApplication.instance() or QApplication([])
    window = OverlayWindow(
        app=app, path=tmp_path / "state.json", read_state=lambda: None,
        owner_pid=None, process_exists=lambda _: True, stale_seconds=20,
        item_limit=2, overlay_alpha=0.88, hover_alpha=0.22,
        heartbeat_path=tmp_path / "heartbeat", header_title_limit=28,
    )
    notice = {
        "id": "renderer-recovery-notice", "title": "Codex 界面无响应",
        "message": "等待恢复", "persistent": True, "dismissible": True,
        "instanceId": "first",
    }
    items = [{"id": "session", "title": "Working", "status": "running"}]
    try:
        window.render_items(items, system_notice=notice)
        notice_anchor = next(anchor for anchor in window._close_anchors if anchor[1].get("systemNotice"))
        window.dismiss_item(notice_anchor[1])
        window.render_items(items, system_notice=notice)
        assert not any(anchor[1].get("systemNotice") for anchor in window._close_anchors)
        assert any(anchor[1]["id"] == "session" for anchor in window._close_anchors)
        window.render_items(items, system_notice={**notice, "instanceId": "second"})
        assert any(anchor[1].get("systemNotice") for anchor in window._close_anchors)
        window.render_items(items, system_notice={})
        assert not any(anchor[1].get("systemNotice") for anchor in window._close_anchors)
    finally:
        window.close()
        app.processEvents()


def test_restart_action_reuses_notice_card_and_clears_after_attach() -> None:
    overlay = DesktopWorkOverlay(item_limit=2)
    existing_items = [{"id": "cli-session", "status": "running"}]
    helper = SimpleNamespace(poll=MagicMock(return_value=None))
    overlay._last_payload_items = existing_items
    overlay._system_notice = {
        "id": "renderer-recovery-notice",
        "title": "正在启动 Renderer",
        "message": "HUD 正在检查 Codex 的 CDP 连接，请稍候。",
        "status": "warning",
        "persistent": True,
    }

    with (
        patch.object(overlay, "_runtime_available", return_value=True),
        patch.object(overlay, "_ensure_helper_healthy"),
        patch.object(overlay, "_theme_payload", return_value={}),
        patch.object(
            overlay,
            "_start",
            side_effect=lambda: setattr(overlay, "_process", helper),
        ),
        patch.object(
            overlay,
            "_wait_for_system_action_command",
            return_value={"action": "systemActionReady"},
        ),
        patch.object(overlay, "_append_transition_audit"),
        patch("codex_usage_hud.desktop_overlay.write_json_object") as write_json,
    ):
        assert overlay.offer_codex_restart(
            title="需要重启 Codex",
            message="当前 Codex 未开启 HUD 所需的 CDP。",
        )

        action_payload = write_json.call_args.args[1]
        assert action_payload["items"] == existing_items
        assert action_payload["systemNotice"] == {}
        assert action_payload["systemAction"]["id"] == "renderer-recovery-notice"

        assert overlay.clear_system_action()
        cleared_payload = write_json.call_args.args[1]
        assert cleared_payload["systemAction"] == {}
        assert cleared_payload["items"] == existing_items


def test_overlay_can_be_released_for_daemon_handoff_without_closing() -> None:
    overlay = SimpleNamespace(close=MagicMock())
    resources = RendererSessionResources(overlay=overlay)

    assert resources.release_overlay_for_handoff() is overlay
    resources.close()

    overlay.close.assert_not_called()
