"""Validate exported push tips, never the possibly dirty working tree."""

from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def push_commits(lines: str) -> list[str]:
    commits = []
    for line in lines.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4 or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", fields[1]):
            raise ValueError("Malformed pre-push input")
        sha = fields[1]
        if set(sha) != {"0"} and sha not in commits:
            commits.append(sha)
    return commits


def check_commit(revision: str, *, root: Path = ROOT) -> int:
    # Resolve tags and reject non-commit objects before using them in commands.
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
        cwd=root, text=True,
    ).strip()
    archive = subprocess.check_output(["git", "archive", "--format=zip", commit], cwd=root)
    with tempfile.TemporaryDirectory(prefix="hud-push-check-") as directory:
        snapshot = Path(directory)
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            bundle.extractall(snapshot)
        validation = snapshot / "tools/run_validation.py"
        if not validation.is_file():
            print(f"Push blocked: {commit[:12]} has no validation entry point.", flush=True)
            return 1
        env = os.environ.copy()
        # Git sets these for hooks; subprocesses in the snapshot must not use
        # this checkout's index or worktree. Do not inherit an editable source path.
        for key in (
            "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        ):
            env.pop(key, None)
        env["PYTHONPATH"] = os.pathsep.join((str(snapshot / "src"), str(snapshot)))
        print(f"Validating push commit {commit[:12]} in a clean snapshot...", flush=True)
        result = subprocess.run([sys.executable, str(validation)], cwd=snapshot, env=env, check=False)
        if result.returncode:
            print("Push blocked. Fix the reported failure, commit the fix, and push again.", flush=True)
        return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("remote", nargs="?", help="Git's remote name")
    parser.add_argument("url", nargs="?", help="Git's remote URL")
    parser.add_argument("--revision", help="check one local revision without pushing")
    args = parser.parse_args(argv)
    try:
        commits = [args.revision] if args.revision else push_commits(sys.stdin.read())
        for commit in commits:
            result = check_commit(commit)
            if result:
                return result
    except (OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"Push check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
