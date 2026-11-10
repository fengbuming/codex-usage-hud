from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from codex_usage_hud.platforms.active_session import ActiveSessionTracker


def test_bulk_titles_read_database_once_and_keep_index_fallback_and_renames(tmp_path):
    database = tmp_path / "state.sqlite"
    index = tmp_path / "session_index.jsonl"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT)")
        connection.executemany("INSERT INTO threads VALUES (?, ?)", [("one", "DB title"), ("two", "")])
    index.write_text("\n".join(json.dumps(row) for row in [
        {"id": "one", "thread_name": "Older title"},
        {"id": "two", "thread_name": "Index title"},
        {"id": "two", "thread_name": "Latest fallback"},
    ]), encoding="utf-8")
    tracker = SimpleNamespace(state_db=database, session_index_path=index)
    real_connect = sqlite3.connect
    with patch("codex_usage_hud.platforms.active_session.sqlite3.connect", wraps=real_connect) as connect:
        assert ActiveSessionTracker.titles_from_thread_ids(tracker, ["one", "two", "missing", "one"]) == {
            "one": "DB title", "two": "Latest fallback", "missing": "",
        }
        assert connect.call_count == 1
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE threads SET title='Renamed title' WHERE id='one'")
    assert ActiveSessionTracker.titles_from_thread_ids(tracker, ["one"]) == {"one": "Renamed title"}
    assert ActiveSessionTracker.titles_from_thread_ids(tracker, ["one"], prefer_session_index=True) == {"one": "Older title"}


def test_bulk_titles_allow_missing_database_and_empty_request(tmp_path):
    tracker = SimpleNamespace(state_db=tmp_path / "missing.sqlite", session_index_path=tmp_path / "missing.jsonl")
    assert ActiveSessionTracker.titles_from_thread_ids(tracker, []) == {}
    assert ActiveSessionTracker.titles_from_thread_ids(tracker, ["missing"]) == {"missing": ""}
