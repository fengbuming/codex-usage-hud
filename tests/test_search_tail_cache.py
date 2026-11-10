from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from codex_usage_hud.core import session_search as search
from codex_usage_hud.core.search_tail_cache import SearchTailCache


def line(kind: str, **payload) -> str:
    return json.dumps({"type": "event_msg", "payload": {"type": kind, **payload}}, ensure_ascii=False) + "\n"


def parse(cache, path):
    return cache.parse(path, search._RolloutTextState)


def test_appended_records_match_full_extraction_and_only_new_records_are_consumed(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(line("user_message", message="中文 起始请求") * 200, encoding="utf-8")
    cache = SearchTailCache()
    assert parse(cache, path) == search.parse_rollout([path])
    additions = [
        line("agent_message", message="new final answer"),
        line("token_count", info={"total_tokens": 123}),
        line("function_call_output", output='- button "other chat" [ref=e123]:\nreal-output'),
        line("item_completed", item={"type": "file_change", "path": "src/新增.py", "diff": "+changed"}),
        line("user_message", message="api_key=secret-value-abc123 and   whitespace"),
    ]
    for addition in additions:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(addition)
        consumed = []
        real = search._RolloutTextState.consume
        with patch.object(search._RolloutTextState, "consume", lambda self, raw: (consumed.append(raw), real(self, raw))[-1]):
            incremental = parse(cache, path)
        assert len(consumed) == 1
        assert incremental == search.parse_rollout([path])
    assert "other chat" not in incremental[2]
    assert "src/新增.py" in incremental[3]


@pytest.mark.parametrize("replacement", ["same-size", "truncate", "replace", "rewrite-and-grow"])
def test_modified_rollouts_reset_extraction_state(tmp_path, replacement):
    path = tmp_path / "session.jsonl"
    original = line("user_message", message="old marker " * 2000)
    path.write_text(original, encoding="utf-8")
    cache = SearchTailCache()
    parse(cache, path)
    stamp = path.stat().st_mtime_ns
    if replacement == "same-size":
        path.write_text(original.replace("old", "new"), encoding="utf-8")
    elif replacement == "truncate":
        path.write_text(line("user_message", message="truncated"), encoding="utf-8")
    elif replacement == "replace":
        next_path = tmp_path / "replacement.jsonl"
        next_path.write_text(original.replace("old", "new"), encoding="utf-8")
        os.utime(next_path, ns=(stamp, stamp))
        os.replace(next_path, path)
    else:
        path.write_text(original.replace("old", "new") + line("agent_message", message="append"), encoding="utf-8")
    assert parse(cache, path) == search.parse_rollout([path])
    assert "old marker" not in parse(cache, path)[0]


def test_middle_rewrite_plus_append_is_detected_even_if_both_witnesses_match(tmp_path):
    path = tmp_path / "session.jsonl"
    prefix = line("user_message", message="padding " * 1000)
    middle = line("agent_message", message="old-middle-marker")
    suffix = line("user_message", message="tail padding " * 1000)
    path.write_text(prefix + middle + suffix, encoding="utf-8")
    cache = SearchTailCache()
    parse(cache, path)
    with path.open("r+b") as handle:
        handle.seek(len(prefix.encode()))
        handle.write(middle.replace("old-middle", "new-middle").encode())
        handle.seek(0, 2)
        handle.write(line("token_count", info={"total_tokens": 123}).encode())
    assert parse(cache, path) == search.parse_rollout([path])
    assert "old-middle-marker" not in parse(cache, path)[1]
    assert "new-middle-marker" in parse(cache, path)[1]


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_incomplete_utf8_and_partial_final_records_are_reconciled(tmp_path, ending):
    path = tmp_path / "session.jsonl"
    path.write_bytes(line("user_message", message="first").replace("\n", ending).encode())
    cache = SearchTailCache()
    parse(cache, path)
    addition = line("agent_message", message="中文第二次追加").replace("\n", ending).encode()
    split = addition.index("中".encode()) + 1
    with path.open("ab") as handle:
        handle.write(addition[:split])
    assert parse(cache, path) == search.parse_rollout([path])
    with path.open("ab") as handle:
        handle.write(addition[split:])
    assert parse(cache, path) == search.parse_rollout([path])
    assert "中文第二次追加" in parse(cache, path)[1]
    with path.open("ab") as handle:
        handle.write(line("user_message", message="valid final record").rstrip().encode())
    assert parse(cache, path) == search.parse_rollout([path])
    with path.open("ab") as handle:
        handle.write(ending.encode() + line("user_message", message="after final").encode())
    assert parse(cache, path) == search.parse_rollout([path])


def test_memory_budget_and_entry_limit_bound_retained_extraction(tmp_path):
    cache = SearchTailCache(max_entries=2, max_bytes=32_000)
    for i in range(8):
        path = tmp_path / f"{i}.jsonl"
        path.write_text(line("user_message", message="x" * 1500), encoding="utf-8")
        assert parse(cache, path) == search.parse_rollout([path])
        assert len(cache._entries) <= 2
        assert sum(entry.state.retained_bytes + 8192 for entry in cache._entries.values()) <= 32_000
    whale = tmp_path / "whale.jsonl"
    whale.write_text(line("user_message", message="x" * 100_000), encoding="utf-8")
    assert parse(cache, whale) == search.parse_rollout([whale])
    assert whale.absolute() not in cache._entries
    cache.clear()
    assert not cache._entries


def test_document_extraction_ceiling_has_identical_results_when_appended(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "MAX_DOCUMENT_CHARS", 150)
    path = tmp_path / "limited.jsonl"
    path.write_text(line("user_message", message="first"), encoding="utf-8")
    cache = SearchTailCache()
    assert parse(cache, path) == search.parse_rollout([path])
    for i in range(4):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line("agent_message", message=f"appended {i}"))
        assert parse(cache, path) == search.parse_rollout([path])


def test_normal_output_avoids_ui_regex_but_snapshot_noise_is_still_removed():
    with patch.object(search, "_UI_A11Y_REF_RE") as a11y, patch.object(search, "_UI_THREAD_PREVIEW_RE") as preview:
        ordinary = "--- ordinary command output with paths and dashes ---" * 100
        assert search._strip_ui_snapshot(ordinary) == ordinary
        a11y.sub.assert_not_called()
        preview.sub.assert_not_called()
    assert "other chat" not in search._strip_ui_snapshot('- button "other chat" [ref=e123]:')


def test_metadata_only_appends_keep_resident_document_and_skip_tokenisation(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(line("user_message", message="searchable marker"), encoding="utf-8")
    index = search.SessionSearchIndex(tmp_path / "search.sqlite")
    entry = ("session", (path,), "Visible title", "project", "provider", "desktop")
    index.sync_batches([entry])
    previous = index._documents["session"]
    before = index.search("searchable marker")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line("token_count", info={"total_tokens": 123}))
    with patch.object(search, "_memory_fields", side_effect=AssertionError("metadata retokenisation")), patch.object(search, "_parse_fields", side_effect=AssertionError("metadata retokenisation")):
        assert index.sync_batches([entry]) == 1
    assert index._documents["session"] is previous
    assert index.search("searchable marker") == before
    assert not index.stale_session_ids([entry])
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line("agent_message", message="new answer marker"))
    index.sync_batches([entry])
    assert index.search("new answer")["matches"][0]["kinds"] == ["assistant"]
    fresh = search.SessionSearchIndex(index.path)
    assert fresh.search("new answer marker") == index.search("new answer marker")


def test_mutation_during_indexing_keeps_old_fingerprint_until_next_event(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(line("user_message", message="original"), encoding="utf-8")
    index = search.SessionSearchIndex(tmp_path / "search.sqlite")
    entry = ("session", (path,), "", "", "", "")
    stream = index._stream_parses

    def racing(jobs, *, cancelled):
        for parsed in stream(jobs, cancelled=cancelled):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line("agent_message", message="racing appended answer"))
            yield parsed

    with patch.object(index, "_stream_parses", racing):
        index.sync_batches([entry])
    assert index.stale_session_ids([entry]) == frozenset({"session"})
    index.sync_batches([entry])
    assert not index.stale_session_ids([entry])
    assert index.search("racing appended answer")["matches"]


def test_multiple_rollouts_keep_original_order_and_shared_extraction_budget(tmp_path, monkeypatch):
    first, second = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    first.write_text(line("user_message", message="first request"), encoding="utf-8")
    second.write_text(line("agent_message", message="second answer"), encoding="utf-8")
    index = search.SessionSearchIndex(tmp_path / "search.sqlite")
    with patch.object(index._tail_cache, "parse", side_effect=AssertionError("multi-file cache")):
        assert index._parse_changed_rollout([first, second]) == search.parse_rollout([first, second])


def test_append_after_cache_eviction_and_index_clear_does_not_lose_old_text(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(line("user_message", message="preserved original"), encoding="utf-8")
    index = search.SessionSearchIndex(tmp_path / "search.sqlite")
    index.upsert("session", [path])
    assert index._tail_cache._entries
    index.clear_index()
    assert not index._tail_cache._entries
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line("agent_message", message="later answer"))
    index.upsert("session", [path])
    assert index.search("preserved original")["matches"]
    assert index.search("later answer")["matches"]


def test_metadata_cursor_does_not_read_or_rewrite_document_blobs(tmp_path):
    import sqlite3

    path = tmp_path / "session.jsonl"
    path.write_text(line("user_message", message="large content " * 50000), encoding="utf-8")
    index = search.SessionSearchIndex(tmp_path / "search.sqlite")
    entry = ("session", (path,), "Title", "cwd", "provider", "desktop")
    index.sync_batches([entry])
    connect = index._connect

    def guard(operation, table, column, *args):
        if table == search._DOC_TABLE and (
            operation == sqlite3.SQLITE_UPDATE or (
                operation == sqlite3.SQLITE_READ
                and column in {"user_text", "assistant_text", "tool_text", "changed_paths", "searchable"}
            )
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def guarded_connect():
        connection = connect()
        connection.set_authorizer(guard)
        return connection

    with path.open("a", encoding="utf-8") as handle:
        handle.write(line("token_count", info={"total_tokens": 123}))
    with patch.object(index, "_connect", guarded_connect):
        assert index.sync_batches([entry]) == 1
        assert not index.stale_session_ids([entry])
    # The small cursor is durable; a fresh reader sees this file as current
    # without retokenising/rebuilding its unchanged resident document.
    fresh = search.SessionSearchIndex(index.path)
    assert not fresh.stale_session_ids([entry])
    assert fresh.search("large content")["matches"]


def test_cursor_is_invalidated_by_document_replacement_and_pruned_with_document(tmp_path):
    import sqlite3
    from contextlib import closing

    path = tmp_path / "session.jsonl"
    path.write_text(line("user_message", message="old text"), encoding="utf-8")
    index = search.SessionSearchIndex(tmp_path / "search.sqlite")
    index.upsert("session", [path])
    with closing(sqlite3.connect(index.path)) as connection:
        connection.execute(
            f"UPDATE {search._DOC_TABLE} SET indexed_at=indexed_at+1 WHERE session_id='session'"
        )
        connection.commit()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line("agent_message", message="new answer"))
    index.upsert("session", [path])
    assert index.search("new answer")["matches"]
    index.remove_missing([])
    with closing(sqlite3.connect(index.path)) as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {search._CURSOR_TABLE}").fetchone()[0] == 0


def test_legacy_optional_fts_is_repaired_even_with_a_valid_extraction_cursor(tmp_path):
    from contextlib import closing
    import sqlite3

    path = tmp_path / "session.jsonl"
    path.write_text(line("user_message", message="fts repair marker"), encoding="utf-8")
    index = search.SessionSearchIndex(tmp_path / "search.sqlite", use_fts=True)
    index.upsert("session", [path])
    if not index._fts_available:
        pytest.skip("SQLite trigram FTS unavailable")
    with closing(sqlite3.connect(index.path)) as connection:
        connection.execute(f"DELETE FROM {search._FTS_TABLE}")
        connection.commit()
    index.upsert("session", [path])
    with closing(sqlite3.connect(index.path)) as connection:
        assert connection.execute(f"SELECT session_id FROM {search._FTS_TABLE}").fetchall() == [("session",)]
    assert index.search("fts repair marker")["matches"]
