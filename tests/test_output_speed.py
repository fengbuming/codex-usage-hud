from datetime import datetime, timedelta, timezone

import pytest

from codex_usage_hud.active_work import _work_item_from_snapshot
from codex_usage_hud.core import Activity, ParsedSession, RequestTokens
from codex_usage_hud.output_speed import format_output_speed, output_speed_projection
from codex_usage_hud.overlay_projection import work_item_to_overlay_dict
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
