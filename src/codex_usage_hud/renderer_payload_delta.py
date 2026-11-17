"""Acknowledged, lossless deltas for large renderer inventory/ranking lists."""

from __future__ import annotations

from collections.abc import Callable, Mapping


class RendererPayloadDeltaCache:
    def __init__(self) -> None:
        self._components: dict[str, object] = {}

    def prepare(
        self,
        payload: dict[str, object],
        baseline_domains: Mapping[str, str],
        digest: Callable[[object], str],
    ) -> tuple[dict[str, object], dict[str, object]]:
        domains = payload.get("payloadDomains")
        if not isinstance(domains, Mapping):
            return payload, {}
        reduced_domains = dict(domains)
        reduced = dict(payload)
        pending: dict[str, object] = {}

        def replace_domain(name: str, value: dict[str, object]) -> None:
            reduced_domains[name] = {**domains[name], name: value}
            if name in payload:
                reduced[name] = value

        session = domains.get("currentSession")
        details = session.get("topDetails") if isinstance(session, Mapping) else None
        if isinstance(details, Mapping):
            identity = (str(session.get("sessionId") or ""), str(session.get("rendererSessionId") or ""),
                        bool(session.get("newSession")), bool(session.get("pendingSession")))
            pending["history.identity"] = identity
            same = self._components.get("history.identity") == identity and "currentSession" in baseline_domains
            fields = ("activityTasks", "heavyRounds", "activityTrail", "workTools")
            next_details = dict(details)
            retained = []
            for field in fields:
                key = "history." + field
                if field not in details:
                    pending[key] = None
                    continue
                hashed = digest(details[field])
                pending[key] = hashed
                if same and self._components.get(key) == hashed:
                    next_details.pop(field)
                    retained.append(field)
            if retained:
                next_details["_retainHistoryFields"] = retained
                next_details["_historySession"] = list(identity)
            reduced_domains["currentSession"] = {**session, "topDetails": next_details}
            if "topDetails" in payload:
                reduced["topDetails"] = next_details

        cleanup_domain = domains.get("sessionCleanup")
        cleanup = cleanup_domain.get("sessionCleanup") if isinstance(cleanup_domain, Mapping) else None
        if isinstance(cleanup, Mapping):
            next_cleanup = dict(cleanup)
            revision = str(cleanup.get("revision") or "")
            pending["cleanup.revision"] = revision
            same_revision = self._components.get("cleanup.revision") == revision
            for field in ("sessions", "workdirs"):
                if field not in cleanup:
                    continue
                key = f"cleanup.{field}"
                value_digest = digest(cleanup[field])
                pending[key] = value_digest
                if (
                    "sessionCleanup" in baseline_domains
                    and same_revision
                    and self._components.get(key) == value_digest
                ):
                    next_cleanup.pop(field)
            replace_domain("sessionCleanup", next_cleanup)

        usage_domain = domains.get("usageInsights")
        usage = usage_domain.get("usageInsights") if isinstance(usage_domain, Mapping) else None
        if isinstance(usage, Mapping):
            next_usage = dict(usage)
            patches = []
            for window in ("today", "week", "month"):
                source = usage.get(window)
                next_window = dict(source) if isinstance(source, Mapping) else None
                for field in ("sessions", "topSessionsByUsage", "topSessionsByCost"):
                    key = f"usage.{window}.{field}"
                    rows = source.get(field) if isinstance(source, Mapping) else None
                    pending[key] = None
                    if not isinstance(rows, list):
                        continue
                    hashes = tuple(digest(row) for row in rows)
                    pending[key] = hashes
                    previous = self._components.get(key)
                    if "usageInsights" not in baseline_domains or not isinstance(previous, tuple):
                        continue
                    changed = [
                        [index, row]
                        for index, row in enumerate(rows)
                        if index >= len(previous) or hashes[index] != previous[index]
                    ]
                    # Full replacement is smaller when most rows changed.
                    # Empty lists are still valid patches (truncate to zero).
                    if changed and len(changed) * 2 >= len(rows):
                        continue
                    next_window.pop(field)
                    patches.append({"path": [window, field], "length": len(rows), "items": changed})
                if next_window is not None:
                    next_usage[window] = next_window
            if patches:
                next_usage["_listPatches"] = patches
            replace_domain("usageInsights", next_usage)

        reduced["payloadDomains"] = reduced_domains
        return reduced, pending

    def commit(self, pending: Mapping[str, object]) -> None:
        for key, value in pending.items():
            if value is None:
                self._components.pop(key, None)
            else:
                self._components[key] = value
