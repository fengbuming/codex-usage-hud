"""Parity and lifecycle checks for the on-demand production search store."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from unittest.mock import patch

import pytest

from codex_usage_hud.core.session_search import SessionSearchIndex
from codex_usage_hud.core.session_search_disk import OnDemandSessionSearchIndex


def _rollout(path, text):
    path.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": text,
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def pair(tmp_path):
    old = SessionSearchIndex(tmp_path / "old.sqlite")
    disk = OnDemandSessionSearchIndex(tmp_path / "disk.sqlite")
    yield old, disk
    disk.close()


def test_same_results_ranking_tokens_and_find_query(pair, tmp_path):
    old, disk = pair
    texts = [
        "renderer active session renderer active session",
        "renderer unrelated active unrelated session session_search.py E:/Project/code",
        "中文扫描索引优化方案，中文双字命中。扫描器测试",
        "a ab abc a_b abcdef renderer_assets",
        "Καλημέρα İSTANBUL Straße STRASSE café 😀 éé 日本語",
        "metadata words nowhere else",
    ]
    for number, text in enumerate(texts):
        rollout = _rollout(tmp_path / f"{number}.jsonl", text)
        for index in pair:
            index.upsert(
                str(number),
                [rollout],
                title="索引标题",
                workdir="E:/Work",
                model_provider="openai",
            )
    queries = [
        "renderer active session",
        "renderer",
        "render",
        "ender",
        "active_session",
        "session_search.py",
        "E:/Project",
        "扫描",
        "索引",
        "优",
        "优化方案",
        "a",
        "ab",
        "abc",
        "a_b",
        "a abcdef",
        "openai",
        "E:/Work",
        "索引标题",
        "καλημέρα",
        "istanbul",
        "straße",
        "café",
        "é",
        "日本語",
        "😀",
        "nohit",
    ]
    for query in queries:
        assert disk.search(query) == old.search(query), query
        assert disk.search(query, session_ids=["0", "2"]) == old.search(
            query, session_ids=["0", "2"]
        ), query
    ids = list(disk._documents)
    assert disk.preview_matches(ids, "扫描") == old.preview_matches(ids, "扫描")
    assert disk.phrase_matches(ids, "renderer active session") == old.phrase_matches(
        ids, "renderer active session"
    )
    disk.unload_memory()
    assert disk.search("renderer active session") == old.search(
        "renderer active session"
    )


def test_background_build_and_restart_do_not_rehydrate_documents(pair, tmp_path):
    old, disk = pair
    for number in range(8):
        path = _rollout(tmp_path / f"bg-{number}.jsonl", "background marker " * 2000)
        old.upsert(str(number), [path])
    # Reuse an existing durable index, without needing its resident pickle.
    disk.path = old.path
    with patch.object(
        disk, "_load_snapshot_locked", side_effect=AssertionError("full unpickle")
    ):
        assert disk.load()["indexed"] == 8
        assert not disk._candidate_cache and not disk._gram_cache
        assert disk._query_db is None
        assert disk.search("marker") == old.search("marker")
    disk.close()
    restarted = OnDemandSessionSearchIndex(old.path)
    try:
        with patch(
            "codex_usage_hud.core.session_search_disk._memory_fields",
            side_effect=AssertionError("retokenized"),
        ):
            assert restarted.load()["indexed"] == 8
            assert restarted._query_db is None
            assert restarted.search("background") == old.search("background")
    finally:
        restarted.close()


def test_incremental_updates_removal_and_slot_reuse(pair, tmp_path):
    old, disk = pair
    paths = [
        _rollout(tmp_path / f"update-{n}.jsonl", f"shared marker{n}") for n in range(4)
    ]
    for index in pair:
        index.sync([(str(n), [path], "", "", "", "") for n, path in enumerate(paths)])
        index.search("shared")
    for n in range(3):
        _rollout(paths[n], f"updated replacement{n}")
        for index in pair:
            index.upsert(str(n), [paths[n]])
        assert disk.search("shared") == old.search("shared")
        assert disk.search(f"replacement{n}") == old.search(f"replacement{n}")
    for index in pair:
        index.remove_missing(["1", "3"])
        index.upsert("new", [paths[0]])
    assert disk.search("replacement0") == old.search("replacement0")
    assert disk.search("shared") == old.search("shared")
    assert len(disk._slot_sessions) == 4


def test_crash_between_source_commit_and_sidecar_commit_repairs_by_stamp(
    pair, tmp_path
):
    old, disk = pair
    path = _rollout(tmp_path / "crash.jsonl", "before_crash")
    disk.upsert("one", [path])
    disk.close()
    legacy = SessionSearchIndex(disk.path)
    _rollout(path, "after_crash")
    legacy.upsert("one", [path])
    restarted = OnDemandSessionSearchIndex(disk.path)
    try:
        assert restarted.load()["reconciled"] == 1
        assert restarted.search("after_crash")["matches"]
        assert not restarted.search("before_crash")["matches"]
    finally:
        restarted.close()


def test_capacity_and_idle_release_preserve_ready_state(pair, tmp_path):
    old, disk = pair
    disk._cache_limit = 4096
    for n in range(5):
        path = _rollout(
            tmp_path / f"cache-{n}.jsonl", " ".join(f"term{n}_{j}" for j in range(100))
        )
        disk.upsert(str(n), [path])
    expected = disk.search("term")
    assert disk._candidate_bytes <= disk._cache_limit
    assert len(disk._candidate_cache) < 5
    timer = disk._idle_timer
    timer.function()  # Deterministic expiry, without sleeping for 60 seconds.
    assert disk.memory_loaded
    assert disk.count() == 5
    assert not disk._candidate_cache and not disk._gram_cache
    assert disk._query_db is None
    assert disk.search("term") == expected
    # An obsolete expiry cannot evict a newer query's buffers.
    old_timer = disk._idle_timer
    disk.search("term0")
    old_timer.function()
    assert disk._candidate_cache


def test_release_waits_for_search_and_clear_removes_only_owned_artifacts(
    pair, tmp_path
):
    old, disk = pair
    path = _rollout(tmp_path / "concurrent.jsonl", "concurrent needle")
    disk.upsert("one", [path])
    expected = disk.search("needle")
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(disk.search, "needle") for _ in range(10)]
        futures.append(pool.submit(disk.unload_memory))
        for result in futures[:-1]:
            assert result.result() == expected
        futures[-1].result()
    assert disk.clear_index()["removedFiles"] >= 2
    assert path.exists()
    assert not any(artifact.exists() for artifact in disk.index_artifact_paths())
    assert not disk.search("needle")["matches"]


def test_sidecar_transaction_failure_cannot_publish_partial_postings(pair, tmp_path):
    old, disk = pair
    path = _rollout(tmp_path / "transaction.jsonl", "initial needle")
    disk.upsert("one", [path])
    connection = disk._query_connection()
    connection.execute(
        "CREATE TRIGGER fail_document BEFORE UPDATE ON documents BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    connection.commit()
    _rollout(path, "replacement needle")
    with pytest.raises(sqlite3.IntegrityError):
        disk.upsert("one", [path])
    disk.close()
    connection = sqlite3.connect(disk._query_path())
    assert b"initial" in (disk._stream_path().read_bytes())
    # The failed update must leave the old stamp/offsets together with its
    # old postings; a new process then repairs from the committed source.
    assert (
        connection.execute("SELECT count(*) FROM postings WHERE gram='rep'").fetchone()[
            0
        ]
        == 0
    )
    connection.execute("DROP TRIGGER fail_document")
    connection.commit()
    connection.close()
    repaired = OnDemandSessionSearchIndex(disk.path)
    try:
        assert repaired.search("replacement")["matches"]
        assert not repaired.search("initial")["matches"]
    finally:
        repaired.close()


@pytest.mark.parametrize("damage", ["truncate", "generation", "corrupt_database"])
def test_disposable_sidecar_recovers_from_damage(pair, tmp_path, damage):
    old, disk = pair
    path = _rollout(tmp_path / "damage.jsonl", "durable recovery marker")
    disk.upsert("one", [path])
    expected = disk.search("marker")
    disk.close()
    if damage == "truncate":
        with disk._stream_path().open("r+b") as handle:
            handle.truncate(35)
    elif damage == "generation":
        with disk._stream_path().open("r+b") as handle:
            handle.write(b"x" * 32)
    else:
        disk._query_path().write_bytes(b"corrupt file")
    restarted = OnDemandSessionSearchIndex(disk.path)
    try:
        assert restarted.search("marker") == expected
    finally:
        restarted.close()


def test_background_refresh_does_not_keep_query_buffers_alive(pair, tmp_path):
    old, disk = pair
    path = _rollout(tmp_path / "background.jsonl", "background live query")
    disk.upsert("one", [path])
    disk.search("background")
    timer = disk._idle_timer
    disk.search("background", load=False)
    assert disk._idle_timer is timer
    timer.function()
    assert not disk._candidate_cache
    disk.search("background", load=False)
    assert not disk._candidate_cache and disk._streams is None
    assert disk._idle_timer is None


def test_compaction_rewrites_offsets_and_bounds_append_garbage(pair, tmp_path):
    old, disk = pair
    path = _rollout(tmp_path / "compact.jsonl", "compaction durable marker")
    disk.upsert("one", [path])
    expected = disk.search("marker")
    disk.unload_memory()
    # Sparse garbage emulates many replaced generations without huge test IO.
    with disk._stream_path().open("r+b") as handle:
        handle.truncate(33 * 1024 * 1024)
    disk._write_snapshot()
    assert disk._stream_path().stat().st_size < 4096
    assert disk.search("marker") == expected
    disk.close()
    restarted = OnDemandSessionSearchIndex(disk.path)
    try:
        assert restarted.search("marker") == expected
    finally:
        restarted.close()


def test_first_explicit_query_keeps_its_cache_after_catalog_load(pair, tmp_path):
    old, disk = pair
    path = _rollout(tmp_path / "first-query.jsonl", "first query marker")
    disk.upsert("one", [path])
    disk.close()
    restarted = OnDemandSessionSearchIndex(disk.path)
    try:
        restarted.begin_query()  # Manager submits with load=False.
        assert restarted.search("marker", load=False)["matches"]
        assert restarted._candidate_cache
        assert restarted._idle_timer is not None
    finally:
        restarted.close()


def test_oversized_document_is_searched_without_overrunning_cache(pair, tmp_path):
    old, disk = pair
    disk._cache_limit = 256
    path = _rollout(
        tmp_path / "oversized.jsonl", " ".join(f"large_token_{n}" for n in range(600))
    )
    for index in pair:
        index.upsert("one", [path])
    assert disk.search("token_42") == old.search("token_42")
    assert disk._candidate_bytes == 0
    assert not disk._candidate_cache
    disk.unload_memory()
    assert disk._streams is None
