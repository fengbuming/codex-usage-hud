from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess as stdlib_subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codex_usage_hud import codex_app_runtime as app


def test_windows_process_audit_excludes_npm_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "ProcessId": 11,
            "Name": "codex.exe",
            "ExecutablePath": "C:\\Users\\test\\AppData\\Roaming\\npm\\codex.exe",
            "CommandLine": "codex.exe --remote-debugging-port=59999",
            "CreationDate": "2026-08-12T12:00:24.37125+08:00",
        },
        {
            "ProcessId": 22,
            "Name": "ChatGPT.exe",
            "ExecutablePath": (
                "C:\\Program Files\\WindowsApps\\OpenAI.Codex_1.0_x64__id"
                "\\app\\ChatGPT.exe"
            ),
            "CommandLine": "ChatGPT.exe --remote-debugging-port=59629",
            "CreationDate": "20260812120130.119660+480",
        },
    ]
    completed = SimpleNamespace(returncode=0, stdout=json.dumps(rows), stderr="")
    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setattr(app.subprocess, "run", MagicMock(return_value=completed))

    desktop = app.windows_running_codex_desktop_processes()
    cli_pids = app.running_standalone_codex_cli_pids()

    assert [process.pid for process in desktop] == [22]
    assert cli_pids == (11,)
    assert desktop[0].started_at == "2026-08-12T04:01:30.119660Z"


def test_windows_process_started_at_accepts_cim_iso_and_dmtf_values() -> None:
    assert app._windows_process_started_at("2026-08-12T12:00:24.37125+08:00") == (
        "2026-08-12T04:00:24.371250Z"
    )
    assert app._windows_process_started_at("20260812120130.119660+480") == (
        "2026-08-12T04:01:30.119660Z"
    )
    assert app._windows_process_started_at("/Date(1786507290119)/") == (
        "2026-08-12T04:01:30.119000Z"
    )


def test_windows_process_audit_fails_closed_on_invalid_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = SimpleNamespace(returncode=0, stdout="not-json", stderr="")
    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setattr(app.subprocess, "run", MagicMock(return_value=completed))

    with pytest.raises(RuntimeError, match="invalid JSON"):
        app.audited_running_codex_desktop_processes()
    assert app.windows_running_codex_desktop_processes() == []


def test_macos_process_audit_requires_verified_app_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = SimpleNamespace(
        returncode=0,
        stdout=(
            "11 /usr/local/bin/codex --remote-debugging-port=59999\n"
            "22 /Applications/Codex.app/Contents/MacOS/Codex "
            "--remote-debugging-port 59629\n"
            "33 /Applications/OpenAI Codex.app/Contents/MacOS/Codex "
            "--remote-debugging-port=60123\n"
        ),
        stderr="",
    )
    monkeypatch.setattr(app.sys, "platform", "darwin")
    monkeypatch.setenv(app.CODEX_APP_PATH_ENV, "/Applications/OpenAI Codex.app")
    monkeypatch.setattr(app.subprocess, "run", MagicMock(return_value=completed))

    processes = app.macos_running_codex_desktop_processes()

    assert [process.pid for process in processes] == [22, 33]
    assert processes[1].executable_path == (
        "/Applications/OpenAI Codex.app/Contents/MacOS/Codex"
    )


def test_process_classifier_accepts_only_verified_desktop_paths() -> None:
    assert app.is_codex_client_process("ChatGPT.exe")
    assert app.is_codex_client_process(
        "Codex.exe",
        "C:\\Program Files\\WindowsApps\\OpenAI.Codex_1.0__id\\app\\Codex.exe",
    )
    assert not app.is_codex_client_process(
        "Codex.exe",
        "C:\\Users\\test\\AppData\\Roaming\\npm\\codex.exe",
    )
    assert not app.is_codex_client_process("codex-hud.exe")


def test_macos_debug_launch_uses_explicit_port_and_reports_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen = MagicMock()
    remembered: list[int] = []
    monkeypatch.setattr(app.sys, "platform", "darwin")
    monkeypatch.setenv(app.CODEX_APP_PATH_ENV, "/Applications/OpenAI Codex.app")
    monkeypatch.setattr(app.subprocess, "Popen", popen)

    launched = app.launch_codex_app(
        debugger=True,
        cdp_port=59629,
        on_debugger_launch=remembered.append,
    )

    assert launched
    command = popen.call_args.args[0]
    assert command[:2] == ["open", "/Applications/OpenAI Codex.app"]
    assert "--remote-debugging-port=59629" in command
    assert "--remote-allow-origins=http://127.0.0.1:59629" in command
    assert remembered == [59629]


def test_windows_debug_launch_prefers_verified_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path("C:/Codex/ChatGPT.exe")
    opened: list[tuple[Path, str, Path]] = []
    remembered: list[int] = []
    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setattr(app, "codex_app_executable_candidates", lambda: [executable])
    monkeypatch.setattr(app, "_windows_registry_environment", lambda: {})
    monkeypatch.setattr(
        app,
        "_shell_execute_open_with_elevation_fallback",
        lambda target, *, parameters, working_dir: opened.append(
            (Path(target), parameters, Path(working_dir))
        )
        or True,
    )

    assert app.launch_codex_app(
        debugger=True,
        cdp_port=61234,
        on_debugger_launch=remembered.append,
    )

    assert opened == [
        (
            executable,
            "--remote-debugging-port=61234 "
            "--remote-allow-origins=http://127.0.0.1:61234",
            executable.parent,
        )
    ]
    assert remembered == [61234]


def test_windows_executable_launch_filters_pyinstaller_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "Codex.exe"
    executable.write_bytes(b"placeholder")
    launched: list[dict[str, object]] = []

    def fake_popen(_command: list[str], **kwargs: object) -> object:
        launched.append(kwargs)
        return object()

    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setattr(app, "codex_app_executable_candidates", lambda: [executable])
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "C:/hud.exe")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    monkeypatch.setattr(app.subprocess, "Popen", fake_popen)

    assert app.launch_codex_app(debugger=False)

    assert launched
    assert not any(
        str(name).casefold().startswith("_pyi_")
        for name in launched[0]["env"]
    )


@pytest.mark.parametrize("activated", [True, False])
def test_store_launch_preserves_identity_and_debug_arguments(
    monkeypatch: pytest.MonkeyPatch, activated: bool,
) -> None:
    executable = Path(
        "C:/Program Files/WindowsApps/OpenAI.Codex_26.924_x64__2p2nqsd0c76g0/app/ChatGPT.exe"
    )
    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.delenv(app.CODEX_APP_ID_ENV, raising=False)
    activate = MagicMock(return_value=activated)
    direct = MagicMock()
    monkeypatch.setattr(app, "activate_packaged_app", activate)
    monkeypatch.setattr(app, "_direct_windows_executable_open", direct)
    parameters = app.codex_app_debugger_parameters(61234)

    assert app._shell_execute_open_with_elevation_fallback(
        executable, parameters=parameters, working_dir=executable.parent,
    ) is activated

    activate.assert_called_once_with(app.CODEX_APP_DEFAULT_ID, parameters)
    direct.assert_not_called()


def test_packaged_shell_launch_filters_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    inherited: dict[str, str] = {}
    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "C:/hud.exe")

    def activate(app_id: str, arguments: str) -> bool:
        assert app_id == "Example.Codex!App"
        assert arguments == "--remote-debugging-port=61234"
        inherited.update(os.environ)
        return True

    monkeypatch.setattr(app, "activate_packaged_app", activate)
    assert app._shell_execute_open(
        "shell:AppsFolder\\Example.Codex!App", parameters="--remote-debugging-port=61234",
    )
    assert "_PYI_ARCHIVE_FILE" not in inherited
    assert os.environ["_PYI_ARCHIVE_FILE"] == "C:/hud.exe"


def test_windows_launch_inherits_registry_env_for_shell_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path("C:/Codex/ChatGPT.exe")
    inherited: dict[str, str] = {}
    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setattr(app, "codex_app_executable_candidates", lambda: [executable])
    monkeypatch.setattr(app.os, "name", "nt")
    monkeypatch.delenv("_168661_API_KEY", raising=False)
    monkeypatch.setenv("PATH", "C:\\existing")
    monkeypatch.setattr(
        app,
        "_windows_registry_environment",
        lambda: {"_168661_API_KEY": "registry-value", "path": "C:\\registry"},
    )

    def fake_open(
        target: object,
        *,
        parameters: str,
        working_dir: object,
    ) -> bool:
        inherited.update(os.environ)
        return True

    monkeypatch.setattr(
        app,
        "_shell_execute_open_with_elevation_fallback",
        fake_open,
    )

    assert app.launch_codex_app(debugger=False)

    assert inherited["_168661_API_KEY"] == "registry-value"
    assert inherited["PATH"] == "C:\\existing"


def test_windows_stop_controls_only_audited_processes() -> None:
    process = app.CodexDesktopProcess(22, "ChatGPT.exe", "C:/Codex/ChatGPT.exe", "")
    alive = {22}
    terminated: list[int] = []

    def terminate(pid: int) -> bool:
        terminated.append(pid)
        alive.discard(pid)
        return True

    assert app.stop_windows_codex_app(
        process_query=lambda: [process],
        process_probe=lambda pid: pid in alive,
        terminate=terminate,
        monotonic=lambda: 0.0,
        sleep=lambda _seconds: None,
    )
    assert terminated == [22]


def test_windows_stop_fails_closed_when_process_audit_fails() -> None:
    def fail_query() -> list[app.CodexDesktopProcess]:
        raise RuntimeError("audit unavailable")

    assert not app.stop_windows_codex_app(process_query=fail_query)


def test_restart_stops_before_debug_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(
        app,
        "stop_codex_app",
        lambda *, timeout_seconds: events.append(("stop", timeout_seconds)) or True,
    )
    monkeypatch.setattr(
        app,
        "launch_codex_app",
        lambda **kwargs: events.append(("launch", kwargs)) or True,
    )

    assert app.restart_codex_app(
        debugger=True,
        cdp_port=62000,
        timeout_seconds=3.5,
    )
    assert events == [
        ("stop", 3.5),
        (
            "launch",
            {
                "debugger": True,
                "cdp_port": 62000,
                "on_debugger_launch": None,
            },
        ),
    ]


def test_codex_processes_exited_fails_closed_on_audit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setattr(
        app,
        "audited_running_codex_desktop_processes",
        lambda: [],
    )
    assert app.codex_processes_exited()

    def fail() -> list[app.CodexDesktopProcess]:
        raise RuntimeError("unavailable")

    monkeypatch.setattr(app, "audited_running_codex_desktop_processes", fail)
    assert not app.codex_processes_exited()


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.001, seconds)


def test_wait_for_visible_window_observes_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = iter(
        [
            SimpleNamespace(status="not_found", reason="missing", hwnd=0),
            SimpleNamespace(status="visible", reason="", hwnd=123),
        ]
    )
    tracker = SimpleNamespace(enabled=True, get_window_snapshot=lambda: next(snapshots))
    clock = _FakeClock()
    monkeypatch.setattr(app.sys, "platform", "win32")

    result = app.wait_for_visible_codex_window(
        timeout_seconds=1.0,
        poll_seconds=0.01,
        tracker_factory=lambda: tracker,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result == (True, "visible", "", 123)


def test_prepare_window_activates_existing_instance_without_relaunch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = iter(
        [
            SimpleNamespace(status="visible", reason="", hwnd=321),
            SimpleNamespace(status="visible", reason="", hwnd=321),
        ]
    )
    active = iter([False, True])
    tracker = SimpleNamespace(
        enabled=True,
        get_window_snapshot=lambda: next(snapshots),
        is_active=lambda _hwnd: next(active),
        activate_main_window=MagicMock(return_value=0),
    )
    activations: list[str] = []
    launches: list[dict[str, object]] = []
    monkeypatch.setattr(app.sys, "platform", "win32")

    result = app.prepare_codex_window_for_renderer(
        timeout_seconds=1.0,
        tracker_factory=lambda: tracker,
        processes_running=lambda: True,
        activate=lambda: activations.append("activate") or True,
        launch=lambda **kwargs: launches.append(kwargs) or True,
        monotonic=lambda: 0.0,
        sleep=lambda _seconds: None,
    )

    assert result == (True, "visible", "", 321)
    assert activations == ["activate"]
    assert launches == []
    tracker.activate_main_window.assert_not_called()


def test_prepare_missing_window_launches_once_with_caller_selected_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = SimpleNamespace(
        enabled=True,
        get_window_snapshot=lambda: SimpleNamespace(
            status="not_found",
            reason="Codex HWND not found",
            hwnd=0,
        ),
        is_active=lambda _hwnd: False,
        activate_main_window=lambda: 0,
    )
    launches: list[dict[str, object]] = []
    monkeypatch.setattr(app.sys, "platform", "win32")

    result = app.prepare_codex_window_for_renderer(
        timeout_seconds=0.0,
        poll_seconds=0.0,
        launch_if_missing=True,
        cdp_port=63333,
        tracker_factory=lambda: tracker,
        processes_running=lambda: False,
        launch=lambda **kwargs: launches.append(kwargs) or True,
        monotonic=lambda: 0.0,
        sleep=lambda _seconds: None,
    )

    assert result == (False, "not_found", "Codex HWND not found", 0)
    assert launches == [{"debugger": True, "cdp_port": 63333}]


def test_owner_import_does_not_eagerly_load_pyside() -> None:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = stdlib_subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import codex_usage_hud.codex_app_runtime; "
                "print(any(name.startswith('PySide') for name in sys.modules))"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "False"
