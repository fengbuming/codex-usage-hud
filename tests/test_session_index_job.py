"""Tests for the progressive session-index warm job (PRD §13/§14).

Covers the range bucketing helpers, persisted state round-trips, the warm-job
state machine (pause / resume / restart / incremental extend / coverage
honesty), the HTTP fallback bridge, and the progress throttle bound.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.config import UserConfigStore
from codex_usage_hud.core.session_index_job import (
    SessionIndexWarmJob,
    WarmJobSnapshot,
    WarmJobState,
    range_candidates,
    range_label,
)
from codex_usage_hud.core.session_search import (
    DEFAULT_RANGE,
    RANGE_OPTIONS,
    range_days,
    wider_range_key,
)
from codex_usage_hud.settings_bridge import SettingsBridgeServer


def _wait_until(predicate, timeout: float = 8.0, interval: float = 0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _make_entries(
    tmp_path: Path,
    ages_days: dict[str, float],
) -> list[tuple[str, tuple[Path, ...], str, str, str, str]]:
    """Build candidate tuples whose rollout mtime encodes ``ages_days``."""
    now = time.time()
    entries: list[tuple[str, tuple[Path, ...], str, str, str, str]] = []
    for session_id, age_days in ages_days.items():
        path = tmp_path / f"{session_id}.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        stamp = now - max(0.0, float(age_days)) * 86_400.0
        os.utime(path, (stamp, stamp))
        entries.append(
            (session_id, (path,), f"title-{session_id}", "/cwd", "provider", "cli")
        )
    return entries


class _FakeSearchIndex:
    """Minimal SessionSearchIndex surface used by the warm job."""

    def __init__(
        self,
        entries: list[tuple[str, tuple[Path, ...], str, str, str, str]],
        *,
        delay_per_entry: float = 0.0,
        load_side_effect=None,
    ) -> None:
        self._entries = list(entries)
        self._committed: list[str] = []
        self._delay = delay_per_entry
        self.sync_calls: list[list[str]] = []
        self.removed_keeps: list[set[str]] = []
        self.write_calls: list[bool] = []
        self.ensure_scan_index_calls = 0
        self.load_calls = 0
        self.clear_calls = 0
        self.stale_ids: set[str] = set()
        self._load_side_effect = load_side_effect

    def search_index_entries(self):
        return list(self._entries)

    def indexed_session_ids(self) -> frozenset[str]:
        return frozenset(self._committed)

    def stale_session_ids(self, entries) -> frozenset[str]:
        del entries
        return frozenset(self.stale_ids)

    def remove_missing(self, session_ids) -> None:
        # Mirror the real index: ``session_ids`` is the keep set, everything
        # else is deleted from the committed set.
        keep = {
            str(item).strip()
            for item in session_ids
            if str(item).strip()
        }
        self._committed = [sid for sid in self._committed if sid in keep]
        self.removed_keeps.append(keep)

    def write_snapshot(self, *, force: bool = False) -> bool:
        self.write_calls.append(bool(force))
        return True

    def ensure_scan_index(self) -> bool:
        self.ensure_scan_index_calls += 1
        return True

    def disk_usage_bytes(self) -> int:
        return len(self._committed)

    def clear_index(self) -> dict[str, object]:
        self.clear_calls += 1
        cleared = len(self._committed)
        self._committed.clear()
        return {"clearedBytes": cleared, "removedFiles": 2, "diskBytes": 0}

    def load(self) -> dict[str, object]:
        self.load_calls += 1
        if self._load_side_effect is not None:
            if isinstance(self._load_side_effect, Exception):
                raise self._load_side_effect
            self._load_side_effect()
        return {"indexed": len(self._committed), "indexAvailable": True}

    def sync_batches(
        self,
        entries,
        *,
        total: int | None = None,
        batch_size: int = 24,
        progress_callback=None,
        cancelled=None,
        write_snapshot: bool = True,
    ) -> int:
        del batch_size, write_snapshot
        ids = [str(entry[0]).strip() for entry in entries]
        self.sync_calls.append(ids)
        processed = 0
        for entry in entries:
            if callable(cancelled) and cancelled():
                break
            if self._delay:
                time.sleep(self._delay)
            if callable(cancelled) and cancelled():
                break
            self._committed.append(str(entry[0]).strip())
            processed += 1
            if callable(progress_callback):
                progress_callback(
                    processed,
                    max(0, int(total or 0)),
                    len(self._committed),
                )
        return processed


# ---------------------------------------------------------------------------
# range helpers
# ---------------------------------------------------------------------------


def test_range_helpers_map_options_and_fallbacks() -> None:
    assert RANGE_OPTIONS == ("1m", "3m", "6m", "1y", "all")
    assert DEFAULT_RANGE == "1m"
    assert range_days("1m") == 30
    assert range_days("3m") == 91
    assert range_days("6m") == 183
    assert range_days("1y") == 365
    assert range_days("all") is None
    assert range_days("bogus") == 30
    assert range_label("1m") == "最近 1 个月"
    assert range_label("ALL") == "全部"
    assert range_label("bogus") == "最近 1 个月"


def test_range_candidates_newest_first_mtime_window_and_delta(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"newest": 0.1, "recent": 3, "oldish": 300})
    month = range_candidates(list(entries), "1m")
    assert [entry[0] for entry in month] == ["newest", "recent"]
    quarter = range_candidates(list(entries), "3m")
    assert {entry[0] for entry in quarter} == {"newest", "recent"}
    delta = range_candidates(
        list(entries),
        "1m",
        covered_ids=frozenset({"newest"}),
    )
    assert [entry[0] for entry in delta] == ["recent"]


def test_wider_range_key_picks_widest_valid_option() -> None:
    assert wider_range_key("1m", "3m") == "3m"
    assert wider_range_key("6m", "1m") == "6m"
    assert wider_range_key("all", "1y") == "all"
    assert wider_range_key("3m") == "3m"
    assert wider_range_key("1m", "1m") == "1m"
    # Unknown or empty keys degrade to the default month, never widen.
    assert wider_range_key("bogus", "3m") == "3m"
    assert wider_range_key("bogus") == DEFAULT_RANGE
    assert wider_range_key("") == DEFAULT_RANGE


# ---------------------------------------------------------------------------
# persisted state
# ---------------------------------------------------------------------------


def test_warm_job_state_roundtrip_and_corruption(tmp_path: Path) -> None:
    path = tmp_path / "warm.json"
    state = WarmJobState(
        selected_range="6m",
        completed_range="1m",
        coverage_boundary=123.5,
        job_state="paused",
        cursor="7",
        built_count=7,
        total_count=9,
        started_at=1.0,
        updated_at=2.0,
        last_error="",
    )
    state.dump(path)
    assert WarmJobState.load(path) == state
    path.write_text("{broken json", encoding="utf-8")
    assert WarmJobState.load(path) == WarmJobState()
    assert WarmJobState.load(tmp_path / "missing.json") == WarmJobState()


# ---------------------------------------------------------------------------
# warm job state machine
# ---------------------------------------------------------------------------


def test_warm_job_builds_default_range_and_finishes(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"a": 5, "b": 10, "c": 20})
    index = _FakeSearchIndex(entries)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        status = job.status()
        assert status["coverage"] == "range_done(1m)"
        assert status["builtCount"] == 3
        assert status["canExtend"] is True
        assert sorted(index.indexed_session_ids()) == ["a", "b", "c"]
    finally:
        job.close()


def test_warm_job_rebuilds_indexed_sessions_with_changed_rollouts(
    tmp_path: Path,
) -> None:
    entries = _make_entries(tmp_path, {"a": 5, "b": 10})
    index = _FakeSearchIndex(entries)
    index._committed = ["a"]
    index.stale_ids = {"a"}
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert index.sync_calls == [["a", "b"]]
    finally:
        job.close()


def test_index_feature_switch_persists_and_reenables_building(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"a": 5, "b": 10})
    state_path = tmp_path / "warm.json"
    index = _FakeSearchIndex(entries)
    job = SessionIndexWarmJob(index, state_path=state_path)
    try:
        assert job.set_enabled(False) is True
        assert job.status()["enabled"] is False
        assert WarmJobState.load(state_path).enabled is False
        assert job.start("1m") is False
        assert index.sync_calls == []

        assert job.set_enabled(True) is True
        assert job.status()["enabled"] is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert index.sync_calls == [["a", "b"]]
    finally:
        job.close()


def test_clear_index_disables_rebuild_and_allows_explicit_new_range(
    tmp_path: Path,
) -> None:
    entries = _make_entries(
        tmp_path,
        {"new": 5, "mid": 45, "old": 200},
    )
    state_path = tmp_path / "warm.json"
    index = _FakeSearchIndex(entries)
    lifecycle: list[str] = []
    job = SessionIndexWarmJob(
        index,
        state_path=state_path,
        before_clear=lambda: lifecycle.append("before") or True,
        after_clear=lambda: lifecycle.append("after"),
    )
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert sorted(index.indexed_session_ids()) == ["new"]

        cleared = job.clear_index()
        assert cleared["accepted"] is True
        assert lifecycle == ["before", "after"]
        assert index.clear_calls == 1
        status = job.status()
        assert status["coverage"] == "empty"
        assert status["selectedRange"] == "1m"
        assert status["builtCount"] == 0
        assert status["totalCount"] == 0
        assert status["diskBytes"] == 0
        assert status["enabled"] is False
        assert index.indexed_session_ids() == frozenset()
        persisted = WarmJobState.load(state_path)
        assert persisted.enabled is False
        assert persisted.completed_range == ""
        assert persisted.built_count == 0
        assert persisted.total_count == 0

        # A delayed startup-prime or filesystem event cannot recreate the
        # physically deleted artifacts after clear.
        assert job.start("all") is False
        assert index.indexed_session_ids() == frozenset()

        assert job.set_enabled(True, "6m") is True
        assert _wait_until(
            lambda: job.status()["jobState"] == "idle"
            and job.status()["coverage"] == "range_done(6m)"
        )
        assert sorted(index.indexed_session_ids()) == ["mid", "new"]
    finally:
        job.close()


def test_warm_job_start_announces_scanning_phase_immediately(tmp_path: Path) -> None:
    # The UI must see an explicit "scanning" phase the instant start() is
    # called, before the first real progress frame, so the silent enumeration
    # window never looks frozen (UX regression guard).
    entries = _make_entries(tmp_path, {"a": 5, "b": 10})
    index = _FakeSearchIndex(entries)
    seen_phases: list[str] = []
    job = SessionIndexWarmJob(
        index,
        state_path=tmp_path / "warm.json",
        progress_callback=lambda s: seen_phases.append(s.phase),
    )
    try:
        # start() force-publishes under the same lock, so the callback fires
        # synchronously before start() returns.
        assert job.start("1m") is True
        assert job.status()["phase"] == "scanning"
        assert "scanning" in seen_phases
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["phase"] == ""
    finally:
        job.close()


def test_warm_job_extend_announces_scanning_phase_immediately(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"a": 5, "b": 10, "c": 20, "d": 200})
    index = _FakeSearchIndex(entries)
    seen_phases: list[str] = []
    job = SessionIndexWarmJob(
        index,
        state_path=tmp_path / "warm.json",
        progress_callback=lambda s: seen_phases.append(s.phase),
    )
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        # Extend to a wider range: the live channel must flip to scanning at
        # once, not stay on the completed idle state.
        assert job.extend("3m") is True
        assert job.status()["phase"] == "scanning"
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["phase"] == ""
    finally:
        job.close()


def test_warm_job_phase_transitions_scanning_to_indexing(tmp_path: Path) -> None:
    # With a perceptible per-entry delay the streaming "indexing" phase must be
    # observed between the initial "scanning" and the terminal "".
    entries = _make_entries(tmp_path, {f"s{i}": i for i in range(6)})
    index = _FakeSearchIndex(entries, delay_per_entry=0.12)
    seen_phases: list[str] = []
    job = SessionIndexWarmJob(
        index,
        state_path=tmp_path / "warm.json",
        progress_callback=lambda s: seen_phases.append(s.phase),
    )
    try:
        assert job.start("1m") is True
        assert "scanning" in seen_phases
        assert _wait_until(lambda: job.status()["phase"] == "indexing", timeout=6)
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["phase"] == ""
        assert "indexing" in seen_phases
    finally:
        job.close()


def test_warm_job_preloads_resident_snapshot_after_finish(tmp_path: Path) -> None:
    # The warm job must pull the resident-snapshot deserialisation off the
    # interactive path by calling ``load()`` in the background worker once the
    # range is done (PRD §14.1 "seconds to first result").
    entries = _make_entries(tmp_path, {"a": 5, "b": 10, "c": 20})
    index = _FakeSearchIndex(entries)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert _wait_until(lambda: index.load_calls >= 1)
        assert job.status()["coverage"] == "range_done(1m)"
    finally:
        job.close()


def test_warm_job_preload_failure_never_fails_the_job(tmp_path: Path) -> None:
    # A failing ``load()`` (e.g. corrupt snapshot) is best-effort: it must not
    # flip the job into ``error`` or lose the completed coverage (PRD §12).
    entries = _make_entries(tmp_path, {"a": 5})
    index = _FakeSearchIndex(entries, load_side_effect=RuntimeError("boom"))
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert _wait_until(lambda: index.load_calls >= 1)
        status = job.status()
        assert status["coverage"] == "range_done(1m)"
        assert status["jobState"] == "idle"
        assert status["error"] == ""
    finally:
        job.close()


def test_finalize_defers_preload_past_grace_window(tmp_path: Path) -> None:
    # The finalize thread's snapshot unpickle is GIL-heavy; with a grace
    # window configured it must not start (and starve the interactive
    # startup payload) until the window has elapsed.
    entries = _make_entries(tmp_path, {"a": 5})
    index = _FakeSearchIndex(entries)
    job = SessionIndexWarmJob(
        index,
        state_path=tmp_path / "warm.json",
        preload_grace_seconds=0.6,
    )
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        time.sleep(0.25)
        assert index.load_calls == 0
        assert _wait_until(lambda: index.load_calls >= 1, timeout=4.0)
    finally:
        job.close()


def test_finalize_skips_preload_when_closed_during_grace(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"a": 5})
    index = _FakeSearchIndex(entries)
    job = SessionIndexWarmJob(
        index,
        state_path=tmp_path / "warm.json",
        preload_grace_seconds=0.6,
    )
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        job.close()
        time.sleep(1.0)
        assert index.load_calls == 0
    finally:
        job.close()


def test_finalize_rewrites_snapshot_after_load_repair(tmp_path: Path) -> None:
    # When the load reconciled stale/missing documents from SQLite rows, the
    # finalize thread must rewrite the pickle so the repair is not repeated
    # on every restart.
    class RepairingIndex(_FakeSearchIndex):
        def load(self) -> dict[str, object]:
            self.load_calls += 1
            return {"indexed": 3, "memoryLoaded": True, "reconciled": 3}

    entries = _make_entries(tmp_path, {"a": 5})
    index = RepairingIndex(entries)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: index.load_calls >= 1)
        assert _wait_until(lambda: index.write_calls.count(True) >= 1)
        assert index.ensure_scan_index_calls >= 1
    finally:
        job.close()


def test_finalize_skips_snapshot_write_without_repair(tmp_path: Path) -> None:
    # A clean load (nothing reconciled) after a no-op pass must not rewrite
    # the ~100 MB pickle. Pre-committing the only candidate makes the warm
    # job take the empty-bucket path: no build mutates the index, so
    # ``pending_write`` stays False and the finalize thread only loads.
    entries = _make_entries(tmp_path, {"a": 5})
    index = _FakeSearchIndex(entries)
    index._committed = ["a"]
    job = SessionIndexWarmJob(
        index,
        state_path=tmp_path / "warm.json",
        preload_grace_seconds=0.6,
    )
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: index.load_calls >= 1)
        time.sleep(0.1)
        assert index.write_calls == []
    finally:
        job.close()


def test_finalize_rewrites_snapshot_on_reconcile_without_build(tmp_path: Path) -> None:
    # The reconcile-driven convergence write must fire even when no build
    # mutated the index (``pending_write`` is False): a stale/missing snapshot
    # that ``load`` had to rebuild from SQLite rows is repaired once, then the
    # pickle is rewritten so the rebuild cost is not paid on every restart.
    class ReconcilingNoBuild(_FakeSearchIndex):
        def load(self) -> dict[str, object]:
            self.load_calls += 1
            return {
                "indexed": 3,
                "indexAvailable": True,
                "memoryLoaded": True,
                "reconciled": 3,
            }

    entries = _make_entries(tmp_path, {"a": 5})
    index = ReconcilingNoBuild(entries)
    index._committed = ["a"]  # no-op pass -> pending_write stays False
    job = SessionIndexWarmJob(
        index,
        state_path=tmp_path / "warm.json",
        preload_grace_seconds=0.6,
    )
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: index.load_calls >= 1)
        assert _wait_until(lambda: index.write_calls.count(True) >= 1)
        assert index.ensure_scan_index_calls >= 1
    finally:
        job.close()


def test_warm_job_skips_preload_without_load_capability(tmp_path: Path) -> None:
    # A surface without ``load`` (e.g. a partial manager that only exposes
    # search_index_entries/sync_batches) must not crash the warm job.
    class NoLoadIndex:
        def __init__(self, entries: list) -> None:
            self._entries = list(entries)
            self._committed: list[str] = []

        def search_index_entries(self):
            return list(self._entries)

        def indexed_session_ids(self) -> frozenset[str]:
            return frozenset(self._committed)

        def sync_batches(self, entries, *, total=None, batch_size=24,
                         progress_callback=None, cancelled=None,
                         write_snapshot: bool = True) -> int:
            del total, batch_size, progress_callback, cancelled, write_snapshot
            self._committed.extend(str(e[0]) for e in entries)
            return len(entries)

    entries = _make_entries(tmp_path, {"a": 5})
    index = NoLoadIndex(entries)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["coverage"] == "range_done(1m)"
    finally:
        job.close()


def test_pause_preserves_batches_and_resume_completes(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {f"s{i}": 1 + i * 2 for i in range(10)})
    index = _FakeSearchIndex(entries, delay_per_entry=0.02)
    state_path = tmp_path / "warm.json"
    job = SessionIndexWarmJob(index, state_path=state_path)
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: int(job.status()["builtCount"]) >= 3)
        assert job.pause() is True
        assert _wait_until(lambda: job.status()["jobState"] == "paused")
        paused = job.status()
        built = int(paused["builtCount"])
        assert 0 < built < 10
        persisted = WarmJobState.load(state_path)
        assert persisted.job_state == "paused"
        assert persisted.cursor == str(built)
        assert job.resume() is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["coverage"] == "range_done(1m)"
        assert len(index.indexed_session_ids()) == 10
    finally:
        job.close()


def test_restart_resumes_from_persisted_paused_state(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {f"r{i}": 1 + i for i in range(8)})
    state_path = tmp_path / "warm.json"
    persisted = WarmJobState(
        selected_range="1m",
        job_state="paused",
        cursor="3",
        built_count=3,
        total_count=8,
        started_at=time.time() - 10,
        updated_at=time.time() - 9,
    )
    persisted.dump(state_path)
    index = _FakeSearchIndex(entries, delay_per_entry=0.01)
    # The real database keeps the first three rows across a restart.
    index._committed = ["r0", "r1", "r2"]
    job = SessionIndexWarmJob(index, state_path=state_path)
    try:
        assert WarmJobState.load(state_path).job_state == "paused"
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["coverage"] == "range_done(1m)"
        assert len(index.indexed_session_ids()) == 8
        assert index.sync_calls == [["r3", "r4", "r5", "r6", "r7"]]
    finally:
        job.close()


def test_persisted_running_state_loads_as_paused_and_resumes_range(
    tmp_path: Path,
) -> None:
    # A ``running`` job_state on disk means the previous process died
    # mid-build. It must load as ``paused`` so the next start() (the startup
    # prime passes the configured default) resumes the persisted wider range
    # from the covered set instead of resetting to the default month
    # (PRD §7.2: unfinished ranges resume across restarts).
    entries = _make_entries(
        tmp_path, {"a": 5, "b": 10, "c": 45, "d": 60, "e": 75, "old": 200}
    )
    state_path = tmp_path / "warm.json"
    WarmJobState(
        selected_range="3m",
        job_state="running",
        built_count=4,
        total_count=5,
        started_at=time.time() - 30,
        updated_at=time.time() - 1,
    ).dump(state_path)
    index = _FakeSearchIndex(entries)
    # The real database keeps everything the killed run had committed.
    index._committed = ["a", "b", "c", "d"]
    assert WarmJobState.load(state_path).job_state == "paused"
    job = SessionIndexWarmJob(index, state_path=state_path)
    try:
        assert job.status()["jobState"] == "paused"
        assert job.status()["selectedRange"] == "3m"
        assert job.start("1m") is True
        assert _wait_until(
            lambda: job.status()["jobState"] == "idle"
            and job.status()["coverage"] == "range_done(3m)"
        )
        assert index.sync_calls == [["e"]]
        assert sorted(index.indexed_session_ids()) == ["a", "b", "c", "d", "e"]
    finally:
        job.close()


def test_extend_appends_only_older_sessions(tmp_path: Path) -> None:
    entries = _make_entries(
        tmp_path,
        {
            "new-a": 5,
            "new-b": 10,
            "mid-c": 45,
            "mid-d": 60,
            "old-e": 200,
            "old-f": 300,
        },
    )
    index = _FakeSearchIndex(entries)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["coverage"] == "range_done(1m)"
        assert sorted(index.indexed_session_ids()) == ["new-a", "new-b"]
        assert index.sync_calls == [["new-a", "new-b"]]

        assert job.extend("3m") is True
        assert _wait_until(
            lambda: job.status()["jobState"] == "idle"
            and job.status()["coverage"] == "range_done(3m)"
        )
        assert sorted(index.indexed_session_ids()) == ["mid-c", "mid-d", "new-a", "new-b"]
        assert index.sync_calls == [["new-a", "new-b"], ["mid-c", "mid-d"]]

        assert job.extend("all") is True
        assert _wait_until(
            lambda: job.status()["jobState"] == "idle"
            and job.status()["coverage"] == "full"
        )
        assert sorted(index.indexed_session_ids()) == [
            "mid-c",
            "mid-d",
            "new-a",
            "new-b",
            "old-e",
            "old-f",
        ]
    finally:
        job.close()


def test_extend_while_running_never_claims_unbuilt_range(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"a": 5, "b": 10, "c": 45, "d": 60})
    index = _FakeSearchIndex(entries, delay_per_entry=0.03)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: int(job.status()["builtCount"]) >= 1)
        assert job.extend("3m") is True
        assert job.status()["selectedRange"] == "3m"

        seen_range_done_while_busy = False
        saw_honest_partial = False
        deadline = time.time() + 8.0
        while True:
            status = job.status()
            if status["jobState"] != "idle" and status["coverage"] == "range_done(3m)":
                seen_range_done_while_busy = True
            if status["coverage"] == "partial(3m)":
                saw_honest_partial = True
            if (
                status["jobState"] == "idle"
                and status["coverage"] == "range_done(3m)"
            ):
                break
            if time.time() > deadline:
                break
            time.sleep(0.01)
        assert not seen_range_done_while_busy
        assert saw_honest_partial
        assert sorted(index.indexed_session_ids()) == ["a", "b", "c", "d"]
    finally:
        job.close()


def test_extend_narrower_than_selected_is_noop(tmp_path: Path) -> None:
    # A narrower extend is already satisfied by the selected wider range:
    # accepted as a no-op, never shrinking the coverage window (PRD §5.1).
    entries = _make_entries(tmp_path, {"a": 5, "b": 45})
    index = _FakeSearchIndex(entries)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.extend("3m") is True
        assert _wait_until(
            lambda: job.status()["jobState"] == "idle"
            and job.status()["coverage"] == "range_done(3m)"
        )
        assert index.sync_calls == [["a"], ["b"]]

        assert job.extend("1m") is True
        assert job.status()["selectedRange"] == "3m"
        assert job.status()["jobState"] == "idle"
        assert index.sync_calls == [["a"], ["b"]]
    finally:
        job.close()


def test_start_never_narrows_completed_range(tmp_path: Path) -> None:
    # PRD §5.1: an extension is remembered as the new coverage boundary. A
    # later start() with a narrower range (e.g. the configured default passed
    # at startup) must keep the wider selection and degenerate to a no-op diff
    # instead of relabelling -- and trimming -- the managed index.
    entries = _make_entries(tmp_path, {"a": 5, "b": 10, "c": 45, "d": 60})
    index = _FakeSearchIndex(entries)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.extend("3m") is True
        assert _wait_until(
            lambda: job.status()["jobState"] == "idle"
            and job.status()["coverage"] == "range_done(3m)"
        )
        assert index.sync_calls == [["a", "b"], ["c", "d"]]

        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        status = job.status()
        assert status["selectedRange"] == "3m"
        assert status["coverage"] == "range_done(3m)"
        assert index.sync_calls == [["a", "b"], ["c", "d"]]
        assert sorted(index.indexed_session_ids()) == ["a", "b", "c", "d"]
    finally:
        job.close()


def test_trim_reconciliation_migrates_legacy_index_once(tmp_path: Path) -> None:
    # A pre-progressive full index holds documents beyond the selected range;
    # the first warm pass reconciles them away so coverage stays honest.
    entries = _make_entries(tmp_path, {"a": 5, "b": 10, "legacy": 200})
    index = _FakeSearchIndex(entries)
    index._committed = ["a", "legacy"]
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert index.removed_keeps == [{"a", "b"}]
        assert sorted(index.indexed_session_ids()) == ["a", "b"]
        assert job.status()["coverage"] == "range_done(1m)"
    finally:
        job.close()


def test_trim_reconciliation_skips_warm_job_owned_index(tmp_path: Path) -> None:
    # Once the warm job completed a range (completed_range set) the index is
    # warm-job-owned: reconciliation must not delete documents that merely
    # aged out of the current window (PRD D3: committed batches are kept).
    entries = _make_entries(tmp_path, {"a": 5, "b": 10, "aged-out": 200})
    state_path = tmp_path / "warm.json"
    WarmJobState(
        selected_range="1m",
        completed_range="1m",
        job_state="idle",
        built_count=2,
        total_count=2,
    ).dump(state_path)
    index = _FakeSearchIndex(entries)
    index._committed = ["a", "b", "aged-out"]
    job = SessionIndexWarmJob(index, state_path=state_path)
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert index.removed_keeps == []
        assert sorted(index.indexed_session_ids()) == ["a", "aged-out", "b"]
        assert job.status()["coverage"] == "range_done(1m)"
    finally:
        job.close()


def test_attach_and_cancel_ui_never_stop_the_job(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {f"t{i}": 1 + i for i in range(12)})
    index = _FakeSearchIndex(entries, delay_per_entry=0.03)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(
            lambda: job.status()["jobState"] in ("running", "attached")
            and int(job.status()["builtCount"]) >= 1
        )
        assert job.attach() is True
        assert _wait_until(lambda: job.status()["jobState"] == "attached")
        attached = job.status()
        assert 0 < int(attached["builtCount"]) < 12
        assert job.cancel_ui() is True
        assert job.status()["jobState"] == "running"
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert len(index.indexed_session_ids()) == 12
    finally:
        job.close()


def test_control_action_mapping_and_unknown_action(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"a": 1, "b": 2})
    index = _FakeSearchIndex(entries, delay_per_entry=0.02)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        response = job.control({"control": "explode", "requestId": "r1"})
        assert response["accepted"] is False
        assert response["error"] == "unknown_action"
        assert response["requestId"] == "r1"
        assert job.status()["jobState"] == "idle"

        response = job.control({"control": "start", "range": "1m", "requestId": "r2"})
        assert response["accepted"] is True
        assert response["requestId"] == "r2"
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.control({"control": "pause"})["accepted"] is False
        assert job.control({"control": "cancel_ui"})["accepted"] is True
    finally:
        job.close()


def test_progress_publish_throttled_to_four_hz(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {f"p{i}": 1 + i for i in range(10)})
    index = _FakeSearchIndex(entries, delay_per_entry=0.05)
    events: list[tuple[float, WarmJobSnapshot]] = []

    def record(snapshot: WarmJobSnapshot) -> None:
        events.append((time.monotonic(), snapshot))

    job = SessionIndexWarmJob(
        index,
        state_path=tmp_path / "warm.json",
        progress_callback=record,
    )
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        final_status = job.status()
    finally:
        job.close()

    assert len(events) >= 2
    assert len(events) <= 6
    timestamps = [stamp for stamp, _snapshot in events]
    gaps = [
        timestamps[i + 1] - timestamps[i]
        for i in range(len(timestamps) - 1)
    ]
    # Terminal idle/error transitions are deliberately delivered immediately
    # so a fast job cannot leave the attached UI stuck in "indexing". Only
    # non-terminal progress notifications are subject to the 4 Hz throttle.
    assert all(gap >= 0.20 for gap in gaps[:-1])
    counts = [snapshot.built_count for _stamp, snapshot in events]
    assert counts == sorted(counts)
    assert final_status["jobState"] == "idle"
    assert final_status["coverage"] == "range_done(1m)"


def test_extend_publishes_range_change_before_worker_bucket(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {f"e{i}": 1 + i for i in range(6)})
    index = _FakeSearchIndex(entries)
    events: list[WarmJobSnapshot] = []
    job = SessionIndexWarmJob(
        index,
        state_path=tmp_path / "warm.json",
        progress_callback=events.append,
    )
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        events.clear()
        assert job.extend("3m") is True
        # The live sessionIndex channel must report the new range on the frame
        # the extend is accepted. Without this publish, a renderer push landing
        # before the worker's first bucket publish would show "最近1个月" as
        # the current range while a 3m job is already running.
        assert events, "extend() 必须立刻发布一次进度，否则实时通道落后于命令响应"
        assert events[0].selected_range == "3m"
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
    finally:
        job.close()


def test_pause_announces_paused_state_to_attached_ui(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {f"s{i}": 1 + i * 2 for i in range(10)})
    index = _FakeSearchIndex(entries, delay_per_entry=0.02)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert job.attach() is True
        assert _wait_until(lambda: int(job.status()["builtCount"]) >= 3)
        assert job.pause() is True
        # The ``paused`` transition is published by the worker, and a detached
        # job publishes no runtime event at all. Detaching on pause would
        # therefore leave the panel stuck on "索引中" until an unrelated
        # full refresh happened to clear the command status.
        assert job.is_attached() is True
        assert _wait_until(lambda: job.status()["jobState"] == "paused")
    finally:
        job.close()


def test_attached_progress_callback_exposes_attachment_state(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"a": 1})
    index = _FakeSearchIndex(entries)
    state_path = tmp_path / "warm.json"
    WarmJobState(job_state="running").dump(state_path)
    job = SessionIndexWarmJob(index, state_path=state_path)
    try:
        assert job.is_attached() is False
        assert job.attach() is True
        assert job.is_attached() is True
        assert job.cancel_ui() is True
        assert job.is_attached() is False
    finally:
        job.close()


def test_job_fails_cleanly_when_surface_is_missing(tmp_path: Path) -> None:
    class Broken:
        pass

    job = SessionIndexWarmJob(Broken(), state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "error")
        assert job.status()["error"] == "search_index_entries unavailable"
        assert WarmJobState.load(tmp_path / "warm.json").job_state == "error"
    finally:
        job.close()


def test_job_resolves_capabilities_from_manager_and_core(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"m1": 1, "m2": 2})
    core = _FakeSearchIndex(entries)

    class ManagerLike:
        _search_index = core

        def search_index_entries(self):
            return core.search_index_entries()

    job = SessionIndexWarmJob(ManagerLike(), state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["coverage"] == "range_done(1m)"
        assert len(core.indexed_session_ids()) == 2
    finally:
        job.close()


# ---------------------------------------------------------------------------
# HTTP fallback bridge (PRD §9.4)
# ---------------------------------------------------------------------------


def _start_bridge(
    store: UserConfigStore,
    *,
    status=None,
    control=None,
) -> SettingsBridgeServer:
    bridge = SettingsBridgeServer(
        store,
        port=0,
        session_index_status_callback=status,
        session_index_control_callback=control,
    )
    bridge.start()
    return bridge


def test_session_index_http_requires_token_and_maps_payloads(tmp_path: Path) -> None:
    store = UserConfigStore(Path(tmp_path) / "hud_settings.json")
    status = _FakeCallback(
        {
            "jobState": "running",
            "coverage": "partial(1m)",
            "builtCount": 3,
            "totalCount": 9,
            "selectedRange": "1m",
        }
    )
    control = _FakeCallback({"accepted": True, "jobState": "running"})
    bridge = _start_bridge(store, status=status.call, control=control.call)
    try:
        with pytest.raises(HTTPError) as denied:
            urlopen(f"{bridge.url}/session-index/status", timeout=2)
        assert denied.value.code == 403
        assert status.calls == 0

        with urlopen(bridge.session_index_url, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["status"] == "ok"
        assert payload["sessionIndex"]["jobState"] == "running"
        assert status.calls == 1

        control_url = bridge.session_index_url.replace(
            "/session-index/status?",
            "/session-index/control?",
        )
        request = Request(
            control_url,
            data=json.dumps({"control": "extend", "range": "3m"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            control_payload = json.loads(response.read().decode("utf-8"))
        assert control_payload["sessionIndex"]["accepted"] is True
        assert control.calls == 1
        assert control.received["accessTokenCheck"] is True
        assert control.received["control"] == "extend"
    finally:
        bridge.close()


def test_session_index_http_503_without_callbacks(tmp_path: Path) -> None:
    store = UserConfigStore(Path(tmp_path) / "hud_settings.json")
    bridge = _start_bridge(store)
    try:
        with pytest.raises(HTTPError) as denied:
            urlopen(bridge.session_index_url, timeout=2)
        assert denied.value.code == 503
        control_url = bridge.session_index_url.replace(
            "/session-index/status?",
            "/session-index/control?",
        )
        request = Request(
            control_url,
            data=json.dumps({"control": "pause"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as denied_control:
            urlopen(request, timeout=2)
        assert denied_control.value.code == 503
    finally:
        bridge.close()


class _FakeCallback:
    def __init__(self, result: dict[str, object]) -> None:
        self._result = dict(result)
        self.calls = 0
        self.received: dict[str, object] = {}

    def call(self, *args, **kwargs) -> dict[str, object]:
        self.calls += 1
        self.received.update(kwargs)
        if args:
            body = args[0]
            if isinstance(body, dict):
                self.received.update(body)
        return dict(self._result)
