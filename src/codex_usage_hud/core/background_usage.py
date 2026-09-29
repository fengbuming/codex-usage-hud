"""Local audit trail for Codex App model usage outside visible sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from contextlib import closing
import json
import re
import sqlite3
from pathlib import Path
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .calculator import UsageCalculator


BACKGROUND_USAGE_KIND = "background_usage"
DEFAULT_IMPORT_DAYS = 30
DEFAULT_GRACE_SECONDS = 8.0
UNKNOWN_FEATURE_KEY = "unknown"
UNKNOWN_FEATURE_LABEL = "未知后台任务"
BACKGROUND_USAGE_SCHEMA_VERSION = 3
REQUEST_SPLIT_REPAIR_METADATA_KEY = "request_split_repair_v1"
TITLE_DESCRIPTION_FEATURE_KEY = "title_description"
TASK_CATCH_UP_FEATURE_KEY = "task_catch_up"
TITLE_DESCRIPTION_USER_PROMPT_MARKER = "User prompt:"
DELEGATION_INPUT_RE = re.compile(
    r"<codex_delegation(?:\s[^>]*)?>.*?<input>\s*(.*?)\s*</input>",
    re.IGNORECASE | re.DOTALL,
)
VSCODE_REQUEST_RE = re.compile(
    r"(?:^|\r?\n)## My request:\s*(?P<request>.*)\Z",
    re.DOTALL,
)
RELATED_SESSION_MAX_LAG_SECONDS = 300
RELATED_SESSION_CLOCK_SKEW_SECONDS = 5

BACKGROUND_FEATURE_LABELS = {
    "memory_consolidation": "记忆整理",
    "context_suggestions": "上下文建议",
    "suggestion_safety": "建议安全检查",
    "title_description": "任务标题与描述",
    "description_refresh": "刷新任务描述",
    TASK_CATCH_UP_FEATURE_KEY: "任务回顾摘要",
    UNKNOWN_FEATURE_KEY: UNKNOWN_FEATURE_LABEL,
}

# 官方后台任务的实际英文名称。
# memory_consolidation: 官方 feature flag Feature::MemoryTool 注释
#   "Enable startup memory extraction and file-backed memory consolidation."
# context_suggestions: Codex 官方设置文档用语 "context-aware suggestions"，
#   官方内部 feature 名为 ambient_suggestions。
# suggestion_safety: 官方内部 feature 名为 ambient_suggestion_safety，官方指令
#   "Classify Codex ambient suggestion candidates for policy safety."
# title_description / description_refresh: 无公开官方界面名，按官方 prompt
#   特征（"provide a short title for a task" / "fork of an existing codex
#   thread" + "structured description field"）命名。
# task_catch_up: 无公开官方界面名，按官方 prompt 的 "brief catch-up"
#   命名；官方 Long-running work 文档将对应用户动作称为 status recap。
BACKGROUND_FEATURE_EN_LABELS = {
    "memory_consolidation": "Memory consolidation",
    "context_suggestions": "Context-aware suggestions",
    "suggestion_safety": "Ambient suggestion safety",
    "title_description": "Title & description generation",
    "description_refresh": "Description refresh",
    TASK_CATCH_UP_FEATURE_KEY: "Task catch-up",
}

# 官方定义的后台任务实际作用（中文译文）。
# 依据：Codex 官方文档（Settings 的 Memories / Suggested prompts 章节）与
# 官方源码/逆向出的官方指令原文。
BACKGROUND_FEATURE_PURPOSES = {
    "memory_consolidation": (
        "在启动时提取记忆并落盘整合，让 ChatGPT 把过往对话中有用的上下文"
        "带入未来的工作"
    ),
    "context_suggestions": (
        "在你开始使用或回到 ChatGPT 时，浮现你可能想要继续的后续任务"
        "与建议"
    ),
    "suggestion_safety": (
        "对 Codex 环境建议（ambient suggestions）候选进行政策安全分类，"
        "过滤不符合安全与合规标准的建议"
    ),
    "title_description": (
        "为任务生成一个简短标题，并写入结构化描述字段"
    ),
    "description_refresh": (
        "在分叉已有 Codex 线程后，重新生成并刷新该任务的结构化描述"
    ),
    TASK_CATCH_UP_FEATURE_KEY: (
        "在用户返回任务时，根据已有会话生成简短进度摘要和可选下一步，"
        "帮助恢复长任务上下文"
    ),
}

_PROMPT_MARKER = 'Text { text: '
_MODEL_RE = re.compile(r"\bmodel=([^\s}:]+)")
_TOTAL_TOKENS_RE = re.compile(r"\btotal_usage_tokens=(\d+)")
_ESTIMATED_TOKENS_RE = re.compile(
    r"\bestimated_token_count=(?:Some\()?([0-9]+)"
)
_CWD_RE = re.compile(r"\bcwd=(.*?)\}:try_run_sampling_request")
_ENDPOINT_RE = re.compile(r'\bendpoint="([^"]+)"')
_API_PATH_RE = re.compile(r'\bapi\.path="?([^"\s}]+)')
_PROCESS_PID_RE = re.compile(r"^pid:(\d+):")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True)
class BackgroundFeature:
    key: str
    label: str
    attribution: str = "feature_signature"


@dataclass(frozen=True)
class BackgroundRequestEvidence:
    source_log_id: int
    occurred_at: int
    model: str
    total_tokens: int
    # None means Codex omitted the input estimate for this row.
    estimated_input_tokens: int | None


@dataclass(frozen=True)
class BackgroundScanResult:
    processed_rows: int = 0
    source_cursor: int = 0
    content_changed: bool = False
    pending_deadline: float | None = None
    diagnostics: tuple[str, ...] = ()


def background_feature_label(key: object, fallback: object = "") -> str:
    """Return the stable Chinese label for a stored feature key."""
    normalized = str(key or "").strip()
    if normalized in BACKGROUND_FEATURE_LABELS:
        return BACKGROUND_FEATURE_LABELS[normalized]
    return str(fallback or normalized or UNKNOWN_FEATURE_LABEL).strip() or UNKNOWN_FEATURE_LABEL


def background_feature_en_label(key: object) -> str:
    """Return the official English name for a stored feature key."""
    normalized = str(key or "").strip()
    return BACKGROUND_FEATURE_EN_LABELS.get(normalized, "")


def background_feature_purpose(key: object) -> str:
    """Return the official-purpose Chinese description for a feature key."""
    normalized = str(key or "").strip()
    return BACKGROUND_FEATURE_PURPOSES.get(normalized, "")


def classify_background_feature(prompt: str) -> BackgroundFeature:
    """Classify one internal prompt without exposing prompt parsing to the UI."""
    value = str(prompt or "")
    lowered = value.casefold()
    if "memory writing agent: phase 2" in lowered and "consolidation" in lowered:
        return BackgroundFeature("memory_consolidation", background_feature_label("memory_consolidation"))
    if "generate 0 to 3 hyperpersonalized suggestions" in lowered:
        return BackgroundFeature("context_suggestions", background_feature_label("context_suggestions"))
    if (
        "safety and compliance standards for codex ambient suggestions" in lowered
        and "ambient suggestion candidates" in lowered
    ):
        return BackgroundFeature("suggestion_safety", background_feature_label("suggestion_safety"))
    if (
        "provide a short title for a task" in lowered
        and "structured description field" in lowered
    ):
        return BackgroundFeature("title_description", background_feature_label("title_description"))
    if (
        lowered.lstrip().startswith("generate a concise, single-line task title")
        and "do not answer the request" in lowered
    ):
        return BackgroundFeature("title_description", background_feature_label("title_description"))
    if (
        "fork of an existing codex thread" in lowered
        and "structured description field" in lowered
    ):
        return BackgroundFeature("description_refresh", background_feature_label("description_refresh"))
    if (
        lowered.lstrip().startswith("write a brief catch-up for a user returning to this")
        and (
            "summary and nullable next_action" in lowered
            or "explain the objective, what was completed or learned, and the next step or blocker" in lowered
        )
    ):
        return BackgroundFeature(
            TASK_CATCH_UP_FEATURE_KEY,
            background_feature_label(TASK_CATCH_UP_FEATURE_KEY),
        )
    return BackgroundFeature(UNKNOWN_FEATURE_KEY, UNKNOWN_FEATURE_LABEL, "")


def decode_submission_prompt(body: object) -> str:
    """Decode the first Rust-debug Text value using JSON string semantics."""
    text = str(body or "")
    marker_at = text.find(_PROMPT_MARKER)
    if marker_at < 0:
        return ""
    value_at = marker_at + len(_PROMPT_MARKER)
    try:
        value, _end = json.JSONDecoder().raw_decode(text[value_at:])
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    return value if isinstance(value, str) else ""


def title_description_source_prompt(prompt: object) -> str:
    """Return the exact user prompt embedded in a title-generation request."""
    _prefix, marker, value = str(prompt or "").rpartition(
        TITLE_DESCRIPTION_USER_PROMPT_MARKER
    )
    return value.strip() if marker else ""


def visible_session_source_prompt(prompt: object) -> str:
    """Unwrap the prompt wrappers used by visible Codex sessions."""
    text = str(prompt or "").strip()
    match = DELEGATION_INPUT_RE.search(text)
    if match:
        text = match.group(1).strip()
    match = VSCODE_REQUEST_RE.search(text)
    return match.group("request").strip() if match else text


def decode_request_evidence(
    source_log_id: object,
    occurred_at: object,
    body: object,
) -> BackgroundRequestEvidence | None:
    """Project one completed sampling log row into stable request counters."""
    text = str(body or "")
    total_match = _TOTAL_TOKENS_RE.search(text)
    if total_match is None:
        return None
    try:
        log_id = int(source_log_id)
        timestamp = int(occurred_at)
        total_tokens = max(0, int(total_match.group(1)))
    except (TypeError, ValueError, OverflowError):
        return None
    estimated_match = _ESTIMATED_TOKENS_RE.search(text)
    estimated_input: int | None
    if estimated_match is None:
        estimated_input = None
    else:
        try:
            estimated_input = max(0, min(int(estimated_match.group(1)), total_tokens))
        except (TypeError, ValueError, OverflowError):
            estimated_input = None
    model_match = _MODEL_RE.search(text)
    model = str(model_match.group(1) if model_match else "").strip()
    return BackgroundRequestEvidence(
        source_log_id=log_id,
        occurred_at=timestamp,
        model=model,
        total_tokens=total_tokens,
        estimated_input_tokens=estimated_input,
    )


def resolve_request_token_split(
    *,
    total_tokens: int,
    estimated_input_tokens: int | None,
    previous_total_tokens: int = 0,
    previous_input_tokens: int = 0,
) -> tuple[int, int, int]:
    """Return (input, cached_input, output) for one background request.

    Newer Codex builds sometimes omit ``estimated_token_count`` from the
    ``post sampling token usage`` row. When that happens, bill the first request
    as input-only and treat later growth against the previous total as output so
    the entire context is not charged at the output price.
    """
    total = max(0, int(total_tokens or 0))
    previous_total = max(0, int(previous_total_tokens or 0))
    previous_input = max(0, int(previous_input_tokens or 0))
    if estimated_input_tokens is None:
        if previous_total > 0:
            estimated_input = min(previous_total, total)
        else:
            estimated_input = total
    else:
        estimated_input = max(0, min(int(estimated_input_tokens), total))
    cached_input = min(previous_input, estimated_input)
    output_tokens = max(0, total - estimated_input)
    return estimated_input, cached_input, output_tokens


def decode_request_context(body: object) -> tuple[str, str, str]:
    """Return model, cwd and endpoint from one request-start feedback row."""
    text = str(body or "")
    model_match = _MODEL_RE.search(text)
    cwd_match = _CWD_RE.search(text)
    endpoint_match = _ENDPOINT_RE.search(text) or _API_PATH_RE.search(text)
    model = str(model_match.group(1) if model_match else "").strip()
    cwd = str(cwd_match.group(1) if cwd_match else "").strip().strip('"')
    endpoint = str(endpoint_match.group(1) if endpoint_match else "").strip()
    if endpoint and not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    return model, cwd, endpoint


def valid_background_event_id(value: object) -> str:
    """Normalize an event identifier accepted by the local query bridge."""
    event_id = str(value or "").strip()
    return event_id if _UUID_RE.fullmatch(event_id) else ""


def _read_only_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro",
        uri=True,
        timeout=0.25,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 250")
    return connection


def _timestamp_iso(value: object) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    try:
        return datetime.fromtimestamp(timestamp).astimezone().isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def _workdir_available(value: object) -> bool:
    """Return whether a logged CWD is currently an existing directory."""
    raw_path = str(value or "").strip()
    if not raw_path:
        return False
    try:
        return Path(raw_path).is_dir()
    except (OSError, ValueError):
        return False


def _today_start_timestamp(now: datetime | None = None) -> int:
    current = (now or datetime.now().astimezone()).astimezone()
    return int(current.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _range_start_timestamp(range_key: str, now: datetime | None = None) -> int:
    current = (now or datetime.now().astimezone()).astimezone()
    today = current.replace(hour=0, minute=0, second=0, microsecond=0)
    key = str(range_key or "today").strip().lower()
    if key == "all":
        return 0
    days = {"today": 1, "7d": 7, "30d": 30}.get(key, 1)
    return int((today - timedelta(days=days - 1)).timestamp())


def _range_key_for_timestamp(value: object, now: datetime | None = None) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError, OverflowError):
        return "today"
    for key in ("today", "7d", "30d"):
        if timestamp >= _range_start_timestamp(key, now):
            return key
    return "all"


def _epoch_seconds(value: object) -> int:
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        text = str(value or "").strip()
        if not text:
            return 0
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return 0
        return int(parsed.timestamp())
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return max(0, int(timestamp))


def _snapshot_metadata(
    snapshot: Mapping[str, object] | None,
) -> tuple[str, str]:
    if snapshot is None:
        return "unavailable", ""
    status = str(snapshot.get("status") or "").strip().lower()
    if status not in {"versioned", "fallback", "unavailable"}:
        status = "fallback" if snapshot.get("prices") else "unavailable"
    version_id = str(snapshot.get("version_id") or "").strip()
    return status, version_id


def _snapshot_json(snapshot: Mapping[str, object] | None) -> str:
    return (
        json.dumps(dict(snapshot), ensure_ascii=False, sort_keys=True)
        if snapshot is not None
        else ""
    )


def _normalized_snapshot(
    snapshot: Mapping[str, object] | None,
) -> dict[str, object] | None:
    if snapshot is None:
        return None
    normalized = dict(snapshot)
    status, version_id = _snapshot_metadata(normalized)
    normalized["status"] = status
    normalized["version_id"] = version_id or None
    return normalized


class BackgroundUsageStore:
    """HUD-owned SQLite history queried by the overlay and settings bridge."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=2.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 2000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scan_state (
                    source_key TEXT PRIMARY KEY,
                    last_log_id INTEGER NOT NULL,
                    initialized_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS process_evidence (
                    process_uuid TEXT PRIMARY KEY,
                    app_evidence TEXT NOT NULL,
                    last_seen_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS background_events (
                    event_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL UNIQUE,
                    process_uuid TEXT NOT NULL DEFAULT '',
                    feature_key TEXT NOT NULL DEFAULT 'unknown',
                    feature_label TEXT NOT NULL DEFAULT '未知后台任务',
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
                    cost_available INTEGER NOT NULL DEFAULT 0,
                    related_session_id TEXT NOT NULL DEFAULT '',
                    association_kind TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS background_requests (
                    request_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    source_log_id INTEGER NOT NULL UNIQUE,
                    occurred_at INTEGER NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    endpoint TEXT NOT NULL DEFAULT '',
                    total_tokens INTEGER NOT NULL,
                    estimated_input_tokens INTEGER NOT NULL,
                    estimated_cached_tokens INTEGER NOT NULL,
                    estimated_output_tokens INTEGER NOT NULL,
                    estimated_cost_usd REAL,
                    price_snapshot_json TEXT NOT NULL DEFAULT '',
                    price_status TEXT NOT NULL DEFAULT 'unavailable',
                    price_version_id TEXT NOT NULL DEFAULT '',
                    last_recalculated_at INTEGER,
                    last_recalculation_id TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(event_id) REFERENCES background_events(event_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS background_recalculations (
                    recalculation_id TEXT PRIMARY KEY,
                    recalculated_at INTEGER NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    start_at INTEGER,
                    end_at INTEGER,
                    request_count INTEGER NOT NULL,
                    changed_count INTEGER NOT NULL,
                    before_total_usd REAL NOT NULL,
                    after_total_usd REAL NOT NULL,
                    delta_usd REAL NOT NULL,
                    before_unavailable_count INTEGER NOT NULL,
                    after_unavailable_count INTEGER NOT NULL,
                    scope_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS background_recalculation_items (
                    recalculation_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    old_cost_usd REAL,
                    new_cost_usd REAL,
                    old_price_snapshot_json TEXT NOT NULL DEFAULT '',
                    new_price_snapshot_json TEXT NOT NULL DEFAULT '',
                    old_price_status TEXT NOT NULL DEFAULT 'unavailable',
                    new_price_status TEXT NOT NULL DEFAULT 'unavailable',
                    old_price_version_id TEXT NOT NULL DEFAULT '',
                    new_price_version_id TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(recalculation_id, request_id),
                    FOREIGN KEY(recalculation_id)
                        REFERENCES background_recalculations(recalculation_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_background_events_last_seen
                    ON background_events(last_seen_at DESC);
                CREATE INDEX IF NOT EXISTS idx_background_events_feature
                    ON background_events(feature_key, last_seen_at DESC);
                CREATE INDEX IF NOT EXISTS idx_background_requests_event
                    ON background_requests(event_id, source_log_id);
                CREATE INDEX IF NOT EXISTS idx_background_requests_model
                    ON background_requests(model, occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_background_recalculations_time
                    ON background_recalculations(recalculated_at DESC);
                INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', '3');
                INSERT OR IGNORE INTO metadata(key, value) VALUES('revision', '0');
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(background_events)")
            }
            if "related_session_id" not in columns:
                connection.execute(
                    "ALTER TABLE background_events "
                    "ADD COLUMN related_session_id TEXT NOT NULL DEFAULT ''"
                )
            if "association_kind" not in columns:
                connection.execute(
                    "ALTER TABLE background_events "
                    "ADD COLUMN association_kind TEXT NOT NULL DEFAULT ''"
                )
            request_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(background_requests)")
            }
            if "price_status" not in request_columns:
                connection.execute(
                    "ALTER TABLE background_requests "
                    "ADD COLUMN price_status TEXT NOT NULL DEFAULT 'unavailable'"
                )
            if "price_version_id" not in request_columns:
                connection.execute(
                    "ALTER TABLE background_requests "
                    "ADD COLUMN price_version_id TEXT NOT NULL DEFAULT ''"
                )
            if "last_recalculated_at" not in request_columns:
                connection.execute(
                    "ALTER TABLE background_requests ADD COLUMN last_recalculated_at INTEGER"
                )
            if "last_recalculation_id" not in request_columns:
                connection.execute(
                    "ALTER TABLE background_requests "
                    "ADD COLUMN last_recalculation_id TEXT NOT NULL DEFAULT ''"
                )
            self._backfill_price_metadata(connection)
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_background_events_related_session
                    ON background_events(related_session_id, confirmed_at, last_seen_at DESC)
                """
            )
            connection.execute(
                "UPDATE metadata SET value=? WHERE key='schema_version'",
                (str(BACKGROUND_USAGE_SCHEMA_VERSION),),
            )

    @staticmethod
    def _backfill_price_metadata(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT request_id, estimated_cost_usd, price_snapshot_json,
                   price_status, price_version_id
            FROM background_requests
            WHERE price_status NOT IN ('versioned', 'fallback', 'unavailable')
               OR (price_status='unavailable' AND price_snapshot_json<>'')
               OR (price_version_id='' AND price_snapshot_json<>'')
            """
        ).fetchall()
        for row in rows:
            snapshot: Mapping[str, object] | None = None
            try:
                decoded = json.loads(str(row["price_snapshot_json"] or ""))
                if isinstance(decoded, dict):
                    snapshot = decoded
            except json.JSONDecodeError:
                pass
            status, version_id = _snapshot_metadata(snapshot)
            if row["estimated_cost_usd"] is None:
                status = "unavailable"
            connection.execute(
                """
                UPDATE background_requests
                SET price_status=?, price_version_id=?
                WHERE request_id=?
                """,
                (status, version_id, str(row["request_id"])),
            )

    def revision(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key='revision'"
            ).fetchone()
        try:
            return int(row[0]) if row is not None else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _bump_revision(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES('revision', '1')
            ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1
            """
        )

    def confirm(
        self,
        event_id: object,
        *,
        now: datetime | None = None,
    ) -> bool:
        normalized = valid_background_event_id(event_id)
        if not normalized:
            return False
        confirmed_at = int((now or datetime.now().astimezone()).timestamp())
        with closing(self._connect()) as connection, connection:
            event = connection.execute(
                "SELECT related_session_id FROM background_events WHERE event_id=?",
                (normalized,),
            ).fetchone()
            related_session_id = str(event[0] or "").strip() if event else ""
            if related_session_id:
                cursor = connection.execute(
                    """
                    UPDATE background_events SET confirmed_at=?
                    WHERE related_session_id=?
                      AND classification_state='background'
                      AND confirmed_at IS NULL
                    """,
                    (confirmed_at, related_session_id),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE background_events SET confirmed_at=?
                    WHERE event_id=? AND classification_state='background'
                      AND confirmed_at IS NULL
                    """,
                    (confirmed_at, normalized),
                )
            changed = cursor.rowcount > 0
            if changed:
                self._bump_revision(connection)
        return changed

    def pending_today(
        self,
        *,
        now: datetime | None = None,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        today_start = _today_start_timestamp(now)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT e.*,
                       (SELECT group_concat(DISTINCT r.model)
                          FROM background_requests r
                         WHERE r.event_id=e.event_id AND r.model<>'') AS models
                FROM background_events e
                WHERE e.classification_state='background'
                  AND e.confirmed_at IS NULL
                  AND e.related_session_id=''
                  AND e.last_seen_at>=?
                  AND e.request_count>0
                ORDER BY e.last_seen_at DESC
                LIMIT ?
                """,
                (today_start, max(1, min(int(limit), 100))),
            ).fetchall()
        return [self._event_summary(row, today_start=today_start) for row in rows]

    def notification_index(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, dict[str, object]]:
        """Return unread related-event summaries keyed by canonical session ID."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT event_id, related_session_id, last_seen_at
                FROM background_events
                WHERE classification_state='background'
                  AND confirmed_at IS NULL
                  AND related_session_id<>''
                  AND request_count>0
                ORDER BY last_seen_at DESC, event_id
                """
            ).fetchall()
        index: dict[str, dict[str, object]] = {}
        for row in rows:
            session_id = str(row["related_session_id"] or "").strip()
            event_id = valid_background_event_id(row["event_id"])
            if not session_id or not event_id:
                continue
            timestamp = int(row["last_seen_at"] or 0)
            summary = index.setdefault(
                session_id,
                {
                    "count": 0,
                    "eventId": event_id,
                    "oldestSeenAt": timestamp,
                },
            )
            summary["count"] = int(summary["count"] or 0) + 1
            summary["oldestSeenAt"] = min(
                int(summary["oldestSeenAt"] or timestamp), timestamp
            )
        for summary in index.values():
            summary["range"] = _range_key_for_timestamp(
                summary.pop("oldestSeenAt", 0), now
            )
        return index

    def range_for_event(
        self,
        event_id: object,
        *,
        now: datetime | None = None,
    ) -> str:
        """Return the smallest available range covering the selected reminder."""
        normalized = valid_background_event_id(event_id)
        if not normalized:
            return "today"
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT related_session_id, last_seen_at
                FROM background_events
                WHERE event_id=? AND classification_state='background'
                """,
                (normalized,),
            ).fetchone()
            if row is None:
                return "today"
            timestamp = int(row["last_seen_at"] or 0)
            related_session_id = str(row["related_session_id"] or "").strip()
            if related_session_id:
                oldest = connection.execute(
                    """
                    SELECT MIN(last_seen_at)
                    FROM background_events
                    WHERE classification_state='background'
                      AND confirmed_at IS NULL
                      AND request_count>0
                      AND related_session_id=?
                    """,
                    (related_session_id,),
                ).fetchone()
                if oldest is not None and oldest[0] is not None:
                    timestamp = int(oldest[0])
        return _range_key_for_timestamp(timestamp, now)

    def query(
        self,
        *,
        range_key: str = "today",
        feature: str = "",
        model: str = "",
        event_id: str = "",
        now: datetime | None = None,
        limit: int = 200,
    ) -> dict[str, object]:
        range_start = _range_start_timestamp(range_key, now)
        today_start = _today_start_timestamp(now)
        feature_key = str(feature or "").strip()
        model_name = str(model or "").strip()
        requested_event_id = valid_background_event_id(event_id)
        clauses = ["e.classification_state='background'", "e.last_seen_at>=?"]
        params: list[object] = [range_start]
        if feature_key:
            clauses.append("e.feature_key=?")
            params.append(feature_key)
        if model_name:
            clauses.append(
                "EXISTS(SELECT 1 FROM background_requests rf "
                "WHERE rf.event_id=e.event_id AND rf.model=?)"
            )
            params.append(model_name)
        where_sql = " AND ".join(clauses)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT e.*,
                       (SELECT group_concat(DISTINCT r.model)
                          FROM background_requests r
                         WHERE r.event_id=e.event_id AND r.model<>'') AS models
                FROM background_events e
                WHERE {where_sql}
                ORDER BY e.last_seen_at DESC
                LIMIT ?
                """,
                (*params, max(1, min(int(limit), 500))),
            ).fetchall()
            aggregate = connection.execute(
                f"""
                SELECT COUNT(*) AS event_count,
                       COALESCE(SUM(e.request_count), 0) AS request_count,
                       COALESCE(SUM(e.total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(e.estimated_cost_usd), 0) AS estimated_cost,
                       COALESCE(SUM(CASE WHEN e.cost_available=0 THEN 1 ELSE 0 END), 0)
                           AS unavailable_costs
                FROM background_events e
                WHERE {where_sql}
                """,
                tuple(params),
            ).fetchone()
            feature_rows = connection.execute(
                """
                SELECT DISTINCT feature_key, feature_label
                FROM background_events
                WHERE classification_state='background'
                ORDER BY feature_label
                """
            ).fetchall()
            model_rows = connection.execute(
                """
                SELECT DISTINCT r.model
                FROM background_requests r
                JOIN background_events e ON e.event_id=r.event_id
                WHERE e.classification_state='background' AND r.model<>''
                ORDER BY r.model
                """
            ).fetchall()
        events = [self._event_summary(row, today_start=today_start) for row in rows]
        selected = requested_event_id
        if selected and not any(item["eventId"] == selected for item in events):
            selected = ""
        if not selected and events:
            selected = str(events[0]["eventId"])
        models_used = sorted(
            {
                model_value
                for item in events
                for model_value in item.get("models", [])
                if isinstance(model_value, str) and model_value
            }
        )
        event_count = int(aggregate["event_count"] or 0)
        unavailable_costs = int(aggregate["unavailable_costs"] or 0)
        estimated_cost: float | None = float(aggregate["estimated_cost"] or 0.0)
        if event_count > 0 and unavailable_costs >= event_count:
            estimated_cost = None
        feature_options = {
            str(row[0]): background_feature_label(row[0], row[1])
            for row in feature_rows
        }
        return {
            "revision": self.revision(),
            "range": str(range_key or "today"),
            "summary": {
                "eventCount": event_count,
                "requestCount": int(aggregate["request_count"] or 0),
                "totalTokens": int(aggregate["total_tokens"] or 0),
                "estimatedCostUsd": estimated_cost,
                "costComplete": unavailable_costs == 0,
                "models": models_used,
                "costSource": "estimate",
                "tokensSource": "local_log",
            },
            "events": events,
            "selectedEventId": selected,
            "filters": {
                "features": sorted(
                    [
                        {
                            "key": key,
                            "label": label,
                        }
                        for key, label in feature_options.items()
                    ],
                    key=lambda item: str(item["label"]).casefold(),
                ),
                "models": [str(row[0]) for row in model_rows],
            },
        }

    def detail(self, event_id: object) -> dict[str, object] | None:
        normalized = valid_background_event_id(event_id)
        if not normalized:
            return None
        with closing(self._connect()) as connection:
            event = connection.execute(
                """
                SELECT e.*,
                       (SELECT group_concat(DISTINCT r.model)
                          FROM background_requests r
                         WHERE r.event_id=e.event_id AND r.model<>'') AS models
                FROM background_events e
                WHERE e.event_id=? AND e.classification_state='background'
                """,
                (normalized,),
            ).fetchone()
            if event is None:
                return None
            requests = connection.execute(
                """
                SELECT * FROM background_requests
                WHERE event_id=? ORDER BY source_log_id
                """,
                (normalized,),
            ).fetchall()
        payload = self._event_summary(event, today_start=_today_start_timestamp())
        payload["prompt"] = str(event["prompt"] or "")
        payload["requests"] = [self._request_detail(row) for row in requests]
        payload["costSource"] = "estimate"
        payload["tokensSource"] = "local_log"
        return payload

    @staticmethod
    def _event_summary(
        row: sqlite3.Row,
        *,
        today_start: int,
    ) -> dict[str, object]:
        models = [
            item.strip()
            for item in str(row["models"] or "").split(",")
            if item.strip()
        ]
        confirmed_at = row["confirmed_at"]
        if confirmed_at is not None:
            status = "confirmed"
        elif int(row["last_seen_at"] or 0) >= today_start:
            status = "pending"
        else:
            status = "history"
        cost_available = bool(row["cost_available"])
        cwd = str(row["cwd"] or "").strip()
        association_kind = str(row["association_kind"] or "").strip()
        workdir_association = (
            "verified_session"
            if association_kind == "exact_first_user_message"
            else "log_observed"
            if cwd
            else ""
        )
        return {
            "eventId": str(row["event_id"]),
            "threadId": str(row["thread_id"]),
            "processUuid": str(row["process_uuid"] or ""),
            "featureKey": str(row["feature_key"] or UNKNOWN_FEATURE_KEY),
            "featureLabel": background_feature_label(
                row["feature_key"], row["feature_label"]
            ),
            "featureEnLabel": background_feature_en_label(row["feature_key"]),
            "featurePurpose": background_feature_purpose(row["feature_key"]),
            "cwd": cwd,
            "workdirAvailable": _workdir_available(cwd),
            "workdirAssociation": workdir_association,
            "endpoint": str(row["endpoint"] or ""),
            "provider": str(row["provider"] or ""),
            "firstSeenAt": _timestamp_iso(row["first_seen_at"]),
            "lastSeenAt": _timestamp_iso(row["last_seen_at"]),
            "confirmedAt": _timestamp_iso(confirmed_at),
            "unread": confirmed_at is None,
            "status": status,
            "appAttribution": str(row["app_attribution"] or ""),
            "requestCount": int(row["request_count"] or 0),
            "totalTokens": int(row["total_tokens"] or 0),
            "estimatedCostUsd": (
                float(row["estimated_cost_usd"] or 0.0)
                if cost_available
                else None
            ),
            "costAvailable": cost_available,
            "costSource": "estimate",
            "tokensSource": "local_log",
            "models": models,
        }

    @staticmethod
    def _request_detail(row: sqlite3.Row) -> dict[str, object]:
        price_snapshot: dict[str, object] | None = None
        try:
            decoded = json.loads(str(row["price_snapshot_json"] or ""))
            if isinstance(decoded, dict):
                price_snapshot = decoded
        except json.JSONDecodeError:
            price_snapshot = None
        return {
            "requestId": str(row["request_id"]),
            "sourceLogId": int(row["source_log_id"]),
            "occurredAt": _timestamp_iso(row["occurred_at"]),
            "model": str(row["model"] or ""),
            "endpoint": str(row["endpoint"] or ""),
            "totalTokens": int(row["total_tokens"] or 0),
            "estimatedInputTokens": int(row["estimated_input_tokens"] or 0),
            "estimatedCachedTokens": int(row["estimated_cached_tokens"] or 0),
            "estimatedOutputTokens": int(row["estimated_output_tokens"] or 0),
            "estimatedCostUsd": (
                float(row["estimated_cost_usd"])
                if row["estimated_cost_usd"] is not None
                else None
            ),
            "priceSnapshot": price_snapshot,
            "priceStatus": str(row["price_status"] or "unavailable"),
            "priceVersionId": str(row["price_version_id"] or ""),
            "costSource": "estimate",
            "tokensSource": "local_log",
        }


class BackgroundUsageScanner:
    """Incrementally project Codex log rows into the HUD audit store."""

    def __init__(
        self,
        *,
        logs_path: str | Path,
        state_path: str | Path,
        store: BackgroundUsageStore,
        provider: str = "",
        price_table: Mapping[str, Mapping[str, Any]] | None = None,
        pricing_versions: Iterable[Any] | None = None,
        import_days: int = DEFAULT_IMPORT_DAYS,
        grace_seconds: float = DEFAULT_GRACE_SECONDS,
        app_process_ids: Iterable[int] = (),
        now: Callable[[], float] | None = None,
    ) -> None:
        self.logs_path = Path(logs_path)
        self.state_path = Path(state_path)
        self.store = store
        self.provider = str(provider or "").strip().lower()
        self.import_days = max(1, int(import_days))
        self.grace_seconds = max(0.0, float(grace_seconds))
        self.app_process_ids = {
            int(value) for value in app_process_ids if int(value) > 0
        }
        self._clock = now or time.time
        self._price_table = (
            None
            if price_table is None
            else {str(name): dict(prices) for name, prices in price_table.items()}
        )
        self._calculator = UsageCalculator(
            self._price_table,
            pricing_versions=pricing_versions,
        )

    def reconfigure(
        self,
        *,
        provider: str,
        price_table: Mapping[str, Mapping[str, Any]],
        pricing_versions: Iterable[Any] | None = None,
        app_process_ids: Iterable[int] = (),
    ) -> None:
        """Apply pricing/process evidence for requests discovered after a settings change."""
        self.provider = str(provider or "").strip().lower()
        self.app_process_ids = {
            int(value) for value in app_process_ids if int(value) > 0
        }
        self._price_table = {
            str(name): dict(prices) for name, prices in price_table.items()
        }
        self._calculator = UsageCalculator(
            self._price_table,
            pricing_versions=pricing_versions,
        )


    def scan(self) -> BackgroundScanResult:
        diagnostics: list[str] = []
        if not self.logs_path.is_file():
            return BackgroundScanResult(diagnostics=("logs_database_missing",))
        if not self.state_path.is_file():
            return BackgroundScanResult(diagnostics=("state_database_missing",))
        try:
            (
                visible,
                child_threads,
                app_visible,
                related_session_candidates,
            ) = self._state_evidence()
        except (OSError, sqlite3.Error):
            return BackgroundScanResult(diagnostics=("state_database_unavailable",))
        now_ts = int(float(self._clock()))
        processed = 0
        content_changed = False
        source_cursor = 0
        source_key = str(self.logs_path.resolve())
        try:
            with (
                closing(_read_only_connection(self.logs_path)) as source,
                closing(self.store._connect()) as target,
                target,
            ):
                max_row = source.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()
                max_log_id = int(max_row[0] if max_row is not None else 0)
                cursor_row = target.execute(
                    "SELECT last_log_id FROM scan_state WHERE source_key=?",
                    (source_key,),
                ).fetchone()
                cursor = int(cursor_row[0]) if cursor_row is not None else -1
                if cursor < 0 or max_log_id < cursor:
                    cutoff = now_ts - (self.import_days * 86400)
                    first_row = source.execute(
                        """
                        SELECT id FROM logs INDEXED BY idx_logs_ts
                        WHERE ts>=?
                        ORDER BY ts ASC, ts_nanos ASC, id ASC LIMIT 1
                        """,
                        (cutoff,),
                    ).fetchone()
                    cursor = (
                        max(0, int(first_row[0]) - 1)
                        if first_row is not None
                        else max_log_id
                    )
                source_cursor = max_log_id
                rows = source.execute(
                    """
                    SELECT id, ts, target, module_path, feedback_log_body,
                           thread_id, process_uuid
                    FROM logs
                    WHERE id>? AND id<=? AND thread_id IS NOT NULL
                      AND (
                        target='codex_core::session::handlers'
                        OR target='codex_core::session::turn'
                        OR (target='feedback_tags' AND module_path='codex_feedback')
                      )
                    ORDER BY id
                    """,
                    (cursor, max_log_id),
                )
                for row in rows:
                    processed += 1
                    thread_id = str(row["thread_id"] or "").strip()
                    process_uuid = str(row["process_uuid"] or "").strip()
                    try:
                        timestamp = int(row["ts"])
                    except (TypeError, ValueError, OverflowError):
                        if "malformed_log_row" not in diagnostics:
                            diagnostics.append("malformed_log_row")
                        continue
                    if thread_id in app_visible and process_uuid:
                        self._record_process_evidence(
                            target,
                            process_uuid,
                            "visible_app_thread",
                            timestamp,
                        )
                    if thread_id in visible or thread_id in child_threads:
                        continue
                    if str(row["target"] or "") == "codex_core::session::handlers":
                        prompt = decode_submission_prompt(row["feedback_log_body"])
                        if prompt:
                            feature = classify_background_feature(prompt)
                            content_changed |= self._upsert_event(
                                target,
                                thread_id=thread_id,
                                process_uuid=process_uuid,
                                timestamp=timestamp,
                                prompt=prompt,
                                feature=feature,
                            )
                        continue
                    if str(row["target"] or "") == "feedback_tags":
                        model, cwd, endpoint = decode_request_context(
                            row["feedback_log_body"]
                        )
                        if model or cwd or endpoint:
                            content_changed |= self._upsert_event(
                                target,
                                thread_id=thread_id,
                                process_uuid=process_uuid,
                                timestamp=timestamp,
                            )
                            content_changed |= self._update_event_context(
                                target,
                                thread_id,
                                cwd=cwd,
                                endpoint=endpoint,
                            )
                        continue
                    request = decode_request_evidence(
                        row["id"],
                        row["ts"],
                        row["feedback_log_body"],
                    )
                    if request is None:
                        continue
                    content_changed |= self._upsert_event(
                        target,
                        thread_id=thread_id,
                        process_uuid=process_uuid,
                        timestamp=timestamp,
                    )
                    content_changed |= self._insert_request(
                        target,
                        event_id=thread_id,
                        request=request,
                    )
                content_changed |= self._repair_missing_input_splits_if_needed(target)
                content_changed |= self._reclassify(
                    target,
                    visible=visible,
                    child_threads=child_threads,
                    related_session_candidates=related_session_candidates,
                    now_ts=now_ts,
                )
                target.execute(
                    """
                    INSERT INTO scan_state(source_key, last_log_id, initialized_at, updated_at)
                    VALUES(?, ?, ?, ?)
                    ON CONFLICT(source_key) DO UPDATE SET
                        last_log_id=excluded.last_log_id,
                        updated_at=excluded.updated_at
                    """,
                    (source_key, max_log_id, now_ts, now_ts),
                )
                if content_changed:
                    self.store._bump_revision(target)
        except sqlite3.OperationalError as exc:
            diagnostics.append(f"logs_database_unavailable:{type(exc).__name__}")
        except (OSError, sqlite3.DatabaseError) as exc:
            diagnostics.append(f"logs_database_invalid:{type(exc).__name__}")
        pending_deadline = self._pending_deadline()
        return BackgroundScanResult(
            processed_rows=processed,
            source_cursor=source_cursor,
            content_changed=content_changed,
            pending_deadline=pending_deadline,
            diagnostics=tuple(diagnostics),
        )

    def _state_evidence(
        self,
    ) -> tuple[
        dict[str, str],
        set[str],
        set[str],
        dict[str, list[tuple[str, int]]],
    ]:
        if not self.state_path.is_file():
            return {}, set(), set(), {}
        with closing(_read_only_connection(self.state_path)) as connection:
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(threads)")
            }
            if "source" in columns:
                rows = connection.execute("SELECT id, source FROM threads").fetchall()
                visible = {str(row[0]): str(row[1] or "") for row in rows}
            else:
                visible = {
                    str(row[0]): "" for row in connection.execute("SELECT id FROM threads")
                }
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            child_threads = (
                {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT child_thread_id FROM thread_spawn_edges"
                    )
                    if row[0]
                }
                if "thread_spawn_edges" in tables
                else set()
            )
            related_session_candidates: dict[str, list[tuple[str, int]]] = {}
            if "first_user_message" in columns:
                created_column = (
                    "created_at"
                    if "created_at" in columns
                    else "created_at_ms"
                    if "created_at_ms" in columns
                    else ""
                )
                created_select = created_column or "0"
                for row in connection.execute(
                    f"SELECT id, first_user_message, {created_select} FROM threads"
                ):
                    session_id = str(row[0] or "").strip()
                    first_user_message = visible_session_source_prompt(row[1])
                    created_at = _epoch_seconds(row[2])
                    if (
                        not _UUID_RE.fullmatch(session_id)
                        or not first_user_message
                        or created_at <= 0
                        or session_id in child_threads
                    ):
                        continue
                    related_session_candidates.setdefault(
                        first_user_message, []
                    ).append((session_id, created_at))
        app_visible = {
            thread_id
            for thread_id, source in visible.items()
            if str(source or "").strip().lower() == "vscode"
        }
        return visible, child_threads, app_visible, related_session_candidates

    @staticmethod
    def _related_session_id(
        *,
        feature_key: str,
        prompt: str,
        first_seen_at: int,
        candidates: Mapping[str, list[tuple[str, int]]],
    ) -> str:
        if feature_key != TITLE_DESCRIPTION_FEATURE_KEY:
            return ""
        source_prompt = title_description_source_prompt(prompt)
        if not source_prompt:
            return ""
        matches = {
            session_id
            for session_id, created_at in candidates.get(source_prompt, [])
            if first_seen_at - RELATED_SESSION_MAX_LAG_SECONDS
            <= created_at
            <= first_seen_at + RELATED_SESSION_CLOCK_SKEW_SECONDS
        }
        return next(iter(matches)) if len(matches) == 1 else ""

    def _upsert_event(
        self,
        connection: sqlite3.Connection,
        *,
        thread_id: str,
        process_uuid: str,
        timestamp: int,
        prompt: str = "",
        feature: BackgroundFeature | None = None,
    ) -> bool:
        if not thread_id:
            return False
        current = connection.execute(
            """
            SELECT process_uuid, feature_key, feature_label, prompt,
                   first_seen_at, last_seen_at, provider
            FROM background_events WHERE event_id=?
            """,
            (thread_id,),
        ).fetchone()
        selected = feature or BackgroundFeature(
            UNKNOWN_FEATURE_KEY, UNKNOWN_FEATURE_LABEL, ""
        )
        if current is None:
            connection.execute(
                """
                INSERT INTO background_events(
                    event_id, thread_id, process_uuid, feature_key, feature_label,
                    prompt, provider, first_seen_at, last_seen_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    thread_id,
                    process_uuid,
                    selected.key,
                    selected.label,
                    prompt,
                    self.provider,
                    timestamp,
                    timestamp,
                ),
            )
            return True
        next_process = process_uuid or str(current["process_uuid"] or "")
        current_feature = str(current["feature_key"] or UNKNOWN_FEATURE_KEY)
        next_feature = selected.key if selected.key != UNKNOWN_FEATURE_KEY else current_feature
        next_label = (
            selected.label
            if selected.key != UNKNOWN_FEATURE_KEY
            else str(current["feature_label"] or UNKNOWN_FEATURE_LABEL)
        )
        next_prompt = prompt or str(current["prompt"] or "")
        first_seen = min(int(current["first_seen_at"] or timestamp), timestamp)
        last_seen = max(int(current["last_seen_at"] or timestamp), timestamp)
        next_values = (
            next_process,
            next_feature,
            next_label,
            next_prompt,
            first_seen,
            last_seen,
        )
        current_values = (
            str(current["process_uuid"] or ""),
            current_feature,
            str(current["feature_label"] or UNKNOWN_FEATURE_LABEL),
            str(current["prompt"] or ""),
            int(current["first_seen_at"] or 0),
            int(current["last_seen_at"] or 0),
        )
        if next_values == current_values:
            return False
        connection.execute(
            """
            UPDATE background_events
            SET process_uuid=?, feature_key=?, feature_label=?, prompt=?,
                first_seen_at=?, last_seen_at=?
            WHERE event_id=?
            """,
            (*next_values, thread_id),
        )
        return True

    @staticmethod
    def _update_event_context(
        connection: sqlite3.Connection,
        event_id: str,
        *,
        cwd: str,
        endpoint: str,
    ) -> bool:
        row = connection.execute(
            "SELECT cwd, endpoint FROM background_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if row is None:
            return False
        next_cwd = cwd or str(row["cwd"] or "")
        next_endpoint = endpoint or str(row["endpoint"] or "")
        if (next_cwd, next_endpoint) == (
            str(row["cwd"] or ""),
            str(row["endpoint"] or ""),
        ):
            return False
        connection.execute(
            "UPDATE background_events SET cwd=?, endpoint=? WHERE event_id=?",
            (next_cwd, next_endpoint, event_id),
        )
        return True

    def _insert_request(
        self,
        connection: sqlite3.Connection,
        *,
        event_id: str,
        request: BackgroundRequestEvidence,
    ) -> bool:
        existing = connection.execute(
            "SELECT 1 FROM background_requests WHERE source_log_id=?",
            (request.source_log_id,),
        ).fetchone()
        if existing is not None:
            return False
        previous = connection.execute(
            """
            SELECT total_tokens, estimated_input_tokens FROM background_requests
            WHERE event_id=? ORDER BY source_log_id DESC LIMIT 1
            """,
            (event_id,),
        ).fetchone()
        previous_total = int(previous[0] or 0) if previous is not None else 0
        previous_input = int(previous[1] or 0) if previous is not None else 0
        estimated_input, cached_input, output_tokens = resolve_request_token_split(
            total_tokens=request.total_tokens,
            estimated_input_tokens=request.estimated_input_tokens,
            previous_total_tokens=previous_total,
            previous_input_tokens=previous_input,
        )
        event = connection.execute(
            "SELECT provider, endpoint FROM background_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        provider = str(event["provider"] or "") if event is not None else self.provider
        endpoint = str(event["endpoint"] or "") if event is not None else ""
        estimated_cost, price_snapshot = self._estimate_request_cost(
            model=request.model,
            input_tokens=estimated_input,
            cached_input_tokens=cached_input,
            output_tokens=output_tokens,
            provider=provider,
            occurred_at=request.occurred_at,
        )
        price_status, price_version_id = _snapshot_metadata(price_snapshot)
        connection.execute(
            """
            INSERT INTO background_requests(
                request_id, event_id, source_log_id, occurred_at, model, endpoint,
                total_tokens, estimated_input_tokens, estimated_cached_tokens,
                estimated_output_tokens, estimated_cost_usd, price_snapshot_json,
                price_status, price_version_id
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"log:{request.source_log_id}",
                event_id,
                request.source_log_id,
                request.occurred_at,
                request.model,
                endpoint,
                request.total_tokens,
                estimated_input,
                cached_input,
                output_tokens,
                estimated_cost,
                _snapshot_json(price_snapshot),
                price_status,
                price_version_id,
            ),
        )
        self._refresh_event_totals(connection, event_id, request.occurred_at)
        return True

    def _estimate_request_cost(
        self,
        *,
        model: str,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        provider: str,
        occurred_at: object,
    ) -> tuple[float | None, dict[str, object] | None]:
        estimated_cost, snapshot = self._calculator.calculate_cost_with_snapshot(
            model,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            provider=provider,
            occurred_at=occurred_at,
        )
        return estimated_cost, _normalized_snapshot(snapshot)

    def _refresh_event_totals(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        fallback_last_seen: int = 0,
    ) -> None:
        aggregate = connection.execute(
            """
            SELECT COUNT(*) AS request_count,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost,
                   SUM(CASE WHEN estimated_cost_usd IS NULL THEN 1 ELSE 0 END)
                       AS missing_costs,
                   MAX(occurred_at) AS last_seen
            FROM background_requests WHERE event_id=?
            """,
            (event_id,),
        ).fetchone()
        connection.execute(
            """
            UPDATE background_events
            SET request_count=?, total_tokens=?, estimated_cost_usd=?,
                cost_available=?, last_seen_at=MAX(last_seen_at, ?)
            WHERE event_id=?
            """,
            (
                int(aggregate["request_count"] or 0),
                int(aggregate["total_tokens"] or 0),
                float(aggregate["estimated_cost"] or 0.0),
                1 if int(aggregate["missing_costs"] or 0) == 0 else 0,
                int(aggregate["last_seen"] or fallback_last_seen or 0),
                event_id,
            ),
        )

    def _repair_missing_input_splits_if_needed(
        self,
        connection: sqlite3.Connection,
    ) -> bool:
        """One-time rewrite of rows billed as pure output after Codex dropped estimates."""
        row = connection.execute(
            "SELECT value FROM metadata WHERE key=?",
            (REQUEST_SPLIT_REPAIR_METADATA_KEY,),
        ).fetchone()
        if row is not None and str(row[0] or "") == "1":
            return False
        changed = self._repair_missing_input_splits(connection)
        connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES(?, '1')
            ON CONFLICT(key) DO UPDATE SET value='1'
            """,
            (REQUEST_SPLIT_REPAIR_METADATA_KEY,),
        )
        return changed

    def _repair_missing_input_splits(
        self,
        connection: sqlite3.Connection,
    ) -> bool:
        broken_events = connection.execute(
            """
            SELECT DISTINCT event_id
            FROM background_requests
            WHERE estimated_input_tokens=0 AND total_tokens>0
            ORDER BY event_id
            """
        ).fetchall()
        if not broken_events:
            return False
        changed = False
        for event_row in broken_events:
            event_id = str(event_row[0])
            event = connection.execute(
                "SELECT provider FROM background_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            provider = (
                str(event["provider"] or "") if event is not None else self.provider
            )
            requests = connection.execute(
                """
                SELECT request_id, total_tokens, estimated_input_tokens, model,
                       occurred_at
                FROM background_requests
                WHERE event_id=?
                ORDER BY source_log_id
                """,
                (event_id,),
            ).fetchall()
            previous_total = 0
            previous_input = 0
            for request in requests:
                total_tokens = int(request["total_tokens"] or 0)
                stored_input = int(request["estimated_input_tokens"] or 0)
                # Rows that still have a real estimate keep it; only pure-output
                # fallbacks (input=0 while total>0) are rewritten.
                if stored_input == 0 and total_tokens > 0:
                    estimated_input, cached_input, output_tokens = (
                        resolve_request_token_split(
                            total_tokens=total_tokens,
                            estimated_input_tokens=None,
                            previous_total_tokens=previous_total,
                            previous_input_tokens=previous_input,
                        )
                    )
                    estimated_cost, price_snapshot = self._estimate_request_cost(
                        model=str(request["model"] or ""),
                        input_tokens=estimated_input,
                        cached_input_tokens=cached_input,
                        output_tokens=output_tokens,
                        provider=provider,
                        occurred_at=int(request["occurred_at"] or 0),
                    )
                    price_status, price_version_id = _snapshot_metadata(price_snapshot)
                    connection.execute(
                        """
                        UPDATE background_requests
                        SET estimated_input_tokens=?,
                            estimated_cached_tokens=?,
                            estimated_output_tokens=?,
                            estimated_cost_usd=?,
                            price_snapshot_json=?,
                            price_status=?,
                            price_version_id=?
                        WHERE request_id=?
                        """,
                        (
                            estimated_input,
                            cached_input,
                            output_tokens,
                            estimated_cost,
                            _snapshot_json(price_snapshot),
                            price_status,
                            price_version_id,
                            str(request["request_id"]),
                        ),
                    )
                    previous_input = estimated_input
                    changed = True
                else:
                    previous_input = stored_input
                previous_total = total_tokens
            self._refresh_event_totals(connection, event_id)
        return changed

    @staticmethod
    def _record_process_evidence(
        connection: sqlite3.Connection,
        process_uuid: str,
        evidence: str,
        timestamp: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO process_evidence(process_uuid, app_evidence, last_seen_at)
            VALUES(?, ?, ?)
            ON CONFLICT(process_uuid) DO UPDATE SET
                app_evidence=excluded.app_evidence,
                last_seen_at=MAX(last_seen_at, excluded.last_seen_at)
            """,
            (process_uuid, evidence, timestamp),
        )

    def _reclassify(
        self,
        connection: sqlite3.Connection,
        *,
        visible: Mapping[str, str],
        child_threads: set[str],
        related_session_candidates: Mapping[str, list[tuple[str, int]]],
        now_ts: int,
    ) -> bool:
        changed = False
        rows = connection.execute(
            """
            SELECT event_id, thread_id, process_uuid, feature_key, prompt,
                   first_seen_at, request_count, classification_state,
                   app_attribution, related_session_id, association_kind
            FROM background_events
            """
        ).fetchall()
        process_evidence = {
            str(row[0]): str(row[1])
            for row in connection.execute(
                "SELECT process_uuid, app_evidence FROM process_evidence"
            )
        }
        for row in rows:
            thread_id = str(row["thread_id"])
            current_state = str(row["classification_state"] or "pending")
            current_attribution = str(row["app_attribution"] or "")
            feature_key = str(row["feature_key"] or UNKNOWN_FEATURE_KEY)
            prompt = str(row["prompt"] or "")
            if feature_key == UNKNOWN_FEATURE_KEY:
                repaired_feature = classify_background_feature(prompt)
                if repaired_feature.key != UNKNOWN_FEATURE_KEY:
                    feature_key = repaired_feature.key
                    connection.execute(
                        """
                        UPDATE background_events
                        SET feature_key=?, feature_label=?
                        WHERE event_id=?
                        """,
                        (
                            repaired_feature.key,
                            repaired_feature.label,
                            str(row["event_id"]),
                        ),
                    )
                    changed = True
            if thread_id in visible or thread_id in child_threads:
                next_state = "excluded"
                next_attribution = "visible_session" if thread_id in visible else "subagent"
            elif int(row["request_count"] or 0) <= 0:
                next_state = "pending"
                next_attribution = current_attribution
            else:
                process_uuid = str(row["process_uuid"] or "")
                pid_match = _PROCESS_PID_RE.match(process_uuid)
                pid = int(pid_match.group(1)) if pid_match is not None else 0
                if feature_key != UNKNOWN_FEATURE_KEY:
                    next_attribution = "feature_signature"
                elif process_uuid in process_evidence:
                    next_attribution = process_evidence[process_uuid]
                elif pid in self.app_process_ids:
                    next_attribution = "live_desktop_process"
                else:
                    next_attribution = ""
                if not next_attribution:
                    next_state = "unattributed"
                elif now_ts - int(row["first_seen_at"] or now_ts) < self.grace_seconds:
                    next_state = "pending"
                else:
                    next_state = "background"
            next_related_session_id = self._related_session_id(
                feature_key=feature_key,
                prompt=prompt,
                first_seen_at=int(row["first_seen_at"] or 0),
                candidates=related_session_candidates,
            )
            next_association_kind = (
                "exact_first_user_message" if next_related_session_id else ""
            )
            inherited_confirmed_at: int | None = None
            if next_related_session_id and not str(row["related_session_id"] or ""):
                confirmed = connection.execute(
                    """
                    SELECT MAX(confirmed_at) FROM background_events
                    WHERE related_session_id=? AND confirmed_at IS NOT NULL
                    """,
                    (next_related_session_id,),
                ).fetchone()
                if confirmed is not None and confirmed[0] is not None:
                    inherited_confirmed_at = int(confirmed[0])
            if (next_state, next_attribution, next_related_session_id, next_association_kind) == (
                current_state,
                current_attribution,
                str(row["related_session_id"] or ""),
                str(row["association_kind"] or ""),
            ):
                continue
            connection.execute(
                """
                UPDATE background_events
                SET classification_state=?, app_attribution=?,
                    related_session_id=?, association_kind=?,
                    confirmed_at=COALESCE(confirmed_at, ?)
                WHERE event_id=?
                """,
                (
                    next_state,
                    next_attribution,
                    next_related_session_id,
                    next_association_kind,
                    inherited_confirmed_at,
                    str(row["event_id"]),
                ),
            )
            changed = True
        return changed

    def _pending_deadline(self) -> float | None:
        try:
            with closing(self.store._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT MIN(first_seen_at) FROM background_events
                    WHERE classification_state='pending' AND request_count>0
                      AND app_attribution<>''
                    """
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None or row[0] is None:
            return None
        return float(row[0]) + self.grace_seconds


__all__ = [
    "BACKGROUND_USAGE_KIND",
    "BackgroundFeature",
    "BackgroundRequestEvidence",
    "BackgroundScanResult",
    "BackgroundUsageScanner",
    "BackgroundUsageStore",
    "background_feature_en_label",
    "background_feature_label",
    "background_feature_purpose",
    "classify_background_feature",
    "decode_request_context",
    "decode_request_evidence",
    "decode_submission_prompt",
    "resolve_request_token_split",
    "title_description_source_prompt",
    "visible_session_source_prompt",
    "valid_background_event_id",
]
