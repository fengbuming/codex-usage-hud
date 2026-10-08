"""Provider deletion orchestration shared by the renderer command paths."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .codex_provider_config import delete_provider_config
from .config import UserConfig, normalize_provider
from .provider_registry import discover_provider_registry


def _provider_price_key_matches(key: object, raw_price: object, provider: str) -> bool:
    explicit = ""
    if isinstance(raw_price, Mapping):
        explicit = normalize_provider(raw_price.get("provider"))
    if explicit == provider:
        return True
    text = str(key or "").strip().casefold()
    return text.startswith(f"{provider}/")


def _remove_provider_quick_launch(
    config: UserConfig,
    provider_id: str,
) -> UserConfig:
    provider = normalize_provider(provider_id)
    if not provider:
        raise ValueError("Provider ID 不能为空。")
    raw = config.to_dict()
    raw["quick_launch_providers"] = [
        item
        for item in raw.get("quick_launch_providers", [])
        if normalize_provider(item) != provider
    ]
    return UserConfig.from_dict(raw)


def remove_provider_pricing(
    config: UserConfig,
    provider_id: str,
) -> tuple[UserConfig, dict[str, int]]:
    """Remove one provider's editable prices and immutable pricing history."""
    provider = normalize_provider(provider_id)
    if not provider:
        raise ValueError("Provider ID 不能为空。")
    raw = config.to_dict()
    raw_prices = raw.get("model_prices")
    removed_price_count = 0
    if isinstance(raw_prices, Mapping):
        retained_prices: dict[str, object] = {}
        for key, value in raw_prices.items():
            if _provider_price_key_matches(key, value, provider):
                removed_price_count += 1
                continue
            retained_prices[str(key)] = value
        raw["model_prices"] = retained_prices

    raw_provider_settings = raw.get("provider_settings")
    removed_provider_settings = 0
    if isinstance(raw_provider_settings, Mapping):
        retained_settings: dict[str, object] = {}
        for key, value in raw_provider_settings.items():
            if normalize_provider(key) == provider:
                removed_provider_settings += 1
                continue
            retained_settings[str(key)] = value
        raw["provider_settings"] = retained_settings

    raw["provider_order"] = [
        item
        for item in raw.get("provider_order", [])
        if normalize_provider(item) != provider
    ]
    raw["selected_providers"] = [
        item
        for item in raw.get("selected_providers", [])
        if normalize_provider(item) != provider
    ]
    raw["notification_only_providers"] = [
        item
        for item in raw.get("notification_only_providers", [])
        if normalize_provider(item) != provider
    ]
    raw["quick_launch_providers"] = [
        item
        for item in raw.get("quick_launch_providers", [])
        if normalize_provider(item) != provider
    ]

    raw_versions = raw.get("pricing_versions")
    removed_version_ids: set[str] = set()
    retained_versions: list[object] = []
    if isinstance(raw_versions, list):
        for value in raw_versions:
            if isinstance(value, Mapping) and normalize_provider(value.get("provider")) == provider:
                removed_version_ids.add(str(value.get("version_id") or "").strip())
                continue
            retained_versions.append(value)
        raw["pricing_versions"] = retained_versions

    raw_audit = raw.get("pricing_audit")
    removed_audit_count = 0
    if isinstance(raw_audit, list):
        retained_audit: list[object] = []
        for value in raw_audit:
            if isinstance(value, Mapping) and (
                str(value.get("version_id") or "").strip() in removed_version_ids
                or str(value.get("replaced_version_id") or "").strip()
                in removed_version_ids
            ):
                removed_audit_count += 1
                continue
            retained_audit.append(value)
        raw["pricing_audit"] = retained_audit

    return UserConfig.from_dict(raw), {
        "modelPrices": removed_price_count,
        "providerSettings": removed_provider_settings,
        "pricingVersions": len(removed_version_ids),
        "pricingAudit": removed_audit_count,
    }


def delete_provider_for_context(
    context: object,
    command: Mapping[str, Any],
) -> dict[str, object]:
    """Delete provider config/pricing after the caller coordinates history cleanup."""
    provider = normalize_provider(command.get("provider") or command.get("providerId"))
    if not provider:
        raise ValueError("Provider ID 不能为空。")
    if provider == normalize_provider(getattr(context, "app_provider", "")):
        raise ValueError("默认 Codex App Provider 不支持删除供应商配置。")

    config_result = delete_provider_config(provider)
    pricing_result: dict[str, int] = {
        "modelPrices": 0,
        "providerSettings": 0,
        "pricingVersions": 0,
        "pricingAudit": 0,
    }
    settings_store = getattr(context, "settings_store", None)
    load = getattr(settings_store, "load", None)
    save = getattr(settings_store, "save", None)
    settings_changed = False
    if bool(command.get("deleteModelPrices")):
        if not callable(load) or not callable(save):
            raise RuntimeError("HUD 单价配置存储当前不可用。")
        current = load()
        updated, pricing_result = remove_provider_pricing(current, provider)
    elif callable(load) and callable(save):
        current = load()
        updated = _remove_provider_quick_launch(current, provider)
    else:
        current = None
        updated = None
    if updated is not None:
        settings_changed = updated != current
        if settings_changed:
            save(updated)
            try:
                context.settings_mtime = None
            except Exception:
                pass
            if not callable(getattr(context, "reload_user_config", None)):
                try:
                    context.user_config = updated
                except Exception:
                    pass

    # ``config.toml`` is separate from the HUD settings store.  Refresh the
    # in-memory provider registry so the Codex config deletion is visible even
    # when no HUD price option was selected. The caller handles history separately,
    # so skip the optional 30-day JSONL history discovery during config cleanup.
    try:
        context.settings_mtime = None
    except Exception:
        pass
    reload_user_config = getattr(context, "reload_user_config", None)
    if callable(reload_user_config):
        reload_user_config(include_history=False)
    try:
        registry = discover_provider_registry(
            user_config=getattr(context, "user_config", UserConfig.defaults()),
            sessions_root=getattr(context, "sessions_root", None),
            include_history=False,
        )
        context.provider_registry = registry
        context.app_provider = registry.app_provider
    except Exception:
        # The config file and optional HUD price changes are already complete;
        # registry refresh is a best-effort in-memory update for the open HUD.
        pass

    profile_count = len(config_result.get("profilePaths", []))
    price_count = int(pricing_result.get("modelPrices", 0))
    version_count = int(pricing_result.get("pricingVersions", 0))
    env_deleted = [
        str(item or "")
        for item in config_result.get("environmentKeys", [])
        if str(item or "")
    ]
    message = f"供应商 {provider} 已删除：已移除 config.toml 相关配置"
    if profile_count:
        message += f"和 {profile_count} 个 Provider profile"
    if env_deleted:
        message += f"；已删除用户环境变量 {', '.join(env_deleted)}"
    if bool(command.get("deleteModelPrices")):
        message += f"；模型单价配置已删除（{price_count} 条价格、{version_count} 个版本）"
    return {
        "status": "ok",
        "providerId": provider,
        "message": message + "。",
        "config": config_result,
        "pricing": pricing_result,
        "settingsChanged": settings_changed,
    }


__all__ = [
    "delete_provider_for_context",
    "remove_provider_pricing",
]
