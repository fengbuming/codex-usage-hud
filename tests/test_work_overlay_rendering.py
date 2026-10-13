"""Focused Qt regressions for the session-bubble body and current-session chrome."""

from __future__ import annotations

import os
import sys
import unittest
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


pytest.importorskip("PySide6")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from PySide6.QtWidgets import QApplication, QWidget

import codex_usage_hud.overlay_projection as overlay_projection
import codex_usage_hud.active_work as active_work
from codex_usage_hud.core.parser import Activity, ParsedSession, WorkStatusItem
from codex_usage_hud.ui.work_overlay.constants import (
    DEFAULT_WORK_OVERLAY_THEME,
    WORK_OVERLAY_BODY_MAX_LINES,
)
from codex_usage_hud.ui.work_overlay.qt_rendering import OverlayRenderingMixin
from codex_usage_hud.ui.work_overlay.qt_window import OverlayWindow, _multiline_elided_text


class WorkOverlayRenderingTests(unittest.TestCase):
    def test_cli_failure_clears_only_the_matching_pending_click(self) -> None:
        state = {"updatedAt": time.time(), "items": [], "systemNotice": {
            "id": "cli-jump", "title": "CLI jump", "message": "CLI has exited",
            "relatedRequestId": "older-request", "dismissible": True,
        }}
        with tempfile.TemporaryDirectory() as directory:
            window = OverlayWindow(app=self._app, path=Path(directory) / "state.json",
                                   read_state=lambda: state, owner_pid=None, process_exists=lambda _: True,
                                   stale_seconds=20, item_limit=2, overlay_alpha=1, hover_alpha=1,
                                   heartbeat_path=Path(directory) / "heartbeat", header_title_limit=28)
            try:
                window._switch_pending_request_id = "current-request"
                window.poll_state()
                self.assertEqual(window._switch_pending_request_id, "current-request")
                state["systemNotice"]["relatedRequestId"] = "current-request"
                window.poll_state()
                self.assertEqual(window._switch_pending_request_id, "")
            finally:
                window.close()
                window.deleteLater()
                self._app.processEvents()

    def test_cli_bubble_footer_emits_a_correlated_terminal_jump(self) -> None:
        rendering = self._rendering()
        item = {"id": "cli-session", "sessionId": "10000000-0000-4000-8000-000000000abc",
                "title": "CLI Session", "clientKind": "cli", "status": "running",
                "workdir": r"E:\Project\repo", "lastText": "CLI work", "activitySteps": ()}
        rendering._build_item_card(item)
        self.assertTrue(any(anchor[1]["sessionId"] == item["sessionId"]
                            for anchor in rendering._workdir_anchors))
        helper = SimpleNamespace(_owner_pid=None, _append_command=MagicMock(return_value=True),
                                 _set_switch_pending=MagicMock())
        OverlayWindow.switch_item(helper, item)
        command = helper._append_command.call_args.args[0]
        self.assertEqual(command["action"], "activateSession")
        self.assertEqual(command["clientKind"], "cli")
        self.assertEqual(command["source"], "")
        self.assertEqual(command["sessionId"], item["sessionId"])
        self.assertEqual(helper._switch_pending_request_id, command["requestId"])

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication(sys.argv[:1])
        cls._app.setQuitOnLastWindowClosed(False)

    def setUp(self) -> None:
        self.shell = QWidget()
        self.shell.resize(430, 260)

    def tearDown(self) -> None:
        self.shell.close()
        self.shell.deleteLater()
        self._app.processEvents()

    def _rendering(self, *, theme: dict[str, str] | None = None) -> OverlayRenderingMixin:
        rendering = object.__new__(OverlayRenderingMixin)
        rendering._shell = self.shell
        rendering._theme_tokens = {**DEFAULT_WORK_OVERLAY_THEME, **(theme or {})}
        rendering._header_title_limit = 28
        rendering._multiline_elided_text = _multiline_elided_text
        rendering._item_widgets = []
        rendering._card_hover_anchors = []
        rendering._system_action_anchors = []
        rendering._rest_action_anchors = []
        rendering._close_anchors = []
        rendering._workdir_anchors = []
        rendering._completed_check_anchors = []
        rendering._feed_row_anchors = []
        rendering._feed_peek_item_ids = set()
        rendering._stable_current_session_id = ""
        rendering._feed_spinner_tick = 0
        rendering._switch_pending_active_for_item = lambda _item: False
        rendering._switch_pending_completed_for_item = lambda _item: False
        return rendering

    @staticmethod
    def _body_height(record: dict[str, object]) -> int:
        detail = record["detail"]
        line_height = detail.fontMetrics().height()
        return max(44, (line_height * 2) + max(18, line_height + 4))

    @staticmethod
    def _command_step(
        detail: str,
        status: str,
        *,
        timestamp: datetime,
        call_id: str,
    ) -> dict[str, object]:
        return {
            "title": "running command" if status == "running" else "command complete",
            "detail": detail,
            "status": status,
            "timestamp": timestamp.isoformat(),
            "toolName": "exec",
            "callId": call_id,
            "commandRaw": detail,
        }

    def test_output_body_reserves_three_rows_and_keeps_blue_current_border(self) -> None:
        rendering = self._rendering(theme={"accent": "#339cff", "info": "#ad7bf9"})
        output = (
            "This output is deliberately long enough to wrap across more than three lines in the "
            "session bubble so the final visible line must be elided. "
        ) * 4
        item = {
            "id": "session-a",
            "sessionId": "session-a",
            "title": "Session A",
            "status": "running",
            "statusLabel": "Running",
            "current": True,
            "elapsedText": "Worked 42s",
            "modelName": "gpt-5.6",
            "lastText": output,
            "activitySteps": (),
        }

        rendering._build_item_card(item)
        self.shell.show()
        self._app.processEvents()
        record = rendering._item_widgets[-1]
        card = record["card"]
        card.layout().activate()

        body_host = record["body_host"]
        detail = record["detail"]
        header = record["header"]
        header_meta = record["header_meta"]
        close_anchor = record["close_anchor"]
        self.assertEqual(body_host.height(), self._body_height(record))
        self.assertEqual(detail.height(), body_host.height())
        self.assertLessEqual(len(detail.text().splitlines()), WORK_OVERLAY_BODY_MAX_LINES)
        layout = card.layout()
        assert layout is not None
        header_row = layout.itemAt(0).layout()
        footer = record["footer_container"]
        assert header_row is not None
        self.assertEqual(
            body_host.y() - header_row.geometry().bottom() - 1,
            footer.y() - body_host.geometry().bottom() - 1,
        )
        self.assertNotIn("Worked", header.text())
        self.assertEqual(header_meta.text(), "gpt-5.6")
        self.assertEqual(header_meta.geometry().center().y(), close_anchor.geometry().center().y())
        self.assertIn("border-left: 2px solid #339cff", card.styleSheet())
        self.assertNotIn("#ad7bf9", card.styleSheet())

        rendering._update_item_card(record, {**item, "modelName": ""})
        self.assertEqual(header_meta.text(), "gpt-5.6")
        self.assertTrue(header_meta.isVisible())

    def test_execution_body_reserves_two_output_rows_and_one_execution_row(self) -> None:
        rendering = self._rendering()
        now = datetime.now(timezone.utc)
        item = {
            "id": "session-b",
            "sessionId": "session-b",
            "title": "Session B",
            "status": "tool",
            "statusLabel": "Running",
            "current": True,
            "taskStartedAt": (now - timedelta(minutes=5)).isoformat(),
            "lastOutputAt": (now - timedelta(minutes=6)).isoformat(),
            "lastText": ("Long assistant output that must remain in the two output rows above the execution row. " * 5),
            "activitySteps": (
                self._command_step("rtk rg first", "completed", timestamp=now - timedelta(minutes=4), call_id="1"),
                self._command_step("rtk git diff --check", "completed", timestamp=now - timedelta(minutes=3), call_id="2"),
                self._command_step("python -m pytest tests/test_ui.py -q", "running", timestamp=now - timedelta(minutes=1), call_id="3"),
            ),
        }

        rendering._build_item_card(item)
        self.shell.show()
        self._app.processEvents()
        record = rendering._item_widgets[-1]
        body_host = record["body_host"]
        pinned = record["pinned_output"]
        live_line = record["live_line"]
        live = record["live_text"]
        detail = record["detail"]

        self.assertEqual(body_host.height(), self._body_height(record))
        self.assertTrue(pinned.isVisible())
        self.assertTrue(live_line.isVisible())
        self.assertTrue(live.isVisible())
        self.assertFalse(detail.isVisible())
        self.assertLessEqual(len(pinned.text().splitlines()), 2)
        self.assertEqual(live_line.y(), pinned.height())
        self.assertEqual(live_line.height(), body_host.height() - pinned.height())
        self.assertIn("border: none", live_line.styleSheet())

        rendering._feed_peek_item_ids.add("session-b")
        rendering._update_item_card(record, item)
        self._app.processEvents()

        self.assertEqual(len(record["feed_rows_meta"]), WORK_OVERLAY_BODY_MAX_LINES)
        self.assertTrue(detail.isVisible())
        self.assertFalse(pinned.isVisible())
        self.assertFalse(live_line.isVisible())
        self.assertEqual(len(detail.text().splitlines()), WORK_OVERLAY_BODY_MAX_LINES)
        self.assertGreaterEqual(body_host.height(), detail.fontMetrics().height() * WORK_OVERLAY_BODY_MAX_LINES)

    def test_explicit_empty_collapsed_title_is_not_restored_from_stale_cache(self) -> None:
        now = datetime.now(timezone.utc)
        cached = WorkStatusItem(
            id="session-c",
            title="Session C",
            status="tool",
            status_label="Running",
            detail="work",
            current=True,
            collapsed_title="Worked for 1m",
            updated_at=now,
        )
        current = WorkStatusItem(
            id="session-c",
            title="Session C",
            status="tool",
            status_label="Running",
            detail="work",
            current=True,
            collapsed_title="",
            updated_at=now - timedelta(seconds=1),
        )
        cache = {cached.id: cached}

        visible = overlay_projection.stabilize_published_items(
            [current],
            item_limit=1,
            cache=cache,
            terminal_tasks={},
            provider_scope=None,
            now=now,
            stale_seconds=60.0,
        )

        self.assertEqual(visible[0].collapsed_title, "")
        self.assertEqual(cache[cached.id].collapsed_title, "")

    def test_source_output_is_not_truncated_before_three_line_rendering(self) -> None:
        now = datetime.now(timezone.utc)
        marker = "marker-after-the-old-180-character-limit"
        output = ("long assistant output " * 16) + marker
        snapshot = ParsedSession(
            session_id="session-output",
            session_title="Output",
            session_started_at=now - timedelta(minutes=1),
            task_started_at=now - timedelta(minutes=1),
            activity=Activity(kind="agent", detail="working", timestamp=now),
            last_output=Activity(kind="agent", detail=output, timestamp=now),
        )
        snapshot.request.status = "running"

        item = active_work._work_item_from_snapshot(snapshot, current=True, now=now)

        self.assertIsNotNone(item)
        assert item is not None
        self.assertGreater(len(item.last_text), 180)
        self.assertIn(marker, item.last_text)
