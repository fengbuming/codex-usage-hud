"""Renderer CDP endpoint selection and bounded startup classification."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
import json
import logging
import os
import re
import socket
import sys

from .codex_app_runtime import (
    CodexDesktopProcess,
    audited_running_codex_desktop_processes,
    codex_processes_running,
    is_codex_desktop_main_process,
    running_codex_desktop_processes,
)
from .platforms.cdp_probe import (
    CDP_PORT_ENV,
    DEFAULT_CDP_PORT,
    cdp_port_from_env,
    cdp_version_info,
    list_targets,
    pick_page_target,
)
from .runtime_diagnostics import append_renderer_diagnostic
from .runtime_paths import renderer_cdp_state_path


RENDERER_CDP_DISCOVERY_TIMEOUT_SECONDS = 0.25
RENDERER_STARTUP_LAUNCH = "launch"
RENDERER_STARTUP_ATTACH = "attach"
RENDERER_STARTUP_RESTART_REQUIRED = "restart-required"
RENDERER_STARTUP_ATTACH_LAUNCHED = "attach-launched"
RENDERER_STARTUP_ATTACH_OBSERVED = "attach-observed"
RENDERER_STARTUP_RELAUNCH_OBSERVED = "relaunch-observed"
RENDERER_STARTUP_UPDATE_RESTARTED = "update-restarted"

_REMOTE_DEBUGGING_PORT_PATTERN = re.compile(
    r"(?:^|\s)--remote-debugging-port(?:=|\s+)(\d{1,5})(?=\s|$)"
)
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RendererStartupPlan:
    scenario: str
    port: int | None = None
    port_source: str = ""
    reason: str = ""


@dataclass(frozen=True)
class RendererCdpPortCandidate:
    port: int
    source: str
    pid: int | None = None


@dataclass(frozen=True)
class RendererStartupPorts:
    """Replaceable environment boundary for deterministic startup scenarios."""

    desktop_processes: Callable[[], list[CodexDesktopProcess]] = (
        running_codex_desktop_processes
    )
    audited_desktop_processes: Callable[[], list[CodexDesktopProcess]] = (
        audited_running_codex_desktop_processes
    )
    desktop_running: Callable[[], bool] = codex_processes_running
    diagnostic: Callable[..., object] = append_renderer_diagnostic
    state_path: Callable[[], object] = renderer_cdp_state_path


def valid_cdp_port(value: object) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def remote_debugging_ports(command_line: object) -> tuple[int, ...]:
    ports: list[int] = []
    for match in _REMOTE_DEBUGGING_PORT_PATTERN.finditer(str(command_line or "")):
        port = valid_cdp_port(match.group(1))
        if port is not None and port not in ports:
            ports.append(port)
    return tuple(ports)


def explicit_cdp_port_from_env() -> int | None:
    return valid_cdp_port(os.environ.get(CDP_PORT_ENV, "").strip())


def read_persisted_cdp_state_port(
    key: str,
    *,
    state_path: Callable[[], object] = renderer_cdp_state_path,
) -> int | None:
    try:
        path = state_path()
        data = json.loads(path.read_text(encoding="utf-8"))
    except (AttributeError, OSError, RuntimeError, json.JSONDecodeError):
        return None
    return valid_cdp_port(data.get(key)) if isinstance(data, Mapping) else None


def read_persisted_cdp_port(
    *,
    state_path: Callable[[], object] = renderer_cdp_state_path,
) -> int | None:
    return read_persisted_cdp_state_port(
        "lastSuccessfulPort",
        state_path=state_path,
    )


def remember_cdp_port(
    port: int | None,
    *,
    requested: bool = False,
    successful: bool = False,
    state_path: Callable[[], object] = renderer_cdp_state_path,
) -> None:
    value = valid_cdp_port(port)
    if value is None:
        return
    try:
        path = state_path()
    except (OSError, RuntimeError):
        return
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        payload = dict(existing) if isinstance(existing, Mapping) else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    if requested:
        payload["lastRequestedPort"] = value
    if successful:
        payload["lastSuccessfulPort"] = value
    payload["updatedAt"] = datetime.now().astimezone().isoformat()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return


def remember_requested_cdp_port(
    port: int | None,
    *,
    state_path: Callable[[], object] = renderer_cdp_state_path,
) -> None:
    remember_cdp_port(port, requested=True, state_path=state_path)


def remember_successful_cdp_port(
    port: int | None,
    *,
    state_path: Callable[[], object] = renderer_cdp_state_path,
) -> None:
    remember_cdp_port(
        port,
        requested=True,
        successful=True,
        state_path=state_path,
    )


def localhost_cdp_port_is_listening(port: int | None) -> bool:
    try:
        with socket.create_connection(
            ("127.0.0.1", int(port or 0)),
            timeout=0.2,
        ):
            return True
    except (OSError, TypeError, ValueError):
        return False


def _localhost_bind_targets() -> list[tuple[int, str]]:
    targets = [(socket.AF_INET, "127.0.0.1")]
    if socket.has_ipv6:
        targets.append((socket.AF_INET6, "::1"))
    return targets


def localhost_cdp_port_available(port: int) -> bool:
    sockets: list[socket.socket] = []
    try:
        for family, host in _localhost_bind_targets():
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    sock.setsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_EXCLUSIVEADDRUSE,
                        1,
                    )
                sock.bind((host, int(port)))
            except OSError:
                sock.close()
                raise
            sockets.append(sock)
    except OSError:
        return False
    finally:
        for sock in sockets:
            try:
                sock.close()
            except OSError:
                pass
    return True


def allocate_fresh_cdp_port() -> int:
    current = cdp_port_from_env()
    for _attempt in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port != current and localhost_cdp_port_available(port):
            return port
    raise RuntimeError("unable to allocate a fresh local CDP port")


def assign_fresh_cdp_port() -> int:
    old_port = cdp_port_from_env()
    new_port = allocate_fresh_cdp_port()
    os.environ[CDP_PORT_ENV] = str(new_port)
    _LOGGER.info("renderer_cdp_port_reassigned old=%s new=%s", old_port, new_port)
    return new_port


def select_initial_cdp_port(
    *,
    state_path: Callable[[], object] = renderer_cdp_state_path,
    listening: Callable[[int | None], bool] = localhost_cdp_port_is_listening,
) -> int:
    explicit = explicit_cdp_port_from_env()
    if explicit is not None:
        return explicit
    persisted = read_persisted_cdp_port(state_path=state_path)
    requested = read_persisted_cdp_state_port(
        "lastRequestedPort",
        state_path=state_path,
    )
    selected = requested if requested is not None and listening(requested) else (
        persisted or DEFAULT_CDP_PORT
    )
    os.environ[CDP_PORT_ENV] = str(selected)
    _LOGGER.info(
        "renderer_cdp_port_selected fixed=%s source=%s",
        selected,
        "requested" if selected == requested else "persisted" if persisted else "default",
    )
    return selected


def select_launch_cdp_port(
    *,
    require_fresh: bool = False,
    state_path: Callable[[], object] = renderer_cdp_state_path,
    available: Callable[[int], bool] = localhost_cdp_port_available,
    allocate: Callable[[], int] = allocate_fresh_cdp_port,
) -> int:
    preferred: list[int] = []
    for candidate in (
        explicit_cdp_port_from_env(),
        read_persisted_cdp_state_port("lastRequestedPort", state_path=state_path),
        read_persisted_cdp_port(state_path=state_path),
        DEFAULT_CDP_PORT,
    ):
        value = valid_cdp_port(candidate)
        if value is not None and value not in preferred:
            preferred.append(value)
    if not require_fresh:
        for port in preferred:
            if available(port):
                os.environ[CDP_PORT_ENV] = str(port)
                return port
    port = allocate()
    os.environ[CDP_PORT_ENV] = str(port)
    return port


def cdp_port_candidates(
    *,
    ports: RendererStartupPorts = RendererStartupPorts(),
) -> list[RendererCdpPortCandidate]:
    candidates: list[RendererCdpPortCandidate] = []

    def append(port: object, source: str, pid: int | None = None) -> None:
        value = valid_cdp_port(port)
        if value is None or any(item.port == value for item in candidates):
            return
        candidates.append(RendererCdpPortCandidate(value, source, pid))

    for process in ports.desktop_processes():
        if not is_codex_desktop_main_process(process):
            continue
        for port in remote_debugging_ports(process.command_line):
            append(port, "desktop-process", process.pid)
    append(explicit_cdp_port_from_env(), "environment")
    append(
        read_persisted_cdp_state_port("lastRequestedPort", state_path=ports.state_path),
        "requested",
    )
    append(read_persisted_cdp_port(state_path=ports.state_path), "successful")
    append(DEFAULT_CDP_PORT, "default")
    return candidates


def validate_cdp_candidate(
    candidate: RendererCdpPortCandidate,
    *,
    listening: Callable[[int | None], bool] = localhost_cdp_port_is_listening,
    version_probe: Callable[[int, float], Mapping[str, object]] = cdp_version_info,
    target_list: Callable[[int, float], list[Mapping[str, object]]] = list_targets,
    page_picker: Callable[[list[Mapping[str, object]]], Mapping[str, object]] = (
        pick_page_target
    ),
) -> tuple[bool, str]:
    if not listening(candidate.port):
        return False, "not-listening"
    try:
        version = version_probe(
            candidate.port,
            RENDERER_CDP_DISCOVERY_TIMEOUT_SECONDS,
        )
        if not str(version.get("Browser") or version.get("Protocol-Version") or ""):
            return False, "CDP version endpoint has no protocol identity"
        targets = target_list(
            candidate.port,
            RENDERER_CDP_DISCOVERY_TIMEOUT_SECONDS,
        )
        page_picker(targets)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def find_existing_cdp_candidate(
    *,
    ports: RendererStartupPorts = RendererStartupPorts(),
    validate: Callable[[RendererCdpPortCandidate], tuple[bool, str]] = (
        validate_cdp_candidate
    ),
) -> RendererCdpPortCandidate | None:
    for candidate in cdp_port_candidates(ports=ports):
        valid, reason = validate(candidate)
        if valid:
            if candidate.source == "desktop-process":
                ports.diagnostic(
                    "renderer_cdp_process_port_discovered",
                    platform=sys.platform,
                    pid=candidate.pid,
                    port=candidate.port,
                )
            return candidate
        ports.diagnostic(
            "renderer_cdp_candidate_rejected",
            port=candidate.port,
            source=candidate.source,
            reason=reason,
        )
    return None


def observed_startup_plan(
    *,
    ports: RendererStartupPorts = RendererStartupPorts(),
) -> RendererStartupPlan:
    try:
        processes = [
            process for process in ports.audited_desktop_processes()
            if is_codex_desktop_main_process(process)
        ]
    except RuntimeError as exc:
        return RendererStartupPlan(
            RENDERER_STARTUP_RESTART_REQUIRED,
            reason=f"observed-codex-process-audit-failed: {exc}",
        )
    if not processes:
        return RendererStartupPlan(
            RENDERER_STARTUP_RESTART_REQUIRED,
            reason="observed-codex-process-not-found",
        )
    declared_ports = sorted(
        {
            port
            for process in processes
            for port in remote_debugging_ports(process.command_line)
        }
    )
    if len(declared_ports) > 1:
        return RendererStartupPlan(
            RENDERER_STARTUP_RESTART_REQUIRED,
            reason="observed-codex-has-conflicting-cdp-ports",
        )
    if not declared_ports:
        return RendererStartupPlan(
            RENDERER_STARTUP_RELAUNCH_OBSERVED,
            reason="observed-codex-has-no-declared-cdp-port",
        )
    port = declared_ports[0]
    os.environ[CDP_PORT_ENV] = str(port)
    return RendererStartupPlan(
        RENDERER_STARTUP_ATTACH_OBSERVED,
        port=port,
        port_source="observed-desktop-process",
    )


def startup_plan(
    *,
    launched_codex: bool = False,
    observed_codex_launch: bool = False,
    update_restarted: bool = False,
    ports: RendererStartupPorts = RendererStartupPorts(),
    select_initial: Callable[[], int] = select_initial_cdp_port,
    select_launch: Callable[..., int] = select_launch_cdp_port,
    find_existing: Callable[[], RendererCdpPortCandidate | None] | None = None,
) -> RendererStartupPlan:
    if launched_codex:
        plan = RendererStartupPlan(
            RENDERER_STARTUP_ATTACH_LAUNCHED,
            port=select_initial(),
            port_source="requested-launch",
        )
    elif observed_codex_launch:
        plan = observed_startup_plan(ports=ports)
        if update_restarted and plan.scenario in {
            RENDERER_STARTUP_ATTACH_OBSERVED,
            RENDERER_STARTUP_RELAUNCH_OBSERVED,
        }:
            plan = RendererStartupPlan(
                RENDERER_STARTUP_UPDATE_RESTARTED,
                port=plan.port,
                port_source=plan.port_source,
                reason="observed-codex-update-restarted",
            )
    elif ports.desktop_running():
        existing = (
            find_existing()
            if find_existing is not None
            else find_existing_cdp_candidate(ports=ports)
        )
        if existing is None:
            plan = RendererStartupPlan(
                RENDERER_STARTUP_RESTART_REQUIRED,
                reason="running-codex-has-no-verified-cdp-target",
            )
        else:
            os.environ[CDP_PORT_ENV] = str(existing.port)
            plan = RendererStartupPlan(
                RENDERER_STARTUP_ATTACH,
                port=existing.port,
                port_source=existing.source,
            )
    else:
        plan = RendererStartupPlan(
            RENDERER_STARTUP_LAUNCH,
            port=select_launch(),
            port_source="launch",
        )
    ports.diagnostic(
        "renderer_startup_classified",
        scenario=plan.scenario,
        port=plan.port,
        source=plan.port_source,
        reason=plan.reason,
    )
    return plan


__all__ = [
    "RENDERER_STARTUP_ATTACH",
    "RENDERER_STARTUP_ATTACH_LAUNCHED",
    "RENDERER_STARTUP_ATTACH_OBSERVED",
    "RENDERER_STARTUP_LAUNCH",
    "RENDERER_STARTUP_RELAUNCH_OBSERVED",
    "RENDERER_STARTUP_RESTART_REQUIRED",
    "RENDERER_STARTUP_UPDATE_RESTARTED",
    "RendererCdpPortCandidate",
    "RendererStartupPlan",
    "RendererStartupPorts",
    "allocate_fresh_cdp_port",
    "assign_fresh_cdp_port",
    "cdp_port_candidates",
    "find_existing_cdp_candidate",
    "localhost_cdp_port_available",
    "localhost_cdp_port_is_listening",
    "read_persisted_cdp_port",
    "read_persisted_cdp_state_port",
    "remember_cdp_port",
    "remember_requested_cdp_port",
    "remember_successful_cdp_port",
    "remote_debugging_ports",
    "select_initial_cdp_port",
    "select_launch_cdp_port",
    "startup_plan",
    "validate_cdp_candidate",
    "valid_cdp_port",
]
