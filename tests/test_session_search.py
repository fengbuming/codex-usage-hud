from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

from codex_usage_hud.core import session_search as session_search_module
from codex_usage_hud.core.session_search import SessionSearchIndex, parse_rollout
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor


def test_rollout_search_reads_late_content_beyond_two_megabytes(tmp_path: Path) -> None:
    rollout = tmp_path / "large.jsonl"
    late_marker = "late-content-marker"
    rollout.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "x" * (3 * 1024 * 1024),
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"text": late_marker}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    index = SessionSearchIndex(tmp_path / "search.sqlite")
    index.upsert("session", (rollout,))

    assert index.search(late_marker)["matches"]


def test_search_works_without_fts5(tmp_path: Path) -> None:
    rollout = tmp_path / "fallback.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "fallback-marker"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    index = SessionSearchIndex(tmp_path / "fallback.sqlite")
    index._fts_available = False
    index.upsert("session", (rollout,))

    assert index.search("fallback-marker")["matches"]


def test_read_tool_paths_are_not_reported_as_modified_files(tmp_path: Path) -> None:
    rollout = tmp_path / "read.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "read_file",
                    "arguments": json.dumps(
                        {"path": "E:/Project/codex-usage-hud/src/active_session.py"}
                    ),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    _user, _assistant, _tool, changed_paths = parse_rollout((rollout,))

    assert not changed_paths


def test_giant_change_record_skips_broad_path_scan(tmp_path: Path) -> None:
    """A multi-megabyte change record must not explode parse_rollout.

    Real rollouts occasionally contain a single 2MB+ ``item_completed`` line whose
    embedded tool output would make ``_PATH_TOKEN_RE`` match tens of thousands of
    junk fragments (separator lines, escaped paths) over several seconds. The
    real changed-file paths live in the structured ``_PATH_KEYS`` fields and the
    authoritative ``*** Update File:`` blocks (captured cheaply by
    ``_PATCH_TARGET_RE``). The broad scan is skipped above ``_PATH_TOKEN_MAX_LINE``.
    """

    import time

    real_path = "E:/real/edited_module.py"
    # ~2.1MB of separator-like noise; if scanned this yields thousands of garbage
    # path fragments and pushes parse time into multiple seconds.
    junk = ("-" * 40 + "\\\n") * 50000
    item = {
        "type": "file_change",
        "file_path": real_path,
        "output": f"*** Update File: {real_path}\n{junk}",
    }
    record = {
        "type": "event_msg",
        "payload": {"type": "item_completed", "item": item},
    }
    rollout = tmp_path / "giant.jsonl"
    rollout.write_text(json.dumps(record) + "\n", encoding="utf-8")

    start = time.perf_counter()
    _user, _assistant, _tool, changed_paths = parse_rollout((rollout,))
    elapsed = time.perf_counter() - start

    # The real path survives via the structured field and the *** patch block.
    assert real_path in " ".join(changed_paths)
    # No junk separator/backslash fragments leak into the changed set.
    junk_like = [
        p
        for p in changed_paths
        if sum(1 for ch in p if ch in "-/\\") > len(p) * 0.4
    ]
    assert not junk_like, junk_like[:5]
    # The guard keeps a multi-megabyte record from blowing up parse time.
    assert elapsed < 2.0, f"parse_rollout took {elapsed:.2f}s on a giant line"


def test_loaded_search_uses_resident_postings_without_reopening_sqlite(
    tmp_path: Path,
) -> None:
    rollout = tmp_path / "resident.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "find renderer_assets active_session",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    index = SessionSearchIndex(tmp_path / "resident.sqlite")
    index.upsert("session", (rollout,))

    with patch.object(index, "_connect", side_effect=AssertionError("disk query")):
        result = index.search("active_session")

    assert result["memoryLoaded"] is True
    assert result["matches"][0]["sessionId"] == "session"


def test_search_ranks_exact_phrase_above_token_hits(tmp_path: Path) -> None:
    """A session that contains the verbatim query phrase must rank above a
    session that merely scatters the same tokens across its text."""

    exact = tmp_path / "exact.jsonl"
    exact.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "please find renderer active session in the log",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    scattered = tmp_path / "scattered.jsonl"
    scattered.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "the renderer ran an active session but not contiguous",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    index = SessionSearchIndex(tmp_path / "phrase.sqlite")
    index.upsert("exact", (exact,))
    index.upsert("scattered", (scattered,))

    result = index.search("renderer active session")
    ids = [match["sessionId"] for match in result["matches"]]
    assert "exact" in ids
    assert "scattered" in ids
    # 精确短语命中必须排在零散分词命中之前。
    assert ids.index("exact") < ids.index("scattered")

    exact_match = next(m for m in result["matches"] if m["sessionId"] == "exact")
    scattered_match = next(m for m in result["matches"] if m["sessionId"] == "scattered")
    assert exact_match["exact_phrase"] is True
    assert scattered_match["exact_phrase"] is False


def test_search_terms_support_fuzzy_path_fragments(tmp_path: Path) -> None:
    rollout = tmp_path / "path.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "apply_patch",
                    "arguments": "*** Update File: E:/Project/codex-usage-hud/src/codex_usage_hud/renderer_assets/active_session.py",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    index = SessionSearchIndex(tmp_path / "path.sqlite")
    index.upsert("session", (rollout,))

    result = index.search("renderer active_sess")
    assert result["matches"]
    assert "file" in result["matches"][0]["kinds"]


def _write_rollout(path: Path, message: str) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": message},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_snapshot_restores_resident_index_on_cold_restart(tmp_path: Path) -> None:
    rollout = tmp_path / "snap.jsonl"
    _write_rollout(rollout, "snapshot-cold-restart-marker")

    index = SessionSearchIndex(tmp_path / "snap.sqlite")
    index.upsert("session", (rollout,))
    assert index._snapshot_path().exists()

    fresh = SessionSearchIndex(tmp_path / "snap.sqlite")
    with patch.object(
        session_search_module,
        "_memory_fields",
        side_effect=AssertionError("re-tokenised on cold restart"),
    ):
        loaded = fresh.load()

    assert loaded["memoryLoaded"] is True
    assert fresh.search("snapshot-cold-restart-marker")["matches"]


def test_clear_index_removes_database_snapshot_and_journal_artifacts(
    tmp_path: Path,
) -> None:
    rollout = tmp_path / "clear.jsonl"
    _write_rollout(rollout, "clear-index-marker")
    database = tmp_path / "clear.sqlite"
    index = SessionSearchIndex(database)
    index.upsert("session", (rollout,))
    # Exercise the companion-file accounting even though SQLite normally
    # removes these files after a short transaction.
    for suffix in ("-wal", "-shm", "-journal", ".tmp"):
        database.with_name(database.name + suffix).write_bytes(b"temporary")
    snapshot_tmp = index._snapshot_path().with_name(
        index._snapshot_path().name + ".tmp"
    )
    snapshot_tmp.write_bytes(b"temporary")

    assert index.disk_usage_bytes() > 0
    result = index.clear_index()

    assert result["removedFiles"] >= 2
    assert index.disk_usage_bytes() == 0
    assert index._documents == {}
    assert index._postings == {}
    assert index.memory_loaded is False
    assert all(not path.exists() for path in index.index_artifact_paths())


def test_snapshot_reconciles_with_newer_sqlite_rows(tmp_path: Path) -> None:
    rollout = tmp_path / "reconcile.jsonl"
    _write_rollout(rollout, "older-content-marker")
    database = tmp_path / "reconcile.sqlite"

    index = SessionSearchIndex(database)
    index.upsert("session", (rollout,))

    # Simulate a post-snapshot write by bumping indexed_at and the text.
    import sqlite3 as sqlite3_module

    connection = sqlite3_module.connect(database)
    connection.execute(
        "UPDATE session_search_documents SET user_text = ?, indexed_at = indexed_at + 10 "
        "WHERE session_id = ?",
        ("newer-content-marker", "session"),
    )
    connection.commit()
    connection.close()

    fresh = SessionSearchIndex(database)
    fresh.load()
    assert not fresh.search("older-content-marker")["matches"]
    assert fresh.search("newer-content-marker")["matches"]


def test_snapshot_reconcile_preserves_field_kinds(tmp_path: Path) -> None:
    """Rebuilding a stale doc from SQLite must not shift its text fields.

    ``_document_from_row`` reads a fixed column order that includes
    ``fingerprints`` at index 1; the text fields start at index 2. A regression
    that reads user_text from index 1 would store the fingerprint JSON as user
    text and drop tool_text entirely, so each marker must land in its own kind.
    """
    rollout = tmp_path / "kinds.jsonl"
    user_marker = "reconcile-user-marker"
    assistant_marker = "reconcile-assistant-marker"
    tool_marker = "reconcile-tool-marker"
    rollout.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": user_marker},
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "agent_message", "content": [{"text": assistant_marker}]},
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call", "name": "bash", "command": tool_marker},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    database = tmp_path / "kinds.sqlite"

    index = SessionSearchIndex(database)
    index.upsert("session", (rollout,))

    import sqlite3 as sqlite3_module

    connection = sqlite3_module.connect(database)
    connection.execute(
        "UPDATE session_search_documents SET indexed_at = indexed_at + 10 WHERE session_id = ?",
        ("session",),
    )
    connection.commit()
    connection.close()

    fresh = SessionSearchIndex(database)
    fresh.load()

    def kinds_of(marker: str) -> list[str]:
        matches = fresh.search(marker)["matches"]
        return matches[0]["kinds"] if matches else []

    assert "user" in kinds_of(user_marker)
    assert "assistant" in kinds_of(assistant_marker)
    # Tool text is the field a column-shift bug would silently drop.
    assert "tool" in kinds_of(tool_marker)


def test_two_character_cjk_query_matches(tmp_path: Path) -> None:
    rollout = tmp_path / "cjk.jsonl"
    _write_rollout(rollout, "今天我们讨论会话管理的性能优化问题")

    index = SessionSearchIndex(tmp_path / "cjk.sqlite")
    index.upsert("session", (rollout,))

    assert index.search("会话")["matches"]
    assert index.search("性能")["matches"]


def test_large_batch_parses_in_worker_processes(tmp_path: Path) -> None:
    entries = []
    for number in range(30):
        rollout = tmp_path / f"parallel-{number}.jsonl"
        _write_rollout(rollout, f"parallel-worker-marker-{number}")
        entries.append((f"session-{number}", (rollout,), "", "", "", ""))

    index = SessionSearchIndex(tmp_path / "parallel.sqlite")
    assert session_search_module._process_pool_allowed()
    processed = index.sync_batches(entries, total=len(entries), batch_size=8)

    assert processed == 30
    assert index.count() == 30
    assert index.search("parallel-worker-marker-17")["matches"][0]["sessionId"] == "session-17"


def test_sync_batches_streams_without_blocking_prefetch(tmp_path: Path) -> None:
    # Regression guard for the progressive warm-up fix: the product path must
    # tokenise and upsert entries as they become ready (so the session-index UI
    # advances during indexing) instead of blocking on a single full-corpus
    # prefetch. If ``sync_batches`` ever reverts to calling the blocking
    # ``_prefetch_parses`` helper, this test fails because that helper is made
    # to raise here.
    entries = []
    for number in range(30):
        rollout = tmp_path / f"stream-{number}.jsonl"
        _write_rollout(rollout, f"stream-worker-marker-{number}")
        entries.append((f"session-{number}", (rollout,), "", "", "", ""))

    index = SessionSearchIndex(tmp_path / "stream.sqlite")
    assert session_search_module._process_pool_allowed()

    def _blocking_prefetch_raises(*_args, **_kwargs):
        raise RuntimeError("blocking _prefetch_parses must not run on the product path")

    index._prefetch_parses = _blocking_prefetch_raises  # type: ignore[assignment]

    progress: list[int] = []
    processed = index.sync_batches(
        entries,
        total=len(entries),
        batch_size=8,
        progress_callback=lambda done, total, indexed: progress.append(done),
    )

    assert processed == 30
    assert index.count() == 30
    # Progress must advance in multiple steps, not a single terminal call.
    assert progress and progress[-1] == 30 and len(progress) > 1
    assert index.search("stream-worker-marker-17")["matches"][0]["sessionId"] == "session-17"


def test_stream_parses_routes_whale_in_small_set_to_pool(tmp_path: Path) -> None:
    # Regression guard: a small stale set (< _PARALLEL_PARSE_MIN_ENTRIES) that
    # nonetheless contains a "whale" rollout must still parse in the process
    # pool, so the main thread never freezes on the single heavy parse. This is
    # exactly the 1-month -> 3-month extension case (a few new sessions, one of
    # them huge) that previously hung the session-index UI for the whole parse.
    big = tmp_path / "big.jsonl"
    # ~12 MB single record, comfortably above _PARALLEL_PARSE_LARGE_BYTES (8 MiB).
    big.write_bytes(
        json.dumps(
            {"type": "event_msg", "payload": {"type": "user_message", "message": "x"}}
        ).encode("utf-8")
        * 200_000
    )
    small = tmp_path / "small.jsonl"
    _write_rollout(small, "small-marker")
    index = SessionSearchIndex(tmp_path / "whale.sqlite")
    assert session_search_module._process_pool_allowed()

    stale_jobs = [("big", [str(big)]), ("small", [str(small)])]
    used_pool: dict[str, bool] = {"flag": False}
    real_exec = ProcessPoolExecutor

    def _tracking(*args, **kwargs):
        used_pool["flag"] = True
        return real_exec(*args, **kwargs)

    with patch("codex_usage_hud.core.session_search.ProcessPoolExecutor", _tracking):
        results = list(index._stream_parses(stale_jobs, cancelled=None))
    assert used_pool["flag"] is True
    assert {r[0] for r in results} == {"big", "small"}


def test_stream_parses_inline_for_tiny_small_set(tmp_path: Path) -> None:
    # The complement: a tiny stale set with no heavy rollout must stay inline
    # (no process startup overhead) -- the pool is only engaged when the work is
    # large enough to amortise it or contains a whale.
    small = tmp_path / "small.jsonl"
    _write_rollout(small, "tiny-marker")
    index = SessionSearchIndex(tmp_path / "tiny.sqlite")

    stale_jobs = [("small", [str(small)])]
    used_pool: dict[str, bool] = {"flag": False}
    real_exec = ProcessPoolExecutor

    def _tracking(*args, **kwargs):
        used_pool["flag"] = True
        return real_exec(*args, **kwargs)

    with patch("codex_usage_hud.core.session_search.ProcessPoolExecutor", _tracking):
        results = list(index._stream_parses(stale_jobs, cancelled=None))
    assert used_pool["flag"] is False
    assert len(results) == 1


def test_stream_parses_frozen_build_without_freeze_support_uses_thread_pool(
    tmp_path: Path, monkeypatch
) -> None:
    # Frozen (PyInstaller) builds that did NOT enable process pools (the entry
    # point never called ``freeze_support`` + ``_enable_frozen_process_pool``)
    # must fall back to a *thread* pool, never spawn worker processes -- even
    # though ``spawn`` would re-execute the executable and deadlock.
    big = tmp_path / "big.jsonl"
    big.write_bytes(
        json.dumps(
            {"type": "event_msg", "payload": {"type": "user_message", "message": "x"}}
        ).encode("utf-8")
        * 200_000
    )
    small = tmp_path / "small.jsonl"
    _write_rollout(small, "small-marker")
    index = SessionSearchIndex(tmp_path / "frozen.sqlite")

    stale_jobs = [("big", [str(big)]), ("small", [str(small)])]
    used_proc: dict[str, bool] = {"flag": False}
    used_thread: dict[str, bool] = {"flag": False}

    def _track_proc(*args, **kwargs):
        used_proc["flag"] = True
        return ProcessPoolExecutor(*args, **kwargs)

    def _track_thread(*args, **kwargs):
        used_thread["flag"] = True
        return ThreadPoolExecutor(*args, **kwargs)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        session_search_module, "_FROZEN_PROCESS_POOL_ENABLED", False
    )
    with patch(
        "codex_usage_hud.core.session_search.ProcessPoolExecutor", _track_proc
    ), patch(
        "codex_usage_hud.core.session_search.ThreadPoolExecutor", _track_thread
    ):
        results = list(index._stream_parses(stale_jobs, cancelled=None))
    assert (
        used_proc["flag"] is False
    ), "frozen build without freeze_support must not spawn processes"
    assert (
        used_thread["flag"] is True
    ), "frozen build must use a thread pool for whales"
    assert {r[0] for r in results} == {"big", "small"}


def test_process_pool_allowed_gating(monkeypatch) -> None:
    # The gating logic that decides threads vs processes. The actual *spawn* of a
    # process pool in a frozen build is wired by ``freeze_support()`` (called in
    # the entry point) and cannot be faithfully exercised inside a dev
    # interpreter, so we unit-test the gate directly; the process-pool execution
    # path itself is covered by the non-frozen dev tests below.
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(session_search_module, "_FROZEN_PROCESS_POOL_ENABLED", False)
    assert session_search_module._process_pool_allowed() is True

    # Frozen build that did NOT enable process pools stays on threads.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(session_search_module, "_FROZEN_PROCESS_POOL_ENABLED", False)
    assert session_search_module._process_pool_allowed() is False

    # Frozen build that DID call freeze_support + _enable_frozen_process_pool
    # unlocks process pools (the frozen exe's spawn is wired by freeze_support).
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(session_search_module, "_FROZEN_PROCESS_POOL_ENABLED", True)
    assert session_search_module._process_pool_allowed() is True

    # The escape hatch always wins, regardless of frozen/flag state.
    monkeypatch.setenv("CODEX_HUD_SEARCH_NO_PROCESSES", "1")
    assert session_search_module._process_pool_allowed() is False


def test_stream_parses_uses_process_pool_when_allowed(tmp_path: Path, monkeypatch) -> None:
    # The non-frozen (dev/test) path must spawn worker *processes* -- this is the
    # exact path the frozen exe reaches once its entry point enables process pools
    # via ``_enable_frozen_process_pool``. Confirms the executor selection routes
    # to ProcessPoolExecutor and still streams small sessions before the whale.
    big = tmp_path / "big.jsonl"
    big.write_bytes(
        json.dumps(
            {"type": "event_msg", "payload": {"type": "user_message", "message": "x"}}
        ).encode("utf-8")
        * 200_000
    )
    small = tmp_path / "small.jsonl"
    _write_rollout(small, "small-marker")
    index = SessionSearchIndex(tmp_path / "proc.sqlite")

    stale_jobs = [("big", [str(big)]), ("small", [str(small)])]
    used_proc: dict[str, bool] = {"flag": False}
    used_thread: dict[str, bool] = {"flag": False}

    def _track_proc(*args, **kwargs):
        used_proc["flag"] = True
        return ProcessPoolExecutor(*args, **kwargs)

    def _track_thread(*args, **kwargs):
        used_thread["flag"] = True
        return ThreadPoolExecutor(*args, **kwargs)

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    with patch(
        "codex_usage_hud.core.session_search.ProcessPoolExecutor", _track_proc
    ), patch(
        "codex_usage_hud.core.session_search.ThreadPoolExecutor", _track_thread
    ):
        results = list(index._stream_parses(stale_jobs, cancelled=None))
    assert used_proc["flag"] is True, "non-frozen build must use a process pool"
    assert used_thread["flag"] is False
    ids = [r[0] for r in results]
    assert ids.index("small") < ids.index("big")


def test_stream_parses_yields_small_sessions_before_whale(tmp_path: Path) -> None:
    # Core regression guard for the 96->109 freeze: a small extension set that
    # mixes ordinary ("small") sessions with a "whale" must stream the small
    # sessions FIRST (parsed inline), so the progress callback advances 96 -> 108
    # immediately instead of freezing at the covered count until the whole whale
    # parse finishes. The whale is only parsed in the pool and yielded last.
    big = tmp_path / "big.jsonl"
    big.write_bytes(
        json.dumps(
            {"type": "event_msg", "payload": {"type": "user_message", "message": "x"}}
        ).encode("utf-8")
        * 200_000
    )
    small_a = tmp_path / "small_a.jsonl"
    small_b = tmp_path / "small_b.jsonl"
    _write_rollout(small_a, "small-a-marker")
    _write_rollout(small_b, "small-b-marker")
    index = SessionSearchIndex(tmp_path / "order.sqlite")

    stale_jobs = [
        ("big", [str(big)]),
        ("small_a", [str(small_a)]),
        ("small_b", [str(small_b)]),
    ]
    results = list(index._stream_parses(stale_jobs, cancelled=None))
    session_ids = [r[0] for r in results]
    assert set(session_ids) == {"big", "small_a", "small_b"}
    # Every ordinary session must appear before the whale, in every execution
    # mode (ProcessPool in dev, ThreadPool in frozen builds).
    whale_pos = session_ids.index("big")
    assert whale_pos > session_ids.index("small_a")
    assert whale_pos > session_ids.index("small_b")


def test_ensure_scan_index_covers_stamp_scans_and_reports_repairs(tmp_path: Path) -> None:
    # The document table stores multi-megabyte text columns, so a two-column
    # full scan walks every overflow page of a multi-GB database (~8.5 s
    # measured). The covering index keeps the warm job's stamp scans off that
    # path, and must be idempotent so the background finalize can call it on
    # every completion.
    import sqlite3

    rollout = tmp_path / "covered.jsonl"
    _write_rollout(rollout, "covering-index-marker")

    index = SessionSearchIndex(tmp_path / "covered.sqlite")
    index.upsert("session", (rollout,))

    assert index.ensure_scan_index() is True
    assert index.ensure_scan_index() is True  # idempotent

    connection = sqlite3.connect(index.path)
    try:
        names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert session_search_module._STAMPS_INDEX in names
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT session_id, indexed_at "
            "FROM session_search_documents"
        ).fetchall()
        assert any("COVERING INDEX" in str(row) for row in plan)
    finally:
        connection.close()

    # A clean cold load of a matching snapshot reports nothing reconciled.
    result = index.load()
    assert result["memoryLoaded"] is True
    assert result["reconciled"] == 0
