"""Unit tests for active-session tracking and session resolution."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.platforms.active_session import (
    ActiveSessionTracker,
    RealtimeSessionWatcher,
    SessionPathResolver,
    _title_matches,
    _title_prefix,
)
from codex_usage_hud.platforms.base import BasePlatform


def _write_thread_mapping(
    state_db: Path,
    thread_id: str,
    rollout_path: Path,
    *,
    title: str = "",
) -> None:
    con = sqlite3.connect(state_db)
    try:
        con.execute(
            "create table threads ("
            "id text primary key, "
            "rollout_path text not null, "
            "title text not null default '', "
            "archived integer not null default 0, "
            "updated_at_ms integer, "
            "updated_at integer)"
        )
        con.execute(
            "insert into threads (id, rollout_path, title, archived, updated_at_ms, updated_at) "
            "values (?, ?, ?, 0, 10, 10)",
            (thread_id, "\\\\?\\" + str(rollout_path), title),
        )
        con.commit()
    finally:
        con.close()


class FakePlatform(BasePlatform):
    def __init__(self, latest_session: Path | None = None) -> None:
        self.latest_session = latest_session
        self.detect_calls = 0

    def get_codex_data_dir(self) -> Path:
        return Path.home() / ".codex"

    def detect_active_session(self, sessions_root: Path) -> Path | None:
        del sessions_root
        self.detect_calls += 1
        return self.latest_session


class FakeInProcessTitlePlatform(FakePlatform):
    def __init__(self, titles: list[str]) -> None:
        super().__init__()
        self.titles = titles
        self.command_requested = False

    def supports_active_title_polling(self) -> bool:
        return True

    def get_active_conversation_title(self) -> str | None:
        if not self.titles:
            return None
        if len(self.titles) == 1:
            return self.titles[0]
        return self.titles.pop(0)

    def build_active_title_command(self, poll_ms: int) -> list[str] | None:
        del poll_ms
        self.command_requested = True
        return ["unexpected-powershell-fallback"]


class FakeCdpRefPlatform(FakePlatform):
    def __init__(self, session_id: str, title: str) -> None:
        super().__init__()
        self.session_id = session_id
        self.title = title
        self.ref_calls = 0

    def get_active_conversation_ref(self) -> tuple[str, str] | None:
        self.ref_calls += 1
        return self.session_id, self.title


class FakeEventTitlePlatform(FakeInProcessTitlePlatform):
    def __init__(self, event_titles: list[str], poll_titles: list[str] | None = None) -> None:
        super().__init__(poll_titles or [])
        self.event_titles = event_titles
        self.events_requested = False

    def supports_active_title_events(self) -> bool:
        return True

    def watch_active_conversation_title(self, stop_event, on_title) -> bool:
        self.events_requested = True
        for title in self.event_titles:
            if stop_event.is_set():
                break
            on_title(title)
        return True


class FakeFailingEventTitlePlatform(FakeInProcessTitlePlatform):
    def __init__(self, poll_titles: list[str]) -> None:
        super().__init__(poll_titles)
        self.events_requested = False

    def supports_active_title_events(self) -> bool:
        return True

    def watch_active_conversation_title(self, stop_event, on_title) -> bool:
        del stop_event, on_title
        self.events_requested = True
        return False


class _TrackerStub:
    def __init__(
        self,
        path: Path | None,
        source: str = "ui:selected",
        enabled: bool = True,
    ) -> None:
        self._path = path
        self.latest_source = source
        self.enabled = enabled

    def current_path(self) -> Path | None:
        return self._path


class _TrackerSequenceStub:
    def __init__(
        self,
        paths: list[Path | None],
        source: str = "ui:selected",
        enabled: bool = True,
    ) -> None:
        self._paths = paths
        self.latest_source = source
        self.enabled = enabled

    def current_path(self) -> Path | None:
        if not self._paths:
            return None
        if len(self._paths) == 1:
            return self._paths[0]
        return self._paths.pop(0)


class ActiveSessionTrackerTests(unittest.TestCase):
    def test_realtime_watcher_falls_back_to_polling_when_events_fail(self) -> None:
        platform = FakeFailingEventTitlePlatform(["Selected Thread"])
        received: list[tuple[str, str, float]] = []
        watcher = RealtimeSessionWatcher(
            platform,
            250,
            lambda title, source, detected_at: received.append(
                (title, source, detected_at)
            ),
        )

        try:
            self.assertTrue(watcher.start())
            deadline = time.time() + 1.0
            while time.time() < deadline and not received:
                time.sleep(0.02)
        finally:
            watcher.close()

        self.assertTrue(platform.events_requested)
        self.assertEqual(received[0][0], "Selected Thread")
        self.assertEqual(received[0][1], "poll")
        self.assertGreater(received[0][2], 0)

    def test_realtime_watcher_does_not_poll_over_realtime_event(self) -> None:
        platform = FakeEventTitlePlatform(
            ["Event Selected Thread"],
            poll_titles=["Poll Should Not Override"],
        )
        received: list[tuple[str, str, float]] = []
        watcher = RealtimeSessionWatcher(
            platform,
            250,
            lambda title, source, detected_at: received.append(
                (title, source, detected_at)
            ),
        )

        try:
            self.assertTrue(watcher.start())
            time.sleep(0.6)
        finally:
            watcher.close()

        self.assertEqual([item[0] for item in received], ["Event Selected Thread"])
        self.assertEqual(received[0][1], "event")

    def test_realtime_watcher_backstop_polls_after_event_gap(self) -> None:
        platform = FakeEventTitlePlatform(
            ["Event Selected Thread"],
            poll_titles=["Polled New Thread"],
        )
        received: list[tuple[str, str, float]] = []
        watcher = RealtimeSessionWatcher(
            platform,
            250,
            lambda title, source, detected_at: received.append(
                (title, source, detected_at)
            ),
        )

        try:
            self.assertTrue(watcher.start())
            deadline = time.time() + 2.0
            while time.time() < deadline and len(received) < 2:
                time.sleep(0.02)
        finally:
            watcher.close()

        self.assertEqual(
            [item[:2] for item in received[:2]],
            [
                ("Event Selected Thread", "event"),
                ("Polled New Thread", "poll-backstop"),
            ],
        )

    def test_path_from_session_index_resolves_thread_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-abc-session-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")

            session_index = root / "session_index.jsonl"
            session_index.write_text(
                '{"id":"abc-session-123","thread_name":"Selected Thread","updated_at":"2026-05-28T00:00:00Z"}\n',
                encoding="utf-8",
            )

            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=500,
                enabled=False,
            )

            self.assertEqual(
                tracker.path_from_session_index("Selected Thread"),
                session_path,
            )
            self.assertEqual(
                tracker.path_from_session_index("Selected Thread extra"),
                session_path,
            )

    def test_path_from_session_index_resolves_elided_long_title_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-long-title-session.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            long_title = (
                "查看首扫页/批扫扫描时图片落盘（D:\\ScanSystemData\\2026-06-23）"
                "时文件的DPI参数，我实测了更多内容"
            )

            session_index = root / "session_index.jsonl"
            session_index.write_text(
                json.dumps(
                    {"id": "long-title-session", "thread_name": long_title},
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=500,
                enabled=False,
            )

            self.assertEqual(
                tracker.path_from_session_index("查看首扫页/批扫扫描时图片落盘…"),
                session_path,
            )
            self.assertIsNone(tracker.path_from_session_index("初始…"))

    def test_path_for_title_falls_back_to_state_db_threads_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-from-state-db.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")

            state_db = root / "state_5.sqlite"
            con = sqlite3.connect(state_db)
            try:
                con.execute(
                    "create table threads ("
                    "id text primary key, "
                    "rollout_path text not null, "
                    "title text not null, "
                    "archived integer not null default 0, "
                    "updated_at_ms integer, "
                    "updated_at integer)"
                )
                con.execute(
                    "insert into threads (id, rollout_path, title, archived, updated_at_ms, updated_at) "
                    "values (?, ?, ?, 0, 10, 10)",
                    (
                        "thread-1",
                        "\\\\?\\" + str(session_path),
                        "Visible Conversation",
                    ),
                )
                con.commit()
            finally:
                con.close()

            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=500,
                enabled=False,
            )

            self.assertEqual(
                tracker.path_for_title("Visible Conversation"),
                session_path,
            )

    def test_path_for_title_keeps_markdown_variant_duplicates_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            paths = [sessions_root / f"rollout-{index}.jsonl" for index in (1, 2)]
            for path in paths:
                path.write_text("{}\n", encoding="utf-8")

            state_db = root / "state_5.sqlite"
            con = sqlite3.connect(state_db)
            try:
                con.execute(
                    "create table threads ("
                    "id text primary key, rollout_path text not null, "
                    "title text not null, archived integer not null default 0, "
                    "updated_at_ms integer, updated_at integer)"
                )
                con.executemany(
                    "insert into threads (id, rollout_path, title, archived, updated_at_ms, updated_at) "
                    "values (?, ?, ?, 0, 10, 10)",
                    [
                        ("thread-1", str(paths[0]), "Visible Conversation"),
                        ("thread-2", str(paths[1]), "# Visible Conversation\n\n## detail"),
                    ],
                )
                con.commit()
            finally:
                con.close()
            (root / "session_index.jsonl").write_text(
                json.dumps({"id": "thread-1", "thread_name": "Visible Conversation"})
                + "\n",
                encoding="utf-8",
            )

            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=500,
                enabled=False,
            )

            self.assertIsNone(tracker.path_for_title("Visible Conversation"))

    def test_path_for_title_matches_heading_variant_after_line_break(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-heading-variant.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            state_db = root / "state_5.sqlite"
            con = sqlite3.connect(state_db)
            try:
                con.execute(
                    "create table threads ("
                    "id text primary key, rollout_path text not null, "
                    "title text not null, archived integer not null default 0, "
                    "updated_at_ms integer, updated_at integer)"
                )
                con.execute(
                    "insert into threads (id, rollout_path, title, archived, updated_at_ms, updated_at) "
                    "values (?, ?, ?, 0, 10, 10)",
                    (
                        "thread-heading-variant",
                        str(session_path),
                        "# Foo\n\n## detail",
                    ),
                )
                con.commit()
            finally:
                con.close()

            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=500,
                enabled=False,
            )

            self.assertEqual(tracker.path_for_title("Foo detail"), session_path)

    def test_title_prefix_only_strips_markdown_markers_at_line_starts(self) -> None:
        self.assertEqual(_title_prefix("# Heading\n\n## Detail"), "Heading Detail")
        self.assertEqual(_title_prefix("A # B"), "A # B")
        self.assertFalse(_title_matches("Fix # issue", "Fix issue"))

    def test_current_path_prefers_cdp_session_id_over_title_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-cdp-thread-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            session_index = root / "session_index.jsonl"
            session_index.write_text(
                "\n".join(
                    [
                        '{"id":"other-thread","thread_name":"Duplicate Title","updated_at":"2026-05-28T00:00:00Z"}',
                        '{"id":"cdp-thread-123","thread_name":"Resolved CDP Title","updated_at":"2026-05-29T00:00:00Z"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            tracker = ActiveSessionTracker(
                platform=FakeCdpRefPlatform("cdp-thread-123", "Duplicate Title"),
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=500,
                enabled=True,
            )

            self.assertEqual(tracker.current_path(), session_path)
            self.assertEqual(tracker.latest_title, "Resolved CDP Title")
            self.assertEqual(tracker.latest_source, "cdp:Resolved CDP Title")

    def test_path_from_thread_id_reuses_cached_rollout_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "rollout-thread-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=root / "state_5.sqlite",
                sessions_root=root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=500,
                enabled=False,
            )

            with patch(
                "codex_usage_hud.platforms.active_session.find_session_file",
                return_value=session_path,
            ) as finder:
                self.assertEqual(tracker.path_from_thread_id("thread-123"), session_path)
                self.assertEqual(tracker.path_from_thread_id("thread-123"), session_path)

        finder.assert_called_once_with("thread-123", root)

    def test_path_from_thread_id_prefers_state_db_before_file_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-thread-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")

            state_db = root / "state_5.sqlite"
            con = sqlite3.connect(state_db)
            try:
                con.execute(
                    "create table threads ("
                    "id text primary key, "
                    "rollout_path text not null, "
                    "archived integer not null default 0, "
                    "updated_at_ms integer, "
                    "updated_at integer)"
                )
                con.execute(
                    "insert into threads (id, rollout_path, archived, updated_at_ms, updated_at) "
                    "values (?, ?, 0, 10, 10)",
                    ("thread-123", "\\\\?\\" + str(session_path)),
                )
                con.commit()
            finally:
                con.close()

            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=500,
                enabled=False,
            )

            with patch(
                "codex_usage_hud.platforms.active_session.find_session_file",
                side_effect=AssertionError("recursive file scan should not run"),
            ) as finder:
                self.assertEqual(tracker.path_from_thread_id("thread-123"), session_path)
                tracker.invalidate_mapping_cache()
                self.assertEqual(tracker.path_from_thread_id("local:thread-123"), session_path)

        finder.assert_not_called()

    def test_start_uses_in_process_title_polling_without_command_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            platform = FakeInProcessTitlePlatform(["", "Selected Thread"])
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=root / "state_5.sqlite",
                sessions_root=root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
            )

            try:
                tracker.start()
                deadline = time.time() + 1.0
                title = ""
                while time.time() < deadline:
                    with tracker._lock:
                        title = tracker.latest_title
                    if title:
                        break
                    time.sleep(0.02)
            finally:
                tracker.close()

            self.assertEqual(title, "Selected Thread")
            self.assertFalse(platform.command_requested)
            self.assertIsNone(tracker._proc)

    def test_start_can_skip_background_watcher_for_renderer_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "rollout-thread-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            state_db = root / "state_5.sqlite"
            _write_thread_mapping(
                state_db,
                "thread-123",
                session_path,
                title="Renderer Selected Thread",
            )
            platform = FakeInProcessTitlePlatform(["Selected Thread"])
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=state_db,
                sessions_root=root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )

            tracker.start()

            self.assertIsNone(tracker._watcher)
            self.assertIsNone(tracker._thread)
            self.assertFalse(platform.command_requested)
            self.assertTrue(
                tracker.observe_conversation_ref(
                    "thread-123",
                    "Renderer Selected Thread",
                )
            )
            self.assertEqual(tracker.current_path(), session_path)
            self.assertEqual(tracker.latest_source, "renderer:Renderer Selected Thread")

    def test_renderer_collapsed_title_tracks_same_session_and_clears_on_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=root / "state_5.sqlite",
                sessions_root=root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )

            self.assertTrue(
                tracker.observe_conversation_ref(
                    "thread-a",
                    "Thread A",
                    selection_seq=1,
                    collapsed_title="已处理 44s",
                )
            )
            self.assertEqual(tracker.renderer_collapsed_title, "已处理 44s")

            self.assertTrue(
                tracker.observe_conversation_ref(
                    "thread-a",
                    "Thread A",
                    selection_seq=1,
                    collapsed_title="已处理 45s",
                )
            )
            self.assertEqual(tracker.renderer_collapsed_title, "已处理 45s")

            self.assertTrue(
                tracker.observe_conversation_ref(
                    "thread-a",
                    "Thread A",
                    selection_seq=1,
                    collapsed_title="",
                )
            )
            self.assertEqual(tracker.renderer_collapsed_title, "")

            self.assertTrue(
                tracker.observe_conversation_ref(
                    source="renderer",
                    new_session=True,
                    selection_seq=2,
                )
            )
            self.assertEqual(tracker.renderer_collapsed_title, "")

    def test_start_prefers_event_stream_over_polling(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-abc-session-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            session_index = root / "session_index.jsonl"
            session_index.write_text(
                '{"id":"abc-session-123","thread_name":"Selected Thread","updated_at":"2026-05-28T00:00:00Z"}\n',
                encoding="utf-8",
            )
            platform = FakeEventTitlePlatform(
                ["Selected Thread"],
                poll_titles=["Poll Fallback Title"],
            )
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=250,
                enabled=True,
            )

            try:
                tracker.start()
                deadline = time.time() + 1.0
                path = None
                while time.time() < deadline:
                    path = tracker.current_path()
                    if path is not None:
                        break
                    time.sleep(0.02)
            finally:
                tracker.close()

            self.assertTrue(platform.events_requested)
            self.assertFalse(platform.command_requested)
            self.assertEqual(path, session_path)
            self.assertEqual(tracker.latest_title, "Selected Thread")

    def test_event_stream_logs_switch_response_latency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-abc-session-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            session_index = root / "session_index.jsonl"
            session_index.write_text(
                '{"id":"abc-session-123","thread_name":"Selected Thread","updated_at":"2026-05-28T00:00:00Z"}\n',
                encoding="utf-8",
            )
            tracker = ActiveSessionTracker(
                platform=FakeEventTitlePlatform(["Selected Thread"]),
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=250,
                enabled=True,
            )

            try:
                with self.assertLogs(
                    "codex_usage_hud.active_session",
                    level="INFO",
                ) as logs:
                    tracker.start()
                    deadline = time.time() + 1.0
                    while time.time() < deadline:
                        if tracker.current_path() is not None:
                            break
                        time.sleep(0.02)
            finally:
                tracker.close()

            combined = "\n".join(logs.output)
            self.assertIn("ACTIVE_SESSION_SWITCH", combined)
            self.assertIn("matched=True", combined)
            self.assertRegex(combined, r"response_ms=\d+\.\d")
            self.assertGreaterEqual(tracker.latest_response_ms, 0.0)

    def test_title_for_session_prefers_live_title_for_active_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "selected.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=root / "state_5.sqlite",
                sessions_root=root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
            )

            tracker.latest_title = "Live Selected Thread"
            tracker.latest_path = session_path

            self.assertEqual(
                tracker.title_for_session(session_path, "session-1"),
                "Live Selected Thread",
            )

    def test_invalidate_mapping_cache_clears_title_and_thread_path_cache(self) -> None:
        tracker = ActiveSessionTracker(
            platform=FakePlatform(),
            state_db=Path("state_5.sqlite"),
            sessions_root=Path("sessions"),
            session_index_path=Path("session_index.jsonl"),
            poll_ms=250,
            enabled=True,
        )
        tracker._title_cache_key = ("thread-1", "path")
        tracker._title_cache_value = "Cached Title"
        tracker._thread_path_cache["thread-1"] = (None, 1.0)

        tracker.invalidate_mapping_cache()

        self.assertIsNone(tracker._title_cache_key)
        self.assertEqual(tracker._title_cache_value, "")
        self.assertEqual(tracker._thread_path_cache, {})

    def test_current_path_resolves_archived_session_from_cdp_thread_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            archived_root = root / "archived_sessions" / "2026" / "06"
            sessions_root.mkdir()
            archived_root.mkdir(parents=True)
            archived_session = archived_root / "session-archived-123.jsonl"
            archived_session.write_text("{}\n", encoding="utf-8")

            tracker = ActiveSessionTracker(
                platform=FakeCdpRefPlatform("archived-123", "Archived Thread"),
                state_db=root / "missing.sqlite",
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
            )

            self.assertEqual(tracker.current_path(), archived_session)
            self.assertEqual(tracker.latest_source, "cdp:Archived Thread")

    def test_renderer_observed_ref_bypasses_cdp_probe_in_current_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-renderer-thread-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            state_db = root / "state_5.sqlite"
            _write_thread_mapping(
                state_db,
                "renderer-thread-123",
                session_path,
                title="Renderer Selected Thread",
            )
            platform = FakeCdpRefPlatform("stale-cdp-thread", "Stale CDP Thread")
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
            )

            changed = tracker.observe_conversation_ref(
                "renderer-thread-123",
                "Renderer Selected Thread",
            )
            tracker.latest_event_source = "cdp"
            tracker.latest_source = "cdp:Stale CDP Thread"
            tracker.latest_title = "Stale CDP Thread"
            tracker.latest_session_id = "stale-cdp-thread"
            tracker.latest_path = None

            self.assertTrue(changed)
            self.assertEqual(tracker.current_path(), session_path)
            self.assertEqual(platform.ref_calls, 0)
            self.assertEqual(tracker.latest_source, "renderer:Renderer Selected Thread")

    def test_renderer_authoritative_tracker_skips_cdp_ref_without_renderer_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-cdp-thread-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            platform = FakeCdpRefPlatform("cdp-thread-123", "CDP Thread")
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )

            self.assertIsNone(tracker.current_path())
            self.assertEqual(platform.ref_calls, 0)
            self.assertEqual(tracker.latest_source, "renderer-waiting")

    def test_renderer_exact_uuid_mapping_never_uses_title_or_file_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-019f-real-thread.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            state_db = root / "state_5.sqlite"
            _write_thread_mapping(
                state_db,
                "019f-real-thread",
                session_path,
                title="Exact Renderer Thread",
            )
            platform = FakePlatform()
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )

            with (
                patch.object(
                    tracker,
                    "path_for_title",
                    side_effect=AssertionError("renderer must not title-map"),
                ),
                patch(
                    "codex_usage_hud.platforms.active_session.find_session_file",
                    side_effect=AssertionError("renderer must not scan session files"),
                ),
            ):
                self.assertTrue(
                    tracker.observe_conversation_ref(
                        "local:019f-real-thread",
                        "Exact Renderer Thread",
                    )
                )
                self.assertEqual(tracker.current_path(), session_path)

            self.assertEqual(platform.detect_calls, 0)
            self.assertEqual(tracker.latest_source, "renderer:Exact Renderer Thread")

    def test_renderer_provisional_client_new_thread_waits_without_activity_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            stale_path = sessions_root / "rollout-stale.jsonl"
            stale_path.write_text("{}\n", encoding="utf-8")
            platform = FakePlatform(latest_session=stale_path)
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )
            resolver = SessionPathResolver(
                platform,
                sessions_root,
                active_session_tracker=tracker,
            )

            self.assertTrue(
                tracker.observe_conversation_ref(
                    "local:client-new-thread:pending-uuid",
                    "Pending Renderer Thread",
                )
            )
            self.assertEqual(resolver.resolve(), (None, "renderer-pending-session"))
            self.assertEqual(platform.detect_calls, 0)
            self.assertEqual(tracker.latest_title, "Pending Renderer Thread")

    def test_filesystem_heal_returns_for_confirmed_renderer_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-2026-08-28T12-00-00-confirmed-id.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            state_db = root / "state_5.sqlite"
            _write_thread_mapping(
                state_db,
                "confirmed-id",
                session_path,
                title="Confirmed renderer thread",
            )
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )
            self.assertTrue(
                tracker.observe_conversation_ref(
                    "confirmed-id",
                    "Confirmed renderer thread",
                )
            )
            result: list[bool] = []
            worker = threading.Thread(
                target=lambda: result.append(tracker.heal_pending_from_filesystem()),
                daemon=True,
            )

            worker.start()
            worker.join(timeout=0.5)

            self.assertFalse(worker.is_alive())
            self.assertEqual(result, [False])

    def test_renderer_provisional_id_resolves_unique_persisted_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-019f-persisted.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            state_db = root / "state_5.sqlite"
            _write_thread_mapping(
                state_db,
                "019f-persisted",
                session_path,
                title="Persisted Provisional Thread",
            )
            session_index = root / "session_index.jsonl"
            session_index.write_text(
                json.dumps(
                    {
                        "id": "019f-persisted",
                        "thread_name": "Persisted Provisional Thread",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )

            self.assertTrue(
                tracker.observe_conversation_ref(
                    "local:client-new-thread:pending-uuid",
                    "Persisted Provisional Thread",
                )
            )
            self.assertEqual(tracker.latest_session_id, "019f-persisted")
            self.assertEqual(tracker.latest_path, session_path)
            self.assertEqual(
                tracker.latest_source,
                "renderer:Persisted Provisional Thread",
            )

    def test_renderer_provisional_id_uses_current_state_db_title_when_index_is_stale(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-019f-current.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            state_db = root / "state_5.sqlite"
            _write_thread_mapping(
                state_db,
                "019f-current",
                session_path,
                title="# Current title after the latest user message\n\n## detail",
            )
            # The index still has the title captured when the session was
            # first created; state_5.sqlite is already current.
            session_index = root / "session_index.jsonl"
            session_index.write_text(
                json.dumps(
                    {"id": "019f-current", "thread_name": "Initial title"}
                )
                + "\n",
                encoding="utf-8",
            )
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )

            self.assertTrue(
                tracker.observe_conversation_ref(
                    "local:client-new-thread:pending-uuid",
                    "Current title after the latest user message",
                )
            )

            self.assertEqual(tracker.latest_session_id, "019f-current")
            self.assertEqual(tracker.latest_path, session_path)
            self.assertEqual(tracker.follow_state, "confirmed")
            self.assertEqual(tracker.follow_reason, "confirmed")

    def test_renderer_provisional_id_reconciles_from_index_before_state_db_row(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            canonical_id = "019f-index-first"
            session_path = sessions_root / f"rollout-2026-08-27T20-30-48-{canonical_id}.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            session_index = root / "session_index.jsonl"
            session_index.write_text(
                json.dumps(
                    {"id": canonical_id, "thread_name": "Index First Thread"}
                )
                + "\n",
                encoding="utf-8",
            )
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )

            self.assertTrue(
                tracker.observe_conversation_ref(
                    "local:client-new-thread:pending-uuid",
                    "Index First Thread",
                )
            )

            self.assertEqual(tracker.latest_session_id, canonical_id)
            self.assertEqual(tracker.latest_path, session_path)
            self.assertEqual(tracker.follow_state, "confirmed")
            self.assertEqual(tracker.follow_reason, "confirmed")

    def test_renderer_provisional_id_reconciles_after_mapping_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            state_db = root / "state_5.sqlite"
            session_index = root / "session_index.jsonl"
            platform = FakePlatform()
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )
            resolver = SessionPathResolver(
                platform,
                sessions_root,
                active_session_tracker=tracker,
            )

            tracker.observe_conversation_ref(
                "",
                "Persisted Later",
                renderer_session_id="local:client-new-thread:pending-uuid",
                pending_session=True,
                selection_seq=7,
                observed_at_ms=1234,
            )
            self.assertEqual(resolver.resolve(), (None, "renderer-pending-session"))
            self.assertEqual(tracker.follow_reason, "awaiting-persistence")

            session_path = sessions_root / "rollout-canonical-id.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            _write_thread_mapping(
                state_db,
                "canonical-id",
                session_path,
                title="Persisted Later",
            )
            session_index.write_text(
                json.dumps(
                    {"id": "canonical-id", "thread_name": "Persisted Later"}
                )
                + "\n",
                encoding="utf-8",
            )

            tracker.invalidate_mapping_cache()

            self.assertEqual(
                resolver.resolve(),
                (session_path, "renderer:Persisted Later"),
            )
            self.assertEqual(tracker.selection_seq, 7)
            self.assertEqual(tracker.selection_observed_at_ms, 1234)
            self.assertEqual(tracker.follow_state, "confirmed")
            self.assertEqual(tracker.follow_reason, "confirmed")
            self.assertGreater(tracker.selection_received_at_ms, 0)
            self.assertGreaterEqual(
                tracker.selection_resolved_at_ms,
                tracker.selection_received_at_ms,
            )

    def test_renderer_provisional_duplicate_title_stays_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            state_db = root / "state_5.sqlite"
            session_index = root / "session_index.jsonl"
            paths = [sessions_root / f"rollout-{index}.jsonl" for index in (1, 2)]
            for path in paths:
                path.write_text("{}\n", encoding="utf-8")
            con = sqlite3.connect(state_db)
            try:
                con.execute(
                    "create table threads (id text primary key, rollout_path text, title text)"
                )
                con.executemany(
                    "insert into threads values (?, ?, ?)",
                    [
                        ("thread-1", str(paths[0]), "Duplicate Title"),
                        ("thread-2", str(paths[1]), "Duplicate Title"),
                    ],
                )
                con.commit()
            finally:
                con.close()
            session_index.write_text(
                "\n".join(
                    json.dumps({"id": thread_id, "thread_name": "Duplicate Title"})
                    for thread_id in ("thread-1", "thread-2")
                )
                + "\n",
                encoding="utf-8",
            )
            platform = FakePlatform()
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )
            resolver = SessionPathResolver(
                platform,
                sessions_root,
                active_session_tracker=tracker,
            )

            tracker.observe_conversation_ref(
                "",
                "Duplicate Title",
                renderer_session_id="local:client-new-thread:pending-uuid",
                pending_session=True,
                selection_seq=3,
            )

            self.assertEqual(resolver.resolve(), (None, "renderer-pending-session"))
            self.assertEqual(tracker.follow_reason, "ambiguous-persisted-identity")
            self.assertEqual(
                [item["sessionId"] for item in tracker.match_candidates],
                ["thread-1", "thread-2"],
            )
            self.assertEqual(platform.detect_calls, 0)

            self.assertTrue(
                tracker.resolve_renderer_candidate("thread-2", selection_seq=3)
            )
            self.assertEqual(resolver.resolve(), (paths[1], "renderer:Duplicate Title"))
            self.assertEqual(tracker.latest_session_id, "thread-2")
            self.assertEqual(tracker.follow_reason, "confirmed")
            self.assertEqual(tracker.match_candidates, [])
            tracker.observe_conversation_ref(
                "",
                "Duplicate Title",
                renderer_session_id="local:client-new-thread:pending-uuid",
                pending_session=True,
                selection_seq=3,
            )
            self.assertEqual(tracker.latest_session_id, "thread-2")
            self.assertEqual(tracker.latest_path, paths[1])
            self.assertEqual(tracker.follow_state, "confirmed")

    def test_renderer_provisional_title_ignores_archived_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            archived_root = root / "archived_sessions" / "2026" / "08"
            sessions_root.mkdir()
            archived_root.mkdir(parents=True)
            active_path = sessions_root / "rollout-active.jsonl"
            archived_path = archived_root / "rollout-archived.jsonl"
            active_path.write_text("{}\n", encoding="utf-8")
            archived_path.write_text("{}\n", encoding="utf-8")
            state_db = root / "state_5.sqlite"
            con = sqlite3.connect(state_db)
            try:
                con.execute(
                    "create table threads ("
                    "id text primary key, rollout_path text, title text, "
                    "archived integer, updated_at_ms integer, updated_at integer)"
                )
                con.executemany(
                    "insert into threads values (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            "active-thread",
                            str(active_path),
                            "Duplicate Title",
                            0,
                            20,
                            20,
                        ),
                        (
                            "archived-thread",
                            str(archived_path),
                            "Duplicate Title",
                            1,
                            30,
                            30,
                        ),
                    ],
                )
                con.commit()
            finally:
                con.close()
            (root / "session_index.jsonl").write_text(
                "\n".join(
                    json.dumps({"id": thread_id, "thread_name": "Duplicate Title"})
                    for thread_id in ("active-thread", "archived-thread")
                )
                + "\n",
                encoding="utf-8",
            )
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )

            tracker.observe_conversation_ref(
                "",
                "Duplicate Title",
                renderer_session_id="local:client-new-thread:pending-uuid",
                pending_session=True,
                selection_seq=4,
            )

            self.assertEqual(tracker.latest_session_id, "active-thread")
            self.assertEqual(tracker.latest_path, active_path)
            self.assertEqual(tracker.follow_reason, "confirmed")
            self.assertEqual(tracker.match_candidates, [])
            self.assertEqual(tracker.path_for_title("Duplicate Title"), active_path)

    def test_renderer_ignores_stale_selection_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-newer.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            state_db = root / "state_5.sqlite"
            _write_thread_mapping(state_db, "newer", session_path, title="Newer")
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )

            self.assertTrue(
                tracker.observe_conversation_ref("newer", "Newer", selection_seq=2)
            )
            self.assertFalse(
                tracker.observe_conversation_ref(
                    "older",
                    "Older",
                    selection_seq=1,
                )
            )
            self.assertEqual(tracker.latest_session_id, "newer")
            self.assertEqual(tracker.selection_seq, 2)

    def test_renderer_channel_disconnect_preserves_selection_and_reports_reason(self) -> None:
        tracker = ActiveSessionTracker(
            platform=FakePlatform(),
            state_db=Path("state_5.sqlite"),
            sessions_root=Path("sessions"),
            session_index_path=Path("session_index.jsonl"),
            poll_ms=250,
            enabled=True,
            start_background_watcher=False,
        )
        tracker.latest_session_id = "thread-current"
        tracker.latest_title = "Current"

        self.assertTrue(tracker.mark_renderer_channel_unavailable("binding closed"))
        self.assertEqual(tracker.latest_session_id, "thread-current")
        self.assertEqual(tracker.latest_title, "Current")
        self.assertEqual(tracker.follow_state, "pending")
        self.assertEqual(tracker.follow_reason, "renderer-channel-unavailable")
        self.assertEqual(tracker.latest_event_source, "binding closed")

    def test_renderer_unmapped_uuid_stays_pending_without_activity_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            stale_path = sessions_root / "rollout-stale.jsonl"
            stale_path.write_text("{}\n", encoding="utf-8")
            platform = FakePlatform(latest_session=stale_path)
            state_db = root / "state_5.sqlite"
            _write_thread_mapping(
                state_db,
                "different-thread",
                stale_path,
                title="Different Thread",
            )
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )
            resolver = SessionPathResolver(
                platform,
                sessions_root,
                active_session_tracker=tracker,
            )

            self.assertTrue(
                tracker.observe_conversation_ref(
                    "local:unmapped-thread",
                    "Unmapped Renderer Thread",
                )
            )
            self.assertEqual(resolver.resolve(), (None, "renderer-pending-map"))
            self.assertEqual(platform.detect_calls, 0)

    def test_renderer_new_session_clears_previous_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            stale_path = sessions_root / "rollout-stale-thread.jsonl"
            stale_path.write_text("{}\n", encoding="utf-8")
            platform = FakeCdpRefPlatform("stale-thread", "Stale CDP Thread")
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
            )
            tracker.latest_title = "Stale Thread"
            tracker.latest_session_id = "stale-thread"
            tracker.latest_path = stale_path
            tracker._mapped_title = "Stale Thread"
            tracker._renderer_session_id = "stale-thread"
            tracker._renderer_title = "Stale Thread"
            tracker._renderer_path = stale_path

            changed = tracker.observe_conversation_ref(
                "",
                "",
                source="renderer",
                new_session=True,
            )

            self.assertTrue(changed)
            self.assertIsNone(tracker.current_path())
            self.assertEqual(platform.ref_calls, 0)
            self.assertEqual(tracker.latest_title, "")
            self.assertEqual(tracker.latest_session_id, "")
            self.assertIsNone(tracker.latest_path)
            self.assertEqual(tracker.latest_source, "renderer-new-session")

    def test_renderer_new_session_retains_draft_until_session_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )

            self.assertTrue(
                tracker.observe_conversation_ref(
                    source="renderer",
                    new_session=True,
                    selection_seq=1,
                    draft_text="Draft text",
                )
            )
            self.assertEqual(tracker.renderer_draft_text, "Draft text")
            self.assertFalse(tracker.renderer_send_requested)

            self.assertTrue(
                tracker.observe_conversation_ref(
                    source="renderer",
                    new_session=True,
                    selection_seq=1,
                    draft_text="Draft text",
                    send_requested=True,
                )
            )
            self.assertEqual(tracker.renderer_draft_text, "Draft text")
            self.assertTrue(tracker.renderer_send_requested)

    def test_renderer_send_pulse_survives_resolved_session_until_followup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-thread-1.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )
            observation = {
                "session_id": "thread-1",
                "title": "Existing Thread",
                "source": "renderer",
                "renderer_session_id": "thread-1",
                "selection_seq": 1,
            }

            with patch.object(
                tracker,
                "path_from_renderer_thread_id",
                return_value=session_path,
            ):
                self.assertTrue(
                    tracker.observe_conversation_ref(
                        **observation,
                        send_requested=True,
                    )
                )
                self.assertTrue(tracker.renderer_send_requested)
                self.assertTrue(
                    tracker.observe_conversation_ref(
                        **observation,
                        send_requested=False,
                    )
                )
                self.assertFalse(tracker.renderer_send_requested)

    def test_active_session_change_callback_runs_for_background_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-thread-1.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            session_index = root / "session_index.jsonl"
            session_index.write_text(
                '{"id":"thread-1","thread_name":"Selected Thread"}\n',
                encoding="utf-8",
            )
            tracker = ActiveSessionTracker(
                platform=FakeEventTitlePlatform(["Selected Thread"]),
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=250,
                enabled=True,
            )
            changes: list[str] = []
            tracker.set_change_callback(lambda: changes.append("changed"))

            try:
                tracker.start()
                deadline = time.time() + 1.0
                while time.time() < deadline and not changes:
                    time.sleep(0.02)
            finally:
                tracker.close()

            self.assertEqual(changes, ["changed"])

    def test_current_path_direct_poll_overrides_stale_event_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            old_path = sessions_root / "rollout-old-thread.jsonl"
            new_path = sessions_root / "rollout-new-thread.jsonl"
            old_path.write_text("{}\n", encoding="utf-8")
            new_path.write_text("{}\n", encoding="utf-8")
            session_index = root / "session_index.jsonl"
            session_index.write_text(
                "\n".join(
                    [
                        '{"id":"old-thread","thread_name":"Old Thread"}',
                        '{"id":"new-thread","thread_name":"New Thread"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            tracker = ActiveSessionTracker(
                platform=FakeInProcessTitlePlatform(["New Thread"]),
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=250,
                enabled=True,
            )
            tracker.latest_title = "Old Thread"
            tracker.latest_path = old_path
            tracker._mapped_title = "Old Thread"

            self.assertEqual(tracker.current_path(), new_path)
            self.assertEqual(tracker.latest_title, "New Thread")
            self.assertEqual(tracker.latest_source, "ui:New Thread")

    def test_current_path_clears_stale_event_title_when_direct_poll_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stale_path = root / "stale.jsonl"
            stale_path.write_text("{}\n", encoding="utf-8")
            tracker = ActiveSessionTracker(
                platform=FakeInProcessTitlePlatform([""]),
                state_db=root / "state_5.sqlite",
                sessions_root=root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
            )
            tracker.latest_title = "Stale Thread"
            tracker.latest_path = stale_path
            tracker._mapped_title = "Stale Thread"

            self.assertIsNone(tracker.current_path())
            self.assertEqual(tracker.latest_title, "")
            self.assertIsNone(tracker.latest_path)
            self.assertEqual(tracker.latest_source, "ui-unmatched")


class SessionPathResolverTests(unittest.TestCase):
    def test_resolver_prefers_tracker_selected_path_over_latest_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            selected = root / "selected.jsonl"
            latest = root / "latest.jsonl"
            selected.write_text("{}\n", encoding="utf-8")
            latest.write_text("{}\n", encoding="utf-8")

            resolver = SessionPathResolver(
                platform=FakePlatform(latest_session=latest),
                sessions_root=root,
                active_session_tracker=_TrackerStub(selected),
            )

            path, source = resolver.resolve()

            self.assertEqual(path, selected)
            self.assertEqual(source, "ui:selected")

    def test_resolver_does_not_fallback_to_activity_for_renderer_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            latest = root / "latest.jsonl"
            latest.write_text("{}\n", encoding="utf-8")
            resolver = SessionPathResolver(
                platform=FakePlatform(latest_session=latest),
                sessions_root=root,
                active_session_tracker=_TrackerStub(
                    None,
                    source="renderer-new-session",
                ),
            )
            resolver.auto_session_file = latest

            path, source = resolver.resolve()

            self.assertIsNone(path)
            self.assertEqual(source, "renderer-new-session")
            self.assertIsNone(resolver.auto_session_file)

    def test_resolver_does_not_fallback_to_activity_while_renderer_tracker_waits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            latest = sessions_root / "latest.jsonl"
            latest.write_text("{}\n", encoding="utf-8")
            platform = FakeCdpRefPlatform("cdp-thread-123", "CDP Thread")
            platform.latest_session = latest
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )
            resolver = SessionPathResolver(
                platform=platform,
                sessions_root=sessions_root,
                active_session_tracker=tracker,
            )

            path, source = resolver.resolve()

            self.assertIsNone(path)
            self.assertEqual(source, "renderer-waiting")
            self.assertIsNone(resolver.auto_session_file)
            self.assertEqual(platform.ref_calls, 0)
            self.assertEqual(platform.detect_calls, 0)

    def test_resolver_waits_for_idle_before_switching_to_newer_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "current.jsonl"
            latest = root / "latest.jsonl"
            current.write_text("{}\n", encoding="utf-8")
            latest.write_text("{}\n", encoding="utf-8")

            now = time.time()
            os.utime(current, (now, now))
            os.utime(latest, (now + 5, now + 5))

            resolver = SessionPathResolver(
                platform=FakePlatform(latest_session=latest),
                sessions_root=root,
                auto_switch_idle_seconds=30.0,
            )
            resolver.auto_session_file = current

            path, source = resolver.resolve()

            self.assertEqual(path, current)
            self.assertEqual(source, "activity")

    def test_resolver_switches_immediately_when_tracker_selects_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.jsonl"
            second = root / "second.jsonl"
            first.write_text("{}\n", encoding="utf-8")
            second.write_text("{}\n", encoding="utf-8")

            resolver = SessionPathResolver(
                platform=FakePlatform(latest_session=second),
                sessions_root=root,
                active_session_tracker=_TrackerSequenceStub([first, second]),
            )

            first_path, first_source = resolver.resolve()
            second_path, second_source = resolver.resolve()

            self.assertEqual(first_path, first)
            self.assertEqual(first_source, "ui:selected")
            self.assertEqual(second_path, second)
            self.assertEqual(second_source, "ui:selected")

    def test_resolver_bypasses_idle_gate_for_unresolved_tracker_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "current.jsonl"
            latest = root / "latest.jsonl"
            current.write_text("{}\n", encoding="utf-8")
            latest.write_text("{}\n", encoding="utf-8")

            now = time.time()
            os.utime(current, (now, now))
            os.utime(latest, (now + 5, now + 5))

            resolver = SessionPathResolver(
                platform=FakePlatform(latest_session=latest),
                sessions_root=root,
                active_session_tracker=_TrackerStub(None, source="ui-unmatched"),
                auto_switch_idle_seconds=30.0,
            )
            resolver.auto_session_file = current

            path, source = resolver.resolve()

            self.assertEqual(path, latest)
            self.assertEqual(source, "ui-unmatched+activity")

    def test_resolver_does_not_fallback_to_activity_for_unresolved_renderer_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "current.jsonl"
            latest = root / "latest.jsonl"
            current.write_text("{}\n", encoding="utf-8")
            latest.write_text("{}\n", encoding="utf-8")

            now = time.time()
            os.utime(current, (now, now))
            os.utime(latest, (now + 5, now + 5))

            platform = FakePlatform(latest_session=latest)
            resolver = SessionPathResolver(
                platform=platform,
                sessions_root=root,
                active_session_tracker=_TrackerStub(None, source="renderer-unmatched"),
                auto_switch_idle_seconds=30.0,
            )
            resolver.auto_session_file = current

            path, source = resolver.resolve()

            self.assertIsNone(path)
            self.assertEqual(source, "renderer-unmatched")
            self.assertIsNone(resolver.auto_session_file)
            self.assertEqual(platform.detect_calls, 0)

    def test_resolver_switches_to_archived_latest_when_tracker_is_unmatched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            archived_root = root / "archived_sessions" / "2026" / "06"
            sessions_root.mkdir()
            archived_root.mkdir(parents=True)

            current = sessions_root / "current.jsonl"
            archived_latest = archived_root / "archived-latest.jsonl"
            current.write_text("{}\n", encoding="utf-8")
            archived_latest.write_text("{}\n", encoding="utf-8")

            now = time.time()
            os.utime(current, (now, now))
            os.utime(archived_latest, (now + 5, now + 5))

            resolver = SessionPathResolver(
                platform=FakePlatform(latest_session=archived_latest),
                sessions_root=sessions_root,
                active_session_tracker=_TrackerStub(None, source="ui-unmatched"),
                auto_switch_idle_seconds=30.0,
            )
            resolver.auto_session_file = current

            path, source = resolver.resolve()

            self.assertEqual(path, archived_latest)
            self.assertEqual(source, "ui-unmatched+activity")

    def test_resolver_clears_stale_missing_auto_session_when_no_latest_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing = root / "missing.jsonl"

            resolver = SessionPathResolver(
                platform=FakePlatform(latest_session=None),
                sessions_root=root,
                active_session_tracker=_TrackerStub(None, source="ui-unmatched"),
            )
            resolver.auto_session_file = missing

            path, source = resolver.resolve()

            self.assertIsNone(path)
            self.assertIsNone(resolver.auto_session_file)
            self.assertEqual(source, "ui-unmatched")


if __name__ == "__main__":
    unittest.main()
