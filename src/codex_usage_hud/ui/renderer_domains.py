"""Renderer-injected Codex HUD driven through local Chrome DevTools Protocol."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..renderer_cdp import RendererTargetDiscovery, _RendererBinding  # noqa: F401
from ..config import DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED
from ..core.connection_health import PROBE_TIMEOUT_SECONDS  # noqa: F401
from ..core.parser import CostEstimator
from ..platforms.cdp_probe import (
    cdp_port_from_env,  # noqa: F401
    install_new_document_script,  # noqa: F401
    list_targets,  # noqa: F401
    pick_page_target,  # noqa: F401
    remove_new_document_script,  # noqa: F401
    send_cdp_command,  # noqa: F401
)  # noqa: F401
from ..platforms.codex_theme import CodexThemeProbe, CodexThemeSnapshot  # noqa: F401
from ..support_assets import support_qr_payload  # noqa: F401
from .. import renderer_catalog
from .. import renderer_payload_builder
from ..renderer_client import RendererHudClient, remove_renderer_hud_from_pages
from ..renderer_client import wait_for_renderer
from ..renderer_payloads import RendererHudPayload, payload_domains
from ..renderer_presenters import budget as renderer_budget
from ..renderer_presenters import common as renderer_common

RENDERER_HUD_ENV = "CODEX_USAGE_HUD_RENDERER"
RENDERER_HUD_VERSION = "20"
DEFAULT_RENDERER_TIMEOUT_SECONDS = 0.45
DEFAULT_RENDERER_TARGET_CACHE_SECONDS = 2.0
SLOW_RENDERER_UPDATE_LOG_MS = 250.0
ACTIVE_SESSION_BINDING_NAME = "codexUsageHudActiveSession"
SETTINGS_COMMAND_BINDING_NAME = "codexUsageHudSettingsCommand"
COMPOSER_ATTACHMENTS_BINDING_NAME = "codexUsageHudComposerAttachments"
LAYOUT_BINDING_NAME = "codexUsageHudLayout"
THEME_BINDING_NAME = "codexUsageHudTheme"
MODEL_CATALOG_JSON_ENV = renderer_catalog.MODEL_CATALOG_JSON_ENV
TOKEN_LEGEND_TEXT = "↑ 输入  ↻ 缓存  ↓ 输出\n◇ 推理  ∑ 合计  $ 金额\n◎ 缓存率  ~ 估算"
TOP_EXPANDED_HEADER_FALLBACK = "Codex 会话 / 预算"
COMPOSER_TIKTOKEN_BADGE_ENABLED = DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED
_LOGGER = logging.getLogger("codex_usage_hud.ui.renderer_hud")
REMOVE_RENDERER_HUD_SCRIPT = (
    "(() => {"
    "let existed = false;"
    "try {"
    "const remove = window.__codexUsageHudRemove;"
    "existed = typeof remove === 'function' || !!document.getElementById('codex-usage-hud-root');"
    "if (typeof remove === 'function') remove();"
    "else {"
    "document.getElementById('codex-usage-hud-root')?.remove();"
    "document.getElementById('codex-usage-hud-style')?.remove();"
    "}"
    "} catch (_) {"
    "document.getElementById('codex-usage-hud-root')?.remove();"
    "document.getElementById('codex-usage-hud-style')?.remove();"
    "existed = true;"
    "}"
    "return existed;"
    "})()"
)

def set_cost_estimator(estimator: CostEstimator) -> None:
    renderer_payload_builder.set_cost_estimator(estimator)


def _renderer_theme_payload(
    snapshot: CodexThemeSnapshot | None,
) -> dict[str, object]:
    return renderer_payload_builder._renderer_theme_payload(snapshot)  # noqa: SLF001


def _configured_model_catalog_path() -> Path | None:
    return renderer_catalog.configured_model_catalog_path()


def _model_catalog_candidate_paths() -> list[Path]:
    return renderer_catalog.model_catalog_candidate_paths()


def _normalize_catalog_model(model: object) -> dict[str, object] | None:
    return renderer_catalog.normalize_catalog_model(model)


def _renderer_model_catalog_payload() -> list[dict[str, object]]:
    return renderer_catalog.model_catalog_payload()


def _renderer_hud_script_with_model_catalog(
    catalog: list[dict[str, object]] | None = None,
) -> str:
    if catalog is None:
        catalog = _renderer_model_catalog_payload()
    return renderer_catalog.renderer_hud_script_with_model_catalog(catalog)


RENDERER_HUD_SCRIPT: str  # Compatibility export; not retained on normal import.


def __getattr__(name: str):
    if name == "RENDERER_HUD_SCRIPT":
        return renderer_catalog.RENDERER_HUD_SCRIPT
    raise AttributeError(name)


def _payload_domains(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    return payload_domains(payload)


_RendererTargetDiscovery = RendererTargetDiscovery


def renderer_enabled_from_env(default: bool = True) -> bool:
    value = os.environ.get(RENDERER_HUD_ENV)
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized not in {"0", "false", "no", "off"}


payload_from_snapshot = renderer_payload_builder.payload_from_snapshot
session_switch_payload_from_snapshot = (
    renderer_payload_builder.session_switch_payload_from_snapshot
)
_runtime_errors_payload = renderer_payload_builder._runtime_errors_payload
_connection_health_payload = renderer_payload_builder._connection_health_payload
_observed_models = renderer_payload_builder._observed_models


def _runtime_expression_params(expression: str) -> dict[str, object]:
    return {
        "expression": expression,
        "returnByValue": True,
        "allowUnsafeEvalBlockedByCSP": True,
    }


def _format_money(value: float | None) -> str:
    return renderer_payload_builder._format_money(value)


def _short_num(value: int | None) -> str:
    return renderer_payload_builder._short_num(value)


def _format_usage_money(tokens: int | None, cost: float | None) -> str:
    return renderer_common.format_usage_money(
        tokens,
        cost,
        short_formatter=_short_num,
        money_formatter=_format_money,
    )


def _budget_progress_overflow_parts(
    cost: float | None,
    limit: float | None,
) -> tuple[str, str]:
    return renderer_budget.progress_overflow_parts(
        cost,
        limit,
        total_ratio=renderer_budget.progress_total_ratio,
        money_formatter=_format_money,
    )


def _budget_limit_text(limit: float | None) -> str:
    return renderer_budget.limit_text(
        limit,
        money_formatter=_format_money,
    )


__all__ = [
    "DEFAULT_RENDERER_TIMEOUT_SECONDS",
    "RENDERER_HUD_ENV",
    "RENDERER_HUD_SCRIPT",
    "RendererHudClient",
    "RendererHudPayload",
    "payload_from_snapshot",
    "remove_renderer_hud_from_pages",
    "renderer_enabled_from_env",
    "set_cost_estimator",
    "wait_for_renderer",
]
