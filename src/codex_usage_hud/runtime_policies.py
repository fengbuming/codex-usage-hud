"""Pure renderer refresh policies used by the CLI runtime coordinator."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path
import threading

from .config import UserConfig, time_parts


RUNTIME_REFRESH_EVENTS = frozenset(
    {
        "overlay_command_received",
        "session_file_changed",
        "settings_command_received",
        "settings_changed",
        "budget_window_changed",
        "renderer_layout_changed",
        "renderer_theme_changed",
        "session_snapshot_hydrated",
        "background_usage_changed",
        "usage_insights_changed",
        "usage_cache_hydrated",
        "session_cleanup_changed",
        "session_index_progress",
    }
)


class RendererRuntimeSignals:
    """Thread-safe wake signals and renderer command queue for one HUD run."""

    def __init__(self) -> None:
        self.command_refresh = threading.Event()
        self.active_session_refresh = threading.Event()
        self._commands: deque[dict[str, object]] = deque()
        self._command_lock = threading.Lock()

    def request_refresh(self) -> None:
        self.command_refresh.set()

    def request_active_session_refresh(self) -> None:
        self.active_session_refresh.set()
        self.command_refresh.set()

    def wake_for_runtime_event(self, event: object) -> None:
        event_type = str(getattr(event, "type", "") or "")
        if event_type in RUNTIME_REFRESH_EVENTS:
            self.request_refresh()
        elif event_type == "active_session_changed":
            self.request_active_session_refresh()

    def enqueue_command(self, command: dict[str, object]) -> None:
        with self._command_lock:
            self._commands.append(dict(command))

    def take_command(self) -> dict[str, object] | None:
        with self._command_lock:
            if not self._commands:
                return None
            return self._commands.popleft()

    def publish_or_wake(
        self,
        publish: Callable[..., object] | None,
        event_type: str,
        *,
        source: str,
        context: dict[str, object],
        active_session: bool = False,
    ) -> None:
        if callable(publish):
            publish(event_type, source=source, context=context)
        elif active_session:
            self.request_active_session_refresh()
        else:
            self.request_refresh()


def budget_windows(
    config: UserConfig | None = None,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    config = config or UserConfig.defaults()
    now = now or datetime.now().astimezone()
    day_hour, day_minute = time_parts(config.daily_reset_time)
    week_hour, week_minute = time_parts(config.weekly_reset_time)
    day_start = datetime.combine(
        now.date(),
        datetime_time(hour=day_hour, minute=day_minute),
        tzinfo=now.tzinfo,
    )
    if now < day_start:
        day_start -= timedelta(days=1)

    days_since_reset = (now.weekday() - int(config.weekly_reset_weekday)) % 7
    week_date = now.date() - timedelta(days=days_since_reset)
    week_start = datetime.combine(
        week_date,
        datetime_time(hour=week_hour, minute=week_minute),
        tzinfo=now.tzinfo,
    )
    if now < week_start:
        week_start -= timedelta(days=7)
    return day_start, week_start


def budget_warning_messages(
    day_cost: float | None,
    week_cost: float | None,
    daily_limit_usd: float,
    weekly_limit_usd: float,
    thresholds: Sequence[float],
) -> list[str]:
    messages: list[str] = []
    for label, used, limit in (
        ("日", day_cost, daily_limit_usd),
        ("周", week_cost, weekly_limit_usd),
    ):
        if used is None or limit <= 0:
            continue
        ratio = used / limit
        crossed = [item for item in thresholds if ratio >= item]
        if crossed:
            percent = int(crossed[-1] * 100)
            messages.append(
                f"{label}额度已用 {used:.2f}/{limit:.0f} USD "
                f"({ratio:.0%})，超过 {percent}% 阈值"
            )
    return messages


def refresh_delay_seconds(
    *,
    poll_ms: int,
    request_status: str,
    elapsed_seconds: float,
    idle_poll_ms: int,
    force_fast: bool = False,
) -> float:
    fast_seconds = max(0.1, poll_ms / 1000.0)
    target_seconds = fast_seconds
    if request_status == "running" and not force_fast:
        # Streaming JSONL writes can arrive faster than the renderer can parse
        # the current-session projection. Keep the live indicator responsive,
        # while leaving enough time for one CDP update to finish before the
        # next one is scheduled.
        target_seconds = max(fast_seconds, 0.9)
    elif not force_fast and request_status != "running":
        target_seconds = max(fast_seconds, idle_poll_ms / 1000.0)
    return max(0.1, target_seconds - max(0.0, elapsed_seconds))


def should_refresh_budget_aggregate(
    *,
    has_snapshot: bool,
    signature_changed: bool,
    file_change_reasons: set[str],
    has_incremental_jsonl_paths: bool,
) -> bool:
    if not has_snapshot or signature_changed:
        return True
    if "sessions-root" not in file_change_reasons:
        return False
    return not has_incremental_jsonl_paths


def should_refresh_active_work_items(
    *,
    has_snapshot: bool,
    latest_refresh_at: float,
    now_monotonic: float,
    refresh_pending: bool,
    file_change_reasons: set[str],
    file_change_paths: set[Path],
    rescan_seconds: float,
) -> bool:
    if not has_snapshot or refresh_pending or "session" in file_change_reasons:
        return True
    if "sessions-root" in file_change_reasons and any(
        path.suffix.lower() == ".jsonl" for path in file_change_paths
    ):
        return True
    return now_monotonic - latest_refresh_at >= rescan_seconds


def active_session_observation_should_refresh(
    *,
    changed: bool,
    selection_seq: object,
    current_seq: object,
    observation_key: object | None = None,
    previous_observation_key: object | None = None,
    applied_seq: object = 0,
    applied_observation_key: object | None = None,
    refresh_pending: bool = False,
) -> bool:
    try:
        incoming = int(selection_seq or 0)
        current = int(current_seq or 0)
        applied = int(applied_seq or 0)
    except (TypeError, ValueError):
        return False
    if current > 0 and incoming > 0 and incoming < current:
        return False
    if (
        refresh_pending
        and observation_key is not None
        and observation_key == previous_observation_key
    ):
        return False
    if changed:
        return True
    if incoming <= 0:
        return False
    if observation_key is not None and observation_key != previous_observation_key:
        return True
    if refresh_pending:
        return False
    if observation_key is not None and observation_key != applied_observation_key:
        return True
    return bool(incoming == current and incoming > applied)


def should_use_visible_first_active_session(
    *,
    active_session_requested: bool,
    has_snapshot: bool,
    has_command: bool,
    has_settings_command_status: bool,
    update_phase: str,
) -> bool:
    return bool(
        active_session_requested
        and has_snapshot
        and not has_command
        and not has_settings_command_status
        and update_phase != "downloading"
    )
