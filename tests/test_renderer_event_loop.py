from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codex_usage_hud.renderer_event_loop import (
    RefreshPlan,
    RendererEventLoop,
    RendererLoopExecutorPorts,
    RendererLoopState,
    RendererRefreshExecutor,
    RendererRefreshPorts,
    RendererTickInputs,
    RendererTickSampler,
    RendererTickSamplerPorts,
    reduce_event,
    reduce_events,
    snapshot_refresh_decision,
)
from codex_usage_hud import renderer_event_loop
from codex_usage_hud.renderer_event_normalization import (
    NormalizedEventBatch,
    event_bus_timestamp,
    normalize_runtime_events,
)
from codex_usage_hud.renderer_wait import (
    RendererWaitPlanner,
    RendererWaitPorts,
    ScheduledDeadlines,
    scheduled_wait_delay,
)
from codex_usage_hud.core import ParsedSession
from codex_usage_hud.core.runtime_events import RuntimeEventBus
from codex_usage_hud.renderer_payload_builder import payload_from_snapshot
from codex_usage_hud.renderer_pre_refresh import (
    RendererPreRefreshExecutor,
    RendererPreRefreshPorts,
)


def _event(event_type: str, **context: object) -> SimpleNamespace:
    return SimpleNamespace(type=event_type, context=context)


def test_renderer_event_loop_reexports_wait_owner_for_compatibility() -> None:
    assert renderer_event_loop.RendererWaitPlanner is RendererWaitPlanner
    assert renderer_event_loop.RendererWaitPorts is RendererWaitPorts
    assert renderer_event_loop.ScheduledDeadlines is ScheduledDeadlines
    assert renderer_event_loop.scheduled_wait_delay is scheduled_wait_delay


def test_renderer_event_loop_reexports_event_normalization_owner() -> None:
    assert renderer_event_loop.NormalizedEventBatch is NormalizedEventBatch
    assert renderer_event_loop.event_bus_timestamp is event_bus_timestamp
    assert renderer_event_loop.normalize_runtime_events is normalize_runtime_events


@pytest.mark.parametrize(
    ("event_type", "snapshot", "force_fast", "domains"),
    [
        ("session_file_changed", True, False, set()),
        ("settings_changed", True, True, set()),
        ("session_snapshot_hydrated", True, True, set()),
        ("budget_window_changed", True, True, set()),
        ("active_work_refresh_requested", True, True, set()),
        ("update_state_changed", False, True, {"settings"}),
        ("rest_reminder_due", False, True, {"settings"}),
        ("usage_cache_hydrated", True, True, set()),
        (
            "usage_insights_changed",
            False,
            True,
            {"settings", "usageInsights"},
        ),
        (
            "session_cleanup_changed",
            False,
            True,
            {"settings", "sessionCleanup"},
        ),
        (
            "session_index_progress",
            False,
            True,
            {"settings", "sessionCleanup"},
        ),
    ],
)
def test_event_reducer_maps_basic_refresh_contracts(
    event_type: str,
    snapshot: bool,
    force_fast: bool,
    domains: set[str],
) -> None:
    state = RendererLoopState()

    next_state, plan = reduce_event(state, _event(event_type))

    assert next_state is state
    assert plan.snapshot is snapshot
    assert plan.force_fast is force_fast
    assert plan.domains == domains


@pytest.mark.parametrize(
    ("action", "snapshot", "background", "domains"),
    [
        ("checkUpdate", False, False, {"settings"}),
        ("restReminderAck", False, False, {"settings"}),
        ("installDesktopOverlay", False, False, {"settings", "overlay"}),
        ("sessionCleanupPreview", False, False, {"settings", "sessionCleanup"}),
        ("usageInsightsRefresh", False, False, {"settings", "usageInsights"}),
        ("openUsageInsightsSession", False, False, {"settings"}),
        ("openUsageInsightsWorkdir", False, False, {"settings"}),
        ("openSessionCleanupWorkdir", False, False, {"settings"}),
        ("openBackgroundUsageWorkdir", False, False, {"settings"}),
        ("openBackgroundUsageFromInsights", False, True, {"backgroundUsage"}),
        ("dismissWarningsToday", False, False, {"currentSession", "settings"}),
        ("unknown", True, False, set()),
    ],
)
def test_settings_command_reducer_preserves_partial_domain_mapping(
    action: str,
    snapshot: bool,
    background: bool,
    domains: set[str],
) -> None:
    _, plan = reduce_event(
        RendererLoopState(),
        _event("settings_command_received", action=action),
    )

    assert plan.snapshot is snapshot
    assert plan.background_usage is background
    assert plan.domains == domains
    assert plan.force_fast


def test_budget_window_event_requests_usage_insights_refresh() -> None:
    _, plan = reduce_event(RendererLoopState(), _event("budget_window_changed"))

    assert plan.snapshot
    assert plan.force_fast
    assert plan.usage_insights_refresh


def test_event_reducer_coalesces_without_layout_only_work() -> None:
    state = RendererLoopState()
    next_state, plan = reduce_events(
        state,
        [
            _event("renderer_layout_changed", panel="budget"),
            _event("active_session_changed"),
            _event("runtime_error"),
            _event("renderer_theme_changed", theme={"mode": "dark"}),
            _event("background_usage_changed"),
        ],
    )

    assert next_state is state
    assert plan.snapshot
    assert plan.active_session
    assert plan.diagnostics
    assert plan.background_usage
    assert plan.force_fast
    assert plan.theme_payload == {"mode": "dark"}
    assert plan.domains == {
        "diagnostics",
        "settings",
        "backgroundUsage",
        "usageInsights",
    }


def test_layout_and_unknown_events_request_no_python_work() -> None:
    _, plan = reduce_events(
        RendererLoopState(),
        [_event("renderer_layout_changed"), _event("unknown")],
    )

    assert not plan.snapshot
    assert not plan.force_fast
    assert not plan.domains
    assert not plan.background_usage


def test_scheduled_deadlines_choose_next_fake_clock_wake() -> None:
    delay = scheduled_wait_delay(
        2.0,
        now=100.0,
        deadlines=ScheduledDeadlines(
            reminder_in=12.0,
            keepalive_in=8.0,
            daemon_at=106.0,
            active_work_at=103.0,
            background_retry_at=104.0,
            probe_in=5.0,
            heal_in=7.0,
        ),
        idle_wait_enabled=True,
        idle_wait_seconds=60.0,
    )

    assert delay == 3.0


def test_scheduled_deadlines_preserve_failure_backoff_until_earlier_event() -> None:
    assert scheduled_wait_delay(
        0.2,
        now=10.0,
        deadlines=ScheduledDeadlines(),
        update_failures=6,
        failure_limit=2,
    ) == 3.0
    assert scheduled_wait_delay(
        0.2,
        now=10.0,
        deadlines=ScheduledDeadlines(active_work_at=10.01),
        update_failures=6,
        failure_limit=2,
    ) == 0.05


def test_scheduled_deadlines_leave_base_delay_unchanged_without_events() -> None:
    assert scheduled_wait_delay(
        1.25,
        now=50.0,
        deadlines=ScheduledDeadlines(),
    ) == 1.25


def test_runtime_event_normalization_coalesces_file_and_wake_signals() -> None:
    existing = _event("settings_changed", source="bridge")
    batch = normalize_runtime_events(
        [existing],
        file_change_reasons={"session", "settings"},
        file_change_paths={Path("session.jsonl")},
        session_map_changed=False,
        active_session_wake=True,
        current_session="current-session",
        timestamp=12.5,
        path_key=lambda path: "session.jsonl",
    )

    assert [getattr(event, "type") for event in batch.events] == [
        "settings_changed",
        "session_file_changed",
        "active_session_changed",
    ]
    assert batch.events[1].session == "session.jsonl"
    assert batch.events[1].timestamp == 12.5
    assert batch.activity_wake_reason == "session-file"


def test_session_map_event_overrides_existing_activity_reason() -> None:
    batch = normalize_runtime_events(
        [],
        file_change_reasons={"session-map"},
        file_change_paths=set(),
        session_map_changed=True,
        active_session_wake=False,
        current_session="current-session",
        timestamp=3.0,
        path_key=lambda path: "",
        existing_activity_wake_reason="background-usage",
    )

    assert [getattr(event, "type") for event in batch.events] == [
        "active_session_changed"
    ]
    assert batch.events[0].context == {
        "reason": "exact_renderer_mapping_available",
        "paths": [],
    }
    assert batch.activity_wake_reason == "session-map"


def test_event_bus_timestamp_uses_bus_clock_and_falls_back_after_error() -> None:
    assert event_bus_timestamp(
        SimpleNamespace(clock=lambda: 4.5),
        fallback_clock=lambda: 9.0,
    ) == 4.5
    assert event_bus_timestamp(
        SimpleNamespace(clock=lambda: (_ for _ in ()).throw(RuntimeError("boom"))),
        fallback_clock=lambda: 9.0,
    ) == 9.0


def test_event_loop_idle_iteration_does_not_build_scan_or_push() -> None:
    class StopLoop(Exception):
        pass

    counters = {"snapshot": 0, "scan": 0, "cdp_push": 0, "keepalive": 0}
    state = RendererLoopState(latest_snapshot=SimpleNamespace())
    inputs = RendererTickInputs(
        started=0.0,
        update_state={"phase": "idle"},
        bridge_wakeup=False,
        active_session_wakeup=False,
        file_change_reasons=set(),
        file_change_paths=set(),
        command=None,
        budget_window_keys=("day", "week"),
        runtime_events=[],
        event_refresh_request=RefreshPlan(),
    )

    def apply_refresh(inputs: RendererTickInputs, force_fast: bool) -> object:
        counters["snapshot"] += 1
        counters["scan"] += 1
        counters["cdp_push"] += 1
        return SimpleNamespace()

    def apply_domain(inputs: RendererTickInputs) -> bool:
        if inputs.event_refresh_request.domains:
            counters["cdp_push"] += 1
        return False

    loop = RendererEventLoop(
        state,
        RendererLoopExecutorPorts(
            sample_inputs=lambda: inputs,
            apply_inputs=lambda value: None,
            exit_requested=lambda: False,
            restart_requested=lambda: False,
            restart_result=lambda: 10,
            daemon_tick=lambda: None,
            compute_force_fast=lambda value: False,
            apply_refresh=apply_refresh,
            current_snapshot=lambda: state.latest_snapshot,
            apply_domain_update=apply_domain,
            keep_alive=lambda: counters.__setitem__(
                "keepalive", counters["keepalive"] + 1
            ),
            after_iteration=lambda snapshot: None,
            compute_wait_delay=lambda snapshot, value, force_fast: 30.0,
            wait=lambda delay: (_ for _ in ()).throw(StopLoop()),
        ),
    )

    with pytest.raises(StopLoop):
        loop.run()

    assert counters == {
        "snapshot": 0,
        "scan": 0,
        "cdp_push": 0,
        "keepalive": 1,
    }


def test_event_loop_refreshes_local_overlay_during_cdp_backoff_for_file_event() -> None:
    class StopLoop(Exception):
        pass

    latest = SimpleNamespace(name="stale")
    local = SimpleNamespace(name="local")
    state = RendererLoopState(latest_snapshot=latest)
    inputs = RendererTickInputs(
        started=1.0,
        update_state={},
        bridge_wakeup=False,
        active_session_wakeup=False,
        file_change_reasons={"session"},
        file_change_paths={Path("session.jsonl")},
        command=None,
        budget_window_keys=("day", "week"),
        runtime_events=[],
        event_refresh_request=RefreshPlan(snapshot=True),
    )
    calls: list[tuple[str, object]] = []

    loop = RendererEventLoop(
        state,
        RendererLoopExecutorPorts(
            sample_inputs=lambda: inputs,
            apply_inputs=lambda value: None,
            exit_requested=lambda: False,
            restart_requested=lambda: False,
            restart_result=lambda: 10,
            daemon_tick=lambda: None,
            compute_force_fast=lambda value: False,
            apply_refresh=lambda value, force_fast: (_ for _ in ()).throw(
                AssertionError("CDP refresh must remain deferred during backoff")
            ),
            current_snapshot=lambda: latest,
            apply_domain_update=lambda value: False,
            keep_alive=lambda: calls.append(("keepalive", latest)),
            after_iteration=lambda snapshot: calls.append(("after", snapshot)),
            compute_wait_delay=lambda snapshot, value, force_fast: 0.1,
            wait=lambda delay: (_ for _ in ()).throw(StopLoop()),
            update_gate=lambda: (False, "cdp-backoff", 0.5),
            apply_local_refresh=lambda value: calls.append(("local", value)) or local,
        ),
    )

    with pytest.raises(StopLoop):
        loop.run()

    assert state.pending_refresh_plan.snapshot
    assert calls == [("local", inputs), ("keepalive", latest), ("after", local)]


def test_refresh_executor_local_refresh_updates_overlay_without_cdp() -> None:
    latest = ParsedSession(session_id="old-session")
    latest.active_work_items = ["old-item"]
    fresh = ParsedSession(session_id="new-session")
    calls: dict[str, object] = {}
    state = RendererLoopState(latest_snapshot=latest)

    def build_snapshot(kwargs: dict[str, object]) -> ParsedSession:
        calls["kwargs"] = kwargs
        return fresh

    def refresh_current_work(
        items: list[object], snapshot: ParsedSession
    ) -> list[object]:
        calls["current_work"] = (items, snapshot)
        return ["new-item"]

    ports = SimpleNamespace(
        build_snapshot=build_snapshot,
        selection_is_stale=lambda snapshot: False,
        refresh_current_work=refresh_current_work,
        publish_overlay=lambda snapshot: calls.setdefault("overlay", snapshot),
    )
    executor = RendererRefreshExecutor(state, ports)
    inputs = RendererTickInputs(
        started=0.0,
        update_state={},
        bridge_wakeup=False,
        active_session_wakeup=False,
        file_change_reasons={"session"},
        file_change_paths={Path("changed.jsonl"), Path("settings.json")},
        command=None,
        budget_window_keys=("day", "week"),
        runtime_events=[],
        event_refresh_request=RefreshPlan(),
    )

    result = executor.apply_local(inputs)

    assert result is fresh
    assert state.latest_snapshot is fresh
    assert calls["kwargs"] == {
        "refresh_budget_aggregate": False,
        "refresh_budget_paths": (Path("changed.jsonl"),),
        "refresh_active_work_items": False,
        "refresh_current_session_usage": False,
        "refresh_visible_app_error": False,
        "reuse_budget_from": latest,
    }
    assert calls["current_work"] == (["old-item"], fresh)
    assert fresh.active_work_items == ["new-item"]
    assert calls["overlay"] is fresh


def _minimal_inputs() -> RendererTickInputs:
    return RendererTickInputs(
        started=0.0,
        update_state={"phase": "idle"},
        bridge_wakeup=False,
        active_session_wakeup=False,
        file_change_reasons=set(),
        file_change_paths=set(),
        command=None,
        budget_window_keys=("day", "week"),
        runtime_events=[],
        event_refresh_request=RefreshPlan(),
    )


def test_event_loop_quiesce_does_no_snapshot_or_cdp_work() -> None:
    class StopLoop(Exception):
        pass

    calls = {"sample": 0, "apply": 0, "after": 0, "daemon": 0}
    wait_delays: list[float] = []

    def wait(delay: float) -> object:
        wait_delays.append(delay)
        raise StopLoop()

    loop = RendererEventLoop(
        RendererLoopState(),
        RendererLoopExecutorPorts(
            sample_inputs=lambda: calls.__setitem__("sample", calls["sample"] + 1),
            apply_inputs=lambda value: calls.__setitem__("apply", calls["apply"] + 1),
            exit_requested=lambda: False,
            restart_requested=lambda: False,
            restart_result=lambda: 10,
            daemon_tick=lambda: calls.__setitem__("daemon", calls["daemon"] + 1),
            compute_force_fast=lambda value: False,
            apply_refresh=lambda value, force_fast: object(),
            current_snapshot=lambda: None,
            apply_domain_update=lambda value: False,
            keep_alive=lambda: None,
            after_iteration=lambda snapshot: calls.__setitem__(
                "after", calls["after"] + 1
            ),
            compute_wait_delay=lambda snapshot, value, force_fast: 30.0,
            wait=wait,
            quiesce_active=lambda: True,
            quiesce_wait_delay=lambda: 5.0,
        ),
    )

    with pytest.raises(StopLoop):
        loop.run()

    # 静默期间：只做守护检测 + 等待，绝不 sample/apply/after_iteration。
    assert calls == {"sample": 0, "apply": 0, "after": 0, "daemon": 1}
    assert wait_delays == [5.0]


def test_event_loop_quiesce_honors_exit_and_restart() -> None:
    def make_loop(*, exit_now: bool, restart_now: bool) -> int:
        state = RendererLoopState()
        return RendererEventLoop(
            state,
            RendererLoopExecutorPorts(
                sample_inputs=lambda: (_ for _ in ()).throw(AssertionError("no sample")),
                apply_inputs=lambda value: None,
                exit_requested=lambda: exit_now,
                restart_requested=lambda: restart_now,
                restart_result=lambda: 42,
                daemon_tick=lambda: None,
                compute_force_fast=lambda value: False,
                apply_refresh=lambda value, force_fast: object(),
                current_snapshot=lambda: None,
                apply_domain_update=lambda value: False,
                keep_alive=lambda: None,
                after_iteration=lambda snapshot: None,
                compute_wait_delay=lambda snapshot, value, force_fast: 30.0,
                wait=lambda delay: None,
                quiesce_active=lambda: True,
                quiesce_wait_delay=lambda: 5.0,
            ),
        ).run()

    assert make_loop(exit_now=True, restart_now=False) == 0
    assert make_loop(exit_now=False, restart_now=True) == 42


def test_event_loop_quiesce_resume_returns_to_normal_iteration() -> None:
    class StopLoop(Exception):
        pass

    quiesced = [True, True, False]  # 前两次静默，第三次恢复
    calls = {"sample": 0}

    def sample() -> RendererTickInputs:
        calls["sample"] += 1
        raise StopLoop()

    loop = RendererEventLoop(
        RendererLoopState(),
        RendererLoopExecutorPorts(
            sample_inputs=sample,
            apply_inputs=lambda value: None,
            exit_requested=lambda: False,
            restart_requested=lambda: False,
            restart_result=lambda: 10,
            daemon_tick=lambda: None,
            compute_force_fast=lambda value: False,
            apply_refresh=lambda value, force_fast: object(),
            current_snapshot=lambda: None,
            apply_domain_update=lambda value: False,
            keep_alive=lambda: None,
            after_iteration=lambda snapshot: None,
            compute_wait_delay=lambda snapshot, value, force_fast: 30.0,
            wait=lambda delay: None,
            quiesce_active=lambda: quiesced.pop(0) if quiesced else False,
            quiesce_wait_delay=lambda: 5.0,
        ),
    )

    with pytest.raises(StopLoop):
        loop.run()

    # 两次静默（不 sample），恢复后立即进入正常 sample 流程。
    assert calls["sample"] == 1


def test_event_loop_quiesce_uses_dedicated_quiesce_wait() -> None:
    class StopLoop(Exception):
        pass

    used: list[str] = []

    def normal_wait(delay: float) -> object:
        used.append(f"wait:{delay}")
        raise StopLoop()

    def quiesce_wait(delay: float) -> object:
        used.append(f"quiesce_wait:{delay}")
        raise StopLoop()

    loop = RendererEventLoop(
        RendererLoopState(),
        RendererLoopExecutorPorts(
            sample_inputs=lambda: None,
            apply_inputs=lambda value: None,
            exit_requested=lambda: False,
            restart_requested=lambda: False,
            restart_result=lambda: 10,
            daemon_tick=lambda: None,
            compute_force_fast=lambda value: False,
            apply_refresh=lambda value, force_fast: object(),
            current_snapshot=lambda: None,
            apply_domain_update=lambda value: False,
            keep_alive=lambda: None,
            after_iteration=lambda snapshot: None,
            compute_wait_delay=lambda snapshot, value, force_fast: 30.0,
            wait=normal_wait,
            quiesce_active=lambda: True,
            quiesce_wait_delay=lambda: 5.0,
            quiesce_wait=quiesce_wait,
        ),
    )

    with pytest.raises(StopLoop):
        loop.run()

    assert used == ["quiesce_wait:5.0"]


def test_event_loop_quiesce_wait_clears_stale_wake_event_no_busy_spin() -> None:
    """A set wake event must not make the quiesce branch busy-spin.

    The shared wake event is only cleared by the sampler, which the quiesce
    branch never runs. The dedicated quiesce wait clears it first, so even with
    the event stuck in the set state each iteration still blocks for the delay.
    """
    import threading
    import time

    wake = threading.Event()
    wake.set()  # 模拟锁屏瞬间 / 静默期被命令泵置位
    calls = {"daemon": 0}

    def quiesce_wait(delay: float) -> object:
        wake.clear()
        return wake.wait(delay)

    def daemon_tick() -> int | None:
        calls["daemon"] += 1
        if calls["daemon"] >= 2:
            return 99  # 第二次迭代停止循环
        return None

    loop = RendererEventLoop(
        RendererLoopState(),
        RendererLoopExecutorPorts(
            sample_inputs=lambda: None,
            apply_inputs=lambda value: None,
            exit_requested=lambda: False,
            restart_requested=lambda: False,
            restart_result=lambda: 10,
            daemon_tick=daemon_tick,
            compute_force_fast=lambda value: False,
            apply_refresh=lambda value, force_fast: object(),
            current_snapshot=lambda: None,
            apply_domain_update=lambda value: False,
            keep_alive=lambda: None,
            after_iteration=lambda snapshot: None,
            compute_wait_delay=lambda snapshot, value, force_fast: 30.0,
            wait=lambda delay: wake.wait(delay),
            quiesce_active=lambda: True,
            quiesce_wait_delay=lambda: 0.05,
            quiesce_wait=quiesce_wait,
        ),
    )

    started = time.perf_counter()
    result = loop.run()
    elapsed = time.perf_counter() - started
    assert result == 99
    # 事件虽 stuck set，但静默 wait 先 clear，第一轮真正阻塞了 0.05s。
    assert elapsed >= 0.04
    assert wake.is_set() is False  # clear 生效


def test_event_loop_snapshot_plan_runs_refresh_before_wait() -> None:
    class StopLoop(Exception):
        pass

    order: list[str] = []
    state = RendererLoopState(latest_snapshot=SimpleNamespace())
    inputs = RendererTickInputs(
        started=0.0,
        update_state={},
        bridge_wakeup=True,
        active_session_wakeup=False,
        file_change_reasons={"session"},
        file_change_paths=set(),
        command=None,
        budget_window_keys=("day", "week"),
        runtime_events=[],
        event_refresh_request=RefreshPlan(snapshot=True),
    )
    loop = RendererEventLoop(
        state,
        RendererLoopExecutorPorts(
            sample_inputs=lambda: inputs,
            apply_inputs=lambda value: order.append("inputs"),
            exit_requested=lambda: False,
            restart_requested=lambda: False,
            restart_result=lambda: 0,
            daemon_tick=lambda: None,
            compute_force_fast=lambda value: True,
            apply_refresh=lambda value, force: order.append("refresh")
            or SimpleNamespace(),
            current_snapshot=lambda: state.latest_snapshot,
            apply_domain_update=lambda value: False,
            keep_alive=lambda: order.append("keepalive"),
            after_iteration=lambda snapshot: order.append("after"),
            compute_wait_delay=lambda snapshot, value, force: 0.1,
            wait=lambda delay: (_ for _ in ()).throw(StopLoop()),
        ),
    )

    with pytest.raises(StopLoop):
        loop.run()

    assert order == ["inputs", "refresh", "keepalive", "after"]


def _sampler_ports(
    *,
    event_bus: RuntimeEventBus,
    update_state: dict[str, object],
    budget_keys: tuple[str, str] = ("day-1", "week-1"),
    take_active_work: object = None,
    tracker: object | None = None,
    published_work: list[list[object]] | None = None,
    bridge_wake_event: Event | None = None,
    active_session_wake_event: Event | None = None,
    update_state_fn: object = None,
    rest_reminder_fn: object = None,
    budget_window_keys_fn: object = None,
    file_changes_fn: object = None,
) -> RendererTickSamplerPorts:
    work_results = published_work if published_work is not None else []
    return RendererTickSamplerPorts(
        monotonic=lambda: 10.0,
        wall_time=lambda: 20.0,
        take_active_work=(
            take_active_work
            if callable(take_active_work)
            else lambda: None
        ),
        tracker=lambda: tracker,
        stabilize_active_work=lambda items: list(items),
        publish_active_work=lambda items: work_results.append(list(items)),
        update_state=(
            update_state_fn
            if callable(update_state_fn)
            else lambda: dict(update_state)
        ),
        update_state_signature=lambda value: repr(sorted(value.items())),
        rest_reminder=(
            rest_reminder_fn
            if callable(rest_reminder_fn)
            else lambda: None
        ),
        publish_rest_reminder=lambda payload: None,
        publish_event=event_bus.publish,
        current_session=lambda: "session-1",
        budget_window_keys=(
            budget_window_keys_fn
            if callable(budget_window_keys_fn)
            else lambda: budget_keys
        ),
        bridge_wake_event=bridge_wake_event or Event(),
        active_session_wake_event=active_session_wake_event or Event(),
        take_file_changes=(
            file_changes_fn
            if callable(file_changes_fn)
            else lambda: (set(), set())
        ),
        invalidate_mapping=lambda: None,
        take_command=lambda: None,
        drain_events=event_bus.drain,
        event_bus=event_bus,
        path_key=lambda path: str(path or ""),
        background_response_pending=lambda status: False,
    )


def test_tick_sampler_idle_second_sample_has_no_refresh_work() -> None:
    bus = RuntimeEventBus(clock=lambda: 20.0)
    state = RendererLoopState(latest_snapshot=SimpleNamespace(selection_seq=1))
    sampler = RendererTickSampler(
        state,
        _sampler_ports(event_bus=bus, update_state={"phase": "idle"}),
    )

    first = sampler.sample()
    second = sampler.sample()

    assert not first.runtime_events
    assert not second.runtime_events
    assert not second.event_refresh_request.snapshot
    assert not second.event_refresh_request.domains
    assert not second.event_refresh_request.force_fast


def test_tick_sampler_active_session_only_skips_deferred_state_sampling() -> None:
    bus = RuntimeEventBus(clock=lambda: 20.0)
    active_wake = Event()
    active_wake.set()
    calls = {"update": 0, "reminder": 0, "budget": 0}
    state = RendererLoopState(
        latest_snapshot=SimpleNamespace(selection_seq=1),
        latest_update_state={"phase": "idle"},
        latest_budget_window_keys=("day-1", "week-1"),
    )

    inputs = RendererTickSampler(
        state,
        _sampler_ports(
            event_bus=bus,
            update_state={"phase": "checking"},
            active_session_wake_event=active_wake,
            update_state_fn=lambda: calls.__setitem__(
                "update", calls["update"] + 1
            ) or {"phase": "checking"},
            rest_reminder_fn=lambda: calls.__setitem__(
                "reminder", calls["reminder"] + 1
            ),
            budget_window_keys_fn=lambda: calls.__setitem__(
                "budget", calls["budget"] + 1
            ) or ("day-2", "week-2"),
        ),
    ).sample()

    assert inputs.active_session_wakeup
    assert inputs.event_refresh_request.snapshot
    assert inputs.budget_window_keys == ("day-1", "week-1")
    assert calls == {"update": 0, "reminder": 0, "budget": 0}


def test_tick_sampler_active_session_wake_with_other_event_keeps_full_sampling() -> None:
    bus = RuntimeEventBus(clock=lambda: 20.0)
    active_wake = Event()
    active_wake.set()
    calls = {"update": 0, "reminder": 0, "budget": 0}
    state = RendererLoopState(
        latest_snapshot=SimpleNamespace(selection_seq=1),
        latest_update_state_signature="old",
        latest_update_state={"phase": "idle"},
        latest_budget_window_keys=("day-1", "week-1"),
    )
    bus.publish("settings_changed", source="test")

    inputs = RendererTickSampler(
        state,
        _sampler_ports(
            event_bus=bus,
            update_state={"phase": "ready"},
            active_session_wake_event=active_wake,
            update_state_fn=lambda: calls.__setitem__(
                "update", calls["update"] + 1
            ) or {"phase": "ready"},
            rest_reminder_fn=lambda: calls.__setitem__(
                "reminder", calls["reminder"] + 1
            ),
            budget_window_keys_fn=lambda: calls.__setitem__(
                "budget", calls["budget"] + 1
            ) or ("day-2", "week-2"),
        ),
    ).sample()

    assert inputs.active_session_wakeup
    assert inputs.event_refresh_request.snapshot
    assert calls == {"update": 1, "reminder": 1, "budget": 1}


def test_tick_sampler_publishes_only_sequence_matching_active_work() -> None:
    bus = RuntimeEventBus(clock=lambda: 20.0)
    published: list[list[object]] = []
    results = iter([(2, ["stale"]), (3, ["current"])])
    state = RendererLoopState(
        latest_snapshot=SimpleNamespace(selection_seq=3, active_work_items=[])
    )
    tracker = SimpleNamespace(selection_seq=3)
    sampler = RendererTickSampler(
        state,
        _sampler_ports(
            event_bus=bus,
            update_state={"phase": "idle"},
            take_active_work=lambda: next(results),
            tracker=tracker,
            published_work=published,
        ),
    )

    sampler.sample()
    sampler.sample()

    assert published == [["current"]]
    assert state.latest_snapshot.active_work_items == ["current"]


def test_tick_sampler_reprojects_current_turn_before_publishing_old_scan() -> None:
    from datetime import datetime, timedelta, timezone
    from codex_usage_hud.active_work import _refresh_visible_current_work_item
    from codex_usage_hud.core import Activity, RequestTokens, WorkStatusItem

    now = datetime.now(timezone.utc)
    stale = WorkStatusItem(
        id="session-1", session_id="session-1", status="recent",
        title="current", status_label="刚完成", detail="",
    )
    background = WorkStatusItem(
        id="session-2", session_id="session-2", status="tool",
        title="background", status_label="工具执行", detail="",
    )
    snapshot = ParsedSession(
        selection_seq=3, session_id="session-1", task_started_at=now,
        request=RequestTokens(status="running", started_at=now, updated_at=now),
        activity=Activity(kind="user", detail="继续", timestamp=now),
    )
    published = []
    context = SimpleNamespace()
    state = RendererLoopState(latest_snapshot=snapshot)
    ports = replace(
        _sampler_ports(
            event_bus=RuntimeEventBus(), update_state={},
            take_active_work=lambda: (3, [stale, background]),
            tracker=SimpleNamespace(selection_seq=3), published_work=published,
        ),
        refresh_current_work=lambda items, fresh: _refresh_visible_current_work_item(
            context, items, fresh
        ),
    )
    sampler = RendererTickSampler(state, ports)
    sampler.sample()
    assert [(item.id, item.status) for item in published[-1]] == [
        ("session-1", "running"), ("session-2", "tool"),
    ]

    # A later completion must also survive an older running result.
    stale.status = "running"
    stale.task_started_at = now
    snapshot.task_completed_at = now + timedelta(seconds=1)
    sampler.sample()
    assert published[-1][0].status == "recent"


def test_tick_sampler_reduces_changed_update_and_budget_events() -> None:
    bus = RuntimeEventBus(clock=lambda: 20.0)
    state = RendererLoopState(
        latest_snapshot=SimpleNamespace(selection_seq=1),
        latest_update_state_signature="old",
        latest_update_state={"phase": "idle"},
        latest_budget_window_keys=("day-0", "week-0"),
    )
    sampler = RendererTickSampler(
        state,
        _sampler_ports(
            event_bus=bus,
            update_state={"phase": "ready"},
            budget_keys=("day-1", "week-1"),
        ),
    )

    inputs = sampler.sample()

    assert [event.type for event in inputs.runtime_events] == [
        "update_state_changed",
        "budget_window_changed",
    ]
    assert inputs.event_refresh_request.snapshot
    assert inputs.event_refresh_request.force_fast
    assert inputs.event_refresh_request.domains == {"settings"}


def _tick_inputs(
    *,
    plan: RefreshPlan,
    command: dict[str, object] | None = None,
    reasons: set[str] | None = None,
    events: list[object] | None = None,
) -> RendererTickInputs:
    return RendererTickInputs(
        started=0.0,
        update_state={"phase": "idle"},
        bridge_wakeup=False,
        active_session_wakeup=False,
        file_change_reasons=set(reasons or set()),
        file_change_paths=set(),
        command=command,
        budget_window_keys=("day", "week"),
        runtime_events=list(events or []),
        event_refresh_request=plan,
    )


def _pre_refresh_ports(**overrides: object) -> RendererPreRefreshPorts:
    values: dict[str, object] = {
        "current_config": lambda: "config",
        "execute_command": lambda command: {},
        "update_status": lambda: {"phase": "idle"},
        "reset_background_retry": lambda: None,
        "renderer_only_status": lambda message: {"message": message},
        "partial_domains_for_command": lambda command, previous, current: None,
        "request_usage_insights_refresh": lambda: None,
        "refresh_latest_snapshot": lambda command, snapshot, previous, current: None,
        "refresh_usage_insights": lambda: None,
        "overlay_configure": lambda: None,
        "overlay_update": lambda items: None,
        "items_with_background_usage": lambda items: list(items),
        "settings_store": None,
        "apply_config": lambda config, mtime: None,
        "changed_config_keys": lambda previous, current: set(),
        "partial_domains_for_changes": lambda keys: None,
    }
    values.update(overrides)
    return RendererPreRefreshPorts(**values)  # type: ignore[arg-type]


def test_pre_refresh_budget_event_requests_usage_insights_worker() -> None:
    requested = MagicMock()
    executor = RendererPreRefreshExecutor(
        RendererLoopState(),
        _pre_refresh_ports(request_usage_insights_refresh=requested),
    )
    inputs = _tick_inputs(
        plan=RefreshPlan(snapshot=True, usage_insights_refresh=True),
        events=[_event("budget_window_changed")],
    )

    executor.apply(inputs)

    requested.assert_called_once_with()


def test_pre_refresh_command_replaces_full_snapshot_with_partial_domains() -> None:
    config = {"value": "before"}
    refreshed = MagicMock()

    def execute(command: dict[str, object]) -> dict[str, object]:
        config["value"] = "after"
        return {"ok": True}

    state = RendererLoopState(latest_snapshot=SimpleNamespace())
    executor = RendererPreRefreshExecutor(
        state,
        _pre_refresh_ports(
            current_config=lambda: config["value"],
            execute_command=execute,
            partial_domains_for_command=lambda command, previous, current: {
                "settings"
            },
            refresh_latest_snapshot=refreshed,
        ),
    )
    inputs = _tick_inputs(
        plan=RefreshPlan(snapshot=True),
        command={"action": "save"},
    )

    executor.apply_settings_command(inputs)

    assert state.settings_command_status == {"ok": True}
    assert not inputs.event_refresh_request.snapshot
    assert inputs.event_refresh_request.domains == {"settings"}
    refreshed.assert_called_once_with(
        {"action": "save"},
        state.latest_snapshot,
        "before",
        "after",
    )


def test_pre_refresh_async_update_commands_do_not_leave_sticky_command_status() -> None:
    """checkUpdate/installUpdate/updateAction 由 AutoUpdateManager（updateState）
    异步驱动最终状态。execute_command 只返回中间态（如 checking），不能作为粘性
    settings_command_status 保留，否则状态栏会卡在"正在检查更新..."而 loading
    弹窗已随 updateState 终态关闭。"""
    for action in ("checkUpdate", "installUpdate", "updateAction"):
        state = RendererLoopState()
        executor = RendererPreRefreshExecutor(
            state,
            _pre_refresh_ports(
                current_config=lambda: "cfg",
                execute_command=lambda command: {
                    "message": "正在检查更新...",
                    "kind": "",
                    "restartVisible": False,
                },
                update_status=lambda: {"phase": "checking", "message": "正在检查更新..."},
                partial_domains_for_command=lambda command, previous, current: None,
            ),
        )
        inputs = _tick_inputs(
            plan=RefreshPlan(snapshot=True),
            command={"action": action},
        )

        executor.apply_settings_command(inputs)

        assert state.settings_command_status == {}, (
            f"{action} 不应留下粘性 settings_command_status"
        )
        assert inputs.update_state == {"phase": "checking", "message": "正在检查更新..."}


def test_pre_refresh_command_updates_overlay_for_partial_overlay_domain() -> None:
    configured = MagicMock()
    updated = MagicMock()
    state = RendererLoopState(
        latest_snapshot=SimpleNamespace(active_work_items=["session-work"])
    )
    executor = RendererPreRefreshExecutor(
        state,
        _pre_refresh_ports(
            current_config=lambda: "after",
            execute_command=lambda command: {},
            partial_domains_for_command=lambda command, previous, current: {
                "settings",
                "overlay",
            },
            overlay_configure=configured,
            overlay_update=updated,
            items_with_background_usage=lambda items: [*items, "background"],
        ),
    )
    inputs = _tick_inputs(
        plan=RefreshPlan(snapshot=True),
        command={"action": "save"},
    )

    executor.apply_settings_command(inputs)

    configured.assert_called_once_with()
    updated.assert_called_once_with(["session-work", "background"])


def test_pre_refresh_settings_file_updates_overlay_for_partial_overlay_domain() -> None:
    configured = MagicMock()
    updated = MagicMock()
    store = SimpleNamespace(load=lambda: "next", mtime=lambda: 4.0)
    state = RendererLoopState(
        latest_snapshot=SimpleNamespace(active_work_items=["session-work"])
    )
    executor = RendererPreRefreshExecutor(
        state,
        _pre_refresh_ports(
            current_config=lambda: "previous",
            settings_store=store,
            apply_config=MagicMock(),
            changed_config_keys=lambda previous, current: {"work_overlay_side"},
            partial_domains_for_changes=lambda keys: {"settings", "overlay"},
            refresh_latest_snapshot=MagicMock(),
            overlay_configure=configured,
            overlay_update=updated,
            items_with_background_usage=lambda items: [*items, "background"],
        ),
    )
    inputs = _tick_inputs(
        plan=RefreshPlan(snapshot=True),
        reasons={"settings"},
        events=[_event("settings_changed")],
    )

    executor.apply_partial_settings_file_change(inputs)

    configured.assert_called_once_with()
    updated.assert_called_once_with(["session-work", "background"])


def test_pre_refresh_codex_cli_launch_runs_async_and_wakes_renderer() -> None:
    started = Event()
    release = Event()
    woke = Event()

    def execute(command: dict[str, object]) -> dict[str, object]:
        started.set()
        assert release.wait(timeout=1.0)
        return {
            "action": "codexCliLaunch",
            "requestId": command["requestId"],
            "message": "已启动 Codex CLI。",
            "kind": "",
            "codexCliLaunch": {"pid": 42},
        }

    state = RendererLoopState(latest_snapshot=SimpleNamespace())
    executor = RendererPreRefreshExecutor(
        state,
        _pre_refresh_ports(execute_command=execute, wake=woke.set),
    )
    inputs = _tick_inputs(
        plan=RefreshPlan(snapshot=True),
        command={"action": "codexCliLaunch", "requestId": "launch-1"},
    )

    try:
        executor.apply_settings_command(inputs)

        assert started.wait(timeout=1.0)
        assert state.settings_command_status == {
            "action": "codexCliLaunchPending",
            "requestId": "launch-1",
            "message": "正在打开终端并启动 Codex CLI...",
            "kind": "",
            "restartVisible": False,
            "cancellable": True,
        }
        assert inputs.event_refresh_request.domains == {"settings"}
        assert not inputs.event_refresh_request.snapshot

        release.set()
        assert woke.wait(timeout=1.0)

        result_inputs = _tick_inputs(plan=RefreshPlan(snapshot=True))
        executor.apply(result_inputs)

        assert state.settings_command_status["action"] == "codexCliLaunch"
        assert state.settings_command_status["codexCliLaunch"] == {"pid": 42}
        assert result_inputs.event_refresh_request.domains == {"settings"}
        assert not result_inputs.event_refresh_request.snapshot
    finally:
        release.set()
        executor.close()


def test_pre_refresh_codex_cli_cancel_before_spawn_prevents_commit() -> None:
    started = Event()
    release = Event()
    woke = Event()

    def execute(command: dict[str, object]) -> dict[str, object]:
        started.set()
        assert release.wait(timeout=1.0)
        cancel_requested = command["_codexCliCancelRequested"]
        commit_spawn = command["_codexCliCommitSpawn"]
        assert callable(cancel_requested)
        assert callable(commit_spawn)
        assert cancel_requested() is True
        assert commit_spawn() is False
        return {
            "action": "codexCliLaunch",
            "requestId": command["requestId"],
            "message": "已停止 Codex CLI 启动，未创建终端。",
            "kind": "",
            "codexCliLaunchCancelled": True,
        }

    state = RendererLoopState(latest_snapshot=SimpleNamespace())
    executor = RendererPreRefreshExecutor(
        state,
        _pre_refresh_ports(execute_command=execute, wake=woke.set),
    )
    launch_inputs = _tick_inputs(
        plan=RefreshPlan(snapshot=True),
        command={"action": "codexCliLaunch", "requestId": "launch-cancel"},
    )

    try:
        executor.apply_settings_command(launch_inputs)
        assert started.wait(timeout=1.0)
        cancel_inputs = _tick_inputs(
            plan=RefreshPlan(snapshot=True),
            command={
                "action": "codexCliLaunchCancel",
                "requestId": "cancel-1",
                "launchRequestId": "launch-cancel",
            },
        )
        executor.apply_settings_command(cancel_inputs)

        assert state.settings_command_status["action"] == "codexCliLaunchCancel"
        assert state.settings_command_status["cancelAccepted"] is True
        assert state.settings_command_status["spawnCommitted"] is False
        release.set()
        assert woke.wait(timeout=1.0)

        result_inputs = _tick_inputs(plan=RefreshPlan(snapshot=True))
        executor.apply(result_inputs)
        assert state.settings_command_status["codexCliLaunchCancelled"] is True
    finally:
        release.set()
        executor.close()


def test_pre_refresh_codex_cli_cancel_after_spawn_commit_is_rejected() -> None:
    committed = Event()
    release = Event()

    def execute(command: dict[str, object]) -> dict[str, object]:
        commit_spawn = command["_codexCliCommitSpawn"]
        assert callable(commit_spawn)
        assert commit_spawn() is True
        committed.set()
        assert release.wait(timeout=1.0)
        return {
            "action": "codexCliLaunch",
            "requestId": command["requestId"],
            "message": "已启动 Codex CLI。",
            "kind": "",
            "codexCliLaunch": {"pid": 43},
        }

    state = RendererLoopState(latest_snapshot=SimpleNamespace())
    executor = RendererPreRefreshExecutor(
        state,
        _pre_refresh_ports(execute_command=execute),
    )
    launch_inputs = _tick_inputs(
        plan=RefreshPlan(snapshot=True),
        command={"action": "codexCliLaunch", "requestId": "launch-committed"},
    )

    try:
        executor.apply_settings_command(launch_inputs)
        assert committed.wait(timeout=1.0)
        cancel_inputs = _tick_inputs(
            plan=RefreshPlan(snapshot=True),
            command={
                "action": "codexCliLaunchCancel",
                "requestId": "cancel-too-late",
                "launchRequestId": "launch-committed",
            },
        )
        executor.apply_settings_command(cancel_inputs)

        assert state.settings_command_status["cancelAccepted"] is False
        assert state.settings_command_status["spawnCommitted"] is True
        assert state.settings_command_status["kind"] == "warning"
    finally:
        release.set()
        executor.close()


def test_pre_refresh_request_rows_command_advances_only_the_page_limit() -> None:
    executed = MagicMock()
    state = RendererLoopState(request_rows_limit=30)
    executor = RendererPreRefreshExecutor(
        state,
        _pre_refresh_ports(execute_command=executed),
    )
    inputs = _tick_inputs(
        plan=RefreshPlan(snapshot=True),
        command={"action": "loadMoreRequestRows"},
    )

    executor.apply_settings_command(inputs)

    assert state.request_rows_limit == 60
    assert state.settings_command_status == {}
    executed.assert_not_called()


def test_pre_refresh_background_event_updates_overlay_without_snapshot() -> None:
    refreshed = MagicMock()
    configured = MagicMock()
    updated = MagicMock()
    state = RendererLoopState(
        latest_snapshot=SimpleNamespace(active_work_items=["session-work"])
    )
    executor = RendererPreRefreshExecutor(
        state,
        _pre_refresh_ports(
            refresh_usage_insights=refreshed,
            overlay_configure=configured,
            overlay_update=updated,
            items_with_background_usage=lambda items: [*items, "background"],
        ),
    )
    inputs = _tick_inputs(
        plan=RefreshPlan(background_usage=True),
        events=[_event("background_usage_changed")],
    )

    executor.apply_background_usage_change(inputs)

    refreshed.assert_called_once_with()
    configured.assert_called_once_with()
    updated.assert_called_once_with(["session-work", "background"])
    assert state.activity_wake_pending == "background-usage"


def test_pre_refresh_settings_only_event_uses_partial_domain_update() -> None:
    store = SimpleNamespace(load=lambda: "next", mtime=lambda: 4.0)
    applied = MagicMock()
    refreshed = MagicMock()
    state = RendererLoopState(latest_snapshot=SimpleNamespace())
    executor = RendererPreRefreshExecutor(
        state,
        _pre_refresh_ports(
            current_config=lambda: "previous",
            settings_store=store,
            apply_config=applied,
            changed_config_keys=lambda previous, current: {"theme"},
            partial_domains_for_changes=lambda keys: {"settings"},
            refresh_latest_snapshot=refreshed,
        ),
    )
    inputs = _tick_inputs(
        plan=RefreshPlan(snapshot=True),
        reasons={"settings"},
        events=[_event("settings_changed")],
    )

    executor.apply_partial_settings_file_change(inputs)

    applied.assert_called_once_with("next", 4.0)
    refreshed.assert_called_once_with(
        {"action": "save"},
        state.latest_snapshot,
        "previous",
        "next",
    )
    assert not inputs.event_refresh_request.snapshot
    assert inputs.event_refresh_request.domains == {"settings"}


def test_snapshot_refresh_decision_builds_initial_full_refresh_kwargs() -> None:
    decision = snapshot_refresh_decision(
        _tick_inputs(plan=RefreshPlan(snapshot=True)),
        latest_snapshot=None,
        latest_budget_signature=None,
        budget_signature=("budget",),
        latest_active_work_refresh_at=0.0,
        active_work_refresh_pending=False,
        active_work_refresh_not_before=0.0,
        now_monotonic=10.0,
        active_work_rescan_seconds=5.0,
        has_settings_command_status=False,
        path_key=lambda path: str(path or ""),
    )

    assert decision.refresh_budget_aggregate
    assert decision.refresh_active_work_items
    assert not decision.lightweight_active_session
    assert decision.snapshot_kwargs == {
        "refresh_budget_aggregate": True,
        "refresh_budget_paths": (),
        "refresh_active_work_items": True,
        "scan_active_work_candidates": False,
    }


def test_snapshot_refresh_decision_preserves_visible_first_contract() -> None:
    latest = ParsedSession(selection_seq=4)
    inputs = _tick_inputs(plan=RefreshPlan(active_session=True))
    inputs.active_session_wakeup = True
    inputs.file_change_reasons = {"sessions-root"}
    inputs.file_change_paths = {Path("session.jsonl")}
    decision = snapshot_refresh_decision(
        inputs,
        latest_snapshot=latest,
        latest_budget_signature=("budget",),
        budget_signature=("budget",),
        latest_active_work_refresh_at=0.0,
        active_work_refresh_pending=True,
        active_work_refresh_not_before=20.0,
        now_monotonic=10.0,
        active_work_rescan_seconds=5.0,
        has_settings_command_status=False,
        path_key=lambda path: str(path or ""),
    )

    assert decision.lightweight_active_session
    assert not decision.refresh_budget_aggregate
    assert decision.refresh_budget_paths == ()
    assert not decision.refresh_active_work_items
    assert decision.snapshot_kwargs == {
        "refresh_budget_aggregate": False,
        "refresh_budget_paths": (),
        "refresh_active_work_items": False,
        "scan_active_work_candidates": True,
        "reuse_budget_from": latest,
        "refresh_visible_app_error": False,
        "refresh_current_session_usage": False,
    }


def test_snapshot_refresh_decision_uses_incremental_paths_and_hydration() -> None:
    latest = ParsedSession()
    inputs = _tick_inputs(
        plan=RefreshPlan(snapshot=True),
        reasons={"sessions-root"},
        events=[_event("session_snapshot_hydrated")],
    )
    inputs.file_change_paths = {Path("b.jsonl"), Path("a.jsonl")}
    decision = snapshot_refresh_decision(
        inputs,
        latest_snapshot=latest,
        latest_budget_signature=("budget",),
        budget_signature=("budget",),
        latest_active_work_refresh_at=9.0,
        active_work_refresh_pending=False,
        active_work_refresh_not_before=0.0,
        now_monotonic=10.0,
        active_work_rescan_seconds=5.0,
        has_settings_command_status=False,
        path_key=lambda path: str(path or ""),
    )

    assert not decision.refresh_budget_aggregate
    assert decision.refresh_budget_paths == (Path("a.jsonl"), Path("b.jsonl"))
    assert decision.active_work_paths == (Path("a.jsonl"), Path("b.jsonl"))
    assert decision.hydrated_session
    assert decision.snapshot_kwargs["refresh_current_session_usage"] is False


def _refresh_ports(
    *,
    fresh: ParsedSession,
    push_lightweight: MagicMock | None = None,
    push_full: MagicMock | None = None,
    publish_overlay: MagicMock | None = None,
    stale: bool = False,
    connection_success: MagicMock | None = None,
    connection_failure: MagicMock | None = None,
    record_failure: MagicMock | None = None,
    wake: MagicMock | None = None,
    capture_observation: MagicMock | None = None,
    acknowledge_observation: MagicMock | None = None,
    retry_observation: MagicMock | None = None,
    request_active_work: MagicMock | None = None,
) -> RendererRefreshPorts:
    return RendererRefreshPorts(
        monotonic=lambda: 10.0,
        wall_time=lambda: 20.0,
        perf_counter=lambda: 1.0,
        budget_signature=lambda: ("budget",),
        build_snapshot=lambda kwargs: fresh,
        selection_is_stale=lambda snapshot: stale,
        current_selection_seq=lambda: 9,
        refresh_current_work=lambda items, snapshot: list(items),
        request_active_work=request_active_work or MagicMock(return_value=True),
        update_snapshot_activity=lambda snapshot: None,
        push_lightweight=push_lightweight or MagicMock(return_value=True),
        push_full=push_full or MagicMock(return_value=True),
        build_domain_payload=lambda snapshot, inputs: {},
        push_domain_payload=lambda payload: True,
        publish_overlay=publish_overlay or MagicMock(),
        update_metrics=lambda: {},
        connection_success=connection_success or MagicMock(),
        connection_failure=connection_failure or MagicMock(),
        sync_connection=lambda snapshot: None,
        wake=wake or MagicMock(),
        background_response_pending=lambda status: False,
        reset_background_retry=lambda: None,
        schedule_background_retry=lambda: None,
        resolve_update_failure=lambda: None,
        record_update_failure=record_failure or MagicMock(),
        client_status=lambda: "status",
        client_error=lambda: "error",
        path_key=lambda path: str(path or ""),
        active_work_rescan_seconds=5.0,
        active_work_after_session_seconds=1.2,
        slow_operation_ms=250.0,
        capture_active_session_observation=capture_observation or MagicMock(return_value=None),
        acknowledge_active_session_update=acknowledge_observation or MagicMock(),
        retry_active_session_update=retry_observation or MagicMock(),
    )


def test_refresh_executor_builds_and_publishes_initial_snapshot() -> None:
    fresh = ParsedSession(active_work_items=["work"])
    push_full = MagicMock(return_value=True)
    publish_overlay = MagicMock()
    connection_success = MagicMock()
    state = RendererLoopState()
    executor = RendererRefreshExecutor(
        state,
        _refresh_ports(
            fresh=fresh,
            push_full=push_full,
            publish_overlay=publish_overlay,
            connection_success=connection_success,
        ),
    )
    inputs = _tick_inputs(plan=RefreshPlan(snapshot=True))

    assert executor.apply(inputs, False) is fresh

    push_full.assert_called_once_with(fresh, inputs)
    publish_overlay.assert_called_once_with(fresh)
    connection_success.assert_called_once_with()
    assert state.latest_snapshot is fresh
    assert state.failures == 0
    assert fresh.follow_timing == {
        "snapshotStartedAt": 20_000,
        "snapshotBuiltAt": 20_000,
        "payloadSendStartedAt": 20_000,
    }


def test_refresh_executor_refreshes_current_bubble_on_visible_first_session_update() -> None:
    latest = ParsedSession(active_work_items=["old-work"])
    fresh = ParsedSession(selection_seq=2)
    refresh_current_work = MagicMock(return_value=["sending-work"])
    ports = replace(
        _refresh_ports(fresh=fresh),
        refresh_current_work=refresh_current_work,
    )
    state = RendererLoopState(latest_snapshot=latest, latest_budget_signature=("budget",))
    executor = RendererRefreshExecutor(state, ports)
    inputs = _tick_inputs(plan=RefreshPlan(active_session=True))
    inputs.active_session_wakeup = True

    assert executor.apply(inputs, False) is fresh

    refresh_current_work.assert_called_once_with(["old-work"], fresh)
    assert fresh.active_work_items == ["sending-work"]


def test_refresh_executor_passes_changed_jsonl_to_active_work() -> None:
    latest = ParsedSession(active_work_items=["existing"])
    fresh = ParsedSession(session_id="session-2")
    request_active_work = MagicMock(return_value=True)
    state = RendererLoopState(
        latest_snapshot=latest,
        latest_budget_signature=("budget",),
    )
    executor = RendererRefreshExecutor(
        state,
        _refresh_ports(
            fresh=fresh,
            request_active_work=request_active_work,
        ),
    )
    inputs = _tick_inputs(
        plan=RefreshPlan(snapshot=True),
        reasons={"sessions-root"},
    )
    changed_path = Path("resumed.jsonl")
    inputs.file_change_paths = {changed_path, Path("hud-settings.json")}

    assert executor.apply(inputs, False) is fresh

    request_active_work.assert_called_once_with(fresh, (changed_path,))


def test_refresh_executor_retains_changed_jsonl_until_deferred_active_work() -> None:
    latest = ParsedSession(active_work_items=["existing"])
    fresh = ParsedSession(session_id="session-2")
    request_active_work = MagicMock(return_value=True)
    state = RendererLoopState(
        latest_snapshot=latest,
        latest_budget_signature=("budget",),
        active_work_refresh_pending=True,
        active_work_refresh_not_before=20.0,
    )
    executor = RendererRefreshExecutor(
        state,
        _refresh_ports(
            fresh=fresh,
            request_active_work=request_active_work,
        ),
    )
    changed_path = Path("resumed.jsonl")
    suppressed_inputs = _tick_inputs(
        plan=RefreshPlan(snapshot=True),
        reasons={"sessions-root"},
    )
    suppressed_inputs.file_change_paths = {changed_path}

    assert executor.apply(suppressed_inputs, False) is fresh
    request_active_work.assert_not_called()
    assert state.pending_active_work_paths == (changed_path,)

    state.active_work_refresh_not_before = 0.0
    deferred_inputs = _tick_inputs(plan=RefreshPlan(snapshot=True))

    assert executor.apply(deferred_inputs, False) is fresh
    request_active_work.assert_called_once_with(fresh, (changed_path,))
    assert state.pending_active_work_paths == ()


def test_refresh_executor_keeps_changed_jsonl_when_active_work_rejects_it() -> None:
    latest = ParsedSession(active_work_items=["existing"])
    fresh = ParsedSession(session_id="session-2")
    request_active_work = MagicMock(return_value=False)
    state = RendererLoopState(
        latest_snapshot=latest,
        latest_budget_signature=("budget",),
    )
    executor = RendererRefreshExecutor(
        state,
        _refresh_ports(
            fresh=fresh,
            request_active_work=request_active_work,
        ),
    )
    changed_path = Path("resumed.jsonl")
    inputs = _tick_inputs(
        plan=RefreshPlan(snapshot=True),
        reasons={"sessions-root"},
    )
    inputs.file_change_paths = {changed_path}

    assert executor.apply(inputs, False) is fresh

    request_active_work.assert_called_once_with(fresh, (changed_path,))
    assert state.pending_active_work_paths == (changed_path,)
    assert state.active_work_refresh_pending
    assert state.active_work_refresh_not_before == 11.2


def test_refresh_executor_queues_changed_jsonl_for_initial_snapshot() -> None:
    fresh = ParsedSession(session_id="session-1")
    request_active_work = MagicMock(return_value=True)
    state = RendererLoopState()
    executor = RendererRefreshExecutor(
        state,
        _refresh_ports(
            fresh=fresh,
            request_active_work=request_active_work,
        ),
    )
    changed_path = Path("resumed.jsonl")
    inputs = _tick_inputs(
        plan=RefreshPlan(snapshot=True),
        reasons={"sessions-root"},
    )
    inputs.file_change_paths = {changed_path}

    assert executor.apply(inputs, False) is fresh

    request_active_work.assert_called_once_with(fresh, (changed_path,))
    assert state.pending_active_work_paths == ()


def test_refresh_executor_visible_first_schedules_deferred_active_work() -> None:
    latest = ParsedSession(active_work_items=["existing"])
    fresh = ParsedSession(session_id="session-2")
    push_light = MagicMock(return_value=True)
    push_full = MagicMock(return_value=True)
    publish_overlay = MagicMock()
    wake = MagicMock()
    state = RendererLoopState(
        latest_snapshot=latest,
        latest_budget_signature=("budget",),
    )
    executor = RendererRefreshExecutor(
        state,
        _refresh_ports(
            fresh=fresh,
            push_lightweight=push_light,
            push_full=push_full,
            publish_overlay=publish_overlay,
            wake=wake,
        ),
    )
    inputs = _tick_inputs(plan=RefreshPlan(active_session=True))
    inputs.active_session_wakeup = True

    executor.apply(inputs, True)

    push_light.assert_called_once_with(fresh)
    push_full.assert_not_called()
    publish_overlay.assert_called_once_with(fresh)
    assert fresh.active_work_items == ["existing"]
    assert state.active_work_refresh_pending
    assert state.active_work_refresh_not_before == 11.2
    wake.assert_called_once_with()


def test_refresh_executor_stale_snapshot_is_rejected_before_push() -> None:
    latest = ParsedSession(selection_seq=9)
    fresh = ParsedSession(selection_seq=8)
    push_full = MagicMock(return_value=True)
    state = RendererLoopState(
        latest_snapshot=latest,
        latest_budget_signature=("budget",),
    )
    executor = RendererRefreshExecutor(
        state,
        _refresh_ports(fresh=fresh, push_full=push_full, stale=True),
    )

    result = executor.apply(_tick_inputs(plan=RefreshPlan(snapshot=True)), False)

    assert result is latest
    assert state.latest_snapshot is latest
    push_full.assert_not_called()


def test_refresh_executor_requeues_active_session_after_stale_snapshot() -> None:
    latest = ParsedSession(selection_seq=9)
    fresh = ParsedSession(selection_seq=8)
    acknowledge = MagicMock()
    retry = MagicMock()
    state = RendererLoopState(
        latest_snapshot=latest,
        latest_budget_signature=("budget",),
    )
    executor = RendererRefreshExecutor(
        state,
        _refresh_ports(
            fresh=fresh,
            stale=True,
            capture_observation=MagicMock(return_value=(8, "session-1")),
            acknowledge_observation=acknowledge,
            retry_observation=retry,
        ),
    )

    result = executor.apply(_tick_inputs(plan=RefreshPlan(snapshot=True)), False)

    assert result is latest
    acknowledge.assert_called_once_with(8, (8, "session-1"), False)
    retry.assert_called_once_with()


def test_refresh_executor_records_failed_push() -> None:
    fresh = ParsedSession()
    connection_failure = MagicMock()
    record_failure = MagicMock()
    state = RendererLoopState()
    executor = RendererRefreshExecutor(
        state,
        _refresh_ports(
            fresh=fresh,
            push_full=MagicMock(return_value=False),
            connection_failure=connection_failure,
            record_failure=record_failure,
        ),
    )

    executor.apply(_tick_inputs(plan=RefreshPlan(snapshot=True)), False)

    assert state.failures == 1
    connection_failure.assert_called_once_with()
    record_failure.assert_called_once_with(1)


def test_refresh_executor_applies_partial_domain_and_clears_matching_response() -> None:
    snapshot = ParsedSession()
    reset_retry = MagicMock()
    connection_success = MagicMock()
    state = RendererLoopState(
        latest_snapshot=snapshot,
        settings_command_status={"backgroundUsageResponse": {"ok": True}},
        failures=2,
    )
    base_ports = _refresh_ports(
        fresh=snapshot,
        connection_success=connection_success,
    )
    ports = replace(
        base_ports,
        build_domain_payload=lambda current, inputs: {"payloadDomains": {}},
        push_domain_payload=lambda payload: True,
        reset_background_retry=reset_retry,
    )
    executor = RendererRefreshExecutor(state, ports)
    inputs = _tick_inputs(
        plan=RefreshPlan(domains={"backgroundUsage"}),
    )

    assert executor.apply_domains(inputs)
    assert state.settings_command_status == {}
    assert state.failures == 0
    reset_retry.assert_called_once_with()
    connection_success.assert_called_once_with()


def _session_index_settings_domain(
    *,
    session_index: dict[str, object],
    command_status: dict[str, object],
) -> dict[str, object]:
    return payload_from_snapshot(
        ParsedSession(),
        session_index=dict(session_index),
        settings_command_status=dict(command_status),
    ).to_domain_json("settings")


def _renderer_session_index_choice(
    domain_payload: dict[str, object],
) -> dict[str, object] | None:
    """Mirror the renderer's selection rule (``sessionIndexDomainState``).

    ``settingsCommandStatus.sessionIndex`` is a one-shot command response and
    wins while present; the live ``sessionIndex`` key is the fallback.
    """
    status = domain_payload.get("settingsCommandStatus")
    status_index = status.get("sessionIndex") if isinstance(status, dict) else None
    if isinstance(status_index, dict) and status_index:
        return status_index
    live = domain_payload.get("sessionIndex")
    return live if isinstance(live, dict) and live else None


def test_session_index_progress_is_not_shadowed_by_command_ack() -> None:
    """Regression: 扩展索引后进度不刷新，只在下一次全量刷新时跳到最终态。

    ``settingsCommandStatus.sessionIndex`` 是一次性命令响应，``sessionIndex``
    是随 warm job 推进的实时通道。渲染端优先读前者，而
    ``settings_command_status`` 只在全量刷新成功后才清空 —— 于是命令响应会一直
    遮蔽实时通道，进度帧全部被丢弃，直到某次全量刷新清空它才显示最终态。
    """
    ack: dict[str, object] = {
        "jobState": "running",
        "coverage": "partial(3m)",
        "builtCount": 0,
        "totalCount": 0,
        "selectedRange": "3m",
        "accepted": True,
        "requestId": "req-1",
    }
    live: dict[str, object] = {
        "jobState": "idle",
        "coverage": "range_done(1m)",
        "builtCount": 120,
        "totalCount": 120,
        "selectedRange": "1m",
    }
    state = RendererLoopState(
        latest_snapshot=ParsedSession(),
        settings_command_status={"status": "", "kind": "", "sessionIndex": dict(ack)},
    )
    live_box = {"value": dict(live)}
    pushed: list[dict[str, object]] = []
    ports = replace(
        _refresh_ports(fresh=ParsedSession()),
        build_domain_payload=lambda snapshot, inputs: _session_index_settings_domain(
            session_index=live_box["value"],
            command_status=state.settings_command_status,
        ),
        push_domain_payload=lambda payload: pushed.append(payload) or True,
    )
    executor = RendererRefreshExecutor(state, ports)
    inputs = _tick_inputs(plan=RefreshPlan(domains={"settings"}))

    # 命令响应帧：ack 优先，UI 立刻从"最近1个月"切到"索引中"。
    assert executor.apply_domains(inputs)
    assert _renderer_session_index_choice(pushed[-1]) == ack

    # warm job 推进后的每一帧都必须由实时通道决定，而不是停在 ack 上。
    for built in (260, 401, 542):
        advanced = dict(ack, builtCount=built, totalCount=542)
        live_box["value"] = advanced
        assert executor.apply_domains(inputs)
        assert _renderer_session_index_choice(pushed[-1]) == advanced


def test_wait_planner_collects_owner_deadlines_with_fake_clock() -> None:
    state = RendererLoopState(
        active_work_refresh_pending=True,
        active_work_refresh_not_before=12.0,
    )
    planner = RendererWaitPlanner(
        state,
        RendererWaitPorts(
            monotonic=lambda: 10.0,
            base_delay=lambda snapshot, elapsed, force_fast: 1.0,
            idle_wait_enabled=lambda snapshot, update, delay, force: True,
            reminder_in=lambda: 8.0,
            keepalive_in=lambda: 6.0,
            daemon_at=lambda: 15.0,
            failure_limit=lambda: 3,
            background_response_pending=lambda status: False,
            probe_in=lambda: 4.0,
            heal_in=lambda: 5.0,
            idle_wait_seconds=30.0,
        ),
    )
    inputs = _tick_inputs(plan=RefreshPlan())
    inputs.started = 9.5

    assert planner.compute(SimpleNamespace(), inputs, False) == 2.0
