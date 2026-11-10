"""Gateway opt-in and failure paths without launching a proxy or Codex."""

from dataclasses import replace
import json
import sys
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codex_usage_hud import codex_provider_config as config
from codex_usage_hud import provider_gateway as gateway
from codex_usage_hud.config import UserConfig, UserConfigStore, read_json_object, write_json_object
from codex_usage_hud.provider_gateway_settings import set_gateway_enabled
from codex_usage_hud.runtime_commands import _handle_renderer_settings_command
from codex_usage_hud.settings_bridge import SettingsBridgeServer


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('''model_provider = "alpha"
[model_providers.alpha]
base_url = "https://alpha.example/v1"
wire_api = "responses"
experimental_bearer_token = "test-only-alpha"
[model_providers.beta]
base_url = "https://beta.example/v1"
wire_api = "responses"
experimental_bearer_token = "test-only-beta"
''', encoding="utf-8")
    monkeypatch.setattr(config, "default_codex_config_path", lambda: path)
    monkeypatch.setattr(config, "_user_environment_value", lambda _: "")
    store = UserConfigStore(tmp_path / "hud.json")
    store.save(replace(UserConfig.defaults(), daily_budget_usd=123))
    relay = SimpleNamespace(route=MagicMock(return_value="http://127.0.0.1:57324/codex_hud/v1"), close=MagicMock())
    return path, store, relay


def test_default_is_off_and_opt_in_round_trips():
    assert UserConfig.defaults().provider_gateway_enabled is False
    assert UserConfig.from_dict({}).provider_gateway_enabled is False
    assert UserConfig.from_dict({"provider_gateway_enabled": "invalid"}).provider_gateway_enabled is False
    enabled = replace(UserConfig.defaults(), provider_gateway_enabled=True)
    assert UserConfig.from_dict(enabled.to_dict()).provider_gateway_enabled is True


def test_toggle_preserves_settings_and_disables_without_stopping_relay(setup):
    path, store, relay = setup
    raw = read_json_object(store.path)
    raw["geometry"] = {"x": 22}
    write_json_object(store.path, raw)
    original = path.read_bytes()
    result = set_gateway_enabled(store, True, config_path=path, relay=relay)
    assert result["enabled"] and result["gateway"]
    assert store.load().provider_gateway_enabled
    assert gateway.gateway_info(path)["default_provider"] == "alpha"
    assert path.with_name(path.name + ".hud-gateway-backup").read_bytes() == original
    result = set_gateway_enabled(store, False, provider="beta", config_path=path, relay=relay)
    assert result["enabled"] is False and result["gateway"] is False
    assert store.load().provider_gateway_enabled is False
    assert store.load().daily_budget_usd == 123
    assert read_json_object(store.path)["geometry"] == {"x": 22}
    assert config._parse_toml_mapping(path.read_text())["model_provider"] == "beta"
    assert not gateway.gateway_info(path)
    gateway.validate_exit("", config_path=path)
    relay.close.assert_not_called()
    # Restart/re-enable preserves the user's most recent direct selection.
    set_gateway_enabled(store, True, config_path=path, relay=relay)
    assert gateway.gateway_info(path)["default_provider"] == "beta"


def test_invalid_disable_selection_leaves_proxy_and_preference_enabled(setup):
    path, store, relay = setup
    set_gateway_enabled(store, True, config_path=path, relay=relay)
    original = path.read_bytes()
    for provider in ("", "missing", "codex_hud"):
        with pytest.raises(ValueError):
            set_gateway_enabled(store, False, provider=provider, config_path=path, relay=relay)
        assert path.read_bytes() == original
        assert store.load().provider_gateway_enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_settings_write_failure_restores_config_and_default_route(setup, monkeypatch, enabled):
    path, store, relay = setup
    if not enabled:
        set_gateway_enabled(store, True, config_path=path, relay=relay)
    original = path.read_bytes()
    monkeypatch.setattr(store, "save", MagicMock(side_effect=PermissionError("write denied")))
    with pytest.raises(PermissionError, match="write denied"):
        set_gateway_enabled(store, enabled, provider="beta", config_path=path, relay=relay)
    assert path.read_bytes() == original
    assert store.load().provider_gateway_enabled is (not enabled)
    if not enabled:
        assert gateway.gateway_info(path)["default_provider"] == "alpha"
    relay.close.assert_not_called()


def test_failed_activation_never_persists_opt_in(setup):
    path, store, relay = setup
    relay.route.side_effect = RuntimeError("occupied port")
    original = path.read_bytes()
    with pytest.raises(RuntimeError, match="occupied"):
        set_gateway_enabled(store, True, config_path=path, relay=relay)
    assert store.load().provider_gateway_enabled is False
    assert path.read_bytes() == original


def test_missing_toml_parser_fails_before_credentials_or_configuration_are_changed(setup, monkeypatch):
    path, store, relay = setup
    path.with_name("auth.json").write_text(json.dumps({"OPENAI_API_KEY":"test-only-key"}), encoding="utf-8")
    original = path.read_bytes()
    monkeypatch.setitem(sys.modules, "tomllib", None)
    monkeypatch.setitem(sys.modules, "tomli", None)
    with pytest.raises(RuntimeError, match="TOML 解析器"):
        set_gateway_enabled(store, True, config_path=path, relay=relay)
    assert path.read_bytes() == original
    assert not store.load().provider_gateway_enabled
    assert not path.with_name(path.name + ".hud-gateway-backup").exists()
    relay.route.assert_not_called()


def test_python310_tomli_fallback_can_activate_gateway(setup, monkeypatch):
    from types import SimpleNamespace
    try:
        import tomllib as parser
    except ModuleNotFoundError:
        import tomli as parser

    path, store, relay = setup
    monkeypatch.setitem(sys.modules, "tomllib", None)
    monkeypatch.setitem(sys.modules, "tomli", SimpleNamespace(loads=parser.loads))
    assert set_gateway_enabled(store, True, config_path=path, relay=relay)["gateway"]
    assert store.load().provider_gateway_enabled
    assert gateway.gateway_info(path)["default_provider"] == "alpha"


def test_invalid_toml_is_rejected_before_starting_relay(setup):
    path, store, relay = setup
    path.write_text('model_provider = "unterminated\n', encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="有效 TOML"):
        set_gateway_enabled(store, True, config_path=path, relay=relay)
    assert path.read_bytes() == original
    assert not store.load().provider_gateway_enabled
    relay.route.assert_not_called()


def test_failed_post_write_confirmation_rolls_back_only_the_gateway_candidate(setup, monkeypatch):
    from codex_usage_hud import provider_gateway_settings as settings

    path, store, relay = setup
    original = path.read_bytes()
    monkeypatch.setattr(settings, "gateway_info", MagicMock(side_effect=[{}, RuntimeError("confirmation failed")]))
    with pytest.raises(RuntimeError, match="confirmation failed"):
        set_gateway_enabled(store, True, config_path=path, relay=relay)
    assert path.read_bytes() == original
    assert not store.load().provider_gateway_enabled


def test_settings_write_failure_does_not_overwrite_a_concurrent_external_edit(setup, monkeypatch):
    path, store, relay = setup
    concurrent = []

    def save(_config):
        text = config._read_text_exact(path) + "\n# concurrent edit\n"
        path.write_text(text, encoding="utf-8", newline="")
        concurrent.append(text)
        raise OSError("cannot save preference")

    monkeypatch.setattr(store, "save", save)
    with pytest.raises(RuntimeError, match="发生了变化"):
        set_gateway_enabled(store, True, config_path=path, relay=relay)
    assert config._read_text_exact(path) == concurrent[0]
    assert not store.load().provider_gateway_enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_renderer_bridge_only_activates_opted_in_gateway(setup, monkeypatch, enabled):
    path, store, _ = setup
    store.save(replace(store.load(), provider_gateway_enabled=enabled))
    activate = MagicMock()
    monkeypatch.setattr("codex_usage_hud.provider_proxy.provider_proxy_relay.activate", activate)
    restore = MagicMock()
    monkeypatch.setattr("codex_usage_hud.provider_proxy.provider_proxy_relay.restore_legacy_routes", restore)
    server = MagicMock()
    server.server_address = ("127.0.0.1", 10000)
    monkeypatch.setattr("codex_usage_hud.settings_bridge.ThreadingHTTPServer", MagicMock(return_value=server))
    monkeypatch.setattr("codex_usage_hud.settings_bridge.Thread", MagicMock())
    SettingsBridgeServer(store, manage_provider_proxy=True).start()
    restore.assert_called_once_with(path)
    assert activate.call_count == int(enabled)
    if enabled:
        activate.assert_called_once_with(path)


@pytest.mark.parametrize("enabled", [False, True])
def test_daemon_only_activates_opted_in_gateway(setup, monkeypatch, enabled):
    from codex_usage_hud import runtime_orchestration as runtime

    path, store, _ = setup
    store.save(replace(store.load(), provider_gateway_enabled=enabled))
    monkeypatch.setattr(runtime, "UserConfigStore", lambda: store)
    activate = MagicMock()
    monkeypatch.setattr("codex_usage_hud.provider_proxy.provider_proxy_relay.activate", activate)
    restore = MagicMock()
    monkeypatch.setattr("codex_usage_hud.provider_proxy.provider_proxy_relay.restore_legacy_routes", restore)

    def run(_args, *, services):
        services.activate_providers()
        return 0

    monkeypatch.setattr(runtime._daemon_runtime_owner, "run_daemon", run)
    assert runtime.run_daemon(SimpleNamespace()) == 0
    restore.assert_called_once_with(path)
    assert activate.call_count == int(enabled)
    if enabled:
        activate.assert_called_once_with(path)


def test_toggle_command_acknowledges_and_refreshes_settings(setup, monkeypatch):
    _, store, relay = setup
    monkeypatch.setattr("codex_usage_hud.provider_gateway_settings.provider_proxy_relay", relay)
    reload = MagicMock()
    context = SimpleNamespace(settings_store=store, reload_user_config=reload)
    status = _handle_renderer_settings_command(
        {"action": "providerGatewayToggle", "enabled": True, "requestId": "toggle-1"},
        context, Event(), Event(),
    )
    assert status["kind"] != "error"
    assert status["requestId"] == "toggle-1"
    assert status["providerGateway"]["enabled"] is True
    reload.assert_called_once_with(include_history=False)


def test_enabling_force_fast_activates_gateway_and_persists_global_override(setup, monkeypatch):
    path, store, relay = setup
    monkeypatch.setattr("codex_usage_hud.provider_gateway_settings.provider_proxy_relay", relay)
    context = SimpleNamespace(
        settings_store=store,
        settings_mtime=None,
        reload_user_config=MagicMock(),
    )
    settings = store.load().to_dict()
    settings["force_fast_requests"] = True

    status = _handle_renderer_settings_command(
        {"action": "save", "settings": settings, "requestId": "force-fast-1"},
        context,
        Event(),
        Event(),
    )

    assert status["kind"] == ""
    assert status["requestId"] == "force-fast-1"
    assert status["forceFastRequests"] == {"enabled": True}
    assert status["restartVisible"] is True
    assert store.load().force_fast_requests is True
    assert store.load().provider_gateway_enabled is True
    assert gateway.gateway_info(path)
    route = gateway.resolve_route(
        path,
        {"session_id": "10000000-0000-4000-8000-000000000001"},
        generation=True,
    )
    assert route["service_tier_override"] == "fast"


def test_default_switch_only_updates_gateway_route_without_reloading_hud(setup):
    from codex_usage_hud.core.parser import ParsedSession
    from codex_usage_hud.renderer_payload_builder import payload_from_snapshot

    path, store, relay = setup
    gateway.activate_gateway(path, relay)
    before = path.read_bytes()
    reload = MagicMock(side_effect=AssertionError("route switches must not reload HUD settings"))
    publish = MagicMock()
    context = SimpleNamespace(settings_store=store, settings_mtime=123,
                              reload_user_config=reload,
                              runtime_events=SimpleNamespace(publish=publish))
    status = _handle_renderer_settings_command(
        {"action": "providerSetDefault", "provider": "beta", "requestId": "route-1"},
        context, Event(), Event(),
    )
    assert status["kind"] != "error"
    assert status["requestId"] == "route-1"
    assert status["providerSetDefault"]["gateway"] is True
    assert "代理默认路由" in status["message"]
    assert path.read_bytes() == before
    assert context.settings_mtime == 123
    reload.assert_not_called()
    publish.assert_not_called()
    assert gateway.gateway_info(path)["default_provider"] == "beta"
    # Full and settings-only snapshots must override the cached registry default.
    payload = payload_from_snapshot(ParsedSession(), default_provider="alpha")
    assert payload.to_json()["settings"]["default_provider"] == "beta"
    assert payload.to_domain_json("settings")["settings"]["provider_gateway"]["default_provider"] == "beta"


def test_generic_save_cannot_bypass_gateway_transition(setup):
    _, store, _ = setup
    status = _handle_renderer_settings_command(
        {"action": "save", "settings": {"provider_gateway_enabled": True}},
        SimpleNamespace(settings_store=store), Event(), Event(),
    )
    assert status["kind"] == "error"
    assert store.load().provider_gateway_enabled is False
