from __future__ import annotations

import os
from pathlib import Path

from codex_usage_hud.active_work_cache import ActiveWorkSnapshotCache
from codex_usage_hud.core import ParsedSession


def test_compact_work_parse_keeps_bubble_fields_and_skips_historical_ui_work(tmp_path):
    import json
    from dataclasses import replace
    from datetime import datetime, timedelta
    from unittest.mock import patch
    from codex_usage_hud import active_work
    from codex_usage_hud.core import JsonlSessionParser

    now = datetime.now().astimezone()
    records = []
    for ordinal in range(2):
        stamp = (now - timedelta(minutes=2 - ordinal)).isoformat()
        records.extend([
            {"timestamp": stamp, "type": "event_msg", "payload": {"type": "task_started"}},
            {"timestamp": stamp, "type": "event_msg", "payload": {"type": "user_message", "message": f"task {ordinal}"}},
            {"timestamp": stamp, "type": "response_item", "payload": {
                "type": "function_call", "name": "exec_command", "call_id": str(ordinal),
                "arguments": json.dumps({"cmd": "echo " + "history payload " * 10000}),
            }},
        ])
        if ordinal == 0:
            records.append({"timestamp": stamp, "type": "event_msg", "payload": {"type": "task_complete"}})
    path = tmp_path / "work.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records), encoding="utf-8")
    parser = JsonlSessionParser()
    full = parser.parse_file(path)
    assert len(full.activity_tasks) == 2
    with patch.object(parser, "build_task_history", side_effect=AssertionError("historical work")):
        work = parser.parse_work_file(path)
    compact = active_work._snapshot_for_active_work_cache(work)
    assert work.activity_tasks == []
    assert compact.session_request_history == []
    assert active_work._work_item_from_snapshot(full, current=False, now=now) == active_work._work_item_from_snapshot(compact, current=False, now=now)
    # Completion grace and stale/startup decisions are evaluated at display
    # time, even when no new bytes arrive and the parsed snapshot is reused.
    compact = replace(compact, final_answer_at=now, task_completed_at=None)
    assert active_work._work_status_from_snapshot(compact, now=now + timedelta(seconds=2))[0] == "recent"


class Parser:
    usage_contribution_version = "prices-1"
    estimate_enabled = True

    def __init__(self) -> None:
        self.calls = 0

    def parse_file(self, path: Path) -> ParsedSession:
        self.calls += 1
        return ParsedSession(task_prompt=path.read_text(encoding="utf-8"))


def test_unchanged_work_snapshot_does_not_reparse_history(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text("running", encoding="utf-8")
    parser = Parser()
    cache = ActiveWorkSnapshotCache()
    first = cache.parse(parser, path)
    assert cache.parse(parser, path) is first
    assert parser.calls == 1

    path.write_text("completed", encoding="utf-8")
    assert cache.parse(parser, path).task_prompt == "completed"
    assert parser.calls == 2


def test_work_cache_detects_replaced_file_with_preserved_size_and_mtime(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text("old", encoding="utf-8")
    parser = Parser()
    cache = ActiveWorkSnapshotCache()
    cache.parse(parser, path)
    stamp = path.stat().st_mtime_ns
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_text("new", encoding="utf-8")
    os.utime(replacement, ns=(stamp, stamp))
    os.replace(replacement, path)
    assert cache.parse(parser, path).task_prompt == "new"
    assert parser.calls == 2


def test_work_cache_invalidates_pricing_estimates_and_parser_identity(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text("unchanged", encoding="utf-8")
    cache = ActiveWorkSnapshotCache()
    parser = Parser()
    cache.parse(parser, path)
    parser.usage_contribution_version = "prices-2"
    cache.parse(parser, path)
    parser.estimate_enabled = False
    cache.parse(parser, path)
    assert parser.calls == 3
    other = Parser()
    cache.parse(other, path)
    assert other.calls == 1


def test_work_cache_is_bounded_and_does_not_cache_racing_writes(tmp_path: Path) -> None:
    cache = ActiveWorkSnapshotCache(max_entries=2)
    parser = Parser()
    paths = [tmp_path / f"{index}.jsonl" for index in range(3)]
    for path in paths:
        path.write_text("before", encoding="utf-8")
        cache.parse(parser, path)
    cache.parse(parser, paths[0])
    assert parser.calls == 4

    class RacingParser(Parser):
        def parse_file(self, path: Path) -> ParsedSession:
            snapshot = super().parse_file(path)
            path.write_text(snapshot.task_prompt + " appended", encoding="utf-8")
            return snapshot

    racing = RacingParser()
    cache.parse(racing, paths[0])
    assert cache.parse(racing, paths[0]).task_prompt.endswith(" appended")
    assert racing.calls == 2
    cache.close()
    assert not cache._entries
