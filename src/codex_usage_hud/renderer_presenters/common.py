"""Pure number, money, rate, and time formatting for Renderer presenters."""

from __future__ import annotations

from datetime import datetime
from typing import Callable


MoneyFormatter = Callable[[float | None], str]
RateFormatter = Callable[[float | None, bool], str]
ShortNumberFormatter = Callable[[int | None], str]


def short_num(value: int | None) -> str:
    amount = int(value or 0)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 1_000_000:
        return f"{sign}{amount / 1_000_000:.1f}M"
    if amount >= 10_000:
        return f"{sign}{amount / 1_000:.0f}k"
    return f"{sign}{amount:,}"


def format_money(value: float | None) -> str:
    amount = max(0.0, float(value or 0.0))
    return f"${amount:.2f}"


def format_realtime_money(
    value: float | None,
    estimated: bool,
    *,
    money_formatter: MoneyFormatter = format_money,
) -> str:
    return f"{'~' if estimated else ''}{money_formatter(value)}"


def format_fixed_money(value: float | None, estimated: bool) -> str:
    amount = max(0.0, float(value or 0.0))
    marker = "~" if estimated else ""
    if amount < 100:
        return f"{marker}${amount:.2f}"
    return f"{marker}${amount:.1f}"


def fixed_token_total(
    value: int | None,
    *,
    short_formatter: ShortNumberFormatter = short_num,
) -> str:
    return short_formatter(value)


def format_usage_money(
    tokens: int | None,
    cost: float | None,
    *,
    short_formatter: ShortNumberFormatter = short_num,
    money_formatter: MoneyFormatter = format_money,
) -> str:
    money = "不可用" if cost is None else money_formatter(cost)
    return f"{short_formatter(tokens)}/{money}"


def format_time(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.astimezone().strftime("%m-%d %H:%M:%S")


def format_start(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.astimezone().strftime("%m-%d %H:%M")


def format_rate_marker(
    value: float | None,
    estimated: bool,
    *,
    value_formatter: RateFormatter | None = None,
) -> str:
    formatter = value_formatter or format_rate_value
    return f"◎{formatter(value, estimated)}"


def format_rate_value(value: float | None, estimated: bool) -> str:
    if value is None:
        return "-"
    clamped = max(0.0, min(float(value), 1.0))
    return f"{'~' if estimated else ''}{clamped:.0%}"


def duration_text(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    amount = max(0.0, float(seconds))
    if amount < 60:
        return f"{amount:.1f}s"
    minutes = int(amount // 60)
    seconds_left = int(amount % 60)
    if minutes < 60:
        return f"{minutes}m{seconds_left}s"
    hours = minutes // 60
    minutes_left = minutes % 60
    return f"{hours}h{minutes_left}m"


def timeline_time(value: datetime | None) -> str:
    if value is None:
        return "--:--"
    return value.astimezone().strftime("%H:%M:%S")


__all__ = [
    "duration_text",
    "fixed_token_total",
    "format_fixed_money",
    "format_money",
    "format_rate_marker",
    "format_rate_value",
    "format_realtime_money",
    "format_start",
    "format_time",
    "format_usage_money",
    "short_num",
    "timeline_time",
]
