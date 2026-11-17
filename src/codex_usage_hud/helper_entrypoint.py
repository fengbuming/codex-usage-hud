"""Dispatch UI helpers before importing the renderer composition root."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import os
import sys


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Frozen proxy helpers use this entry too. Keep their detached service
    # independent of the renderer imports, as the source-module entry already is.
    if "--provider-proxy-helper" in arguments and "--provider-compatible-codex" not in arguments:
        from .provider_proxy_service import main as run_proxy

        return run_proxy(arguments)
    helper_flags = {"--work-overlay-helper", "--loading-feedback-helper"}
    prior_actions = {
        "--cli-console-probe",
        "--daemon-launch-broker",
        "--provider-compatible-codex",
        "--provider-proxy-helper",
    }
    action_names = {argument.split("=", 1)[0] for argument in arguments}
    if helper_flags.isdisjoint(action_names) or not prior_actions.isdisjoint(action_names):
        if (
            os.environ.get("CODEX_USAGE_HUD_MEMORY_PROFILE", "").strip().lower()
            in {"1", "true", "yes", "on"}
            and "--multiprocessing-fork" not in arguments
        ):
            from .runtime_memory_profile import start_if_enabled

            start_if_enabled()
        from .runtime_orchestration import main as run_hud

        return run_hud(argv)

    # Parse only helper flags so detached UI processes do not import the
    # coordinator CLI, daemon, or updater modules.
    parser = argparse.ArgumentParser(prog="codex-hud")
    parser.add_argument("--work-overlay-helper", action="store_true")
    parser.add_argument("--work-overlay-state-file", default="")
    parser.add_argument("--loading-feedback-helper", action="store_true")
    parser.add_argument("--loading-feedback-state-file", default="")
    args = parser.parse_args(arguments)
    from .runtime_diagnostics import enable_crash_diagnostics

    enable_crash_diagnostics()
    if args.loading_feedback_helper:
        from .loading_feedback import run_loading_feedback_helper

        return run_loading_feedback_helper(args.loading_feedback_state_file)
    from .desktop_overlay_setup import (
        _init_force_desktop_overlay_missing_from_env,
        run_work_overlay_helper,
    )

    _init_force_desktop_overlay_missing_from_env()
    return run_work_overlay_helper(args.work_overlay_state_file)
