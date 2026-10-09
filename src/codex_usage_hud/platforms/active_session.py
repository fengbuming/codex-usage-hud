"""Active-session tracking and session-path resolution helpers."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import sys
import threading
import time
import datetime
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import BasePlatform
from ..process_environment import external_process_environment

_LOGGER = logging.getLogger("codex_usage_hud.active_session")
_LOGGER.addHandler(logging.NullHandler())
_THREAD_PATH_NEGATIVE_CACHE_SECONDS = 2.0
_TITLE_PREFIX_MATCH_MIN_CHARS = 8
_PROVISIONAL_RENDERER_THREAD_PREFIX = "client-new-thread:"
_RENDERER_DRAFT_MAX_CHARS = 8000
_RENDERER_COLLAPSED_TITLE_MAX_CHARS = 512


def _session_search_roots(sessions_root: Path) -> tuple[Path, ...]:
    roots = [sessions_root]
    if sessions_root.name == "sessions":
        roots.append(sessions_root.parent / "archived_sessions")
    ordered: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        ordered.append(root)
    return tuple(ordered)


def compact_text(value: Any, limit: int = 28) -> str:
    """Collapse whitespace and trim text for short status labels."""
    if value is None:
        return ""
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _title_prefix(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    # Codex's sidebar title is rendered from the first user message and may
    # drop Markdown heading markers that are still present in the persisted
    # title.  Normalize those presentation-only markers before comparing the
    # renderer title with the local state database.
    text = re.sub(r"(?m)^[ \t]*#+(?=\s)", "", text)
    text = " ".join(text.split()).strip()
    while text.endswith("..."):
        text = text[:-3].rstrip()
    return text.rstrip("…").rstrip()


def _title_search_hint(value: Any) -> str:
    """Return a raw-text hint that survives heading/newline rendering loss."""
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    for line in raw.split("\n"):
        candidate = re.sub(r"^[ \t]*#+(?=\s)", "", line).strip()
        if not candidate:
            continue
        token = candidate.split(None, 1)[0]
        if token:
            return token[: max(_TITLE_PREFIX_MATCH_MIN_CHARS, min(32, len(token)))]
    return _title_prefix(value)[:_TITLE_PREFIX_MATCH_MIN_CHARS]


def _title_matches(name: str, title: str) -> bool:
    name_text = _title_prefix(name)
    title_text = _title_prefix(title)
    if not name_text or not title_text:
        return False
    if name_text == title_text:
        return True
    if name_text.startswith(title_text):
        return len(title_text) >= _TITLE_PREFIX_MATCH_MIN_CHARS
    if title_text.startswith(name_text):
        return len(name_text) >= _TITLE_PREFIX_MATCH_MIN_CHARS
    return False


def is_new_session_source(source: str) -> bool:
    """Return whether an active-session source represents Codex's new-chat page."""
    text = str(source or "").strip()
    return text.startswith(
        (
            "renderer-new-session",
            "cdp-new-session",
            "ui-new-session",
        )
    )


def is_pending_session_source(source: str) -> bool:
    """Return whether renderer authority has a session whose data is not ready yet.

    This is deliberately distinct from an unmatched session. Renderer mode
    receives the selected row immediately, while Codex may publish its exact
    local rollout mapping a moment later. A controlled exact-title fallback may
    use one unarchived persisted candidate; ambiguity remains pending.
    """
    return str(source or "").strip().startswith(
        ("renderer-pending-session", "renderer-pending-map")
    )


def is_provisional_renderer_session_id(session_id: str) -> bool:
    """Whether a renderer id is Codex's pre-persistence new-thread alias."""
    text = str(session_id or "").strip()
    if ":" in text:
        prefix, suffix = text.split(":", 1)
        if prefix.casefold() in {"local", "remote", "thread", "session", "conversation"}:
            text = suffix.strip()
    return text.casefold().startswith(_PROVISIONAL_RENDERER_THREAD_PREFIX)


def _is_new_session_title(title: str) -> bool:
    text = compact_text(title, 80).casefold()
    return text in {
        "new chat",
        "new conversation",
        "new session",
        "新对话",
        "新会话",
        "新聊天",
    }


def _path_archive_state(path: Path | None, sessions_root: Path) -> bool | None:
    """Infer archive state from the canonical local rollout roots when possible."""
    if path is None:
        return None
    try:
        path_key = path.expanduser().resolve(strict=False)
        active_root = sessions_root.expanduser().resolve(strict=False)
        archived_root = (sessions_root.parent / "archived_sessions").resolve(
            strict=False
        )
        try:
            path_key.relative_to(archived_root)
        except ValueError:
            pass
        else:
            return True
        try:
            path_key.relative_to(active_root)
        except ValueError:
            return None
        return False
    except OSError:
        return None


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _archive_flag(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"", "0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def find_session_file(session_id: str, sessions_root: Path) -> Path | None:
    """Find a session JSONL by id substring or direct path."""
    direct = Path(session_id)
    if direct.exists():
        return direct
    search_roots = [root for root in _session_search_roots(sessions_root) if root.exists()]
    if not search_roots:
        return None

    matches: list[Path] = []
    for root in search_roots:
        try:
            matches.extend(root.rglob(f"*{session_id}*.jsonl"))
        except OSError:
            continue
    try:
        matches.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        matches = sorted(
            [item for item in matches if item.exists()],
            reverse=True,
        )
    return matches[0] if matches else None


_THREAD_ID_FILENAME_RE = re.compile(
    r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def _thread_id_from_session_filename(filename: str) -> str:
    """Extract the canonical thread id from a rollout JSONL filename.

    Filename format: ``rollout-YYYY-MM-DDTHH-MM-SS-<thread_uuid>_<turn_uuid>.jsonl``
    Returns the thread uuid or an empty string if the filename does not match.
    """
    match = _THREAD_ID_FILENAME_RE.search(filename or "")
    return match.group(1) if match else ""


class RealtimeSessionWatcher:
    """Stream active Codex conversation titles with event-first fallback paths."""

    def __init__(
        self,
        platform: BasePlatform,
        poll_ms: int,
        on_title: Callable[[str, str, float], None],
        *,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.platform = platform
        self.poll_ms = max(250, int(poll_ms))
        self.on_title = on_title
        self.stop_event = stop_event or threading.Event()
        self.process: subprocess.Popen[str] | None = None
        self.primary_thread: threading.Thread | None = None
        self._threads: list[threading.Thread] = []
        self._last_emitted = ""
        self._last_event_at = 0.0
        self._emit_lock = threading.Lock()

    def start(self) -> bool:
        """Start the best available realtime watcher."""
        if self.primary_thread is not None or self.process is not None:
            return True

        self.stop_event.clear()
        if self.platform.supports_active_title_events():
            self.primary_thread = self._start_thread(
                self._event_loop,
                "codex-hud-active-events",
            )
            if self.platform.supports_active_title_polling():
                self._start_thread(
                    self._poll_backstop_loop,
                    "codex-hud-active-poll-backstop",
                )
            return True

        if self.platform.supports_active_title_polling():
            self.primary_thread = self._start_thread(
                self._poll_loop,
                "codex-hud-active-poll",
            )
            return True

        return self._start_command_sidecar()

    def close(self) -> None:
        """Stop any background watcher threads or sidecar process."""
        self.stop_event.set()
        proc = self.process
        self.process = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

        current = threading.current_thread()
        for thread in list(self._threads):
            if thread is not current and thread.is_alive():
                thread.join(timeout=2.0)
        self._threads = [thread for thread in self._threads if thread.is_alive()]
        self.primary_thread = None if not self._threads else self.primary_thread

    def _start_thread(
        self,
        target: Callable[[], None],
        name: str,
    ) -> threading.Thread:
        thread = threading.Thread(target=target, name=name, daemon=True)
        self._threads.append(thread)
        thread.start()
        return thread

    def _start_command_sidecar(self) -> bool:
        command = self.platform.build_active_title_command(self.poll_ms)
        if not command:
            return False

        kwargs: dict[str, Any] = {}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=external_process_environment(),
                **kwargs,
            )
        except OSError:
            self.process = None
            return False

        self.primary_thread = self._start_thread(
            self._read_loop,
            "codex-hud-active-sidecar",
        )
        return True

    def _event_loop(self) -> None:
        try:
            started = self.platform.watch_active_conversation_title(
                self.stop_event,
                lambda title: self._emit(title, "event"),
            )
        except Exception as exc:
            _LOGGER.debug("active_session_event_stream_failed error=%s", exc)
            started = False
        if started or self.stop_event.is_set():
            return
        if self.platform.supports_active_title_polling():
            self._poll_loop()

    def _poll_backstop_loop(self) -> None:
        backstop_seconds = max(1.0, (self.poll_ms / 1000.0) * 2.0)
        if self.stop_event.wait(backstop_seconds):
            return
        delay = max(0.5, self.poll_ms / 1000.0)
        last_probe_at = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now - max(self._last_event_at, last_probe_at) >= backstop_seconds:
                self._poll_once("poll-backstop")
                last_probe_at = time.monotonic()
            self.stop_event.wait(delay)

    def _poll_loop(self) -> None:
        delay = max(250, self.poll_ms) / 1000.0
        while not self.stop_event.is_set():
            self._poll_once("poll")
            self.stop_event.wait(delay)

    def _poll_once(self, source: str) -> None:
        try:
            title = self.platform.get_active_conversation_title() or ""
        except Exception as exc:
            _LOGGER.debug("active_session_poll_failed error=%s", exc)
            title = ""
        self._emit(title, source)

    def _read_loop(self) -> None:
        proc = self.process
        if proc is None or proc.stdout is None:
            return

        for line in proc.stdout:
            if self.stop_event.is_set():
                return
            title = line.rstrip("\r\n")
            if not title.startswith("TITLE\t"):
                continue
            self._emit(title[6:], "sidecar")

    def _emit(self, title: str, source: str) -> None:
        text = title.strip()
        if not text:
            return
        detected_at = time.monotonic()
        with self._emit_lock:
            if text == self._last_emitted:
                return
            self._last_emitted = text
            if source == "event":
                self._last_event_at = detected_at
        self.on_title(text, source, detected_at)


class ActiveSessionTracker:
    """Track the currently selected Codex conversation and map it to a JSONL path."""

    def __init__(
        self,
        platform: BasePlatform,
        state_db: Path,
        sessions_root: Path,
        session_index_path: Path,
        poll_ms: int,
        enabled: bool,
        *,
        start_background_watcher: bool = True,
    ) -> None:
        self.platform = platform
        self.state_db = state_db
        self.sessions_root = sessions_root
        self.session_index_path = session_index_path
        self.poll_ms = max(250, int(poll_ms))
        self.enabled = bool(enabled)
        self.start_background_watcher = bool(start_background_watcher)
        self.latest_session_id = ""
        self.latest_title = ""
        self.latest_path: Path | None = None
        self.latest_source = (
            "renderer-waiting"
            if self.enabled and not self.start_background_watcher
            else ("ui-waiting" if self.enabled else "activity")
        )
        self._mapped_title = ""
        self._title_cache_key: tuple[str, str] | None = None
        self._title_cache_value = ""
        self._thread_path_cache: dict[str, tuple[Path | None, float]] = {}
        self._threads_table_available: bool | None = None
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._watcher: RealtimeSessionWatcher | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.latest_response_ms = 0.0
        self.latest_event_source = ""
        self._renderer_session_id = ""
        self._renderer_raw_session_id = ""
        self._renderer_title = ""
        self._renderer_path: Path | None = None
        self._renderer_new_session = False
        self._renderer_pending_session = False
        self._renderer_draft_text = ""
        self._renderer_draft_updated_at_ms = 0
        self._renderer_send_requested = False
        self._renderer_collapsed_title = ""
        self._renderer_collapsed_disclosure_ambiguous = False
        self._renderer_match_candidates: list[dict[str, object]] = []
        self._renderer_manual_candidate_id = ""
        self._selection_seq = 0
        self._selection_observed_at_ms = 0
        self._selection_received_at_ms = 0
        self._selection_resolved_at_ms = 0
        # Wall-clock ms when we first entered a sticky follow state
        # (new-session / pending / channel-unavailable). Repeated identical
        # new-session observes must not refresh this, or self-heal never fires.
        self._follow_stuck_since_ms = 0
        self._follow_state = "waiting"
        self._follow_reason = "renderer-waiting"
        self._change_callback: Callable[[], None] | None = None

    @property
    def selection_seq(self) -> int:
        with self._lock:
            return self._selection_seq

    @property
    def selection_observed_at_ms(self) -> int:
        with self._lock:
            return self._selection_observed_at_ms

    @property
    def selection_received_at_ms(self) -> int:
        with self._lock:
            return self._selection_received_at_ms

    @property
    def selection_resolved_at_ms(self) -> int:
        with self._lock:
            return self._selection_resolved_at_ms

    @property
    def follow_stuck_since_ms(self) -> int:
        """When the current sticky follow state first started (0 if not stuck)."""
        with self._lock:
            return int(self._follow_stuck_since_ms or 0)

    @property
    def follow_stuck_elapsed_ms(self) -> int:
        """Milliseconds spent in the current sticky follow state."""
        with self._lock:
            started = int(self._follow_stuck_since_ms or 0)
        if started <= 0:
            return 0
        return max(0, int(time.time() * 1000) - started)

    @property
    def follow_state(self) -> str:
        with self._lock:
            return self._follow_state

    @property
    def follow_reason(self) -> str:
        with self._lock:
            return self._follow_reason

    @property
    def renderer_session_id(self) -> str:
        """Return the exact raw identity currently exposed by the renderer."""
        with self._lock:
            return self._renderer_raw_session_id

    @property
    def renderer_new_session(self) -> bool:
        with self._lock:
            return bool(self._renderer_new_session)

    @property
    def renderer_pending_session(self) -> bool:
        with self._lock:
            return bool(self._renderer_pending_session)

    @property
    def renderer_draft_text(self) -> str:
        """Return the unsent Renderer composer text, if any."""
        with self._lock:
            return self._renderer_draft_text

    @property
    def renderer_draft_updated_at_ms(self) -> int:
        with self._lock:
            return int(self._renderer_draft_updated_at_ms or 0)

    @property
    def renderer_send_requested(self) -> bool:
        """Whether the latest Renderer composer event requested a send."""
        with self._lock:
            return bool(self._renderer_send_requested)

    @property
    def renderer_collapsed_title(self) -> str:
        """Return the native current-session work-disclosure label."""
        with self._lock:
            return self._renderer_collapsed_title

    @property
    def renderer_collapsed_disclosure_ambiguous(self) -> bool:
        """Return whether the current turn has multiple work disclosures."""
        with self._lock:
            return bool(self._renderer_collapsed_disclosure_ambiguous)

    @property
    def match_candidates(self) -> list[dict[str, object]]:
        """Return safe, unarchived candidates for a pending renderer match."""
        with self._lock:
            return [dict(item) for item in self._renderer_match_candidates]

    def follow_snapshot(self) -> dict[str, object]:
        """Compact follow diagnostics for heal progress checks."""
        with self._lock:
            return {
                "followState": self._follow_state,
                "followReason": self._follow_reason,
                "newSession": bool(self._renderer_new_session),
                "pendingSession": bool(self._renderer_pending_session),
                "sessionId": self._renderer_session_id,
                "rendererSessionId": self._renderer_raw_session_id,
                "title": self._renderer_title,
                # Do not include draft content in diagnostics; only expose the
                # state needed to explain a pending send without leaking text.
                "draftLength": len(self._renderer_draft_text),
                "sendRequested": bool(self._renderer_send_requested),
                "path": str(self._renderer_path) if self._renderer_path is not None else "",
                "matchCandidates": [
                    dict(item) for item in self._renderer_match_candidates
                ],
                "stuckSinceMs": int(self._follow_stuck_since_ms or 0),
                "stuckElapsedMs": (
                    max(0, int(time.time() * 1000) - int(self._follow_stuck_since_ms or 0))
                    if int(self._follow_stuck_since_ms or 0) > 0
                    else 0
                ),
            }

    @staticmethod
    def follow_progressed(
        before: dict[str, object] | None,
        after: dict[str, object] | None,
    ) -> bool:
        """Whether a self-heal report actually advanced session follow state."""
        prev = dict(before or {})
        nxt = dict(after or {})
        if bool(prev.get("newSession")) and not bool(nxt.get("newSession")):
            return True
        if str(prev.get("followState") or "") == "new-session" and str(
            nxt.get("followState") or ""
        ) not in {"", "new-session"}:
            return True
        prev_id = str(prev.get("sessionId") or prev.get("rendererSessionId") or "")
        next_id = str(nxt.get("sessionId") or nxt.get("rendererSessionId") or "")
        if next_id and next_id != prev_id:
            return True
        prev_path = str(prev.get("path") or "")
        next_path = str(nxt.get("path") or "")
        if next_path and next_path != prev_path:
            return True
        if str(nxt.get("followState") or "") == "confirmed" and str(
            prev.get("followState") or ""
        ) != "confirmed":
            return True
        if str(prev.get("followReason") or "") == "renderer-channel-unavailable" and str(
            nxt.get("followReason") or ""
        ) not in {"", "renderer-channel-unavailable"}:
            return True
        return False

    def set_change_callback(self, callback: Callable[[], None] | None) -> None:
        """Notify the renderer loop when the background active-session watcher moves."""
        self._change_callback = callback

    def mark_renderer_channel_unavailable(self, reason: str = "") -> bool:
        """Keep the current selection while exposing a renderer transport failure."""
        now_ms = int(time.time() * 1000)
        with self._lock:
            changed = (
                self._follow_state != "pending"
                or self._follow_reason != "renderer-channel-unavailable"
            )
            self._follow_state = "pending"
            self._follow_reason = "renderer-channel-unavailable"
            self.latest_event_source = str(reason or "renderer-binding-disconnected")
            if self._follow_stuck_since_ms <= 0:
                self._follow_stuck_since_ms = now_ms
        if changed:
            self._notify_change()
        return changed

    def _notify_change(self) -> None:
        callback = self._change_callback
        if callback is None:
            return
        try:
            callback()
        except Exception:
            return

    def start(self) -> None:
        """Begin best-effort platform tracking for the selected Codex conversation."""
        if (
            not self.enabled
            or not self.start_background_watcher
            or self._watcher is not None
        ):
            return

        self._stop_event.clear()
        watcher = RealtimeSessionWatcher(
            self.platform,
            self.poll_ms,
            self._handle_title_candidate,
            stop_event=self._stop_event,
        )
        if not watcher.start():
            self.enabled = False
            self.latest_source = "activity"
            return
        self._watcher = watcher
        self._proc = watcher.process
        self._thread = watcher.primary_thread

    def close(self) -> None:
        """Stop the background title tracker process if one is running."""
        self._stop_event.set()
        watcher = self._watcher
        self._watcher = None
        if watcher is not None:
            watcher.close()
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def wait_for_title(self, timeout_ms: int = 1200) -> None:
        """Give the background title probe a short chance to produce an initial title."""
        if not self.enabled or not self.start_background_watcher:
            return
        deadline = time.time() + (max(0, timeout_ms) / 1000.0)
        while time.time() < deadline:
            with self._lock:
                if self.latest_title:
                    return
            if self._proc is not None and self._proc.poll() is not None:
                return
            time.sleep(0.05)

    def current_path(self) -> Path | None:
        """Resolve the latest observed Codex conversation title to a JSONL path."""
        if not self.enabled:
            return None
        renderer_selected, renderer_path = self._current_renderer_selection()
        if renderer_selected:
            return renderer_path
        if not self.start_background_watcher:
            with self._lock:
                self.latest_session_id = ""
                self.latest_title = ""
                self.latest_path = None
                self._mapped_title = ""
                self.latest_source = "renderer-waiting"
                self.latest_event_source = "renderer"
            return None
        ref = self.platform.get_active_conversation_ref()
        if ref is not None:
            session_id, title = ref
            if not session_id and _is_new_session_title(title):
                with self._lock:
                    self.latest_session_id = ""
                    self.latest_title = ""
                    self.latest_path = None
                    self._mapped_title = ""
                    self.latest_source = "cdp-new-session"
                    self.latest_event_source = "cdp"
                return None
            path = self.path_from_thread_id(session_id) if session_id else None
            if path is None and title:
                path = self.path_for_title(title)
            display_title = title
            if session_id and path is not None:
                display_title = (
                    self.title_from_session_index_id(session_id)
                    or self.title_from_thread_id(session_id)
                    or title
                )
            if path is not None or display_title or session_id:
                with self._lock:
                    self.latest_session_id = session_id
                    self.latest_title = display_title or self.latest_title
                    self.latest_path = path
                    self._mapped_title = display_title or self._mapped_title
                    self.latest_source = (
                        f"cdp:{compact_text(display_title or session_id)}"
                        if path is not None
                        else "cdp-unmatched"
                    )
                    self.latest_event_source = "cdp"
                return path
        poll_attempted = self.platform.supports_active_title_polling()
        polled_title = ""
        if poll_attempted:
            try:
                polled_title = (self.platform.get_active_conversation_title() or "").strip()
            except Exception as exc:
                _LOGGER.debug("active_session_direct_poll_failed error=%s", exc)
                polled_title = ""
        if polled_title:
            path = self.path_for_title(polled_title)
            with self._lock:
                event_path = self.latest_path
                event_source = self.latest_event_source
            if path is None and event_path is not None and event_source == "event":
                return event_path
            with self._lock:
                self.latest_title = polled_title
                self.latest_session_id = ""
                self.latest_path = path
                self._mapped_title = polled_title
                self.latest_source = (
                    f"ui:{compact_text(polled_title)}"
                    if path is not None
                    else "ui-unmatched"
                )
                self.latest_event_source = "poll"
            return path
        if poll_attempted:
            with self._lock:
                had_cached_title = bool(self.latest_title)
                if had_cached_title:
                    self.latest_title = ""
                    self.latest_session_id = ""
                    self.latest_path = None
                    self._mapped_title = ""
                    self.latest_source = "ui-unmatched"
                    self.latest_event_source = "poll-empty"
            if had_cached_title:
                return None
        with self._lock:
            title = self.latest_title
        if not title:
            self.latest_source = "ui-waiting"
            return None
        if title == self._mapped_title and self.latest_path is not None:
            return self.latest_path

        path = self.path_for_title(title)
        self._mapped_title = title
        self.latest_path = path
        self.latest_source = (
            f"ui:{compact_text(title)}" if path is not None else "ui-unmatched"
        )
        return path

    def observe_conversation_ref(
        self,
        session_id: str = "",
        title: str = "",
        *,
        source: str = "renderer",
        detected_at: float | None = None,
        new_session: bool = False,
        pending_session: bool = False,
        renderer_session_id: str = "",
        selection_seq: int = 0,
        observed_at_ms: int = 0,
        draft_text: str | None = None,
        send_requested: bool = False,
        collapsed_title: str | None = None,
        collapsed_disclosure_ambiguous: bool | None = None,
        allow_filesystem_fallback: bool = False,
    ) -> bool:
        """Accept an active conversation ref pushed by the renderer bridge."""
        if not self.enabled:
            return False
        session_id = str(session_id or "").strip()
        renderer_session_id = str(renderer_session_id or session_id).strip()
        title = str(title or "").strip()
        incoming_draft = None
        if draft_text is not None:
            incoming_draft = str(draft_text or "").replace("\x00", "")
            if len(incoming_draft) > _RENDERER_DRAFT_MAX_CHARS:
                incoming_draft = incoming_draft[:_RENDERER_DRAFT_MAX_CHARS]
        incoming_send_requested = bool(send_requested)
        incoming_collapsed_title = None
        if collapsed_title is not None:
            incoming_collapsed_title = compact_text(
                str(collapsed_title or "").replace("\x00", ""),
                _RENDERER_COLLAPSED_TITLE_MAX_CHARS,
            )
        provisional_renderer_id = is_provisional_renderer_session_id(
            renderer_session_id
        )
        pending_session = bool(pending_session) or is_provisional_renderer_session_id(
            renderer_session_id
        )
        new_session = bool(new_session) or (
            not renderer_session_id and _is_new_session_title(title)
        )
        try:
            incoming_seq = max(0, int(selection_seq or 0))
        except (TypeError, ValueError):
            incoming_seq = 0
        has_incoming_seq = incoming_seq > 0
        try:
            incoming_observed_at_ms = max(0, int(observed_at_ms or 0))
        except (TypeError, ValueError):
            incoming_observed_at_ms = 0
        incoming_received_at_ms = int(time.time() * 1000)
        with self._lock:
            current_seq = self._selection_seq
            current_identity = (
                self._renderer_raw_session_id,
                self._renderer_title,
                self._renderer_new_session,
                self._renderer_pending_session,
            )
        if incoming_seq and incoming_seq < current_seq:
            return False
        identity = (renderer_session_id, title, new_session, pending_session)
        if not incoming_seq:
            incoming_seq = current_seq + 1 if identity != current_identity else current_seq
        follow_reason = ""
        match_candidates: list[dict[str, object]] = []
        manual_candidate_id = ""
        resolved_path: Path | None = None
        if provisional_renderer_id and title:
            with self._lock:
                if (
                    renderer_session_id == self._renderer_raw_session_id
                    and title == self._renderer_title
                    and (not has_incoming_seq or incoming_seq == current_seq)
                ):
                    candidate_id = self._renderer_manual_candidate_id
                    candidate_path = self.path_from_renderer_thread_id(
                        candidate_id,
                        allow_filesystem_fallback=True,
                    )
                    if candidate_path is not None:
                        candidate_record = self._renderer_candidate_record(
                            candidate_id,
                            candidate_path,
                            title,
                        )
                        if candidate_record.get("archived") is False:
                            manual_candidate_id = candidate_id
            if manual_candidate_id:
                resolved_id = manual_candidate_id
                resolved_path = self.path_from_renderer_thread_id(
                    resolved_id,
                    allow_filesystem_fallback=True,
                )
                follow_reason = "manual-selection" if resolved_path is not None else ""
            else:
                (
                    resolved_id,
                    resolved_path,
                    follow_reason,
                    match_candidates,
                ) = self._resolve_provisional_renderer_ref_details(
                    renderer_session_id,
                    title,
                )
            if resolved_id and resolved_path is not None:
                session_id = resolved_id
                pending_session = False
        if new_session:
            session_id = ""
            renderer_session_id = ""
            title = ""
            follow_reason = "new-session"
        elif pending_session:
            session_id = ""
            follow_reason = follow_reason or "awaiting-canonical-id"
        elif not session_id:
            session_id = renderer_session_id
        if not session_id and not title and not new_session and not pending_session:
            return False
        detected = detected_at if detected_at is not None else time.monotonic()

        # Renderer is the authority. A canonical renderer id may only map via
        # its exact state-db record. Provisional rows use only the controlled
        # unique-unarchived title fallback; ambiguity never picks a substitute.
        path = (
            resolved_path
            if resolved_path is not None
            else (
                self.path_from_renderer_thread_id(
                    session_id,
                    allow_filesystem_fallback=allow_filesystem_fallback,
                )
                if session_id
                else None
            )
        )
        # Title-only refs (common when DOM exposes the chrome title but no
        # thread id yet) still need an exact title map or they stick forever on
        # awaiting-exact-mapping with an empty sessionId.
        if path is None and not session_id and title and not new_session and not pending_session:
            path = self.path_for_title(title)
            if path is not None and self.state_db.exists():
                try:
                    con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
                    try:
                        raw_path = str(path)
                        prefixed = (
                            raw_path
                            if raw_path.startswith("\\\\?\\")
                            else f"\\\\?\\{raw_path}"
                        )
                        row = con.execute(
                            """
                            select id
                            from threads
                            where rollout_path in (?, ?)
                            order by archived asc, updated_at_ms desc, updated_at desc
                            limit 1
                            """,
                            (raw_path, prefixed),
                        ).fetchone()
                    finally:
                        con.close()
                except sqlite3.Error:
                    row = None
                if row is not None:
                    session_id = str(row[0] or "").strip() or session_id
                    if session_id and not renderer_session_id:
                        renderer_session_id = session_id
        if session_id and path is None and not pending_session:
            follow_reason = "awaiting-exact-mapping"
        elif path is None and title and not new_session and not pending_session:
            follow_reason = "awaiting-exact-mapping"
        elif path is not None:
            follow_reason = "confirmed"
        display_title = title
        if session_id and path is not None:
            display_title = (
                self.title_from_session_index_id(session_id)
                or self.title_from_thread_id(session_id)
                or title
            )
        if not display_title and session_id:
            display_title = self.title_from_session_index_id(session_id)

        response_ms = (time.monotonic() - detected) * 1000.0
        incoming_resolved_at_ms = int(time.time() * 1000)
        source_label = compact_text(display_title or session_id)
        latest_source = (
            f"{source}-new-session"
            if new_session
            else (
                f"{source}-pending-session"
                if pending_session
                else (
                    f"{source}:{source_label}"
                    if path is not None
                    else f"{source}-pending-map"
                )
            )
        )
        with self._lock:
            previous_title = self.latest_title
            previous_path = self.latest_path
            previous_source = self.latest_source
            previous_session_id = self.latest_session_id
            previous_renderer_session_id = self._renderer_session_id
            previous_renderer_title = self._renderer_title
            previous_renderer_path = self._renderer_path
            previous_renderer_new_session = self._renderer_new_session
            previous_renderer_pending_session = self._renderer_pending_session
            previous_renderer_draft = self._renderer_draft_text
            previous_renderer_draft_updated_at_ms = self._renderer_draft_updated_at_ms
            previous_renderer_send_requested = self._renderer_send_requested
            previous_renderer_collapsed_title = self._renderer_collapsed_title
            previous_renderer_collapsed_disclosure_ambiguous = (
                self._renderer_collapsed_disclosure_ambiguous
            )
            previous_match_candidates = [
                dict(item) for item in self._renderer_match_candidates
            ]
            previous_selection_seq = self._selection_seq
            previous_follow_state = self._follow_state
            previous_follow_reason = self._follow_reason
            previous_stuck_since = int(self._follow_stuck_since_ms or 0)
            next_follow_state = (
                "confirmed"
                if path is not None
                else ("new-session" if new_session else "pending")
            )
            sticky_same_new_session = bool(
                new_session
                and previous_renderer_new_session
                and next_follow_state == "new-session"
            )
            sticky_same_pending = bool(
                pending_session
                and previous_renderer_pending_session
                and not new_session
                and next_follow_state == "pending"
                and renderer_session_id == self._renderer_raw_session_id
                and display_title == previous_renderer_title
                and path is None
            )
            # Canonical id OR title-only waiting on mapping (awaiting-exact-mapping).
            # This is NOT provisional pending_session, so the branch above missed it
            # and every re-observe reset the stuck clock / blocked heal.
            # Live logs showed title-only pending-map with empty sessionId still
            # labeled awaiting-exact-mapping by current_path().
            sticky_same_pending_map = bool(
                not new_session
                and not pending_session
                and next_follow_state == "pending"
                and path is None
                and previous_follow_state == "pending"
                and previous_renderer_path is None
                and (
                    (
                        bool(session_id)
                        and session_id == previous_renderer_session_id
                    )
                    or (
                        not session_id
                        and not previous_renderer_session_id
                        and bool(display_title)
                        and display_title == previous_renderer_title
                    )
                )
            )
            # Keep the first latch timestamp while we remain stuck. Refreshing
            # observed_at on every MutationObserver new-session pulse zeroed
            # follow_elapsed and blocked self-heal forever.
            if sticky_same_new_session or sticky_same_pending or sticky_same_pending_map:
                observed_at_to_store = (
                    previous_stuck_since
                    if previous_stuck_since > 0
                    else (
                        incoming_observed_at_ms
                        if incoming_observed_at_ms > 0
                        else incoming_received_at_ms
                    )
                )
                stuck_since_to_store = (
                    previous_stuck_since
                    if previous_stuck_since > 0
                    else observed_at_to_store
                )
            elif next_follow_state in {"new-session", "pending"} and path is None:
                observed_at_to_store = (
                    incoming_observed_at_ms
                    if incoming_observed_at_ms > 0
                    else incoming_received_at_ms
                )
                stuck_since_to_store = observed_at_to_store
            else:
                observed_at_to_store = (
                    incoming_observed_at_ms
                    if incoming_observed_at_ms > 0
                    else incoming_received_at_ms
                )
                stuck_since_to_store = 0

            selection_changed = (
                renderer_session_id != previous_renderer_session_id
                or display_title != previous_renderer_title
                or new_session != previous_renderer_new_session
                or pending_session != previous_renderer_pending_session
            )
            if new_session or pending_session:
                next_collapsed_title = ""
                next_collapsed_disclosure_ambiguous = False
            elif incoming_collapsed_title is not None:
                next_collapsed_title = incoming_collapsed_title
                next_collapsed_disclosure_ambiguous = bool(
                    collapsed_disclosure_ambiguous
                )
            elif selection_changed:
                # Do not carry a native DOM label across a real selection.
                next_collapsed_title = ""
                next_collapsed_disclosure_ambiguous = False
            else:
                next_collapsed_title = previous_renderer_collapsed_title
                next_collapsed_disclosure_ambiguous = (
                    previous_renderer_collapsed_disclosure_ambiguous
                )
            next_draft = (
                incoming_draft
                if incoming_draft is not None
                else previous_renderer_draft
            )
            next_draft_updated_at_ms = previous_renderer_draft_updated_at_ms
            if incoming_draft is not None and (
                incoming_draft != previous_renderer_draft
                or incoming_send_requested
            ):
                next_draft_updated_at_ms = (
                    incoming_observed_at_ms
                    or incoming_received_at_ms
                )
            provisional_selection = bool(
                path is None and (new_session or pending_session)
            )
            if path is not None:
                # The send pulse must reach the current-session refresh even
                # after its JSONL path is known. A later non-send observation
                # clears it; browser draft content still yields to JSONL.
                next_draft = ""
                next_draft_updated_at_ms = 0
                next_send_requested = incoming_send_requested
            elif selection_changed and not provisional_selection:
                # A switch to a different unresolved/ordinary selection cannot
                # inherit the previous conversation's draft.
                next_draft = ""
                next_draft_updated_at_ms = 0
                next_send_requested = False
            else:
                next_send_requested = (
                    incoming_send_requested or previous_renderer_send_requested
                )
            if provisional_selection and next_draft_updated_at_ms <= 0:
                next_draft_updated_at_ms = (
                    incoming_observed_at_ms
                    or incoming_received_at_ms
                )
            self._renderer_session_id = session_id
            self._renderer_raw_session_id = renderer_session_id
            self._renderer_title = display_title
            self._renderer_path = path
            self._renderer_new_session = new_session
            self._renderer_pending_session = pending_session
            self._renderer_draft_text = next_draft
            self._renderer_draft_updated_at_ms = next_draft_updated_at_ms
            self._renderer_send_requested = next_send_requested
            self._renderer_collapsed_title = next_collapsed_title
            self._renderer_collapsed_disclosure_ambiguous = (
                next_collapsed_disclosure_ambiguous
            )
            self._renderer_match_candidates = (
                []
                if new_session or path is not None or not provisional_renderer_id
                else [dict(item) for item in match_candidates]
            )
            if manual_candidate_id:
                self._renderer_manual_candidate_id = manual_candidate_id
            elif (
                not provisional_renderer_id
                or incoming_seq != current_seq
                or renderer_session_id != previous_renderer_session_id
                or display_title != previous_renderer_title
            ):
                self._renderer_manual_candidate_id = ""
            self._selection_seq = incoming_seq
            self._selection_observed_at_ms = observed_at_to_store
            self._selection_received_at_ms = incoming_received_at_ms
            self._selection_resolved_at_ms = incoming_resolved_at_ms
            self._follow_stuck_since_ms = stuck_since_to_store
            self._follow_state = next_follow_state
            self._follow_reason = follow_reason
            self.latest_session_id = session_id
            self.latest_title = display_title
            self.latest_path = path
            self._mapped_title = display_title
            self.latest_source = latest_source
            self.latest_response_ms = response_ms
            self.latest_event_source = source

        changed = (
            display_title != previous_title
            or path != previous_path
            or latest_source != previous_source
            or session_id != previous_session_id
            or session_id != previous_renderer_session_id
            or display_title != previous_renderer_title
            or path != previous_renderer_path
            or new_session != previous_renderer_new_session
            or pending_session != previous_renderer_pending_session
            or self._renderer_match_candidates != previous_match_candidates
            or incoming_seq != previous_selection_seq
            or self._follow_state != previous_follow_state
            or follow_reason != previous_follow_reason
            or self._renderer_draft_text != previous_renderer_draft
            or self._renderer_draft_updated_at_ms
            != previous_renderer_draft_updated_at_ms
            or self._renderer_send_requested != previous_renderer_send_requested
            or self._renderer_collapsed_title != previous_renderer_collapsed_title
            or self._renderer_collapsed_disclosure_ambiguous
            != previous_renderer_collapsed_disclosure_ambiguous
        )
        if changed:
            _LOGGER.info(
                "ACTIVE_SESSION_SWITCH source=%s matched=%s response_ms=%.1f title=%r stuck_ms=%s",
                source,
                path is not None and not new_session,
                response_ms,
                "new-session"
                if new_session
                else (
                    "pending-session"
                    if pending_session
                    else compact_text(display_title or session_id, 80)
                ),
                self.follow_stuck_elapsed_ms,
            )
            self._notify_change()
        return changed

    def path_for_title(self, title: str) -> Path | None:
        """Map a visible Codex conversation title to a session JSONL path."""
        indexed_path = self.path_from_session_index(title)
        if not self.state_db.exists():
            return indexed_path
        try:
            con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
            try:
                con.row_factory = sqlite3.Row
                columns = {
                    str(row[1] or "").strip()
                    for row in con.execute("pragma table_info(threads)")
                }
                archive_column = ", archived" if "archived" in columns else ""

                def active_paths(sql: str, params: tuple[object, ...]) -> list[Path]:
                    rows = con.execute(sql, params).fetchall()
                    paths: dict[str, Path] = {}
                    for item in rows:
                        candidate = self._normalize_rollout_path(
                            str(item["rollout_path"] or "")
                        )
                        if candidate is None:
                            continue
                        archived = _path_archive_state(candidate, self.sessions_root)
                        if "archived" in columns and item["archived"] is not None:
                            archived = _archive_flag(item["archived"])
                        if archived is not False:
                            continue
                        paths[self._path_key(candidate)] = candidate
                    return list(paths.values())

                exact_paths = active_paths(
                    f"select rollout_path{archive_column} from threads where title = ?",
                    (title,),
                )
                if len(exact_paths) > 1:
                    return None

                title_prefix = _title_prefix(title)
                prefix_paths: list[Path] = []
                if indexed_path is not None:
                    prefix_paths.append(indexed_path)
                prefix_paths.extend(exact_paths)
                prefix_paths = list(
                    {
                        self._path_key(candidate): candidate
                        for candidate in prefix_paths
                    }.values()
                )
                if len(title_prefix) >= _TITLE_PREFIX_MATCH_MIN_CHARS:
                    if not prefix_paths:
                        prefix_paths.extend(
                            active_paths(
                                f"""
                                select rollout_path{archive_column}
                                from threads
                                where length(title) >= ?
                                  and title like ? || '%'
                                """,
                                (_TITLE_PREFIX_MATCH_MIN_CHARS, title_prefix),
                            )
                        )
                    if not prefix_paths:
                        prefix_paths.extend(
                            active_paths(
                                f"""
                                select rollout_path{archive_column}
                                from threads
                                where length(title) >= ?
                                  and ? like title || '%'
                                """,
                                (_TITLE_PREFIX_MATCH_MIN_CHARS, title),
                            )
                        )

                if (
                    len(prefix_paths) <= 1
                    and len(title_prefix) >= _TITLE_PREFIX_MATCH_MIN_CHARS
                ):
                    # The persisted title can contain newlines and Markdown
                    # heading markers that the sidebar omits.  Use a small
                    # content hint to bound the fallback query,
                    # then apply the same normalized comparison in Python so
                    # duplicate-title ambiguity still fails closed.
                    search_hint = _title_search_hint(title)
                    rows = con.execute(
                        f"select rollout_path{archive_column}, title "
                        "from threads "
                        "where instr(lower(title), lower(?)) > 0",
                        (search_hint,),
                    ).fetchall()
                    normalized_paths: dict[str, Path] = {}
                    for item in rows:
                        if not _title_matches(str(item["title"] or ""), title):
                            continue
                        candidate = self._normalize_rollout_path(
                            str(item["rollout_path"] or "")
                        )
                        if candidate is None:
                            continue
                        archived = _path_archive_state(candidate, self.sessions_root)
                        if "archived" in columns and item["archived"] is not None:
                            archived = _archive_flag(item["archived"])
                        if archived is not False:
                            continue
                        normalized_paths[self._path_key(candidate)] = candidate
                    existing_paths = {
                        self._path_key(candidate): candidate for candidate in prefix_paths
                    }
                    existing_paths.update(normalized_paths)
                    prefix_paths = list(existing_paths.values())
            finally:
                con.close()
        except sqlite3.Error:
            return indexed_path
        return prefix_paths[0] if len(prefix_paths) == 1 else None

    def title_for_session(
        self,
        session_path: Path | None,
        session_id: str = "",
    ) -> str:
        """Resolve the display title for the session currently shown in the HUD."""
        session_key = (session_id or "", self._path_key(session_path))
        with self._lock:
            latest_title = self.latest_title.strip()
            latest_path = self.latest_path
        if latest_title and self._same_path(latest_path, session_path):
            self._title_cache_key = session_key
            self._title_cache_value = latest_title
            return latest_title
        if self._title_cache_key == session_key:
            return self._title_cache_value

        title = ""
        if session_id:
            title = self.title_from_session_index_id(session_id)
            if not title:
                title = self.title_from_thread_id(session_id)
        if not title and session_path is not None:
            title = self.title_from_rollout_path(session_path)
        self._title_cache_key = session_key
        self._title_cache_value = title
        return title

    def path_from_session_index(self, title: str) -> Path | None:
        """Map a session title via ``session_index.jsonl`` before hitting SQLite."""
        if not self.session_index_path.exists():
            return None

        candidate_ids: list[str] = []
        try:
            with self.session_index_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    name = str(item.get("thread_name") or "")
                    if _title_matches(name, title):
                        candidate_id = str(item.get("id") or "").strip()
                        if candidate_id and candidate_id not in candidate_ids:
                            candidate_ids.append(candidate_id)
        except OSError:
            return None

        active_paths: dict[str, Path] = {}
        for candidate_id in candidate_ids:
            path = self.path_from_renderer_thread_id(candidate_id)
            if path is None:
                path = find_session_file(candidate_id, self.sessions_root)
            if path is None:
                continue
            record = self._renderer_candidate_record(candidate_id, path, title)
            if record.get("archived") is False:
                active_paths[self._path_key(path)] = path
        if len(active_paths) != 1:
            return None
        return next(iter(active_paths.values()))

    def title_from_session_index_id(self, session_id: str) -> str:
        """Look up the visible thread title for a known session id."""
        if not session_id or not self.session_index_path.exists():
            return ""
        best_title = ""
        try:
            with self.session_index_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if str(item.get("id") or "") != session_id:
                        continue
                    title = str(item.get("thread_name") or "").strip()
                    if title:
                        best_title = title
        except OSError:
            return ""
        return best_title

    def resolve_provisional_renderer_ref(
        self,
        session_id: str,
        title: str,
    ) -> tuple[str, Path | None]:
        """Resolve a temporary sidebar id when it represents a persisted thread.

        Codex can keep ``client-new-thread:*`` in a task row after persistence.
        Accept only an exact, unique unarchived title candidate from
        session_index.jsonl whose canonical rollout path already exists;
        otherwise remain pending.
        """
        resolved_id, path, _reason, _candidates = (
            self._resolve_provisional_renderer_ref_details(
                session_id,
                title,
            )
        )
        return resolved_id, path

    def _renderer_candidate_record(
        self,
        session_id: str,
        path: Path,
        title: str,
    ) -> dict[str, object]:
        """Read archive metadata for one exact renderer candidate."""
        archived = _path_archive_state(path, self.sessions_root)
        candidate_title = str(title or "").strip()
        updated_at_ms = 0
        try:
            if self.state_db.exists():
                con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
                try:
                    columns = {
                        str(row[1] or "").strip()
                        for row in con.execute("pragma table_info(threads)")
                    }
                    selected = ["id"]
                    for column in ("title", "archived", "updated_at_ms"):
                        if column in columns:
                            selected.append(column)
                    row = con.execute(
                        f"select {', '.join(selected)} from threads where id = ? limit 1",
                        (session_id,),
                    ).fetchone()
                finally:
                    con.close()
                if row is not None:
                    values = dict(zip(selected, row))
                    db_title = str(values.get("title") or "").strip()
                    if db_title:
                        candidate_title = db_title
                    if "archived" in values and values.get("archived") is not None:
                        archived = _archive_flag(values.get("archived"))
                    updated_at_ms = _safe_int(values.get("updated_at_ms"))
        except sqlite3.Error:
            pass
        return {
            "sessionId": str(session_id or "").strip(),
            "title": candidate_title,
            "archived": archived,
            "updatedAtMs": updated_at_ms,
            "rolloutName": path.name,
        }

    def _thread_ids_for_rollout_path(self, session_path: Path) -> list[str]:
        """Return active thread ids that point at one exact rollout path.

        The renderer can keep a provisional ``client-new-thread:*`` id after
        the local session has already been persisted.  In that window the
        session index may still contain the old title, while ``threads`` has
        the current title and canonical id.  Resolve the id from the selected
        path without falling back to latest-activity heuristics.
        """
        if not self.state_db.exists() or not self._threads_table_exists():
            return []
        raw_path = str(session_path)
        prefixed_path = (
            raw_path
            if raw_path.startswith("\\\\?\\")
            else f"\\\\?\\{raw_path}"
        )
        try:
            con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
            try:
                con.row_factory = sqlite3.Row
                columns = {
                    str(row[1] or "").strip()
                    for row in con.execute("pragma table_info(threads)")
                }
                if "id" not in columns or "rollout_path" not in columns:
                    return []
                selected = ["id"]
                if "archived" in columns:
                    selected.append("archived")
                order_by = [
                    column
                    for column in ("archived", "updated_at_ms", "updated_at")
                    if column in columns
                ]
                query = (
                    f"select {', '.join(selected)} from threads "
                    "where rollout_path in (?, ?)"
                )
                if order_by:
                    query += " order by " + ", ".join(
                        f"{column} asc" for column in order_by
                    )
                rows = con.execute(query, (raw_path, prefixed_path)).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            return []

        result: list[str] = []
        seen: set[str] = set()
        path_archive = _path_archive_state(session_path, self.sessions_root)
        for row in rows:
            session_id = str(row["id"] or "").strip()
            if not session_id or session_id in seen:
                continue
            archived = path_archive
            if "archived" in row.keys() and row["archived"] is not None:
                archived = _archive_flag(row["archived"])
            if archived is not False:
                continue
            seen.add(session_id)
            result.append(session_id)
        return result

    def _resolve_provisional_renderer_ref_details(
        self,
        session_id: str,
        title: str,
    ) -> tuple[str, Path | None, str, list[dict[str, object]]]:
        """Resolve a provisional row using only persisted unarchived candidates."""
        if not is_provisional_renderer_session_id(session_id) or not title:
            return "", None, "awaiting-canonical-id", []
        candidates: dict[str, tuple[Path, dict[str, object]]] = {}
        resolved_by_id: dict[str, tuple[Path, dict[str, object]]] = {}
        try:
            with self.session_index_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not _title_matches(
                        str(item.get("thread_name") or "").strip(),
                        title,
                    ):
                        continue
                    candidate_id = str(item.get("id") or "").strip()
                    if not candidate_id or is_provisional_renderer_session_id(candidate_id):
                        continue
                    path = self.path_from_renderer_thread_id(candidate_id)
                    if path is None:
                        # The session index can be written before state_5.sqlite
                        # receives its thread row.  The rollout filename is an
                        # exact-id lookup, so it is safe for this controlled
                        # provisional-session reconciliation.
                        path = find_session_file(candidate_id, self.sessions_root)
                    if path is not None:
                        record = self._renderer_candidate_record(candidate_id, path, title)
                        resolved_by_id.setdefault(candidate_id, (path, record))
                        if record.get("archived") is False:
                            candidates[candidate_id] = (path, record)
        except OSError:
            # The state DB can already contain the canonical row even while
            # session_index.jsonl is being replaced or is temporarily absent.
            pass

        # A long-running Desktop process can update the current thread title in
        # state_5.sqlite before session_index.jsonl catches up.  Use the same
        # exact-title/prefix mapping as ordinary title resolution, then recover
        # the canonical id from that exact rollout path.  The active-path set
        # remains unique-only, so duplicate titles still fail closed.
        title_path = self.path_for_title(title)
        if title_path is not None:
            for candidate_id in self._thread_ids_for_rollout_path(title_path):
                if candidate_id in resolved_by_id:
                    continue
                record = self._renderer_candidate_record(
                    candidate_id,
                    title_path,
                    title,
                )
                resolved_by_id[candidate_id] = (title_path, record)
                if record.get("archived") is False:
                    candidates[candidate_id] = (title_path, record)

        matched_candidate_ids = len(resolved_by_id)
        resolved_records = [record for _path, record in resolved_by_id.values()]
        if len(candidates) != 1:
            reason = (
                "ambiguous-persisted-identity"
                if len(candidates) > 1
                else (
                    "no-unarchived-candidate"
                    if matched_candidate_ids
                    and len(resolved_records) == matched_candidate_ids
                    and resolved_records
                    and all(item.get("archived") is True for item in resolved_records)
                    else "awaiting-persistence"
                )
            )
            payload_candidates = [
                dict(record)
                for _path, record in sorted(
                    candidates.values(),
                    key=lambda item: (
                        -_safe_int(item[1].get("updatedAtMs")),
                        str(item[1].get("sessionId") or ""),
                    ),
                )
            ]
            return "", None, reason, payload_candidates
        candidate_id, (path, _record) = next(iter(candidates.items()))
        return candidate_id, path, "confirmed", []

    def resolve_renderer_candidate(
        self,
        session_id: str,
        *,
        selection_seq: int = 0,
    ) -> bool:
        """Bind the current provisional row to one displayed unarchived candidate."""
        if not self.enabled:
            return False
        candidate_id = str(session_id or "").strip()
        try:
            requested_seq = max(0, int(selection_seq or 0))
        except (TypeError, ValueError):
            requested_seq = 0
        with self._lock:
            raw_id = str(self._renderer_raw_session_id or "").strip()
            title = str(self._renderer_title or self.latest_title or "").strip()
            current_seq = int(self._selection_seq or 0)
            candidates = {
                str(item.get("sessionId") or "").strip(): dict(item)
                for item in self._renderer_match_candidates
            }
        if not is_provisional_renderer_session_id(raw_id) or not candidate_id:
            return False
        if requested_seq and requested_seq != current_seq:
            return False
        candidate = candidates.get(candidate_id)
        if candidate is None or candidate.get("archived") is not False:
            return False
        path = self.path_from_renderer_thread_id(
            candidate_id,
            allow_filesystem_fallback=True,
        )
        if path is None:
            return False
        record = self._renderer_candidate_record(candidate_id, path, title)
        if record.get("archived") is not False:
            return False
        with self._lock:
            if (
                self._renderer_raw_session_id != raw_id
                or self._renderer_title != title
                or self._selection_seq != current_seq
            ):
                return False
            self._renderer_manual_candidate_id = candidate_id
        self.observe_conversation_ref(
            candidate_id,
            title,
            source="renderer",
            renderer_session_id=raw_id,
            selection_seq=current_seq,
            observed_at_ms=self.selection_observed_at_ms,
        )
        with self._lock:
            return bool(
                self._renderer_session_id == candidate_id
                and self._renderer_path is not None
                and self._follow_state == "confirmed"
            )

    def _threads_table_exists(self) -> bool:
        """Lazily check whether the state DB has the legacy ``threads`` table.

        Newer Codex builds store thread metadata in ``local_thread_catalog``
        (without a ``rollout_path`` column), so the old
        ``select rollout_path from threads`` query always fails. Cached for
        the process lifetime because table schema does not change at runtime.
        """
        if self._threads_table_available is not None:
            return self._threads_table_available
        if not self.state_db.exists():
            self._threads_table_available = False
            return False
        try:
            con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "select 1 from sqlite_master "
                    "where type='table' and name='threads' limit 1"
                ).fetchone()
                self._threads_table_available = row is not None
            finally:
                con.close()
        except sqlite3.Error:
            self._threads_table_available = False
        return self._threads_table_available

    def _session_date_dir_from_catalog(self, thread_id: str) -> Path | None:
        """Derive the per-day session directory from local_thread_catalog.

        Newer Codex builds store thread metadata in ``local_thread_catalog``
        with a ``source_created_at`` unix timestamp. Session JSONL files are
        organised under ``sessions/YYYY/MM/DD/`` by local date. Using this
        hint narrows the filesystem search from a full recursive rglob to a
        single directory glob, which stays fast regardless of how many
        historical sessions accumulate.
        """
        if not self.state_db.exists():
            return None
        try:
            con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "select source_created_at from local_thread_catalog "
                    "where thread_id = ? limit 1",
                    (thread_id,),
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return None
        if not row or not row[0]:
            return None
        try:
            ts = float(row[0])
        except (TypeError, ValueError):
            return None
        # Session directories use local date, matching the filename timestamp.
        local_dt = datetime.datetime.fromtimestamp(ts)
        return self.sessions_root / f"{local_dt.year:04d}" / f"{local_dt.month:02d}" / f"{local_dt.day:02d}"

    def path_from_renderer_thread_id(
        self,
        thread_id: str,
        *,
        allow_filesystem_fallback: bool = False,
    ) -> Path | None:
        """Resolve a canonical renderer id through its exact state-db row.

        By default this is intentionally narrower than :meth:`path_from_thread_id`
        and never uses title matching. When ``allow_filesystem_fallback`` is set
        (self-heal / rematerialize after the mapping has been stuck), a last-resort
        exact-id filesystem lookup is allowed so a missing/lagging state-db row
        does not pin the HUD on “加载精确会话映射” forever.
        """
        thread_id = str(thread_id or "").strip()
        if not thread_id or is_provisional_renderer_session_id(thread_id):
            return None
        if ":" in thread_id:
            prefix, suffix = thread_id.split(":", 1)
            if prefix.lower() in {
                "local",
                "remote",
                "thread",
                "session",
                "conversation",
            }:
                thread_id = suffix.strip()
        if not thread_id:
            return None

        cache_key = f"renderer:{thread_id}"
        now = time.monotonic()
        threads_table_ok = self._threads_table_exists()
        cached = self._thread_path_cache.get(cache_key)
        if cached is not None:
            cached_path, cached_at = cached
            if cached_path is not None and cached_path.exists():
                return cached_path
            if (
                cached_path is None
                and now - cached_at <= _THREAD_PATH_NEGATIVE_CACHE_SECONDS
                and threads_table_ok
                and not allow_filesystem_fallback
            ):
                return None

        path = None
        if threads_table_ok and self.state_db.exists():
            try:
                con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
                try:
                    row = con.execute(
                        "select rollout_path from threads where id = ? limit 1",
                        (thread_id,),
                    ).fetchone()
                finally:
                    con.close()
            except sqlite3.Error:
                row = None
            path = self._normalize_rollout_path(str(row[0] or "")) if row else None
        # Filesystem fallback is explicit for a known legacy schema, but a
        # database that genuinely has no ``threads`` table is a newer Codex
        # layout where the filename is the only exact-id index.  A completely
        # missing database is different: it may simply be a cold-start race,
        # so the initial renderer observation stays pending until the explicit
        # reconciliation path opts into the fallback.
        if path is None and (
            allow_filesystem_fallback
            or (not threads_table_ok and self.state_db.exists())
        ):
            # Narrow the search to the session's creation-date directory when
            # local_thread_catalog has source_created_at. A single-directory
            # glob is O(files_in_day) instead of O(all_historical_sessions).
            date_dir = self._session_date_dir_from_catalog(thread_id)
            if date_dir is not None and date_dir.is_dir():
                try:
                    date_matches = list(date_dir.glob(f"*{thread_id}*.jsonl"))
                    if date_matches:
                        date_matches.sort(
                            key=lambda item: item.stat().st_mtime, reverse=True
                        )
                        path = date_matches[0]
                except OSError:
                    path = None
            # Fall back to full recursive search if the date hint was absent
            # or the file was not found there (e.g. archived / moved sessions).
            if path is None:
                path = find_session_file(thread_id, self.sessions_root)
            if path is not None:
                _LOGGER.info(
                    "RENDERER_MAPPING_FILESYSTEM_FALLBACK thread_id=%s path=%s",
                    compact_text(thread_id, 64),
                    path,
                )
        self._thread_path_cache[cache_key] = (path, now)
        return path

    def rematerialize_renderer_mapping(self, *, force: bool = True) -> bool:
        """Re-resolve the latched renderer session id/title against state-db / files.

        Used when follow is stuck on awaiting-exact-mapping. DOM re-report alone
        cannot help when the page only exposes a title, or a known id whose
        state-db row lags. Returns True when the mapping advanced to a real path.
        """
        if not self.enabled:
            return False
        with self._lock:
            if self._renderer_new_session:
                return False
            session_id = str(self._renderer_session_id or "").strip()
            raw_id = str(self._renderer_raw_session_id or session_id).strip()
            title = str(self._renderer_title or self.latest_title or "").strip()
            selection_seq = int(self._selection_seq or 0)
            observed_at_ms = int(self._selection_observed_at_ms or 0)
            already_mapped = (
                self._renderer_path is not None and self._renderer_path.exists()
            )
        if already_mapped:
            return False
        if not session_id and not raw_id and not title:
            return False
        if force:
            self.invalidate_mapping_cache()

        path: Path | None = None
        resolved_id = session_id
        if session_id:
            path = self.path_from_renderer_thread_id(
                session_id,
                allow_filesystem_fallback=True,
            )
        if path is None and raw_id and raw_id != session_id:
            path = self.path_from_renderer_thread_id(
                raw_id,
                allow_filesystem_fallback=True,
            )
            if path is not None and not resolved_id:
                resolved_id = raw_id
        # Title-only pending-map (live bug): page exposed a real conversation
        # title but no thread id. Exact title → state-db / session_index is still
        # renderer-authority-safe because it does not pick "latest activity".
        if path is None and title:
            path = self.path_for_title(title)
            if path is not None:
                mapped_id = ""
                if self.state_db.exists():
                    try:
                        con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
                        try:
                            raw_path = str(path)
                            prefixed = (
                                raw_path
                                if raw_path.startswith("\\\\?\\")
                                else f"\\\\?\\{raw_path}"
                            )
                            row = con.execute(
                                """
                                select id
                                from threads
                                where rollout_path in (?, ?)
                                order by archived asc, updated_at_ms desc, updated_at desc
                                limit 1
                                """,
                                (raw_path, prefixed),
                            ).fetchone()
                        finally:
                            con.close()
                    except sqlite3.Error:
                        row = None
                    if row is not None:
                        mapped_id = str(row[0] or "").strip()
                if mapped_id:
                    resolved_id = mapped_id
                _LOGGER.info(
                    "RENDERER_MAPPING_TITLE_FALLBACK title=%r path=%s id=%s",
                    compact_text(title, 80),
                    path,
                    compact_text(resolved_id, 64),
                )

        if path is None:
            _LOGGER.info(
                "RENDERER_MAPPING_STILL_PENDING session_id=%s raw_id=%s title=%r stuck_ms=%s",
                compact_text(session_id, 64),
                compact_text(raw_id, 64),
                compact_text(title, 80),
                self.follow_stuck_elapsed_ms,
            )
            return False

        changed = self.observe_conversation_ref(
            resolved_id or session_id or raw_id,
            title,
            source="renderer",
            renderer_session_id=raw_id or resolved_id or session_id,
            selection_seq=selection_seq,
            observed_at_ms=observed_at_ms,
            allow_filesystem_fallback=True,
        )
        with self._lock:
            confirmed = (
                self._renderer_path is not None and self._follow_state == "confirmed"
            )
        _LOGGER.info(
            "RENDERER_MAPPING_REMATERIALIZED session_id=%s path=%s changed=%s confirmed=%s",
            compact_text(resolved_id or session_id or raw_id, 64),
            path,
            changed,
            confirmed,
        )
        return confirmed or changed

    def heal_pending_from_filesystem(self) -> bool:
        """Resolve a stuck new-session / pending mapping from the newest
        session file on disk.

        When the user sends a message in a brand-new conversation, Codex
        creates the rollout JSONL within ~100-500ms but the canonical
        thread id may not appear in the renderer DOM for several seconds.
        The sessions-root file watcher fires on the new file; this method
        extracts the thread id directly from the filename (which embeds the
        UUID right after the creation timestamp) and feeds it into
        observe_conversation_ref, so the bubble can appear without waiting
        for the DOM to expose the canonical id.

        Returns True when the mapping advanced to a real path.
        """
        if not self.enabled:
            return False
        with self._lock:
            follow_state = self._follow_state
            renderer_session_id = self._renderer_session_id
            renderer_path = self._renderer_path
            stuck_since_ms = int(self._follow_stuck_since_ms or 0)
        stuck_ms = (
            max(0, int(time.time() * 1000) - stuck_since_ms)
            if stuck_since_ms > 0
            else 0
        )
        if follow_state not in ("new-session", "pending"):
            return False
        if renderer_session_id and renderer_path is not None:
            return False
        # Only heal within a reasonable window after the send event.
        if stuck_ms > 30_000:
            return False
        try:
            now = time.time()
            cutoff = now - 15.0
            candidates: list[tuple[float, Path]] = []
            for root in _session_search_roots(self.sessions_root):
                if not root.exists():
                    continue
                try:
                    for path in root.rglob("*.jsonl"):
                        try:
                            mtime = path.stat().st_mtime
                        except OSError:
                            continue
                        if mtime >= cutoff:
                            candidates.append((mtime, path))
                except OSError:
                    continue
            if not candidates:
                return False
            candidates.sort(key=lambda item: item[0], reverse=True)
            newest_path = candidates[0][1]
            thread_id = _thread_id_from_session_filename(newest_path.name)
            if not thread_id:
                return False
            # Don't heal if the newest file is already the current one.
            with self._lock:
                if (
                    self._renderer_path is not None
                    and self._renderer_path == newest_path
                ):
                    return False
            changed = self.observe_conversation_ref(
                thread_id,
                "",
                source="filesystem-heal",
                renderer_session_id=thread_id,
                allow_filesystem_fallback=True,
            )
            with self._lock:
                confirmed = (
                    self._renderer_path is not None
                    and self._follow_state == "confirmed"
                )
            if confirmed or changed:
                _LOGGER.info(
                    "RENDERER_MAPPING_FILESYSTEM_HEAL thread_id=%s path=%s stuck_ms=%s",
                    compact_text(thread_id, 64),
                    newest_path,
                    stuck_ms,
                )
                return True
        except Exception as exc:
            _LOGGER.debug("renderer_mapping_filesystem_heal_failed error=%s", exc)
        return False

    def path_from_thread_id(self, thread_id: str) -> Path | None:
        """Resolve a known thread id to a local rollout JSONL path."""
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return None
        direct = Path(thread_id)
        if direct.exists():
            return direct
        if ":" in thread_id:
            prefix, suffix = thread_id.split(":", 1)
            if prefix.lower() in {"local", "remote", "thread", "session", "conversation"}:
                thread_id = suffix.strip()
        if not thread_id:
            return None
        now = time.monotonic()
        cached = self._thread_path_cache.get(thread_id)
        if cached is not None:
            cached_path, cached_at = cached
            if cached_path is not None and cached_path.exists():
                return cached_path
            if (
                cached_path is None
                and now - cached_at <= _THREAD_PATH_NEGATIVE_CACHE_SECONDS
            ):
                return None

        path = None
        if self.state_db.exists():
            try:
                con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
                try:
                    row = con.execute(
                        "select rollout_path from threads where id = ? limit 1",
                        (thread_id,),
                    ).fetchone()
                finally:
                    con.close()
            except sqlite3.Error:
                row = None
            if row is not None:
                path = self._normalize_rollout_path(str(row[0] or ""))
                if path is not None:
                    self._thread_path_cache[thread_id] = (path, now)
                    return path

        path = find_session_file(thread_id, self.sessions_root)
        if path is not None:
            self._thread_path_cache[thread_id] = (path, now)
            return path
        self._thread_path_cache[thread_id] = (path, now)
        return path

    def title_from_thread_id(self, thread_id: str) -> str:
        """Resolve a known thread id to its visible title via the state database."""
        if not thread_id or not self.state_db.exists():
            return ""
        try:
            con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
            try:
                row = con.execute(
                    """
                    select title
                    from threads
                    where id = ?
                    order by archived asc, updated_at_ms desc, updated_at desc
                    limit 1
                    """,
                    (thread_id,),
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return ""
        if row is None:
            return ""
        return str(row[0] or "").strip()

    def title_from_rollout_path(self, session_path: Path) -> str:
        """Resolve a visible title using the rollout path stored in the state database."""
        if not self.state_db.exists():
            return ""
        raw_path = str(session_path)
        prefixed_path = raw_path if raw_path.startswith("\\\\?\\") else f"\\\\?\\{raw_path}"
        try:
            con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
            try:
                row = con.execute(
                    """
                    select title
                    from threads
                    where rollout_path in (?, ?)
                    order by archived asc, updated_at_ms desc, updated_at desc
                    limit 1
                    """,
                    (raw_path, prefixed_path),
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return ""
        if row is None:
            return ""
        return str(row[0] or "").strip()

    def invalidate_mapping_cache(self) -> None:
        """Clear title/thread lookup caches after session mapping files change."""
        with self._lock:
            self._title_cache_key = None
            self._title_cache_value = ""
            self._thread_path_cache.clear()

    def _handle_title_candidate(
        self,
        title: str,
        source: str = "event",
        detected_at: float | None = None,
    ) -> None:
        title = title.strip()
        if not title:
            return
        detected = detected_at if detected_at is not None else time.monotonic()
        previous_title = ""
        previous_path: Path | None = None
        with self._lock:
            previous_title = self.latest_title
            previous_path = self.latest_path

        path = self.path_for_title(title)
        if path is None and self.platform.supports_active_title_polling():
            fallback_title = (self.platform.get_active_conversation_title() or "").strip()
            if fallback_title and fallback_title != title:
                fallback_path = self.path_for_title(fallback_title)
                if fallback_path is not None:
                    title = fallback_title
                    path = fallback_path
                    source = f"{source}+poll-confirm"
        response_ms = (time.monotonic() - detected) * 1000.0
        with self._lock:
            self.latest_title = title
            self.latest_session_id = ""
            self.latest_path = path
            self._mapped_title = title
            self.latest_source = (
                f"ui:{compact_text(title)}" if path is not None else "ui-unmatched"
            )
            self.latest_response_ms = response_ms
            self.latest_event_source = source

        if title != previous_title or path != previous_path:
            _LOGGER.info(
                "ACTIVE_SESSION_SWITCH source=%s matched=%s response_ms=%.1f title=%r",
                source,
                path is not None,
                response_ms,
                compact_text(title, 80),
            )
            self._notify_change()

    def _current_renderer_selection(self) -> tuple[bool, Path | None]:
        with self._lock:
            if self._renderer_new_session:
                # Keep transport-down diagnostics visible. Overwriting the follow
                # reason with "new-session" hid CDP binding failures behind the
                # sticky blank-chat latch and made the top bar look healthy-but-stale.
                channel_down = self._follow_reason == "renderer-channel-unavailable"
                self.latest_session_id = ""
                self.latest_title = ""
                self.latest_path = None
                self._mapped_title = ""
                self.latest_source = "renderer-new-session"
                self.latest_event_source = "renderer"
                if channel_down:
                    self._follow_state = "pending"
                else:
                    self._follow_state = "new-session"
                    self._follow_reason = "new-session"
                if self._follow_stuck_since_ms <= 0:
                    self._follow_stuck_since_ms = (
                        int(self._selection_observed_at_ms or 0)
                        or int(time.time() * 1000)
                    )
                return True, None
            if self._renderer_pending_session:
                raw_session_id = self._renderer_raw_session_id
                title = self._renderer_title
                selection_seq = self._selection_seq
                observed_at_ms = self._selection_observed_at_ms
            else:
                raw_session_id = ""
                title = ""
                selection_seq = 0
                observed_at_ms = 0
        if raw_session_id and is_provisional_renderer_session_id(raw_session_id):
            resolved_id, resolved_path, reason, match_candidates = (
                self._resolve_provisional_renderer_ref_details(raw_session_id, title)
            )
            if resolved_id and resolved_path is not None:
                self.observe_conversation_ref(
                    resolved_id,
                    title,
                    source="renderer",
                    renderer_session_id=raw_session_id,
                    selection_seq=selection_seq,
                    observed_at_ms=observed_at_ms,
                )
                return True, resolved_path
            with self._lock:
                self.latest_session_id = ""
                self.latest_title = title
                self.latest_path = None
                self._mapped_title = ""
                self.latest_source = "renderer-pending-session"
                self.latest_event_source = "renderer"
                self._follow_state = "pending"
                self._follow_reason = reason
                self._renderer_match_candidates = [
                    dict(item) for item in match_candidates
                ]
                if self._follow_stuck_since_ms <= 0:
                    self._follow_stuck_since_ms = (
                        int(self._selection_observed_at_ms or 0)
                        or int(time.time() * 1000)
                    )
            return True, None
        with self._lock:
            if self._renderer_pending_session:
                self.latest_session_id = ""
                self.latest_path = None
                self._mapped_title = ""
                self.latest_source = "renderer-pending-session"
                self.latest_event_source = "renderer"
                self._follow_state = "pending"
                self._follow_reason = "awaiting-canonical-id"
                if self._follow_stuck_since_ms <= 0:
                    self._follow_stuck_since_ms = (
                        int(self._selection_observed_at_ms or 0)
                        or int(time.time() * 1000)
                    )
                return True, None
            if not self._renderer_session_id and not self._renderer_title:
                return False, None
            title = self._renderer_title
            session_id = self._renderer_session_id
            path = self._renderer_path
            selection_seq = self._selection_seq
            observed_at_ms = self._selection_observed_at_ms
            raw_id = self._renderer_raw_session_id
        if path is not None and path.exists():
            with self._lock:
                self.latest_session_id = session_id
                self.latest_title = title
                self.latest_path = path
                self._mapped_title = title
                self.latest_source = f"renderer:{compact_text(title or session_id)}"
                self.latest_event_source = "renderer"
                self._follow_state = "confirmed"
                self._follow_reason = "confirmed"
                self._follow_stuck_since_ms = 0
            return True, path
        if session_id:
            path = self.path_from_renderer_thread_id(session_id)
            if path is not None and not title:
                title = self.title_from_session_index_id(
                    session_id
                ) or self.title_from_thread_id(session_id)
        elif title:
            # Title-only selection (no thread id from DOM yet). Exact title map is
            # still safer than activity fallback and unblocks live pending-map.
            path = self.path_for_title(title)
            if path is not None:
                mapped_id = ""
                if self.state_db.exists():
                    try:
                        con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
                        try:
                            raw_path = str(path)
                            prefixed = (
                                raw_path
                                if raw_path.startswith("\\\\?\\")
                                else f"\\\\?\\{raw_path}"
                            )
                            row = con.execute(
                                """
                                select id
                                from threads
                                where rollout_path in (?, ?)
                                order by archived asc, updated_at_ms desc, updated_at desc
                                limit 1
                                """,
                                (raw_path, prefixed),
                            ).fetchone()
                        finally:
                            con.close()
                    except sqlite3.Error:
                        row = None
                    if row is not None:
                        mapped_id = str(row[0] or "").strip()
                if mapped_id:
                    self.observe_conversation_ref(
                        mapped_id,
                        title,
                        source="renderer",
                        renderer_session_id=raw_id or mapped_id,
                        selection_seq=selection_seq,
                        observed_at_ms=observed_at_ms,
                    )
                    return True, path
                session_id = ""
        with self._lock:
            self._renderer_session_id = session_id
            self._renderer_title = title
            self._renderer_path = path
            self._renderer_new_session = False
            self._renderer_pending_session = False
            self.latest_session_id = session_id
            self.latest_title = title
            self.latest_path = path
            self._mapped_title = title
            self.latest_source = (
                f"renderer:{compact_text(title or session_id)}"
                if path is not None
                else "renderer-pending-map"
            )
            self.latest_event_source = "renderer"
            self._follow_state = "confirmed" if path is not None else "pending"
            self._follow_reason = (
                "confirmed" if path is not None else "awaiting-exact-mapping"
            )
            if path is not None:
                self._follow_stuck_since_ms = 0
            elif self._follow_stuck_since_ms <= 0:
                self._follow_stuck_since_ms = (
                    int(self._selection_observed_at_ms or 0)
                    or int(time.time() * 1000)
                )
        return True, path

    def _normalize_rollout_path(self, path_text: str) -> Path | None:
        text = path_text[4:] if path_text.startswith("\\\\?\\") else path_text
        path = Path(text)
        return path if path.exists() else None

    @staticmethod
    def _path_key(path: Path | None) -> str:
        if path is None:
            return ""
        try:
            return str(path.resolve())
        except OSError:
            return str(path)

    @classmethod
    def _same_path(cls, left: Path | None, right: Path | None) -> bool:
        if left is None or right is None:
            return False
        return cls._path_key(left) == cls._path_key(right)


class SessionPathResolver:
    """Choose which session JSONL the HUD should follow."""

    def __init__(
        self,
        platform: BasePlatform,
        sessions_root: Path,
        session_id: str | None = None,
        session_file: Path | None = None,
        active_session_tracker: ActiveSessionTracker | None = None,
        auto_switch_idle_seconds: float = 30.0,
    ) -> None:
        self.platform = platform
        self.sessions_root = sessions_root
        self.session_id = session_id
        self.session_file = session_file
        self.active_session_tracker = active_session_tracker
        self.auto_switch_idle_seconds = max(0.0, float(auto_switch_idle_seconds))
        self.auto_session_file: Path | None = None
        self.selection_source = (
            "pinned" if (self.session_id or self.session_file) else "activity"
        )

    @staticmethod
    def _has_unresolved_tracker_selection(source: str) -> bool:
        return (
            source.startswith("ui-unmatched")
            or source.startswith("cdp-unmatched")
        )

    def resolve(self) -> tuple[Path | None, str]:
        """Resolve the current session path plus a short source label."""
        if self.session_file is not None:
            self.selection_source = "pinned:file"
            return self.session_file, self.selection_source

        if self.session_id:
            self.selection_source = "pinned:id"
            return find_session_file(self.session_id, self.sessions_root), self.selection_source

        tracker_source = ""
        active_path = None
        if self.active_session_tracker is not None:
            active_path = self.active_session_tracker.current_path()
            tracker_source = self.active_session_tracker.latest_source
        if active_path is not None:
            self.auto_session_file = active_path
            self.selection_source = tracker_source
            return active_path, self.selection_source

        if self.active_session_tracker is not None and self.active_session_tracker.enabled:
            self.selection_source = tracker_source
            if is_new_session_source(tracker_source):
                self.auto_session_file = None
                return None, self.selection_source
            if is_pending_session_source(tracker_source):
                self.auto_session_file = None
                return None, self.selection_source
            if tracker_source.startswith("renderer-unmatched"):
                self.auto_session_file = None
                return None, self.selection_source
            if tracker_source.startswith("renderer-waiting"):
                self.auto_session_file = None
                return None, self.selection_source

        latest = self.platform.detect_active_session(self.sessions_root)
        if latest is None:
            if self.auto_session_file is not None and not self.auto_session_file.exists():
                self.auto_session_file = None
                self.selection_source = tracker_source or "activity"
            return self.auto_session_file, self.selection_source

        if self.auto_session_file is None or not self.auto_session_file.exists():
            self.auto_session_file = latest
            self.selection_source = "activity"
            return self.auto_session_file, self.selection_source

        if latest == self.auto_session_file:
            self.selection_source = "activity"
            return self.auto_session_file, self.selection_source

        try:
            current_mtime = self.auto_session_file.stat().st_mtime
            latest_mtime = latest.stat().st_mtime
        except OSError:
            self.auto_session_file = latest
            self.selection_source = "activity"
            return self.auto_session_file, self.selection_source

        current_idle = time.time() - current_mtime
        tracker_requested_switch = self._has_unresolved_tracker_selection(tracker_source)
        if (
            latest_mtime > current_mtime
            and (
                tracker_requested_switch
                or current_idle >= self.auto_switch_idle_seconds
            )
        ):
            self.auto_session_file = latest
            self.selection_source = (
                f"{tracker_source}+activity"
                if tracker_requested_switch
                else "activity"
            )
        return self.auto_session_file, self.selection_source
