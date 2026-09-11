from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codex_usage_hud.config import ModelPrice, ProviderSettings, UserConfig
from codex_usage_hud.runtime_commands import GeneralCommandPorts, handle_general_command
from codex_usage_hud.pricing_sync import OfficialPrice


def _ports(state: dict[str, UserConfig], **overrides: object) -> GeneralCommandPorts:
    values = {
        "load_config": lambda: state["config"],
        "save_config": lambda value: state.__setitem__("config", value),
        "fetch_prices": lambda _url: {},
        "rest_reminder": None,
        "update_manager": None,
        "work_overlay": None,
        "request_restart": lambda: None,
        "request_exit": lambda: None,
        "check_update": lambda: SimpleNamespace(
            error="", available=False, current_version="1"
        ),
        "install_update": lambda _info: None,
        "overlay_status": lambda: {},
        "start_overlay_install": lambda: False,
        "clear_forced_missing": lambda: None,
        "forced_missing_with_real_install": lambda: False,
        "pyside_version": lambda: "",
        "default_overlay_limit": lambda: 1,
        "dismiss_warnings_today": lambda: True,
    }
    values.update(overrides)
    return GeneralCommandPorts(**values)


def test_save_pricing_starts_new_version_now_without_scanning_history() -> None:
    state = {"config": UserConfig.defaults()}
    payload = state["config"].to_dict()
    payload["model_prices"]["gpt-5.6-sol"]["input"] = 7.0
    ports = _ports(state)

    before = datetime.now(timezone.utc)
    saved = handle_general_command(
        {"action": "savePricing", "settings": payload},
        ports,
    )
    after = datetime.now(timezone.utc)

    assert not saved["kind"]
    assert "已有记录不变" in saved["message"]
    assert state["config"].model_prices["gpt-5.6-sol"].input == 7.0
    version = next(
        version
        for version in state["config"].pricing_versions
        if version.match_pattern == "gpt-5.6-sol"
    )
    assert before <= version.effective_at <= after
    assert version.input == 7


def test_generic_save_cannot_bypass_pricing_version_workflow() -> None:
    state = {"config": UserConfig.defaults()}
    ports = _ports(state)
    payload = state["config"].to_dict()
    payload["model_prices"]["gpt-5.6-sol"]["input"] = 7.0

    result = handle_general_command({"action": "save", "settings": payload}, ports)

    assert result["kind"] == "error"
    assert state["config"].model_prices["gpt-5.6-sol"].input == 5.0


def test_import_preview_is_read_only_and_commit_is_atomic() -> None:
    state = {"config": UserConfig.defaults()}
    ports = _ports(state)
    payload = {
        "schema_version": 1,
        "unit": "USD_per_1M_tokens",
        "prices": [
            {
                "model": "gpt-imported",
                "provider": "custom",
                "input": 1,
                "output": 2,
                "cached_input": 0.1,
                "cache_write": 1.25,
                "reasoning": 2,
            }
        ],
    }

    preview = handle_general_command(
        {"action": "pricingImportPreview", "payload": payload}, ports
    )
    assert preview["pricingPreview"]["addedCount"] == 1
    assert state["config"].pricing_versions == ()

    committed = handle_general_command(
        {
            "action": "pricingImportCommit",
            "payload": preview["pricingPayload"],
            "conflictPolicy": "overwrite",
        },
        ports,
    )
    assert committed["pricingImportResult"]["addedCount"] == 1
    assert len(state["config"].pricing_versions) == 1
    assert state["config"].provider_settings["custom"].model_prices[
        "gpt-imported"
    ].cache_write == 1.25
    assert committed["pricingSync"]["pending_changes"] == []


def test_commit_clears_pending_official_price_changes() -> None:
    state = {
        "config": replace(
            UserConfig.defaults(),
            pricing_sync={
                "pending_changes": [{"model": "gpt-imported", "kind": "added"}],
                "unread_change_count": 1,
            },
        )
    }
    ports = _ports(state)
    payload = {
        "schema_version": 1,
        "unit": "USD_per_1M_tokens",
        "prices": [
            {
                "model": "gpt-imported",
                "provider": "custom",
                "input": 1,
                "output": 2,
                "cached_input": 0.1,
                "cache_write": 1.25,
                "reasoning": 2,
            }
        ],
    }
    preview = handle_general_command(
        {"action": "pricingImportPreview", "payload": payload}, ports
    )
    committed = handle_general_command(
        {
            "action": "pricingImportCommit",
            "payload": preview["pricingPayload"],
            "conflictPolicy": "overwrite",
        },
        ports,
    )

    sync = committed["pricingSync"]
    assert sync["pending_changes"] == []
    assert sync["unread_change_count"] == 0
    assert sync["last_applied_at"]
    assert state["config"].pricing_sync["pending_changes"] == []

    before = state["config"].to_dict()
    invalid = handle_general_command(
        {
            "action": "pricingImportCommit",
            "payload": {
                "schema_version": 1,
                "unit": "USD_per_1M_tokens",
                "prices": [{"model": "broken", "input": -1, "output": 2}],
            },
            "conflictPolicy": "overwrite",
        },
        ports,
    )
    assert invalid["kind"] == "error"
    assert state["config"].to_dict() == before


def test_import_of_an_older_version_keeps_the_editable_current_price() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    config, _ = UserConfig.defaults().apply_price_updates(
        {"gpt-test": {"input": 1, "output": 2}},
        effective_at=now - timedelta(days=4),
        provider="custom",
        created_at=now,
    )
    config, _ = config.apply_price_updates(
        {"gpt-test": {"input": 3, "output": 4}},
        effective_at=now - timedelta(days=1),
        provider="custom",
        created_at=now,
    )
    state = {"config": config}
    ports = _ports(state)
    historical_payload = {
        "schema_version": 1,
        "unit": "USD_per_1M_tokens",
        "prices": [
            {
                "model": "gpt-test",
                "provider": "custom",
                "input": 0.5,
                "cached_input": 0.05,
                "cache_write": 0.75,
                "output": 1,
                "reasoning": 1,
                "effective_at": (now - timedelta(days=6)).isoformat(),
            }
        ],
    }

    preview = handle_general_command(
        {"action": "pricingImportPreview", "payload": historical_payload}, ports
    )
    committed = handle_general_command(
        {
            "action": "pricingImportCommit",
            "payload": preview["pricingPayload"],
            "conflictPolicy": "overwrite",
        },
        ports,
    )

    assert committed["pricingImportResult"]["addedCount"] == 1
    current = state["config"].provider_settings["custom"].model_prices["gpt-test"]
    assert current.input == 3
    assert current.output == 4


def test_historical_repricing_commands_are_removed() -> None:
    state = {"config": UserConfig.defaults()}
    ports = _ports(state)

    preview = handle_general_command(
        {"action": "pricingRecalculationPreview", "provider": "custom"}, ports
    )
    execute = handle_general_command(
        {"action": "pricingRecalculationExecute", "provider": "custom"}, ports
    )
    impact = handle_general_command(
        {"action": "pricingImpactPreview", "provider": "custom"}, ports
    )

    assert preview["kind"] == "error"
    assert execute["kind"] == "error"
    assert impact["kind"] == "error"


def test_apply_pricing_sync_replaces_provider_prices_and_clears_unread_state() -> None:
    config = UserConfig.from_dict(
        {
            "provider_settings": {
                "custom": {"model_prices": {"obsolete": {"input": 1, "output": 2}}}
            },
            "pricing_sync": {
                "unread_change_count": 2,
                "pending_changes": [{"model": "official", "kind": "added"}],
                "pending_prices": [
                    {
                        "model": "official",
                        "provider": "custom",
                        "input": 3,
                        "cached_input": 0.3,
                        "cache_write": 4,
                        "output": 12,
                        "reasoning": 12,
                    }
                ],
            },
        }
    )
    state = {"config": config}

    result = handle_general_command({"action": "applyPricingSync"}, _ports(state))

    assert not result["kind"]
    prices = state["config"].provider_settings["custom"].model_prices
    assert set(prices) == {"official"}
    assert prices["official"].cache_write == 4
    assert state["config"].pricing_sync["unread_change_count"] == 0
    assert state["config"].pricing_sync["pending_prices"] == []
    assert state["config"].pricing_versions


def test_manual_official_preview_lists_prices_when_there_are_no_differences() -> None:
    row = {
        "input": 10,
        "cached_input": 1,
        "cache_write": 12.5,
        "output": 50,
        "reasoning": 50,
    }
    config = replace(
        UserConfig.defaults(),
        provider_settings={
            "custom": ProviderSettings(
                model_prices={"gpt-6-astra": ModelPrice.from_mapping(row, "gpt-6-astra")}
            )
        },
    )
    state = {"config": config}
    official = OfficialPrice(
        model="gpt-6-astra",
        input=Decimal("10"),
        cached_input=Decimal("1"),
        cache_write=Decimal("12.5"),
        output=Decimal("50"),
        reasoning=Decimal("50"),
    )

    with patch(
        "codex_usage_hud.runtime_commands.fetch_pricing_snapshot",
        return_value=({"gpt-6-astra": official}, {"checked_at": "2026-01-01T00:00:00Z"}),
    ):
        result = handle_general_command(
            {
                "action": "fetchPricesPreview",
                "provider": "custom",
                "reason": "official-sync",
            },
            _ports(state),
        )

    assert result["pricingPreview"]["changeCount"] == 0
    assert result["pricingPreview"]["modelCount"] == 1
    assert result["pricingPreview"]["prices"][0]["model"] == "gpt-6-astra"
    assert result["pricingCheckedAt"]
    assert result["pricingSync"]["scope_provider"] == "custom"
    assert result["pricingSync"]["pending_prices"][0]["model"] == "gpt-6-astra"
    assert result["pricingSync"]["last_success_at"]
    assert state["config"].pricing_sync == result["pricingSync"]
    assert state["config"].pricing_versions == config.pricing_versions


def test_official_preview_keeps_locally_configured_models_missing_from_snapshot() -> None:
    local_row = {
        "input": 2.5,
        "cached_input": 0.25,
        "cache_write": 0,
        "output": 15,
        "reasoning": 15,
    }
    config = replace(
        UserConfig.defaults(),
        provider_settings={
            "custom": ProviderSettings(
                model_prices={
                    "gpt-6-astra": ModelPrice.from_mapping(
                        {"input": 9, "cached_input": 1, "cache_write": 12.5, "output": 50, "reasoning": 50},
                        "gpt-6-astra",
                    ),
                    "gpt-5.4": ModelPrice.from_mapping(local_row, "gpt-5.4"),
                }
            )
        },
    )
    state = {"config": config}
    official = OfficialPrice(
        model="gpt-6-astra",
        input=Decimal("10"),
        cached_input=Decimal("1"),
        cache_write=Decimal("12.5"),
        output=Decimal("50"),
        reasoning=Decimal("50"),
    )

    with patch(
        "codex_usage_hud.runtime_commands.fetch_pricing_snapshot",
        return_value=({"gpt-6-astra": official}, {"checked_at": "2026-01-01T00:00:00Z"}),
    ):
        result = handle_general_command(
            {
                "action": "fetchPricesPreview",
                "provider": "custom",
                "reason": "official-sync",
            },
            _ports(state),
        )

    rows = {str(row["model"]): row for row in result["pricingPreview"]["prices"]}
    assert set(rows) == {"gpt-6-astra", "gpt-5.4"}
    assert rows["gpt-5.4"]["officialMissing"] is True
    assert rows["gpt-5.4"]["input"] == 2.5
    assert rows["gpt-6-astra"]["officialMissing"] is False
    # The snapshot omission of gpt-5.4 is not counted as a price change.
    assert result["pricingPreview"]["changeCount"] == 1
    assert result["pricingPreview"]["modelCount"] == 2
    assert result["pricingSync"]["unread_change_count"] == 1
    assert [row["model"] for row in result["pricingSync"]["pending_prices"]] == [
        "gpt-6-astra",
        "gpt-5.4",
    ]


def test_export_price_file_uses_current_prices_or_builtin_template(tmp_path: Path) -> None:
    state = {"config": UserConfig.defaults()}
    opened: list[Path] = []
    ports = _ports(
        state,
        pricing_open_path=lambda path: opened.append(path),
    )

    with patch("codex_usage_hud.runtime_commands.hud_program_root", return_value=tmp_path):
        exported = handle_general_command({"action": "pricingExport"}, ports)
        exported_path = Path(str(exported["pricingPath"]))
        opened_status = handle_general_command(
            {"action": "pricingOpen", "filename": exported["filename"]}, ports
        )

    assert exported["pricingUsedTemplate"] is False
    assert exported["filename"] == "codex-usage-hud-pricing.json"
    assert exported_path == tmp_path / str(exported["filename"])
    assert json.loads(exported_path.read_text(encoding="utf-8"))["prices"]
    assert not opened_status["kind"]
    assert opened == [exported_path]

    state["config"] = replace(
        UserConfig.defaults(),
        model_prices={},
        pricing_versions=(),
        pricing_audit=(),
    )
    with patch("codex_usage_hud.runtime_commands.hud_program_root", return_value=tmp_path):
        fallback = handle_general_command({"action": "pricingExport"}, ports)

    fallback_path = Path(str(fallback["pricingPath"]))
    fallback_prices = json.loads(fallback_path.read_text(encoding="utf-8"))["prices"]
    assert fallback["pricingUsedTemplate"] is True
    assert fallback_path == exported_path
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert len(fallback_prices) == 1
    assert fallback_prices[0]["model"] == "gpt-5.6-sol"
    assert fallback_prices[0]["input"] == 5.0
    assert fallback_prices[0]["cached_input"] == 0.5
    assert fallback_prices[0]["cache_write"] == 6.25
    assert fallback_prices[0]["output"] == 30.0
    assert fallback_prices[0]["reasoning"] == 30.0
