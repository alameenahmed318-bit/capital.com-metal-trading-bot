"""Offline MASTER AI tests. No broker requests, orders or external model calls."""
import pathlib
import sys
import time
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from master_ai import decide, management_support, learned_factors
from decision_guard import entry_council_mode
from groq_advisor import _compact

def quant():
    return {"symbol":"BTCUSDm","bar_time":1700000100,"decision":"BUY","confidence":.7,
        "strategy_id":1,"entry_atr":5,"entry_reference_price":100,
        "directional_scores":{"BUY":.70,"SELL":.69},
        "directional_candidates":{"BUY":{"strategy_id":1,"strategy_name":"trend"},
            "SELL":{"strategy_id":2,"strategy_name":"reversal"}},
        "context":{"atr":5},"family_votes":{"trend":"BUY"}}

def vote(name,side,confidence=.8,status="READY",model_id=None):
    return {"model":name,"direction":side,"confidence":confidence,"status":status,
            "model_id":model_id,"reason_ar":"evidence"}

class MasterTests(unittest.TestCase):
    def test_owner_explicit_mode(self):
        s={"position_management":{"entry_council_mode":"MASTER","entry_council_mode_user_selected":True}}
        self.assertEqual(entry_council_mode(s,True),"MASTER")
        self.assertEqual(entry_council_mode(s,False),"STRICT")
    def test_disagreement_not_veto(self):
        d=quant()
        c={"bar_time":d["bar_time"],"approved":False,"votes":{"Fast AI":vote("Fast AI","BUY"),
            "Qwen":vote("Qwen","SELL",.9),"DeepSeek":vote("DeepSeek","SELL",.9)}}
        result=decide(d,c)
        self.assertEqual(result["decision"],"SELL")
        self.assertEqual(result["strategy_id"],2)
        self.assertFalse(result["master"]["unanimity_required"])
        self.assertFalse(result["master"]["execution_authorized"])
    def test_cloud_is_used_but_not_required(self):
        d=quant();c={"bar_time":d["bar_time"],"votes":{}}
        local=decide(d,c)
        self.assertEqual(local["decision"],"BUY")
        self.assertEqual(local["master"]["source"],"LOCAL_CONTROLLER")
        cloud=decide(d,{**c,"groq_advisory":vote("GPT-OSS 120B","SELL",.9)})
        self.assertEqual(cloud["master"]["source"],"CLOUD_ASSISTED")
        self.assertEqual(cloud["decision"],"SELL")
        self.assertGreater(management_support(cloud,"SELL"),0)
        self.assertLess(management_support(cloud,"BUY"),0)
    def test_stale_and_missing_strategy_are_not_authorized(self):
        d=quant();c={"bar_time":d["bar_time"]-60,"groq_advisory":vote("GPT-OSS 120B","SELL")}
        self.assertEqual(decide(d,c)["decision"],"BUY")
        d.update(strategy_id=0,decision="WAIT",directional_candidates={})
        self.assertEqual(decide(d,{**c,"bar_time":d["bar_time"]})["decision"],"WAIT")
    def test_calibration_bounded_and_market_specific(self):
        r={"status":"RESEARCH_ONLY","symbol":"BTCUSDm","updated_at_unix":time.time(),
            "models":[{"model":"qwen2.5:1.5b","status":"CALIBRATION_CANDIDATE",
            "training":100,"validation":45,"baseline_brier":.6,"candidate_brier":.25,
            "candidate_confidence_factor":.4}]}
        self.assertEqual(learned_factors(r,"BTCUSDm"),{"qwen2.5:1.5b":.65})
        self.assertEqual(learned_factors(r,"XAUUSD"),{})
        d=quant();c={"bar_time":d["bar_time"],"votes":{"Qwen":vote("Qwen","SELL",1,model_id="qwen2.5:1.5b")}}
        self.assertTrue(decide(d,c,r)["master"]["calibration_applied"])
    def test_cloud_snapshot_analyst_evidence_no_account(self):
        d=quant();d["master_review_votes"]={"Qwen":vote("Qwen","SELL"),
            "DeepSeek":vote("DeepSeek","WAIT",status="AWAITING_LOCAL_VOTE")}
        d["account"]={"secret":"do not send"}
        p=_compact(d)
        self.assertEqual([x["model"] for x in p["model_analysis"]],["Qwen"])
        self.assertNotIn("account",p)

if __name__=="__main__":unittest.main()
