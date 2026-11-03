from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from codex_usage_hud.active_work import _work_item_from_snapshot
from codex_usage_hud.core import Activity, ParsedSession, RequestTokens
from codex_usage_hud.output_speed import (
    format_output_speed,
    format_session_output_speed,
    output_speed_projection,
)
from codex_usage_hud.overlay_projection import _merge_stable_item_metadata, work_item_to_overlay_dict
from codex_usage_hud.renderer_payload_builder import _output_speed_projection


@pytest.mark.parametrize("rate, expected", [
    (89.5, "89.5 tok/s"), (2100, "2100 tok/s"), (100.5, "101 tok/s"),
    (80.25, "80.3 tok/s"), (3.125, "3.13 tok/s"),
    (None, ""), (True, ""), (0, ""), (-1, ""), (float("nan"), ""),
    (float("inf"), ""), ("invalid", ""),
])
def test_speed_label_matches_renderer_rounding_without_approximation_prefix(rate, expected):
    assert format_output_speed(rate) == expected


@pytest.mark.parametrize("status, estimated, complete, expected", [
    ("running", True, False, (89.5, "estimated")),
    ("confirmed", False, True, (89.5, "measured")),
    ("confirmed", False, False, (None, "idle")),
    ("waiting", True, False, (None, "idle")),
])
def test_renderer_and_bubble_require_the_same_speed_evidence(status, estimated, complete, expected):
    request = RequestTokens(
        status=status, estimated=estimated, output_usage_complete=complete,
        output_tokens_per_second=89.5,
    )
    snapshot = ParsedSession(request=request)
    assert output_speed_projection(request) == expected
    assert _output_speed_projection(snapshot) == expected


def test_jsonl_speed_reaches_the_matching_bubble_and_clears_on_completion():
    now = datetime.now(timezone.utc)
    snapshot = ParsedSession(
        session_id="speed-session", session_title="Speed",
        session_output_tokens_per_second=25.0,
        session_output_min_tokens_per_second=10.0,
        session_output_max_tokens_per_second=89.5,
        session_started_at=now - timedelta(minutes=1),
        task_started_at=now - timedelta(seconds=30),
        activity=Activity(kind="agent", detail="working", timestamp=now),
    )
    snapshot.request = RequestTokens(
        status="confirmed", source="jsonl", output_tokens_per_second=89.5,
        output_speed_estimated=True,
    )
    # A completed model response can be followed by a live tool invocation.
    snapshot.activity.kind = "tool call"
    snapshot.activity.detail = "exec {}"
    item = _work_item_from_snapshot(snapshot, current=True, now=now)
    assert item is not None
    payload = work_item_to_overlay_dict(item)
    assert payload["outputTokensPerSecond"] == _output_speed_projection(snapshot)[0] == 89.5
    assert payload["outputSpeedMode"] == "estimated"
    snapshot.task_completed_at = now
    completed = _work_item_from_snapshot(snapshot, current=True, now=now)
    assert completed is not None
    assert completed.output_tokens_per_second is None
    assert completed.output_speed_mode == "idle"
    # Completion clears live request speed but preserves the whole-session mean.
    completed_payload = work_item_to_overlay_dict(completed)
    assert completed_payload["sessionOutputTokensPerSecond"] == 25.0
    assert completed_payload["sessionOutputMinTokensPerSecond"] == 10.0
    assert completed_payload["sessionOutputMaxTokensPerSecond"] == 89.5
    assert completed_payload["outputTokensPerSecond"] is None


@pytest.mark.parametrize("new_task", [False, True])
def test_partial_completed_speed_is_preserved_only_for_the_matching_task(new_task):
    now = datetime.now(timezone.utc)
    snapshot = ParsedSession(
        session_id="average", task_started_at=now - timedelta(seconds=20),
        task_completed_at=now, session_output_tokens_per_second=89.5,
        session_output_min_tokens_per_second=10.0,
        session_output_max_tokens_per_second=120.0,
    )
    cached = _work_item_from_snapshot(snapshot, current=True, now=now)
    assert cached is not None
    partial = replace(
        cached, session_output_tokens_per_second=None,
        session_output_min_tokens_per_second=None,
        session_output_max_tokens_per_second=None,
        task_started_at=now if new_task else cached.task_started_at,
    )

    merged = _merge_stable_item_metadata(cached, partial)

    assert merged.session_output_tokens_per_second == (None if new_task else 89.5)
    assert merged.session_output_min_tokens_per_second == (None if new_task else 10.0)
    assert merged.session_output_max_tokens_per_second == (None if new_task else 120.0)


@pytest.mark.parametrize("rate, expected", [
    (5, "5.00 tok/s"), (25, "25.0 tok/s"), (None, "— tok/s"),
])
def test_session_speed_formats_pooled_samples_without_approximation(rate, expected):
    snapshot = ParsedSession(session_output_tokens_per_second=rate)
    # The latest request can have a different speed from the whole session.
    snapshot.request = RequestTokens(
        status="confirmed", output_tokens_per_second=89.5,
        output_usage_complete=True,
    )

    assert format_session_output_speed(snapshot) == expected
