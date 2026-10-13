"""Unit tests for daemon process matching and state transitions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.daemon import (
    CodexDaemonManager,
    DaemonState,
    MacOSProcessListener,
    ProcessListenerError,
    ProcessSnapshot,
    WindowsProcessListener,
    current_process_listener,
    is_codex_client_process,
)


class _FakeListener:
    def __init__(self, snapshots: list[ProcessSnapshot]) -> None:
        self.snapshots = list(snapshots)
        self.calls = 0

    def snapshot(self) -> ProcessSnapshot:
        self.calls += 1
        if not self.snapshots:
            return ProcessSnapshot(found=False)
        if len(self.snapshots) == 1:
            return self.snapshots[0]
        return self.snapshots.pop(0)


class _FailingListener:
    def snapshot(self) -> ProcessSnapshot:
        raise RuntimeError("process api denied")


class _FakeExitMonitor:
    def __init__(self, states: list[bool | None]) -> None:
        self.states = list(states)
        self.calls = 0
        self.closed = False

    def is_running(self) -> bool | None:
        self.calls += 1
        if not self.states:
            return None
        if len(self.states) == 1:
            return self.states[0]
        return self.states.pop(0)

    def close(self) -> None:
        self.closed = True


class DaemonProcessMatchingTests(unittest.TestCase):
    def test_chatgpt_desktop_process_names_are_detected(self) -> None:
        # Codex Desktop 26.707+ uses this name for the Electron process family.
        self.assertTrue(is_codex_client_process("ChatGPT.exe"))
        self.assertTrue(is_codex_client_process("chatgpt.exe"))

    def test_cli_and_unrelated_processes_are_not_detected_by_name(self) -> None:
        self.assertFalse(is_codex_client_process("Codex.exe"))
        self.assertFalse(is_codex_client_process("codex-client.exe"))
        self.assertFalse(is_codex_client_process("OpenAI Codex.exe"))
        self.assertFalse(is_codex_client_process("codex-hud.exe"))
        self.assertFalse(is_codex_client_process("codex_usage_hud.exe"))
        self.assertFalse(is_codex_client_process("codex-plus-plus.exe"))
        self.assertFalse(is_codex_client_process("codex-plus-plus-manager.exe"))
        self.assertFalse(is_codex_client_process("codex-computer-use.exe"))
        self.assertFalse(is_codex_client_process("python.exe"))
        self.assertFalse(is_codex_client_process(""))

    def test_desktop_resource_codex_is_detected_by_verified_path(self) -> None:
        self.assertTrue(
            is_codex_client_process(
                "codex.exe",
                r"C:\Program Files\WindowsApps\OpenAI.Codex_26.707.8479.0_x64__2p2nqsd0c76g0\app\resources\codex.exe",
            )
        )
        self.assertTrue(
            is_codex_client_process(
                "Codex.exe",
                r"C:\Users\person\AppData\Local\Programs\Codex\Codex.exe",
            )
        )
        self.assertTrue(
            is_codex_client_process(
                "codex.exe",
                r"C:\Users\person\AppData\Local\Programs\CodexRelocated\app\resources\codex.exe",
            )
        )

    def test_npm_cli_codex_is_rejected_by_path(self) -> None:
        self.assertFalse(
            is_codex_client_process(
                "codex.exe",
                r"C:\Users\person\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe",
            )
        )

    def test_macos_listener_keeps_only_codex_app_processes(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout=(
                "11 /usr/local/bin/codex --remote-debugging-port=59999\n"
                "22 /Applications/Codex.app/Contents/MacOS/Codex\n"
                "33 /Applications/OpenAI Codex.app/Contents/MacOS/Codex "
                "--remote-debugging-port=59629\n"
            ),
            stderr="",
        )

        with (
            patch.object(sys, "platform", "darwin"),
            patch("codex_usage_hud.daemon.subprocess.run", return_value=completed),
        ):
            snapshot = MacOSProcessListener(exclude_pid=999).snapshot()

        self.assertTrue(snapshot.found)
        self.assertEqual(snapshot.pids, (22, 33))
        self.assertEqual(snapshot.names, ("Codex", "Codex"))

    def test_current_listener_selects_macos_snapshot_fallback(self) -> None:
        with (
            patch.object(sys, "platform", "darwin"),
            patch("codex_usage_hud.daemon._logger.info") as log_info,
        ):
            listener = current_process_listener()

        self.assertIsInstance(listener, MacOSProcessListener)
        log_info.assert_called_once_with(
            "daemon_process_listener_selected platform=macos mode=ps-snapshot-fallback"
        )


class DaemonStateMachineTests(unittest.TestCase):
    def test_wait_for_codex_moves_to_running_when_process_appears(self) -> None:
        listener = _FakeListener(
            [
                ProcessSnapshot(found=False),
                ProcessSnapshot(found=True, pids=(123,), names=("Codex.exe",)),
            ]
        )
        manager = CodexDaemonManager(listener=listener, poll_ms=1)

        snapshot = manager.wait_for_codex()

        self.assertTrue(snapshot.found)
        self.assertEqual(snapshot.primary_pid, 123)
        self.assertEqual(manager.state, DaemonState.HUD_RUNNING)
        self.assertGreaterEqual(listener.calls, 2)

    def test_listener_failure_moves_to_fallback(self) -> None:
        manager = CodexDaemonManager(listener=_FailingListener(), poll_ms=1)

        with self.assertRaises(ProcessListenerError):
            manager.snapshot()

        self.assertEqual(manager.state, DaemonState.FALLBACK)

    def test_missing_process_moves_to_exiting_after_hud_started(self) -> None:
        manager = CodexDaemonManager(
            listener=_FakeListener([ProcessSnapshot(found=False)]),
            poll_ms=1,
        )

        self.assertFalse(manager.codex_is_running())

        self.assertEqual(manager.state, DaemonState.EXITING)

    def test_codex_is_running_prefers_exit_monitor_over_full_snapshot(self) -> None:
        listener = _FakeListener([ProcessSnapshot(found=True, pids=(123,))])
        monitor = _FakeExitMonitor([True])
        manager = CodexDaemonManager(
            listener=listener,
            poll_ms=1,
            exit_monitor_factory=lambda snapshot: monitor,
        )
        manager.snapshot()
        calls_after_initial_snapshot = listener.calls

        self.assertTrue(manager.codex_is_running())

        self.assertEqual(listener.calls, calls_after_initial_snapshot)
        self.assertEqual(monitor.calls, 1)

    def test_codex_is_running_rescans_after_exit_monitor_reports_exit(self) -> None:
        listener = _FakeListener(
            [
                ProcessSnapshot(found=True, pids=(123,)),
                ProcessSnapshot(found=False),
            ]
        )
        monitor = _FakeExitMonitor([False])
        manager = CodexDaemonManager(
            listener=listener,
            poll_ms=1,
            exit_monitor_factory=lambda snapshot: monitor,
        )
        manager.snapshot()

        self.assertFalse(manager.codex_is_running())

        self.assertTrue(monitor.closed)
        self.assertEqual(manager.state, DaemonState.EXITING)

    def test_codex_replacement_is_reported_as_exit_for_renderer_restart(self) -> None:
        listener = _FakeListener(
            [
                ProcessSnapshot(found=True, pids=(123,)),
                ProcessSnapshot(found=True, pids=(456,)),
            ]
        )
        monitor = _FakeExitMonitor([False])
        manager = CodexDaemonManager(
            listener=listener,
            poll_ms=1,
            exit_monitor_factory=lambda snapshot: monitor,
        )
        manager.snapshot()

        self.assertFalse(manager.codex_is_running())
        self.assertEqual(manager.last_snapshot.primary_pid, 456)
        self.assertEqual(manager.state, DaemonState.EXITING)

    def test_wait_for_codex_replacement_waits_for_old_pid_to_disappear(self) -> None:
        listener = _FakeListener(
            [
                ProcessSnapshot(found=True, pids=(123,)),
                ProcessSnapshot(found=True, pids=(123,)),
                ProcessSnapshot(found=True, pids=(456,)),
            ]
        )
        manager = CodexDaemonManager(listener=listener, poll_ms=1)
        manager.snapshot()

        self.assertTrue(
            manager.wait_for_codex_replacement(123, timeout_seconds=1.0)
        )
        self.assertEqual(manager.last_snapshot.primary_pid, 456)
        self.assertEqual(manager.state, DaemonState.HUD_RUNNING)

    def test_signaled_exit_handle_detects_replacement_even_when_pid_is_reused(self) -> None:
        listener = _FakeListener([
            ProcessSnapshot(found=True, pids=(123,)),
            ProcessSnapshot(found=True, pids=(123,)),
        ])
        monitor = _FakeExitMonitor([False])
        manager = CodexDaemonManager(
            listener=listener, exit_monitor_factory=lambda _snapshot: monitor,
        )
        manager.snapshot()

        self.assertFalse(manager.codex_is_running())
        self.assertTrue(monitor.closed)
        self.assertEqual(manager.state, DaemonState.EXITING)


def test_windows_listener_tracks_main_across_residual_helpers(monkeypatch) -> None:
    from codex_usage_hud import codex_app_runtime as app

    rows = [(11, 22), (22, 99)]  # Toolhelp can enumerate the helper first.
    cursor = [0]

    def write_entry(pointer) -> bool:
        if cursor[0] >= len(rows):
            return False
        entry = pointer._obj
        entry.th32ProcessID, entry.th32ParentProcessID = rows[cursor[0]]
        entry.szExeFile = "ChatGPT.exe"
        return True

    def first(_handle, pointer) -> bool:
        cursor[0] = 0
        return write_entry(pointer)

    def next_entry(_handle, pointer) -> bool:
        cursor[0] += 1
        return write_entry(pointer)

    listener = WindowsProcessListener.__new__(WindowsProcessListener)
    listener.enabled = True
    listener.error = ""
    listener.exclude_pid = 999
    listener._main_process_cache = {}
    listener.kernel32 = SimpleNamespace(
        CreateToolhelp32Snapshot=lambda *_args: 1,
        Process32FirstW=first, Process32NextW=next_entry,
        CloseHandle=MagicMock(),
    )
    main = app.CodexDesktopProcess(22, "ChatGPT.exe", "C:/Codex/ChatGPT.exe", "ChatGPT.exe")
    helper = app.CodexDesktopProcess(11, "ChatGPT.exe", "C:/Codex/ChatGPT.exe",
                                  "ChatGPT.exe --type=renderer --remote-debugging-port=59629")
    new_main = app.CodexDesktopProcess(33, "ChatGPT.exe", "C:/Codex/ChatGPT.exe", "ChatGPT.exe")
    audit = MagicMock(return_value=[helper, main])
    monkeypatch.setattr(app, "audited_running_codex_desktop_processes", audit)

    assert listener.snapshot().pids == (22,)
    assert listener.snapshot().pids == (22,)
    assert audit.call_count == 1  # No repeat CIM query for an unchanged root.

    rows[:] = [(11, 22)]
    audit.return_value = [helper]
    assert not listener.snapshot().found  # Orphan cannot keep the App alive.

    rows[:] = [(11, 22), (33, 99)]
    audit.return_value = [helper, new_main]
    assert listener.snapshot().pids == (33,)
    assert audit.call_count == 3
    assert listener.kernel32.CloseHandle.call_count == 4


if __name__ == "__main__":
    unittest.main()
