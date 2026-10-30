from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codex_usage_hud.config import UserConfig
from codex_usage_hud.renderer_session_lifecycle import (
    RendererSessionLoopControls,
    RendererSessionResources,
    RendererStartupFeedback,
)
import codex_usage_hud.renderer_runtime as renderer_runtime
from codex_usage_hud.renderer_session_ports import RendererSessionPorts
from codex_usage_hud.renderer_event_loop import RendererLoopState
from codex_usage_hud.runtime_orchestration import (
    RENDERER_STARTUP_ATTACH,
    RendererStartupPlan,
    run_renderer_hud_session,
)
from codex_usage_hud.runtime_ports import RuntimeServices


def test_renderer_session_ports_have_a_direct_owner_and_legacy_alias() -> None:
    assert renderer_runtime.RendererSessionPorts is RendererSessionPorts
    with pytest.raises(RuntimeError, match="missing ports"):
        RendererSessionPorts.from_mapping({})


def test_startup_update_replacement_wait_uses_last_successful_cdp_port() -> None:
    manager = SimpleNamespace(
        last_snapshot=SimpleNamespace(primary_pid=10704),
        codex_is_running=MagicMock(return_value=False),
        wait_for_codex_replacement=MagicMock(return_value=True),
    )
    assert renderer_runtime._wait_for_startup_update_replacement(manager)

    manager.codex_is_running.assert_called_once_with()
    manager.wait_for_codex_replacement.assert_called_once_with(
        10704,
        timeout_seconds=renderer_runtime.RENDERER_STARTUP_UPDATE_REPLACEMENT_WAIT_SECONDS,
    )


def test_startup_update_replacement_wait_skips_first_time_plain_desktop() -> None:
    manager = SimpleNamespace(
        last_snapshot=SimpleNamespace(primary_pid=10704),
        codex_is_running=MagicMock(return_value=True),
        wait_for_codex_replacement=MagicMock(return_value=True),
    )
    with patch(
        "codex_usage_hud.renderer_runtime._renderer_startup_owner.read_persisted_cdp_port",
        return_value=None,
    ):
        assert not renderer_runtime._wait_for_startup_update_replacement(manager)

    manager.codex_is_running.assert_called_once_with()
    manager.wait_for_codex_replacement.assert_not_called()


def _closing_resource(
    name: str,
    order: list[str],
    *,
    fail: bool = False,
) -> SimpleNamespace:
    def close() -> None:
        order.append(name)
        if fail:
            raise RuntimeError(name)

    return SimpleNamespace(close=close)


def test_renderer_session_resources_close_in_reverse_construction_order() -> None:
    order: list[str] = []
    resources = RendererSessionResources(
        context=_closing_resource("context", order),
        overlay=_closing_resource("overlay", order),
        update_manager=_closing_resource("updates", order),
        client=_closing_resource("client", order),
        runtime_event_unsubscribe=lambda: order.append("unsubscribe"),
        bridge_callbacks=SimpleNamespace(
            disconnect_tracker=lambda: order.append("tracker")
        ),
        bridge=_closing_resource("bridge", order),
        command_pump=_closing_resource("commands", order),
        file_events=_closing_resource("files", order),
        active_work_pump=_closing_resource("active-work", order),
    )

    resources.close()

    assert order == [
        "active-work",
        "files",
        "commands",
        "bridge",
        "tracker",
        "unsubscribe",
        "client",
        "updates",
        "overlay",
        "context",
    ]


def test_renderer_session_resources_support_partial_construction() -> None:
    order: list[str] = []
    resources = RendererSessionResources(
        context=_closing_resource("context", order),
        overlay=_closing_resource("overlay", order),
    )

    resources.close()

    assert order == ["overlay", "context"]


def test_renderer_session_resources_close_is_idempotent() -> None:
    order: list[str] = []
    resources = RendererSessionResources(
        context=_closing_resource("context", order),
    )

    resources.close()
    resources.close()

    assert order == ["context"]


def test_renderer_session_resources_close_skips_cdp_removal_when_requested() -> None:
    client = SimpleNamespace(close=MagicMock())
    resources = RendererSessionResources(client=client)

    resources.close(remove_renderer=False)

    client.close.assert_called_once_with(remove_from_page=False)


def test_renderer_session_resources_close_removes_page_by_default() -> None:
    client = SimpleNamespace(close=MagicMock())
    resources = RendererSessionResources(client=client)

    resources.close()

    client.close.assert_called_once_with()


def test_renderer_session_resources_continue_after_close_error() -> None:
    order: list[str] = []
    resources = RendererSessionResources(
        context=_closing_resource("context", order),
        overlay=_closing_resource("overlay", order),
        client=_closing_resource("client", order, fail=True),
        bridge=_closing_resource("bridge", order, fail=True),
    )

    resources.close()

    assert order == ["bridge", "client", "overlay", "context"]


def test_renderer_session_startup_failure_closes_partial_resources_in_reverse() -> None:
    order: list[str] = []
    context = SimpleNamespace(
        settings_store=SimpleNamespace(),
        user_config=UserConfig.defaults(),
        close=lambda: order.append("context"),
    )
    overlay = _closing_resource("overlay", order)
    updates = _closing_resource("updates", order)
    client = _closing_resource("client", order)

    def start_bridge() -> str:
        raise RuntimeError("bridge start failed")

    bridge = SimpleNamespace(
        start=start_bridge,
        close=lambda: order.append("bridge"),
    )
    services = RuntimeServices(
        clock=SimpleNamespace(),
        context_factory=lambda _args: context,
        renderer_factory=lambda _port, _timeout: client,
        overlay_factory=lambda _context: overlay,
        update_manager_factory=lambda: updates,
        bridge_factory=lambda *_args, **_kwargs: bridge,
        snapshot_builder=lambda _context: None,
    )

    with (
        patch(
            "codex_usage_hud.runtime_orchestration._renderer_startup_plan",
            return_value=RendererStartupPlan(
                scenario=RENDERER_STARTUP_ATTACH,
                port=9229,
                port_source="test",
            ),
        ),
        pytest.raises(RuntimeError, match="bridge start failed"),
    ):
        run_renderer_hud_session(
            SimpleNamespace(hud_mode="renderer"),
            lock_already_held=True,
            services=services,
        )

    assert order == ["bridge", "client", "updates", "overlay", "context"]


def test_renderer_session_loop_controls_schedule_and_reset_response_retry() -> None:
    state = RendererLoopState(
        settings_command_status={"responsePending": True},
    )
    controls = RendererSessionLoopControls(
        state=state,
        monotonic=lambda: 10.0,
        response_pending=lambda status: bool(status.get("responsePending")),
        response_retry_delay=lambda attempt: 2.5 if attempt == 1 else None,
        exit_event=SimpleNamespace(is_set=lambda: False),
        restart_event=SimpleNamespace(is_set=lambda: False),
        overlay=SimpleNamespace(),
        daemon_restart_result=17,
    )

    controls.schedule_background_retry()
    assert state.background_usage_response_retry_attempts == 1
    assert state.background_usage_response_retry_not_before == 12.5

    controls.schedule_background_retry()
    assert state.background_usage_response_retry_attempts == 0
    assert state.background_usage_response_retry_not_before == 0.0


def test_renderer_session_loop_controls_maintain_connection_after_iteration() -> None:
    state = RendererLoopState(activity_wake_pending="background-usage", failures=3)
    manager = SimpleNamespace(
        activity_wake=MagicMock(),
        maybe_heal=MagicMock(),
        maybe_probe=MagicMock(),
        maybe_escalate_renderer_hung=MagicMock(),
    )
    controls = RendererSessionLoopControls(
        state=state,
        monotonic=lambda: 0.0,
        response_pending=lambda _status: False,
        response_retry_delay=lambda _attempt: None,
        exit_event=SimpleNamespace(is_set=lambda: True),
        restart_event=SimpleNamespace(is_set=lambda: True),
        overlay=SimpleNamespace(keep_alive=MagicMock()),
        daemon_restart_result=17,
        connection_manager=manager,
    )
    snapshot = object()

    controls.schedule_soft_reinstall()
    controls.keep_overlay_alive()
    controls.after_iteration(snapshot)

    assert state.soft_reinstall_pending
    assert state.activity_wake_pending == ""
    assert controls.exit_requested()
    assert controls.restart_requested()
    assert controls.restart_result() == 17
    controls.overlay.keep_alive.assert_called_once_with()
    manager.activity_wake.assert_called_once_with(
        snapshot,
        reason="background-usage",
    )
    manager.maybe_heal.assert_called_once_with(snapshot)
    manager.maybe_probe.assert_called_once_with(snapshot, update_failures=3)
    manager.maybe_escalate_renderer_hung.assert_called_once_with()


def test_renderer_session_loop_controls_return_codex_restart_result() -> None:
    state = RendererLoopState()
    controls = RendererSessionLoopControls(
        state=state,
        monotonic=lambda: 0.0,
        response_pending=lambda _status: False,
        response_retry_delay=lambda _attempt: None,
        exit_event=SimpleNamespace(is_set=lambda: False),
        restart_event=SimpleNamespace(is_set=lambda: False),
        restart_codex_event=SimpleNamespace(is_set=lambda: True),
        restart_codex_result=32,
        overlay=SimpleNamespace(),
        daemon_restart_result=17,
    )

    assert controls.restart_requested()
    assert controls.restart_result() == 32


def test_renderer_session_loop_controls_resolve_current_snapshot_session() -> None:
    state = RendererLoopState(
        latest_snapshot=SimpleNamespace(session_path="session.jsonl")
    )
    controls = RendererSessionLoopControls(
        state=state,
        monotonic=lambda: 0.0,
        response_pending=lambda _status: False,
        response_retry_delay=lambda _attempt: None,
        exit_event=SimpleNamespace(is_set=lambda: False),
        restart_event=SimpleNamespace(is_set=lambda: False),
        overlay=SimpleNamespace(),
        daemon_restart_result=0,
    )

    assert controls.current_session(lambda path: f"key:{path}") == (
        "key:session.jsonl"
    )


def test_renderer_session_loop_controls_run_bounded_daemon_watchdog() -> None:
    class WatchError(RuntimeError):
        pass

    state = RendererLoopState(next_daemon_check_at=5.0)
    manager = SimpleNamespace(
        poll_seconds=3.0,
        codex_is_running=MagicMock(return_value=True),
    )
    controls = RendererSessionLoopControls(
        state=state,
        monotonic=lambda: 5.0,
        response_pending=lambda _status: False,
        response_retry_delay=lambda _attempt: None,
        exit_event=SimpleNamespace(is_set=lambda: False),
        restart_event=SimpleNamespace(is_set=lambda: False),
        overlay=SimpleNamespace(),
        daemon_restart_result=17,
        daemon_manager=manager,
        daemon_failure_exception=WatchError,
        unavailable_result=29,
    )

    assert controls.daemon_tick() is None
    assert state.next_daemon_check_at == 8.0

    manager.codex_is_running.return_value = False
    state.next_daemon_check_at = 5.0
    assert controls.daemon_tick() == 17

    manager.codex_is_running.side_effect = WatchError("watch failed")
    state.next_daemon_check_at = 5.0
    assert controls.daemon_tick() == 29


def test_renderer_startup_payload_clamps_progress_without_copy_changes() -> None:
    payload = RendererStartupFeedback.payload(
        step="第 1 步，共 4 步",
        detail="正在准备…",
        progress=140,
    )

    assert payload == {
        "payloadDomains": {
            "startup": {
                "step": "第 1 步，共 4 步",
                "title": "正在启动 Codex HUD",
                "detail": "正在准备…",
                "progress": 100,
            }
        }
    }


def test_renderer_startup_bootstrap_clears_then_waits_for_binding() -> None:
    order: list[str] = []
    wake = SimpleNamespace(
        clear=lambda: order.append("clear"),
        wait=lambda seconds: order.append(f"wait:{seconds}"),
    )
    payloads: list[dict[str, object]] = []

    def bootstrap(*, startup_payload: dict[str, object]) -> bool:
        order.append("bootstrap")
        payloads.append(startup_payload)
        return True

    feedback = RendererStartupFeedback(
        SimpleNamespace(
            bootstrap_active_session=bootstrap,
            last_bootstrap_metrics={"totalMs": 4},
        ),
        wake,
        bootstrap_wait_seconds=0.35,
    )

    assert feedback.bootstrap(step="step", detail="detail", progress=18)
    assert order == ["clear", "bootstrap", "wait:0.35"]
    assert payloads[0]["payloadDomains"]["startup"]["progress"] == 18


def test_renderer_startup_progress_uses_fixed_stage_mapping() -> None:
    client = SimpleNamespace(show_startup=MagicMock(return_value=True))
    feedback = RendererStartupFeedback(
        client,
        SimpleNamespace(),
        bootstrap_wait_seconds=0.0,
    )

    feedback.progress("reading_session")
    feedback.progress("showing_hud")

    assert [
        call.args[0]["payloadDomains"]["startup"]["progress"]
        for call in client.show_startup.call_args_list
    ] == [62, 88]
