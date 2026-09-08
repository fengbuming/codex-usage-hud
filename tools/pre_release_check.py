#!/usr/bin/env python3
"""Pre-release sanity check for codex-usage-hud releases."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REQUIRED_PATHS = (
    Path("pyproject.toml"),
    Path("LICENSE"),
    Path("CHANGELOG.md"),
    Path("README.md"),
    Path("README_EN.md"),
    Path("docs/PRIVACY.md"),
    Path("docs/RELEASE_PLAYBOOK.md"),
    Path("tools/installer/CodexUsageHud.iss"),
    Path("src/codex_usage_hud/__init__.py"),
    Path("codex_usage_hud/__init__.py"),
)
VERSION_PATHS = (
    Path("src/codex_usage_hud/__init__.py"),
    Path("codex_usage_hud/__init__.py"),
)
VERSION_RE = re.compile(r'^__version__\s*=\s*["\'](?P<version>\d+\.\d+\.\d+)["\']', re.MULTILINE)


def _use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _style(text: str, code: str) -> str:
    if not _use_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def _ok(text: str) -> str:
    return _style(text, "1;32")


def _warn(text: str) -> str:
    return _style(text, "1;33")


def _fail(text: str) -> str:
    return _style(text, "1;31")


def _info(text: str) -> str:
    return _style(text, "1;36")


def _read_version(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = VERSION_RE.search(text)
    return match.group("version") if match else None


def main() -> int:
    root = Path.cwd()

    print(_info("codex-usage-hud pre-release check"))
    print(f"Working directory: {root}")
    print()

    failures: list[str] = []
    for relative in REQUIRED_PATHS:
        candidate = root / relative
        if candidate.is_file():
            print(f"{_ok('[OK]')} {relative}")
        elif candidate.exists():
            print(f"{_warn('[WARN]')} {relative} exists but is not a file")
            failures.append(f"{relative} is not a file")
        else:
            print(f"{_fail('[MISS]')} {relative}")
            failures.append(f"{relative} is missing")

    versions = {relative: _read_version(root / relative) for relative in VERSION_PATHS}
    parsed_versions = {version for version in versions.values() if version}
    if None in versions.values():
        failures.append("package version is missing or is not MAJOR.MINOR.PATCH")
    elif len(parsed_versions) != 1:
        failures.append("package versions do not match")
    else:
        version = parsed_versions.pop()
        print(f"{_ok('[OK]')} package version {version}")
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        release_heading = re.compile(
            rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$",
            re.MULTILINE,
        )
        if release_heading.search(changelog):
            print(f"{_ok('[OK]')} CHANGELOG.md contains {version}")
        else:
            failures.append(f"CHANGELOG.md has no dated [{version}] section")
        release_notes = sorted(root.glob(f"RELEASE_NOTES_v{version}_*.md"))
        if release_notes:
            print(f"{_ok('[OK]')} {release_notes[0].name}")
        else:
            failures.append(f"RELEASE_NOTES_v{version}_*.md is missing")

    print()
    if failures:
        print(_fail("Pre-release check failed."))
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(_ok("Release files and version metadata are consistent."))
    print("Complete tests, commit, CI, installer, and checksum verification before publishing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
