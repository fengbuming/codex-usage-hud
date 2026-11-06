"""Manual fallback never installs, closes Codex, or changes global routing."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codex_usage_hud import provider_compatibility as compat


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_instructions_pin_isolated_core_without_executing_commands(tmp_path, monkeypatch, platform):
    run = MagicMock(side_effect=AssertionError("help cannot run a process"))
    monkeypatch.setattr(compat.subprocess, "run", run)
    monkeypatch.setattr(compat.subprocess, "Popen", run)
    result = compat.manual_instructions(platform=platform, runtime_dir=tmp_path / "User's files")
    assert result["version"] == "0.165.0"
    assert "@openai/codex@0.165.0" in result["installCommand"]
    assert "--prefix" in result["installCommand"]
    assert "--global" not in result["installCommand"]
    assert "--provider-compatible-codex" in result["launchCommand"]
    run.assert_not_called()


def test_hoisted_npm_native_package_is_found_and_version_checked(tmp_path, monkeypatch):
    core = tmp_path / "node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
    core.parent.mkdir(parents=True)
    core.write_bytes(b"placeholder")
    monkeypatch.setattr(compat.sys, "platform", "win32")
    run = MagicMock(return_value=SimpleNamespace(returncode=0, stdout="codex-cli 0.165.0\n"))
    monkeypatch.setattr(compat.subprocess, "run", run)
    assert compat._core_executable(tmp_path) == core
    assert run.call_args.args[0] == [str(core), "--version"]
    run.return_value.stdout = "codex-cli 0.166.0\n"
    with pytest.raises(ValueError, match="0.165.0"):
        compat._core_executable(tmp_path)


def test_pythonw_hud_emits_a_console_launcher_for_visible_manual_errors(tmp_path, monkeypatch):
    console = tmp_path / "python.exe"
    console.write_bytes(b"placeholder")
    monkeypatch.setattr(compat.sys, "executable", str(tmp_path / "pythonw.exe"))
    instructions = compat.manual_instructions(platform="win32", runtime_dir=tmp_path)
    assert "pythonw.exe" not in instructions["launchCommand"]
    assert "python.exe" in instructions["launchCommand"]


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_manual_launch_keeps_parent_environment_and_gateway_alive(tmp_path, monkeypatch, platform):
    core, app = tmp_path / "codex-core", tmp_path / "Codex Desktop"
    monkeypatch.setattr(compat.sys, "platform", platform)
    monkeypatch.setattr(compat, "_core_executable", lambda _: core)
    monkeypatch.setattr(compat, "_desktop_executable", lambda _: app)
    monkeypatch.setattr("codex_usage_hud.codex_app_runtime.audited_running_codex_desktop_processes", lambda: [])
    monkeypatch.setattr("codex_usage_hud.codex_app_runtime.macos_running_codex_desktop_processes", lambda: [])
    monkeypatch.setattr("codex_usage_hud.renderer_startup.read_persisted_cdp_port", lambda **_: 9235)
    monkeypatch.setenv("CODEX_CLI_PATH", "parent-core")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "frozen-parent")
    popen = MagicMock(return_value=SimpleNamespace(pid=123))
    monkeypatch.setattr(compat.subprocess, "Popen", popen)
    assert compat.launch_compatible_codex(tmp_path, check=True)["desktopRunning"] is False
    popen.assert_not_called()
    result = compat.launch_compatible_codex(tmp_path)
    assert result["pid"] == 123
    args, options = popen.call_args
    assert args[0][0] == str(app)
    assert "--remote-debugging-port=9235" in args[0]
    assert options["env"]["CODEX_CLI_PATH"] == str(core)
    assert "_PYI_ARCHIVE_FILE" not in options["env"]
    import os
    assert os.environ["CODEX_CLI_PATH"] == "parent-core"


def test_running_desktop_prevents_a_second_launch_without_stopping_it(tmp_path, monkeypatch):
    monkeypatch.setattr(compat.sys, "platform", "win32")
    monkeypatch.setattr(compat, "_core_executable", lambda _: Path("core.exe"))
    monkeypatch.setattr(compat, "_desktop_executable", lambda _: Path("Codex.exe"))
    monkeypatch.setattr("codex_usage_hud.codex_app_runtime.audited_running_codex_desktop_processes", lambda: [object()])
    spawn = MagicMock()
    monkeypatch.setattr(compat.subprocess, "Popen", spawn)
    with pytest.raises(ValueError, match="关闭所有 Codex"):
        compat.launch_compatible_codex(tmp_path)
    spawn.assert_not_called()


def test_help_command_only_returns_manual_steps(monkeypatch):
    from threading import Event
    from codex_usage_hud.config import UserConfig
    from codex_usage_hud.runtime_commands import _handle_renderer_settings_command

    help_ = {"version":"0.165.0", "installCommand":"install", "launchCommand":"launch"}
    monkeypatch.setattr(compat, "manual_instructions", lambda: help_)
    restart, stop = Event(), Event()
    status = _handle_renderer_settings_command(
        {"action":"providerCompatibilityHelp", "requestId":"manual-1"},
        SimpleNamespace(user_config=UserConfig.defaults()), restart, stop,
    )
    assert status["providerCompatibility"] == help_
    assert status["requestId"] == "manual-1"
    assert not restart.is_set() and not stop.is_set()
