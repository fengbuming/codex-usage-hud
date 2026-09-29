from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import codex_usage_hud.renderer_connection as _rc
from codex_usage_hud.core.connection_health import ConnectionHealth
from codex_usage_hud.renderer_connection import RendererConnectionManager


class _Tracker:
    def __init__(self) -> None:
        self.follow_state = "new-session"
        self.follow_reason = "renderer-channel-unavailable"
        self.follow_stuck_elapsed_ms = 6_000
        self.renderer_new_session = True
        self.rematerialize_renderer_mapping = MagicMock(return_value=False)

    def follow_snapshot(self) -> dict[str, object]:
        return {
            "followState": self.follow_state,
            "followReason": self.follow_reason,
            "newSession": self.renderer_new_session,
        }

    @staticmethod
    def follow_progressed(
        before: dict[str, object],
        after: dict[str, object],
    ) -> bool:
        return before.get("followState") != after.get("followState")


def _manager(
    *,
    client: object | None = None,
    tracker: object | None = None,
    health: ConnectionHealth | None = None,
    wake: MagicMock | None = None,
    schedule: MagicMock | None = None,
    escalate: object | None = None,
) -> tuple[RendererConnectionManager, object, MagicMock, MagicMock]:
    client = client or SimpleNamespace(update_payload=MagicMock(return_value=True))
    wake = wake or MagicMock()
    schedule = schedule or MagicMock()
    manager = RendererConnectionManager(
        client=client,
        tracker_provider=lambda: tracker,
        wake=wake,
        schedule_soft_reinstall=schedule,
        debug_enabled=lambda: True,
        runtime_errors=lambda: [{"code": "sample"}],
        health=health,
        wall_time=lambda: 20.0,
        escalate_renderer_hung=escalate,
    )
    return manager, client, wake, schedule


def test_connection_light_wakes_before_enable_then_pushes_lightweight_payload() -> None:
    manager, client, wake, _ = _manager()

    manager.request_light()
    manager.enable_light_push()
    manager.request_light()

    wake.assert_called_once_with()
    payload = client.update_payload.call_args.args[0]
    assert set(payload) == {
        "connectionHealth",
        "debug",
        "runtimeErrors",
        "payloadDomains",
    }
    assert payload["payloadDomains"]["diagnostics"] == {
        "connectionHealth": payload["connectionHealth"],
        "debug": True,
        "runtimeErrors": [{"code": "sample"}],
    }


def test_connection_probe_is_bounded_by_health_deadline() -> None:
    health = ConnectionHealth(next_probe_at=float("inf"), last_ok_at=10.0)
    client = SimpleNamespace(
        probe_connection=MagicMock(return_value=True),
        update_payload=MagicMock(return_value=True),
        last_status="ok",
        last_error="",
    )
    manager, _, _, _ = _manager(client=client, health=health)
    manager.enable_light_push()

    assert not manager.maybe_probe(None, update_failures=0)
    client.probe_connection.assert_not_called()

    health.next_probe_at = 0.0
    health.last_ok_at = 0.0
    assert manager.maybe_probe(None, update_failures=0)
    client.probe_connection.assert_called_once_with()
    assert health.reason == "probe-ok"


def test_connection_heal_l1_requires_follow_progress() -> None:
    tracker = _Tracker()
    health = ConnectionHealth()
    health.note_channel_unavailable(now=0.0)

    def report(reason: str) -> bool:
        tracker.follow_state = "confirmed"
        tracker.follow_reason = "confirmed"
        tracker.renderer_new_session = False
        return True

    client = SimpleNamespace(
        report_active_session=MagicMock(side_effect=report),
        rebind_active_session_channel=MagicMock(return_value=False),
        update_payload=MagicMock(return_value=True),
    )
    manager, _, wake, schedule = _manager(
        client=client,
        tracker=tracker,
        health=health,
    )
    manager.enable_light_push()

    assert manager.maybe_heal(SimpleNamespace(follow_state="new-session"))
    client.report_active_session.assert_called_once_with("self-heal:channel-unavailable")
    assert health.state == "ok"
    wake.assert_called_once_with()
    schedule.assert_not_called()


def test_connection_heal_l3_schedules_soft_reinstall_once() -> None:
    tracker = _Tracker()
    health = ConnectionHealth()
    health.note_channel_unavailable(now=0.0)
    client = SimpleNamespace(
        report_active_session=MagicMock(return_value=False),
        rebind_active_session_channel=MagicMock(return_value=False),
        _clear_target_cache=MagicMock(),
        update_payload=MagicMock(return_value=True),
    )
    manager, _, wake, schedule = _manager(
        client=client,
        tracker=tracker,
        health=health,
    )
    manager.enable_light_push()

    assert manager.maybe_heal(SimpleNamespace(follow_state="new-session"))
    client._clear_target_cache.assert_called_once_with(clear_script=True)
    schedule.assert_called_once_with()
    wake.assert_called_once_with()


def test_activity_wake_rematerializes_pending_mapping_before_dom_report() -> None:
    tracker = _Tracker()
    tracker.follow_state = "pending"
    tracker.follow_reason = "awaiting-exact-mapping"

    def rematerialize(*, force: bool) -> bool:
        assert force
        tracker.follow_state = "confirmed"
        tracker.follow_reason = "confirmed"
        tracker.renderer_new_session = False
        return True

    tracker.rematerialize_renderer_mapping.side_effect = rematerialize
    client = SimpleNamespace(
        report_active_session=MagicMock(return_value=True),
        update_payload=MagicMock(return_value=True),
    )
    manager, _, wake, _ = _manager(client=client, tracker=tracker)
    manager.enable_light_push()

    assert manager.activity_wake(
        SimpleNamespace(follow_state="pending", follow_reason="awaiting-exact-mapping"),
        reason="session-map",
    )
    client.report_active_session.assert_not_called()
    wake.assert_called_once_with()


def _hung_health(now: float, last_ok: float) -> ConnectionHealth:
    health = ConnectionHealth()
    health.last_ok_at = last_ok
    health.last_fail_at = now
    health.consecutive_failures = 3
    health.transport_state = "failed"
    return health


def test_hung_escalation_fires_after_grace_when_unlocked(monkeypatch) -> None:
    now = [10_000.0]
    monkeypatch.setattr(_rc.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(_rc, "windows_session_locked", lambda: False)
    escalations: list[str] = []
    health = _hung_health(
        now[0],
        now[0] - _rc.RENDERER_HUNG_GRACE_SECONDS - 1.0,
    )
    manager, _, _, _ = _manager(health=health, escalate=escalations.append)
    manager.enable_light_push()

    assert manager.maybe_escalate_renderer_hung()
    assert escalations == [f"renderer-hung:{int(_rc.RENDERER_HUNG_GRACE_SECONDS + 1.0)}s"]
    # A second call in the same episode must not re-escalate.
    assert not manager.maybe_escalate_renderer_hung()
    assert len(escalations) == 1


def test_hung_escalation_does_not_fire_while_locked(monkeypatch) -> None:
    now = [10_000.0]
    monkeypatch.setattr(_rc.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(_rc, "windows_session_locked", lambda: True)
    escalations: list[str] = []
    health = _hung_health(
        now[0],
        now[0] - _rc.RENDERER_HUNG_GRACE_SECONDS - 1.0,
    )
    manager, _, _, _ = _manager(health=health, escalate=escalations.append)

    assert not manager.maybe_escalate_renderer_hung()
    assert escalations == []


def test_hung_escalation_does_not_fire_when_healthy(monkeypatch) -> None:
    now = [10_000.0]
    monkeypatch.setattr(_rc.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(_rc, "windows_session_locked", lambda: False)
    escalations: list[str] = []
    health = ConnectionHealth()
    health.note_success(now=now[0])
    manager, _, _, _ = _manager(health=health, escalate=escalations.append)

    assert not manager.maybe_escalate_renderer_hung()
    assert escalations == []


def test_hung_escalation_does_not_fire_before_any_success(monkeypatch) -> None:
    now = [10_000.0]
    monkeypatch.setattr(_rc.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(_rc, "windows_session_locked", lambda: False)
    escalations: list[str] = []
    health = ConnectionHealth()  # last_ok_at == 0.0 -> never connected
    manager, _, _, _ = _manager(health=health, escalate=escalations.append)

    assert not manager.maybe_escalate_renderer_hung()
    assert escalations == []


def test_hung_escalation_gives_post_unlock_grace(monkeypatch) -> None:
    now = [10_000.0]
    monkeypatch.setattr(_rc.time, "monotonic", lambda: now[0])
    locked = [True]
    monkeypatch.setattr(_rc, "windows_session_locked", lambda: locked[0])
    escalations: list[str] = []
    health = _hung_health(
        now[0],
        now[0] - 2 * _rc.RENDERER_HUNG_GRACE_SECONDS,
    )
    manager, _, _, _ = _manager(health=health, escalate=escalations.append)

    # First pass while still locked: records the locked probe, no escalation.
    assert not manager.maybe_escalate_renderer_hung()
    assert escalations == []

    # Unlock: the renderer gets the post-unlock grace to thaw.
    locked[0] = False
    assert not manager.maybe_escalate_renderer_hung()
    assert escalations == []

    # After the post-unlock grace elapses, escalation fires.
    now[0] += _rc.RENDERER_HUNG_POST_UNLOCK_GRACE_SECONDS + 1.0
    assert manager.maybe_escalate_renderer_hung()
    assert len(escalations) == 1


def test_note_session_resumed_arms_thaw_grace_and_rearms_escalation(monkeypatch) -> None:
    now = [10_000.0]
    monkeypatch.setattr(_rc.time, "monotonic", lambda: now[0])
    manager, _, _, _ = _manager()

    # 恢复前无宽限；恢复后设置解冻宽限并重置挂死升级标志。
    assert manager._escalate_not_before == 0.0  # noqa: SLF001
    manager._hung_escalated = True  # noqa: SLF001
    manager.note_session_resumed()
    assert manager._escalate_not_before == (
        now[0] + _rc.RENDERER_HUNG_POST_UNLOCK_GRACE_SECONDS
    )  # noqa: SLF001
    assert manager._hung_escalated is False  # noqa: SLF001


def test_hung_escalation_rearms_after_recovery(monkeypatch) -> None:
    now = [10_000.0]
    monkeypatch.setattr(_rc.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(_rc, "windows_session_locked", lambda: False)
    escalations: list[str] = []
    health = _hung_health(
        now[0],
        now[0] - _rc.RENDERER_HUNG_GRACE_SECONDS - 1.0,
    )
    manager, _, _, _ = _manager(health=health, escalate=escalations.append)
    recovered = MagicMock()
    manager.renderer_recovered = recovered

    assert manager.maybe_escalate_renderer_hung()
    assert len(escalations) == 1
    assert not manager.maybe_escalate_renderer_hung()
    recovered.assert_not_called()

    # Renderer recovers: a success refreshes last_ok_at, rearming the check.
    now[0] += 10.0
    health.note_success(now=now[0])
    assert not manager.maybe_escalate_renderer_hung()
    recovered.assert_called_once_with()

    # Healthy for a long stretch, then it wedges again beyond the grace.
    now[0] += _rc.RENDERER_HUNG_MIN_REESCALATE_SECONDS
    health.note_success(now=now[0])
    assert not manager.maybe_escalate_renderer_hung()
    now[0] += _rc.RENDERER_HUNG_GRACE_SECONDS + 1.0
    assert manager.maybe_escalate_renderer_hung()
    assert len(escalations) == 2
    now[0] += 1.0
    health.note_success(now=now[0])
    assert not manager.maybe_escalate_renderer_hung()
    assert recovered.call_count == 2


def test_hung_recovery_notice_survives_resume_until_ack(monkeypatch) -> None:
    now = [10_000.0]
    monkeypatch.setattr(_rc.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(_rc, "windows_session_locked", lambda: False)
    health = _hung_health(now[0], now[0] - 31.0)
    manager, _, _, _ = _manager(health=health, escalate=MagicMock())
    recovered = MagicMock(side_effect=[RuntimeError("temporary failure"), None])
    manager.renderer_recovered = recovered
    assert manager.maybe_escalate_renderer_hung()
    manager.note_session_resumed()
    assert not manager.maybe_escalate_renderer_hung()
    recovered.assert_not_called()
    now[0] += 1.0
    health.note_success(now=now[0])
    assert not manager.maybe_escalate_renderer_hung()
    assert not manager.maybe_escalate_renderer_hung()
    assert not manager.maybe_escalate_renderer_hung()
    assert recovered.call_count == 2
