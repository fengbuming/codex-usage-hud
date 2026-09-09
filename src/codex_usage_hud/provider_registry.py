"""Provider discovery for pricing and scope selection.

The registry intentionally uses Codex's existing TOML parser rather than a
second partial parser.  Profile names are display aliases only; every entry is
keyed by the session/accounting `model_provider` value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from .codex_provider_config import default_codex_config_path, read_provider_definitions
from .config import UserConfig, normalize_provider
from .platforms.codex_theme import read_codex_config

UNKNOWN_PROVIDER = "unknown"


@dataclass(frozen=True)
class ProviderRegistryEntry:
    provider: str
    profile_names: tuple[str, ...] = ()
    name: str = ""
    base_url: str = ""
    env_key: str = ""
    official_account: bool = False
    requires_openai_auth: bool = False
    wire_api: str = "responses"
    has_api_key: bool = False
    config_text: str = ""
    from_base_config: bool = False
    from_profile: bool = False
    from_provider_definition: bool = False
    from_saved_settings: bool = False
    from_history: bool = False

    @property
    def historical_only(self) -> bool:
        return self.from_history and not any(
            (
                self.from_base_config,
                self.from_profile,
                self.from_provider_definition,
                self.from_saved_settings,
            )
        )


@dataclass(frozen=True)
class ProviderRegistry:
    entries: dict[str, ProviderRegistryEntry]
    app_provider: str = ""
    config_path: str = ""

    def providers(self) -> tuple[str, ...]:
        # ``entries`` is built in discovery order.  Preserve it so callers
        # can append newly discovered providers after already materialized
        # settings instead of re-sorting tabs alphabetically.
        return tuple(self.entries)


def discover_provider_registry(
    *,
    user_config: UserConfig,
    config_path: str | Path | None = None,
    sessions_root: Path | None = None,
    now: datetime | None = None,
    include_history: bool = True,
) -> ProviderRegistry:
    """Collect provider keys from config/settings and optionally recent JSONL."""
    resolved_config_path = (
        Path(config_path).expanduser()
        if config_path is not None
        else default_codex_config_path()
    )
    raw_config = read_codex_config(resolved_config_path)
    provider_definitions = read_provider_definitions(resolved_config_path)
    entries: dict[str, dict[str, object]] = {}

    def include(provider_value: object, **source: bool) -> None:
        provider = normalize_provider(provider_value)
        if not provider:
            return
        state = entries.setdefault(provider, {"profiles": set()})
        for key, value in source.items():
            if value:
                state[key] = True

    app_provider = normalize_provider(raw_config.get("model_provider"))
    include(app_provider, from_base_config=True)

    model_providers = raw_config.get("model_providers")
    if isinstance(model_providers, Mapping):
        for provider in model_providers:
            include(provider, from_provider_definition=True)
            normalized_provider = normalize_provider(provider)
            definition = provider_definitions.get(normalized_provider)
            if definition is not None and normalized_provider in entries:
                entries[normalized_provider].update(
                    {
                        "name": definition.name,
                        "base_url": definition.base_url,
                        "env_key": definition.env_key,
                        "official_account": definition.official_account,
                        "requires_openai_auth": definition.requires_openai_auth,
                        "wire_api": definition.wire_api,
                        "has_api_key": definition.has_api_key,
                        "config_text": definition.section_text,
                    }
                )

    profiles = raw_config.get("profiles")
    if isinstance(profiles, Mapping):
        for profile_name, profile_config in profiles.items():
            if not isinstance(profile_config, Mapping):
                continue
            provider = normalize_provider(profile_config.get("model_provider"))
            include(provider, from_profile=True)
            if provider:
                profile_names = entries[provider].setdefault("profiles", set())
                if isinstance(profile_names, set):
                    profile_names.add(str(profile_name))

    for provider in user_config.provider_settings:
        include(provider, from_saved_settings=True)
    for price in user_config.model_prices.values():
        include(price.provider, from_saved_settings=True)

    if include_history and sessions_root is not None:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=30)
        for provider in discover_recent_session_providers(sessions_root, cutoff=cutoff):
            include(provider, from_history=True)

    normalized_entries = {
        provider: ProviderRegistryEntry(
            provider=provider,
            profile_names=tuple(sorted(state.get("profiles", set()))),
            name=str(state.get("name") or ""),
            base_url=str(state.get("base_url") or ""),
            env_key=str(state.get("env_key") or ""),
            official_account=bool(state.get("official_account")),
            requires_openai_auth=bool(state.get("requires_openai_auth")),
            wire_api=str(state.get("wire_api") or "responses"),
            has_api_key=bool(state.get("has_api_key")),
            config_text=str(state.get("config_text") or ""),
            from_base_config=bool(state.get("from_base_config")),
            from_profile=bool(state.get("from_profile")),
            from_provider_definition=bool(state.get("from_provider_definition")),
            from_saved_settings=bool(state.get("from_saved_settings")),
            from_history=bool(state.get("from_history")),
        )
        for provider, state in entries.items()
    }
    return ProviderRegistry(
        entries=normalized_entries,
        app_provider=app_provider,
        config_path=str(resolved_config_path),
    )


def discover_recent_session_providers(
    sessions_root: Path,
    *,
    cutoff: datetime,
) -> set[str]:
    """Read only `session_meta` rows from the current and archived session trees."""
    providers: set[str] = set()
    for root in _session_roots(sessions_root):
        if not root.exists():
            continue
        try:
            paths = root.rglob("*.jsonl")
            for path in paths:
                metadata = _session_meta_from_path(path)
                if metadata is None:
                    continue
                timestamp, payload = metadata
                if timestamp is None or timestamp < cutoff:
                    continue
                providers.add(normalize_provider(payload.get("model_provider")) or UNKNOWN_PROVIDER)
        except OSError:
            continue
    return providers


def _session_roots(sessions_root: Path) -> tuple[Path, ...]:
    root = Path(sessions_root).expanduser()
    if root.name != "sessions":
        return (root,)
    return (root, root.parent / "archived_sessions")


def _session_meta_from_path(path: Path) -> tuple[datetime | None, Mapping[str, Any]] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, Mapping) or record.get("type") != "session_meta":
                    continue
                payload = record.get("payload")
                if isinstance(payload, Mapping):
                    return _parse_timestamp(record.get("timestamp")), payload
                return None
    except OSError:
        return None
    return None


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


__all__ = [
    "ProviderRegistry",
    "ProviderRegistryEntry",
    "UNKNOWN_PROVIDER",
    "discover_provider_registry",
    "discover_recent_session_providers",
]
