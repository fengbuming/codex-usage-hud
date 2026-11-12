"""Incremental, disk-backed projections owned only by selected-session parsing.

Closed task bodies and confirmed session rounds are immutable. Each published
view captures its length/count/rollback state, so a later append cannot mutate
an in-flight renderer snapshot. The live task still uses the authoritative
JSONL/SSE projection on every hydration.
"""

from dataclasses import replace
from datetime import timezone

from ..disk_sequence import DiskSequence


class _RoundCursor:
    def __init__(self, cache):
        self.model = ""
        self.last_cumulative_total = None
        self.seen_usage_keys = set()
        self.entries_factory = lambda: DiskSequence(cache=cache)
        self.entries = self.entries_factory()
        self.started_at = None
        self.count = 0
        self.next_record = None


class SelectedHistoryProjection:
    def __init__(self, parser, cache):
        self.parser = parser
        self.cache = cache
        self.pricing = getattr(parser.cost_estimator, 'pricing_fingerprint', None)
        self.provider = None
        self._reset()

    def _reset(self):
        self.rounds = DiskSequence(cache=self.cache)
        self.cursor = _RoundCursor(self.cache)
        self.closed = DiskSequence(cache=self.cache)
        self.closed_keys = []

    def valid_for(self, parser):
        return (parser is self.parser and self.pricing ==
                getattr(parser.cost_estimator, 'pricing_fingerprint', None))

    def session_rounds(self, parser, records, provider):
        if self.provider != provider:
            self._reset()
            self.provider = provider
        try:
            for row in parser.iter_token_rounds(records, None, _cursor=self.cursor):
                self.rounds.append(row)
        except BaseException:
            # A failed disk write/parse must never commit a partial cursor.
            self._reset()
            raise
        return self.rounds.freeze()

    def task_history(self, parser, records, parsed, latest):
        from .parser import datetime

        starts = parser.task_start_indices(records)
        rolled_back = parser.task_rolled_back_ordinals(records)
        keys = [(start, starts[index + 1], parsed.request.model)
                for index, start in enumerate(starts[:-1])]
        keys = [(*key, parsed.model_provider) for key in keys]
        if keys[:len(self.closed_keys)] != self.closed_keys:
            # Boundaries/fallback model changed; old published snapshots keep
            # their old storage while this owner rebuilds the closed prefix.
            self.closed = DiskSequence(cache=self.cache)
            self.closed_keys = []
        for index in range(len(self.closed_keys), len(keys)):
            item = parser.task_history_item(records, parsed, latest, starts, index + 1, rolled_back)
            self.closed.append(item)
            self.closed_keys.append(keys[index])
        current = DiskSequence(cache=self.cache)
        if starts:
            current.append(parser.task_history_item(records, parsed, latest, starts, len(starts), rolled_back))
        return _TaskHistoryView(self.closed.freeze(), current, len(starts), frozenset(rolled_back), datetime.now(timezone.utc))


class _TaskHistoryView(DiskSequence):
    def __init__(self, closed, current, count, rolled_back, observed_at):
        self.closed = closed
        self.current = current
        self.count = count
        self.rolled_back = rolled_back
        self.observed_at = observed_at

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        if isinstance(index, slice):
            from ..disk_sequence import _DiskSlice

            return _DiskSlice(self, range(len(self))[index])
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        item = self.closed[index] if index < len(self.closed) else self.current[0]
        slow = item.slow
        if index < len(self.closed) and slow.current_gap_active and item.last_event_time is not None:
            from .parser import seconds_between

            slow = replace(slow, current_gap=f"{seconds_between(item.last_event_time, self.observed_at):.1f}s")
        return replace(item, count=self.count, rolled_back=(index + 1) in self.rolled_back, slow=slow)
