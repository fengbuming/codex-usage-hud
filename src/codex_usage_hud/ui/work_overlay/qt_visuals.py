from __future__ import annotations

import math
import time
from collections.abc import Mapping

from PySide6.QtCore import (
    QPointF,
    QRectF,
    QSize,
    QTimer,
    Property,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from .constants import (
    WORK_OVERLAY_COMPLETED_BADGE_ANIMATION_MS,
    WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
    WORK_OVERLAY_COMPLETED_BADGE_SIZE,
    WORK_OVERLAY_COMPLETED_PENDING_FINISH_SECONDS,
    WORK_OVERLAY_SHIMMER_BAND_WIDTH_PX,
    WORK_OVERLAY_SHIMMER_HIGHLIGHT,
    WORK_OVERLAY_SHIMMER_PEAK_ALPHA,
    WORK_OVERLAY_SHIMMER_STEP_PX,
    WORK_OVERLAY_SHIMMER_TIMER_MS,
    WORK_OVERLAY_TEXT_WRAP_WIDTH,
)
from .geometry import (
    _completed_pending_caption_opacity,
    _completed_pending_finish_progress,
    _completed_pending_launch_progress,
    _completed_pending_launch_scale,
    _completed_pending_particle_state,
    _ease_out_cubic,
)
from .model import (
    _clamp01,
    _compact_work_text,
    _profile_display_name,
    _workdir_display_name,
)
from .theme import _completed_badge_palette, _resolved_overlay_theme

widget_attrs = Qt.WidgetAttribute
focus_policy = Qt.FocusPolicy
mouse_buttons = Qt.MouseButton
alignment = Qt.AlignmentFlag
window_type = Qt.WindowType


class ShimmerTextLabel(QWidget):
    """QPainter text-mask shimmer for active work status text."""

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        base_color: str = "#8492A6",
        highlight_color: str = WORK_OVERLAY_SHIMMER_HIGHLIGHT,
        band_width_px: float = WORK_OVERLAY_SHIMMER_BAND_WIDTH_PX,
        step_px: float = WORK_OVERLAY_SHIMMER_STEP_PX,
        timer_ms: int = WORK_OVERLAY_SHIMMER_TIMER_MS,
    ) -> None:
        super().__init__(parent)
        self._text = str(text or "")
        self._base_color = QColor(base_color)
        self._highlight_color = QColor(highlight_color)
        self._band_width_px = max(1.0, float(band_width_px))
        self._step_px = max(0.25, float(step_px))
        self._timer_ms = max(10, int(timer_ms))
        self._phase_x = -self._band_width_px
        self._shimmer_enabled = True
        self._cached_text_path: QPainterPath | None = None
        self._cached_text_path_key: tuple[int, str, str] | None = None
        self._cached_text_width = 0.0
        self._cached_text_width_key: tuple[int, str, str] | None = None
        self._single_line = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance_shimmer)
        self.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self._timer.start(self._timer_ms)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        next_text = str(text or "")
        if next_text == self._text:
            return
        self._text = next_text
        self._invalidate_text_cache()
        self._phase_x = -self._band_width_px
        self.updateGeometry()
        self.update()

    def setSingleLine(self, enabled: bool = True) -> None:
        next_value = bool(enabled)
        if next_value == self._single_line:
            return
        self._single_line = next_value
        self._invalidate_text_cache()
        self.updateGeometry()
        self.update()

    def setBaseColor(self, color: str) -> None:
        self._base_color = QColor(color)
        self.update()

    def setHighlightColor(self, color: str) -> None:
        self._highlight_color = QColor(color)
        self.update()

    def setShimmerEnabled(self, enabled: bool) -> None:
        next_enabled = bool(enabled)
        if next_enabled == self._shimmer_enabled:
            return
        self._shimmer_enabled = next_enabled
        self._phase_x = -self._band_width_px
        if self._shimmer_enabled and self.isVisible():
            self._timer.start(self._timer_ms)
        else:
            self._timer.stop()
        self.update()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout_height(max(1, int(width)))

    def sizeHint(self) -> QSize:
        width = self.width() or WORK_OVERLAY_TEXT_WRAP_WIDTH
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        width = self.width() or WORK_OVERLAY_TEXT_WRAP_WIDTH
        return QSize(1, self.heightForWidth(width))

    def showEvent(self, event: object) -> None:
        if self._shimmer_enabled and not self._timer.isActive():
            self._timer.start(self._timer_ms)
        super().showEvent(event)

    def hideEvent(self, event: object) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event: object) -> None:
        self._invalidate_text_cache()
        super().resizeEvent(event)

    def paintEvent(self, event: object) -> None:
        del event
        path = self._text_path(max(1, self.width()))
        if path.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        painter.setBrush(self._base_color)
        painter.drawPath(path)

        if not self._shimmer_enabled:
            return

        transparent = QColor(self._highlight_color)
        transparent.setAlpha(0)
        peak = QColor(self._highlight_color)
        peak.setAlpha(WORK_OVERLAY_SHIMMER_PEAK_ALPHA)
        shoulder = QColor(self._highlight_color)
        shoulder.setAlpha(90)
        gradient = QLinearGradient(
            QPointF(self._phase_x - self._band_width_px, 0.0),
            QPointF(self._phase_x + self._band_width_px, 0.0),
        )
        gradient.setColorAt(0.0, transparent)
        gradient.setColorAt(0.36, shoulder)
        gradient.setColorAt(0.5, peak)
        gradient.setColorAt(0.64, shoulder)
        gradient.setColorAt(1.0, transparent)
        painter.setBrush(gradient)
        painter.drawPath(path)

    def _advance_shimmer(self) -> None:
        if not self.isVisible():
            return
        limit = max(self.width(), self._text_width()) + self._band_width_px
        self._phase_x += self._step_px
        if self._phase_x > limit:
            self._phase_x = -self._band_width_px
        self.update()

    def _text_width(self) -> float:
        layout_width = max(1, self.width())
        key = self._text_cache_key(layout_width)
        if key == self._cached_text_width_key:
            return self._cached_text_width
        text_width = 0.0
        for _, _, _, _, line_width in self._layout_lines(layout_width):
            text_width = max(text_width, line_width)
        self._cached_text_width_key = key
        self._cached_text_width = text_width
        return self._cached_text_width

    def _layout_height(self, width: int) -> int:
        if self._single_line:
            return max(1, int(self.fontMetrics().height() + 3))
        lines = self._layout_lines(width)
        if not lines:
            return max(1, int(self.fontMetrics().height() + 2))
        bottom = max(y + line_height for y, _, _, line_height, _ in lines)
        return max(1, int(bottom + 2.999))

    def _layout_lines(self, width: int) -> list[tuple[float, str, float, float, float]]:
        text = self._text
        if not text:
            return []
        if self._single_line:
            text = QFontMetrics(self.font()).elidedText(
                text,
                Qt.TextElideMode.ElideRight,
                max(1, int(width)),
            )
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout = QTextLayout(text, self.font())
        layout.setTextOption(option)
        lines: list[tuple[float, str, float, float, float]] = []
        y = 0.0
        layout.beginLayout()
        try:
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(max(1, width))
                line_y = (
                    max(0.0, (float(self.height()) - float(line.height())) / 2.0)
                    if self._single_line
                    else y
                )
                line.setPosition(QPointF(0.0, line_y))
                start = line.textStart()
                length = line.textLength()
                segment = text[start : start + length]
                lines.append(
                    (
                        line_y,
                        segment,
                        float(line.ascent()),
                        float(line.height()),
                        float(line.naturalTextWidth()),
                    )
                )
                y = line_y + float(line.height())
                if self._single_line:
                    break
        finally:
            layout.endLayout()
        return lines

    def _text_path(self, width: int) -> QPainterPath:
        key = self._text_cache_key(width)
        if key == self._cached_text_path_key and self._cached_text_path is not None:
            return self._cached_text_path
        path = QPainterPath()
        text = self._text
        if not text:
            self._cached_text_path_key = key
            self._cached_text_path = path
            return path
        for y, segment, line_ascent, _, _ in self._layout_lines(width):
            if not segment:
                continue
            baseline = y + line_ascent
            path.addText(QPointF(0.0, baseline), self.font(), segment)
        self._cached_text_path_key = key
        self._cached_text_path = path
        return path

    def _text_cache_key(self, width: int) -> tuple[int, str, str]:
        return (max(1, int(width)), self.font().key(), self._text)

    def _invalidate_text_cache(self) -> None:
        self._cached_text_path = None
        self._cached_text_path_key = None
        self._cached_text_width = 0.0
        self._cached_text_width_key = None

class CardSwitchPendingOverlayWidget(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._switch_pending = False
        self._switch_pending_started_at = 0.0
        self._switch_pending_completed = False
        self._switch_pending_completed_at = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
        self.hide()

    def set_switch_pending(
        self,
        pending: bool,
        started_at: float,
        *,
        completed: bool = False,
        completed_at: float = 0.0,
    ) -> None:
        pending = bool(pending)
        started_at = float(started_at or 0.0)
        completed = bool(completed)
        completed_at = float(completed_at or 0.0)
        if self._switch_pending == pending and (
            not pending
            or abs(self._switch_pending_started_at - started_at) < 0.001
        ) and self._switch_pending_completed == completed and (
            not completed
            or abs(self._switch_pending_completed_at - completed_at) < 0.001
        ):
            return
        self._switch_pending = pending
        self._switch_pending_started_at = started_at if pending else 0.0
        self._switch_pending_completed = completed if pending else False
        self._switch_pending_completed_at = completed_at if pending and completed else 0.0
        if pending:
            self.show()
            self.raise_()
            if not self._timer.isActive():
                self._timer.start(24)
        else:
            self._timer.stop()
            self.hide()
        self.update()

    def _advance(self) -> None:
        if not self._switch_pending:
            self._timer.stop()
            self.hide()
            return
        if (
            self._switch_pending_completed
            and self._switch_pending_completed_at > 0.0
            and time.monotonic() - self._switch_pending_completed_at
            > WORK_OVERLAY_COMPLETED_PENDING_FINISH_SECONDS
        ):
            self._timer.stop()
            self.hide()
            return
        self.update()

    def paintEvent(self, event: object) -> None:
        del event
        if not self._switch_pending:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        now = time.monotonic()
        started_at = self._switch_pending_started_at or now
        elapsed = max(0.0, now - started_at)
        completed = self._switch_pending_completed
        finish_elapsed = (
            max(0.0, now - self._switch_pending_completed_at)
            if completed and self._switch_pending_completed_at > 0.0
            else 0.0
        )
        finish_progress = (
            _completed_pending_finish_progress(finish_elapsed)
            if completed
            else 0.0
        )
        motion_alpha = 1.0 - _ease_out_cubic(finish_progress)
        launch_progress = 1.0 if completed else _completed_pending_launch_progress(elapsed)

        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        center = QPointF(rect.center())
        painter.setPen(Qt.PenStyle.NoPen)
        veil_opacity = _completed_pending_caption_opacity(
            elapsed,
            completed=completed,
            finish_elapsed_seconds=finish_elapsed,
        )
        painter.setBrush(QColor(3, 17, 27, int(54 * veil_opacity)))
        painter.drawRoundedRect(rect, 10.0, 10.0)

        self._draw_card_quantum_frame(
            painter,
            rect,
            elapsed,
            launch_progress=launch_progress,
            finish_progress=finish_progress,
            motion_alpha=motion_alpha,
            completed=completed,
        )
        self._draw_card_caption(
            painter,
            center,
            elapsed,
            completed=completed,
            finish_elapsed=finish_elapsed,
        )

    def _draw_card_quantum_frame(
        self,
        painter: QPainter,
        rect: QRectF,
        elapsed: float,
        *,
        launch_progress: float,
        finish_progress: float,
        motion_alpha: float,
        completed: bool,
    ) -> None:
        frame_rect = rect.adjusted(8.0, 7.0, -8.0, -7.0)
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if launch_progress < 1.0:
            alpha = int(155 * (1.0 - launch_progress))
            launch_rect = frame_rect.adjusted(
                -10.0 * _ease_out_cubic(launch_progress),
                -5.0 * _ease_out_cubic(launch_progress),
                10.0 * _ease_out_cubic(launch_progress),
                5.0 * _ease_out_cubic(launch_progress),
            )
            painter.setPen(QPen(QColor(82, 218, 255, alpha), 1.6))
            painter.drawRoundedRect(launch_rect, 12.0, 12.0)

        if completed:
            eased = _ease_out_cubic(finish_progress)
            alpha = int(185 * (1.0 - finish_progress))
            finish_rect = frame_rect.adjusted(
                -18.0 * eased,
                -8.0 * eased,
                18.0 * eased,
                8.0 * eased,
            )
            painter.setPen(QPen(QColor(102, 255, 218, alpha), 2.0))
            painter.drawRoundedRect(finish_rect, 14.0, 14.0)

        painter.setPen(QPen(QColor(93, 216, 255, int(128 * motion_alpha)), 1.1))
        painter.drawRoundedRect(frame_rect, 10.0, 10.0)

        points: list[QPointF] = []
        pulses: list[float] = []
        count = 3
        for index in range(count):
            angle, jitter, pulse = _completed_pending_particle_state(elapsed, index, count)
            points.append(self._card_perimeter_point(frame_rect, angle / math.tau, jitter))
            pulses.append(pulse)

        for index, point in enumerate(points):
            next_index = (index + 1) % len(points)
            strength = min(pulses[index], pulses[next_index])
            if strength <= 0.08:
                continue
            pen = QPen(
                QColor(102, 255, 218, int(120 * strength * motion_alpha)),
                max(0.45, 1.0 * strength),
            )
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(point, points[next_index])

        for index, point in enumerate(points):
            pulse = pulses[index]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(66, 225, 255, int((78 + pulse * 80) * motion_alpha)))
            painter.drawEllipse(point, 4.7 + pulse * 1.8, 4.7 + pulse * 1.8)
            painter.setBrush(QColor(238, 255, 255, int(238 * motion_alpha)))
            painter.drawEllipse(point, 1.7, 1.7)
        painter.restore()

    def _card_perimeter_point(
        self,
        rect: QRectF,
        progress: float,
        jitter: float,
    ) -> QPointF:
        width = max(1.0, rect.width())
        height = max(1.0, rect.height())
        distance = (float(progress) % 1.0) * (width + height) * 2.0
        if distance <= width:
            point = QPointF(rect.left() + distance, rect.top())
        elif distance <= width + height:
            point = QPointF(rect.right(), rect.top() + distance - width)
        elif distance <= width * 2.0 + height:
            point = QPointF(rect.right() - (distance - width - height), rect.bottom())
        else:
            point = QPointF(rect.left(), rect.bottom() - (distance - width * 2.0 - height))
        center = rect.center()
        dx = point.x() - center.x()
        dy = point.y() - center.y()
        length = max(1.0, math.hypot(dx, dy))
        return QPointF(
            point.x() + (dx / length) * jitter * 2.8,
            point.y() + (dy / length) * jitter * 2.8,
        )

    def _draw_card_caption(
        self,
        painter: QPainter,
        center: QPointF,
        pending_elapsed: float,
        *,
        completed: bool,
        finish_elapsed: float,
    ) -> None:
        opacity = _completed_pending_caption_opacity(
            pending_elapsed,
            completed=completed,
            finish_elapsed_seconds=finish_elapsed,
        )
        if opacity <= 0.001:
            return
        finish_progress = (
            _completed_pending_finish_progress(finish_elapsed)
            if completed
            else 0.0
        )
        scale = 1.0 + (0.055 * math.sin(math.pi * min(1.0, finish_progress)) if completed else 0.0)
        text = "已跳转" if completed else "正在跳转"
        panel_rect = QRectF(center.x() - 47.0, center.y() - 16.0, 94.0, 32.0)

        painter.save()
        painter.translate(center)
        painter.scale(scale, scale)
        painter.translate(-center)
        painter.setPen(
            QPen(
                QColor(118, 255, 202, int(210 * opacity))
                if completed
                else QColor(76, 213, 255, int(180 * opacity)),
                1.0,
            )
        )
        painter.setBrush(QColor(5, 23, 34, int(152 * opacity)))
        painter.drawRoundedRect(panel_rect, 10.0, 10.0)
        painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
        painter.setPen(
            QColor(204, 255, 232, int(245 * opacity))
            if completed
            else QColor(229, 250, 255, int(245 * opacity))
        )
        painter.drawText(panel_rect, alignment.AlignCenter, text)
        painter.restore()

class EnergyRingAnnihilationWidget(QWidget):
    """Transient completion-circle disappearance effect."""

    def __init__(
        self,
        *,
        max_radius: float,
        center: QPointF,
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
        self._progress = 0.0
        self._max_radius = max(10.0, float(max_radius))
        self._center = QPointF(center) if center is not None else QPointF(0.0, 0.0)
        self.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
        self.setAttribute(widget_attrs.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(focus_policy.NoFocus)

    def set_core_center(self, center: QPointF) -> None:
        self._center = QPointF(center)
        self.update()

    def get_progress(self) -> float:
        return self._progress

    def set_progress(self, value: float) -> None:
        value = _clamp01(value)
        if abs(self._progress - value) < 0.00001:
            return
        self._progress = value
        self.update()

    progress = Property(float, get_progress, set_progress)

    def paintEvent(self, event: object) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        center = self._center
        if center.isNull():
            center = QPointF(self.width() / 2.0, self.height() / 2.0)

        progress = _clamp01(self._progress)
        outer_radius = 10.0 + (self._max_radius - 10.0) * progress
        inner_radius = 0.0
        if progress > 0.1:
            inner_radius = (outer_radius * 0.85) * ((progress - 0.1) / 0.9)

        alpha = max(0, int(255 * (1.0 - progress * progress)))
        if outer_radius <= inner_radius or alpha <= 0:
            return

        ring_path = QPainterPath()
        ring_path.addEllipse(center, outer_radius, outer_radius)
        if inner_radius > 0.0:
            inner_path = QPainterPath()
            inner_path.addEllipse(center, inner_radius, inner_radius)
            ring_path = ring_path.subtracted(inner_path)

        gradient = QRadialGradient(center, outer_radius)
        mid_point = _clamp01(inner_radius / max(1.0, outer_radius))
        core_color = QColor(0, 191, 255, alpha)
        edge_color = QColor(135, 206, 250, int(alpha * 0.3))
        transparent_color = QColor(135, 206, 250, 0)
        gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        gradient.setColorAt(mid_point, QColor(0, 0, 0, 0))
        gradient.setColorAt(mid_point + (1.0 - mid_point) * 0.3, core_color)
        gradient.setColorAt(0.95, edge_color)
        gradient.setColorAt(1.0, transparent_color)

        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(ring_path)

        if progress > 0.4:
            delayed = _clamp01(progress - 0.4)
            delayed_outer = 10.0 + (self._max_radius - 10.0) * delayed
            delayed_alpha = max(0, int(100 * (1.0 - delayed)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 191, 255, delayed_alpha), 1.5))
            painter.drawEllipse(center, delayed_outer, delayed_outer)

class CompletedBadgeWidget(QWidget):
    """Animated circular summary for finished work."""

    def __init__(
        self,
        item: Mapping[str, object],
        parent: QWidget | None = None,
        *,
        animate_intro: bool = True,
        theme_tokens: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(parent)
        self._item: Mapping[str, object] = dict(item)
        self._theme_tokens = _resolved_overlay_theme(theme_tokens)
        self._started_at = time.monotonic()
        self._progress = 0.0 if animate_intro else 1.0
        self._switch_pending = False
        self._switch_pending_started_at = 0.0
        self._switch_pending_completed = False
        self._switch_pending_completed_at = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(
            WORK_OVERLAY_COMPLETED_BADGE_SIZE,
            WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
        )
        if animate_intro:
            self._timer.start(16)

    def set_item(self, item: Mapping[str, object]) -> None:
        self._item = dict(item)
        self.update()

    def set_theme_tokens(
        self,
        theme_tokens: Mapping[str, object] | None,
    ) -> None:
        self._theme_tokens = _resolved_overlay_theme(theme_tokens)
        self.update()

    def set_switch_pending(
        self,
        pending: bool,
        started_at: float,
        *,
        completed: bool = False,
        completed_at: float = 0.0,
    ) -> None:
        pending = bool(pending)
        started_at = float(started_at or 0.0)
        completed = bool(completed)
        completed_at = float(completed_at or 0.0)
        if self._switch_pending == pending and (
            not pending
            or abs(self._switch_pending_started_at - started_at) < 0.001
        ) and self._switch_pending_completed == completed and (
            not completed
            or abs(self._switch_pending_completed_at - completed_at) < 0.001
        ):
            return
        self._switch_pending = pending
        self._switch_pending_started_at = started_at if pending else 0.0
        self._switch_pending_completed = completed if pending else False
        self._switch_pending_completed_at = completed_at if pending and completed else 0.0
        if pending and not self._timer.isActive():
            self._timer.start(24)
        elif not pending and self._progress >= 1.0:
            self._timer.stop()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(
            WORK_OVERLAY_COMPLETED_BADGE_SIZE,
            WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
        )

    def _advance(self) -> None:
        elapsed_ms = (time.monotonic() - self._started_at) * 1000.0
        if self._progress < 1.0:
            self._progress = min(1.0, elapsed_ms / WORK_OVERLAY_COMPLETED_BADGE_ANIMATION_MS)
        if self._progress >= 1.0 and not self._switch_pending:
            self._timer.stop()
        self.update()

    def paintEvent(self, event: object) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        paint_scale = min(
            max(1.0, float(self.width())) / float(WORK_OVERLAY_COMPLETED_BADGE_SIZE),
            max(1.0, float(self.height())) / float(WORK_OVERLAY_COMPLETED_BADGE_SIZE),
        )
        painter.translate(
            (float(self.width()) - WORK_OVERLAY_COMPLETED_BADGE_SIZE * paint_scale) / 2.0,
            0.0,
        )
        painter.scale(paint_scale, paint_scale)

        eased = 1.0 - pow(1.0 - max(0.0, min(1.0, self._progress)), 3)
        palette = _completed_badge_palette(self._theme_tokens)
        final_size = float(WORK_OVERLAY_COMPLETED_BADGE_SIZE)
        pending_elapsed = (
            max(0.0, time.monotonic() - self._switch_pending_started_at)
            if self._switch_pending
            else 0.0
        )
        launch_scale = (
            _completed_pending_launch_scale(pending_elapsed)
            if not self._switch_pending_completed
            else 1.0
        )
        if abs(launch_scale - 1.0) > 0.0001:
            launch_center = QPointF(final_size / 2.0, final_size / 2.0)
            painter.translate(launch_center)
            painter.scale(launch_scale, launch_scale)
            painter.translate(-launch_center)
        start_height = 118.0
        start_width = float(WORK_OVERLAY_COMPLETED_BADGE_SIZE)
        start_rect = QRectF(
            0.0,
            (WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT - start_height) / 2.0,
            start_width,
            start_height,
        )
        end_rect = QRectF(
            0.0,
            0.0,
            final_size,
            final_size,
        )
        rect = QRectF(
            start_rect.x() + (end_rect.x() - start_rect.x()) * eased,
            start_rect.y() + (end_rect.y() - start_rect.y()) * eased,
            start_rect.width() + (end_rect.width() - start_rect.width()) * eased,
            start_rect.height() + (end_rect.height() - start_rect.height()) * eased,
        )
        radius = 10.0 + ((final_size / 2.0) - 10.0) * eased

        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor(palette["fillStart"]))
        gradient.setColorAt(0.5, QColor(palette["fillMid"]))
        gradient.setColorAt(1.0, QColor(palette["fillEnd"]))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(palette["border"]), 1.4))
        painter.drawRoundedRect(rect, radius, radius)

        if eased < 0.24:
            return

        content_alpha = int(255 * min(1.0, (eased - 0.24) / 0.42))
        painter.save()
        painter.setOpacity(content_alpha / 255.0)
        center = QPointF(end_rect.center())

        outer_pen = QPen(QColor(palette["ring"]), 2.0)
        painter.setPen(outer_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(end_rect.adjusted(4.0, 4.0, -4.0, -4.0))

        dashed_color = QColor(palette["dashedRing"])
        dashed_color.setAlpha(145)
        dashed_pen = QPen(dashed_color, 1.1)
        dashed_pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(dashed_pen)
        painter.drawEllipse(end_rect.adjusted(17.0, 17.0, -17.0, -17.0))

        title = str(self._item.get("title") or "Codex 工作").strip()
        profile = _profile_display_name(self._item)
        if str(self._item.get("clientKind") or "").strip().lower() == "app":
            profile = _compact_work_text(self._item.get("modelProvider"), 24)
            if profile.lower() in {"unknown", "n/a"}:
                profile = ""
        workdir = _workdir_display_name(self._item)
        self._draw_arc_text(
            painter,
            title,
            center=center,
            radius=67.0,
            start_degrees=-149.0,
            end_degrees=-31.0,
            font=QFont("Microsoft YaHei UI", 11, QFont.Weight.Bold),
            color=QColor(palette["titleText"]),
            bottom=False,
        )
        if workdir:
            self._draw_arc_text(
                painter,
                workdir,
                center=center,
                radius=78.0,
                start_degrees=145.0,
                end_degrees=35.0,
                font=QFont("Microsoft YaHei UI", 9),
                color=QColor(palette["workdirText"]),
                bottom=True,
                compact_spacing=True,
            )

        check_font = QFont("Segoe UI Symbol", 38, QFont.Weight.Bold)
        painter.setFont(check_font)
        painter.setPen(QColor(palette["checkText"]))
        painter.drawText(
            QRectF(center.x() - 34.0, center.y() - 66.0, 68.0, 56.0),
            alignment.AlignCenter,
            "✓",
        )

        elapsed = str(self._item.get("elapsedText") or "已处理 --").strip()
        painter.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
        painter.setPen(QColor(palette["elapsedText"]))
        painter.drawText(
            QRectF(center.x() - 54.0, center.y() - 10.0, 108.0, 18.0),
            alignment.AlignCenter,
            _compact_work_text(elapsed, 18),
        )

        stats = [
            ("Tokens", str(self._item.get("tokensText") or "0").strip() or "0"),
            ("Cost", str(self._item.get("costText") or "$0").strip() or "$0"),
            ("Cache", str(self._item.get("cacheHitText") or "--").strip() or "--"),
        ]
        box_width = 44.0
        box_height = 25.0
        spacing = 5.0
        start_x = center.x() - ((box_width * 3.0 + spacing * 2.0) / 2.0)
        y = center.y() + 14.0
        for index, (label, value) in enumerate(stats):
            box = QRectF(start_x + index * (box_width + spacing), y, box_width, box_height)
            stat_border = QColor(palette["statBoxBorder"])
            stat_border.setAlpha(220)
            painter.setPen(QPen(stat_border, 0.8))
            stat_fill = QColor(palette["statBoxFill"])
            stat_fill.setAlpha(235)
            painter.setBrush(stat_fill)
            painter.drawRoundedRect(box, 6.0, 6.0)
            painter.setPen(QColor(palette["statValue"]))
            painter.setFont(QFont("Microsoft YaHei UI", 7, QFont.Weight.Bold))
            painter.drawText(box.adjusted(1.0, 1.0, -1.0, -11.0), alignment.AlignCenter, value)
            painter.setPen(QColor(palette["statLabel"]))
            painter.setFont(QFont("Microsoft YaHei UI", 5))
            painter.drawText(box.adjusted(1.0, 13.0, -1.0, -1.0), alignment.AlignCenter, label)

        if profile:
            painter.setPen(QColor(palette["workdirText"]))
            painter.setFont(QFont("Microsoft YaHei UI", 7, QFont.Weight.DemiBold))
            painter.drawText(
                QRectF(center.x() - 60.0, center.y() + 41.0, 120.0, 14.0),
                alignment.AlignCenter,
                f"[{profile}]",
            )

        painter.restore()
        if self._switch_pending:
            self._draw_switch_pending_quantum(
                painter,
                end_rect,
                completed=self._switch_pending_completed,
                completed_at=self._switch_pending_completed_at,
            )

    def _arc_limited_text(self, text: str, font: QFont, max_width: float) -> str:
        compact = " ".join(str(text or "").split())
        if not compact:
            return ""
        metrics = QFontMetrics(font)
        if metrics.horizontalAdvance(compact) <= max_width:
            return compact
        suffix = "..."
        available = max(1, int(max_width - metrics.horizontalAdvance(suffix)))
        result = ""
        for char in compact:
            if metrics.horizontalAdvance(result + char) > available:
                break
            result += char
        return (result.rstrip() + suffix) if result else suffix

    def _draw_arc_text(
        self,
        painter: QPainter,
        text: str,
        *,
        center: QPointF,
        radius: float,
        start_degrees: float,
        end_degrees: float,
        font: QFont,
        color: QColor,
        bottom: bool,
        compact_spacing: bool = False,
    ) -> None:
        span = abs(end_degrees - start_degrees)
        max_width = math.radians(span) * radius * 0.86
        arc_text = self._arc_limited_text(text, font, max_width)
        if not arc_text:
            return
        painter.save()
        painter.setFont(font)
        painter.setPen(color)
        metrics = painter.fontMetrics()
        widths = [max(1, metrics.horizontalAdvance(char)) for char in arc_text]
        total_width = float(sum(widths))
        tracking_px = 0.0
        layout_width = total_width + tracking_px * max(0, len(widths) - 1)
        effective_span = span
        if compact_spacing:
            effective_span = min(span, math.degrees(layout_width / max(1.0, radius)))
        degrees_per_px = effective_span / max(1.0, layout_width)
        direction = 1.0 if end_degrees >= start_degrees else -1.0
        current = start_degrees + direction * ((span - effective_span) / 2.0)
        for char, width in zip(arc_text, widths):
            angle = current + direction * (width * degrees_per_px / 2.0)
            radians = math.radians(angle)
            x = center.x() + math.cos(radians) * radius
            y = center.y() + math.sin(radians) * radius
            painter.save()
            painter.translate(QPointF(x, y))
            rotation = angle - 90.0 if bottom else angle + 90.0
            painter.rotate(rotation)
            painter.drawText(
                QRectF(-width / 2.0 - 1.0, -metrics.ascent(), width + 2.0, metrics.height()),
                alignment.AlignCenter,
                char,
            )
            painter.restore()
            current += direction * (width + tracking_px) * degrees_per_px
        painter.restore()

    def _draw_switch_pending_quantum(
        self,
        painter: QPainter,
        rect: QRectF,
        *,
        completed: bool,
        completed_at: float,
    ) -> None:
        started_at = self._switch_pending_started_at or time.monotonic()
        elapsed = max(0.0, time.monotonic() - started_at)
        finish_elapsed = (
            max(0.0, time.monotonic() - completed_at)
            if completed and completed_at > 0.0
            else 0.0
        )
        finish_progress = (
            _completed_pending_finish_progress(finish_elapsed)
            if completed
            else 0.0
        )
        motion_alpha = 1.0 - _ease_out_cubic(finish_progress)
        launch_progress = 1.0 if completed else _completed_pending_launch_progress(elapsed)
        center = QPointF(rect.center())
        orbit_radius = min(rect.width(), rect.height()) / 2.0 - 7.0

        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if launch_progress < 1.0:
            self._draw_switch_pending_launch(painter, center, orbit_radius, launch_progress)
        if completed:
            self._draw_switch_pending_finish(painter, center, orbit_radius, finish_progress)

        ring_alpha = motion_alpha if completed else max(0.18, motion_alpha)
        ring_color = QColor(93, 216, 255, int(170 * ring_alpha))
        painter.setPen(QPen(ring_color, 1.6))
        painter.drawEllipse(center, orbit_radius, orbit_radius)

        collapse_color = QColor(119, 255, 210, int(60 * motion_alpha))
        collapse_pen = QPen(collapse_color, 0.9)
        collapse_pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(collapse_pen)
        wobble = 1.8 * math.sin(elapsed * 5.5)
        painter.drawEllipse(center, orbit_radius - 10.0 + wobble, orbit_radius - 10.0 + wobble)

        points: list[QPointF] = []
        pulses: list[float] = []
        count = 3
        for index in range(count):
            angle, radial_jitter, link_pulse = _completed_pending_particle_state(
                elapsed,
                index,
                count,
            )
            particle_radius = orbit_radius + radial_jitter * 4.2
            point = QPointF(
                center.x() + math.cos(angle) * particle_radius,
                center.y() + math.sin(angle) * particle_radius,
            )
            points.append(point)
            pulses.append(link_pulse)

        for index, point in enumerate(points):
            next_index = (index + 1) % len(points)
            strength = min(pulses[index], pulses[next_index])
            if strength <= 0.08:
                continue
            filament = QColor(102, 255, 218, int(150 * strength * motion_alpha))
            pen = QPen(filament, max(0.55, 1.15 * strength))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(point, points[next_index])

        for index, point in enumerate(points):
            pulse = pulses[index]
            glow_radius = 5.8 + pulse * 2.2
            glow = QColor(66, 225, 255, int((95 + pulse * 90) * motion_alpha))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(point, glow_radius, glow_radius)

            core = QColor(238, 255, 255, int(242 * motion_alpha))
            painter.setBrush(core)
            painter.drawEllipse(point, 2.0, 2.0)

        self._draw_switch_pending_caption(
            painter,
            center,
            elapsed,
            completed=completed,
            finish_elapsed=finish_elapsed,
        )
        painter.restore()

    def _draw_switch_pending_launch(
        self,
        painter: QPainter,
        center: QPointF,
        orbit_radius: float,
        progress: float,
    ) -> None:
        progress = _clamp01(progress)
        eased = _ease_out_cubic(progress)
        alpha = int(210 * (1.0 - progress))
        if alpha <= 0:
            return

        wave_center = QPointF(center.x(), center.y() + orbit_radius * 0.74)
        wave_radius_x = 14.0 + (orbit_radius * 0.58) * eased
        wave_radius_y = 4.0 + (orbit_radius * 0.16) * eased
        wave_pen = QPen(QColor(100, 255, 218, alpha), 1.4)
        wave_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(wave_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(wave_center, wave_radius_x, wave_radius_y)

        sweep_rect = QRectF(
            center.x() - orbit_radius,
            center.y() - orbit_radius,
            orbit_radius * 2.0,
            orbit_radius * 2.0,
        )
        sweep_pen = QPen(QColor(84, 220, 255, min(255, int(alpha * 1.15))), 3.1)
        sweep_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(sweep_pen)
        start_degrees = -115.0 + 395.0 * eased
        painter.drawArc(
            sweep_rect,
            int(-start_degrees * 16),
            int(-76.0 * (1.0 - progress * 0.35) * 16),
        )

        inner_pen = QPen(QColor(228, 255, 255, int(alpha * 0.58)), 1.0)
        inner_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(inner_pen)
        painter.drawArc(
            sweep_rect.adjusted(10.0, 10.0, -10.0, -10.0),
            int(-(start_degrees + 28.0) * 16),
            int(-34.0 * 16),
        )

    def _draw_switch_pending_finish(
        self,
        painter: QPainter,
        center: QPointF,
        orbit_radius: float,
        progress: float,
    ) -> None:
        progress = _clamp01(progress)
        eased = _ease_out_cubic(progress)
        alpha = int(170 * (1.0 - progress))
        if alpha <= 0:
            return
        finish_radius = orbit_radius * (0.42 + 0.58 * eased)
        finish_pen = QPen(QColor(102, 255, 218, alpha), 2.0)
        finish_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(finish_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, finish_radius, finish_radius)

        sweep_rect = QRectF(
            center.x() - orbit_radius,
            center.y() - orbit_radius,
            orbit_radius * 2.0,
            orbit_radius * 2.0,
        )
        sweep_pen = QPen(QColor(230, 255, 255, int(alpha * 0.75)), 2.4)
        sweep_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(sweep_pen)
        painter.drawArc(
            sweep_rect.adjusted(5.0, 5.0, -5.0, -5.0),
            int((-60.0 - 240.0 * eased) * 16),
            int(-92.0 * (1.0 - progress * 0.4) * 16),
        )

    def _draw_switch_pending_caption(
        self,
        painter: QPainter,
        center: QPointF,
        pending_elapsed: float,
        *,
        completed: bool,
        finish_elapsed: float,
    ) -> None:
        opacity = _completed_pending_caption_opacity(
            pending_elapsed,
            completed=completed,
            finish_elapsed_seconds=finish_elapsed,
        )
        if opacity <= 0.001:
            return

        finish_progress = (
            _completed_pending_finish_progress(finish_elapsed)
            if completed
            else 0.0
        )
        scale = 1.0 + (0.07 * math.sin(math.pi * min(1.0, finish_progress)) if completed else 0.0)
        text = "已跳转" if completed else "正在跳转"
        panel_rect = QRectF(center.x() - 45.0, center.y() - 16.0, 90.0, 32.0)

        painter.save()
        painter.translate(center)
        painter.scale(scale, scale)
        painter.translate(-center)

        panel_alpha = int(150 * opacity)
        border_alpha = int((210 if completed else 180) * opacity)
        text_alpha = int(245 * opacity)
        panel_color = QColor(5, 23, 34, panel_alpha)
        border_color = QColor(118, 255, 202, border_alpha) if completed else QColor(76, 213, 255, border_alpha)
        text_color = QColor(204, 255, 232, text_alpha) if completed else QColor(229, 250, 255, text_alpha)

        painter.setPen(QPen(border_color, 1.0))
        painter.setBrush(panel_color)
        painter.drawRoundedRect(panel_rect, 10.0, 10.0)

        painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
        painter.setPen(text_color)
        painter.drawText(panel_rect, alignment.AlignCenter, text)
        painter.restore()

__all__ = [
    'ShimmerTextLabel',
    'CardSwitchPendingOverlayWidget',
    'EnergyRingAnnihilationWidget',
    'CompletedBadgeWidget'
]
