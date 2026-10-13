"""Unit tests for the Windows exe build helper."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = PROJECT_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import build_exe  # noqa: E402


class BootstrapSourceTests(unittest.TestCase):
    def test_bootstrap_entry_uses_package_main(self) -> None:
        source = build_exe.bootstrap_source()

        self.assertIn("from codex_usage_hud.cli import main", source)
        self.assertIn("raise SystemExit(main())", source)
        self.assertNotIn("from .cli import main", source)

    def test_write_bootstrap_entry_creates_expected_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "entry.py"

            written = build_exe.write_bootstrap_entry(path)

            self.assertEqual(written, path)
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(encoding="utf-8"), build_exe.bootstrap_source())


class PyInstallerCommandTests(unittest.TestCase):
    def test_command_keeps_analysis_pinned_to_src_and_excludes_repo_junk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            build_root = temp_root / "build"
            dist_root = temp_root / "dist"
            entry_script = build_root / build_exe.BOOTSTRAP_FILENAME

            command = build_exe.build_pyinstaller_command(
                python_executable=Path(r"C:\Python\python.exe"),
                entry_script=entry_script,
                src_root=PROJECT_ROOT / "src",
                dist_root=dist_root,
                build_root=build_root,
            )

            self.assertEqual(command[:4], [r"C:\Python\python.exe", "-m", "PyInstaller", "--noconfirm"])
            self.assertIn("--onefile", command)
            self.assertIn("--noconsole", command)
            self.assertIn("--collect-submodules", command)
            self.assertIn("codex_usage_hud", command)
            self.assertIn("--add-data", command)
            self.assertIn("--icon", command)
            self.assertTrue(
                any("hud-app-icon.ico" in item for item in command),
                msg=command,
            )
            self.assertTrue(
                any("sponsor_alipay.jpg" in item for item in command),
                msg=command,
            )
            self.assertTrue(
                any("codex_usage_hud/assets" in item for item in command),
                msg=command,
            )
            self.assertIn("--paths", command)
            self.assertIn(str(PROJECT_ROOT / "src"), command)
            self.assertIn("--distpath", command)
            self.assertIn(str(dist_root), command)
            self.assertIn("--workpath", command)
            self.assertIn(str(build_root / "work"), command)
            self.assertIn("--specpath", command)
            self.assertIn(str(build_root / "spec"), command)
            self.assertIn("--exclude-module", command)
            self.assertIn("tests", command)
            self.assertIn("docs", command)
            self.assertIn("tools", command)
            self.assertIn("pytest", command)
            self.assertIn("PyQt5", command)
            self.assertIn("PyQt6", command)
            self.assertIn("PySide2", command)
            self.assertIn("codex_usage_hud.platforms.linux", command)
            self.assertIn("codex_usage_hud.platforms.macos", command)
            self.assertNotIn("--hidden-import", command)
            self.assertNotIn("tkinter.font", command)
            self.assertNotIn("PySide6", command)
            self.assertEqual(command[-1], str(entry_script))

    def test_format_command_returns_copy_pasteable_string(self) -> None:
        text = build_exe.format_command(["python", "-m", "PyInstaller", "--name", "codex-hud"])

        self.assertIn("PyInstaller", text)
        self.assertIn("--name", text)
        self.assertIn("codex-hud", text)

    def test_build_environment_pins_submodule_discovery_to_selected_source(self) -> None:
        source_root = PROJECT_ROOT / "src"
        with patch.dict(
            os.environ,
            {"PYTHONHOME": "foreign-home", "PYTHONPATH": "foreign-checkout"},
        ):
            environment = build_exe.build_environment(source_root=source_root)

        self.assertNotIn("PYTHONHOME", environment)
        self.assertEqual(environment["PYTHONPATH"], str(source_root.resolve()))
        self.assertEqual(environment["PYTHONNOUSERSITE"], "1")

    def test_windows_build_environment_excludes_foreign_native_toolchains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            windows_root = Path(temp_dir) / "Windows"
            with patch.object(build_exe.sys, "platform", "win32"), patch.dict(
                os.environ, {"PATH": "foreign-poppler-bin", "SystemRoot": str(windows_root)}
            ):
                environment = build_exe.build_environment()
        self.assertNotIn("foreign-poppler-bin", environment["PATH"])
        paths = environment["PATH"].split(os.pathsep)
        self.assertIn(str(windows_root / "System32"), paths)
        self.assertIn(str(Path(sys.executable).resolve().parent), paths)


if __name__ == "__main__":
    unittest.main()
