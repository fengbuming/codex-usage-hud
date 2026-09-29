"""Tests for Codex App non-session background usage auditing."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.core.background_usage import (
    BackgroundUsageScanner,
    BackgroundUsageStore,
    background_feature_en_label,
    background_feature_purpose,
    classify_background_feature,
    decode_request_context,
    decode_request_evidence,
    decode_submission_prompt,
    resolve_request_token_split,
    visible_session_source_prompt,
)
from codex_usage_hud.background_usage_runtime import BackgroundUsageRuntime
from codex_usage_hud.core.runtime_events import RuntimeEventBus


VISIBLE_ID = "10000000-0000-4000-8000-000000000001"
CHILD_ID = "10000000-0000-4000-8000-000000000002"
BACKGROUND_ID = "10000000-0000-4000-8000-000000000003"
UNKNOWN_ID = "10000000-0000-4000-8000-000000000004"
GRACE_ID = "10000000-0000-4000-8000-000000000005"
RELATED_SESSION_ID = "20000000-0000-4000-8000-000000000001"
AMBIGUOUS_SESSION_ID = "20000000-0000-4000-8000-000000000002"
APP_PROCESS = "pid:1234:aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
WORKER_PROCESS = "pid:2345:aaaaaaaa-bbbb-4ccc-8ddd-ffffffffffff"


def _submission_body(thread_id: str, prompt: str) -> str:
    return (
        f"session_loop{{thread_id={thread_id}}}: Submission "
        'sub=Submission { id: "submission", op: UserInput { items: '
        f"[Text {{ text: {json.dumps(prompt)}, text_elements: [] }}] }} }}"
    )


def _feedback_body(thread_id: str, model: str, cwd: str) -> str:
    return (
        f"session_loop{{thread_id={thread_id}}}:turn{{model={model}}}:"
        f"run_sampling_request{{model={model} cwd={cwd}}}:"
        f"try_run_sampling_request{{model={model}}}:"
        'endpoint_session.stream_encoded_json_with{http.method=POST '
        'api.path="responses"}: endpoint="/responses"'
    )


def _turn_body(
    thread_id: str,
    model: str,
    total_tokens: int,
    estimated_tokens: int | None,
) -> str:
    estimated_fragment = (
        f"estimated_token_count=Some({estimated_tokens}) "
        if estimated_tokens is not None
        else ""
    )
    return (
        f"session_loop{{thread_id={thread_id}}}:turn{{model={model}}}: "
        "post sampling "
        f"turn_id=turn total_usage_tokens={total_tokens} "
        f"{estimated_fragment}"
        "model_needs_follow_up=false"
    )


def _create_logs(path: Path, rows: list[tuple[object, ...]]) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE logs (
                id INTEGER PRIMARY KEY,
                ts INTEGER NOT NULL,
                ts_nanos INTEGER NOT NULL DEFAULT 0,
                level TEXT,
                target TEXT,
                feedback_log_body TEXT,
                module_path TEXT,
                file TEXT,
                line INTEGER,
                thread_id TEXT,
                process_uuid TEXT,
                estimated_bytes INTEGER
            );
            CREATE INDEX idx_logs_ts ON logs(ts DESC, ts_nanos DESC, id DESC);
            """
        )
        connection.executemany(
            """
            INSERT INTO logs(
                id, ts, target, module_path, feedback_log_body,
                thread_id, process_uuid
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _create_state(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE threads (id TEXT PRIMARY KEY, source TEXT);
            CREATE TABLE thread_spawn_edges (
                parent_thread_id TEXT,
                child_thread_id TEXT,
                status TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO threads(id, source) VALUES(?, 'vscode')",
            (VISIBLE_ID,),
        )
        connection.execute(
            """
            INSERT INTO thread_spawn_edges(parent_thread_id, child_thread_id, status)
            VALUES(?, ?, 'running')
            """,
            (VISIBLE_ID, CHILD_ID),
        )


def _create_state_with_sessions(
    path: Path,
    sessions: list[tuple[str, str, int]],
    *,
    spawn_edges: tuple[tuple[str, str], ...] = (),
) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                source TEXT,
                first_user_message TEXT,
                created_at INTEGER
            );
            CREATE TABLE thread_spawn_edges (
                parent_thread_id TEXT,
                child_thread_id TEXT,
                status TEXT
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO threads(id, source, first_user_message, created_at)
            VALUES(?, 'vscode', ?, ?)
            """,
            sessions,
        )
        connection.executemany(
            """
            INSERT INTO thread_spawn_edges(parent_thread_id, child_thread_id, status)
            VALUES(?, ?, 'completed')
            """,
            spawn_edges,
        )


def _title_description_prompt(source_prompt: str) -> str:
    return (
        "You will be presented with a user prompt, and your job is to provide "
        "a short title for a task. Fill the structured description field.\n"
        f"User prompt:\n{source_prompt}"
    )


def _vscode_first_user_message(source_prompt: str) -> str:
    return (
        "# Files mentioned by the user:\n\n"
        "## Replace-OpenCvSharpFallback.V2.ps1: E:/Work/scan_project/artifacts/"
        "Replace-OpenCvSharpFallback.V2.ps1\n\n"
        "## My request:\n"
        f"{source_prompt}"
    )


def _prices() -> dict[str, dict[str, object]]:
    return {
        "custom/gpt-test": {
            "model": "gpt-test",
            "provider": "custom",
            "input": 2.0,
            "cached_input": 0.5,
            "cache_write": 0.0,
            "output": 4.0,
            "reasoning": 4.0,
        }
    }


def _version(
    version_id: str,
    *,
    effective_at: int,
    input_price: float,
    output_price: float,
    model: str = "gpt-test",
    provider: str = "custom",
) -> dict[str, object]:
    timestamp = datetime.fromtimestamp(effective_at, timezone.utc).isoformat()
    return {
        "version_id": version_id,
        "model": model,
        "provider": provider,
        "input": input_price,
        "cached_input": input_price,
        "cache_write": input_price,
        "output": output_price,
        "reasoning": output_price,
        "effective_at": timestamp,
        "created_at": timestamp,
        "created_by": "user_edit",
        "source": "manual",
    }


class BackgroundUsageDecoderTests(unittest.TestCase):
    def test_decoders_project_real_log_shapes(self) -> None:
        prompt = "## Memory Writing Agent: Phase 2 (Consolidation)\nBody"
        submission = _submission_body(BACKGROUND_ID, prompt)
        self.assertEqual(decode_submission_prompt(submission), prompt)
        self.assertEqual(
            classify_background_feature(prompt).key,
            "memory_consolidation",
        )
        self.assertEqual(
            classify_background_feature(prompt).label,
            "记忆整理",
        )

        model, cwd, endpoint = decode_request_context(
            _feedback_body(BACKGROUND_ID, "gpt-test", r"C:\work tree")
        )
        self.assertEqual(model, "gpt-test")
        self.assertEqual(cwd, r"C:\work tree")
        self.assertEqual(endpoint, "/responses")

        request = decode_request_evidence(
            99,
            123,
            _turn_body(BACKGROUND_ID, "gpt-test", 120, 100),
        )
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.model, "gpt-test")
        self.assertEqual(request.total_tokens, 120)
        self.assertEqual(request.estimated_input_tokens, 100)

        missing_estimate = decode_request_evidence(
            100,
            124,
            _turn_body(BACKGROUND_ID, "gpt-test", 33673, None),
        )
        self.assertIsNotNone(missing_estimate)
        assert missing_estimate is not None
        self.assertEqual(missing_estimate.total_tokens, 33673)
        self.assertIsNone(missing_estimate.estimated_input_tokens)

    def test_missing_estimate_split_uses_previous_total_as_input(self) -> None:
        first = resolve_request_token_split(
            total_tokens=33673,
            estimated_input_tokens=None,
        )
        self.assertEqual(first, (33673, 0, 0))
        second = resolve_request_token_split(
            total_tokens=36220,
            estimated_input_tokens=None,
            previous_total_tokens=33673,
            previous_input_tokens=33673,
        )
        self.assertEqual(second, (33673, 33673, 2547))

    def test_unknown_feature_is_retained(self) -> None:
        feature = classify_background_feature("New internal feature prompt")
        self.assertEqual(feature.key, "unknown")
        self.assertEqual(feature.label, "未知后台任务")

    def test_current_title_and_task_catch_up_prompts_are_attributed(self) -> None:
        title = classify_background_feature(
            "Generate a concise, single-line task title of at most 36 characters. "
            "Do not answer the request.\n\nUser prompt:\nRepair the HUD."
        )
        self.assertEqual(title.key, "title_description")
        self.assertEqual(title.label, "任务标题与描述")

        for prompt in (
            "Write a brief catch-up for a user returning to this task. "
            "Return JSON with summary and nullable next_action.",
            "Write a brief catch-up for a user returning to this Codex task. "
            "In at most 40 words, explain the objective, what was completed or learned, and the next step or blocker.",
        ):
            catch_up = classify_background_feature(prompt)
            self.assertEqual(catch_up.key, "task_catch_up")
            self.assertEqual(catch_up.label, "任务回顾摘要")
        self.assertEqual(
            background_feature_en_label("task_catch_up"), "Task catch-up"
        )
        self.assertIn("恢复长任务上下文", background_feature_purpose("task_catch_up"))

    def test_reclassify_repairs_historical_unknown_feature_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = BackgroundUsageStore(root / "audit.sqlite3")
            scanner = BackgroundUsageScanner(
                logs_path=root / "logs.sqlite3",
                state_path=root / "state.sqlite3",
                store=store,
                grace_seconds=0,
                now=lambda: 2_000.0,
            )
            with closing(store._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO background_events(
                        event_id, thread_id, process_uuid, feature_key,
                        feature_label, prompt, first_seen_at, last_seen_at,
                        classification_state, request_count
                    ) VALUES(?, ?, ?, 'unknown', '未知后台任务', ?, ?, ?, ?, 1)
                    """,
                    (
                        UNKNOWN_ID,
                        UNKNOWN_ID,
                        APP_PROCESS,
                        "Write a brief catch-up for a user returning to this task. "
                        "Return JSON with summary and nullable next_action.",
                        1_000,
                        1_001,
                        "unattributed",
                    ),
                )
                self.assertTrue(
                    scanner._reclassify(
                        connection,
                        visible={},
                        child_threads=set(),
                        related_session_candidates={},
                        now_ts=2_000,
                    )
                )
                row = connection.execute(
                    """
                    SELECT feature_key, feature_label, classification_state,
                           app_attribution
                    FROM background_events WHERE event_id=?
                    """,
                    (UNKNOWN_ID,),
                ).fetchone()
            assert row is not None
            self.assertEqual(row["feature_key"], "task_catch_up")
            self.assertEqual(row["feature_label"], "任务回顾摘要")
            self.assertEqual(row["classification_state"], "background")
            self.assertEqual(row["app_attribution"], "feature_signature")


class BackgroundUsageScannerTests(unittest.TestCase):
    def test_visible_session_source_prompt_unwraps_vscode_request(self) -> None:
        source_prompt = "Build the OpenCV fallback into the scanner."
        self.assertEqual(
            visible_session_source_prompt(_vscode_first_user_message(source_prompt)),
            source_prompt,
        )
        self.assertEqual(
            visible_session_source_prompt(
                f"<codex_delegation><input>{source_prompt}</input></codex_delegation>"
            ),
            source_prompt,
        )

    def test_exact_title_prompt_association_unwraps_vscode_first_user_message(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            source_prompt = "Design the OpenCV automatic fallback plan."
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(
                            BACKGROUND_ID,
                            _title_description_prompt(source_prompt),
                        ),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 30, 20),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state_with_sessions(
                state_path,
                [
                    (
                        RELATED_SESSION_ID,
                        _vscode_first_user_message(source_prompt),
                        base_ts - 10,
                    )
                ],
            )
            store = BackgroundUsageStore(root / "audit.sqlite3")
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                grace_seconds=0,
                now=lambda: float(base_ts + 30),
            )

            scanner.scan()

            now = datetime.fromtimestamp(base_ts + 30).astimezone()
            self.assertEqual(store.pending_today(now=now), [])
            self.assertEqual(
                store.notification_index(now=now),
                {
                    RELATED_SESSION_ID: {
                        "count": 1,
                        "eventId": BACKGROUND_ID,
                        "range": "today",
                    }
                },
            )

    def test_exact_title_prompt_association_uses_session_badge_and_unread_range(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            source_prompt = "Optimize the usage overview without changing other HUDs."
            workdir = root / "workspace"
            workdir.mkdir()
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(
                            BACKGROUND_ID,
                            _title_description_prompt(source_prompt),
                        ),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "feedback_tags",
                        "codex_feedback",
                        _feedback_body(BACKGROUND_ID, "gpt-test", str(workdir)),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        3,
                        base_ts + 2,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 30, 20),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state_with_sessions(
                state_path,
                [(RELATED_SESSION_ID, source_prompt, base_ts - 10)],
            )
            store = BackgroundUsageStore(root / "audit.sqlite3")
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                grace_seconds=0,
                now=lambda: float(base_ts + 30),
            )

            scanner.scan()

            now = datetime.fromtimestamp(base_ts + 30).astimezone()
            self.assertEqual(store.pending_today(now=now), [])
            self.assertEqual(
                store.notification_index(now=now),
                {
                    RELATED_SESSION_ID: {
                        "count": 1,
                        "eventId": BACKGROUND_ID,
                        "range": "today",
                    }
                },
            )
            payload = store.query(range_key="all", now=now)
            self.assertTrue(payload["events"][0]["unread"])
            self.assertTrue(payload["events"][0]["workdirAvailable"])
            self.assertEqual(
                payload["events"][0]["workdirAssociation"],
                "verified_session",
            )
            later = datetime.fromtimestamp(base_ts + (8 * 86400)).astimezone()
            self.assertEqual(store.range_for_event(BACKGROUND_ID, now=later), "30d")

            with closing(sqlite3.connect(store.path)) as connection:
                row = connection.execute(
                    "SELECT related_session_id, association_kind "
                    "FROM background_events WHERE event_id=?",
                    (BACKGROUND_ID,),
                ).fetchone()
            self.assertEqual(
                row,
                (RELATED_SESSION_ID, "exact_first_user_message"),
            )
            self.assertTrue(store.confirm(BACKGROUND_ID, now=now))
            self.assertEqual(store.notification_index(now=now), {})

    def test_ambiguous_title_prompt_stays_as_unrelated_overlay_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            source_prompt = "The same first message was used twice."
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(
                            BACKGROUND_ID,
                            _title_description_prompt(source_prompt),
                        ),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 30, 20),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state_with_sessions(
                state_path,
                [
                    (RELATED_SESSION_ID, source_prompt, base_ts - 10),
                    (AMBIGUOUS_SESSION_ID, source_prompt, base_ts - 5),
                ],
            )
            store = BackgroundUsageStore(root / "audit.sqlite3")
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                grace_seconds=0,
                now=lambda: float(base_ts + 30),
            )

            scanner.scan()

            now = datetime.fromtimestamp(base_ts + 30).astimezone()
            self.assertEqual(store.notification_index(now=now), {})
            self.assertEqual(
                [item["eventId"] for item in store.pending_today(now=now)],
                [BACKGROUND_ID],
            )

    def test_exact_prompt_matching_a_spawn_child_stays_as_overlay_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            source_prompt = "A child agent received this exact prompt."
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(
                            BACKGROUND_ID,
                            _title_description_prompt(source_prompt),
                        ),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 30, 20),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state_with_sessions(
                state_path,
                [
                    (RELATED_SESSION_ID, "Parent prompt", base_ts - 20),
                    (CHILD_ID, source_prompt, base_ts - 10),
                ],
                spawn_edges=((RELATED_SESSION_ID, CHILD_ID),),
            )
            store = BackgroundUsageStore(root / "audit.sqlite3")
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                grace_seconds=0,
                now=lambda: float(base_ts + 30),
            )

            scanner.scan()

            now = datetime.fromtimestamp(base_ts + 30).astimezone()
            self.assertEqual(store.notification_index(now=now), {})
            self.assertEqual(
                [item["eventId"] for item in store.pending_today(now=now)],
                [BACKGROUND_ID],
            )

    def test_v1_store_migrates_session_association_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "audit.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.executescript(
                    """
                    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    INSERT INTO metadata(key, value) VALUES('schema_version', '1');
                    INSERT INTO metadata(key, value) VALUES('revision', '0');
                    CREATE TABLE background_events (
                        event_id TEXT PRIMARY KEY,
                        thread_id TEXT NOT NULL UNIQUE,
                        process_uuid TEXT NOT NULL DEFAULT '',
                        feature_key TEXT NOT NULL DEFAULT 'unknown',
                        feature_label TEXT NOT NULL DEFAULT '',
                        prompt TEXT NOT NULL DEFAULT '',
                        cwd TEXT NOT NULL DEFAULT '',
                        endpoint TEXT NOT NULL DEFAULT '',
                        provider TEXT NOT NULL DEFAULT '',
                        first_seen_at INTEGER NOT NULL,
                        last_seen_at INTEGER NOT NULL,
                        confirmed_at INTEGER,
                        classification_state TEXT NOT NULL DEFAULT 'pending',
                        app_attribution TEXT NOT NULL DEFAULT '',
                        request_count INTEGER NOT NULL DEFAULT 0,
                        total_tokens INTEGER NOT NULL DEFAULT 0,
                        estimated_cost_usd REAL NOT NULL DEFAULT 0,
                        cost_available INTEGER NOT NULL DEFAULT 0
                    );
                    """
                )

            BackgroundUsageStore(path)

            with closing(sqlite3.connect(path)) as connection:
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(background_events)"
                    )
                }
                version = connection.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                ).fetchone()[0]
                request_columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(background_requests)"
                    )
                }
            self.assertIn("related_session_id", columns)
            self.assertIn("association_kind", columns)
            self.assertIn("price_status", request_columns)
            self.assertIn("price_version_id", request_columns)
            self.assertEqual(version, "3")

    def test_scan_excludes_sessions_aggregates_requests_and_persists_confirmation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            audit_path = root / "background-usage.sqlite3"
            base_ts = 1_900_000_000
            title_prompt = (
                "You are a helpful assistant. You will be presented with a user prompt, "
                "and your job is to provide a short title for a task. "
                "Fill the structured description field."
            )
            rows = [
                (
                    1,
                    base_ts,
                    "codex_core::session::handlers",
                    "codex_core::session::handlers",
                    _submission_body(VISIBLE_ID, "Visible user session"),
                    VISIBLE_ID,
                    APP_PROCESS,
                ),
                (
                    2,
                    base_ts + 1,
                    "codex_core::session::turn",
                    "codex_core::session::turn",
                    _turn_body(VISIBLE_ID, "gpt-test", 10, 8),
                    VISIBLE_ID,
                    APP_PROCESS,
                ),
                (
                    3,
                    base_ts + 2,
                    "codex_core::session::handlers",
                    "codex_core::session::handlers",
                    _submission_body(CHILD_ID, "Explicit child agent"),
                    CHILD_ID,
                    APP_PROCESS,
                ),
                (
                    4,
                    base_ts + 3,
                    "codex_core::session::turn",
                    "codex_core::session::turn",
                    _turn_body(CHILD_ID, "gpt-test", 10, 8),
                    CHILD_ID,
                    APP_PROCESS,
                ),
                (
                    5,
                    base_ts + 4,
                    "codex_core::session::handlers",
                    "codex_core::session::handlers",
                    _submission_body(BACKGROUND_ID, title_prompt),
                    BACKGROUND_ID,
                    WORKER_PROCESS,
                ),
                (
                    6,
                    base_ts + 5,
                    "feedback_tags",
                    "codex_feedback",
                    _feedback_body(BACKGROUND_ID, "gpt-test", r"C:\project"),
                    BACKGROUND_ID,
                    WORKER_PROCESS,
                ),
                (
                    7,
                    base_ts + 6,
                    "codex_core::session::turn",
                    "codex_core::session::turn",
                    _turn_body(BACKGROUND_ID, "gpt-test", 100, 80),
                    BACKGROUND_ID,
                    WORKER_PROCESS,
                ),
                (
                    8,
                    base_ts + 7,
                    "codex_core::session::turn",
                    "codex_core::session::turn",
                    _turn_body(BACKGROUND_ID, "gpt-test", 120, 100),
                    BACKGROUND_ID,
                    WORKER_PROCESS,
                ),
                (
                    9,
                    base_ts + 8,
                    "codex_core::session::handlers",
                    "codex_core::session::handlers",
                    _submission_body(UNKNOWN_ID, "Unrecognized internal prompt"),
                    UNKNOWN_ID,
                    APP_PROCESS,
                ),
                (
                    10,
                    base_ts + 9,
                    "codex_core::session::turn",
                    "codex_core::session::turn",
                    _turn_body(UNKNOWN_ID, "gpt-test", 50, 40),
                    UNKNOWN_ID,
                    APP_PROCESS,
                ),
            ]
            _create_logs(logs_path, rows)
            _create_state(state_path)
            store = BackgroundUsageStore(audit_path)
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                grace_seconds=8,
                now=lambda: float(base_ts + 30),
            )

            first = scanner.scan()
            payload = store.query(
                range_key="all",
                now=datetime.fromtimestamp(base_ts + 30).astimezone(),
            )

            self.assertEqual(first.source_cursor, 10)
            self.assertEqual(payload["summary"]["eventCount"], 2)
            by_id = {item["eventId"]: item for item in payload["events"]}
            self.assertNotIn(VISIBLE_ID, by_id)
            self.assertNotIn(CHILD_ID, by_id)
            self.assertEqual(by_id[BACKGROUND_ID]["requestCount"], 2)
            self.assertFalse(by_id[BACKGROUND_ID]["workdirAvailable"])
            self.assertEqual(by_id[BACKGROUND_ID]["workdirAssociation"], "log_observed")
            self.assertEqual(by_id[BACKGROUND_ID]["featureLabel"], "任务标题与描述")
            self.assertIn(
                {"key": "title_description", "label": "任务标题与描述"},
                payload["filters"]["features"],
            )
            self.assertEqual(by_id[BACKGROUND_ID]["totalTokens"], 220)
            self.assertEqual(
                by_id[BACKGROUND_ID]["featureKey"],
                "title_description",
            )
            self.assertEqual(by_id[UNKNOWN_ID]["featureKey"], "unknown")
            self.assertEqual(
                by_id[UNKNOWN_ID]["appAttribution"],
                "visible_app_thread",
            )
            self.assertNotIn("prompt", json.dumps(payload).casefold())

            with closing(sqlite3.connect(audit_path)) as connection:
                connection.execute(
                    "UPDATE background_events SET feature_key=?, feature_label=? "
                    "WHERE event_id=?",
                    (
                        "title_description",
                        "Title and description generation",
                        UNKNOWN_ID,
                    ),
                )
            deduplicated = store.query(
                range_key="all",
                now=datetime.fromtimestamp(base_ts + 30).astimezone(),
            )
            self.assertEqual(
                [
                    item
                    for item in deduplicated["filters"]["features"]
                    if item["key"] == "title_description"
                ],
                [{"key": "title_description", "label": "任务标题与描述"}],
            )

            detail = store.detail(BACKGROUND_ID)
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail["prompt"], title_prompt)
            self.assertEqual(len(detail["requests"]), 2)
            self.assertEqual(
                detail["requests"][1]["estimatedCachedTokens"],
                80,
            )
            self.assertEqual(
                detail["requests"][0]["priceSnapshot"]["provider"],
                "custom",
            )

            with closing(sqlite3.connect(logs_path)) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO logs(
                        id, ts, target, module_path, feedback_log_body,
                        thread_id, process_uuid
                    ) VALUES(11, ?, 'codex_core::session::turn',
                             'codex_core::session::turn', ?, ?, ?)
                    """,
                    (
                        base_ts + 31,
                        _turn_body(BACKGROUND_ID, "gpt-test", 140, 110),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                )
            second = scanner.scan()
            self.assertEqual(second.source_cursor, 11)
            self.assertLess(second.processed_rows, first.processed_rows)
            self.assertEqual(store.detail(BACKGROUND_ID)["requestCount"], 3)

            self.assertTrue(
                store.confirm(
                    BACKGROUND_ID,
                    now=datetime.fromtimestamp(base_ts + 40).astimezone(),
                )
            )
            self.assertFalse(store.confirm(BACKGROUND_ID))
            restarted = BackgroundUsageStore(audit_path)
            self.assertNotIn(
                BACKGROUND_ID,
                {item["eventId"] for item in restarted.pending_today()},
            )

    def test_scan_estimates_missing_token_counts_without_output_overcharge(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            prompt = (
                "## Memory Writing Agent: Phase 2 (Consolidation)\n"
                "Consolidate raw memories for later reuse."
            )
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(BACKGROUND_ID, prompt),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "feedback_tags",
                        "codex_feedback",
                        _feedback_body(BACKGROUND_ID, "gpt-test", r"C:\memories"),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        3,
                        base_ts + 2,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 33673, None),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        4,
                        base_ts + 3,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 36220, None),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state(state_path)
            store = BackgroundUsageStore(root / "audit.sqlite3")
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                grace_seconds=0,
                now=lambda: float(base_ts + 30),
            )

            scanner.scan()
            detail = store.detail(BACKGROUND_ID)
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail["featureKey"], "memory_consolidation")
            self.assertEqual(detail["featureLabel"], "记忆整理")
            self.assertEqual(
                detail["featureEnLabel"], "Memory consolidation"
            )
            self.assertIn("提取记忆", detail["featurePurpose"])
            self.assertEqual(detail["requests"][0]["estimatedInputTokens"], 33673)
            self.assertEqual(detail["requests"][0]["estimatedOutputTokens"], 0)
            self.assertEqual(detail["requests"][1]["estimatedInputTokens"], 33673)
            self.assertEqual(detail["requests"][1]["estimatedCachedTokens"], 33673)
            self.assertEqual(detail["requests"][1]["estimatedOutputTokens"], 2547)
            # Pure-output overcharge would bill 33673+36220 at $4/MTok = $0.279572.
            self.assertLess(float(detail["estimatedCostUsd"] or 0.0), 0.10)

    def test_scan_repairs_legacy_zero_input_output_overcharge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            audit_path = root / "audit.sqlite3"
            base_ts = 1_900_000_000
            prompt = (
                "## Memory Writing Agent: Phase 2 (Consolidation)\n"
                "Repair previously overcharged request splits."
            )
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(BACKGROUND_ID, prompt),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state(state_path)
            store = BackgroundUsageStore(audit_path)
            with closing(sqlite3.connect(audit_path)) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO background_events(
                        event_id, thread_id, process_uuid, feature_key, feature_label,
                        prompt, provider, first_seen_at, last_seen_at,
                        classification_state, app_attribution, request_count,
                        total_tokens, estimated_cost_usd, cost_available
                    ) VALUES(?, ?, ?, 'memory_consolidation', '记忆整理', ?,
                             'custom', ?, ?, 'background', 'feature_signature',
                             2, 69893, 0.279572, 1)
                    """,
                    (
                        BACKGROUND_ID,
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                        prompt,
                        base_ts,
                        base_ts + 3,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO background_requests(
                        request_id, event_id, source_log_id, occurred_at, model,
                        endpoint, total_tokens, estimated_input_tokens,
                        estimated_cached_tokens, estimated_output_tokens,
                        estimated_cost_usd, price_snapshot_json
                    ) VALUES(?, ?, ?, ?, 'gpt-test', '/responses', ?, 0, 0, ?, ?, '')
                    """,
                    [
                        (
                            "log:10",
                            BACKGROUND_ID,
                            10,
                            base_ts + 2,
                            33673,
                            33673,
                            0.134692,
                        ),
                        (
                            "log:11",
                            BACKGROUND_ID,
                            11,
                            base_ts + 3,
                            36220,
                            36220,
                            0.14488,
                        ),
                    ],
                )
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                grace_seconds=0,
                now=lambda: float(base_ts + 30),
            )

            first = scanner.scan()
            detail = store.detail(BACKGROUND_ID)
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertTrue(first.content_changed)
            self.assertEqual(detail["requests"][0]["estimatedInputTokens"], 33673)
            self.assertEqual(detail["requests"][0]["estimatedOutputTokens"], 0)
            self.assertEqual(detail["requests"][1]["estimatedInputTokens"], 33673)
            self.assertEqual(detail["requests"][1]["estimatedOutputTokens"], 2547)
            self.assertLess(float(detail["estimatedCostUsd"] or 0.0), 0.10)

            second = scanner.scan()
            self.assertFalse(second.content_changed)

    def test_scan_waits_for_state_database_before_classifying(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            prompt = (
                "# Overview\nGenerate 0 to 3 hyperpersonalized suggestions "
                "for what this user can do with Codex"
            )
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(BACKGROUND_ID, prompt),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 30, 20),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            store = BackgroundUsageStore(root / "audit.sqlite3")
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                grace_seconds=0,
                now=lambda: float(base_ts + 30),
            )

            unavailable = scanner.scan()
            self.assertEqual(unavailable.processed_rows, 0)
            self.assertEqual(unavailable.diagnostics, ("state_database_missing",))
            self.assertEqual(store.query(range_key="all")["events"], [])

            _create_state(state_path)
            recovered = scanner.scan()
            self.assertEqual(recovered.source_cursor, 2)
            self.assertEqual(
                store.query(range_key="all")["events"][0]["eventId"],
                BACKGROUND_ID,
            )

    def test_scan_skips_one_malformed_log_row_and_advances_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            prompt = (
                "# Overview\nGenerate 0 to 3 hyperpersonalized suggestions "
                "for what this user can do with Codex"
            )
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(BACKGROUND_ID, prompt),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        "invalid-timestamp",
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 999, 900),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        3,
                        base_ts + 2,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 40, 30),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state(state_path)
            store = BackgroundUsageStore(root / "audit.sqlite3")
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                grace_seconds=0,
                now=lambda: float(base_ts + 30),
            )

            result = scanner.scan()
            detail = store.detail(BACKGROUND_ID)
            self.assertEqual(result.source_cursor, 3)
            self.assertIn("malformed_log_row", result.diagnostics)
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail["requestCount"], 1)
            self.assertEqual(detail["totalTokens"], 40)

    def test_missing_price_is_unavailable_instead_of_zero_cost(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            prompt = (
                "# Overview\nGenerate 0 to 3 hyperpersonalized suggestions "
                "for what this user can do with Codex"
            )
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(BACKGROUND_ID, prompt),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "unpriced-model", 40, 30),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        3,
                        base_ts + 2,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-5.6-luna", 50, 40),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state(state_path)
            store = BackgroundUsageStore(root / "audit.sqlite3")
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table={},
                pricing_versions=[],
                grace_seconds=0,
                now=lambda: float(base_ts + 30),
            )

            scanner.scan()
            payload = store.query(range_key="all")
            self.assertFalse(payload["summary"]["costComplete"])
            self.assertIsNone(payload["summary"]["estimatedCostUsd"])
            self.assertIsNone(payload["events"][0]["estimatedCostUsd"])
            requests = store.detail(BACKGROUND_ID)["requests"]
            self.assertEqual(requests[0]["priceStatus"], "unavailable")
            self.assertEqual(requests[0]["priceSnapshot"]["status"], "unavailable")
            self.assertEqual(requests[1]["priceStatus"], "fallback")
            self.assertEqual(requests[1]["priceSnapshot"]["status"], "fallback")

    def test_versioned_price_uses_request_time_and_reconfigure_keeps_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            prompt = (
                "## Memory Writing Agent: Phase 2 (Consolidation)\n"
                "Keep the selected historical price immutable."
            )
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(BACKGROUND_ID, prompt),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 1_000_000, 1_000_000),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state(state_path)
            store = BackgroundUsageStore(root / "audit.sqlite3")
            old_version = _version(
                "old-version",
                effective_at=base_ts - 100,
                input_price=1.0,
                output_price=2.0,
            )
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                pricing_versions=[old_version],
                grace_seconds=0,
                now=lambda: float(base_ts + 30),
            )

            scanner.scan()
            request = store.detail(BACKGROUND_ID)["requests"][0]
            self.assertEqual(request["estimatedCostUsd"], 1.0)
            self.assertEqual(request["priceStatus"], "versioned")
            self.assertEqual(request["priceVersionId"], "old-version")

            scanner.reconfigure(
                provider="custom",
                price_table=_prices(),
                pricing_versions=[
                    old_version,
                    _version(
                        "new-version",
                        effective_at=base_ts,
                        input_price=3.0,
                        output_price=6.0,
                    ),
                ],
            )
            self.assertFalse(scanner.scan().content_changed)
            unchanged = store.detail(BACKGROUND_ID)["requests"][0]
            self.assertEqual(unchanged["estimatedCostUsd"], 1.0)
            self.assertEqual(unchanged["priceVersionId"], "old-version")

    def test_grace_deadline_promotes_without_new_source_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            prompt = (
                "# Overview\nGenerate 0 to 3 hyperpersonalized suggestions "
                "for what this user can do with Codex"
            )
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(GRACE_ID, prompt),
                        GRACE_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(GRACE_ID, "gpt-test", 30, 20),
                        GRACE_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state(state_path)
            store = BackgroundUsageStore(root / "audit.sqlite3")
            clock = [float(base_ts + 5)]
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                grace_seconds=10,
                now=lambda: clock[0],
            )

            first = scanner.scan()
            self.assertEqual(first.pending_deadline, float(base_ts + 10))
            self.assertEqual(store.query(range_key="all")["events"], [])

            clock[0] = float(base_ts + 11)
            second = scanner.scan()
            self.assertEqual(second.processed_rows, 0)
            self.assertTrue(second.content_changed)
            self.assertEqual(
                store.query(range_key="all")["events"][0]["eventId"],
                GRACE_ID,
            )


class _FakeWatcher:
    instance: "_FakeWatcher | None" = None

    def __init__(self, callback: object, **_kwargs: object) -> None:
        self.callback = callback
        self.specs: list[object] = []
        self.closed = False
        self.event_driven = True
        self.polling_cause = ""
        _FakeWatcher.instance = self

    def update(self, specs: object) -> None:
        self.specs = list(specs)  # type: ignore[arg-type]

    def close(self) -> None:
        self.closed = True

    def trigger(self) -> None:
        self.callback({"background-usage-log"}, set())  # type: ignore[operator]


class BackgroundUsageRuntimeTests(unittest.TestCase):
    def test_runtime_refreshes_session_notification_cache_after_confirm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            source_prompt = "Show associated background work in the current HUD."
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(
                            BACKGROUND_ID,
                            _title_description_prompt(source_prompt),
                        ),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 30, 20),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state_with_sessions(
                state_path,
                [(RELATED_SESSION_ID, source_prompt, base_ts - 10)],
            )
            events = RuntimeEventBus()
            runtime = BackgroundUsageRuntime(
                logs_path=logs_path,
                state_path=state_path,
                database_path=root / "audit.sqlite3",
                provider="custom",
                price_table=_prices(),
                event_bus=events,
                watcher_factory=_FakeWatcher,  # type: ignore[arg-type]
                clock=lambda: float(base_ts + 30),
            )
            try:
                runtime.start()
                self.assertTrue(runtime.wait_until_idle())
                self.assertEqual(
                    runtime.notification_for_session(RELATED_SESSION_ID),
                    {
                        "count": 1,
                        "eventId": BACKGROUND_ID,
                        "range": "today",
                    },
                )
                self.assertEqual(runtime.pending_today(), [])
                self.assertTrue(runtime.confirm(BACKGROUND_ID))
                self.assertEqual(
                    runtime.notification_for_session(RELATED_SESSION_ID),
                    {},
                )
            finally:
                runtime.close()

    def test_runtime_is_event_driven_and_closes_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = int(time.time()) - 30
            prompt = (
                "# Overview\nGenerate 0 to 3 hyperpersonalized suggestions "
                "for what this user can do with Codex"
            )
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(BACKGROUND_ID, prompt),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 30, 20),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state(state_path)
            events = RuntimeEventBus()
            runtime = BackgroundUsageRuntime(
                logs_path=logs_path,
                state_path=state_path,
                database_path=root / "audit.sqlite3",
                provider="custom",
                price_table=_prices(),
                event_bus=events,
                watcher_factory=_FakeWatcher,  # type: ignore[arg-type]
            )
            try:
                runtime.start()
                self.assertTrue(runtime.wait_until_idle())
                initial_events = events.drain()
                self.assertEqual(
                    [event.type for event in initial_events],
                    ["background_usage_changed"],
                )
                self.assertEqual(len(runtime.pending_today()), 1)
                self.assertIsNotNone(_FakeWatcher.instance)
                assert _FakeWatcher.instance is not None
                self.assertEqual(len(_FakeWatcher.instance.specs), 2)

                time.sleep(0.05)
                self.assertEqual(events.drain(), [])

                with closing(sqlite3.connect(logs_path)) as connection, connection:
                    connection.execute(
                        """
                        INSERT INTO logs(
                            id, ts, target, module_path, feedback_log_body,
                            thread_id, process_uuid
                        ) VALUES(3, ?, 'codex_core::session::turn',
                                 'codex_core::session::turn', ?, ?, ?)
                        """,
                        (
                            base_ts + 2,
                            _turn_body(BACKGROUND_ID, "gpt-test", 40, 30),
                            BACKGROUND_ID,
                            WORKER_PROCESS,
                        ),
                    )
                _FakeWatcher.instance.trigger()
                self.assertTrue(runtime.wait_until_idle())
                changed_events = events.drain()
                self.assertEqual(
                    [event.type for event in changed_events],
                    ["background_usage_changed"],
                )
                self.assertEqual(runtime.detail(BACKGROUND_ID)["requestCount"], 2)
            finally:
                runtime.close()
            self.assertTrue(_FakeWatcher.instance.closed)


if __name__ == "__main__":
    unittest.main()
