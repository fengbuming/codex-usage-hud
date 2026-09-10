"""Event driven pricing sync scheduler; it never mutates prices itself."""
from __future__ import annotations

from threading import Event, Thread
import time
from typing import Callable, Mapping
from datetime import datetime


class PricingSyncScheduler:
    def __init__(self, load_config: Callable[[], object], check: Callable[[], Mapping[str, object]], publish: Callable[[Mapping[str, object]], None], *, clock: Callable[[], float] = time.time) -> None:
        self._load_config, self._check, self._publish, self._clock = load_config, check, publish, clock
        self._stop = Event(); self._wake = Event(); self._thread: Thread | None = None
        self._failure_count = 0; self._next_at = 0.0

    @property
    def next_run_at(self) -> float: return self._next_at

    def start(self) -> None:
        if self._thread and self._thread.is_alive(): return
        self._stop.clear(); self._thread = Thread(target=self._run, name="codex-pricing-sync", daemon=True); self._thread.start()

    def trigger(self) -> None: self._next_at = 0.0; self._wake.set()

    def close(self) -> None:
        self._stop.set(); self._wake.set()
        if self._thread: self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            config = self._load_config(); sync = getattr(config, "pricing_sync", {}) or {}
            if not bool(sync.get("enabled", True)):
                self._wake.wait(3600); self._wake.clear(); continue
            interval = max(1, int(sync.get("interval_hours", 4))) * 3600
            if self._next_at <= 0:
                last_checked = str(sync.get("last_checked_at") or "").replace("Z", "+00:00")
                try:
                    last_timestamp = datetime.fromisoformat(last_checked).timestamp()
                except (TypeError, ValueError):
                    last_timestamp = 0.0
                self._next_at = last_timestamp + interval if last_timestamp else self._clock()
            wait = max(0.0, self._next_at - self._clock())
            if self._wake.wait(wait): self._wake.clear(); continue
            if self._stop.is_set(): break
            try:
                result = dict(self._check()); self._failure_count = 0; self._next_at = self._clock() + interval
            except Exception as exc:
                self._failure_count += 1; delay = (3600, 14400, 86400)[min(self._failure_count - 1, 2)]
                self._next_at = self._clock() + delay; result = {"ok": False, "error": str(exc), "retry_at": self._next_at}
            self._publish(result)
