"""Bounded, event-driven progress logs for diagnosing stalled HUD refreshes."""

from __future__ import annotations

from collections.abc import Callable
import logging
import time


class RefreshDiagnosticTrace:
    """Sample progress without timers; always record a changed state key.

    Callers supply metadata only, never prompts, command text or credentials.
    A missing next phase identifies the operation that did not return.
    """

    def __init__(
        self,
        logger: logging.Logger,
        name: str,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        interval: float = 30.0,
    ) -> None:
        self._logger = logger
        self._name = name
        self._monotonic = monotonic
        self._interval = interval
        self._next_at = 0.0
        self._key: object = None
        self._sequence = 0
        self._started = 0.0
        self._active = False

    def begin(self, *, key: object = None, **fields: object) -> None:
        now = self._monotonic()
        self._sequence += 1
        self._active = now >= self._next_at or key != self._key
        if not self._active:
            return
        self._key = key
        self._next_at = now + self._interval
        self._started = now
        self.phase("begin", **fields)

    def phase(self, phase: str, **fields: object) -> None:
        if self._active:
            self._logger.info(
                "%s_progress seq=%s phase=%s elapsed_ms=%.1f fields=%s",
                self._name,
                self._sequence,
                phase,
                (self._monotonic() - self._started) * 1000.0,
                fields,
            )
