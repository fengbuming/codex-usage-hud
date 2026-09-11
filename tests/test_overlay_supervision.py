from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codex_usage_hud import desktop_overlay, overlay_supervision
from codex_usage_hud.desktop_overlay import DesktopWorkOverlay


def test_clean_helper_exit_is_immediately_restartable() -> None:
    decision = overlay_supervision.evaluate_helper_health(
        process_exit_code=0,
        user_object_count=10_000,
        helper_started_at=10.0,
        last_heartbeat_at=10.0,
        now_monotonic=50.0,
        now_wall=50.0,
        heartbeat_timeout_seconds=35.0,
        max_user_objects=2_000,
        restart_backoff_seconds=60.0,
    )

    assert decision == overlay_supervision.HelperHealthDecision(
        action=overlay_supervision.EXITED,
        exit_code=0,
    )


def test_failed_helper_exit_uses_bounded_backoff_and_reason() -> None:
    decision = overlay_supervision.evaluate_helper_health(
        process_exit_code=3,
        user_object_count=None,
        helper_started_at=10.0,
        last_heartbeat_at=10.0,
        now_monotonic=50.0,
        now_wall=50.0,
        heartbeat_timeout_seconds=35.0,
        max_user_objects=2_000,
        restart_backoff_seconds=60.0,
    )

    assert decision.action == overlay_supervision.EXITED
    assert decision.exit_code == 3
    assert decision.restart_blocked_until == 110.0
    assert decision.reason.endswith("code 3")


def test_resource_and_heartbeat_failures_request_restart() -> None:
    resource = overlay_supervision.evaluate_helper_health(
        process_exit_code=None,
        user_object_count=2_000,
        helper_started_at=10.0,
        last_heartbeat_at=10.0,
        now_monotonic=20.0,
        now_wall=20.0,
        heartbeat_timeout_seconds=35.0,
        max_user_objects=2_000,
        restart_backoff_seconds=60.0,
    )
    stale = overlay_supervision.evaluate_helper_health(
        process_exit_code=None,
        user_object_count=None,
        helper_started_at=10.0,
        last_heartbeat_at=10.0,
        now_monotonic=50.0,
        now_wall=45.0,
        heartbeat_timeout_seconds=35.0,
        max_user_objects=2_000,
        restart_backoff_seconds=60.0,
    )

    assert resource == overlay_supervision.HelperHealthDecision(
        action=overlay_supervision.RESTART,
        reason="user_objects=2000",
    )
    assert stale == overlay_supervision.HelperHealthDecision(
        action=overlay_supervision.RESTART,
        reason="heartbeat_age_seconds=35.0",
    )


def test_recent_heartbeat_and_missing_start_are_healthy() -> None:
    for started_at, heartbeat_at in ((0.0, 0.0), (10.0, 12.0)):
        decision = overlay_supervision.evaluate_helper_health(
            process_exit_code=None,
            user_object_count=None,
            helper_started_at=started_at,
            last_heartbeat_at=heartbeat_at,
            now_monotonic=20.0,
            now_wall=20.0,
            heartbeat_timeout_seconds=35.0,
            max_user_objects=2_000,
            restart_backoff_seconds=60.0,
        )
        assert decision == overlay_supervision.HelperHealthDecision(
            action=overlay_supervision.HEALTHY,
        )


def test_availability_probe_caches_and_normalizes_failure_reason() -> None:
    probe_calls = 0

    def probe() -> bool:
        nonlocal probe_calls
        probe_calls += 1
        raise ImportError("PySide6 missing")

    failed = overlay_supervision.probe_runtime_availability(
        cached=None,
        probe=probe,
    )
    cached = overlay_supervision.probe_runtime_availability(
        cached=False,
        probe=lambda: True,
        unavailable_reason=failed.reason,
    )

    assert failed == overlay_supervision.RuntimeAvailabilityDecision(
        available=False,
        reason="PySide6 missing",
    )
    assert cached == failed
    assert probe_calls == 1


def test_keep_alive_policy_uses_minimum_delay_and_disabled_gates() -> None:
    assert overlay_supervision.next_keep_alive_seconds(
        closed=False,
        enabled=True,
        has_payload=True,
        has_rest_reminder=False,
        now_monotonic=20.0,
        last_state_write_at=0.0,
        keepalive_seconds=15.0,
    ) == 0.1
    assert overlay_supervision.next_keep_alive_seconds(
        closed=False,
        enabled=False,
        has_payload=True,
        has_rest_reminder=False,
        now_monotonic=20.0,
        last_state_write_at=0.0,
        keepalive_seconds=15.0,
    ) is None


def test_system_action_router_keeps_deferred_rows_and_first_match() -> None:
    matched, deferred, runtime_error = overlay_supervision.route_system_action_commands(
        [
            {"action": "activateSession", "sessionId": "thread-1"},
            {"action": "runtimeError", "message": "state read failed"},
            {
                "action": "restartCodex",
                "actionId": "restart-1",
            },
            {
                "action": "restartCodex",
                "actionId": "restart-1",
            },
            {
                "action": "restartCodex",
                "actionId": "stale",
            },
        ],
        accepted_actions={"restartCodex"},
        expected_action_id="restart-1",
    )

    assert matched == {"action": "restartCodex", "actionId": "restart-1"}
    assert deferred == [{"action": "activateSession", "sessionId": "thread-1"}]
    assert runtime_error == "state read failed"


def test_desktop_overlay_adapts_health_decision_from_pure_owner() -> None:
    overlay = DesktopWorkOverlay(item_limit=2)
    process = SimpleNamespace(poll=lambda: None)
    overlay._process = process
    decision = overlay_supervision.HelperHealthDecision(
        action=overlay_supervision.HEALTHY,
    )

    with (
        patch.object(overlay, "_refresh_helper_heartbeat"),
        patch.object(desktop_overlay, "_windows_user_object_count", return_value=None),
        patch.object(
            overlay_supervision,
            "evaluate_helper_health",
            return_value=decision,
        ) as evaluate,
    ):
        overlay._ensure_helper_healthy(20.0)

    evaluate.assert_called_once()
    assert evaluate.call_args.kwargs["heartbeat_timeout_seconds"] == (
        desktop_overlay.WORK_OVERLAY_HELPER_HEARTBEAT_TIMEOUT_SECONDS
    )
    assert overlay._process is process


class _FakeClock:
    """Monotonic/wall clock the tests can advance without sleeping."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeHelper:
    """Stand-in for the overlay helper subprocess."""

    def __init__(
        self,
        *,
        exit_code: int | None,
        command: list[str],
        spawns: list[list[str]],
    ) -> None:
        spawns.append(command)
        self._exit_code = exit_code
        self.pid = 1_000 + len(spawns)
        self._handle = None

    @property
    def returncode(self) -> int | None:
        return self._exit_code

    def poll(self) -> int | None:
        return self._exit_code

    def wait(self, timeout: float | None = None) -> int | None:
        return self._exit_code

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


def _build_overlay(
    tmp_path: Path,
    clock: _FakeClock,
    monkeypatch,
    *,
    exit_code: int | None,
) -> tuple[DesktopWorkOverlay, list[list[str]]]:
    spawns: list[list[str]] = []

    def fake_popen(command: list[str], **kwargs: object) -> _FakeHelper:
        return _FakeHelper(exit_code=exit_code, command=command, spawns=spawns)

    monkeypatch.setattr(desktop_overlay.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        desktop_overlay,
        "_windows_user_object_count",
        lambda process: None,
    )
    overlay = DesktopWorkOverlay(
        enabled=True,
        clock=clock,
        runtime_dir=lambda: tmp_path,
        runtime_available=lambda: True,
        state_path=tmp_path / "work-overlay-1-1.json",
    )
    overlay._wait_for_helper_ready = lambda: True
    return overlay, spawns


_REST_PAYLOAD = {
    "bubbleVisible": True,
    "phase": "focus",
    "title": "休息一下",
    "message": "喝口水",
}


_TICKS = 12
# The storm was measured at roughly one helper process per renderer tick, so a
# correct supervisor must stay far below the tick count.
_MAX_SPAWNS_PER_BURST = 3


def test_rest_reminder_republish_does_not_respawn_helper_at_tick_speed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A helper that exits cleanly must not be respawned once per renderer tick.

    Regression: the renderer republishes an unchanged rest-reminder payload on
    every tick, and the overlay used to zero its restart backoff on each of
    those calls. A clean helper exit carried no backoff either, so the
    supervisor spawned a full Python/PySide6 process per tick (~150/minute),
    which starved the Codex renderer into a blank window.
    """
    clock = _FakeClock()
    overlay, spawns = _build_overlay(tmp_path, clock, monkeypatch, exit_code=0)

    for _ in range(_TICKS):
        overlay.update_rest_reminder(_REST_PAYLOAD)

    assert len(spawns) == _MAX_SPAWNS_PER_BURST
    assert overlay._helper_breaker_until > clock.monotonic()


def test_rest_reminder_republish_keeps_single_helper_while_it_stays_up(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = _FakeClock()
    overlay, spawns = _build_overlay(tmp_path, clock, monkeypatch, exit_code=None)

    for _ in range(_TICKS):
        overlay.update_rest_reminder(_REST_PAYLOAD)

    assert len(spawns) == 1


def test_rest_reminder_retries_once_breaker_backoff_expires(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = _FakeClock()
    overlay, spawns = _build_overlay(tmp_path, clock, monkeypatch, exit_code=0)

    for _ in range(_TICKS):
        overlay.update_rest_reminder(_REST_PAYLOAD)
    blocked = len(spawns)
    assert blocked == _MAX_SPAWNS_PER_BURST

    clock.advance(
        desktop_overlay.WORK_OVERLAY_HELPER_BREAKER_BACKOFF_SECONDS + 1.0
    )
    overlay.update_rest_reminder(_REST_PAYLOAD)

    assert len(spawns) == blocked + 1


def test_system_notice_honours_helper_restart_backoff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = _FakeClock()
    overlay, spawns = _build_overlay(tmp_path, clock, monkeypatch, exit_code=0)

    results = [
        overlay.show_system_notice(title="Codex HUD", message="正在恢复")
        for _ in range(_TICKS)
    ]

    assert len(spawns) == _MAX_SPAWNS_PER_BURST
    assert results[-1] is False


def test_reset_runtime_availability_clears_helper_breaker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = _FakeClock()
    overlay, spawns = _build_overlay(tmp_path, clock, monkeypatch, exit_code=0)

    for _ in range(_TICKS):
        overlay.update_rest_reminder(_REST_PAYLOAD)
    blocked = len(spawns)

    overlay.reset_runtime_availability()
    overlay.update_rest_reminder(_REST_PAYLOAD)

    assert overlay._helper_breaker_until == 0.0
    assert len(spawns) == blocked + 1
