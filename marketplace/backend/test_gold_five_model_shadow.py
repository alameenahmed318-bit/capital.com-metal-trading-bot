"""Regression tests for gold five-model shadow. No broker or database access."""
import copy
import unittest

from master_balanced import gold_five_model_shadow

class GoldFiveModelShadowTests(unittest.TestCase):
    def setUp(self):
        self.trial = {"direction": "WAIT", "scores": {
            s: {"components": {"strategy": .8, "market": .8},
                "failed_checks": [], "strategy_id": 1, "combination_id": 415}
            for s in ("BUY", "SELL")}}

    def check(self, forecasts, expected, fail=None):
        if fail:
            self.trial["scores"]["BUY"]["failed_checks"] = [fail]
        before = copy.deepcopy(self.trial)
        result = gold_five_model_shadow(self.trial, forecasts)
        self.assertEqual(result["direction"], expected)
        self.assertFalse(result["execution_authorized"])
        self.assertEqual(result["mode"], "SHADOW_ONLY")
        self.assertEqual(self.trial, before)
        self.assertAlmostEqual(sum(result["weights"].values()), 1)

    def test_buy(self):
        self.check({"Chronos-2": .9}, "BUY")

    def test_sell(self):
        self.check({"TimesFM-2.5": -.9}, "SELL")

    def test_retired(self):
        self.check({"XGBoost": 1, "LightGBM": 1}, "WAIT")

    def test_missing(self):
        self.check({}, "WAIT")

    def test_weak(self):
        self.check({"CatBoost": .06}, "WAIT")

    def test_opposition(self):
        self.check({"Chronos-2": .9, "TimesFM-2.5": -.9}, "WAIT")

    def test_strategy_guard(self):
        self.check({"Chronos-2": .9}, "WAIT", "strategy")

    def test_price_guard(self):
        self.check({"Chronos-2": .9}, "WAIT", "price")

if __name__ == "__main__":
    unittest.main()
