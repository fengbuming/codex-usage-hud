"""User-facing configuration for codex-usage-hud."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Iterable, Mapping
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .core.calculator import MODEL_PRICES
from .pricing import (
    PriceAuditRecord,
    PriceVersion,
    PricingApplyResult,
    PricingImportPreview,
    apply_pricing_import as apply_pricing_import_preview,
    empty_pricing_template,
    minimal_price_example,
    normalize_price_audit,
    normalize_price_versions,
    parse_utc_datetime,
    preview_price_versions,
    preview_pricing_import as build_pricing_import_preview,
    pricing_export_payload,
    utc_now,
)
from .pricing_sync import OPENAI_MODELS_URL
from .pricing_sync import parse_openai_models_html, source_hash

HUD_SETTINGS_FILENAME = "hud_settings.json"
USER_CONFIG_KEY = "user"
RUNTIME_STATE_KEY = "runtime"
WARNING_DISMISSED_DATE_KEY = "warning_dismissed_date"
REST_REMINDER_STATE_KEY = "rest_reminder"
DEFAULT_DAILY_BUDGET_USD = 100.0
DEFAULT_WEEKLY_BUDGET_USD = 400.0
DEFAULT_BUDGET_THRESHOLDS = (0.5, 0.8, 0.9, 1.0)
DEFAULT_DAILY_RESET_TIME = "10:00"
DEFAULT_WEEKLY_RESET_WEEKDAY = 3
DEFAULT_WEEKLY_RESET_TIME = "10:00"
DEFAULT_DISPLAY_MODE = "renderer"
VALID_DISPLAY_MODES = {"renderer"}
DEFAULT_SUPPORT_URL = "https://github.com/fengbuming/codex-usage-hud"
DEFAULT_WORK_OVERLAY_MAX_ITEMS = 6
DEFAULT_WORK_OVERLAY_SIDE = "right"
VALID_WORK_OVERLAY_SIDES = {"left", "right"}
DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED = False
DEFAULT_STOP_HUD_ON_LOCK_SCREEN = False
DEFAULT_REST_REMINDER_ENABLED = False
DEFAULT_REST_REMINDER_INTERVAL_MINUTES = 45
DEFAULT_REST_REMINDER_BREAK_MINUTES = 2
DEFAULT_REST_REMINDER_POSTPONE_MINUTES = 10
DEFAULT_REST_REMINDER_IDLE_RESET_MINUTES = 0
DEFAULT_REST_REMINDER_WORK_START_TIME = "09:00"
DEFAULT_REST_REMINDER_WORK_END_TIME = "18:00"
DEFAULT_REST_REMINDER_LUNCH_ENABLED = True
DEFAULT_REST_REMINDER_LUNCH_START_TIME = "12:00"
DEFAULT_REST_REMINDER_LUNCH_END_TIME = "13:30"
REST_REMINDER_INTERVAL_MIN = 1
REST_REMINDER_INTERVAL_MAX = 180
REST_REMINDER_BREAK_MIN = 1
REST_REMINDER_BREAK_MAX = 10
REST_REMINDER_POSTPONE_MIN = 5
REST_REMINDER_POSTPONE_MAX = 30
REST_REMINDER_IDLE_RESET_MIN = 0
REST_REMINDER_IDLE_RESET_MAX = 60
JSON_WRITE_REPLACE_RETRIES = 8
JSON_WRITE_REPLACE_DELAY_SECONDS = 0.01
MAX_PRICING_RESPONSE_BYTES = 2 * 1024 * 1024


def _normalize_pricing_sync(value: Any) -> dict[str, Any]:
    defaults = {
        "enabled": True, "interval_hours": 4, "last_checked_at": "",
        "last_success_at": "", "last_result": "idle", "unread_change_count": 0,
        "source_url": OPENAI_MODELS_URL,
    }
    if not isinstance(value, Mapping):
        return defaults
    result = dict(defaults)
    result["enabled"] = bool(value.get("enabled", True))
    try:
        result["interval_hours"] = max(1, min(168, int(value.get("interval_hours", 24))))
    except (TypeError, ValueError):
        pass
    for key in ("last_checked_at", "last_success_at", "last_result", "snapshot_checked_at"):
        result[key] = str(value.get(key) or "")
    result["source_hash"] = str(value.get("source_hash") or "")
    result["last_error"] = str(value.get("last_error") or "")[:500]
    changes = value.get("pending_changes")
    result["pending_changes"] = list(changes[:500]) if isinstance(changes, list) else []
    pending_prices = value.get("pending_prices")
    result["pending_prices"] = list(pending_prices[:500]) if isinstance(pending_prices, list) else []
    try:
        result["unread_change_count"] = max(0, int(value.get("unread_change_count", 0)))
    except (TypeError, ValueError):
        pass
    source = str(value.get("source_url") or "").strip()
    result["source_url"] = source or OPENAI_MODELS_URL
    return result

_PRICE_ALIASES = {
    "input": ("input", "prompt", "input_price", "input_per_million"),
    "cached_input": (
        "cached_input",
        "cached",
        "cache",
        "cache_read",
        "cachedInput",
        "cached_input_price",
        "cached_input_per_million",
    ),
    "cache_write": (
        "cache_write",
        "cache_creation",
        "cacheWrite",
        "cache_write_price",
        "cache_creation_price",
        "cache_write_per_million",
        "cache_creation_per_million",
    ),
    "output": ("output", "completion", "output_price", "output_per_million"),
    "reasoning": (
        "reasoning",
        "reasoning_output",
        "reasoning_price",
        "reasoning_per_million",
    ),
}


@dataclass
class ModelPrice:
    """Per-million-token pricing for one model."""

    input: float
    cached_input: float
    output: float
    reasoning: float
    cache_write: float = 0.0
    model: str = ""
    provider: str = ""
    base_url: str = ""

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        model_hint: str = "",
    ) -> "ModelPrice | None":
        prices: dict[str, float] = {}
        for canonical, aliases in _PRICE_ALIASES.items():
            raw = _first_present(value, aliases)
            amount = _optional_float(raw)
            if amount is not None:
                prices[canonical] = max(0.0, amount)
        if "input" not in prices or "output" not in prices:
            return None
        cached_input = prices.get("cached_input", prices["input"])
        reasoning = prices.get("reasoning", prices["output"])
        model = _optional_str(
            value.get("model") or value.get("model_pattern") or value.get("pattern")
        ) or str(model_hint or "").strip()
        builtin_name = model.lower().rsplit("/", 1)[-1]
        builtin = MODEL_PRICES.get(builtin_name, {})
        cache_write = prices.get("cache_write", float(builtin.get("cache_write", 0.0)))
        return cls(
            input=prices["input"],
            cached_input=cached_input,
            output=prices["output"],
            reasoning=reasoning,
            cache_write=cache_write,
            model=model,
            provider=normalize_provider(value.get("provider")),
            base_url=normalize_base_url(
                value.get("base_url") or value.get("baseUrl") or value.get("api_base")
            ),
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "input": float(self.input),
            "cached_input": float(self.cached_input),
            "cache_write": float(self.cache_write),
            "output": float(self.output),
            "reasoning": float(self.reasoning),
        }
        if self.model:
            payload["model"] = self.model
        if self.provider:
            payload["provider"] = self.provider
        if self.base_url:
            payload["base_url"] = self.base_url
        return payload


def default_model_prices() -> dict[str, ModelPrice]:
    """Return the built-in model price table as config dataclasses."""
    return {
        name: ModelPrice.from_mapping(values, name) or ModelPrice(
            input=0.0,
            cached_input=0.0,
            output=0.0,
            reasoning=0.0,
        )
        for name, values in MODEL_PRICES.items()
    }


@dataclass
class ProviderSettings:
    """User-managed pricing and adjustment values for one billing provider."""

    model_prices: dict[str, ModelPrice] = field(default_factory=default_model_prices)
    pricing_url: str = ""
    weekly_adjustment_usd: float = 0.0

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        provider: str = "",
    ) -> "ProviderSettings | None":
        if not isinstance(value, Mapping):
            return None
        prices = default_model_prices()
        parsed_prices = normalize_model_prices(value.get("model_prices"))
        prices.update(_normalize_provider_model_prices(parsed_prices, provider))
        provider_pricing_url = _optional_str(value.get("pricing_url"))
        if not provider_pricing_url and provider == "openai":
            provider_pricing_url = OPENAI_MODELS_URL
        return cls(
            model_prices=prices,
            pricing_url=provider_pricing_url or "",
            weekly_adjustment_usd=max(0.0, _optional_float(value.get("weekly_adjustment_usd")) or 0.0),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "model_prices": {
                name: price.to_dict() for name, price in sorted(self.model_prices.items())
            },
            "pricing_url": self.pricing_url,
            "weekly_adjustment_usd": float(self.weekly_adjustment_usd),
        }


@dataclass
class UserConfig:
    """All user-editable runtime settings."""

    daily_budget_usd: float = DEFAULT_DAILY_BUDGET_USD
    weekly_budget_usd: float = DEFAULT_WEEKLY_BUDGET_USD
    daily_reset_time: str = DEFAULT_DAILY_RESET_TIME
    weekly_reset_weekday: int = DEFAULT_WEEKLY_RESET_WEEKDAY
    weekly_reset_time: str = DEFAULT_WEEKLY_RESET_TIME
    display_mode: str = DEFAULT_DISPLAY_MODE
    work_overlay_max_items: int = DEFAULT_WORK_OVERLAY_MAX_ITEMS
    work_overlay_side: str = DEFAULT_WORK_OVERLAY_SIDE
    model_prices: dict[str, ModelPrice] = field(default_factory=default_model_prices)
    pricing_versions: tuple[PriceVersion, ...] = ()
    pricing_audit: tuple[PriceAuditRecord, ...] = ()
    pricing_url: str = ""
    pricing_sync: dict[str, Any] = field(default_factory=lambda: {
        "enabled": True, "interval_hours": 4, "last_checked_at": "",
        "last_success_at": "", "last_result": "idle", "unread_change_count": 0,
        "source_url": OPENAI_MODELS_URL,
    })
    budget_thresholds: list[float] = field(
        default_factory=lambda: list(DEFAULT_BUDGET_THRESHOLDS)
    )
    weekly_adjustment_usd: float = 0.0
    provider_settings: dict[str, ProviderSettings] = field(default_factory=dict)
    provider_order: list[str] = field(default_factory=list)
    provider_scope_mode: str = "all"
    selected_providers: list[str] = field(default_factory=list)
    notification_only_providers: list[str] = field(default_factory=list)
    quick_launch_providers: list[str] = field(default_factory=list)
    support_url: str = DEFAULT_SUPPORT_URL
    stop_hud_on_lock_screen: bool = DEFAULT_STOP_HUD_ON_LOCK_SCREEN
    rest_reminder_enabled: bool = DEFAULT_REST_REMINDER_ENABLED
    rest_reminder_interval_minutes: int = DEFAULT_REST_REMINDER_INTERVAL_MINUTES
    rest_reminder_break_minutes: int = DEFAULT_REST_REMINDER_BREAK_MINUTES
    rest_reminder_postpone_minutes: int = DEFAULT_REST_REMINDER_POSTPONE_MINUTES
    rest_reminder_idle_reset_minutes: int = DEFAULT_REST_REMINDER_IDLE_RESET_MINUTES
    rest_reminder_work_start_time: str = DEFAULT_REST_REMINDER_WORK_START_TIME
    rest_reminder_work_end_time: str = DEFAULT_REST_REMINDER_WORK_END_TIME
    rest_reminder_lunch_enabled: bool = DEFAULT_REST_REMINDER_LUNCH_ENABLED
    rest_reminder_lunch_start_time: str = DEFAULT_REST_REMINDER_LUNCH_START_TIME
    rest_reminder_lunch_end_time: str = DEFAULT_REST_REMINDER_LUNCH_END_TIME
    # Progressive session-search coverage used for the initial warm index.
    session_search_range: str = "1m"

    @classmethod
    def defaults(cls) -> "UserConfig":
        return cls()

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        migration_at: datetime | None = None,
    ) -> "UserConfig":
        if not isinstance(value, Mapping):
            return cls.defaults()
        defaults = cls.defaults()
        prices = dict(defaults.model_prices)
        prices.update(normalize_model_prices(value.get("model_prices")))
        pricing_versions = normalize_price_versions(value.get("pricing_versions"))
        pricing_audit = normalize_price_audit(value.get("pricing_audit"))
        if "pricing_versions" not in value:
            migrated_versions, migrated_audit = _migrate_legacy_price_versions(
                value,
                migration_at=migration_at,
            )
            pricing_versions = migrated_versions
            pricing_audit = (*pricing_audit, *migrated_audit)
        legacy_overlay_enabled = _optional_bool(value.get("work_overlay_enabled"))
        work_overlay_max_items = normalize_work_overlay_max_items(
            value.get("work_overlay_max_items"),
            defaults.work_overlay_max_items,
        )
        if legacy_overlay_enabled is False:
            work_overlay_max_items = 0
        provider_settings = normalize_provider_settings(value.get("provider_settings"))
        scope_mode = str(value.get("provider_scope_mode") or "all").strip().lower()
        if scope_mode not in {"all", "custom"}:
            scope_mode = "all"
        selected_providers = normalize_provider_names(value.get("selected_providers"))
        notification_only_providers = normalize_provider_names(
            value.get("notification_only_providers")
        )
        quick_launch_providers = normalize_provider_names(
            value.get("quick_launch_providers")
        )
        stop_hud_on_lock_screen = _optional_bool(
            value.get("stop_hud_on_lock_screen")
        )
        if stop_hud_on_lock_screen is None:
            stop_hud_on_lock_screen = defaults.stop_hud_on_lock_screen
        if scope_mode == "all":
            notification_only_providers = []
        else:
            selected_provider_set = set(selected_providers)
            notification_only_providers = [
                provider
                for provider in notification_only_providers
                if provider not in selected_provider_set
            ]
        provider_pricing_url = _optional_str(value.get("pricing_url")) or ""
        return cls(
            daily_budget_usd=max(
                0.0,
                _optional_float(value.get("daily_budget_usd"))
                if value.get("daily_budget_usd") is not None
                else defaults.daily_budget_usd,
            ),
            weekly_budget_usd=max(
                0.0,
                _optional_float(value.get("weekly_budget_usd"))
                if value.get("weekly_budget_usd") is not None
                else defaults.weekly_budget_usd,
            ),
            daily_reset_time=normalize_time_text(
                value.get("daily_reset_time"), defaults.daily_reset_time
            ),
            weekly_reset_weekday=normalize_weekday(
                value.get("weekly_reset_weekday"), defaults.weekly_reset_weekday
            ),
            weekly_reset_time=normalize_time_text(
                value.get("weekly_reset_time"), defaults.weekly_reset_time
            ),
            display_mode=normalize_display_mode(value.get("display_mode")),
            work_overlay_max_items=work_overlay_max_items,
            work_overlay_side=normalize_work_overlay_side(value.get("work_overlay_side")),
            model_prices=prices,
            pricing_versions=pricing_versions,
            pricing_audit=pricing_audit,
            pricing_url=provider_pricing_url or "",
            pricing_sync=_normalize_pricing_sync(value.get("pricing_sync")),
            budget_thresholds=parse_thresholds(
                value.get("budget_thresholds"), defaults.budget_thresholds
            ),
            weekly_adjustment_usd=max(
                0.0,
                _optional_float(value.get("weekly_adjustment_usd"))
                if value.get("weekly_adjustment_usd") is not None
                else defaults.weekly_adjustment_usd,
            ),
            provider_settings=provider_settings,
            provider_order=normalize_provider_order(value.get("provider_order")),
            provider_scope_mode=scope_mode,
            selected_providers=selected_providers,
            notification_only_providers=notification_only_providers,
            quick_launch_providers=quick_launch_providers,
            support_url=_optional_str(value.get("support_url")) or DEFAULT_SUPPORT_URL,
            stop_hud_on_lock_screen=stop_hud_on_lock_screen,
            rest_reminder_enabled=_optional_bool(value.get("rest_reminder_enabled"))
            if value.get("rest_reminder_enabled") is not None
            else defaults.rest_reminder_enabled,
            rest_reminder_interval_minutes=_bounded_int(
                value.get("rest_reminder_interval_minutes"),
                defaults.rest_reminder_interval_minutes,
                minimum=REST_REMINDER_INTERVAL_MIN,
                maximum=REST_REMINDER_INTERVAL_MAX,
            ),
            rest_reminder_break_minutes=_bounded_int(
                value.get("rest_reminder_break_minutes"),
                defaults.rest_reminder_break_minutes,
                minimum=REST_REMINDER_BREAK_MIN,
                maximum=REST_REMINDER_BREAK_MAX,
            ),
            rest_reminder_postpone_minutes=_bounded_int(
                value.get("rest_reminder_postpone_minutes"),
                defaults.rest_reminder_postpone_minutes,
                minimum=REST_REMINDER_POSTPONE_MIN,
                maximum=REST_REMINDER_POSTPONE_MAX,
            ),
            # Legacy value is deliberately ignored: brief idle periods must not
            # silently reset a user's focus timer.
            rest_reminder_idle_reset_minutes=DEFAULT_REST_REMINDER_IDLE_RESET_MINUTES,
            rest_reminder_work_start_time=normalize_time_text(
                value.get("rest_reminder_work_start_time"),
                defaults.rest_reminder_work_start_time,
            ),
            rest_reminder_work_end_time=normalize_time_text(
                value.get("rest_reminder_work_end_time"),
                defaults.rest_reminder_work_end_time,
            ),
            rest_reminder_lunch_enabled=(
                _optional_bool(value.get("rest_reminder_lunch_enabled"))
                if value.get("rest_reminder_lunch_enabled") is not None
                else defaults.rest_reminder_lunch_enabled
            ),
            rest_reminder_lunch_start_time=normalize_time_text(
                value.get("rest_reminder_lunch_start_time"),
                defaults.rest_reminder_lunch_start_time,
            ),
            rest_reminder_lunch_end_time=normalize_time_text(
                value.get("rest_reminder_lunch_end_time"),
                defaults.rest_reminder_lunch_end_time,
            ),
            session_search_range=(
                str(value.get("session_search_range") or defaults.session_search_range)
                .strip().casefold()
                if str(value.get("session_search_range") or defaults.session_search_range).strip().casefold()
                in {"1m", "3m", "6m", "1y", "all"}
                else defaults.session_search_range
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "daily_budget_usd": float(self.daily_budget_usd),
            "weekly_budget_usd": float(self.weekly_budget_usd),
            "daily_reset_time": self.daily_reset_time,
            "weekly_reset_weekday": int(self.weekly_reset_weekday),
            "weekly_reset_time": self.weekly_reset_time,
            "display_mode": self.display_mode,
            "work_overlay_max_items": int(self.work_overlay_max_items),
            "work_overlay_side": self.work_overlay_side,
            "pricing_url": self.pricing_url,
            "pricing_sync": dict(self.pricing_sync),
            "budget_thresholds": list(self.budget_thresholds),
            "weekly_adjustment_usd": float(self.weekly_adjustment_usd),
            "provider_settings": {
                provider: settings.to_dict()
                for provider, settings in self.provider_settings.items()
            },
            "provider_order": list(self.provider_order),
            "provider_scope_mode": self.provider_scope_mode,
            "selected_providers": list(self.selected_providers),
            "notification_only_providers": list(self.notification_only_providers),
            "quick_launch_providers": list(self.quick_launch_providers),
            "support_url": self.support_url,
            "stop_hud_on_lock_screen": bool(self.stop_hud_on_lock_screen),
            "rest_reminder_enabled": bool(self.rest_reminder_enabled),
            "rest_reminder_interval_minutes": int(self.rest_reminder_interval_minutes),
            "rest_reminder_break_minutes": int(self.rest_reminder_break_minutes),
            "rest_reminder_postpone_minutes": int(self.rest_reminder_postpone_minutes),
            "rest_reminder_idle_reset_minutes": int(
                self.rest_reminder_idle_reset_minutes
            ),
            "rest_reminder_work_start_time": self.rest_reminder_work_start_time,
            "rest_reminder_work_end_time": self.rest_reminder_work_end_time,
            "rest_reminder_lunch_enabled": bool(self.rest_reminder_lunch_enabled),
            "rest_reminder_lunch_start_time": self.rest_reminder_lunch_start_time,
            "rest_reminder_lunch_end_time": self.rest_reminder_lunch_end_time,
            "session_search_range": self.session_search_range,
            "model_prices": {
                name: price.to_dict()
                for name, price in sorted(self.model_prices.items())
            },
            "pricing_versions": [
                version.to_dict() for version in self.pricing_versions
            ],
            "pricing_audit": [record.to_dict() for record in self.pricing_audit],
        }

    def export_pricing_payload(self) -> dict[str, object]:
        """Return every immutable price version in the portable schema-v1 shape.

        Fresh configurations expose builtin prices before the first versioned
        save.  Represent those visible prices as non-persisted migration rows
        during export so the generated JSON is useful immediately and can be
        imported without losing the table shown in settings.
        """
        versions = self.pricing_versions
        if not versions:
            versions, _audit = _migrate_legacy_price_versions(
                self.to_dict(),
                migration_at=utc_now(),
            )
        return pricing_export_payload(versions)

    @staticmethod
    def empty_pricing_template() -> dict[str, object]:
        return empty_pricing_template()

    @staticmethod
    def minimal_price_example() -> dict[str, object]:
        return minimal_price_example()

    def preview_pricing_import(
        self,
        payload: str | bytes | Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> PricingImportPreview:
        return build_pricing_import_preview(
            self.pricing_versions,
            payload,
            now=now,
        )

    def apply_pricing_import(
        self,
        preview: PricingImportPreview,
        *,
        conflict_policy: str = "cancel",
        applied_at: datetime | None = None,
    ) -> tuple["UserConfig", PricingApplyResult]:
        """Apply a previously validated preview without mutating this config."""
        result = apply_pricing_import_preview(
            self.pricing_versions,
            preview,
            conflict_policy=conflict_policy,
            applied_at=applied_at,
        )
        return (
            replace(
                self,
                pricing_versions=result.versions,
                pricing_audit=(*self.pricing_audit, *result.audit),
            ),
            result,
        )

    def apply_price_updates(
        self,
        prices: Mapping[str, ModelPrice | Mapping[str, Any]],
        *,
        effective_at: datetime | str,
        provider: str = "",
        created_at: datetime | None = None,
    ) -> tuple["UserConfig", PricingApplyResult]:
        """Validate and atomically append one immutable version per edited row."""
        current = parse_utc_datetime(created_at or utc_now(), field_name="created_at")
        effective = parse_utc_datetime(effective_at, field_name="effective_at")
        if effective > current:
            raise ValueError("effective_at must not be in the future")
        normalized_provider = normalize_provider(provider)
        incoming: list[PriceVersion] = []
        normalized_prices: dict[str, ModelPrice] = {}
        for key, raw_price in prices.items():
            model_key = str(key or "").strip()
            if not model_key:
                raise ValueError("price update model key is required")
            if isinstance(raw_price, ModelPrice):
                price = raw_price
                payload = price.to_dict()
            elif isinstance(raw_price, Mapping):
                price = ModelPrice.from_mapping(raw_price, model_key)
                if price is None:
                    raise ValueError(f"invalid price update for {model_key}")
                payload = dict(raw_price)
            else:
                raise ValueError(f"invalid price update for {model_key}")
            normalized_prices[model_key] = price
            payload["model"] = str(
                payload.get("model") or payload.get("model_pattern") or model_key
            ).strip()
            payload["provider"] = normalized_provider or normalize_provider(
                payload.get("provider")
            )
            payload["effective_at"] = effective
            payload["created_at"] = current
            payload["created_by"] = "user_edit"
            payload["source"] = "manual"
            incoming.append(PriceVersion.from_mapping(payload, now=current))
        preview = preview_price_versions(self.pricing_versions, incoming)
        result = apply_pricing_import_preview(
            self.pricing_versions,
            preview,
            conflict_policy="overwrite",
            applied_at=current,
        )
        legacy_updated = self.with_price_updates(
            normalized_prices,
            provider=normalized_provider,
        )
        return (
            replace(
                legacy_updated,
                pricing_versions=result.versions,
                pricing_audit=(*self.pricing_audit, *result.audit),
            ),
            result,
        )

    def with_price_version_updates(
        self,
        prices: Mapping[str, ModelPrice | Mapping[str, Any]],
        *,
        effective_at: datetime | str,
        provider: str = "",
        created_at: datetime | None = None,
    ) -> "UserConfig":
        updated, _result = self.apply_price_updates(
            prices,
            effective_at=effective_at,
            provider=provider,
            created_at=created_at,
        )
        return updated

    def price_table(self) -> dict[str, dict[str, object]]:
        table = {name: price.to_dict() for name, price in self.model_prices.items()}
        for provider, settings in self.provider_settings.items():
            for name, price in settings.model_prices.items():
                table[f"{provider}/{name}"] = {
                    **price.to_dict(),
                    "model": price.model or name,
                    "provider": provider,
                }
        return table

    def provider_price_table(self, provider: str) -> dict[str, dict[str, object]]:
        """Return one provider's price table, preserving legacy global settings as fallback."""
        normalized_provider = normalize_provider(provider)
        settings = self.provider_settings.get(normalized_provider)
        prices = settings.model_prices if settings is not None else self.model_prices
        return {
            name: {**price.to_dict(), "provider": normalized_provider}
            for name, price in prices.items()
        }

    def effective_provider_scope(
        self,
        app_provider: str = "",
    ) -> frozenset[str] | None:
        """Return the selected provider set, or ``None`` for the all-provider mode."""
        if self.provider_scope_mode != "custom":
            return None
        selected = set(normalize_provider_names(self.selected_providers))
        required_provider = normalize_provider(app_provider)
        if required_provider and required_provider != "unknown":
            selected.add(required_provider)
        return frozenset(selected)

    def effective_notification_provider_scope(
        self,
        app_provider: str = "",
    ) -> frozenset[str] | None:
        """Return providers whose active work should produce notification bubbles."""
        included = self.effective_provider_scope(app_provider)
        if included is None:
            return None
        return frozenset(
            set(included)
            | set(normalize_provider_names(self.notification_only_providers))
        )

    def weekly_adjustment_for_scope(
        self,
        providers: Iterable[str] | None,
    ) -> float:
        """Return provider adjustments for the same scope used by usage aggregation."""
        if not self.provider_settings:
            return max(0.0, float(self.weekly_adjustment_usd))
        scope = None if providers is None else set(normalize_provider_names(providers))
        return round(
            sum(
                max(0.0, float(settings.weekly_adjustment_usd))
                for provider, settings in self.provider_settings.items()
                if scope is None or provider in scope
            ),
            6,
        )

    def migrate_legacy_provider_settings(
        self,
        providers: Iterable[str],
        *,
        app_provider: str = "",
    ) -> "UserConfig":
        """Materialize provider tables and defaults once providers are known.

        Older settings only had one global price table.  Keep that migration
        path for configurations with no provider tables, but append a clean
        default table whenever a provider appears after the first migration.
        This keeps newly discovered providers independent from legacy global
        rows that may contain provider-prefixed compatibility entries.
        """
        targets: list[str] = []
        seen: set[str] = set()
        for item in providers:
            provider = normalize_provider(item)
            if not provider or provider == "unknown" or provider in seen:
                continue
            seen.add(provider)
            targets.append(provider)
        if not targets:
            return self
        required_provider = normalize_provider(app_provider)
        provider_order: list[str] = []
        order_seen: set[str] = set()

        def append_provider_order(values: Iterable[str]) -> None:
            for item in values:
                provider = normalize_provider(item)
                if not provider or provider in order_seen:
                    continue
                order_seen.add(provider)
                provider_order.append(provider)

        append_provider_order(self.provider_order)
        append_provider_order(self.provider_settings)
        append_provider_order(targets)
        if not self.provider_settings:
            scoped: dict[str, dict[str, ModelPrice]] = {
                provider: {} for provider in targets
            }
            for name, price in self.model_prices.items():
                explicit_provider = normalize_provider(price.provider)
                if explicit_provider:
                    if explicit_provider in scoped:
                        scoped[explicit_provider][name] = replace(
                            price, provider=explicit_provider
                        )
                    continue
                for provider in targets:
                    scoped[provider][name] = replace(price, provider=provider)
            settings = {
                provider: ProviderSettings(
                    model_prices=prices or default_model_prices(),
                    pricing_url=self.pricing_url,
                    weekly_adjustment_usd=(
                        self.weekly_adjustment_usd
                        if provider == required_provider
                        else 0.0
                    ),
                )
                for provider, prices in scoped.items()
            }
            notification_only = list(self.notification_only_providers)
            if self.provider_scope_mode == "custom":
                selected = set(normalize_provider_names(self.selected_providers))
                notification_only.extend(
                    provider
                    for provider in targets
                    if provider != required_provider and provider not in selected
                )
            return replace(
                self,
                provider_settings=settings,
                provider_order=provider_order,
                notification_only_providers=normalize_provider_names(notification_only),
            )

        settings = dict(self.provider_settings)
        added: list[str] = []
        for provider in targets:
            if provider in settings:
                continue
            settings[provider] = ProviderSettings()
            added.append(provider)
        if not added and provider_order == self.provider_order:
            return self

        notification_only = list(self.notification_only_providers)
        if added and self.provider_scope_mode == "custom":
            selected = set(normalize_provider_names(self.selected_providers))
            notification_only.extend(
                provider
                for provider in added
                if provider != required_provider and provider not in selected
            )
        return replace(
            self,
            provider_settings=settings,
            provider_order=provider_order,
            notification_only_providers=normalize_provider_names(notification_only),
        )

    def with_price_updates(
        self,
        prices: Mapping[str, ModelPrice],
        *,
        pricing_url: str | None = None,
        provider: str | None = None,
    ) -> "UserConfig":
        normalized_provider = normalize_provider(provider)
        if normalized_provider:
            settings = self.provider_settings.get(normalized_provider, ProviderSettings())
            next_prices = dict(settings.model_prices)
            next_prices.update(prices)
            next_settings = dict(self.provider_settings)
            next_settings[normalized_provider] = ProviderSettings(
                model_prices=next_prices,
                pricing_url=settings.pricing_url if pricing_url is None else pricing_url,
                weekly_adjustment_usd=settings.weekly_adjustment_usd,
            )
            return replace(self, provider_settings=next_settings)
        next_prices = dict(self.model_prices)
        next_prices.update(prices)
        return replace(
            self,
            model_prices=next_prices,
            pricing_url=self.pricing_url if pricing_url is None else pricing_url,
        )


class UserConfigStore:
    """Read and write user config while preserving HUD geometry keys."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_settings_path()

    def load(self) -> UserConfig:
        raw = read_json_object(self.path)
        raw_user = raw.get(USER_CONFIG_KEY)
        file_mtime = self.mtime()
        migration_at = (
            datetime.fromtimestamp(file_mtime, tz=timezone.utc)
            if file_mtime is not None
            else None
        )
        config = UserConfig.from_dict(raw_user, migration_at=migration_at)
        if (
            isinstance(raw_user, Mapping)
            and "pricing_versions" not in raw_user
            and config.pricing_versions
        ):
            raw[USER_CONFIG_KEY] = config.to_dict()
            try:
                write_json_object(self.path, raw)
            except OSError:
                pass
        return config

    def save(self, config: UserConfig) -> None:
        raw = read_json_object(self.path)
        raw[USER_CONFIG_KEY] = config.to_dict()
        write_json_object(self.path, raw)

    def mtime(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None


def default_settings_path() -> Path:
    """Return the per-user shared HUD settings path."""
    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
        return base / "codex-usage-hud" / HUD_SETTINGS_FILENAME
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "codex-usage-hud"
            / HUD_SETTINGS_FILENAME
        )
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "codex-usage-hud" / HUD_SETTINGS_FILENAME


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_json_object(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(
        json.dumps(dict(value), indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    last_error: OSError | None = None
    for attempt in range(JSON_WRITE_REPLACE_RETRIES):
        try:
            temp_path.replace(path)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt + 1 >= JSON_WRITE_REPLACE_RETRIES:
                break
            time.sleep(JSON_WRITE_REPLACE_DELAY_SECONDS)
    if last_error is not None:
        raise last_error


def local_date_key(now: datetime | None = None) -> str:
    """Return the local calendar date used for per-day UI state."""
    current = (now or datetime.now()).astimezone()
    return current.date().isoformat()


def warning_dismissed_today(
    path: Path | str | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether the expanded warning banner is dismissed for today."""
    settings_path = Path(path) if path is not None else default_settings_path()
    raw = read_json_object(settings_path)
    runtime = raw.get(RUNTIME_STATE_KEY)
    if not isinstance(runtime, Mapping):
        return False
    return str(runtime.get(WARNING_DISMISSED_DATE_KEY) or "") == local_date_key(now)


def dismiss_warning_for_today(
    path: Path | str | None = None,
    *,
    now: datetime | None = None,
) -> None:
    """Persist that the expanded warning banner should stay hidden today."""
    settings_path = Path(path) if path is not None else default_settings_path()
    raw = read_json_object(settings_path)
    runtime = raw.get(RUNTIME_STATE_KEY)
    if not isinstance(runtime, dict):
        runtime = {}
    runtime[WARNING_DISMISSED_DATE_KEY] = local_date_key(now)
    raw[RUNTIME_STATE_KEY] = runtime
    write_json_object(settings_path, raw)


def load_rest_reminder_state(path: Path | str | None = None) -> dict[str, Any]:
    """Load process-restart timing for the focus rest reminder from runtime state."""
    settings_path = Path(path) if path is not None else default_settings_path()
    raw = read_json_object(settings_path)
    runtime = raw.get(RUNTIME_STATE_KEY)
    if not isinstance(runtime, Mapping):
        return {}
    state = runtime.get(REST_REMINDER_STATE_KEY)
    return dict(state) if isinstance(state, Mapping) else {}


def save_rest_reminder_state(
    state: Mapping[str, Any] | None,
    path: Path | str | None = None,
) -> None:
    """Persist rest-reminder cycle wall-clock timing under the runtime section."""
    settings_path = Path(path) if path is not None else default_settings_path()
    raw = read_json_object(settings_path)
    runtime = raw.get(RUNTIME_STATE_KEY)
    if not isinstance(runtime, dict):
        runtime = {}
    if state is None:
        runtime.pop(REST_REMINDER_STATE_KEY, None)
    else:
        runtime[REST_REMINDER_STATE_KEY] = dict(state)
    raw[RUNTIME_STATE_KEY] = runtime
    write_json_object(settings_path, raw)


def parse_thresholds(
    value: Any,
    default: list[float] | tuple[float, ...] | None = None,
) -> list[float]:
    """Parse budget warning thresholds from CSV, percent, fraction, or list."""
    source: list[Any]
    if isinstance(value, str):
        source = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        source = list(value)
    else:
        source = list(default or DEFAULT_BUDGET_THRESHOLDS)
    thresholds: list[float] = []
    for part in source:
        amount = _optional_float(part)
        if amount is None:
            continue
        if amount > 1:
            amount /= 100.0
        if amount > 0:
            thresholds.append(amount)
    return sorted(set(thresholds)) or list(default or DEFAULT_BUDGET_THRESHOLDS)


def normalize_provider(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text


def normalize_provider_names(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return sorted({provider for item in value if (provider := normalize_provider(item))})


def normalize_provider_order(value: Any) -> list[str]:
    """Normalize a persisted provider order while preserving first occurrence."""
    if not isinstance(value, (list, tuple)):
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for item in value:
        provider = normalize_provider(item)
        if not provider or provider in seen:
            continue
        seen.add(provider)
        ordered.append(provider)
    return ordered


def normalize_provider_settings(value: Any) -> dict[str, ProviderSettings]:
    if not isinstance(value, Mapping):
        return {}
    settings: dict[str, ProviderSettings] = {}
    for raw_provider, raw_settings in value.items():
        provider = normalize_provider(raw_provider)
        parsed = ProviderSettings.from_dict(raw_settings, provider=provider)
        if provider and parsed is not None:
            settings[provider] = parsed
    return settings


def _normalize_provider_model_prices(
    prices: Mapping[str, ModelPrice],
    provider: str,
) -> dict[str, ModelPrice]:
    """Collapse duplicate model rows inside one provider table.

    A legacy global table can contain both ``model`` and
    ``provider/model`` keys for the same model.  Once a table is scoped to one
    provider, those rows must compete for one model identity; otherwise a new
    provider inherits two visible rows for every model.
    """
    normalized_provider = normalize_provider(provider)
    if not normalized_provider:
        return dict(prices)

    normalized: dict[str, ModelPrice] = {}
    priorities: dict[tuple[str, str], int] = {}
    identity_keys: dict[tuple[str, str], str] = {}
    for key, price in prices.items():
        model = str(price.model or "").strip()
        if not model:
            normalized[key] = price
            continue
        explicit_provider = normalize_provider(price.provider)
        scoped_key = f"{normalized_provider}/{model}"
        is_current_provider_row = (
            explicit_provider == normalized_provider
            or (not explicit_provider and key.casefold() == scoped_key.casefold())
        )
        base_url = normalize_base_url(price.base_url)
        identity = (model.casefold(), base_url)
        if base_url:
            # Keep the scope-bearing key for Base URL-specific rows.  A
            # provider/model row and an unscoped model row may legitimately
            # share the model name while targeting different endpoints.
            canonical_key = key
        else:
            canonical_key = model
        priority = (
            3
            if explicit_provider == normalized_provider
            else 2
            if is_current_provider_row
            else 1
            if not explicit_provider
            else 0
        )
        previous_key = identity_keys.get(identity)
        previous_priority = priorities.get(identity, -1)
        if previous_key is not None and priority < previous_priority:
            continue
        if previous_key is not None and previous_key != canonical_key:
            normalized.pop(previous_key, None)
        normalized[canonical_key] = price
        priorities[identity] = priority
        identity_keys[identity] = canonical_key
    return normalized


def normalize_base_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    if not parts.scheme or not parts.netloc:
        return text.rstrip("/").lower()
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            "",
            "",
        )
    )


def normalize_model_prices(value: Any) -> dict[str, ModelPrice]:
    """Normalize a model price payload into the internal table."""
    prices: dict[str, ModelPrice] = {}
    if not isinstance(value, Mapping):
        return prices
    for name, raw_price in value.items():
        model = str(name or "").strip()
        if not model or not isinstance(raw_price, Mapping):
            continue
        price = ModelPrice.from_mapping(raw_price, model)
        if price is not None:
            prices[model] = price
    return prices


def _migrate_legacy_price_versions(
    value: Mapping[str, Any],
    *,
    migration_at: datetime | None = None,
) -> tuple[tuple[PriceVersion, ...], tuple[PriceAuditRecord, ...]]:
    """Turn explicitly persisted legacy rows into deterministic migration versions."""
    current = parse_utc_datetime(migration_at or utc_now(), field_name="migration_at")
    candidates: list[tuple[str, str, ModelPrice]] = []
    for key, price in normalize_model_prices(value.get("model_prices")).items():
        candidates.append((key, normalize_provider(price.provider), price))

    raw_provider_settings = value.get("provider_settings")
    if isinstance(raw_provider_settings, Mapping):
        for raw_provider, raw_settings in raw_provider_settings.items():
            provider = normalize_provider(raw_provider)
            if not provider or not isinstance(raw_settings, Mapping):
                continue
            for key, price in normalize_model_prices(
                raw_settings.get("model_prices")
            ).items():
                candidates.append((key, provider, price))

    by_conflict: dict[tuple[str, str, str, datetime], PriceVersion] = {}
    for key, provider, price in candidates:
        payload = price.to_dict()
        payload.update(
            {
                "model": price.model or key,
                "provider": provider,
                "effective_at": current,
                "created_at": current,
                "created_by": "builtin_migration",
                "source": "builtin",
            }
        )
        version = PriceVersion.from_mapping(
            payload,
            now=current,
            default_created_by="builtin_migration",
            default_source="builtin",
            deterministic_id=True,
        )
        by_conflict[version.conflict_key] = version

    versions = tuple(
        sorted(
            by_conflict.values(),
            key=lambda item: (
                item.provider,
                item.base_url,
                item.match_pattern.lower(),
                item.effective_at,
            ),
        )
    )
    audit = tuple(
        PriceAuditRecord(
            audit_id=f"migration:{version.version_id}",
            action="migrate",
            version_id=version.version_id,
            occurred_at=current,
            created_by="builtin_migration",
        )
        for version in versions
    )
    return versions, audit


def fetch_model_prices(url: str, timeout_seconds: float = 8.0) -> dict[str, ModelPrice]:
    """Fetch and normalize a bounded JSON model-pricing table over HTTP(S)."""
    target = str(url or "").strip()
    if not target:
        raise ValueError("pricing URL is empty")
    parsed_target = urlsplit(target)
    if parsed_target.scheme.lower() not in {"http", "https"} or not parsed_target.netloc:
        raise ValueError("pricing URL must use HTTP(S)")
    request = Request(
        target,
        headers={
            "Accept": "application/json",
            "User-Agent": "codex-usage-hud",
        },
    )
    try:
        with urlopen(request, timeout=max(1.0, timeout_seconds)) as response:
            body = response.read(MAX_PRICING_RESPONSE_BYTES + 1)
            if len(body) > MAX_PRICING_RESPONSE_BYTES:
                raise ValueError("pricing response is too large")
            payload = json.loads(body.decode("utf-8"))
    except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to fetch pricing: {exc}") from exc
    prices = extract_model_prices(payload)
    if not prices:
        raise ValueError("pricing payload did not contain supported model prices")
    return prices


def fetch_openai_models_prices(url: str = OPENAI_MODELS_URL, timeout_seconds: float = 15.0) -> tuple[dict[str, ModelPrice], dict[str, object]]:
    """Fetch the official HTML page with bounded, read-only failure protection."""
    target = str(url or OPENAI_MODELS_URL).strip()
    parsed_target = urlsplit(target)
    if parsed_target.scheme.lower() != "https" or not parsed_target.netloc:
        raise ValueError("official pricing URL must use HTTPS")
    request = Request(target, headers={"Accept": "text/html", "User-Agent": "codex-usage-hud"})
    try:
        with urlopen(request, timeout=max(1.0, timeout_seconds)) as response:
            body = response.read(MAX_PRICING_RESPONSE_BYTES + 1)
            if len(body) > MAX_PRICING_RESPONSE_BYTES:
                raise ValueError("pricing response is too large")
            prices = parse_openai_models_html(body)
    except (OSError, URLError, UnicodeDecodeError) as exc:
        raise ValueError(f"unable to fetch official pricing: {exc}") from exc
    metadata = {"source_url": target, "source_hash": source_hash(body), "checked_at": utc_now().isoformat().replace("+00:00", "Z")}
    return {
        model: ModelPrice(input=float(price.input), cached_input=float(price.cached_input),
                          cache_write=float(price.cache_write), output=float(price.output),
                          reasoning=float(price.reasoning), model=model, provider="openai")
        for model, price in prices.items()
    }, metadata


def extract_model_prices(payload: Any) -> dict[str, ModelPrice]:
    """Extract model prices from common top-level JSON shapes."""
    if not isinstance(payload, Mapping):
        return {}
    for key in ("model_prices", "prices", "pricing", "models"):
        extracted = _extract_price_collection(payload.get(key))
        if extracted:
            return extracted
    return _extract_price_collection(payload)


def normalize_time_text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) < 2:
        return default
    hour = _optional_int(parts[0])
    minute = _optional_int(parts[1])
    if hour is None or minute is None or not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return f"{hour:02d}:{minute:02d}"


def time_parts(value: str) -> tuple[int, int]:
    text = normalize_time_text(value, DEFAULT_DAILY_RESET_TIME)
    hour, minute = text.split(":", 1)
    return int(hour), int(minute)


def normalize_weekday(value: Any, default: int = DEFAULT_WEEKLY_RESET_WEEKDAY) -> int:
    if isinstance(value, str):
        text = value.strip().lower()
        names = {
            "monday": 0,
            "mon": 0,
            "tuesday": 1,
            "tue": 1,
            "wednesday": 2,
            "wed": 2,
            "thursday": 3,
            "thu": 3,
            "friday": 4,
            "fri": 4,
            "saturday": 5,
            "sat": 5,
            "sunday": 6,
            "sun": 6,
        }
        if text in names:
            return names[text]
    weekday = _optional_int(value)
    if weekday is None or not (0 <= weekday <= 6):
        return default
    return weekday


def normalize_display_mode(value: Any) -> str:
    mode = str(value or DEFAULT_DISPLAY_MODE).strip().lower().replace("-", "_")
    legacy_renderer_aliases = {"auto", "inject", "injection", "renderer_hud"}
    legacy_qt_aliases = {"qt", "qt_hud", "pyside", "pyside6"}
    legacy_tk_aliases = {"tk", "tkinter", "tk_hud"}
    if mode in legacy_renderer_aliases | legacy_qt_aliases | legacy_tk_aliases:
        return "renderer"
    return mode if mode in VALID_DISPLAY_MODES else DEFAULT_DISPLAY_MODE


def effective_display_mode(value: Any) -> str:
    """Collapse a configured display preference into the active HUD surface."""
    del value
    return "renderer"


def normalize_work_overlay_max_items(
    value: Any,
    default: int = DEFAULT_WORK_OVERLAY_MAX_ITEMS,
    *,
    max_items: int | None = None,
) -> int:
    amount = _optional_int(value)
    if amount is None:
        amount = int(default)
    amount = max(0, int(amount))
    if max_items is None:
        return amount
    return min(amount, max(0, int(max_items)))


def normalize_work_overlay_side(value: Any) -> str:
    side = str(value or DEFAULT_WORK_OVERLAY_SIDE).strip().lower().replace("-", "_")
    return side if side in VALID_WORK_OVERLAY_SIDES else DEFAULT_WORK_OVERLAY_SIDE


def _extract_price_collection(value: Any) -> dict[str, ModelPrice]:
    prices: dict[str, ModelPrice] = {}
    if isinstance(value, Mapping):
        for name, raw in value.items():
            if not isinstance(raw, Mapping):
                continue
            model = str(raw.get("id") or raw.get("model") or raw.get("name") or name).strip()
            price = ModelPrice.from_mapping(
                raw.get("pricing") or raw.get("prices") or raw,
                model,
            )
            if model and price is not None:
                prices[model] = price
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, Mapping):
                continue
            model = str(item.get("id") or item.get("model") or item.get("name") or "").strip()
            price = ModelPrice.from_mapping(
                item.get("pricing") or item.get("prices") or item,
                model,
            )
            if model and price is not None:
                prices[model] = price
    return prices


def _first_present(value: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return None


def _optional_int(value: Any) -> int | None:
    try:
        return None if value is None or value == "" else int(value)
    except (TypeError, ValueError):
        return None


def _bounded_int(
    value: Any,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    parsed = _optional_int(value)
    if parsed is None:
        parsed = int(default)
    return max(int(minimum), min(int(maximum), parsed))


__all__ = [
    "DEFAULT_BUDGET_THRESHOLDS",
    "DEFAULT_DAILY_BUDGET_USD",
    "DEFAULT_DAILY_RESET_TIME",
    "DEFAULT_DISPLAY_MODE",
    "DEFAULT_STOP_HUD_ON_LOCK_SCREEN",
    "DEFAULT_SUPPORT_URL",
    "DEFAULT_WEEKLY_BUDGET_USD",
    "DEFAULT_WEEKLY_RESET_TIME",
    "DEFAULT_WEEKLY_RESET_WEEKDAY",
    "DEFAULT_WORK_OVERLAY_MAX_ITEMS",
    "DEFAULT_WORK_OVERLAY_SIDE",
    "HUD_SETTINGS_FILENAME",
    "MAX_PRICING_RESPONSE_BYTES",
    "ModelPrice",
    "PriceAuditRecord",
    "PriceVersion",
    "PricingApplyResult",
    "PricingImportPreview",
    "ProviderSettings",
    "REST_REMINDER_STATE_KEY",
    "RUNTIME_STATE_KEY",
    "USER_CONFIG_KEY",
    "UserConfig",
    "UserConfigStore",
    "WARNING_DISMISSED_DATE_KEY",
    "default_model_prices",
    "fetch_openai_models_prices",
    "default_settings_path",
    "dismiss_warning_for_today",
    "effective_display_mode",
    "empty_pricing_template",
    "extract_model_prices",
    "fetch_model_prices",
    "load_rest_reminder_state",
    "local_date_key",
    "normalize_display_mode",
    "normalize_model_prices",
    "normalize_price_versions",
    "normalize_provider_names",
    "normalize_provider_order",
    "normalize_provider_settings",
    "save_rest_reminder_state",
    "normalize_work_overlay_max_items",
    "normalize_work_overlay_side",
    "parse_thresholds",
    "pricing_export_payload",
    "minimal_price_example",
    "read_json_object",
    "time_parts",
    "warning_dismissed_today",
    "write_json_object",
]
