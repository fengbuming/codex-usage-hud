"""Windows and macOS Codex Desktop discovery and lifecycle control."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from typing import TypeAlias

from .codex_cli_launcher import _windows_registry_environment
from .instance_lock import process_exists, terminate_process
from .process_environment import (
    PYINSTALLER_INTERNAL_ENV_PREFIX,
    external_environment_scope,
    external_process_environment,
)
from .platforms.cdp_probe import cdp_port_from_env
from .platforms.windows_activation import activate_packaged_app
from .runtime_paths import (
    CODEX_APP_DEFAULT_ID,
    CODEX_APP_ID_ENV,
    CODEX_APP_PATH_ENV,
    codex_app_executable_candidates as _discover_codex_app_executables,
    codex_app_shell_targets,
)

WindowReadiness: TypeAlias = tuple[bool, str, str, int]
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodexDesktopProcess:
    pid: int
    name: str
    executable_path: str
    command_line: str
    started_at: str = ""


def _windows_process_started_at(value: object) -> str:
    """Normalize a Win32/CIM CreationDate value to an UTC ISO timestamp."""
    text = str(value or "").strip()
    if not text:
        return ""
    epoch_match = re.fullmatch(r"/Date\((?P<millis>-?\d+)(?:[+-]\d+)?\)/", text)
    if epoch_match is not None:
        try:
            value_dt = datetime.fromtimestamp(
                int(epoch_match.group("millis")) / 1000.0,
                tz=timezone.utc,
            )
            return value_dt.isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OverflowError, OSError):
            return ""
    iso_text = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    # Python 3.10 rejects otherwise valid ISO timestamps whose fractional
    # seconds are not exactly three or six digits (CIM providers commonly
    # emit five).  Normalize the fraction before handing it to fromisoformat;
    # the resulting UTC value keeps the same precision as the source.
    iso_match = re.fullmatch(
        r"(?P<prefix>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
        r"\.(?P<fraction>\d+)(?P<offset>[+-]\d{2}:\d{2})?",
        iso_text,
    )
    if iso_match is not None:
        fraction = iso_match.group("fraction")
        iso_text = (
            f"{iso_match.group('prefix')}.{fraction[:6].ljust(6, '0')}"
            f"{iso_match.group('offset') or ''}"
        )
    try:
        iso_value = datetime.fromisoformat(iso_text)
    except ValueError:
        iso_value = None
    if iso_value is not None:
        if iso_value.tzinfo is None:
            iso_value = iso_value.replace(tzinfo=timezone.utc)
        return iso_value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    match = re.fullmatch(
        r"(?P<base>\d{14})(?:\.(?P<micro>\d{1,6}))?(?P<offset>[+-]\d{3})?",
        text,
    )
    if match is None:
        return ""
    try:
        micro = (match.group("micro") or "").ljust(6, "0")
        value_dt = datetime.strptime(match.group("base"), "%Y%m%d%H%M%S")
        if micro:
            value_dt = value_dt.replace(microsecond=int(micro))
        offset = int(match.group("offset") or "0")
        value_dt = value_dt.replace(tzinfo=timezone(timedelta(minutes=offset)))
        return value_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return ""


def _normalized_windows_path(value: str) -> str:
    return str(value or "").strip().replace("/", "\\").rstrip("\\").lower()


def _is_known_codex_desktop_path(executable_path: str) -> bool:
    path = _normalized_windows_path(executable_path)
    if not path or not path.endswith("\\codex.exe"):
        return False
    configured = _normalized_windows_path(os.environ.get(CODEX_APP_PATH_ENV, ""))
    if configured and path == configured:
        return True
    if "\\windowsapps\\openai.codex_" in path and "\\app\\" in path:
        return True
    markers = (
        "\\appdata\\local\\programs\\codexrelocated\\",
        "\\appdata\\local\\programs\\codex\\",
        "\\appdata\\local\\programs\\openai codex\\",
        "\\program files\\codex\\",
        "\\program files\\openai codex\\",
        "\\program files (x86)\\codex\\",
        "\\program files (x86)\\openai codex\\",
    )
    return any(marker in path for marker in markers)


def is_codex_client_process(process_name: str, executable_path: str = "") -> bool:
    """Return whether a process is verified as part of Codex Desktop."""
    name = Path(str(process_name or "")).name.strip().lower()
    if not name:
        return False
    stem = name[:-4] if name.endswith(".exe") else name
    if stem == "chatgpt":
        return True
    if stem == "codex":
        return _is_known_codex_desktop_path(executable_path)
    return False


def codex_appx_install_locations() -> list[Path]:
    if not sys.platform.startswith("win"):
        return []
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                (
                    "Get-AppxPackage -Name OpenAI.Codex | "
                    "Select-Object -ExpandProperty InstallLocation"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]


def codex_app_executable_candidates() -> list[Path]:
    return _discover_codex_app_executables(
        appx_install_locations=codex_appx_install_locations()
    )


def _shell_execute_open(
    target: str | Path,
    *,
    verb: str = "open",
    parameters: str = "",
    working_dir: str | Path | None = None,
) -> bool:
    if not sys.platform.startswith("win"):
        return False
    prefix = "shell:appsfolder\\"
    if str(target).lower().startswith(prefix):
        with external_environment_scope():
            return activate_packaged_app(str(target)[len(prefix):], parameters)
    try:
        import ctypes
        from ctypes import wintypes

        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.argtypes = [
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
        ]
        shell32.ShellExecuteW.restype = wintypes.HINSTANCE
        with external_environment_scope():
            result = shell32.ShellExecuteW(
                None,
                verb,
                str(target),
                parameters or None,
                str(working_dir) if working_dir else None,
                1,
            )
        return int(result or 0) > 32
    except Exception as exc:
        _LOGGER.info(
            "codex_app_shell_execute_failed verb=%s target=%s error=%s",
            verb,
            target,
            exc,
        )
    return False


def _direct_windows_executable_open(
    target: str | Path,
    *,
    parameters: str = "",
    working_dir: str | Path | None = None,
) -> bool:
    """Open a verified executable with an explicit clean environment.

    ``ShellExecuteW`` has no environment-block parameter.  Use CreateProcess
    through ``subprocess`` for the normal executable-candidate path and keep
    ShellExecute for shell targets (AppX/shortcuts) where it is required.
    """
    if not sys.platform.startswith("win"):
        return False
    executable = Path(str(target))
    try:
        if not executable.is_file():
            return False
        args = shlex.split(str(parameters or ""), posix=False)
        subprocess.Popen(
            [str(executable), *args],
            cwd=str(working_dir) if working_dir else None,
            env=external_process_environment(),
        )
    except (OSError, ValueError):
        return False
    return True


def _shell_execute_open_with_elevation_fallback(
    target: str | Path,
    *,
    parameters: str = "",
    working_dir: str | Path | None = None,
) -> bool:
    # Store executables require package activation. A successful Popen only
    # confirms process creation; the app can then fail before opening a window.
    normalized = _normalized_windows_path(str(target))
    if "\\windowsapps\\openai.codex_" in normalized:
        app_id = os.environ.get(CODEX_APP_ID_ENV, "").strip() or CODEX_APP_DEFAULT_ID
        target_id = app_id if app_id.lower().startswith("shell:") else f"shell:AppsFolder\\{app_id}"
        return _shell_execute_open(target_id, parameters=parameters)
    if _direct_windows_executable_open(
        target,
        parameters=parameters,
        working_dir=working_dir,
    ):
        return True
    if _shell_execute_open(target, parameters=parameters, working_dir=working_dir):
        return True
    if _shell_execute_open(
        target,
        verb="runas",
        parameters=parameters,
        working_dir=working_dir,
    ):
        _LOGGER.info("codex_app_launch_elevated target=%s", target)
        return True
    return False


def _catch_up_process_environment_from_windows_registry() -> None:
    """Refresh in-process env so shell-launched Desktop children inherit it.

    ``ShellExecuteW`` cannot receive an environment block, so a Codex Desktop
    started by the HUD inherits this process's environment snapshot.  Provider
    API keys persisted to the user registry after the HUD was launched would
    otherwise stay invisible to the Desktop backend and chats on those
    providers fail at credential resolution.  Names already present always
    win, mirroring ``codex_cli_launcher._launch_environment``.
    """
    if os.name != "nt":
        return
    existing_names = {str(name).casefold() for name in os.environ}
    merged = 0
    for name, value in _windows_registry_environment().items():
        normalized_name = str(name)
        if normalized_name.casefold().startswith(PYINSTALLER_INTERNAL_ENV_PREFIX):
            continue
        if normalized_name.casefold() in existing_names:
            continue
        os.environ[normalized_name] = str(value)
        existing_names.add(normalized_name.casefold())
        merged += 1
    if merged:
        _LOGGER.info("codex_app_env_catch_up merged=%d", merged)


def codex_app_debugger_parameters(port: int) -> str:
    value = int(port)
    return (
        f"--remote-debugging-port={value} "
        f"--remote-allow-origins=http://127.0.0.1:{value}"
    )


def codex_app_debugger_args(port: int) -> list[str]:
    return codex_app_debugger_parameters(port).split()


def macos_codex_app_target() -> str:
    return os.environ.get(CODEX_APP_PATH_ENV, "").strip() or "Codex"


def macos_codex_app_name() -> str:
    target = macos_codex_app_target()
    name = Path(target).stem if target.endswith(".app") or "/" in target else target
    return name or "Codex"


def _notify_debugger_launch(
    callback: Callable[[int], object] | None,
    port: int,
) -> None:
    if callback is not None:
        callback(port)


def launch_macos_codex_app(
    *,
    debugger: bool = False,
    cdp_port: int | None = None,
    on_debugger_launch: Callable[[int], object] | None = None,
) -> bool:
    target = macos_codex_app_target()
    command = ["open"]
    if target.endswith(".app") or "/" in target:
        command.append(target)
    else:
        command.extend(["-a", target])
    port: int | None = None
    if debugger:
        port = int(cdp_port) if cdp_port is not None else cdp_port_from_env()
        command.extend(["--args", *codex_app_debugger_args(port)])
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=external_process_environment(),
        )
    except Exception as exc:
        _LOGGER.info("codex_app_macos_launch_failed target=%s error=%s", target, exc)
        return False
    _LOGGER.info(
        "codex_app_launched mode=%s target=%s",
        "debugger" if debugger else "normal",
        target,
    )
    if debugger and port is not None:
        _notify_debugger_launch(on_debugger_launch, port)
    return True


def launch_codex_app(
    *,
    debugger: bool = False,
    cdp_port: int | None = None,
    on_debugger_launch: Callable[[int], object] | None = None,
) -> bool:
    """Best-effort launch or activation of Codex Desktop."""
    if sys.platform == "darwin":
        return launch_macos_codex_app(
            debugger=debugger,
            cdp_port=cdp_port,
            on_debugger_launch=on_debugger_launch,
        )
    # ShellExecuteW children inherit this process env; catch it up first.
    _catch_up_process_environment_from_windows_registry()
    port = None
    if debugger:
        port = int(cdp_port) if cdp_port is not None else cdp_port_from_env()
    parameters = codex_app_debugger_parameters(port) if port is not None else ""
    for executable in codex_app_executable_candidates():
        if _shell_execute_open_with_elevation_fallback(
            executable,
            parameters=parameters,
            working_dir=executable.parent,
        ):
            if port is not None:
                _notify_debugger_launch(on_debugger_launch, port)
            _LOGGER.info(
                "codex_app_launched mode=%s target=%s%s",
                "debugger" if debugger else "normal",
                executable,
                f" port={port}" if port is not None else "",
            )
            return True
    for target in codex_app_shell_targets():
        if _shell_execute_open(target, parameters=parameters):
            if port is not None:
                _notify_debugger_launch(on_debugger_launch, port)
            _LOGGER.info(
                "codex_app_launched mode=%s target=%s%s",
                "debugger" if debugger else "normal",
                target,
                f" port={port}" if port is not None else "",
            )
            return True
    if port is not None:
        _LOGGER.info("codex_app_debugger_launch_unavailable port=%s", port)
    else:
        _LOGGER.info("codex_app_launch_unavailable")
    return False


def windows_running_codex_processes() -> list[CodexDesktopProcess]:
    """Return exact Windows Codex/App rows or raise if audit is unavailable."""
    if not sys.platform.startswith("win"):
        return []
    script = (
        "$items = @(Get-CimInstance Win32_Process "
        "-Filter \"Name='ChatGPT.exe' OR Name='Codex.exe'\" | "
        "Select-Object ProcessId,Name,ExecutablePath,CommandLine,CreationDate); "
        "ConvertTo-Json -InputObject $items -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=3.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Windows Codex process query failed") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            f"Windows Codex process query returned {result.returncode}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Windows Codex process query returned invalid JSON") from exc
    rows = payload if isinstance(payload, list) else [payload]
    processes: list[CodexDesktopProcess] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("Name") or "").strip()
        executable_path = str(row.get("ExecutablePath") or "").strip()
        if Path(name).stem.casefold() not in {"chatgpt", "codex"}:
            continue
        try:
            pid = int(row.get("ProcessId") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            continue
        processes.append(
            CodexDesktopProcess(
                pid=pid,
                name=name,
                executable_path=executable_path,
                command_line=str(row.get("CommandLine") or ""),
                started_at=_windows_process_started_at(row.get("CreationDate")),
            )
        )
    return processes


def windows_running_codex_desktop_processes() -> list[CodexDesktopProcess]:
    try:
        processes = windows_running_codex_processes()
    except RuntimeError as exc:
        _LOGGER.info("codex_app_process_query_failed platform=windows error=%s", exc)
        return []
    return [
        process
        for process in processes
        if is_codex_client_process(process.name, process.executable_path)
    ]


def is_macos_codex_desktop_command(executable: str, command_line: str) -> bool:
    normalized_executable = str(executable or "").replace("\\", "/").casefold()
    normalized_command = str(command_line or "").replace("\\", "/").casefold()
    if "/codex.app/contents/macos/" in normalized_executable:
        return True
    configured = os.environ.get(CODEX_APP_PATH_ENV, "").strip()
    if configured:
        configured_path = configured.replace("\\", "/").rstrip("/").casefold()
        if configured_path and configured_path in normalized_command:
            return "/contents/macos/" in normalized_executable
    return False


def macos_executable_from_command_line(command_line: object) -> str:
    text = str(command_line or "").strip()
    if not text:
        return ""
    try:
        args = shlex.split(text, posix=True)
    except ValueError:
        args = []
    executable = args[0] if args else ""
    if "/contents/macos/" in executable.replace("\\", "/").casefold():
        return executable
    match = re.match(
        r"^(.+?\.app/Contents/MacOS/[^\s]+)(?:\s|$)",
        text.replace("\\", "/"),
        flags=re.IGNORECASE,
    )
    return match.group(1).strip("\"'") if match is not None else executable


def _macos_process_rows(*, raise_on_error: bool = False) -> list[tuple[int, str, str, str]]:
    """Return ``(pid, started_at, comm, command_line)`` process rows."""
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,lstart=,comm=,command="],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if raise_on_error:
            raise RuntimeError("macOS Codex process query failed") from exc
        _LOGGER.info("codex_app_process_query_failed platform=macos error=%s", exc)
        return []
    if result.returncode != 0:
        if raise_on_error:
            raise RuntimeError(
                f"macOS Codex process query returned {result.returncode}"
            )
        _LOGGER.info(
            "codex_app_process_query_failed platform=macos code=%s",
            result.returncode,
        )
        return []
    rows: list[tuple[int, str, str, str]] = []
    for line in result.stdout.splitlines():
        row = line.strip()
        if not row:
            continue
        parts = row.split(None, 7)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except (TypeError, ValueError):
            continue
        started_at = ""
        comm = ""
        if (
            len(parts) >= 7
            and parts[1] in {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
        ):
            try:
                started = datetime.strptime(
                    " ".join(parts[1:6]), "%a %b %d %H:%M:%S %Y"
                ).replace(tzinfo=datetime.now().astimezone().tzinfo)
                started_at = started.astimezone(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                )
            except (TypeError, ValueError, OverflowError):
                started_at = ""
            comm = parts[6]
            command_line = parts[7] if len(parts) >= 8 else comm
        else:
            command_line = row.partition(" ")[2]
        if pid <= 0:
            continue
        rows.append((pid, started_at, comm, command_line))
    return rows


def macos_running_codex_desktop_processes() -> list[CodexDesktopProcess]:
    if sys.platform != "darwin":
        return []
    processes: list[CodexDesktopProcess] = []
    for pid, started_at, comm, command_line in _macos_process_rows():
        executable = macos_executable_from_command_line(command_line) or comm
        if not is_macos_codex_desktop_command(executable, command_line):
            continue
        processes.append(
            CodexDesktopProcess(
                pid=pid,
                name=Path(executable).name,
                executable_path=executable,
                command_line=command_line,
                started_at=started_at,
            )
        )
    return processes


def running_codex_desktop_processes() -> list[CodexDesktopProcess]:
    if sys.platform.startswith("win"):
        return windows_running_codex_desktop_processes()
    if sys.platform == "darwin":
        return macos_running_codex_desktop_processes()
    return []


def audited_running_codex_desktop_processes() -> list[CodexDesktopProcess]:
    if sys.platform.startswith("win"):
        return [
            process
            for process in windows_running_codex_processes()
            if is_codex_client_process(process.name, process.executable_path)
        ]
    if sys.platform == "darwin":
        processes = macos_running_codex_desktop_processes()
        if not processes:
            raise RuntimeError("Codex Desktop process could not be verified")
        return processes
    raise RuntimeError(f"Codex Desktop process audit is unsupported on {sys.platform}")


def running_standalone_codex_cli_pids() -> tuple[int, ...]:
    """Return Codex CLI PIDs, failing closed when audit is unavailable."""
    return tuple(sorted(process.pid for process in running_standalone_codex_cli_processes()))


def running_standalone_codex_cli_processes() -> list[CodexDesktopProcess]:
    """Return standalone Codex CLI process rows with generation metadata."""
    if sys.platform.startswith("win"):
        rows = windows_running_codex_processes()
        return [
            process
            for process in rows
            if Path(process.name).stem.casefold() == "codex"
            and not is_codex_client_process(process.name, process.executable_path)
        ]
    if sys.platform == "darwin":
        processes: list[CodexDesktopProcess] = []
        for pid, started_at, comm, command_line in _macos_process_rows(
            raise_on_error=True
        ):
            executable = macos_executable_from_command_line(command_line) or comm
            if Path(executable).name.casefold() != "codex":
                continue
            if is_macos_codex_desktop_command(executable, command_line):
                continue
            if pid > 0 and pid != os.getpid():
                processes.append(
                    CodexDesktopProcess(
                        pid=pid,
                        name=Path(executable).name,
                        executable_path=executable,
                        command_line=command_line,
                        started_at=started_at,
                    )
                )
        return processes
    raise RuntimeError(f"Codex process audit is unsupported on {sys.platform}")


def stop_macos_codex_app(*, timeout_seconds: float = 8.0) -> bool:
    app_name = macos_codex_app_name()
    try:
        subprocess.run(
            ["osascript", "-e", f'tell application "{app_name}" to quit'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(1.0, float(timeout_seconds)),
            check=False,
        )
    except Exception as exc:
        _LOGGER.info("codex_app_macos_quit_failed app=%s error=%s", app_name, exc)
        return False
    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["pgrep", "-x", app_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
                check=False,
            )
        except Exception:
            return True
        if result.returncode != 0:
            return True
        time.sleep(0.1)
    _LOGGER.info("codex_app_macos_quit_timeout app=%s", app_name)
    return False


def stop_windows_codex_app(
    *,
    timeout_seconds: float = 8.0,
    process_query: Callable[[], list[CodexDesktopProcess]] | None = None,
    process_probe: Callable[[int], bool] | None = None,
    terminate: Callable[[int], bool] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> bool:
    query = process_query or audited_running_codex_desktop_processes
    probe = process_probe or process_exists
    stop = terminate or terminate_process
    now = monotonic or time.monotonic
    wait = sleep or time.sleep
    try:
        processes = query()
    except RuntimeError as exc:
        _LOGGER.info("codex_app_stop_unavailable error=%s", exc)
        return False
    pending = {process.pid for process in processes if process.pid > 0}
    if not pending:
        return True
    deadline = now() + max(0.5, float(timeout_seconds))
    while pending and now() < deadline:
        for pid in list(pending):
            if not probe(pid):
                pending.discard(pid)
                continue
            stop(pid)
        wait(0.1)
        pending = {pid for pid in pending if probe(pid)}
        if not pending:
            return True
        try:
            pending.update(process.pid for process in query() if process.pid > 0)
        except RuntimeError:
            pass
    remaining = [pid for pid in sorted(pending) if probe(pid)]
    if remaining:
        _LOGGER.info("codex_app_stop_incomplete pids=%s", ",".join(map(str, remaining)))
        return False
    return True


def stop_codex_app(*, timeout_seconds: float = 8.0) -> bool:
    if sys.platform.startswith("win"):
        return stop_windows_codex_app(timeout_seconds=timeout_seconds)
    if sys.platform == "darwin":
        return stop_macos_codex_app(timeout_seconds=timeout_seconds)
    return False


def restart_codex_app(
    *,
    debugger: bool = False,
    cdp_port: int | None = None,
    timeout_seconds: float = 8.0,
    on_debugger_launch: Callable[[int], object] | None = None,
) -> bool:
    if not stop_codex_app(timeout_seconds=timeout_seconds):
        return False
    return launch_codex_app(
        debugger=debugger,
        cdp_port=cdp_port,
        on_debugger_launch=on_debugger_launch,
    )


def codex_processes_running() -> bool:
    if sys.platform.startswith("win"):
        try:
            return bool(audited_running_codex_desktop_processes())
        except RuntimeError:
            return False
    if sys.platform == "darwin":
        return bool(macos_running_codex_desktop_processes())
    return False


def codex_processes_exited() -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        return not bool(audited_running_codex_desktop_processes())
    except RuntimeError:
        return False


def activate_running_codex_app(
    *,
    launch: Callable[..., bool] | None = None,
) -> bool:
    if not codex_processes_running():
        return False
    activate = launch or launch_codex_app
    return bool(activate(debugger=False))


def _new_window_tracker() -> object:
    from .platforms import CodexWindowTracker

    return CodexWindowTracker(enable_uia=False)


def _window_snapshot_values(
    snapshot: object,
    *,
    fallback_hwnd: int = 0,
) -> tuple[str, str, int]:
    return (
        str(getattr(snapshot, "status", "") or ""),
        str(getattr(snapshot, "reason", "") or ""),
        int(getattr(snapshot, "hwnd", 0) or fallback_hwnd),
    )


def _tracker_window_is_active(tracker: object, hwnd: int) -> bool:
    if not hwnd:
        return False
    try:
        return bool(tracker.is_active(hwnd))
    except Exception:
        return False


def wait_for_visible_codex_window(
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.25,
    tracker_factory: Callable[[], object] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> WindowReadiness:
    if not sys.platform.startswith("win"):
        return True, "unsupported", "", 0
    try:
        tracker = (tracker_factory or _new_window_tracker)()
    except Exception:
        return True, "tracker-error", "", 0
    if not getattr(tracker, "enabled", False):
        return True, "disabled", "", 0
    now = monotonic or time.monotonic
    wait = sleep or time.sleep
    deadline = now() + max(0.0, float(timeout_seconds))
    while True:
        snapshot = tracker.get_window_snapshot()
        status, reason, hwnd = _window_snapshot_values(snapshot)
        if status == "visible":
            return True, status, reason, hwnd
        if now() >= deadline:
            return False, status, reason, hwnd
        wait(max(0.01, float(poll_seconds)))


def prepare_codex_window_for_renderer(
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.25,
    launch_if_missing: bool = False,
    cdp_port: int | None = None,
    tracker_factory: Callable[[], object] | None = None,
    processes_running: Callable[[], bool] | None = None,
    activate: Callable[[], bool] | None = None,
    launch: Callable[..., bool] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> WindowReadiness:
    """Restore and focus Codex before renderer injection, without restart policy."""
    if not sys.platform.startswith("win"):
        return True, "unsupported", "", 0
    try:
        tracker = (tracker_factory or _new_window_tracker)()
    except Exception:
        return True, "tracker-error", "", 0
    if not getattr(tracker, "enabled", False):
        return True, "disabled", "", 0

    is_running = processes_running or codex_processes_running
    activate_app = activate or activate_running_codex_app
    launch_app = launch or launch_codex_app
    now = monotonic or time.monotonic
    wait = sleep or time.sleep
    deadline = now() + max(0.0, float(timeout_seconds))
    launch_attempted = False
    activation_attempted = False
    last_status = "not_found"
    last_reason = ""
    last_hwnd = 0
    while True:
        snapshot = tracker.get_window_snapshot()
        last_status, last_reason, last_hwnd = _window_snapshot_values(snapshot)
        is_active = _tracker_window_is_active(tracker, last_hwnd)
        if last_status == "visible" and is_active:
            return True, last_status, last_reason, last_hwnd

        if (
            not activation_attempted
            and is_running()
            and (last_status != "visible" or not is_active)
        ):
            activation_attempted = True
            activated = activate_app()
            _LOGGER.info(
                "codex_app_shell_activation_requested activated=%s status=%s "
                "hwnd=%s reason=%s",
                activated,
                last_status,
                last_hwnd,
                last_reason,
            )
            if activated:
                wait(max(0.05, float(poll_seconds)))
                continue

        try:
            activated_hwnd = int(tracker.activate_main_window() or 0)
        except Exception:
            activated_hwnd = 0
        if activated_hwnd:
            snapshot = tracker.get_window_snapshot()
            last_status, last_reason, last_hwnd = _window_snapshot_values(
                snapshot,
                fallback_hwnd=activated_hwnd,
            )
            activated_is_active = _tracker_window_is_active(tracker, last_hwnd)
            if last_status == "visible" and activated_is_active:
                return True, last_status, last_reason, last_hwnd

        if (
            launch_if_missing
            and not launch_attempted
            and last_status in {"not_found", "hidden", "cloaked"}
        ):
            launch_attempted = True
            if is_running() and last_status == "not_found":
                launched = False
                action = "await_restart_confirmation"
            else:
                launched = bool(launch_app(debugger=True, cdp_port=cdp_port))
                action = "launch_debugger"
            _LOGGER.info(
                "codex_app_window_restore_requested action=%s launched=%s "
                "status=%s hwnd=%s reason=%s",
                action,
                launched,
                last_status,
                last_hwnd,
                last_reason,
            )

        if now() >= deadline:
            return False, last_status, last_reason, last_hwnd
        wait(max(0.01, float(poll_seconds)))


__all__ = [
    "CODEX_APP_DEFAULT_ID", "CODEX_APP_ID_ENV", "CODEX_APP_PATH_ENV",
    "CodexDesktopProcess", "WindowReadiness", "activate_running_codex_app",
    "audited_running_codex_desktop_processes", "codex_app_debugger_args",
    "codex_app_debugger_parameters", "codex_app_executable_candidates",
    "codex_app_shell_targets", "codex_appx_install_locations",
    "codex_processes_exited", "codex_processes_running", "is_codex_client_process",
    "is_macos_codex_desktop_command", "launch_codex_app",
    "launch_macos_codex_app", "macos_executable_from_command_line",
    "macos_running_codex_desktop_processes", "prepare_codex_window_for_renderer",
    "restart_codex_app", "running_codex_desktop_processes",
    "running_standalone_codex_cli_pids", "stop_codex_app",
    "stop_macos_codex_app", "stop_windows_codex_app",
    "wait_for_visible_codex_window", "windows_running_codex_desktop_processes",
    "windows_running_codex_processes",
]
