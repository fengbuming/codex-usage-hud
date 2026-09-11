from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from codex_usage_hud.config import UserConfig
from codex_usage_hud.pricing_sync import CODEX_MODEL_IDS, OfficialPrice, classify_price_changes, fetch_pricing_snapshot, merge_pricing_rows, parse_openai_models_html
from codex_usage_hud.pricing_sync import normalize_local_price_scope, pricing_snapshot_urls
from codex_usage_hud.pricing_sync_scheduler import PricingSyncScheduler
from tools.sync_openai_pricing import validate_sources


def test_parser_reads_model_rows_and_rejects_empty_page():
    html = "<table><tr><th>Model</th><th>Input</th><th>Cached</th><th>Output</th></tr><tr><td>gpt-5</td><td>$1.00</td><td>$0.10</td><td>$2.00</td></tr></table>"
    parsed = parse_openai_models_html(html)
    assert parsed["gpt-5"].input == 1
    assert str(parsed["gpt-5"].cached_input) == "0.10"
    assert parsed["gpt-5"].output == 2


def test_scheduler_does_not_poll_until_due_and_uses_failure_backoff():
    now = [0.0]; calls = []; published = []
    config = SimpleNamespace(pricing_sync={"enabled": True, "interval_hours": 24})
    scheduler = PricingSyncScheduler(lambda: config, lambda: calls.append(now[0]) or (_ for _ in ()).throw(ValueError("down")), published.append, clock=lambda: now[0])
    scheduler._next_at = 86400
    assert scheduler.next_run_at == 86400
    assert calls == []
    scheduler._failure_count = 1
    scheduler._next_at = now[0] + 3600
    assert scheduler.next_run_at == 3600


def test_pricing_snapshot_uses_github_then_existing_regional_transports():
    urls = pricing_snapshot_urls()
    assert urls[0].startswith("https://raw.githubusercontent.com/")
    assert urls[1].startswith("https://ghproxy.net/")
    assert urls[2].startswith("https://gh-proxy.com/")


def test_pricing_snapshot_races_regional_transports_instead_of_waiting_serially():
    calls = []
    price = OfficialPrice(model="gpt-6-astra", input=10, output=50)

    def download(url, timeout_seconds, allowed_models):
        calls.append((url, timeout_seconds, frozenset(allowed_models)))
        if "ghproxy.net" in url:
            return {price.model: price}, {"snapshot_url": url, "checked_at": "2026-09-10T08:00:00Z"}
        raise TimeoutError(url)

    with patch("codex_usage_hud.pricing_sync._download_pricing_snapshot", side_effect=download):
        prices, metadata = fetch_pricing_snapshot(
            timeout_seconds=2.5, extra_model_ids=["gpt-5.4", " gpt-5.4-mini ", ""]
        )

    assert prices["gpt-6-astra"].output == 50
    assert "ghproxy.net" in str(metadata["snapshot_url"])
    assert {url for url, _timeout, _allowed in calls} == set(pricing_snapshot_urls())
    assert all(timeout == 2.5 for _url, timeout, _allowed in calls)
    assert all(
        allowed == CODEX_MODEL_IDS | {"gpt-5.4", "gpt-5.4-mini"}
        for _url, _timeout, allowed in calls
    )


def test_models_page_semantic_cards_are_parsed_without_tables():
    html = "<div><span>gpt-6-astra</span><div>Input price</div><div>$10 / Input MTok</div><div>Output price</div><div>$50 / Output MTok</div></div>"
    parsed = parse_openai_models_html(html)
    assert parsed["gpt-6-astra"].input == 10
    assert parsed["gpt-6-astra"].output == 50


def test_pricing_table_uses_short_context_columns_from_first_tier():
    html = "<table><tr><th>Model</th><th>Input</th><th>Cached input</th><th>Cache writes</th><th>Output</th><th>Input</th><th>Cached input</th><th>Cache writes</th><th>Output</th></tr><tr><td>gpt-6-astra</td><td>$10</td><td>$1</td><td>$12.50</td><td>$50</td><td>$20</td><td>$2</td><td>$25</td><td>$75</td></tr></table>"
    price = parse_openai_models_html(html)["gpt-6-astra"]
    assert (price.input, price.cached_input, price.cache_write, price.output) == (
        10,
        1,
        12.5,
        50,
    )


class _PriceLike:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = dict(values)

    def to_dict(self) -> dict[str, object]:
        return dict(self._values)


def test_default_provider_scope_excludes_other_providers_prices():
    # The official comparison only sees the default (Codex App) provider's own
    # table. A non-default provider's price must never stand in for a model the
    # default provider has not priced itself, so the runtime scopes through
    # ``provider_price_table`` instead of the all-provider ``price_table``.
    config = UserConfig.from_dict(
        {
            "model_prices": {"gpt-5.6-sol": {"input": 5, "output": 30}},
            "provider_settings": {
                "app": {"model_prices": {"gpt-5.6-sol": {"input": 4, "output": 20}}},
                "reseller": {"model_prices": {"gpt-6-astra": {"input": 9, "output": 40}}},
            },
            "provider_order": ["app", "reseller"],
        }
    )

    # The all-provider table does surface the reseller's model...
    assert "reseller/gpt-6-astra" in config.price_table()

    # ...but the default provider's scope must not, and its own price must win
    # over the legacy global fallback.
    scope = normalize_local_price_scope(config.provider_price_table("app"))
    assert "gpt-6-astra" not in scope
    assert scope["gpt-5.6-sol"]["input"] == 4.0
    assert scope["gpt-5.6-sol"]["provider"] == "app"

    # With no default provider configured the legacy global table is the fallback.
    fallback = normalize_local_price_scope(config.provider_price_table(""))
    assert fallback["gpt-5.6-sol"]["input"] == 5.0


def test_normalize_local_price_scope_folds_provider_scoped_duplicates():
    # UserConfig.price_table() keys provider-scoped prices as
    # "<provider>/<model>". The official comparison is keyed by the bare model
    # id, so the same model must not surface once per provider.
    table = {
        "gpt-5.6-sol": {"model": "gpt-5.6-sol", "input": 5.0, "output": 30.0},
        "custom/gpt-5.6-sol": {"model": "gpt-5.6-sol", "provider": "custom", "input": 4.0, "output": 20.0},
        "dkby/gpt-5.6-sol": {"model": "gpt-5.6-sol", "provider": "dkby", "input": 4.0, "output": 20.0},
        "hiyo/gpt-5.6-sol": {"model": "gpt-5.6-sol", "provider": "hiyo", "input": 4.0, "output": 20.0},
        "custom/gpt-5.4": {"model": "gpt-5.4", "provider": "custom", "input": 2.5, "output": 15.0},
    }

    normalized = normalize_local_price_scope(table)

    # 5 provider-scoped rows collapse to 2 models, keyed by the bare model id.
    assert sorted(normalized) == ["gpt-5.4", "gpt-5.6-sol"]
    # gpt-5.4 only exists per provider, so that row is the representative.
    assert normalized["gpt-5.4"]["provider"] == "custom"
    # Without a preference the provider-less legacy row wins, as it did before.
    assert normalized["gpt-5.6-sol"]["input"] == 5.0

    # The provider actually in use wins, so its price is the one compared.
    active = normalize_local_price_scope(table, preferred_provider="hiyo")
    assert active["gpt-5.6-sol"]["input"] == 4.0
    assert active["gpt-5.6-sol"]["provider"] == "hiyo"

    official = OfficialPrice(model="gpt-5.6-sol", input=Decimal("4"), output=Decimal("20"))
    # The active provider already matches the snapshot: no field changes, and no
    # spurious "removed" entries for the folded provider copies.
    assert classify_price_changes(active, [official]) == [
        {"model": "gpt-5.4", "kind": "removed"}
    ]

    # A model the snapshot genuinely does not list is reported exactly once,
    # not once per provider copy.
    only_local = normalize_local_price_scope(
        {
            "custom/gpt-5.4": {"model": "gpt-5.4", "provider": "custom"},
            "dkby/gpt-5.4": {"model": "gpt-5.4", "provider": "dkby"},
        }
    )
    assert sorted(only_local) == ["gpt-5.4"]
    removed = [
        item for item in classify_price_changes(only_local, [official])
        if item["kind"] == "removed"
    ]
    assert removed == [{"model": "gpt-5.4", "kind": "removed"}]

    # The comparison table carries one row per model, never "provider/model".
    merged = merge_pricing_rows([official.to_dict()], active, "hiyo")
    assert [row["model"] for row in merged] == ["gpt-5.6-sol", "gpt-5.4"]
    assert all("/" not in str(row["model"]) for row in merged)


def test_merge_pricing_rows_unions_local_models_and_dedupes_by_model_id():
    merged = merge_pricing_rows(
        [{"model": "gpt-6-astra", "input": 10}, {"model": "GPT-6-Astra", "input": 10}],
        {"gpt-6-astra": {"input": 10}, "gpt-5.4": _PriceLike({"input": 2.5})},
        "custom",
    )

    assert [row["model"] for row in merged] == ["gpt-6-astra", "gpt-5.4"]
    assert merged[0]["officialMissing"] is False
    assert merged[1]["officialMissing"] is True
    assert merged[1]["input"] == 2.5
    assert merged[1]["provider"] == "custom"


def test_bundled_snapshot_preserves_cache_write_and_compares_it():
    prices, _metadata = fetch_pricing_snapshot(timeout_seconds=0.01)
    assert set(prices) == set(CODEX_MODEL_IDS)
    price = prices["gpt-6-astra"]
    assert price.cache_write == 12.5
    changes = classify_price_changes(
        {"gpt-6-astra": {**price.to_dict(), "cache_write": 0}},
        [price],
    )
    assert changes == [
        {
            "model": "gpt-6-astra",
            "field": "cache_write",
            "kind": "increased",
            "local": 0.0,
            "official": 12.5,
        }
    ]


def test_snapshot_generation_rejects_disagreement_between_official_pages():
    models = parse_openai_models_html(
        "<div><span>gpt-6-astra</span><div>Input price</div><div>$10</div><div>Output price</div><div>$50</div>"
        "<span>gpt-5.6-terra</span><div>Input price</div><div>$2</div><div>Output price</div><div>$12</div></div>"
    )
    pricing = dict(models)
    validate_sources(models, pricing)
    changed = dict(pricing)
    changed["gpt-6-astra"] = changed["gpt-6-astra"].__class__(
        model="gpt-6-astra", input=20, output=50
    )
    try:
        validate_sources(models, changed)
    except ValueError as exc:
        assert "gpt-6-astra" in str(exc)
    else:
        raise AssertionError("source disagreement was accepted")
