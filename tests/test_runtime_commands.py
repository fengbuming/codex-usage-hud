from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from unittest.mock import patch

from codex_usage_hud.runtime_commands import (
    GeneralCommandPorts,
    RuntimeCommandPorts,
    _handle_renderer_settings_command,
    handle_background_command,
    handle_active_session_command,
    handle_cleanup_command,
    handle_session_index_command,
    handle_insights_command,
    dispatch_command,
)
from codex_usage_hud.config import UserConfig


TARGET_ID = "10000000-0000-4000-8000-000000000012"


def test_renderer_resume_transfer_target_builds_verified_resume_launch(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    (tmp_path / "sessions").mkdir()
    manager = MagicMock()
    manager.workdir_for_transfer_target.return_value = workdir
    context = SimpleNamespace(
        app_provider="custom",
        sessions_root=tmp_path / "sessions",
        state_db_path=tmp_path / "state_5.sqlite",
        session_cleanup_manager=manager,
    )

    with (
        patch(
            "codex_usage_hud.runtime_commands.discover_codex_cli_options",
            return_value={
                "profile": "routin",
                "defaultProvider": "custom",
                "defaultTerminal": "windows-terminal",
                "terminals": [
                    {
                        "id": "windows-terminal",
                        "shell": "cmd",
                    }
                ],
                "proxy": {"enabled": False, "port": 7897},
            },
        ) as discover,
        patch(
            "codex_usage_hud.runtime_commands.build_codex_cli_command",
            return_value="codex --profile routin resume " + TARGET_ID,
        ) as build,
        patch(
            "codex_usage_hud.runtime_commands.launch_codex_cli",
            return_value={"pid": 42, "terminal": "Windows Terminal"},
        ) as launch,
    ):
        status = _handle_renderer_settings_command(
            {
                "action": "codexCliLaunch",
                "requestId": "resume-1",
                "provider": "routin",
                "sessionTransferResumeId": TARGET_ID,
            },
            context,
            MagicMock(),
            MagicMock(),
        )

    manager.workdir_for_transfer_target.assert_called_once_with(TARGET_ID, "routin")
    discover.assert_called_once()
    build.assert_called_once()
    build_kwargs = build.call_args.kwargs
    assert build_kwargs["resume"] is True
    assert build_kwargs["resume_session_id"] == TARGET_ID
    assert build_kwargs["provider"] == "routin"
    assert build_kwargs["shell"] == "cmd"
    launch.assert_called_once()
    assert launch.call_args.kwargs["codex_home"] == tmp_path
    assert status["kind"] == ""
    assert status["codexCliLaunch"]["sessionTransferResumeId"] == TARGET_ID


def test_renderer_no_project_launch_uses_neutral_home_workdir() -> None:
    context = SimpleNamespace(app_provider="custom")

    with patch(
        "codex_usage_hud.runtime_commands.launch_codex_cli",
        return_value={"pid": 43, "terminal": "PowerShell"},
    ) as launch:
        status = _handle_renderer_settings_command(
            {
                "action": "codexCliLaunch",
                "requestId": "no-project-1",
                "provider": "custom",
                "terminalId": "powershell7",
                "command": "codex --help",
                "noProject": True,
            },
            context,
            MagicMock(),
            MagicMock(),
        )

    assert status["kind"] == ""
    assert launch.call_args.kwargs["workdir"] == str(Path.home())


@pytest.mark.parametrize(
    ("handler", "command", "ports", "field"),
    [
        (
            handle_cleanup_command,
            {"action": "sessionCleanupScan", "requestId": "cleanup-1"},
            RuntimeCommandPorts(cleanup_worker=None),
            "sessionCleanupRequestId",
        ),
        (
            handle_insights_command,
            {"action": "usageInsightsRefresh", "requestId": "insights-1"},
            RuntimeCommandPorts(insights_worker=None),
            "usageInsightsRequestId",
        ),
    ],
)
def test_command_errors_keep_request_correlation(
    handler, command, ports, field: str
) -> None:
    status = handler(command, ports)

    assert status[field] == command["requestId"]
    assert status["kind"] == "error"


def test_session_index_status_keeps_request_correlation() -> None:
    job = SimpleNamespace(
        attach=MagicMock(),
        status=MagicMock(return_value={"jobState": "idle", "coverage": "range_done(1m)"}),
    )

    status = handle_session_index_command(
        {"action": "sessionIndexStatus", "requestId": "index-status-1"},
        RuntimeCommandPorts(session_index_job=job),
    )

    assert status["sessionIndex"]["requestId"] == "index-status-1"
    job.attach.assert_called_once_with()


@pytest.mark.parametrize(
    ("control", "method", "argument"),
    [
        ("enable", "set_enabled", True),
        ("disable", "set_enabled", False),
    ],
)
def test_session_index_feature_switch_is_forwarded(
    control: str,
    method: str,
    argument: bool,
) -> None:
    job = SimpleNamespace(
        set_enabled=MagicMock(return_value=True),
        status=MagicMock(
            return_value={"enabled": argument, "jobState": "idle", "diskBytes": 7}
        ),
    )

    status = handle_session_index_command(
        {"action": "sessionIndexControl", "control": control, "requestId": "switch-1"},
        RuntimeCommandPorts(session_index_job=job),
    )

    job.set_enabled.assert_called_once_with(argument, "")
    assert status["sessionIndex"]["accepted"] is True
    assert status["sessionIndex"]["enabled"] is argument
    assert status["sessionIndex"]["diskBytes"] == 7


def test_session_index_clear_returns_clear_result() -> None:
    job = SimpleNamespace(
        clear_index=MagicMock(
            return_value={
                "accepted": True,
                "error": "",
                "clearedBytes": 123,
                "removedFiles": 4,
            }
        ),
        status=MagicMock(
            return_value={"enabled": True, "coverage": "empty", "diskBytes": 0}
        ),
    )

    status = handle_session_index_command(
        {"action": "sessionIndexControl", "control": "clear", "requestId": "clear-1"},
        RuntimeCommandPorts(session_index_job=job),
    )

    job.clear_index.assert_called_once_with()
    assert status["sessionIndex"]["accepted"] is True
    assert status["sessionIndex"]["clearedBytes"] == 123
    assert status["sessionIndex"]["removedFiles"] == 4


def test_session_index_clear_publishes_reset_session_cleanup_payload() -> None:
    job = SimpleNamespace(
        clear_index=MagicMock(return_value={"accepted": True, "error": ""}),
        status=MagicMock(return_value={"coverage": "empty", "diskBytes": 0}),
    )
    manager = SimpleNamespace(
        snapshot=MagicMock(
            return_value={"search": {"indexState": "idle", "matches": []}}
        )
    )
    worker = SimpleNamespace(_publish=MagicMock())

    status = handle_session_index_command(
        {"action": "sessionIndexControl", "control": "clear", "requestId": "clear-2"},
        RuntimeCommandPorts(
            session_index_job=job,
            cleanup_manager=manager,
            cleanup_worker=worker,
        ),
    )

    assert status["sessionIndex"]["accepted"] is True
    manager.snapshot.assert_called_once_with()
    worker._publish.assert_called_once_with(manager.snapshot.return_value)


def test_active_session_candidate_command_binds_exact_id() -> None:
    received: list[dict[str, object]] = []

    status = handle_active_session_command(
        {
            "action": "resolveActiveSession",
            "sessionId": "thread-2",
            "selectionSeq": 9,
            "requestId": "candidate-1",
        },
        RuntimeCommandPorts(
            resolve_active_session=lambda command: received.append(dict(command))
            or True
        ),
    )

    assert received[0]["sessionId"] == "thread-2"
    assert received[0]["selectionSeq"] == 9
    assert status["message"] == "已按你的选择匹配当前未归档会话。"


def test_background_exception_keeps_request_and_response_kind() -> None:
    runtime = SimpleNamespace(query=MagicMock(side_effect=RuntimeError("boom")))

    status = handle_background_command(
        {"action": "backgroundUsageQuery", "requestId": "background-1"},
        RuntimeCommandPorts(background_usage=runtime),
    )

    response = status["backgroundUsageResponse"]
    assert response["requestId"] == "background-1"
    assert response["kind"] == "query"
    assert "boom" in response["error"]


def test_background_policy_set_uses_only_normal_policy_path() -> None:
    runtime = SimpleNamespace(
        policy_set=MagicMock(
            return_value={
                "featureKey": "memory_consolidation",
                "verificationState": "verified",
                "effectiveState": "disabled",
            }
        ),
    )

    status = handle_background_command(
        {
            "action": "backgroundUsagePolicySet",
            "requestId": "policy-1",
            "featureKey": "memory_consolidation",
            "desiredState": "disabled",
            "expectedPolicyRevision": 4,
        },
        RuntimeCommandPorts(background_usage=runtime),
    )

    runtime.policy_set.assert_called_once_with(
        "memory_consolidation", "disabled", 4, "", "usage_detail"
    )
    response = status["backgroundUsageResponse"]
    assert response["requestId"] == "policy-1"
    assert response["kind"] == "policyApply"
    assert response["payload"]["verificationState"] == "verified"


def test_usage_insights_workdir_opens_only_payload_directory(tmp_path: Path) -> None:
    session_id = "10000000-0000-4000-8000-000000000001"
    payload = {
        "week": {
            "topSessionsByUsage": [
                {"sessionId": session_id, "workdir": str(tmp_path)}
            ]
        }
    }

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_insights_command(
            {"action": "openUsageInsightsWorkdir", "sessionId": session_id},
            RuntimeCommandPorts(insights_payload=payload),
        )

    opener.assert_called_once_with(tmp_path)
    assert status["kind"] == ""
    assert status["message"] == "已打开工作目录。"


def test_usage_insights_workdir_rejects_missing_or_unlisted_directory(tmp_path: Path) -> None:
    session_id = "10000000-0000-4000-8000-000000000001"
    payload = {"week": {"topSessionsByUsage": [{"sessionId": session_id, "workdir": str(tmp_path / "missing")} ]}}

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_insights_command(
            {"action": "openUsageInsightsWorkdir", "sessionId": session_id},
            RuntimeCommandPorts(insights_payload=payload),
        )

    opener.assert_not_called()
    assert status["kind"] == "error"


def test_usage_insights_workdir_rejects_empty_or_relative_directory() -> None:
    session_id = "10000000-0000-4000-8000-000000000001"
    payload = {"week": {"topSessionsByUsage": [{"sessionId": session_id, "workdir": ""}]}}

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_insights_command(
            {"action": "openUsageInsightsWorkdir", "sessionId": session_id},
            RuntimeCommandPorts(insights_payload=payload),
        )

    opener.assert_not_called()
    assert status["kind"] == "error"


def test_session_cleanup_workdir_opens_manager_resolved_directory(tmp_path: Path) -> None:
    manager = SimpleNamespace(workdir_for_item=MagicMock(return_value=tmp_path))

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_cleanup_command(
            {
                "action": "openSessionCleanupWorkdir",
                "itemId": "session-opaque-id",
                "inventoryRevision": "1-opaque-revision",
                "cwd": str(tmp_path / "untrusted"),
            },
            RuntimeCommandPorts(cleanup_manager=manager),
        )

    manager.workdir_for_item.assert_called_once_with(
        "session-opaque-id", "1-opaque-revision"
    )
    opener.assert_called_once_with(tmp_path)
    assert status["kind"] == ""
    assert status["message"] == "已打开工作目录。"
    assert "backgroundUsageResponse" not in status


def test_session_cleanup_workdir_options_stay_in_settings_response() -> None:
    manager = SimpleNamespace(
        workdir_options=MagicMock(
            return_value=[
                {
                    "id": "opaque-workdir",
                    "label": "project",
                    "available": True,
                    "sessionCount": 2,
                }
            ]
        )
    )

    status = handle_cleanup_command(
        {
            "action": "sessionCleanupWorkdirOptions",
            "requestId": "workdir-options-1",
        },
        RuntimeCommandPorts(cleanup_manager=manager),
    )

    manager.workdir_options.assert_called_once_with()
    assert status["sessionCleanupWorkdirOptions"]["requestId"] == "workdir-options-1"
    assert status["sessionCleanupWorkdirOptions"]["options"][0]["id"] == "opaque-workdir"
    assert status["sessionCleanupWorkdirOptions"]["detailed"] is False


@pytest.mark.parametrize("workdir", [None, Path("relative-workdir")])
def test_session_cleanup_workdir_rejects_invalid_manager_directory(workdir: object) -> None:
    manager = SimpleNamespace(workdir_for_item=MagicMock(return_value=workdir))

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_cleanup_command(
            {
                "action": "openSessionCleanupWorkdir",
                "itemId": "session-opaque-id",
                "inventoryRevision": "1-opaque-revision",
            },
            RuntimeCommandPorts(cleanup_manager=manager),
        )

    opener.assert_not_called()
    assert status["kind"] == "error"
    assert status["message"] == "该会话没有可打开的工作目录。"


def test_session_cleanup_workdir_reports_manager_read_failure() -> None:
    manager = SimpleNamespace(workdir_for_item=MagicMock(side_effect=RuntimeError("offline")))

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_cleanup_command(
            {
                "action": "openSessionCleanupWorkdir",
                "itemId": "session-opaque-id",
                "inventoryRevision": "1-opaque-revision",
            },
            RuntimeCommandPorts(cleanup_manager=manager),
        )

    opener.assert_not_called()
    assert status["kind"] == "error"
    assert status["message"] == "无法读取会话工作目录：offline"


def test_background_usage_workdir_opens_current_event_directory(tmp_path: Path) -> None:
    event_id = "10000000-0000-4000-8000-000000000002"
    runtime = SimpleNamespace(
        detail=MagicMock(return_value={"eventId": event_id, "cwd": str(tmp_path)}),
        confirm=MagicMock(),
    )

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_background_command(
            {
                "action": "openBackgroundUsageWorkdir",
                "eventId": event_id,
                "cwd": str(tmp_path / "untrusted"),
            },
            RuntimeCommandPorts(background_usage=runtime),
        )

    runtime.detail.assert_called_once_with(event_id)
    runtime.confirm.assert_not_called()
    opener.assert_called_once_with(tmp_path)
    assert status["kind"] == ""
    assert status["message"] == "已打开工作目录。"
    assert "backgroundUsageResponse" not in status


@pytest.mark.parametrize(
    "detail_value",
    [
        None,
        {"eventId": "10000000-0000-4000-8000-000000000002", "cwd": ""},
        {
            "eventId": "10000000-0000-4000-8000-000000000002",
            "cwd": "relative-directory",
        },
    ],
)
def test_background_usage_workdir_rejects_unavailable_or_invalid_directory(
    detail_value: object,
) -> None:
    event_id = "10000000-0000-4000-8000-000000000002"
    runtime = SimpleNamespace(detail=detail_value)

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_background_command(
            {"action": "openBackgroundUsageWorkdir", "eventId": event_id},
            RuntimeCommandPorts(background_usage=runtime),
        )

    opener.assert_not_called()
    assert status["kind"] == "error"
    assert status["message"] == "该后台任务没有可打开的工作目录。"
    assert "backgroundUsageResponse" not in status


def test_background_usage_workdir_rejects_nonexistent_or_mismatched_event_directory(
    tmp_path: Path,
) -> None:
    event_id = "10000000-0000-4000-8000-000000000002"
    other_event_id = "10000000-0000-4000-8000-000000000003"

    for detail_payload in (
        {"eventId": event_id, "cwd": str(tmp_path / "missing")},
        {"eventId": other_event_id, "cwd": str(tmp_path)},
    ):
        runtime = SimpleNamespace(detail=MagicMock(return_value=detail_payload))
        with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
            status = handle_background_command(
                {"action": "openBackgroundUsageWorkdir", "eventId": event_id},
                RuntimeCommandPorts(background_usage=runtime),
            )

        opener.assert_not_called()
        assert status["kind"] == "error"
        assert status["message"] == "该后台任务没有可打开的工作目录。"
        assert "backgroundUsageResponse" not in status


def test_background_usage_workdir_reports_detail_read_failure() -> None:
    event_id = "10000000-0000-4000-8000-000000000002"
    runtime = SimpleNamespace(detail=MagicMock(side_effect=RuntimeError("offline")))

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_background_command(
            {"action": "openBackgroundUsageWorkdir", "eventId": event_id},
            RuntimeCommandPorts(background_usage=runtime),
        )

    opener.assert_not_called()
    assert status["kind"] == "error"
    assert status["message"] == "无法读取后台任务工作目录：offline"
    assert "backgroundUsageResponse" not in status


def test_general_command_status_keeps_request_and_action() -> None:
    config = UserConfig.defaults()
    ports = GeneralCommandPorts(
        load_config=lambda: config,
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
    )

    status = dispatch_command(
        {"action": "dismissWarningsToday", "requestId": "settings-1"},
        RuntimeCommandPorts(),
        ports,
    )

    assert status["requestId"] == "settings-1"
    assert status["action"] == "dismissWarningsToday"


def test_general_command_can_request_codex_restart() -> None:
    config = UserConfig.defaults()
    restart_codex = MagicMock()
    ports = GeneralCommandPorts(
        load_config=lambda: config,
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        request_restart_codex=restart_codex,
    )

    status = dispatch_command(
        {"action": "restartCodex", "requestId": "codex-restart-1"},
        RuntimeCommandPorts(),
        ports,
    )

    restart_codex.assert_called_once_with()
    assert status["requestId"] == "codex-restart-1"
    assert status["action"] == "restartCodex"
    assert "Codex Desktop" in status["message"]


def test_general_provider_save_prompts_for_default_codex_restart() -> None:
    config = UserConfig.defaults()
    save_provider = MagicMock(
        return_value={
            "changed": True,
            "providerIds": ["custom"],
            "defaultProviderEdited": True,
        }
    )
    ports = GeneralCommandPorts(
        load_config=lambda: config,
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        save_codex_providers=save_provider,
    )

    status = dispatch_command(
        {
            "action": "save",
            "requestId": "provider-save-1",
            "settings": config.to_dict(),
            "codexProviders": [{"provider_id": "custom"}],
        },
        RuntimeCommandPorts(),
        ports,
    )

    save_provider.assert_called_once()
    assert status["requestId"] == "provider-save-1"
    assert status["restartVisible"] is True
    assert status["restartCodex"] is True
    assert "重启 Codex Desktop" in status["message"]


def test_general_provider_set_default_dispatches_without_restart_prompt() -> None:
    config = UserConfig.defaults()
    set_default = MagicMock(
        return_value={"changed": True, "providerId": "hiyo", "name": "Hiyo Free"}
    )
    ports = GeneralCommandPorts(
        load_config=lambda: config,
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        set_default_provider=set_default,
    )

    status = dispatch_command(
        {"action": "providerSetDefault", "provider": "hiyo", "requestId": "switch-1"},
        RuntimeCommandPorts(),
        ports,
    )

    set_default.assert_called_once_with("hiyo")
    assert status["requestId"] == "switch-1"
    assert status["action"] == "providerSetDefault"
    assert status["providerSetDefaultProvider"] == "hiyo"
    assert "Hiyo Free" in status["message"]
    assert status.get("restartVisible") is not True
    assert status.get("restartCodex") is not True


def test_general_provider_set_default_surfaces_failure() -> None:
    config = UserConfig.defaults()

    def failing(provider):
        raise ValueError("供应商「nope」未在 config.toml 中定义")

    ports = GeneralCommandPorts(
        load_config=lambda: config,
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        set_default_provider=failing,
    )

    status = dispatch_command(
        {"action": "providerSetDefault", "provider": "nope", "requestId": "switch-2"},
        RuntimeCommandPorts(),
        ports,
    )

    assert status["action"] == "providerSetDefault"
    assert status["kind"] == "error"
    assert "未在 config.toml" in status["message"]


def test_general_provider_save_without_default_change_does_not_prompt_restart() -> None:
    config = UserConfig.defaults()
    ports = GeneralCommandPorts(
        load_config=lambda: config,
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        save_codex_providers=lambda updates: {
            "changed": True,
            "providerIds": ["muyuan"],
            "defaultProviderEdited": False,
        },
    )

    status = dispatch_command(
        {
            "action": "save",
            "requestId": "provider-save-2",
            "settings": config.to_dict(),
            "codexProviders": [{"provider_id": "muyuan"}],
        },
        RuntimeCommandPorts(),
        ports,
    )

    assert status["restartVisible"] is False
    assert "muyuan" in status["message"]
    assert "重启 Codex Desktop" not in status["message"]


def test_general_command_dispatches_provider_delete() -> None:
    deleted: list[dict[str, object]] = []
    config = UserConfig.defaults()
    ports = GeneralCommandPorts(
        load_config=lambda: config,
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        delete_provider=lambda command: deleted.append(dict(command)) or {
            "status": "ok",
            "providerId": "muyuan",
        },
    )

    status = dispatch_command(
        {"action": "deleteProvider", "provider": "muyuan", "requestId": "delete-1"},
        RuntimeCommandPorts(),
        ports,
    )

    assert deleted == [
        {"action": "deleteProvider", "provider": "muyuan", "requestId": "delete-1"}
    ]
    assert status["status"] == "ok"
    assert status["providerId"] == "muyuan"
    assert status["requestId"] == "delete-1"


def test_general_command_dispatches_fetch_provider_models() -> None:
    calls: list[tuple[str, str]] = []
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        fetch_provider_models=lambda base, key: calls.append((base, key)) or ["gpt-5", "gpt-5.6"],
    )

    status = dispatch_command(
        {
            "action": "fetchProviderModels",
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-secret",
            "requestId": "models-1",
        },
        RuntimeCommandPorts(),
        ports,
    )

    assert calls == [("https://api.example.com/v1", "sk-secret")]
    assert status["providerConnected"] is True
    assert status["models"] == ["gpt-5", "gpt-5.6"]
    assert status["requestId"] == "models-1"


def test_general_command_fetch_provider_models_surfaces_errors() -> None:
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        fetch_provider_models=lambda base, key: (_ for _ in ()).throw(
            ValueError("API key 不能为空。")
        ),
    )

    status = dispatch_command(
        {"action": "fetchProviderModels", "baseUrl": "https://x.example/v1", "apiKey": ""},
        RuntimeCommandPorts(),
        ports,
    )

    assert status["kind"] == "error"


def test_general_command_dispatches_codex_cli_fetch_models() -> None:
    received: list[str] = []
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        fetch_cli_provider_models=lambda provider: received.append(provider) or {
            "provider": provider,
            "baseUrl": "https://api.example.com/v1",
            "envKey": "MY_API_KEY",
            "models": ["gpt-5", "gpt-5.6"],
        },
    )

    status = dispatch_command(
        {
            "action": "codexCliFetchModels",
            "provider": "muyuan",
            "requestId": "cli-models-1",
        },
        RuntimeCommandPorts(),
        ports,
    )

    assert received == ["muyuan"]
    assert status["codexCliModels"]["models"] == ["gpt-5", "gpt-5.6"]
    assert status["codexCliModels"]["envKey"] == "MY_API_KEY"
    assert status["requestId"] == "cli-models-1"


def test_general_command_codex_cli_fetch_models_surfaces_errors() -> None:
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        fetch_cli_provider_models=lambda provider: (_ for _ in ()).throw(
            ValueError("用户环境变量中没有可用的 API key。")
        ),
    )

    status = dispatch_command(
        {"action": "codexCliFetchModels", "provider": "muyuan"},
        RuntimeCommandPorts(),
        ports,
    )

    assert status["kind"] == "error"
    assert status["message"] == "用户环境变量中没有可用的 API key。"


def test_general_command_dispatches_provider_chat_test() -> None:
    calls: list[tuple[str, str, str, str]] = []
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        send_provider_chat_probe=lambda base, key, model, message: calls.append(
            (base, key, model, message)
        ) or {"ok": True, "reply": "Hello!", "model": model},
    )

    status = dispatch_command(
        {
            "action": "providerChatTest",
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-secret",
            "model": "Deepseek-v4-flash",
            "message": "hi",
            "requestId": "chat-1",
        },
        RuntimeCommandPorts(),
        ports,
    )

    assert calls == [
        ("https://api.example.com/v1", "sk-secret", "Deepseek-v4-flash", "hi")
    ]
    assert status["providerChatTest"]["ok"] is True
    assert status["providerChatTest"]["reply"] == "Hello!"
    assert status["requestId"] == "chat-1"


def test_general_command_provider_chat_test_surfaces_failure() -> None:
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        send_provider_chat_probe=lambda base, key, model, message: {
            "ok": False,
            "error": "聊天测试失败（HTTP 400）：invalid model",
        },
    )

    status = dispatch_command(
        {
            "action": "providerChatTest",
            "baseUrl": "https://x.example/v1",
            "apiKey": "key",
            "model": "bad-model",
        },
        RuntimeCommandPorts(),
        ports,
    )

    assert status["kind"] == "error"
    assert "invalid model" in str(status["message"])


def test_general_command_dispatches_codex_cli_chat_test() -> None:
    calls: list[tuple[str, str, str]] = []
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        send_cli_chat_probe=lambda provider, model, message: calls.append(
            (provider, model, message)
        ) or {
            "ok": True,
            "reply": "Hello!",
            "model": model,
            "provider": provider,
        },
    )

    status = dispatch_command(
        {
            "action": "codexCliChatTest",
            "provider": "qq",
            "model": "Deepseek-v4-flash",
            "requestId": "cli-chat-1",
        },
        RuntimeCommandPorts(),
        ports,
    )

    assert calls == [("qq", "Deepseek-v4-flash", "hi")]
    assert status["codexCliChatTest"]["ok"] is True
    assert status["codexCliChatTest"]["provider"] == "qq"
    assert status["requestId"] == "cli-chat-1"


def test_general_command_codex_cli_chat_test_surfaces_failure() -> None:
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        send_cli_chat_probe=lambda provider, model, message: {
            "ok": False,
            "error": "用户环境变量 QQ_API_KEY 中没有可用的 API key。",
        },
    )

    status = dispatch_command(
        {
            "action": "codexCliChatTest",
            "provider": "qq",
            "model": "Deepseek-v4-flash",
        },
        RuntimeCommandPorts(),
        ports,
    )

    assert status["kind"] == "error"
    assert "QQ_API_KEY" in str(status["message"])
