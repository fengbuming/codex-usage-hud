from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from codex_usage_hud import overlay_commands

from codex_usage_hud.overlay_command_pump import WorkOverlayCommandPump
from codex_usage_hud.overlay_commands import (
    OverlayRuntimeCommandCallbacks,
    activation_context,
    handle_command,
)


def _switch_result(**overrides: object) -> SimpleNamespace:
    values = {
        "ok": True,
        "status": "switched",
        "backend": "cdp",
        "requested_session_id": "session-1",
        "active_session_id": "session-1",
        "requested_title": "Session One",
        "active_title": "Session One",
        "matched_by": "id",
        "message": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_overlay_command_opens_window_before_switch_and_checks_focus() -> None:
    controller = SimpleNamespace(activate_session=MagicMock(return_value=_switch_result()))
    prepare = MagicMock(return_value=(True, "visible", "", 1))
    refocus = MagicMock(return_value=(True, "visible", "", 1))
    order = MagicMock()
    order.attach_mock(prepare, "prepare")
    order.attach_mock(controller.activate_session, "switch")
    order.attach_mock(refocus, "refocus")

    result = handle_command(
        {"action": "activateSession", "sessionId": "session-1"},
        controller,
        prepare_window_callback=prepare,
        refocus_window_callback=refocus,
    )

    assert result.ok
    prepare.assert_called_once()
    refocus.assert_called_once()
    assert [call[0] for call in order.mock_calls] == ["prepare", "switch", "refocus"]


def test_overlay_command_retries_once_after_cdp_transport_failure() -> None:
    controller = SimpleNamespace(
        activate_session=MagicMock(
            side_effect=[_switch_result(ok=False, status="cdp-error"), _switch_result()]
        )
    )
    prepare = MagicMock(return_value=(True, "visible", "", 1))

    result = handle_command(
        {"action": "activateSession", "sessionId": "session-1"},
        controller,
        prepare_window_callback=prepare,
        refocus_window_callback=MagicMock(return_value=(True, "visible", "", 1)),
    )

    assert result.ok
    assert controller.activate_session.call_count == 2
    prepare.assert_called_once()


def test_overlay_command_never_activates_cli_session() -> None:
    controller = SimpleNamespace(activate_session=MagicMock())
    result = handle_command(
        {"action": "activateSession", "sessionId": "cli-1", "clientKind": "cli"},
        controller,
    )
    assert result is None
    controller.activate_session.assert_not_called()


def test_overlay_command_waits_for_cold_renderer_after_window_exists(monkeypatch) -> None:
    controller = SimpleNamespace(activate_session=MagicMock(side_effect=[
        _switch_result(ok=False, status="cdp-error"),
        _switch_result(ok=False, status="desktop-bridge-unavailable"),
        _switch_result(),
    ]))
    monkeypatch.setattr(overlay_commands.time, "sleep", lambda _: None)
    result = handle_command(
        {"action": "activateSession", "sessionId": "session-1"}, controller,
        prepare_window_callback=lambda: (True, "visible", "", 1),
        refocus_window_callback=lambda: (True, "visible", "", 1),
    )
    assert result.ok
    assert controller.activate_session.call_count == 3


def test_overlay_command_does_not_route_if_window_cannot_open() -> None:
    controller = SimpleNamespace(activate_session=MagicMock())
    result = handle_command(
        {"action": "activateSession", "sessionId": "session-1"}, controller,
        prepare_window_callback=lambda: (False, "not_found", "UI did not open", 0),
    )
    assert not result.ok
    assert result.status == "window-unavailable"
    controller.activate_session.assert_not_called()


def test_overlay_command_rejects_route_success_when_foreground_failed() -> None:
    controller = SimpleNamespace(activate_session=MagicMock(return_value=_switch_result()))
    result = handle_command(
        {"action": "activateSession", "sessionId": "session-1", "current": True}, controller,
        prepare_window_callback=lambda: (True, "visible", "", 1),
        refocus_window_callback=lambda: (False, "minimized", "focus denied", 1),
    )
    assert not result.ok
    assert result.status == "window-not-foreground"
    assert result.active_session_id == "session-1"


def test_missing_thread_is_terminal_and_not_retried() -> None:
    controller = SimpleNamespace(activate_session=MagicMock(
        return_value=_switch_result(ok=False, status="thread-not-owned")))
    result = handle_command(
        {"action": "activateSession", "sessionId": "deleted-session"}, controller,
        prepare_window_callback=lambda: (True, "visible", "", 1),
    )
    assert not result.ok
    controller.activate_session.assert_called_once()


def test_cdp_readiness_retry_stops_at_deadline(monkeypatch) -> None:
    ticks = iter([0.0, 0.0, 20.0])
    monkeypatch.setattr(overlay_commands.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(overlay_commands.time, "sleep", lambda _: None)
    controller = SimpleNamespace(activate_session=MagicMock(
        return_value=_switch_result(ok=False, status="cdp-error")))
    result = handle_command(
        {"action": "activateSession", "sessionId": "session-1"}, controller,
        prepare_window_callback=lambda: (True, "visible", "", 1),
    )
    assert not result.ok
    assert controller.activate_session.call_count == 2


def test_overlay_runtime_commands_queue_background_query_with_correlation() -> None:
    enqueue = MagicMock()
    callbacks = OverlayRuntimeCommandCallbacks(
        background_runtime=SimpleNamespace(confirm=MagicMock(return_value=True)),
        rest_reminder=None,
        work_overlay=SimpleNamespace(),
        enqueue_renderer_command=enqueue,
        request_id_factory=lambda: "request-1",
    )

    assert callbacks.handle_background(
        {"action": "openBackgroundUsage", "eventId": "event-1"}
    )
    enqueue.assert_called_once_with(
        {
            "id": "background-overlay-request-1",
            "requestId": "background-overlay-request-1",
            "action": "openBackgroundUsage",
            "eventId": "event-1",
        }
    )


def test_overlay_runtime_commands_update_rest_reminder_projection() -> None:
    presenter = SimpleNamespace(
        postpone=MagicMock(return_value=True),
        desktop_bubble_payload=MagicMock(return_value={"phase": "postponed"}),
    )
    overlay = SimpleNamespace(update_rest_reminder=MagicMock())
    callbacks = OverlayRuntimeCommandCallbacks(
        background_runtime=None,
        rest_reminder=presenter,
        work_overlay=overlay,
        enqueue_renderer_command=MagicMock(),
    )

    assert callbacks.handle_rest_reminder({"action": "restReminderPostpone"})
    presenter.postpone.assert_called_once_with()
    overlay.update_rest_reminder.assert_called_once_with({"phase": "postponed"})


def test_overlay_runtime_commands_credit_early_rest_minutes() -> None:
    presenter = SimpleNamespace(
        credit_early_rest=MagicMock(return_value=True),
        desktop_bubble_payload=MagicMock(return_value={"phase": "focus"}),
    )
    overlay = SimpleNamespace(update_rest_reminder=MagicMock())
    callbacks = OverlayRuntimeCommandCallbacks(
        background_runtime=None,
        rest_reminder=presenter,
        work_overlay=overlay,
        enqueue_renderer_command=MagicMock(),
    )

    assert callbacks.handle_rest_reminder(
        {"action": "restReminderCredit", "minutes": 5}
    )
    presenter.credit_early_rest.assert_called_once_with(5)
    overlay.update_rest_reminder.assert_called_once_with({"phase": "focus"})


def test_overlay_activation_context_uses_injected_clock() -> None:
    context = activation_context(
        {"action": "activateSession", "requestedAt": 10.0},
        _switch_result(),
        clock=lambda: 10.125,
    )
    assert context["latencyMs"] == 125.0


def test_overlay_command_pump_only_delegates_watcher_wakes() -> None:
    watchers: list[object] = []

    class FakeWatcher:
        def __init__(self, callback, **kwargs):
            self.callback = callback
            self.kwargs = kwargs
            self.closed = False
            watchers.append(self)

        def update(self, specs):
            self.specs = list(specs)

        def close(self):
            self.closed = True

    handler = MagicMock(return_value=1)
    event = SimpleNamespace(set=MagicMock())
    pump = WorkOverlayCommandPump(
        SimpleNamespace(command_path=Path("commands.jsonl")),
        handler,
        command_event=event,
        watcher_factory=FakeWatcher,
    )

    assert pump.start()
    watchers[0].callback({"work-overlay-command"}, {Path("commands.jsonl")})
    pump.close()

    assert handler.call_count == 2
    assert event.set.call_count == 2
    assert watchers[0].closed
