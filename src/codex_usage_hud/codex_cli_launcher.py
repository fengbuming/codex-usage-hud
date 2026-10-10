"""Discover and launch Codex CLI from the renderer settings surface.

The renderer owns the form and command preview, while this module owns the
platform boundary: installed terminal discovery, Codex profile resolution,
local work-directory discovery, and launching a new terminal process.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

from .process_environment import (
    PYINSTALLER_INTERNAL_ENV_PREFIX,
    external_process_environment,
)
from .codex_provider_config import read_provider_definitions, default_codex_config_path


POWERSHELL_INSTALL_URL = (
    "https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows"
)
REFERENCE_TERMINAL_PROFILE = "Codex (cunai, HUD)"
DEFAULT_PROXY_PORT = 7897
_PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_MODEL_PROVIDER_PATTERN = re.compile(
    r"(?m)^\s*model_provider\s*=\s*[\"']([^\"']+)[\"']"
)
_PROXY_PORT_PATTERN = re.compile(
    r"(?i)https?://(?:[^@/\s]+@)?127\.0\.0\.1:(?P<port>\d{1,5})"
)


def _canonical_session_id(value: object) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        canonical = str(uuid.UUID(candidate))
    except (AttributeError, TypeError, ValueError):
        return ""
    return canonical if candidate.casefold() == canonical else ""


def _platform_name(value: str | None = None) -> str:
    if value:
        return str(value).strip().lower()
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _windows_registry_environment() -> dict[str, str]:
    """Read environment values added after a long-running HUD was started."""
    try:
        import winreg
    except ImportError:
        return {}

    values: dict[str, str] = {}
    locations = (
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    )
    for root, subkey in locations:
        try:
            with winreg.OpenKey(root, subkey) as key:
                value_count = int(winreg.QueryInfoKey(key)[1])
                for index in range(value_count):
                    try:
                        name, value, _value_type = winreg.EnumValue(key, index)
                    except OSError:
                        continue
                    if isinstance(name, str) and isinstance(value, str):
                        values[name] = value
        except OSError:
            continue
    return values


def _launch_environment(
    *,
    platform_name: str | None = None,
    codex_home: str | Path | None = None,
) -> dict[str, str]:
    environment = external_process_environment()
    if codex_home is not None:
        home = _normalise_existing_path(codex_home)
        if home is None:
            raise ValueError("Codex 会话主目录不存在或不可访问。")
        # A transfer target is verified against this exact local store.  The
        # spawned terminal must inherit the same store or ``codex resume`` can
        # silently open a different profile's history.
        environment["CODEX_HOME"] = str(home)
    active_platform = _platform_name(platform_name)
    if active_platform != "windows":
        return environment

    path_separator = ";"
    existing_names = {str(name).casefold() for name in environment}
    for name, value in _windows_registry_environment().items():
        normalized_name = str(name)
        if normalized_name.casefold().startswith(PYINSTALLER_INTERNAL_ENV_PREFIX):
            continue
        if normalized_name.casefold() == "path":
            current_name = next(
                (item for item in environment if str(item).casefold() == "path"),
                "PATH",
            )
            current_value = str(environment.get(current_name) or "")
            registry_value = str(value or "")
            merged: list[str] = []
            seen_path_entries: set[str] = set()
            for entry in (
                *current_value.split(path_separator),
                *registry_value.split(path_separator),
            ):
                path_entry = entry.strip()
                if not path_entry:
                    continue
                path_key = ntpath.normcase(ntpath.normpath(path_entry)).rstrip("\\/")
                if path_key in seen_path_entries:
                    continue
                seen_path_entries.add(path_key)
                merged.append(path_entry)
            if merged:
                environment[current_name] = path_separator.join(merged)
            existing_names.add(current_name.casefold())
            continue
        if normalized_name.casefold() in existing_names:
            continue
        environment[normalized_name] = value
        existing_names.add(normalized_name.casefold())
    return environment


def _first_available(*candidates: object) -> str:
    for candidate in candidates:
        value = str(candidate or "").strip()
        if not value:
            continue
        if any(separator in value for separator in ("/", "\\")):
            if Path(value).is_file():
                return value
            continue
        resolved = shutil.which(value)
        if resolved:
            return str(resolved)
    return ""


def _windows_terminal_candidates() -> tuple[Path, ...]:
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    return (
        local_app_data / "Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json",
        local_app_data / "Microsoft/Windows Terminal/settings.json",
    )


def _reference_proxy_defaults() -> dict[str, object]:
    """Read the user's existing named Windows Terminal profile when present."""
    for path in _windows_terminal_candidates():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        profiles = payload.get("profiles", {}) if isinstance(payload, Mapping) else {}
        profile_list = profiles.get("list", []) if isinstance(profiles, Mapping) else []
        if not isinstance(profile_list, list):
            continue
        for profile in profile_list:
            if not isinstance(profile, Mapping):
                continue
            if str(profile.get("name") or "").strip() != REFERENCE_TERMINAL_PROFILE:
                continue
            environment = profile.get("environment", {})
            if not isinstance(environment, Mapping):
                environment = {}
            for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
                match = _PROXY_PORT_PATTERN.search(str(environment.get(key) or ""))
                if match:
                    return {
                        "enabled": True,
                        "port": int(match.group("port")),
                        "profileName": REFERENCE_TERMINAL_PROFILE,
                        "source": str(path),
                    }
            return {
                "enabled": False,
                "port": DEFAULT_PROXY_PORT,
                "profileName": REFERENCE_TERMINAL_PROFILE,
                "source": str(path),
            }
    return {
        "enabled": False,
        "port": DEFAULT_PROXY_PORT,
        "profileName": "",
        "source": "",
    }


def _terminal_entry(
    terminal_id: str,
    label: str,
    executable: str,
    *,
    shell: str,
    kind: str = "shell",
    recommended: bool = False,
    shell_executable: str = "",
) -> dict[str, object]:
    return {
        "id": terminal_id,
        "label": label,
        "executable": executable,
        "shell": shell,
        "kind": kind,
        "recommended": recommended,
        "shellExecutable": shell_executable or executable,
    }


def discover_terminals(*, platform_name: str | None = None) -> list[dict[str, object]]:
    """Return terminals that can be launched on the current machine."""
    platform = _platform_name(platform_name)
    terminals: list[dict[str, object]] = []
    if platform == "windows":
        pwsh = _first_available(
            "pwsh",
            Path(os.environ.get("ProgramFiles") or "") / "PowerShell/7/pwsh.exe",
            Path(os.environ.get("ProgramW6432") or "") / "PowerShell/7/pwsh.exe",
            Path(os.environ.get("LOCALAPPDATA") or "") / "Microsoft/PowerShell/7/pwsh.exe",
        )
        powershell = _first_available(
            "powershell",
            os.environ.get("WINDIR", "")
            + r"\System32\WindowsPowerShell\v1.0\powershell.exe",
        )
        cmd = _first_available("cmd", os.environ.get("COMSPEC", ""))
        wt = _first_available(
            "wt",
            Path(os.environ.get("LOCALAPPDATA") or "") / "Microsoft/WindowsApps/wt.exe",
        )
        git_bash = _first_available(
            "bash",
            Path(os.environ.get("ProgramFiles") or "") / "Git/bin/bash.exe",
            Path(os.environ.get("ProgramFiles") or "") / "Git/git-bash.exe",
        )
        wsl = _first_available("wsl")
        if pwsh:
            terminals.append(
                _terminal_entry(
                    "powershell7",
                    "PowerShell 7（推荐）",
                    pwsh,
                    shell="powershell",
                    recommended=True,
                )
            )
        if powershell:
            terminals.append(
                _terminal_entry(
                    "powershell",
                    "Windows PowerShell 5.1",
                    powershell,
                    shell="powershell",
                )
            )
        if wt:
            shell_executable = pwsh or powershell or cmd or "cmd.exe"
            terminals.append(
                _terminal_entry(
                    "windows-terminal",
                    "Windows Terminal",
                    wt,
                    shell="powershell" if pwsh or powershell else "cmd",
                    kind="windows_terminal",
                    shell_executable=shell_executable,
                )
            )
        if cmd:
            terminals.append(
                _terminal_entry("cmd", "命令提示符（cmd）", cmd, shell="cmd")
            )
        if git_bash:
            terminals.append(
                _terminal_entry("git-bash", "Git Bash", git_bash, shell="bash")
            )
        if wsl:
            terminals.append(
                _terminal_entry("wsl", "WSL", wsl, shell="bash")
            )
        return terminals

    if platform == "macos":
        zsh = _first_available("zsh")
        terminal_app = next(
            (
                str(path)
                for path in (
                    Path("/System/Applications/Utilities/Terminal.app"),
                    Path("/Applications/Utilities/Terminal.app"),
                )
                if path.exists()
            ),
            "",
        )
        iterm_app = next(
            (
                str(path)
                for path in (
                    Path("/Applications/iTerm.app"),
                    Path.home() / "Applications/iTerm.app",
                )
                if path.exists()
            ),
            "",
        )
        if terminal_app:
            terminals.append(
                _terminal_entry(
                    "terminal-app",
                    "Terminal",
                    _first_available("osascript") or "osascript",
                    shell="zsh",
                    kind="terminal_app",
                    recommended=True,
                    shell_executable=zsh or "zsh",
                )
            )
        if iterm_app and _first_available("osascript"):
            terminals.append(
                _terminal_entry(
                    "iterm2",
                    "iTerm2",
                    "osascript",
                    shell="zsh",
                    kind="iterm2",
                    shell_executable=zsh or "zsh",
                )
            )
        if zsh and not terminals:
            terminals.append(_terminal_entry("zsh", "zsh", zsh, shell="zsh"))
        return terminals

    shell = _first_available(os.environ.get("SHELL"), "bash", "sh")
    for terminal_id, label, command, kind in (
        ("gnome-terminal", "GNOME Terminal", "gnome-terminal", "gnome_terminal"),
        ("konsole", "Konsole", "konsole", "konsole"),
        ("xterm", "xterm", "xterm", "xterm"),
    ):
        executable = _first_available(command)
        if executable:
            terminals.append(
                _terminal_entry(
                    terminal_id,
                    label,
                    executable,
                    shell="bash",
                    kind=kind,
                    recommended=not terminals,
                    shell_executable=shell or "bash",
                )
            )
    if shell and not terminals:
        terminals.append(_terminal_entry("shell", "默认 Shell", shell, shell="bash"))
    return terminals


def _codex_home(codex_home: str | Path | None = None) -> Path:
    value = str(codex_home or os.environ.get("CODEX_HOME") or "").strip()
    return Path(value).expanduser() if value else Path.home() / ".codex"


def _model_provider_from_file(path: Path) -> str:
    try:
        match = _MODEL_PROVIDER_PATTERN.search(path.read_text(encoding="utf-8"))
    except OSError:
        return ""
    return str(match.group(1) if match else "").strip().lower()


def resolve_cli_profile(
    provider: str,
    *,
    codex_home: str | Path | None = None,
) -> tuple[str, str]:
    """Return ``(profile_name, default_provider)`` for a provider id."""
    normalized = str(provider or "").strip().lower()
    home = _codex_home(codex_home)
    default_provider = _model_provider_from_file(home / "config.toml")
    try:
        profile_files = sorted(home.glob("*.config.toml"), key=lambda path: path.name.casefold())
    except OSError:
        profile_files = []
    for path in profile_files:
        if _model_provider_from_file(path) == normalized:
            profile_name = path.name
            if profile_name.endswith(".config.toml"):
                profile_name = profile_name[: -len(".config.toml")]
            else:
                profile_name = path.stem
            return profile_name, default_provider
    return "", default_provider


def _normalise_existing_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text or "\x00" in text:
        return None
    try:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = path.resolve(strict=False)
        return path if path.is_dir() else None
    except OSError:
        return None


def _workdirs_from_local_projects(codex_home: Path) -> dict[str, str]:
    """Map project root paths to friendly labels from Codex Desktop local-projects.

    Returns ``{root_path_string: label}``. A label is the project ``name`` when
    present, falling back to the root path's basename. Non-absolute or
    non-existent root paths are excluded.
    """
    state_file = codex_home / ".codex-global-state.json"
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    projects = payload.get("local-projects")
    if not isinstance(projects, Mapping):
        return {}
    result: dict[str, str] = {}
    for project in projects.values():
        if not isinstance(project, Mapping):
            continue
        name = str(project.get("name") or "").strip()
        root_paths = project.get("rootPaths")
        if not isinstance(root_paths, list):
            continue
        for raw_root in root_paths:
            path = _normalise_existing_path(raw_root)
            if path is None:
                continue
            text = str(path)
            result[text] = name or (path.name or text)
    return result


def discover_workdirs(
    *,
    sessions_root: str | Path | None = None,
    state_db_path: str | Path | None = None,
    current_workdir: str | Path | None = None,
) -> list[dict[str, str]]:
    """List Codex Desktop project root directories, de-duplicated and readable.

    ``state_db_path`` is accepted only for backward compatibility and is no
    longer read; the deprecation mirrors the callers that still pass it.
    """
    root = Path(sessions_root).expanduser() if sessions_root else Path.home() / ".codex/sessions"
    root_paths_to_labels = _workdirs_from_local_projects(root.parent)
    normalized_to_label = {
        os.path.normcase(text): label for text, label in root_paths_to_labels.items()
    }
    entries: list[dict[str, str]] = []
    seen: set[str] = set()

    def append(path_value: object, label: str = "") -> None:
        nonlocal seen
        path = _normalise_existing_path(path_value)
        if path is None:
            return
        key = os.path.normcase(str(path))
        if key in seen:
            return
        seen.add(key)
        entries.append(
            {
                "path": str(path),
                "label": label or normalized_to_label.get(key) or (path.name or str(path)),
                "source": "Codex Desktop",
            }
        )

    append(current_workdir)
    for path_string, label in root_paths_to_labels.items():
        append(path_string, label)

    entries.sort(key=lambda entry: (str(entry["label"]).casefold(), str(entry["path"]).casefold()))
    if entries and current_workdir:
        current_key = os.path.normcase(str(Path(current_workdir).expanduser()))
        for index, entry in enumerate(entries):
            if os.path.normcase(entry["path"]) == current_key:
                entries.insert(0, entries.pop(index))
                break
    return entries


def _permission_args(permission: str) -> list[str]:
    value = str(permission or "full").strip().lower()
    if value == "read-only":
        return ["--sandbox", "read-only", "--ask-for-approval", "on-request"]
    if value == "workspace-write":
        return ["--sandbox", "workspace-write", "--ask-for-approval", "on-request"]
    return ["--dangerously-bypass-approvals-and-sandbox"]


def build_codex_cli_args(
    *,
    provider: str,
    profile: str = "",
    default_provider: str = "",
    permission: str = "full",
    resume: bool = False,
    resume_session_id: str = "",
    model: str = "",
) -> list[str]:
    args: list[str] = []
    normalized_provider = str(provider or "").strip().lower()
    normalized_profile = str(profile or "").strip()
    if normalized_profile:
        args.extend(("--profile", normalized_profile))
    elif normalized_provider and normalized_provider != str(default_provider or "").strip().lower():
        if _PROVIDER_ID_PATTERN.fullmatch(normalized_provider):
            args.extend(("--config", f"model_provider={normalized_provider}"))
    normalized_model = str(model or "").strip()
    if normalized_model:
        args.extend(("--config", f"model={normalized_model}"))
    args.extend(_permission_args(permission))
    if resume:
        args.append("resume")
        normalized_session_id = _canonical_session_id(resume_session_id)
        if resume_session_id and not normalized_session_id:
            raise ValueError("Codex 会话标识无效。")
        if normalized_session_id:
            args.append(normalized_session_id)
    return args


def _shell_quote(value: object, shell: str, *, force: bool = False) -> str:
    text = str(value or "")
    if (
        not force
        and "://" not in text
        and re.fullmatch(r"[A-Za-z0-9_./:@%+=,-]+", text)
    ):
        return text
    if shell == "powershell":
        return "'" + text.replace("'", "''") + "'"
    if shell == "cmd":
        return '"' + text.replace('"', '\\"') + '"'
    return "'" + text.replace("'", "'\\''") + "'"


def _clean_terminal_title_part(value: object) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "").strip())
    return " ".join(text.split())


def build_codex_cli_title(*, provider: str, workdir: str | Path) -> str:
    """Build the stable terminal title for a Provider/work-directory launch."""
    normalized_provider = str(provider or "").strip().lower()
    if not _PROVIDER_ID_PATTERN.fullmatch(normalized_provider):
        return ""
    workdir_text = str(workdir or "").strip()
    if not workdir_text:
        return ""
    try:
        workdir_path = Path(workdir_text)
        workdir_label = workdir_path.name or workdir_path.anchor or workdir_text
    except (OSError, ValueError):
        workdir_label = workdir_text
    workdir_label = _clean_terminal_title_part(workdir_label)
    return f"[{normalized_provider}] {workdir_label}" if workdir_label else ""


def build_codex_cli_command(
    *,
    provider: str,
    profile: str = "",
    default_provider: str = "",
    permission: str = "full",
    resume: bool = False,
    resume_session_id: str = "",
    model: str = "",
    use_proxy: bool = False,
    proxy_port: int | str = DEFAULT_PROXY_PORT,
    workdir: str = "",
    shell: str = "powershell",
    executable: str = "codex",
    provider_base_url: str = "",
) -> str:
    shell_name = str(shell or "powershell").strip().lower()
    if shell_name not in {"powershell", "cmd", "bash", "zsh"}:
        shell_name = "powershell"
    args = [executable, *build_codex_cli_args(
        provider=provider,
        profile=profile,
        default_provider=default_provider,
        permission=permission,
        resume=resume,
        resume_session_id=resume_session_id,
        model=model,
    )]
    if provider_base_url:
        args.extend(["--config", f'model_providers.{provider}.base_url={json.dumps(provider_base_url)}'])
    command = " ".join(
        value if index == 0 and value == executable else _shell_quote(value, shell_name)
        for index, value in enumerate(args)
    )
    try:
        port = max(1, min(65535, int(proxy_port)))
    except (TypeError, ValueError):
        port = DEFAULT_PROXY_PORT
    lines: list[str] = []
    proxy_keys = "HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy NO_PROXY no_proxy".split()
    if shell_name == "powershell":
        lines.append("Remove-Item " + ",".join(f"Env:{key}" for key in proxy_keys) + " -ErrorAction SilentlyContinue")
    elif shell_name == "cmd":
        lines.extend(f'set "{key}="' for key in proxy_keys)
    else:
        lines.append("unset " + " ".join(proxy_keys))
    if use_proxy:
        proxy = f"http://127.0.0.1:{port}"
        if shell_name == "powershell":
            lines.extend(
                [
                    f"$env:HTTP_PROXY = {_shell_quote(proxy, shell_name)}",
                    f"$env:HTTPS_PROXY = {_shell_quote(proxy, shell_name)}",
                ]
            )
        elif shell_name == "cmd":
            lines.extend([f'set "HTTP_PROXY={proxy}"', f'set "HTTPS_PROXY={proxy}"'])
        else:
            lines.append(
                f"export HTTP_PROXY={_shell_quote(proxy, shell_name)} HTTPS_PROXY={_shell_quote(proxy, shell_name)}"
            )
    if str(workdir or "").strip():
        # Always quote the working directory.  Besides paths containing
        # spaces, this keeps generated PowerShell commands stable for POSIX
        # test hosts that exercise the Windows shell boundary.
        quoted_workdir = _shell_quote(
            str(workdir).strip(), shell_name, force=True
        )
        if shell_name == "powershell":
            lines.append(f"Set-Location -LiteralPath {quoted_workdir}")
        elif shell_name == "cmd":
            lines.append(f"cd /d {quoted_workdir}")
        else:
            lines.append(f"cd -- {quoted_workdir}")
    lines.append(command)
    return "\n".join(lines)


def discover_codex_cli_options(
    *,
    provider: str,
    sessions_root: str | Path | None = None,
    state_db_path: str | Path | None = None,
    current_workdir: str | Path | None = None,
    codex_home: str | Path | None = None,
    platform_name: str | None = None,
) -> dict[str, object]:
    normalized_provider = str(provider or "").strip().lower()
    profile, default_provider = resolve_cli_profile(
        normalized_provider,
        codex_home=codex_home,
    )
    terminals = discover_terminals(platform_name=platform_name)
    default_terminal = next(
        (
            str(item.get("id") or "")
            for item in terminals
            if item.get("recommended")
        ),
    ) or (str(terminals[0].get("id") or "") if terminals else "")
    workdirs = discover_workdirs(
        sessions_root=sessions_root,
        state_db_path=state_db_path,
        current_workdir=current_workdir,
    )
    definition = read_provider_definitions(
        Path(codex_home) / "config.toml" if codex_home else default_codex_config_path()
    ).get(normalized_provider)
    proxy = {
        "enabled": definition.use_proxy if definition else False,
        "port": definition.proxy_port if definition else DEFAULT_PROXY_PORT,
        "baseUrl": definition.base_url if definition else "",
        "profileName": "",
        "source": "",
    }
    codex_command = _first_available("codex")
    return {
        "platform": _platform_name(platform_name),
        "provider": normalized_provider,
        "profile": profile,
        "defaultProvider": default_provider,
        "terminals": terminals,
        "defaultTerminal": default_terminal,
        "powershell7": {
            "available": any(item.get("id") == "powershell7" for item in terminals),
            "installUrl": POWERSHELL_INSTALL_URL,
        },
        "codex": {
            "available": bool(codex_command),
            "command": "codex",
        },
        "proxy": proxy,
        "permissions": [
            {
                "id": "full",
                "label": "完全访问（跳过审批与沙箱）",
                "dangerous": True,
            },
            {
                "id": "workspace-write",
                "label": "仅工作区读写，操作前询问",
                "dangerous": False,
            },
            {
                "id": "read-only",
                "label": "只读沙箱，操作前询问",
                "dangerous": False,
            },
        ],
        "defaultPermission": "full",
        "workdirs": workdirs,
        "defaultWorkdir": "",
        "noProjectWorkdir": str(Path.home()),
    }


def _terminal_process_command(
    terminal: Mapping[str, object],
    command: str,
    workdir: str,
    *,
    open_as_tab: bool = False,
    title: str = "",
) -> list[str]:
    kind = str(terminal.get("kind") or "shell")
    executable = str(terminal.get("executable") or "").strip()
    shell_executable = str(
        terminal.get("shellExecutable") or terminal.get("executable") or ""
    ).strip()
    shell = str(terminal.get("shell") or "powershell").strip().lower()
    command_text = str(command or "")
    if title:
        if shell == "powershell":
            title_command = (
                "$Host.UI.RawUI.WindowTitle = "
                f"{_shell_quote(title, shell)}"
            )
        elif shell == "cmd":
            title_command = f"title {_shell_quote(title, shell)}"
        else:
            title_command = (
                r"printf '\033]0;%s\007' "
                f"{_shell_quote(title, shell)}"
            )
        command_text = f"{title_command}\n{command_text}"
    if kind == "windows_terminal":
        args = [executable]
        if open_as_tab:
            args.extend(("-w", "0", "new-tab"))
        if title:
            args.extend(("--title", title, "--suppressApplicationTitle"))
        args.extend(
            (
                "-d",
                workdir,
                shell_executable,
            )
        )
        if shell == "cmd":
            args.extend(("/D", "/K", command_text))
        elif shell in {"bash", "zsh"}:
            shell_name = Path(shell_executable).stem.casefold()
            if shell_name == "wsl":
                args.extend(("--", "bash", "-lc", f"{command_text}\nexec bash"))
            else:
                args.extend(
                    (
                        "--login",
                        "-i",
                        "-c",
                        f"{command_text}\nexec {_shell_quote(shell_executable, 'bash')}",
                    )
                )
        else:
            args.extend(("-NoLogo", "-NoExit", "-Command", command_text))
        return args
    if shell == "powershell":
        return [executable, "-NoLogo", "-NoExit", "-Command", command_text]
    if shell == "cmd":
        return [executable, "/D", "/K", command_text]
    if kind in {"terminal_app", "iterm2"}:
        apple_command = (
            command_text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", "\\r")
        )
        if kind == "iterm2":
            app_name = "iTerm2"
            if open_as_tab:
                script = (
                    f'tell application "{app_name}"\n'
                    'if (count of windows) > 0 then\n'
                    '  tell current window to create tab with default profile '
                    f'command "{apple_command}"\n'
                    'else\n'
                    '  create window with default profile '
                    f'command "{apple_command}"\n'
                    'end if\n'
                    'end tell'
                )
            else:
                script = (
                    f'tell application "{app_name}" to create window with default profile '
                    f'command "{apple_command}"'
                )
        else:
            if open_as_tab:
                script = (
                    'tell application "Terminal"\n'
                    'if (count of windows) > 0 then\n'
                    f'  do script "{apple_command}" in front window\n'
                    'else\n'
                    f'  do script "{apple_command}"\n'
                    'end if\n'
                    'end tell'
                )
            else:
                script = f'tell application "Terminal" to do script "{apple_command}"'
        return [executable, "-e", script]
    if kind == "gnome_terminal":
        return [
            executable,
            "--working-directory",
            workdir,
            "--",
            shell_executable,
            "-lc",
            command_text,
        ]
    if kind == "konsole":
        return [
            executable,
            "--workdir",
            workdir,
            "-e",
            shell_executable,
            "-lc",
            command_text,
        ]
    if kind == "xterm":
        return [executable, "-e", shell_executable, "-lc", command_text]
    return [
        executable,
        "--login",
        "-i",
        "-c",
        f"{command_text}\nexec {shell_executable}",
    ]


def _command_with_codex_home(
    command: str,
    *,
    codex_home: Path | None,
    shell: object,
) -> str:
    """Set the verified store inside the launched shell as well as its env.

    Reusing an already-running Windows Terminal can create the tab through its
    existing host process.  Prefixing the shell command makes the selected
    ``CODEX_HOME`` explicit even when that host does not propagate the client
    process environment to the new tab.
    """
    if codex_home is None:
        return str(command or "")
    shell_name = str(shell or "powershell").strip().lower()
    home_text = str(codex_home)
    if shell_name == "powershell":
        prefix = f"$env:CODEX_HOME = {_shell_quote(home_text, shell_name)}"
    elif shell_name == "cmd":
        prefix = f'set "CODEX_HOME={home_text}"'
    else:
        prefix = f"export CODEX_HOME={_shell_quote(home_text, shell_name)}"
    return f"{prefix}\n{str(command or '')}"


def _terminal_process_probe(
    command: list[str],
    *,
    platform_name: str,
) -> bool:
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "text": True,
        "timeout": 2.0,
        "check": False,
    }
    if platform_name == "windows":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(command, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    output = str(getattr(result, "stdout", "") or "").strip()
    if platform_name == "windows":
        return bool(output) and not output.casefold().startswith(("info:", "信息:"))
    return bool(output)


def is_terminal_open(
    terminal: Mapping[str, object],
    *,
    platform_name: str | None = None,
) -> bool:
    """Return whether a terminal host that can receive a new tab is running."""
    platform = _platform_name(platform_name)
    kind = str(terminal.get("kind") or "shell").strip().lower()
    if platform == "windows" and kind == "windows_terminal":
        return _terminal_process_probe(
            [
                "tasklist",
                "/FI",
                "IMAGENAME eq WindowsTerminal.exe",
                "/FO",
                "CSV",
                "/NH",
            ],
            platform_name=platform,
        )
    if platform == "macos":
        process_name = {
            "terminal_app": "Terminal",
            "iterm2": "iTerm2",
        }.get(kind)
        if process_name:
            return _terminal_process_probe(
                ["pgrep", "-x", process_name],
                platform_name=platform,
            )
    return False


def _windows_terminal_host(
    terminals: list[dict[str, object]],
    selected: Mapping[str, object],
) -> dict[str, object] | None:
    """Return a Windows Terminal host using the selected shell when possible."""
    selected_kind = str(selected.get("kind") or "shell").strip().lower()
    if selected_kind == "windows_terminal":
        return dict(selected)
    for candidate in terminals:
        if str(candidate.get("kind") or "").strip().lower() != "windows_terminal":
            continue
        host = dict(candidate)
        host["shell"] = str(selected.get("shell") or host.get("shell") or "powershell")
        host["shellExecutable"] = str(
            selected.get("shellExecutable")
            or selected.get("executable")
            or host.get("shellExecutable")
            or ""
        )
        return host
    return None


def launch_codex_cli(
    *,
    terminal_id: str,
    command: str,
    workdir: str,
    provider: str = "",
    codex_home: str | Path | None = None,
    platform_name: str | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    commit_spawn: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Launch the user-edited command in a new terminal window or tab."""
    def cancelled() -> bool:
        try:
            return bool(cancel_requested is not None and cancel_requested())
        except Exception:
            return True

    def cancelled_result() -> dict[str, object]:
        return {
            "cancelled": True,
            "terminalId": str(terminal_id or ""),
            "workdir": str(workdir or "").strip(),
        }

    if cancelled():
        return cancelled_result()
    command_text = str(command or "").strip()
    if not command_text:
        raise ValueError("Codex CLI 命令不能为空。")
    path = _normalise_existing_path(workdir)
    if path is None:
        raise ValueError("工作目录不存在或不可访问。")
    resolved_codex_home = (
        _normalise_existing_path(codex_home) if codex_home is not None else None
    )
    if codex_home is not None and resolved_codex_home is None:
        raise ValueError("Codex 会话主目录不存在或不可访问。")
    if cancelled():
        return cancelled_result()
    terminals = discover_terminals(platform_name=platform_name)
    terminal = next(
        (
            item
            for item in terminals
            if str(item.get("id") or "") == str(terminal_id or "")
        ),
        None,
    )
    if terminal is None:
        raise ValueError("所选终端当前不可用，请重新打开终端列表。")
    if cancelled():
        return cancelled_result()
    executable = str(terminal.get("executable") or "").strip()
    if not executable:
        raise ValueError("所选终端没有可执行文件。")
    launch_terminal: Mapping[str, object] = terminal
    opened_as_tab = False
    platform = _platform_name(platform_name)
    if platform == "windows":
        terminal_host = _windows_terminal_host(terminals, terminal)
        if terminal_host is not None and is_terminal_open(
            terminal_host,
            platform_name=platform,
        ):
            launch_terminal = terminal_host
            opened_as_tab = True
    elif is_terminal_open(terminal, platform_name=platform):
        opened_as_tab = True
    if cancelled():
        return cancelled_result()
    creationflags = (
        getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        if platform == "windows"
        else 0
    )
    title = build_codex_cli_title(provider=provider, workdir=str(path))
    if commit_spawn is not None:
        try:
            if not bool(commit_spawn()):
                return cancelled_result()
        except Exception:
            return cancelled_result()
    elif cancelled():
        return cancelled_result()
    # Redirected stdio makes PowerShell treat -NoExit as non-interactive and
    # exit after -Command completes. Let the new terminal own its stdio.
    terminal_command = _command_with_codex_home(
        command_text,
        codex_home=resolved_codex_home,
        shell=launch_terminal.get("shell"),
    )
    process = subprocess.Popen(
        _terminal_process_command(
            launch_terminal,
            terminal_command,
            str(path),
            open_as_tab=opened_as_tab,
            title=title,
        ),
        cwd=str(path),
        env=_launch_environment(
            platform_name=platform_name,
            codex_home=resolved_codex_home,
        ),
        creationflags=creationflags,
    )
    return {
        "pid": int(process.pid),
        "terminal": str(terminal.get("label") or terminal_id),
        "terminalId": str(terminal_id),
        "workdir": str(path),
        "openedAsTab": opened_as_tab,
        "launchMode": "new-tab" if opened_as_tab else "new-window",
        "title": title,
    }


__all__ = [
    "DEFAULT_PROXY_PORT",
    "POWERSHELL_INSTALL_URL",
    "REFERENCE_TERMINAL_PROFILE",
    "build_codex_cli_args",
    "build_codex_cli_command",
    "build_codex_cli_title",
    "discover_codex_cli_options",
    "discover_terminals",
    "discover_workdirs",
    "is_terminal_open",
    "launch_codex_cli",
    "resolve_cli_profile",
]
