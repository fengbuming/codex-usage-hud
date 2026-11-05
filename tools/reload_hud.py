"""Run the source reload workflow from this checkout."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_usage_hud.development_reload import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
