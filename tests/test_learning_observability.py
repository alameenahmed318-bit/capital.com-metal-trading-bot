"""Offline safety checks for live learning evidence and market isolation."""
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "marketplace" / "backend"))
from learning_observability import decision_audit_event, live_learning_health


class LearningObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 22, 8, 20, tzinfo=timezone.utc)
        self.bar = 1795335540
        self.profile = {"closed_positions": 117, "processed_positions": 17,
                        "unmatched_positions": 100, "coverage_percent": 14.5}

    def test_audit_does_not_claim_trades_or_validated_model(self):
        event = decision_audit_event("client-a", "BTCUSDm", "PERIOD_M1", self.bar,
                                     {"status": "APPLIED", "applied_strategies": 5,
                                      "applied_combinations": 8, "processed_positions": 17})
        self.assertEqual(event["event_type"], "LEARNING_DECISION_AUDIT")
        self.assertTrue(event["payload"]["applied_to_candle_analysis"])
        self.assertFalse(event["payload"]["trade_execution_proven"])
        self.assertFalse(event["payload"]["validation_proven"])
        self.assertEqual(event["payload"]["market"], "BTCUSD")
        self.assertEqual(event["payload"]["effective_non_neutral_strategies"], 5)
        self.assertNotIn("trading_enabled", event["payload"])

    def test_dedupe_one_event_per_fifteen_minute_bucket_per_market(self):
        a = decision_audit_event("owner", "BTCUSDm", "PERIOD_M1", 1800, {"status": "APPLIED"})
        b = decision_audit_event("owner", "BTCUSDm", "PERIOD_M1", 1810, {"status": "APPLIED"})
        c = decision_audit_event("owner", "XAUUSD", "PERIOD_M1", 1810, {"status": "APPLIED"})
        d = decision_audit_event("owner", "BTCUSDm", "PERIOD_M1", 2700, {"status": "APPLIED"})
        self.assertEqual(a["event_id"], b["event_id"])
        self.assertNotEqual(a["event_id"], c["event_id"])
        self.assertNotEqual(a["event_id"], d["event_id"])

    def test_non_applied_result_is_reported_without_false_success(self):
        event = decision_audit_event("owner", "XAUUSD", "PERIOD_M1", self.bar,
                                     {"status": "NEUTRAL_NO_CHANGE"})
        self.assertFalse(event["payload"]["applied_to_candle_analysis"])

    def test_unsupported_market_and_malformed_data_do_not_create_event(self):
        self.assertIsNone(decision_audit_event("owner", "ETHUSD", "PERIOD_M1", 2, {}))
        self.assertIsNone(decision_audit_event("", "XAUUSD", "PERIOD_M1", 2, {}))
        self.assertIsNone(decision_audit_event("owner", "XAUUSD", "PERIOD_M1", 0, {}))
        self.assertIsNone(decision_audit_event("owner", "XAUUSD", "PERIOD_M1", 2, None))
        self.assertEqual(decision_audit_event("owner", "BTCUSDm", "PERIOD_M1", 2,
                         {"applied_strategies": "nan"})["payload"]["effective_non_neutral_strategies"], 0)

    def test_fresh_matching_market_and_bar_marks_weight_application(self):
        current = {"received_at": self.now - timedelta(seconds=2), "symbol": "BTCUSDm",
                   "closed_candles": [{"time": self.bar}]}
        decision = {"bar_time": self.bar, "learning": {"status": "APPLIED"}}
        result = live_learning_health("BTCUSDm", current, decision, self.profile, self.now)
        self.assertTrue(result["terminal_fresh"])
        self.assertTrue(result["decision_current"])
        self.assertTrue(result["learned_weights_used_in_analysis"])
        self.assertFalse(result["trade_execution_proven"])
        self.assertEqual(result["unattributed_positions"], 100)

    def test_stale_telemetry_cannot_claim_current_application(self):
        current = {"received_at": self.now - timedelta(minutes=3), "symbol": "BTCUSDm",
                   "closed_candles": [{"time": self.bar}]}
        decision = {"bar_time": self.bar, "learning": {"status": "APPLIED"}}
        result = live_learning_health("BTCUSDm", current, decision, self.profile, self.now)
        self.assertFalse(result["terminal_fresh"])
        self.assertFalse(result["learned_weights_used_in_analysis"])

    def test_no_cross_market_learning_even_when_bar_matches(self):
        current = {"received_at": self.now, "symbol": "XAUUSD",
                   "closed_candles": [{"time": self.bar}]}
        decision = {"bar_time": self.bar, "learning": {"status": "APPLIED"}}
        result = live_learning_health("BTCUSDm", current, decision, self.profile, self.now)
        self.assertFalse(result["decision_current"])

    def test_fresh_heartbeat_does_not_validate_old_weekend_candle(self):
        old_bar = self.bar - 86400
        current = {"received_at": self.now, "symbol": "XAUUSD",
                   "closed_candles": [{"time": old_bar}]}
        decision = {"bar_time": old_bar, "learning": {"status": "APPLIED"}}
        result = live_learning_health("XAUUSD", current, decision, self.profile, self.now)
        self.assertTrue(result["terminal_fresh"])
        self.assertFalse(result["closed_candle_fresh"])
        self.assertGreater(result["last_closed_bar_age_seconds"], 180)
        self.assertFalse(result["decision_current"])
        self.assertFalse(result["learned_weights_used_in_analysis"])

    def test_outdated_decision_cannot_claim_current_weights(self):
        current = {"received_at": self.now, "symbol": "XAUUSD",
                   "closed_candles": [{"time": self.bar}]}
        decision = {"bar_time": self.bar - 60, "learning": {"status": "APPLIED"}}
        result = live_learning_health("XAUUSD", current, decision, self.profile, self.now)
        self.assertFalse(result["decision_current"])
        self.assertFalse(result["learned_weights_used_in_analysis"])


if __name__ == "__main__":
    unittest.main()
