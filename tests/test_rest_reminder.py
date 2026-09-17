"""Unit tests for rest reminder scheduler and config fields."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.config import UserConfig, UserConfigStore
from codex_usage_hud.core.rest_reminder import (
    RestReminderConfig,
    RestReminderPresenter,
    RestReminderScheduler,
)
import codex_usage_hud.core.rest_reminder as rest_reminder_module


class RestReminderConfigTests(unittest.TestCase):
    def test_defaults_disabled(self) -> None:
        config = UserConfig.defaults()
        self.assertFalse(config.rest_reminder_enabled)
        self.assertEqual(config.rest_reminder_interval_minutes, 45)
        self.assertEqual(config.rest_reminder_break_minutes, 2)
        self.assertEqual(config.rest_reminder_postpone_minutes, 10)
        self.assertEqual(config.rest_reminder_idle_reset_minutes, 0)
        self.assertEqual(config.rest_reminder_work_start_time, "09:00")
        self.assertEqual(config.rest_reminder_work_end_time, "18:00")
        self.assertTrue(config.rest_reminder_lunch_enabled)
        self.assertEqual(config.rest_reminder_lunch_start_time, "12:00")
        self.assertEqual(config.rest_reminder_lunch_end_time, "13:30")

    def test_round_trip_and_bounds(self) -> None:
        config = UserConfig.from_dict(
            {
                "rest_reminder_enabled": True,
                "rest_reminder_interval_minutes": 0,
                "rest_reminder_break_minutes": 100,
                "rest_reminder_postpone_minutes": 100,
                    "rest_reminder_idle_reset_minutes": -3,
                    "rest_reminder_work_start_time": "8:5",
                    "rest_reminder_work_end_time": "19:10",
                    "rest_reminder_lunch_enabled": False,
                    "rest_reminder_lunch_start_time": "12:00",
                    "rest_reminder_lunch_end_time": "13:00",
            }
        )
        self.assertTrue(config.rest_reminder_enabled)
        self.assertEqual(config.rest_reminder_interval_minutes, 1)
        self.assertEqual(config.rest_reminder_break_minutes, 10)
        self.assertEqual(config.rest_reminder_postpone_minutes, 30)
        self.assertEqual(config.rest_reminder_idle_reset_minutes, 0)
        self.assertEqual(config.rest_reminder_work_start_time, "08:05")
        self.assertEqual(config.rest_reminder_work_end_time, "19:10")
        self.assertFalse(config.rest_reminder_lunch_enabled)
        payload = config.to_dict()
        self.assertTrue(payload["rest_reminder_enabled"])
        self.assertEqual(payload["rest_reminder_interval_minutes"], 1)

    def test_supported_focus_intervals_round_trip(self) -> None:
        for interval_minutes in (1, 10, 37, 180):
            with self.subTest(interval_minutes=interval_minutes):
                config = UserConfig.from_dict(
                    {
                        "rest_reminder_enabled": True,
                        "rest_reminder_interval_minutes": interval_minutes,
                    }
                )

                self.assertEqual(
                    config.rest_reminder_interval_minutes,
                    interval_minutes,
                )
                self.assertEqual(
                    config.to_dict()["rest_reminder_interval_minutes"],
                    interval_minutes,
                )

    def test_store_preserves_rest_reminder_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            store = UserConfigStore(path)
            config = UserConfig.from_dict(
                {
                    "rest_reminder_enabled": True,
                    "rest_reminder_interval_minutes": 10,
                    "rest_reminder_break_minutes": 3,
                    "rest_reminder_postpone_minutes": 15,
                    "rest_reminder_idle_reset_minutes": 8,
                    "rest_reminder_work_start_time": "08:30",
                    "rest_reminder_work_end_time": "17:30",
                    "rest_reminder_lunch_enabled": True,
                    "rest_reminder_lunch_start_time": "12:00",
                    "rest_reminder_lunch_end_time": "13:00",
                }
            )
            store.save(config)
            loaded = store.load()
            self.assertTrue(loaded.rest_reminder_enabled)
            self.assertEqual(loaded.rest_reminder_interval_minutes, 10)
            self.assertEqual(loaded.rest_reminder_break_minutes, 3)
            self.assertEqual(loaded.rest_reminder_postpone_minutes, 15)
            self.assertEqual(loaded.rest_reminder_idle_reset_minutes, 8)
            self.assertEqual(loaded.rest_reminder_work_start_time, "08:30")
            self.assertEqual(loaded.rest_reminder_work_end_time, "17:30")


class RestReminderSchedulerTests(unittest.TestCase):
    WORK_WALL = datetime(2024, 1, 2, 10, 0).timestamp()

    def test_focus_interval_changes_restart_current_round(self) -> None:
        for old_minutes, new_minutes in ((15, 10), (10, 37), (37, 1), (1, 180)):
            with self.subTest(old_minutes=old_minutes, new_minutes=new_minutes):
                clock = {"now": 1000.0}
                wall = {"now": self.WORK_WALL}
                scheduler = RestReminderScheduler(
                    idle_seconds_provider=lambda: 0.0,
                    clock=lambda: clock["now"],
                    wall_clock=lambda: wall["now"],
                )
                scheduler.configure(
                    RestReminderConfig(enabled=True, interval_minutes=old_minutes),
                    force_reset=True,
                )

                clock["now"] += 120.0
                wall["now"] += 120.0
                scheduler.configure(
                    RestReminderConfig(enabled=True, interval_minutes=new_minutes)
                )

                self.assertEqual(scheduler.cycle_started_at, 1120.0)
                self.assertEqual(
                    scheduler.next_fire_at,
                    1120.0 + new_minutes * 60.0,
                )
                self.assertEqual(
                    scheduler.seconds_until_next(),
                    new_minutes * 60.0,
                )

    def test_presenter_payload_exposes_timer_start_and_remaining(self) -> None:
        clock = {"now": 1000.0}
        wall = {"now": 1_700_000_000.0}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        presenter = RestReminderPresenter(
            scheduler,
            wall_clock=lambda: wall["now"],
            persist_enabled=False,
        )
        presenter.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                postpone_minutes=5,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )

        payload = presenter.renderer_payload()
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["timerStartedAtMs"], 1_700_000_000_000)
        self.assertEqual(payload["remainingSeconds"], 60)
        self.assertEqual(payload["durationSeconds"], 60)

        clock["now"] = 1012.0
        wall["now"] += 12.0
        payload = presenter.renderer_payload()
        self.assertEqual(payload["remainingSeconds"], 48)
        self.assertEqual(payload["timerStartedAtMs"], 1_700_000_000_000)

    def test_prompt_payload_exposes_popup_start_for_elapsed_title_timer(self) -> None:
        clock = {"now": 0.0}
        wall = {"now": self.WORK_WALL}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        presenter = RestReminderPresenter(
            scheduler,
            wall_clock=lambda: wall["now"],
            persist_enabled=False,
        )
        presenter.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=1,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )

        clock["now"] = 61.0
        wall["now"] += 61.0
        self.assertIsNotNone(presenter.tick())
        first = presenter.desktop_bubble_payload()
        self.assertEqual(first["phase"], "prompt")
        self.assertEqual(first["promptStartedAtMs"], int(wall["now"] * 1000))

        wall["now"] += 7.0
        second = presenter.renderer_payload()
        self.assertEqual(second["promptStartedAtMs"], first["promptStartedAtMs"])

    def test_prompt_start_and_early_finish_reschedules(self) -> None:
        clock = {"now": 1000.0}
        wall = {"now": self.WORK_WALL}
        idle = {"seconds": 0.0}
        messages = iter(["msg-a", "msg-b"])
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: idle["seconds"],
            message_picker=lambda: next(messages),
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                postpone_minutes=1,
                idle_reset_minutes=5,
            ),
            force_reset=True,
        )
        self.assertIsNone(scheduler.tick())
        clock["now"] = 1000.0 + 61.0
        event = scheduler.tick()
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.message, "msg-a")
        self.assertTrue(event.can_postpone)
        # While showing, no second fire.
        self.assertIsNone(scheduler.tick())
        scheduler.acknowledge()
        self.assertFalse(scheduler.showing)
        self.assertTrue(scheduler.resting)
        self.assertEqual(scheduler.today_rested_count, 1)
        self.assertEqual(scheduler.completed_today_count, 0)
        clock["now"] += 30.0
        wall["now"] += 30.0
        self.assertTrue(scheduler.finish_rest())
        transition = scheduler.take_transition()
        assert transition is not None
        self.assertEqual(transition["kind"], "finished_early")
        self.assertEqual(transition["restDurationSeconds"], 30)
        self.assertEqual(transition["todayRestedSeconds"], 30)
        self.assertEqual(transition["todayRestedCount"], 1)
        self.assertEqual(scheduler.completed_today_count, 1)
        clock["now"] = scheduler.next_fire_at + 0.1
        wall["now"] += 60.1
        event2 = scheduler.tick()
        self.assertIsNotNone(event2)
        assert event2 is not None
        self.assertEqual(event2.message, "msg-b")

    def test_prompt_waits_indefinitely_without_rest_credit(self) -> None:
        clock = {"now": 0.0}
        messages = iter(["first", "second"])
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: next(messages),
            clock=lambda: clock["now"],
            wall_clock=lambda: self.WORK_WALL,
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=1,
                postpone_minutes=1,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )

        clock["now"] = 61.0
        first = scheduler.tick()
        self.assertIsNotNone(first)
        self.assertTrue(scheduler.showing)

    def test_credit_early_rest_adds_duration_and_count_and_rearms_focus(self) -> None:
        clock = {"now": 0.0}
        wall = {"now": self.WORK_WALL}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=2,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )
        clock["now"] = 61.0
        wall["now"] += 61.0
        self.assertIsNotNone(scheduler.tick())
        self.assertTrue(scheduler.credit_early_rest(3))
        self.assertFalse(scheduler.showing)
        self.assertEqual(scheduler.phase, "focus")
        self.assertEqual(scheduler.today_rested_seconds, 180)
        self.assertEqual(scheduler.today_rested_count, 1)
        self.assertEqual(scheduler.completed_today_seconds, 180)
        self.assertEqual(scheduler.completed_today_count, 1)
        self.assertEqual(scheduler.last_rest_duration_seconds, 180)
        transition = scheduler.take_transition()
        self.assertIsNotNone(transition)
        assert transition is not None
        self.assertEqual(transition["kind"], "credited_early")
        self.assertEqual(transition["restDurationSeconds"], 180)
        self.assertEqual(scheduler.next_fire_at, clock["now"] + 60.0)

    def test_credit_early_rest_accepts_more_minutes_and_rejects_invalid_values(self) -> None:
        clock = {"now": 0.0}
        wall = {"now": self.WORK_WALL}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        scheduler.configure(
            RestReminderConfig(enabled=True, interval_minutes=1, idle_reset_minutes=0),
            force_reset=True,
        )
        clock["now"] = 61.0
        self.assertIsNotNone(scheduler.tick())
        self.assertFalse(scheduler.credit_early_rest(0))
        self.assertTrue(scheduler.showing)
        self.assertFalse(scheduler.credit_early_rest(1441))
        self.assertTrue(scheduler.showing)
        self.assertFalse(scheduler.credit_early_rest("5.5"))
        self.assertTrue(scheduler.showing)
        self.assertTrue(scheduler.credit_early_rest(15))
        self.assertEqual(scheduler.today_rested_seconds, 900)
        self.assertEqual(scheduler.today_rested_count, 1)
        self.assertEqual(scheduler.phase, "focus")
        self.assertFalse(scheduler.showing)
        self.assertIsNone(scheduler.seconds_until_prompt_end())
        self.assertIsNotNone(scheduler.seconds_until_wake())

    def test_credit_early_rest_during_postponed_credits_and_rearms(self) -> None:
        clock = {"now": 0.0}
        wall = {"now": self.WORK_WALL}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                postpone_minutes=10,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )
        clock["now"] = 61.0
        wall["now"] += 61.0
        self.assertIsNotNone(scheduler.tick())
        self.assertTrue(scheduler.postpone())
        self.assertEqual(scheduler.phase, "postponed")

        wall["now"] += 120.0
        self.assertTrue(scheduler.credit_early_rest(5))
        self.assertEqual(scheduler.phase, "focus")
        self.assertFalse(scheduler.resting)
        self.assertEqual(scheduler.today_rested_seconds, 300)
        self.assertEqual(scheduler.today_rested_count, 1)
        self.assertEqual(scheduler.last_rest_duration_seconds, 300)
        transition = scheduler.take_transition()
        assert transition is not None
        self.assertEqual(transition["kind"], "credited_early")
        self.assertEqual(transition["restDurationSeconds"], 300)
        self.assertEqual(scheduler.next_fire_at, clock["now"] + 60.0)

    def test_credit_early_rest_during_resting_replaces_elapsed_with_stated_minutes(self) -> None:
        clock = {"now": 0.0}
        wall = {"now": self.WORK_WALL}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=10,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )
        clock["now"] = 61.0
        wall["now"] += 61.0
        self.assertIsNotNone(scheduler.tick())
        self.assertTrue(scheduler.start_rest())
        self.assertTrue(scheduler.resting)

        clock["now"] += 90.0
        wall["now"] += 90.0
        self.assertEqual(scheduler.today_rested_seconds, 90)
        self.assertTrue(scheduler.credit_early_rest(5))
        self.assertEqual(scheduler.phase, "focus")
        self.assertFalse(scheduler.resting)
        self.assertEqual(scheduler.rest_started_at_wall, 0.0)
        # The stated minutes replace the in-app elapsed rest, not add to it.
        self.assertEqual(scheduler.today_rested_seconds, 300)
        self.assertEqual(scheduler.today_rested_count, 1)
        self.assertEqual(scheduler.last_rest_duration_seconds, 300)
        transition = scheduler.take_transition()
        assert transition is not None
        self.assertEqual(transition["kind"], "credited_early")
        self.assertEqual(transition["todayRestedSeconds"], 300)
        self.assertEqual(scheduler.next_fire_at, clock["now"] + 60.0)

    def test_presenter_keeps_due_prompt_visible_until_explicit_choice(self) -> None:
        clock = {"now": 0.0}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: self.WORK_WALL,
        )
        presenter = RestReminderPresenter(
            scheduler,
            wall_clock=lambda: self.WORK_WALL,
            persist_enabled=False,
        )
        presenter.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=1,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )

        clock["now"] = 61.0
        with patch.object(
            rest_reminder_module,
            "_show_system_notification",
            return_value={
                "status": "sent",
                "channel": "test",
                "error": "",
                "lastSentAtMs": 123,
            },
        ) as notify:
            due = presenter.tick()
            self.assertIsNotNone(due)
            self.assertIsNone(presenter.tick())
            notify.assert_called_once()

        clock["now"] = 121.0
        self.assertIsNone(presenter.tick())
        payload = presenter.renderer_payload()
        self.assertTrue(payload["visible"])
        self.assertTrue(payload["bubbleVisible"])
        self.assertTrue(payload["promptWaitInfinite"])
        self.assertNotIn("autoSkipped", payload)
        self.assertEqual(payload["todayRestedSeconds"], 0)

    def test_postpone_once_only(self) -> None:
        clock = {"now": 0.0}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: self.WORK_WALL,
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                postpone_minutes=2,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )
        clock["now"] = 61.0
        event = scheduler.tick()
        self.assertIsNotNone(event)
        self.assertTrue(scheduler.postpone())
        self.assertFalse(scheduler.showing)
        clock["now"] = scheduler.next_fire_at + 0.1
        event2 = scheduler.tick()
        self.assertIsNotNone(event2)
        assert event2 is not None
        self.assertFalse(event2.can_postpone)
        self.assertFalse(scheduler.postpone())
        scheduler.acknowledge()

    def test_postponed_bubble_can_start_rest_at_any_time(self) -> None:
        clock = {"now": 0.0}
        wall = {"now": self.WORK_WALL}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        presenter = RestReminderPresenter(
            scheduler,
            wall_clock=lambda: wall["now"],
            persist_enabled=False,
        )
        presenter.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=1,
                postpone_minutes=2,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )

        clock["now"] = 61.0
        wall["now"] += 61.0
        with patch.object(
            rest_reminder_module,
            "_show_system_notification",
            return_value={"status": "sent", "channel": "test", "error": "", "lastSentAtMs": 1},
        ):
            self.assertIsNotNone(presenter.tick())
        self.assertTrue(presenter.postpone())
        postponed = presenter.desktop_bubble_payload()
        self.assertEqual(postponed["phase"], "postponed")
        self.assertGreater(postponed["postponeEndsAtMs"], int(wall["now"] * 1000))
        self.assertFalse(postponed["canPostpone"])

        clock["now"] += 30.0
        wall["now"] += 30.0
        self.assertTrue(presenter.start_rest())
        resting = presenter.desktop_bubble_payload()
        self.assertEqual(resting["phase"], "resting")
        self.assertGreater(resting["restEndsAtMs"], resting["restStartedAtMs"])

    def test_rest_auto_completes_credits_today_and_starts_next_focus_round(self) -> None:
        clock = {"now": 0.0}
        wall = {"now": self.WORK_WALL}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        presenter = RestReminderPresenter(
            scheduler,
            wall_clock=lambda: wall["now"],
            persist_enabled=False,
        )
        presenter.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=1,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )
        clock["now"] = 61.0
        wall["now"] += 61.0
        with patch.object(
            rest_reminder_module,
            "_show_system_notification",
            return_value={"status": "sent", "channel": "test", "error": "", "lastSentAtMs": 1},
        ):
            presenter.tick()
        self.assertTrue(presenter.start_rest())
        clock["now"] += 60.0
        wall["now"] += 60.0

        completed = presenter.tick()
        assert completed is not None
        self.assertTrue(completed["autoCompleted"])
        self.assertEqual(completed["phase"], "completed")
        self.assertEqual(completed["completedTodaySeconds"], 60)
        self.assertEqual(completed["completedTodayCount"], 1)
        self.assertEqual(completed["todayRestedCount"], 1)
        self.assertEqual(completed["lastRestDurationSeconds"], 60)
        self.assertEqual(scheduler.phase, "focus")
        self.assertEqual(scheduler.next_fire_at, clock["now"] + 60.0)

        clock["now"] += 1.5
        wall["now"] += 1.5
        cleared = presenter.tick()
        assert cleared is not None
        self.assertFalse(cleared["bubbleVisible"])

    def test_rest_crossing_midnight_counts_only_current_day(self) -> None:
        clock = {"now": 0.0}
        wall = {"now": datetime(2024, 1, 2, 23, 57, 49).timestamp()}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=2,
                idle_reset_minutes=0,
                work_start_time="00:00",
                work_end_time="23:59",
                lunch_enabled=False,
            ),
            force_reset=True,
        )
        clock["now"] = 61.0
        wall["now"] += 61.0
        self.assertIsNotNone(scheduler.tick())
        self.assertTrue(scheduler.start_rest())

        clock["now"] += 80.0
        wall["now"] += 80.0
        self.assertEqual(datetime.fromtimestamp(wall["now"]).date().isoformat(), "2024-01-03")
        self.assertAlmostEqual(scheduler.today_rested_seconds, 10.0, places=1)
        self.assertTrue(scheduler.finish_rest())
        transition = scheduler.take_transition()
        assert transition is not None
        self.assertEqual(transition["todayRestedSeconds"], 10)
        self.assertEqual(transition["todayRestedCount"], 1)
        self.assertEqual(scheduler.completed_today_seconds, 10)
        self.assertEqual(scheduler.completed_today_count, 1)

    def test_idle_reset_skips_fire(self) -> None:
        clock = {"now": 0.0}
        idle = {"seconds": 0.0}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: idle["seconds"],
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: self.WORK_WALL,
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                postpone_minutes=1,
                idle_reset_minutes=1,
            ),
            force_reset=True,
        )
        clock["now"] = 61.0
        idle["seconds"] = 120.0
        self.assertIsNone(scheduler.tick())
        self.assertGreater(scheduler.next_fire_at, 61.0)

    def test_idle_threshold_and_return_have_bounded_one_shot_wakes(self) -> None:
        clock = {"now": 0.0}
        idle = {"seconds": 0.0}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: idle["seconds"],
            clock=lambda: clock["now"],
            wall_clock=lambda: self.WORK_WALL,
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=45,
                idle_reset_minutes=2,
            ),
            force_reset=True,
        )

        self.assertEqual(scheduler.seconds_until_wake(), 120.0)
        idle["seconds"] = 119.0
        self.assertEqual(scheduler.seconds_until_wake(), 1.0)
        idle["seconds"] = 120.0
        self.assertEqual(scheduler.seconds_until_wake(), 0.0)
        self.assertIsNone(scheduler.tick())
        self.assertEqual(scheduler.state, "away")
        self.assertEqual(scheduler.seconds_until_wake(), 30.0)

        idle["seconds"] = 0.0
        clock["now"] = 30.0
        self.assertIsNone(scheduler.tick())
        self.assertEqual(scheduler.state, "work")
        self.assertEqual(scheduler.next_fire_at, 45 * 60 + 30.0)

    def test_presenter_emits_idle_deadline_changes_for_renderer(self) -> None:
        clock = {"now": 0.0}
        wall = {"now": self.WORK_WALL}
        idle = {"seconds": 0.0}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: idle["seconds"],
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        presenter = RestReminderPresenter(
            scheduler,
            wall_clock=lambda: wall["now"],
            persist_enabled=False,
        )
        presenter.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=45,
                idle_reset_minutes=2,
            ),
            force_reset=True,
        )

        clock["now"] = 120.0
        wall["now"] += 120.0
        idle["seconds"] = 120.0
        away = presenter.tick()
        self.assertIsNotNone(away)
        assert away is not None
        self.assertTrue(away["stateChanged"])
        self.assertEqual(away["state"], "away")
        self.assertFalse(away["running"])

        clock["now"] = 150.0
        wall["now"] += 30.0
        idle["seconds"] = 0.0
        resumed = presenter.tick()
        self.assertIsNotNone(resumed)
        assert resumed is not None
        self.assertTrue(resumed["stateChanged"])
        self.assertEqual(resumed["state"], "work")
        self.assertTrue(resumed["running"])
        self.assertEqual(resumed["remainingSeconds"], 45 * 60)
        self.assertEqual(
            resumed["timerStartedAtMs"],
            int(wall["now"] * 1000),
        )
        self.assertIsNone(presenter.tick())

    def test_lunch_and_off_hours_pause_until_schedule_boundary(self) -> None:
        clock = {"now": 0.0}
        wall = {"now": self.WORK_WALL + 2 * 60 * 60}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                postpone_minutes=1,
                idle_reset_minutes=0,
                work_start_time="09:00",
                work_end_time="18:00",
                lunch_enabled=True,
                lunch_start_time="12:00",
                lunch_end_time="13:30",
            ),
            force_reset=True,
        )
        self.assertEqual(scheduler.state, "lunch")
        self.assertIsNone(scheduler.tick())
        self.assertGreater(scheduler.seconds_until_wake() or 0, 0)
        wall["now"] += 90 * 60
        clock["now"] += 90 * 60
        self.assertIsNone(scheduler.tick())
        self.assertEqual(scheduler.state, "work")
        wall["now"] = self.WORK_WALL + 9 * 60 * 60
        clock["now"] = 10 * 60 * 60
        self.assertEqual(scheduler.state, "off")

    def test_repeated_schedule_waiting_ticks_keep_the_same_deadline(self) -> None:
        clock = {"now": 100.0}
        wall = {"now": self.WORK_WALL + 9 * 60 * 60}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=45,
                idle_reset_minutes=0,
                work_start_time="09:00",
                work_end_time="18:00",
                lunch_enabled=False,
            ),
            force_reset=True,
        )

        self.assertIsNone(scheduler.tick())
        first = scheduler.export_wall_state()
        assert first is not None

        clock["now"] += 10.0
        wall["now"] += 10.0
        self.assertIsNone(scheduler.tick())
        second = scheduler.export_wall_state()
        assert second is not None

        self.assertEqual(second["cycleStartedAtMs"], first["cycleStartedAtMs"])
        self.assertEqual(second["nextFireAtMs"], first["nextFireAtMs"])
        self.assertTrue(second["scheduleWaiting"])

    def test_off_hours_presenter_persists_only_when_waiting_boundary_changes(self) -> None:
        from codex_usage_hud import config as config_module

        clock = {"now": 100.0}
        wall = {"now": self.WORK_WALL + 9 * 60 * 60}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        with tempfile.NamedTemporaryFile(suffix=".json") as state_file:
            presenter = RestReminderPresenter(
                scheduler,
                wall_clock=lambda: wall["now"],
                persist_enabled=True,
                state_path=Path(state_file.name),
            )
            config = RestReminderConfig(
                enabled=True,
                interval_minutes=45,
                idle_reset_minutes=0,
                work_start_time="09:00",
                work_end_time="18:00",
                lunch_enabled=False,
            )

            with patch.object(
                config_module,
                "save_rest_reminder_state",
                wraps=config_module.save_rest_reminder_state,
            ) as save_state:
                presenter.configure(config, force_reset=True)
                presenter.tick()
                clock["now"] += 10.0
                wall["now"] += 10.0
                presenter.tick()

            written = [
                call.args[0] for call in save_state.call_args_list if call.args[0]
            ]
            self.assertGreaterEqual(len(written), 2)
            self.assertIn(False, {item.get("scheduleWaiting") for item in written})
            self.assertIn(True, {item.get("scheduleWaiting") for item in written})

    def test_lunch_boundary_does_not_close_visible_prompt(self) -> None:
        clock = {"now": 0.0}
        wall = {"now": self.WORK_WALL + 118 * 60}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=2,
                idle_reset_minutes=0,
                lunch_enabled=True,
                lunch_start_time="12:00",
                lunch_end_time="13:30",
            ),
            force_reset=True,
        )

        clock["now"] = 61.0
        wall["now"] += 61.0
        self.assertIsNotNone(scheduler.tick())
        self.assertTrue(scheduler.showing)
        self.assertIsNone(scheduler.seconds_until_wake())

        clock["now"] = 120.0
        wall["now"] = self.WORK_WALL + 120 * 60
        self.assertIsNone(scheduler.tick())
        self.assertTrue(scheduler.showing)
        self.assertEqual(scheduler.state, "lunch")
        self.assertIsNone(scheduler.seconds_until_wake())

        clock["now"] = 120.0 + 95 * 60
        wall["now"] = self.WORK_WALL + (120 + 95) * 60
        self.assertIsNone(scheduler.tick())
        self.assertTrue(scheduler.showing)

    def test_idle_break_starts_new_round_when_user_returns(self) -> None:
        clock = {"now": 0.0}
        idle = {"seconds": 0.0}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: idle["seconds"],
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: self.WORK_WALL,
        )
        scheduler.configure(RestReminderConfig(enabled=True, interval_minutes=1, idle_reset_minutes=1), force_reset=True)
        original_start = scheduler.cycle_started_at
        idle["seconds"] = 120.0
        clock["now"] = 30.0
        self.assertIsNone(scheduler.tick())
        self.assertEqual(scheduler.state, "away")
        idle["seconds"] = 0.0
        clock["now"] = 40.0
        self.assertIsNone(scheduler.tick())
        self.assertGreater(scheduler.cycle_started_at, original_start)
        self.assertEqual(scheduler.next_fire_at, 100.0)

    def test_cycle_start_can_be_adjusted_from_wall_clock(self) -> None:
        clock = {"now": 1000.0}
        wall = {"now": self.WORK_WALL + 3600}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        scheduler.configure(RestReminderConfig(enabled=True, interval_minutes=45), force_reset=True)
        scheduler.set_cycle_started_at_wall(wall["now"] - 600)
        self.assertEqual(scheduler.cycle_started_at, 400.0)
        self.assertEqual(scheduler.next_fire_at, 3100.0)

    def test_due_payload_contains_system_notification_result(self) -> None:
        clock = {"now": 0.0}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: self.WORK_WALL,
        )
        presenter = RestReminderPresenter(
            scheduler,
            wall_clock=lambda: 1_700_000_000.0,
            persist_enabled=False,
        )
        presenter.configure(RestReminderConfig(enabled=True, interval_minutes=1), force_reset=True)
        clock["now"] = 61.0
        original = rest_reminder_module._show_system_notification
        rest_reminder_module._show_system_notification = lambda _message: {
            "status": "sent",
            "channel": "test",
            "error": "",
            "lastSentAtMs": 123,
        }
        try:
            payload = presenter.tick()
        finally:
            rest_reminder_module._show_system_notification = original
        assert payload is not None
        self.assertEqual(payload["notification"]["status"], "sent")
        self.assertEqual(presenter.renderer_payload()["notification"]["channel"], "test")

    def test_test_notification_opens_preview_without_resetting_timer(self) -> None:
        clock = {"now": 1000.0}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: self.WORK_WALL,
        )
        presenter = RestReminderPresenter(
            scheduler,
            wall_clock=lambda: self.WORK_WALL,
            persist_enabled=False,
        )
        presenter.configure(
            RestReminderConfig(enabled=True, interval_minutes=45, postpone_minutes=10),
            force_reset=True,
        )
        next_before = scheduler.next_fire_at
        start_before = scheduler.cycle_started_at
        original = rest_reminder_module._show_system_notification
        rest_reminder_module._show_system_notification = lambda _message: {
            "status": "sent",
            "channel": "test",
            "error": "",
            "lastSentAtMs": 123,
        }
        try:
            result = presenter.test_notification()
        finally:
            rest_reminder_module._show_system_notification = original
        self.assertTrue(result["preview"])
        self.assertEqual(result["status"], "sent")
        payload = presenter.renderer_payload()
        self.assertTrue(payload["visible"])
        self.assertTrue(payload["preview"])
        self.assertIn("预览", str(payload["message"]))
        self.assertEqual(scheduler.next_fire_at, next_before)
        self.assertEqual(scheduler.cycle_started_at, start_before)

        presenter.acknowledge()
        after = presenter.renderer_payload()
        self.assertFalse(after["visible"])
        self.assertFalse(after.get("preview"))
        self.assertEqual(scheduler.next_fire_at, next_before)
        self.assertEqual(scheduler.cycle_started_at, start_before)

    def test_preview_auto_closes_without_resetting_timer(self) -> None:
        clock = {"now": 1000.0}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: self.WORK_WALL,
        )
        presenter = RestReminderPresenter(
            scheduler,
            wall_clock=lambda: self.WORK_WALL,
            persist_enabled=False,
        )
        presenter.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=45,
                break_minutes=1,
            ),
            force_reset=True,
        )
        next_before = scheduler.next_fire_at
        start_before = scheduler.cycle_started_at
        with patch.object(
            rest_reminder_module,
            "_show_system_notification",
            return_value={
                "status": "sent",
                "channel": "test",
                "error": "",
                "lastSentAtMs": 123,
            },
        ):
            presenter.test_notification()

        clock["now"] += 60.0
        completed = presenter.tick()
        assert completed is not None
        self.assertFalse(completed["visible"])
        self.assertFalse(completed["bubbleVisible"])
        self.assertTrue(completed["autoCompleted"])
        self.assertTrue(completed["preview"])
        self.assertEqual(scheduler.next_fire_at, next_before)
        self.assertEqual(scheduler.cycle_started_at, start_before)

    def test_preview_without_message_uses_configured_picker(self) -> None:
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "picked",
            clock=lambda: 0.0,
            wall_clock=lambda: self.WORK_WALL,
        )
        scheduler.configure(
            RestReminderConfig(enabled=True, break_minutes=1),
            force_reset=True,
        )

        event = scheduler.begin_preview()

        self.assertEqual(event.message, "picked")

    def test_preview_auto_closes_even_when_reminders_are_disabled(self) -> None:
        clock = {"now": 0.0}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            clock=lambda: clock["now"],
            wall_clock=lambda: self.WORK_WALL,
        )
        presenter = RestReminderPresenter(
            scheduler,
            wall_clock=lambda: self.WORK_WALL,
            persist_enabled=False,
        )
        with patch.object(
            rest_reminder_module,
            "_show_system_notification",
            return_value={
                "status": "sent",
                "channel": "test",
                "error": "",
                "lastSentAtMs": 123,
            },
        ):
            presenter.test_notification()

        self.assertEqual(presenter.seconds_until_wake(), 120.0)
        clock["now"] = 120.0
        completed = presenter.tick()
        assert completed is not None
        self.assertFalse(completed["visible"])
        self.assertFalse(completed["bubbleVisible"])
        self.assertTrue(completed["autoCompleted"])
        self.assertTrue(completed["preview"])
        self.assertFalse(scheduler.showing)

    def test_export_and_restore_wall_state_survives_restart(self) -> None:
        clock = {"now": 1000.0}
        wall = {"now": self.WORK_WALL}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=45,
                postpone_minutes=10,
                idle_reset_minutes=5,
            ),
            force_reset=True,
        )
        clock["now"] = 1600.0
        wall["now"] = self.WORK_WALL + 600.0
        snapshot = scheduler.export_wall_state()
        assert snapshot is not None
        self.assertEqual(snapshot["intervalMinutes"], 45)
        self.assertEqual(snapshot["breakMinutes"], 2)
        self.assertFalse(snapshot["postponeUsed"])

        restarted = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: 0.0,
            wall_clock=lambda: wall["now"],
        )
        restarted.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=45,
                postpone_minutes=10,
                idle_reset_minutes=5,
            ),
            force_reset=True,
        )
        self.assertTrue(restarted.restore_wall_state(snapshot))
        self.assertAlmostEqual(restarted.seconds_until_next() or 0.0, 45 * 60 - 600.0, places=3)
        self.assertAlmostEqual(restarted.cycle_started_at, -600.0, places=3)

    def test_daily_accounting_survives_timing_setting_changes(self) -> None:
        from codex_usage_hud.config import load_rest_reminder_state, save_rest_reminder_state

        clock = {"now": 0.0}
        wall = {"now": self.WORK_WALL}
        original_config = RestReminderConfig(
            enabled=True,
            interval_minutes=1,
            break_minutes=1,
            idle_reset_minutes=0,
            work_start_time="09:00",
            work_end_time="18:00",
            lunch_enabled=False,
        )
        original = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        original.configure(original_config, force_reset=True)
        clock["now"] = 61.0
        wall["now"] += 61.0
        self.assertIsNotNone(original.tick())
        self.assertTrue(original.start_rest())
        clock["now"] += 30.0
        wall["now"] += 30.0
        self.assertTrue(original.finish_rest())
        snapshot = original.export_wall_state()
        assert snapshot is not None
        self.assertEqual(snapshot["dailyRestedSeconds"], 30)
        self.assertEqual(snapshot["dailyRestedCount"], 1)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            save_rest_reminder_state(snapshot, path)
            changed_config = RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=5,
                idle_reset_minutes=0,
                work_start_time="09:00",
                work_end_time="21:00",
                lunch_enabled=True,
                lunch_start_time="12:00",
                lunch_end_time="13:30",
            )
            restarted_clock = {"now": 100.0}
            restarted = RestReminderScheduler(
                idle_seconds_provider=lambda: 0.0,
                clock=lambda: restarted_clock["now"],
                wall_clock=lambda: wall["now"],
            )
            presenter = RestReminderPresenter(
                restarted,
                wall_clock=lambda: wall["now"],
                state_path=path,
            )
            presenter.configure(
                changed_config,
                force_reset=True,
                restore_persisted=True,
            )

            payload = presenter.renderer_payload()
            self.assertEqual(payload["todayRestedSeconds"], 30)
            self.assertEqual(payload["todayRestedCount"], 1)
            saved = load_rest_reminder_state(path)
            self.assertEqual(saved["dailyRestedSeconds"], 30)
            self.assertEqual(saved["dailyRestedCount"], 1)
            self.assertEqual(saved["breakMinutes"], 5)

    def test_active_rest_snapshot_restores_remaining_rest_after_restart(self) -> None:
        clock = {"now": 0.0}
        wall = {"now": self.WORK_WALL}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=1,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )
        clock["now"] = 61.0
        wall["now"] += 61.0
        self.assertIsNotNone(scheduler.tick())
        self.assertTrue(scheduler.start_rest())
        snapshot = scheduler.export_wall_state()
        assert snapshot is not None
        self.assertEqual(snapshot["phase"], "resting")

        wall["now"] += 20.0
        restarted_clock = {"now": 10.0}
        restarted = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            clock=lambda: restarted_clock["now"],
            wall_clock=lambda: wall["now"],
        )
        restarted.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=1,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )

        self.assertTrue(restarted.restore_wall_state(snapshot))
        self.assertFalse(restarted.showing)
        self.assertTrue(restarted.resting)
        self.assertAlmostEqual(restarted.seconds_until_break_end() or 0.0, 40.0)
        self.assertIsNone(restarted.tick())

    def test_active_prompt_snapshot_restores_unbounded_waiting_choice(self) -> None:
        clock = {"now": 0.0}
        wall = {"now": self.WORK_WALL}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        config = RestReminderConfig(
            enabled=True,
            interval_minutes=1,
            break_minutes=1,
            idle_reset_minutes=0,
        )
        scheduler.configure(config, force_reset=True)
        clock["now"] = 61.0
        wall["now"] += 61.0
        self.assertIsNotNone(scheduler.tick())
        snapshot = scheduler.export_wall_state()
        assert snapshot is not None
        self.assertEqual(snapshot["phase"], "prompt")
        self.assertEqual(snapshot["promptEndsAtMs"], 0)
        self.assertEqual(snapshot["promptStartedAtMs"], int(wall["now"] * 1000))
        self.assertTrue(snapshot["promptWaitInfinite"])

        wall["now"] += 20.0
        restarted = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            clock=lambda: 10.0,
            wall_clock=lambda: wall["now"],
        )
        restarted.configure(config, force_reset=True)
        self.assertTrue(restarted.restore_wall_state(snapshot))
        self.assertTrue(restarted.showing)
        self.assertEqual(restarted.phase, "prompt")
        self.assertIsNone(restarted.seconds_until_prompt_end())
        self.assertIsNone(restarted.seconds_until_wake())

    def test_expired_focus_snapshot_fires_on_first_tick_after_restart(self) -> None:
        clock = {"now": 0.0}
        wall = {"now": self.WORK_WALL}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=1,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )
        snapshot = scheduler.export_wall_state()
        assert snapshot is not None
        wall["now"] += 120.0

        restarted = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            clock=lambda: 5.0,
            wall_clock=lambda: wall["now"],
        )
        restarted.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=1,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )

        self.assertTrue(restarted.restore_wall_state(snapshot))
        self.assertEqual(restarted.next_fire_at, 5.0)
        self.assertIsNotNone(restarted.tick())
        self.assertTrue(restarted.showing)

    def test_lunch_wait_snapshot_resumes_round_from_lunch_end_after_restart(self) -> None:
        clock = {"now": 0.0}
        lunch_start = datetime.fromtimestamp(self.WORK_WALL).replace(
            hour=12, minute=30, second=0, microsecond=0
        ).timestamp()
        wall = {"now": lunch_start}
        config = RestReminderConfig(
            enabled=True,
            interval_minutes=45,
            idle_reset_minutes=0,
            work_start_time="09:00",
            work_end_time="18:00",
            lunch_enabled=True,
            lunch_start_time="12:00",
            lunch_end_time="13:30",
        )
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            clock=lambda: clock["now"],
            wall_clock=lambda: wall["now"],
        )
        scheduler.configure(config, force_reset=True)
        self.assertIsNone(scheduler.tick())
        snapshot = scheduler.export_wall_state()
        assert snapshot is not None
        self.assertTrue(snapshot["scheduleWaiting"])

        wall["now"] += 90 * 60
        restarted_clock = {"now": 5.0}
        restarted = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            clock=lambda: restarted_clock["now"],
            wall_clock=lambda: wall["now"],
        )
        restarted.configure(config, force_reset=True)

        self.assertTrue(restarted.restore_wall_state(snapshot))
        self.assertAlmostEqual(restarted.seconds_until_next() or 0.0, 15 * 60)
        restarted_clock["now"] += 15 * 60
        wall["now"] += 15 * 60
        self.assertIsNotNone(restarted.tick())
        self.assertTrue(restarted.showing)

    def test_windows_notification_requests_max_notifyicon_lifetime(self) -> None:
        with patch.object(rest_reminder_module.sys, "platform", "win32"), patch.object(
            rest_reminder_module.subprocess,
            "Popen",
        ) as popen:
            result = rest_reminder_module._show_system_notification("起来活动一下")

        self.assertEqual(result["status"], "sent")
        command = popen.call_args.args[0]
        script = command[-1]
        self.assertIn("ShowBalloonTip(30000", script)
        self.assertIn("Start-Sleep -Seconds 30", script)
        self.assertIn("SystemSounds]::Asterisk.Play()", script)

    def test_presenter_persists_cycle_to_settings_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            store = UserConfigStore(path)
            store.save(
                UserConfig.from_dict(
                    {
                        "rest_reminder_enabled": True,
                        "rest_reminder_interval_minutes": 45,
                        "rest_reminder_postpone_minutes": 10,
                        "rest_reminder_idle_reset_minutes": 5,
                    }
                )
            )
            clock = {"now": 1000.0}
            wall = {"now": self.WORK_WALL}
            scheduler = RestReminderScheduler(
                idle_seconds_provider=lambda: 0.0,
                message_picker=lambda: "rest",
                clock=lambda: clock["now"],
                wall_clock=lambda: wall["now"],
            )
            presenter = RestReminderPresenter(
                scheduler,
                wall_clock=lambda: wall["now"],
                state_path=path,
            )
            presenter.configure(
                RestReminderConfig(
                    enabled=True,
                    interval_minutes=45,
                    postpone_minutes=10,
                    idle_reset_minutes=5,
                ),
                force_reset=True,
            )
            clock["now"] = 1300.0
            wall["now"] = self.WORK_WALL + 300.0
            presenter.tick()

            from codex_usage_hud.config import load_rest_reminder_state

            saved = load_rest_reminder_state(path)
            self.assertTrue(saved)
            self.assertEqual(saved["intervalMinutes"], 45)

            clock2 = {"now": 50.0}
            wall2 = {"now": self.WORK_WALL + 300.0}
            scheduler2 = RestReminderScheduler(
                idle_seconds_provider=lambda: 0.0,
                clock=lambda: clock2["now"],
                wall_clock=lambda: wall2["now"],
            )
            presenter2 = RestReminderPresenter(
                scheduler2,
                wall_clock=lambda: wall2["now"],
                state_path=path,
            )
            presenter2.configure(
                RestReminderConfig(
                    enabled=True,
                    interval_minutes=45,
                    postpone_minutes=10,
                    idle_reset_minutes=5,
                ),
                force_reset=True,
                restore_persisted=True,
            )
            remaining = presenter2.scheduler.seconds_until_next() or 0.0
            self.assertAlmostEqual(remaining, 45 * 60 - 300.0, places=1)

    def test_presenter_credit_early_rest_persists_daily_accounting(self) -> None:
        from codex_usage_hud.config import load_rest_reminder_state

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            clock = {"now": 0.0}
            wall = {"now": self.WORK_WALL}
            scheduler = RestReminderScheduler(
                idle_seconds_provider=lambda: 0.0,
                message_picker=lambda: "rest",
                clock=lambda: clock["now"],
                wall_clock=lambda: wall["now"],
            )
            presenter = RestReminderPresenter(
                scheduler,
                wall_clock=lambda: wall["now"],
                state_path=path,
            )
            config = RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=2,
                idle_reset_minutes=0,
            )
            presenter.configure(config, force_reset=True)
            clock["now"] = 61.0
            wall["now"] += 61.0
            self.assertIsNotNone(presenter.tick())
            self.assertTrue(presenter.credit_early_rest(5))

            payload = presenter.renderer_payload()
            self.assertEqual(payload["todayRestedSeconds"], 300)
            self.assertEqual(payload["todayRestedCount"], 1)
            saved = load_rest_reminder_state(path)
            self.assertEqual(saved["dailyRestedSeconds"], 300)
            self.assertEqual(saved["dailyRestedCount"], 1)
            self.assertEqual(saved["phase"], "focus")

    def test_presenter_keeps_expired_snapshot_due_until_first_tick(self) -> None:
        from codex_usage_hud.config import (
            load_rest_reminder_state,
            save_rest_reminder_state,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            wall = {"now": self.WORK_WALL}
            original = RestReminderScheduler(
                idle_seconds_provider=lambda: 0.0,
                clock=lambda: 0.0,
                wall_clock=lambda: wall["now"],
            )
            config = RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                break_minutes=1,
                idle_reset_minutes=0,
            )
            original.configure(config, force_reset=True)
            snapshot = original.export_wall_state()
            assert snapshot is not None
            save_rest_reminder_state(snapshot, path)
            wall["now"] += 120.0

            restarted = RestReminderScheduler(
                idle_seconds_provider=lambda: 0.0,
                clock=lambda: 5.0,
                wall_clock=lambda: wall["now"],
            )
            presenter = RestReminderPresenter(
                restarted,
                wall_clock=lambda: wall["now"],
                state_path=path,
            )
            presenter.configure(
                config,
                force_reset=True,
                restore_persisted=True,
            )

            saved = load_rest_reminder_state(path)
            self.assertEqual(saved["phase"], "focus")
            self.assertLessEqual(saved["nextFireAtMs"], int(wall["now"] * 1000))
            self.assertFalse(restarted.showing)
            due = presenter.tick()
            assert due is not None
            self.assertTrue(due["due"])
            self.assertTrue(restarted.showing)


if __name__ == "__main__":
    unittest.main()
