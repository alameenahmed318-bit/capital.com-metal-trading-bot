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
        self.assertTrue(self.gate()["approved"])

    def test_trading_off_fails_closed(self):
        self.assertFalse(self.gate(trading_enabled=False)["approved"])

    def test_stale_telemetry_fails_closed(self):
        self.assertFalse(self.gate(received_at=self.now-timedelta(seconds=31))["approved"])

    def test_family_disagreement_fails_closed(self):
        self.decision["family_votes"] = {"A": "SELL", "B": "BUY"}
        self.assertFalse(self.gate()["approved"])

    def test_missing_account_fails_closed(self):
        self.assertFalse(self.gate(account={})["approved"])

    def test_no_free_margin_fails_closed(self):
        self.account["free_margin"] = 0
        self.assertFalse(self.gate()["approved"])

    def test_wait_signal_fails_closed(self):
        self.decision["decision"] = "WAIT"
        self.assertFalse(self.gate()["approved"])


if __name__ == "__main__":
    unittest.main()
