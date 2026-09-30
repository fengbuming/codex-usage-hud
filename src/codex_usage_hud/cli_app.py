"""Argument parsing and command dispatch for the codex-hud entry point."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import sys
from typing import Any

from . import __version__
from .config import (
    DEFAULT_BUDGET_THRESHOLDS,
    DEFAULT_DAILY_BUDGET_USD,
    DEFAULT_WEEKLY_BUDGET_USD,
    UserConfigStore,
    normalize_display_mode,
)
from .daemon import DEFAULT_DAEMON_POLL_MS, MAX_DAEMON_POLL_MS
from .updater import (
    check_for_update,
    download_update_asset,
    format_update_info,
    launch_installer,
)


DEFAULT_POLL_MS = 500
DEFAULT_ACTIVE_SESSION_POLL_MS = 500
DEFAULT_AUTO_SWITCH_IDLE_SECONDS = 30.0
DEFAULT_BUDGET_THRESHOLDS_TEXT = ",".join(
    f"{item:g}" for item in DEFAULT_BUDGET_THRESHOLDS
)


@dataclass(frozen=True)
class CliAppServices:
    run_daemon: Callable[[argparse.Namespace], int]
    run_once: Callable[[argparse.Namespace], int]
    stop: Callable[[], str]
    run_loading_helper: Callable[[str], int]
    run_overlay_helper: Callable[[str], int]
    cleanup_loading: Callable[[], object]
    cleanup_overlay: Callable[[], object]
    enable_crash_diagnostics: Callable[[], object]
    init_overlay_dependency_override: Callable[[], object]
    config_store_factory: Callable[[], object] = UserConfigStore
    parser_factory: Callable[[], argparse.ArgumentParser] | None = None
    update_check: Callable[[], int] | None = None
    update_install: Callable[[], int] | None = None
    detach_daemon: Callable[[Sequence[str]], int | None] | None = None
    resume_broker: Callable[[str, str], int] | None = None


def configure_stdout() -> None:
    """Prefer UTF-8 output without failing on detached Windows consoles."""
    stream = getattr(sys, "stdout", None)
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-hud",
        description="Local-first Codex usage HUD from local JSONL and SQLite logs.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Print the installed codex-usage-hud version and exit.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Print the current local snapshot and exit without opening the HUD.",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop the currently running HUD instance recorded by the local pid lock.",
    )
    parser.add_argument(
        "--check-update",
        action="store_true",
        help="Check GitHub Releases for a newer Windows installer and exit.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Download and launch the latest Windows installer when one is available.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help=(
            "Explicitly run the default persistent daemon: wait for Codex, "
            "show the HUD, and keep watching after Codex closes."
        ),
    )
    parser.add_argument(
        "--no-startup-prompt",
        action="store_true",
        help=(
            "Deprecated compatibility flag. Renderer startup no longer uses a "
            "modal startup choice."
        ),
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Use compact output mode for CLI snapshots.",
    )
    parser.set_defaults(renderer_hud=None)
    parser.add_argument(
        "--renderer-hud",
        dest="renderer_hud",
        action="store_true",
        help=(
            "Use the renderer-injected HUD when Codex exposes a local CDP target. "
            "Enabled by default."
        ),
    )
    parser.add_argument(
        "--hud-mode",
        choices=["renderer"],
        help="Override the configured HUD display mode for this run.",
    )
    parser.add_argument("--session-file", help="Optional exact session JSONL file to monitor.")
    parser.add_argument(
        "--session-id",
        help="Optional session id to pin instead of following the active Codex conversation.",
    )
    parser.add_argument(
        "--sessions-root",
        help="Optional override for the root directory containing Codex session JSONL files.",
    )
    parser.add_argument(
        "--sse-db",
        help="Optional override for the Codex SQLite OTel log database path.",
    )
    parser.add_argument(
        "--state-db",
        help="Optional override for the Codex state SQLite path used for active-session mapping.",
    )
    parser.add_argument(
        "--poll-ms",
        type=int,
        default=DEFAULT_POLL_MS,
        help=f"HUD refresh interval in milliseconds. Default: {DEFAULT_POLL_MS}.",
    )
    parser.add_argument(
        "--active-session-poll-ms",
        type=int,
        default=DEFAULT_ACTIVE_SESSION_POLL_MS,
        help=(
            "Polling interval for tracking the currently selected Codex conversation. "
            f"Default: {DEFAULT_ACTIVE_SESSION_POLL_MS}."
        ),
    )
    parser.add_argument(
        "--daemon-poll-ms",
        type=int,
        default=DEFAULT_DAEMON_POLL_MS,
        help=(
            "Windows daemon process polling interval in milliseconds. "
            f"Default: {DEFAULT_DAEMON_POLL_MS}; values above "
            f"{MAX_DAEMON_POLL_MS} are clamped."
        ),
    )
    parser.add_argument(
        "--auto-switch-idle-seconds",
        type=float,
        default=DEFAULT_AUTO_SWITCH_IDLE_SECONDS,
        help=(
            "When no conversation is selected explicitly, only switch to a newer "
            "mtime-based session after the current session has been idle this many "
            f"seconds. Default: {DEFAULT_AUTO_SWITCH_IDLE_SECONDS:g}."
        ),
    )
    parser.add_argument(
        "--no-follow-active-session",
        action="store_true",
        help="Disable best-effort tracking of the currently selected Codex conversation.",
    )
    parser.add_argument(
        "--legacy-active-session-diagnostics",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--no-sse",
        action="store_true",
        help="Disable SQLite SSE tracking and use JSONL-only fallback parsing.",
    )
    parser.add_argument(
        "--daily-budget-usd",
        type=float,
        default=None,
        help=(
            "Daily reminder budget in USD. "
            f"Configured default: {DEFAULT_DAILY_BUDGET_USD:g}."
        ),
    )
    parser.add_argument(
        "--weekly-budget-usd",
        type=float,
        default=None,
        help=(
            "Weekly reminder budget in USD. "
            f"Configured default: {DEFAULT_WEEKLY_BUDGET_USD:g}."
        ),
    )
    parser.add_argument(
        "--budget-thresholds",
        default=None,
        help=(
            "Comma-separated budget warning thresholds. "
            f"Configured default: {DEFAULT_BUDGET_THRESHOLDS_TEXT}."
        ),
    )
    parser.add_argument("--loading-feedback-helper", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--loading-feedback-state-file", default="", help=argparse.SUPPRESS)
    parser.add_argument("--work-overlay-helper", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--work-overlay-state-file", default="", help=argparse.SUPPRESS)
    parser.add_argument("--daemon-launch-broker", nargs=2, help=argparse.SUPPRESS)
    return parser


def run_update_check() -> int:
    info = check_for_update(current_version=__version__)
    print(format_update_info(info))
    return 1 if info.error else 0


def run_update_install() -> int:
    info = check_for_update(current_version=__version__)
    if info.error:
        print(format_update_info(info), file=sys.stderr)
        return 1
    if not info.available:
        print(format_update_info(info))
        return 0
    try:
        installer = download_update_asset(info)
        launch_installer(installer)
    except Exception as exc:
        print(f"Update install failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Launched {info.asset_name}. "
        "The installer will stop the running HUD before replacing files."
    )
    return 0


def run_once_snapshot(
    args: argparse.Namespace,
    *,
    context_factory: Callable[[argparse.Namespace], Any],
    snapshot_builder: Callable[[Any], object],
    snapshot_formatter: Callable[..., str],
) -> int:
    context = context_factory(args)
    try:
        if context.active_session_tracker is not None:
            context.active_session_tracker.wait_for_title()
        print(snapshot_formatter(snapshot_builder(context), compact=args.compact))
        return 0
    finally:
        context.close()


def main(
    argv: Sequence[str] | None = None,
    *,
    services: CliAppServices,
) -> int:
    configure_stdout()
    parser = (services.parser_factory or build_parser)()
    args = parser.parse_args(argv)
    if getattr(args, "daemon_launch_broker", None) is not None:
        if services.resume_broker is None:
            parser.error("independent daemon broker is unavailable")
        return services.resume_broker(*args.daemon_launch_broker)
    services.enable_crash_diagnostics()
    services.init_overlay_dependency_override()
    if getattr(args, "loading_feedback_helper", False):
        return services.run_loading_helper(args.loading_feedback_state_file)
    if getattr(args, "work_overlay_helper", False):
        return services.run_overlay_helper(args.work_overlay_state_file)
    services.cleanup_loading()
    services.cleanup_overlay()
    if args.check_update:
        return (services.update_check or run_update_check)()
    if args.update:
        return (services.update_install or run_update_install)()
    if args.renderer_hud is None and not getattr(args, "hud_mode", None):
        normalize_display_mode(services.config_store_factory().load().display_mode)
    args.hud_mode = "renderer"
    args.runtime_hud_mode = "renderer"
    args.standalone_hud_mode = None
    args.renderer_hud = True
    if args.stop:
        print(services.stop())
        return 0
    if args.daemon and args.once:
        parser.error("--daemon cannot be combined with --once")
    if args.once:
        return services.run_once(args)
    if services.detach_daemon is not None:
        launch_result = services.detach_daemon(list(sys.argv[1:] if argv is None else argv))
        if launch_result is not None:
            return launch_result
    return services.run_daemon(args)


__all__ = [
    "CliAppServices",
    "build_parser",
    "configure_stdout",
    "main",
    "run_once_snapshot",
    "run_update_check",
    "run_update_install",
]
