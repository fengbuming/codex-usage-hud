"""Qt card, completed-badge, and layout rendering owner."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from PySide6.QtCore import (
    QPoint,
    Property,
    QRect,
    QRectF,
    Qt,
    QEasingCurve,
    QPropertyAnimation,
    QTimer,
)
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .constants import (
    WORK_OVERLAY_BODY_MAX_LINES,
    WORK_OVERLAY_CARD_SPACING,
    WORK_OVERLAY_CARD_X_PADDING,
    WORK_OVERLAY_CARD_Y_PADDING,
    WORK_OVERLAY_CLOSE_SIZE,
    WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
    WORK_OVERLAY_COMPLETED_BADGE_SIZE,
    WORK_OVERLAY_EMPTY_GRACE_SECONDS,
    WORK_OVERLAY_FEED_SPINNER_ENABLED,
    WORK_OVERLAY_FEED_SPINNER_TIMER_MS,
    WORK_OVERLAY_MARGIN,
    WORK_OVERLAY_QT_TRANSITION_ANIMATIONS_ENABLED,
    WORK_OVERLAY_TEXT_WRAP_WIDTH,
    WORK_OVERLAY_TRANSITION_CARD_HEIGHT,
    WORK_OVERLAY_WIDTH,
    WORK_OVERLAY_WORKDIR_FOOTER_WIDTH,
)
from .geometry import (
    OverlayRect,
    _card_slot_rect,
    _completed_badge_row_width,
    _completed_slot_rect,
    _defer_other_transition_items,
    _detect_transition,
    _detect_transition_item_id,
    _find_item_rect,
    _overlay_items_required_height,
    _overlay_window_top_y,
    _overlay_window_x,
    _transition_layout_width,
)
from .model import (
    _compact_work_text,
    _item_id,
    _item_is_background_usage,
    _item_is_completed,
    _item_is_rest_reminder,
    _item_is_system_action,
    _item_is_system_notice,
    _matched_overlay_item_records,
    _next_stable_current_session_id,
    _overlay_activity_step_texts,
    _overlay_activity_steps,
    _overlay_activity_tooltip,
    _overlay_feed_active,
    _overlay_feed_rows,
    _overlay_feed_summary_row,
    _overlay_feed_spinner_frame,
    _overlay_footer_selection,
    _overlay_execution_body_active,
    _overlay_execution_group_title,
    _overlay_execution_live_summary,
    _overlay_execution_rows,
    _overlay_running_execution_title,
    _overlay_title_is_active,
    _overlay_title_is_elapsed,
    _overlay_item_is_stably_current,
    _overlay_step_elapsed_seconds,
    _normalized_rest_reminder,
    _normalized_system_action,
    _normalized_system_notice,
    _ordered_overlay_items,
    _rest_reminder_overlay_item,
    _system_action_overlay_item,
    _system_notice_overlay_item,
    _visible_overlay_items,
    _work_overlay_header_text,
    _work_overlay_item_with_live_elapsed_text,
    _work_overlay_live_elapsed_text,
    _workdir_clickable_for_item,
    _workdir_display_name,
    _workdir_external_link_for_item,
    _workdir_footer_display_name,
)
from .qt_visuals import (
    CardSwitchPendingOverlayWidget,
    CompletedBadgeWidget,
    ShimmerTextLabel,
)
from .theme import _color_for, _overlay_payload_signature, _round_badge_palette, _theme_mix

widget_attrs = Qt.WidgetAttribute
focus_policy = Qt.FocusPolicy
alignment = Qt.AlignmentFlag
text_format = Qt.TextFormat
window_type = Qt.WindowType


class ScrollingFeedLabel(QLabel):
    """Three-line label that rolls the execution window upward on updates."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scroll_lines: list[str] = []
        self._old_scroll_lines: list[str] = []
        self._scroll_offset = 0.0
        self._scroll_animation = QPropertyAnimation(self, b"scrollOffset", self)
        self._scroll_animation.setDuration(180)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setAttribute(widget_attrs.WA_OpaquePaintEvent, False)

    def _get_scroll_offset(self) -> float:
        return float(self._scroll_offset)

    def _set_scroll_offset(self, value: float) -> None:
        self._scroll_offset = max(0.0, min(1.0, float(value)))
        self.update()

    scrollOffset = Property(float, _get_scroll_offset, _set_scroll_offset)

    def clear_scrolling_lines(self) -> None:
        self._scroll_animation.stop()
        self._scroll_lines = []
        self._old_scroll_lines = []
        self._scroll_offset = 0.0
        self.update()

    def set_scrolling_lines(self, lines: Sequence[str]) -> None:
        normalized = [str(line or "") for line in lines if str(line or "")]
        if normalized == self._scroll_lines:
            return
        old = list(self._scroll_lines)
        self._scroll_animation.stop()
        self._old_scroll_lines = old
        self._scroll_lines = normalized
        self.setText("\n".join(normalized))
        if old and normalized:
            self._scroll_offset = 0.0
            self._scroll_animation.setStartValue(0.0)
            self._scroll_animation.setEndValue(1.0)
            self._scroll_animation.start()
        else:
            self._scroll_offset = 1.0
        self.update()

    def paintEvent(self, event: object) -> None:
        if not self._old_scroll_lines or self._scroll_offset >= 1.0:
            super().paintEvent(event)
            return
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setClipRect(self.rect())
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        line_height = max(1, self.fontMetrics().height())
        progress = self._scroll_offset
        old_y = -progress * line_height
        new_y = (1.0 - progress) * line_height
        for index, line in enumerate(self._old_scroll_lines):
            painter.drawText(
                QRect(0, int(old_y + index * line_height), self.width(), line_height),
                int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft),
                line,
            )
        for index, line in enumerate(self._scroll_lines):
            painter.drawText(
                QRect(0, int(new_y + index * line_height), self.width(), line_height),
                int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft),
                line,
            )


class OverlayRenderingMixin:
    def _wrapped_label_height(self, label: QLabel, width: int) -> int:
        return max(label.sizeHint().height(), label.heightForWidth(width), label.minimumSizeHint().height())

    @staticmethod
    def _item_identity(item: Mapping[str, object], index: int) -> str:
        item_id = str(item.get("id") or "").strip()
        return item_id or f"overlay-index-{index}"

    @staticmethod
    def _item_widget_kind(item: Mapping[str, object]) -> str:
        return "completed" if _item_is_completed(item) else "card"

    @staticmethod
    def _widget_global_bounds(widget: QWidget) -> tuple[int, int, int, int] | None:
        if not widget.isVisible():
            return None
        top_left = widget.mapToGlobal(QPoint(0, 0))
        return (
            top_left.x(),
            top_left.y(),
            max(1, widget.width()),
            max(1, widget.height()),
        )

    def _clear_shell(self) -> None:
        self._close_anchors.clear()
        self._workdir_anchors.clear()
        self._completed_check_anchors.clear()
        self._system_action_anchors.clear()
        self._rest_action_anchors.clear()
        self._card_hover_anchors.clear()
        self._completed_hover_anchors.clear()
        feed_row_anchors = getattr(self, "_feed_row_anchors", None)
        if isinstance(feed_row_anchors, list):
            feed_row_anchors.clear()
        self._item_widgets.clear()
        self.circles.clear()
        self.rects.clear()
        for child in list(self._shell.findChildren(QWidget)):
            if child.parent() is self._shell:
                child.deleteLater()

    def _sync_live_elapsed_timer(
        self,
        items: Sequence[Mapping[str, object]],
    ) -> None:
        feed_active = any(_overlay_feed_active(item) for item in items)
        if feed_active or any(
            _work_overlay_live_elapsed_text(item) is not None for item in items
        ):
            if not self._elapsed_text_timer.isActive():
                self._elapsed_text_timer.start()
        else:
            self._elapsed_text_timer.stop()
        spinner_timer = getattr(self, "_feed_spinner_timer", None)
        if isinstance(spinner_timer, QTimer):
            if feed_active and WORK_OVERLAY_FEED_SPINNER_ENABLED:
                if not spinner_timer.isActive():
                    spinner_timer.start()
            else:
                spinner_timer.stop()

    def _activity_step_display_text(
        self,
        record: dict[str, Any],
        item: Mapping[str, object],
        *,
        now: float | None = None,
    ) -> tuple[str, str]:
        """Show the newest parsed action as soon as the payload refreshes.

        The footer picks its row by priority (running > 等确认 > 失败 > 思考
        标题 > 末条). Running rows carry the local elapsed seconds that share
        the 1s elapsed tick, and thinking rows carry the rotating spinner;
        the full step list stays available in the tooltip.
        """
        del record, now
        native_title = _compact_work_text(
            item.get("nativeCollapsedTitle") or item.get("collapsedTitle"),
            240,
        )
        native_title_is_elapsed = _overlay_title_is_elapsed(native_title)
        if native_title and not native_title_is_elapsed and _overlay_title_is_active(native_title):
            tooltip = native_title
            activity_tooltip = _overlay_activity_tooltip(item)
            if activity_tooltip:
                tooltip = f"{tooltip}\n{activity_tooltip}"
            return native_title, tooltip
        texts = _overlay_activity_step_texts(item)
        running_title = _overlay_running_execution_title(item)
        if running_title and _overlay_feed_active(item) and _overlay_execution_body_active(item):
            tooltip = running_title
            activity_tooltip = _overlay_activity_tooltip(item)
            if activity_tooltip:
                tooltip = f"{tooltip}\n{activity_tooltip}"
            return running_title, tooltip
        if bool(item.get("collapsedDisclosureAmbiguous")):
            return "", ""
        if native_title and not native_title_is_elapsed:
            tooltip = native_title
            activity_tooltip = _overlay_activity_tooltip(item)
            if activity_tooltip:
                tooltip = f"{tooltip}\n{activity_tooltip}"
            return native_title, tooltip
        if not texts:
            return "", ""
        group_title = _overlay_execution_group_title(item)
        if group_title and _overlay_feed_active(item) and _overlay_execution_body_active(item):
            tooltip = group_title
            activity_tooltip = _overlay_activity_tooltip(item)
            if activity_tooltip:
                tooltip = f"{tooltip}\n{activity_tooltip}"
            return group_title, tooltip
        kind, text = _overlay_footer_selection(item)
        if not text:
            return "", ""
        if kind == "running":
            steps = _overlay_activity_steps(item)
            running = [
                step
                for step in steps
                if str(step.get("status") or "").strip().lower() == "running"
            ]
            elapsed = (
                _overlay_step_elapsed_seconds(
                    running[-1],
                    datetime.now().astimezone(),
                )
                if running
                else 0
            )
            spinner = self._feed_spinner_glyph()
            prefix = f"{spinner} " if spinner else ""
            text = f"{prefix}{text} · {elapsed}s"
        elif kind == "thinking":
            spinner = self._feed_spinner_glyph()
            prefix = f"{spinner} " if spinner else ""
            text = f"{prefix}{text}"
        return text, _overlay_activity_tooltip(item)

    def _feed_spinner_glyph(self) -> str:
        if not WORK_OVERLAY_FEED_SPINNER_ENABLED:
            return ""
        return _overlay_feed_spinner_frame(getattr(self, "_feed_spinner_tick", 0))

    def _footer_elide_width(self, label: QLabel) -> int:
        # Reserve room for the local-seconds suffix and the workdir leaf that
        # share the footer row; commands keep head and tail via middle elision.
        return max(60, WORK_OVERLAY_TEXT_WRAP_WIDTH - 96)

    def _footer_display_text(self, label: QLabel, text: str) -> str:
        return QFontMetrics(label.font()).elidedText(
            text,
            Qt.TextElideMode.ElideMiddle,
            self._footer_elide_width(label),
        )

    def _feed_rows_for_item(
        self,
        record: dict[str, Any],
        item: Mapping[str, object],
    ) -> list[dict[str, str]]:
        """Build the compact execution summary, or expanded activity rows."""
        if str(record.get("kind") or "") != "card":
            return []
        detail = record.get("detail")
        if not isinstance(detail, QLabel):
            return []
        if not _overlay_feed_active(item):
            peek_ids = getattr(self, "_feed_peek_item_ids", None)
            if isinstance(peek_ids, set):
                peek_ids.discard(_item_id(item))
            return []
        item_id = _item_id(item)
        peek_ids = getattr(self, "_feed_peek_item_ids", None)
        rows = _overlay_feed_rows(
            item,
            spinner_frame=self._feed_spinner_glyph(),
            now=datetime.now().astimezone(),
        )
        if not rows:
            return []
        expanded = isinstance(peek_ids, set) and item_id and item_id in peek_ids
        if _overlay_execution_body_active(item):
            rows = _overlay_execution_rows(
                item,
                max_rows=WORK_OVERLAY_BODY_MAX_LINES if expanded else 1,
            )
            if expanded and rows:
                rows = [dict(row) for row in rows]
                rows[-1]["action"] = "resume_feed"
                rows[-1]["tooltip"] = "收起执行详情"
        elif not expanded:
            summary = _overlay_feed_summary_row(rows)
            rows = [dict(summary)] if summary else rows[-1:]
            if rows:
                rows[0]["action"] = "peek_output"
                rows[0]["tooltip"] = "展开执行详情"
        else:
            rows = [dict(row) for row in rows]
            if rows:
                rows[-1]["action"] = "resume_feed"
                rows[-1]["tooltip"] = "收起执行详情"
        if rows:
            mode = "expanded" if expanded else str(rows[0].get("mode") or "summary")
            for row in rows:
                row.setdefault("mode", mode)
        metrics = QFontMetrics(detail.font())
        width = WORK_OVERLAY_TEXT_WRAP_WIDTH
        for row in rows:
            text = str(row.get("text") or "")
            if row.get("kind") == "cmd" and row.get("command"):
                command = str(row["command"])
                row["text"] = metrics.elidedText(
                    f"$ {command}",
                    Qt.TextElideMode.ElideMiddle,
                    width,
                )
            elif text:
                row["text"] = metrics.elidedText(
                    text,
                    Qt.TextElideMode.ElideRight,
                    width,
                )
        return rows

    def _refresh_feed_body(
        self,
        record: dict[str, Any],
        item: Mapping[str, object],
    ) -> None:
        """Timer-path refresh of the feed body text (seconds and spinner)."""
        # Rest-reminder cards never show the session activity feed; skip them
        # here so a stale record cannot inherit a "思考中" row or "展开"
        # affordance from the timer tick.
        if _item_is_rest_reminder(item):
            return
        detail = record.get("detail")
        if not isinstance(detail, QLabel):
            return
        rows = self._feed_rows_for_item(record, item)
        if not rows:
            return
        expanded = _item_id(item) in getattr(self, "_feed_peek_item_ids", set())
        mode = str(rows[0].get("mode") or "summary")
        if expanded or mode == "expanded" or mode == "execution":
            text = "\n".join(str(row.get("text") or "") for row in rows)
        else:
            body_text = str(item.get("lastText") or item.get("detail") or "").strip()
            output_text = self._multiline_elided_text(
                body_text,
                font=detail.font(),
                width=WORK_OVERLAY_TEXT_WRAP_WIDTH,
                max_lines=2,
            )
            summary_text = str(rows[0].get("text") or "")
            summary_text = self._multiline_elided_text(
                summary_text,
                font=detail.font(),
                width=max(1, WORK_OVERLAY_TEXT_WRAP_WIDTH - 52),
                max_lines=1,
            )
            text = "\n".join(part for part in (output_text, summary_text) if part)
        record["feed_rows_meta"] = list(rows)
        if not expanded and mode in {"summary", "execution"}:
            live_symbol = record.get("live_symbol")
            if isinstance(live_symbol, QLabel):
                live_symbol.setText(
                    "!"
                    if any(str(row.get("kind") or "") == "wait" for row in rows)
                    else "×"
                    if any(str(row.get("kind") or "") in {"fail", "failed"} for row in rows)
                    else "…"
                )
            self._sync_scheme_b_body(
                record,
                mode=mode,
                expanded=False,
                body_text=str(item.get("lastText") or item.get("detail") or "").strip(),
                live_text=str(rows[-1].get("text") or ""),
                detail_text=text,
            )
            self._sync_feed_action_label(record, expanded=False)
            self._sync_feed_anchor_geometry(record)
            return
        if text == detail.text() and mode != "expanded":
            return
        if isinstance(detail, ScrollingFeedLabel):
            detail.clear_scrolling_lines()
        detail.setText(text)
        detail.setFixedHeight(
            self._wrapped_label_height(detail, WORK_OVERLAY_TEXT_WRAP_WIDTH)
        )
        detail.setToolTip(
            "\n".join(
                str(row.get("tooltip") or "") for row in rows if row.get("tooltip")
            )
        )
        self._sync_scheme_b_body(
            record,
            mode="expanded",
            expanded=True,
            body_text="",
            live_text="",
            detail_text=text,
        )
        self._sync_feed_action_label(record, expanded=True)
        self._sync_feed_anchor_geometry(record)

    def _sync_feed_anchor_geometry(self, record: dict[str, Any]) -> None:
        """Position one invisible click target over each visible feed row."""
        rows = record.get("feed_rows_meta") or []
        anchors = record.get("feed_anchors") or []
        detail = record.get("detail")
        body_host = record.get("body_host")
        if not isinstance(detail, QLabel) or not anchors:
            return
        count = max(1, len(rows))
        source = body_host if isinstance(body_host, QWidget) else detail
        row_height = max(1, source.height() // count) if rows else 0
        expanded = _item_id(record.get("item") or {}) in getattr(
            self, "_feed_peek_item_ids", set()
        )
        for index, anchor in enumerate(anchors):
            if (
                index >= len(rows)
                or str(rows[index].get("action") or "") != "copy_command"
                or (not expanded and str(rows[index].get("mode") or "") == "execution")
            ):
                anchor.hide()
                continue
            anchor.setGeometry(
                0 if source is body_host else detail.x(),
                index * row_height if source is body_host else detail.y() + index * row_height,
                min(
                    source.width(),
                    max(
                        24,
                        QFontMetrics(detail.font()).horizontalAdvance(
                            str(rows[index].get("text") or "")
                        )
                        + 12,
                    ),
                ),
                row_height,
            )
            anchor.show()
        action_anchor = record.get("feed_action_anchor")
        action_label = record.get("feed_action_label")
        if (
            isinstance(action_anchor, QWidget)
            and isinstance(action_label, QLabel)
            and action_label.isVisible()
        ):
            action_anchor.setGeometry(action_label.geometry())
            action_anchor.show()
        elif isinstance(action_anchor, QWidget):
            action_anchor.hide()

    def _sync_feed_action_label(
        self,
        record: dict[str, Any],
        *,
        expanded: bool,
    ) -> None:
        """Place the visible scheme-B expand/collapse affordance in the body."""
        label = record.get("feed_action_label")
        detail = record.get("detail")
        body_host = record.get("body_host")
        rows = record.get("feed_rows_meta") or []
        if (
            not isinstance(label, QLabel)
            or not isinstance(detail, QLabel)
            or not isinstance(body_host, QWidget)
            or not rows
        ):
            if isinstance(label, QLabel):
                label.hide()
            action_anchor = record.get("feed_action_anchor")
            if isinstance(action_anchor, QWidget):
                action_anchor.hide()
            return
        control_width = 22 if expanded else 48
        line_height = max(1, detail.fontMetrics().height())
        row_height = max(18, line_height + 4)
        body_height = max(44, (line_height * 2) + row_height)
        live_y = max(0, body_height - row_height)
        label.setText("↓" if expanded else "展开")
        label.setToolTip("收起执行详情" if expanded else "展开执行详情")
        label.setFixedWidth(control_width)
        label.setFixedHeight(row_height)
        label.setGeometry(
            max(0, body_host.width() - control_width),
            0
            if expanded
            else live_y,
            control_width,
            row_height,
        )
        info = "#76B8F6"
        divider = str(self._theme_tokens.get("panelBorder") or "#323232")
        label.setStyleSheet(
            "QLabel {"
            f"color: {info};"
            "background-color: #212121;"
            f"border: 1px solid {divider};"
            "border-radius: 5px;"
            "padding: 0 4px;"
            "}"
        )
        label.show()
        label.raise_()
        action_anchor = record.get("feed_action_anchor")
        if isinstance(action_anchor, QWidget):
            action_anchor.setGeometry(label.geometry())
            action_anchor.show()

    def _sync_scheme_b_body(
        self,
        record: dict[str, Any],
        *,
        mode: str,
        expanded: bool,
        body_text: str,
        live_text: str,
        detail_text: str,
    ) -> None:
        """Apply the HTML scheme-B pinned/output/feed layout inside one card."""
        host = record.get("body_host")
        detail = record.get("detail")
        pinned = record.get("pinned_output")
        live_line = record.get("live_line")
        live_symbol = record.get("live_symbol")
        live = record.get("live_text")
        action = record.get("feed_action_label")
        if not isinstance(host, QWidget) or not isinstance(detail, QLabel):
            return
        width = max(
            WORK_OVERLAY_TEXT_WRAP_WIDTH,
            int(host.width() or WORK_OVERLAY_TEXT_WRAP_WIDTH),
        )
        line_height = max(1, detail.fontMetrics().height())
        live_row_height = max(18, line_height + 4)
        body_height = max(44, (line_height * 2) + live_row_height)
        pinned_height = body_height - live_row_height
        live_y = pinned_height

        if mode == "legacy":
            # Special cards (rest reminders, system notices/actions, and
            # background-usage notices) never participate in scheme B. Keep
            # their original body/detail path and hide every execution-only
            # child so a reminder cannot inherit an expand affordance.
            if isinstance(pinned, QLabel):
                pinned.hide()
            if isinstance(live_line, QWidget):
                live_line.hide()
            if isinstance(live_symbol, QLabel):
                live_symbol.hide()
            if isinstance(live, QLabel):
                live.hide()
            if isinstance(action, QLabel):
                action.hide()
            action_anchor = record.get("feed_action_anchor")
            if isinstance(action_anchor, QWidget):
                action_anchor.hide()
            host.setFixedHeight(
                max(1, self._wrapped_label_height(detail, width))
            )
            detail.show()
            detail.setGeometry(0, 0, width, host.height())
            detail.setFixedHeight(host.height())
            if isinstance(detail, ScrollingFeedLabel):
                detail.clear_scrolling_lines()
            detail.setText(detail_text)
            return

        if mode in {"summary", "execution"} and not expanded:
            host.setFixedHeight(body_height)
            detail.hide()
            if isinstance(pinned, QLabel):
                pinned_text = self._multiline_elided_text(
                    body_text or "等待输出",
                    font=pinned.font(),
                    width=width,
                    max_lines=2,
                )
                pinned.setText(pinned_text)
                pinned.setGeometry(0, 0, width, pinned_height)
                pinned.setStyleSheet(
                    "QLabel { color: #B9B9B9; border: none; background: transparent; opacity: 0.72; }"
                )
                pinned.show()
            if isinstance(live_line, QWidget):
                live_line.setGeometry(0, live_y, width, body_height - live_y)
                live_line.show()
            if isinstance(live_symbol, QLabel):
                live_symbol.setGeometry(0, 0, 16, max(1, body_height - live_y))
                live_symbol.show()
            if isinstance(live, QLabel):
                live.setGeometry(18, 0, max(1, width - 70), max(1, body_height - live_y))
                live.setText(
                    self._multiline_elided_text(
                        live_text or "执行中",
                        font=live.font(),
                        width=max(1, width - 70),
                        max_lines=1,
                    )
                )
                live.show()
            if isinstance(action, QLabel):
                action.show()
            if isinstance(action, QLabel):
                action.raise_()
            return

        if isinstance(pinned, QLabel):
            pinned.hide()
        if isinstance(live_line, QWidget):
            live_line.hide()
        if isinstance(live_symbol, QLabel):
            live_symbol.hide()
        if isinstance(live, QLabel):
            live.hide()
        # The compact body is a stable three-line slot.  Short output must not
        # collapse the card; when execution is active the last slot is used by
        # the live execution row, leaving two output lines above it.
        host.setFixedHeight(body_height)
        detail.show()
        if isinstance(detail, ScrollingFeedLabel):
            detail.set_scrolling_lines(detail_text.splitlines())
        else:
            detail.setText(detail_text)
        detail.setGeometry(0, 0, width, host.height())
        detail.setFixedHeight(host.height())
        if isinstance(action, QLabel):
            action.setVisible(bool(expanded and record.get("feed_rows_meta")))
            if expanded:
                action.raise_()

    def _tick_feed_spinner(self) -> None:
        """Rotate the feed spinner glyph; only runs while a feed is active."""
        if not WORK_OVERLAY_FEED_SPINNER_ENABLED:
            return
        self._feed_spinner_tick = int(getattr(self, "_feed_spinner_tick", 0)) + 1
        for record in self._item_widgets:
            if record.get("kind") != "card":
                continue
            item = record.get("item")
            if not isinstance(item, Mapping):
                continue
            if not _overlay_feed_active(item):
                continue
            self._refresh_feed_body(record, item)
            self._refresh_activity_step_label(record, item)

    @staticmethod
    def _workdir_footer_width(label: QLabel, text: str) -> int:
        """Reserve only the visible provider/workdir width, up to its cap."""
        natural_width = QFontMetrics(label.font()).horizontalAdvance(text) + 4
        return max(1, min(WORK_OVERLAY_WORKDIR_FOOTER_WIDTH, natural_width))

    def _refresh_activity_step_label(
        self,
        record: dict[str, Any],
        item: Mapping[str, object],
        *,
        now: float | None = None,
    ) -> None:
        label = record.get("status_label")
        if not isinstance(label, ShimmerTextLabel):
            return
        if (
            _item_is_system_action(item)
            or _item_is_system_notice(item)
            or _item_is_background_usage(item)
            or _item_is_rest_reminder(item)
            or str(item.get("status") or "").strip() == "draft"
        ):
            return
        text, tooltip = self._activity_step_display_text(record, item, now=now)
        if not text:
            return
        label.setText(self._footer_display_text(label, text))
        label.setToolTip(tooltip or text)

    def _refresh_live_elapsed_text(self) -> None:
        now = time.monotonic()
        for record in self._item_widgets:
            if record.get("kind") != "card":
                continue
            item = record.get("item")
            if not isinstance(item, Mapping):
                continue
            updated_item = _work_overlay_item_with_live_elapsed_text(item)
            elapsed_text = str(updated_item.get("elapsedText") or "")
            if elapsed_text != str(item.get("elapsedText") or ""):
                record["item"] = updated_item
                header = record.get("header")
                if isinstance(header, QLabel):
                    header.setText(
                        _work_overlay_header_text(
                            str(item.get("startedAt") or ""),
                            elapsed_text,
                            str(item.get("title") or "Codex 工作"),
                            title_limit=self._header_title_limit,
                        )
                    )
                item = updated_item
            self._refresh_activity_step_label(record, item, now=now)
            self._refresh_feed_body(record, item)

    def _build_item_widget(self, item: Mapping[str, object]) -> None:
        self._build_item_card(item)

    def _update_item_widget(self, record: dict[str, Any], item: Mapping[str, object]) -> None:
        if record.get("kind") == "completed":
            self._update_completed_badge(record, item)
        else:
            self._update_item_card(record, item)

    def _build_completed_row(
        self,
        completed_items: Sequence[Mapping[str, object]],
    ) -> None:
        for item in completed_items:
            self._build_completed_badge(item)

    def _build_completed_badge(
        self,
        item: Mapping[str, object],
    ) -> None:
        item_id = _item_id(item)
        animate_intro = item_id not in self._settled_completed_intro_ids
        badge = CompletedBadgeWidget(
            item,
            self._shell,
            animate_intro=animate_intro,
            theme_tokens=self._theme_tokens,
        )
        if item_id:
            self._settled_completed_intro_ids.add(item_id)
        hover_anchor = QWidget(badge)
        hover_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        hover_anchor.setFixedSize(
            WORK_OVERLAY_COMPLETED_BADGE_SIZE,
            WORK_OVERLAY_COMPLETED_BADGE_SIZE,
        )
        hover_anchor.move(0, 0)
        hover_anchor.show()
        workdir_anchor = QWidget(badge)
        workdir_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        workdir_anchor.setFixedSize(WORK_OVERLAY_COMPLETED_BADGE_SIZE - 24, 40)
        workdir_anchor.move(12, 124)
        workdir_anchor.show()
        check_anchor = QWidget(badge)
        check_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        check_anchor.setFixedSize(68, 56)
        check_anchor.move(50, 18)
        check_anchor.show()
        record = {
            "kind": "completed",
            "item_id": item_id,
            "badge": badge,
            "hover_anchor": hover_anchor,
            "workdir_anchor": workdir_anchor,
            "check_anchor": check_anchor,
        }
        self._item_widgets.append(record)
        self._update_completed_badge(record, item)

    def _update_completed_badge(
        self,
        record: dict[str, Any],
        item: Mapping[str, object],
    ) -> None:
        badge = record["badge"]
        record["item"] = dict(item)
        badge.set_theme_tokens(self._theme_tokens)
        badge.set_item(item)
        pending = self._switch_pending_active_for_item(item)
        completed = self._switch_pending_completed_for_item(item)
        badge.set_switch_pending(
            pending,
            self._switch_pending_started_at if pending else 0.0,
            completed=completed,
            completed_at=self._switch_pending_completed_at if completed else 0.0,
        )
        self._completed_hover_anchors.append(record["hover_anchor"])
        workdir_text = _workdir_display_name(item)
        if workdir_text and _workdir_external_link_for_item(item):
            self._workdir_anchors.append((record["workdir_anchor"], dict(item)))
        self._completed_check_anchors.append((record["check_anchor"], dict(item)))

    def _build_item_card(self, item: Mapping[str, object]) -> None:
        card = QFrame(self._shell)
        card.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        card.setFixedWidth(WORK_OVERLAY_WIDTH)
        card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(
            WORK_OVERLAY_CARD_X_PADDING,
            WORK_OVERLAY_CARD_Y_PADDING,
            WORK_OVERLAY_CARD_X_PADDING,
            WORK_OVERLAY_CARD_Y_PADDING,
        )
        if _item_is_rest_reminder(item):
            # The rest card stacks hint text and an action row on top of the
            # normal body; tighten the row spacing so everything stays inside
            # the shared 110px card slot instead of growing the bubble.
            card_layout.setSpacing(max(0, WORK_OVERLAY_CARD_SPACING - 2))
        else:
            # Keep the three-line body closer to the header while preserving
            # the same gap on both sides of the body and footer.
            card_layout.setSpacing(max(0, WORK_OVERLAY_CARD_SPACING - 2))

        head_layout = QHBoxLayout()
        head_layout.setContentsMargins(0, 0, 0, 0)
        head_layout.setSpacing(8)

        header = QLabel("", card)
        header.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        header.setWordWrap(False)
        header.setTextFormat(text_format.PlainText)
        header.setAlignment(alignment.AlignVCenter | alignment.AlignLeft)
        header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold))
        head_layout.addWidget(header, 1)

        header_meta = QLabel("", card)
        header_meta.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        header_meta.setWordWrap(False)
        header_meta.setTextFormat(text_format.PlainText)
        header_meta.setAlignment(alignment.AlignVCenter | alignment.AlignRight)
        header_meta.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        header_meta.setFont(QFont("Microsoft YaHei UI", 7, QFont.Weight.DemiBold))
        header_meta.setVisible(False)
        head_layout.addWidget(header_meta, 0, alignment.AlignVCenter)

        close_anchor = QWidget(card)
        close_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        close_anchor.setFixedSize(WORK_OVERLAY_CLOSE_SIZE, WORK_OVERLAY_CLOSE_SIZE)
        head_layout.addWidget(close_anchor, 0, alignment.AlignVCenter)
        card_layout.addLayout(head_layout)

        body_host = QWidget(card)
        body_host.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        # Match the existing footer content width. The card's frame/margins
        # leave six extra pixels beyond WORK_OVERLAY_TEXT_WRAP_WIDTH; using the
        # same inner width keeps the scheme-B right control aligned with the
        # workdir/round slot.
        body_width = WORK_OVERLAY_TEXT_WRAP_WIDTH + 6
        body_host.setFixedWidth(body_width)
        body_host.setFixedHeight(1)
        body_host.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        detail = ScrollingFeedLabel(body_host)
        detail.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        detail.setWordWrap(True)
        detail.setTextFormat(text_format.PlainText)
        detail.setAlignment(alignment.AlignTop | alignment.AlignLeft)
        detail.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        detail.setFont(QFont("Microsoft YaHei UI", 8))
        detail.setFixedWidth(body_width)

        pinned_output = QLabel("", body_host)
        pinned_output.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        pinned_output.setWordWrap(True)
        pinned_output.setTextFormat(text_format.PlainText)
        pinned_output.setAlignment(alignment.AlignTop | alignment.AlignLeft)
        pinned_output.setFont(QFont("Microsoft YaHei UI", 8))
        pinned_output.setFixedWidth(body_width)
        pinned_output.hide()

        live_line = QWidget(body_host)
        live_line.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        live_line.setStyleSheet("QWidget { border: none; background: transparent; }")
        live_line.hide()

        live_symbol = QLabel("", live_line)
        live_symbol.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        live_symbol.setAlignment(alignment.AlignCenter)
        live_symbol.setFont(QFont("Microsoft YaHei UI", 8))
        live_symbol.setStyleSheet("QLabel { border: none; background: transparent; color: #9CCBFF; }")

        live_text = QLabel("", live_line)
        live_text.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        live_text.setWordWrap(False)
        live_text.setTextFormat(text_format.PlainText)
        live_text.setAlignment(alignment.AlignVCenter | alignment.AlignLeft)
        live_text.setFont(QFont("Microsoft YaHei UI", 8))
        live_text.setStyleSheet("QLabel { border: none; background: transparent; color: #8492A6; }")

        feed_action_label = QLabel("", body_host)
        feed_action_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        feed_action_label.setAlignment(alignment.AlignCenter)
        feed_action_label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.DemiBold))
        feed_action_label.hide()

        feed_action_anchor = QWidget(body_host)
        feed_action_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        feed_action_anchor.hide()

        body_host.setFixedHeight(1)
        card_layout.addWidget(body_host)

        rest_hint = QLabel("", card)
        rest_hint.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        rest_hint.setWordWrap(False)
        rest_hint.setTextFormat(text_format.PlainText)
        rest_hint.setAlignment(alignment.AlignTop | alignment.AlignLeft)
        rest_hint.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        rest_hint.setFont(QFont("Microsoft YaHei UI", 7))
        rest_hint.setFixedWidth(WORK_OVERLAY_TEXT_WRAP_WIDTH)
        rest_hint.setFixedHeight(rest_hint.fontMetrics().height() + 4)
        rest_hint.setVisible(False)
        card_layout.addWidget(rest_hint)

        footer_container = QWidget(card)
        footer_container.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        footer_layout = QHBoxLayout(footer_container)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)

        status_label = ShimmerTextLabel("", footer_container, base_color="#8492A6")
        status_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        status_label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
        status_label.setSingleLine(True)
        status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        status_label.setMinimumWidth(1)
        footer_layout.addWidget(status_label, 1, alignment.AlignVCenter)

        rest_actions_row = QWidget(card)
        rest_actions_row.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        rest_row_layout = QHBoxLayout(rest_actions_row)
        rest_row_layout.setContentsMargins(0, 0, 0, 0)
        rest_row_layout.setSpacing(4)
        rest_actions_row.setFixedHeight(24)

        secondary_action_label = QLabel("", rest_actions_row)
        secondary_action_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        secondary_action_label.setTextFormat(text_format.PlainText)
        secondary_action_label.setAlignment(alignment.AlignCenter)
        secondary_action_label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.DemiBold))
        secondary_action_label.setFixedHeight(24)
        secondary_action_label.setVisible(False)
        rest_row_layout.addWidget(secondary_action_label, 0)

        extra_rest_action_labels: list[QLabel] = []
        for _ in range(4):
            action_label = QLabel("", rest_actions_row)
            action_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            action_label.setTextFormat(text_format.PlainText)
            action_label.setAlignment(alignment.AlignCenter)
            action_label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.DemiBold))
            action_label.setFixedHeight(24)
            action_label.setVisible(False)
            rest_row_layout.addWidget(action_label, 0)
            extra_rest_action_labels.append(action_label)

        primary_action_label = QLabel("", rest_actions_row)
        primary_action_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        primary_action_label.setTextFormat(text_format.PlainText)
        primary_action_label.setAlignment(alignment.AlignCenter)
        primary_action_label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
        primary_action_label.setFixedHeight(24)
        primary_action_label.setVisible(False)
        rest_row_layout.addWidget(primary_action_label, 0)

        round_badge = QLabel("", footer_container)
        round_badge.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        round_badge.setTextFormat(text_format.PlainText)
        round_badge.setAlignment(alignment.AlignCenter)
        round_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        round_badge.setFont(QFont("Microsoft YaHei UI", 6, QFont.Weight.Bold))
        round_badge.setFixedHeight(18)
        round_badge.setVisible(False)

        workdir_label = QLabel("", footer_container)
        workdir_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        workdir_label.setWordWrap(False)
        workdir_label.setTextFormat(text_format.PlainText)
        workdir_label.setAlignment(alignment.AlignVCenter | alignment.AlignRight)
        workdir_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        workdir_label.setMinimumWidth(0)
        workdir_label.setMaximumWidth(WORK_OVERLAY_WORKDIR_FOOTER_WIDTH)
        workdir_label.setFont(QFont("Microsoft YaHei UI", 7))
        workdir_label.setFixedWidth(1)
        workdir_label.setStyleSheet(
            "QLabel {"
            "color: #5E6A78;"
            "border: none;"
            "background: transparent;"
            "}"
        )
        footer_height = max(
            18,
            status_label.fontMetrics().height() + 4,
            workdir_label.fontMetrics().height() + 4,
        )
        footer_container.setFixedHeight(footer_height)
        status_label.setFixedHeight(footer_height)
        workdir_label.setFixedHeight(footer_height)
        footer_layout.addWidget(workdir_label, 0, alignment.AlignVCenter)
        footer_layout.addWidget(round_badge, 0, alignment.AlignVCenter)

        card_layout.addWidget(footer_container)
        card_layout.addWidget(rest_actions_row)
        switch_overlay = CardSwitchPendingOverlayWidget(card)

        # Invisible per-row click targets over the activity feed body. Their
        # geometry tracks the detail label rows and is refreshed in the
        # interactive-window reposition pass after the layout settles.
        feed_anchors: list[QWidget] = []
        for _ in range(WORK_OVERLAY_BODY_MAX_LINES):
            feed_anchor = QWidget(body_host)
            feed_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            feed_anchor.hide()
            feed_anchors.append(feed_anchor)

        record = {
            "kind": "card",
            "item_id": _item_id(item),
            "card": card,
            "header": header,
            "header_meta": header_meta,
            "detail": detail,
            "body_host": body_host,
            "pinned_output": pinned_output,
            "live_line": live_line,
            "live_symbol": live_symbol,
            "live_text": live_text,
            "rest_hint": rest_hint,
            "footer_container": footer_container,
            "rest_actions_row": rest_actions_row,
            "rest_actions_layout": rest_row_layout,
            "status_label": status_label,
            "secondary_action_label": secondary_action_label,
            "primary_action_label": primary_action_label,
            "rest_action_labels": [
                secondary_action_label,
                primary_action_label,
                *extra_rest_action_labels,
            ],
            "round_badge": round_badge,
            "workdir_label": workdir_label,
            "switch_overlay": switch_overlay,
            "close_anchor": close_anchor,
            "feed_anchors": feed_anchors,
            "feed_action_anchor": feed_action_anchor,
            "feed_action_label": feed_action_label,
            "feed_rows_meta": [],
        }
        self._item_widgets.append(record)
        self._update_item_card(record, item)

    def _update_item_card(
        self,
        record: dict[str, Any],
        item: Mapping[str, object],
        *,
        collect_anchors: bool = True,
    ) -> None:
        item = _work_overlay_item_with_live_elapsed_text(item)
        record["item"] = dict(item)
        status = str(item.get("status") or "")
        system_action = _item_is_system_action(item)
        system_notice = _item_is_system_notice(item)
        background_usage = _item_is_background_usage(item)
        rest_reminder = _item_is_rest_reminder(item)
        # Partial same-session refreshes can omit ``current``. Keep the blue
        # left accent attached to the established session identity until an
        # explicit selection replaces it or its card disappears.
        current = _overlay_item_is_stably_current(
            item,
            getattr(self, "_stable_current_session_id", ""),
        )
        accent, pill_bg, card_bg, border_color = _color_for(
            status,
            self._theme_tokens,
        )
        current_border_color = (
            str(self._theme_tokens.get("accent") or accent)
            if current
            else border_color
        )
        feed_rows: list[dict[str, str]] = []
        peeking = False
        item_id = _item_id(item)
        if not (
            system_action
            or system_notice
            or background_usage
            or rest_reminder
            or status == "draft"
        ):
            feed_rows = self._feed_rows_for_item(record, item)
            peeking = bool(feed_rows) and item_id in getattr(self, "_feed_peek_item_ids", set())
        theme = self._theme_tokens
        elapsed_text = (
            ""
            if system_action or system_notice or background_usage or rest_reminder
            else str(item.get("elapsedText") or "").strip() or "--"
        )
        header_text = (
            str(item.get("title") or "Codex HUD")
            if system_notice
            else (
                str(item.get("title") or "休息提醒")
                if rest_reminder
                else (
                    str(item.get("statusLabel") or "Codex App 后台任务：未知后台任务")
                    if background_usage
                    else _work_overlay_header_text(
                        str(item.get("startedAt") or ""),
                        elapsed_text,
                        str(item.get("title") or "Codex 工作"),
                        title_limit=self._header_title_limit,
                    )
                )
            )
        )
        if rest_reminder and str(item.get("headerElapsed") or "").strip():
            header_text = " · ".join(
                part
                for part in (
                    header_text,
                    str(item.get("headerElapsed") or "").strip(),
                )
                if part
            )

        card = record["card"]
        if peeking:
            # 回看输出：dashed accent border marks the temporary output view.
            card.setStyleSheet(
                "QFrame {"
                f"background-color: {card_bg};"
                f"border: 1px dashed {accent};"
                f"border-left: {2 if current else 1}px solid {current_border_color};"
                "border-radius: 10px;"
                "}"
            )
        else:
            card.setStyleSheet(
                "QFrame {"
                f"background-color: {card_bg};"
                f"border: 1px solid {border_color};"
                f"border-left: {2 if current else 1}px solid {current_border_color};"
                "border-radius: 10px;"
                "}"
            )

        header = record["header"]
        header.setText(header_text)
        header.setStyleSheet(
            "QLabel {"
            f"color: {accent if status in {'recent', 'background_usage'} else _theme_mix(theme['text'], theme['muted'], 0.36, fallback=theme['text'])};"
            "border: none;"
            "background: transparent;"
            "}"
        )

        close_anchor = record["close_anchor"]
        close_anchor.setVisible(not rest_reminder)

        header_meta = record.get("header_meta")
        if rest_reminder:
            header_meta_text = str(item.get("headerMeta") or "").strip()
        else:
            incoming_model_name = _compact_work_text(item.get("modelName"), 48)
            if incoming_model_name:
                record["stable_model_name"] = incoming_model_name
            header_meta_text = str(record.get("stable_model_name") or "").strip()
        if isinstance(header_meta, QLabel) and header_meta_text:
            header_meta.setText(header_meta_text)
            header_meta.setStyleSheet(
                "QLabel {"
                f"color: {theme['muted']};"
                "border: none;"
                "background: transparent;"
                "padding: 0;"
                "}"
            )
            header_meta.adjustSize()
            header_meta.setFixedWidth(header_meta.sizeHint().width())
            header_meta.setToolTip(header_meta_text if not rest_reminder else "")
            header_meta.setVisible(True)
        elif isinstance(header_meta, QLabel):
            header_meta.setText("")
            header_meta.setToolTip("")
            header_meta.setVisible(False)

        round_badge = record["round_badge"]
        round_index = max(0, int(item.get("roundIndex") or 0))
        badge_visible = not rest_reminder and status != "recent" and round_index > 0
        if badge_visible:
            round_badge_theme = _round_badge_palette(status, theme)
            round_badge.setText(str(round_index))
            round_badge.setToolTip(f"当前第 {round_index} 轮")
            round_badge.setStyleSheet(
                "QLabel {"
                f"color: {round_badge_theme['text']};"
                f"background-color: {round_badge_theme['background']};"
                f"border: 1px solid {round_badge_theme['border']};"
                "border-radius: 9px;"
                "padding: 0 6px;"
                "}"
            )
            round_badge.adjustSize()
            round_badge.setFixedWidth(max(18, round_badge.sizeHint().width()))
            round_badge.setVisible(True)
        else:
            round_badge.setText("")
            round_badge.setToolTip("")
            round_badge.setFixedWidth(18)
            round_badge.setVisible(False)

        if background_usage:
            feature_text = str(item.get("title") or "未知后台任务").strip()
            model_text = str(item.get("modelName") or "").strip()
            progress_text = str(item.get("progress") or item.get("detail") or "").strip()
            secondary = " · ".join(
                part for part in (model_text, progress_text) if part
            )
            body_text = "\n".join(
                part for part in (feature_text, secondary) if part
            )
        else:
            body_text = str(item.get("lastText") or item.get("detail") or "").strip()
        detail = record["detail"]
        feed_mode = str(feed_rows[0].get("mode") or "summary") if feed_rows else ""
        if feed_rows and peeking:
            # Live activity feed: pre-elided single lines so each row keeps a
            # stable click band without touching the body layout constants.
            action_width = 22
            metrics = QFontMetrics(detail.font())
            detail_lines = [
                metrics.elidedText(
                    str(row.get("text") or ""),
                    Qt.TextElideMode.ElideRight,
                    max(
                        1,
                        WORK_OVERLAY_TEXT_WRAP_WIDTH
                        - (action_width if index == len(feed_rows) - 1 else 0),
                    ),
                )
                for index, row in enumerate(feed_rows)
            ]
            detail_text = "\n".join(detail_lines)
            detail.setToolTip(
                "\n".join(
                    str(row.get("tooltip") or "")
                    for row in feed_rows
                    if row.get("tooltip")
                )
            )
        elif feed_rows and feed_mode == "execution":
            # When the Codex turn is inside a collapsed tool group, show the
            # group's latest concrete instructions in the body. The footer
            # carries the group heading; stale assistant text stays hidden
            # until a fresh assistant message arrives.
            detail_text = "\n".join(str(row.get("text") or "") for row in feed_rows)
            detail.setToolTip(
                "\n".join(
                    str(row.get("tooltip") or "")
                    for row in feed_rows
                    if row.get("tooltip")
                )
            )
        elif feed_rows:
            # Scheme B: preserve the latest assistant output and append one
            # truthful execution summary line. Full rows appear only after
            # the user activates that summary.
            summary_text = str(feed_rows[0].get("text") or "")
            output_text = self._multiline_elided_text(
                body_text,
                font=detail.font(),
                width=WORK_OVERLAY_TEXT_WRAP_WIDTH,
                max_lines=2,
            )
            summary_text = self._multiline_elided_text(
                summary_text,
                font=detail.font(),
                width=max(1, WORK_OVERLAY_TEXT_WRAP_WIDTH - 52),
                max_lines=1,
            )
            detail_text = "\n".join(
                part for part in (output_text, summary_text) if part
            )
            detail.setToolTip(
                "\n".join(
                    str(row.get("tooltip") or "")
                    for row in feed_rows
                    if row.get("tooltip")
                )
            )
        else:
            detail_text = self._multiline_elided_text(
                body_text,
                font=detail.font(),
                width=WORK_OVERLAY_TEXT_WRAP_WIDTH,
            )
            if peeking:
                detail.setToolTip("执行详情 · 点击收起")
            else:
                detail.setToolTip(
                    body_text if detail_text and detail_text != body_text else ""
                )
        if isinstance(detail, ScrollingFeedLabel):
            if peeking:
                detail.set_scrolling_lines(detail_text.splitlines())
            else:
                detail.clear_scrolling_lines()
                detail.setText(detail_text)
        else:
            detail.setText(detail_text)
        detail_height = self._wrapped_label_height(detail, WORK_OVERLAY_TEXT_WRAP_WIDTH)
        detail.setFixedHeight(detail_height)
        record["feed_rows_meta"] = list(feed_rows)
        live_body_text = _overlay_execution_live_summary(item) if feed_rows else ""
        scheme_b_enabled = not (
            system_action
            or system_notice
            or background_usage
            or rest_reminder
            or status == "draft"
        )
        if not scheme_b_enabled:
            record["feed_rows_meta"] = []
            self._sync_scheme_b_body(
                record,
                mode="legacy",
                expanded=False,
                body_text=body_text,
                live_text="",
                detail_text=detail_text,
            )
        elif feed_rows and not peeking:
            live_symbol = record.get("live_symbol")
            if isinstance(live_symbol, QLabel):
                live_symbol.setText(
                    "!"
                    if any(str(row.get("kind") or "") == "wait" for row in feed_rows)
                    else "×"
                    if any(str(row.get("kind") or "") in {"fail", "failed"} for row in feed_rows)
                    else "…"
                )
            self._sync_scheme_b_body(
                record,
                mode=feed_mode or "summary",
                expanded=False,
                body_text=body_text,
                live_text=live_body_text,
                detail_text=detail_text,
            )
        elif feed_rows and peeking:
            self._sync_scheme_b_body(
                record,
                mode="expanded",
                expanded=True,
                body_text=body_text,
                live_text="",
                detail_text=detail_text,
            )
        else:
            self._sync_scheme_b_body(
                record,
                mode="output",
                expanded=False,
                body_text=body_text,
                live_text="",
                detail_text=detail_text,
            )
        detail.setStyleSheet(
            "QLabel {"
            f"color: {theme['requestText']};"
            "border: none;"
            "background: transparent;"
            "}"
        )
        self._sync_feed_action_label(record, expanded=peeking)
        self._sync_feed_anchor_geometry(record)

        rest_hint = record["rest_hint"]
        rest_hint_text = (
            str(item.get("restHint") or "").strip() if rest_reminder else ""
        )
        rest_hint.setText(rest_hint_text)
        rest_hint.setToolTip("")
        rest_hint.setStyleSheet(
            "QLabel {"
            f"color: {theme['muted']};"
            "border: none;"
            "background: transparent;"
            "}"
        )
        rest_hint.setVisible(bool(rest_hint_text))

        workdir_text = _workdir_display_name(item)
        workdir_footer_text = _workdir_footer_display_name(item, limit=96)
        full_workdir = str(item.get("workdir") or "").strip()
        workdir_clickable = _workdir_clickable_for_item(item)
        status_text = (
            " · ".join(
                part
                for part in (
                    (
                        f"{str(item.get('tokensText') or '').strip()} tokens"
                        if str(item.get("tokensText") or "").strip()
                        else ""
                    ),
                    str(item.get("costText") or "").strip(),
                )
                if part
            )
            if background_usage
            else str(item.get("statusText") or item.get("statusLabel") or "").strip()
        )
        activity_text, activity_tooltip = self._activity_step_display_text(record, item)
        footer_kind = _overlay_footer_selection(item)[0]
        if activity_text and not (
            system_action
            or system_notice
            or background_usage
            or rest_reminder
            or status == "draft"
        ):
            status_text = activity_text
        # 紧凑休息档：提示行 + 按钮行已占满共享的 110px 卡片槽，底部状态行让位，
        # 按钮因此完整可见，气泡高度也与普通会话气泡保持一致。
        if rest_reminder and rest_hint_text:
            status_text = ""
        footer_container = record["footer_container"]
        footer_container.setVisible(bool(status_text or workdir_footer_text))

        status_label = record["status_label"]
        if status_text and not rest_reminder:
            if footer_kind == "wait" or status == "waiting_user":
                status_text_color = _color_for("waiting_user", self._theme_tokens)[0]
            elif footer_kind == "failed":
                status_text_color = _color_for("error", self._theme_tokens)[0]
            elif status in {"recent", "error", "background_usage"}:
                status_text_color = accent
            else:
                status_text_color = theme["muted"]
            footer_status_text = _compact_work_text(status_text, 240)
            status_label.setText(self._footer_display_text(status_label, footer_status_text))
            status_label.setToolTip(activity_tooltip or status_text)
            status_label.setBaseColor(status_text_color)
            status_label.setShimmerEnabled(
                not system_notice
                and not rest_reminder
                and status not in {"recent", "background_usage", "draft"}
            )
            status_label.setVisible(True)
        else:
            status_label.setText("")
            status_label.setToolTip("")
            status_label.setShimmerEnabled(False)
            status_label.setVisible(False)

        rest_actions = (
            [dict(action) for action in item.get("restActions", []) if isinstance(action, Mapping)]
            if rest_reminder
            else []
        )
        action_labels = [
            label for label in record.get("rest_action_labels", [])
            if isinstance(label, QLabel)
        ]
        # Keep every rest action in one compact horizontal bar. Secondary actions
        # stay on the left; the highlighted primary action is pushed to the right.
        rest_actions_row = record.get("rest_actions_row")
        rest_actions_layout = record.get("rest_actions_layout")
        if isinstance(rest_actions_row, QWidget):
            rest_actions_row.setVisible(bool(rest_actions))
        secondary_labels: list[QLabel] = []
        primary_label: QLabel | None = None
        for index, label in enumerate(action_labels):
            action = rest_actions[index] if index < len(rest_actions) else None
            if action is None:
                label.setText("")
                label.setVisible(False)
                continue
            primary = bool(action.get("primary"))
            label.setText(str(action.get("label") or ""))
            label.setMinimumWidth(36)
            label.setMaximumWidth(92)
            label.setStyleSheet(
                "QLabel {"
                f"color: {theme['surface'] if primary else theme['text']};"
                f"background-color: {accent if primary else pill_bg};"
                f"border: 1px solid {accent if primary else border_color};"
                "border-radius: 7px;"
                "padding: 0 5px;"
                "}"
            )
            label.adjustSize()
            label.setFixedHeight(24)
            label.setFixedWidth(max(32, min(88, label.sizeHint().width() + 4)))
            label.setVisible(True)
            if primary:
                primary_label = label
            else:
                secondary_labels.append(label)
        if isinstance(rest_actions_layout, QHBoxLayout):
            while rest_actions_layout.count():
                rest_actions_layout.takeAt(0)
            for label in secondary_labels:
                rest_actions_layout.addWidget(label, 0)
            if primary_label is not None:
                rest_actions_layout.addStretch(1)
                rest_actions_layout.addWidget(primary_label, 0)

        workdir_label = record["workdir_label"]
        if workdir_footer_text and not rest_reminder:
            footer_width = self._workdir_footer_width(
                workdir_label,
                workdir_footer_text,
            )
            workdir_label.setFixedWidth(footer_width)
            workdir_label.setText(
                QFontMetrics(workdir_label.font()).elidedText(
                    workdir_footer_text,
                    Qt.TextElideMode.ElideLeft,
                    max(1, footer_width - 4),
                )
            )
            workdir_label.setToolTip((full_workdir or workdir_text) if workdir_clickable else "")
            workdir_label.setStyleSheet(
                "QLabel {"
                f"color: {theme['info'] if workdir_clickable else theme['requestMuted']};"
                "border: none;"
                "background: transparent;"
                "}"
            )
            workdir_label.setVisible(True)
        else:
            workdir_label.setText("")
            workdir_label.setToolTip("")
            workdir_label.setFixedWidth(1)
            workdir_label.setVisible(False)

        if collect_anchors:
            self._card_hover_anchors.append(card)
            if system_action:
                self._system_action_anchors.append((status_label, dict(item)))
            elif rest_reminder:
                for index, action in enumerate(rest_actions):
                    if index >= len(action_labels):
                        continue
                    label = action_labels[index]
                    action_item = dict(item)
                    action_item["action"] = str(action.get("action") or "")
                    action_item["actionLabel"] = str(action.get("label") or "")
                    if "minutes" in action:
                        action_item["minutes"] = action.get("minutes")
                    self._rest_action_anchors.append((label, action_item))
            elif not system_notice or item.get("dismissible"):
                self._close_anchors.append(
                    (record["close_anchor"], dict(item), card_bg, pill_bg, accent)
                )
            if (
                not system_action
                and not system_notice
                and _workdir_external_link_for_item(item)
            ):
                self._workdir_anchors.append((record["workdir_label"], dict(item)))
            feed_rows_meta = record.get("feed_rows_meta") or []
            feed_row_anchors = getattr(self, "_feed_row_anchors", None)
            if (
                feed_rows_meta
                and isinstance(feed_row_anchors, list)
                and not (
                    system_action
                    or system_notice
                    or background_usage
                    or rest_reminder
                    or status == "draft"
                )
            ):
                feed_item_id = _item_id(item)
                feed_anchors = record.get("feed_anchors") or []
                for index, row in enumerate(feed_rows_meta):
                    if index >= len(feed_anchors):
                        break
                    if (
                        str(row.get("action") or "") != "copy_command"
                        or (
                            _item_id(item) not in getattr(self, "_feed_peek_item_ids", set())
                            and str(row.get("mode") or "") == "execution"
                        )
                    ):
                        feed_anchors[index].hide()
                        continue
                    feed_row_anchors.append(
                        (
                            feed_anchors[index],
                            {
                                "id": feed_item_id,
                                "feedAction": str(row.get("action") or ""),
                                "command": str(row.get("command") or ""),
                            },
                        )
                    )
                action_anchor = record.get("feed_action_anchor")
                action_label = record.get("feed_action_label")
                if (
                    isinstance(action_anchor, QWidget)
                    and isinstance(action_label, QLabel)
                    and not action_label.isHidden()
                ):
                    feed_row_anchors.append(
                        (
                            action_anchor,
                            {
                                "id": feed_item_id,
                                "feedAction": (
                                    "resume_feed"
                                    if feed_item_id in getattr(self, "_feed_peek_item_ids", set())
                                    else "peek_output"
                                ),
                                "command": "",
                            },
                        )
                    )
        switch_overlay = record.get("switch_overlay")
        if isinstance(switch_overlay, CardSwitchPendingOverlayWidget):
            pending = self._switch_pending_active_for_item(item)
            completed = self._switch_pending_completed_for_item(item)
            switch_overlay.setGeometry(card.rect())
            switch_overlay.set_switch_pending(
                pending,
                self._switch_pending_started_at if pending else 0.0,
                completed=completed,
                completed_at=self._switch_pending_completed_at if completed else 0.0,
            )
            if pending:
                switch_overlay.raise_()

    def _build_transition_card_widget(self, item: Mapping[str, object]) -> QFrame:
        card = QFrame(self._shell)
        card.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        card.setMinimumSize(1, 1)
        card.setMaximumSize(16777215, 16777215)
        card.resize(WORK_OVERLAY_WIDTH, WORK_OVERLAY_TRANSITION_CARD_HEIGHT)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(
            WORK_OVERLAY_CARD_X_PADDING,
            WORK_OVERLAY_CARD_Y_PADDING,
            WORK_OVERLAY_CARD_X_PADDING,
            WORK_OVERLAY_CARD_Y_PADDING,
        )
        card_layout.setSpacing(WORK_OVERLAY_CARD_SPACING)

        head_layout = QHBoxLayout()
        head_layout.setContentsMargins(0, 0, 0, 0)
        head_layout.setSpacing(8)

        header = QLabel("", card)
        header.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        header.setWordWrap(False)
        header.setTextFormat(text_format.PlainText)
        header.setAlignment(alignment.AlignVCenter | alignment.AlignLeft)
        header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold))
        head_layout.addWidget(header, 1)

        close_anchor = QWidget(card)
        close_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        close_anchor.setFixedSize(WORK_OVERLAY_CLOSE_SIZE, WORK_OVERLAY_CLOSE_SIZE)
        head_layout.addWidget(close_anchor, 0)
        card_layout.addLayout(head_layout)

        detail = QLabel("", card)
        detail.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        detail.setWordWrap(True)
        detail.setTextFormat(text_format.PlainText)
        detail.setAlignment(alignment.AlignTop | alignment.AlignLeft)
        detail.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        detail.setFont(QFont("Microsoft YaHei UI", 8))
        detail.setFixedWidth(WORK_OVERLAY_TEXT_WRAP_WIDTH)
        card_layout.addWidget(detail)

        rest_hint = QLabel("", card)
        rest_hint.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        rest_hint.setWordWrap(False)
        rest_hint.setTextFormat(text_format.PlainText)
        rest_hint.setAlignment(alignment.AlignTop | alignment.AlignLeft)
        rest_hint.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        rest_hint.setFont(QFont("Microsoft YaHei UI", 7))
        rest_hint.setFixedWidth(WORK_OVERLAY_TEXT_WRAP_WIDTH)
        rest_hint.setFixedHeight(rest_hint.fontMetrics().height() + 4)
        rest_hint.setVisible(False)
        card_layout.addWidget(rest_hint)

        footer_container = QWidget(card)
        footer_container.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        footer_layout = QHBoxLayout(footer_container)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)

        status_label = ShimmerTextLabel("", footer_container, base_color="#8492A6")
        status_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        status_label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
        status_label.setSingleLine(True)
        status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        status_label.setMinimumWidth(1)
        footer_layout.addWidget(status_label, 1, alignment.AlignVCenter)

        round_badge = QLabel("", footer_container)
        round_badge.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        round_badge.setTextFormat(text_format.PlainText)
        round_badge.setAlignment(alignment.AlignCenter)
        round_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        round_badge.setFont(QFont("Microsoft YaHei UI", 6, QFont.Weight.Bold))
        round_badge.setFixedHeight(18)
        round_badge.setVisible(False)

        workdir_label = QLabel("", footer_container)
        workdir_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        workdir_label.setWordWrap(False)
        workdir_label.setTextFormat(text_format.PlainText)
        workdir_label.setAlignment(alignment.AlignVCenter | alignment.AlignRight)
        workdir_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        workdir_label.setMinimumWidth(0)
        workdir_label.setMaximumWidth(WORK_OVERLAY_WORKDIR_FOOTER_WIDTH)
        workdir_label.setFont(QFont("Microsoft YaHei UI", 7))
        workdir_label.setFixedWidth(1)
        workdir_label.setStyleSheet(
            "QLabel {"
            "color: #5E6A78;"
            "border: none;"
            "background: transparent;"
            "}"
        )
        footer_height = max(
            18,
            status_label.fontMetrics().height() + 4,
            workdir_label.fontMetrics().height() + 4,
        )
        footer_container.setFixedHeight(footer_height)
        status_label.setFixedHeight(footer_height)
        workdir_label.setFixedHeight(footer_height)
        footer_layout.addWidget(workdir_label, 0, alignment.AlignVCenter)
        footer_layout.addWidget(round_badge, 0, alignment.AlignVCenter)
        card_layout.addWidget(footer_container)

        record = {
            "kind": "card",
            "item_id": _item_id(item),
            "card": card,
            "header": header,
            "detail": detail,
            "rest_hint": rest_hint,
            "footer_container": footer_container,
            "status_label": status_label,
            "round_badge": round_badge,
            "workdir_label": workdir_label,
            "close_anchor": close_anchor,
        }
        self._update_item_card(record, item, collect_anchors=False)
        return card

    def _widget_shell_rect(self, widget: QWidget) -> QRectF:
        top_left = widget.mapTo(self._shell, QPoint(0, 0))
        return QRectF(
            float(top_left.x()),
            float(top_left.y()),
            float(max(1, widget.width())),
            float(max(1, widget.height())),
        )

    def _record_for_item_kind(
        self,
        item_id: str,
        kind: str,
    ) -> dict[str, Any] | None:
        for record in self._item_widgets:
            if record.get("kind") == kind and str(record.get("item_id") or "") == item_id:
                return record
        return None

    def _record_visual_widget(self, record: Mapping[str, Any]) -> QWidget | None:
        widget = record.get("badge") if record.get("kind") == "completed" else record.get("card")
        return widget if isinstance(widget, QWidget) else None

    def _record_widget_for_kind(
        self,
        item_id: str,
        kind: str,
    ) -> QWidget | None:
        record = self._record_for_item_kind(item_id, kind)
        return self._record_visual_widget(record) if record is not None else None

    def _sync_record_arrays(
        self,
        visible_items: Sequence[Mapping[str, object]],
    ) -> None:
        self.circles = []
        self.rects = []
        for record, item in _matched_overlay_item_records(
            self._item_widgets,
            visible_items,
        ):
            kind = self._item_widget_kind(item)
            if kind == "completed":
                self.circles.append(record)
            else:
                self.rects.append(record)

    def _completed_record_geometry(
        self,
        index_from_right: int,
        completed_count: int,
    ) -> QRect:
        return self._completed_badge_geometry_for_slot(
            _completed_slot_rect(
                index_from_right,
                completed_count,
                layout_width=self._layout_width,
                side=self._side,
            )
        )

    def _card_record_geometry(
        self,
        item_id: str,
        visible_items: Sequence[Mapping[str, object]],
    ) -> QRect:
        rect = _find_item_rect(
            visible_items,
            item_id,
            "card",
            layout_width=self._layout_width,
            side=self._side,
        )
        return QRect(
            int(round(rect[0])),
            int(round(rect[1])),
            max(1, int(round(rect[2]))),
            max(1, int(round(rect[3]))),
        )

    def _card_slot_record_geometry(
        self,
        index_from_top: int,
        completed_count: int,
    ) -> QRect:
        return self._card_geometry_for_slot(
            _card_slot_rect(
                index_from_top,
                completed_count,
                layout_width=self._layout_width,
                side=self._side,
            )
        )

    def _card_geometry_for_slot(self, slot_rect: OverlayRect) -> QRect:
        return QRect(
            int(round(slot_rect[0])),
            int(round(slot_rect[1])),
            WORK_OVERLAY_WIDTH,
            WORK_OVERLAY_TRANSITION_CARD_HEIGHT,
        )

    def _qrect_from_rectf(self, rect: QRectF) -> QRect:
        return QRect(
            int(round(rect.x())),
            int(round(rect.y())),
            max(1, int(round(rect.width()))),
            max(1, int(round(rect.height()))),
        )

    def _badge_geometry_for_slot(
        self,
        widget: QWidget,
        slot_rect: OverlayRect,
    ) -> QRect:
        return QRect(
            int(round(slot_rect[0])),
            widget.y(),
            max(1, widget.width()),
            max(1, widget.height()),
        )

    def _completed_badge_geometry_for_slot(self, slot_rect: OverlayRect) -> QRect:
        return QRect(
            int(round(slot_rect[0])),
            int(round(slot_rect[1])),
            WORK_OVERLAY_COMPLETED_BADGE_SIZE,
            WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
        )

    def _sync_item_widget_geometries(
        self,
        visible_items: Sequence[Mapping[str, object]],
    ) -> None:
        self._sync_record_arrays(visible_items)
        completed_count = len(self.circles)
        for list_idx, record in enumerate(self.circles):
            widget = self._record_visual_widget(record)
            if widget is None:
                continue
            index_from_right = completed_count - 1 - list_idx
            widget.setGeometry(
                self._completed_record_geometry(index_from_right, completed_count)
            )
            widget.show()

        for record in self.rects:
            widget = self._record_visual_widget(record)
            if widget is None:
                continue
            widget.setGeometry(
                self._card_record_geometry(
                    str(record.get("item_id") or ""),
                    visible_items,
                )
            )
            switch_overlay = record.get("switch_overlay")
            if isinstance(switch_overlay, CardSwitchPendingOverlayWidget):
                switch_overlay.setGeometry(widget.rect())
                if switch_overlay.isVisible():
                    switch_overlay.raise_()
            widget.show()

    def _sync_overlay_geometry(self) -> None:
        layout_width = max(WORK_OVERLAY_WIDTH, int(self._layout_width))
        content_height = max(
            1,
            self._transition_required_height,
            _overlay_items_required_height(
                self._layout_items,
                layout_width=layout_width,
                side=self._side,
            ),
        )
        screen = self._qt_app.primaryScreen()
        available_geometry = screen.availableGeometry() if screen is not None else self.geometry()
        screen_geometry = screen.geometry() if screen is not None else available_geometry
        x = _overlay_window_x(
            available_geometry.left(),
            available_geometry.right(),
            layout_width,
            WORK_OVERLAY_MARGIN,
            self._side,
        )
        y = _overlay_window_top_y(screen_geometry.top())
        max_height = max(1, available_geometry.bottom() - y - WORK_OVERLAY_MARGIN + 1)
        content_height = min(content_height, max_height)
        self.setGeometry(x, y, layout_width, content_height)
        self._shell.setMinimumSize(0, 0)
        self._shell.setMaximumSize(16777215, 16777215)
        self._shell.setGeometry(0, 0, layout_width, content_height)
        self._shell.move(0, 0)
        if not self.isVisible():
            self.show()
        self.raise_()
        QTimer.singleShot(0, self._reposition_interactive_windows_if_settled)
        # QWidget visibility and geometry can lag one event turn behind the
        # rebuilt shell on Windows. Retry once after the layout reaches the
        # native window system so current anchors always get their HWNDs.
        QTimer.singleShot(48, self._reposition_interactive_windows_if_settled)

    @staticmethod
    def _system_notice_structure_change_only(
        old_items: Sequence[Mapping[str, object]],
        new_items: Sequence[Mapping[str, object]],
    ) -> bool:
        old_without_notice = [
            item for item in old_items if not _item_is_system_notice(item)
        ]
        new_without_notice = [
            item for item in new_items if not _item_is_system_notice(item)
        ]
        if [
            (
                str(item.get("id") or ""),
                "completed" if _item_is_completed(item) else "card",
            )
            for item in old_without_notice
        ] != [
            (
                str(item.get("id") or ""),
                "completed" if _item_is_completed(item) else "card",
            )
            for item in new_without_notice
        ]:
            return False
        old_notices = [item for item in old_items if _item_is_system_notice(item)]
        new_notices = [item for item in new_items if _item_is_system_notice(item)]
        if len(old_notices) > 1 or len(new_notices) > 1:
            return False
        if bool(old_notices) == bool(new_notices):
            return False
        return True

    def _remove_system_notice_record(self, item_id: str) -> None:
        record = self._record_for_item_kind(item_id, "card")
        if record is None:
            return
        try:
            self._item_widgets.remove(record)
        except ValueError:
            return
        widget = record.get("card")
        if not isinstance(widget, QWidget):
            return
        widget.hide()
        widget.deleteLater()

    def render_items(
        self,
        items: Sequence[Mapping[str, object]],
        *,
        system_action: Mapping[str, object] | None = None,
        system_notice: Mapping[str, object] | None = None,
        rest_reminder: Mapping[str, object] | None = None,
    ) -> None:
        if system_action is not None:
            self._system_action = _normalized_system_action(system_action)
        if system_notice is not None:
            self._system_notice = _normalized_system_notice(system_notice)
        if rest_reminder is not None:
            self._rest_reminder = _normalized_rest_reminder(rest_reminder)
        if self._rest_reminder is not None:
            if not self._rest_countdown_timer.isActive():
                self._rest_countdown_timer.start()
        else:
            self._rest_countdown_timer.stop()
        if self._transition_in_progress:
            self._raw_items = list(items)
            self._sync_live_elapsed_timer(self._raw_items)
            return
        self._raw_items = list(items)
        ordered_items = _ordered_overlay_items(self._raw_items)
        visible_items = _visible_overlay_items(
            ordered_items,
            self._dismissed_instances,
            item_limit=self._item_limit,
        )
        if self._system_action is not None:
            visible_items = [
                _system_action_overlay_item(self._system_action),
                *[
                    item
                    for item in visible_items
                    if not _item_is_system_action(item)
                ],
            ]
        if self._system_notice is not None:
            notice_item = _system_notice_overlay_item(self._system_notice)
            notice_items = (
                _visible_overlay_items([notice_item], self._dismissed_instances, item_limit=1)
                if notice_item.get("dismissible") else [notice_item]
            )
            visible_items = [
                *notice_items,
                *[
                    item
                    for item in visible_items
                    if not _item_is_system_notice(item)
                ],
            ]
        if self._rest_reminder is not None:
            rest_item = _rest_reminder_overlay_item(self._rest_reminder)
            if rest_item:
                visible_items = [
                    rest_item,
                    *[item for item in visible_items if not _item_is_rest_reminder(item)],
                ]
        self._sync_switch_pending(visible_items)
        self._sync_live_elapsed_timer(visible_items)
        if not visible_items:
            if self.isVisible():
                now = time.time()
                if self._empty_since <= 0.0:
                    self._empty_since = now
                    return
                if (now - self._empty_since) < WORK_OVERLAY_EMPTY_GRACE_SECONDS:
                    return
            self._empty_since = 0.0
            self._last_payload_signature = "[]"
            self._last_structure_signature = ""
            self._layout_width = WORK_OVERLAY_WIDTH
            self._layout_items = []
            self._clear_shell()
            self.hide_overlay()
            self._previous_visible_items = []
            self._stable_current_session_id = ""
            return
        self._empty_since = 0.0
        self._stable_current_session_id = _next_stable_current_session_id(
            visible_items,
            getattr(self, "_stable_current_session_id", ""),
        )
        completed_count = sum(1 for item in visible_items if _item_is_completed(item))
        self._layout_width = max(
            WORK_OVERLAY_WIDTH,
            _completed_badge_row_width(completed_count),
        )
        visible_completed_ids = {
            _item_id(item)
            for item in visible_items
            if _item_id(item) and _item_is_completed(item)
        }
        self._settled_completed_intro_ids.intersection_update(visible_completed_ids)
        payload_signature = _overlay_payload_signature(
            visible_items,
            self._theme_tokens,
        )
        if payload_signature == self._last_payload_signature:
            return
        transition = (
            _detect_transition(self._previous_visible_items, visible_items)
            if WORK_OVERLAY_QT_TRANSITION_ANIMATIONS_ENABLED
            else None
        )
        if transition is not None:
            item_id = _detect_transition_item_id(self._previous_visible_items, visible_items)
            if item_id:
                previous_items = list(self._previous_visible_items)
                transition_items = _defer_other_transition_items(
                    previous_items,
                    visible_items,
                    item_id,
                )
                self._layout_width = _transition_layout_width(
                    previous_items,
                    transition_items,
                )
                self._layout_items = list(previous_items)
                self._sync_overlay_geometry()
                self._sync_item_widget_geometries(previous_items)
                # A transition may fail synchronously while constructing its
                # temporary card. Commit the next visual state first so its
                # cleanup render cannot re-enter the same transition.
                self._previous_visible_items = list(transition_items)
                self._start_transition(
                    transition,
                    item_id,
                    previous_items,
                    transition_items,
                )
                return
        previous_visible_items = list(self._previous_visible_items)
        self._previous_visible_items = list(visible_items)
        self._last_payload_signature = payload_signature
        self._layout_items = list(visible_items)

        structure_signature = json.dumps(
            [
                f"{self._item_identity(item, index)}:{self._item_widget_kind(item)}"
                for index, item in enumerate(visible_items)
            ],
            ensure_ascii=False,
        )
        rebuild = structure_signature != self._last_structure_signature
        record_items = _matched_overlay_item_records(
            self._item_widgets,
            visible_items,
        )
        if not rebuild and len(record_items) != len(visible_items):
            rebuild = True
        if rebuild and self._system_notice_structure_change_only(
            previous_visible_items,
            visible_items,
        ):
            old_notice = next(
                (
                    item
                    for item in previous_visible_items
                    if _item_is_system_notice(item)
                ),
                None,
            )
            new_notice = next(
                (item for item in visible_items if _item_is_system_notice(item)),
                None,
            )
            if old_notice is not None and new_notice is None:
                self._remove_system_notice_record(_item_id(old_notice))
            elif old_notice is None and new_notice is not None:
                self._build_item_widget(new_notice)
            self._last_structure_signature = structure_signature
            rebuild = False
            record_items = _matched_overlay_item_records(
                self._item_widgets,
                visible_items,
            )
        self._close_anchors.clear()
        self._workdir_anchors.clear()
        self._completed_check_anchors.clear()
        self._system_action_anchors.clear()
        self._rest_action_anchors.clear()
        self._card_hover_anchors.clear()
        self._completed_hover_anchors.clear()
        self._feed_row_anchors.clear()
        if rebuild:
            self._last_structure_signature = structure_signature
            self._clear_shell()
            completed_items = [
                item for item in visible_items if self._item_widget_kind(item) == "completed"
            ]
            active_items = [
                item for item in visible_items if self._item_widget_kind(item) != "completed"
            ]
            if completed_items:
                self._build_completed_row(completed_items)
            for item in active_items:
                self._build_item_widget(item)
        else:
            for record, item in record_items:
                self._update_item_widget(record, item)
        self._sync_overlay_geometry()
        self._sync_item_widget_geometries(visible_items)

__all__ = [
    'OverlayRenderingMixin',
]
