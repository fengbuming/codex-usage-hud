from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import time
from types import SimpleNamespace

from codex_usage_hud.config import UserConfig
from codex_usage_hud.core.runtime_events import RuntimeEventBus
from codex_usage_hud.usage_insights import (
    UsageInsightsWorker,
    _refresh_usage_after_session_delete,
    _usage_insights_windows,
)


def test_ranking_projects_all_titles_in_one_bulk_lookup(tmp_path):
    from codex_usage_hud.usage_insights import build_usage_insights_payload

    calls = []

    def titles(ids):
        calls.append(set(ids))
        return {"first": "Renamed title", "second": ""}

    context = SimpleNamespace(
        sessions_root=tmp_path,
        active_session_tracker=SimpleNamespace(titles_from_thread_ids=titles),
        usage_cache=SimpleNamespace(insights=lambda *args, **kwargs: {
            "ready": True,
            "today": {"sessions": [{"sessionId": "first"}, {"sessionId": "second"}]},
            "week": {"topSessionsByUsage": [{"sessionId": "first"}]},
        }),
    )
    now = datetime.now().astimezone()
    payload = build_usage_insights_payload(context, day_start=now, week_start=now, included_providers=None)
    assert calls == [{"first", "second"}]
    assert payload["today"]["sessions"][0]["title"] == "Renamed title"
    assert payload["week"]["topSessionsByUsage"][0]["title"] == "Renamed title"
    assert payload["today"]["sessions"][1]["title"] == "会话 second"


def test_usage_insights_seven_day_window_uses_local_calendar_days() -> None:
    config = replace(
        UserConfig.defaults(),
        weekly_reset_weekday=0,
        weekly_reset_time="00:00",
    )

    day_start, budget_week_start, rolling_week_start = _usage_insights_windows(
        config,
        now=datetime(2026, 8, 10, 15, 30, tzinfo=timezone.utc),
    )

    assert day_start == datetime(2026, 8, 10, 10, tzinfo=timezone.utc)
    assert budget_week_start == datetime(2026, 8, 10, tzinfo=timezone.utc)
    assert rolling_week_start == datetime(2026, 8, 4, tzinfo=timezone.utc)


def test_usage_insights_worker_publishes_ready_payload_and_closes() -> None:
    events = RuntimeEventBus()
    context = SimpleNamespace(usage_insights_payload={}, runtime_events=events)

    worker = UsageInsightsWorker(
        context,
        refresh=lambda _context, request_id: {
            "state": "ready",
            "ready": True,
            "revision": 7,
            "requestId": request_id,
        },
    )
    try:
        assert worker.request_refresh(request_id="request-1")
        deadline = time.monotonic() + 2
        while context.usage_insights_payload.get("state") != "ready":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        published = events.drain()
        assert [event.type for event in published] == [
            "usage_insights_changed",
            "usage_insights_changed",
            "usage_cache_hydrated",
        ]
        assert published[-1].context["revision"] == 7
    finally:
        worker.close()

    assert not worker.request_refresh(request_id="late")


def test_usage_insights_worker_projects_refresh_failure() -> None:
    context = SimpleNamespace(
        usage_insights_payload={},
        runtime_events=RuntimeEventBus(),
    )

    def fail(_context: object, _request_id: str) -> dict[str, object]:
        raise RuntimeError("refresh failed")

    worker = UsageInsightsWorker(context, refresh=fail)
    try:
        worker.request_refresh(request_id="request-2")
        deadline = time.monotonic() + 2
        while context.usage_insights_payload.get("state") != "failed":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert context.usage_insights_payload["requestId"] == "request-2"
        assert context.usage_insights_payload["error"] == "refresh failed"
    finally:
        worker.close()


def test_deleted_session_refresh_uses_the_existing_background_worker() -> None:
    requests: list[str] = []

    class Worker:
        def request_refresh(self, *, request_id: str = "") -> bool:
            requests.append(request_id)
            return True

    context = SimpleNamespace(usage_insights_worker=Worker())

    _refresh_usage_after_session_delete(context, "delete-1")

    assert requests == ["session-delete:delete-1"]
