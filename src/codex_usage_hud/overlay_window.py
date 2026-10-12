"""Window activation and bounded recovery actions for the desktop overlay."""

from __future__ import annotations

from collections.abc import Callable
from .platforms.cdp_probe import cdp_port_from_env

WindowResult = tuple[bool, str, str, int]


def prepare_codex_window_for_standalone(
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.25,
    launch_if_missing: bool = False,
    prepare_window_for_renderer: Callable[..., WindowResult],
    tracker_factory: Callable[[], object],
    processes_running: Callable[[], bool],
    activate: Callable[[], bool],
    launch: Callable[..., bool],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> WindowResult:
    """Prepare a visible Codex window through injected platform actions."""
    return prepare_window_for_renderer(
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        launch_if_missing=launch_if_missing,
        cdp_port=cdp_port_from_env(),
        tracker_factory=tracker_factory,
        processes_running=processes_running,
        activate=activate,
        launch=launch,
        monotonic=monotonic,
        sleep=sleep,
    )


def prepare_codex_window_for_work_overlay_switch(
    *,
    platform: str,
    launch_codex_app: Callable[..., bool],
    prepare_standalone: Callable[..., WindowResult],
    timeout_seconds: float,
    poll_seconds: float,
    launch_if_missing: bool,
) -> WindowResult:
    """Open the Codex UI before switching an overlay's target session."""
    if platform == "darwin":
        activated = launch_codex_app(debugger=True, cdp_port=cdp_port_from_env())
        return (
            bool(activated),
            "activated" if activated else "launch-failed",
            "" if activated else "macOS open failed",
            0,
        )
    return prepare_standalone(
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        launch_if_missing=launch_if_missing,
    )


def refocus_codex_window_after_work_overlay_switch(
    *,
    platform: str,
    launch_codex_app: Callable[..., bool],
    prepare_standalone: Callable[..., WindowResult],
    sleep: Callable[[float], None],
    delay_seconds: float,
    timeout_seconds: float,
    poll_seconds: float,
    launch_if_missing: bool,
) -> WindowResult:
    """Refocus Codex after an overlay session switch completes."""
    if delay_seconds > 0:
        sleep(delay_seconds)
    if platform == "darwin":
        activated = launch_codex_app(debugger=False)
        return (
            bool(activated),
            "activated" if activated else "launch-failed",
            "" if activated else "macOS open failed",
            0,
        )
    return prepare_standalone(
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        launch_if_missing=launch_if_missing,
    )


def refocus_codex_window_after_current_session_click(
    refocus: Callable[[], WindowResult],
) -> WindowResult:
    """Reuse the standard post-switch refocus action for current-session clicks."""
    return refocus()


__all__ = [
    "WindowResult",
    "prepare_codex_window_for_standalone",
    "prepare_codex_window_for_work_overlay_switch",
    "refocus_codex_window_after_current_session_click",
    "refocus_codex_window_after_work_overlay_switch",
]
