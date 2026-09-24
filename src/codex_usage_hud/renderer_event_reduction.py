"""Pure reduction of renderer runtime events into refresh plans."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import runtime_settings

if TYPE_CHECKING:
    from .renderer_event_loop import RendererLoopState


@dataclass
class RefreshPlan:
    """Coalesced work requested by one or more runtime events."""

    snapshot: bool = False
    force_fast: bool = False
    active_session: bool = False
    diagnostics: bool = False
    background_usage: bool = False
    usage_insights_refresh: bool = False
    domains: set[str] = field(default_factory=set)
    theme_payload: dict[str, object] | None = None

    @property
    def has_work(self) -> bool:
        """Return whether the coalesced plan contains executable work."""
        return bool(
            self.snapshot
            or self.active_session
            or self.diagnostics
            or self.background_usage
            or self.usage_insights_refresh
            or self.domains
            or self.theme_payload
        )

    def request_snapshot(self, *, force_fast: bool = False) -> None:
        self.snapshot = True
        self.force_fast = self.force_fast or force_fast

    def request_active_session(self) -> None:
        self.active_session = True
        self.request_snapshot(force_fast=True)

    def request_diagnostics(self) -> None:
        self.diagnostics = True
        self.force_fast = True
        self.domains.add("diagnostics")

    def request_background_usage(self) -> None:
        self.background_usage = True
        self.force_fast = True

    def request_usage_insights_refresh(self) -> None:
        self.usage_insights_refresh = True

    def request_domains(
        self,
        *domain_names: str,
        force_fast: bool = False,
    ) -> None:
        self.force_fast = self.force_fast or force_fast
        for name in domain_names:
            key = str(name or "").strip()
            if key:
                self.domains.add(key)

    def merge(self, other: "RefreshPlan") -> None:
        self.snapshot = self.snapshot or other.snapshot
        self.force_fast = self.force_fast or other.force_fast
        self.active_session = self.active_session or other.active_session
        self.diagnostics = self.diagnostics or other.diagnostics
        self.background_usage = self.background_usage or other.background_usage
        self.usage_insights_refresh = (
            self.usage_insights_refresh or other.usage_insights_refresh
        )
        self.domains.update(other.domains)
        if other.theme_payload is not None:
            self.theme_payload = dict(other.theme_payload)


def _event_action(event: object) -> str:
    context = getattr(event, "context", None)
    if not isinstance(context, Mapping):
        return ""
    return str(context.get("action") or "").strip()


def reduce_event(
    state: RendererLoopState,
    event: object,
) -> tuple[RendererLoopState, RefreshPlan]:
    """Map one event to work without touching files, clients, clocks, or workers."""
    plan = RefreshPlan()
    event_type = str(getattr(event, "type", "") or "")

    if event_type in {"session_file_changed"}:
        plan.request_snapshot()
    elif event_type == "budget_window_changed":
        plan.request_snapshot(force_fast=True)
        plan.request_usage_insights_refresh()
    elif event_type in {
        "settings_changed",
        "session_snapshot_hydrated",
        "active_work_refresh_requested",
    }:
        plan.request_snapshot(force_fast=True)
    elif event_type == "active_session_changed":
        plan.request_active_session()
    elif event_type == "runtime_error":
        plan.request_diagnostics()
    elif event_type in {"update_state_changed", "rest_reminder_due", "pricing_sync"}:
        plan.request_domains("settings", force_fast=True)
    elif event_type == "usage_cache_hydrated":
        plan.request_snapshot(force_fast=True)
    elif event_type == "usage_insights_changed":
        plan.request_domains("settings", "usageInsights", force_fast=True)
    elif event_type == "session_cleanup_changed":
        plan.request_domains("settings", "sessionCleanup", force_fast=True)
    elif event_type == "session_index_progress":
        plan.request_domains("settings", "sessionCleanup", force_fast=True)
    elif event_type == "background_usage_changed":
        plan.request_background_usage()
        plan.request_domains("backgroundUsage", "usageInsights", force_fast=True)
    elif event_type == "renderer_theme_changed":
        context = getattr(event, "context", None)
        theme = context.get("theme") if isinstance(context, Mapping) else None
        if isinstance(theme, Mapping) and theme:
            plan.theme_payload = dict(theme)
            plan.request_domains("settings", force_fast=True)
    elif event_type == "settings_command_received":
        action = _event_action(event)
        if action in {
            "checkUpdate",
            "installUpdate",
            "updateAction",
            "restReminderAck",
            "restReminderPostpone",
            "restReminderStart",
            "restReminderFinish",
            "restReminderTestNotification",
            "openUsageInsightsSession",
            "openUsageInsightsWorkdir",
            "openSessionCleanupWorkdir",
            "openSessionCleanupSession",
            "resumeSessionCleanupSession",
            "openBackgroundUsageWorkdir",
        }:
            plan.request_domains("settings", force_fast=True)
        elif action in {"installDesktopOverlay", "enableDesktopOverlay"}:
            plan.request_domains("settings", "overlay", force_fast=True)
        elif action in runtime_settings.SESSION_CLEANUP_COMMANDS:
            plan.request_domains("settings", "sessionCleanup", force_fast=True)
        elif action == "usageInsightsRefresh":
            plan.request_domains("settings", "usageInsights", force_fast=True)
        elif action == "openBackgroundUsageFromInsights":
            plan.request_background_usage()
            plan.request_domains("backgroundUsage", force_fast=True)
        elif action == "dismissWarningsToday":
            plan.request_domains("currentSession", "settings", force_fast=True)
        else:
            plan.request_snapshot(force_fast=True)
    elif event_type == "overlay_command_received":
        action = _event_action(event)
        if action in {"dismissBackgroundUsage", "openBackgroundUsage"}:
            plan.request_background_usage()
        elif action in {
            "restReminderAck",
            "restReminderPostpone",
            "restReminderStart",
            "restReminderFinish",
        }:
            plan.request_domains("settings", force_fast=True)
        else:
            plan.request_snapshot(force_fast=True)
    # renderer_layout_changed intentionally wakes without invalidating Python data.
    return state, plan


def reduce_events(
    state: RendererLoopState,
    events: Iterable[object],
) -> tuple[RendererLoopState, RefreshPlan]:
    """Coalesce a sampled event batch while preserving event order."""
    plan = RefreshPlan()
    next_state = state
    for event in events:
        next_state, event_plan = reduce_event(next_state, event)
        plan.merge(event_plan)
    return next_state, plan


__all__ = ["RefreshPlan", "reduce_event", "reduce_events"]
