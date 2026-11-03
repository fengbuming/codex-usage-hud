from __future__ import annotations

import sys

from tools.run_validation import DEFAULT_FOCUSED_TESTS, build_phases, main


def test_validation_defaults_to_fast_focused_closeout() -> None:
    phases = build_phases()
    assert [name for name, _ in phases] == [
        "compileall",
        "renderer-contract",
        "focused-tests",
    ]
    assert phases[-1][1] == [sys.executable, "-m", "pytest", *DEFAULT_FOCUSED_TESTS, "-q"]
    assert "tests/test_renderer_contract_tool.py" in DEFAULT_FOCUSED_TESTS


def test_validation_full_mode_appends_timed_full_suite() -> None:
    phases = build_phases(full=True, focused_tests=("tests/test_renderer_hud.py",))
    assert [name for name, _ in phases] == [
        "compileall",
        "renderer-contract",
        "focused-tests",
        "full-tests",
    ]
    assert phases[-1][1][-1] == "--durations=10"


def test_feature_tests_supplement_baseline_and_failure_stops_later_phases(monkeypatch):
    import tools.run_validation as validation

    seen = []

    def run(command):
        seen.append(command)
        return (3 if "pytest" in command else 0), 0.0

    monkeypatch.setattr(validation, "_run_phase", run)
    assert main(["tests/test_renderer_application_menu.py", "--full"]) == 3
    assert len(seen) == 3
    assert set(DEFAULT_FOCUSED_TESTS).issubset(seen[-1])
    assert "tests/test_renderer_application_menu.py" in seen[-1]
