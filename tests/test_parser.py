"""Unit tests for Codex JSONL and SQLite SSE parsing."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.core.calculator import estimate_tokens
from codex_usage_hud.core.parser import (
    CostEstimator,
    JsonlSessionParser,
    JsonlTailState,
    RequestTokens,
    SseRequestStateMachine,
    active_output_tail,
    classify_gap,
    command_execution_text,
    extract_log_field,
    extract_session_thread_identity,
    parse_timestamp,
    tool_command_text,
)
from codex_usage_hud.core.calculator import UsageCalculator


def record(
    timestamp: str,
    record_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {"timestamp": timestamp, "type": record_type, "payload": payload}


def token_count(
    timestamp: str,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    total_tokens: int,
    cumulative_total: int,
) -> dict[str, Any]:
    return record(
        timestamp,
        "event_msg",
        {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": reasoning_tokens,
                    "total_tokens": total_tokens,
                },
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": reasoning_tokens,
                    "total_tokens": cumulative_total,
                },
            },
        },
    )


class TimestampAndFieldTests(unittest.TestCase):
    def test_parse_timestamp_accepts_z_suffix(self) -> None:
        parsed = parse_timestamp("2026-05-28T01:02:03Z")

        self.assertEqual(parsed, datetime(2026, 5, 28, 1, 2, 3, tzinfo=timezone.utc))

    def test_extract_log_field_handles_quoted_values(self) -> None:
        body = 'event.name="codex.sse_event" slug="gpt-5.5" model=gpt-5.4'

        self.assertEqual(extract_log_field(body, "slug"), "gpt-5.5")
        self.assertEqual(extract_log_field(body, "model"), "gpt-5.4")


class JsonlSessionParserTests(unittest.TestCase):
    def test_latest_activity_keeps_command_visible_after_token_count(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record(
                "2026-05-28T00:00:00Z",
                "event_msg",
                {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "command": ["pwsh.exe", "-Command", "git status"],
                        "status": "completed",
                    },
                },
            ),
            token_count("2026-05-28T00:00:01Z", 1, 0, 1, 0, 2, 2),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        activity = parser.latest_activity(records)

        self.assertEqual(activity.kind, "tool call")
        self.assertEqual(activity.detail, "执行命令: git status")

    def test_activity_steps_closes_unique_exec_without_merging_other_tools(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:00:01Z",
                "response_item",
                {
                    "type": "function_call",
                    "name": "wait",
                    "call_id": "wait-call",
                    "arguments": "{}",
                },
            ),
            record(
                "2026-05-28T00:00:02Z",
                "response_item",
                {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "call_id": "exec-call",
                    "input": json.dumps({"cmd": "git status --short"}),
                },
            ),
            record(
                "2026-05-28T00:00:03Z",
                "event_msg",
                {
                    "type": "item_completed",
                    "item": {
                        "id": "desktop-command-id",
                        "type": "CommandExecution",
                        "command": [
                            "pwsh.exe",
                            "-NoProfile",
                            "-Command",
                            "git status --short",
                        ],
                        "status": "failed",
                    },
                },
            ),
            record(
                "2026-05-28T00:00:04Z",
                "response_item",
                {
                    "type": "custom_tool_call_output",
                    "call_id": "exec-call",
                    "output": "exit code: 1",
                },
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        steps = parser.activity_steps(records, task_started_index=0)

        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].title, "调用工具")
        self.assertEqual(steps[0].status, "running")
        self.assertEqual(steps[1].title, "命令失败")
        self.assertEqual(steps[1].status, "failed")
        self.assertIn("git status --short", steps[1].detail)

    def test_activity_steps_normalize_shell_launcher_and_keep_tool_output(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:00:01Z",
                "response_item",
                {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "call_id": "exec-call",
                    "input": json.dumps({"cmd": "git status --short"}),
                },
            ),
            record(
                "2026-05-28T00:00:02Z",
                "event_msg",
                {
                    "type": "item_started",
                    "item": {
                        "id": "desktop-command-id",
                        "type": "CommandExecution",
                        "command": [
                            "pwsh.exe",
                            "-NoProfile",
                            "-Command",
                            "git status --short",
                        ],
                        "status": "in_progress",
                    },
                },
            ),
            record(
                "2026-05-28T00:00:03Z",
                "event_msg",
                {
                    "type": "item_completed",
                    "item": {
                        "id": "desktop-command-id",
                        "type": "CommandExecution",
                        "command": [
                            "pwsh.exe",
                            "-NoProfile",
                            "-Command",
                            "git status --short",
                        ],
                        "status": "completed",
                        "aggregated_output": "working tree clean",
                    },
                },
            ),
            record(
                "2026-05-28T00:00:04Z",
                "response_item",
                {
                    "type": "custom_tool_call_output",
                    "call_id": "exec-call",
                    "output": "working tree clean",
                },
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        steps = parser.activity_steps(records, task_started_index=0)

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].tool_name, "exec")
        self.assertEqual(steps[0].status, "completed")
        self.assertEqual(steps[0].detail, "git status --short")
        self.assertEqual(steps[0].output, "working tree clean")

    def test_activity_steps_include_event_level_mcp_tool_calls(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-08-29T02:00:00Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-08-29T02:00:01Z",
                "event_msg",
                {
                    "type": "item_completed",
                    "item": {
                        "type": "McpToolCall",
                        "id": "mcp-1",
                        "server": "codebase-memory-mcp",
                        "tool": "search_graph",
                        "arguments": {"query": "activity steps"},
                        "status": "completed",
                    },
                },
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        steps = parser.activity_steps(records, task_started_index=0)

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].tool_name, "mcp__codebase_memory_mcp__search_graph")
        self.assertEqual(steps[0].title, "工具完成")
        self.assertIn("search_graph", steps[0].detail)

    def test_command_execution_text_only_strips_shell_launcher_wrapper(self) -> None:
        # A real shell wrapper is normalised to the inner command.
        self.assertEqual(
            command_execution_text(
                {
                    "type": "CommandExecution",
                    "command": [
                        "pwsh.exe",
                        "-NoProfile",
                        "-Command",
                        "git status --short",
                    ],
                }
            ),
            "git status --short",
        )
        # A bare command whose own arguments contain a flag word must be
        # preserved verbatim - never stripped.
        self.assertEqual(
            command_execution_text(
                {
                    "type": "CommandExecution",
                    "command": [
                        "curl",
                        "-c",
                        "cookies.txt",
                        "https://example.com",
                    ],
                }
            ),
            "curl -c cookies.txt https://example.com",
        )
        self.assertEqual(
            command_execution_text(
                {
                    "type": "CommandExecution",
                    "command": ["git", "log", "-c", "user.name=alice", "--oneline"],
                }
            ),
            "git log -c user.name=alice --oneline",
        )
        self.assertEqual(
            command_execution_text(
                {
                    "type": "CommandExecution",
                    "command": ["bash", "-lc", "npm test"],
                }
            ),
            "npm test",
        )

    def test_session_meta_exposes_provider_and_cli_client_kind(self) -> None:
        parser = JsonlSessionParser()
        snapshot = parser.parse_records(
            [
                record(
                    "2026-05-28T00:00:00Z",
                    "session_meta",
                    {
                        "id": "cli-1",
                        "model_provider": "Muyuan",
                        "originator": "codex-tui",
                        "source": "cli",
                    },
                )
            ]
        )

        self.assertEqual(snapshot.model_provider, "muyuan")
        self.assertEqual(snapshot.profile_name, "muyuan")
        self.assertEqual(snapshot.originator, "codex-tui")
        self.assertEqual(snapshot.session_source, "cli")
        self.assertEqual(snapshot.client_kind, "cli")

    def test_session_meta_without_provider_uses_visible_unknown_channel(self) -> None:
        parser = JsonlSessionParser()
        snapshot = parser.parse_records(
            [
                record(
                    "2026-05-28T00:00:00Z",
                    "session_meta",
                    {"id": "app-1", "originator": "Codex Desktop", "source": "vscode"},
                )
            ]
        )

        self.assertEqual(snapshot.model_provider, "unknown")
        self.assertEqual(snapshot.client_kind, "app")

    def test_exec_source_uses_originator_instead_of_assuming_app(self) -> None:
        parser = JsonlSessionParser()
        cli_snapshot = parser.parse_records(
            [
                record(
                    "2026-05-28T00:00:00Z",
                    "session_meta",
                    {
                        "id": "exec-cli",
                        "originator": "codex_exec",
                        "source": "exec",
                    },
                )
            ]
        )
        unknown_snapshot = parser.parse_records(
            [
                record(
                    "2026-05-28T00:00:00Z",
                    "session_meta",
                    {"id": "exec-unknown", "source": "exec"},
                )
            ]
        )

        self.assertEqual(cli_snapshot.client_kind, "cli")
        self.assertEqual(unknown_snapshot.client_kind, "unknown")

    def test_cost_estimator_selects_provider_specific_price(self) -> None:
        estimator = CostEstimator(
            UsageCalculator(
                {
                    "base": {"input": 1, "cached_input": 1, "output": 1, "reasoning": 1},
                    "muyuan": {
                        "model": "gpt-5",
                        "provider": "muyuan",
                        "input": 9,
                        "cached_input": 9,
                        "output": 9,
                        "reasoning": 9,
                    },
                }
            )
        )

        self.assertEqual(estimator.calculate("gpt-5", 1_000_000, 0, 0, provider="muyuan"), 9.0)

    def test_incremental_parse_reads_only_appended_complete_records(self) -> None:
        parser = JsonlSessionParser()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            first = record("2026-05-28T00:00:00Z", "session_meta", {"id": "s1"})
            second = token_count("2026-05-28T00:00:01Z", 10, 2, 3, 1, 13, 13)
            path.write_text(json.dumps(first) + "\n", encoding="utf-8", newline="\n")

            snapshot, state = parser.parse_file_incremental(path)
            initial_offset = state.offset
            path.write_text(
                json.dumps(first) + "\n" + json.dumps(second) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            snapshot, state = parser.parse_file_incremental(path, state)

        self.assertEqual(snapshot.line_count, 2)
        self.assertEqual(snapshot.confirmed.cumulative_total, 13)
        self.assertIsInstance(state, JsonlTailState)
        self.assertEqual(state.line_count, 2)
        self.assertGreater(state.offset, initial_offset)

    def test_incremental_parse_preserves_incomplete_trailing_line(self) -> None:
        parser = JsonlSessionParser()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            first = record("2026-05-28T00:00:00Z", "session_meta", {"id": "s1"})
            second = record("2026-05-28T00:00:01Z", "event_msg", {"type": "task_started"})
            complete = json.dumps(first) + "\n"
            partial = json.dumps(second)[:24]
            path.write_text(complete + partial, encoding="utf-8", newline="\n")

            snapshot, state = parser.parse_file_incremental(path)
            path.write_text(
                complete + json.dumps(second) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            snapshot, state = parser.parse_file_incremental(path, state)

        self.assertEqual(snapshot.line_count, 2)
        self.assertEqual(snapshot.task_count, 1)
        self.assertEqual(
            state.offset,
            len((complete + json.dumps(second) + "\n").encode("utf-8")),
        )

    def test_tail_preview_is_bounded_and_uses_the_latest_token_state(self) -> None:
        parser = JsonlSessionParser(
            cost_estimator=CostEstimator(
                UsageCalculator(
                    {
                        "gpt-5": {
                            "input": 1,
                            "cached_input": 1,
                            "output": 1,
                            "reasoning": 1,
                        }
                    }
                )
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            metadata = record("2026-05-28T00:00:00Z", "session_meta", {"id": "s1"})
            filler = record("2026-05-28T00:00:01Z", "event_msg", {"type": "notice", "text": "x" * 1024})
            context = record("2026-05-28T00:09:59Z", "turn_context", {"model": "gpt-5"})
            latest = token_count("2026-05-28T00:10:00Z", 120, 20, 8, 2, 128, 128)
            path.write_text(
                json.dumps(metadata)
                + "\n"
                + (json.dumps(filler) + "\n") * 32
                + json.dumps(context)
                + "\n"
                + json.dumps(latest)
                + "\n",
                encoding="utf-8",
                newline="\n",
            )

            preview = parser.parse_file_tail_preview(
                path,
                session_id="s1",
                max_bytes=4096,
            )

        self.assertEqual(preview.status, "loading")
        self.assertEqual(preview.session_id, "s1")
        self.assertEqual(preview.confirmed.cumulative_total, 128)
        self.assertIsNone(preview.confirmed.cumulative_cost_usd)
        self.assertLess(preview.line_count, 33)

    def test_incremental_parse_resets_after_truncate_or_rotation(self) -> None:
        parser = JsonlSessionParser()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            first = record("2026-05-28T00:00:00Z", "session_meta", {"id": "s1"})
            second = record("2026-05-28T00:00:01Z", "event_msg", {"type": "task_started"})
            path.write_text(
                json.dumps(first) + "\n" + json.dumps(second) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            _snapshot, state = parser.parse_file_incremental(path)

            rotated = record("2026-05-28T01:00:00Z", "session_meta", {"id": "s2"})
            path.write_text(json.dumps(rotated) + "\n", encoding="utf-8", newline="\n")
            snapshot, state = parser.parse_file_incremental(path, state)

        self.assertEqual(snapshot.session_id, "s2")
        self.assertEqual(snapshot.line_count, 1)
        self.assertEqual(state.line_count, 1)

    def test_load_records_lenient_skips_partial_jsonl(self) -> None:
        parser = JsonlSessionParser()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            path.write_text(
                json.dumps(record("2026-05-28T00:00:00Z", "session_meta", {"id": "s1"}))
                + "\n"
                + '{"timestamp":',
                encoding="utf-8",
            )

            records = parser.load_records_lenient(path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["_line"], 1)
        self.assertIsNotNone(records[0]["_dt"])

    def test_parse_records_extracts_confirmed_and_pending_estimates(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record("2026-05-28T00:00:01Z", "turn_context", {"model": "gpt-5.5"}),
            token_count("2026-05-28T00:00:02Z", 100, 40, 20, 3, 120, 120),
            record(
                "2026-05-28T00:00:03Z",
                "event_msg",
                {"type": "user_message", "message": "abcd"},
            ),
            record(
                "2026-05-28T00:00:04Z",
                "response_item",
                {"type": "message", "content": [{"text": "abcdefgh"}]},
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records)

        self.assertEqual(snapshot.session_id, "session-a")
        self.assertEqual(snapshot.confirmed.last_input, 100)
        self.assertEqual(snapshot.confirmed.last_cached, 40)
        self.assertEqual(snapshot.confirmed.last_output, 20)
        self.assertEqual(snapshot.confirmed.last_reasoning, 3)
        self.assertEqual(snapshot.token_events, 1)
        self.assertEqual(snapshot.estimate.input_tokens, estimate_tokens("abcd"))
        self.assertEqual(snapshot.estimate.output_tokens, estimate_tokens("abcdefgh"))
        self.assertEqual(snapshot.request.status, "running")
        self.assertEqual(snapshot.request.source, "jsonl")
        self.assertEqual(snapshot.request.started_at, parse_timestamp("2026-05-28T00:00:03Z"))
        self.assertEqual(snapshot.request.updated_at, parse_timestamp("2026-05-28T00:00:04Z"))
        self.assertEqual(len(snapshot.request_history), 2)
        self.assertEqual(snapshot.request_history[0].status, "confirmed")
        self.assertEqual(snapshot.request_history[1].status, "running")
        self.assertEqual(
            snapshot.request_history[1].started_at,
            parse_timestamp("2026-05-28T00:00:03Z"),
        )

    def test_latest_output_ignores_later_token_count_events(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record("2026-05-28T00:00:01Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:00:02Z",
                "event_msg",
                {"type": "agent_message", "message": "最后一轮输出会保留"},
            ),
            token_count("2026-05-28T00:00:03Z", 100, 20, 5, 1, 105, 105),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records)

        self.assertEqual(snapshot.activity.kind, "agent")
        self.assertEqual(snapshot.activity.detail, "最后一轮输出会保留")
        self.assertEqual(snapshot.last_output.kind, "agent")
        self.assertEqual(snapshot.last_output.detail, "最后一轮输出会保留")

    def test_usage_events_can_be_summarized_for_time_windows(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-27T23:59:58Z", "turn_context", {"model": "gpt-5.5"}),
            token_count("2026-05-27T23:59:59Z", 50, 0, 5, 0, 55, 55),
            token_count("2026-05-28T00:00:01Z", 100, 0, 0, 0, 100, 155),
            token_count("2026-05-28T00:00:02Z", 200, 100, 10, 2, 210, 365),
        ]
        records[2]["payload"]["info"]["total_token_usage"] = {
            "input_tokens": 150,
            "cached_input_tokens": 0,
            "output_tokens": 5,
            "reasoning_output_tokens": 0,
            "total_tokens": 155,
        }
        records[3]["payload"]["info"]["total_token_usage"] = {
            "input_tokens": 350,
            "cached_input_tokens": 100,
            "output_tokens": 15,
            "reasoning_output_tokens": 2,
            "total_tokens": 365,
        }
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        events = parser.usage_events(records)
        summary = parser.summarize_usage_events(
            events,
            parse_timestamp("2026-05-28T00:00:00Z"),
        )

        self.assertEqual(len(events), 3)
        self.assertEqual(summary.tokens, 310)
        self.assertEqual(summary.input_tokens, 300)
        self.assertEqual(summary.cached_tokens, 100)
        self.assertEqual(summary.output_tokens, 10)
        self.assertEqual(summary.reasoning_tokens, 2)
        self.assertEqual(summary.cost_usd, 0.00135)

    def test_usage_events_skip_subagent_replayed_parent_history(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record(
                "2026-07-18T13:00:00Z",
                "session_meta",
                {
                    "id": "child-thread",
                    "source": {"subagent": {"thread_spawn": {"parent_thread_id": "parent"}}},
                },
            ),
            record("2026-07-18T13:00:01Z", "turn_context", {"model": "gpt-5.6-sol"}),
            token_count("2026-07-18T13:00:02Z", 100, 80, 10, 4, 110, 110),
            record(
                "2026-07-18T13:00:03Z",
                "inter_agent_communication_metadata",
                {},
            ),
            token_count("2026-07-18T13:00:04Z", 40, 32, 5, 2, 45, 155),
        ]
        records[4]["payload"]["info"]["total_token_usage"] = {
            "input_tokens": 140,
            "cached_input_tokens": 112,
            "cache_write_input_tokens": 0,
            "output_tokens": 15,
            "reasoning_output_tokens": 6,
            "total_tokens": 155,
        }
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        events = parser.usage_events(records)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].input_tokens, 40)
        self.assertEqual(events[0].cached_tokens, 32)
        self.assertEqual(events[0].output_tokens, 5)
        self.assertEqual(events[0].reasoning_tokens, 2)
        self.assertEqual(events[0].total_tokens, 45)
        self.assertEqual(events[0].cost_usd, 0.000206)

    def test_usage_events_skip_dense_forked_history_cluster_after_boundary(self) -> None:
        """Parent history is dumped at the boundary timestamp; only later live work counts."""
        parser = JsonlSessionParser()
        fork_ts = "2026-07-23T12:57:51.282Z"
        live_ts = "2026-07-23T12:58:10.000Z"
        records = [
            record(
                fork_ts,
                "session_meta",
                {
                    "id": "child-thread",
                    "session_id": "parent-thread",
                    "forked_from_id": "parent-thread",
                    "source": {
                        "subagent": {
                            "thread_spawn": {"parent_thread_id": "parent-thread"}
                        }
                    },
                },
            ),
            record(fork_ts, "turn_context", {"model": "gpt-5.6-terra"}),
            token_count(fork_ts, 180269, 178432, 1045, 100, 181314, 181314),
            record(
                fork_ts,
                "event_msg",
                {"type": "thread_settings_applied"},
            ),
            record(fork_ts, "event_msg", {"type": "task_started"}),
            # Parent-history deltas still appear after the structural boundary
            # but share the fork dump timestamp.
            token_count(fork_ts, 2076, 1024, 189, 20, 2265, 183579),
            token_count(fork_ts, 292, 192, 387, 30, 679, 184258),
            record(live_ts, "event_msg", {"type": "task_started"}),
            token_count(live_ts, 28703, 27904, 636, 50, 29339, 213597),
        ]
        # cumulative totals on last two history + live events already set via helper;
        # rewrite total_token_usage for mid/history/live deltas explicitly.
        records[5]["payload"]["info"]["total_token_usage"] = {
            "input_tokens": 182345,
            "cached_input_tokens": 179456,
            "cache_write_input_tokens": 0,
            "output_tokens": 1234,
            "reasoning_output_tokens": 120,
            "total_tokens": 183579,
        }
        records[6]["payload"]["info"]["total_token_usage"] = {
            "input_tokens": 182637,
            "cached_input_tokens": 179648,
            "cache_write_input_tokens": 0,
            "output_tokens": 1621,
            "reasoning_output_tokens": 150,
            "total_tokens": 184258,
        }
        records[8]["payload"]["info"]["total_token_usage"] = {
            "input_tokens": 211340,
            "cached_input_tokens": 207552,
            "cache_write_input_tokens": 0,
            "output_tokens": 2257,
            "reasoning_output_tokens": 200,
            "total_tokens": 213597,
        }
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        events = parser.usage_events(records)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].input_tokens, 28703)
        self.assertEqual(events[0].cached_tokens, 27904)
        self.assertEqual(events[0].output_tokens, 636)
        self.assertEqual(events[0].total_tokens, 29339)
        self.assertEqual(
            parser._history_replay_boundary(records),
            3,
        )
        self.assertGreater(
            parser._history_replay_usage_start(records) or 0,
            parser._history_replay_boundary(records) or 0,
        )

    def test_token_rounds_since_latest_task_only_include_current_task(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "turn_context", {"model": "gpt-5.5"}),
            token_count("2026-05-28T00:00:01Z", 10, 0, 5, 0, 15, 15),
            record("2026-05-28T00:00:02Z", "event_msg", {"type": "task_started"}),
            token_count("2026-05-28T00:00:03Z", 20, 5, 6, 1, 26, 41),
            token_count("2026-05-28T00:00:04Z", 20, 5, 6, 1, 26, 41),
            token_count("2026-05-28T00:00:05Z", 30, 7, 9, 2, 39, 80),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        task_index, _ = parser.latest_task_started(records)
        rounds = parser.token_rounds_since_task(records, task_index)

        self.assertEqual(len(rounds), 2)
        self.assertEqual([item.input_tokens for item in rounds], [20, 30])
        self.assertEqual([item.index for item in rounds], [1, 2])

    def test_parse_records_keeps_session_request_history_across_tasks(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "turn_context", {"model": "gpt-5.5"}),
            record(
                "2026-05-28T00:00:01Z",
                "event_msg",
                {"type": "task_started", "turn_id": "turn-1"},
            ),
            token_count("2026-05-28T00:00:02Z", 20, 5, 6, 1, 26, 26),
            record("2026-05-28T00:00:03Z", "event_msg", {"type": "task_complete"}),
            record(
                "2026-05-28T00:00:04Z",
                "event_msg",
                {"type": "task_started", "turn_id": "turn-2"},
            ),
            token_count("2026-05-28T00:00:05Z", 30, 7, 9, 2, 39, 65),
            record("2026-05-28T00:00:06Z", "event_msg", {"type": "task_complete"}),
            record(
                "2026-05-28T00:00:07Z",
                "event_msg",
                {"type": "task_started", "turn_id": "turn-3"},
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records)

        self.assertEqual(len(snapshot.session_request_history), 2)
        self.assertEqual([item.input_tokens for item in snapshot.session_request_history], [20, 30])
        self.assertEqual([item.index for item in snapshot.session_request_history], [1, 2])
        self.assertEqual(len(snapshot.activity_tasks), 3)
        self.assertEqual([item.index for item in snapshot.activity_tasks], [1, 2, 3])
        self.assertEqual([item.count for item in snapshot.activity_tasks], [3, 3, 3])
        self.assertEqual(
            [item.turn_id for item in snapshot.activity_tasks],
            ["turn-1", "turn-2", "turn-3"],
        )
        self.assertEqual(snapshot.task_turn_id, "turn-3")
        self.assertEqual(
            [len(item.request_history) for item in snapshot.activity_tasks],
            [1, 1, 1],
        )
        self.assertTrue(snapshot.activity_tasks[0].completed_at)
        self.assertTrue(snapshot.activity_tasks[1].completed_at)
        self.assertIsNone(snapshot.activity_tasks[2].completed_at)

    def test_task_rolled_back_ordinals_replays_multi_turn_rollbacks(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:01Z", "event_msg", {"type": "task_started"}),
            record("2026-05-28T00:00:02Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:00:03Z",
                "event_msg",
                {"type": "thread_rolled_back", "num_turns": 1},
            ),
            record("2026-05-28T00:00:04Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:00:05Z",
                "event_msg",
                {"type": "thread_rolled_back", "num_turns": 2},
            ),
            record("2026-05-28T00:00:06Z", "event_msg", {"type": "task_started"}),
        ]

        self.assertEqual(parser.task_rolled_back_ordinals(records), {1, 2, 3})

    def test_parse_records_marks_rolled_back_tasks(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record(
                "2026-05-28T00:00:01Z",
                "event_msg",
                {"type": "task_started", "turn_id": "turn-1"},
            ),
            token_count("2026-05-28T00:00:02Z", 20, 5, 6, 1, 26, 26),
            record("2026-05-28T00:00:03Z", "event_msg", {"type": "task_complete"}),
            # 用户重试：回滚上一轮后带着同一 turn_id 重新提交
            record(
                "2026-05-28T00:00:04Z",
                "event_msg",
                {"type": "thread_rolled_back", "num_turns": 1},
            ),
            record(
                "2026-05-28T00:00:05Z",
                "event_msg",
                {"type": "task_started", "turn_id": "turn-1"},
            ),
            token_count("2026-05-28T00:00:06Z", 30, 7, 9, 2, 39, 65),
            record("2026-05-28T00:00:07Z", "event_msg", {"type": "task_complete"}),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records)

        self.assertEqual(
            [item.rolled_back for item in snapshot.activity_tasks],
            [True, False],
        )

    def test_parse_records_extracts_prompt_for_latest_task(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record(
                "2026-05-28T00:00:01Z",
                "event_msg",
                {"type": "user_message", "message": "旧需求"},
            ),
            record("2026-05-28T00:00:02Z", "event_msg", {"type": "task_started"}),
            record("2026-05-28T00:00:03Z", "event_msg", {"type": "task_complete"}),
            record(
                "2026-05-28T00:00:04Z",
                "event_msg",
                {"type": "user_message", "message": "优化右侧完成态统计"},
            ),
            record("2026-05-28T00:00:05Z", "event_msg", {"type": "task_started"}),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records)

        self.assertEqual(snapshot.task_prompt, "优化右侧完成态统计")
        self.assertEqual(snapshot.task_index, 2)
        self.assertEqual(snapshot.task_count, 2)

    def test_user_steer_invalidates_prior_completion_without_new_task_start(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:00:01Z",
                "event_msg",
                {
                    "type": "agent_message",
                    "message": "first answer",
                    "phase": "final_answer",
                },
            ),
            record("2026-05-28T00:00:02Z", "event_msg", {"type": "task_complete"}),
            record(
                "2026-05-28T00:00:03Z",
                "event_msg",
                {"type": "user_message", "message": "continue with one more change"},
            ),
            record(
                "2026-05-28T00:00:04Z",
                "event_msg",
                {"type": "agent_reasoning", "text": "continuing"},
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records)

        self.assertIsNone(snapshot.task_completed_at)
        self.assertIsNone(snapshot.task_aborted_at)
        self.assertIsNone(snapshot.final_answer_at)
        self.assertEqual(snapshot.task_prompt, "continue with one more change")

        records.extend(
            [
                record(
                    "2026-05-28T00:00:05Z",
                    "event_msg",
                    {
                        "type": "agent_message",
                        "message": "second answer",
                        "phase": "final_answer",
                    },
                ),
                record("2026-05-28T00:00:06Z", "event_msg", {"type": "task_complete"}),
            ]
        )
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        completed = parser.parse_records(records)

        self.assertEqual(
            completed.final_answer_at,
            parse_timestamp("2026-05-28T00:00:05Z"),
        )
        self.assertEqual(
            completed.task_completed_at,
            parse_timestamp("2026-05-28T00:00:06Z"),
        )

    def test_compaction_handoff_invalidates_pre_compaction_terminal_markers(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:00:01Z",
                "event_msg",
                {"type": "user_message", "message": "continue across compaction"},
            ),
            record(
                "2026-05-28T00:00:02Z",
                "response_item",
                {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "handoff summary"}],
                },
            ),
            record("2026-05-28T00:00:03Z", "event_msg", {"type": "task_complete"}),
            record("2026-05-28T00:00:04Z", "compacted", {"summary": "handoff"}),
            record(
                "2026-05-28T00:00:05Z",
                "event_msg",
                {"type": "context_compacted"},
            ),
            record(
                "2026-05-28T00:00:06Z",
                "response_item",
                {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "work resumed"}],
                },
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        resumed = parser.parse_records(records)

        self.assertEqual(resumed.request.status, "running")
        self.assertIsNone(resumed.task_completed_at)
        self.assertIsNone(resumed.task_aborted_at)
        self.assertIsNone(resumed.final_answer_at)
        self.assertEqual(resumed.task_prompt, "continue across compaction")

        records.extend(
            [
                record(
                    "2026-05-28T00:00:07Z",
                    "event_msg",
                    {
                        "type": "agent_message",
                        "message": "actual final answer",
                        "phase": "final_answer",
                    },
                ),
                record("2026-05-28T00:00:08Z", "event_msg", {"type": "task_complete"}),
            ]
        )
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        completed = parser.parse_records(records)

        self.assertEqual(
            completed.final_answer_at,
            parse_timestamp("2026-05-28T00:00:07Z"),
        )
        self.assertEqual(
            completed.task_completed_at,
            parse_timestamp("2026-05-28T00:00:08Z"),
        )

    def test_token_rounds_capture_activity_summary_for_heavy_rounds(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "turn_context", {"model": "gpt-5.5"}),
            record("2026-05-28T00:00:01Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:00:02Z",
                "event_msg",
                {"type": "user_message", "message": "分析一个很大的日志文件"},
            ),
            token_count("2026-05-28T00:00:03Z", 200, 20, 5, 0, 205, 205),
            record(
                "2026-05-28T00:00:04Z",
                "response_item",
                {"type": "message", "content": [{"text": "输出了很长的分析结果"}]},
            ),
            token_count("2026-05-28T00:00:05Z", 10, 0, 220, 0, 230, 435),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        task_index, _ = parser.latest_task_started(records)
        rounds = parser.token_rounds_since_task(records, task_index)

        self.assertEqual(rounds[0].activity_summary, "输入：分析一个很大的日志文件")
        self.assertIn("分析一个很大的日志文件", rounds[0].copy_text)
        self.assertEqual(rounds[1].activity_summary, "输出：输出了很长的分析结果")
        self.assertIn("输出了很长的分析结果", rounds[1].copy_text)
        # 定位流程用整轮条目文本在聊天 DOM 里找可见段落，不能只有首选条目。
        self.assertEqual(
            rounds[0].activity_texts,
            ("分析一个很大的日志文件",),
        )
        self.assertEqual(
            rounds[1].activity_texts,
            ("输出了很长的分析结果",),
        )

    def test_slow_summary_ignores_tool_calls_before_latest_task(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:00:01Z",
                "response_item",
                {
                    "type": "function_call",
                    "call_id": "old",
                    "name": "shell_command",
                    "arguments": '{"command":"old"}',
                },
            ),
            record(
                "2026-05-28T00:00:21Z",
                "response_item",
                {"type": "function_call_output", "call_id": "old", "output": "old output"},
            ),
            record("2026-05-28T00:00:22Z", "event_msg", {"type": "task_complete"}),
            record("2026-05-28T00:01:00Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:01:01Z",
                "response_item",
                {
                    "type": "function_call",
                    "call_id": "new",
                    "name": "shell_command",
                    "arguments": '{"command":"new"}',
                },
            ),
            record(
                "2026-05-28T00:01:03Z",
                "response_item",
                {"type": "function_call_output", "call_id": "new", "output": "new output"},
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        summary = parser.slow_summary(records, records[-1]["_dt"])

        self.assertIsNotNone(summary.slowest_tool_call)
        assert summary.slowest_tool_call is not None
        self.assertEqual(summary.slowest_tool_call.call_id, "new")
        self.assertNotIn("20.0s", summary.slowest_tool)

    def test_parse_records_falls_back_to_jsonl_when_sse_errors(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record("2026-05-28T00:00:01Z", "turn_context", {"model": "gpt-5.5"}),
            token_count("2026-05-28T00:00:02Z", 100, 20, 5, 1, 105, 105),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        missing_db = Path(tempfile.gettempdir()) / "missing-codex-hud-sse.sqlite"
        machine = SseRequestStateMachine(db_path=missing_db)

        snapshot = parser.parse_records(records, sse_tracker=machine)

        self.assertEqual(snapshot.request.status, "confirmed")
        self.assertEqual(snapshot.request.source, "jsonl")
        self.assertEqual(snapshot.request.input_tokens, 100)
        self.assertIn("SSE log database not found", snapshot.error)

    def test_parse_records_extracts_slow_tool_and_request_wait_summaries(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record("2026-05-28T00:00:01Z", "turn_context", {"model": "gpt-5.5"}),
            record("2026-05-28T00:00:02Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:00:03Z",
                "response_item",
                {"type": "function_call", "call_id": "call_1", "name": "shell", "arguments": "{}"},
            ),
            record(
                "2026-05-28T00:00:09Z",
                "response_item",
                {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            ),
            record(
                "2026-05-28T00:00:10Z",
                "event_msg",
                {"type": "user_message", "message": "hello"},
            ),
            record(
                "2026-05-28T00:00:17Z",
                "response_item",
                {"type": "message", "content": [{"text": "hi"}]},
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records)

        self.assertEqual(snapshot.slow.slowest_tool, "6.0s shell")
        self.assertEqual(snapshot.slow.slowest_user_wait, "无（本任务无用户确认）")
        self.assertEqual(snapshot.slow.longest_gap, "7.0s model_startup")
        self.assertNotEqual(snapshot.slow.current_gap, "无")

    def test_parse_records_marks_current_gap_as_finished_after_task_complete(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record("2026-05-28T00:00:01Z", "event_msg", {"type": "task_started"}),
            record("2026-05-28T00:00:02Z", "event_msg", {"type": "task_complete"}),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records)

        self.assertEqual(snapshot.slow.current_gap, "任务已结束")
        self.assertFalse(snapshot.slow.current_gap_active)
        self.assertEqual(
            snapshot.task_completed_at,
            parse_timestamp("2026-05-28T00:00:02Z"),
        )

    def test_parse_records_ignores_turn_aborted_marker_and_ends_task(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record("2026-05-28T00:00:01Z", "turn_context", {"model": "gpt-5.5"}),
            record("2026-05-28T00:00:02Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:00:03Z",
                "response_item",
                {"type": "message", "role": "assistant", "content": [{"text": "继续处理"}]},
            ),
            token_count("2026-05-28T00:00:04Z", 80, 20, 12, 2, 92, 92),
            record(
                "2026-05-28T00:00:05Z",
                "response_item",
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "<turn_aborted>\n"
                                "The user interrupted the previous turn on purpose.\n"
                                "</turn_aborted>"
                            )
                        }
                    ],
                },
            ),
            record(
                "2026-05-28T00:00:06Z",
                "event_msg",
                {"type": "turn_aborted", "reason": "interrupted"},
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records)

        self.assertEqual(snapshot.request.status, "confirmed")
        self.assertEqual(snapshot.estimate.total_tokens, 0)
        self.assertFalse(snapshot.slow.current_gap_active)
        self.assertEqual(snapshot.last_output.detail, "继续处理")
        self.assertEqual(
            snapshot.task_aborted_at,
            parse_timestamp("2026-05-28T00:00:06Z"),
        )

    def test_parse_records_clears_running_sse_request_after_turn_aborted(self) -> None:
        parser = JsonlSessionParser()
        tracker = SseRequestStateMachine(db_path=None)
        tracker.current = RequestTokens(
            status="running",
            round_index=1,
            model="gpt-5.5",
            source="sse",
            started_at=parse_timestamp("2026-05-28T00:00:05Z"),
            updated_at=parse_timestamp("2026-05-28T00:00:05Z"),
            total_tokens=0,
            output_tokens=0,
            estimated=True,
        )
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record("2026-05-28T00:00:01Z", "turn_context", {"model": "gpt-5.5"}),
            record("2026-05-28T00:00:02Z", "event_msg", {"type": "task_started"}),
            token_count("2026-05-28T00:00:04Z", 40, 10, 8, 1, 48, 48),
            record(
                "2026-05-28T00:00:05Z",
                "response_item",
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "<turn_aborted>\n"
                                "The user interrupted the previous turn on purpose.\n"
                                "</turn_aborted>"
                            )
                        }
                    ],
                },
            ),
            record(
                "2026-05-28T00:00:06Z",
                "event_msg",
                {"type": "turn_aborted", "reason": "interrupted"},
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records, sse_tracker=tracker)

        self.assertEqual(snapshot.request.status, "confirmed")
        self.assertEqual(
            snapshot.request.completed_at,
            parse_timestamp("2026-05-28T00:00:06Z"),
        )
        self.assertTrue(snapshot.request_history)
        self.assertNotIn("running", [item.status for item in snapshot.request_history])


class SseRequestStateMachineTests(unittest.TestCase):
    def test_state_machine_estimates_delta_then_confirms_from_otel_counts(self) -> None:
        machine = SseRequestStateMachine()
        rows = [
            {
                "id": 1,
                "target": "codex_otel.log_only",
                "feedback_log_body": (
                    'event.name="codex.sse_event" event.kind=response.created '
                    'conversation.id=session-a slug=gpt-5.5 '
                    "event.timestamp=2026-05-28T00:00:00Z"
                ),
            },
            {
                "id": 2,
                "target": "codex_api::sse::responses",
                "feedback_log_body": (
                    'SSE event: {"type":"response.output_text.delta","delta":"abcd"}'
                ),
            },
            {
                "id": 3,
                "target": "codex_otel.log_only",
                "feedback_log_body": (
                    'event.name="codex.sse_event" '
                    "event.kind=response.output_text.delta "
                    "conversation.id=session-a"
                ),
            },
            {
                "id": 4,
                "target": "codex_otel.log_only",
                "feedback_log_body": (
                    'event.name="codex.sse_event" event.kind=response.completed '
                    "input_token_count=100 cached_token_count=20 "
                    "output_token_count=5 reasoning_token_count=1 "
                    'conversation.id=session-a slug=gpt-5.5 '
                    "event.timestamp=2026-05-28T00:00:02Z"
                ),
            },
        ]

        machine.consume_log_rows(rows, "session-a")

        self.assertEqual(machine.current.status, "confirmed")
        self.assertEqual(machine.current.input_tokens, 100)
        self.assertEqual(machine.current.cached_tokens, 20)
        self.assertEqual(machine.current.output_tokens, 5)
        self.assertEqual(machine.current.reasoning_tokens, 1)
        self.assertEqual(machine.current.total_tokens, 105)
        self.assertFalse(machine.current.estimated)
        self.assertEqual(machine.rounds()[0].status, "confirmed")

    def test_sse_completed_event_can_confirm_without_otel_counts(self) -> None:
        machine = SseRequestStateMachine()
        machine.start_round(parse_timestamp("2026-05-28T00:00:00Z"), "gpt-5.5")
        machine.consume_sse(
            "SSE event: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_1",
                        "model": "gpt-5.5",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 4,
                            "total_tokens": 14,
                            "input_tokens_details": {"cached_tokens": 3},
                            "output_tokens_details": {"reasoning_tokens": 1},
                        },
                    },
                }
            )
        )

        self.assertEqual(machine.current.status, "confirmed")
        self.assertEqual(machine.current.response_id, "resp_1")
        self.assertEqual(machine.current.cached_tokens, 3)
        self.assertEqual(machine.current.total_tokens, 14)

    def test_build_reads_sqlite_logs_table_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "logs.sqlite"
            con = sqlite3.connect(db_path)
            try:
                con.execute(
                    "create table logs ("
                    "id integer primary key, "
                    "ts integer, "
                    "target text, "
                    "feedback_log_body text)"
                )
                con.executemany(
                    "insert into logs (id, ts, target, feedback_log_body) values (?, ?, ?, ?)",
                    [
                        (
                            1,
                            1_779_926_400,
                            "codex_otel.log_only",
                            'event.name="codex.sse_event" '
                            "event.kind=response.created "
                            "conversation.id=session-a slug=gpt-5.5 "
                            "event.timestamp=2026-05-28T00:00:00Z",
                        ),
                        (
                            2,
                            1_779_926_401,
                            "codex_otel.log_only",
                            'event.name="codex.sse_event" '
                            "event.kind=response.completed "
                            "input_token_count=50 cached_token_count=10 "
                            "output_token_count=8 reasoning_token_count=2 "
                            "conversation.id=session-a slug=gpt-5.5 "
                            "event.timestamp=2026-05-28T00:00:01Z",
                        ),
                    ],
                )
                con.commit()
            finally:
                con.close()

            machine = SseRequestStateMachine(db_path=db_path)
            request, history = machine.build("session-a")

        self.assertEqual(request.status, "confirmed")
        self.assertEqual(request.input_tokens, 50)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].output_tokens, 8)


if __name__ == "__main__":
    unittest.main()


class MultiAgentSessionMetaTests(unittest.TestCase):
    def test_extract_session_thread_identity_for_subagent_meta(self) -> None:
        payload = {
            "id": "child-1",
            "thread_source": "subagent",
            "parent_thread_id": "parent-1",
            "agent_nickname": "Rawls",
            "source": {
                "subagent": {
                    "thread_spawn": {
                        "parent_thread_id": "parent-1",
                        "agent_nickname": "Rawls",
                    }
                }
            },
        }
        thread_source, parent_id, nickname, is_sub = extract_session_thread_identity(payload)
        self.assertEqual(thread_source, "subagent")
        self.assertEqual(parent_id, "parent-1")
        self.assertEqual(nickname, "Rawls")
        self.assertTrue(is_sub)

    def test_extract_session_thread_identity_for_user_meta(self) -> None:
        payload = {
            "id": "parent-1",
            "thread_source": "user",
            "source": "cli",
        }
        thread_source, parent_id, nickname, is_sub = extract_session_thread_identity(payload)
        self.assertEqual(thread_source, "user")
        self.assertEqual(parent_id, "")
        self.assertEqual(nickname, "")
        self.assertFalse(is_sub)

    def test_parser_marks_subagent_from_first_session_meta(self) -> None:
        rows = [
            record(
                "2026-07-18T01:00:00Z",
                "session_meta",
                {
                    "id": "child-1",
                    "thread_source": "subagent",
                    "parent_thread_id": "parent-1",
                    "agent_nickname": "Singer",
                    "cwd": "E:/Project/demo",
                    "originator": "codex-tui",
                    "source": "cli",
                },
            ),
            record(
                "2026-07-18T01:00:01Z",
                "session_meta",
                {
                    "id": "parent-1",
                    "thread_source": "user",
                    "cwd": "E:/Project/demo",
                    "originator": "codex-tui",
                    "source": "cli",
                },
            ),
            record("2026-07-18T01:00:02Z", "event_msg", {"type": "task_started"}),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "child.jsonl"
            path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            parsed = JsonlSessionParser().parse_file(path)
        self.assertEqual(parsed.session_id, "child-1")
        self.assertEqual(parsed.thread_source, "subagent")
        self.assertEqual(parsed.parent_thread_id, "parent-1")
        self.assertEqual(parsed.agent_nickname, "Singer")
        self.assertTrue(parsed.is_subagent)


class ActivityExecutionFeedTests(unittest.TestCase):
    """WP feed 回归：剥壳命令、parsed_cmd、折叠标题、FileChange、active_tail。"""

    @staticmethod
    def _dated(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])
        return records

    def test_command_execution_text_prefers_parsed_cmd(self) -> None:
        item = {
            "type": "CommandExecution",
            "command": [
                "D:\\Program Files\\PowerShell\\7\\pwsh.exe",
                "-Command",
                "git status --short",
            ],
            "parsed_cmd": [{"type": "unknown", "cmd": "git status --short"}],
        }

        self.assertEqual(command_execution_text(item), "git status --short")

    def test_command_execution_text_strips_full_path_shell_wrapper(self) -> None:
        # 无 parsed_cmd 时回退 command 数组：完整路径 pwsh 包装也要剥掉。
        item = {
            "type": "CommandExecution",
            "command": [
                "D:\\Program Files\\PowerShell\\7\\pwsh.exe",
                "-Command",
                "pytest -x -q",
            ],
        }

        self.assertEqual(command_execution_text(item), "pytest -x -q")

    def test_tool_command_text_extracts_cmd_from_js_glue_input(self) -> None:
        payload = {
            "name": "exec",
            "input": (
                "const r = await tools.exec_command({\n"
                "  cmd: 'hud-live-long-command',\n"
                "  workdir: 'E:\\\\demo',\n"
                "})"
            ),
        }

        self.assertEqual(tool_command_text(payload), "hud-live-long-command")

    def test_activity_steps_backfill_completion_attributes(self) -> None:
        parser = JsonlSessionParser()
        records = self._dated(
            [
                record("2026-08-29T02:00:00Z", "event_msg", {"type": "task_started"}),
                record(
                    "2026-08-29T02:00:01Z",
                    "response_item",
                    {
                        "type": "custom_tool_call",
                        "name": "exec",
                        "call_id": "call-1",
                        "input": json.dumps({"cmd": "hud-long-running"}),
                    },
                ),
                record(
                    "2026-08-29T02:00:02Z",
                    "event_msg",
                    {
                        "type": "item_completed",
                        "item": {
                            "id": "exec-1",
                            "type": "CommandExecution",
                            "command": [
                                "D:\\Program Files\\PowerShell\\7\\pwsh.exe",
                                "-Command",
                                "hud-long-running",
                            ],
                            "parsed_cmd": [
                                {"type": "unknown", "cmd": "hud-long-running"}
                            ],
                            "status": "completed",
                            "exit_code": 0,
                            "formatted_output": "line-1\r\nline-2\r\nline-3\r\nline-4\r\n",
                            "duration": {"secs": 24, "nanos": 481244700},
                        },
                    },
                ),
                record(
                    "2026-08-29T02:00:03Z",
                    "response_item",
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call-1",
                        "output": "exit code: 0",
                    },
                ),
            ]
        )

        steps = parser.activity_steps(records, task_started_index=0)

        self.assertEqual(len(steps), 1)
        step = steps[0]
        self.assertEqual(step.status, "completed")
        self.assertEqual(step.detail, "hud-long-running")
        # formatted_output 优先，且不被 custom_tool_call_output 原始串覆盖。
        self.assertEqual(step.output, "line-1 line-2 line-3 line-4")
        self.assertEqual(step.active_tail, "line-2\nline-3\nline-4")
        self.assertEqual(step.exit_code, 0)
        self.assertEqual(step.duration_text, "24s")
        self.assertEqual(step.command_raw, "hud-long-running")

    def test_active_output_tail_keeps_last_three_lines_with_newlines(self) -> None:
        text = "a\r\nb\r\nc\r\nd\r\n\r\n"

        self.assertEqual(active_output_tail(text), "b\nc\nd")
        self.assertEqual(active_output_tail(""), "")

    def test_failed_command_prefers_stderr_and_keeps_exit_code(self) -> None:
        parser = JsonlSessionParser()
        records = self._dated(
            [
                record("2026-08-29T02:00:00Z", "event_msg", {"type": "task_started"}),
                record(
                    "2026-08-29T02:00:01Z",
                    "event_msg",
                    {
                        "type": "item_completed",
                        "item": {
                            "id": "exec-2",
                            "type": "CommandExecution",
                            "command": ["pwsh.exe", "-Command", "hud-nope"],
                            "parsed_cmd": [{"cmd": "hud-nope"}],
                            "status": "failed",
                            "exit_code": 1,
                            "stdout": "",
                            "stderr": "command not found",
                        },
                    },
                ),
            ]
        )

        steps = parser.activity_steps(records, task_started_index=0)

        self.assertEqual(len(steps), 1)
        step = steps[0]
        self.assertEqual(step.title, "命令失败")
        self.assertEqual(step.status, "failed")
        self.assertEqual(step.exit_code, 1)
        self.assertEqual(step.output, "command not found")
        self.assertEqual(step.active_tail, "command not found")

    def test_reasoning_summary_titles_become_steps(self) -> None:
        parser = JsonlSessionParser()
        records = self._dated(
            [
                record("2026-08-29T02:00:00Z", "event_msg", {"type": "task_started"}),
                record(
                    "2026-08-29T02:00:01Z",
                    "event_msg",
                    {
                        "type": "item_completed",
                        "item": {
                            "type": "Reasoning",
                            "id": "rs-1",
                            "summary_text": [
                                "**读取 AGENTS.md 前 8 行**",
                                "**Planning next step**",
                            ],
                        },
                    },
                ),
            ]
        )

        steps = parser.activity_steps(records, task_started_index=0)

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].title, "思考")
        self.assertEqual(steps[0].detail, "读取 AGENTS.md 前 8 行")
        self.assertEqual(steps[0].status, "completed")

    def test_response_item_reasoning_title_is_fallback_step(self) -> None:
        parser = JsonlSessionParser()
        records = self._dated(
            [
                record("2026-08-29T02:00:00Z", "event_msg", {"type": "task_started"}),
                record(
                    "2026-08-29T02:00:01Z",
                    "response_item",
                    {
                        "type": "reasoning",
                        "summary": [
                            {"type": "summary_text", "text": "**准备补丁**"},
                        ],
                    },
                ),
            ]
        )

        steps = parser.activity_steps(records, task_started_index=0)

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].title, "思考")
        self.assertEqual(steps[0].detail, "准备补丁")

    def test_completed_reasoning_event_preferred_over_response_item(self) -> None:
        parser = JsonlSessionParser()
        records = self._dated(
            [
                record("2026-08-29T02:00:00Z", "event_msg", {"type": "task_started"}),
                record(
                    "2026-08-29T02:00:01Z",
                    "response_item",
                    {
                        "type": "reasoning",
                        "summary": [
                            {"type": "summary_text", "text": "**Raw draft**"},
                        ],
                    },
                ),
                record(
                    "2026-08-29T02:00:02Z",
                    "event_msg",
                    {
                        "type": "item_completed",
                        "item": {
                            "type": "Reasoning",
                            "id": "rs-2",
                            "summary_text": ["**Final heading**"],
                        },
                    },
                ),
            ]
        )

        steps = parser.activity_steps(records, task_started_index=0)

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].detail, "Final heading")

    def test_file_change_item_becomes_edit_step(self) -> None:
        parser = JsonlSessionParser()
        records = self._dated(
            [
                record("2026-08-29T02:00:00Z", "event_msg", {"type": "task_started"}),
                record(
                    "2026-08-29T02:00:01Z",
                    "event_msg",
                    {
                        "type": "item_completed",
                        "item": {
                            "id": "fc-1",
                            "type": "FileChange",
                            "status": "completed",
                            "changes": {
                                "E:\\Project\\demo\\tmp\\hud-observe.txt": {
                                    "type": "add",
                                    "content": "desktop-observe-ok\n",
                                    "unified_diff": "",
                                }
                            },
                        },
                    },
                ),
            ]
        )

        steps = parser.activity_steps(records, task_started_index=0)

        self.assertEqual(len(steps), 1)
        step = steps[0]
        self.assertEqual(step.title, "编辑文件")
        self.assertEqual(step.detail, "hud-observe.txt")
        self.assertEqual(step.status, "completed")
        self.assertEqual(step.output, "hud-observe.txt +1")
        self.assertEqual(step.tool_name, "apply_patch")

    def test_latest_activity_keeps_request_user_input_classification(self) -> None:
        parser = JsonlSessionParser()
        records = self._dated(
            [
                record("2026-08-29T02:00:00Z", "event_msg", {"type": "task_started"}),
                record(
                    "2026-08-29T02:00:01Z",
                    "response_item",
                    {
                        "type": "custom_tool_call",
                        "name": "request_user_input",
                        "call_id": "wait-1",
                        "input": json.dumps({"prompt": "允许执行？"}),
                    },
                ),
            ]
        )

        # 等确认分类的正源是 activity.detail 前缀（_work_status_from_snapshot）。
        activity = parser.latest_activity(records)
        self.assertEqual(activity.kind, "tool call")
        self.assertTrue(activity.detail.startswith("request_user_input"))
