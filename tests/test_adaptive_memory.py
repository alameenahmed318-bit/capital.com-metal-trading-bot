"""Offline regression coverage for adaptive research only."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
import adaptive_memory as a
from feedback_learning import action_feedback
from master_ai import decide, WEIGHTS
import time

def rows(model="Fast AI",kind="FAST_AI",symbol="XAUUSD",n=150,net=.4):
    out=[]
    for i in range(n):
        bar=1700000100+i*60
        out.append({"payload":{"symbol":symbol,"model":model,"kind":kind,
            "version":"AZ_ACCURACY_V4","analysis_mode":"UNIFIED",
            "metric":"NEXT_CLOSE_QUOTE_PROXY_NOT_BROKER_FILL","direction":"BUY",
            "bar_time":bar,"forecast_time_unix":bar+65,"next_bar_time":bar+60,
            "model_net_atr":net}})
    return out

class AdaptiveMemoryTests(unittest.TestCase):
    def test_small_sample_no_change(self):
        self.assertEqual(a.performance_factors(rows(n=20),"XAUUSD")["Fast AI"]["factor"],1)
    def test_market_and_duplicate_isolation(self):
        data=rows(n=70)
        self.assertEqual(a.performance_factors(data+data+rows(symbol="BTCUSD"),"XAUUSD")["Fast AI"]["samples"],70)
    def test_chronological_sign(self):
        self.assertEqual(a.performance_factors(rows(),"XAUUSD")["Fast AI"]["factor"],1.2)
        self.assertEqual(a.performance_factors(rows(net=-.4),"XAUUSD")["Fast AI"]["factor"],.8)
        data=rows()
        for r in data[105:]:r["payload"]["model_net_atr"]=-.4
        self.assertEqual(a.performance_factors(data,"XAUUSD")["Fast AI"]["factor"],1)
    def test_model_identity_and_weight_budget(self):
        evidence={"qwen2.5:1.5b":{"factor":.8}}
        self.assertEqual(a.candidate_weights(WEIGHTS,evidence,{"Qwen":{"model_id":"other"}}),WEIGHTS)
        w=a.candidate_weights(WEIGHTS,evidence,{"Qwen":{"model_id":"qwen2.5:1.5b"}})
        self.assertAlmostEqual(sum(w.values()),sum(WEIGHTS.values()))
        self.assertLess(w["Qwen"],WEIGHTS["Qwen"])
    def test_side_memory_full_position(self):
        now=int(time.time())-60
        def d(ticket,pid,entry,typ,profit,volume=.01,symbol="XAUUSD"):
            return {"ticket":ticket,"position_id":pid,"entry":entry,"type":typ,
                    "symbol":symbol,"time":now,"volume":volume,"profit":profit,
                    "fee":0,"swap":0,"commission":0,"comment":"ALZOOL-RECOVERY" if entry==0 else ""}
        data=[d("1","a",0,0,0),d("2","a",1,1,-3),
              d("3","b",0,1,0),d("4","b",1,0,2),
              d("5","c",0,0,0),d("6","c",1,1,10,.005)]
        report={(r["side"],r["action"]):r for r in a.action_side_memory(data,"XAUUSD",action_feedback)}
        self.assertEqual(report["BUY","RECOVERY"]["net"],-3)
        self.assertEqual(report["BUY","RECOVERY"]["incomplete_positions"],1)
        self.assertEqual(report["SELL","RECOVERY"]["net"],2)
        self.assertFalse(report["SELL","RECOVERY"]["execution_authorized"])
    def test_master_live_policy_unchanged(self):
        base={"symbol":"XAUUSD","bar_time":1700000100,"decision":"BUY","strategy_id":11,
              "confidence":.98,"directional_scores":{"BUY":.98,"SELL":.02},
              "directional_candidates":{"BUY":{"strategy_id":11,"combination_id":13}},
              "entry_reference_price":100,"entry_atr":2,
              "context":{"atr":2,"buy_quality":100,"sell_quality":0,"regime":"up"}}
        council={"bar_time":1700000100,"votes":{"Fast AI":{"model":"Fast AI","direction":"BUY","status":"READY","confidence":.99}}}
        original=decide(base,council)
        feedback={"status":"RESEARCH_ONLY","symbol":"XAUUSD","updated_at_unix":time.time(),
                  "adaptive_memory":{"model_performance":{"Fast AI":{"factor":1.2}}}}
        updated=decide(base,council,feedback)
        for key in ("decision","confidence","strategy_id","combination_id"):
            self.assertEqual(original.get(key),updated.get(key))
        self.assertFalse(updated["master"]["adaptive_shadow"]["execution_authorized"])

if __name__=="__main__":
    unittest.main()
