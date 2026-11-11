"""Shared streaming readers for plain and Codex-compressed rollouts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import io
from pathlib import Path
from typing import TextIO


def resolve_rollout_path(path: Path) -> Path:
    """Resolve only the exact sibling left by Codex rollout compression.

    Codex can retain a .jsonl path in state_5.sqlite after replacing the file
    with .jsonl.zst. Prefer the recorded file whenever it still exists; never
    search other directories or guess another session/recorder identity.
    """
    path = Path(path)
    if path.is_file():
        return path
    name = path.name.casefold()
    if name.endswith(".jsonl"):
        sibling = path.with_name(path.name + ".zst")
    elif name.endswith(".jsonl.zst"):
        sibling = path.with_suffix("")
    else:
        return path
    return sibling if sibling.is_file() else path


@contextmanager
def open_rollout_text(path: Path) -> Iterator[TextIO]:
    """Open plain or zstd-compressed rollout data as a streaming text source."""

    path = resolve_rollout_path(Path(path))
    if path.suffix.casefold() != ".zst":
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            yield handle
        return

    try:
        from compression import zstd as stdlib_zstd
    except ImportError:
        stdlib_zstd = None

    if stdlib_zstd is not None:
        with stdlib_zstd.open(
            path,
            "rt",
            encoding="utf-8",
            errors="replace",
        ) as handle:
            yield handle
        return

    try:
        import zstandard
    except ImportError as exc:
        raise OSError(
            "zstandard support is required to read Codex .jsonl.zst rollouts"
        ) from exc

    with path.open("rb") as compressed:
        decompressed = zstandard.ZstdDecompressor().stream_reader(compressed)
        with decompressed:
            with io.TextIOWrapper(
                decompressed,
                encoding="utf-8",
                errors="replace",
            ) as handle:
                yield handle
