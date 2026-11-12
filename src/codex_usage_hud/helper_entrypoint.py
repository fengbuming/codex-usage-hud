"""Dispatch UI helpers before importing the renderer composition root."""

from __future__ import annotations

from collections.abc import Sequence
import sys


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    helper_flags = {"--work-overlay-helper", "--loading-feedback-helper"}
    prior_actions = {
        "--cli-console-probe",
        "--daemon-launch-broker",
        "--provider-compatible-codex",
        "--provider-proxy-helper",
    }
    action_names = {argument.split("=", 1)[0] for argument in arguments}
    if helper_flags.isdisjoint(action_names) or not prior_actions.isdisjoint(action_names):
        from .runtime_orchestration import main as run_hud

        return run_hud(argv)

    # Use the same parser and helper owners as the regular CLI. This is only
    # an import boundary: flags, dependency overrides and diagnostics retain
    # their existing behavior, including frozen Windows/macOS launches.
    from .cli_app import build_parser, configure_stdout
    from .desktop_overlay_setup import (
        _init_force_desktop_overlay_missing_from_env,
        run_work_overlay_helper,
    )
    from .runtime_diagnostics import enable_crash_diagnostics

    configure_stdout()
    args = build_parser().parse_args(arguments)
    enable_crash_diagnostics()
    _init_force_desktop_overlay_missing_from_env()
    if args.loading_feedback_helper:
        from .loading_feedback import run_loading_feedback_helper

        return run_loading_feedback_helper(args.loading_feedback_state_file)
    return run_work_overlay_helper(args.work_overlay_state_file)
