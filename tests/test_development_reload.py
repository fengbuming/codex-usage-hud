from types import SimpleNamespace
from unittest.mock import MagicMock
import json

import pytest

from codex_usage_hud import development_reload as reload
from codex_usage_hud.provider_proxy_reload import bootstrap_digest


@pytest.mark.parametrize("health,expected", [
    ({"pid": 3000}, False),
    ({"pid": 3000, "rendererReady": False}, False),
    ({"pid": 3000, "rendererReady": True}, True),
    ({"pid": 1000, "rendererReady": True}, False),
])
def test_reload_requires_renderer_ack_not_just_http_listener(monkeypatch, health, expected):
    response = SimpleNamespace(status=200, read=lambda: json.dumps(health).encode())
    connection = MagicMock()
    connection.getresponse.return_value = response
    monkeypatch.setattr(reload.http.client, "HTTPConnection", lambda *args, **kwargs: connection)
    assert reload._bridge_matches_pid("http://127.0.0.1:57322", 3000) is expected
    connection.close.assert_called_once()


def test_hud_observation_uses_existing_persisted_cdp_schema(tmp_path, monkeypatch):
    from codex_usage_hud.platforms import cdp_probe
    from codex_usage_hud.renderer_cdp import connection

    state = tmp_path / "renderer_cdp_state.json"
    state.write_text(json.dumps({"lastSuccessfulPort": 50270, "lastRequestedPort": 50271}), encoding="utf-8")
    monkeypatch.setattr(reload, "runtime_file_path", lambda _name: state)
    targets = MagicMock(return_value=[{"type": "page", "url": "app://-/index.html", "webSocketDebuggerUrl": "ws://127.0.0.1:50270/devtools/page/test"}])
    monkeypatch.setattr(cdp_probe, "list_targets", targets)
    socket = MagicMock()
    monkeypatch.setattr(connection, "connect_websocket", lambda _url, _timeout: socket)
    monkeypatch.setattr(connection, "send_command", MagicMock())
    payload = {"bridge": "http://127.0.0.1:57322", "settingsOpen": False}
    monkeypatch.setattr(connection, "receive_message", lambda _connection: json.dumps({"id": 1, "result": {"result": {"value": json.dumps(payload)}}}))
    assert reload._hud_observation() == payload
    targets.assert_called_once_with(50270, 3)
    socket.close.assert_called_once()


def test_legacy_proxy_is_never_stopped_or_claimed_as_reloaded(tmp_path):
    client = MagicMock()
    client._call.return_value = {"pid": 2000, "version": 2}
    with pytest.raises(RuntimeError, match="first migration"):
        reload.reload_proxy(client, tmp_path)
    client._call.assert_called_once_with("health")


def test_control_bootstrap_changes_require_maintenance_without_touching_proxy(tmp_path):
    client = MagicMock()
    client._call.return_value = {"pid": 2000, "reloadSupported": True, "bootstrapDigest": "old"}
    with pytest.raises(RuntimeError, match="bootstrap"):
        reload.reload_proxy(client, tmp_path)
    client._call.assert_called_once_with("health")


def test_proxy_reload_verifies_same_pid_and_new_generation(tmp_path):
    client = MagicMock()
    state = {"pid": 2000, "reloadSupported": True, "bootstrapDigest": bootstrap_digest(tmp_path), "generation": "old"}

    def call(action):
        if action == "reload":
            state["generation"] = "new"
        return dict(state)

    client._call.side_effect = call
    result = reload.reload_proxy(client, tmp_path)
    assert result["pid"] == 2000 and result["generation"] == "new"
    assert [call.args[0] for call in client._call.call_args_list] == ["health", "reload", "health"]


def test_windows_source_reload_prefers_same_interpreter_windowless_executable(tmp_path, monkeypatch):
    executable = tmp_path / "python.exe"
    windowless = tmp_path / "pythonw.exe"
    windowless.touch()
    monkeypatch.setattr(reload.sys, "executable", str(executable))
    monkeypatch.delattr(reload.sys, "frozen", raising=False)
    assert reload._hud_python_executable(platform="nt") == str(windowless)
    monkeypatch.setattr(reload.sys, "frozen", True, raising=False)
    assert reload._hud_python_executable(platform="nt") == str(executable)


@pytest.mark.skipif(reload.os.name != "nt", reason="Windows process console flags")
def test_process_identity_probe_hides_windows_console(monkeypatch):
    monkeypatch.setattr(reload.shutil, "which", lambda _name: "powershell.exe")
    run = MagicMock(return_value=SimpleNamespace(returncode=0, stdout="pythonw -m codex_usage_hud"))
    monkeypatch.setattr(reload.subprocess, "run", run)
    assert "codex_usage_hud" in reload._hud_command_line(1000)
    assert run.call_args.kwargs["creationflags"] == reload.subprocess.CREATE_NO_WINDOW


@pytest.fixture
def hud_reload(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    package = root / "src/codex_usage_hud"
    package.mkdir(parents=True)
    (package / "__main__.py").write_text("", encoding="utf-8")
    state = {"pid": 1000, "alive": True, "settingsOpen": False}
    health = MagicMock(return_value={"pid": 2000, "version": 3})
    monkeypatch.setattr(reload, "proxy_health", health)
    monkeypatch.setattr(reload.instance_lock, "read_pid", lambda _path: state["pid"])
    monkeypatch.setattr(reload.instance_lock, "process_exists", lambda pid: state["alive"] if pid == 1000 else pid == state["pid"])
    monkeypatch.setattr(reload, "_hud_command_line", lambda _pid: "python -m codex_usage_hud")
    monkeypatch.setattr(reload, "_hud_observation", lambda: {"bridge": "http://127.0.0.1:57322/settings", "settingsOpen": state["settingsOpen"]})
    monkeypatch.setattr(reload, "_bridge_matches_pid", lambda _bridge, pid: pid == state["pid"])
    stop = MagicMock(side_effect=lambda _bridge, _pid: state.update(alive=False))
    monkeypatch.setattr(reload, "_request_development_stop", stop)
    terminate = MagicMock(side_effect=AssertionError("must not terminate the independent helper"))
    monkeypatch.setattr(reload.instance_lock, "terminate_process", terminate)
    monkeypatch.setattr(reload, "runtime_file_path", lambda name: tmp_path / name)
    find_spec = reload.importlib.util.find_spec
    monkeypatch.setattr(reload.importlib.util, "find_spec", lambda name: object() if name == "zstandard" else find_spec(name))

    def spawn(*args, **kwargs):
        state["pid"] = 3000
        return SimpleNamespace(pid=3000, poll=lambda: None)

    launch = MagicMock(side_effect=spawn)
    monkeypatch.setattr(reload.subprocess, "Popen", launch)
    return root, state, health, stop, terminate, launch


def test_hud_source_reload_starts_fresh_python_and_keeps_proxy_pid(hud_reload):
    root, state, health, stop, terminate, launch = hud_reload
    result = reload.restart_hud(object(), root)
    assert result["hudPid"] == 3000 and result["proxyPid"] == 2000
    stop.assert_called_once_with("http://127.0.0.1:57322/settings", 1000)
    terminate.assert_not_called()
    args, options = launch.call_args
    assert args[0][-2:] == ["-m", "codex_usage_hud"]
    assert options["cwd"] == str(root)
    assert options["env"]["PYTHONPATH"].split(reload.os.pathsep)[0] == str(root / "src")
    assert health.call_count >= 2


def test_open_settings_drafts_prevent_hud_reload(hud_reload):
    root, state, _, stop, terminate, launch = hud_reload
    state["settingsOpen"] = True
    with pytest.raises(RuntimeError, match="drafts"):
        reload.restart_hud(object(), root)
    stop.assert_not_called()
    terminate.assert_not_called()
    launch.assert_not_called()


def test_proxy_pid_in_hud_lock_is_rejected_before_any_stop(hud_reload):
    root, _, health, stop, terminate, launch = hud_reload
    health.return_value = {"pid": 1000}
    with pytest.raises(RuntimeError, match="separated"):
        reload.restart_hud(object(), root)
    stop.assert_not_called()
    terminate.assert_not_called()
    launch.assert_not_called()


def test_helper_command_line_cannot_be_used_as_hud_main_pid(hud_reload, monkeypatch):
    root, _, _, stop, terminate, launch = hud_reload
    monkeypatch.setattr(reload, "_hud_command_line", lambda _pid: "python -m codex_usage_hud --provider-proxy-helper")
    with pytest.raises(RuntimeError, match="not the HUD main"):
        reload.restart_hud(object(), root)
    stop.assert_not_called()
    terminate.assert_not_called()
    launch.assert_not_called()


def test_missing_runtime_dependency_does_not_stop_hud(hud_reload, monkeypatch):
    root, _, _, stop, terminate, launch = hud_reload
    find_spec = reload.importlib.util.find_spec
    monkeypatch.setattr(reload.importlib.util, "find_spec", lambda name: None if name == "zstandard" else find_spec(name))
    with pytest.raises(RuntimeError, match="zstandard"):
        reload.restart_hud(object(), root)
    stop.assert_not_called()
    terminate.assert_not_called()
    launch.assert_not_called()


def test_development_stop_requires_exact_main_pid_and_preserves_proxy(monkeypatch):
    import os
    from threading import Event

    from codex_usage_hud.config import UserConfig
    from codex_usage_hud.runtime_commands import _handle_renderer_settings_command

    monkeypatch.setattr(reload, "gateway_client", lambda **_kwargs: object())
    monkeypatch.setattr(reload, "proxy_health", lambda _client: {"pid": os.getpid() + 1})
    context = SimpleNamespace(user_config=UserConfig.defaults())
    restart, stop = Event(), Event()
    rejected = _handle_renderer_settings_command({"action": "developmentStop", "expectedPid": -1, "preserveProxy": True}, context, restart, stop)
    assert rejected["kind"] == "error" and not stop.is_set()
    accepted = _handle_renderer_settings_command({"action": "developmentStop", "expectedPid": os.getpid(), "preserveProxy": True}, context, restart, stop)
    assert accepted["kind"] != "error" and stop.is_set()
    assert not restart.is_set()


@pytest.mark.parametrize("mode", ["hud", "proxy", "all"])
def test_settings_reload_executes_only_selected_components(monkeypatch, mode):
    from threading import Event

    from codex_usage_hud.config import UserConfig
    from codex_usage_hud.runtime_commands import _handle_renderer_settings_command

    client = object()
    monkeypatch.setattr(reload, "gateway_client", lambda: client)
    proxy = MagicMock(return_value={"pid": 2000, "generation": "new"})
    launch = MagicMock(return_value={"pending": True, "coordinatorPid": 3000})
    monkeypatch.setattr(reload, "reload_proxy", proxy)
    monkeypatch.setattr(reload, "launch_hud_reload", launch)
    context = SimpleNamespace(user_config=UserConfig.defaults())
    restart, stop = Event(), Event()
    status = _handle_renderer_settings_command({"action": "developmentReload", "mode": mode, "requestId": "reload-test"}, context, restart, stop)
    assert status["kind"] != "error" and status["requestId"] == "reload-test"
    assert status["developmentReload"]["mode"] == mode
    assert proxy.call_count == (mode in {"proxy", "all"})
    assert launch.call_count == (mode in {"hud", "all"})
    assert not stop.is_set() and not restart.is_set()


def test_settings_all_does_not_restart_hud_when_proxy_reload_fails(monkeypatch):
    from threading import Event

    from codex_usage_hud.config import UserConfig
    from codex_usage_hud.runtime_commands import _handle_renderer_settings_command

    monkeypatch.setattr(reload, "gateway_client", lambda **_kwargs: object())
    monkeypatch.setattr(reload, "reload_proxy", MagicMock(side_effect=RuntimeError("keep old proxy")))
    launch = MagicMock()
    monkeypatch.setattr(reload, "launch_hud_reload", launch)
    status = _handle_renderer_settings_command({"action": "developmentReload", "mode": "all"}, SimpleNamespace(user_config=UserConfig.defaults()), Event(), Event())
    assert status["kind"] == "error"
    launch.assert_not_called()


def test_hud_source_reload_works_without_a_gateway(hud_reload):
    root, _, health, stop, terminate, launch = hud_reload
    result = reload.restart_hud(None, root)
    assert result["hudPid"] == 3000 and result["proxyPid"] is None
    health.assert_not_called()
    stop.assert_called_once()
    terminate.assert_not_called()
    launch.assert_called_once()


def test_optional_gateway_client_does_not_start_a_helper(tmp_path, monkeypatch):
    monkeypatch.setattr("codex_usage_hud.provider_gateway.gateway_info", lambda: {})
    monkeypatch.setattr(reload, "runtime_file_path", lambda name: tmp_path / name)
    assert reload.gateway_client(required=False) is None
    with pytest.raises(RuntimeError, match="No authenticated"):
        reload.gateway_client()


def test_development_handoff_without_gateway_requests_only_hud_exit(monkeypatch):
    import os
    from threading import Event
    from codex_usage_hud.runtime_commands import _handle_renderer_settings_command

    monkeypatch.setattr(reload, "gateway_client", lambda **_kwargs: None)
    health = MagicMock(side_effect=AssertionError("no proxy should be contacted"))
    monkeypatch.setattr(reload, "proxy_health", health)
    stop = Event()
    status = _handle_renderer_settings_command(
        {"action": "developmentStop", "expectedPid": os.getpid(), "preserveProxy": True},
        SimpleNamespace(), Event(), stop,
    )
    assert status["kind"] != "error" and stop.is_set()
    health.assert_not_called()
