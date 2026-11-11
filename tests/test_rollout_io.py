"""Exact compression-sibling lookup, without broad session/path guessing."""

import pytest

from codex_usage_hud.core.rollout_io import open_rollout_text, resolve_rollout_path


def compress_rollout(payload: bytes) -> bytes:
    try:
        from compression import zstd
    except ImportError:
        import zstandard

        return zstandard.ZstdCompressor().compress(payload)
    return zstd.compress(payload)


def test_stale_plain_path_reads_exact_compressed_sibling(tmp_path):
    plain = tmp_path / "rollout-session_recorder.jsonl"
    compressed = plain.with_name(plain.name + ".zst")
    text = '{"message":"Linux.do & IDCFlare 回帖足迹"}\n'
    compressed.write_bytes(compress_rollout(text.encode("utf-8")))
    assert resolve_rollout_path(plain) == compressed
    with open_rollout_text(plain) as handle:
        assert handle.read() == text


def test_recorded_existing_file_wins_and_plain_restore_is_supported(tmp_path):
    plain = tmp_path / "rollout.jsonl"
    compressed = plain.with_name(plain.name + ".zst")
    plain.write_text("plain\n", encoding="utf-8")
    compressed.write_bytes(compress_rollout(b"compressed\n"))
    assert resolve_rollout_path(plain) == plain
    assert resolve_rollout_path(compressed) == compressed
    compressed.unlink()
    assert resolve_rollout_path(compressed) == plain
    with open_rollout_text(compressed) as handle:
        assert handle.read() == "plain\n"


def test_missing_file_does_not_guess_another_recorder_or_directory(tmp_path):
    expected = tmp_path / "rollout-session_recorder.jsonl"
    (tmp_path / "rollout-session_other.jsonl.zst").write_bytes(b"wrong identity")
    elsewhere = tmp_path / "archived"
    elsewhere.mkdir()
    (elsewhere / (expected.name + ".zst")).write_bytes(b"wrong directory")
    assert resolve_rollout_path(expected) == expected
    with pytest.raises(FileNotFoundError):
        with open_rollout_text(expected):
            pass
    unknown = tmp_path / "unrelated.txt"
    unknown.with_name(unknown.name + ".zst").write_bytes(b"not a rollout")
    assert resolve_rollout_path(unknown) == unknown


def test_month_range_uses_actual_compressed_file_time(tmp_path):
    import os
    from codex_usage_hud.core.session_search import range_candidates

    now = 1_800_000_000
    old = tmp_path / "old.jsonl"
    recent = tmp_path / "recent.jsonl"
    for path, days in ((old, 40), (recent, 10)):
        compressed = path.with_name(path.name + ".zst")
        compressed.write_bytes(compress_rollout(b"{}\n"))
        stamp = now - days * 86400
        os.utime(compressed, (stamp, stamp))
    entries = [(path.stem, [path], "", "", "", "") for path in (old, recent)]
    assert [entry[0] for entry in range_candidates(entries, "1m", now=now)] == [
        "recent"
    ]
