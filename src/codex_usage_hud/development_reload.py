"""Explicit development reloads that never terminate a provider helper."""

from __future__ import annotations

import argparse
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

from . import instance_lock
from .provider_proxy_reload import bootstrap_digest
from .provider_proxy_service import ProviderProxyServiceClient, _CREATE_BREAKAWAY_FROM_JOB, _read_state
from .runtime_paths import runtime_file_path


def gateway_client() -> ProviderProxyServiceClient:
    from .provider_gateway import gateway_info

    info = gateway_info()
    url = urlsplit(str(info.get("endpoint") or "http://127.0.0.1:57324"))
    if url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("The active gateway is not a local HUD service.")
    client = ProviderProxyServiceClient(url.port or 57324, state_path=runtime_file_path("provider-gateway-service.json"))
    client.state = _read_state(client.state_path, client.port)
    if not client.state:
        raise RuntimeError("No authenticated gateway service state is available; nothing was stopped.")
    return client


def proxy_health(client) -> dict:
    health = client._call("health")
    if not isinstance(health.get("pid"), int) or health["pid"] <= 0:
        raise RuntimeError("The gateway did not return a valid process identity.")
    return health


def reload_proxy(client, source_root: Path) -> dict:
    before = proxy_health(client)
    if before.get("reloadSupported") is not True:
        raise RuntimeError("The running proxy predates hot reload. It was left running. Its first migration requires a planned idle maintenance window; never kill it during generation.")
    if before.get("bootstrapDigest") != bootstrap_digest(source_root):
        raise RuntimeError("Proxy listener/control code changed. Handler hot reload cannot replace the bootstrap; the running proxy was preserved. Schedule a maintenance migration.")
    result = client._call("reload")
    after = proxy_health(client)
    if before["pid"] != after["pid"] or result.get("pid") != after["pid"]:
        raise RuntimeError("Proxy identity changed during reload; verification failed.")
    return after


def _hud_command_line(pid: int) -> str:
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            raise RuntimeError("Cannot verify the recorded HUD process identity.")
        script = f"Get-CimInstance Win32_Process -Filter 'ProcessId = {int(pid)}' | Select-Object -ExpandProperty CommandLine"
        command = [shell, "-NoProfile", "-NonInteractive", "-Command", script]
    else:
        command = ["ps", "-p", str(int(pid)), "-o", "args="]
    result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=5, check=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("Cannot verify the recorded HUD process identity.")
    return result.stdout.strip()


def _hud_observation() -> dict:
    from .renderer_cdp.connection import connect_websocket, receive_message, send_command
    from .platforms.cdp_probe import list_targets, pick_page_target
    from .renderer_startup import read_persisted_cdp_port

    port = read_persisted_cdp_port(state_path=lambda: runtime_file_path("renderer_cdp_state.json"))
    if not port:
        raise RuntimeError("No persisted Codex CDP endpoint is available; no process was stopped.")
    target = pick_page_target(list_targets(port, 3))
    url = target.get("webSocketDebuggerUrl", "") if isinstance(target, dict) else getattr(target, "websocket_url", "")
    if not url or not str(target.get("url") or "").startswith("app://"):
        raise RuntimeError("No live Codex renderer was found; no process was stopped.")
    connection = connect_websocket(url, 3)
    try:
        send_command(connection, 1, "Runtime.evaluate", {"expression": "JSON.stringify({bridge:window.__codexUsageHudState?.payload?.settingsBridgeUrl,settingsOpen:!!window.__codexUsageHudSettingsUiState?.open})", "returnByValue": True})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                reply = json.loads(receive_message(connection))
            except TimeoutError:
                continue
            if reply.get("id") == 1:
                return json.loads(reply["result"]["result"]["value"])
        raise RuntimeError("HUD state inspection timed out; no process was stopped.")
    finally:
        connection.close()


def _request_development_stop(bridge: str, pid: int) -> None:
    url = urlsplit(bridge)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("HUD settings bridge is not a local endpoint.")
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=3)
    try:
        connection.request("POST", "/command", json.dumps({"action": "developmentStop", "expectedPid": pid, "preserveProxy": True}), {"Content-Type": "application/json"})
        response = connection.getresponse()
        response.read()
        if response.status != 200:
            raise RuntimeError("HUD source-reload handoff was rejected.")
    finally:
        connection.close()


def _bridge_matches_pid(bridge: str, pid: int) -> bool:
    url = urlsplit(bridge)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=1)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        return response.status == 200 and json.loads(response.read()).get("pid") == pid
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def _hud_python_executable(*, platform: str | None = None) -> str:
    executable = Path(sys.executable)
    if (platform or os.name) == "nt" and not getattr(sys, "frozen", False):
        windowless = executable.with_name("pythonw.exe")
        if windowless.is_file():
            return str(windowless)
    return str(executable)


def launch_hud_reload() -> dict:
    """Start the reload coordinator outside the HUD's process/job lifetime."""
    root = Path(__file__).resolve().parents[2]
    tool = root / "tools/reload_hud.py"
    if not tool.is_file():
        raise RuntimeError("当前安装不包含源码重载工具，请使用安装包更新后重启。")
    options = {"cwd": str(root), "stdin": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | _CREATE_BREAKAWAY_FROM_JOB
    else:
        options["start_new_session"] = True
    log_path = runtime_file_path("development-reload.log")
    with log_path.open("ab") as log:
        process = subprocess.Popen([_hud_python_executable(), str(tool), "--hud-only", "--wait-settings-close"], stdout=log, stderr=log, **options)
    return {"coordinatorPid": process.pid, "pending": True}


def restart_hud(client, project_root: Path, *, timeout: float = 20, wait_settings_close: bool = False) -> dict:
    from .runtime_paths import hud_lock_path

    proxy = proxy_health(client)
    pid_path = hud_lock_path()
    pid = instance_lock.read_pid(pid_path)
    if not pid or pid == proxy["pid"] or pid == os.getpid():
        raise RuntimeError("HUD and proxy process identities could not be safely separated.")
    command_line = _hud_command_line(pid)
    if not re.search(r"(?:-m\s+codex_usage_hud|codex[-_]usage[-_]hud(?:\.exe)?|codex-hud)", command_line, re.I) or any(flag in command_line for flag in ("--provider-proxy-helper", "--work-overlay-helper")):
        raise RuntimeError("The recorded process is not the HUD main process; it was left running.")
    observation = _hud_observation()
    if wait_settings_close:
        close_deadline = time.monotonic() + min(5, timeout)
        while observation.get("settingsOpen") and time.monotonic() < close_deadline:
            time.sleep(.1)
            observation = _hud_observation()
    if observation.get("settingsOpen"):
        raise RuntimeError("Close the HUD settings editor before reloading source, so drafts are preserved.")
    if not (project_root / "src/codex_usage_hud/__main__.py").is_file():
        raise RuntimeError("Source checkout was not found; the HUD was left running.")
    if importlib.util.find_spec("zstandard") is None:
        raise RuntimeError("The current Python environment is missing the project's zstandard dependency; install dependencies before source reload. HUD and proxy were left running.")
    _request_development_stop(str(observation.get("bridge") or ""), pid)
    deadline = time.monotonic() + min(3, timeout)
    while instance_lock.process_exists(pid) and time.monotonic() < deadline:
        time.sleep(.1)
    if instance_lock.process_exists(pid):
        # One-time compatibility with older HUDs that do not know the handoff
        # command. Only the verified main PID may be stopped, never its helper.
        if instance_lock.read_pid(pid_path) != pid or proxy_health(client)["pid"] != proxy["pid"]:
            raise RuntimeError("Process identities changed; the HUD was left running.")
        if not instance_lock.terminate_process(pid):
            raise RuntimeError("Unable to stop the HUD main process; proxy was left running.")
    deadline = time.monotonic() + timeout
    while instance_lock.process_exists(pid) and time.monotonic() < deadline:
        time.sleep(.1)
    if instance_lock.process_exists(pid):
        raise RuntimeError("HUD main process did not finish stopping; proxy was left running.")
    env = dict(os.environ)
    source = str(project_root / "src")
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    options = {"cwd": str(project_root), "env": env, "stdin": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | _CREATE_BREAKAWAY_FROM_JOB
    else:
        options["start_new_session"] = True
    log_path = runtime_file_path("development-reload.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        process = subprocess.Popen([_hud_python_executable(), "-m", "codex_usage_hud"], stdout=log, stderr=log, **options)
    while time.monotonic() < deadline:
        current = instance_lock.read_pid(pid_path)
        if current == process.pid and instance_lock.process_exists(current):
            try:
                ready = _hud_observation()
            except (OSError, RuntimeError, ValueError, KeyError):
                ready = {}
            if ready.get("bridge") and _bridge_matches_pid(ready["bridge"], current):
                if proxy_health(client)["pid"] != proxy["pid"]:
                    raise RuntimeError("Proxy identity changed during HUD reload.")
                return {"hudPid": current, "proxyPid": proxy["pid"], "log": str(log_path)}
        if process.poll() is not None:
            break
        time.sleep(.2)
    raise RuntimeError(f"Fresh HUD startup was not confirmed; proxy remains running. See {log_path}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Reload HUD source without stopping the independent provider proxy.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--hud-only", action="store_true", help="start a fresh HUD Python process, preserving the proxy")
    mode.add_argument("--proxy-only", action="store_true", help="hot reload new-request handlers; existing streams finish on old code")
    mode.add_argument("--all", action="store_true", help="hot reload proxy handlers, then refresh HUD source")
    mode.add_argument("--status", action="store_true", help="read process/generation status only")
    parser.add_argument("--wait-settings-close", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        client = gateway_client()
        root = Path(__file__).resolve().parents[2]
        if args.status:
            result = proxy_health(client)
        else:
            result = {}
            with instance_lock.HudInstanceLock(path=runtime_file_path("development-reload.pid"), mutex_name="Local\\codex-usage-hud-development-reload"):
                if args.proxy_only or args.all:
                    result["proxy"] = reload_proxy(client, root / "src/codex_usage_hud")
                if not args.proxy_only:
                    result["hud"] = restart_hud(client, root, wait_settings_close=args.wait_settings_close)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, http.client.HTTPException) as error:
        print(f"Source reload stopped safely: {error}", file=sys.stderr)
        return 1
