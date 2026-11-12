"""Read the generated UTF-8 renderer resource only during installation.

Python asset modules remain the editable source/contract surface. The running
HUD never imports that assembly graph or pins its large Unicode constants.
"""

from pathlib import Path

BUNDLE_PATH = Path(__file__).with_name("assets") / "renderer.bundle.js"


def read_renderer_template() -> str:
    return BUNDLE_PATH.read_text(encoding="utf-8")
