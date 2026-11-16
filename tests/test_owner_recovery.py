"""Offline tests for recovery threshold and optional AI evidence.

No live broker or cloud service needed.
Run: python -m unittest discover -s tests -p 'test_owner_recovery.py' -v
"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from position_action_engine import plan_account_actions

def scenario(positions, signal="BUY", **pm):
    snapshot={"symbol":"XAUUSD","positions":positions,
              "tick":{"bid":3009.0,"ask":3009.2}}
    decision={"decision":signal,"confidence":0.70,"entry_atr":3.0,
              "bar_time":1794815340,"context":{"atr_ratio":1.0}}
    settings={"trading_enabled":True,"position_management":{"mode":"AI",
            "recovery_enabled":True,"recovery_loss_trigger":20.0,**pm}}
    return plan_account_actions(snapshot,decision,{"approved":False,"votes":{}},
                                {"approved":True},settings)

class RecoveryTests(unittest.TestCase):
    def test_loss_amount_arms_recovery_without_extra_atr_gate(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.02,
            "open_price":3010.0,"profit":-20.0,"swap":0.0}]
        result=scenario(p)
        self.assertEqual(result["buy"],"RECOVERY_PROPOSAL",result)
        self.assertEqual(result["actions"][0]["lot_cap"],0.06,result)
        self.assertTrue(result["actions"][0]["recovery_armed"])

    def test_opposite_position_does_not_globally_disable_buy_recovery(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.02,"open_price":3010,
            "profit":-20,"swap":0},
           {"symbol":"XAUUSD","type":1,"volume":0.01,"open_price":3010,
            "profit":1,"swap":0}]
        result=scenario(p)
        self.assertEqual(result["buy"],"RECOVERY_PROPOSAL",result)

    def test_recovery_wait_reason_shows_loss_and_mismatched_ai_direction(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.01,"open_price":3010,
            "profit":-25,"swap":0}]
        result=scenario(p,signal="SELL")
        hold=[a for a in result["actions"] if a["side"]=="BUY"][0]
        self.assertTrue(hold["recovery_armed"])
        self.assertIn("25",hold["reason_ar"])
        self.assertEqual(hold["action"],"HOLD")

    def test_unconfigured_addon_max_zero_is_unlimited(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.01,"open_price":3004,
            "profit":3,"swap":0} for _ in range(5)]
        result=scenario(p,recovery_enabled=False,addon_enabled=True,
                        addon_lot=0.01,addon_max=0)
        self.assertEqual(result["buy"],"ADDON_PROPOSAL",result)

if __name__=="__main__":
    unittest.main()
