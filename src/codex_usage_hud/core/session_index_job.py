"""Progressive warm-up job for the session search index.

Implements the single-task warm-job state machine from the PRD
(``docs/PRD_SESSION_SEARCH_PROGRESSIVE_INDEX.md``):

- ``idle`` -> ``running`` on startup / extend / new session
- ``running`` -> ``attached`` when the session-management dialog subscribes
- ``running`` -> ``paused`` after the current batch commits and the cursor is
  persisted (committed batches stay searchable)
- ``running`` -> ``idle`` on range completion (coverage -> ``range_done``)
- exception -> ``error`` (already-built batches are kept, job is retryable)

The job is a single background worker: at most one warm job may run at any
time; entering session management merely attaches (subscribes) to the same
job's progress events.  State/cursor persistence lives in a small JSON file
next to the index database and is written *after* each committed batch, so the
persisted state never outruns the actually-built index (PRD §8).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
import time

from .session_search import (
    DEFAULT_RANGE,
    RANGE_OPTIONS,
    range_candidates,
    range_label,
    wider_range_key,
)

# Throughput baselines measured on a real corpus (PRD §5.3): a full 971-session
# / 1.69 GB build completes in roughly 71.5 s => ~23.6 MB/s.  These are only
# used for the initial estimate; live speed corrects the remaining time.
THROUGHPUT_MBPS = 23.6
THROUGHPUT_SESSIONS_PER_SEC = 13.6

# Progress refresh cadence (PRD §9.3): no more than 4 Hz.
PROGRESS_MAX_HZ = 4.0

# Batch size for the warm job's ``sync_batches`` call. Smaller batches make
# the progress callback fire more often so the UI shows finer, more frequent
# increments instead of a few large jumps (UX: avoids the "frozen" feel during
# a long single-session tokenisation). Commit cost per batch stays negligible
# versus the total build time.
WARM_BATCH_SIZE = 24

# Max process-pool parallelism while building (resource governance §11).
MAX_BUILD_WORKERS = max(1, min(8, (os.cpu_count() or 4) // 2))


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# Persisted state
# ---------------------------------------------------------------------------


@dataclass
class WarmJobState:
    """Persisted, auditable state for one warm job (no private content)."""

    selected_range: str = DEFAULT_RANGE
    completed_range: str = ""
    coverage_boundary: float = 0.0
    job_state: str = "idle"  # idle | running | paused | error
    cursor: str = ""
    built_count: int = 0
    total_count: int = 0
    phase: str = ""  # "" | scanning | indexing (transient work signal)
    started_at: float = 0.0
    updated_at: float = 0.0
    last_error: str = ""
    enabled: bool = True

    @classmethod
    def load(cls, path: Path) -> WarmJobState:
        try:
            payload = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return cls()
        try:
            data = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        state = cls()
        state.selected_range = str(
            data.get("selected_range", DEFAULT_RANGE)
        ).strip() or DEFAULT_RANGE
        state.completed_range = str(data.get("completed_range") or "").strip()
        try:
            boundary = float(data.get("coverage_boundary") or 0.0)
        except (TypeError, ValueError):
            boundary = 0.0
        state.coverage_boundary = boundary
        state.job_state = str(data.get("job_state") or "idle").strip()
        # A ``running`` job_state on disk can only mean the process died
        # mid-run: the live state machine always lands on idle/paused/error
        # before its worker exits. Load it as ``paused`` so the next start()
        # takes the resume branch (keeps selected_range and the covered set)
        # instead of treating the interrupted build as fresh (PRD §7.2).
        if state.job_state in ("running", "attached"):
            state.job_state = "paused"
        state.cursor = str(data.get("cursor") or "").strip()
        state.built_count = max(0, int(data.get("built_count") or 0))
        state.total_count = max(0, int(data.get("total_count") or 0))
        state.phase = str(data.get("phase") or "").strip()
        try:
            state.started_at = float(data.get("started_at") or 0.0)
            state.updated_at = float(data.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            pass
        state.last_error = str(data.get("last_error") or "").strip()
        raw_enabled = data.get("enabled", True)
        if isinstance(raw_enabled, str):
            state.enabled = raw_enabled.strip().casefold() not in {
                "0",
                "false",
                "off",
                "disabled",
                "no",
            }
        else:
            state.enabled = raw_enabled is not False and raw_enabled != 0
        return state

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "selected_range": self.selected_range,
            "completed_range": self.completed_range,
            "coverage_boundary": self.coverage_boundary,
            "job_state": self.job_state,
            "cursor": self.cursor,
            "built_count": self.built_count,
            "total_count": self.total_count,
            "phase": self.phase,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "last_error": self.last_error,
            "enabled": bool(self.enabled),
        }
        # Atomic-ish write: temp file then rename so a crash can never leave a
        # half-written state that looks like a completed job.
        temp_path = path.with_name(path.name + ".tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temp_path, path)
        except OSError:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Time budget helpers
# ---------------------------------------------------------------------------


def estimated_remaining_seconds(
    pending_bytes: int | None,
    pending_sessions: int,
    *,
    effective_mbps: float = THROUGHPUT_MBPS,
    effective_sessions_per_sec: float = THROUGHPUT_SESSIONS_PER_SEC,
) -> tuple[float, bool]:
    """Return (seconds, reliable) for one remaining workload.

    ``reliable`` is False when we have no byte estimate (PRD §12: degrade to
    "sessions only", no fake precision).
    """
    seconds = 0.0
    if pending_sessions > 0 and effective_sessions_per_sec > 0:
        seconds = max(seconds, pending_sessions / effective_sessions_per_sec)
    if pending_bytes is not None and pending_bytes > 0 and effective_mbps > 0:
        seconds = max(seconds, pending_bytes / (effective_mbps * 1_048_576.0))
    return round(seconds, 1), pending_bytes is not None and pending_bytes > 0


def estimate_range_bytes(entries: Sequence[object]) -> int | None:
    """Approximate the byte volume of a candidate list for the time budget.

    Returns None when no file size can be read so the caller degrades to
    session-count-only estimates.
    """
    total = 0
    seen = 0
    for entry in entries:
        paths = entry[1] if len(entry) > 1 else ()
        if not isinstance(paths, Sequence):
            continue
        for path in paths:
            try:
                total += path.stat().st_size
                seen += 1
            except (OSError, ValueError):
                continue
    return total if seen else None


# ---------------------------------------------------------------------------
# Warm job
# ---------------------------------------------------------------------------


@dataclass
class WarmJobSnapshot:
    """Immutable point-in-time status of the warm job (safe to publish)."""

    coverage: str = "empty"  # empty | partial(range) | range_done(range) | full
    coverage_boundary: float = 0.0
    job_state: str = "idle"  # idle | running | attached | paused | error
    built_count: int = 0
    total_count: int = 0
    phase: str = ""  # "" | scanning | indexing
    started_at: float = 0.0
    estimated_remaining_sec: float = 0.0
    selected_range: str = DEFAULT_RANGE
    can_extend: bool = False
    error: str = ""
    enabled: bool = True
    disk_bytes: int = 0


class SessionIndexWarmJob:
    """Single background task that progressively builds the search index.

    Owned by the HUD runtime; never touches the renderer directly.  The
    session-management UI can attach (subscribe) to progress via ``attach``,
    and detach (``cancel_ui``) without stopping the job.
    """

    def __init__(
        self,
        search_index: object,
        *,
        state_path: Path | str,
        clock: Callable[[], float] = time.time,
        progress_callback: Callable[[WarmJobSnapshot], None] | None = None,
        preload_grace_seconds: float = 0.0,
        before_clear: Callable[[], bool] | None = None,
        after_clear: Callable[[], None] | None = None,
    ) -> None:
        self._search_index = search_index
        # The production wiring passes the cleanup manager, which owns the
        # candidate source (``search_index_entries``) and the search database
        # (``_search_index``); keep both so the job can resolve capabilities
        # from either surface (PRD §4.2 / D3 incremental extension).
        self._index_core = getattr(search_index, "_search_index", None)
        self._state_path = Path(state_path)
        self._clock = clock
        self._progress_callback = progress_callback
        self._before_clear = before_clear
        self._after_clear = after_clear
        # Seconds the finalize thread waits before starting its GIL/IO-heavy
        # work (snapshot unpickle, scan-index build). Production wiring sets
        # this past the cold-start interactive window so the authoritative
        # HUD payload push is never starved (measured: ~3 s unpickle for a
        # 117 MB pickle + ~8.5 s doc-table scan on a 2.89 GB database).
        self._preload_grace_seconds = max(0.0, float(preload_grace_seconds))
        self._lock = threading.RLock()
        self._state = WarmJobState.load(self._state_path)
        self._attached = False
        self._requested_pause = threading.Event()
        self._worker: threading.Thread | None = None
        self._closed = False
        self._last_progress_emitted = 0.0
        self._run_range_key: str | None = None
        self._range_reconciled = False
        # Set after a successful ``sync_batches`` build so _finish_range knows
        # to defer the heavy snapshot pickle off-thread (see _finalize_off_thread).
        self._pending_snapshot_write = False
        self._lifecycle_generation = 0
        self._clearing = False
        self._finalize_threads: set[threading.Thread] = set()

    # ------------------------------------------------------------------
    # status surface
    # ------------------------------------------------------------------

    def status(self) -> dict[str, object]:
        with self._lock:
            return self._snapshot_dict_locked()

    def snapshot(self) -> WarmJobSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def is_attached(self) -> bool:
        """Return whether a renderer UI is subscribed to progress events."""
        with self._lock:
            return self._attached

    def _snapshot_dict_locked(self) -> dict[str, object]:
        snapshot = self._snapshot_locked()
        return {
            "coverage": snapshot.coverage,
            "coverageBoundary": snapshot.coverage_boundary,
            "jobState": snapshot.job_state,
            "builtCount": snapshot.built_count,
            "totalCount": snapshot.total_count,
            "phase": snapshot.phase,
            # Frontend ``formatSessionCleanupElapsed`` expects an epoch in
            # MILLISECONDS (it subtracts from ``Date.now()``); the internal
            # ``started_at`` is seconds (``time.time``), so convert at the
            # boundary. Internal ETA math keeps using seconds untouched.
            "startedAt": snapshot.started_at * 1000.0,
            "estimatedRemainingSec": snapshot.estimated_remaining_sec,
            "selectedRange": snapshot.selected_range,
            "canExtend": snapshot.can_extend,
            "error": snapshot.error,
            "enabled": snapshot.enabled,
            "diskBytes": snapshot.disk_bytes,
        }

    def _snapshot_locked(self) -> WarmJobSnapshot:
        state = self._state
        return WarmJobSnapshot(
            coverage=self._coverage_locked(),
            coverage_boundary=state.coverage_boundary,
            job_state=self._job_state_locked(),
            built_count=state.built_count,
            total_count=state.total_count,
            phase=state.phase,
            started_at=state.started_at,
            estimated_remaining_sec=self._estimated_remaining_locked(),
            selected_range=state.selected_range,
            can_extend=state.selected_range != "all",
            error=state.last_error,
            enabled=bool(state.enabled),
            disk_bytes=self._disk_bytes_locked(),
        )

    def _disk_bytes_locked(self) -> int:
        disk_usage = self._capability("disk_usage_bytes")
        if not callable(disk_usage):
            return 0
        try:
            return max(0, int(disk_usage()))
        except (OSError, TypeError, ValueError):
            return 0

    def _job_state_locked(self) -> str:
        state = self._state
        if state.job_state in ("running", "attached"):
            return "attached" if self._attached else "running"
        return state.job_state

    def _coverage_locked(self) -> str:
        state = self._state
        built = state.built_count
        total = state.total_count
        if state.selected_range == "all":
            return (
                "full"
                if built > 0 and built >= total
                else "partial(all)"
            )
        if (
            state.completed_range == state.selected_range
            and built > 0
            and built >= total
        ):
            return f"range_done({state.selected_range})"
        if built > 0 or total > 0:
            return f"partial({state.selected_range})"
        return "empty"

    def _estimated_remaining_locked(self) -> float:
        state = self._state
        if state.job_state not in {"running", "attached", "paused"}:
            return 0.0
        rate = self._effective_sessions_per_sec_locked()
        pending = max(0, state.total_count - state.built_count)
        if pending <= 0 or rate <= 0:
            return 0.0
        return round(pending / rate, 1)

    def _effective_sessions_per_sec_locked(self) -> float:
        state = self._state
        elapsed = max(0.0, self._clock() - state.started_at)
        if elapsed <= 0 or state.built_count <= 0:
            return 0.0
        return max(
            0.0,
            state.built_count / (elapsed * 1.15),
        ) or THROUGHPUT_SESSIONS_PER_SEC

    # ------------------------------------------------------------------
    # control API
    # ------------------------------------------------------------------

    def start(self, range_key: str = DEFAULT_RANGE) -> bool:
        """Begin (or resume) a warm job for ``range_key``.

        Idempotent: a running/attached job just returns True with its state
        intact; a paused job resumes from its cursor; a fresh coverage starts
        a new bucket.  Returns True when the job is running.
        """
        with self._lock:
            if self._closed or self._clearing or not self._state.enabled:
                return False
            # Mark the transient work phase immediately so the UI can show
            # "scanning" before the first real progress frame lands (the
            # worker re-enumerates candidate sessions, which can take several
            # seconds on a large corpus and would otherwise look frozen).
            self._state.phase = "scanning"
            # The coverage window never narrows (PRD §5.1: an extension is
            # remembered as the new boundary): a start() asking for a range
            # narrower than the already-selected one is satisfied by the wider
            # selection, and its pass degenerates to a fingerprint diff.
            key = wider_range_key(range_key, self._state.selected_range)
            if self._worker is not None and self._worker.is_alive():
                # Already running/attached: reuse it unchanged.
                self._state.selected_range = key
                self._state.completed_range = ""
                self._state.job_state = "running"
                self._state.updated_at = self._clock()
                self._maybe_persist_locked()
                # A resume arriving while the previous worker is still
                # unwinding a pause must re-arm it (otherwise the pending
                # pause event would stop the next batch and flip back to
                # ``paused`` right after the UI confirmed the resume).
                self._requested_pause.clear()
                self._publish_locked(force=True)
                return True
            if self._state.job_state == "paused":
                self._state.job_state = "running"
                self._state.updated_at = self._clock()
                self._maybe_persist_locked()
            else:
                self._state.selected_range = key
                self._state.job_state = "running"
                self._state.built_count = 0
                self._state.total_count = 0
                self._state.cursor = ""
                self._state.last_error = ""
                self._state.started_at = self._clock()
                self._state.updated_at = self._clock()
                self._maybe_persist_locked()
            self._requested_pause.clear()
            self._attached = False
            self._spawn_worker()
            # Publish the range/state change now so the live ``sessionIndex``
            # channel is never one frame behind the command ack. Otherwise a
            # domain push landing between the ack and the worker's first bucket
            # publish would show the *previous* range as current.
            self._publish_locked(force=True)
            return True

    def _trim_index_to_range(self, range_key: str) -> None:
        """Reconcile documents left by pre-progressive full indexing."""
        entries_fn = self._capability("search_index_entries")
        remove_fn = self._capability("remove_missing")
        if not callable(entries_fn) or not callable(remove_fn):
            return
        try:
            entries = list(entries_fn())
            keep = {
                str(entry[0] or "").strip()
                for entry in range_candidates(
                    entries, range_key, covered_ids=frozenset()
                )
                if str(entry[0] or "").strip()
            }
            remove_fn(keep)
        except Exception:
            # Scope reconciliation is a migration aid. Never block the warm
            # worker if an older index cannot be opened or rewritten.
            return

    def extend(self, range_key: str) -> bool:
        """Extend the coverage window; only appends older sessions (D3)."""
        with self._lock:
            if self._closed or self._clearing or not self._state.enabled:
                return False
            key = str(range_key or "").strip().casefold()
            if key not in RANGE_OPTIONS:
                return False
            # A narrower extend is already satisfied by the selected wider
            # range; accepting it as a no-op keeps the window monotonic
            # instead of silently shrinking the coverage (PRD §5.1).
            if RANGE_OPTIONS.index(key) < RANGE_OPTIONS.index(
                self._state.selected_range
                if self._state.selected_range in RANGE_OPTIONS
                else DEFAULT_RANGE
            ):
                return True
            if self._state.job_state in ("running", "attached") and self._worker is not None and self._worker.is_alive():
                if self._state.selected_range == key:
                    return True
                # Range changed while running: let the current pass finish
                # then the next start() re-buckets; update the label now so
                # the UI is honest about the selected target.
                self._state.selected_range = key
                self._state.phase = "scanning"
                self._state.updated_at = self._clock()
                self._maybe_persist_locked()
                self._publish_locked(force=True)
                return True
            self._state.selected_range = key
            self._state.job_state = "running"
            self._state.phase = "scanning"
            self._state.cursor = ""
            self._state.last_error = ""
            self._state.started_at = self._clock()
            self._state.updated_at = self._clock()
            self._maybe_persist_locked()
            if self._worker is not None and self._worker.is_alive():
                self._publish_locked(force=True)
                return True
            self._spawn_worker()
            self._publish_locked(force=True)
            return True

    def pause(self) -> bool:
        """Pause the current job; committed batches remain searchable."""
        with self._lock:
            if self._state.job_state not in ("running", "attached"):
                return False
            self._requested_pause.set()
            # Keep the UI subscription: the worker still has to announce the
            # ``paused`` transition, and a detached job publishes no event, so
            # clearing the flag here would leave the panel stuck on "索引中".
            return True

    def resume(self) -> bool:
        return self.start(self._state.selected_range)

    def cancel_ui(self) -> bool:
        """Detach the UI subscription, keep building in the background."""
        with self._lock:
            self._attached = False
            return True

    def attach(self) -> bool:
        """Subscribe the UI; does not create any new task."""
        with self._lock:
            if self._state.job_state in ("running", "attached", "paused"):
                self._attached = True
                return True
            return False

    def detach(self) -> bool:
        return self.cancel_ui()

    def set_enabled(self, enabled: bool, range_key: str = "") -> bool:
        """Enable or disable automatic index maintenance.

        Disabling preserves already-built documents so existing search results
        remain available.  A running build observes the pause flag at its next
        cancellable batch boundary; enabling starts/resumes the selected range.
        """
        desired = bool(enabled)
        requested_range = str(range_key or "").strip().casefold()
        if requested_range not in RANGE_OPTIONS:
            requested_range = ""
        with self._lock:
            if self._closed or self._clearing:
                return False
            self._state.enabled = desired
            self._state.updated_at = self._clock()
            if not desired:
                self._requested_pause.set()
                if self._worker is None or not self._worker.is_alive():
                    self._state.job_state = "idle"
                    self._state.phase = ""
            self._maybe_persist_locked()
            self._publish_locked(force=True)
            selected_range = requested_range or self._state.selected_range
        if desired:
            return self.start(selected_range)
        return True

    def clear_index(self) -> dict[str, object]:
        """Safely clear only HUD-owned search-index artifacts."""
        with self._lock:
            if self._closed or self._clearing:
                return {"accepted": False, "error": "busy"}
            self._clearing = True
            self._lifecycle_generation += 1
            self._requested_pause.set()
            worker = self._worker
        prepared = False
        try:
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=15.0)
            if worker is not None and worker.is_alive():
                return {"accepted": False, "error": "index_job_busy"}
            with self._lock:
                finalizers = tuple(self._finalize_threads)
            for finalizer in finalizers:
                if finalizer is not threading.current_thread():
                    finalizer.join(timeout=15.0)
            if any(finalizer.is_alive() for finalizer in finalizers):
                return {"accepted": False, "error": "index_finalize_busy"}
            before_clear = self._before_clear
            prepared = callable(before_clear)
            if prepared:
                try:
                    ready = bool(before_clear())
                except Exception:
                    ready = False
                if not ready:
                    return {"accepted": False, "error": "index_job_busy"}
            clear_fn = self._capability("clear_index")
            if not callable(clear_fn):
                return {"accepted": False, "error": "clear_unavailable"}
            result = clear_fn()
            cleared = result if isinstance(result, Mapping) else {}
            reset_search_state = self._capability("reset_search_index_state")
            if callable(reset_search_state):
                reset_search_state()
            with self._lock:
                self._state.selected_range = DEFAULT_RANGE
                self._state.completed_range = ""
                self._state.coverage_boundary = 0.0
                self._state.job_state = "idle"
                self._state.cursor = ""
                self._state.built_count = 0
                self._state.total_count = 0
                self._state.phase = ""
                self._state.started_at = 0.0
                self._state.updated_at = self._clock()
                self._state.last_error = ""
                # A successful clear is a durable opt-out from automatic
                # rebuilding.  Otherwise the startup-prime thread or the next
                # session filesystem delta can call start() immediately after
                # the artifacts are unlinked and silently recreate the whole
                # index.  The user can explicitly re-enable indexing and pick
                # a new range from the Renderer controls.
                self._state.enabled = False
                self._range_reconciled = False
                self._pending_snapshot_write = False
                self._last_progress_emitted = 0.0
                self._requested_pause.clear()
                self._maybe_persist_locked()
                self._publish_locked(force=True)
            return {
                "accepted": True,
                "error": "",
                "clearedBytes": max(0, int(cleared.get("clearedBytes") or 0)),
                "removedFiles": max(0, int(cleared.get("removedFiles") or 0)),
            }
        except (OSError, TypeError, ValueError) as exc:
            return {"accepted": False, "error": str(exc) or type(exc).__name__}
        finally:
            with self._lock:
                self._clearing = False
                self._requested_pause.clear()
            if prepared:
                after_clear = self._after_clear
                if callable(after_clear):
                    try:
                        after_clear()
                    except Exception:
                        pass

    def clear(self) -> bool:
        return bool(self.clear_index().get("accepted"))

    def control(self, command: Mapping[str, object]) -> dict[str, object]:
        """Handle one ``sessionIndexControl`` payload (CDP and HTTP share this).

        Returns the same status shape as :meth:`status` plus ``accepted`` and
        ``requestId``, keeping the CDP and HTTP fallback contracts identical
        (PRD §9.4).
        """
        control_action = str(command.get("control") or "").strip().casefold()
        valid = {
            "start",
            "extend",
            "pause",
            "resume",
            "cancel_ui",
            "enable",
            "disable",
            "clear",
        }
        request_id = str(command.get("requestId") or command.get("id") or "")
        if control_action not in valid:
            payload = dict(self.status())
            payload["accepted"] = False
            payload["error"] = "unknown_action"
            payload["requestId"] = request_id
            return payload
        range_key = str(command.get("range") or "").strip().casefold()
        try:
            if control_action == "start":
                accepted = self.start(range_key)
            elif control_action == "extend":
                accepted = self.extend(range_key)
            elif control_action == "pause":
                accepted = self.pause()
            elif control_action == "resume":
                accepted = self.resume()
            elif control_action == "enable":
                accepted = self.set_enabled(True, range_key)
            elif control_action == "disable":
                accepted = self.set_enabled(False)
            elif control_action == "clear":
                clear_result = self.clear_index()
                accepted = bool(clear_result.get("accepted"))
            else:
                accepted = self.cancel_ui()
        except Exception as exc:
            payload = dict(self.status())
            payload["accepted"] = False
            payload["error"] = str(exc) or type(exc).__name__
            payload["requestId"] = request_id
            return payload
        payload = dict(self.status())
        payload["accepted"] = bool(accepted)
        payload["error"] = ""
        payload["requestId"] = request_id
        if control_action == "clear":
            payload.update(
                {
                    key: value
                    for key, value in clear_result.items()
                    if key not in {"accepted", "error"}
                }
            )
            payload["error"] = str(clear_result.get("error") or "")
        return payload

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._requested_pause.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _spawn_worker(self) -> None:
        thread = threading.Thread(
            target=self._run,
            name="session-index-warm-job",
            daemon=True,
        )
        self._worker = thread
        thread.start()

    def _maybe_persist_locked(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._state.dump(self._state_path)
        except OSError:
            pass

    def _publish_locked(self, *, force: bool = False) -> None:
        if not callable(self._progress_callback) or self._closed:
            return
        snapshot = self._snapshot_locked()
        now = self._clock()
        # Never throttle terminal state transitions. A very small corpus can
        # finish inside one throttle window; suppressing its final idle/error
        # event would leave an attached Renderer showing stale "indexing" UI
        # until the next explicit status request. A control-driven change
        # (start/extend) is forced for the same reason: it must reach the live
        # channel immediately, but it still opens a fresh throttle window so
        # the 4 Hz bound on streaming progress is preserved.
        terminal = snapshot.job_state not in {"running", "attached"}
        if (
            not force
            and not terminal
            and now - self._last_progress_emitted < 1.0 / PROGRESS_MAX_HZ
        ):
            return
        self._last_progress_emitted = now
        try:
            self._progress_callback(snapshot)
        except Exception:
            pass

    def _capability(self, name: str) -> Callable | None:
        """Resolve one warm-job capability from the job's index surface.

        Candidates live on the owning manager, while batch sync and the
        covered-session set live on the underlying ``SessionSearchIndex``;
        working against the manager directly means both are reachable here.
        """
        fn = getattr(self._search_index, name, None)
        if callable(fn):
            return fn
        core = self._index_core
        if core is not None:
            fn = getattr(core, name, None)
        return fn if callable(fn) else None

    def _should_stop(self) -> bool:
        if self._closed or self._requested_pause.is_set():
            return True
        with self._lock:
            return (
                not self._state.enabled
                or self._state.job_state not in ("running", "attached")
            )

    def _run(self) -> None:
        try:
            self._run_loop()
        except Exception as exc:
            with self._lock:
                self._state.job_state = "error"
                self._state.last_error = str(exc) or type(exc).__name__
                self._state.updated_at = self._clock()
                self._maybe_persist_locked()
                self._publish_locked()
        finally:
            with self._lock:
                self._worker = None

    def _run_loop(self) -> None:
        """Run coverage passes until the selected range is fully built.

        Each pass buckets the *current* ``selected_range``.  If the range was
        extended while a pass was running, the next pass re-buckets so the
        extension is applied incrementally instead of being silently dropped
        (PRD D3) -- and coverage never claims a larger range than what was
        actually built (PRD §12, no fake completion).
        """
        while not self._closed:
            with self._lock:
                self._run_range_key = self._state.selected_range
            self._run_once()
            with self._lock:
                if self._state.job_state in ("paused", "error"):
                    return
                if not self._state.enabled:
                    return
                if (
                    self._state.job_state == "idle"
                    and self._state.selected_range == self._run_range_key
                ):
                    return

    def _run_once(self) -> None:
        # Re-affirm the scan phase before the (potentially slow) candidate
        # enumeration so the UI shows active work instead of a frozen 100%
        # during the silent window. ``start``/``extend`` already force-published
        # ``scanning``; we only re-set it here defensively without an extra
        # publish so the 4 Hz streaming throttle is preserved.
        self._pending_snapshot_write = False
        with self._lock:
            self._state.phase = "scanning"
        if self._closed or self._requested_pause.is_set() or not self._state.enabled:
            self._persist_paused()
            return
        entries_fn = self._capability("search_index_entries")
        if not callable(entries_fn):
            self._fail("search_index_entries unavailable")
            return
        candidates = entries_fn()
        if not candidates:
            self._finish_range(self._state.selected_range)
            return

        with self._lock:
            range_key = self._state.selected_range
        if not self._range_reconciled:
            self._range_reconciled = True
            # Scope reconciliation is a one-time migration aid for indexes left
            # behind by pre-progressive full builds. Once the warm job itself
            # has completed a range (``completed_range`` set), the index is
            # warm-job-owned: trimming here would delete legitimately built
            # documents that simply aged out of the current window, so the
            # reconciliation only runs while coverage was never established
            # (PRD D3: extension is incremental; committed batches are kept).
            if not self._state.completed_range:
                self._trim_index_to_range(range_key)
        covered_fn = self._capability("indexed_session_ids")
        covered_ids = frozenset(covered_fn()) if callable(covered_fn) else frozenset()
        stale_fn = self._capability("stale_session_ids")
        if callable(stale_fn):
            try:
                # A changed rollout path (for example .jsonl -> .jsonl.zst)
                # must be rebuilt even when its session id is already present.
                covered_ids -= frozenset(stale_fn(candidates))
            except Exception:
                # Staleness is an optimisation; sync_batches still performs
                # its own fingerprint check for the entries it receives.
                pass
        pass_range = range_key
        target_bucket = range_candidates(
            list(candidates),
            range_key,
            covered_ids=frozenset(),
        )
        bucket = range_candidates(
            list(candidates),
            range_key,
            covered_ids=covered_ids,
        )
        target_total = len(target_bucket)
        covered_target = max(0, target_total - len(bucket))
        if not bucket:
            self._finish_range(
                pass_range,
                target_total=target_total,
                target_built=covered_target,
            )
            return
        total = len(bucket)

        with self._lock:
            self._state.total_count = target_total
            self._state.built_count = covered_target
            # NOTE: ``started_at`` is set at ``start()``/``extend()`` (the user's
            # click) and intentionally NOT reset here, so the UI "已用时" timer is
            # monotonic from the click instead of jumping back to 0 when indexing
            # begins. It is only consumed by the ETA throughput rate, where the
            # brief scan window is negligible (<12%) and biases the estimate
            # slightly conservative.
            self._state.updated_at = self._clock()
            self._state.job_state = "running"
            self._maybe_persist_locked()
        self._publish_locked()

        sync_batches = self._capability("sync_batches")
        if not callable(sync_batches):
            self._fail("sync_batches unavailable")
            return

        def report(processed: int, _total: int, _indexed: int) -> None:
            with self._lock:
                # First real indexing work flips the phase from "scanning" to
                # "indexing" so the UI reflects the transition into measured
                # progress.
                self._state.phase = "indexing"
                self._state.built_count = min(
                    target_total,
                    covered_target + max(0, int(processed)),
                )
                self._state.total_count = target_total
                self._state.updated_at = self._clock()
                self._publish_locked()

        try:
            sync_batches(
                bucket,
                total=total,
                batch_size=WARM_BATCH_SIZE,
                progress_callback=report,
                cancelled=self._should_stop,
                # Defer the ~200 MB pickle (sync_batches skips it); _finish_range
                # publishes "done" first, then defers the serialisation off-thread
                # so the UI is never frozen at 100%.
                write_snapshot=False,
            )
            # A real build happened: the resident snapshot needs rewriting.
            self._pending_snapshot_write = True
        except Exception as exc:
            if self._should_stop():
                pass
            else:
                self._fail(str(exc) or type(exc).__name__)
                return

        if self._should_stop():
            self._persist_paused()
            return
        self._finish_range(
            pass_range,
            target_total=target_total,
            target_built=target_total,
        )

    def _persist_paused(self) -> None:
        with self._lock:
            self._state.job_state = "paused"
            self._state.cursor = str(self._state.built_count)
            self._state.updated_at = self._clock()
            self._maybe_persist_locked()
            self._publish_locked()

    def _finish_range(
        self,
        pass_range: str,
        *,
        target_total: int | None = None,
        target_built: int | None = None,
    ) -> None:
        covered_count: int | None = None
        covered_fn = self._capability("indexed_session_ids")
        if callable(covered_fn):
            try:
                covered_count = len(covered_fn())
            except Exception:
                covered_count = None
        with self._lock:
            self._state.job_state = "idle"
            self._state.phase = ""
            if target_total is not None:
                self._state.total_count = max(0, int(target_total))
                self._state.built_count = min(
                    self._state.total_count,
                    max(0, int(target_built if target_built is not None else target_total)),
                )
            elif covered_count is not None and covered_count > 0:
                self._state.built_count = covered_count
                self._state.total_count = max(self._state.total_count, covered_count)
            else:
                self._state.built_count = self._state.total_count
            self._state.completed_range = str(pass_range or "").strip().casefold()
            self._state.cursor = ""
            self._state.coverage_boundary = self._clock()
            self._state.updated_at = self._clock()
            self._maybe_persist_locked()
            self._publish_locked()
            pending_write = self._pending_snapshot_write
            self._pending_snapshot_write = False
            lifecycle_generation = self._lifecycle_generation
        # Hand the heavy post-build work to a background thread ONLY AFTER the
        # "done" state is published.  Pickling the ~200 MB snapshot (and, on a
        # cold cache, deserialising it via _warm_memory) is GIL-heavy; if it ran
        # inline the renderer main thread could not push the final idle state
        # over CDP and the UI would freeze at 100% for seconds.  Yield the GIL
        # briefly first so the main thread gets a slice to deliver "done" before
        # the background serialisation starts saturating the GIL.
        time.sleep(0.05)
        finalizer = threading.Thread(
            target=self._finalize_off_thread,
            args=(pending_write, lifecycle_generation),
            name="session-index-finalize",
            daemon=True,
        )
        with self._lock:
            self._finalize_threads.add(finalizer)
        finalizer.start()

    def _finalize_off_thread(self, pending_write: bool, lifecycle_generation: int) -> None:
        """Best-effort post-build finalisation, off the worker thread.

        Rewrites the resident pickle snapshot (only when a build mutated it)
        and pre-loads it into the search index.  Both are GIL-heavy and must
        not block the renderer main thread's delivery of the "done" state
        (PRD §14.1 / §12).  The whole routine additionally waits out
        ``preload_grace_seconds`` first: at cold start the interactive
        startup (startup bubble steps, first full HUD payload) is still in
        flight, and the unpickle/scan work measured here (seconds of GIL
        saturation plus multi-GB reads) delayed that payload by 10-20 s in
        practice, leaving zeroed HUD panels behind the bubble until it
        landed (PRD §11: the warm job yields while the UI is busy).
        """
        try:
            self._sleep_past_interactive_startup(lifecycle_generation)
            with self._lock:
                current = (
                    not self._closed
                    and self._lifecycle_generation == lifecycle_generation
                )
            if not current:
                return
            # One full-table read on legacy multi-GB databases, then milliseconds
            # forever after; safe on this already-background thread only.
            ensure_fn = self._capability("ensure_scan_index")
            if callable(ensure_fn):
                try:
                    ensure_fn()
                except Exception:
                    pass
            if pending_write:
                with self._lock:
                    if self._lifecycle_generation != lifecycle_generation:
                        return
                self._write_snapshot_best_effort()
            with self._lock:
                if self._lifecycle_generation != lifecycle_generation:
                    return
            result = self._warm_memory()
            try:
                reconciled = int(result.get("reconciled") or 0)
            except (AttributeError, TypeError, ValueError):
                reconciled = 0
            if reconciled > 0:
                # The load repaired stale/missing snapshot documents from SQLite
                # rows; rewrite the pickle so the repair is not repeated on every
                # restart.
                self._write_snapshot_best_effort()
        finally:
            with self._lock:
                if (
                    not self._closed
                    and self._lifecycle_generation == lifecycle_generation
                ):
                    # Snapshot serialization happens after the terminal build
                    # event; publish once more so the UI's diskBytes includes
                    # the newly written .memory artifact.
                    self._publish_locked(force=True)
                self._finalize_threads.discard(threading.current_thread())

    def _sleep_past_interactive_startup(
        self,
        lifecycle_generation: int | None = None,
    ) -> None:
        """Wait out the grace window in small, clear-interruptible slices."""
        remaining = self._preload_grace_seconds
        while remaining > 0.0 and not self._closed:
            if lifecycle_generation is not None:
                with self._lock:
                    if self._lifecycle_generation != lifecycle_generation:
                        return
            step = min(0.25, remaining)
            time.sleep(step)
            remaining -= step

    def _write_snapshot_best_effort(self) -> None:
        write_fn = self._capability("write_snapshot")
        if not callable(write_fn):
            return
        try:
            write_fn(force=True)
        except Exception:
            pass

    def _warm_memory(self) -> dict[str, object]:
        """Pre-load the resident search snapshot in the background.

        The resident index is a pickled snapshot (``.memory``, ~200 MB on a
        full corpus) whose first deserialisation costs on the order of 20 s on
        a cold page cache.  Without this step that cost lands on the user's
        first search (PRD §14.1 "seconds to first result").  The warm job is
        already the startup-time background task, so it is the natural place
        to pull that cost off the interactive path: ``load()`` populates the
        in-process ``SessionSearchIndex`` instance that the renderer searches
        against, and its ``_memory_loaded`` flag makes this idempotent across
        the whole process.

        This must never block the worker's state transitions or fail the job:
        a missing ``load`` capability, an already-loaded index, or any error
        is simply skipped (PRD §12: warm-up is best-effort).  Returns the
        ``load()`` result dict (empty when skipped) so the caller can decide
        whether a convergence snapshot write is warranted.
        """
        load_fn = self._capability("load")
        if not callable(load_fn):
            return {}
        try:
            result = load_fn()
        except Exception:
            # Best-effort only; the indexer already surfaced any real error.
            return {}
        return result if isinstance(result, dict) else {}

    def _fail(self, message: str) -> None:
        with self._lock:
            self._state.job_state = "error"
            self._state.phase = ""
            self._state.last_error = str(message) or "internal error"
            self._state.updated_at = self._clock()
            self._maybe_persist_locked()
            self._publish_locked()


__all__ = [
    "PROGRESS_MAX_HZ",
    "THROUGHPUT_MBPS",
    "THROUGHPUT_SESSIONS_PER_SEC",
    "WARM_BATCH_SIZE",
    "WarmJobSnapshot",
    "WarmJobState",
    "SessionIndexWarmJob",
    "estimate_range_bytes",
    "estimated_remaining_seconds",
    "range_label",
]
