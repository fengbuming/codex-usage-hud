"""Locate a running CLI by its rollout ownership, then activate its terminal."""

from __future__ import annotations

from collections.abc import Callable
import json
import ntpath
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

from ..cli_launch_registry import cli_launch_for_id
from ..codex_app_runtime import is_codex_client_process
from ..process_environment import external_process_environment
from .session_switch import SessionSwitchResult
from .windows_cli import CliProcess, WindowsCliApi


def _canonical_id(value: object) -> str:
    try:
        text = str(value or "").strip().lower()
        return text if str(uuid.UUID(text)) == text else ""
    except ValueError:
        return ""


def _is_interactive_cli(process: CliProcess) -> bool:
    return (bool(process.command_line) and ntpath.basename(process.executable).casefold() == "codex.exe"
            and not is_codex_client_process("codex.exe", process.executable)
            and not re.search(r'(?:^|[\s"])(?:app-server|exec|review|mcp)(?:[\s"]|$)', process.command_line))


def rollout_identity(path: str) -> tuple[str, str]:
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            first = stream.readline(256 * 1024)
        data = json.loads(first)
        meta = data.get("payload", {})
        if data.get("type") != "session_meta":
            return "", ""
        return _canonical_id(meta.get("id")), str(meta.get("cwd") or "")
    except (OSError, ValueError, TypeError, AttributeError):
        return "", ""


def _console_probe(pid: int) -> dict[str, object]:
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        command = [str(executable), "--cli-console-probe", str(pid)]
    else:
        if executable.name.casefold() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        command = [str(executable), "-m", "codex_usage_hud", "--cli-console-probe", str(pid)]
    # Windowed PyInstaller executables have no sys.stdout. A temporary reply
    # file works for both packaged HUDs and source/pip installations.
    with tempfile.NamedTemporaryFile(prefix="codex-hud-cli-probe-", suffix=".json", delete=False) as stream:
        output = Path(stream.name)
    command.extend(["--cli-console-probe-output", str(output)])
    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                env=external_process_environment(), check=False)
        data = json.loads(output.read_text(encoding="utf-8"))
        return data if result.returncode == 0 and isinstance(data, dict) else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    finally:
        output.unlink(missing_ok=True)


class CliSessionNavigator:
    def __init__(self, *, api=None, identity: Callable = rollout_identity,
                 console_probe: Callable = _console_probe,
                 launch_lookup: Callable = cli_launch_for_id) -> None:
        self._api = api
        self._identity = identity
        self._console_probe = console_probe
        self._launch_lookup = launch_lookup

    def activate(self, session_id: str, workdir: str = "") -> SessionSwitchResult:
        wanted = _canonical_id(session_id)
        def outcome(status: str, message: str = "", *, ok: bool = False) -> SessionSwitchResult:
            return SessionSwitchResult(ok=ok, status=status, backend="cli-terminal",
                                       requested_session_id=session_id,
                                       active_session_id=wanted if ok else "",
                                       matched_by="rollout-owner" if ok else "", message=message)
        if not wanted:
            return outcome("missing-target", "CLI 会话标识无效。")
        if self._api is None and not sys.platform.startswith("win"):
            return outcome("cli-terminal-unsupported", "当前平台尚未支持 CLI 终端跳转。")
        try:
            api = self._api or WindowsCliApi()
            processes = api.processes()
            roots = [p for p in processes if _is_interactive_cli(p)]
            index = {p.pid: p for p in processes}
            def ancestors(pid: int) -> list[CliProcess]:
                chain, seen = [], set()
                while pid in index and pid not in seen:
                    seen.add(pid)
                    item = index[pid]
                    chain.append(item)
                    pid = item.parent_pid
                return chain
            owners = {root.pid: {root.pid} | {
                p.pid for p in processes
                if "app-server" in p.command_line and "--managed-daemon" not in p.command_line
                and root.pid in {a.pid for a in ancestors(p.pid)[1:]}
            } for root in roots}
            paths = api.open_rollouts(set().union(*owners.values()) if owners else set())
            matches = []
            for root in roots:
                for path in set().union(*(paths.get(pid, set()) for pid in owners[root.pid])):
                    identity, _ = self._identity(path)
                    if identity == wanted:
                        matches.append(root)
                        break
            if len(matches) > 1:
                return outcome("cli-session-ambiguous", "多个 CLI 正在使用此会话，无法确定目标终端。")
            if not matches:
                return outcome("cli-session-not-bound", "未找到已确认归属的运行中 CLI。若已退出，可在会话管理中选择“在 CLI 中恢复”。")
            root = matches[0]
            # PID reuse or an exit during discovery must never activate a new process.
            if not any(p.pid == root.pid and p.generation == root.generation for p in api.processes()):
                return outcome("cli-session-exited", "CLI 已退出，可在会话管理中恢复此会话。")
            if any(ntpath.basename(p.executable).casefold() == "code.exe" for p in ancestors(root.pid)):
                return outcome("cli-terminal-unsupported", "VS Code 内置终端需要单独适配，暂不支持直接跳转。")
            terminal_pids = {p.pid for p in ancestors(root.pid) if ntpath.basename(p.executable).casefold() == "windowsterminal.exe"}
            record = self._launch_lookup(root.launch_id) if root.launch_id else None
            info = {}
            if not terminal_pids:
                info = self._console_probe(root.pid)
                terminal_pids = {p.pid for p in ancestors(int(info.get("ownerPid") or 0))
                                 if ntpath.basename(p.executable).casefold() == "windowsterminal.exe"}
                # Default-terminal delegation can leave the shell parent gone
                # and the console HWND owned by the shell, not the WT broker.
                # Search actual WT windows using the verified CLI's live title;
                # focus_terminal rejects missing or duplicate tabs before focus.
                if not terminal_pids and info.get("hwnd") and not info.get("classic"):
                    terminal_pids = {p.pid for p in processes
                                     if ntpath.basename(p.executable).casefold() == "windowsterminal.exe"}
            if terminal_pids:
                if not record and not info:
                    info = self._console_probe(root.pid)
                title = str((record or {}).get("title") or info.get("title") or "")
                if not title:
                    return outcome("cli-terminal-unidentified", "无法确认此 CLI 对应的终端标签页。")
                windows = api.windows(terminal_pids)
                ready, status = api.focus_terminal(windows, title)
                # Codex changes the shell title after startup unless WT pins it.
                # Retry only a missing registered title, never an ambiguous tab.
                if not ready and status == "terminal-tab-not-found":
                    if not info:
                        info = self._console_probe(root.pid)
                    live_title = str(info.get("title") or "")
                    if live_title and live_title != title:
                        ready, status = api.focus_terminal(windows, live_title)
                messages = {
                    "terminal-ambiguous": "多个终端标签页使用相同标题，无法确定目标；请保持标签页标题唯一。",
                    "terminal-tab-not-found": "未找到此 CLI 对应的终端标签页，请确认标签页仍在运行。",
                    "terminal-not-foreground": "目标 CLI 终端未能恢复到前台。",
                }
                return outcome(status, "" if ready else messages.get(
                    status, "未能确认目标终端标签页或输入焦点，请稍后重试。"), ok=ready)
            hwnd = int(info.get("hwnd") or 0)
            if not hwnd or not info.get("classic"):
                return outcome("cli-terminal-unidentified", "无法确认此 CLI 对应的终端窗口或标签页。")
            ready = api.restore(hwnd)
            return outcome("switched" if ready else "terminal-not-foreground",
                           "" if ready else "目标 CLI 终端未能恢复到前台。", ok=ready)
        except Exception:
            return outcome("cli-terminal-unavailable", "CLI 终端身份暂时不可读取，请稍后重试。")
