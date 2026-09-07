from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from codex_usage_hud.core.session_cleanup import (
    SessionCleanupError,
    SessionDeleteCapability,
    SessionCleanupManager,
)
from codex_usage_hud.core.deleted_usage import DeletedUsageLedger
from codex_usage_hud.core.session_search import search_terms


ROOT_ID = "10000000-0000-4000-8000-000000000001"
CHILD_ID = "10000000-0000-4000-8000-000000000002"
SECOND_ID = "10000000-0000-4000-8000-000000000003"


def _create_state(path: Path, rows: list[tuple[object, ...]]) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                archived INTEGER NOT NULL DEFAULT 0,
                updated_at_ms INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE thread_spawn_edges (
                parent_thread_id TEXT NOT NULL,
                child_thread_id TEXT NOT NULL,
                status TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO threads(id, rollout_path, title, cwd, archived, updated_at_ms)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


class SessionCleanupManagerTests(unittest.TestCase):
    def _fixture(self, *, child_status: str = "closed"):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        sessions = root / "sessions"
        archived = root / "archived_sessions"
        sessions.mkdir()
        archived.mkdir()
        rollouts = {
            ROOT_ID: sessions / f"rollout-{ROOT_ID}.jsonl",
            CHILD_ID: sessions / f"rollout-{CHILD_ID}.jsonl",
            SECOND_ID: archived / f"rollout-{SECOND_ID}.jsonl",
        }
        metadata = {
            ROOT_ID: {
                "model_provider": "openai-custom",
                "originator": "Codex Desktop",
                "source": "vscode",
            },
            CHILD_ID: {
                "model_provider": "openai-custom",
                "originator": "Codex Desktop",
                "source": "vscode",
            },
            SECOND_ID: {
                "model_provider": "routin",
                "originator": "codex-tui",
                "source": "cli",
            },
        }
        for session_id, path in rollouts.items():
            path.write_text(
                json.dumps({"type": "session_meta", "payload": metadata[session_id]})
                + "\n",
                encoding="utf-8",
            )
        state_db = root / "state_5.sqlite"
        _create_state(
            state_db,
            [
                (
                    ROOT_ID,
                    str(rollouts[ROOT_ID]),
                    "Root",
                    str(root / "project-a"),
                    0,
                    1_700_000_000_000,
                ),
                (
                    CHILD_ID,
                    str(rollouts[CHILD_ID]),
                    "Child",
                    str(root / "project-a"),
                    0,
                    1_700_000_100_000,
                ),
                (
                    SECOND_ID,
                    str(rollouts[SECOND_ID]),
                    "Archived",
                    str(root / "project-b"),
                    1,
                    1_600_000_000_000,
                ),
            ],
        )
        with closing(sqlite3.connect(state_db)) as connection, connection:
            connection.execute(
                "INSERT INTO thread_spawn_edges VALUES(?, ?, ?)",
                (ROOT_ID, CHILD_ID, child_status),
            )
        index_path = root / "session_index.jsonl"
        index_path.write_text(
            "\n".join(
                json.dumps({"id": item, "thread_name": f"Name {item[-1]}"})
                for item in (ROOT_ID, CHILD_ID, SECOND_ID)
            )
            + "\n",
            encoding="utf-8",
        )
        manager = SessionCleanupManager(
            state_db_path=state_db,
            sessions_root=sessions,
            session_index_path=index_path,
            token_factory=iter(f"token-{index}" for index in range(100)).__next__,
        )
        return temporary, root, state_db, index_path, rollouts, manager

    @staticmethod
    def _confirmed_desktop_lifecycle(
        state: Path,
        rollouts: dict[str, Path],
        calls: list[tuple[str, str]] | None = None,
    ):
        """Model Desktop's already-confirmed archive/delete side effects."""

        def archive_then_delete(thread_id: str, cwd: str) -> dict[str, object]:
            if calls is not None:
                calls.append((thread_id, cwd))
            rollout = rollouts.get(thread_id)
            if rollout is not None:
                rollout.unlink(missing_ok=True)
            with closing(sqlite3.connect(state)) as connection, connection:
                connection.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
                connection.execute(
                    "DELETE FROM thread_spawn_edges "
                    "WHERE parent_thread_id = ? OR child_thread_id = ?",
                    (thread_id, thread_id),
                )
            return {
                "threadId": thread_id,
                "archived": True,
                "deleted": True,
                "archiveNotification": True,
                "deleteNotification": True,
                "verified": True,
                "error": "",
            }

        return archive_then_delete

    @staticmethod
    def _confirmed_desktop_preflight(
        thread_ids: object,
        _cwd: str,
    ) -> dict[str, object]:
        return {
            "verified": True,
            "threadIds": list(thread_ids),
            "error": "",
        }

    def test_scan_groups_children_and_keeps_sensitive_identifiers_private(self) -> None:
        fixture = self._fixture()
        temporary, root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="scan-1")

        self.assertEqual(payload["totals"]["sessions"], 2)
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        self.assertEqual(root_row["descendantCount"], 1)
        self.assertTrue(root_row["selectable"])
        self.assertEqual(root_row["clientKind"], "app")
        self.assertEqual(root_row["modelProvider"], "openai-custom")
        self.assertEqual(
            root_row["updatedAt"],
            datetime.fromtimestamp(1_700_000_100_000 / 1000.0)
            .astimezone()
            .isoformat(timespec="seconds"),
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(ROOT_ID, serialized)
        self.assertNotIn(CHILD_ID, serialized)
        self.assertNotIn(str(root), serialized)

    def test_scan_does_not_build_content_index_on_inventory_thread(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        with patch.object(
            manager._search_index,
            "sync",
            side_effect=AssertionError("content indexing must be deferred"),
        ):
            payload = manager.scan(request_id="fast-scan")

        self.assertEqual(payload["operation"]["state"], "completed")
        self.assertEqual(payload["search"]["indexState"], "stale")

    def test_search_indexes_user_assistant_and_changed_file_text(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        rollouts[ROOT_ID].write_text(
            rollouts[ROOT_ID].read_text(encoding="utf-8")
            + json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "请检查会话索引中的 fuzzy-marker",
                    },
                },
                ensure_ascii=False,
            )
            + "\n"
            + json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "已修改 active_session.py"}],
                    },
                },
                ensure_ascii=False,
            )
            + "\n"
            + json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "name": "apply_patch",
                        "arguments": "*** Begin Patch\\n*** Update File: E:/Project/codex-usage-hud/src/codex_usage_hud/renderer_assets/active_session.py\\n*** End Patch",
                    },
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        payload = manager.scan(request_id="search-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        manager._search_index.sync(manager.search_index_entries())

        for query in ("fuzzy-marker", "active_session.py", "renderer_assets"):
            result = manager.search(query, request_id=f"search-{query}")
            assert root_row["id"] in result["search"]["matches"]
            assert result["operation"]["action"] == "search"
            assert result["operation"]["state"] == "completed"

        lightweight = manager.search(
            "fuzzy-marker",
            request_id="search-lightweight",
            include_sessions=False,
        )
        assert "sessions" not in lightweight
        assert "workdirs" not in lightweight

        if manager._search_index._fts_available:
            with closing(sqlite3.connect(manager._search_index.path)) as connection, connection:
                connection.execute("DELETE FROM session_search_fts")
            rebuilt = manager.search("fuzzy-marker", request_id="search-fts-rebuild")
            assert root_row["id"] in rebuilt["search"]["matches"]

        rollouts[ROOT_ID].write_text(
            rollouts[ROOT_ID].read_text(encoding="utf-8")
            + json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "append-only-marker",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        manager._search_index.sync(manager.search_index_entries())
        refreshed = manager.search("append-only-marker", request_id="search-append")
        assert root_row["id"] in refreshed["search"]["matches"]

        filtered = manager.search(
            "", workdir_id=root_row["workdirId"], request_id="search-workdir"
        )
        assert root_row["id"] in filtered["search"]["matches"]
        assert all(
            row["workdirId"] == root_row["workdirId"]
            for row in filtered["sessions"]
            if row["id"] in filtered["search"]["matches"]
        )

    def test_workdir_options_are_opaque_and_include_history(self) -> None:
        fixture = self._fixture()
        temporary, root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="workdir-scan")

        options = payload["workdirs"]
        assert any(item["label"] == "project-a" for item in options)
        assert any(item["label"] == "project-b" for item in options)
        detailed = manager.workdir_options_detailed()
        assert any(item.get("path") == str(root / "project-a") for item in detailed)
        serialized = json.dumps(payload, ensure_ascii=False)
        assert str(root) not in serialized

    def test_workdir_for_item_rechecks_current_inventory_directory(self) -> None:
        fixture = self._fixture()
        temporary, root, state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        project = root / "project-a"
        project.mkdir()

        payload = manager.scan()
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        self.assertEqual(
            manager.workdir_for_item(root_row["id"], payload["revision"]), project
        )
        self.assertIsNone(manager.workdir_for_item("unknown", payload["revision"]))
        self.assertIsNone(manager.workdir_for_item(root_row["id"], "stale-revision"))

        with closing(sqlite3.connect(state)) as connection, connection:
            connection.execute(
                "UPDATE threads SET cwd = ? WHERE id = ?",
                ("relative-workdir", ROOT_ID),
            )
        self.assertIsNone(manager.workdir_for_item(root_row["id"], payload["revision"]))

    def test_scan_reports_the_protection_phase_and_progress(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        progress_payloads: list[dict[str, object]] = []
        operations: list[dict[str, object]] = []

        def publish(payload: dict[str, object]) -> None:
            progress_payloads.append(dict(payload))
            operations.append(dict(payload["operation"]))

        manager.progress_publisher = publish

        manager.scan(request_id="scan-progress")

        phase_three = [
            operation for operation in operations if operation.get("phaseIndex") == 3
        ]
        self.assertTrue(phase_three)
        self.assertEqual(phase_three[0]["phaseLabel"], "Checking deletion protection")
        self.assertEqual(phase_three[0]["progress"], 60)
        self.assertEqual(phase_three[-1]["progress"], 99)
        self.assertTrue(progress_payloads)
        self.assertTrue(all("sessions" not in payload for payload in progress_payloads))

    def test_current_and_running_session_trees_are_blocked(self) -> None:
        fixture = self._fixture(child_status="running")
        temporary, _root, state, index, _rollouts, _manager = fixture
        self.addCleanup(temporary.cleanup)
        manager = SessionCleanupManager(
            state_db_path=state,
            sessions_root=state.parent / "sessions",
            session_index_path=index,
            current_session_ids=lambda: (SECOND_ID,),
            token_factory=iter(f"blocked-{index}" for index in range(100)).__next__,
        )

        payload = manager.scan()
        rows = {row["title"]: row for row in payload["sessions"]}

        self.assertEqual(rows["Root"]["status"], "running")
        self.assertFalse(rows["Root"]["selectable"])
        self.assertEqual(rows["Archived"]["status"], "current")
        self.assertFalse(rows["Archived"]["selectable"])

    def test_historical_open_spawn_edge_does_not_claim_runtime_activity(self) -> None:
        fixture = self._fixture(child_status="open")
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan()
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        self.assertEqual(root_row["status"], "idle")
        self.assertTrue(root_row["selectable"])

    def test_provider_history_delete_removes_matching_session_tree(self) -> None:
        fixture = self._fixture()
        temporary, _root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        expected_bytes = sum(
            rollouts[session_id].stat().st_size for session_id in (ROOT_ID, CHILD_ID)
        )

        result = manager.delete_provider_history(
            "OPENAI-CUSTOM", request_id="provider-1"
        )

        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(result["operation"]["provider"], "openai-custom")
        self.assertEqual(result["operation"]["sessionCount"], 1)
        self.assertEqual(result["operation"]["deletedCount"], 1)
        self.assertEqual(result["operation"]["actualBytes"], expected_bytes)
        self.assertFalse(rollouts[ROOT_ID].exists())
        self.assertFalse(rollouts[CHILD_ID].exists())
        self.assertFalse((state.parent / ".hud-session-delete-staging").exists())
        with closing(sqlite3.connect(state)) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM threads").fetchone()[0], 1
            )

    def test_provider_history_delete_refuses_protected_matching_tree(self) -> None:
        fixture = self._fixture(child_status="running")
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        with self.assertRaisesRegex(SessionCleanupError, "受保护会话"):
            manager.delete_provider_history("openai-custom")

    def test_session_transfer_copy_keeps_source_and_uses_private_session_context(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        calls: list[tuple[str, str, str]] = []
        verified: list[tuple[str, str]] = []
        target_id = "10000000-0000-4000-8000-000000000004"

        def fork(session_id: str, provider: str, cwd: str) -> str:
            calls.append((session_id, provider, cwd))
            return target_id

        def verify(session_id: str, provider: str) -> bool:
            verified.append((session_id, provider))
            return bool(session_id and provider)

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "OPENAI-CUSTOM",
            "ROUTIN",
            "copy",
            fork=fork,
            materialize=lambda *_args: None,
            verify=verify,
            request_id="transfer-copy",
        )

        operation = result["operation"]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["copiedCount"], 1)
        self.assertEqual(operation["migratedCount"], 0)
        self.assertEqual(calls, [(ROOT_ID, "routin", str(root / "project-a"))])
        self.assertEqual(verified, [(target_id, "routin")])
        self.assertTrue(_rollouts[ROOT_ID].exists())
        index_rows = [
            json.loads(line)
            for line in _index.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(
            next(row["thread_name"] for row in index_rows if row["id"] == target_id),
            "Root",
        )

    def test_session_transfer_uses_batch_materialize_and_verify_callbacks(self) -> None:
        fixture = self._fixture()
        temporary, root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        target_id = "10000000-0000-4000-8000-000000000010"
        batch_materialize_calls: list[object] = []
        batch_verify_calls: list[object] = []

        def materialize_batch(
            targets: object,
            _progress_callback: object = None,
        ) -> dict[str, object]:
            batch_materialize_calls.append(targets)
            return {target_id: True}

        def verify_batch(
            targets: object,
            _progress_callback: object = None,
        ) -> dict[str, object]:
            batch_verify_calls.append(targets)
            return {target_id: True}

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "copy",
            fork=lambda *_args: target_id,
            materialize_batch=materialize_batch,
            verify_batch=verify_batch,
            started_at_ms=1234567890,
            request_id="transfer-batch-callbacks",
        )

        self.assertEqual(result["operation"]["copiedCount"], 1)
        self.assertEqual(result["operation"]["startedAt"], 1234567890)
        self.assertEqual(
            batch_materialize_calls,
            [[(target_id, ROOT_ID)]],
        )
        self.assertEqual(
            batch_verify_calls,
            [[(target_id, "routin")]],
        )
        self.assertEqual(
            result["operation"]["results"][0]["workdir"],
            str(root / "project-a"),
        )

    def test_materialize_target_rollouts_uses_bounded_workers(self) -> None:
        fixture = self._fixture()
        temporary, root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        manager.transfer_max_workers = 2
        target_ids = (
            "10000000-0000-4000-8000-000000000011",
            "10000000-0000-4000-8000-000000000012",
        )
        source_path = root / "sessions" / "source.jsonl"
        target_paths = {
            target_id: root / "sessions" / f"{target_id}.jsonl"
            for target_id in target_ids
        }
        source_path.write_text("{}\n", encoding="utf-8")
        for path in target_paths.values():
            path.write_text("{}\n", encoding="utf-8")
        records = {
            ROOT_ID: SimpleNamespace(rollout_path=source_path),
            **{
                target_id: SimpleNamespace(rollout_path=path)
                for target_id, path in target_paths.items()
            },
        }
        manager._load_state = MagicMock(
            return_value=(records, {}, {}, set(), 0)
        )
        barrier = threading.Barrier(2)
        thread_names: list[str] = []
        progress: list[tuple[str, str, bool, str]] = []

        def materialize(**_kwargs: object) -> None:
            thread_names.append(threading.current_thread().name)
            barrier.wait(timeout=2)

        with patch(
            "codex_usage_hud.core.session_cleanup.materialize_forked_rollout",
            side_effect=materialize,
        ):
            outcomes = manager.materialize_target_rollouts(
                [(target_id, ROOT_ID) for target_id in target_ids],
                progress_callback=lambda *values: progress.append(values),
            )

        self.assertEqual(outcomes, {target_id: True for target_id in target_ids})
        self.assertEqual(len(thread_names), 2)
        self.assertEqual(len(set(thread_names)), 2)
        self.assertEqual(
            {target_id for target_id, _phase, success, _error in progress if success},
            set(target_ids),
        )

    def test_materialize_target_rollouts_resets_replaced_history_projection(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        history_db = root / "thread_history_1.sqlite"
        target_id = "10000000-0000-4000-8000-000000000011"
        unrelated_id = "10000000-0000-4000-8000-000000000099"
        with closing(sqlite3.connect(history_db)) as connection, connection:
            connection.execute(
                "CREATE TABLE thread_history_projection_state ("
                "thread_id TEXT PRIMARY KEY, "
                "next_rollout_byte_offset INTEGER NOT NULL, "
                "next_rollout_ordinal INTEGER NOT NULL"
                ")"
            )
            connection.executemany(
                "INSERT INTO thread_history_projection_state "
                "(thread_id, next_rollout_byte_offset, next_rollout_ordinal) "
                "VALUES (?, ?, ?)",
                [(target_id, 20, 3), (unrelated_id, 30, 4)],
            )
        manager.thread_history_db_path = history_db

        source_path = root / "sessions" / "source.jsonl"
        target_path = root / "sessions" / f"{target_id}.jsonl"
        source_path.write_text("{}\n", encoding="utf-8")
        target_path.write_text("{}\n", encoding="utf-8")
        manager._load_state = MagicMock(
            return_value=(
                {
                    ROOT_ID: SimpleNamespace(rollout_path=source_path),
                    target_id: SimpleNamespace(rollout_path=target_path),
                },
                {},
                {},
                set(),
                0,
            )
        )

        with patch(
            "codex_usage_hud.core.session_cleanup.materialize_forked_rollout",
        ):
            outcomes = manager.materialize_target_rollouts(
                [(target_id, ROOT_ID)],
            )

        self.assertEqual(outcomes, {target_id: True})
        with closing(sqlite3.connect(history_db)) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM thread_history_projection_state WHERE thread_id=?",
                    (target_id,),
                ).fetchone()
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM thread_history_projection_state WHERE thread_id=?",
                    (unrelated_id,),
                ).fetchone()
            )

    def test_sync_thread_state_metadata_makes_target_cli_visible(self) -> None:
        fixture = self._fixture()
        temporary, root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        target_id = "10000000-0000-4000-8000-000000000011"
        state_db = root / "target-state.sqlite"
        with closing(sqlite3.connect(state_db)) as connection, connection:
            connection.execute(
                "CREATE TABLE threads ("
                "id TEXT PRIMARY KEY, source TEXT, thread_source TEXT, "
                "has_user_event INTEGER, title TEXT, name TEXT, "
                "first_user_message TEXT, preview TEXT"
                ")"
            )
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (target_id, "vscode", "user", 0, "", "", "", ""),
            )
        target_path = root / "sessions" / f"{target_id}.jsonl"
        target_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {"id": target_id},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {
                                "item": {
                                    "type": "UserMessage",
                                    "content": [
                                        {"type": "text", "text": "continue migrated work"}
                                    ],
                                }
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        manager.state_db_path = state_db

        manager._sync_thread_state_metadata({target_id: target_path})

        with closing(sqlite3.connect(state_db)) as connection:
            row = connection.execute(
                "SELECT source, thread_source, has_user_event, title, "
                "name, first_user_message, preview FROM threads WHERE id=?",
                (target_id,),
            ).fetchone()
        self.assertEqual(
            row,
            (
                "cli",
                "user",
                1,
                "continue migrated work",
                "continue migrated work",
                "continue migrated work",
                "continue migrated work",
            ),
        )

    def test_session_transfer_copy_keeps_source_when_target_is_not_persistent(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "copy",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000009",
            materialize=lambda *_args: None,
            verify=lambda *_args: False,
            request_id="transfer-copy-unverified",
        )

        operation = result["operation"]
        self.assertEqual(operation["state"], "partial")
        self.assertEqual(operation["copiedCount"], 0)
        self.assertEqual(operation["failedCount"], 1)
        self.assertIn("目标 Provider 可见和续聊验证", operation["results"][0]["error"])
        self.assertTrue(rollouts[ROOT_ID].exists())

    def test_session_transfer_migrate_deletes_source_only_after_fork(self) -> None:
        fixture = self._fixture()
        temporary, root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        forked: list[str] = []
        lifecycle_calls: list[tuple[str, str]] = []

        def fork(session_id: str, _provider: str, _cwd: str) -> str:
            forked.append(session_id)
            return "10000000-0000-4000-8000-000000000005"

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=fork,
            materialize=lambda *_args: None,
            verify=lambda session_id, provider: bool(session_id and provider),
            desktop_source_preflight=self._confirmed_desktop_preflight,
            desktop_source_lifecycle=self._confirmed_desktop_lifecycle(
                state,
                rollouts,
                lifecycle_calls,
            ),
            request_id="transfer-migrate",
        )

        operation = result["operation"]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["copiedCount"], 1)
        self.assertEqual(operation["migratedCount"], 1)
        self.assertEqual(
            operation["results"][0]["workdir"], str(root / "project-a")
        )
        self.assertEqual(forked, [ROOT_ID])
        self.assertEqual(
            lifecycle_calls,
            [
                (ROOT_ID, str(root / "project-a")),
                (CHILD_ID, str(root / "project-a")),
            ],
        )
        self.assertFalse(rollouts[ROOT_ID].exists())
        self.assertFalse(rollouts[CHILD_ID].exists())
        with closing(sqlite3.connect(state)) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM threads").fetchone()[0], 1
            )

    def test_session_transfer_migrate_requires_desktop_archive_and_delete_notifications(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        lifecycle_calls: list[tuple[str, str]] = []

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000015",
            materialize=lambda *_args: None,
            verify=lambda *_args: True,
            desktop_source_preflight=self._confirmed_desktop_preflight,
            desktop_source_lifecycle=self._confirmed_desktop_lifecycle(
                state,
                rollouts,
                lifecycle_calls,
            ),
            request_id="transfer-desktop-lifecycle",
        )

        operation = result["operation"]
        row = operation["results"][0]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["desktopLifecycleFailureCount"], 0)
        self.assertTrue(row["sourceDeleted"])
        self.assertTrue(row["sourceArchived"])
        self.assertTrue(row["desktopLifecycleVerified"])
        self.assertEqual(
            lifecycle_calls,
            [
                (ROOT_ID, str(root / "project-a")),
                (CHILD_ID, str(root / "project-a")),
            ],
        )
        self.assertFalse(rollouts[ROOT_ID].exists())
        self.assertFalse(rollouts[CHILD_ID].exists())

    def test_session_transfer_preserves_inventory_ids_after_safety_rescan(self) -> None:
        fixture = self._fixture()
        temporary, root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        archived_row = next(
            row for row in payload["sessions"] if row["title"] == "Archived"
        )

        with patch.object(manager, "scan", wraps=manager.scan) as scan:
            result = manager.transfer(
                [root_row["id"]],
                payload["revision"],
                "openai-custom",
                "routin",
                "migrate",
                fork=lambda *_args: "10000000-0000-4000-8000-000000000024",
                materialize=lambda *_args: None,
                verify=lambda *_args: True,
                desktop_source_preflight=self._confirmed_desktop_preflight,
                desktop_source_lifecycle=self._confirmed_desktop_lifecycle(
                    state,
                    rollouts,
                ),
            )

        self.assertEqual(scan.call_count, 1)
        # The migrated source (Root + its child) is excluded from the final
        # inventory; the untouched session keeps its original opaque id so
        # renderer rows remain addressable.
        remaining = {row["id"]: row["title"] for row in result["sessions"]}
        self.assertEqual(
            remaining,
            {archived_row["id"]: "Archived"},
        )
        self.assertNotIn(root_row["id"], remaining)
        self.assertEqual(result["operation"]["state"], "completed")

    def test_session_transfer_migrated_sources_stay_hidden_after_rescan(self) -> None:
        # Regression: reopening the transfer dialog triggers a fresh scan that
        # rebuilds _items with new opaque ids.  A migrated source must not
        # reappear even when its state rows and rollout files still exist
        # (Desktop deletion can lag behind the fork and its notification).
        fixture = self._fixture()
        temporary, _root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        # Desktop reports a confirmed archive/delete but the state DB and the
        # rollout files are intentionally NOT touched, modelling a lagging
        # cleanup channel that a subsequent scan would otherwise resurrect.
        def confirmed_but_lagging(thread_id: str, _cwd: str) -> dict[str, object]:
            return {
                "threadId": thread_id,
                "archived": True,
                "deleted": True,
                "archiveNotification": True,
                "deleteNotification": True,
                "verified": True,
                "error": "",
            }

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000025",
            materialize=lambda *_args: None,
            verify=lambda *_args: True,
            desktop_source_preflight=self._confirmed_desktop_preflight,
            desktop_source_lifecycle=confirmed_but_lagging,
            request_id="transfer-migrate-rescan",
        )
        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(result["operation"]["migratedCount"], 1)
        # Final payload already hides the migrated source.
        self.assertEqual(
            [row["title"] for row in result["sessions"]],
            ["Archived"],
        )
        # Sources still exist on disk and in the state DB: a fresh scan must
        # not resurrect them.
        self.assertTrue(rollouts[ROOT_ID].exists())
        self.assertTrue(rollouts[CHILD_ID].exists())
        with closing(sqlite3.connect(state)) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM threads").fetchone()[0], 3
            )

        fresh = manager.scan(request_id="transfer-rescan")
        fresh_titles = [row["title"] for row in fresh["sessions"]]
        self.assertEqual(fresh_titles, ["Archived"])

    def test_migrated_sources_persist_across_manager_restart(self) -> None:
        # Regression: reopening the transfer dialog after a HUD restart builds
        # a fresh manager.  A migrated source whose Desktop state-db deletion
        # lags behind its confirmed notification must stay hidden.
        fixture = self._fixture()
        temporary, root, state, index, _rollouts, _manager = fixture
        self.addCleanup(temporary.cleanup)
        store = root / "hud" / "migrated_session_sources.json"
        manager = SessionCleanupManager(
            state_db_path=state,
            sessions_root=_manager.sessions_root,
            session_index_path=index,
            token_factory=iter(f"persist-{i}" for i in range(100)).__next__,
            migrated_source_store=store,
        )
        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        def confirmed_but_lagging(thread_id: str, _cwd: str) -> dict[str, object]:
            return {
                "threadId": thread_id,
                "archived": True,
                "deleted": True,
                "archiveNotification": True,
                "deleteNotification": True,
                "verified": True,
                "error": "",
            }

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000035",
            materialize=lambda *_args: None,
            verify=lambda *_args: True,
            desktop_source_preflight=self._confirmed_desktop_preflight,
            desktop_source_lifecycle=confirmed_but_lagging,
            request_id="transfer-persist",
        )
        self.assertEqual(result["operation"]["state"], "completed")

        restarted = SessionCleanupManager(
            state_db_path=state,
            sessions_root=_manager.sessions_root,
            session_index_path=index,
            token_factory=iter(f"restart-{i}" for i in range(100)).__next__,
            migrated_source_store=store,
        )
        fresh = restarted.scan(request_id="restart-scan")
        self.assertEqual(
            [row["title"] for row in fresh["sessions"]],
            ["Archived"],
        )

    def test_migrated_source_store_prunes_fully_deleted_rows(self) -> None:
        fixture = self._fixture()
        temporary, root, state, index, _rollouts, _manager = fixture
        self.addCleanup(temporary.cleanup)
        store = root / "migrated_session_sources.json"
        manager = SessionCleanupManager(
            state_db_path=state,
            sessions_root=_manager.sessions_root,
            session_index_path=index,
            token_factory=iter(f"prune-{i}" for i in range(100)).__next__,
            migrated_source_store=store,
        )
        manager.mark_migrated_sources((ROOT_ID, CHILD_ID))
        persisted = json.loads(store.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(persisted["migratedSourceIds"]),
            sorted([ROOT_ID, CHILD_ID]),
        )

        # Desktop deletion finally lands in the state DB; the next scan may
        # drop the ids because they can no longer resurrect from any scan.
        with closing(sqlite3.connect(state)) as connection, connection:
            connection.execute(
                "DELETE FROM threads WHERE id IN (?, ?)", (ROOT_ID, CHILD_ID)
            )
        manager.scan(request_id="prune-scan")

        persisted = json.loads(store.read_text(encoding="utf-8"))
        self.assertEqual(persisted["migratedSourceIds"], [])

    def test_retained_sources_are_recorded_as_pending_cleanup(self) -> None:
        fixture = self._fixture()
        temporary, root, state, index, _rollouts, _manager = fixture
        self.addCleanup(temporary.cleanup)
        store = root / "migrated_session_sources.json"
        manager = SessionCleanupManager(
            state_db_path=state,
            sessions_root=_manager.sessions_root,
            session_index_path=index,
            token_factory=iter(f"retain-{i}" for i in range(100)).__next__,
            migrated_source_store=store,
        )
        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        def rejected_lifecycle(thread_id: str, _cwd: str) -> dict[str, object]:
            return {
                "threadId": thread_id,
                "archived": True,
                "deleted": False,
                "archiveNotification": True,
                "deleteNotification": False,
                "verified": False,
                "error": "desktop-delete-failed",
            }

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000036",
            materialize=lambda *_args: None,
            verify=lambda *_args: True,
            desktop_source_preflight=self._confirmed_desktop_preflight,
            desktop_source_lifecycle=rejected_lifecycle,
            request_id="transfer-retained",
        )
        row = result["operation"]["results"][0]
        self.assertTrue(row["sourceRetained"])
        self.assertFalse(row["sourceDeleted"])

        # Retained sources must never be recorded as fully migrated...
        persisted = json.loads(store.read_text(encoding="utf-8"))
        self.assertEqual(persisted["migratedSourceIds"], [])
        # ...but their verified copy is bookkept so the transfer dialog can
        # refuse a duplicate copy after a reopen or restart.
        self.assertEqual(
            sorted(persisted["pendingCleanupSourceIds"]),
            sorted([ROOT_ID, CHILD_ID]),
        )
        # The source stays listed for storage management, now flagged.
        rows = {item["title"]: item for item in result["sessions"]}
        self.assertTrue(rows["Root"]["pendingSourceCleanup"])
        self.assertFalse(rows["Archived"]["pendingSourceCleanup"])

    def test_pending_cleanup_flag_survives_rescan_and_restart(self) -> None:
        fixture = self._fixture()
        temporary, root, state, index, _rollouts, _manager = fixture
        self.addCleanup(temporary.cleanup)
        store = root / "migrated_session_sources.json"
        manager = SessionCleanupManager(
            state_db_path=state,
            sessions_root=_manager.sessions_root,
            session_index_path=index,
            token_factory=iter(f"flag-{i}" for i in range(100)).__next__,
            migrated_source_store=store,
        )
        manager.scan(request_id="transfer-scan")
        manager.mark_pending_cleanup_sources((ROOT_ID, CHILD_ID))

        fresh = manager.scan(request_id="rescan")
        rows = {item["title"]: item for item in fresh["sessions"]}
        self.assertTrue(rows["Root"]["pendingSourceCleanup"])

        restarted = SessionCleanupManager(
            state_db_path=state,
            sessions_root=_manager.sessions_root,
            session_index_path=index,
            token_factory=iter(f"restart-{i}" for i in range(100)).__next__,
            migrated_source_store=store,
        )
        restarted_payload = restarted.scan(request_id="restart-scan")
        rows = {item["title"]: item for item in restarted_payload["sessions"]}
        self.assertTrue(rows["Root"]["pendingSourceCleanup"])

        # A later confirmed deletion moves the id out of pending cleanup.
        restarted.mark_migrated_sources((ROOT_ID, CHILD_ID))
        self.assertNotIn(ROOT_ID, restarted._pending_cleanup_source_ids)
        persisted = json.loads(store.read_text(encoding="utf-8"))
        self.assertEqual(persisted["pendingCleanupSourceIds"], [])
        self.assertEqual(
            sorted(persisted["migratedSourceIds"]),
            sorted([ROOT_ID, CHILD_ID]),
        )

    def test_session_transfer_migrate_retains_source_without_desktop_cleanup_channel(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000016",
            materialize=lambda *_args: None,
            verify=lambda *_args: True,
            request_id="transfer-desktop-binding-unavailable",
        )

        operation = result["operation"]
        row = operation["results"][0]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["migratedCount"], 0)
        self.assertTrue(row["sourceRetained"])
        self.assertEqual(operation["targetFailedCount"], 0)
        self.assertEqual(operation["failedCount"], 0)
        self.assertIn("Codex Desktop 归档/删除通道当前不可用", row["sourceCleanupError"])
        self.assertEqual(row["error"], "")
        self.assertTrue(rollouts[ROOT_ID].exists())
        self.assertTrue(rollouts[CHILD_ID].exists())

    def test_session_transfer_migrate_never_deletes_without_desktop_preflight(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        lifecycle = MagicMock()

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000023",
            materialize=lambda *_args: None,
            verify=lambda *_args: True,
            desktop_source_lifecycle=lifecycle,
            request_id="transfer-desktop-preflight-unavailable",
        )

        operation = result["operation"]
        row = operation["results"][0]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["migratedCount"], 0)
        self.assertEqual(operation["sourceCleanupPendingCount"], 1)
        self.assertTrue(row["sourceRetained"])
        self.assertFalse(row["sourceCleanupPreflighted"])
        self.assertIn("源会话预检通道当前不可用", row["sourceCleanupError"])
        self.assertEqual(row["error"], "")
        lifecycle.assert_not_called()
        self.assertTrue(rollouts[ROOT_ID].exists())
        self.assertTrue(rollouts[CHILD_ID].exists())

    def test_session_transfer_copy_never_invokes_desktop_lifecycle(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        lifecycle = MagicMock()
        events: list[str] = []

        def materialize(*_args: object) -> None:
            events.append("materialize")

        def verify(*_args: object) -> bool:
            self.assertEqual(events, ["materialize"])
            events.append("verify")
            return True

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "copy",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000017",
            materialize=materialize,
            verify=verify,
            desktop_source_lifecycle=lifecycle,
            request_id="transfer-copy-desktop-lifecycle",
        )

        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(events, ["materialize", "verify"])
        self.assertTrue(result["operation"]["results"][0]["historyMaterialized"])
        lifecycle.assert_not_called()
        self.assertTrue(rollouts[ROOT_ID].exists())

    def test_session_transfer_rejects_archived_source_before_fork(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        rollouts[SECOND_ID].write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "model_provider": "openai-custom",
                        "originator": "Codex Desktop",
                        "source": "vscode",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        payload = manager.scan(request_id="transfer-scan")
        archived_row = next(
            row for row in payload["sessions"] if row["title"] == "Archived"
        )
        fork = MagicMock()

        with self.assertRaisesRegex(SessionCleanupError, "已归档"):
            manager.transfer(
                [archived_row["id"]],
                payload["revision"],
                "openai-custom",
                "routin",
                "migrate",
                fork=fork,
                materialize=lambda *_args: None,
                verify=lambda *_args: True,
            )

        fork.assert_not_called()

    def test_session_transfer_rechecks_archived_source_before_fork(self) -> None:
        fixture = self._fixture()
        temporary, _root, state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        with closing(sqlite3.connect(state)) as connection, connection:
            connection.execute(
                "UPDATE threads SET archived = 1 WHERE id = ?",
                (ROOT_ID,),
            )
        fork = MagicMock()

        with self.assertRaisesRegex(SessionCleanupError, "提交前已归档"):
            manager.transfer(
                [root_row["id"]],
                payload["revision"],
                "openai-custom",
                "routin",
                "migrate",
                fork=fork,
                materialize=lambda *_args: None,
                verify=lambda *_args: True,
            )

        fork.assert_not_called()

    def test_session_transfer_retain_source_when_archived_before_cleanup(self) -> None:
        fixture = self._fixture()
        temporary, _root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        lifecycle = MagicMock()

        def verify(_session_id: str, _provider: str) -> bool:
            with closing(sqlite3.connect(state)) as connection, connection:
                connection.execute(
                    "UPDATE threads SET archived = 1 WHERE id = ?",
                    (ROOT_ID,),
                )
            return True

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000025",
            materialize=lambda *_args: None,
            verify=verify,
            desktop_source_preflight=self._confirmed_desktop_preflight,
            desktop_source_lifecycle=lifecycle,
        )

        operation = result["operation"]
        row = operation["results"][0]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["copiedCount"], 1)
        self.assertEqual(operation["sourceCleanupPendingCount"], 1)
        self.assertTrue(row["sourceRetained"])
        self.assertIn("删除前已归档", row["sourceCleanupError"])
        lifecycle.assert_not_called()
        self.assertTrue(rollouts[ROOT_ID].exists())

    def test_session_transfer_migrate_retains_source_when_desktop_lifecycle_fails(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        def lifecycle(_thread_id: str, _cwd: str) -> dict[str, object]:
            raise SessionCleanupError("Codex Desktop lifecycle is not writable.")

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000018",
            materialize=lambda *_args: None,
            verify=lambda *_args: True,
            desktop_source_preflight=self._confirmed_desktop_preflight,
            desktop_source_lifecycle=lifecycle,
            request_id="transfer-desktop-lifecycle-error",
        )

        operation = result["operation"]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["migratedCount"], 0)
        self.assertEqual(operation["copiedCount"], 1)
        self.assertEqual(operation["targetFailedCount"], 0)
        self.assertEqual(operation["failedCount"], 0)
        self.assertEqual(operation["sourceCleanupPendingCount"], 1)
        self.assertIn(
            "Codex Desktop lifecycle is not writable",
            operation["results"][0]["sourceCleanupError"],
        )
        self.assertEqual(operation["results"][0]["error"], "")
        self.assertTrue(rollouts[ROOT_ID].exists())

    def test_session_transfer_migrate_skips_source_cleanup_when_desktop_preflight_fails(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        lifecycle = MagicMock()

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000020",
            materialize=lambda *_args: None,
            verify=lambda *_args: True,
            desktop_source_preflight=lambda *_args: {
                "verified": False,
                "error": "no rollout found for thread id",
            },
            desktop_source_lifecycle=lifecycle,
            request_id="transfer-desktop-preflight-error",
        )

        operation = result["operation"]
        row = operation["results"][0]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["copiedCount"], 1)
        self.assertEqual(operation["targetFailedCount"], 0)
        self.assertEqual(operation["sourceCleanupPendingCount"], 1)
        self.assertTrue(row["sourceRetained"])
        self.assertEqual(row["sourceCleanupError"], "no rollout found for thread id")
        self.assertEqual(row["error"], "")
        lifecycle.assert_not_called()
        self.assertTrue(rollouts[ROOT_ID].exists())

    def test_session_transfer_preflights_every_root_before_any_source_delete(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        rollouts[SECOND_ID].write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "model_provider": "openai-custom",
                        "originator": "Codex Desktop",
                        "source": "vscode",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with closing(sqlite3.connect(state)) as connection, connection:
            connection.execute(
                "UPDATE threads SET archived = 0 WHERE id = ?",
                (SECOND_ID,),
            )
        payload = manager.scan(request_id="transfer-scan")
        selected = [
            row["id"]
            for row in payload["sessions"]
            if row["title"] in {"Root", "Archived"}
        ]
        target_ids = iter(
            (
                "10000000-0000-4000-8000-000000000021",
                "10000000-0000-4000-8000-000000000022",
            )
        )
        preflight_calls: list[tuple[tuple[str, ...], str]] = []
        lifecycle = MagicMock()

        def preflight(ids: tuple[str, ...], cwd: str) -> dict[str, object]:
            preflight_calls.append((ids, cwd))
            return {
                "verified": SECOND_ID not in ids,
                "error": "no rollout found for thread id" if SECOND_ID in ids else "",
            }

        result = manager.transfer(
            selected,
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: next(target_ids),
            materialize=lambda *_args: None,
            verify=lambda *_args: True,
            desktop_source_preflight=preflight,
            desktop_source_lifecycle=lifecycle,
            request_id="transfer-batch-preflight-error",
        )

        operation = result["operation"]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["copiedCount"], 2)
        self.assertEqual(operation["sourceCleanupPendingCount"], 2)
        self.assertEqual(len(preflight_calls), 2)
        lifecycle.assert_not_called()
        self.assertTrue(rollouts[ROOT_ID].exists())
        self.assertTrue(rollouts[CHILD_ID].exists())
        self.assertTrue(rollouts[SECOND_ID].exists())

    def test_session_transfer_keeps_archived_source_when_desktop_delete_is_unconfirmed(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        def lifecycle(thread_id: str, _cwd: str) -> dict[str, object]:
            return {
                "threadId": thread_id,
                "archived": True,
                "deleted": False,
                "archiveNotification": True,
                "deleteNotification": False,
                "verified": False,
                "error": "desktop-delete-failed",
            }

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000019",
            materialize=lambda *_args: None,
            verify=lambda *_args: True,
            desktop_source_preflight=self._confirmed_desktop_preflight,
            desktop_source_lifecycle=lifecycle,
            request_id="transfer-desktop-delete-unconfirmed",
        )

        operation = result["operation"]
        row = operation["results"][0]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["migratedCount"], 0)
        self.assertEqual(operation["desktopLifecycleFailureCount"], 1)
        self.assertEqual(operation["failedCount"], 0)
        self.assertFalse(row["sourceDeleted"])
        self.assertTrue(row["sourceArchived"])
        self.assertTrue(row["sourceRetained"])
        self.assertIn("desktop-delete-failed", row["sourceCleanupError"])
        self.assertEqual(row["error"], "")
        self.assertTrue(rollouts[ROOT_ID].exists())
        self.assertTrue(rollouts[CHILD_ID].exists())

    def test_session_transfer_migrate_runs_desktop_lifecycle_after_all_forks(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        # Make the archived root part of the same source-provider selection.
        rollouts[SECOND_ID].write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "model_provider": "openai-custom",
                        "originator": "Codex Desktop",
                        "source": "vscode",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with closing(sqlite3.connect(state)) as connection, connection:
            connection.execute(
                "UPDATE threads SET archived = 0 WHERE id = ?",
                (SECOND_ID,),
            )
        payload = manager.scan(request_id="transfer-scan")
        selected = [
            row["id"]
            for row in payload["sessions"]
            if row["title"] in {"Root", "Archived"}
        ]
        target_ids = iter(
            (
                "10000000-0000-4000-8000-000000000006",
                "10000000-0000-4000-8000-000000000007",
            )
        )

        events: list[str] = []
        lifecycle_calls: list[tuple[str, str]] = []

        def fork(_session_id: str, _provider: str, _cwd: str) -> str:
            events.append("fork")
            return next(target_ids)

        progress: list[dict[str, object]] = []
        manager.progress_publisher = progress.append
        lifecycle = self._confirmed_desktop_lifecycle(
            state,
            rollouts,
            lifecycle_calls,
        )

        def record_lifecycle(thread_id: str, cwd: str) -> dict[str, object]:
            events.append("lifecycle")
            return lifecycle(thread_id, cwd)

        result = manager.transfer(
            selected,
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=fork,
            materialize=lambda *_args: None,
            verify=lambda session_id, provider: bool(session_id and provider),
            desktop_source_preflight=self._confirmed_desktop_preflight,
            desktop_source_lifecycle=record_lifecycle,
            request_id="transfer-batch",
        )

        operation = result["operation"]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["migratedCount"], 2)
        self.assertEqual(events[:2], ["fork", "fork"])
        self.assertEqual(events[2:], ["lifecycle", "lifecycle", "lifecycle"])
        self.assertEqual(
            [thread_id for thread_id, _cwd in lifecycle_calls],
            [ROOT_ID, CHILD_ID, SECOND_ID],
        )
        self.assertTrue(progress)
        self.assertTrue(all("results" not in row["operation"] for row in progress))
        self.assertTrue(all("sessions" not in row for row in progress))
        transfer_progress = [
            row["operation"]
            for row in progress
            if row["operation"].get("action") == "sessionTransfer"
            and row["operation"].get("state") == "running"
        ]
        self.assertTrue(transfer_progress)
        self.assertEqual(
            [row["completedCount"] for row in transfer_progress],
            sorted(row["completedCount"] for row in transfer_progress),
        )
        self.assertEqual(
            [row["progress"] for row in transfer_progress],
            sorted(row["progress"] for row in transfer_progress),
        )
        self.assertEqual(transfer_progress[0]["completedCount"], 0)
        self.assertEqual(max(row["completedCount"] for row in transfer_progress), 2)
        self.assertEqual(
            len({row["startedAt"] for row in transfer_progress}),
            1,
        )
        self.assertTrue(
            any(
                row.get("sourceCleanupCompletedCount") == 1
                for row in transfer_progress
            ),
            transfer_progress,
        )

    def test_session_transfer_migrate_keeps_source_when_target_is_not_persistent(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000008",
            materialize=lambda *_args: None,
            verify=lambda *_args: False,
            request_id="transfer-unverified",
        )

        operation = result["operation"]
        self.assertEqual(operation["state"], "partial")
        self.assertEqual(operation["copiedCount"], 0)
        self.assertEqual(operation["migratedCount"], 0)
        self.assertIn("目标 Provider 可见和续聊验证", operation["results"][0]["error"])
        self.assertTrue(rollouts[ROOT_ID].exists())
        self.assertTrue(rollouts[CHILD_ID].exists())

    def test_session_transfer_migrate_keeps_source_when_materialization_fails(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000010",
            materialize=lambda *_args: (_ for _ in ()).throw(
                SessionCleanupError("source rollout unavailable")
            ),
            verify=lambda *_args: True,
            request_id="transfer-materialize-failed",
        )

        operation = result["operation"]
        self.assertEqual(operation["state"], "partial")
        self.assertEqual(operation["copiedCount"], 0)
        self.assertEqual(operation["migratedCount"], 0)
        self.assertEqual(operation["targetFailedCount"], 1)
        self.assertIn("source rollout unavailable", operation["results"][0]["error"])
        self.assertTrue(rollouts[ROOT_ID].exists())
        self.assertTrue(rollouts[CHILD_ID].exists())

    def test_session_transfer_rejects_a_target_id_that_already_exists(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        materialize = MagicMock()
        verify = MagicMock(return_value=True)
        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "copy",
            fork=lambda *_args: CHILD_ID.upper(),
            materialize=materialize,
            verify=verify,
            request_id="transfer-collision",
        )

        operation = result["operation"]
        self.assertEqual(operation["state"], "failed")
        self.assertEqual(operation["copiedCount"], 0)
        self.assertEqual(operation["targetFailedCount"], 1)
        self.assertIn("目标会话冲突", operation["results"][0]["error"])
        materialize.assert_not_called()
        verify.assert_not_called()
        self.assertTrue(rollouts[ROOT_ID].exists())

    def test_session_transfer_rejects_case_variant_duplicate_target_in_batch(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        rollouts[SECOND_ID].write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "model_provider": "openai-custom",
                        "originator": "Codex Desktop",
                        "source": "vscode",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with closing(sqlite3.connect(state)) as connection, connection:
            connection.execute(
                "UPDATE threads SET archived = 0 WHERE id = ?",
                (SECOND_ID,),
            )
        payload = manager.scan(request_id="transfer-scan")
        selected = [
            row["id"]
            for row in payload["sessions"]
            if row["title"] in {"Root", "Archived"}
        ]
        target_id = "10000000-0000-4000-8000-000000000014"
        returned_ids = iter((target_id, target_id.upper()))

        result = manager.transfer(
            selected,
            payload["revision"],
            "openai-custom",
            "routin",
            "copy",
            fork=lambda *_args: next(returned_ids),
            materialize=lambda *_args: None,
            verify=lambda *_args: True,
            request_id="transfer-duplicate-target",
        )

        operation = result["operation"]
        self.assertEqual(operation["state"], "partial")
        self.assertEqual(operation["copiedCount"], 1)
        self.assertEqual(operation["targetFailedCount"], 1)
        self.assertEqual(operation["failedCount"], 1)
        self.assertTrue(
            any("目标会话冲突" in row["error"] for row in operation["results"])
        )

    def test_session_transfer_reports_desktop_lifecycle_error_and_retains_source(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        def lifecycle(_thread_id: str, _cwd: str) -> dict[str, object]:
            raise SessionCleanupError("Codex Desktop thread/delete failed.")

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000012",
            materialize=lambda *_args: None,
            verify=lambda *_args: True,
            desktop_source_preflight=self._confirmed_desktop_preflight,
            desktop_source_lifecycle=lifecycle,
            request_id="transfer-desktop-delete-error",
        )

        operation = result["operation"]
        row = operation["results"][0]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["copiedCount"], 1)
        self.assertEqual(operation["migratedCount"], 0)
        self.assertEqual(operation["sourceRetainedCount"], 1)
        self.assertEqual(operation["targetFailedCount"], 0)
        self.assertEqual(operation["unmigratedCount"], 0)
        self.assertEqual(row["sourceDeleted"], False)
        self.assertEqual(row["sourceRetained"], True)
        self.assertIn("Codex Desktop thread/delete failed", row["sourceCleanupError"])
        self.assertEqual(row["error"], "")
        self.assertTrue(rollouts[ROOT_ID].exists())

    def test_session_transfer_keeps_source_if_fork_is_registered_as_a_child(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        target_id = "10000000-0000-4000-8000-000000000013"
        target_path = root / "sessions" / f"rollout-{target_id}.jsonl"

        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        def fork(_session_id: str, _provider: str, cwd: str) -> str:
            target_path.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "model_provider": "routin",
                            "originator": "codex-tui",
                            "source": "cli",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with closing(sqlite3.connect(state)) as connection, connection:
                connection.execute(
                    "INSERT INTO threads(id, rollout_path, title, cwd, archived, updated_at_ms) "
                    "VALUES(?, ?, ?, ?, ?, ?)",
                    (target_id, str(target_path), "Target", cwd, 0, 1_800_000_000_000),
                )
                connection.execute(
                    "INSERT INTO thread_spawn_edges VALUES(?, ?, ?)",
                    (ROOT_ID, target_id, "closed"),
                )
            return target_id

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=fork,
            materialize=lambda *_args: None,
            verify=lambda *_args: True,
            request_id="transfer-child-target",
        )

        operation = result["operation"]
        row = operation["results"][0]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["copiedCount"], 1)
        self.assertEqual(operation["migratedCount"], 0)
        self.assertEqual(operation["sourceRetainedCount"], 1)
        self.assertTrue(row["targetVisible"])
        self.assertFalse(row["sourceDeleted"])
        self.assertTrue(row["sourceRetained"])
        self.assertIn("目标会话仍属于源会话子树", row["sourceCleanupError"])
        self.assertEqual(row["error"], "")
        self.assertTrue(rollouts[ROOT_ID].exists())
        self.assertTrue(target_path.exists())

    def test_ambiguous_spawn_relation_blocks_every_affected_root(self) -> None:
        fixture = self._fixture()
        temporary, _root, state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        with closing(sqlite3.connect(state)) as connection, connection:
            connection.execute(
                "INSERT INTO thread_spawn_edges VALUES(?, ?, ?)",
                (SECOND_ID, CHILD_ID, "closed"),
            )

        payload = manager.scan()

        self.assertGreaterEqual(payload["totals"]["unresolved"], 1)
        self.assertTrue(payload["sessions"])
        self.assertTrue(all(not row["selectable"] for row in payload["sessions"]))
        self.assertTrue(
            all(row["status"] == "unresolved" for row in payload["sessions"])
        )

    def test_paginated_history_without_source_rollout_is_not_selectable(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        rollouts[ROOT_ID].write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "session_id": ROOT_ID,
                        "history_mode": "paginated",
                        "history_base": {
                            "thread_id": "10000000-0000-4000-8000-000000000099",
                            "end_ordinal_exclusive": 10,
                        },
                        "model_provider": "openai-custom",
                        "originator": "Codex Desktop",
                        "source": "vscode",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        payload = manager.scan()
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        self.assertTrue(root_row["selectable"])
        self.assertTrue(root_row["transferable"] is False)
        self.assertEqual(root_row["status"], "idle")
        self.assertIn(
            "paginated history source rollout", root_row["transferBlockedReason"]
        )

    def test_paginated_history_root_without_base_remains_transferable(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        rollouts[ROOT_ID].write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "session_id": ROOT_ID,
                        "history_mode": "paginated",
                        "model_provider": "openai-custom",
                        "originator": "Codex Desktop",
                        "source": "vscode",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        payload = manager.scan()
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        self.assertTrue(root_row["selectable"])
        self.assertTrue(root_row["transferable"])

    def test_transfer_rejects_session_with_unavailable_paginated_history_source(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        rollouts[ROOT_ID].write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "session_id": ROOT_ID,
                        "history_mode": "paginated",
                        "history_base": {
                            "thread_id": "10000000-0000-4000-8000-000000000099",
                            "end_ordinal_exclusive": 10,
                        },
                        "model_provider": "openai-custom",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        payload = manager.scan()
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        with self.assertRaisesRegex(SessionCleanupError, "分页历史源无法验证"):
            manager.transfer(
                [root_row["id"]],
                payload["revision"],
                "openai-custom",
                "routin",
                "copy",
                fork=lambda *_args: "10000000-0000-4000-8000-000000000011",
                materialize=lambda *_args: None,
                verify=lambda *_args: True,
            )

    def test_preview_and_execute_delete_parent_cascade_in_one_local_transaction(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")
        preview = manager.preview([root_row["id"]], scan["revision"])
        token = preview["operation"]["confirmationToken"]

        result = manager.execute(
            [root_row["id"]],
            scan["revision"],
            token,
            request_id="delete-1",
        )

        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(result["operation"]["deletedCount"], 1)
        self.assertEqual(
            [row["title"] for row in result["sessions"]],
            ["Archived"],
        )
        with closing(sqlite3.connect(state)) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM threads").fetchone()[0], 1
            )
        self.assertFalse(rollouts[ROOT_ID].exists())
        self.assertFalse(rollouts[CHILD_ID].exists())
        self.assertFalse((_root / ".hud-session-delete-staging").exists())

    def test_delete_commits_prepared_usage_only_after_cascade_verification(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        events: list[tuple[str, object]] = []

        def prepare(item) -> str:
            self.assertTrue(all(path.exists() for path in item._rollout_paths))
            events.append(("prepare", item._session_id))
            return "usage-receipt"

        def commit(receipt: object) -> None:
            self.assertTrue(all(not path.exists() for path in selected_paths))
            events.append(("commit", receipt))

        manager.usage_snapshot_prepare = prepare
        manager.usage_snapshot_commit = commit
        manager.usage_snapshot_discard = lambda receipt: events.append(
            ("discard", receipt)
        )
        scan = manager.scan()
        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")
        selected_paths = {rollouts[ROOT_ID], rollouts[CHILD_ID]}
        preview = manager.preview([root_row["id"]], scan["revision"])

        result = manager.execute(
            [root_row["id"]],
            scan["revision"],
            preview["operation"]["confirmationToken"],
        )

        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(
            events,
            [("prepare", ROOT_ID), ("commit", "usage-receipt")],
        )

    def test_delete_commit_is_idempotent_after_usage_refresh_recovers_snapshot(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        now = datetime.now(timezone.utc)
        ledger = DeletedUsageLedger(root / "deleted-session-usage.json")

        class Parser:
            @staticmethod
            def load_records_lenient(path: Path) -> Path:
                return path

            @staticmethod
            def usage_events(_path: Path) -> tuple[dict[str, object], ...]:
                return (
                    {
                        "timestamp": now,
                        "model": "gpt-test",
                        "total_tokens": 10,
                        "input_tokens": 8,
                        "output_tokens": 2,
                    },
                )

            @staticmethod
            def session_model_provider(_path: Path) -> str:
                return "custom"

        parser = Parser()

        def prepare(item) -> str:
            return ledger.prepare(
                session_id=item._session_id,
                family_session_ids=(item._session_id, *item._descendant_ids),
                title=item.title,
                workdir_name=item.workdir_name,
                rollout_paths=item._rollout_paths,
                parser=parser,
                now=now,
            )

        manager.usage_snapshot_prepare = prepare
        manager.usage_snapshot_commit = lambda receipt: ledger.commit(
            str(receipt), now=now
        )
        manager.usage_snapshot_discard = lambda receipt: ledger.discard(str(receipt))
        scan = manager.scan()
        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")
        preview = manager.preview([root_row["id"]], scan["revision"])
        verify_deleted = manager._verify_deleted_batch

        def verify_and_refresh(items) -> None:
            verify_deleted(items)
            self.assertEqual(len(ledger.sessions(now=now)), 1)

        with patch.object(
            manager,
            "_verify_deleted_batch",
            side_effect=verify_and_refresh,
        ):
            result = manager.execute(
                [root_row["id"]],
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        sessions = ledger.sessions(now=now)
        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(len(sessions[0].events), 2)

    def test_delete_failure_discards_pending_usage_snapshot(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        events: list[tuple[str, object]] = []
        manager.usage_snapshot_prepare = lambda item: "pending-usage"
        manager.usage_snapshot_commit = lambda receipt: events.append(
            ("commit", receipt)
        )
        manager.usage_snapshot_discard = lambda receipt: events.append(
            ("discard", receipt)
        )
        scan = manager.scan()
        row = next(row for row in scan["sessions"] if row["title"] == "Archived")
        preview = manager.preview([row["id"]], scan["revision"])

        with patch.object(
            manager,
            "_delete_local_batch",
            side_effect=SessionCleanupError("local delete rejected"),
        ):
            result = manager.execute(
                [row["id"]],
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        self.assertEqual(result["operation"]["state"], "failed")
        self.assertEqual(events, [("discard", "pending-usage")])

    def test_usage_snapshot_failure_prevents_local_delete(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        row = next(row for row in scan["sessions"] if row["title"] == "Root")
        preview = manager.preview([row["id"]], scan["revision"])

        def fail_prepare(_item) -> object:
            raise SessionCleanupError("usage snapshot unavailable")

        manager.usage_snapshot_prepare = fail_prepare
        delete_batch = MagicMock()
        with patch.object(manager, "_delete_local_batch", delete_batch):
            result = manager.execute(
                [row["id"]],
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        self.assertEqual(result["operation"]["state"], "failed")
        delete_batch.assert_not_called()
        self.assertIn(
            "usage snapshot unavailable",
            result["operation"]["results"][0]["error"],
        )

    def test_confirmation_is_single_use_and_revision_bound(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        item_id = scan["sessions"][0]["id"]
        preview = manager.preview([item_id], scan["revision"])
        token = preview["operation"]["confirmationToken"]
        with self.assertRaises(SessionCleanupError):
            manager.execute([item_id], "stale", token)

    def test_missing_local_store_capability_disables_delete(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        with patch.object(
            manager,
            "probe_capability",
            return_value=SessionDeleteCapability(
                False,
                "Codex local session store is unavailable.",
            ),
        ):
            payload = manager.scan()

        self.assertFalse(payload["capability"]["available"])
        self.assertEqual(payload["totals"]["selectable"], 0)
        self.assertTrue(all(not row["selectable"] for row in payload["sessions"]))

    def test_batch_delete_fails_as_a_whole_before_any_local_mutation(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        selected = [row["id"] for row in scan["sessions"]]
        preview = manager.preview(selected, scan["revision"])

        with patch.object(
            manager,
            "_delete_local_batch",
            side_effect=SessionCleanupError("local transaction rejected"),
        ):
            result = manager.execute(
                selected,
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        self.assertEqual(result["operation"]["state"], "failed")
        self.assertEqual(result["operation"]["deletedCount"], 0)
        self.assertEqual(result["operation"]["failedCount"], 2)

    def test_batch_delete_probes_capability_once_before_revalidating_items(
        self,
    ) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        selected = [row["id"] for row in scan["sessions"]]
        preview = manager.preview(selected, scan["revision"])

        with patch.object(
            manager, "probe_capability", wraps=manager.probe_capability
        ) as probe:
            result = manager.execute(
                selected,
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(probe.call_count, 1)

    def test_batch_delete_reuses_one_preflight_and_one_final_verification(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        selected = [row["id"] for row in scan["sessions"]]
        preview = manager.preview(selected, scan["revision"])

        with patch.object(
            manager, "_load_state", wraps=manager._load_state
        ) as load_state:
            result = manager.execute(
                selected,
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(load_state.call_count, 3)

    def test_batch_delete_failure_preserves_all_selected_session_trees(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")
        archived_row = next(
            row for row in scan["sessions"] if row["title"] == "Archived"
        )
        preview = manager.preview(
            [root_row["id"], archived_row["id"]],
            scan["revision"],
        )

        with patch.object(
            manager,
            "_delete_local_batch",
            side_effect=SessionCleanupError("local transaction rejected"),
        ):
            result = manager.execute(
                [root_row["id"], archived_row["id"]],
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        self.assertEqual(result["operation"]["deletedCount"], 0)
        self.assertEqual(result["operation"]["failedCount"], 2)
        self.assertFalse(result["operation"]["interrupted"])
        self.assertEqual(len(manager.scan()["sessions"]), 2)

    def test_delete_verification_checks_index_rows_without_titles(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        index.write_text(
            json.dumps({"id": ROOT_ID}) + "\n" + json.dumps({"id": SECOND_ID}) + "\n",
            encoding="utf-8",
        )
        scan = manager.scan()
        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")
        preview = manager.preview([root_row["id"]], scan["revision"])

        def delete_without_index(items) -> None:
            family = (items[0]._session_id, *items[0]._descendant_ids)
            placeholders = ",".join("?" for _ in family)
            with closing(sqlite3.connect(_state)) as connection, connection:
                connection.execute(
                    f"DELETE FROM thread_spawn_edges WHERE parent_thread_id IN ({placeholders}) "
                    f"OR child_thread_id IN ({placeholders})",
                    (*family, *family),
                )
                connection.execute(
                    f"DELETE FROM threads WHERE id IN ({placeholders})", family
                )
            for path in items[0]._rollout_paths:
                path.unlink()

        with patch.object(
            manager, "_delete_local_batch", side_effect=delete_without_index
        ):
            result = manager.execute(
                [root_row["id"]],
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        self.assertEqual(result["operation"]["state"], "failed")
        self.assertIn("session index", result["operation"]["results"][0]["error"])

    def test_provider_in_non_session_meta_record_is_detected(self) -> None:
        """Risk 1: a rollout without session_meta must not be classified unknown.

        Older or truncated rollouts may carry the provider only in a
        ``turn_context`` ``model`` field (``provider/model``) or in another
        record's ``provider`` field.  The inventory scan must pick that up so
        provider-scoped deletion does not silently skip the session.
        """
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        # Replace the root rollout with one that has no session_meta but
        # carries the provider in a turn_context model field.
        rollouts[ROOT_ID].write_text(
            json.dumps(
                {
                    "type": "turn_context",
                    "payload": {"model": "openai-custom/gpt-4o"},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        payload = manager.scan()
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        self.assertEqual(root_row["modelProvider"], "openai-custom")

    def test_provider_history_delete_matches_unknown_via_base_url_deep_scan(
        self,
    ) -> None:
        """Risk 1: unknown sessions are deep-scanned and matched by base_url.

        When the inventory metadata reports ``unknown`` (no provider field in
        any record), ``delete_provider_history`` runs a targeted deep scan
        that checks ``session_meta.base_url`` against known provider hints.
        """
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        # Root has session_meta with base_url but no model_provider.
        rollouts[ROOT_ID].write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "base_url": "https://api.openai.com/v1",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        # Child has no model_provider in session_meta, uses info nesting.
        rollouts[CHILD_ID].write_text(
            json.dumps(
                {
                    "type": "token_count",
                    "payload": {
                        "info": {"model_provider": "openai"},
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = manager.delete_provider_history(
            "openai", request_id="provider-deep-1"
        )

        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(result["operation"]["deletedCount"], 1)
        self.assertFalse(rollouts[ROOT_ID].exists())
        self.assertFalse(rollouts[CHILD_ID].exists())

    def test_provider_history_delete_reports_unresolved_unknown_sessions(
        self,
    ) -> None:
        """Risk 1: truly unknown sessions are counted, not silently skipped.

        A rollout with no provider signal anywhere cannot be safely attributed
        to the target provider.  It must not be deleted, but the result must
        report ``unresolvedUnknownCount`` so the caller knows sessions may
        have been left behind.
        """
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        # Root has no provider signal at all — just a user message.
        rollouts[ROOT_ID].write_text(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "item": {
                            "type": "UserMessage",
                            "content": [{"type": "text", "text": "hello"}],
                        }
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        # Child still has openai-custom session_meta, so it gets deleted.
        # But root is the parent, so the child is in root's tree.
        # To test unresolved count independently, make SECOND_ID unknown too.
        rollouts[SECOND_ID].write_text(
            json.dumps({"type": "event_msg", "payload": {"item": {}}}) + "\n",
            encoding="utf-8",
        )

        result = manager.delete_provider_history(
            "openai-custom", request_id="provider-unknown-1"
        )

        # Root+child tree: root is unknown but child is openai-custom.
        # The root item's model_provider is "unknown" (no signal), but its
        # deep scan also returns "unknown".  The child is a descendant, not
        # a separate inventory item, so it follows the root.  The root is
        # NOT matched → not deleted.  SECOND_ID is also unknown.
        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(result["operation"]["deletedCount"], 0)
        self.assertGreaterEqual(result["operation"]["unresolvedUnknownCount"], 1)
        # Truly unknown sessions are preserved on disk.
        self.assertTrue(rollouts[ROOT_ID].exists())

    def test_scan_cleans_up_orphaned_staging_from_previous_crash(self) -> None:
        """Risk 2: staging left by a crashed deletion is removed on next scan."""
        fixture = self._fixture()
        temporary, root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        staging_parent = root / ".hud-session-delete-staging"
        orphaned = staging_parent / "deadbeefdeadbeef"
        orphaned.mkdir(parents=True)
        (orphaned / "rollout-leftover.jsonl").write_text(
            '{"type":"session_meta"}\n', encoding="utf-8"
        )

        self.assertTrue(orphaned.exists())
        manager.scan(request_id="scan-recovery")

        self.assertFalse(orphaned.exists())
        self.assertFalse(staging_parent.exists())

    def test_delete_succeeds_when_rmtree_fails_after_db_commit(self) -> None:
        """Risk 2: a persistent rmtree failure after DB commit must not fail deletion.

        Windows file locks (AV, indexer) can cause ``shutil.rmtree`` to fail
        even though the database transaction already committed.  The deletion
        must succeed (original paths gone, DB rows removed) and the fallback
        file-by-file removal plus ``rmdir`` must reclaim the staging.
        """
        fixture = self._fixture()
        temporary, root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")
        preview = manager.preview([root_row["id"]], scan["revision"])
        token = preview["operation"]["confirmationToken"]

        def always_fail_rmtree(_path, *args, **kwargs):
            raise OSError("simulated persistent file lock")

        with patch(
            "codex_usage_hud.core.session_cleanup.shutil.rmtree",
            side_effect=always_fail_rmtree,
        ):
            result = manager.execute(
                [root_row["id"]],
                scan["revision"],
                token,
                request_id="delete-rmtree-fail",
            )

        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(result["operation"]["deletedCount"], 1)
        self.assertFalse(rollouts[ROOT_ID].exists())
        self.assertFalse(rollouts[CHILD_ID].exists())
        with closing(sqlite3.connect(state)) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM threads").fetchone()[0], 1
            )
        # Fallback unlink + rmdir must have reclaimed the staging.
        self.assertFalse((root / ".hud-session-delete-staging").exists())

    def test_thread_find_for_item_returns_query_and_session_target_key(self) -> None:
        """Native in-thread find payload: query is echoed, targetKey lets the
        renderer confirm the landed session before opening Codex's find bar."""
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan(request_id="scan-thread-find")
        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")

        result = manager.thread_find_for_item(root_row["id"], scan["revision"], "fuzzy-marker")

        self.assertEqual(result.get("query"), "fuzzy-marker")
        self.assertEqual(result.get("error"), None)
        self.assertEqual(
            result.get("targetKey"),
            hashlib.sha256(ROOT_ID.encode()).hexdigest(),
        )
        # 跳转浮窗所需的富化载荷：查询分词、索引 revision、有序命中列表。
        self.assertEqual(result.get("revision"), scan["revision"])
        self.assertEqual(result.get("tokens"), list(search_terms("fuzzy-marker")))
        self.assertEqual(result.get("matches"), [])

    def test_thread_find_for_item_ships_ranked_matches_in_search_order(self) -> None:
        """After a resident search, the payload carries the ranked match list
        (id/title/kinds/score/exactPhrase) so the in-session float can list and
        walk hits in the same verbatim-first order the index produced."""
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        # 给 Root 会话注入可检索的正文，否则管理器的常驻索引只有元数据。
        rollouts[ROOT_ID].write_text(
            rollouts[ROOT_ID].read_text(encoding="utf-8")
            + json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "请检查会话索引中的 fuzzy-marker",
                    },
                },
                ensure_ascii=False,
            )
            + "\n"
            + json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "已修改 active_session.py"}],
                    },
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        scan = manager.scan(request_id="scan-thread-find-ranked")
        manager._search_index.sync(manager.search_index_entries())
        manager.search("fuzzy-marker", request_id="search-thread-find-ranked")

        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")
        result = manager.thread_find_for_item(root_row["id"], scan["revision"], "fuzzy-marker")

        matches = result.get("matches")
        self.assertIsInstance(matches, list)
        self.assertTrue(matches, "expected at least one ranked match after search()")
        first = matches[0]
        self.assertEqual(first["id"], root_row["id"])
        self.assertIn("title", first)
        self.assertIn("kinds", first)
        self.assertIn("score", first)
        self.assertIn("exactPhrase", first)
        # The match order must mirror the resident index ranking (verbatim
        # phrase above scattered token hits, then by relevance score).
        self.assertEqual(
            [entry["id"] for entry in matches],
            list(manager._search_state.get("matches") or []),
        )

    def test_thread_find_for_item_rejects_stale_or_unknown_inventory(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan(request_id="scan-thread-find-stale")
        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")

        stale = manager.thread_find_for_item(root_row["id"], "stale-revision", "q")
        self.assertEqual(stale.get("error"), "inventory-changed")
        self.assertEqual(stale.get("targetKey"), None)

        unknown = manager.thread_find_for_item("does-not-exist", scan["revision"], "q")
        self.assertEqual(unknown.get("error"), "inventory-changed")
        self.assertEqual(unknown.get("targetKey"), None)


if __name__ == "__main__":
    unittest.main()
