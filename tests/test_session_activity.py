from __future__ import annotations

import logging

import pytest

from codex_usage_hud import session_activity as _session_activity
from codex_usage_hud.session_activity import WindowsSessionLockMonitor


def test_failed_lock_callback_keeps_begin_and_failure_evidence(caplog) -> None:
    def fail() -> None:
        raise RuntimeError("callback stalled then failed")

    monitor = WindowsSessionLockMonitor(on_lock=fail, on_unlock=lambda: None)
    caplog.set_level(logging.INFO, logger=_session_activity.__name__)
    with pytest.raises(RuntimeError, match="callback stalled"):
        monitor._emit_transition(True)
    assert "session_lock_callback_begin" in caplog.text
    assert "session_lock_callback_failed" in caplog.text
    assert "session_lock_callback_complete" not in caplog.text


def test_monitor_emits_lock_state_transitions_including_first_event() -> None:
    """The first observation must not be swallowed.

    A WTS/power event that arrives between ``start()`` and the initial-state
    probe would otherwise be recorded without firing a callback, and the probe
    would then dedup against it, losing the lock notification entirely.
    """
    transitions: list[str] = []
    monitor = WindowsSessionLockMonitor(
        on_lock=lambda: transitions.append("lock"),
        on_unlock=lambda: transitions.append("unlock"),
        locked_probe=lambda: False,
    )

    monitor._emit_transition(False)  # noqa: SLF001
    monitor._emit_transition(True)  # noqa: SLF001
    monitor._emit_transition(True)  # noqa: SLF001
    monitor._emit_transition(False)  # noqa: SLF001
    monitor._emit_transition(False)  # noqa: SLF001

    assert transitions == ["unlock", "lock", "unlock"]


def test_monitor_emits_first_lock_event_without_prior_state() -> None:
    """A lock event that is the very first observation still fires on_lock."""
    transitions: list[str] = []
    monitor = WindowsSessionLockMonitor(
        on_lock=lambda: transitions.append("lock"),
        on_unlock=lambda: transitions.append("unlock"),
        locked_probe=lambda: True,
    )

    monitor._emit_transition(True)  # noqa: SLF001
    # The subsequent probe dedups against the already-recorded locked state.
    monitor._synchronize_initial_state()

    assert transitions == ["lock"]


def test_monitor_initial_probe_fires_lock_when_already_locked() -> None:
    transitions: list[str] = []
    monitor = WindowsSessionLockMonitor(
        on_lock=lambda: transitions.append("lock"),
        on_unlock=lambda: transitions.append("unlock"),
        locked_probe=lambda: True,
    )

    monitor._synchronize_initial_state()

    assert transitions == ["lock"]


def test_monitor_power_broadcast_suspend_and_resume_map_to_transitions() -> None:
    transitions: list[str] = []
    monitor = WindowsSessionLockMonitor(
        on_lock=lambda: transitions.append("lock"),
        on_unlock=lambda: transitions.append("unlock"),
        locked_probe=lambda: False,
    )
    # Establish an unlocked baseline so suspend/resume are real transitions.
    monitor._emit_transition(False)  # noqa: SLF001
    transitions.clear()

    # Suspend event → away/locked.
    monitor._handle_power_broadcast(_session_activity._PBT_APMSUSPEND)  # noqa: SLF001
    # Resume event → back.
    monitor._handle_power_broadcast(  # noqa: SLF001
        _session_activity._PBT_APMRESUMEAUTOMATIC  # noqa: SLF001
    )
    # Duplicate resume must not re-emit.
    monitor._handle_power_broadcast(  # noqa: SLF001
        _session_activity._PBT_APMRESUMEAUTOMATIC  # noqa: SLF001
    )

    assert transitions == ["lock", "unlock"]


def test_power_broadcast_constants_match_windows_values() -> None:
    assert _session_activity._WM_POWERBROADCAST == 0x0218
    assert _session_activity._PBT_APMSUSPEND == 0x0004
    assert _session_activity._PBT_APMRESUMEAUTOMATIC == 0x0012
    assert _session_activity._PBT_APMRESUMESUSPEND == 0x0007
    assert _session_activity._PBT_APMRESUMECRITICAL == 0x0006


def test_power_broadcast_handler_ignores_unknown_events() -> None:
    transitions: list[str] = []
    monitor = WindowsSessionLockMonitor(
        on_lock=lambda: transitions.append("lock"),
        on_unlock=lambda: transitions.append("unlock"),
        locked_probe=lambda: False,
    )
    monitor._emit_transition(False)  # noqa: SLF001
    transitions.clear()

    monitor._handle_power_broadcast(0x9999)  # noqa: SLF001

    assert transitions == []


def test_power_broadcast_suspend_query_cancel_does_not_stop_hud() -> None:
    """The suspend handshake must not stop the HUD.

    PBT_APMQUERYSUSPEND can be vetoed and PBT_APMQUERYSUSPENDFAILED means the
    suspend was cancelled — the system stays awake, so neither may map to
    "away" (that would stop the HUD for no reason).
    """
    transitions: list[str] = []
    monitor = WindowsSessionLockMonitor(
        on_lock=lambda: transitions.append("lock"),
        on_unlock=lambda: transitions.append("unlock"),
        locked_probe=lambda: False,
    )
    monitor._emit_transition(False)  # noqa: SLF001
    transitions.clear()

    monitor._handle_power_broadcast(  # noqa: SLF001
        _session_activity._PBT_APMQUERYSUSPEND  # noqa: SLF001
    )
    monitor._handle_power_broadcast(  # noqa: SLF001
        _session_activity._PBT_APMQUERYSUSPENDFAILED  # noqa: SLF001
    )

    assert transitions == []

    # Only the definitive suspend event maps to away.
    monitor._handle_power_broadcast(_session_activity._PBT_APMSUSPEND)  # noqa: SLF001
    assert transitions == ["lock"]
