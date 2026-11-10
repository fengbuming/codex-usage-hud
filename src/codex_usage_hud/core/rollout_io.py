"""Shared streaming readers for plain and Codex-compressed rollouts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import io
from pathlib import Path
from typing import TextIO


@contextmanager
def open_rollout_text(path: Path) -> Iterator[TextIO]:
    """Open plain or zstd-compressed rollout data as a streaming text source."""

    path = Path(path)
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
