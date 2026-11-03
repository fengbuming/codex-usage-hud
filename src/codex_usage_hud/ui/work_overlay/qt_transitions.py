"""Qt transition, animation-phase, watchdog, and cleanup owner."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPoint,
    QPointF,
    QParallelAnimationGroup,
    QPauseAnimation,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSequentialAnimationGroup,
    Qt,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from .constants import (
    WORK_OVERLAY_COMPLETED_ANNIHILATION_MARGIN,
    WORK_OVERLAY_COMPLETED_ANNIHILATION_MS,
    WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
    WORK_OVERLAY_COMPLETED_BADGE_SIZE,
    WORK_OVERLAY_RESTORE_DESCEND_MS,
    WORK_OVERLAY_RESTORE_FADE_IN_MS,
    WORK_OVERLAY_RESTORE_FADE_OUT_MS,
    WORK_OVERLAY_RESTORE_SHIFT_MS,
    WORK_OVERLAY_TRANSITION_MOVE_MS,
    WORK_OVERLAY_TRANSITION_PAUSE_MS,
    WORK_OVERLAY_TRANSITION_SHIFT_MS,
    WORK_OVERLAY_TRANSITION_SHRINK_MS,
)
from .geometry import (
    _card_height_circle_rect_for_rect,
    _card_yield_delay_ms,
    _card_yield_rect_for_circle_path,
    _overlay_required_height_for_counts,
    _transition_hides_source_before_effect_reset,
    _transition_total_ms,
)
from .model import _item_id, _item_is_completed, _mark_item_dismissed
from .qt_visuals import CardToCompletedMorphWidget, CompletedBadgeWidget, EnergyRingAnnihilationWidget

widget_attrs = Qt.WidgetAttribute

class OverlayTransitionsMixin:

    def _animate_widget_geometry(
                self,
                widget: QWidget,
                target: QRect,
                duration_ms: int,
                easing: QEasingCurve.Type,
            ) -> QPropertyAnimation:
                animation = QPropertyAnimation(widget, b"geometry")
                animation.setStartValue(widget.geometry())
                animation.setEndValue(target)
                animation.setDuration(duration_ms)
                animation.setEasingCurve(easing)
                return animation

    def _animate_effect_opacity(
                self,
                effect: QGraphicsOpacityEffect,
                start: float,
                end: float,
                duration_ms: int,
            ) -> QPropertyAnimation:
                animation = QPropertyAnimation(effect, b"opacity")
                animation.setStartValue(float(start))
                animation.setEndValue(float(end))
                animation.setDuration(duration_ms)
                animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
                return animation

    def _build_annihilation_widget(self, source_widget: QWidget) -> EnergyRingAnnihilationWidget:
                source_top_left = source_widget.mapToGlobal(QPoint(0, 0))
                circle_size = float(
                    max(
                        1,
                        min(
                            source_widget.width(),
                            source_widget.height(),
                            WORK_OVERLAY_COMPLETED_BADGE_SIZE,
                        ),
                    )
                )
                max_radius = circle_size / 2.0 + float(WORK_OVERLAY_COMPLETED_ANNIHILATION_MARGIN)
                side = int(math.ceil(max_radius * 2.0 + 20.0))
                center_global = QPointF(
                    float(source_top_left.x()) + circle_size / 2.0,
                    float(source_top_left.y()) + circle_size / 2.0,
                )
                x = int(round(center_global.x() - side / 2.0))
                y = int(round(center_global.y() - side / 2.0))
                screen = self._qt_app.primaryScreen()
                if screen is not None:
                    available = screen.availableGeometry()
                    if side <= available.width():
                        x = max(available.left(), min(x, available.right() - side + 1))
                    if side <= available.height():
                        y = max(available.top(), min(y, available.bottom() - side + 1))
                center = QPointF(center_global.x() - float(x), center_global.y() - float(y))
                widget = EnergyRingAnnihilationWidget(max_radius=max_radius, center=center)
                widget.setGeometry(x, y, side, side)
                widget.setWindowOpacity(self.windowOpacity())
                widget.show()
                widget.raise_()
                return widget

    def _build_annihilation_animation(
                self,
                widget: EnergyRingAnnihilationWidget,
            ) -> QParallelAnimationGroup:
                duration_ms = WORK_OVERLAY_COMPLETED_ANNIHILATION_MS
                group = QParallelAnimationGroup(self)
                animation = QPropertyAnimation(widget, b"progress")
                animation.setStartValue(0.0)
                animation.setEndValue(1.0)
                animation.setDuration(duration_ms)
                animation.setEasingCurve(QEasingCurve.Type.OutCubic)
                group.addAnimation(animation)
                return group

    def _hide_transition_interactive_windows(self) -> None:
                for window in [
                    *self._close_windows,
                    *self._workdir_windows,
                    *self._completed_check_windows,
                    *self._system_action_windows,
                ]:
                    window.hide()

    def _transition_item_payload(
                self,
                item_id: str,
                old_items: Sequence[Mapping[str, object]],
                new_items: Sequence[Mapping[str, object]],
            ) -> Mapping[str, object]:
                for items in (new_items, old_items):
                    for item in items:
                        if _item_id(item) == item_id:
                            return item
                return {}

    def _add_noop_animation(
                self,
                group: QParallelAnimationGroup,
                duration_ms: int,
            ) -> None:
                group.addAnimation(QPauseAnimation(max(1, int(duration_ms)), self))

    def _start_transition_watchdog(self, duration_ms: int) -> None:
                self._transition_watchdog.start(max(500, int(duration_ms) + 900))

    def _touch_transition_watchdog(self, duration_ms: int) -> None:
                if self._transition_in_progress:
                    self._start_transition_watchdog(duration_ms)

    def _stop_transition_animation_group(self) -> None:
                group = self._transition_animation_group
                if group is None:
                    return
                try:
                    if group.state() != QAbstractAnimation.State.Stopped:
                        group.stop()
                    group.deleteLater()
                except RuntimeError:
                    pass
                self._transition_animation_group = None

    def _handle_transition_timeout(self) -> None:
                if self._transition_in_progress:
                    self._end_transition()

    def _transition_records_for_current_layout(
                self,
            ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
                circles = [record for record in self.circles if record.get("kind") == "completed"]
                rects = [record for record in self.rects if record.get("kind") == "card"]
                if circles or rects:
                    return list(circles), list(rects)
                return (
                    [record for record in self._item_widgets if record.get("kind") == "completed"],
                    [record for record in self._item_widgets if record.get("kind") == "card"],
                )

    def _set_transition_height_for_counts(
                self,
                completed_count: int,
                card_count: int,
                extra_rects: Sequence[QRect] = (),
            ) -> None:
                bottoms = [
                    float(
                        _overlay_required_height_for_counts(
                            completed_count,
                            card_count,
                            layout_width=self._layout_width,
                        )
                    )
                ]
                bottoms.extend(float(rect.y() + rect.height()) for rect in extra_rects)
                self._transition_required_height = max(
                    self._transition_required_height,
                    max(1, int(math.ceil(max(bottoms)))),
                )
                self._sync_overlay_geometry()

    def _start_completed_dismiss_transition(
                self,
                item: Mapping[str, object],
            ) -> None:
                item_id = _item_id(item)
                clicked_record = self._record_for_item_kind(item_id, "completed")
                source_widget = (
                    self._record_visual_widget(clicked_record)
                    if clicked_record is not None
                    else None
                )
                if not item_id or clicked_record is None or source_widget is None:
                    _mark_item_dismissed(self._dismissed_instances, item)
                    self._last_payload_signature = ""
                    self._last_structure_signature = ""
                    self.render_items(self._raw_items)
                    return

                try:
                    _mark_item_dismissed(self._dismissed_instances, item)
                    self._transition_in_progress = True
                    self._transition_type = "completed_dismiss"
                    self._transition_item_id = item_id
                    self._transition_started_at = time.monotonic()
                    self._hide_transition_interactive_windows()
                    self._transition_source_widget = source_widget
                    self._transition_hidden_widget = source_widget

                    effect = QGraphicsOpacityEffect(source_widget)
                    effect.setOpacity(1.0)
                    source_widget.setGraphicsEffect(effect)
                    source_widget.show()
                    source_widget.raise_()
                    self._transition_source_effect = effect

                    annihilation_widget = self._build_annihilation_widget(source_widget)
                    self._transition_annihilation_widget = annihilation_widget

                    duration_ms = WORK_OVERLAY_COMPLETED_ANNIHILATION_MS
                    self._start_transition_watchdog(duration_ms)
                    self._touch_transition_watchdog(duration_ms)
                    group = QParallelAnimationGroup(self)
                    group.addAnimation(
                        self._animate_effect_opacity(
                            effect,
                            1.0,
                            0.0,
                            duration_ms,
                        )
                    )
                    group.addAnimation(self._build_annihilation_animation(annihilation_widget))

                    circles, rects = self._transition_records_for_current_layout()
                    remaining_circles = [record for record in circles if record is not clicked_record]
                    completed_count_after = len(remaining_circles)
                    self._set_transition_height_for_counts(len(circles), len(rects))
                    self._set_transition_height_for_counts(completed_count_after, len(rects))
                    shift_delay_ms = max(0, duration_ms - WORK_OVERLAY_RESTORE_SHIFT_MS)
                    for list_idx, record in enumerate(remaining_circles):
                        widget = self._record_visual_widget(record)
                        if widget is None:
                            continue
                        index_from_right = completed_count_after - 1 - list_idx
                        sequence = QSequentialAnimationGroup(self)
                        if shift_delay_ms > 0:
                            sequence.addAnimation(QPauseAnimation(shift_delay_ms, self))
                        sequence.addAnimation(
                            self._animate_widget_geometry(
                                widget,
                                self._completed_record_geometry(
                                    index_from_right,
                                    completed_count_after,
                                ),
                                WORK_OVERLAY_RESTORE_SHIFT_MS,
                                QEasingCurve.Type.InOutQuad,
                            )
                        )
                        group.addAnimation(sequence)

                    group.finished.connect(lambda: self._finish_completed_dismiss(item))
                    self._transition_animation_group = group
                    group.start()
                except Exception:
                    self._end_transition()

    def _finish_completed_dismiss(self, item: Mapping[str, object]) -> None:
                _mark_item_dismissed(self._dismissed_instances, item)
                self._end_transition()

    def _start_completed_to_card_transition(
                self,
                item_id: str,
                old_items: list[Mapping[str, object]],
                new_items: list[Mapping[str, object]],
            ) -> None:
                try:
                    self._transition_in_progress = True
                    self._transition_type = "completed_to_card"
                    self._transition_item_id = item_id
                    self._transition_started_at = time.monotonic()
                    self._hide_transition_interactive_windows()

                    circles, rects = self._transition_records_for_current_layout()
                    clicked_record = self._record_for_item_kind(item_id, "completed")
                    source_widget = (
                        self._record_visual_widget(clicked_record)
                        if clicked_record is not None
                        else None
                    )
                    if clicked_record is None or source_widget is None:
                        self._end_transition()
                        return

                    staged_circles = list(circles)
                    staged_circles.remove(clicked_record)
                    staged_circles.append(clicked_record)
                    self._transition_source_widget = source_widget
                    self._transition_hidden_widget = source_widget
                    effect = QGraphicsOpacityEffect(source_widget)
                    effect.setOpacity(1.0)
                    source_widget.setGraphicsEffect(effect)
                    source_widget.show()
                    source_widget.raise_()
                    self._transition_source_effect = effect
                    self._set_transition_height_for_counts(len(circles), len(rects))
                    self._start_completed_to_card_phase1(
                        old_items,
                        new_items,
                        clicked_record,
                        staged_circles,
                        rects,
                    )
                except Exception:
                    self._end_transition()

    def _start_completed_to_card_phase1(
                self,
                old_items: list[Mapping[str, object]],
                new_items: list[Mapping[str, object]],
                clicked_record: dict[str, Any],
                staged_circles: list[dict[str, Any]],
                rects: list[dict[str, Any]],
            ) -> None:
                try:
                    duration_ms = max(
                        WORK_OVERLAY_RESTORE_FADE_OUT_MS,
                        WORK_OVERLAY_RESTORE_SHIFT_MS,
                    )
                    self._touch_transition_watchdog(duration_ms)
                    phase1 = QParallelAnimationGroup(self)
                    if self._transition_source_effect is not None:
                        phase1.addAnimation(
                            self._animate_effect_opacity(
                                self._transition_source_effect,
                                1.0,
                                0.0,
                                WORK_OVERLAY_RESTORE_FADE_OUT_MS,
                            )
                        )

                    staged_count = len(staged_circles)
                    for list_idx, record in enumerate(staged_circles[:-1]):
                        widget = self._record_visual_widget(record)
                        if widget is None:
                            continue
                        index_from_right = staged_count - 1 - list_idx
                        phase1.addAnimation(
                            self._animate_widget_geometry(
                                widget,
                                self._completed_record_geometry(index_from_right, staged_count),
                                WORK_OVERLAY_RESTORE_SHIFT_MS,
                                QEasingCurve.Type.InOutQuad,
                            )
                        )
                    if phase1.animationCount() == 0:
                        self._add_noop_animation(phase1, duration_ms)
                    phase1.finished.connect(
                        lambda: self._start_completed_to_card_phase2(
                            old_items,
                            new_items,
                            clicked_record,
                            staged_circles,
                            rects,
                        )
                    )
                    self._transition_animation_group = phase1
                    phase1.start()
                except Exception:
                    self._end_transition()

    def _start_completed_to_card_phase2(
                self,
                old_items: list[Mapping[str, object]],
                new_items: list[Mapping[str, object]],
                clicked_record: dict[str, Any],
                staged_circles: list[dict[str, Any]],
                rects: list[dict[str, Any]],
            ) -> None:
                try:
                    source_widget = self._transition_source_widget
                    if source_widget is None:
                        self._end_transition()
                        return
                    duration_ms = WORK_OVERLAY_RESTORE_FADE_IN_MS
                    self._touch_transition_watchdog(duration_ms)
                    effect = self._transition_source_effect
                    if effect is None:
                        effect = QGraphicsOpacityEffect(source_widget)
                        source_widget.setGraphicsEffect(effect)
                        self._transition_source_effect = effect
                    effect.setOpacity(0.0)
                    rightmost_rect = self._completed_record_geometry(0, len(staged_circles))
                    source_widget.setGeometry(rightmost_rect)
                    source_widget.show()
                    source_widget.raise_()

                    phase2 = QParallelAnimationGroup(self)
                    phase2.addAnimation(
                        self._animate_effect_opacity(
                            effect,
                            0.0,
                            1.0,
                            duration_ms,
                        )
                    )
                    phase2.finished.connect(
                        lambda: self._start_completed_to_card_phase3(
                            old_items,
                            new_items,
                            clicked_record,
                            staged_circles,
                            rects,
                        )
                    )
                    self._transition_animation_group = phase2
                    phase2.start()
                except Exception:
                    self._end_transition()

    def _start_completed_to_card_phase3(
                self,
                old_items: list[Mapping[str, object]],
                new_items: list[Mapping[str, object]],
                clicked_record: dict[str, Any],
                staged_circles: list[dict[str, Any]],
                rects: list[dict[str, Any]],
            ) -> None:
                try:
                    duration_ms = WORK_OVERLAY_RESTORE_DESCEND_MS
                    self._touch_transition_watchdog(duration_ms)
                    phase3 = QParallelAnimationGroup(self)
                    source_widget = self._transition_source_widget
                    circle_count_after = max(0, len(staged_circles) - 1)
                    target_card = self._card_slot_record_geometry(0, circle_count_after)

                    if source_widget is not None:
                        source_rect = source_widget.geometry()
                        source_widget.hide()
                        transition_card = self._build_transition_card_widget(
                            self._transition_item_payload(
                                str(clicked_record.get("item_id") or ""),
                                old_items,
                                new_items,
                            )
                        )
                        transition_card.setGeometry(source_rect)
                        transition_card.show()
                        transition_card.raise_()
                        self._transition_card_widget = transition_card
                        phase3.addAnimation(
                            self._animate_widget_geometry(
                                transition_card,
                                target_card,
                                duration_ms,
                                QEasingCurve.Type.OutBack,
                            )
                        )

                    for index_from_top, record in enumerate(rects):
                        widget = self._record_visual_widget(record)
                        if widget is None:
                            continue
                        phase3.addAnimation(
                            self._animate_widget_geometry(
                                widget,
                                self._card_slot_record_geometry(index_from_top + 1, circle_count_after),
                                duration_ms,
                                QEasingCurve.Type.OutBack,
                            )
                        )

                    for list_idx, record in enumerate(staged_circles[:-1]):
                        widget = self._record_visual_widget(record)
                        if widget is None:
                            continue
                        index_from_right = circle_count_after - 1 - list_idx
                        phase3.addAnimation(
                            self._animate_widget_geometry(
                                widget,
                                self._completed_record_geometry(index_from_right, circle_count_after),
                                duration_ms,
                                QEasingCurve.Type.InOutQuad,
                            )
                        )

                    self._set_transition_height_for_counts(
                        circle_count_after,
                        len(rects) + 1,
                        [target_card],
                    )
                    if phase3.animationCount() == 0:
                        self._add_noop_animation(phase3, duration_ms)
                    phase3.finished.connect(self._end_transition)
                    self._transition_animation_group = phase3
                    phase3.start()
                except Exception:
                    self._end_transition()

    def _build_transition_badge_widget(self, item: Mapping[str, object]) -> QWidget:
                badge = CompletedBadgeWidget(
                    item,
                    self._shell,
                    animate_intro=False,
                    animate_speed_intro=True,
                    theme_tokens=self._theme_tokens,
                )
                badge.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
                badge.setMinimumSize(1, 1)
                badge.setMaximumSize(16777215, 16777215)
                badge.resize(
                    WORK_OVERLAY_COMPLETED_BADGE_SIZE,
                    WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
                )
                return badge

    def _card_yield_geometry_for_circle_path(
                self,
                widget: QWidget,
                circle_rect: QRect,
            ) -> QRect:
                card_rect = widget.geometry()
                target = _card_yield_rect_for_circle_path(
                    (
                        float(card_rect.x()),
                        float(card_rect.y()),
                        float(card_rect.width()),
                        float(card_rect.height()),
                    ),
                    (
                        float(circle_rect.x()),
                        float(circle_rect.y()),
                        float(circle_rect.width()),
                        float(circle_rect.height()),
                    ),
                    side=self._side,
                )
                return self._card_geometry_for_slot(target)

    def _card_yield_delay_for_circle_path(
                self,
                widget: QWidget,
                source_circle: QRect,
                target_circle: QRect,
                duration_ms: int,
            ) -> int:
                card_rect = widget.geometry()
                return _card_yield_delay_ms(
                    (
                        float(card_rect.x()),
                        float(card_rect.y()),
                        float(card_rect.width()),
                        float(card_rect.height()),
                    ),
                    (
                        float(source_circle.x()),
                        float(source_circle.y()),
                        float(source_circle.width()),
                        float(source_circle.height()),
                    ),
                    (
                        float(target_circle.x()),
                        float(target_circle.y()),
                        float(target_circle.width()),
                        float(target_circle.height()),
                    ),
                    duration_ms,
                )

    def _start_card_to_completed_transition(
                self,
                item_id: str,
                old_items: list[Mapping[str, object]],
                new_items: list[Mapping[str, object]],
            ) -> None:
                try:
                    self._transition_in_progress = True
                    self._transition_type = "card_to_completed"
                    self._transition_item_id = item_id
                    self._transition_started_at = time.monotonic()
                    self._hide_transition_interactive_windows()

                    circles, rects = self._transition_records_for_current_layout()
                    clicked_record = self._record_for_item_kind(item_id, "card")
                    source_widget = (
                        self._record_visual_widget(clicked_record)
                        if clicked_record is not None
                        else None
                    )
                    if clicked_record is None or source_widget is None:
                        self._end_transition()
                        return

                    clicked_idx = rects.index(clicked_record) if clicked_record in rects else -1
                    next_rects = [record for record in rects if record is not clicked_record]
                    next_circles = [*circles, clicked_record]
                    circle_count_after = len(next_circles)
                    source_rect = self._widget_shell_rect(source_widget)
                    source_circle = self._qrect_from_rectf(
                        QRectF(
                            *_card_height_circle_rect_for_rect(
                                (
                                    source_rect.x(),
                                    source_rect.y(),
                                    source_rect.width(),
                                    source_rect.height(),
                                ),
                                side=self._side,
                            )
                        )
                    )
                    target_circle = self._completed_record_geometry(0, circle_count_after)
                    self._set_transition_height_for_counts(
                        circle_count_after,
                        len(next_rects),
                        [source_circle, target_circle],
                    )

                    source_snapshot = source_widget.grab()
                    source_widget.hide()
                    self._transition_hidden_widget = source_widget
                    self._transition_source_widget = source_widget
                    transition_card = CardToCompletedMorphWidget(
                        source_snapshot, self._shell, theme_tokens=self._theme_tokens,
                    )
                    transition_card.setGeometry(self._qrect_from_rectf(source_rect))
                    transition_card.show()
                    transition_card.raise_()
                    self._transition_card_widget = transition_card
                    transition_badge = self._build_transition_badge_widget(
                        self._transition_item_payload(item_id, old_items, new_items)
                    )
                    transition_badge.setGeometry(source_circle)
                    badge_effect = QGraphicsOpacityEffect(transition_badge)
                    badge_effect.setOpacity(0.0)
                    transition_badge.setGraphicsEffect(badge_effect)
                    transition_badge.show()
                    transition_badge.raise_()
                    self._transition_badge_widget = transition_badge

                    self._start_card_to_completed_shape_phase(
                        circles,
                        rects,
                        clicked_record,
                        clicked_idx,
                        next_rects,
                        circle_count_after,
                        source_circle,
                        target_circle,
                        transition_card,
                        transition_badge,
                        badge_effect,
                    )
                except Exception:
                    self._end_transition()

    def _start_card_to_completed_shape_phase(
                self,
                circles: list[dict[str, Any]],
                rects: list[dict[str, Any]],
                clicked_record: dict[str, Any],
                clicked_idx: int,
                next_rects: list[dict[str, Any]],
                circle_count_after: int,
                source_circle: QRect,
                target_circle: QRect,
                transition_card: QWidget,
                transition_badge: QWidget,
                badge_effect: QGraphicsOpacityEffect,
            ) -> None:
                try:
                    duration_ms = WORK_OVERLAY_TRANSITION_SHRINK_MS + WORK_OVERLAY_TRANSITION_PAUSE_MS
                    self._touch_transition_watchdog(duration_ms)
                    card_effect = QGraphicsOpacityEffect(transition_card)
                    card_effect.setOpacity(1.0)
                    transition_card.setGraphicsEffect(card_effect)

                    phase = QParallelAnimationGroup(self)
                    morph = QPropertyAnimation(transition_card, b"morphProgress")
                    morph.setStartValue(0.0)
                    morph.setEndValue(1.0)
                    morph.setDuration(WORK_OVERLAY_TRANSITION_SHRINK_MS)
                    phase.addAnimation(morph)
                    phase.addAnimation(
                        self._animate_widget_geometry(
                            transition_card,
                            source_circle,
                            WORK_OVERLAY_TRANSITION_SHRINK_MS,
                            QEasingCurve.Type.OutCubic,
                        )
                    )
                    # Keep the shrinking rounded silhouette opaque until it
                    # is nearly circular, then hand off to the orb readout.
                    handoff_ms = max(1, int(WORK_OVERLAY_TRANSITION_SHRINK_MS * 0.35))
                    for effect, start, end in ((card_effect, 1.0, 0.0), (badge_effect, 0.0, 1.0)):
                        handoff = QSequentialAnimationGroup(self)
                        handoff.addAnimation(QPauseAnimation(WORK_OVERLAY_TRANSITION_SHRINK_MS - handoff_ms))
                        handoff.addAnimation(self._animate_effect_opacity(effect, start, end, handoff_ms))
                        phase.addAnimation(handoff)
                    self._add_noop_animation(phase, duration_ms)
                    phase.finished.connect(
                        lambda: self._start_card_to_completed_fly_phase(
                            circles,
                            rects,
                            clicked_record,
                            clicked_idx,
                            next_rects,
                            circle_count_after,
                            source_circle,
                            target_circle,
                            transition_card,
                            transition_badge,
                        )
                    )
                    self._transition_animation_group = phase
                    phase.start()
                except Exception:
                    self._end_transition()

    def _start_card_to_completed_fly_phase(
                self,
                circles: list[dict[str, Any]],
                rects: list[dict[str, Any]],
                clicked_record: dict[str, Any],
                clicked_idx: int,
                next_rects: list[dict[str, Any]],
                circle_count_after: int,
                source_circle: QRect,
                target_circle: QRect,
                transition_card: QWidget,
                transition_badge: QWidget,
            ) -> None:
                try:
                    del clicked_record
                    transition_card.hide()
                    transition_badge.setGeometry(source_circle)
                    if isinstance(transition_badge, CompletedBadgeWidget):
                        transition_badge.start_speed_intro()
                    transition_badge.show()
                    transition_badge.raise_()
                    effect = transition_badge.graphicsEffect()
                    if isinstance(effect, QGraphicsOpacityEffect):
                        effect.setOpacity(1.0)
                    duration_ms = WORK_OVERLAY_TRANSITION_MOVE_MS + WORK_OVERLAY_TRANSITION_SHIFT_MS
                    self._touch_transition_watchdog(duration_ms)
                    group = QParallelAnimationGroup(self)
                    group.addAnimation(
                        self._animate_widget_geometry(
                            transition_badge,
                            target_circle,
                            duration_ms,
                            QEasingCurve.Type.InOutQuad,
                        )
                    )

                    for list_idx, record in enumerate(circles):
                        widget = self._record_visual_widget(record)
                        if widget is None:
                            continue
                        index_from_right = circle_count_after - 1 - list_idx
                        group.addAnimation(
                            self._animate_widget_geometry(
                                widget,
                                self._completed_record_geometry(
                                    index_from_right,
                                    circle_count_after,
                                ),
                                duration_ms,
                                QEasingCurve.Type.InOutQuad,
                            )
                        )

                    for index_from_top, record in enumerate(next_rects):
                        widget = self._record_visual_widget(record)
                        if widget is None:
                            continue
                        target = self._card_slot_record_geometry(index_from_top, circle_count_after)
                        old_index = rects.index(record) if record in rects else index_from_top
                        if clicked_idx >= 0 and old_index < clicked_idx:
                            sequence = QSequentialAnimationGroup(self)
                            yield_rect = self._card_yield_geometry_for_circle_path(
                                widget,
                                target_circle,
                            )
                            yield_delay = self._card_yield_delay_for_circle_path(
                                widget,
                                source_circle,
                                target_circle,
                                duration_ms,
                            )
                            yield_ms = max(1, min(WORK_OVERLAY_TRANSITION_SHIFT_MS, duration_ms // 3))
                            settle_ms = max(1, min(WORK_OVERLAY_TRANSITION_SHIFT_MS, duration_ms // 3))
                            max_yield_start = max(0, duration_ms - yield_ms - settle_ms - 1)
                            yield_start = min(max_yield_start, max(0, yield_delay - yield_ms // 2))
                            hold_ms = max(1, duration_ms - yield_start - yield_ms - settle_ms)
                            if yield_start > 0:
                                sequence.addAnimation(QPauseAnimation(yield_start, self))
                            sequence.addAnimation(
                                self._animate_widget_geometry(
                                    widget,
                                    yield_rect,
                                    yield_ms,
                                    QEasingCurve.Type.InOutQuad,
                                )
                            )
                            sequence.addAnimation(QPauseAnimation(hold_ms, self))
                            sequence.addAnimation(
                                self._animate_widget_geometry(
                                    widget,
                                    target,
                                    settle_ms,
                                    QEasingCurve.Type.InOutQuad,
                                )
                            )
                            group.addAnimation(sequence)
                        else:
                            group.addAnimation(
                                self._animate_widget_geometry(
                                    widget,
                                    target,
                                    duration_ms,
                                    QEasingCurve.Type.InOutQuad,
                                )
                            )

                    if group.animationCount() == 0:
                        self._add_noop_animation(group, duration_ms)
                    group.finished.connect(self._end_transition)
                    self._transition_animation_group = group
                    group.start()
                except Exception:
                    self._end_transition()

    def _start_transition(
                self,
                transition_type: str,
                item_id: str,
                old_items: list[Mapping[str, object]],
                new_items: list[Mapping[str, object]],
            ) -> None:
                if self._transition_in_progress:
                    return
                self._start_transition_watchdog(_transition_total_ms() + WORK_OVERLAY_RESTORE_DESCEND_MS)
                if transition_type == "card_to_completed":
                    self._start_card_to_completed_transition(item_id, old_items, new_items)
                    return
                if transition_type == "completed_to_card":
                    self._start_completed_to_card_transition(item_id, old_items, new_items)
                    return
                self._end_transition()

    def _end_transition(self) -> None:
                finished_transition_type = self._transition_type
                finished_item_id = self._transition_item_id
                liquid_state = (
                    self._transition_badge_widget.liquid_animation_state()
                    if finished_transition_type == "card_to_completed"
                    and isinstance(self._transition_badge_widget, CompletedBadgeWidget)
                    else None
                )
                completed_ids = {
                    _item_id(item)
                    for item in self._raw_items
                    if _item_id(item) and _item_is_completed(item)
                }
                self._settled_completed_intro_ids.update(completed_ids)
                self._transition_in_progress = False
                self._transition_type = ""
                self._transition_item_id = ""
                self._transition_started_at = 0.0
                self._transition_watchdog.stop()
                self._stop_transition_animation_group()
                if self._transition_source_widget is not None:
                    if _transition_hides_source_before_effect_reset(finished_transition_type):
                        self._transition_source_widget.hide()
                    self._transition_source_widget.setGraphicsEffect(None)
                    if finished_transition_type not in {
                        "card_to_completed",
                        "completed_to_card",
                        "completed_dismiss",
                    }:
                        self._transition_source_widget.show()
                self._transition_source_widget = None
                self._transition_source_effect = None
                if self._transition_card_widget is not None:
                    self._transition_card_widget.setGraphicsEffect(None)
                    self._transition_card_widget.setParent(None)
                    self._transition_card_widget.deleteLater()
                    self._transition_card_widget = None
                if self._transition_badge_widget is not None:
                    self._transition_badge_widget.setGraphicsEffect(None)
                    self._transition_badge_widget.setParent(None)
                    self._transition_badge_widget.deleteLater()
                    self._transition_badge_widget = None
                if self._transition_annihilation_widget is not None:
                    try:
                        self._transition_annihilation_widget.close()
                        self._transition_annihilation_widget.deleteLater()
                    except RuntimeError:
                        pass
                    self._transition_annihilation_widget = None
                self._transition_hidden_widget = None
                self._transition_required_height = 0
                self._last_payload_signature = ""
                self._last_structure_signature = ""
                self.render_items(self._raw_items)
                if liquid_state is not None:
                    badge = self._record_widget_for_kind(finished_item_id, "completed")
                    if isinstance(badge, CompletedBadgeWidget):
                        badge.restore_liquid_animation(liquid_state)
__all__ = [
    "OverlayTransitionsMixin",
]
