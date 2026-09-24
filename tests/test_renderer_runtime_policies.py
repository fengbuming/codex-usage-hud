from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from codex_usage_hud import renderer_runtime, renderer_runtime_policies
from codex_usage_hud import runtime_policies
from codex_usage_hud import runtime_compat
from codex_usage_hud.config import UserConfig


_OWNER_NAMES = (
    "RENDERER_IDLE_POLL_MS",
    "RENDERER_ACTIVE_WORK_RESCAN_SECONDS",
    "RENDERER_UPDATE_FAILURE_LIMIT",
    "AUTO_RENDERER_TIMEOUT_FAILURE_LIMIT",
    "_renderer_update_failure_limit",
    "_renderer_refresh_delay_seconds",
    "_renderer_initial_failure_can_be_fixed_by_restart",
    "_renderer_initial_failure_should_recover_cdp_port",
    "_valid_renderer_cdp_port",
    "_remote_debugging_ports_from_command_line",
    "_json_signature",
    "_path_stat_signature",
    "_renderer_runtime_signature",
    "_renderer_budget_window_keys",
    "_renderer_budget_signature",
    "_paths_only_current_session",
    "_renderer_budget_refresh_paths",
    "_renderer_should_refresh_budget_aggregate",
    "_renderer_should_refresh_active_work_items",
    "_renderer_snapshot_selection_is_stale",
    "_renderer_active_session_observation_should_refresh",
    "_renderer_should_use_visible_first_active_session",
    "_renderer_deferred_active_work_refresh_due",
    "_renderer_event_idle_wait_enabled",
)


def test_policy_symbols_have_one_direct_owner_and_legacy_aliases() -> None:
    for name in _OWNER_NAMES:
        owner = getattr(renderer_runtime_policies, name)
        assert getattr(renderer_runtime, name) is owner
        assert runtime_compat.resolve(name) is owner


def test_policy_failure_and_port_adapters_preserve_contract() -> None:
    assert renderer_runtime_policies._renderer_update_failure_limit(
        "auto", "connection timed out"
    ) == renderer_runtime_policies.RENDERER_UPDATE_FAILURE_LIMIT
    assert renderer_runtime_policies._renderer_update_failure_limit(
        "renderer", "connection timed out"
    ) == renderer_runtime_policies.RENDERER_UPDATE_FAILURE_LIMIT
    assert renderer_runtime_policies._renderer_initial_failure_should_recover_cdp_port(
        "connection refused"
    )
    assert not renderer_runtime_policies._renderer_initial_failure_can_be_fixed_by_restart(
        "timed out"
    )
    assert renderer_runtime_policies._remote_debugging_ports_from_command_line(
        "--remote-debugging-port=9222 --remote-debugging-port 9333 "
        "--remote-debugging-port=9222 --remote-debugging-port=70000"
    ) == (9222, 9333)


def test_policy_invalidation_adapters_keep_incremental_jsonl_contract() -> None:
    current = Path("2026/08/03/session-a.jsonl")
    other = Path("2026/08/03/session-b.jsonl")
    assert renderer_runtime_policies._paths_only_current_session({current}, current)
    assert not renderer_runtime_policies._paths_only_current_session(
        {current, other}, current
    )
    assert renderer_runtime_policies._renderer_budget_refresh_paths(
        {other, current}
    ) == (current, other)
    assert renderer_runtime_policies._renderer_budget_refresh_paths(
        {current, Path("settings.json")}
    ) == ()


def test_budget_signature_changes_with_pricing() -> None:
    estimator = SimpleNamespace(pricing_fingerprint="old")
    context = SimpleNamespace(
        sessions_root=Path("sessions"),
        user_config=UserConfig.defaults(),
        parser=SimpleNamespace(cost_estimator=estimator),
    )

    before = renderer_runtime_policies._renderer_budget_signature(context)
    estimator.pricing_fingerprint = "new"

    assert renderer_runtime_policies._renderer_budget_signature(context) != before


def test_policy_idle_wait_adapter_only_wakes_for_event_driven_idle_state() -> None:
    file_events = SimpleNamespace(event_driven=True)
    assert renderer_runtime_policies._renderer_event_idle_wait_enabled(
        file_events,
        object(),
        {"phase": "idle"},
        1.0,
        force_fast=False,
    )
    assert not renderer_runtime_policies._renderer_event_idle_wait_enabled(
        file_events,
        object(),
        {"phase": "checking"},
        1.0,
        force_fast=False,
    )
    assert not renderer_runtime_policies._renderer_event_idle_wait_enabled(
        file_events,
        object(),
        {"phase": "idle"},
        1.0,
        force_fast=True,
    )


def test_active_session_policy_deduplicates_only_after_matching_ack() -> None:
    key = (7, "session-1", "renderer-1", "Session", False, False)

    assert not runtime_policies.active_session_observation_should_refresh(
        changed=False,
        selection_seq=7,
        current_seq=7,
        observation_key=key,
        previous_observation_key=key,
        applied_seq=7,
        applied_observation_key=key,
        refresh_pending=False,
    )
    assert not runtime_policies.active_session_observation_should_refresh(
        changed=False,
        selection_seq=7,
        current_seq=7,
        observation_key=key,
        previous_observation_key=key,
        applied_seq=7,
        applied_observation_key=None,
        refresh_pending=True,
    )
    assert runtime_policies.active_session_observation_should_refresh(
        changed=False,
        selection_seq=7,
        current_seq=7,
        observation_key=key,
        previous_observation_key=key,
        applied_seq=7,
        applied_observation_key=None,
        refresh_pending=False,
    )
    assert not runtime_policies.active_session_observation_should_refresh(
        changed=True,
        selection_seq=7,
        current_seq=7,
        observation_key=key,
        previous_observation_key=key,
        applied_seq=0,
        applied_observation_key=None,
        refresh_pending=True,
    )


def test_active_session_policy_refreshes_identity_changes_and_newer_sequences() -> None:
    old_key = (7, "session-1", "renderer-1", "Session", False, False)
    renamed_key = (7, "session-1", "renderer-1", "Renamed", False, False)
    newer_key = (8, "session-2", "renderer-2", "Other", False, False)

    assert runtime_policies.active_session_observation_should_refresh(
        changed=False,
        selection_seq=7,
        current_seq=7,
        observation_key=renamed_key,
        previous_observation_key=old_key,
        applied_seq=7,
        applied_observation_key=old_key,
    )
    assert runtime_policies.active_session_observation_should_refresh(
        changed=False,
        selection_seq=8,
        current_seq=8,
        observation_key=newer_key,
        previous_observation_key=old_key,
        applied_seq=7,
        applied_observation_key=old_key,
    )
    assert not runtime_policies.active_session_observation_should_refresh(
        changed=False,
        selection_seq=7,
        current_seq=8,
        observation_key=old_key,
        previous_observation_key=old_key,
        applied_seq=7,
        applied_observation_key=old_key,
    )
