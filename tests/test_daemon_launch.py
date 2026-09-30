from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codex_usage_hud import daemon_launch as launch


@pytest.fixture
def windows_launcher(monkeypatch):
    monkeypatch.setattr(launch.sys, "platform", "win32")
    broker = MagicMock(return_value=123)
    monkeypatch.setattr(launch, "_broker_launch", broker)
    return broker


@pytest.mark.parametrize("flags", [0, 0x800])
def test_normal_launch_keeps_foreground_contract(monkeypatch, windows_launcher, flags):
    monkeypatch.setattr(launch, "current_job_limit_flags", lambda: flags)
    assert launch.detach_daemon_if_needed([]) is None
    windows_launcher.assert_not_called()


def test_kill_on_close_job_uses_desktop_broker_and_preserves_arguments(monkeypatch, windows_launcher):
    monkeypatch.setattr(launch, "current_job_limit_flags", lambda: 0x2800)
    assert launch.detach_daemon_if_needed(["--session-file", "C:/space here/test.jsonl"]) == 0
    windows_launcher.assert_called_once_with(["--session-file", "C:/space here/test.jsonl"])


def test_failed_broker_does_not_fall_back_inside_job(monkeypatch, windows_launcher, capsys):
    monkeypatch.setattr(launch, "current_job_limit_flags", lambda: 0x2000)
    windows_launcher.side_effect = OSError("Access denied")
    assert launch.detach_daemon_if_needed([]) == 2
    assert "external terminal" in capsys.readouterr().err
    assert windows_launcher.call_count == 1


def test_macos_keeps_existing_lifecycle(monkeypatch):
    monkeypatch.setattr(launch.sys, "platform", "darwin")
    assert launch.detach_daemon_if_needed([]) is None


def test_broker_child_restores_context_before_daemon(monkeypatch, tmp_path):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.recv.return_value = {
        "environment": {"HUD_TEST_HANDOFF": "preserved"}, "cwd": str(tmp_path), "argv": ["--daemon"],
    }
    monkeypatch.setattr(launch, "Client", MagicMock(return_value=connection))
    monkeypatch.setattr(launch, "current_job_limit_flags", lambda: 0x800)
    change_dir = MagicMock()
    monkeypatch.setattr(launch.os, "chdir", change_dir)
    monkeypatch.setenv("HUD_TEST_HANDOFF", "original")
    monkeypatch.setattr(launch.sys, "argv", ["hud", launch.BROKER_OPTION, "pipe", "token"])
    run = MagicMock(side_effect=lambda args: 7 if launch.os.environ["HUD_TEST_HANDOFF"] == "preserved" else 99)
    assert launch.resume_brokered_daemon("pipe", "ab", run) == 7
    run.assert_called_once_with(["--daemon"])
    change_dir.assert_called_once_with(str(tmp_path))
    assert launch.sys.argv == ["hud", "--daemon"]
    assert connection.send.call_args.args[0]["jobFlags"] == 0x800


def test_broker_child_rejects_remaining_kill_job(monkeypatch):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    monkeypatch.setattr(launch, "Client", MagicMock(return_value=connection))
    monkeypatch.setattr(launch, "current_job_limit_flags", lambda: 0x2000)
    run = MagicMock()
    assert launch.resume_brokered_daemon("pipe", "ab", run) == 2
    run.assert_not_called()
    assert "kill-on-close" in connection.send.call_args.args[0]["error"]


@pytest.mark.parametrize("reply, succeeds", [
    ({"pid": 123, "jobFlags": 0}, True),
    ({"pid": 123, "jobFlags": 0x2000}, False),
    ({"error": "desktop unavailable"}, False),
    ({"pid": 0, "jobFlags": 0}, False),
])
def test_launcher_requires_verified_child_ack(monkeypatch, reply, succeeds):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.recv.return_value = reply
    listener = MagicMock()
    listener.accept.return_value = connection
    monkeypatch.setattr(launch, "Listener", MagicMock(return_value=listener))
    shell = MagicMock()
    monkeypatch.setattr(launch, "_shell_launch", shell)
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    if succeeds:
        assert launch._broker_launch(["--daemon"]) == 123
        payload = connection.send.call_args.args[0]
        assert payload["argv"] == ["--daemon"]
        assert "_PYI_PARENT_PROCESS_LEVEL" not in payload["environment"]
    else:
        with pytest.raises(RuntimeError):
            launch._broker_launch(["--daemon"])
    listener.close.assert_called_once()
    shell.assert_called_once()


@pytest.mark.parametrize("frozen", [False, True])
def test_shell_uses_explorer_desktop_object_and_quotes_arguments(monkeypatch, frozen):
    monkeypatch.setattr(launch.sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(launch.sys, "executable", "C:/Program Files/O'Brien/Hud.exe")
    monkeypatch.setattr(launch.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    runner = MagicMock()
    monkeypatch.setattr(launch.subprocess, "run", runner)
    launch._shell_launch([launch.BROKER_OPTION, "pipe", "token"])
    script = runner.call_args.args[0][-1]
    assert "FindWindowSW" in script and "$desktop.Document.Application" in script
    assert "O''Brien" in script
    assert ("-m codex_usage_hud" in script) is not frozen
