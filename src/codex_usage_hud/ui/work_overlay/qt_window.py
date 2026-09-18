"""PySide6 work-overlay window owner."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QCursor, QFont, QFontMetrics, QTextLayout, QTextOption
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QInputDialog, QWidget

from ... import overlay_ipc
from ...config import normalize_work_overlay_max_items, normalize_work_overlay_side
from .constants import *  # noqa: F401,F403
from .dismissal_store import (
    WORK_OVERLAY_DISMISSAL_FILENAME,
    WorkOverlayDismissalStore,
)
from .geometry import *  # noqa: F401,F403
from .model import *  # noqa: F401,F403
from .qt_hotspots import ClickHotspotWindow, CloseButtonWindow, WorkdirLinkWindow
from .qt_rendering import OverlayRenderingMixin
from .qt_transitions import OverlayTransitionsMixin
from .qt_visuals import CardSwitchPendingOverlayWidget, CompletedBadgeWidget, ShimmerTextLabel
from .theme import *  # noqa: F401,F403

_Qt = Qt
_QFont = QFont
_QFontMetrics = QFontMetrics
_QTextLayout = QTextLayout
_QTextOption = QTextOption

widget_attrs = Qt.WidgetAttribute
focus_policy = Qt.FocusPolicy
window_type = Qt.WindowType


def _screen_item_limit(screen: object | None) -> int | None:
    """Return a geometry clamp only after Qt exposes a usable screen."""
    if screen is None:
        return None
    try:
        screen_height = int(screen.availableGeometry().height())
    except (AttributeError, TypeError, ValueError):
        return None
    if screen_height <= 1:
        return None
    return work_overlay_max_items_for_screen_height(screen_height)


def _multiline_elided_text(
    value: object,
    *,
    font: object,
    width: int,
    max_lines: int = WORK_OVERLAY_BODY_MAX_LINES,
) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if (
        _QTextLayout is None
        or _QTextOption is None
        or _QFontMetrics is None
        or _Qt is None
        or not isinstance(font, _QFont)
        or width <= 0
        or max_lines <= 0
    ):
        return text

    option = _QTextOption()
    option.setWrapMode(_QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout = _QTextLayout(text, font)
    layout.setTextOption(option)
    visible_lines: list[tuple[int, int]] = []
    layout.beginLayout()
    try:
        while len(visible_lines) < max_lines:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, int(width)))
            visible_lines.append((line.textStart(), line.textLength()))
    finally:
        layout.endLayout()

    if not visible_lines:
        return text

    consumed = sum(length for _start, length in visible_lines)
    if consumed >= len(text):
        return text

    metrics = _QFontMetrics(font)
    lines: list[str] = []
    for index, (start, length) in enumerate(visible_lines):
        if index < len(visible_lines) - 1:
            lines.append(text[start : start + length].rstrip())
            continue
        remaining = text[start:].lstrip()
        lines.append(metrics.elidedText(remaining, _Qt.TextElideMode.ElideRight, max(1, int(width))))
    return "\n".join(line for line in lines if line)
def _allow_foreground_process(
    process_id: int | None,
    *,
    user32: object | None = None,
    platform: str | None = None,
) -> bool:
    active_platform = platform if platform is not None else sys.platform
    if not active_platform.startswith("win") or not process_id or int(process_id) <= 0:
        return False
    try:
        if user32 is None:
            import ctypes

            user32 = ctypes.windll.user32
        return bool(user32.AllowSetForegroundWindow(int(process_id)))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _state_has_persistent_sidecar(
    system_action: Mapping[str, object] | None,
    system_notice: Mapping[str, object] | None,
    rest_reminder: Mapping[str, object] | None,
) -> bool:
    """Return whether an unchanged overlay state must remain visible."""
    return bool(
        (system_action and system_action.get("persistent"))
        or (system_notice and system_notice.get("persistent"))
        or rest_reminder
    )


class OverlayWindow(OverlayTransitionsMixin, OverlayRenderingMixin, QWidget):
        def __init__(
        self,
        *,
        app: QApplication,
        path: Path,
        read_state: Callable[[], dict[str, object] | None],
        owner_pid: int | None,
        process_exists: Callable[[int], bool],
        stale_seconds: float,
        item_limit: int,
        overlay_alpha: float,
        hover_alpha: float,
        heartbeat_path: Path,
        header_title_limit: int,
    ) -> None:
            flags = (
                window_type.Tool
                | window_type.FramelessWindowHint
                | window_type.WindowStaysOnTopHint
            )
            transparent_input = getattr(window_type, "WindowTransparentForInput", 0)
            if transparent_input:
                flags |= transparent_input
            super().__init__(None, flags)
            self._state_path = path
            self._read_state = read_state
            self._owner_pid = owner_pid
            self._process_exists = process_exists
            self._stale_seconds = float(stale_seconds)
            self._overlay_alpha = float(overlay_alpha)
            self._hover_alpha = float(hover_alpha)
            self._heartbeat_path = heartbeat_path
            self._dismissed_instances = WorkOverlayDismissalStore(
                path.parent / WORK_OVERLAY_DISMISSAL_FILENAME
            )
            self._last_payload_signature = ""
            self._last_structure_signature = ""
            self._stable_current_session_id = ""
            self._raw_items: list[Mapping[str, object]] = []
            self._previous_visible_items: list[Mapping[str, object]] = []
            self._command_path = _work_overlay_command_path(path)
            self._default_item_limit = normalize_work_overlay_max_items(item_limit, item_limit)
            self._item_limit = self._default_item_limit
            self._side = normalize_work_overlay_side(None)
            self._qt_app = app
            self._header_title_limit = int(header_title_limit)
            self._multiline_elided_text = _multiline_elided_text
            self._theme_tokens = dict(DEFAULT_WORK_OVERLAY_THEME)
            self._close_windows: list[CloseButtonWindow] = []
            self._workdir_windows: list[WorkdirLinkWindow] = []
            self._completed_check_windows: list[ClickHotspotWindow] = []
            self._system_action_windows: list[ClickHotspotWindow] = []
            self._rest_action_windows: list[ClickHotspotWindow] = []
            self._feed_windows: list[ClickHotspotWindow] = []
            self._close_anchors: list[tuple[QWidget, Mapping[str, object], str, str, str]] = []
            self._workdir_anchors: list[tuple[QWidget, Mapping[str, object]]] = []
            self._completed_check_anchors: list[tuple[QWidget, Mapping[str, object]]] = []
            self._system_action_anchors: list[tuple[QWidget, Mapping[str, object]]] = []
            self._rest_action_anchors: list[tuple[QWidget, Mapping[str, object]]] = []
            self._feed_row_anchors: list[tuple[QWidget, Mapping[str, object]]] = []
            self._card_hover_anchors: list[QWidget] = []
            self._completed_hover_anchors: list[QWidget] = []
            # Items whose body is temporarily switched back to the output view
            # from the activity feed ("回输出" affordance).
            self._feed_peek_item_ids: set[str] = set()
            self._feed_spinner_tick = 0
            self._system_action: dict[str, object] | None = None
            self._system_notice: dict[str, object] | None = None
            self._rest_reminder: dict[str, object] | None = None
            self._ready_system_action_ids: set[str] = set()
            self._requested_system_action_ids: set[str] = set()
            self._item_widgets: list[dict[str, Any]] = []
            self.circles: list[dict[str, Any]] = []
            self.rects: list[dict[str, Any]] = []
            self._empty_since = 0.0
            self._state_read_failed_at = 0.0
            self._last_runtime_error_signature = ""
            self._last_runtime_error_at = 0.0
            self._layout_width = WORK_OVERLAY_WIDTH
            self._layout_items: list[Mapping[str, object]] = []
            self._transition_in_progress = False
            self._transition_type = ""
            self._transition_item_id = ""
            self._transition_started_at = 0.0
            self._transition_required_height = 0
            self._transition_card_widget: QWidget | None = None
            self._transition_badge_widget: QWidget | None = None
            self._transition_annihilation_widget: QWidget | None = None
            self._transition_animation_group: Any | None = None
            self._transition_source_effect: QGraphicsOpacityEffect | None = None
            self._transition_source_widget: QWidget | None = None
            self._transition_hidden_widget: QWidget | None = None
            self._settled_completed_intro_ids: set[str] = set()
            self._switch_pending_key = ""
            self._switch_pending_started_at = 0.0
            self._switch_pending_completed_at = 0.0
            self._transition_watchdog = QTimer(self)
            self._transition_watchdog.setSingleShot(True)
            self._transition_watchdog.timeout.connect(self._handle_transition_timeout)
            self._switch_pending_timer = QTimer(self)
            self._switch_pending_timer.timeout.connect(self._tick_switch_pending)
            self._rest_countdown_timer = QTimer(self)
            self._rest_countdown_timer.setInterval(1000)
            self._rest_countdown_timer.timeout.connect(self._tick_rest_reminder)
            self._elapsed_text_timer = QTimer(self)
            self._elapsed_text_timer.setInterval(WORK_OVERLAY_ELAPSED_TEXT_TIMER_MS)
            self._elapsed_text_timer.timeout.connect(self._refresh_live_elapsed_text)
            self._feed_spinner_timer = QTimer(self)
            self._feed_spinner_timer.setInterval(WORK_OVERLAY_FEED_SPINNER_TIMER_MS)
            self._feed_spinner_timer.timeout.connect(self._tick_feed_spinner)
            self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
            self.setAttribute(widget_attrs.WA_ShowWithoutActivating, True)
            self.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            self.setFocusPolicy(focus_policy.NoFocus)
            self.setWindowOpacity(overlay_alpha)

            self._shell = QWidget(self)
            self._shell.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            self._shell.setGeometry(0, 0, WORK_OVERLAY_WIDTH, 1)


        def dismiss_item(self, item: Mapping[str, object]) -> None:
            item_id = str(item.get("id") or "")
            if _item_is_background_usage(item):
                event_id = str(item.get("eventId") or item_id).strip()
                if not event_id or not self._append_command(
                    {
                        "action": "dismissBackgroundUsage",
                        "eventId": event_id,
                        "requestedAt": time.time(),
                    }
                ):
                    return
                _mark_item_dismissed(self._dismissed_instances, item)
                self._last_payload_signature = ""
                self._last_structure_signature = ""
                self.render_items(self._raw_items)
                return
            if (
                WORK_OVERLAY_QT_TRANSITION_ANIMATIONS_ENABLED
                and item_id
                and _item_is_completed(item)
                and not self._transition_in_progress
                and self._record_widget_for_kind(item_id, "completed") is not None
            ):
                self._start_completed_dismiss_transition(dict(item))
                return
            if item_id:
                _mark_item_dismissed(self._dismissed_instances, item)
            self._last_payload_signature = ""
            self._last_structure_signature = ""
            self.render_items(self._raw_items)

        def activate_feed_row(self, item: Mapping[str, object]) -> None:
            """Dispatch an activity-feed row click (copy command / peek output)."""
            action = str(item.get("feedAction") or "").strip()
            if action == "copy_command":
                command = str(item.get("command") or "")
                if command:
                    clipboard = self._qt_app.clipboard()
                    if clipboard is not None:
                        clipboard.setText(command)
                return
            if action in {"peek_output", "resume_feed"}:
                item_id = str(item.get("id") or "")
                if not item_id:
                    return
                if item_id in self._feed_peek_item_ids:
                    self._feed_peek_item_ids.discard(item_id)
                else:
                    self._feed_peek_item_ids.add(item_id)
                # Force the payload pass to re-render this item's body even
                # though the payload signature itself is unchanged.
                self._last_payload_signature = ""
                self.render_items(self._raw_items)

        def _switch_pending_active_for_item(self, item: Mapping[str, object]) -> bool:
            if not self._switch_pending_key or self._switch_pending_started_at <= 0.0:
                return False
            if _switch_item_key(item) != self._switch_pending_key:
                return False
            if self._switch_pending_completed_at > 0.0:
                return (
                    time.monotonic() - self._switch_pending_completed_at
                    <= WORK_OVERLAY_COMPLETED_PENDING_FINISH_SECONDS
                )
            return (
                time.monotonic() - self._switch_pending_started_at
                <= WORK_OVERLAY_SWITCH_PENDING_TIMEOUT_SECONDS
            )

        def _switch_pending_completed_for_item(self, item: Mapping[str, object]) -> bool:
            if self._switch_pending_completed_at <= 0.0:
                return False
            if _switch_item_key(item) != self._switch_pending_key:
                return False
            return (
                time.monotonic() - self._switch_pending_completed_at
                <= WORK_OVERLAY_COMPLETED_PENDING_FINISH_SECONDS
            )

        def _set_switch_pending(self, item: Mapping[str, object]) -> None:
            key = _switch_item_key(item)
            if not key:
                return
            self._switch_pending_key = key
            self._switch_pending_started_at = time.monotonic()
            self._switch_pending_completed_at = 0.0
            if not self._switch_pending_timer.isActive():
                self._switch_pending_timer.start(WORK_OVERLAY_SWITCH_PENDING_TIMER_MS)
            self._sync_completed_pending_animations()
            self.reposition_interactive_windows()

        def _clear_switch_pending(self) -> None:
            self._switch_pending_key = ""
            self._switch_pending_started_at = 0.0
            self._switch_pending_completed_at = 0.0
            self._switch_pending_timer.stop()
            self._sync_completed_pending_animations()
            for window in self._workdir_windows:
                window.update()

        def _complete_switch_pending(self) -> None:
            if not self._switch_pending_key:
                return
            if self._switch_pending_completed_at <= 0.0:
                self._switch_pending_completed_at = time.monotonic()
            if not self._switch_pending_timer.isActive():
                self._switch_pending_timer.start(WORK_OVERLAY_SWITCH_PENDING_TIMER_MS)
            self._sync_completed_pending_animations()
            self.reposition_interactive_windows()

        def _sync_switch_pending(self, items: Sequence[Mapping[str, object]]) -> None:
            if not self._switch_pending_key:
                return
            if self._switch_pending_completed_at > 0.0:
                if (
                    time.monotonic() - self._switch_pending_completed_at
                    > WORK_OVERLAY_COMPLETED_PENDING_FINISH_SECONDS
                ):
                    self._clear_switch_pending()
                else:
                    self._sync_completed_pending_animations()
                return
            elapsed = time.monotonic() - self._switch_pending_started_at
            if elapsed > WORK_OVERLAY_SWITCH_PENDING_TIMEOUT_SECONDS:
                self._clear_switch_pending()
                return
            pending_items = [
                item
                for item in items
                if _switch_item_key(item) == self._switch_pending_key
            ]
            if not pending_items:
                self._clear_switch_pending()
                return
            if any(bool(item.get("current")) for item in pending_items):
                self._complete_switch_pending()

        def _tick_switch_pending(self) -> None:
            if not self._switch_pending_key:
                self._switch_pending_timer.stop()
                return
            if self._switch_pending_completed_at > 0.0:
                if (
                    time.monotonic() - self._switch_pending_completed_at
                    > WORK_OVERLAY_COMPLETED_PENDING_FINISH_SECONDS
                ):
                    self._clear_switch_pending()
                    return
                for window in self._workdir_windows:
                    window.update()
                self._sync_completed_pending_animations()
                return
            if (
                time.monotonic() - self._switch_pending_started_at
                > WORK_OVERLAY_SWITCH_PENDING_TIMEOUT_SECONDS
            ):
                self._clear_switch_pending()
                return
            for window in self._workdir_windows:
                window.update()
            self._sync_completed_pending_animations()

        def _sync_completed_pending_animations(self) -> None:
            for record in self._item_widgets:
                item = record.get("item")
                pending = (
                    isinstance(item, Mapping)
                    and self._switch_pending_active_for_item(item)
                )
                completed = (
                    isinstance(item, Mapping)
                    and self._switch_pending_completed_for_item(item)
                )
                if record.get("kind") == "completed":
                    badge = record.get("badge")
                    if isinstance(badge, CompletedBadgeWidget):
                        badge.set_switch_pending(
                            pending,
                            self._switch_pending_started_at if pending else 0.0,
                            completed=completed,
                            completed_at=self._switch_pending_completed_at if completed else 0.0,
                        )
                    continue
                if record.get("kind") == "card":
                    switch_overlay = record.get("switch_overlay")
                    card = record.get("card")
                    if isinstance(switch_overlay, CardSwitchPendingOverlayWidget):
                        if isinstance(card, QWidget):
                            switch_overlay.setGeometry(card.rect())
                        switch_overlay.set_switch_pending(
                            pending,
                            self._switch_pending_started_at if pending else 0.0,
                            completed=completed,
                            completed_at=self._switch_pending_completed_at if completed else 0.0,
                        )
                        if pending:
                            switch_overlay.raise_()

        def switch_item(self, item: Mapping[str, object]) -> None:
            if _item_is_background_usage(item):
                event_id = str(item.get("eventId") or item.get("id") or "").strip()
                if not event_id:
                    return
                self._append_command(
                    {
                        "action": "openBackgroundUsage",
                        "eventId": event_id,
                        "requestedAt": time.time(),
                    }
                )
                return
            if _item_is_cli(item):
                return
            session_id = str(item.get("sessionId") or item.get("id") or "").strip()
            target_title = str(item.get("targetTitle") or item.get("title") or "").strip()
            if not session_id and not target_title:
                return
            payload = {
                "action": "activateSession",
                "sessionId": session_id,
                "targetTitle": target_title,
                "title": str(item.get("title") or "").strip(),
                "workdir": str(item.get("workdir") or "").strip(),
                "clientKind": str(item.get("clientKind") or "unknown").strip().lower(),
                "requestedAt": time.time(),
                "current": bool(item.get("current")),
            }
            # This helper receives the user's click, while the daemon performs
            # the actual window activation. Transfer Windows foreground rights
            # before the command-file watcher wakes the daemon.
            _allow_foreground_process(self._owner_pid)
            if not self._append_command(payload):
                return
            self._set_switch_pending(item)

        def trigger_system_action(self, item: Mapping[str, object]) -> None:
            if not _item_is_system_action(item):
                return
            action_id = str(item.get("id") or "").strip()
            if not action_id or action_id in self._requested_system_action_ids:
                return
            payload = {
                "action": str(item.get("action") or "").strip(),
                "actionId": action_id,
                "requestedAt": time.time(),
            }
            if not payload["action"] or not self._append_command(payload):
                return
            self._requested_system_action_ids.add(action_id)
            for window in self._system_action_windows:
                window.hide()

        def trigger_rest_action(self, item: Mapping[str, object]) -> None:
            if not _item_is_rest_reminder(item):
                return
            action = str(item.get("action") or "").strip()
            if action == "restReminderCreditMore":
                phase = str(item.get("phase") or "").strip()
                if phase == "resting":
                    dialog_title, dialog_label = "记录休息", "本次实际已休息了多少分钟？"
                elif phase == "postponed":
                    dialog_title, dialog_label = "记录休息", "我已休息了多少分钟？"
                else:
                    dialog_title, dialog_label = "提前休息", "我已提前休息了多少分钟？"
                minutes, accepted = QInputDialog.getInt(
                    self,
                    dialog_title,
                    dialog_label,
                    15,
                    1,
                    1440,
                    1,
                )
                if not accepted:
                    return
                action = "restReminderCredit"
                item = {**dict(item), "minutes": int(minutes)}
            if action not in {
                "restReminderAck",
                "restReminderPostpone",
                "restReminderStart",
                "restReminderFinish",
                "restReminderCredit",
            }:
                return
            command = {
                "action": action,
                "phase": str(item.get("phase") or "").strip(),
                "requestedAt": time.time(),
            }
            if action == "restReminderCredit":
                command["minutes"] = int(item.get("minutes") or 0)
            if not self._append_command(command):
                return
            for window in self._rest_action_windows:
                window.hide()

        def _tick_rest_reminder(self) -> None:
            if self._rest_reminder is None:
                self._rest_countdown_timer.stop()
                return
            self._last_payload_signature = ""
            self.render_items(
                self._raw_items,
                system_action=self._system_action or {},
                system_notice=self._system_notice or {},
                rest_reminder=self._rest_reminder,
            )

        def _append_command(self, payload: Mapping[str, object]) -> bool:
            try:
                command = overlay_ipc.command_message(**dict(payload))
                self._command_path.parent.mkdir(parents=True, exist_ok=True)
                with self._command_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(command, ensure_ascii=False) + "\n")
            except OSError:
                return False
            return True

        def emit_system_action_ready(self, action: Mapping[str, object]) -> None:
            action_id = str(action.get("id") or "").strip()
            if not action_id or action_id in self._ready_system_action_ids:
                return
            if self._append_command(
                {
                    "action": "systemActionReady",
                    "actionId": action_id,
                    "reportedAt": time.time(),
                }
            ):
                self._ready_system_action_ids.add(action_id)

        def emit_helper_heartbeat(self) -> None:
            try:
                self._heartbeat_path.write_text(f"{time.time():.6f}", encoding="utf-8")
            except OSError:
                return

        def emit_runtime_error(
            self,
            *,
            code: str,
            message: str,
            severity: str = "error",
            context: Mapping[str, object] | None = None,
        ) -> None:
            payload = {
                "action": "runtimeError",
                "source": "work_overlay_helper",
                "code": str(code or "helper_error"),
                "message": str(message or "Desktop work overlay helper error."),
                "severity": str(severity or "error"),
                "context": dict(context or {}),
                "reportedAt": time.time(),
            }
            signature = json.dumps(
                {
                    "code": payload["code"],
                    "message": payload["message"],
                    "severity": payload["severity"],
                    "context": payload["context"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            now = time.monotonic()
            if (
                signature == self._last_runtime_error_signature
                and (now - self._last_runtime_error_at) < 1.0
            ):
                return
            self._last_runtime_error_signature = signature
            self._last_runtime_error_at = now
            self._append_command(payload)

        def hide_overlay(self) -> None:
            self.hide()
            for close_window in self._close_windows:
                close_window.hide()
            for workdir_window in self._workdir_windows:
                workdir_window.hide()
            for check_window in self._completed_check_windows:
                check_window.hide()
            for action_window in self._system_action_windows:
                action_window.hide()
            for action_window in self._rest_action_windows:
                action_window.hide()

        def shutdown(self, reason: str = "") -> None:
            try:
                sys.stderr.write(
                    f"work_overlay_helper_shutdown reason={reason or 'unknown'}\n"
                )
            except Exception:
                pass
            self.hide_overlay()
            self._dispose_interactive_windows()
            try:
                self._heartbeat_path.unlink()
            except OSError:
                pass
            self.close()
            self._qt_app.quit()

        def _dispose_interactive_windows(self) -> None:
            for windows in (
                self._close_windows,
                self._workdir_windows,
                self._completed_check_windows,
                self._system_action_windows,
                self._rest_action_windows,
            ):
                while windows:
                    window = windows.pop()
                    try:
                        window.hide()
                        window.close()
                        window.deleteLater()
                    except RuntimeError:
                        continue

        def poll_state(self) -> bool:
            state = self._read_state()
            if state is None:
                now = time.monotonic()
                if self._owner_pid is not None and not self._process_exists(self._owner_pid):
                    self.shutdown("owner-gone")
                    return True
                if self._state_read_failed_at <= 0.0:
                    self._state_read_failed_at = now
                if (
                    now - self._state_read_failed_at
                ) < WORK_OVERLAY_STATE_READ_FAILURE_GRACE_SECONDS:
                    return False
                self.emit_runtime_error(
                    code="state_read_failed",
                    message="Desktop work overlay helper could not read state file.",
                    context={"stateFile": str(self._state_path)},
                )
                self.shutdown("state-read-failed")
                return True
            self._state_read_failed_at = 0.0
            previous_side = self._side
            self._side = normalize_work_overlay_side(state.get("side"))
            should_close = bool(state.get("close"))
            system_action = _normalized_system_action(state.get("systemAction"))
            system_notice = _normalized_system_notice(state.get("systemNotice"))
            rest_reminder = _normalized_rest_reminder(state.get("restReminder"))
            if self._owner_pid is not None and not self._process_exists(self._owner_pid):
                self.shutdown("owner-gone")
                return True
            # White-screen cure (2026-09-18): a stale state file means
            # the HUD has not republished (renderer busy or no change),
            # NOT that the HUD is dead. The owner-alive check above is the
            # authoritative liveness signal. Without this, a busy
            # renderer stalls state writes, the helper self-terminates on
            # staleness, and the rapid-exit breaker respawn loop runs
            # exactly when the overlay is needed most.
            if should_close:
                self.shutdown("close-requested")
                return True
            raw_items = state.get("items") or []
            items = [item for item in raw_items if isinstance(item, Mapping)]
            command_path_text = str(state.get("commandPath") or "").strip()
            self._command_path = (
                Path(command_path_text).expanduser()
                if command_path_text
                else _work_overlay_command_path(self._state_path)
            )
            theme_payload = state.get("theme")
            self._theme_tokens = _resolved_overlay_theme(
                theme_payload if isinstance(theme_payload, Mapping) else None
            )
            screen = self._qt_app.primaryScreen()
            # Qt can briefly report no primary screen while the helper window
            # is being created.  The window's initial height is 1px; treating
            # that placeholder as a real screen height clamps the configured
            # limit to one card and leaves the overlay stuck there.  Defer the
            # geometry-based clamp until a usable screen size is available.
            self._item_limit = normalize_work_overlay_max_items(
                state.get("itemLimit"),
                self._default_item_limit,
                max_items=_screen_item_limit(screen),
            )
            if self._side != previous_side:
                self._last_payload_signature = ""
            self.render_items(
                items,
                system_action=system_action or {},
                system_notice=system_notice or {},
                rest_reminder=rest_reminder or {},
            )
            if system_action is not None:
                self.emit_system_action_ready(system_action)
            return True

        def sync_pointer_state(self) -> None:
            if not self.isVisible():
                target = self._overlay_alpha
            else:
                cursor_pos = QCursor.pos()
                inside_overlay = _overlay_hover_hit_test(
                    cursor_pos.x(),
                    cursor_pos.y(),
                    rects=[
                        bounds
                        for anchor in self._card_hover_anchors
                        if (bounds := self._widget_global_bounds(anchor)) is not None
                    ],
                    circle_rects=[
                        bounds
                        for anchor in self._completed_hover_anchors
                        if (bounds := self._widget_global_bounds(anchor)) is not None
                    ],
                )
                action_hover = False
                for record in self._item_widgets:
                    action_anchor = record.get("feed_action_anchor")
                    if (
                        isinstance(action_anchor, QWidget)
                        and action_anchor.isVisible()
                        and (bounds := self._widget_global_bounds(action_anchor)) is not None
                        and _overlay_hover_hit_test(
                            cursor_pos.x(),
                            cursor_pos.y(),
                            rects=[bounds],
                        )
                    ):
                        action_hover = True
                        break
                # The normal bubble intentionally fades while the pointer is
                # over its card. Scheme B's expand/collapse control is the
                # exception: keep the card readable while the pointer is on
                # that small, explicit action target.
                target = (
                    self._overlay_alpha
                    if action_hover
                    else self._hover_alpha
                    if inside_overlay
                    else self._overlay_alpha
                )
            if abs(self.windowOpacity() - target) < 0.01:
                return
            self.setWindowOpacity(target)
            for close_window in self._close_windows:
                close_window.set_overlay_opacity(target)
            for workdir_window in self._workdir_windows:
                workdir_window.set_overlay_opacity(target)
            for check_window in self._completed_check_windows:
                check_window.set_overlay_opacity(target)
            for action_window in self._system_action_windows:
                action_window.set_overlay_opacity(target)
            for action_window in self._rest_action_windows:
                action_window.set_overlay_opacity(target)






















































        def _reposition_interactive_windows_if_settled(self) -> None:
            if self._transition_in_progress:
                return
            self.reposition_interactive_windows()



        def reposition_interactive_windows(self) -> None:
            # These are top-level native windows.  Keep the bounded pools alive and
            # hide unused entries; QWidget.close() only hides a top-level window
            # and caused USER-handle exhaustion when anchors repeatedly changed.
            while len(self._close_windows) < len(self._close_anchors):
                self._close_windows.append(CloseButtonWindow(self.dismiss_item))

            current_opacity = self.windowOpacity()
            for index, (anchor, item, card_bg, pill_bg, accent) in enumerate(
                self._close_anchors
            ):
                close_window = self._close_windows[index]
                anchor_top_left = anchor.mapToGlobal(QPoint(0, 0))
                close_window.configure(
                    item,
                    background=card_bg,
                    hover_background=pill_bg,
                    foreground="#A9B6C6",
                    hover_foreground=accent,
                    opacity=current_opacity,
                )
                close_window.move(anchor_top_left)
                close_window.show()
                close_window.raise_()

            for close_window in self._close_windows[len(self._close_anchors) :]:
                close_window.hide()

            while len(self._workdir_windows) < len(self._workdir_anchors):
                self._workdir_windows.append(WorkdirLinkWindow(self.switch_item))

            for index, (anchor, item) in enumerate(self._workdir_anchors):
                workdir_window = self._workdir_windows[index]
                anchor_top_left = anchor.mapToGlobal(QPoint(0, 0))
                pending = self._switch_pending_active_for_item(item)
                hotspot_pending = _workdir_link_pending_for_item(item, pending)
                workdir_window.configure(
                    item,
                    opacity=current_opacity,
                    pending=hotspot_pending,
                    pending_started_at=self._switch_pending_started_at if hotspot_pending else 0.0,
                )
                screen = self._qt_app.primaryScreen()
                geometry = screen.availableGeometry() if screen is not None else self.geometry()
                workdir_window.setGeometry(
                    *_pending_workdir_window_rect(
                        anchor_top_left.x(),
                        anchor_top_left.y(),
                        anchor.width(),
                        anchor.height(),
                        pending=hotspot_pending,
                        screen_left=geometry.left(),
                        side=self._side,
                    )
                )
                workdir_window.show()
                workdir_window.raise_()

            for workdir_window in self._workdir_windows[len(self._workdir_anchors) :]:
                workdir_window.hide()

            completed_palette = _completed_badge_palette(self._theme_tokens)
            while len(self._completed_check_windows) < len(self._completed_check_anchors):
                self._completed_check_windows.append(
                    ClickHotspotWindow(
                        self.dismiss_item,
                        circle=False,
                        hover_color=completed_palette["ring"],
                    )
                )

            for index, (anchor, item) in enumerate(self._completed_check_anchors):
                check_window = self._completed_check_windows[index]
                anchor_top_left = anchor.mapToGlobal(QPoint(0, 0))
                check_window.configure(
                    item,
                    opacity=current_opacity,
                    tooltip="关闭气泡",
                    hover_color=completed_palette["ring"],
                )
                check_window.setGeometry(
                    anchor_top_left.x(),
                    anchor_top_left.y(),
                    max(1, anchor.width()),
                    max(1, anchor.height()),
                )
                check_window.show()
                check_window.raise_()

            for check_window in self._completed_check_windows[
                len(self._completed_check_anchors) :
            ]:
                check_window.hide()

            action_color = _color_for("warning", self._theme_tokens)[0]
            while len(self._system_action_windows) < len(self._system_action_anchors):
                self._system_action_windows.append(
                    ClickHotspotWindow(
                        self.trigger_system_action,
                        circle=False,
                        hover_color=action_color,
                    )
                )

            for index, (anchor, item) in enumerate(self._system_action_anchors):
                action_window = self._system_action_windows[index]
                action_id = str(item.get("id") or "").strip()
                if action_id in self._requested_system_action_ids:
                    action_window.hide()
                    continue
                anchor_top_left = anchor.mapToGlobal(QPoint(0, 0))
                action_window.configure(
                    item,
                    opacity=current_opacity,
                    tooltip=str(item.get("statusText") or "重启 Codex"),
                    hover_color=action_color,
                )
                action_window.setGeometry(
                    anchor_top_left.x(),
                    anchor_top_left.y(),
                    max(1, anchor.width()),
                    max(1, anchor.height()),
                )
                action_window.show()
                action_window.raise_()

            for action_window in self._system_action_windows[
                len(self._system_action_anchors) :
            ]:
                action_window.hide()

            rest_action_color = _color_for("waiting_user", self._theme_tokens)[0]
            while len(self._rest_action_windows) < len(self._rest_action_anchors):
                self._rest_action_windows.append(
                    ClickHotspotWindow(
                        self.trigger_rest_action,
                        circle=False,
                        hover_color=rest_action_color,
                    )
                )

            for index, (anchor, item) in enumerate(self._rest_action_anchors):
                action_window = self._rest_action_windows[index]
                anchor_top_left = anchor.mapToGlobal(QPoint(0, 0))
                action_window.configure(
                    item,
                    opacity=current_opacity,
                    tooltip=str(item.get("actionLabel") or "休息提醒操作"),
                    hover_color=rest_action_color,
                )
                action_window.setGeometry(
                    anchor_top_left.x(),
                    anchor_top_left.y(),
                    max(1, anchor.width()),
                    max(1, anchor.height()),
                )
                action_window.show()
                action_window.raise_()

            for action_window in self._rest_action_windows[
                len(self._rest_action_anchors) :
            ]:
                action_window.hide()

            feed_row_color = _color_for("tool", self._theme_tokens)[0]
            # Refresh feed anchor rects now that card layout has settled, then
            # place one click hotspot per visible feed row.
            for record in self._item_widgets:
                if record.get("feed_rows_meta"):
                    self._sync_feed_action_label(
                        record,
                        expanded=(
                            _item_id(record.get("item") or {})
                            in getattr(self, "_feed_peek_item_ids", set())
                        ),
                    )
                    self._sync_feed_anchor_geometry(record)

            while len(self._feed_windows) < len(self._feed_row_anchors):
                self._feed_windows.append(
                    ClickHotspotWindow(
                        self.activate_feed_row,
                        circle=False,
                        hover_color=feed_row_color,
                    )
                )

            for index, (anchor, action_item) in enumerate(self._feed_row_anchors):
                feed_window = self._feed_windows[index]
                anchor_top_left = anchor.mapToGlobal(QPoint(0, 0))
                action = str(action_item.get("feedAction") or "")
                tooltip = "复制完整命令" if action == "copy_command" else "展开执行详情"
                if action == "resume_feed":
                    tooltip = "收起执行详情"
                feed_window.configure(
                    action_item,
                    opacity=current_opacity,
                    tooltip=tooltip,
                    hover_color=feed_row_color,
                )
                feed_window.setGeometry(
                    anchor_top_left.x(),
                    anchor_top_left.y(),
                    max(1, anchor.width()),
                    max(1, anchor.height()),
                )
                feed_window.show()
                feed_window.raise_()

            for feed_window in self._feed_windows[len(self._feed_row_anchors) :]:
                feed_window.hide()


def _work_overlay_command_path(state_path: Path) -> Path:
    return overlay_ipc.command_path(state_path)


__all__ = [
    "OverlayWindow",
    "_allow_foreground_process",
    "_multiline_elided_text",
]
