#!/usr/bin/env python3
"""Build a single-file, no-console Windows executable for codex-usage-hud.

The build stays pinned to ``src/`` so repo-root noise such as ``tests/``,
``docs/``, ``tools/``, ``.venv/``, ``__pycache__/``, ``.git/``, ``*.sqlite``,
and ``*.log`` never enters PyInstaller's analysis graph.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DEFAULT_BUILD_ROOT = PROJECT_ROOT / "build" / "pyinstaller"
DEFAULT_DIST_ROOT = PROJECT_ROOT / "dist"
BOOTSTRAP_FILENAME = "_codex_hud_exe_entry.py"
DEFAULT_EXE_NAME = "codex-hud"
DEFAULT_ICON_FILE = PROJECT_ROOT / "icon" / "hud-app-icon.ico"
DEFAULT_LOG_LEVEL = "WARN"

# The package is tiny, but we still keep the graph tight and explicit:
# - collect our own package submodules for reliability across conditional imports
# - explicitly include package data files used by bundled support QR codes
# - exclude Linux/macOS platform branches from the Windows build
# - exclude dev/test helper module names so they cannot be pulled in accidentally
DEFAULT_COLLECT_PACKAGES = ("codex_usage_hud",)
DEFAULT_DATA_FILES = (
    (
        PROJECT_ROOT / "src" / "codex_usage_hud" / "assets" / "sponsor_alipay.jpg",
        "codex_usage_hud/assets",
    ),
    (
        PROJECT_ROOT / "src" / "codex_usage_hud" / "assets" / "sponsor_wechat.jpg",
        "codex_usage_hud/assets",
    ),
    (
        PROJECT_ROOT / "src" / "codex_usage_hud" / "assets" / "sponsor_alipay.png",
        "codex_usage_hud/assets",
    ),
    (
        PROJECT_ROOT / "src" / "codex_usage_hud" / "assets" / "sponsor_wechat.png",
        "codex_usage_hud/assets",
    ),
    (
        DEFAULT_ICON_FILE,
        "codex_usage_hud/assets",
    ),
)
DEFAULT_EXCLUDED_MODULES = (
    "tests",
    "docs",
    "tools",
    "pytest",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "codex_usage_hud.platforms.linux",
    "codex_usage_hud.platforms.macos",
)

# PySide6 Addons modules are not used by the desktop work-overlay helper
# (it only needs QtCore/QtGui/QtWidgets/Network from Essentials). Excluding
# them keeps the PyInstaller bundle slim (~25MB vs ~52MB full Addons).
DEFAULT_EXCLUDED_QT_MODULES = (
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQml",
    "PySide6.QtQmlModels",
    "PySide6.QtQmlWorkerScript",
    "PySide6.QtQmlMeta",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSensors",
    "PySide6.QtLocation",
    "PySide6.QtPositioning",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtSql",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtPrintSupport",
    "PySide6.QtTest",
    "PySide6.QtDBus",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtUiTools",
    "PySide6.QtNetworkAuth",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtTextToSpeech",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtConcurrent",
    "PySide6.QtCore5Compat",
)
# QtCore/QtGui/QtWidgets/QtNetwork must NOT be excluded: they are the
# Essentials modules the desktop work-overlay helper actually imports.


def _resolve_project_path(value: Path) -> Path:
    """Resolve ``value`` relative to the repository root when needed."""
    if value.is_absolute():
        return value
    return (PROJECT_ROOT / value).resolve()


def bootstrap_source() -> str:
    """Return the tiny top-level entry script used by PyInstaller."""
    return (
        '"""PyInstaller bootstrap for codex_usage_hud."""\n\n'
        "from codex_usage_hud.cli import main\n\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    )


def write_bootstrap_entry(path: Path) -> Path:
    """Write the PyInstaller bootstrap script and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bootstrap_source(), encoding="utf-8")
    return path


def build_pyinstaller_command(
    *,
    python_executable: Path,
    entry_script: Path,
    src_root: Path,
    dist_root: Path,
    build_root: Path,
    name: str = DEFAULT_EXE_NAME,
    icon_path: Path | None = DEFAULT_ICON_FILE,
    log_level: str = DEFAULT_LOG_LEVEL,
    collect_packages: Sequence[str] = DEFAULT_COLLECT_PACKAGES,
    data_files: Sequence[tuple[Path, str]] = DEFAULT_DATA_FILES,
    excluded_modules: Sequence[str] = DEFAULT_EXCLUDED_MODULES,
    excluded_qt_modules: Sequence[str] = DEFAULT_EXCLUDED_QT_MODULES,
) -> list[str]:
    """Return the exact PyInstaller command used for the Windows exe build."""
    command = [
        str(python_executable),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name",
        name,
        "--distpath",
        str(dist_root),
        "--workpath",
        str(build_root / "work"),
        "--specpath",
        str(build_root / "spec"),
        "--paths",
        str(src_root),
        "--log-level",
        log_level.upper(),
    ]
    for package in collect_packages:
        command.extend(["--collect-submodules", package])
    if icon_path is not None:
        command.extend(["--icon", str(icon_path)])
    for source, destination in data_files:
        command.extend(["--add-data", pyinstaller_data_spec(source, destination)])
    for module in excluded_modules:
        command.extend(["--exclude-module", module])
    for module in excluded_qt_modules:
        command.extend(["--exclude-module", module])
    command.append(str(entry_script))
    return command


def pyinstaller_data_spec(source: Path, destination: str) -> str:
    """Return a platform-correct PyInstaller --add-data value."""
    separator = ";" if os.name == "nt" else ":"
    return f"{source}{separator}{destination}"


def format_command(command: Sequence[str]) -> str:
    """Render a shell-friendly command string for dry runs and logging."""
    if os.name == "nt":
        return subprocess.list2cmdline(list(command))
    return shlex.join(command)


def build_environment(*, source_root: Path | None = None) -> dict[str, str]:
    """Return a small, source-pinned environment for build subprocesses."""
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    if source_root is not None:
        # ``--paths`` is applied during Analysis, after PyInstaller evaluates
        # the generated spec's ``collect_submodules`` call. Pin that earlier
        # discovery too, otherwise an editable checkout elsewhere on the host
        # can leak extra module names into an otherwise isolated worktree build.
        env["PYTHONPATH"] = str(Path(source_root).resolve())
    env["PYTHONNOUSERSITE"] = "1"
    if sys.platform.startswith("win"):
        # Dependency analysis also searches PATH for native DLLs. Unrelated
        # toolchains (e.g. Poppler's versioned ICU) can shadow Windows ICU and
        # produce an executable whose QtCore cannot load. Keep only the active
        # Python runtime and Windows directories in the build search path.
        python_root = Path(sys.executable).resolve().parent
        windows_env = {name.casefold(): value for name, value in env.items()}
        windows_root = Path(windows_env.get("systemroot") or windows_env.get("windir") or r"C:\Windows")
        env["PATH"] = os.pathsep.join(str(path) for path in dict.fromkeys((
            python_root, python_root / "Scripts", python_root / "DLLs",
            Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs",
            windows_root / "System32", windows_root,
            windows_root / "System32" / "Wbem",
        )))
    return env


def ensure_pyinstaller(python_executable: Path, *, bootstrap: bool) -> None:
    """Ensure PyInstaller is importable in the active Python environment."""
    if importlib.util.find_spec("PyInstaller") is not None:
        return
    if not bootstrap:
        raise RuntimeError(
            "PyInstaller is not installed in this environment. "
            "Run `python -m pip install PyInstaller` and retry."
        )

    print("[build] PyInstaller not found; installing into the active environment...")
    try:
        subprocess.run(
            [str(python_executable), "-m", "pip", "install", "PyInstaller"],
            cwd=str(PROJECT_ROOT),
            env=build_environment(),
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Unable to install PyInstaller automatically. "
            "Run `python -m pip install PyInstaller` and retry."
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser for the build helper."""
    parser = argparse.ArgumentParser(
        description=(
            "Build a single-file Windows exe for codex-usage-hud with PyInstaller."
        )
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=DEFAULT_BUILD_ROOT,
        help="Directory used for PyInstaller work and spec files.",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_ROOT,
        help="Output directory for the final codex-hud.exe.",
    )
    parser.add_argument(
        "--name",
        default=DEFAULT_EXE_NAME,
        help="Executable base name. Default: codex-hud.",
    )
    parser.add_argument(
        "--no-bootstrap-pyinstaller",
        action="store_true",
        help="Fail instead of auto-installing PyInstaller into the active environment.",
    )
    parser.add_argument(
        "--no-exclude-qt",
        action="store_true",
        help="Do not exclude PySide6 Addons modules; bundle the full Qt runtime.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the PyInstaller command without running it.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Build ``dist/codex-hud.exe`` or print the command in dry-run mode."""
    args = build_parser().parse_args(argv)
    build_root = _resolve_project_path(args.build_dir)
    dist_root = _resolve_project_path(args.dist_dir)
    entry_script = build_root / BOOTSTRAP_FILENAME
    command = build_pyinstaller_command(
        python_executable=Path(sys.executable),
        entry_script=entry_script,
        src_root=SRC_ROOT,
        dist_root=dist_root,
        build_root=build_root,
        name=args.name,
        excluded_qt_modules=() if args.no_exclude_qt else DEFAULT_EXCLUDED_QT_MODULES,
    )

    if args.dry_run:
        print("[dry-run] PyInstaller command:")
        print(format_command(command))
        print(f"[dry-run] Expected output: {dist_root / f'{args.name}.exe'}")
        return 0

    if not sys.platform.startswith("win"):
        print(
            "This build helper is Windows-only; use it on Windows to produce codex-hud.exe.",
            file=sys.stderr,
        )
        return 1

    output_exe = dist_root / f"{args.name}.exe"
    try:
        ensure_pyinstaller(
            Path(sys.executable),
            bootstrap=not args.no_bootstrap_pyinstaller,
        )
        write_bootstrap_entry(entry_script)

        build_root.mkdir(parents=True, exist_ok=True)
        dist_root.mkdir(parents=True, exist_ok=True)

        try:
            output_exe.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

        print("[build] Running PyInstaller...")
        print(format_command(command))
        subprocess.run(
            command,
            cwd=str(build_root),
            env=build_environment(source_root=SRC_ROOT),
            check=True,
        )

        if not output_exe.exists():
            raise RuntimeError(
                f"Build finished but the expected output is missing: {output_exe}"
            )
    except subprocess.CalledProcessError as exc:
        print(
            f"[ERROR] PyInstaller failed with exit code {exc.returncode}",
            file=sys.stderr,
        )
        return exc.returncode or 1
    except (OSError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(f"[build] Created {output_exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
