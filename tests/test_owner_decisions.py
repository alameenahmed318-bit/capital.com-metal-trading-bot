"""Offline regression checks for owner-selected caps and nonblocking AI advice.

Run with: python -m unittest discover -s tests -p 'test_owner_decisions.py' -v
No broker connections, server calls or order execution are performed.
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "marketplace" / "backend"))
from decision_guard import entry_council_mode, evaluate_entry_gate
from multi_model_council import risk_guardian, risk_limits_from_settings
from position_action_engine import plan_account_actions


class OwnerDecisionTests(unittest.TestCase):
    def test_legacy_strict_does_not_require_three_models_for_owner(self):
        self.assertEqual(entry_council_mode(
            {"position_management": {"entry_council_mode": "STRICT"}}, True), "ADVISORY")

    def test_explicit_owner_strict_still_respected(self):
        self.assertEqual(entry_council_mode(
            {"position_management": {"entry_council_mode": "STRICT",
                                     "entry_council_mode_user_selected": True}}, True), "STRICT")

    def test_absent_optional_risk_caps_do_not_block(self):
        limits = risk_limits_from_settings({"position_management": {}})
        self.assertEqual(limits, {})
        positions = [{"volume": 0.1}] * 50
        report = risk_guardian({"balance": 1000, "equity": 900, "free_margin": 20},
                               positions, limits)
        self.assertTrue(report["approved"], report)

    def test_explicit_owner_position_cap_is_honored(self):
        limits = risk_limits_from_settings({"position_management": {"max_positions": 2}})
        report = risk_guardian({"balance": 1000, "equity": 1000, "free_margin": 20},
                               [{"volume": 0.01}, {"volume": 0.01}], limits)
        self.assertFalse(report["approved"])

    def test_low_valid_confidence_no_longer_hard_vetoed(self):
        now = datetime.now(timezone.utc)
        d = {"decision": "BUY", "confidence": 0.32, "strategy_id": 5,
             "bar_time": int(now.timestamp()) - 60,
             "entry_atr": 3.0, "entry_reference_price": 3000}
        a = {"balance": 1000, "equity": 980, "free_margin": 200}
        gate = evaluate_entry_gate(d, a, "XAUUSD", now, now, True,
                                   strategy_first=True, require_local_ai=False)
        self.assertTrue(gate["approved"], gate)

    def test_open_position_plan_does_not_wait_for_three_model_council(self):
        snapshot = {"symbol": "XAUUSD", "positions": [
            {"symbol": "XAUUSD", "type": 0, "volume": 0.01,
             "open_price": 2990, "profit": 4, "swap": 0}],
            "tick": {"bid": 3000, "ask": 3000.2}}
        decision = {"decision": "BUY", "confidence": 0.7, "entry_atr": 3,
                    "bar_time": 100, "context": {"atr_ratio": 1}}
        settings = {"trading_enabled": True, "position_management": {
            "mode": "AI", "addon_enabled": True, "addon_lot": 0.01}}
        report = plan_account_actions(snapshot, decision,
                                      {"approved": False}, {"approved": True}, settings)
        self.assertEqual(report["buy"], "ADDON_PROPOSAL", report)


if __name__ == "__main__":
    unittest.main()
