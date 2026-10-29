"""Stable full and partial Renderer payload schemas."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import __version__


@dataclass(frozen=True)
class RendererHudPayload:
    top_line: str
    request_line: str
    session: str
    model: str
    source: str
    request_status: str
    last_event: str
    refreshed_at: str
    warning: bool = False
    new_session: bool = False
    pending_session: bool = False
    selection_seq: int = 0
    session_id: str = ""
    renderer_session_id: str = ""
    selection_observed_at_ms: int = 0
    follow_state: str = ""
    follow_reason: str = ""
    follow_elapsed_ms: int = 0
    follow_timing: dict[str, int] = field(default_factory=dict)
    match_candidates: list[dict[str, object]] = field(default_factory=list)
    top_details: dict[str, object] = field(default_factory=dict)
    top_progress: dict[str, object] = field(default_factory=dict)
    top_copies: dict[str, str] = field(default_factory=dict)
    request_rows: list[str] = field(default_factory=list)
    request_row_details: list[dict[str, object]] = field(default_factory=list)
    request_rows_total: int = 0
    observed_models: list[str] = field(default_factory=list)
    observed_models_by_provider: dict[str, list[str]] = field(default_factory=dict)
    settings: dict[str, object] = field(default_factory=dict)
    active_session_provider: str = ""
    active_display_mode: str = "renderer"
    settings_path: str = ""
    settings_bridge_url: str = ""
    background_usage_bridge_url: str = ""
    session_index_url: str = ""
    session_index: dict[str, object] = field(default_factory=dict)
    background_usage_revision: int = 0
    background_usage_notification: dict[str, object] = field(default_factory=dict)
    rest_reminder: dict[str, object] = field(default_factory=dict)
    settings_command_status: dict[str, object] = field(default_factory=dict)
    usage_insights: dict[str, object] = field(default_factory=dict)
    session_cleanup: dict[str, object] = field(default_factory=dict)
    work_overlay_selectable_max: int = 6
    desktop_overlay_dependency: dict[str, object] = field(default_factory=dict)
    support_images: list[dict[str, str]] = field(default_factory=list)
    theme: dict[str, object] = field(default_factory=dict)
    update_state: dict[str, object] = field(default_factory=dict)
    app_version: str = __version__
    pre_send_estimate: str = ""
    pre_send_base_tokens: int = 0
    pre_send_breakdown: list[dict[str, object]] = field(default_factory=list)
    pre_send_input_price: float = 0.0
    pre_send_total_cost: float | None = None
    pre_send_has_prices: bool = False
    activity_warning: bool = False
    activity_reading_file: str = ""
    debug: bool = False
    runtime_errors: list[dict[str, object]] = field(default_factory=list)
    connection_health: dict[str, object] = field(default_factory=dict)
    output_tokens_per_second: float | None = None
    output_speed_mode: str = "idle"

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "topLine": self.top_line,
            "requestLine": self.request_line,
            "session": self.session,
            "model": self.model,
            "source": self.source,
            "requestStatus": self.request_status,
            "outputTokensPerSecond": self.output_tokens_per_second,
            "outputSpeedMode": self.output_speed_mode,
            "lastEvent": self.last_event,
            "refreshedAt": self.refreshed_at,
            "warning": self.warning,
            "newSession": bool(self.new_session),
            "pendingSession": bool(self.pending_session),
            "selectionSeq": int(self.selection_seq),
            "sessionId": self.session_id,
            "rendererSessionId": self.renderer_session_id,
            "cachedPreview": False,
            "selectionObservedAt": int(self.selection_observed_at_ms),
            "followState": self.follow_state,
            "followReason": self.follow_reason,
            "followElapsedMs": int(self.follow_elapsed_ms),
            "followTiming": dict(self.follow_timing),
            "matchCandidates": [dict(item) for item in self.match_candidates],
            "topDetails": dict(self.top_details),
            "topProgress": dict(self.top_progress),
            "topCopies": dict(self.top_copies),
            "requestRows": list(self.request_rows),
            "requestRowDetails": [dict(item) for item in self.request_row_details],
            "requestRowsTotal": max(0, int(self.request_rows_total or 0)),
            "observedModels": list(self.observed_models),
            "observedModelsByProvider": {
                provider: list(models)
                for provider, models in self.observed_models_by_provider.items()
            },
            "settings": dict(self.settings),
            "activeSessionProvider": str(self.active_session_provider or ""),
            "activeDisplayMode": self.active_display_mode,
            "settingsPath": self.settings_path,
            "settingsBridgeUrl": self.settings_bridge_url,
            "backgroundUsageBridgeUrl": self.background_usage_bridge_url,
            "sessionIndexUrl": self.session_index_url,
            "sessionIndex": dict(self.session_index),
            "backgroundUsageRevision": int(self.background_usage_revision),
            "backgroundUsageNotification": dict(self.background_usage_notification),
            "restReminder": dict(self.rest_reminder),
            "settingsCommandStatus": dict(self.settings_command_status),
            "usageInsights": dict(self.usage_insights),
            "sessionCleanup": dict(self.session_cleanup),
            "workOverlaySelectableMax": int(self.work_overlay_selectable_max),
            "desktopOverlayDependency": dict(self.desktop_overlay_dependency),
            "supportImages": [dict(item) for item in self.support_images],
            "theme": dict(self.theme),
            "updateState": dict(self.update_state),
            "appVersion": self.app_version,
            "preSendEstimate": self.pre_send_estimate,
            "preSendBaseTokens": int(self.pre_send_base_tokens),
            "preSendBreakdown": [dict(item) for item in self.pre_send_breakdown],
            "preSendInputPrice": float(self.pre_send_input_price),
            "preSendTotalCost": self.pre_send_total_cost,
            "preSendHasPrices": bool(self.pre_send_has_prices),
            "activityWarning": bool(self.activity_warning),
            "activityReadingFile": self.activity_reading_file,
            "debug": bool(self.debug),
            "runtimeErrors": [dict(item) for item in self.runtime_errors],
            "connectionHealth": dict(self.connection_health),
        }
        payload["payloadDomains"] = payload_domains(payload)
        return payload

    def to_domain_json(self, *domain_names: str) -> dict[str, object]:
        payload = self.to_json()
        domains = payload.get("payloadDomains")
        if not isinstance(domains, dict):
            return payload
        selected: dict[str, dict[str, object]] = {}
        for name in domain_names:
            key = str(name or "").strip()
            value = domains.get(key)
            if isinstance(value, dict):
                selected[key] = dict(value)
        if not selected:
            return {}
        partial: dict[str, object] = {}
        for domain_payload in selected.values():
            partial.update(domain_payload)
        if partial.get("supportImages") == []:
            partial.pop("supportImages", None)
            settings_domain = selected.get("settings")
            if isinstance(settings_domain, dict):
                settings_domain.pop("supportImages", None)
        if partial.get("theme") == {}:
            partial.pop("theme", None)
            settings_domain = selected.get("settings")
            if isinstance(settings_domain, dict):
                settings_domain.pop("theme", None)
        partial["payloadDomains"] = selected
        return partial


def payload_domains(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    session_switch_keys = (
        "topLine", "requestLine", "session", "model", "source",
        "requestStatus", "lastEvent", "refreshedAt", "warning",
        "outputTokensPerSecond",
        "outputSpeedMode",
        "newSession", "pendingSession", "selectionSeq", "sessionId",
        "rendererSessionId", "cachedPreview", "selectionObservedAt",
        "followState", "followReason", "followElapsedMs", "followTiming",
        "matchCandidates",
        "backgroundUsageNotification", "connectionHealth", "activeSessionProvider",
    )
    current_session_keys = (
        "topLine", "requestLine", "session", "model", "source",
        "requestStatus", "lastEvent", "refreshedAt", "warning",
        "outputTokensPerSecond",
        "outputSpeedMode",
        "newSession", "pendingSession", "selectionSeq", "sessionId",
        "rendererSessionId", "cachedPreview", "selectionObservedAt",
        "followState", "followReason", "followElapsedMs", "topDetails",
        "matchCandidates",
        "topCopies", "requestRows", "requestRowDetails", "requestRowsTotal", "observedModels",
        "preSendEstimate", "preSendBaseTokens", "preSendBreakdown",
        "preSendInputPrice", "preSendTotalCost", "preSendHasPrices",
        "activityWarning", "activityReadingFile", "backgroundUsageNotification",
        "connectionHealth", "activeSessionProvider",
    )
    settings_keys = (
        "settings", "activeDisplayMode", "settingsPath", "settingsBridgeUrl",
        "settingsCommandStatus", "sessionIndex", "restReminder", "supportImages", "theme",
        "updateState", "appVersion", "activeSessionProvider",
        "model", "observedModels", "observedModelsByProvider",
    )

    def pick(keys: tuple[str, ...]) -> dict[str, object]:
        return {key: payload[key] for key in keys if key in payload}

    domains = {
        "currentSession": pick(current_session_keys),
        "sessionSwitch": pick(session_switch_keys),
        "budget": pick(("topProgress",)),
        "settings": pick(settings_keys),
        "overlay": pick(("workOverlaySelectableMax", "desktopOverlayDependency")),
        "backgroundUsage": pick(
            (
                "backgroundUsageBridgeUrl", "backgroundUsageRevision",
                "backgroundUsageNotification", "settingsCommandStatus",
            )
        ),
        "diagnostics": pick(("debug", "runtimeErrors", "connectionHealth")),
    }
    usage_insights = payload.get("usageInsights")
    if isinstance(usage_insights, dict) and usage_insights:
        domains["usageInsights"] = {"usageInsights": dict(usage_insights)}
    session_cleanup = payload.get("sessionCleanup")
    if isinstance(session_cleanup, dict) and session_cleanup:
        domains["sessionCleanup"] = {"sessionCleanup": dict(session_cleanup)}
    return domains


__all__ = ["RendererHudPayload", "payload_domains"]
