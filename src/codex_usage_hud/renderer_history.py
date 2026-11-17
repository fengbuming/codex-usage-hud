"""Bounded session ownership and on-demand renderer history artifacts.

Only small descriptors stay resident. Full copy strings and projected historical
tasks live in temporary files and are fetched on interaction. Recent generations
stay valid for an in-flight renderer request; closing the client removes them.
"""

from collections import OrderedDict
import copy
from dataclasses import dataclass
import hashlib
import json
import secrets
import tempfile
from threading import RLock
from urllib.parse import urlencode


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class _Artifact:
    token: str
    kind: str
    file: object = None
    factory: object = None
    dependencies: tuple = ()

    def close(self):
        if self.file is not None:
            self.file.close()
        self.factory = None


class RendererHistoryStore:
    def __init__(self, *, max_sessions=4):
        self.url = ""
        self.max_sessions = max(1, int(max_sessions))
        self._sessions = OrderedDict()
        self._tokens = {}
        self._lock = RLock()

    def begin(self, snapshot):
        with self._lock:
            session = str(snapshot.session_id)
            scope = self._sessions.get(session)
            if scope is None:
                scope = _SessionScope(self, session)
                self._sessions[session] = scope
            self._sessions.move_to_end(session)
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)[1].close()
            scope.generation += 1
            scope.pending = set()
            return scope

    def read(self, token):
        with self._lock:
            entry = self._tokens.get(token)
            if entry is None:
                return None
            if entry.file is None:
                value = entry.factory()
                _write(entry, value)
                entry.factory = None
            entry.file.seek(0)
            return {"ref": token, "kind": entry.kind, "value": json.load(entry.file)}

    def close(self):
        with self._lock:
            for scope in self._sessions.values():
                scope.close()
            self._sessions.clear()


def _write(entry, value):
    file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", prefix="codex-hud-renderer-detail-")
    try:
        json.dump(value, file, ensure_ascii=False, separators=(",", ":"))
        file.flush()
    except BaseException:
        file.close()
        raise
    entry.file = file
    dependencies = []
    def collect(item):
        if isinstance(item, dict):
            if item.get("_hudHistoryRef"):
                dependencies.append(item["_hudHistoryRef"])
            else:
                for child in item.values():
                    collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)
    collect(value)
    entry.dependencies = tuple(dependencies)


class _SessionScope:
    def __init__(self, owner, session):
        self.owner = owner
        self.session = session
        self.entries = {}
        self.generation = 0
        self.acknowledged = set()
        self.pending = set()

    def reference(self, key, *, kind, value=None, factory=None):
        with self.owner._lock:
            key = (kind, key)
            entry = self.entries.get(key)
            if entry is None:
                entry = _Artifact(secrets.token_urlsafe(24), kind, factory=factory)
                if factory is None:
                    _write(entry, value)
                self.entries[key] = entry
                self.owner._tokens[entry.token] = entry
            self.pending.add(entry.token)
            separator = "&" if "?" in self.owner.url else "?"
            return {"_hudHistoryRef": entry.token, "kind": kind,
                    "url": self.owner.url + separator + urlencode({"ref": entry.token})}

    def externalize(self, value, *, text=False):
        if text and isinstance(value, str) and len(value) > 2048:
            return self.reference(_digest(value), kind="text", value=value)
        if isinstance(value, list):
            return [self.externalize(item, text=text) for item in value]
        if isinstance(value, dict):
            # Only clipboard bodies become text refs. Display strings and DOM
            # locate needles keep their established compact presentation.
            return {key: self.externalize(item, text=text or key in {"copyText", "copies"})
                    for key, item in value.items()}
        return value

    def tasks(self, snapshot, project):
        tasks = snapshot.activity_tasks
        result = []
        context = self._context_key(snapshot)
        for index in range(len(tasks)):
            descriptor = getattr(tasks, "renderer_descriptor", None)
            if callable(descriptor):
                metadata, revision = descriptor(index)
            else:
                task = tasks[index]
                metadata = {"index": task.index, "count": task.count, "turnId": task.turn_id,
                            "currentTask": task.prompt}
                revision = (id(tasks), index, self.generation)
            if index == len(tasks) - 1:
                # The latest task can differ from the top-level activity view
                # while a new prompt is waiting for its task marker. Preserve
                # the established projection rather than substituting raw UI.
                result.append(self.externalize(project(snapshot, tasks[index])))
                continue
            key = (revision, context)
            captured = copy.copy(snapshot)

            def build(captured=captured, index=index):
                return self.externalize(project(captured, captured.activity_tasks[index]))

            ref = self.reference(key, kind="task", factory=build)
            result.append({**metadata, "detailRef": ref})
        return result

    def _context_key(self, snapshot):
        return _digest((snapshot.session_id, snapshot.session_title, snapshot.session_started_at,
                        snapshot.status, snapshot.selection_source, snapshot.request.model,
                        # Historical activity uses the same presentation context
                        # as the legacy projector. Capture its changing inputs.
                        [vars(item) for item in snapshot.active_work_items], snapshot.task_count))

    def heavy_rounds(self, snapshot, project):
        tasks = snapshot.activity_tasks
        descriptor = getattr(tasks, "renderer_descriptor", None)
        if not callable(descriptor) or len(tasks) < 2:
            return self.externalize(project(snapshot))
        # Ranking depends on historical usage and the fallback model, not the
        # selected task's clock, current work footer or running status.
        context = str(snapshot.request.model or "")
        revision = tuple(descriptor(index, include_clock=False)[1] for index in range(len(tasks) - 1))
        key = ("closed-top-three", revision, context)
        captured = copy.copy(snapshot)
        captured.activity_tasks = tasks[:-1]
        ref = self.reference(key, kind="ranking", factory=lambda: self.externalize(project(captured, _with_rank=True, _allow_session_fallback=False)))
        closed = self.owner.read(ref["_hudHistoryRef"])["value"]
        current = copy.copy(snapshot)
        current.activity_tasks = tasks[-1:]
        items = [*closed, *self.externalize(project(current, _with_rank=True, _allow_session_fallback=False))]
        if not items:
            return self.externalize(project(snapshot))
        items.sort(key=lambda row: tuple(row["_historyRank"]), reverse=True)
        return [{key: value for key, value in row.items() if key != "_historyRank"} for row in items[:3]]

    def finish(self):
        with self.owner._lock:
            self._collect(self.pending)
            self._collect(self.acknowledged)
            retained = self.pending | self.acknowledged
            for key, entry in tuple(self.entries.items()):
                if entry.token not in retained:
                    entry.close()
                    self.owner._tokens.pop(entry.token, None)
                    del self.entries[key]

    def _collect(self, tokens):
        pending = list(tokens)
        while pending:
            entry = self.owner._tokens.get(pending.pop())
            if entry is None:
                continue
            for token in entry.dependencies:
                if token not in tokens:
                    tokens.add(token)
                    pending.append(token)

    def acknowledge(self):
        with self.owner._lock:
            self._collect(self.pending)
            self.acknowledged = set(self.pending)

    def close(self):
        for entry in self.entries.values():
            entry.close()
            self.owner._tokens.pop(entry.token, None)
        self.entries.clear()
