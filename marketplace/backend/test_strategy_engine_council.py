import unittest

from strategy_engine import evaluate_execution_council


class ExecutionCouncilTests(unittest.TestCase):
    def test_matching_direction_is_approved_in_normal_market(self):
        result = evaluate_execution_council(
            "BUY",
            {"buy_trap": False, "sell_trap": False, "atr_ratio": 1.1, "regime": "اتجاه صاعد"},
            {"direction": "BUY"},
        )
        self.assertTrue(result["approved"])
        self.assertEqual(result["final_decision"] if "final_decision" in result else result["candidate"], "BUY")

    def test_opposite_council_vote_blocks_entry(self):
        result = evaluate_execution_council(
            "BUY",
            {"buy_trap": False, "atr_ratio": 1.0},
            {"direction": "SELL"},
        )
        self.assertFalse(result["approved"])
        self.assertIn("مجلس التصويت لم يوافق على اتجاه الإشارة", result["reasons"])

    def test_price_trap_blocks_matching_vote(self):
        result = evaluate_execution_council(
            "BUY",
            {"buy_trap": True, "atr_ratio": 1.0},
            {"direction": "BUY"},
        )
        self.assertFalse(result["approved"])
        self.assertIn("رُصد احتمال فخ شراء", result["reasons"])

    def test_extreme_volatility_blocks_entry(self):
        result = evaluate_execution_council(
            "SELL",
            {"sell_trap": False, "atr_ratio": 2.2},
            {"direction": "SELL"},
        )
        self.assertFalse(result["approved"])
        self.assertIn("التقلب السعري مرتفع بصورة غير طبيعية", result["reasons"])

    def test_wait_is_never_approved(self):
        result = evaluate_execution_council(
            "WAIT",
            {"atr_ratio": 1.0},
            {"direction": "WAIT"},
        )
        self.assertFalse(result["approved"])


if __name__ == "__main__":
    unittest.main()
