from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codex_usage_hud.core import JsonlSessionParser
from codex_usage_hud.core.calculator import UsageCalculator
from codex_usage_hud.core.deleted_usage import DeletedUsageLedger
from codex_usage_hud.core.parser import CostEstimator
from codex_usage_hud.usage_cache import UsageSummaryCache
from codex_usage_hud.usage_summary_store import UsageSummaryStore


def _record(
    timestamp: str, record_type: str, payload: dict[str, object]
) -> dict[str, object]:
    return {"timestamp": timestamp, "type": record_type, "payload": payload}


def _token_count(timestamp: str, cumulative_input: int) -> dict[str, object]:
    return _record(
        timestamp,
        "event_msg",
        {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": cumulative_input,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                }
            },
        },
    )


def _append_record(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload) + "\n")


def test_usage_cache_reports_warm_only_after_matching_window_scan(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    cache = UsageSummaryCache(JsonlSessionParser())
    day = datetime(2026, 7, 30, tzinfo=timezone.utc)
    week = datetime(2026, 7, 27, tzinfo=timezone.utc)

    assert not cache.is_warm_for(sessions, day, week)

    cache.summarize(sessions, day, week)

    assert cache.is_warm_for(sessions, day, week)
    assert not cache.is_warm_for(sessions, day + timedelta(days=1), week)


def test_usage_cache_bounds_retained_raw_tail_records(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    for index in range(3):
        (sessions / f"session-{index}.jsonl").write_text(
            json.dumps(
                _record(
                    "2026-07-30T00:00:00Z",
                    "session_meta",
                    {"id": f"s{index}", "model_provider": "custom"},
                )
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    cache = UsageSummaryCache(
        JsonlSessionParser(),
        max_tail_state_bytes=0,
    )
    day = datetime(2026, 7, 30, tzinfo=timezone.utc)
    week = datetime(2026, 7, 27, tzinfo=timezone.utc)

    cache.summarize(sessions, day, week)

    assert all(entry.tail_state is None for entry in cache._entries.values())


def test_usage_cache_append_reuses_tail_state_and_matches_full_rebuild(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    current = sessions / "current.jsonl"
    current.write_text(
        json.dumps(
            _record(
                "2026-07-30T00:00:00Z",
                "session_meta",
                {"id": "s1", "model_provider": "custom"},
            )
        )
        + "\n"
        + json.dumps(_token_count("2026-07-30T00:00:01Z", 10))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    parser = JsonlSessionParser()
    cache = UsageSummaryCache(parser, min_rescan_seconds=60)
    day = datetime(2026, 7, 30, tzinfo=timezone.utc)
    week = datetime(2026, 7, 27, tzinfo=timezone.utc)

    first, _ = cache.summarize(sessions, day, week)
    first_state = cache._entries[current.resolve()].tail_state
    assert first_state is not None
    first_offset = first_state.offset

    _append_record(current, _token_count("2026-07-30T00:00:02Z", 25))
    incremental, _ = cache.summarize(
        sessions,
        day,
        week,
        allow_stale=True,
        refresh_paths=(current,),
    )
    second_state = cache._entries[current.resolve()].tail_state
    assert second_state is first_state
    assert second_state.offset > first_offset

    rebuilt, _ = UsageSummaryCache(JsonlSessionParser()).summarize(sessions, day, week)
    assert first.tokens == 10
    assert incremental == rebuilt
    assert incremental.tokens == 25


def test_usage_cache_deduplicates_continuation_files_by_session_id(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    session_id = "00000000-0000-0000-0000-000000000001"
    child_id = "00000000-0000-0000-0000-000000000002"
    first = sessions / "first.jsonl"
    continuation = sessions / "continuation.jsonl"
    child = sessions / "child.jsonl"

    for path, cumulative_input in ((first, 10), (continuation, 25)):
        path.write_text(
            json.dumps(
                _record(
                    "2026-07-30T00:00:00Z",
                    "session_meta",
                    {"id": session_id, "model_provider": "custom"},
                )
            )
            + "\n"
            + json.dumps(_token_count("2026-07-30T00:00:01Z", cumulative_input))
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    child.write_text(
        json.dumps(
            _record(
                "2026-07-30T00:00:02Z",
                "session_meta",
                {
                    "id": child_id,
                    "model_provider": "custom",
                    "thread_source": "subagent",
                    "parent_thread_id": session_id,
                },
            )
        )
        + "\n"
        + json.dumps(_token_count("2026-07-30T00:00:03Z", 7))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    cache = UsageSummaryCache(JsonlSessionParser())
    day = datetime(2026, 7, 30, tzinfo=timezone.utc)
    week = datetime(2026, 7, 27, tzinfo=timezone.utc)

    summary, _ = cache.summarize(sessions, day, week, force_rescan=True)
    family = cache.family_lifetime_usage(session_id, included_providers={"custom"})
    insights = cache.insights(
        sessions,
        day,
        week,
        included_providers={"custom"},
    )

    assert summary.tokens == 32
    assert family.tokens == 32
    assert insights["today"]["totals"]["tokens"] == 32
    assert insights["today"]["totals"]["sessionCount"] == 1


def test_usage_cache_does_not_build_full_session_snapshots_for_aggregate_scan(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    current = sessions / "current.jsonl"
    current.write_text(
        json.dumps(
            _record(
                "2026-07-30T00:00:00Z",
                "session_meta",
                {"id": "s1", "model_provider": "custom"},
            )
        )
        + "\n"
        + json.dumps(_token_count("2026-07-30T00:00:01Z", 10))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    class AggregateParser(JsonlSessionParser):
        def parse_file_incremental(self, *args: object, **kwargs: object):
            raise AssertionError("aggregate scans must not build ParsedSession")

    cache = UsageSummaryCache(AggregateParser(), min_rescan_seconds=60)
    day = datetime(2026, 7, 30, tzinfo=timezone.utc)
    week = datetime(2026, 7, 27, tzinfo=timezone.utc)

    summary, _ = cache.summarize(sessions, day, week)

    assert summary.tokens == 10
    assert cache._entries[current.resolve()].tail_state is not None


def test_usage_cache_parser_version_change_resets_tail_state(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "current.jsonl"
    path.write_text(
        json.dumps(_record("2026-07-30T00:00:00Z", "session_meta", {"id": "s1"}))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    parser = JsonlSessionParser()
    parser.usage_contribution_version = "v1"
    cache = UsageSummaryCache(parser, min_rescan_seconds=60)
    day = datetime(2026, 7, 30, tzinfo=timezone.utc)
    week = datetime(2026, 7, 27, tzinfo=timezone.utc)

    cache.summarize(sessions, day, week)
    old_state = cache._entries[path.resolve()].tail_state
    parser.usage_contribution_version = "v2"
    _append_record(path, _token_count("2026-07-30T00:00:01Z", 5))
    cache.summarize(
        sessions,
        day,
        week,
        allow_stale=True,
        refresh_paths=(path,),
    )

    entry = cache._entries[path.resolve()]
    assert entry.parser_version == "v2"
    assert entry.tail_state is not old_state
    assert entry.summary_day.tokens == 5


def test_persisted_session_summaries_only_reparse_changed_jsonl(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    paths = [sessions / "first.jsonl", sessions / "second.jsonl"]
    for index, path in enumerate(paths, 1):
        path.write_text(
            json.dumps(
                _record(
                    "2026-07-30T00:00:00Z",
                    "session_meta",
                    {
                        "id": f"s{index}",
                        "model_provider": "custom",
                        "cwd": str(tmp_path / f"project-{index}"),
                    },
                )
            )
            + "\n"
            + json.dumps(_token_count("2026-07-30T00:00:01Z", index * 10))
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

    class CountingParser(JsonlSessionParser):
        def __init__(self) -> None:
            super().__init__()
            self.read_paths: list[Path] = []

        def load_records_incremental(self, path: Path, state=None):
            self.read_paths.append(path.resolve())
            return super().load_records_incremental(path, state)

    day = datetime(2026, 7, 30, tzinfo=timezone.utc)
    week = datetime(2026, 7, 27, tzinfo=timezone.utc)
    database = tmp_path / "usage-summary.sqlite3"
    first_parser = CountingParser()
    first_cache = UsageSummaryCache(
        first_parser,
        summary_store=UsageSummaryStore(database),
    )

    first_total, _ = first_cache.summarize(sessions, day, week)

    assert first_total.tokens == 30
    assert set(first_parser.read_paths) == {path.resolve() for path in paths}

    restarted_parser = CountingParser()
    restarted_cache = UsageSummaryCache(
        restarted_parser,
        summary_store=UsageSummaryStore(database),
    )
    restored_total, _ = restarted_cache.summarize(sessions, day, week)

    assert restored_total == first_total
    assert restarted_parser.read_paths == []
    assert all(entry.tail_state is None for entry in restarted_cache._entries.values())
    assert restarted_cache._entries[paths[0].resolve()].workdir == str(
        tmp_path / "project-1"
    )

    rollover_parser = CountingParser()
    rollover_cache = UsageSummaryCache(
        rollover_parser,
        summary_store=UsageSummaryStore(database),
    )
    rollover_day, rollover_week = rollover_cache.summarize(
        sessions,
        datetime(2026, 7, 31, tzinfo=timezone.utc),
        week,
    )

    assert rollover_day.tokens == 0
    assert rollover_week.tokens == first_total.tokens
    assert rollover_parser.read_paths == []

    _append_record(paths[0], _token_count("2026-07-30T00:00:02Z", 25))
    changed_parser = CountingParser()
    changed_cache = UsageSummaryCache(
        changed_parser,
        summary_store=UsageSummaryStore(database),
    )
    changed_total, _ = changed_cache.summarize(sessions, day, week)

    assert changed_total.tokens == 45
    assert changed_parser.read_paths == [paths[0].resolve()]


def test_usage_cache_reprices_unchanged_session_after_price_update(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "current.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                _record(
                    "2026-07-30T00:00:00Z",
                    "session_meta",
                    {"id": "s1", "model_provider": "custom"},
                ),
                _record(
                    "2026-07-30T00:00:01Z",
                    "turn_context",
                    {"model": "gpt-6-sol"},
                ),
                _token_count("2026-07-30T00:00:02Z", 1_000_000),
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    unchanged_stat = path.stat()
    day = datetime(2026, 7, 30, tzinfo=timezone.utc)
    week = datetime(2026, 7, 27, tzinfo=timezone.utc)
    database = tmp_path / "usage-summary.sqlite3"
    parser = JsonlSessionParser(cost_estimator=CostEstimator(UsageCalculator({})))
    cache = UsageSummaryCache(
        parser,
        min_rescan_seconds=3600,
        summary_store=UsageSummaryStore(database),
    )

    unpriced_day, unpriced_week = cache.summarize(sessions, day, week)
    for summary in (unpriced_day, unpriced_week):
        assert summary.total_event_count == 1
        assert summary.priced_event_count == 0
        assert summary.cost_usd == 0

    restart_database = tmp_path / "usage-summary-before-restart.sqlite3"
    shutil.copyfile(database, restart_database)
    updated_estimator = CostEstimator(
        UsageCalculator(
            {
                "gpt-6-sol": {
                    "model": "gpt-6-sol",
                    "provider": "custom",
                    "input": 2,
                    "cached_input": 2,
                    "output": 2,
                    "reasoning": 2,
                }
            }
        )
    )
    parser.cost_estimator = updated_estimator

    updated_day, updated_week = cache.summarize(
        sessions, day, week, allow_stale=True
    )
    restarted_cache = UsageSummaryCache(
        JsonlSessionParser(cost_estimator=updated_estimator),
        summary_store=UsageSummaryStore(restart_database),
    )
    restarted_day, restarted_week = restarted_cache.summarize(sessions, day, week)

    for summary in (updated_day, updated_week, restarted_day, restarted_week):
        assert summary.total_event_count == 1
        assert summary.priced_event_count == 1
        assert summary.cost_usd == 2
    assert path.stat().st_mtime_ns == unchanged_stat.st_mtime_ns
    assert path.stat().st_size == unchanged_stat.st_size


def test_deleted_unpriced_usage_can_use_new_unscoped_price(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = day - timedelta(days=6)
    timestamp = now.isoformat()
    session_id = "00000000-0000-0000-0000-000000000001"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "deleted.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                _record(
                    timestamp,
                    "session_meta",
                    {"id": session_id, "model_provider": "custom"},
                ),
                _record(timestamp, "turn_context", {"model": "gpt-6-sol"}),
                _token_count(timestamp, 1_000_000),
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    parser = JsonlSessionParser(cost_estimator=CostEstimator(UsageCalculator({})))
    ledger = DeletedUsageLedger(tmp_path / "deleted-usage.json")
    receipt = ledger.prepare(
        session_id=session_id,
        family_session_ids=(session_id,),
        title="",
        workdir_name="",
        rollout_paths=(path,),
        parser=parser,
        now=now,
    )
    ledger.commit(receipt, now=now)
    path.unlink()
    cache = UsageSummaryCache(
        parser,
        min_rescan_seconds=3600,
        deleted_usage_ledger=ledger,
    )

    unpriced, _ = cache.summarize(sessions, day, week)
    assert (unpriced.priced_event_count, unpriced.total_event_count) == (0, 1)

    parser.cost_estimator = CostEstimator(
        UsageCalculator(
            {
                "gpt-6-sol": {
                    "model": "gpt-6-sol",
                    "provider": "custom",
                    "base_url": "https://scoped.example/v1",
                    "input": 2,
                    "cached_input": 2,
                    "output": 2,
                }
            }
        )
    )
    scoped, _ = cache.summarize(sessions, day, week, allow_stale=True)
    assert (scoped.priced_event_count, scoped.total_event_count) == (0, 1)

    parser.cost_estimator = CostEstimator(
        UsageCalculator(
            {
                "gpt-6-sol": {
                    "model": "gpt-6-sol",
                    "provider": "custom",
                    "input": 2,
                    "cached_input": 2,
                    "output": 2,
                }
            }
        )
    )
    priced, _ = cache.summarize(sessions, day, week, allow_stale=True)
    assert (priced.priced_event_count, priced.total_event_count) == (1, 1)
    assert priced.cost_usd == 2
