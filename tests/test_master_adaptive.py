import sys
import unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
import master_adaptive as a
import master_ai
import master_v3
BAR=1795580000
NOW=BAR+65
def base(symbol="XAUUSD"):
    return {"symbol":symbol,"bar_time":BAR,"decision":"BUY","strategy_id":11,"combination_id":13,
        "confidence":.98,"directional_scores":{"BUY":.98,"SELL":.02},
        "directional_candidates":{"BUY":{"strategy_id":11,"combination_id":13}},
        "entry_reference_price":100,"entry_atr":2,
        "context":{"atr":2,"buy_quality":100,"sell_quality":0,"last_closed_candle_pressure":.8}}
def evidence(names=("LightGBM","TiRex-2")):
    return {"status":"SHADOW_READY","symbol":"XAUUSD","bar_time":BAR,"received_at":NOW-1,
            "models":{n:1.0 for n in names},"actions":{"ADDON":{"BUY":.8},"RECOVERY":{"BUY":-1.}}}
def run(b=None,e=None,now=NOW,tick=None,feedback=None):
    b=b or base();e=e or evidence();feedback=feedback or {}
    with patch("master_ai.time.time",return_value=now):
        current=master_ai.decide(b,{"bar_time":BAR,"votes":{}},feedback,arena_evidence=e)
    return a.evaluate(b,current,e,feedback,tick or {"bid":100,"ask":100.1},{},now)
class AdaptiveTests(unittest.TestCase):
    def test_two_families_can_release_research_early(self):
        r=run()
        self.assertEqual(r["direction"],"BUY")
        self.assertEqual(r["supporting_families"],2)
        self.assertEqual(r["delay_target_sec"],2)
        self.assertFalse(r["execution_authorized"])
    def test_correlated_models_not_independent(self):
        r=run(e=evidence(("LightGBM","XGBoost","CatBoost")))
        self.assertEqual(r["supporting_families"],1)
        self.assertEqual(r["direction"],"WAIT")
        self.assertEqual(r["delay_target_sec"],10)
    def test_price_trap_and_late(self):
        self.assertEqual(run(tick={"bid":105,"ask":105.1})["direction"],"WAIT")
        b=base();b["context"]["buy_trap"]=True
        self.assertEqual(run(b=b)["direction"],"WAIT")
        self.assertEqual(run(now=BAR+80)["reason"],"RESEARCH_WINDOW_EXPIRED")
    def test_missing_wrong_market_and_no_strategy(self):
        e=evidence();e["symbol"]="BTCUSDm"
        self.assertEqual(run(e=e)["direction"],"WAIT")
        b=base();b.update(strategy_id=0,decision="WAIT",directional_candidates={})
        self.assertEqual(run(b=b)["direction"],"WAIT")
    def test_profiles_chronological_mature_nonoverlapping(self):
        rows=[]
        for i in range(150):
            t=BAR+i*180
            rows.append({"payload":{"symbol":"XAUUSD","bar_time":t,"observed_at":t+65,"lane":5,
              "candidate":{"adaptive":{"version":a.VERSION,"forecasts":{"TiRex-2":.7,"XGBoost":-.7},"direction":"BUY"},"adaptive_baseline":"WAIT"},
              "outcomes":{"180":{"quote_time":t+241,"max_gap_sec":4,"payoffs":{"BUY":.6,"SELL":-.7}}}}})
        end=BAR+150*180+300
        result=a.summarize(rows,"XAUUSD",end)
        self.assertEqual(result["model_profiles"]["TiRex-2"]["factor"],1.2)
        self.assertEqual(result["model_profiles"]["XGBoost"]["factor"],.8)
        self.assertTrue(result["comparisons"]["5"]["ready_for_review"])
        self.assertEqual(a.profile(rows,"BTCUSD",end)["TiRex-2"]["samples"],0)
        self.assertEqual(a.profile(rows,"XAUUSD",BAR)["TiRex-2"]["samples"],0)
        w=a.candidate_weights({"symbol":"XAUUSD","master_adaptive":result},"XAUUSD",end)
        self.assertGreater(w["TiRex-2"],w["XGBoost"])
        self.assertAlmostEqual(sum(w.values()),.14,places=6)
    def test_attribution_full_volume_and_unique_audit(self):
        common={"symbol":"XAUUSD","position_id":"p","volume":.01,"commission":-.1,"swap":0,"fee":0}
        entry={**common,"ticket":"1","entry":0,"type":0,"time":NOW,"profit":0,"comment":"ALZOOL-S11-C13"}
        exit={**common,"ticket":"2","entry":1,"type":1,"time":NOW+5,"profit":1}
        audit={"payload":{"symbol":"XAUUSD","strategy_id":11,"combination_id":13,"direction":"BUY","status":"AUTHORIZED","decision_at":datetime.fromtimestamp(NOW-1,timezone.utc).isoformat(),"arena_advisory":{"used":["TiRex-2"]}}}
        r=a.attribution([entry,exit],"XAUUSD",[audit])
        self.assertEqual(r["complete_positions"],1)
        self.assertEqual(r["strategy_linked"],1)
        self.assertEqual(r["unique_audit_candidates"],1)
        self.assertAlmostEqual(r["complete_net"],.8)
        self.assertEqual(a.attribution([entry,{**exit,"volume":.005}],"XAUUSD",[audit])["complete_positions"],0)
        self.assertEqual(a.attribution([entry,exit],"XAUUSD",[audit,audit])["unique_audit_candidates"],0)
    def test_action_and_profit_research_only(self):
        r=run()
        m=a.management(r,evidence(),[{"symbol":"XAUUSD","type":0,"profit":1,"swap":0}],"XAUUSD")
        self.assertEqual(m["BUY"]["actions"]["ADDON"]["assessment"],"SUPPORT")
        self.assertEqual(m["BUY"]["actions"]["RECOVERY"]["assessment"],"CAUTION")
        self.assertFalse(m["BUY"]["execution_authorized"])
    def test_v3_adaptive_same_snapshot(self):
        with patch("master_ai.time.time",return_value=NOW):
            r=master_v3.evaluate(base(),{"bar_time":BAR,"votes":{}},{},{"bid":100,"ask":100.1},{},NOW,evidence(),[])
        self.assertEqual(r["adaptive_baseline"],"WAIT")
        self.assertEqual(r["adaptive"]["direction"],"BUY")
        self.assertFalse(r["adaptive"]["execution_authorized"])
if __name__=="__main__":unittest.main()
