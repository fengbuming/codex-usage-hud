"""Unit tests for the Inno Setup installer build helper."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = PROJECT_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import build_installer  # noqa: E402


class InstallerBuildHelperTests(unittest.TestCase):
    def test_installer_stops_any_running_hud_before_replacing_files(self) -> None:
        script = build_installer.DEFAULT_INNO_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('DestName: "codex-hud-stop.exe"; Flags: dontcopy', script)
        self.assertIn("ExtractTemporaryFile('codex-hud-stop.exe')", script)
        self.assertIn("CheckForMutexes('Local\\codex_usage_hud_single_instance')", script)
        self.assertIn("ewWaitUntilTerminated, ResultCode", script)
        self.assertIn("ResultCode <> 0", script)
        self.assertIn("旧 HUD 进程仍在运行", script)
        self.assertIn("CloseApplications=force", script)
        self.assertIn("RestartApplications=no", script)

    def test_setup_base_filename_uses_versioned_windows_setup_convention(self) -> None:
        self.assertEqual(
            build_installer.setup_base_filename("1.0.0"),
            "codex-usage-hud-v1.0.0-windows-x64-setup",
        )

    def test_inno_define_quotes_string_values(self) -> None:
        self.assertEqual(
            build_installer.inno_define("AppVersion", "1.0.0"),
            "/DAppVersion=1.0.0",
        )

    def test_build_inno_command_passes_expected_defines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            command = build_installer.build_inno_command(
                iscc=temp / "ISCC.exe",
                script=temp / "CodexUsageHud.iss",
                version="1.0.0",
                source_exe=temp / "codex-hud.exe",
                output_dir=temp / "dist",
            )

        self.assertEqual(command[0].endswith("ISCC.exe"), True)
        self.assertIn("/DAppVersion=1.0.0", command)
        self.assertTrue(any(item.startswith("/DSourceExe=") for item in command))
        self.assertTrue(command[-1].endswith("CodexUsageHud.iss"))

    def test_write_installer_checksum_uses_release_filename_and_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "codex-usage-hud-v1.0.0-windows-x64-setup.exe"
            installer.write_bytes(b"installer-payload")

            checksum = build_installer.write_installer_checksum(installer)

            self.assertEqual(checksum.name, f"{installer.name}.sha256")
            self.assertEqual(
                checksum.read_text(encoding="ascii"),
                "fbc5fd97006521785cd1aa58917a4e2999e66d835748400dcb47e1df5e5a8226  "
                f"{installer.name}\n",
            )


if __name__ == "__main__":
    unittest.main()
