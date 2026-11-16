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

    def test_account_wide_loss_and_x3_total_lots_with_hedged_positions(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.02,"open_price":3010,
            "profit":-22,"swap":0},
           {"symbol":"XAUUSD","type":1,"volume":0.01,"open_price":3010,
            "profit":1,"swap":0}]
        result=scenario(p)
        self.assertEqual(result["buy"],"RECOVERY_PROPOSAL",result)
        self.assertEqual(result["actions"][0]["lot_cap"],0.09,result)

    def test_opposite_entry_signal_no_longer_blocks_account_recovery(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.01,"open_price":3010,
            "profit":-25,"swap":0}]
        result=scenario(p,signal="SELL")
        intent=result["actions"][0]
        self.assertTrue(intent["recovery_armed"])
        self.assertIn("25",intent["reason_ar"])
        self.assertEqual(intent["action"],"RECOVERY_PROPOSAL")
        self.assertEqual(intent["side"],"BUY")

    def test_account_wide_threshold_not_per_side(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.01,"open_price":3010,
            "profit":-12,"swap":0},
           {"symbol":"XAUUSD","type":1,"volume":0.02,"open_price":3010,
            "profit":-11,"swap":0}]
        result=scenario(p,signal="WAIT")
        actions=[a for a in result["actions"] if a["action"]=="RECOVERY_PROPOSAL"]
        self.assertEqual(len(actions),1,result)
        self.assertEqual(actions[0]["side"],"BUY",result)
        self.assertEqual(actions[0]["lot_cap"],0.09,result)

    def test_existing_recovery_does_not_repeat(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.01,"open_price":3010,
            "profit":-25,"swap":0,"comment":"ALZOOL-RECOVERY"}]
        result=scenario(p)
        self.assertEqual(result["actions"][0]["action"],"HOLD",result)
        self.assertTrue(result["actions"][0]["recovery_armed"])

    def test_account_other_symbol_lots_count_toward_exact_x3(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.02,"open_price":3010,
            "profit":-30,"swap":0},
           {"symbol":"EURUSD","type":0,"volume":0.01,"open_price":1.10,
            "profit":-2,"swap":0}]
        result=scenario(p)
        self.assertEqual(result["actions"][0]["lot_cap"],0.09,result)

    def test_explicit_owner_cap_remains_effective(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.02,"open_price":3010,
            "profit":-30,"swap":0}]
        result=scenario(p,recovery_max_lot=0.05)
        self.assertEqual(result["actions"][0]["action"],"HOLD",result)


    def test_side_loss_does_not_trigger_if_account_above_threshold(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.02,"open_price":3010,
            "profit":-25,"swap":0},
           {"symbol":"XAUUSD","type":1,"volume":0.01,"open_price":3010,
            "profit":10,"swap":0}]
        result=scenario(p)
        self.assertNotIn("RECOVERY_PROPOSAL",[a["action"] for a in result["actions"]],result)
        self.assertFalse(result["recovery"]["armed"],result)

    def test_open_recovery_disables_other_management_proposals(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.01,"open_price":3000,
            "profit":5,"swap":0,"comment":"ALZOOL-RECOVERY"}]
        result=scenario(p,recovery_enabled=True,addon_enabled=True,addon_lot=0.01)
        self.assertTrue(result["recovery"]["active_cycle"],result)
        self.assertEqual(result["actions"],[],result)

    def test_unconfigured_addon_max_zero_is_unlimited(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.01,"open_price":3004,
            "profit":3,"swap":0} for _ in range(5)]
        result=scenario(p,recovery_enabled=False,addon_enabled=True,
                        addon_lot=0.01,addon_max=0)
        self.assertEqual(result["buy"],"ADDON_PROPOSAL",result)

if __name__=="__main__":
    unittest.main()
