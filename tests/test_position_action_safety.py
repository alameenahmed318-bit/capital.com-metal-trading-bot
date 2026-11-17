"""Verify proposals, recovery arithmetic and explicit safety caps without placing orders."""
import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from position_action_engine import plan_account_actions
from central_action_protocol import _allowed
from app import entry_direction_capacity

def pos(ticket,volume,profit=-20,open_price=101,symbol="XAUUSD",side=0):
    return {"ticket":str(ticket),"symbol":symbol,"type":side,"volume":volume,
        "profit":profit,"swap":0,"open_price":open_price,"comment":"manual"}
def scenario(positions, pm=None):
    settings={"trading_enabled":True,"position_management":{
        "mode":"AI","recovery_enabled":True,"recovery_loss_trigger":10,
        "recovery_max_lot":0.20,"cooling_enabled":False,"addon_enabled":False,
        "max_total_lots":0.50,"max_positions_per_direction":3,"central_actions_enabled":True,
        **(pm or {})}}
    snap={"symbol":"XAUUSD","tick":{"bid":98,"ask":98.1,"point_size":0.01},
        "account":{"login":"123"},"positions":positions,
        "received_at":datetime.now(timezone.utc)}
    dec={"decision":"BUY","bar_time":int(datetime.now(timezone.utc).timestamp())-30,
         "confidence":0.9,"entry_atr":1.0}
    return snap,dec,settings

class PositionActionSafety(unittest.TestCase):
    def test_recovery_x3_uses_sum_not_largest(self):
        snap,d,settings=scenario([pos(1,.01),pos(2,.02)])
        p=plan_account_actions(snap,d,{"approved":True},{"approved":True},settings)
        self.assertEqual(p["actions"][0]["action"],"RECOVERY_PROPOSAL")
        self.assertAlmostEqual(p["actions"][0]["lot_cap"],.09)
    def test_recovery_never_silently_shrinks(self):
        snap,d,settings=scenario([pos(1,.01),pos(2,.02)],{"recovery_max_lot":.05})
        p=plan_account_actions(snap,d,{"approved":True},{"approved":True},settings)
        self.assertEqual(p["actions"][0]["action"],"HOLD")
        self.assertEqual(p["actions"][0]["lot_cap"],0)
    def test_same_symbol_only_and_profitable_lots_excluded(self):
        snap,d,settings=scenario([pos(1,.01),pos(2,.02),pos(3,.5,100,symbol="EURUSD")])
        p=plan_account_actions(snap,d,{"approved":True},{"approved":True},settings)
        self.assertEqual(p["actions"][0]["action"],"HOLD")
        self.assertEqual(p["actions"][0]["lot_cap"],0)
    def test_direction_cap_applies_to_manual_positions(self):
        snap,d,settings=scenario([pos(1,.01),pos(2,.02)],{"max_positions_per_direction":2})
        p=plan_account_actions(snap,d,{"approved":True},{"approved":True},settings)
        self.assertEqual(p["actions"][0]["action"],"HOLD")
    def test_manual_cooling_still_requires_council(self):
        snap,d,settings=scenario([pos(1,.01,profit=-2,open_price=98.2)],{
            "mode":"MANUAL","recovery_enabled":False,"cooling_enabled":True,
            "cooling_max":3,"cooling_lot":0.02,"cooling_distance_points":10,
            "cooling_max_lot":0.05})
        p=plan_account_actions(snap,d,{"approved":True},{"approved":True},settings)
        self.assertEqual(p["actions"][0]["action"],"COOLING_PROPOSAL")
        self.assertEqual(p["actions"][0]["lot_cap"],0.02)
        gate={"approved":True}
        auth=_allowed("client",snap,d,gate,{"approved":True},{"approved":True},settings,
                      datetime.now(timezone.utc))
        self.assertTrue(auth["execution_ready"])
        auth2=_allowed("client",snap,d,gate,{"approved":False},{"approved":True},settings,
                       datetime.now(timezone.utc))
        self.assertFalse(auth2["execution_ready"])
    def test_entry_blocked_at_two_per_direction(self):
        positions=[pos(1,.01),pos(2,.02),pos(3,.01,side=1)]
        result=entry_direction_capacity(positions,"XAUUSD","BUY",{
            "position_management":{"max_positions_per_direction":2}})
        self.assertFalse(result["approved"])
        self.assertEqual(result["open_same_side"],2)
        self.assertFalse(entry_direction_capacity(positions,"XAUUSD","SELL",{
            "position_management":{"max_positions_per_direction":2}})["approved"])
    def test_opposite_gold_rejected_even_without_direction_cap(self):
        sell=[pos(1,.01,side=1)]
        report=entry_direction_capacity(sell,"XAUUSD","BUY",{"position_management":{}})
        self.assertFalse(report["approved"],report)
        self.assertEqual(report["open_opposite_side"],1)
        self.assertIn("معاكسة",report["reason_ar"])
        self.assertTrue(entry_direction_capacity(sell,"XAUUSD","SELL",{"position_management":{}})["approved"])

    def test_other_symbols_do_not_block_gold_entry(self):
        eur=[pos(1,.1,symbol="EURUSD",side=1)]
        self.assertTrue(entry_direction_capacity(eur,"XAUUSD","BUY",{"position_management":{}})["approved"])

    def test_mixed_gold_blocks_new_entry_addons_cooling_and_recovery(self):
        positions=[pos(1,.01,side=0),pos(2,.01,side=1)]
        snap,decision,settings=scenario(positions,{
            "recovery_enabled":True,"addon_enabled":True,"cooling_enabled":True})
        for direction in ("BUY","SELL"):
            report=entry_direction_capacity(positions,"XAUUSD",direction,settings)
            self.assertFalse(report["approved"])
        planned=plan_account_actions(snap,decision,{"approved":True},{"approved":True},settings)
        self.assertEqual(planned["actions"],[])
        self.assertIn("BUY وSELL",planned["reason_ar"])
        report=_allowed("owner",snap,decision,{"approved":True},{"approved":True},
                         settings,datetime.now(timezone.utc))
        self.assertFalse(report["execution_ready"])

    def test_ea_disallows_opposite_even_with_legacy_input_false(self):
        source=(Path(__file__).resolve().parents[1]/"mt5_client"/"ALZOOL_Central_Client.mq5").read_text(encoding="utf-8")
        self.assertIn('InpBlockOppositePositions = true;',source)
        self.assertNotIn('if(!InpBlockOppositePositions) return true;',source)
        self.assertIn('if(!IsAccountDirectionAllowed(direction))',source)
        self.assertIn('!IsAccountDirectionAllowed(side)',source)

    def test_group_close_retry_blocks_new_entry_and_survives_restart(self):
        source=(Path(__file__).resolve().parents[1]/"mt5_client"/"ALZOOL_Central_Client.mq5").read_text(encoding="utf-8")
        self.assertIn("g_groupClosePending",source)
        self.assertIn("GlobalVariableSet(g_groupCloseKey,1.0)",source)
        self.assertIn("ClosePositionTicket(ticket,\"group-pending\")",source)
        self.assertIn("if(g_groupClosePending) return;",source)
        self.assertNotIn("double rawLots=totalLots*2.0;",source)
        self.assertNotIn("AL-ZOOL ADD-ON/COOLING WAIT: no action-specific",source)
    def test_owner_learning_is_bound_to_own_license(self):
        source=(Path(__file__).resolve().parents[1]/"marketplace"/"backend"/"app.py").read_text(encoding="utf-8")
        self.assertNotIn('select=client_id&order=uploaded_at.desc&limit=1',source)
        self.assertNotIn('select=client_id&order=created_at.desc&limit=1',source)
    def test_no_positions_no_recovery(self):
        snap,d,settings=scenario([])
        p=plan_account_actions(snap,d,{"approved":True},{"approved":True},settings)
        self.assertEqual(p["actions"],[])
if __name__=="__main__":unittest.main()
