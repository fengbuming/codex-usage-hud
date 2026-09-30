from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from codex_usage_hud import cli_app


def _services(**changes: object) -> cli_app.CliAppServices:
    values: dict[str, object] = {
        "run_daemon": MagicMock(return_value=0),
        "run_once": MagicMock(return_value=0),
        "stop": MagicMock(return_value="stopped"),
        "run_loading_helper": MagicMock(return_value=0),
        "run_overlay_helper": MagicMock(return_value=0),
        "cleanup_loading": MagicMock(),
        "cleanup_overlay": MagicMock(),
        "enable_crash_diagnostics": MagicMock(),
        "init_overlay_dependency_override": MagicMock(),
        "config_store_factory": lambda: SimpleNamespace(
            load=lambda: SimpleNamespace(display_mode="renderer")
        ),
    }
    values.update(changes)
    return cli_app.CliAppServices(**values)  # type: ignore[arg-type]


def test_parser_keeps_renderer_only_legacy_alias_contract() -> None:
    args = cli_app.build_parser().parse_args(["--hud-mode", "renderer"])

    assert args.hud_mode == "renderer"
    assert args.renderer_hud is None


def test_main_once_normalizes_runtime_to_renderer() -> None:
    run_once = MagicMock(return_value=7)
    services = _services(run_once=run_once)

    result = cli_app.main(["--once"], services=services)

    assert result == 7
    args = run_once.call_args.args[0]
    assert args.hud_mode == "renderer"
    assert args.runtime_hud_mode == "renderer"
    assert args.renderer_hud is True
    services.run_daemon.assert_not_called()


def test_main_stop_does_not_start_daemon(capsys: object) -> None:
    services = _services()

    assert cli_app.main(["--stop"], services=services) == 0

    assert capsys.readouterr().out.strip() == "stopped"
    services.stop.assert_called_once_with()
    services.run_daemon.assert_not_called()


def test_main_default_uses_persistent_daemon() -> None:
    services = _services()

    assert cli_app.main([], services=services) == 0

    services.run_daemon.assert_called_once()
    services.run_once.assert_not_called()


def test_main_detaches_only_persistent_daemon() -> None:
    for argv in (["--once"], ["--stop"], ["--work-overlay-helper"]):
        detach = MagicMock(return_value=0)
        cli_app.main(argv, services=_services(detach_daemon=detach))
        detach.assert_not_called()
    detach = MagicMock(return_value=0)
    services = _services(detach_daemon=detach)
    assert cli_app.main(["--daemon"], services=services) == 0
    detach.assert_called_once_with(["--daemon"])
    services.run_daemon.assert_not_called()


def test_main_continues_in_independent_child() -> None:
    services = _services(detach_daemon=MagicMock(return_value=None))
    assert cli_app.main(["--daemon"], services=services) == 0
    services.run_daemon.assert_called_once()


def test_broker_restores_environment_before_initializing_services() -> None:
    resume = MagicMock(return_value=7)
    services = _services(resume_broker=resume)
    assert cli_app.main(["--daemon-launch-broker", "pipe", "token"], services=services) == 7
    resume.assert_called_once_with("pipe", "token")
    services.enable_crash_diagnostics.assert_not_called()
    services.cleanup_overlay.assert_not_called()
    services.run_daemon.assert_not_called()
