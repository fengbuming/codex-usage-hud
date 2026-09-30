from __future__ import annotations

from pathlib import Path

import pytest

from codex_usage_hud.runtime_paths import (
    CODEX_APP_DEFAULT_ID,
    codex_app_executable_candidates,
    codex_app_shell_targets,
    crash_diagnostic_path,
    daemon_log_path,
    hud_lock_path,
    hud_program_root,
    hud_runtime_dir,
    renderer_cdp_state_path,
    renderer_diagnostic_path,
    runtime_file_path,
)


def test_hud_runtime_dir_preserves_platform_location_rules() -> None:
    home = Path("C:/Users/test")

    assert hud_runtime_dir(
        platform="win32",
        environ={"LOCALAPPDATA": "D:/Local"},
        home=home,
    ) == Path("D:/Local/codex-usage-hud")
    assert hud_runtime_dir(platform="win32", environ={}, home=home) == (
        home / "AppData" / "Local" / "codex-usage-hud"
    )
    assert hud_runtime_dir(platform="darwin", environ={}, home=Path("/Users/test")) == (
        Path("/Users/test/Library/Application Support/codex-usage-hud")
    )
    assert hud_runtime_dir(
        platform="linux",
        environ={"XDG_RUNTIME_DIR": "/run/user/42", "XDG_STATE_HOME": "/state"},
        home=Path("/home/test"),
    ) == Path("/run/user/42/codex-usage-hud")
    assert hud_runtime_dir(
        platform="linux",
        environ={"XDG_STATE_HOME": "/state"},
        home=Path("/home/test"),
    ) == Path("/state/codex-usage-hud")


def test_named_runtime_paths_share_the_explicit_runtime_root(tmp_path: Path) -> None:
    assert hud_lock_path(runtime_dir=tmp_path) == tmp_path / "codex_usage_hud.pid"
    assert renderer_diagnostic_path(runtime_dir=tmp_path) == (
        tmp_path / "renderer_fallback.log"
    )
    assert renderer_cdp_state_path(runtime_dir=tmp_path) == (
        tmp_path / "renderer_cdp_state.json"
    )
    assert crash_diagnostic_path(runtime_dir=tmp_path) == tmp_path / "crash.log"


def test_runtime_file_path_rejects_an_empty_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must name one file"):
        runtime_file_path("", runtime_dir=tmp_path)


def test_hud_program_root_uses_executable_or_source_project_root(tmp_path: Path) -> None:
    assert hud_program_root(
        frozen=True,
        executable=Path("D:/Apps/codex-usage-hud.exe"),
    ) == Path("D:/Apps").resolve()

    project = tmp_path / "project"
    module_file = project / "src" / "codex_usage_hud" / "runtime_paths.py"
    module_file.parent.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert hud_program_root(frozen=False, module_file=module_file) == project.resolve()


def test_daemon_log_path_keeps_override_and_legacy_non_windows_root() -> None:
    assert daemon_log_path(
        platform="win32",
        environ={"CODEX_USAGE_HUD_DAEMON_LOG": "D:/logs/hud.log"},
        home=Path("C:/Users/test"),
    ) == Path("D:/logs/hud.log")
    assert daemon_log_path(
        platform="darwin",
        environ={"XDG_STATE_HOME": "/state"},
        home=Path("/Users/test"),
    ) == Path("/state/codex-usage-hud/daemon.log")


def test_codex_shell_targets_keep_configured_values_ahead_of_default() -> None:
    targets = codex_app_shell_targets(
        environ={
            "CODEX_USAGE_HUD_CODEX_APP_ID": "Example.Codex!App",
            "CODEX_USAGE_HUD_CODEX_APP": "D:/Codex/ChatGPT.exe",
            "APPDATA": "D:/Roaming",
        }
    )

    assert targets[:3] == [
        "shell:AppsFolder\\Example.Codex!App",
        f"shell:AppsFolder\\{CODEX_APP_DEFAULT_ID}",
        "D:/Codex/ChatGPT.exe",
    ]
    assert targets[-3:] == [
        str(Path("D:/Roaming/Microsoft/Windows/Start Menu/Programs/Codex.lnk")),
        str(
            Path(
                "D:/Roaming/Microsoft/Windows/Start Menu/Programs/OpenAI Codex.lnk"
            )
        ),
        str(
            Path(
                "D:/Roaming/Microsoft/Windows/Start Menu/Programs/OpenAI/Codex.lnk"
            )
        ),
    ]


def test_codex_executable_candidates_prefer_registered_package_over_old_version(
    tmp_path: Path,
) -> None:
    program_files = tmp_path / "Program Files"
    old = program_files / "WindowsApps" / "OpenAI.Codex_26.901.0.0_x64__2p2nqsd0c76g0"
    current = program_files / "WindowsApps" / "OpenAI.Codex_26.928.1915.0_x64__2p2nqsd0c76g0"
    for package in (old, current):
        executable = package / "app" / "ChatGPT.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()
    candidates = codex_app_executable_candidates(
        appx_install_locations=[current],
        environ={"ProgramFiles": str(program_files)},
    )
    assert candidates == [current / "app" / "ChatGPT.exe", old / "app" / "ChatGPT.exe"]


def test_codex_executable_candidates_preserve_configured_and_relocated_priority(
    tmp_path: Path,
) -> None:
    configured = tmp_path / "configured" / "ChatGPT.exe"
    relocated = tmp_path / "local" / "Programs" / "CodexRelocated" / "app"
    appx = tmp_path / "appx"
    for path in (
        configured,
        relocated / "ChatGPT.exe",
        relocated / "Codex.exe",
        appx / "app" / "ChatGPT.exe",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    candidates = codex_app_executable_candidates(
        appx_install_locations=[appx],
        environ={
            "CODEX_USAGE_HUD_CODEX_APP": str(configured),
            "LOCALAPPDATA": str(tmp_path / "local"),
        },
    )

    assert candidates == [
        configured,
        relocated / "ChatGPT.exe",
        relocated / "Codex.exe",
        appx / "app" / "ChatGPT.exe",
    ]
