import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_usage_hud import codex_cli_launcher as launcher
from codex_usage_hud.config import UserConfig
from codex_usage_hud.runtime_commands import GeneralCommandPorts, dispatch_command
from codex_usage_hud.runtime_commands import RuntimeCommandPorts


def test_build_codex_cli_command_matches_reference_profile_shape(tmp_path: Path) -> None:
    command = launcher.build_codex_cli_command(
        provider="cunai",
        profile="cunai",
        permission="full",
        resume=True,
        use_proxy=True,
        proxy_port=7897,
        workdir=str(tmp_path),
        shell="powershell",
    )

    assert "$env:HTTP_PROXY = 'http://127.0.0.1:7897'" in command
    assert "$env:HTTPS_PROXY = 'http://127.0.0.1:7897'" in command
    assert f"Set-Location -LiteralPath '{str(tmp_path)}'" in command
    assert "codex --profile cunai --dangerously-bypass-approvals-and-sandbox resume" in command


def test_build_codex_cli_command_can_resume_a_specific_session(tmp_path: Path) -> None:
    command = launcher.build_codex_cli_command(
        provider="cunai",
        profile="cunai",
        permission="workspace-write",
        resume=True,
        resume_session_id="10000000-0000-4000-8000-000000000001",
        workdir=str(tmp_path),
    )

    assert "codex --profile cunai --sandbox workspace-write --ask-for-approval on-request resume 10000000-0000-4000-8000-000000000001" in command


def test_build_codex_cli_args_appends_model_override() -> None:
    assert launcher.build_codex_cli_args(
        provider="custom", default_provider="custom", permission="full"
    ) == ["--dangerously-bypass-approvals-and-sandbox"]
    assert launcher.build_codex_cli_args(
        provider="custom",
        default_provider="custom",
        permission="full",
        model="gpt-5",
    ) == ["--config", "model=gpt-5", "--dangerously-bypass-approvals-and-sandbox"]
    assert launcher.build_codex_cli_args(
        provider="custom",
        permission="read-only",
        model="claude-sonnet",
    ) == [
        "--config",
        "model_provider=custom",
        "--config",
        "model=claude-sonnet",
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "on-request",
    ]


def test_build_codex_cli_command_includes_model_override() -> None:
    command = launcher.build_codex_cli_command(
        provider="custom",
        default_provider="custom",
        permission="full",
        model="gpt-5",
        shell="powershell",
    )
    assert "codex --config model=gpt-5 --dangerously-bypass-approvals-and-sandbox" in command


def test_build_codex_cli_command_model_override_can_combine_with_profile() -> None:
    command = launcher.build_codex_cli_command(
        provider="cunai",
        profile="cunai",
        permission="full",
        model="gpt-5",
        resume=True,
        shell="cmd",
    )
    assert (
        "codex --profile cunai --config model=gpt-5 --dangerously-bypass-approvals-and-sandbox resume"
        in command
    )


def test_build_codex_cli_title_uses_provider_and_workdir_name(tmp_path: Path) -> None:
    assert launcher.build_codex_cli_title(
        provider="CunAI",
        workdir=str(tmp_path),
    ) == f"[cunai] {tmp_path.name}"


def test_terminal_process_command_sets_shell_title() -> None:
    command = launcher._terminal_process_command(
        {
            "executable": "C:/pwsh.exe",
            "shell": "powershell",
            "kind": "shell",
            "shellExecutable": "C:/pwsh.exe",
        },
        "codex --help",
        "C:/project",
        title="[cunai] project",
    )

    assert command[-1] == (
        "$Host.UI.RawUI.WindowTitle = '[cunai] project'\n"
        "codex --help"
    )


def test_resolve_cli_profile_prefers_provider_profile_file(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('model_provider = "custom"\n', encoding="utf-8")
    (tmp_path / "cunai.config.toml").write_text(
        'model_provider = "cunai"\n',
        encoding="utf-8",
    )

    assert launcher.resolve_cli_profile("cunai", codex_home=tmp_path) == (
        "cunai",
        "custom",
    )
    assert launcher.resolve_cli_profile("custom", codex_home=tmp_path) == ("", "custom")


def test_discover_workdirs_lists_local_projects(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    missing_dir = tmp_path / "project-missing"
    project_a.mkdir()
    project_b.mkdir()
    (tmp_path / ".codex-global-state.json").write_text(
        json.dumps(
            {
                "local-projects": {
                    "abc": {"name": "Alpha", "rootPaths": [str(project_a)]},
                    "def": {
                        "name": "Beta",
                        "rootPaths": [str(project_b), str(missing_dir)],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    result = launcher.discover_workdirs(
        sessions_root=sessions,
        state_db_path=tmp_path / "state.sqlite",
        current_workdir=str(project_a),
    )

    paths = {item["path"] for item in result}
    assert paths == {str(project_a), str(project_b)}
    assert all(item["source"] == "Codex Desktop" for item in result)
    labels = {item["path"]: item["label"] for item in result}
    assert labels[str(project_a)] == "Alpha"
    assert labels[str(project_b)] == "Beta"
    assert result[0]["path"] == str(project_a)


def test_discover_workdirs_tolerates_missing_global_state(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    current = tmp_path / "current"
    current.mkdir()

    result = launcher.discover_workdirs(
        sessions_root=sessions,
        current_workdir=str(current),
    )

    assert {item["path"] for item in result} == {str(current)}
    assert result[0]["path"] == str(current)


def test_discover_codex_cli_options_does_not_choose_a_default_workdir(
    tmp_path: Path, monkeypatch
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    current = tmp_path / "current"
    current.mkdir()
    monkeypatch.setattr(
        launcher,
        "discover_terminals",
        lambda **_: [{"id": "shell", "recommended": True}],
    )

    result = launcher.discover_codex_cli_options(
        provider="custom",
        sessions_root=sessions,
        current_workdir=current,
        codex_home=tmp_path,
        platform_name="linux",
    )

    assert result["defaultWorkdir"] == ""
    assert result["noProjectWorkdir"] == str(Path.home())
    assert [item["path"] for item in result["workdirs"]] == [str(current)]


@pytest.mark.parametrize(
    ("terminals", "expected"),
    [
        ([], ""),
        ([{"id": "shell"}], "shell"),
        ([{"id": "shell"}, {"id": "preferred", "recommended": True}], "preferred"),
    ],
    ids=["no-terminals", "no-recommendation", "recommended"],
)
def test_discover_codex_cli_options_handles_missing_terminal_recommendation(
    tmp_path: Path, monkeypatch, terminals, expected: str,
) -> None:
    monkeypatch.setattr(launcher, "discover_terminals", lambda **_: terminals)
    monkeypatch.setattr(launcher, "_first_available", lambda *_: "")
    result = launcher.discover_codex_cli_options(
        provider="custom",
        codex_home=tmp_path,
        sessions_root=tmp_path / "sessions",
        state_db_path=tmp_path / "state.sqlite",
        platform_name="linux",
    )
    assert result["defaultTerminal"] == expected
    assert result["terminals"] == terminals


def test_discover_terminals_marks_powershell7_as_recommended(monkeypatch) -> None:
    def fake_first_available(*candidates: object) -> str:
        values = " ".join(str(candidate) for candidate in candidates).lower()
        if "pwsh" in values:
            return "C:/PowerShell/7/pwsh.exe"
        if "powershell" in values:
            return "C:/Windows/powershell.exe"
        if "cmd" in values or "comspec" in values:
            return "C:/Windows/System32/cmd.exe"
        if "wt" in values or "windowsterminal" in values:
            return "C:/WindowsApps/wt.exe"
        if "bash" in values or "git" in values:
            return "C:/Program Files/Git/bin/bash.exe"
        if "wsl" in values:
            return "C:/Windows/System32/wsl.exe"
        return ""

    monkeypatch.setattr(launcher, "_first_available", fake_first_available)
    terminals = launcher.discover_terminals(platform_name="windows")

    assert terminals[0]["id"] == "powershell7"
    assert terminals[0]["recommended"] is True
    assert {item["id"] for item in terminals} >= {
        "powershell7",
        "powershell",
        "windows-terminal",
        "cmd",
    }


def test_launch_codex_cli_uses_selected_terminal_and_workdir(tmp_path: Path, monkeypatch) -> None:
    process_calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 321

    def fake_popen(args, **kwargs):
        process_calls.append({"args": args, **kwargs})
        return FakeProcess()

    monkeypatch.setattr(
        launcher,
        "discover_terminals",
        lambda **_: [
            {
                "id": "powershell7",
                "label": "PowerShell 7",
                "executable": "C:/pwsh.exe",
                "shell": "powershell",
                "kind": "shell",
                "shellExecutable": "C:/pwsh.exe",
            }
        ],
    )
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "C:/hud.exe")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()

    result = launcher.launch_codex_cli(
        terminal_id="powershell7",
        command="codex --help",
        workdir=str(tmp_path),
        codex_home=codex_home,
        platform_name="linux",
    )

    assert result["pid"] == 321
    assert process_calls[0]["args"] == [
        "C:/pwsh.exe",
        "-NoLogo",
        "-NoExit",
        "-Command",
        f"$env:CODEX_HOME = {launcher._shell_quote(str(codex_home), 'powershell')}\ncodex --help",
    ]
    assert process_calls[0]["cwd"] == str(tmp_path)
    assert "stdin" not in process_calls[0]
    assert "stdout" not in process_calls[0]
    assert "stderr" not in process_calls[0]
    assert process_calls[0]["env"]["CODEX_HOME"] == str(codex_home)
    assert not any(
        str(name).casefold().startswith("_pyi_")
        for name in process_calls[0]["env"]
    )


def test_external_launch_environment_drops_pyinstaller_bootloader_variables(monkeypatch) -> None:
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "C:/hud.exe")
    monkeypatch.setenv("_pyi_parent_process_level", "1")
    monkeypatch.setenv("HUD_KEEP_ENV", "yes")

    environment = launcher._launch_environment(platform_name="windows")

    assert "_PYI_ARCHIVE_FILE" not in environment
    assert "_pyi_parent_process_level" not in environment
    assert environment["HUD_KEEP_ENV"] == "yes"


def test_launch_codex_cli_cancel_gate_prevents_popen(tmp_path: Path, monkeypatch) -> None:
    process_calls: list[object] = []
    monkeypatch.setattr(
        launcher,
        "discover_terminals",
        lambda **_: [
            {
                "id": "powershell7",
                "label": "PowerShell 7",
                "executable": "C:/pwsh.exe",
                "shell": "powershell",
                "kind": "shell",
                "shellExecutable": "C:/pwsh.exe",
            }
        ],
    )
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda *args, **kwargs: process_calls.append((args, kwargs)),
    )

    result = launcher.launch_codex_cli(
        terminal_id="powershell7",
        command="codex --help",
        workdir=str(tmp_path),
        platform_name="linux",
        cancel_requested=lambda: False,
        commit_spawn=lambda: False,
    )

    assert result["cancelled"] is True
    assert process_calls == []


def test_launch_codex_cli_refreshes_missing_windows_environment(
    tmp_path: Path, monkeypatch
) -> None:
    process_calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 654

    def fake_popen(args, **kwargs):
        process_calls.append({"args": args, **kwargs})
        return FakeProcess()

    monkeypatch.setenv("HUD_EXISTING_ENV", "process-value")
    monkeypatch.setattr(
        launcher,
        "_windows_registry_environment",
        lambda: {
            "HUD_EXISTING_ENV": "registry-value",
            "HUD_FRESH_ENV": "fresh-value",
            "_PYI_ARCHIVE_FILE": "registry-hud.exe",
        },
    )
    monkeypatch.setattr(
        launcher,
        "discover_terminals",
        lambda **_: [
            {
                "id": "powershell7",
                "label": "PowerShell 7",
                "executable": "C:/pwsh.exe",
                "shell": "powershell",
                "kind": "shell",
                "shellExecutable": "C:/pwsh.exe",
            }
        ],
    )
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

    launcher.launch_codex_cli(
        terminal_id="powershell7",
        command="codex --help",
        workdir=str(tmp_path),
        platform_name="windows",
    )

    environment = process_calls[0]["env"]
    assert environment["HUD_FRESH_ENV"] == "fresh-value"
    assert environment["HUD_EXISTING_ENV"] == "process-value"
    assert "_PYI_ARCHIVE_FILE" not in environment


def test_launch_environment_merges_fresh_windows_path_entries(monkeypatch) -> None:
    monkeypatch.setenv("PATH", r"C:\old-node;C:\shared")
    monkeypatch.setattr(
        launcher,
        "_windows_registry_environment",
        lambda: {"Path": r"C:\shared;C:\new-node"},
    )

    environment = launcher._launch_environment(platform_name="windows")

    assert environment["PATH"].split(";")[:3] == [
        r"C:\old-node",
        r"C:\shared",
        r"C:\new-node",
    ]


def test_launch_codex_cli_uses_new_windows_terminal_tab_when_host_is_open(
    tmp_path: Path, monkeypatch
) -> None:
    process_calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 987

    def fake_popen(args, **kwargs):
        process_calls.append({"args": args, **kwargs})
        return FakeProcess()

    monkeypatch.setattr(
        launcher,
        "discover_terminals",
        lambda **_: [
            {
                "id": "windows-terminal",
                "label": "Windows Terminal",
                "executable": "C:/WindowsApps/wt.exe",
                "shell": "powershell",
                "kind": "windows_terminal",
                "shellExecutable": "C:/pwsh.exe",
            }
        ],
    )
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout='"WindowsTerminal.exe","321","Console","1","1,024 K"',
        ),
    )
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()

    result = launcher.launch_codex_cli(
        terminal_id="windows-terminal",
        command="codex --help",
        workdir=str(tmp_path),
        codex_home=codex_home,
        platform_name="windows",
    )

    assert process_calls[0]["args"] == [
        "C:/WindowsApps/wt.exe",
        "-w",
        "0",
        "new-tab",
        "-d",
        str(tmp_path),
        "C:/pwsh.exe",
        "-NoLogo",
        "-NoExit",
        "-Command",
        f"$env:CODEX_HOME = {launcher._shell_quote(str(codex_home), 'powershell')}\ncodex --help",
    ]
    assert process_calls[0]["env"]["CODEX_HOME"] == str(codex_home)
    assert result["openedAsTab"] is True
    assert result["launchMode"] == "new-tab"


def test_windows_terminal_host_wraps_selected_shell_in_new_tab(
    tmp_path: Path, monkeypatch
) -> None:
    process_calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 988

    def fake_popen(args, **kwargs):
        process_calls.append({"args": args, **kwargs})
        return FakeProcess()

    monkeypatch.setattr(
        launcher,
        "discover_terminals",
        lambda **_: [
            {
                "id": "powershell7",
                "label": "PowerShell 7",
                "executable": "C:/pwsh.exe",
                "shell": "powershell",
                "kind": "shell",
                "shellExecutable": "C:/pwsh.exe",
            },
            {
                "id": "windows-terminal",
                "label": "Windows Terminal",
                "executable": "C:/WindowsApps/wt.exe",
                "shell": "powershell",
                "kind": "windows_terminal",
                "shellExecutable": "C:/pwsh.exe",
            },
        ],
    )
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="WindowsTerminal"),
    )
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

    result = launcher.launch_codex_cli(
        terminal_id="powershell7",
        command="codex --help",
        workdir=str(tmp_path),
        platform_name="windows",
    )

    assert process_calls[0]["args"][0:5] == [
        "C:/WindowsApps/wt.exe",
        "-w",
        "0",
        "new-tab",
        "-d",
    ]
    assert process_calls[0]["args"][5] == str(tmp_path)
    assert process_calls[0]["args"][6] == "C:/pwsh.exe"
    assert result["terminalId"] == "powershell7"
    assert result["openedAsTab"] is True


def test_windows_terminal_launch_sets_provider_workdir_title(
    tmp_path: Path, monkeypatch
) -> None:
    process_calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 989

    def fake_popen(args, **kwargs):
        process_calls.append({"args": args, **kwargs})
        return FakeProcess()

    monkeypatch.setattr(
        launcher,
        "discover_terminals",
        lambda **_: [
            {
                "id": "windows-terminal",
                "label": "Windows Terminal",
                "executable": "C:/WindowsApps/wt.exe",
                "shell": "powershell",
                "kind": "windows_terminal",
                "shellExecutable": "C:/pwsh.exe",
            }
        ],
    )
    monkeypatch.setattr(launcher, "is_terminal_open", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

    result = launcher.launch_codex_cli(
        terminal_id="windows-terminal",
        provider="cunai",
        command="codex --help",
        workdir=str(tmp_path),
        platform_name="windows",
    )

    args = process_calls[0]["args"]
    assert args[1:4] == ["--title", f"[cunai] {tmp_path.name}", "--suppressApplicationTitle"]
    assert f"[cunai] {tmp_path.name}" in args[-1]
    assert result["title"] == f"[cunai] {tmp_path.name}"


def test_macos_terminal_tab_command_targets_front_window() -> None:
    command = launcher._terminal_process_command(
        {
            "executable": "/usr/bin/osascript",
            "shell": "zsh",
            "kind": "terminal_app",
            "shellExecutable": "/bin/zsh",
        },
        "cd -- '/tmp/project'\ncodex --help",
        "/tmp/project",
        open_as_tab=True,
    )

    assert command[0:2] == ["/usr/bin/osascript", "-e"]
    assert "do script" in command[2]
    assert "in front window" in command[2]


def test_runtime_command_exposes_cli_discovery_payload() -> None:
    config = UserConfig.defaults()
    ports = GeneralCommandPorts(
        load_config=lambda: config,
        save_config=lambda _value: None,
        fetch_prices=lambda _url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda _info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        codex_cli_discover=lambda _command: {"terminals": [], "workdirs": []},
    )

    status = dispatch_command(
        {"action": "codexCliDiscover", "provider": "custom", "requestId": "cli-1"},
        RuntimeCommandPorts(),
        ports,
    )

    assert status["action"] == "codexCliDiscover"
    assert status["requestId"] == "cli-1"
    assert status["codexCli"]["terminals"] == []
