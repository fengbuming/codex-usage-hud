from __future__ import annotations

from pathlib import Path
import threading
import time
from types import SimpleNamespace
from codex_usage_hud.core import JsonlTailState, ParsedSession
from codex_usage_hud.session_snapshots import SessionSnapshotCache


class _EventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def publish(self, event_type: str, **payload: object) -> None:
        self.events.append((event_type, payload))


class _BlockingParser:
    def __init__(self, *, snapshot: ParsedSession | None = None) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.snapshot = snapshot or ParsedSession(status="parsed")

    def parse_file_tail_preview(self, path: Path, **kwargs: object) -> ParsedSession:
        del path, kwargs
        return ParsedSession(status="preview")

    def parse_file_incremental(self, path: Path, state: object, **kwargs: object):
        del path, state, kwargs
        self.started.set()
        self.release.wait(timeout=2)
        return self.snapshot, JsonlTailState(file_id=(1, 1))

    def _file_id(self, path: Path, stat: object) -> tuple[int, int]:
        del path, stat
        return (1, 1)


def test_pending_canonical_session_id_wins_during_hydration(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    parser = _BlockingParser()
    cache = SessionSnapshotCache(parser, preview_bytes=32)
    try:
        cache.snapshot_for(path)
        assert parser.started.wait(timeout=1)
        cache.snapshot_for(path, session_id="canonical-id")
        parser.release.set()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            result = cache.snapshot_for(path, session_id="canonical-id")
            if result.status == "parsed":
                break
            time.sleep(0.01)
        assert result.session_id == "canonical-id"
    finally:
        parser.release.set()
        cache.close()


def test_close_during_parse_suppresses_cache_store_and_event(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    parser = _BlockingParser(snapshot=ParsedSession(session_id="late", status="parsed"))
    events = _EventBus()
    cache = SessionSnapshotCache(parser, event_bus=events, preview_bytes=32)
    cache.snapshot_for(path)
    assert parser.started.wait(timeout=1)

    cache.close()
    parser.release.set()
    time.sleep(0.05)

    assert not events.events
    assert not cache._entries
    cache.close()


def test_price_change_rehydrates_unchanged_session(tmp_path: Path) -> None:
    class PricingParser(_BlockingParser):
        def __init__(self) -> None:
            super().__init__()
            self.cost_estimator = SimpleNamespace(pricing_fingerprint="old")
            self.release.set()

        def parse_file_incremental(self, path: Path, state: object, **kwargs: object):
            del path, state, kwargs
            return (
                ParsedSession(status=self.cost_estimator.pricing_fingerprint),
                JsonlTailState(file_id=(1, 1)),
            )

    path = tmp_path / "session.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    parser = PricingParser()
    cache = SessionSnapshotCache(parser)
    try:
        cache.snapshot_for(path)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and cache.snapshot_for(path).status != "old":
            time.sleep(0.01)
        assert cache.snapshot_for(path).status == "old"

        parser.cost_estimator.pricing_fingerprint = "new"
        assert cache.snapshot_for(path).status != "old"
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and cache.snapshot_for(path).status != "new":
            time.sleep(0.01)
        assert cache.snapshot_for(path).status == "new"
    finally:
        cache.close()


def test_price_change_during_hydration_discards_old_result(tmp_path: Path) -> None:
    class PricingParser(_BlockingParser):
        def __init__(self) -> None:
            super().__init__()
            self.cost_estimator = SimpleNamespace(pricing_fingerprint="old")

        def parse_file_incremental(self, path: Path, state: object, **kwargs: object):
            del path, state, kwargs
            version = self.cost_estimator.pricing_fingerprint
            self.started.set()
            self.release.wait(timeout=2)
            return ParsedSession(status=version), JsonlTailState(file_id=(1, 1))

    path = tmp_path / "session.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    parser = PricingParser()
    events = _EventBus()
    cache = SessionSnapshotCache(parser, event_bus=events)
    try:
        cache.snapshot_for(path)
        assert parser.started.wait(timeout=1)
        parser.cost_estimator.pricing_fingerprint = "new"
        parser.release.set()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and cache.snapshot_for(path).status != "new":
            time.sleep(0.01)
        assert cache.snapshot_for(path).status == "new"
        assert len(events.events) == 1
    finally:
        parser.release.set()
        cache.close()
