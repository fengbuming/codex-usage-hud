"""Pure settings-command contracts for the renderer runtime."""

from __future__ import annotations

from collections.abc import Mapping

from .config import UserConfig

BACKGROUND_USAGE_RESPONSE_RETRY_DELAYS_SECONDS = (0.15, 0.35, 0.75)
SESSION_CLEANUP_COMMANDS = frozenset(
    {
        "sessionCleanupScan",
        "sessionCleanupSearch",
        "sessionCleanupWorkdirOptions",
        "sessionCleanupPreview",
        "sessionCleanupExecute",
        "sessionCleanupCancel",
        "sessionTransfer",
    }
)
SESSION_INDEX_COMMANDS = frozenset(
    {
        "sessionIndexStatus",
        "sessionIndexControl",
    }
)

_OVERLAY_KEYS = frozenset({"work_overlay_max_items", "work_overlay_side"})
_SESSION_ACTIVITY_KEYS = frozenset({"stop_hud_on_lock_screen"})
_QUICK_LAUNCH_KEYS = frozenset({"quick_launch_providers"})
_REST_KEYS = frozenset(
    {
        "rest_reminder_enabled",
        "rest_reminder_interval_minutes",
        "rest_reminder_break_minutes",
        "rest_reminder_postpone_minutes",
        "rest_reminder_idle_reset_minutes",
        "rest_reminder_work_start_time",
        "rest_reminder_work_end_time",
        "rest_reminder_lunch_enabled",
        "rest_reminder_lunch_start_time",
        "rest_reminder_lunch_end_time",
    }
)
PRICE_CALC_KEYS = frozenset(
    {"pricing_url", "model_prices", "pricing_versions", "pricing_audit"}
)
PRICING_KEYS = PRICE_CALC_KEYS | frozenset({"pricing_sync"})
BUDGET_KEYS = frozenset(
    {
        "daily_budget_usd",
        "weekly_budget_usd",
        "budget_thresholds",
        "weekly_adjustment_usd",
    }
)
_SAFE_PARTIAL_KEYS = (
    frozenset({"display_mode"})
    | _OVERLAY_KEYS
    | _SESSION_ACTIVITY_KEYS
    | _QUICK_LAUNCH_KEYS
    | _REST_KEYS
    | PRICING_KEYS
    | BUDGET_KEYS
)


def config_from_payload(current: UserConfig, payload: object) -> UserConfig:
    merged = current.to_dict()
    if isinstance(payload, Mapping):
        merged.update(dict(payload))
    return UserConfig.from_dict(merged)


def changed_config_keys(previous: UserConfig, current: UserConfig) -> set[str]:
    previous_payload = previous.to_dict()
    current_payload = current.to_dict()
    return {
        key
        for key in previous_payload.keys() | current_payload.keys()
        if previous_payload.get(key) != current_payload.get(key)
    }


def partial_domains_for_changed_config(changed_keys: set[str]) -> set[str] | None:
    if changed_keys and not changed_keys.issubset(_SAFE_PARTIAL_KEYS):
        return None
    domains = {"settings"}
    if changed_keys & _OVERLAY_KEYS:
        domains.add("overlay")
    if changed_keys & PRICE_CALC_KEYS:
        domains.add("currentSession")
    if changed_keys & BUDGET_KEYS:
        domains.update({"currentSession", "budget"})
    return domains


def partial_domains_for_command(
    command: Mapping[str, object],
    *,
    previous_config: UserConfig,
    current_config: UserConfig,
) -> set[str] | None:
    action = str(command.get("action") or "").strip()
    if action in SESSION_CLEANUP_COMMANDS:
        return {"settings", "sessionCleanup"}
    if action in SESSION_INDEX_COMMANDS:
        return {"settings", "sessionCleanup"}
    if action == "usageInsightsRefresh":
        return {"settings", "usageInsights"}
    if action in {
        "openUsageInsightsSession",
        "openUsageInsightsWorkdir",
        "openSessionCleanupWorkdir",
        "openSessionCleanupSession",
        "resumeSessionCleanupSession",
        "openBackgroundUsageWorkdir",
        "applyDisplayMode",
    }:
        return {"settings"}
    if action == "deleteProvider":
        return {"settings", "sessionCleanup"}
    if action == "openBackgroundUsageFromInsights":
        return {"backgroundUsage"}
    if action in {
        "restReminderAck",
        "restReminderPostpone",
        "restReminderStart",
        "restReminderFinish",
        "restReminderTestNotification",
    }:
        return {"settings"}
    if action == "save":
        return partial_domains_for_changed_config(
            changed_config_keys(previous_config, current_config)
        )
    if action in {
        "fetchPrices",
        "savePricing",
        "pricingImportCommit",
    }:
        return {"currentSession", "settings"}
    if action in {
        "fetchPricesPreview",
        "pricingImportPreview",
        "pricingExport",
        "pricingTemplate",
        "pricingOpen",
    }:
        return {"settings"}
    if action in {
        "openBackgroundUsage",
        "backgroundUsageQuery",
        "backgroundUsageDetail",
        "backgroundUsagePolicyQuery",
        "backgroundUsagePolicySet",
    }:
        return {"backgroundUsage"}
    return None


def settings_status(
    message: str,
    *,
    kind: str = "",
    restart_visible: bool = False,
    switch_mode: str = "",
    restart_codex: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "message": message,
        "kind": kind,
        "restartVisible": restart_visible,
    }
    if switch_mode:
        payload["switchMode"] = switch_mode
    if restart_codex:
        payload["restartCodex"] = True
    return payload


def background_usage_response_status(
    kind: str,
    request_id: str,
    *,
    payload: object = None,
    event_id: str = "",
    error: str = "",
) -> dict[str, object]:
    status = settings_status("")
    response: dict[str, object] = {
        "kind": kind,
        "requestId": request_id,
        "payload": payload,
        "error": error,
    }
    if event_id:
        response["eventId"] = event_id
    status["backgroundUsageResponse"] = response
    if kind == "open":
        status["backgroundUsageOpenEventId"] = event_id
    return status


def background_usage_retry_delay(
    attempt: int,
    delays: tuple[float, ...] = BACKGROUND_USAGE_RESPONSE_RETRY_DELAYS_SECONDS,
) -> float | None:
    index = int(attempt) - 1
    if index < 0 or index >= len(delays):
        return None
    return delays[index]


def has_pending_background_usage_response(status: Mapping[str, object]) -> bool:
    response = status.get("backgroundUsageResponse")
    if not isinstance(response, Mapping):
        return False
    return bool(
        str(response.get("requestId") or "").strip()
        and str(response.get("kind") or "").strip() in {"query", "detail", "open", "policyQuery", "policyApply"}
    )
