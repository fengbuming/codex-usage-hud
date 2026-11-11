"""The approved Liquid orb treatment for the independent completed badge.

Adapted from Agent Speed's Liquid design (MIT; see THIRD_PARTY_NOTICES.md).
The caller animates visible badges; this painter owns no timer.
"""

from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

from ...output_speed import positive_output_speed


COMPLETED_LIQUID_REVEAL_SECONDS = 0.9
COMPLETED_LIQUID_RISE_SECONDS = 1.4
COMPLETED_LIQUID_PEAK_SECONDS = 0.3
COMPLETED_LIQUID_SETTLE_SECONDS = 1.1
COMPLETED_LIQUID_SWEEP_SECONDS = (
    COMPLETED_LIQUID_REVEAL_SECONDS + COMPLETED_LIQUID_RISE_SECONDS
    + COMPLETED_LIQUID_PEAK_SECONDS + COMPLETED_LIQUID_SETTLE_SECONDS
)
# Matches the selected preview; vehicle tiers retain their independent scale.
COMPLETED_LIQUID_REDLINE = 400.0
_COLOR_STOPS = (
    (0.0, "#66B2E2"), (0.38, "#64D1BC"),
    (0.67, "#F1CD70"), (1.0, "#FF8958"),
)


def _color_mix(first: QColor, second: QColor, share: float) -> QColor:
    return QColor(*(
        round(a + (b - a) * share)
        for a, b in zip(first.getRgb()[:3], second.getRgb()[:3])
    ))


def _alpha(color: QColor, opacity: float) -> QColor:
    result = QColor(color)
    result.setAlphaF(max(0.0, min(1.0, opacity)))
    return result


def completed_liquid_heat(speed: object) -> float:
    return min(1.0, ((positive_output_speed(speed) or 0.0) / COMPLETED_LIQUID_REDLINE) ** 0.6)


def completed_liquid_intro_speed(
    average: object, minimum: object, maximum: object, elapsed: float,
) -> float | None:
    """Reveal at the session minimum, crest at its maximum, then settle."""
    mean = positive_output_speed(average)
    if mean is None:
        return None
    low = min(mean, positive_output_speed(minimum) or mean)
    high = max(mean, positive_output_speed(maximum) or mean)
    elapsed = max(0.0, elapsed - COMPLETED_LIQUID_REVEAL_SECONDS)
    if elapsed <= COMPLETED_LIQUID_RISE_SECONDS:
        progress = elapsed / COMPLETED_LIQUID_RISE_SECONDS
        eased = progress * progress * (3.0 - 2.0 * progress)
        return low + (high - low) * eased
    elapsed -= COMPLETED_LIQUID_RISE_SECONDS + COMPLETED_LIQUID_PEAK_SECONDS
    if elapsed <= 0.0:
        return high
    progress = min(1.0, elapsed / COMPLETED_LIQUID_SETTLE_SECONDS)
    eased = progress * progress * (3.0 - 2.0 * progress)
    return high + (mean - high) * eased


@lru_cache(maxsize=16)
def _liquid_samples(left: float, width: float, scale: float) -> tuple[tuple[float, float, float], ...]:
    samples = max(2, math.ceil(width / (2.0 * scale)))
    return tuple(
        (x, (x - left) / scale * 0.047, x / scale * 0.09)
        for index in range(samples + 1)
        for x in (left + width * index / samples,)
    )


def paint_completed_liquid(
    painter: QPainter,
    rect: QRectF,
    speed: object,
    *,
    elapsed: float = COMPLETED_LIQUID_REVEAL_SECONDS,
    light: bool = False,
    phase: float | None = None,
    bubble_phase: float | None = None,
) -> None:
    """Draw liquid below the readout, clipped to the original circular badge."""
    rate = positive_output_speed(speed)
    if rate is None:
        return
    heat = completed_liquid_heat(rate)
    color = QColor(_COLOR_STOPS[-1][1])
    for (low, first), (high, second) in zip(_COLOR_STOPS, _COLOR_STOPS[1:]):
        if heat <= high:
            color = _color_mix(QColor(first), QColor(second), (heat - low) / (high - low))
            break
    elapsed = max(0.0, elapsed)
    reveal = 1.0 - (1.0 - min(1.0, elapsed / COMPLETED_LIQUID_REVEAL_SECONDS)) ** 3
    inner = rect.adjusted(5.0, 5.0, -5.0, -5.0)
    center = inner.center()
    scale = rect.width() / 168.0
    level = inner.bottom() - inner.height() * (0.12 + 0.74 * heat * reveal)
    amplitude = (1.4 + 5.2 * heat) * scale
    phase = elapsed * (1.5 + 3.0 * heat) if phase is None else phase
    bubble_phase = elapsed * (0.22 + 0.16 * heat) if bubble_phase is None else bubble_phase
    clip = QPainterPath()
    clip.addEllipse(inner)
    painter.save()
    painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
    dark_color = _color_mix(color, QColor("#0D1B25"), 0.55)
    surface_color = _color_mix(color, QColor("#F7F1DD"), 0.36)
    for layer in (1, 0):
        surface = QPainterPath()
        for index, (x, first_angle, second_angle) in enumerate(_liquid_samples(inner.left(), inner.width(), scale)):
            y = (
                level + layer * 2.0 * scale
                + math.sin(first_angle + phase + layer * 1.4) * amplitude
                + math.sin(second_angle - phase * 0.73 + layer) * amplitude * 0.3
            )
            if index == 0:
                surface.moveTo(x, y)
            else:
                surface.lineTo(x, y)
        fill = QPainterPath(surface)
        fill.lineTo(inner.right(), inner.bottom())
        fill.lineTo(inner.left(), inner.bottom())
        fill.closeSubpath()
        gradient = QLinearGradient(0.0, level - 5.0 * scale, 0.0, inner.bottom())
        gradient.setColorAt(0.0, _alpha(color, 0.42 if light else (0.20 if layer else 0.39)))
        gradient.setColorAt(1.0, _alpha(dark_color, 0.24 if light else 0.32))
        painter.fillPath(fill, gradient)
        painter.setPen(QPen(
            _alpha(surface_color, 0.25 if layer else 0.83),
            (0.7 if layer else 1.0) * scale,
        ))
        painter.drawPath(surface)

    bubble_color = _color_mix(color, QColor("#F5F7EE"), 0.6)
    for index in range(11 + round(heat * 7)):
        progress = (bubble_phase + index * 0.177) % 1.0
        x = center.x() + math.sin(index * 13.7) * 60.0 * scale
        y = inner.bottom() - progress * (inner.bottom() - level + 14.0 * scale)
        if y < level + 2.0 * scale:
            continue
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(
            _alpha(bubble_color, math.sin(math.pi * progress) * 0.55),
            0.55 * scale,
        ))
        radius = (0.8 + index % 3 * 0.25) * scale
        painter.drawEllipse(QPointF(x + math.sin(elapsed * 1.6 + index) * scale, y), radius, radius)

    shade = QRadialGradient(center, inner.width() / 2.0)
    shade.setColorAt(0.0, QColor("#00000000"))
    shade.setColorAt(0.72, QColor("#00000000"))
    shade.setColorAt(1.0, QColor(56, 83, 106, 13) if light else QColor(0, 0, 0, 64))
    painter.fillPath(clip, shade)
    if not light:
        scrim = QRadialGradient(QPointF(center.x(), center.y() + 12.0 * scale), 59.0 * scale)
        scrim.setColorAt(0.0, QColor(0, 0, 0, 65))
        scrim.setColorAt(0.55, QColor(0, 0, 0, 48))
        scrim.setColorAt(1.0, QColor("#00000000"))
        painter.fillPath(clip, scrim)
    painter.restore()
