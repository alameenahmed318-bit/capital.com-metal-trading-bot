"""Offline tests for recovery threshold and optional AI evidence.

No live broker or cloud service needed.
Run: python -m unittest discover -s tests -p 'test_owner_recovery.py' -v
"""
import sys
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from position_action_engine import plan_account_actions

def scenario(positions, signal="BUY", **pm):
    now=datetime.now(timezone.utc)
    bar_time=(int(now.timestamp())//60-1)*60
    snapshot={"symbol":"XAUUSD","timeframe":"PERIOD_M1","positions":positions,
              "received_at":now,
              "closed_candles":[{"time":bar_time,"open":3008.0,
                                 "high":3011.0,"low":3007.5,"close":3010.5}],
              "recovery_armed_at":bar_time-60,
              "tick":{"bid":3009.0,"ask":3009.2}}
    decision={"decision":signal,"confidence":0.70,"entry_atr":3.0,
              "bar_time":bar_time,"context":{"atr_ratio":1.0}}
    settings={"trading_enabled":True,"position_management":{"mode":"AI",
            "recovery_enabled":True,"recovery_loss_trigger":20.0,**pm}}
    return plan_account_actions(snapshot,decision,{"approved":False,"votes":{}},
                                {"approved":True},settings)

class RecoveryTests(unittest.TestCase):
    def test_loss_amount_with_confirmed_new_candle_authorizes_recovery(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.02,
            "open_price":3010.0,"profit":-20.0,"swap":0.0}]
        result=scenario(p)
        self.assertEqual(result["buy"],"RECOVERY_PROPOSAL",result)
        self.assertEqual(result["actions"][0]["lot_cap"],0.06,result)
        self.assertTrue(result["actions"][0]["recovery_armed"])

    def test_mixed_gold_positions_cannot_expand_with_x3_recovery(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.02,"open_price":3010,
            "profit":-22,"swap":0},
           {"symbol":"XAUUSD","type":1,"volume":0.01,"open_price":3010,
            "profit":1,"swap":0}]
        result=scenario(p)
        self.assertEqual(result["actions"],[],result)
        self.assertIn("BUY وSELL",result["reason_ar"])

    def test_opposite_entry_signal_no_longer_blocks_account_recovery(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.01,"open_price":3010,
            "profit":-25,"swap":0}]
        result=scenario(p,signal="SELL")
        intent=result["actions"][0]
        self.assertTrue(intent["recovery_armed"])
        self.assertIn("25",intent["reason_ar"])
        self.assertEqual(intent["action"],"RECOVERY_PROPOSAL")
        self.assertEqual(intent["side"],"BUY")

    def test_account_wide_threshold_never_overrides_mixed_side_safety(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.01,"open_price":3010,
            "profit":-12,"swap":0},
           {"symbol":"XAUUSD","type":1,"volume":0.02,"open_price":3010,
            "profit":-11,"swap":0}]
        result=scenario(p,signal="WAIT")
        self.assertEqual(result["actions"],[],result)
        self.assertIn("حماية الاتجاه",result["reason_ar"])

    def test_existing_recovery_does_not_repeat(self):
        p=[{"symbol":"XAUUSD","type":0,"volume":0.01,"open_price":3010,
            "profit":-25,"swap":0,"comment":"ALZOOL-RECOVERY"}]
        result=scenario(p)
        self.assertEqual(result["actions"],[],result)
        self.assertTrue(result["recovery"]["active_cycle"],result)

    def test_foreign_symbol_cannot_increase_recovery_lots(self):
        # Market-isolated Meeno engine: gold and BTC baskets never share x3.
        p=[{"symbol":"XAUUSD","type":0,"volume":0.02,"open_price":3010,
            "profit":-30,"swap":0},
           {"symbol":"EURUSD","type":0,"volume":0.01,"open_price":1.10,
            "profit":-2,"swap":0}]
        result=scenario(p)
        self.assertEqual(result["actions"],[],result)
        self.assertIn("سوق آخر",result["reason_ar"],result)

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
        self.assertIn("حماية الاتجاه",result["reason_ar"])

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

class IndependentManagementTests(unittest.TestCase):
    def plan(self,price=97,signal="BUY",specialist=.5,ready=False,active=False,**pm):
        now=datetime.now(timezone.utc)
        bar=(int(now.timestamp())//60-1)*60
        snapshot={"symbol":"BTCUSDm","timeframe":"M1","received_at":now,
                  "positions":[{"symbol":"BTCUSDm","type":0,"volume":.02,"open_price":100,"profit":-10,"swap":0}],
                  "tick":{"bid":price,"ask":price+.1,"volume_min":.01,"volume_step":.01}}
        decision={"symbol":"BTCUSDm","decision":signal,"bar_time":bar,"entry_atr":1,
                  "confidence":.8,"master":{"mode":"MASTER",
                    "scores":{"BUY":1 if signal=="BUY" else .1,"SELL":.1 if signal=="BUY" else 1},
                    "arena_shadow":{"status":"SHADOW_READY","symbol":"BTCUSDm","bar_time":bar,
                                    "actions":{"ADDON":{"BUY":specialist},"COOLING":{"BUY":specialist}}}}}
        settings={"trading_enabled":True,"position_management":{"mode":"AI","recovery_enabled":True,
            "recovery_loss_trigger":5,"addon_enabled":True,"cooling_enabled":True,
            "addon_lot":.01,"cooling_lot":.01,**pm}}
        recovery={"armed":not active,"active_cycle":active,"action":"RECOVERY_PROPOSAL" if ready else "HOLD",
                  "side":"BUY","lot_cap":.06 if ready else 0,"candidate_lot":.06,
                  "account_net":-10,"reason_ar":"انتظار تأكيد التعافي",
                  "candle_confirmation":{"approved":ready}}
        with patch("position_action_engine.observe_recovery_arm"),patch("position_action_engine.recovery_intent",return_value=recovery):
            return plan_account_actions(snapshot,decision,{},{"approved":True},settings)
    def test_cooling_can_be_considered_when_recovery_is_waiting(self):
        r=self.plan()
        self.assertEqual(r["buy"],"COOLING_PROPOSAL",r)
        self.assertEqual(r["recovery_wait"]["action"],"HOLD",r)
        self.assertEqual(r["actions"][0]["lot_cap"],.01,r)
        self.assertFalse(r["actions"][0]["execution_authorized"])
    def test_addon_can_be_considered_when_recovery_is_waiting(self):
        r=self.plan(price=103)
        self.assertEqual(r["buy"],"ADDON_PROPOSAL",r)
        self.assertEqual(r["actions"][0]["side"],"BUY")
    def test_no_blind_cooling_against_current_evidence(self):
        for r in (self.plan(signal="SELL"),self.plan(specialist=-.2)):
            self.assertEqual(r["buy"],"HOLD",r)
            self.assertTrue(r["independent_evaluations"])
            self.assertTrue(all(a["lot_cap"]==0 for a in r["actions"]))
    def test_recovery_ready_keeps_priority_and_exact_lot(self):
        r=self.plan(ready=True)
        self.assertEqual(r["buy"],"RECOVERY_PROPOSAL",r)
        self.assertEqual(len(r["actions"]),1,r)
        self.assertEqual(r["actions"][0]["lot_cap"],.06)
    def test_recovery_active_keeps_existing_exclusive_cycle(self):
        r=self.plan(active=True)
        self.assertEqual(r["actions"],[],r)
    def test_owner_caps_still_apply_to_fallback(self):
        r=self.plan(max_total_lots=.02)
        self.assertEqual(r["buy"],"HOLD",r)
        self.assertTrue(all(a["lot_cap"]==0 for a in r["actions"]))
    def test_manual_mode_does_not_change(self):
        r=self.plan(mode="MANUAL")
        self.assertEqual(r["actions"][0]["action"],"HOLD",r)
        self.assertNotIn("independent_evaluations",r)

if __name__=="__main__":
    unittest.main()
