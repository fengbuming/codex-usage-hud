"""Native animated rendering of the renderer HUD's existing vehicle line art."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
import re
import time

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ...renderer_assets.speed_titles import VEHICLE_ART
from ...speed_title_model import SPEED_TITLE_STEPS, SpeedTitleState, speed_title_color


def _line_art_path(data: str) -> QPainterPath:
    """Read the M/L/C/Z path subset used by the shared, trusted vehicle asset."""
    tokens = re.findall(r"[MLCZmlcz]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", data)
    path = QPainterPath()
    index, command = 0, ""
    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
        kind = command.upper()
        if kind == "Z":
            path.closeSubpath()
            continue
        size = 6 if kind == "C" else 2
        values = [float(v) for v in tokens[index:index + size]]
        index += size
        if command.islower():
            current = path.currentPosition()
            values = [v + (current.x() if n % 2 == 0 else current.y()) for n, v in enumerate(values)]
        if kind == "M":
            path.moveTo(*values)
            command = "l" if command.islower() else "L"
        elif kind == "L":
            path.lineTo(*values)
        elif kind == "C":
            path.cubicTo(*values)
        else:
            raise ValueError(f"Unsupported vehicle path command: {command}")
    return path


@dataclass(frozen=True)
class VehiclePart:
    path: QPainterPath
    role: str
    pivot: tuple[float, float] | None = None


@lru_cache(maxsize=13)
def vehicle_parts(key: str) -> tuple[VehiclePart, ...]:
    result = []
    wheel = crank = None
    leg = 0
    for part in VEHICLE_ART[key]["parts"]:
        role = part["role"]
        if "circle" in part:
            x, y, radius = part["circle"]
            path = QPainterPath()
            path.addEllipse(QPointF(x, y), radius, radius)
            if role == "wheel":
                wheel = (x, y)
            if role == "crank":
                crank = (x, y)
        else:
            path = _line_art_path(part["d"])
        pivot = None
        if role == "spoke":
            role, pivot = "wheel-motion", wheel
        elif role == "crank" and "d" in part:
            role, pivot = "crank-motion", crank
        elif role == "leg":
            role, pivot = f"leg-{leg}", tuple(part["origin"])
            leg += 1
        result.append(VehiclePart(path, role, pivot))
    return tuple(result)


class AnimatedSpeedIcon(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(22, 18)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._state = SpeedTitleState()
        self._epoch = time.monotonic()
        self._settle_due: float | None = None
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(33)
        self._animation_timer.timeout.connect(self.update)
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._settle)

    def set_speed(self, value: object, *, enabled: bool) -> None:
        deadline = self._state.update(value if enabled else None, now=time.monotonic())
        self.setVisible(enabled)
        self._sync_motion(deadline)
        self.update()

    def _settle(self) -> None:
        self._settle_due = None
        self._sync_motion(self._state.advance(now=time.monotonic()))
        self.update()

    def _sync_motion(self, deadline: float | None) -> None:
        if not self.isVisible():
            return
        if deadline != self._settle_due:
            self._settle_timer.stop()
            self._settle_due = deadline
            if deadline is not None:
                self._settle_timer.start(max(1, math.ceil((deadline - time.monotonic()) * 1000)))
        if self._state.tier:
            if not self._animation_timer.isActive():
                self._animation_timer.start()
        else:
            self._animation_timer.stop()
        label = SPEED_TITLE_STEPS[self._state.tier - 1][1] if self._state.tier else "待机"
        self.setToolTip(label)

    def showEvent(self, event: object) -> None:
        super().showEvent(event)
        self._sync_motion(self._state.advance(now=time.monotonic()))

    def hideEvent(self, event: object) -> None:
        self._animation_timer.stop()
        self._settle_timer.stop()
        self._settle_due = None
        super().hideEvent(event)

    def paintEvent(self, event: object) -> None:
        del event
        tier = self._state.tier
        key = SPEED_TITLE_STEPS[tier - 1][0] if tier else "walk"
        x, y, width, height = VEHICLE_ART[key]["viewBox"]
        scale = min((self.width() - 4) / width, (self.height() - 4) / height)
        t = time.monotonic() - self._epoch
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if tier:
            if key in {"bulletTrain", "maglev"}:
                painter.translate(2 * math.sin(t * math.tau / 0.85), -0.5)
            elif key in {"jetliner", "fighterJet", "hypersonic"}:
                painter.translate(0, 1.5 * math.cos(t * math.tau / 1.2))
            elif key == "rocket":
                painter.translate(math.sin(t * math.tau / 0.3), -math.sin(t * math.tau / 0.3))
            else:
                cycle = 0.8 if key == "walk" else 0.4
                painter.translate(0, -0.7 * (1 - math.cos(t * math.tau / cycle)))
        painter.translate((self.width() - width * scale) / 2, (self.height() - height * scale) / 2)
        painter.scale(scale, scale)
        painter.translate(-x, -y)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        color = QColor(speed_title_color(self._state.value or 0))
        for part in vehicle_parts(key):
            painter.save()
            role = part.role
            opacity = 0.72 if role in {"detail", "rim", "spoke", "wheel-motion"} else 1.0
            if tier and part.pivot:
                px, py = part.pivot
                if role.startswith("leg-"):
                    angle = 18 * math.sin(t * math.tau / 1.6) * (-1 if role == "leg-1" else 1)
                else:
                    cycle = 0.65
                    if role == "wheel-motion":
                        cycle = 0.3 if key in {"car", "sportsCar", "raceCar"} else 0.45 if key in {"ebike", "motorcycle"} else 0.65
                    angle = t / cycle * 360
                painter.translate(px, py)
                painter.rotate(angle)
                painter.translate(-px, -py)
            if tier and role in {"flame", "flameCore", "glow", "shock"}:
                flicker = (1 + math.sin(t * math.tau / 0.36 + (math.pi if role == "flameCore" else 0))) / 2
                opacity *= 0.55 + 0.45 * flicker
                if role in {"flame", "flameCore"}:
                    center = part.path.boundingRect().center()
                    painter.translate(center)
                    painter.scale(0.8 + 0.35 * flicker, 0.8 + 0.35 * flicker)
                    painter.translate(-center)
            ink = QColor(color)
            ink.setAlphaF(opacity)
            pen = QPen(ink, 0.9 if opacity < 1 else 1.25 if role == "wheel" else 1.45)
            pen.setCosmetic(True)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(part.path)
            painter.restore()
