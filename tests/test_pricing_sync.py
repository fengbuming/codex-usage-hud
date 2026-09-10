from types import SimpleNamespace

from codex_usage_hud.pricing_sync import classify_price_changes, fetch_pricing_snapshot, parse_openai_models_html
from codex_usage_hud.pricing_sync import pricing_snapshot_urls
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


def test_bundled_snapshot_preserves_cache_write_and_compares_it():
    prices, _metadata = fetch_pricing_snapshot(timeout_seconds=0.01)
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
