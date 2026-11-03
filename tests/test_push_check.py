"""The push gate checks committed content and propagates failures."""

from pathlib import Path
import subprocess
import sys

import pytest

from tools.check_push import check_commit, main, push_commits


def test_push_input_deduplicates_tips_and_ignores_deletions():
    sha, zero = "a" * 40, "0" * 40
    assert push_commits(
        f"refs/heads/main {sha} refs/heads/main {zero}\n"
        f"refs/tags/v1 {sha} refs/tags/v1 {zero}\n"
        f"(delete) {zero} refs/heads/old {sha}\n"
    ) == [sha]
    with pytest.raises(ValueError):
        push_commits("invalid input")


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "user.name=Push Check Test", "-c", "user.email=push-check@example.invalid", *args],
        cwd=root, text=True,
    ).strip()


@pytest.mark.parametrize("exit_code", [0, 7])
def test_snapshot_ignores_uncommitted_edits_and_uses_its_own_imports(tmp_path, exit_code):
    _git(tmp_path, "init", "-q")
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "push_probe.py").write_text("VALUE = 'committed'\n", encoding="utf-8")
    validator = tools_dir / "run_validation.py"
    output = tmp_path / "result.txt"
    validator.write_text(
        "from pathlib import Path\nimport push_probe\n"
        "assert push_probe.VALUE == 'committed'\n"
        "assert not Path('untracked.txt').exists()\n"
        f"Path({str(output)!r}).write_text(str(Path.cwd()))\n"
        f"raise SystemExit({exit_code})\n", encoding="utf-8",
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "snapshot")
    validator.write_text("raise SystemExit(99)\n", encoding="utf-8")
    (source_dir / "push_probe.py").write_text("VALUE = 'dirty'\n", encoding="utf-8")
    (tmp_path / "untracked.txt").touch()
    assert check_commit("HEAD", root=tmp_path) == exit_code
    snapshot = Path(output.read_text())
    assert snapshot != tmp_path
    assert not snapshot.exists(), "temporary snapshot must be cleaned on success and failure"


def test_missing_validation_in_commit_blocks_push(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "README.md").touch()
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "no gate")
    assert check_commit("HEAD", root=tmp_path) == 1


def test_hook_reports_failure_and_checks_every_tip(monkeypatch):
    import io
    import tools.check_push as gate

    tips = ["a" * 40, "b" * 40]
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(
        f"refs/heads/branch-{n} {tip} refs/heads/branch-{n} {'0' * 40}"
        for n, tip in enumerate(tips)
    )))
    checked = []

    def check(tip):
        checked.append(tip)
        return 5 if tip == tips[1] else 0

    monkeypatch.setattr(gate, "check_commit", check)
    assert main(["origin", "https://example.invalid/repo"]) == 5
    assert checked == tips


def test_installer_preserves_an_existing_custom_hook(tmp_path):
    from tools import install_git_hooks
    from unittest.mock import patch

    _git(tmp_path, "init", "-q")
    existing = tmp_path / ".git/hooks/pre-push"
    existing.write_text("existing hook", encoding="utf-8")
    with patch.object(install_git_hooks, "ROOT", tmp_path):
        assert install_git_hooks.main() == 1
    assert existing.read_text() == "existing hook"
