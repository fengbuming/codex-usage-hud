"""Manual, per-launch fallback for cores that reject historical providers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys

from .process_environment import external_process_environment
from .runtime_paths import hud_runtime_dir, runtime_file_path

# Last stable release before 0.166; offline thread/resume across providers was
# checked with its published native package. Do not accept every older version.
COMPATIBLE_CORE_VERSION = "0.165.0"


def _shell_command(parts: list[str], platform: str) -> str:
    if platform.startswith("win"):
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in parts)
    return shlex.join(parts)


def manual_instructions(*, platform: str | None = None, runtime_dir: Path | None = None) -> dict:
    platform = platform or sys.platform
    if not (platform.startswith("win") or platform == "darwin"):
        raise ValueError("手动兼容启动目前支持 Windows 和 macOS。")
    root = (runtime_dir or hud_runtime_dir()) / "compatible-core" / COMPATIBLE_CORE_VERSION
    install = _shell_command([
        "npm.cmd" if platform.startswith("win") else "npm", "install", "--prefix", str(root),
        "--no-audit", "--no-fund", f"@openai/codex@{COMPATIBLE_CORE_VERSION}",
    ], platform)
    executable = Path(sys.executable)
    if not getattr(sys, "frozen", False) and executable.name.casefold() == "pythonw.exe":
        console = executable.with_name("python.exe")
        if console.is_file():
            executable = console
    command = [str(executable)]
    tool = Path(__file__).resolve().parents[2] / "tools/start_provider_compatible_codex.py"
    if not getattr(sys, "frozen", False):
        command += [str(tool)] if tool.is_file() else ["-m", "codex_usage_hud.provider_compatibility"]
    command += ["--provider-compatible-codex", str(root)]
    return {"version": COMPATIBLE_CORE_VERSION, "installCommand": install,
            "launchCommand": _shell_command(command, platform)}


def _core_executable(root: Path) -> Path:
    root = root.expanduser().resolve()
    name = "codex.exe" if sys.platform.startswith("win") else "codex"
    if root.is_file():
        candidates = [root]
    else:
        package = root / "node_modules/@openai"
        candidates = [path for path in package.rglob(name) if path.is_file()]
    if len(candidates) != 1:
        raise ValueError("未找到唯一的兼容核心，请先执行安装命令，或指定核心可执行文件。")
    core = candidates[0]
    result = subprocess.run([str(core), "--version"], capture_output=True, text=True,
                            timeout=10, check=False, env=external_process_environment(),
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or result.stdout.strip() != f"codex-cli {COMPATIBLE_CORE_VERSION}":
        raise ValueError(f"兼容入口仅接受已核对的 codex-cli {COMPATIBLE_CORE_VERSION}。")
    return core


def _desktop_executable(explicit: str = "") -> Path:
    from .codex_app_runtime import codex_app_executable_candidates, macos_codex_app_target

    if explicit:
        candidates = [Path(explicit).expanduser()]
    elif sys.platform.startswith("win"):
        candidates = codex_app_executable_candidates()
    elif sys.platform == "darwin":
        target = macos_codex_app_target()
        candidates = [Path(target)] if "/" in target else [
            Path("/Applications/Codex.app"), Path.home() / "Applications/Codex.app",
        ]
    else:
        raise ValueError("兼容启动仅支持 Windows 和 macOS。")
    for candidate in candidates:
        if candidate.suffix == ".app" and candidate.is_dir():
            with (candidate / "Contents/Info.plist").open("rb") as info:
                executable = plistlib.load(info)["CFBundleExecutable"]
            candidate = candidate / "Contents/MacOS" / executable
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError("未找到 Codex Desktop，请用 --app 指定应用可执行文件或 .app。")


def launch_compatible_codex(core_root: Path, *, app: str = "", check: bool = False) -> dict:
    from .codex_app_runtime import audited_running_codex_desktop_processes, codex_app_debugger_args, macos_running_codex_desktop_processes
    from .platforms.cdp_probe import cdp_port_from_env
    from .renderer_startup import read_persisted_cdp_port

    core = _core_executable(core_root)
    executable = _desktop_executable(app)
    running = (audited_running_codex_desktop_processes() if sys.platform.startswith("win")
               else macos_running_codex_desktop_processes())
    result = {"version": COMPATIBLE_CORE_VERSION, "core": str(core),
              "app": str(executable), "desktopRunning": bool(running)}
    if check:
        return result
    if running:
        raise ValueError("请先等待任务结束并关闭所有 Codex 窗口，再执行兼容启动；现有进程未被停止。")
    port = read_persisted_cdp_port(state_path=lambda: runtime_file_path("renderer_cdp_state.json")) or cdp_port_from_env()
    environment = external_process_environment()
    environment["CODEX_CLI_PATH"] = str(core)
    options = {"env": environment, "cwd": str(executable.parent), "stdin": subprocess.DEVNULL,
               "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform.startswith("win"):
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    process = subprocess.Popen([str(executable), *codex_app_debugger_args(port)], **options)
    return {**result, "pid": process.pid}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manually start Codex with a verified provider-compatible core.")
    parser.add_argument("--provider-compatible-codex", type=Path, required=True)
    parser.add_argument("--app", default="")
    parser.add_argument("--check", action="store_true", help="check paths and version without launching")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(launch_compatible_codex(args.provider_compatible_codex, app=args.app, check=args.check), ensure_ascii=False))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
