from codex_usage_hud.config import UserConfig
from codex_usage_hud.runtime_settings import (
    background_usage_response_status,
    changed_config_keys,
    partial_domains_for_command,
)


def test_background_usage_response_correlates_request() -> None:
    status = background_usage_response_status(
        "detail", "request-1", payload={"id": "event-1"}, event_id="event-1"
    )

    assert status["backgroundUsageResponse"] == {
        "kind": "detail",
        "requestId": "request-1",
        "payload": {"id": "event-1"},
        "error": "",
        "eventId": "event-1",
    }


def test_partial_domains_follow_changed_settings() -> None:
    previous = UserConfig.defaults()
    current = UserConfig.from_dict(
        {**previous.to_dict(), "daily_budget_usd": previous.daily_budget_usd + 1}
    )

    assert "daily_budget_usd" in changed_config_keys(previous, current)
    assert partial_domains_for_command(
        {"action": "save"},
        previous_config=previous,
        current_config=current,
    ) == {"settings", "currentSession", "budget"}


def test_background_usage_workdir_uses_settings_partial_domain() -> None:
    current = UserConfig.defaults()

    assert partial_domains_for_command(
        {"action": "openBackgroundUsageWorkdir"},
        previous_config=current,
        current_config=current,
    ) == {"settings"}


def test_overlay_side_save_uses_overlay_partial_domain() -> None:
    previous = UserConfig.defaults()
    current = UserConfig.from_dict(
        {**previous.to_dict(), "work_overlay_side": "left"}
    )

    assert "work_overlay_side" in changed_config_keys(previous, current)
    assert partial_domains_for_command(
        {"action": "save"},
        previous_config=previous,
        current_config=current,
    ) == {"settings", "overlay"}


def test_lock_screen_stop_save_uses_settings_partial_domain() -> None:
    previous = UserConfig.defaults()
    current = UserConfig.from_dict(
        {**previous.to_dict(), "stop_hud_on_lock_screen": True}
    )

    assert "stop_hud_on_lock_screen" in changed_config_keys(previous, current)
    assert partial_domains_for_command(
        {"action": "save"},
        previous_config=previous,
        current_config=current,
    ) == {"settings"}


def test_session_cleanup_workdir_uses_settings_partial_domain() -> None:
    current = UserConfig.defaults()

    assert partial_domains_for_command(
        {"action": "openSessionCleanupWorkdir"},
        previous_config=current,
        current_config=current,
    ) == {"settings"}


def test_pricing_sync_status_change_only_refreshes_settings_domain() -> None:
    previous = UserConfig.defaults()
    payload = previous.to_dict()
    payload["pricing_sync"] = {**payload["pricing_sync"], "last_result": "success"}
    current = UserConfig.from_dict(payload)

    assert changed_config_keys(previous, current) == {"pricing_sync"}
    assert partial_domains_for_command(
        {"action": "save"}, previous_config=previous, current_config=current
    ) == {"settings"}
