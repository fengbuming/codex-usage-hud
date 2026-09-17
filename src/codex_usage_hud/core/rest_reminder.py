"""Lightweight rest / eye-care reminder scheduler.

Defaults and product choices mirror mature break tools such as Stretchly and
BreakTimer: optional, timed breaks, one postpone, idle reset, and automatic
progression to the next focus round.
"""

from __future__ import annotations

import ctypes
import logging
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from ..process_environment import external_process_environment

# Keep defaults local to avoid circular import with config -> core package init.
DEFAULT_REST_REMINDER_ENABLED = False
DEFAULT_REST_REMINDER_INTERVAL_MINUTES = 45
DEFAULT_REST_REMINDER_BREAK_MINUTES = 2
DEFAULT_REST_REMINDER_POSTPONE_MINUTES = 10
DEFAULT_REST_REMINDER_IDLE_RESET_MINUTES = 5
DEFAULT_REST_REMINDER_WORK_START_TIME = "09:00"
DEFAULT_REST_REMINDER_WORK_END_TIME = "18:00"
DEFAULT_REST_REMINDER_LUNCH_ENABLED = True
DEFAULT_REST_REMINDER_LUNCH_START_TIME = "12:00"
DEFAULT_REST_REMINDER_LUNCH_END_TIME = "13:30"
REST_REMINDER_INTERVAL_MIN = 1
REST_REMINDER_INTERVAL_MAX = 180
REST_REMINDER_BREAK_MIN = 1
REST_REMINDER_BREAK_MAX = 10
REST_REMINDER_POSTPONE_MIN = 5
REST_REMINDER_POSTPONE_MAX = 30
REST_REMINDER_IDLE_RESET_MIN = 0
REST_REMINDER_IDLE_RESET_MAX = 60
REST_REMINDER_IDLE_RETURN_POLL_SECONDS = 30.0
REST_REMINDER_COMPLETION_FEEDBACK_SECONDS = 1.5
REST_REMINDER_EARLY_REST_OPTIONS: tuple[int, ...] = (3, 5, 10)
REST_REMINDER_EARLY_REST_MINUTES_MIN = 1
REST_REMINDER_EARLY_REST_MINUTES_MAX = 24 * 60

_LOGGER = logging.getLogger("codex_usage_hud.rest_reminder")
_LOGGER.addHandler(logging.NullHandler())
WINDOWS_NOTIFICATION_DISPLAY_MILLISECONDS = 30_000

REST_REMINDER_MESSAGES: tuple[str, ...] = (
    "写了挺久了，起来走走，给眼睛放个假。",
    "把视线移到远处，放松眼睛，再起身活动一下。",
    "喝口水、伸个懒腰，让眼睛和肩颈放松一下。",
    "给自己几分钟喘口气，回来时会更专注。",
    "站起来活动一下，再开始下一段专注时间。",
)


@dataclass(frozen=True)
class RestReminderConfig:
    enabled: bool = DEFAULT_REST_REMINDER_ENABLED
    interval_minutes: int = DEFAULT_REST_REMINDER_INTERVAL_MINUTES
    break_minutes: int = DEFAULT_REST_REMINDER_BREAK_MINUTES
    postpone_minutes: int = DEFAULT_REST_REMINDER_POSTPONE_MINUTES
    idle_reset_minutes: int = DEFAULT_REST_REMINDER_IDLE_RESET_MINUTES
    work_start_time: str = DEFAULT_REST_REMINDER_WORK_START_TIME
    work_end_time: str = DEFAULT_REST_REMINDER_WORK_END_TIME
    lunch_enabled: bool = DEFAULT_REST_REMINDER_LUNCH_ENABLED
    lunch_start_time: str = DEFAULT_REST_REMINDER_LUNCH_START_TIME
    lunch_end_time: str = DEFAULT_REST_REMINDER_LUNCH_END_TIME

    @classmethod
    def from_user_config(cls, user_config: object) -> "RestReminderConfig":
        return cls(
            enabled=bool(getattr(user_config, "rest_reminder_enabled", False)),
            interval_minutes=_clamp_int(
                getattr(
                    user_config,
                    "rest_reminder_interval_minutes",
                    DEFAULT_REST_REMINDER_INTERVAL_MINUTES,
                ),
                DEFAULT_REST_REMINDER_INTERVAL_MINUTES,
                REST_REMINDER_INTERVAL_MIN,
                REST_REMINDER_INTERVAL_MAX,
            ),
            break_minutes=_clamp_int(
                getattr(
                    user_config,
                    "rest_reminder_break_minutes",
                    DEFAULT_REST_REMINDER_BREAK_MINUTES,
                ),
                DEFAULT_REST_REMINDER_BREAK_MINUTES,
                REST_REMINDER_BREAK_MIN,
                REST_REMINDER_BREAK_MAX,
            ),
            postpone_minutes=_clamp_int(
                getattr(
                    user_config,
                    "rest_reminder_postpone_minutes",
                    DEFAULT_REST_REMINDER_POSTPONE_MINUTES,
                ),
                DEFAULT_REST_REMINDER_POSTPONE_MINUTES,
                REST_REMINDER_POSTPONE_MIN,
                REST_REMINDER_POSTPONE_MAX,
            ),
            idle_reset_minutes=_clamp_int(
                getattr(
                    user_config,
                    "rest_reminder_idle_reset_minutes",
                    DEFAULT_REST_REMINDER_IDLE_RESET_MINUTES,
                ),
                DEFAULT_REST_REMINDER_IDLE_RESET_MINUTES,
                REST_REMINDER_IDLE_RESET_MIN,
                REST_REMINDER_IDLE_RESET_MAX,
            ),
            work_start_time=_normalize_time_text(
                getattr(user_config, "rest_reminder_work_start_time", None),
                DEFAULT_REST_REMINDER_WORK_START_TIME,
            ),
            work_end_time=_normalize_time_text(
                getattr(user_config, "rest_reminder_work_end_time", None),
                DEFAULT_REST_REMINDER_WORK_END_TIME,
            ),
            lunch_enabled=bool(
                getattr(
                    user_config,
                    "rest_reminder_lunch_enabled",
                    DEFAULT_REST_REMINDER_LUNCH_ENABLED,
                )
            ),
            lunch_start_time=_normalize_time_text(
                getattr(user_config, "rest_reminder_lunch_start_time", None),
                DEFAULT_REST_REMINDER_LUNCH_START_TIME,
            ),
            lunch_end_time=_normalize_time_text(
                getattr(user_config, "rest_reminder_lunch_end_time", None),
                DEFAULT_REST_REMINDER_LUNCH_END_TIME,
            ),
        )


@dataclass(frozen=True)
class RestReminderEvent:
    """A due rest reminder ready for UI presentation."""

    message: str
    can_postpone: bool
    interval_minutes: int
    break_minutes: int
    postpone_minutes: int
    fired_at: float
    ends_at: float


def _clamp_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        amount = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        amount = int(default)
    return max(int(minimum), min(int(maximum), amount))


def _normalize_time_text(value: object, default: str) -> str:
    text = str(value or "").strip()
    try:
        hour, minute = (int(part) for part in text.split(":", 1))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    except (TypeError, ValueError):
        pass
    return default


def _minutes_from_time_text(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":", 1))
    return hour * 60 + minute


def system_idle_seconds(now: float | None = None) -> float | None:
    """Return OS idle seconds when available; None if unsupported/unavailable."""
    del now
    if sys.platform != "win32":
        return None
    try:

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("dwTime", ctypes.c_uint),
            ]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        tick = int(ctypes.windll.kernel32.GetTickCount())  # type: ignore[attr-defined]
        last = int(info.dwTime)
        idle_ms = max(0, tick - last)
        return idle_ms / 1000.0
    except Exception:
        return None


class RestReminderScheduler:
    """Process-local focus, prompt, postpone, and explicit-rest state machine."""

    def __init__(
        self,
        *,
        idle_seconds_provider: Callable[[], float | None] | None = None,
        message_picker: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
    ) -> None:
        self._idle_seconds = idle_seconds_provider or system_idle_seconds
        self._pick_message = message_picker or (
            lambda: random.choice(REST_REMINDER_MESSAGES)
        )
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or time.time
        self._config = RestReminderConfig()
        self._phase = "focus"
        self._cycle_started_at = 0.0
        self._cycle_started_wall = 0.0
        self._next_fire_at = 0.0
        self._next_fire_wall = 0.0
        self._prompt_until = 0.0
        self._prompt_until_wall = 0.0
        self._prompt_started_wall = 0.0
        self._postpone_until_wall = 0.0
        self._rest_started_at = 0.0
        self._rest_started_wall = 0.0
        self._rest_until = 0.0
        self._rest_until_wall = 0.0
        self._postpone_used = False
        self._last_event: RestReminderEvent | None = None
        self._active_message = ""
        self._schedule_waiting = False
        self._idle_break_active = False
        self._daily_date = ""
        self._daily_rested_seconds = 0.0
        self._daily_rested_count = 0
        self._last_rest_duration_seconds = 0.0
        self._pending_transition: dict[str, object] | None = None
        self.configure(self._config, force_reset=True)

    @property
    def config(self) -> RestReminderConfig:
        return self._config

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def showing(self) -> bool:
        return self._phase == "prompt"

    @property
    def showing_until(self) -> float:
        return self._prompt_until if self.showing else 0.0

    @property
    def resting(self) -> bool:
        return self._phase == "resting"

    @property
    def active_event(self) -> RestReminderEvent | None:
        return self._last_event

    @property
    def active_message(self) -> str:
        return self._active_message

    @property
    def postpone_used(self) -> bool:
        return self._postpone_used

    @property
    def next_fire_at(self) -> float:
        return self._next_fire_at

    @property
    def cycle_started_at(self) -> float:
        return self._cycle_started_at

    @property
    def prompt_ends_at_wall(self) -> float:
        return self._prompt_until_wall if self.showing else 0.0

    @property
    def prompt_started_at_wall(self) -> float:
        return self._prompt_started_wall if self.showing else 0.0

    @property
    def postpone_ends_at_wall(self) -> float:
        return self._postpone_until_wall if self._phase == "postponed" else 0.0

    @property
    def rest_started_at_wall(self) -> float:
        return self._rest_started_wall if self.resting else 0.0

    @property
    def rest_ends_at_wall(self) -> float:
        return self._rest_until_wall if self.resting else 0.0

    @property
    def completed_today_seconds(self) -> float:
        self._refresh_daily_date(float(self._wall_clock()))
        return max(0.0, self._daily_rested_seconds)

    @property
    def completed_today_count(self) -> int:
        self._refresh_daily_date(float(self._wall_clock()))
        return max(0, int(self._daily_rested_count))

    @property
    def today_rested_seconds(self) -> float:
        wall_now = float(self._wall_clock())
        self._refresh_daily_date(wall_now)
        active = 0.0
        if self.resting:
            day_start = self._day_start_timestamp(wall_now)
            active = max(
                0.0,
                min(wall_now, self._rest_until_wall)
                - max(self._rest_started_wall, day_start),
            )
        return max(0.0, self._daily_rested_seconds + active)

    @property
    def today_rested_count(self) -> int:
        wall_now = float(self._wall_clock())
        self._refresh_daily_date(wall_now)
        active = 0
        if self.resting:
            day_start = self._day_start_timestamp(wall_now)
            active = int(
                self._rest_started_wall > 0
                and self._rest_started_wall <= wall_now
                and self._rest_until_wall > max(self._rest_started_wall, day_start)
                and self._rest_until_wall > day_start
            )
        return max(0, int(self._daily_rested_count) + active)

    @property
    def last_rest_duration_seconds(self) -> float:
        return max(0.0, self._last_rest_duration_seconds)

    @property
    def state(self) -> str:
        if not self._config.enabled:
            return "disabled"
        schedule_state, _ = self._schedule_state(float(self._wall_clock()))
        if schedule_state != "work":
            return schedule_state
        if self._phase != "focus":
            return self._phase
        if self._idle_break_active:
            return "away"
        return "work"

    def monotonic_now(self) -> float:
        return float(self._clock())

    def seconds_until_next(self, now: float | None = None) -> float | None:
        if not self._config.enabled:
            return None
        current = self._clock() if now is None else float(now)
        return max(0.0, self._next_fire_at - current)

    def seconds_until_prompt_end(self, now: float | None = None) -> float | None:
        if not self.showing:
            return None
        # Prompt interaction is intentionally unbounded.  Keep the optional
        # argument for API compatibility with the other deadline helpers.
        del now
        return None

    def seconds_until_break_end(self, now: float | None = None) -> float | None:
        if not self.resting:
            return None
        current = self._clock() if now is None else float(now)
        return max(0.0, self._rest_until - current)

    def current_rest_elapsed_seconds(self, now: float | None = None) -> float:
        if not self.resting:
            return 0.0
        current = self._clock() if now is None else float(now)
        return max(0.0, min(current, self._rest_until) - self._rest_started_at)

    def seconds_until_wake(self, now: float | None = None) -> float | None:
        """Return the next phase or work-schedule boundary that needs a tick."""
        current = self._clock() if now is None else float(now)
        if not self._config.enabled:
            return None
        if self.showing:
            # There is no prompt timeout anymore.  Renderer/overlay commands
            # are event-driven and will wake the runtime when the user acts.
            return None
        wall_now = float(self._wall_clock())
        state, boundary = self._schedule_state(wall_now)
        if self.resting:
            delay = max(0.0, self._rest_until - current)
        else:
            delay = max(0.0, self._next_fire_at - current)
        if state != "work" and boundary is not None:
            return max(0.0, boundary - wall_now)
        if boundary is not None:
            delay = min(delay, max(0.0, boundary - wall_now))
        if self._phase in {"focus", "postponed"}:
            idle_reset = float(self._config.idle_reset_minutes) * 60.0
            if idle_reset > 0:
                if self._idle_break_active:
                    delay = min(delay, REST_REMINDER_IDLE_RETURN_POLL_SECONDS)
                else:
                    idle_seconds = self._idle_seconds()
                    if idle_seconds is not None:
                        delay = min(delay, max(0.0, idle_reset - idle_seconds))
        return delay

    def set_cycle_started_at_wall(self, started_at_wall: float) -> None:
        """Adjust the current focus round start from a wall-clock timestamp."""
        if not self._config.enabled:
            return
        now_wall = float(self._wall_clock())
        current = float(self._clock())
        started_wall = float(started_at_wall)
        elapsed = max(0.0, now_wall - started_wall)
        self._phase = "focus"
        self._cycle_started_at = current - elapsed
        self._cycle_started_wall = started_wall
        duration = float(self._config.interval_minutes) * 60.0
        self._next_fire_at = self._cycle_started_at + duration
        self._next_fire_wall = started_wall + duration
        self._clear_prompt()
        self._clear_rest()
        self._postpone_until_wall = 0.0
        self._postpone_used = False
        self._idle_break_active = False
        self._schedule_waiting = False

    def export_wall_state(self) -> dict[str, Any] | None:
        """Return wall-clock phase timing and today's rest accounting."""
        if not self._config.enabled:
            return None
        wall_now = float(self._wall_clock())
        self._refresh_daily_date(wall_now)
        snapshot: dict[str, Any] = {
            "enabled": True,
            "phase": self._phase,
            "cycleStartedAtMs": int(round(self._cycle_started_wall * 1000.0)),
            "nextFireAtMs": int(round(self._next_fire_wall * 1000.0)),
            "postponeUsed": bool(self._postpone_used),
            "intervalMinutes": int(self._config.interval_minutes),
            "breakMinutes": int(self._config.break_minutes),
            "postponeMinutes": int(self._config.postpone_minutes),
            "idleResetMinutes": int(self._config.idle_reset_minutes),
            "workStartTime": self._config.work_start_time,
            "workEndTime": self._config.work_end_time,
            "lunchEnabled": bool(self._config.lunch_enabled),
            "lunchStartTime": self._config.lunch_start_time,
            "lunchEndTime": self._config.lunch_end_time,
            "scheduleWaiting": bool(self._schedule_waiting),
            "dailyDate": self._daily_date,
            "dailyRestedSeconds": int(round(self._daily_rested_seconds)),
            "dailyRestedCount": int(self._daily_rested_count),
            "reminderMessage": self._active_message,
        }
        if self.showing:
            # Zero explicitly represents an infinite wait.  Retaining the key
            # keeps the persisted schema compatible with older installations.
            snapshot["promptEndsAtMs"] = 0
            snapshot["promptWaitInfinite"] = True
            snapshot["promptStartedAtMs"] = int(round(self._prompt_started_wall * 1000.0))
            snapshot["reminderCanPostpone"] = not self._postpone_used
        elif self._phase == "postponed":
            snapshot["postponeEndsAtMs"] = int(round(self._postpone_until_wall * 1000.0))
        elif self.resting:
            snapshot["restStartedAtMs"] = int(round(self._rest_started_wall * 1000.0))
            snapshot["restEndsAtMs"] = int(round(self._rest_until_wall * 1000.0))
        return snapshot

    def restore_wall_state(self, state: Mapping[str, Any] | None) -> bool:
        """Restore future prompt/postpone/rest timing from one wall snapshot."""
        if not self._config.enabled or not isinstance(state, Mapping):
            return False

        # Daily accounting is independent from the current timing settings.
        # Load it before validating the saved interval/break/schedule so a
        # harmless settings change cannot make today's completed rest vanish.
        wall_now = float(self._wall_clock())
        self._daily_date = str(state.get("dailyDate") or "")
        try:
            self._daily_rested_seconds = max(
                0.0, float(state.get("dailyRestedSeconds") or 0.0)
            )
        except (TypeError, ValueError):
            self._daily_rested_seconds = 0.0
        try:
            self._daily_rested_count = max(
                0, int(float(state.get("dailyRestedCount") or 0.0))
            )
        except (TypeError, ValueError, OverflowError):
            self._daily_rested_count = 0
        self._refresh_daily_date(wall_now)

        try:
            saved_interval = int(state.get("intervalMinutes"))
            saved_break = int(state.get("breakMinutes", self._config.break_minutes))
            saved_postpone = int(state.get("postponeMinutes"))
            saved_idle_reset = int(state.get("idleResetMinutes"))
        except (TypeError, ValueError):
            return False
        if (
            saved_interval != int(self._config.interval_minutes)
            or saved_break != int(self._config.break_minutes)
            or saved_postpone != int(self._config.postpone_minutes)
            or saved_idle_reset != int(self._config.idle_reset_minutes)
        ):
            return False
        for key, expected in (
            ("workStartTime", self._config.work_start_time),
            ("workEndTime", self._config.work_end_time),
            ("lunchStartTime", self._config.lunch_start_time),
            ("lunchEndTime", self._config.lunch_end_time),
        ):
            if key in state and str(state.get(key) or "") != str(expected):
                return False
        if "lunchEnabled" in state and bool(state.get("lunchEnabled")) != bool(
            self._config.lunch_enabled
        ):
            return False

        current = float(self._clock())
        self._postpone_used = bool(state.get("postponeUsed"))
        self._active_message = str(state.get("reminderMessage") or "")
        self._idle_break_active = False
        self._schedule_waiting = False
        self._pending_transition = None

        try:
            started_wall = float(state.get("cycleStartedAtMs") or 0.0) / 1000.0
            next_wall = float(state.get("nextFireAtMs") or 0.0) / 1000.0
        except (TypeError, ValueError):
            return False
        if started_wall <= 0 or next_wall <= 0 or next_wall < started_wall:
            return False
        self._restore_focus_times(current, wall_now, started_wall, next_wall)
        phase = str(state.get("phase") or "focus").strip().lower()

        if phase == "focus" and bool(state.get("scheduleWaiting")):
            resume_wall = next_wall
            if resume_wall > wall_now:
                self._schedule_waiting = True
                return True
            interval = float(self._config.interval_minutes) * 60.0
            self._restore_focus_times(
                current,
                wall_now,
                resume_wall,
                resume_wall + interval,
            )
            return True

        if phase in {"break", "resting"}:
            try:
                rest_started_wall = float(state.get("restStartedAtMs") or 0.0) / 1000.0
                rest_ends_wall = float(state.get("restEndsAtMs") or 0.0) / 1000.0
            except (TypeError, ValueError):
                rest_started_wall = 0.0
                rest_ends_wall = 0.0
            if rest_started_wall > 0 and rest_ends_wall > wall_now:
                self._phase = "resting"
                self._rest_started_wall = rest_started_wall
                self._rest_until_wall = rest_ends_wall
                self._rest_started_at = current - max(0.0, wall_now - rest_started_wall)
                self._rest_until = current + max(0.0, rest_ends_wall - wall_now)
                self._clear_prompt()
                return True
            if rest_started_wall > 0 and rest_ends_wall > rest_started_wall:
                self._credit_rest_wall_interval(rest_started_wall, rest_ends_wall)
            self._phase = "focus"
            self._postpone_used = False
            self._arm_from(current, wall_now)
            return True

        if phase == "prompt":
            # Prompt state is restored indefinitely, including snapshots made
            # by older versions that stored a finite prompt deadline.
            self._phase = "prompt"
            self._prompt_until_wall = 0.0
            self._prompt_until = 0.0
            try:
                prompt_started_wall = float(state.get("promptStartedAtMs") or 0.0) / 1000.0
            except (TypeError, ValueError):
                prompt_started_wall = 0.0
            # Older snapshots do not have a prompt start timestamp.  Keep
            # them compatible while making all new prompts count from the
            # moment the prompt was actually shown.
            self._prompt_started_wall = prompt_started_wall if prompt_started_wall > 0 else wall_now
            self._last_event = RestReminderEvent(
                message=self._active_message or REST_REMINDER_MESSAGES[0],
                can_postpone=not self._postpone_used,
                interval_minutes=self._config.interval_minutes,
                break_minutes=self._config.break_minutes,
                postpone_minutes=self._config.postpone_minutes,
                fired_at=current,
                ends_at=0.0,
            )
            return True

        if phase == "postponed":
            try:
                postpone_wall = float(state.get("postponeEndsAtMs") or 0.0) / 1000.0
            except (TypeError, ValueError):
                postpone_wall = 0.0
            if postpone_wall > wall_now:
                self._phase = "postponed"
                self._postpone_until_wall = postpone_wall
                self._next_fire_wall = postpone_wall
                self._next_fire_at = current + (postpone_wall - wall_now)
                self._cycle_started_wall = wall_now
                self._cycle_started_at = current
                return True
            self._phase = "focus"
            self._postpone_used = False
            self._arm_from(current, wall_now)
            return True

        self._phase = "focus"
        if next_wall <= wall_now:
            self._postpone_used = False
            # Preserve the missed deadline so the first post-restart tick emits
            # the reminder instead of silently starting a fresh full interval.
            self._next_fire_at = current
        return True

    def configure(
        self,
        config: RestReminderConfig | object,
        *,
        force_reset: bool = False,
    ) -> None:
        next_config = (
            config
            if isinstance(config, RestReminderConfig)
            else RestReminderConfig.from_user_config(config)
        )
        previous = self._config
        if previous.enabled and not next_config.enabled and self.resting:
            self._credit_rest_wall_interval(
                self._rest_started_wall,
                min(float(self._wall_clock()), self._rest_until_wall),
            )
        self._config = next_config
        if not next_config.enabled:
            self._reset_runtime_state()
            return
        timing_changed = (
            force_reset
            or not previous.enabled
            or previous.interval_minutes != next_config.interval_minutes
            or previous.break_minutes != next_config.break_minutes
            or previous.postpone_minutes != next_config.postpone_minutes
            or previous.idle_reset_minutes != next_config.idle_reset_minutes
            or previous.work_start_time != next_config.work_start_time
            or previous.work_end_time != next_config.work_end_time
            or previous.lunch_enabled != next_config.lunch_enabled
            or previous.lunch_start_time != next_config.lunch_start_time
            or previous.lunch_end_time != next_config.lunch_end_time
        )
        if timing_changed and self._phase == "focus":
            self._arm_from_now()
            self._postpone_used = False

    def tick(self, now: float | None = None) -> RestReminderEvent | None:
        current = self._clock() if now is None else float(now)
        if not self._config.enabled:
            return None
        wall_now = float(self._wall_clock())
        self._refresh_daily_date(wall_now)
        schedule_state, boundary = self._schedule_state(wall_now)
        if self.showing:
            # A prompt remains visible until an explicit user choice.  In
            # particular, work-hour/lunch boundaries must not auto-skip it.
            return None
        if schedule_state != "work":
            if self.resting:
                self._finalize_rest(current, wall_now, "schedule_ended", arm=False)
            elif self.showing:
                self._clear_prompt()
            waiting_for_same_boundary = bool(
                self._schedule_waiting
                and boundary is not None
                and abs(self._next_fire_wall - boundary) <= 1e-6
            )
            self._phase = "focus"
            self._postpone_until_wall = 0.0
            self._schedule_waiting = True
            self._idle_break_active = False
            if not waiting_for_same_boundary:
                self._cycle_started_at = current
                self._cycle_started_wall = wall_now
                wait = max(0.0, (boundary or wall_now) - wall_now)
                self._next_fire_at = current + wait
                self._next_fire_wall = float(boundary or wall_now)
            self._postpone_used = False
            return None
        if self._schedule_waiting:
            self._schedule_waiting = False
            self._arm_from(current, wall_now)
            return None
        if self.resting:
            if current < self._rest_until:
                return None
            self._finalize_rest(current, wall_now, "auto_completed")
            return None

        idle_seconds = self._idle_seconds()
        idle_reset = float(self._config.idle_reset_minutes) * 60.0
        if idle_reset > 0 and idle_seconds is not None and idle_seconds >= idle_reset:
            if not self._idle_break_active:
                self._phase = "focus"
                self._postpone_until_wall = 0.0
                self._arm_from(current, wall_now)
                self._postpone_used = False
            self._idle_break_active = True
            return None
        if self._idle_break_active:
            self._idle_break_active = False
            self._phase = "focus"
            self._postpone_until_wall = 0.0
            self._arm_from(current, wall_now)
            return None
        if current < self._next_fire_at:
            return None
        return self._begin_prompt(current, wall_now)

    def start_rest(self, now: float | None = None) -> bool:
        if self._phase not in {"prompt", "postponed"}:
            return False
        current = self._clock() if now is None else float(now)
        wall_now = float(self._wall_clock())
        duration = float(self._config.break_minutes) * 60.0
        self._clear_prompt()
        self._phase = "resting"
        self._postpone_until_wall = 0.0
        self._rest_started_at = current
        self._rest_started_wall = wall_now
        self._rest_until = current + duration
        self._rest_until_wall = wall_now + duration
        self._pending_transition = None
        return True

    def credit_early_rest(self, minutes: object, now: float | None = None) -> bool:
        """Credit a rest the user reports already having taken.

        During an active rest the stated minutes replace the in-app timing,
        so the wall interval measured so far is discarded rather than added.
        """
        if self._phase not in {"prompt", "postponed", "resting"}:
            return False
        try:
            amount = int(minutes)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError):
            return False
        if not (
            REST_REMINDER_EARLY_REST_MINUTES_MIN
            <= amount
            <= REST_REMINDER_EARLY_REST_MINUTES_MAX
        ):
            return False
        current = self._clock() if now is None else float(now)
        wall_now = float(self._wall_clock())
        duration = float(amount) * 60.0
        if self.resting:
            self._clear_rest()
        self._refresh_daily_date(wall_now)
        self._daily_rested_seconds += duration
        self._daily_rested_count += 1
        self._last_rest_duration_seconds = duration
        self._clear_prompt()
        self._phase = "focus"
        self._postpone_until_wall = 0.0
        self._postpone_used = False
        self._record_transition("credited_early", duration)
        self._arm_from(current, wall_now)
        return True

    def finish_rest(self, now: float | None = None) -> bool:
        if not self.resting:
            return False
        current = self._clock() if now is None else float(now)
        self._finalize_rest(current, float(self._wall_clock()), "finished_early")
        return True

    def acknowledge(self, now: float | None = None) -> None:
        if self.resting:
            self.finish_rest(now)
        else:
            self.start_rest(now)

    def postpone(self, now: float | None = None) -> bool:
        if not self.showing or self._postpone_used:
            return False
        current = self._clock() if now is None else float(now)
        wall_now = float(self._wall_clock())
        duration = float(self._config.postpone_minutes) * 60.0
        self._clear_prompt()
        self._phase = "postponed"
        self._postpone_used = True
        self._cycle_started_at = current
        self._cycle_started_wall = wall_now
        self._next_fire_at = current + duration
        self._next_fire_wall = wall_now + duration
        self._postpone_until_wall = self._next_fire_wall
        return True

    def dismiss_without_reschedule(self) -> None:
        self._clear_prompt()
        self._clear_rest()
        self._phase = "focus"

    def begin_preview(
        self,
        message: str | None = None,
        *,
        now: float | None = None,
    ) -> RestReminderEvent:
        current = self._clock() if now is None else float(now)
        text = str(message or "").strip() or str(
            self._pick_message() or REST_REMINDER_MESSAGES[0]
        )
        break_seconds = float(self._config.break_minutes) * 60.0
        return RestReminderEvent(
            message=text,
            can_postpone=False,
            interval_minutes=int(self._config.interval_minutes),
            break_minutes=int(self._config.break_minutes),
            postpone_minutes=int(self._config.postpone_minutes),
            fired_at=current,
            ends_at=current + break_seconds,
        )

    def take_transition(self) -> dict[str, object] | None:
        transition = self._pending_transition
        self._pending_transition = None
        return dict(transition) if transition is not None else None

    def _begin_prompt(self, current: float, wall_now: float) -> RestReminderEvent:
        message = str(self._pick_message() or REST_REMINDER_MESSAGES[0])
        event = RestReminderEvent(
            message=message,
            can_postpone=not self._postpone_used,
            interval_minutes=self._config.interval_minutes,
            break_minutes=self._config.break_minutes,
            postpone_minutes=self._config.postpone_minutes,
            fired_at=current,
            ends_at=0.0,
        )
        self._phase = "prompt"
        self._active_message = message
        self._prompt_until = 0.0
        self._prompt_until_wall = 0.0
        self._prompt_started_wall = wall_now
        self._postpone_until_wall = 0.0
        self._last_event = event
        return event

    def _finalize_rest(
        self,
        current: float,
        wall_now: float,
        reason: str,
        *,
        arm: bool = True,
    ) -> None:
        if not self.resting:
            return
        end_mono = min(float(current), self._rest_until)
        end_wall = min(float(wall_now), self._rest_until_wall)
        duration = max(0.0, end_mono - self._rest_started_at)
        self._credit_rest_wall_interval(self._rest_started_wall, end_wall)
        self._last_rest_duration_seconds = duration
        self._clear_rest()
        self._phase = "focus"
        self._postpone_used = False
        self._record_transition(reason, duration)
        if arm and self._config.enabled:
            self._arm_from(float(current), float(wall_now))

    def _record_transition(self, kind: str, duration: float) -> None:
        self._pending_transition = {
            "kind": str(kind),
            "restDurationSeconds": int(round(max(0.0, duration))),
            "todayRestedSeconds": int(round(self.today_rested_seconds)),
            "todayRestedCount": int(self.today_rested_count),
        }

    def _clear_prompt(self) -> None:
        self._prompt_until = 0.0
        self._prompt_until_wall = 0.0
        self._prompt_started_wall = 0.0
        self._last_event = None

    def _clear_rest(self) -> None:
        self._rest_started_at = 0.0
        self._rest_started_wall = 0.0
        self._rest_until = 0.0
        self._rest_until_wall = 0.0

    def _reset_runtime_state(self) -> None:
        self._phase = "focus"
        self._cycle_started_at = 0.0
        self._cycle_started_wall = 0.0
        self._next_fire_at = 0.0
        self._next_fire_wall = 0.0
        self._postpone_until_wall = 0.0
        self._postpone_used = False
        self._schedule_waiting = False
        self._idle_break_active = False
        self._active_message = ""
        self._pending_transition = None
        self._clear_prompt()
        self._clear_rest()

    def _arm_from_now(self) -> None:
        self._arm_from(float(self._clock()), float(self._wall_clock()))

    def _arm_from(self, current: float, wall_now: float | None = None) -> None:
        wall_value = float(self._wall_clock()) if wall_now is None else float(wall_now)
        duration = float(self._config.interval_minutes) * 60.0
        self._phase = "focus"
        self._cycle_started_at = current
        self._cycle_started_wall = wall_value
        self._next_fire_at = current + duration
        self._next_fire_wall = wall_value + duration
        self._postpone_until_wall = 0.0

    def rearm_from_now(self) -> None:
        """Re-arm the focus cycle from the current moment (post-lock recovery).

        A reminder that matured while the machine was locked must not fire
        right after the user returns; the cycle is restarted from the unlock
        moment instead. A stale prompt that was left visible before the lock
        is dismissed, and any postpone credit is cleared.
        """
        self._clear_prompt()
        self._arm_from_now()
        self._postpone_used = False

    def _restore_focus_times(
        self,
        current: float,
        wall_now: float,
        started_wall: float,
        next_wall: float,
    ) -> None:
        self._cycle_started_wall = started_wall
        self._next_fire_wall = next_wall
        self._cycle_started_at = current - max(0.0, wall_now - started_wall)
        self._next_fire_at = current + max(0.0, next_wall - wall_now)

    def _refresh_daily_date(self, wall_now: float) -> None:
        date_key = datetime.fromtimestamp(wall_now).date().isoformat()
        if self._daily_date != date_key:
            self._daily_date = date_key
            self._daily_rested_seconds = 0.0
            self._daily_rested_count = 0

    def _day_start_timestamp(self, wall_now: float) -> float:
        local = datetime.fromtimestamp(wall_now)
        return datetime(local.year, local.month, local.day).timestamp()

    def _credit_rest_wall_interval(self, started_wall: float, ended_wall: float) -> None:
        if started_wall <= 0 or ended_wall <= started_wall:
            return
        self._refresh_daily_date(ended_wall)
        end_date = datetime.fromtimestamp(ended_wall).date().isoformat()
        if self._daily_date != end_date:
            return
        day_start = self._day_start_timestamp(ended_wall)
        credited = max(0.0, ended_wall - max(started_wall, day_start))
        if credited <= 0:
            return
        self._daily_rested_seconds += credited
        self._daily_rested_count += 1

    def _schedule_state(self, wall_now: float) -> tuple[str, float | None]:
        local = datetime.fromtimestamp(wall_now)
        minute = local.hour * 60 + local.minute
        work_start = _minutes_from_time_text(self._config.work_start_time)
        work_end = _minutes_from_time_text(self._config.work_end_time)
        lunch_start = _minutes_from_time_text(self._config.lunch_start_time)
        lunch_end = _minutes_from_time_text(self._config.lunch_end_time)
        if work_start >= work_end:
            work_start, work_end = 0, 24 * 60
        in_work = work_start <= minute < work_end
        in_lunch = (
            in_work
            and self._config.lunch_enabled
            and lunch_start < lunch_end
            and lunch_start <= minute < lunch_end
        )
        state = "lunch" if in_lunch else "work" if in_work else "off"
        boundaries = [work_start, work_end]
        if self._config.lunch_enabled and lunch_start < lunch_end:
            boundaries.extend([lunch_start, lunch_end])
        next_minute = next((candidate for candidate in sorted(set(boundaries)) if candidate > minute), None)
        if next_minute is None:
            next_minute = min(boundaries) + 24 * 60
        midnight = datetime(local.year, local.month, local.day)
        boundary = midnight.timestamp() + next_minute * 60
        if boundary <= wall_now:
            boundary += 24 * 60 * 60
        return state, boundary


class RestReminderPresenter:
    """Expose one authoritative reminder state to renderer and desktop bubbles."""

    def __init__(
        self,
        scheduler: RestReminderScheduler | None = None,
        wall_clock: Callable[[], float] | None = None,
        *,
        state_path: Path | str | None = None,
        persist_enabled: bool = True,
    ) -> None:
        self.scheduler = scheduler or RestReminderScheduler()
        self._wall_clock = wall_clock or time.time
        self._state_path = Path(state_path) if state_path is not None else None
        self._persist_enabled = bool(persist_enabled)
        self._last_persisted_snapshot: dict[str, Any] | None = None
        self._preview_event: RestReminderEvent | None = None
        self._preview_until = 0.0
        self._preview_until_wall = 0.0
        self._completion: dict[str, object] | None = None
        self._completion_until = 0.0
        self._completion_until_wall = 0.0
        self._notification: dict[str, object] = {
            "status": "unknown",
            "channel": "",
            "error": "",
            "lastSentAtMs": 0,
        }

    def configure(
        self,
        user_config: object,
        *,
        force_reset: bool = False,
        restore_persisted: bool = False,
    ) -> None:
        was_enabled = self.scheduler.config.enabled
        self.scheduler.configure(user_config, force_reset=force_reset)
        if was_enabled and not self.scheduler.config.enabled:
            self.close()
            self._persist_state(clear=True)
            return
        if restore_persisted and self.scheduler.config.enabled:
            restored = self._restore_persisted_state()
            if restored:
                return
        self._persist_state()

    def tick(self) -> dict[str, object] | None:
        """Drive phase deadlines and return a payload only when state changes."""
        timing_before = self._scheduler_timing_signature()
        current = self.scheduler.monotonic_now()
        if self._preview_event is not None and current >= self._preview_until:
            self._clear_preview()
            payload = self.renderer_payload()
            payload.update({"stateChanged": True, "autoCompleted": True, "preview": True})
            return payload
        if self._completion is not None and current >= self._completion_until:
            self._clear_completion()
            payload = self.renderer_payload()
            payload["stateChanged"] = True
            return payload
        event = self.scheduler.tick()
        transition = self.scheduler.take_transition()
        if event is not None:
            self._clear_completion()
            self._notification = _show_system_notification(event.message)
            self._persist_state()
            payload = self.renderer_payload()
            payload["due"] = True
            return payload
        if transition is not None:
            kind = str(transition.get("kind") or "")
            if kind in {"auto_completed", "finished_early", "schedule_ended"}:
                self._show_completion(transition)
            self._persist_state()
            payload = self.renderer_payload()
            payload["stateChanged"] = True
            if kind == "auto_skipped":
                payload["autoSkipped"] = True
            elif kind == "auto_completed":
                payload["autoCompleted"] = True
            elif kind == "finished_early":
                payload["finishedEarly"] = True
            return payload
        self._persist_state()
        if self._scheduler_timing_signature() != timing_before:
            payload = self.renderer_payload()
            payload["stateChanged"] = True
            return payload
        return None

    def _scheduler_timing_signature(self) -> tuple[object, ...]:
        """Track deadline/state transitions that require a renderer refresh."""
        return (
            str(self.scheduler.state),
            float(self.scheduler.cycle_started_at),
            float(self.scheduler.next_fire_at),
            float(self.scheduler.showing_until),
            float(self.scheduler.rest_started_at_wall),
            float(self.scheduler.rest_ends_at_wall),
            float(self.scheduler.completed_today_seconds),
            int(self.scheduler.completed_today_count),
            float(self._preview_until),
            float(self._completion_until),
        )

    def acknowledge(self) -> None:
        if self._preview_event is not None:
            self._clear_preview()
            return
        if self.scheduler.resting:
            self.finish_rest()
        else:
            self.start_rest()

    def credit_early_rest(self, minutes: object) -> bool:
        """Credit a rest the user already took before this reminder."""
        ok = self.scheduler.credit_early_rest(minutes)
        if ok:
            self._clear_completion()
            transition = self.scheduler.take_transition()
            if transition is not None:
                self._show_completion(transition)
            self._persist_state()
        return ok

    def postpone(self) -> bool:
        if self._preview_event is not None:
            self._clear_preview()
            return True
        ok = self.scheduler.postpone()
        if ok:
            self._clear_completion()
            self._persist_state()
        return ok

    def start_rest(self) -> bool:
        if self._preview_event is not None:
            self._clear_preview()
            return True
        ok = self.scheduler.start_rest()
        if ok:
            self._clear_completion()
            self._persist_state()
        return ok

    def finish_rest(self) -> bool:
        if self._preview_event is not None:
            self._clear_preview()
            return True
        ok = self.scheduler.finish_rest()
        if not ok:
            return False
        transition = self.scheduler.take_transition()
        if transition is not None:
            self._show_completion(transition)
        self._persist_state()
        return True

    def seconds_until_wake(self) -> float | None:
        delays = [
            value
            for value in (
                self.scheduler.seconds_until_wake(),
                max(0.0, self._preview_until - self.scheduler.monotonic_now())
                if self._preview_event is not None
                else None,
                max(0.0, self._completion_until - self.scheduler.monotonic_now())
                if self._completion is not None
                else None,
            )
            if value is not None
        ]
        return min(delays) if delays else None

    def renderer_payload(self) -> dict[str, object]:
        config = self.scheduler.config
        state = self.scheduler.state
        now_wall_ms = float(self._wall_clock()) * 1000.0
        if config.enabled and self.scheduler.next_fire_at > 0:
            remaining = float(self.scheduler.seconds_until_next() or 0.0)
            duration = max(0.0, self.scheduler.next_fire_at - self.scheduler.cycle_started_at)
            elapsed = max(0.0, min(duration, duration - remaining))
            timing = {
                "enabled": True,
                "running": state == "work",
                "timerStartedAtMs": int(now_wall_ms - (elapsed * 1000.0)),
                "nextReminderAtMs": int(now_wall_ms + (remaining * 1000.0)),
                "remainingSeconds": int(round(remaining)),
                "durationSeconds": int(round(duration)),
            }
        else:
            timing = {
                "enabled": False,
                "running": False,
                "timerStartedAtMs": 0,
                "nextReminderAtMs": 0,
                "remainingSeconds": 0,
                "durationSeconds": int(config.interval_minutes * 60),
            }
        prompt_remaining = self.scheduler.seconds_until_prompt_end()
        break_remaining = self.scheduler.seconds_until_break_end()
        phase = self.scheduler.phase
        message = self.scheduler.active_message
        preview = self._preview_event is not None
        if preview:
            phase = "preview"
            message = self._preview_event.message if self._preview_event is not None else ""
        elif self._completion is not None:
            phase = "completed"
        break_timing = {
            "breakDurationSeconds": int(config.break_minutes * 60),
            "breakRemainingSeconds": int(round(break_remaining or 0.0)),
            "breakEndsAtMs": int(
                round(now_wall_ms + float(break_remaining or 0.0) * 1000.0)
            )
            if break_remaining is not None
            else 0,
            "promptRemainingSeconds": int(round(prompt_remaining or 0.0)),
            "promptEndsAtMs": int(round(self.scheduler.prompt_ends_at_wall * 1000.0)),
            "promptStartedAtMs": int(
                round(self.scheduler.prompt_started_at_wall * 1000.0)
            ),
            "postponeEndsAtMs": int(round(self.scheduler.postpone_ends_at_wall * 1000.0)),
            "restStartedAtMs": int(round(self.scheduler.rest_started_at_wall * 1000.0)),
            "restEndsAtMs": int(round(self.scheduler.rest_ends_at_wall * 1000.0)),
        }
        if preview:
            break_timing["promptEndsAtMs"] = int(round(self._preview_until_wall * 1000.0))
            break_timing["breakEndsAtMs"] = int(round(self._preview_until_wall * 1000.0))
        completion = dict(self._completion or {})
        payload = {
            "visible": phase in {"prompt", "preview"},
            "bubbleVisible": phase in {"prompt", "postponed", "resting", "completed", "preview"},
            "preview": preview,
            "notification": dict(self._notification),
            "state": state,
            "phase": phase,
            "message": message,
            "canPostpone": bool(
                phase == "prompt" and not self.scheduler.postpone_used
            ),
            "promptWaitInfinite": bool(phase == "prompt" and not preview),
            "earlyRestOptionsMinutes": [3, 5, 10],
            "intervalMinutes": int(config.interval_minutes),
            "breakMinutes": int(config.break_minutes),
            "postponeMinutes": int(config.postpone_minutes),
            "todayRestedSeconds": int(round(self.scheduler.today_rested_seconds)),
            "completedTodaySeconds": int(round(self.scheduler.completed_today_seconds)),
            "todayRestedCount": int(self.scheduler.today_rested_count),
            "completedTodayCount": int(self.scheduler.completed_today_count),
            "currentRestElapsedSeconds": int(
                round(self.scheduler.current_rest_elapsed_seconds())
            ),
            "lastRestDurationSeconds": int(
                completion.get(
                    "restDurationSeconds", self.scheduler.last_rest_duration_seconds
                )
                or 0
            ),
            "completionEndsAtMs": int(round(self._completion_until_wall * 1000.0))
            if self._completion is not None
            else 0,
            **timing,
            **break_timing,
        }
        if completion:
            payload.update(completion)
        return payload

    def desktop_bubble_payload(self) -> dict[str, object]:
        payload = self.renderer_payload()
        stable_keys = (
            "bubbleVisible",
            "preview",
            "phase",
            "message",
            "canPostpone",
            "intervalMinutes",
            "breakMinutes",
            "postponeMinutes",
            "promptEndsAtMs",
            "promptStartedAtMs",
            "postponeEndsAtMs",
            "promptWaitInfinite",
            "earlyRestOptionsMinutes",
            "restStartedAtMs",
            "restEndsAtMs",
            "todayRestedSeconds",
            "completedTodaySeconds",
            "todayRestedCount",
            "completedTodayCount",
            "lastRestDurationSeconds",
            "completionEndsAtMs",
        )
        return {key: payload.get(key) for key in stable_keys}

    def adjust_cycle_started_at_ms(self, started_at_ms: object) -> bool:
        try:
            started = float(started_at_ms) / 1000.0
        except (TypeError, ValueError):
            return False
        self.scheduler.set_cycle_started_at_wall(started)
        self._persist_state()
        return True

    def rearm_after_session_unlock(self) -> dict[str, object] | None:
        """Restart the focus cycle from the unlock moment.

        Called by the renderer runtime right after a session-lock resume so
        that a reminder which matured while the machine was locked never
        fires the moment the user returns. A rest in progress is left to
        finish naturally; otherwise the cycle is re-armed from the unlock
        moment and any stale prompt is dismissed. Returns a renderer
        payload when the cycle changed, else None.
        """
        if self.scheduler.phase == "resting":
            return None
        self.scheduler.rearm_from_now()
        self._clear_completion()
        self._clear_preview()
        self._persist_state()
        payload = self.renderer_payload()
        payload["stateChanged"] = True
        payload["rescheduledAfterUnlock"] = True
        return payload

    def test_notification(self) -> dict[str, object]:
        """Send a system notification and open the full rest overlay as a preview."""
        message = "系统通知测试成功 · 这是专注休息提醒的实际弹窗预览"
        event = self.scheduler.begin_preview(message)
        notification = _show_system_notification(event.message)
        self._notification = notification
        remaining = max(0.0, event.ends_at - self.scheduler.monotonic_now())
        self._preview_event = event
        self._preview_until = event.ends_at
        self._preview_until_wall = float(self._wall_clock()) + remaining
        self._clear_completion()
        result = dict(notification)
        result["preview"] = True
        result["message"] = event.message
        return result

    def close(self) -> None:
        self._clear_preview()
        self._clear_completion()
        self.scheduler.dismiss_without_reschedule()

    def _persist_state(self, *, clear: bool = False) -> None:
        if not self._persist_enabled:
            return
        try:
            from ..config import save_rest_reminder_state

            if clear or not self.scheduler.config.enabled:
                if self._last_persisted_snapshot is None:
                    return
                save_rest_reminder_state(None, self._state_path)
                self._last_persisted_snapshot = None
                return
            snapshot = self.scheduler.export_wall_state()
            if snapshot is None:
                if self._last_persisted_snapshot is None:
                    return
                save_rest_reminder_state(None, self._state_path)
                self._last_persisted_snapshot = None
                return
            if self._last_persisted_snapshot == snapshot:
                return
            save_rest_reminder_state(snapshot, self._state_path)
            self._last_persisted_snapshot = dict(snapshot)
        except Exception:
            _LOGGER.debug("rest_reminder_persist_failed", exc_info=True)

    def _restore_persisted_state(self) -> bool:
        if not self._persist_enabled:
            return False
        try:
            from ..config import load_rest_reminder_state, save_rest_reminder_state

            state = load_rest_reminder_state(self._state_path)
            if not state:
                return False
            restored = bool(self.scheduler.restore_wall_state(state))
            if restored:
                normalized = self.scheduler.export_wall_state()
                restored_state = dict(normalized) if normalized is not None else {}
                if restored_state != dict(state):
                    save_rest_reminder_state(restored_state or None, self._state_path)
                self._last_persisted_snapshot = restored_state or None
            return restored
        except Exception:
            _LOGGER.debug("rest_reminder_restore_failed", exc_info=True)
            return False

    def _show_completion(self, transition: Mapping[str, object]) -> None:
        self._completion = dict(transition)
        self._completion_until = (
            self.scheduler.monotonic_now() + REST_REMINDER_COMPLETION_FEEDBACK_SECONDS
        )
        self._completion_until_wall = (
            float(self._wall_clock()) + REST_REMINDER_COMPLETION_FEEDBACK_SECONDS
        )

    def _clear_completion(self) -> None:
        self._completion = None
        self._completion_until = 0.0
        self._completion_until_wall = 0.0

    def _clear_preview(self) -> None:
        self._preview_event = None
        self._preview_until = 0.0
        self._preview_until_wall = 0.0


def _show_system_notification(message: str) -> dict[str, object]:
    """Send a desktop notification and expose the selected channel/result."""
    title = "休息提醒"
    body = str(message or "").strip() or "该休息一下了。"
    sent_at = int(time.time() * 1000)
    if sys.platform == "win32":
        try:
            safe = body.replace("'", "''")[:180]
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "Add-Type -AssemblyName System.Drawing; "
                "$n = New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                "$n.Visible = $true; "
                "[System.Media.SystemSounds]::Asterisk.Play(); "
                f"$n.ShowBalloonTip({WINDOWS_NOTIFICATION_DISPLAY_MILLISECONDS}, "
                f"'{title}', '{safe}', "
                "[System.Windows.Forms.ToolTipIcon]::Info); "
                "Start-Sleep -Seconds 30; $n.Dispose()"
            )
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=external_process_environment(),
                creationflags=creationflags,
            )
            return {"status": "sent", "channel": "windows-notifyicon", "error": "", "lastSentAtMs": sent_at}
        except Exception as exc:
            return {"status": "failed", "channel": "windows-notifyicon", "error": str(exc), "lastSentAtMs": sent_at}
    if sys.platform == "darwin":
        try:
            subprocess.run(["osascript", "-e", f'display notification "{body.replace(chr(34), chr(39))}" with title "{title}"'], check=True, timeout=5, env=external_process_environment())
            return {"status": "sent", "channel": "osascript", "error": "", "lastSentAtMs": sent_at}
        except Exception as exc:
            return {"status": "failed", "channel": "osascript", "error": str(exc), "lastSentAtMs": sent_at}
    try:
        subprocess.run(["notify-send", title, body], check=True, timeout=5, env=external_process_environment())
        return {"status": "sent", "channel": "notify-send", "error": "", "lastSentAtMs": sent_at}
    except Exception as exc:
        return {"status": "failed", "channel": "notify-send", "error": str(exc), "lastSentAtMs": sent_at}
