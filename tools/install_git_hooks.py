"""Enable the repository's push gate for this checkout."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    current = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"], cwd=ROOT,
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    if current and current != ".githooks":
        print(f"Existing core.hooksPath={current}; integrate the gate there before changing it.")
        return 1
    if not current:
        hook_path = subprocess.check_output(
            ["git", "rev-parse", "--git-path", "hooks"], cwd=ROOT, text=True,
        ).strip()
        existing = [path for path in (ROOT / hook_path).glob("*") if path.is_file() and not path.name.endswith(".sample")]
        if existing:
            print("Existing Git hooks are installed; integrate them before switching hooksPath.")
            return 1
    hook = ROOT / ".githooks/pre-push"
    hook.chmod(hook.stat().st_mode | 0o111)
    subprocess.run(["git", "config", "--local", "hud.validationPython", sys.executable], cwd=ROOT, check=True)
    subprocess.run(["git", "config", "--local", "core.hooksPath", ".githooks"], cwd=ROOT, check=True)
    print(f"Push checks enabled with {sys.executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
