import unittest
from datetime import datetime, timezone, timedelta
from decision_guard import evaluate_entry_gate


class EntryGateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.decision = {
            "decision": "BUY", "confidence": 0.72, "strategy_id": 12,
            "bar_time": int(self.now.timestamp()) - 60,
            "entry_reference_price": 2350.0, "entry_atr": 3.0,
            "context": {"atr": 3.0},
            "family_votes": {"EMA Trend": "BUY", "RSI Momentum": "BUY", "MACD": "SELL"},
            "combination_summary": {"BUY": 420, "SELL": 180, "WAIT": 400},
        }
        self.account = {"balance": 1000, "equity": 990, "free_margin": 800}

    def gate(self, **overrides):
        args = dict(decision=self.decision, account=self.account, symbol="XAUUSD",
                    received_at=self.now, now=self.now, trading_enabled=True)
        args.update(overrides)
        return evaluate_entry_gate(**args)

    def test_valid_council_and_risk_pass(self):
        result = self.gate()
        self.assertTrue(result["approved"])
        self.assertTrue(result["authorization_id"])
        self.assertGreater(result["expires_at_unix"], int(self.now.timestamp()))

    def test_trading_off_fails_closed(self):
        self.assertFalse(self.gate(trading_enabled=False)["approved"])

    def test_stale_telemetry_fails_closed(self):
        self.assertFalse(self.gate(received_at=self.now-timedelta(seconds=31))["approved"])

    def test_future_telemetry_fails_closed(self):
        self.assertFalse(self.gate(received_at=self.now+timedelta(seconds=3))["approved"])

    def test_family_disagreement_fails_closed(self):
        self.decision["family_votes"] = {"A": "SELL", "B": "BUY"}
        self.assertFalse(self.gate()["approved"])

    def test_missing_family_votes_fails_closed(self):
        self.decision.pop("family_votes")
        self.assertFalse(self.gate()["approved"])

    def test_combination_tie_fails_closed(self):
        self.decision["combination_summary"] = {"BUY": 200, "SELL": 200, "WAIT": 600}
        self.assertFalse(self.gate()["approved"])

    def test_missing_account_fails_closed(self):
        self.assertFalse(self.gate(account={})["approved"])

    def test_no_free_margin_fails_closed(self):
        self.account["free_margin"] = 0
        self.assertFalse(self.gate()["approved"])

    def test_invalid_symbol_fails_closed(self):
        self.assertFalse(self.gate(symbol="EURUSD")["approved"])

    def test_low_confidence_fails_closed(self):
        self.decision["confidence"] = 0.44
        self.assertFalse(self.gate()["approved"])

    def test_invalid_strategy_id_fails_closed(self):
        self.decision["strategy_id"] = 0
        self.assertFalse(self.gate()["approved"])

    def test_invalid_closed_bar_fails_closed(self):
        self.decision["bar_time"] = 0
        self.assertFalse(self.gate()["approved"])

    def test_missing_quant_data_fails_closed(self):
        self.decision["context"] = {"atr": 0}
        self.decision["entry_atr"] = 0
        self.assertFalse(self.gate()["approved"])

    def test_wait_signal_fails_closed(self):
        self.decision["decision"] = "WAIT"
        self.assertFalse(self.gate()["approved"])

    def test_required_local_ai_missing_fails_closed(self):
        result = self.gate(require_local_ai=True)
        self.assertFalse(result["approved"])
        self.assertEqual(result["checks"][-1]["check"], "local_ai")

    def test_required_local_ai_disagreement_fails_closed(self):
        advice = {"bar_time": self.decision["bar_time"], "direction": "SELL",
                  "confidence": 0.9, "age_seconds": 1}
        self.assertFalse(self.gate(local_ai_advice=advice, require_local_ai=True)["approved"])

    def test_required_local_ai_stale_vote_fails_closed(self):
        advice = {"bar_time": self.decision["bar_time"], "direction": "BUY",
                  "confidence": 0.9, "age_seconds": 21}
        self.assertFalse(self.gate(local_ai_advice=advice, require_local_ai=True)["approved"])

    def test_required_local_ai_matching_vote_passes(self):
        advice = {"bar_time": self.decision["bar_time"], "direction": "BUY",
                  "confidence": 0.8, "age_seconds": 1}
        result = self.gate(local_ai_advice=advice, require_local_ai=True)
        self.assertTrue(result["approved"])
        self.assertEqual(result["checks"][-1]["check"], "local_ai_vote")


if __name__ == "__main__":
    unittest.main()
