"""New Smart Recovery: currency threshold only ARMS; new M1 candle times entry."""
import sys
import unittest
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from recovery_candle_guard import observe_recovery_arm,recovery_m1_confirmation
from recovery_logic import recovery_intent
from position_action_engine import plan_account_actions

BASE=1794829200  # exact 60-second boundary for deterministic tests

def pos(side="BUY",loss=-22):
    return {"ticket":"p-"+side,"symbol":"XAUUSD","type":0 if side=="BUY" else 1,
            "volume":.02,"profit":loss,"swap":0,"open_price":3050}

def settings(**overrides):
    return {"trading_enabled":True,"position_management":{
        "mode":"AI","recovery_enabled":True,"recovery_loss_trigger":20,
        "recovery_profit_target":3,**overrides}}

def snap(side="BUY",arm=BASE+14,now=BASE+135,bar=BASE+60,candle_side="BUY",
         positions=None,net=-22,timeframe="M1",candles=True):
    bullish={"time":bar,"open":3047.0,"high":3052.0,"low":3046.0,"close":3051.0}
    bearish={"time":bar,"open":3051.0,"high":3052.0,"low":3046.0,"close":3047.0}
    return {"symbol":"XAUUSD","timeframe":timeframe,
        "positions":positions if positions is not None else [pos(side)],
        "recovery_group_net":net,
        "recovery_armed_at":arm,
        "closed_candles":([bullish if candle_side=="BUY" else bearish] if candles else []),
        "received_at":datetime.fromtimestamp(now,timezone.utc),
        "tick":{"bid":3048,"ask":3048.2}}


def plan(x,signal="WAIT",decision_bar=None,**kwargs):
    d={"decision":signal,"entry_atr":3.0,
       "bar_time":decision_bar if decision_bar is not None else BASE+60,
       "context":{"atr_ratio":1.0,**kwargs}}
    return plan_account_actions(x,d,{"votes":{}},
                                {"approved":True},settings(),council_advisory=True)


class RecoveryCandleChecks(unittest.TestCase):
    def test_threshold_hit_does_not_open_on_same_partial_candle(self):
        x=snap(arm=BASE+14,now=BASE+65,bar=BASE)
        r=plan(x,decision_bar=BASE)
        self.assertEqual(r["recovery"]["armed"],True,r)
        self.assertEqual(r["recovery"]["action"],"HOLD",r)
        self.assertEqual(r["recovery"]["candle_confirmation"]["state"],"WAIT_FULL_M1",r)
        self.assertEqual(r["actions"][0]["lot_cap"],0.0,r)

    def test_one_full_m1_after_loss_is_enough_when_candle_confirms(self):
        x=snap()
        r=plan(x,signal="SELL")
        self.assertEqual(r["buy"],"RECOVERY_PROPOSAL",r)
        self.assertEqual(r["recovery"]["candle_confirmation"]["state"],"CONFIRMED",r)
        self.assertEqual(r["actions"][0]["lot_cap"],.06)
        self.assertTrue(r["actions"][0]["candle_confirmed"])

    def test_opposing_candle_prevents_wrong_side_recovery(self):
        r=plan(snap(candle_side="SELL"))
        self.assertEqual(r["recovery"]["action"],"HOLD")
        self.assertEqual(r["recovery"]["candle_confirmation"]["state"],"WAIT_DIRECTION")
        self.assertIn("شمعة",r["reason_ar"])

    def test_hedged_loss_chooses_losing_side_matching_candle(self):
        x=snap(positions=[pos("BUY",-13),pos("SELL",-10)],net=-23,candle_side="SELL")
        r=plan(x,signal="BUY")
        self.assertEqual(r["sell"],"RECOVERY_PROPOSAL",r)
        self.assertEqual(r["actions"][0]["lot_cap"],.12)

    def test_doji_does_not_authorize_recovery(self):
        x=snap()
        x["closed_candles"][0].update(open=3049,close=3049)
        r=plan(x)
        self.assertEqual(r["recovery"]["candle_confirmation"]["state"],"WAIT_DOJI")

    def test_weak_small_body_candle_is_not_confirmation(self):
        x=snap()
        x["closed_candles"][0].update(open=3049,close=3049.3,high=3052,low=3046)
        r=plan(x)
        self.assertEqual(r["recovery"]["candle_confirmation"]["state"],"WAIT_WEAK_CANDLE")

    def test_new_strategy_decision_required_after_candle(self):
        r=plan(snap(),decision_bar=BASE)
        self.assertEqual(r["recovery"]["candle_confirmation"]["state"],"WAIT_NEW_ANALYSIS")
        self.assertEqual(r["recovery"]["action"],"HOLD")

    def test_missing_candles_wait_instead_of_falling_back_to_loss_number(self):
        r=plan(snap(candles=False))
        self.assertEqual(r["recovery"]["candle_confirmation"]["state"],"WAIT_M1_DATA")

    def test_wrong_timeframe_awaits_m1_instead_of_m5_candle(self):
        r=plan(snap(timeframe="PERIOD_M5"))
        self.assertEqual(r["recovery"]["candle_confirmation"]["state"],"WAIT_TIMEFRAME")

    def test_threshold_no_candle_arm_timestamp_fails_safe(self):
        x=snap()
        x.pop("recovery_armed_at")
        r=plan(x)
        self.assertEqual(r["recovery"]["action"],"HOLD")
        self.assertEqual(r["recovery"]["candle_confirmation"]["state"],"WAIT_ARM")

    def test_opposite_hybrid_vote_only_reduces_score_not_strict_veto(self):
        x=snap()
        a=plan(x,signal="BUY")
        b=plan(x,signal="SELL")
        self.assertGreater(a["actions"][0]["candle_score"],
                           b["actions"][0]["candle_score"])
        self.assertEqual(b["buy"],"RECOVERY_PROPOSAL",b)

    def test_false_breakout_flag_reduces_candle_quality(self):
        x=snap()
        a=plan(x,signal="BUY")
        b=plan(x,signal="BUY",buy_trap=True)
        self.assertGreater(a["actions"][0]["candle_score"],
                           b["actions"][0]["candle_score"])

    def test_arm_records_first_threshold_crossing_only(self):
        x=snap()
        x["client_id"]="test-confirm-first-hit"
        x["account"]={"login":"test-account-1"}
        x.pop("recovery_armed_at")
        x["received_at"]=datetime.fromtimestamp(BASE+25,timezone.utc)
        self.assertEqual(observe_recovery_arm(x,settings(),BASE+25),BASE+25)
        x["received_at"]=datetime.fromtimestamp(BASE+30,timezone.utc)
        self.assertEqual(observe_recovery_arm(x,settings(),BASE+30),BASE+25)
        x["recovery_group_net"]=-19
        self.assertEqual(observe_recovery_arm(x,settings(),BASE+31),0)
        x["recovery_group_net"]=-23
        x["received_at"]=datetime.fromtimestamp(BASE+42,timezone.utc)
        self.assertEqual(observe_recovery_arm(x,settings(),BASE+42),BASE+42)
        observe_recovery_arm(x,settings(recovery_enabled=False),BASE+43)

    def test_recovery_duplicate_still_blocked(self):
        x=snap(positions=[{**pos(),"comment":"ALZOOL-RECOVERY"}])
        r=plan(x)
        self.assertFalse(r["recovery"]["armed"])
        self.assertTrue(r["recovery"]["active_cycle"])
        self.assertEqual(r["actions"],[])

    def test_all_existing_owner_caps_still_block_excess_size(self):
        x=snap()
        r=plan_account_actions(x,{"decision":"BUY","bar_time":BASE+60,
             "entry_atr":3,"context":{}},{"votes":{}},{"approved":True},
             settings(recovery_max_lot=.02))
        self.assertEqual(r["actions"][0]["action"],"HOLD",r)

    def test_mt5_local_check_uses_broker_m1_on_recovery(self):
        src=(Path(__file__).resolve().parents[1]/"mt5_client"/
             "ALZOOL_Central_Client.mq5").read_text(encoding="utf-8")
        self.assertIn('if(action=="RECOVERY_PROPOSAL")',src)
        self.assertIn('iTime(_Symbol,PERIOD_M1,1)',src)
        self.assertIn('candleOpen!=(datetime)barTime',src)
        self.assertIn('AL-ZOOL RECOVERY WAIT CANDLE',src)


if __name__=="__main__":
    unittest.main()
