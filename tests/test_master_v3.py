"""Offline tests, no broker/network. Uses actual V1/V2/review modules."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
import master_v3 as v3
BAR=1700000100
def base(symbol="BTCUSDm"):
    return {"symbol":symbol,"bar_time":BAR,"decision":"BUY","strategy_id":11,
        "confidence":.98,"directional_scores":{"BUY":.98,"SELL":.02},
        "directional_candidates":{"BUY":{"strategy_id":11,"combination_id":13}},
        "entry_reference_price":100,"entry_atr":2,
        "context":{"atr":2,"buy_quality":100,"sell_quality":0,"regime":"اتجاه صاعد"}}
def vote(model,side="BUY",status="READY"):
    return {"model":model,"direction":side,"status":status,"confidence":.99}
def council(q=True,d=True):
    return {"bar_time":BAR,"votes":{"Fast AI":vote("Fast AI"),
        "Qwen":vote("Qwen") if q else vote("Qwen","WAIT","AWAITING_LOCAL_VOTE"),
        "DeepSeek":vote("DeepSeek") if d else vote("DeepSeek","WAIT","AWAITING_LOCAL_VOTE")},
        "groq_advisory":vote("GPT-OSS 120B","WAIT","PENDING")}
def tick(bid=100,ask=100.1):return {"bid":bid,"ask":ask}
class V3Tests(unittest.TestCase):
    def setUp(self):v3._PENDING.clear();v3._LATEST.clear();self.events=[]
    def test_lanes(self):
        self.assertIsNone(v3.lane_for(4));self.assertEqual(v3.lane_for(5),5)
        self.assertEqual(v3.lane_for(19),10);self.assertEqual(v3.lane_for(34),20)
        self.assertEqual(v3.lane_for(59),35);self.assertIsNone(v3.lane_for(60))
    def test_strong_early_candidate_never_authorizes(self):
        r=v3.evaluate(base(),council(),{},tick(),{},BAR+70)
        self.assertEqual(r["current"],"WAIT");self.assertEqual(r["challenger"],"BUY")
        self.assertTrue(r["early_candidate"]);self.assertFalse(r["execution_authorized"])
    def test_adaptive_baseline_receives_live_arena_policy(self):
        from unittest.mock import patch
        now=BAR+70
        arena={"status":"SHADOW_READY","symbol":"BTCUSDm","bar_time":BAR,
               "received_at":now-1,"models":{"LightGBM":.9,"TiRex-2":.9}}
        with patch("master_ai.time.time",return_value=now):
            result=v3.evaluate(base(),council(q=False,d=False),{},tick(),{},now,arena)
        review=result["adaptive_baseline_review"]
        self.assertEqual(review["policy"],"MASTER_REVIEW_V2")
        self.assertEqual(set(review["arena_models_used"]),{"LightGBM","TiRex-2"})
        self.assertFalse(result["execution_authorized"])
    def test_single_vote_cannot_release_early(self):
        r=v3.evaluate(base(),council(d=False),{},tick(),{},BAR+70)
        self.assertEqual(r["challenger"],"WAIT")
    def test_price_guard_remains(self):
        r=v3.evaluate(base(),council(),{},tick(105,105.1),{},BAR+70)
        self.assertEqual(r["challenger"],"WAIT")
    def test_payoffs_include_both_quotes(self):
        self.assertAlmostEqual(v3.quote_payoff("BUY",100,101,103,104,2),1)
        self.assertAlmostEqual(v3.quote_payoff("SELL",100,101,96,97,2),1.5)
    def send(self,age,client="owner",symbol="BTCUSDm",b=None,t=None):
        v3.process(client,symbol,base(symbol) if b is None else b,council(),{},t or tick(),{},BAR+60+age,self.events.append)
    def test_dedup_and_isolation(self):
        self.send(5);self.send(6);self.send(6,"other")
        self.assertEqual(len(self.events),2);self.assertEqual(len(v3._PENDING),2)
        self.assertEqual(v3.latest("owner","BTCUSDm")["lane"],5)
        self.assertEqual(v3.latest("owner","XAUUSD"),{})
    def test_common_horizons_and_missing_quotes(self):
        self.send(5)
        self.send(60,b={},t=tick(101,101.1))
        self.send(190,b={})
        self.send(300,b={},t=tick(102,102.1))
        p=self.events[-1]["payload"]
        self.assertEqual(self.events[-1]["event_type"],"MASTER_V3_OUTCOME")
        self.assertIsNone(p["outcomes"]["180"])
        self.assertAlmostEqual(p["outcomes"]["60"]["payoffs"]["BUY"],.45)
        self.assertFalse(p["actual_trade_proven"])
        self.assertEqual(v3.summarize([{"payload":p}],"BTCUSDm")["lanes"]["5"]["60"]["all"]["n"],1)
    def test_wrong_bar_no_sample(self):
        b=base();b["bar_time"]+=60
        self.send(5,b=b);self.assertFalse(self.events)
    def test_unified_validation_nonoverlap_and_market_scope(self):
        def row(bar,symbol="BTCUSDm",gap=5):
            return {"payload":{"version":v3.VERSION,"symbol":symbol,"lane":10,"bar_time":bar,
                "observed_at":bar+70,"candidate":{"adaptive_baseline":"WAIT",
                    "adaptive_baseline_review":{"unified_score":{"version":v3.master_entry_review.UNIFIED_SCORE_VERSION,
                        "direction":"BUY","execution_authorized":False}}},
                "outcomes":{"180":{"quote_time":bar+240,"max_gap_sec":gap,"payoffs":{"BUY":.2,"SELL":-.3}}}}}
        rows=[row(BAR),row(BAR+60),row(BAR+180),row(BAR+360),row(BAR+540,"XAUUSD"),row(BAR+720,gap=21)]
        r=v3.summarize_unified(rows,"BTCUSDm")
        self.assertEqual(r["train"]["windows"]+r["validation"]["windows"],3)
        self.assertFalse(r["enough_for_review"])
        self.assertFalse(r["auto_promoted"])
        self.assertFalse(r["execution_authorized"])
    def test_balanced_demo_research_has_no_execution_authority(self):
        from unittest.mock import patch
        now=BAR+70
        arena={"status":"SHADOW_READY","symbol":"BTCUSDm","bar_time":BAR,
               "received_at":now-1,"models":{"LightGBM":.9,"TiRex-2":.9}}
        with patch("master_ai.time.time",return_value=now):
            result=v3.evaluate(base(),council(),{},tick(),{},now,arena)
        trial=result["balanced_demo_comparison"]
        self.assertEqual(trial["mode"],"SHADOW_ONLY")
        self.assertFalse(trial["execution_authorized"])
        self.assertIn(trial["baseline_direction"],("BUY","SELL","WAIT"))
        self.assertFalse(v3.summarize([],"BTCUSDm")["balanced_demo_validation"]["auto_promoted"])
if __name__=="__main__":unittest.main()
