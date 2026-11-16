"""Broker-step geometric sizing and live add/cool decision regression tests."""
import sys
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from position_sizing import next_action_lot,count_action
from position_action_engine import plan_account_actions
from central_action_protocol import _allowed


def p(side="BUY",comment="",vol=.02,opened=3000,profit=5.0):
    return {"symbol":"XAUUSD","type":0 if side=="BUY" else 1,"volume":vol,
            "comment":comment,"open_price":opened,"profit":profit,"swap":0}

def config(**updates):
    return {"trading_enabled":True,"position_management":{
        "mode":"AI","addon_enabled":True,"cooling_enabled":True,
        "addon_lot":.02,"cooling_lot":.02,"addon_multiplier":1.5,
        "cooling_multiplier":1.2,"addon_max":3,"cooling_max":5,
        "recovery_enabled":False, **updates}}

def tick(bid=3005.0,ask=3005.2):
    return {"bid":bid,"ask":ask,"point_size":.01,
            "volume_min":.01,"volume_max":100,"volume_step":.01}

def plan(positions,side="BUY",price=3005.0,atr=3.0,**pm):
    return plan_account_actions(
        {"symbol":"XAUUSD","positions":positions,"tick":tick(price,price+.2)},
        {"decision":side,"confidence":.7,"bar_time":12345,
         "entry_atr":atr,"context":{"atr_ratio":1}},
        {"approved":False,"votes":{}},{"approved":True},config(**pm),
        council_advisory=True)

class ProgressiveSizingTests(unittest.TestCase):
    def test_first_addon_is_base_lot_without_original_in_count(self):
        group=[p(),p(comment="manual")]
        out=next_action_lot(group,"ADDON_PROPOSAL",config()["position_management"],tick())
        self.assertEqual(out["count"],0)
        self.assertEqual(out["index"],1)
        self.assertEqual(out["lot"],.02)

    def test_addon_1_5_geometric_sequence_rounded_down_to_broker_step(self):
        pm=config()["position_management"]
        a=[p(comment="manual")]
        lots=[]
        for i in range(3):
            out=next_action_lot(a,"ADDON_PROPOSAL",pm,tick())
            lots.append(out["lot"])
            a.append(p(comment="ALZOOL-ADD"))
        self.assertEqual(lots,[.02,.03,.04])
        self.assertAlmostEqual(out["raw_lot"],.045)

    def test_cooling_1_2_independent_sequence_broker_rounded(self):
        pm=config()["position_management"]
        a=[p(comment="ALZOOL-ADD")]
        lots=[]
        for i in range(5):
            out=next_action_lot(a,"COOLING_PROPOSAL",pm,tick())
            lots.append(out["lot"])
            a.append(p(comment="ALZOOL-COOL"))
        self.assertEqual(lots,[.02,.02,.02,.03,.04])
        self.assertEqual(count_action(a,"ADDON_PROPOSAL"),1)
        self.assertEqual(count_action(a,"COOLING_PROPOSAL"),5)

    def test_manual_mode_uses_fixed_lot_independent_of_existing_count(self):
        pm=config(mode="MANUAL")["position_management"]
        a=[p(comment="ALZOOL-ADD") for _ in range(3)]
        out=next_action_lot(a,"ADDON_PROPOSAL",pm,tick())
        self.assertEqual(out["lot"],.02)

    def test_never_rounds_up_below_broker_min(self):
        pm=config(addon_lot=.005)["position_management"]
        r=next_action_lot([p()],"ADDON_PROPOSAL",pm,tick())
        self.assertEqual(r["lot"],0)

    def test_lot_floor_respects_finer_broker_step(self):
        pm=config()["position_management"]
        r=next_action_lot([p(comment="ALZOOL-COOL")],"COOLING_PROPOSAL",pm,
                          {"volume_min":.001,"volume_step":.001,"volume_max":1})
        self.assertEqual(r["lot"],.024)

    def test_original_positions_do_not_consume_addon_owner_max(self):
        a=[p(),p(comment="manual"),p(comment="ALZOOL-COOL")]
        d=plan(a,addon_max=1,cooling_enabled=False)
        self.assertEqual(d["buy"],"ADDON_PROPOSAL",d)
        self.assertEqual(d["actions"][0]["existing_addons"],0)

    def test_addon_max_applies_only_to_matching_action_count(self):
        a=[p(),p(comment="ALZOOL-ADD"),p(comment="ALZOOL-ADD")]
        d=plan(a,addon_max=2,cooling_enabled=False)
        self.assertEqual(d["buy"],"HOLD",d)

    def test_cooling_works_against_opposing_entry_signal(self):
        d=plan([p(opened=3010,profit=-5)],side="SELL",price=3005)
        self.assertEqual(d["buy"],"COOLING_PROPOSAL",d)
        self.assertEqual(d["actions"][0]["lot_cap"],.02,d)

    def test_wait_entry_signal_does_not_block_management(self):
        d=plan([p(opened=3000)],side="WAIT",price=3005)
        self.assertEqual(d["buy"],"ADDON_PROPOSAL",d)

    def test_manual_distances_do_not_depend_on_atr(self):
        d=plan([p(opened=3000)],side="WAIT",price=3005,atr=0,
               mode="MANUAL",cooling_enabled=False,addon_distance_points=100)
        self.assertEqual(d["buy"],"ADDON_PROPOSAL",d)

    def test_both_sides_qualify_select_one_instead_of_council_deadlock(self):
        a=[p(opened=3000),p(side="SELL",opened=3018)]
        d=plan(a,side="WAIT",price=3005)
        self.assertEqual(len([x for x in d["actions"] if x["action"]!="HOLD"]),1,d)

    def test_one_time_claim_allows_position_management_side_without_new_entry_match(self):
        now=datetime.now(timezone.utc)
        snap={"symbol":"XAUUSD","positions":[p(opened=3010,profit=-5)],
              "tick":tick(),"received_at":now}
        decision={"decision":"SELL","confidence":.7,"entry_atr":3,
                  "bar_time":int(now.timestamp())-60,"context":{"atr_ratio":1}}
        s=config(central_actions_enabled=True)
        d=_allowed("owner",snap,decision,{},{"approved":False,"votes":{}},
                   {"approved":True},s,now,council_advisory=True)
        self.assertTrue(d["execution_ready"],d)
        self.assertEqual(d["action"],"COOLING_PROPOSAL",d)
        self.assertEqual(d["side"],"BUY",d)

    def test_mt5_sends_volume_step_and_explicit_local_enablement(self):
        source=(Path(__file__).resolve().parents[1]/"mt5_client"/"ALZOOL_Central_Client.mq5").read_text(encoding="utf-8")
        self.assertIn('SYMBOL_VOLUME_STEP',source)
        self.assertIn('mt5_position_management_enabled',source)
        self.assertIn('InpEnableCentralPositionManagement',source)
        self.assertIn('AL-ZOOL POSITION ACTION WAIT',source)


if __name__=="__main__":unittest.main()
