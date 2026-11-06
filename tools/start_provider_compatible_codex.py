"""Manual compatibility launcher for a source checkout."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from codex_usage_hud.provider_compatibility import main

if __name__ == "__main__":
    raise SystemExit(main())
