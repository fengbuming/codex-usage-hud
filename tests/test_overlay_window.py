from unittest.mock import MagicMock

from codex_usage_hud import overlay_window
from codex_usage_hud.platforms.cdp_probe import cdp_port_from_env


def test_prepare_standalone_forwards_platform_ports_without_side_effects() -> None:
    prepare = MagicMock(return_value=(True, "visible", "", 7))
    tracker_factory = MagicMock()
    processes_running = MagicMock()
    activate = MagicMock()
    launch = MagicMock()
    monotonic = MagicMock()
    sleep = MagicMock()

    result = overlay_window.prepare_codex_window_for_standalone(
        timeout_seconds=0.8,
        poll_seconds=0.08,
        launch_if_missing=True,
        prepare_window_for_renderer=prepare,
        tracker_factory=tracker_factory,
        processes_running=processes_running,
        activate=activate,
        launch=launch,
        monotonic=monotonic,
        sleep=sleep,
    )

    assert result == (True, "visible", "", 7)
    prepare.assert_called_once_with(
        timeout_seconds=0.8,
        poll_seconds=0.08,
        launch_if_missing=True,
        cdp_port=cdp_port_from_env(),
        tracker_factory=tracker_factory,
        processes_running=processes_running,
        activate=activate,
        launch=launch,
        monotonic=monotonic,
        sleep=sleep,
    )


def test_prepare_switch_uses_mac_os_activation_without_window_polling() -> None:
    launch = MagicMock(return_value=True)
    prepare = MagicMock()

    result = overlay_window.prepare_codex_window_for_work_overlay_switch(
        platform="darwin",
        launch_codex_app=launch,
        prepare_standalone=prepare,
        timeout_seconds=0.8,
        poll_seconds=0.08,
        launch_if_missing=True,
    )

    assert result == (True, "activated", "", 0)
    launch.assert_called_once_with(debugger=True, cdp_port=cdp_port_from_env())
    prepare.assert_not_called()


def test_refocus_switch_waits_then_delegates_non_mac_window_prepare() -> None:
    sleep = MagicMock()
    prepare = MagicMock(return_value=(True, "visible", "", 9))
    launch = MagicMock()

    result = overlay_window.refocus_codex_window_after_work_overlay_switch(
        platform="win32",
        launch_codex_app=launch,
        prepare_standalone=prepare,
        sleep=sleep,
        delay_seconds=0.08,
        timeout_seconds=0.8,
        poll_seconds=0.08,
        launch_if_missing=True,
    )

    assert result == (True, "visible", "", 9)
    sleep.assert_called_once_with(0.08)
    prepare.assert_called_once_with(
        timeout_seconds=0.8,
        poll_seconds=0.08,
        launch_if_missing=True,
    )
    launch.assert_not_called()
