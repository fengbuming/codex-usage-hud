"""Shared output-speed projection for the renderer and desktop bubbles."""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core.parser import RequestTokens


def positive_output_speed(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        rate = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return rate if math.isfinite(rate) and rate > 0 else None


def output_speed_projection(request: RequestTokens) -> tuple[float | None, str]:
    rate = positive_output_speed(request.output_tokens_per_second)
    if rate is None:
        return None, "idle"
    if request.status in {"running", "confirmed"} and request.output_speed_estimated:
        return rate, "estimated"
    if request.status == "running" and request.estimated:
        return rate, "estimated"
    if request.status == "confirmed" and request.output_usage_complete:
        return rate, "measured"
    return None, "idle"


def format_output_speed(value: object) -> str:
    rate = positive_output_speed(value)
    if rate is None:
        return ""
    if rate >= 100:
        # Match Math.round in the renderer, including positive half ties.
        number = str(math.floor(rate + 0.5))
    else:
        precision = Decimal("0.1") if rate >= 10 else Decimal("0.01")
        number = str(Decimal.from_float(rate).quantize(precision, rounding=ROUND_HALF_UP))
    return f"{number} tok/s"
