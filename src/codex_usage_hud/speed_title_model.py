"""Shared vehicle tiers, colors and settling rules for both HUD surfaces."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .output_speed import positive_output_speed


SPEED_TITLE_REDLINE = 2000
SPEED_TITLE_STEPS = (
    ("walk", "步行", 0.5), ("bicycle", "自行车", 40),
    ("ebike", "电动自行车", 60), ("motorcycle", "摩托车", 80),
    ("car", "汽车", 120), ("sportsCar", "跑车", 160),
    ("bulletTrain", "高铁", 240), ("raceCar", "赛车", 340),
    ("maglev", "磁浮列车", 480), ("jetliner", "喷射客机", 680),
    ("fighterJet", "战斗机", 1000), ("hypersonic", "高超音速飞机", 1400),
    ("rocket", "火箭", 2000),
)
SPEED_TITLE_COLOR_STOPS = (
    (0, "#7C8798"), (0.1, "#2DD4BF"), (0.3, "#38BDF8"), (0.48, "#818CF8"),
    (0.64, "#E879F9"), (0.78, "#FB7185"), (0.9, "#FB923C"), (1, "#FACC15"),
)


def speed_title_color(value: object) -> str:
    heat = min(1.0, ((positive_output_speed(value) or 0) / SPEED_TITLE_REDLINE) ** 0.6)
    for (lower, a), (upper, b) in zip(SPEED_TITLE_COLOR_STOPS, SPEED_TITLE_COLOR_STOPS[1:]):
        if heat > upper:
            continue
        share = (heat - lower) / (upper - lower)
        channels = []
        for offset in (1, 3, 5):
            def linear(text: str) -> float:
                c = int(text[offset:offset + 2], 16) / 255
                return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
            c = linear(a) + (linear(b) - linear(a)) * share
            c = c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
            channels.append(math.floor(max(0, min(1, c)) * 255 + 0.5))
        return "#" + "".join(f"{c:02x}" for c in channels)
    return SPEED_TITLE_COLOR_STOPS[-1][1]


@dataclass
class SpeedTitleState:
    tier: int = 0
    value: float | None = None
    pending: int | None = None
    pending_at: float = 0.0
    idle_at: float | None = None

    def update(self, value: object, *, now: float) -> float | None:
        self.value = positive_output_speed(value)
        return self.advance(now=now)

    def advance(self, *, now: float) -> float | None:
        """Return the next settling deadline; no polling is needed while idle."""
        if self.value is None:
            self.tier, self.pending, self.idle_at = 0, None, None
            return None
        if self.value < 0.5:
            self.pending = None
            if self.tier == 0:
                return None
            if self.idle_at is None:
                self.idle_at = now
            deadline = self.idle_at + 3.0
            if now >= deadline:
                self.tier, self.idle_at = 0, None
                return None
            return deadline
        self.idle_at = None
        raw = next(
            index for index in range(len(SPEED_TITLE_STEPS), 0, -1)
            if self.value >= SPEED_TITLE_STEPS[index - 1][2]
        )
        candidate = raw
        if raw < self.tier:
            candidate = next(
                (index for index in range(self.tier, 1, -1)
                 if self.value >= SPEED_TITLE_STEPS[index - 1][2] * 0.9), 1,
            )
        if candidate == self.tier:
            self.pending = None
            return None
        if candidate != self.pending:
            self.pending, self.pending_at = candidate, now
        deadline = self.pending_at + 0.8
        if now >= deadline:
            self.tier, self.pending = candidate, None
            return None
        return deadline
