"""Development-time package shim for ``python -m codex_usage_hud`` from source."""

from __future__ import annotations

from pathlib import Path

__version__ = "1.2.0"

_SRC_PACKAGE = Path(__file__).resolve().parent.parent / "src" / "codex_usage_hud"
if _SRC_PACKAGE.is_dir():
    __path__.append(str(_SRC_PACKAGE))
