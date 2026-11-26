from __future__ import annotations

import json
from pathlib import Path

from codex_usage_hud.overlay_projection import visible_payload_items
from codex_usage_hud.ui.work_overlay.dismissal_store import WorkOverlayDismissalStore
from codex_usage_hud.ui.work_overlay.model import _item_dismiss_key


def _completed_item(task_started_at: str = "2026-08-17T09:00:00+08:00") -> dict[str, object]:
    return {
        "id": "thread-1",
        "status": "recent",
        "taskStartedAt": task_started_at,
        "tokensText": "0",
        "costText": "$0",
    }


def test_dismissal_store_rehydrates_and_hides_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "overlay-dismissals.json"
    item = _completed_item()
    first = WorkOverlayDismissalStore(path)
    first[item["id"]] = _item_dismiss_key(item)

    restarted = WorkOverlayDismissalStore(path)
    assert visible_payload_items(
        [item],
        restarted,
        item_limit=1,
        dismiss_key=_item_dismiss_key,
    ) == []


def test_dismissal_survives_a_temporary_payload_omission(tmp_path: Path) -> None:
    path = tmp_path / "overlay-dismissals.json"
    item = _completed_item()
    store = WorkOverlayDismissalStore(path)
    store[item["id"]] = _item_dismiss_key(item)

    assert visible_payload_items([], store, item_limit=1, dismiss_key=_item_dismiss_key) == []
    assert visible_payload_items(
        [item],
        store,
        item_limit=1,
        dismiss_key=_item_dismiss_key,
    ) == []


def test_new_task_for_a_dismissed_session_is_visible(tmp_path: Path) -> None:
    path = tmp_path / "overlay-dismissals.json"
    old_item = _completed_item()
    new_item = _completed_item("2026-08-17T10:00:00+08:00")
    store = WorkOverlayDismissalStore(path)
    store[old_item["id"]] = _item_dismiss_key(old_item)

    assert visible_payload_items(
        [new_item],
        store,
        item_limit=1,
        dismiss_key=_item_dismiss_key,
    ) == [new_item]
    assert old_item["id"] not in store


def test_visible_items_generate_keys_only_for_previously_dismissed_cards() -> None:
    dismissed = _completed_item()
    visible = {**dismissed, "id": "thread-2", "status": "running"}
    dismissed_instances = {
        dismissed["id"]: json.dumps(
            {
                "id": dismissed["id"],
                "status": "work",
                "errorText": "",
                "taskStartedAt": dismissed["taskStartedAt"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    }
    checked_ids = []

    def dismiss_key(item):
        checked_ids.append(item["id"])
        return _item_dismiss_key(item)

    assert visible_payload_items(
        [visible, dismissed], dismissed_instances, item_limit=2, dismiss_key=dismiss_key,
    ) == [visible]
    assert checked_ids == [dismissed["id"]]


def test_dismissal_store_is_bounded(tmp_path: Path) -> None:
    store = WorkOverlayDismissalStore(
        tmp_path / "overlay-dismissals.json",
        clock=iter([1.0, 2.0, 3.0]).__next__,
        max_entries=2,
    )
    store["oldest"] = "key-1"
    store["middle"] = "key-2"
    store["newest"] = "key-3"

    assert set(store) == {"middle", "newest"}
