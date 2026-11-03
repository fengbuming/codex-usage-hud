#!/usr/bin/env python3
"""Run the project's staged closeout validation with timing feedback."""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import threading
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FOCUSED_TESTS = (
    "tests/test_renderer_contract_tool.py",
    "tests/test_validation_tool.py",
    "tests/test_push_check.py",
    "tests/test_speed_title_model.py",
    "tests/test_background_control.py",
    "tests/test_runtime_commands.py",
    "tests/test_settings_bridge.py",
    "tests/test_renderer_leaf_domains.py",
    "tests/test_renderer_assets.py",
    "tests/test_architecture.py",
)


def _display_command(command: list[str]) -> str:
    if sys.platform == "win32":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def _run_phase(command: list[str]) -> tuple[int, float]:
    started = time.perf_counter()
    completed = subprocess.Popen(command, cwd=ROOT)
    stop_heartbeat = threading.Event()

    def heartbeat() -> None:
        while not stop_heartbeat.wait(15.0):
            elapsed = time.perf_counter() - started
            print(f"  still running... ({elapsed:.0f}s)", flush=True)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        returncode = completed.wait()
    finally:
        stop_heartbeat.set()
        thread.join(timeout=1.0)
    return returncode, time.perf_counter() - started


def build_phases(
    *,
    focused_tests: tuple[str, ...] = DEFAULT_FOCUSED_TESTS,
    full: bool = False,
) -> list[tuple[str, list[str]]]:
    phases = [
        (
            "compileall",
            [sys.executable, "-m", "compileall", "-q", "src", "tests", "tools"],
        ),
        (
            "renderer-contract",
            [sys.executable, "tools/update_renderer_contract.py", "--check"],
        ),
        (
            "focused-tests",
            [sys.executable, "-m", "pytest", *focused_tests, "-q"],
        ),
    ]
    if full:
        phases.append(
            (
                "full-tests",
                [sys.executable, "-m", "pytest", "-q", "--durations=10"],
            )
        )
    return phases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run fast closeout checks first; pass --full to continue with all pytest tests."
        )
    )
    parser.add_argument(
        "tests",
        nargs="*",
        help="focused pytest paths/nodeids (defaults to the Renderer/settings set)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="run the full suite after focused tests pass",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Feature-specific tests supplement the shared regression gate.
    focused_tests = tuple(dict.fromkeys((*DEFAULT_FOCUSED_TESTS, *args.tests)))
    phases = build_phases(focused_tests=focused_tests, full=args.full)
    total = len(phases)
    print(f"Validation plan: {total} phase(s)")
    for index, (name, command) in enumerate(phases, start=1):
        print(f"\n[{index}/{total}] START {name}")
        print(f"  {_display_command(command)}", flush=True)
        returncode, elapsed = _run_phase(command)
        if returncode != 0:
            print(f"[{index}/{total}] FAIL {name} ({elapsed:.1f}s)")
            print("Validation stopped before later phases.")
            return returncode or 1
        print(f"[{index}/{total}] PASS {name} ({elapsed:.1f}s)")
    print("\nValidation completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
