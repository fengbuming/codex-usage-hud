"""Speed-driven lower-edge tide for the independent desktop work bubble."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QWidget

from ...output_speed import positive_output_speed


class EnergyTideCard(QFrame):
    """Paint beneath child labels; keep animation local to visible active cards."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._heat = 0.0
        self._target_heat = 0.0
        self._phase = 0.0
        self._bubble_phase = 0.0
        self._last_frame = 0.0
        self._tide_color = QColor("#F3D27A")
        self._tide_timer = QTimer(self)
        self._tide_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._tide_timer.setInterval(33)
        self._tide_timer.timeout.connect(self._advance_tide)

    def set_output_speed(
        self, value: object, *, enabled: bool, color: str = "#F3D27A"
    ) -> None:
        speed = positive_output_speed(value) if enabled else None
        target = min(1.0, math.log1p(speed / 6.0) / math.log1p(400.0 / 6.0)) if speed and speed >= 0.5 else 0.0
        next_color = QColor(color)
        changed = target != self._target_heat or next_color != self._tide_color
        self._target_heat = target
        self._tide_color = next_color
        if not enabled:
            self._heat = 0.0
            self._tide_timer.stop()
        elif self.isVisible() and (target > 0 or self._heat > 0.006):
            if not self._tide_timer.isActive():
                self._last_frame = time.monotonic()
                self._tide_timer.start()
        if changed:
            self._update_tide_region()

    def _update_tide_region(self) -> None:
        self.update(QRect(0, max(0, self.height() - 64), self.width(), 64))

    def _advance_tide(self) -> None:
        if not self.isVisible():
            self._tide_timer.stop()
            return
        now = time.monotonic()
        dt = min(0.08, max(0.0, now - self._last_frame))
        self._last_frame = now
        self._heat += (self._target_heat - self._heat) * (1.0 - math.exp(-dt / 0.22))
        self._phase += dt * (1.2 + 4.8 * self._heat)
        self._bubble_phase = (self._bubble_phase + dt * (0.16 + 0.28 * self._heat)) % 1.0
        if self._target_heat == 0 and self._heat <= 0.006:
            self._heat = 0.0
            self._tide_timer.stop()
        self._update_tide_region()

    def showEvent(self, event: object) -> None:
        super().showEvent(event)
        if self._target_heat > 0:
            self._last_frame = time.monotonic()
            self._tide_timer.start()

    def hideEvent(self, event: object) -> None:
        self._tide_timer.stop()
        super().hideEvent(event)

    def _color(self, opacity: float) -> QColor:
        color = QColor(self._tide_color)
        color.setAlphaF(max(0.0, min(1.0, opacity)))
        return color

    def _surface_y(self, x: float, height: float, layer: int = 0) -> float:
        heat = self._heat
        amplitude = 2.0 + 11.0 * heat ** 1.4
        level = height - 12 - 2 * heat + layer * 2.6
        angle = x * 0.021 + self._phase + layer * 0.8
        return level + amplitude * (
            math.sin(angle)
            + math.sin(x * 0.041 - self._phase * 0.73 + layer) * 0.20
            + math.sin(2 * angle + 0.4) * 0.15 * heat
        )

    def _draw_bubbles(self, painter: QPainter, width: int, height: int, presence: float) -> None:
        # A bounded pool of hollow bubbles. Edge lanes can rise farther;
        # inner lanes stay below the assistant output and status text.
        lanes = (0.018, 0.982, 0.035, 0.965, 0.07, 0.93, 0.12, 0.88, 0.2, 0.8, 0.34, 0.66)
        heat = self._heat
        for index in range(min(len(lanes), math.ceil(4 + 8 * heat))):
            progress = (self._bubble_phase + index * 0.618034) % 1.0
            lane = lanes[index]
            rise = 38 + 15 * heat if lane < 0.06 or lane > 0.94 else 17 + 8 * heat
            x = width * lane + math.sin(self._phase * 0.6 + index) * 1.4
            y = height - 2 - progress * rise
            radius = 1.1 + (index % 3) * 0.4 + 0.6 * heat
            alpha = (0.38 + 0.28 * heat) * math.sin(progress * math.pi) ** 0.7 * presence
            painter.setPen(QPen(self._color(alpha), 0.85))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(x, y), radius, radius)

    def paintEvent(self, event: object) -> None:
        super().paintEvent(event)
        heat = self._heat
        if heat <= 0.006:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 9, 9)
        painter.setClipPath(clip)
        width, height = self.width(), self.height()
        # Keep the low-speed tide legible: multiplying opacity by heat made a
        # real 2.98 tok/s sample nearly transparent and only a few pixels tall.
        presence = min(1.0, heat / 0.035)
        amplitude = 2.0 + 11.0 * heat ** 1.4
        level = height - 12 - 2 * heat
        for layer in (2, 1, 0):
            path = QPainterPath()
            for x in range(0, width + 4, 4):
                y = self._surface_y(x, height, layer)
                if x == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            surface = QPainterPath(path)
            path.lineTo(width, height)
            path.lineTo(0, height)
            path.closeSubpath()
            gradient = QLinearGradient(0, level - amplitude, 0, height)
            gradient.setColorAt(0, self._color((0.10 + 0.13 * heat) * presence))
            gradient.setColorAt(1, self._color((0.04 + 0.07 * heat) * presence))
            painter.fillPath(path, gradient)
            surface_opacity = (0.45 + 0.35 * heat) * presence * (1 if layer == 0 else 0.38)
            painter.setPen(QPen(self._color(surface_opacity), 1.0 if layer == 0 else 0.7))
            painter.drawPath(surface)
        self._draw_bubbles(painter, width, height, presence)
