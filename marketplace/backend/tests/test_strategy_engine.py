"""Regression tests for the central Quant Engine and council output."""
import math
import unittest

from strategy_engine import (
    STRATEGIES, COMBINATIONS, evaluate_market,
    analyze_market_context, adaptive_gold_parameters,
)


def sample_rates(count=140):
    rows = []
    previous = 2300.0
    for i in range(count):
        # Deterministic gently trending candles with bounded oscillation.
        close = 2300.0 + i * 0.22 + math.sin(i / 4.0) * 1.8
        open_price = previous
        high = max(open_price, close) + 0.65 + (i % 3) * 0.05
        low = min(open_price, close) - 0.60 - (i % 2) * 0.05
        rows.append({
            "time": 1_800_000_000 + i * 60,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + (i % 11) * 7,
        })
        previous = close
    return rows


class QuantEngineRegressionTests(unittest.TestCase):
    def test_candidate_catalog_has_exact_approved_counts(self):
        self.assertEqual(len(STRATEGIES), 200)
        self.assertEqual(len(COMBINATIONS), 1000)
        self.assertEqual(len({x["id"] for x in STRATEGIES}), 200)
        self.assertEqual(len({x["id"] for x in COMBINATIONS}), 1000)

    def test_every_combination_references_five_valid_variants(self):
        valid_ids = {x["id"] for x in STRATEGIES}
        for combo in COMBINATIONS:
            self.assertEqual(len(combo["members"]), 5)
            self.assertEqual(len(set(combo["members"])), 5)
            self.assertTrue(set(combo["members"]).issubset(valid_ids))

    def test_market_output_schema_and_counts_are_stable(self):
        rates = sample_rates()
        result = evaluate_market(rates)
        self.assertIn(result["decision"], ("BUY", "SELL", "WAIT"))
        self.assertEqual(result["strategy_count"], 200)
        self.assertEqual(result["combination_count"], 1000)
        self.assertEqual(len(result["votes"]), 200)
        self.assertEqual(len(result["combination_votes"]), 1000)
        self.assertEqual(len(result["family_votes"]), 20)
        self.assertIn(result["decision"], result["council"]["direction"])
        self.assertGreaterEqual(result["confidence"], 0.0)
        self.assertLessEqual(result["confidence"], 1.0)
        self.assertGreater(result["entry_reference_price"], 0.0)
        self.assertGreater(result["entry_atr"], 0.0)

    def test_insufficient_candles_never_create_entry(self):
        result = evaluate_market(sample_rates(40))
        self.assertEqual(result["decision"], "WAIT")
        self.assertEqual(result["strategy_id"], 0)

    def test_market_context_and_adaptive_parameters_are_finite(self):
        rates = sample_rates()
        context = analyze_market_context(rates)
        adaptive = adaptive_gold_parameters(rates, context)
        for key in ("atr", "atr_ratio", "trend_strength", "adx", "buy_quality", "sell_quality"):
            self.assertTrue(math.isfinite(float(context[key])), key)
        self.assertGreater(context["atr"], 0.0)
        self.assertIn(adaptive["regime"], ("اتجاه صاعد", "اتجاه هابط", "تذبذب جانبي", "تقلب مرتفع"))
        self.assertGreaterEqual(adaptive["ema_fast"], 5)
        self.assertLess(adaptive["ema_fast"], adaptive["ema_slow"])

    def test_evaluation_does_not_mutate_market_candles(self):
        rates = sample_rates()
        before = [dict(row) for row in rates]
        evaluate_market(rates)
        self.assertEqual(rates, before)


if __name__ == "__main__":
    unittest.main()
