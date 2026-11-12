"""Renderer model catalog discovery, normalization, and boot script assembly."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from .renderer_bundle import read_renderer_template


MODEL_CATALOG_JSON_ENV = "CODEX_USAGE_HUD_MODEL_CATALOG_JSON"
_BUNDLE_FINGERPRINT_PLACEHOLDER = "__CODEX_USAGE_HUD_BUNDLE_FINGERPRINT__"


def configured_model_catalog_path() -> Path | None:
    config_path = Path.home() / ".codex" / "config.toml"
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"(?m)^\s*model_catalog_json\s*=\s*(['\"])(.*?)\1", text)
    if not match:
        return None
    raw = match.group(2).strip()
    return Path(raw.replace("\\\\", "\\")) if raw else None


def model_catalog_candidate_paths() -> list[Path]:
    paths: list[Path] = []
    env_path = os.environ.get(MODEL_CATALOG_JSON_ENV, "").strip()
    if env_path:
        paths.append(Path(env_path))
    configured = configured_model_catalog_path()
    if configured is not None:
        paths.append(configured)
    catalog_dir = Path.home() / ".codex" / "model-catalogs"
    try:
        paths.extend(
            sorted(
                catalog_dir.glob("*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        )
    except OSError:
        pass

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        expanded = path.expanduser()
        key = str(expanded).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(expanded)
    return deduped


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := str(item or "").strip())]


def _normalize_reasoning_levels(
    model: dict[str, object],
) -> list[dict[str, str]]:
    raw_levels = model.get("supported_reasoning_levels")
    if raw_levels is None:
        raw_levels = model.get("supportedReasoningEfforts")
    if not isinstance(raw_levels, list):
        return []
    levels: list[dict[str, str]] = []
    for item in raw_levels:
        if not isinstance(item, dict):
            continue
        effort = str(item.get("effort") or item.get("reasoningEffort") or "").strip()
        if effort:
            levels.append(
                {
                    "reasoningEffort": effort,
                    "description": str(item.get("description") or effort).strip(),
                }
            )
    return levels


def normalize_catalog_model(model: object) -> dict[str, object] | None:
    if not isinstance(model, dict):
        return None
    visibility = str(model.get("visibility") or "list").strip().lower()
    if visibility not in {"", "list", "visible"}:
        return None
    slug = str(model.get("slug") or model.get("model") or model.get("id") or "").strip()
    if not slug:
        return None
    reasoning_efforts = _normalize_reasoning_levels(model)
    default_reasoning = str(
        model.get("default_reasoning_level")
        or model.get("defaultReasoningEffort")
        or (reasoning_efforts[0]["reasoningEffort"] if reasoning_efforts else "medium")
    ).strip()
    return {
        "model": slug,
        "displayName": str(
            model.get("display_name") or model.get("displayName") or slug
        ).strip(),
        "description": str(model.get("description") or "").strip(),
        "defaultReasoningEffort": default_reasoning,
        "supportedReasoningEfforts": reasoning_efforts,
        "inputModalities": _as_string_list(
            model.get("input_modalities") or model.get("inputModalities")
        )
        or ["text"],
        "priority": int(model.get("priority") or 1000),
    }


def model_catalog_payload() -> list[dict[str, object]]:
    models_by_slug: dict[str, dict[str, object]] = {}
    for path in model_catalog_candidate_paths():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        raw_models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(raw_models, list):
            continue
        for raw_model in raw_models:
            model = normalize_catalog_model(raw_model)
            if model is not None:
                models_by_slug.setdefault(str(model["model"]), model)
    models = sorted(
        models_by_slug.values(),
        key=lambda item: (
            int(item.get("priority") or 1000),
            str(item.get("model") or ""),
        ),
    )
    for model in models:
        model.pop("priority", None)
    return models


def renderer_hud_script_with_model_catalog(
    catalog: list[dict[str, object]] | None = None,
    *,
    template: str | None = None,
) -> str:
    payload = model_catalog_payload() if catalog is None else catalog
    source = read_renderer_template() if template is None else template
    fingerprint = hashlib.sha256(
        source.replace(
            _BUNDLE_FINGERPRINT_PLACEHOLDER,
            "",
        ).encode("utf-8")
    ).hexdigest()[:16]
    template = source.replace(
        _BUNDLE_FINGERPRINT_PLACEHOLDER,
        fingerprint,
    )
    return template.replace(
        "__CODEX_MODEL_PICKER_CATALOG__",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )


RENDERER_HUD_SCRIPT: str  # Lazily provided by __getattr__, without a pinned copy.


def __getattr__(name: str):
    if name == "RENDERER_HUD_SCRIPT":
        # Public compatibility value. Production installs a catalog-specific
        # script, so retaining this second complete no-catalog copy is wasted.
        return renderer_hud_script_with_model_catalog([])
    raise AttributeError(name)


__all__ = [
    "MODEL_CATALOG_JSON_ENV",
    "RENDERER_HUD_SCRIPT",
    "configured_model_catalog_path",
    "model_catalog_candidate_paths",
    "model_catalog_payload",
    "normalize_catalog_model",
    "renderer_hud_script_with_model_catalog",
]
