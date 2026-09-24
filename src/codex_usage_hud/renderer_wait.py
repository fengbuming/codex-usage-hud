"""Renderer event-loop wait planning and explicit runtime deadlines.

This module owns only the decision about when the renderer loop should wake
again. It consumes loop state and injected runtime ports, but it does not
perform snapshot, file, or CDP work.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .renderer_event_loop import RendererLoopState, RendererTickInputs


@dataclass(frozen=True, slots=True)
class RendererWaitPorts:
    """Runtime-owned clocks and deadline providers used by the wait planner."""

    monotonic: Callable[[], float]
    base_delay: Callable[[object, float, bool], float]
    idle_wait_enabled: Callable[[object, dict[str, object], float, bool], bool]
    reminder_in: Callable[[], float | None]
    keepalive_in: Callable[[], float | None]
    daemon_at: Callable[[], float | None]
    failure_limit: Callable[[], int]
    background_response_pending: Callable[[dict[str, object]], bool]
    probe_in: Callable[[], float | None]
    heal_in: Callable[[], float | None]
    idle_wait_seconds: float
    recovery_in: Callable[[], float | None] = lambda: None


class RendererWaitPlanner:
    """Collect runtime-owned deadlines and choose the next event-loop wait."""

    def __init__(
        self,
        state: RendererLoopState,
        ports: RendererWaitPorts,
    ) -> None:
        self.state = state
        self.ports = ports

    def compute(
        self,
        snapshot: object,
        inputs: RendererTickInputs,
        force_fast: bool,
    ) -> float:
        now = self.ports.monotonic()
        base_delay = self.ports.base_delay(
            snapshot,
            now - inputs.started,
            force_fast,
        )
        background_retry_at = None
        if (
            self.ports.background_response_pending(
                self.state.settings_command_status
            )
            and self.state.background_usage_response_retry_attempts > 0
        ):
            background_retry_at = (
                self.state.background_usage_response_retry_not_before
            )
        return scheduled_wait_delay(
            base_delay,
            now=now,
            deadlines=ScheduledDeadlines(
                reminder_in=self.ports.reminder_in(),
                keepalive_in=self.ports.keepalive_in(),
                daemon_at=self.ports.daemon_at(),
                active_work_at=(
                    self.state.active_work_refresh_not_before
                    if self.state.active_work_refresh_pending
                    else None
                ),
                background_retry_at=background_retry_at,
                retry_at=(
                    self.state.pending_retry_not_before
                    if self.state.pending_retry_not_before > 0.0
                    else None
                ),
                probe_in=self.ports.probe_in(),
                heal_in=self.ports.heal_in(),
                recovery_in=self.ports.recovery_in(),
            ),
            idle_wait_enabled=self.ports.idle_wait_enabled(
                snapshot,
                inputs.update_state,
                base_delay,
                force_fast,
            ),
            idle_wait_seconds=self.ports.idle_wait_seconds,
            update_failures=self.state.failures,
            failure_limit=self.ports.failure_limit(),
        )


@dataclass(frozen=True, slots=True)
class ScheduledDeadlines:
    """Optional wake deadlines sampled from runtime-owned resources."""

    reminder_in: float | None = None
    keepalive_in: float | None = None
    daemon_at: float | None = None
    active_work_at: float | None = None
    background_retry_at: float | None = None
    retry_at: float | None = None
    probe_in: float | None = None
    heal_in: float | None = None
    recovery_in: float | None = None


def scheduled_wait_delay(
    base_delay: float,
    *,
    now: float,
    deadlines: ScheduledDeadlines,
    idle_wait_enabled: bool = False,
    idle_wait_seconds: float = 0.0,
    update_failures: int = 0,
    failure_limit: int = 0,
) -> float:
    """Choose the next explicit runtime deadline without doing runtime work."""
    delay = max(0.0, float(base_delay))
    if idle_wait_enabled:
        delay = max(delay, max(0.0, float(idle_wait_seconds)))
    if deadlines.reminder_in is not None:
        delay = min(delay, max(0.05, float(deadlines.reminder_in)))
    if deadlines.keepalive_in is not None:
        delay = min(delay, max(0.1, float(deadlines.keepalive_in)))
    if deadlines.daemon_at is not None:
        delay = min(delay, max(0.1, float(deadlines.daemon_at) - now))
    if failure_limit > 0 and update_failures >= failure_limit:
        delay = max(delay, min(5.0, update_failures * 0.5))
    if deadlines.active_work_at is not None:
        delay = min(delay, max(0.05, float(deadlines.active_work_at) - now))
    if deadlines.background_retry_at is not None:
        delay = min(
            delay,
            max(0.05, float(deadlines.background_retry_at) - now),
        )
    if deadlines.retry_at is not None:
        delay = min(delay, max(0.05, float(deadlines.retry_at) - now))
    if deadlines.probe_in is not None:
        delay = min(delay, max(0.05, float(deadlines.probe_in)))
    if deadlines.heal_in is not None and deadlines.heal_in > 0:
        delay = min(delay, max(0.05, float(deadlines.heal_in)))
    if deadlines.recovery_in is not None:
        delay = min(delay, max(0.05, float(deadlines.recovery_in)))
    return delay


__all__ = [
    "RendererWaitPlanner",
    "RendererWaitPorts",
    "ScheduledDeadlines",
    "scheduled_wait_delay",
]
