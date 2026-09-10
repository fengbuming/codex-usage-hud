"""Build the project-hosted pricing snapshot from official OpenAI pages."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_usage_hud.pricing_sync import (  # noqa: E402
    CODEX_MODEL_IDS,
    OPENAI_MODELS_URL,
    parse_openai_models_html,
    source_hash,
)

PRICING_URL = "https://platform.openai.com/docs/pricing"
OUTPUTS = (
    ROOT / "docs" / "openai-pricing-snapshot.json",
    ROOT / "src" / "codex_usage_hud" / "assets" / "openai-pricing-snapshot.json",
)


def fetch(url: str) -> bytes:
    request = Request(url, headers={"Accept": "text/html", "User-Agent": "codex-usage-hud-pricing-sync"})
    with urlopen(request, timeout=30) as response:
        return response.read(2 * 1024 * 1024 + 1)


def validate_sources(models: dict[str, object], pricing: dict[str, object]) -> None:
    common = sorted(set(models) & set(pricing))
    if len(common) < 2:
        raise ValueError("official pricing sources have insufficient model overlap")
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


def main() -> int:
    models_body = fetch(OPENAI_MODELS_URL)
    pricing_body = fetch(PRICING_URL)
    models = parse_openai_models_html(models_body)
    prices = parse_openai_models_html(pricing_body)
    validate_sources(models, prices)
    prices = {model: price for model, price in prices.items() if model in CODEX_MODEL_IDS}
    if set(prices) != set(CODEX_MODEL_IDS):
        missing = sorted(set(CODEX_MODEL_IDS) - set(prices))
        raise ValueError("official pricing page is missing Codex models: " + ", ".join(missing))
    payload = {
        "schema_version": 2,
        "provider": "openai",
        "currency": "USD",
        "unit": "USD_per_1M_tokens",
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sources": [
            {"url": OPENAI_MODELS_URL, "sha256": source_hash(models_body)},
            {"url": PRICING_URL, "sha256": source_hash(pricing_body)},
        ],
        "prices": [price.to_dict() for price in prices.values()],
    }
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    for output in OUTPUTS:
        output.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
