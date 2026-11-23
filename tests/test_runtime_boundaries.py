from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
from dataclasses import replace

import pytest

from codex_usage_hud import (
    overlay_projection,
    overlay_ipc,
    runtime_policies,
    runtime_settings,
    runtime_usage,
)
from codex_usage_hud.config import UserConfig
from codex_usage_hud.core import UsageSummary
from codex_usage_hud.usage_contributions import (
    ContributionIndex,
    FileUsageContribution,
    canonical_usage_path,
    path_under_usage_roots,
    usage_scan_roots,
)


def test_overlay_ipc_derives_sidecar_paths() -> None:
    state_path = Path("work-overlay-1.json")

    assert overlay_ipc.command_path(state_path) == Path(
        "work-overlay-1-commands.jsonl"
    )
    assert overlay_ipc.heartbeat_path(state_path) == Path("work-overlay-1-heartbeat")
    assert overlay_ipc.ack_path(state_path) == Path("work-overlay-1-acks.jsonl")
    assert overlay_ipc.transition_audit_path(Path("runtime")) == Path(
        "runtime/work-overlay-transitions.jsonl"
    )


def test_overlay_ipc_versioned_messages_round_trip_without_changing_flat_fields() -> None:
    state = overlay_ipc.state_message(
        ownerPid=7,
        items=[{"id": "session-1"}],
        revision=3,
        producerInstanceId="producer-1",
        close=False,
    )
    command = overlay_ipc.command_message(
        action="activateSession",
        sessionId="session-1",
        producerInstanceId="helper-1",
    )
    ack = overlay_ipc.ack_message(
        requestId=command["requestId"],
        action=command["action"],
        status="completed",
    )
    transition = overlay_ipc.transition_message(
        transition="card_to_completed",
        stateRevision=3,
        producerInstanceId="producer-1",
    )

    assert overlay_ipc.parse_state(state)["items"] == [{"id": "session-1"}]
    assert overlay_ipc.parse_command(command)["sessionId"] == "session-1"
    assert overlay_ipc.parse_ack(ack)["requestId"] == command["requestId"]
    assert overlay_ipc.parse_transition(transition)["stateRevision"] == 3
    assert state["schemaVersion"] == overlay_ipc.SCHEMA_VERSION


def test_overlay_ipc_accepts_legacy_v0_and_rejects_unknown_version() -> None:
    legacy = {"ownerPid": 7, "items": [], "close": False}
    assert overlay_ipc.parse_state(legacy) == legacy

    with pytest.raises(overlay_ipc.OverlayContractError, match="unsupported"):
        overlay_ipc.parse_state({**legacy, "schemaVersion": 2})


def test_overlay_ipc_rejects_malformed_versioned_messages() -> None:
    with pytest.raises(overlay_ipc.OverlayContractError, match="requestId"):
        overlay_ipc.parse_command(
            {
                "schemaVersion": 1,
                "messageType": "overlay.command",
                "messageId": "message-1",
                "createdAt": "2026-07-31T00:00:00+00:00",
                "action": "activateSession",
                "requestedAt": "2026-07-31T00:00:00+00:00",
            }
        )

    with pytest.raises(overlay_ipc.OverlayContractError, match="ack status"):
        overlay_ipc.ack_message(requestId="request-1", action="x", status="maybe")


def test_overlay_ipc_matches_by_session_before_title() -> None:
    assert overlay_ipc.command_matches_item(
        {"sessionId": "thread-1"},
        {"sessionId": "thread-1", "title": "Different title"},
    )
    assert not overlay_ipc.command_matches_item(
        {"title": "Thread", "workdir": "C:/one"},
        {"title": "Thread", "workdir": "C:/two"},
    )


def test_overlay_ipc_names_accounting_transition() -> None:
    assert overlay_ipc.transition_name(
        {"status": "recent", "pendingAccounting": True},
        {"status": "recent", "pendingAccounting": False},
    ) == "accounting_finalized"


def test_runtime_policy_keeps_running_refresh_fast() -> None:
    assert runtime_policies.refresh_delay_seconds(
        poll_ms=500,
        request_status="running",
        elapsed_seconds=0.2,
        idle_poll_ms=1500,
    ) == 0.7
    assert runtime_policies.refresh_delay_seconds(
        poll_ms=500,
        request_status="idle",
        elapsed_seconds=0.2,
        idle_poll_ms=1500,
    ) == 1.3


def test_runtime_policy_refreshes_background_jsonl_work_items() -> None:
    assert runtime_policies.should_refresh_active_work_items(
        has_snapshot=True,
        latest_refresh_at=10.0,
        now_monotonic=10.1,
        refresh_pending=False,
        file_change_reasons={"sessions-root"},
        file_change_paths={Path("background.jsonl")},
        rescan_seconds=5.0,
    )


def test_overlay_projection_preserves_client_and_provider_identity() -> None:
    item = SimpleNamespace(
        kind="session", event_id="", id="s1", title="CLI task", session_id="s1",
        target_title="", round_index=1, model_name="gpt", status="running",
        status_label="running", detail="work", status_text="", last_text="",
        elapsed_text="1s", progress="", tokens_text="", cost_text="",
        cache_hit_text="", workdir_name="repo", source="cli", workdir="E:/repo",
        model_provider="custom", client_kind="cli", session_started_at=None,
        task_started_at=None, started_at=None, updated_at=None, current=False,
        pending_accounting=False,
    )

    payload = overlay_projection.work_item_to_overlay_dict(item)

    assert payload["clientKind"] == "cli"
    assert payload["modelProvider"] == "custom"
    assert payload["sessionId"] == "s1"


def test_runtime_signals_coalesce_events_and_preserve_command_order() -> None:
    signals = runtime_policies.RendererRuntimeSignals()
    signals.enqueue_command({"id": "one"})
    signals.enqueue_command({"id": "two"})
    signals.wake_for_runtime_event(SimpleNamespace(type="settings_changed"))

    assert signals.command_refresh.is_set()
    assert not signals.active_session_refresh.is_set()
    assert signals.take_command() == {"id": "one"}
    assert signals.take_command() == {"id": "two"}
    assert signals.take_command() is None


def test_runtime_signals_wakes_for_usage_cache_hydration() -> None:
    signals = runtime_policies.RendererRuntimeSignals()

    signals.wake_for_runtime_event(SimpleNamespace(type="usage_cache_hydrated"))

    assert signals.command_refresh.is_set()
    assert not signals.active_session_refresh.is_set()


def test_runtime_signals_wakes_for_session_index_progress() -> None:
    signals = runtime_policies.RendererRuntimeSignals()

    signals.wake_for_runtime_event(SimpleNamespace(type="session_index_progress"))

    assert signals.command_refresh.is_set()
    assert not signals.active_session_refresh.is_set()


def test_runtime_signals_active_session_wakes_both_channels() -> None:
    signals = runtime_policies.RendererRuntimeSignals()

    signals.wake_for_runtime_event(SimpleNamespace(type="active_session_changed"))

    assert signals.command_refresh.is_set()
    assert signals.active_session_refresh.is_set()


def test_runtime_settings_classifies_budget_only_save() -> None:
    previous = UserConfig.defaults()
    current = runtime_settings.config_from_payload(
        previous,
        {"daily_budget_usd": previous.daily_budget_usd + 1},
    )

    assert runtime_settings.partial_domains_for_command(
        {"action": "save"},
        previous_config=previous,
        current_config=current,
    ) == {"settings", "currentSession", "budget"}


def test_runtime_settings_rejects_unknown_partial_config_change() -> None:
    assert runtime_settings.partial_domains_for_changed_config(
        {"daily_reset_time"}
    ) is None


def test_runtime_settings_correlates_background_usage_response() -> None:
    status = runtime_settings.background_usage_response_status(
        "detail",
        "request-1",
        payload={"eventId": "event-1"},
        event_id="event-1",
    )

    assert runtime_settings.has_pending_background_usage_response(status)
    assert status["backgroundUsageResponse"]["requestId"] == "request-1"


def test_runtime_policy_budget_windows_respect_reset_boundaries() -> None:
    config = runtime_settings.config_from_payload(
        UserConfig.defaults(),
        {"daily_reset_time": "06:00", "weekly_reset_weekday": 3},
    )

    day_start, week_start = runtime_policies.budget_windows(
        config,
        now=datetime(2026, 7, 30, 5, 0, tzinfo=timezone.utc),
    )

    assert day_start == datetime(2026, 7, 29, 6, 0, tzinfo=timezone.utc)
    assert week_start <= day_start


def test_runtime_usage_replaces_file_contribution_without_negative_values() -> None:
    result = runtime_usage.replace_usage(
        UsageSummary(tokens=10, cost_usd=1.0),
        UsageSummary(tokens=20, cost_usd=2.0),
        UsageSummary(tokens=3, cost_usd=0.25),
    )

    assert result.tokens == 0
    assert result.cost_usd == 0.0


def test_runtime_usage_extracts_cross_platform_workdir_leaf() -> None:
    assert runtime_usage.workdir_leaf(r"E:\Project\codex-usage-hud") == "codex-usage-hud"
    assert runtime_usage.workdir_leaf("/work/codex-usage-hud/") == "codex-usage-hud"


def test_usage_contribution_paths_include_archived_sibling(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    roots = usage_scan_roots(sessions)

    assert roots == (sessions, tmp_path / "archived_sessions")
    assert path_under_usage_roots(sessions / "2026" / "rollout.jsonl", roots)
    assert not path_under_usage_roots(tmp_path / "other" / "rollout.jsonl", roots)


def test_usage_contribution_index_replaces_canonical_path_entry(
    tmp_path: Path,
) -> None:
    index: ContributionIndex[FileUsageContribution] = ContributionIndex()
    first = FileUsageContribution(
        mtime=1.0,
        file_size=10,
        day_start=datetime(2026, 7, 30, tzinfo=timezone.utc),
        week_start=datetime(2026, 7, 27, tzinfo=timezone.utc),
        month_start=datetime(2026, 7, 1, tzinfo=timezone.utc),
        model_provider="custom",
        summary_day=UsageSummary(tokens=10),
        summary_week=UsageSummary(tokens=10),
        summary_month=UsageSummary(tokens=10),
    )
    second = replace(first, file_size=20, summary_day=UsageSummary(tokens=15))
    path = tmp_path / "sessions" / "rollout.jsonl"

    assert index.replace(path, first) is None
    assert index.replace(path, second) is first
    assert index.get(canonical_usage_path(path)) is second
    assert index.remove(path) is second
