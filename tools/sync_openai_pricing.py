"""Build the project-hosted pricing snapshot from official OpenAI pages."""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_usage_hud.pricing_sync import (  # noqa: E402
    OPENAI_MODELS_URL,
    parse_openai_models_html,
    source_hash,
)

PRICING_URL = "https://platform.openai.com/docs/pricing"
HISTORICAL_SERIES_LIMIT = 2
OUTPUTS = (
    ROOT / "docs" / "openai-pricing-snapshot.json",
    ROOT / "src" / "codex_usage_hud" / "assets" / "openai-pricing-snapshot.json",
)


def fetch(url: str) -> bytes:
    request = Request(url, headers={"Accept": "text/html", "User-Agent": "codex-usage-hud-pricing-sync"})
    with urlopen(request, timeout=30) as response:
        return response.read(2 * 1024 * 1024 + 1)


def validate_sources(models: dict[str, object], pricing: dict[str, object]) -> None:
    # The Models page defines the current Codex catalog. A fixed list would
    # reject retired models and silently omit newly released ones.
    if len(models) < 2:
        raise ValueError("official Models page has insufficient Codex models")
    missing = sorted(models.keys() - pricing.keys())
    if missing:
        raise ValueError("official Pricing page is missing Codex models: " + ", ".join(missing))
    common = sorted(models)
    mismatches = [
        model
        for model in common
        if models[model].input != pricing[model].input
        or models[model].output != pricing[model].output
    ]
    if mismatches:
        raise ValueError(
            "official Models and Pricing pages disagree for: " + ", ".join(mismatches)
        )


def model_series(model: str) -> str | None:
    """Return the version family used for historical retention.

    Examples: ``gpt-6-sol`` -> ``gpt-6`` and ``gpt-5.6-terra`` ->
    ``gpt-5.6``. Models without a numeric family are not retained by the
    historical policy; they remain available as local-only user prices.
    """
    parts = str(model or "").strip().split("-")
    if len(parts) < 2:
        return None
    for index, part in enumerate(parts[1:], start=1):
        if re.fullmatch(r"\d+(?:\.\d+)?[A-Za-z]*", part):
            return "-".join(parts[: index + 1]).casefold()
    return None


def _series_sort_key(series: str) -> tuple[tuple[float, ...], str]:
    numbers = tuple(float(value) for value in re.findall(r"\d+(?:\.\d+)?", series))
    return numbers, series


def _load_snapshot(path: Path | None) -> dict[str, object] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def retain_historical_prices(
    active_prices: dict[str, object],
    previous_payloads: tuple[dict[str, object], ...],
    *,
    checked_at: str,
    limit: int = HISTORICAL_SERIES_LIMIT,
) -> list[dict[str, object]]:
    """Carry forward the last known prices for the two prior model series."""
    active_keys = {str(model).casefold() for model in active_prices}
    active_series = {
        series
        for model in active_prices
        if (series := model_series(model)) is not None
    }
    candidates: dict[str, dict[str, object]] = {}
    for payload in previous_payloads:
        previous_checked_at = str(payload.get("checked_at") or checked_at)
        rows = payload.get("prices")
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            model = str(raw.get("model") or "").strip()
            series = model_series(model)
            if not model or not series or model.casefold() in active_keys or series in active_series:
                continue
            if str(raw.get("catalog_status") or "active").casefold() not in {"active", "historical"}:
                continue
            candidate = dict(raw)
            candidate["model"] = model
            candidate["catalog_status"] = "historical"
            candidate["last_seen_at"] = str(candidate.get("last_seen_at") or previous_checked_at)
            current = candidates.get(model.casefold())
            if current is None or str(candidate["last_seen_at"]) > str(current.get("last_seen_at") or ""):
                candidates[model.casefold()] = candidate

    series_order = sorted(
        {model_series(str(row.get("model") or "")) for row in candidates.values()} - {None},
        key=lambda value: _series_sort_key(str(value)),
        reverse=True,
    )
    retained_series = set(series_order[: max(0, int(limit))])
    return [
        row
        for row in candidates.values()
        if model_series(str(row.get("model") or "")) in retained_series
    ]


def build_snapshot_payload(
    models: dict[str, object],
    prices: dict[str, object],
    *,
    models_body: str | bytes,
    pricing_body: str | bytes,
    previous_payloads: tuple[dict[str, object], ...] = (),
    checked_at: str | None = None,
) -> dict[str, object]:
    validate_sources(models, prices)
    checked = checked_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    active_prices = {model: prices[model] for model in models}
    price_rows = [
        {**price.to_dict(), "catalog_status": "active", "last_seen_at": checked}
        for price in active_prices.values()
    ]
    price_rows.extend(
        retain_historical_prices(
            active_prices,
            previous_payloads,
            checked_at=checked,
        )
    )
    payload = {
        "schema_version": 2,
        "provider": "openai",
        "currency": "USD",
        "unit": "USD_per_1M_tokens",
        "checked_at": checked,
        "sources": [
            {"url": OPENAI_MODELS_URL, "sha256": source_hash(models_body)},
            {"url": PRICING_URL, "sha256": source_hash(pricing_body)},
        ],
        "prices": price_rows,
    }
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous-snapshot", type=Path)
    args = parser.parse_args([] if argv is None else argv)
    models_body = fetch(OPENAI_MODELS_URL)
    pricing_body = fetch(PRICING_URL)
    models = parse_openai_models_html(models_body)
    prices = parse_openai_models_html(pricing_body)
    previous_payloads = tuple(
        payload
        for payload in (
            _load_snapshot(args.previous_snapshot),
            _load_snapshot(ROOT / "docs" / "openai-pricing-snapshot.json"),
        )
        if payload is not None
    )
    payload = build_snapshot_payload(
        models,
        prices,
        models_body=models_body,
        pricing_body=pricing_body,
        previous_payloads=previous_payloads,
    )
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    for output in OUTPUTS:
        output.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
