from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from codex_usage_hud.config import ModelPrice, ProviderSettings, UserConfig
from codex_usage_hud.provider_registry import ProviderRegistry, ProviderRegistryEntry
from codex_usage_hud.runtime_config import ConfigApplyPorts, apply_to_context


def test_price_change_requests_background_usage_refresh() -> None:
    initial = UserConfig.defaults()
    parser = SimpleNamespace(cost_estimator=object())
    request_refresh = MagicMock()
    context = SimpleNamespace(
        user_config=initial,
        sessions_root=None,
        parser=parser,
        usage_insights_worker=SimpleNamespace(request_refresh=request_refresh),
    )
    estimator = object()
    ports = ConfigApplyPorts(
        discover_providers=MagicMock(),
        cost_estimator_from_config=lambda config: estimator,
        usage_cache_factory=MagicMock(),
        configure_ui_cost_estimators=MagicMock(),
    )
    priced = replace(
        initial,
        model_prices={
            **initial.model_prices,
            "gpt-6-sol": ModelPrice(2.0, 0.2, 10.0, 10.0),
        },
    )

    apply_to_context(context, priced, mtime=None, ports=ports)

    assert parser.cost_estimator is estimator
    request_refresh.assert_called_once_with(request_id="pricing-change")

    request_refresh.reset_mock()
    apply_to_context(
        context,
        replace(priced, daily_budget_usd=priced.daily_budget_usd + 1),
        mtime=None,
        ports=ports,
    )
    request_refresh.assert_not_called()


def test_apply_to_context_keeps_history_only_providers_out_of_settings() -> None:
    initial = replace(
        UserConfig.defaults(),
        provider_settings={"custom": ProviderSettings()},
        provider_order=["custom"],
    )
    registry = ProviderRegistry(
        entries={
            "custom": ProviderRegistryEntry(provider="custom", from_base_config=True),
            "history-only": ProviderRegistryEntry(
                provider="history-only", from_history=True
            ),
        },
        app_provider="custom",
    )
    context = SimpleNamespace(user_config=initial, sessions_root=Path("."))
    ports = ConfigApplyPorts(
        discover_providers=lambda **_: registry,
        cost_estimator_from_config=lambda config: object(),
        usage_cache_factory=MagicMock(),
        configure_ui_cost_estimators=MagicMock(),
    )

    apply_to_context(context, initial, mtime=None, ports=ports)

    # 历史来源的供应商只能用于统计，不得被写回 provider_settings/provider_order，
    # 否则已删除的供应商会重新出现在设置页与供应商切换菜单。
    assert set(context.user_config.provider_settings) == {"custom"}
    assert context.user_config.provider_order == ["custom"]
