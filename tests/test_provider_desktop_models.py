from types import SimpleNamespace

import pytest

from codex_usage_hud import codex_provider_config as providers
from codex_usage_hud.config import UserConfig
from codex_usage_hud.provider_registry import discover_provider_registry
from codex_usage_hud.usage_insights import _provider_registry_payload


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    environment = {}
    monkeypatch.setattr(providers, "_user_environment_value", lambda name: environment.get(name, ""))
    monkeypatch.setattr(providers, "_set_user_environment_value", lambda name, value: environment.__setitem__(name, value))
    monkeypatch.setattr(providers, "_delete_user_environment_value", lambda name: environment.pop(name, None))
    monkeypatch.setattr(providers, "_broadcast_environment_change", lambda: None)
    path = tmp_path / "config.toml"
    path.write_text(
        'model = "gpt-6.1-sol"\nmodel_reasoning_effort = "max"\n'
        'model_provider = "native"\n\n'
        '[model_providers.native]\nbase_url = "https://native.example/v1"\n'
        'experimental_bearer_token = "native-key"\n\n'
        '[model_providers.cheaprouter]\nbase_url = "https://cheap.example/v1"\n'
        'experimental_bearer_token = "cheap-key"\n\n'
        '[model_providers.other]\nbase_url = "https://other.example/v1"\n'
        'experimental_bearer_token = "other-key"\n',
        encoding="utf-8",
    )
    return path


def save_model(path, provider, model, **extra):
    definition = providers.read_provider_definitions(path)[provider]
    return providers.save_provider_configs({
        "provider_id": provider, "base_url": definition.base_url,
        "desktop_model": model, **extra,
    }, config_path=path)


def read_config(path):
    return providers._parse_toml_mapping(path.read_text(encoding="utf-8"))


def test_inactive_preference_roundtrips_through_registry_without_changing_default(config_path):
    result = save_model(config_path, "cheaprouter", "glm-5.3-flash")
    assert read_config(config_path)["model"] == "gpt-6.1-sol"
    assert read_config(config_path)["model_provider"] == "native"
    assert result["desktopModelUpdates"][0]["model"] == "glm-5.3-flash"
    registry = discover_provider_registry(user_config=UserConfig.defaults(), config_path=config_path, include_history=False)
    payload = _provider_registry_payload(SimpleNamespace(provider_registry=registry))
    assert payload["cheaprouter"]["desktopModel"] == "glm-5.3-flash"
    assert payload["cheaprouter"]["apiKey"] == "cheap-key"
    assert "desktop_model" not in read_config(config_path)["model_providers"]["cheaprouter"]


def test_switches_between_preferences_and_restores_native_model_and_effort(config_path):
    save_model(config_path, "cheaprouter", "glm-5.3-flash")
    save_model(config_path, "other", "custom-other")
    providers.set_default_codex_provider("cheaprouter", config_path=config_path)
    assert read_config(config_path)["model"] == "glm-5.3-flash"
    # Desktop can save the new model's reasoning level after the config refresh.
    text = config_path.read_text(encoding="utf-8").replace('model_reasoning_effort = "max"', 'model_reasoning_effort = "low"')
    config_path.write_text(text, encoding="utf-8")
    providers.set_default_codex_provider("other", config_path=config_path)
    assert read_config(config_path)["model"] == "custom-other"
    restored = providers.set_default_codex_provider("native", config_path=config_path)
    assert restored["desktopModelConfiguration"]["restoredNativeModel"] is True
    assert read_config(config_path)["model"] == "gpt-6.1-sol"
    assert read_config(config_path)["model_reasoning_effort"] == "max"
    assert providers.DESKTOP_MODEL_STATE_MARKER not in config_path.read_text(encoding="utf-8")


def test_editing_current_provider_applies_new_model_and_default_without_switching_identity(config_path):
    save_model(config_path, "cheaprouter", "glm-5.3-flash")
    providers.set_default_codex_provider("cheaprouter", config_path=config_path)
    save_model(config_path, "cheaprouter", "glm-5.3")
    assert read_config(config_path)["model"] == "glm-5.3"
    result = save_model(config_path, "cheaprouter", "")
    assert result["desktopModelUpdates"][0]["model"] == "gpt-6.1-sol"
    assert read_config(config_path)["model_provider"] == "cheaprouter"
    assert read_config(config_path)["model"] == "gpt-6.1-sol"
    assert providers.read_provider_definitions(config_path)["cheaprouter"].desktop_model == ""


def test_native_picker_changes_are_preserved_when_leaving_an_override(config_path):
    save_model(config_path, "cheaprouter", "glm-5.3-flash")
    providers.set_default_codex_provider("cheaprouter", config_path=config_path)
    text = config_path.read_text(encoding="utf-8").replace('model = "glm-5.3-flash"', 'model = "gpt-6-astra"')
    config_path.write_text(text, encoding="utf-8")
    providers.set_default_codex_provider("native", config_path=config_path)
    assert read_config(config_path)["model"] == "gpt-6-astra"
    assert providers.DESKTOP_MODEL_STATE_MARKER not in config_path.read_text(encoding="utf-8")


def test_default_restores_absent_keys_and_unrelated_config(config_path):
    text = config_path.read_text(encoding="utf-8")
    text = text.replace('model = "gpt-6.1-sol"\n', '').replace('model_reasoning_effort = "max"\n', '')
    text = 'notify = ["a", "b"]\n' + text
    config_path.write_text(text, encoding="utf-8")
    save_model(config_path, "cheaprouter", "glm-5.3-flash")
    providers.set_default_codex_provider("cheaprouter", config_path=config_path)
    text = config_path.read_text(encoding="utf-8").replace('model_provider = "cheaprouter"', 'model_reasoning_effort = "low"\nmodel_provider = "cheaprouter"')
    config_path.write_text(text, encoding="utf-8")
    providers.set_default_codex_provider("native", config_path=config_path)
    config = read_config(config_path)
    assert "model" not in config and "model_reasoning_effort" not in config
    assert config["notify"] == ["a", "b"]


def test_new_provider_saves_preference_and_legacy_save_preserves_it(config_path):
    providers.save_provider_configs({"provider_id": "new", "base_url": "https://new.example/v1",
        "env_key": "HUD_TEST_NEW_KEY", "api_key": "new-key", "is_new": True,
        "desktop_model": "glm-5.3-flash"}, config_path=config_path)
    assert providers.read_provider_definitions(config_path)["new"].desktop_model == "glm-5.3-flash"
    assert (config_path.parent / "new.config.toml").exists()
    providers.save_provider_configs({"provider_id": "new", "base_url": "https://new.example/v1",
        "api_key": "replacement"}, config_path=config_path)
    assert providers.read_provider_definitions(config_path)["new"].desktop_model == "glm-5.3-flash"


@pytest.mark.parametrize("model", ["glm\n[model_providers.injected]", "x\x00y", "x" * 257, {"model": "glm"}])
def test_invalid_model_does_not_write_partial_config(config_path, model):
    original = config_path.read_bytes()
    with pytest.raises(ValueError, match="Desktop 模型"):
        save_model(config_path, "cheaprouter", model)
    assert config_path.read_bytes() == original


def test_reapplying_preference_is_idempotent_even_with_brackets_in_model_id(config_path):
    save_model(config_path, "cheaprouter", "glm[flash]")
    providers.set_default_codex_provider("cheaprouter", config_path=config_path)
    original = config_path.read_bytes()
    result = providers.set_default_codex_provider("cheaprouter", config_path=config_path)
    assert result["changed"] is False
    assert config_path.read_bytes() == original
    state = providers._hud_comment_value(config_path.read_text(encoding="utf-8"), providers.DESKTOP_MODEL_STATE_MARKER)
    assert state["nativeModel"] == "gpt-6.1-sol"


def test_multiple_models_attach_to_picker_with_first_as_default(config_path):
    result = providers.save_provider_configs({
        "provider_id": "cheaprouter",
        "base_url": "https://cheap.example/v1",
        "desktop_model": "glm-5.3-flash",
        "desktop_models": ["glm-5.3-flash", "glm-5.3", "deepseek-v4"],
    }, config_path=config_path)
    update = result["desktopModelUpdates"][0]
    assert update["customModel"] == "glm-5.3-flash"
    assert update["customModels"] == ["glm-5.3-flash", "glm-5.3", "deepseek-v4"]
    assert read_config(config_path)["model"] == "gpt-6.1-sol"
    definition = providers.read_provider_definitions(config_path)["cheaprouter"]
    assert definition.desktop_model == "glm-5.3-flash"
    assert definition.desktop_models == ("glm-5.3-flash", "glm-5.3", "deepseek-v4")
    registry = discover_provider_registry(
        user_config=UserConfig.defaults(), config_path=config_path, include_history=False
    )
    payload = _provider_registry_payload(SimpleNamespace(provider_registry=registry))
    assert payload["cheaprouter"]["desktopModels"] == ["glm-5.3-flash", "glm-5.3", "deepseek-v4"]
    assert payload["cheaprouter"]["desktopModel"] == "glm-5.3-flash"


def test_legacy_single_model_reads_back_as_one_item_list(config_path):
    providers.save_provider_configs({
        "provider_id": "cheaprouter",
        "base_url": "https://cheap.example/v1",
        "desktop_model": "glm-5.3-flash",
    }, config_path=config_path)
    definition = providers.read_provider_definitions(config_path)["cheaprouter"]
    assert definition.desktop_models == ("glm-5.3-flash",)
    assert definition.desktop_model == "glm-5.3-flash"


def test_deselecting_all_models_clears_preference(config_path):
    providers.save_provider_configs({
        "provider_id": "cheaprouter",
        "base_url": "https://cheap.example/v1",
        "desktop_model": "glm-5.3-flash",
        "desktop_models": ["glm-5.3-flash", "glm-5.3"],
    }, config_path=config_path)
    result = providers.save_provider_configs({
        "provider_id": "cheaprouter",
        "base_url": "https://cheap.example/v1",
        "desktop_model": "",
        "desktop_models": [],
    }, config_path=config_path)
    assert result["desktopModelUpdates"][0]["customModels"] == []
    definition = providers.read_provider_definitions(config_path)["cheaprouter"]
    assert definition.desktop_models == ()
    assert definition.desktop_model == ""


@pytest.mark.parametrize("models", [["ok", "bad\n[model_providers.x]"], ["ok", {"m": 1}], ["ok", "x" * 257]])
def test_invalid_model_list_does_not_write_partial_config(config_path, models):
    original = config_path.read_bytes()
    with pytest.raises(ValueError, match="Desktop 模型"):
        providers.save_provider_configs({
            "provider_id": "cheaprouter",
            "base_url": "https://cheap.example/v1",
            "desktop_model": "ok",
            "desktop_models": models,
        }, config_path=config_path)
    assert config_path.read_bytes() == original
