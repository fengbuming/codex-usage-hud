from types import SimpleNamespace
from unittest.mock import MagicMock
import json


from codex_usage_hud.platforms.cli_session_switch import CliSessionNavigator, rollout_identity
from codex_usage_hud.platforms.windows_cli import CliProcess
from codex_usage_hud.platforms.session_switch import SessionSwitchResult
from codex_usage_hud import overlay_commands, cli_launch_registry
from codex_usage_hud import cli_app

SESSION = "10000000-0000-4000-8000-000000000abc"
OTHER = "20000000-0000-4000-8000-000000000def"
CWD = r"E:\Project\same-directory"


def cli(pid=1, parent=10, generation=100, launch_id=""):
    return CliProcess(pid, parent, r"C:\npm\codex.exe", generation, "codex resume " + SESSION, launch_id)


def fixture_api(roots=None, paths=None):
    roots = roots if roots is not None else [cli()]
    rows = [*roots, CliProcess(10, 20, r"C:\pwsh.exe", 101),
            CliProcess(20, 0, r"C:\WindowsTerminal.exe", 102)]
    return SimpleNamespace(
        processes=MagicMock(return_value=rows),
        open_rollouts=MagicMock(return_value=paths if paths is not None else {1: {"owned"}}),
        windows=MagicMock(return_value=[200]),
        focus_terminal=MagicMock(return_value=(True, "switched")),
        restore=MagicMock(return_value=True),
    )


def navigator(api, *, identity=None, probe=None, lookup=None):
    return CliSessionNavigator(api=api, identity=identity or (lambda _: (SESSION, CWD)),
                               console_probe=probe or (lambda _: {"title": "unique CLI"}),
                               launch_lookup=lookup or (lambda _: {"title": "registered CLI"}))


def test_same_workdir_uses_session_ownership_not_title_or_directory():
    api = fixture_api([cli(1), cli(2)], {1: {"other"}, 2: {"wanted"}})
    n = navigator(api, identity=lambda path: (SESSION if path == "wanted" else OTHER, CWD))
    assert n.activate(SESSION, CWD).ok
    api.focus_terminal.assert_called_once_with([200], "unique CLI")


def test_duplicate_running_owners_do_not_pick_a_terminal():
    api = fixture_api([cli(1), cli(2)], {1: {"wanted"}, 2: {"wanted"}})
    result = navigator(api).activate(SESSION)
    assert not result.ok and result.status == "cli-session-ambiguous"
    api.focus_terminal.assert_not_called()


def test_closed_cli_does_not_spawn_or_activate_another_cli():
    api = fixture_api([], {})
    result = navigator(api).activate(SESSION)
    assert result.status == "cli-session-not-bound"
    assert "恢复" in result.message
    api.restore.assert_not_called()
    api.focus_terminal.assert_not_called()


def test_pid_reuse_between_resolution_and_activation_is_rejected():
    api = fixture_api()
    rows = api.processes.return_value
    api.processes.side_effect = [rows, [cli(generation=200), *rows[1:]]]
    assert navigator(api).activate(SESSION).status == "cli-session-exited"
    api.focus_terminal.assert_not_called()


def test_hud_launch_uses_registered_unique_title_without_console_attachment():
    api = fixture_api([cli(launch_id="launch-1")])
    probe = MagicMock()
    assert navigator(api, probe=probe).activate(SESSION).ok
    api.focus_terminal.assert_called_once_with([200], "registered CLI")
    probe.assert_not_called()


def test_repeated_tab_titles_remain_a_failure():
    api = fixture_api()
    api.focus_terminal.return_value = False, "terminal-ambiguous"
    result = navigator(api).activate(SESSION)
    assert not result.ok and result.status == "terminal-ambiguous"


def test_resumed_cli_can_change_workdir_without_losing_session_identity():
    api = fixture_api()
    n = navigator(api, identity=lambda _: (SESSION, "\\\\?\\" + CWD))
    assert n.activate(SESSION, r"E:\Project\different-resume-directory").ok


def test_desktop_app_server_is_not_a_cli_owner():
    process = CliProcess(1, 10, r"C:\OpenAI\Codex\bin\codex.exe", 100, "codex app-server")
    api = fixture_api([process])
    assert not navigator(api).activate(SESSION).ok
    api.focus_terminal.assert_not_called()


def test_cli_child_app_server_rollout_ownership_is_supported():
    api = fixture_api(paths={2: {"owned"}})
    api.processes.return_value.append(CliProcess(2, 1, r"C:\npm\codex.exe", 100, "codex app-server"))
    assert navigator(api).activate(SESSION).ok


def test_shared_daemon_rollout_is_not_assigned_to_its_first_cli_client():
    api = fixture_api(paths={2: {"owned"}})
    api.processes.return_value.append(CliProcess(2, 1, r"C:\npm\codex.exe", 100,
                                                 "codex app-server --managed-daemon"))
    result = navigator(api).activate(SESSION)
    assert result.status == "cli-session-not-bound"
    api.focus_terminal.assert_not_called()


def test_standalone_console_restores_verified_console_hwnd():
    api = fixture_api([cli(parent=0)])
    n = navigator(api, probe=lambda _: {"hwnd": 300, "classic": True})
    assert n.activate(SESSION).ok
    api.restore.assert_called_once_with(300)
    api.focus_terminal.assert_not_called()


def test_pseudoconsole_or_vscode_handle_is_never_shown_as_a_window():
    api = fixture_api([cli(parent=0)])
    n = navigator(api, probe=lambda _: {"hwnd": 300, "classic": False})
    assert not n.activate(SESSION).ok
    api.restore.assert_not_called()


def test_vscode_does_not_use_inherited_windows_terminal_identity():
    root = CliProcess(1, 10, r"C:\npm\codex.exe", 100, "codex resume " + SESSION,
                      "", "inherited-wt-session")
    api = fixture_api([root])
    api.processes.return_value[1] = CliProcess(10, 20, r"C:\VSCode\Code.exe", 101)
    assert navigator(api).activate(SESSION).status == "cli-terminal-unsupported"
    api.focus_terminal.assert_not_called()


def test_cli_bubble_dispatch_never_calls_desktop_navigation(monkeypatch):
    result = SessionSwitchResult(ok=True, status="switched", backend="cli-terminal")
    activate = MagicMock(return_value=result)
    monkeypatch.setattr(CliSessionNavigator, "activate", activate)
    controller, prepare, refocus = MagicMock(), MagicMock(), MagicMock()
    assert overlay_commands.handle_command(
        {"action": "activateSession", "clientKind": "cli", "sessionId": SESSION, "workdir": CWD},
        controller, prepare_window_callback=prepare, refocus_window_callback=refocus) is result
    controller.activate_session.assert_not_called()
    prepare.assert_not_called()
    refocus.assert_not_called()


def test_legacy_cli_client_kind_alias_never_calls_desktop_navigation(monkeypatch):
    result = SessionSwitchResult(ok=True, status="switched", backend="cli-terminal")
    activate = MagicMock(return_value=result)
    monkeypatch.setattr(CliSessionNavigator, "activate", activate)
    controller, prepare, refocus = MagicMock(), MagicMock(), MagicMock()
    assert overlay_commands.handle_command(
        {"action": "activateSession", "clientKind": "codex-cli", "sessionId": SESSION, "workdir": CWD},
        controller, prepare_window_callback=prepare, refocus_window_callback=refocus) is result
    controller.activate_session.assert_not_called()
    prepare.assert_not_called()
    refocus.assert_not_called()


def test_cli_source_fallback_never_calls_desktop_navigation(monkeypatch):
    result = SessionSwitchResult(ok=True, status="switched", backend="cli-terminal")
    activate = MagicMock(return_value=result)
    monkeypatch.setattr(CliSessionNavigator, "activate", activate)
    controller, prepare, refocus = MagicMock(), MagicMock(), MagicMock()
    assert overlay_commands.handle_command(
        {"action": "activateSession", "clientKind": "unknown", "source": "cli",
         "sessionId": SESSION, "workdir": CWD},
        controller, prepare_window_callback=prepare, refocus_window_callback=refocus) is result
    controller.activate_session.assert_not_called()


def test_cli_failure_is_acknowledged_and_visible_without_a_success_mark(monkeypatch):
    monkeypatch.setattr(CliSessionNavigator, "activate", lambda *_: SessionSwitchResult(
        ok=False, status="cli-session-not-bound", backend="cli-terminal", message="CLI 已退出"))
    overlay = SimpleNamespace(take_commands=lambda: [{"action": "activateSession", "clientKind": "cli", "sessionId": SESSION}],
                              acknowledge_command=MagicMock(), mark_switch_completed=MagicMock(), show_system_notice=MagicMock())
    assert overlay_commands.handle_commands(overlay, MagicMock()) == 1
    overlay.mark_switch_completed.assert_not_called()
    overlay.show_system_notice.assert_called_once_with(title="CLI 会话跳转", message="CLI 已退出", dismissible=True, related_request_id="")
    assert overlay.acknowledge_command.call_args.kwargs["status"] == "rejected"
    assert overlay.acknowledge_command.call_args.kwargs["result"]["message"] == "CLI 已退出"


def test_rollout_metadata_is_required_even_if_filename_matches(tmp_path):
    path = tmp_path / f"rollout-{SESSION}.jsonl"
    path.write_text(json.dumps({"type": "event_msg", "payload": {"id": SESSION}}), encoding="utf-8")
    assert rollout_identity(str(path)) == ("", "")
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": SESSION, "cwd": CWD}}), encoding="utf-8")
    assert rollout_identity(str(path)) == (SESSION, CWD)


def test_unique_launch_registration_and_invalid_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_launch_registry, "hud_runtime_dir", lambda: tmp_path)
    first = cli_launch_registry.register_cli_launch("Codex CLI", "windows_terminal", CWD)
    second = cli_launch_registry.register_cli_launch("Codex CLI", "windows_terminal", CWD)
    assert first["title"] != second["title"]
    assert cli_launch_registry.cli_launch_for_id(first["launchId"]) == first
    assert cli_launch_registry.cli_launch_for_id("../../other") is None
    cli_launch_registry.remove_cli_launch(first["launchId"])
    assert cli_launch_registry.cli_launch_for_id(first["launchId"]) is None


def test_windowed_console_probe_writes_reply_without_stdout_or_daemon_cleanup(tmp_path, monkeypatch):
    import sys
    from codex_usage_hud.platforms import windows_cli
    monkeypatch.setattr(windows_cli, "WindowsCliApi", lambda: SimpleNamespace(
        console_info=lambda pid: {"hwnd": pid, "classic": True}))
    services = SimpleNamespace(parser_factory=None, cleanup_overlay=MagicMock(), run_daemon=MagicMock())
    output = tmp_path / "probe.json"
    monkeypatch.setattr(sys, "stdout", None)
    assert cli_app.main(["--cli-console-probe", "321", "--cli-console-probe-output", str(output)],
                        services=services) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {"hwnd": 321, "classic": True}
    services.cleanup_overlay.assert_not_called()
    services.run_daemon.assert_not_called()
