"""Unit tests for token estimation and billing logic."""

from __future__ import annotations

from datetime import datetime, timezone
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.core.calculator import MODEL_PRICES, UsageCalculator, estimate_tokens
from codex_usage_hud.pricing import PriceVersion


class EstimateTokensTests(unittest.TestCase):
    def test_estimate_tokens_for_empty_string(self) -> None:
        self.assertEqual(estimate_tokens(""), 0)

    def test_estimate_tokens_for_none(self) -> None:
        self.assertEqual(estimate_tokens(None), 0)

    def test_estimate_tokens_for_english_text(self) -> None:
        self.assertEqual(estimate_tokens("abcd"), 1)

    def test_estimate_tokens_for_chinese_text(self) -> None:
        self.assertEqual(estimate_tokens("你好世界"), 3)


class UsageCalculatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calculator = UsageCalculator()

    @staticmethod
    def _version(
        version_id: str,
        *,
        model: str,
        input_price: float,
        effective_at: str,
        provider: str = "",
        base_url: str = "",
    ) -> PriceVersion:
        return PriceVersion.from_mapping(
            {
                "version_id": version_id,
                "model": model,
                "provider": provider,
                "base_url": base_url,
                "input": input_price,
                "cached_input": input_price,
                "cache_write": input_price,
                "output": input_price,
                "reasoning": input_price,
                "effective_at": effective_at,
                "created_at": "2026-08-05T00:00:00Z",
                "created_by": "user_edit",
                "source": "manual",
            }
        )

    def test_price_versions_use_old_before_boundary_and_new_at_boundary(self) -> None:
        old = self._version(
            "old-version",
            model="gpt-5.5",
            input_price=1,
            effective_at="2026-07-01T00:00:00Z",
        )
        new = self._version(
            "new-version",
            model="gpt-5.5",
            input_price=2,
            effective_at="2026-08-05T09:00:00+08:00",
        )
        calculator = UsageCalculator({}, pricing_versions=(old, new))

        before_cost, before_snapshot = calculator.calculate_cost_with_snapshot(
            "gpt-5.5",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=0,
            occurred_at="2026-08-05T00:59:59Z",
        )
        boundary_cost, boundary_snapshot = calculator.calculate_cost_with_snapshot(
            "gpt-5.5",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=0,
            occurred_at="2026-08-05T01:00:00Z",
        )

        self.assertEqual(before_cost, 1.0)
        self.assertEqual(before_snapshot["version_id"], "old-version")
        self.assertEqual(before_snapshot["status"], "versioned")
        self.assertEqual(boundary_cost, 2.0)
        self.assertEqual(boundary_snapshot["version_id"], "new-version")
        self.assertEqual(boundary_snapshot["effective_at"], "2026-08-05T01:00:00Z")

    def test_scope_priority_is_resolved_before_effective_version(self) -> None:
        global_old = self._version(
            "global-old",
            model="gpt-5.5",
            input_price=9,
            effective_at="2026-01-01T00:00:00Z",
        )
        scoped_future = self._version(
            "scoped-future",
            model="gpt-5.5",
            input_price=2,
            provider="vendor-a",
            base_url="https://api.vendor-a.example/v1",
            effective_at="2026-08-01T00:00:00Z",
        )
        calculator = UsageCalculator({}, pricing_versions=(global_old, scoped_future))

        cost, snapshot = calculator.calculate_cost_with_snapshot(
            "gpt-5.5",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=0,
            provider="vendor-a",
            base_url="https://api.vendor-a.example/v1/chat/completions",
            occurred_at="2026-07-31T23:59:59Z",
        )

        self.assertEqual(cost, MODEL_PRICES["gpt-5.5"]["input"])
        self.assertEqual(snapshot["status"], "fallback")
        self.assertIsNone(snapshot["version_id"])

    def test_provider_and_base_url_specific_version_wins(self) -> None:
        versions = (
            self._version(
                "global",
                model="custom-model",
                input_price=1,
                effective_at="2026-01-01T00:00:00Z",
            ),
            self._version(
                "provider",
                model="custom-model",
                input_price=2,
                provider="vendor-a",
                effective_at="2026-01-01T00:00:00Z",
            ),
            self._version(
                "base-url",
                model="custom-model",
                input_price=3,
                provider="vendor-a",
                base_url="https://api.vendor-a.example/v1",
                effective_at="2026-01-01T00:00:00Z",
            ),
        )
        calculator = UsageCalculator({}, pricing_versions=versions)

        cost, snapshot = calculator.calculate_cost_with_snapshot(
            "custom-model",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=0,
            provider="vendor-a",
            base_url="https://api.vendor-a.example/v1/chat",
            occurred_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )

        self.assertEqual(cost, 3.0)
        self.assertEqual(snapshot["version_id"], "base-url")

    def test_pre_version_builtin_fallback_and_unknown_unavailable(self) -> None:
        future = self._version(
            "future-relative-to-event",
            model="gpt-5.5",
            input_price=2,
            effective_at="2026-08-01T00:00:00Z",
        )
        calculator = UsageCalculator({}, pricing_versions=(future,))

        fallback_cost, fallback_snapshot = calculator.calculate_cost_with_snapshot(
            "gpt-5.5",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=0,
            occurred_at="2026-07-01T00:00:00Z",
        )
        unavailable_cost, unavailable_snapshot = calculator.calculate_cost_with_snapshot(
            "unknown-model",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=0,
            occurred_at="2026-07-01T00:00:00Z",
        )

        self.assertEqual(fallback_cost, MODEL_PRICES["gpt-5.5"]["input"])
        self.assertEqual(fallback_snapshot["status"], "fallback")
        self.assertIsNone(unavailable_cost)
        self.assertEqual(unavailable_snapshot["status"], "unavailable")
        self.assertIsNone(unavailable_snapshot["prices"])

    def test_legacy_current_price_fallback_when_no_eligible_version(self) -> None:
        # Regression: a versioned price for a model may have an effective_at that
        # is *after* the usage event (e.g. the user adds the price after already
        # using the model). In that case no versioned profile is eligible, and the
        # calculator must fall back to the user's legacy "current" price
        # (model_prices / provider_settings) instead of only the builtin table.
        # Otherwise past usage becomes "unavailable" even though a price exists.
        versioned = self._version(
            "astra-version",
            model="gpt-6-astra",
            provider="custom",
            input_price=5,
            effective_at="2026-09-08T02:35:52Z",
        )
        legacy_price = {
            "model": "gpt-6-astra",
            "provider": "custom",
            "input": 10.0,
            "cached_input": 1.0,
            "cache_write": 1.0,
            "output": 50.0,
            "reasoning": 50.0,
        }
        calculator = UsageCalculator(
            {"custom/gpt-6-astra": legacy_price},
            pricing_versions=(versioned,),
        )

        past_cost, past_snapshot = calculator.calculate_cost_with_snapshot(
            "gpt-6-astra",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=0,
            provider="custom",
            occurred_at="2026-09-07T02:38:02Z",
        )
        self.assertEqual(past_cost, 10.0)
        self.assertEqual(past_snapshot["status"], "fallback")
        self.assertEqual(past_snapshot["prices"]["input"], 10.0)
        self.assertIsNone(past_snapshot["version_id"])

        future_cost, future_snapshot = calculator.calculate_cost_with_snapshot(
            "gpt-6-astra",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=0,
            provider="custom",
            occurred_at="2026-09-09T00:00:00Z",
        )
        self.assertEqual(future_cost, 5.0)
        self.assertEqual(future_snapshot["version_id"], "astra-version")

    def test_explicit_naive_occurred_at_is_rejected(self) -> None:
        calculator = UsageCalculator({}, pricing_versions=())
        with self.assertRaises(ValueError):
            calculator.price_snapshot(
                "gpt-5.5",
                occurred_at=datetime(2026, 8, 5, 9, 0),
            )

    def test_cached_input_discount_for_gpt_5_5(self) -> None:
        cost = self.calculator.calculate_cost_usd(
            model_name="gpt-5.5",
            input_tokens=1_000_000,
            cached_input_tokens=1_000_000,
            output_tokens=0,
        )
        self.assertEqual(cost, 0.5)

    def test_zero_token_counts_return_zero_cost(self) -> None:
        cost = self.calculator.calculate_cost_usd(
            model_name="gpt-5.4",
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
        )
        self.assertEqual(cost, 0.0)

    def test_gpt_5_6_sol_uses_cc_switch_four_bucket_pricing(self) -> None:
        cost = self.calculator.calculate_cost_usd(
            model_name="gpt-5.6-sol",
            input_tokens=1_000_000,
            cached_input_tokens=600_000,
            cache_write_tokens=100_000,
            output_tokens=200_000,
            reasoning_tokens=50_000,
        )

        self.assertEqual(cost, 8.425)

    def test_reasoning_is_not_billed_twice_when_included_in_output(self) -> None:
        cost = self.calculator.calculate_cost_usd(
            model_name="gpt-5.6-sol",
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=1_000_000,
            reasoning_tokens=400_000,
        )

        self.assertEqual(cost, 30.0)

    def test_model_prefix_matching_uses_base_price_table(self) -> None:
        fallback_cost = self.calculator.calculate_cost_usd(
            model_name="gpt-5.5-fallback",
            input_tokens=1_000_000,
            cached_input_tokens=1_000_000,
            output_tokens=0,
        )
        base_cost = self.calculator.calculate_cost_usd(
            model_name="gpt-5.5",
            input_tokens=1_000_000,
            cached_input_tokens=1_000_000,
            output_tokens=0,
        )
        self.assertEqual(fallback_cost, base_cost)
        self.assertEqual(fallback_cost, MODEL_PRICES["gpt-5.5"]["cached_input"])

    def test_base_url_specific_price_wins_for_same_model_name(self) -> None:
        calculator = UsageCalculator(
            {
                "shared-model": {
                    "input": 1.0,
                    "cached_input": 1.0,
                    "output": 1.0,
                    "reasoning": 1.0,
                },
                "vendor-a/shared-model": {
                    "model": "shared-model",
                    "provider": "vendor-a",
                    "base_url": "https://api.vendor-a.example/v1",
                    "input": 3.0,
                    "cached_input": 3.0,
                    "output": 3.0,
                    "reasoning": 3.0,
                },
                "vendor-b/shared-model": {
                    "model": "shared-model",
                    "provider": "vendor-b",
                    "base_url": "https://api.vendor-b.example/v1",
                    "input": 7.0,
                    "cached_input": 7.0,
                    "output": 7.0,
                    "reasoning": 7.0,
                },
            }
        )

        vendor_a_cost = calculator.calculate_cost_usd(
            model_name="shared-model",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=0,
            provider="vendor-a",
            base_url="https://api.vendor-a.example/v1",
        )
        fallback_cost = calculator.calculate_cost_usd(
            model_name="shared-model",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=0,
            provider="unknown",
            base_url="https://unknown.example/v1",
        )

        self.assertEqual(vendor_a_cost, 3.0)
        self.assertEqual(fallback_cost, 1.0)

    def test_wildcard_model_profile_matches_unknown_model_prefix(self) -> None:
        calculator = UsageCalculator(
            {
                "custom-family": {
                    "model": "custom-*",
                    "input": 2.0,
                    "cached_input": 2.0,
                    "output": 2.0,
                    "reasoning": 2.0,
                }
            }
        )

        cost = calculator.calculate_cost_usd(
            model_name="custom-large",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=0,
        )

        self.assertEqual(cost, 2.0)


if __name__ == "__main__":
    unittest.main()
