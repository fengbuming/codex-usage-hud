from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codex_usage_hud import codex_provider_config
from codex_usage_hud.config import UserConfig
from codex_usage_hud.provider_cleanup import (
    delete_provider_for_context,
    remove_provider_pricing,
)


def _price(model: str, provider: str = "") -> dict[str, object]:
    value: dict[str, object] = {
        "model": model,
        "input": 1.0,
        "cached_input": 0.5,
        "cache_write": 0.25,
        "output": 2.0,
        "reasoning": 2.0,
    }
    if provider:
        value["provider"] = provider
    return value


def test_remove_provider_pricing_removes_current_and_versioned_data() -> None:
    config = UserConfig.from_dict(
        {
            "model_prices": {
                "muyuan/gpt-test": _price("gpt-test", "muyuan"),
                "other/gpt-test": _price("gpt-test", "other"),
                "shared": _price("shared"),
            },
            "provider_settings": {
                "muyuan": {"model_prices": {"gpt-test": _price("gpt-test", "muyuan")}},
                "other": {"model_prices": {"gpt-test": _price("gpt-test", "other")}},
            },
            "provider_order": ["muyuan", "other"],
            "selected_providers": ["muyuan", "other"],
            "notification_only_providers": ["muyuan"],
            "quick_launch_providers": ["MUYUAN", "other"],
            "provider_scope_mode": "custom",
            "pricing_versions": [
                {
                    "version_id": "muyuan-version",
                    "provider": "muyuan",
                    "model": "gpt-test",
                    "input": 1,
                    "cached_input": 0.5,
                    "cache_write": 0.25,
                    "output": 2,
                    "reasoning": 2,
                    "effective_at": "2026-08-01T00:00:00Z",
                    "created_at": "2026-08-01T00:00:00Z",
                    "created_by": "user_edit",
                    "source": "manual",
                },
                {
                    "version_id": "other-version",
                    "provider": "other",
                    "model": "gpt-test",
                    "input": 1,
                    "cached_input": 0.5,
                    "cache_write": 0.25,
                    "output": 2,
                    "reasoning": 2,
                    "effective_at": "2026-08-01T00:00:00Z",
                    "created_at": "2026-08-01T00:00:00Z",
                    "created_by": "user_edit",
                    "source": "manual",
                },
            ],
            "pricing_audit": [
                {
                    "audit_id": "muyuan-audit",
                    "action": "replace",
                    "version_id": "muyuan-version",
                    "occurred_at": "2026-08-01T00:00:00Z",
                    "created_by": "user_edit",
                },
                {
                    "audit_id": "other-audit",
                    "action": "replace",
                    "version_id": "other-version",
                    "occurred_at": "2026-08-01T00:00:00Z",
                    "created_by": "user_edit",
                },
            ],
        }
    )

    updated, counts = remove_provider_pricing(config, "MUYUAN")

    assert "muyuan" not in updated.provider_settings
    assert "other" in updated.provider_settings
    assert all("muyuan" not in key.casefold() for key in updated.model_prices)
    assert updated.provider_order == ["other"]
    assert updated.selected_providers == ["other"]
    assert updated.notification_only_providers == []
    assert updated.quick_launch_providers == ["other"]
    assert [item.version_id for item in updated.pricing_versions] == ["other-version"]
    assert [item.audit_id for item in updated.pricing_audit] == ["other-audit"]
    assert counts == {
        "modelPrices": 1,
        "providerSettings": 1,
        "pricingVersions": 1,
        "pricingAudit": 1,
    }


def test_delete_provider_for_context_refreshes_registry_after_config_delete() -> None:
    config = UserConfig.from_dict(
        {
            "provider_settings": {
                "muyuan": {"model_prices": {"gpt-test": _price("gpt-test", "muyuan")}}
            },
            "provider_order": ["muyuan"],
            "quick_launch_providers": ["muyuan"],
        }
    )
    store = SimpleNamespace(load=lambda: config, save=MagicMock())
    context = SimpleNamespace(
        app_provider="custom",
        user_config=config,
        settings_store=store,
        sessions_root=None,
        settings_mtime=123.0,
        reload_user_config=MagicMock(),
    )
    registry = SimpleNamespace(app_provider="custom")

    with patch(
        "codex_usage_hud.provider_cleanup.delete_provider_config",
        return_value={"changed": True, "profilePaths": []},
    ) as delete_config, patch(
        "codex_usage_hud.provider_cleanup.discover_provider_registry",
        return_value=registry,
    ) as discover_registry:
        result = delete_provider_for_context(
            context,
            {"provider": "muyuan", "deleteModelPrices": True},
        )

    delete_config.assert_called_once_with("muyuan")
    context.reload_user_config.assert_called_once_with(include_history=False)
    assert all(
        call.kwargs.get("include_history") is False
        for call in discover_registry.call_args_list
    )
    assert context.provider_registry is registry
    assert result["status"] == "ok"
    assert result["pricing"]["providerSettings"] == 1


def test_delete_provider_for_context_removes_quick_launch_without_price_deletion() -> None:
    config = UserConfig.from_dict(
        {
            "provider_order": ["muyuan", "other"],
            "quick_launch_providers": ["muyuan", "other"],
        }
    )
    save = MagicMock()
    context = SimpleNamespace(
        app_provider="custom",
        user_config=config,
        settings_store=SimpleNamespace(load=lambda: config, save=save),
        sessions_root=None,
        reload_user_config=MagicMock(),
    )
    registry = SimpleNamespace(app_provider="custom")

    with patch(
        "codex_usage_hud.provider_cleanup.delete_provider_config",
        return_value={"changed": True, "profilePaths": []},
    ), patch(
        "codex_usage_hud.provider_cleanup.discover_provider_registry",
        return_value=registry,
    ):
        result = delete_provider_for_context(
            context,
            {"provider": "MUYUAN", "deleteModelPrices": False},
        )

    save.assert_called_once()
    updated = save.call_args.args[0]
    assert updated.quick_launch_providers == ["other"]
    assert result["settingsChanged"] is True


def test_delete_provider_for_context_ignores_observed_session_provider(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'model_provider = "custom"\n[model_providers.custom]\nname = "Default"\n'
        '[model_providers.other]\nname = "Other"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(codex_provider_config, "default_codex_config_path", lambda: path)
    context = SimpleNamespace(app_provider="other")
    with patch(
        "codex_usage_hud.provider_cleanup.discover_provider_registry",
        return_value=SimpleNamespace(app_provider="custom"),
    ):
        result = delete_provider_for_context(context, {"provider": "other"})

    assert result["status"] == "ok"
    assert "other" not in codex_provider_config.read_provider_definitions(path)
    assert "custom" in codex_provider_config.read_provider_definitions(path)


def test_delete_provider_for_context_rejects_persisted_default_provider(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.toml"
    original = 'model_provider = "custom"\n[model_providers.custom]\nname = "Default"\n'
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(codex_provider_config, "default_codex_config_path", lambda: path)
    context = SimpleNamespace(app_provider="other")

    with pytest.raises(ValueError, match="默认 Codex App Provider"):
        delete_provider_for_context(context, {"provider": "custom"})

    assert path.read_text(encoding="utf-8") == original
