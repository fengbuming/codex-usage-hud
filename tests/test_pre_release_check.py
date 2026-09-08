from __future__ import annotations

from pathlib import Path

from tools import pre_release_check


def _write_release_tree(root: Path, version: str = "1.2.0") -> None:
    for relative in pre_release_check.REQUIRED_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative in pre_release_check.VERSION_PATHS:
            path.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
        elif relative == Path("CHANGELOG.md"):
            path.write_text(f"## [{version}] - 2026-09-08\n", encoding="utf-8")
        else:
            path.write_text("release fixture\n", encoding="utf-8")


def test_pre_release_check_validates_versioned_metadata(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_release_tree(tmp_path)
    (tmp_path / "RELEASE_NOTES_v1.2.0_TEST.md").write_text("notes\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert pre_release_check.main() == 0
    output = capsys.readouterr().out
    assert "Release files and version metadata are consistent." in output
    assert "safely execute git push" not in output


def test_pre_release_check_rejects_missing_release_notes(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_release_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert pre_release_check.main() == 1
    assert "RELEASE_NOTES_v1.2.0_*.md is missing" in capsys.readouterr().out
