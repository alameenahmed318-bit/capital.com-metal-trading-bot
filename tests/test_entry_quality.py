"""Regression tests: faster, stronger soft entry evidence without new WAIT gates."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from entry_evidence import closed_candle_pressure,model_market_fit
from hybrid_decision import fuse_live_decision
from strategy_engine import evaluate_market


def base():
    return {"decision":"BUY","strategy_id":11,"strategy_name":"Buy",
            "combination_id":2,"confidence":.67,"bar_time":1700000100,
            "directional_scores":{"BUY":.67,"SELL":.62},
            "directional_candidates":{
                "BUY":{"strategy_id":11,"strategy_name":"Buy","combination_id":2,
                       "reason_ar":"مؤشر شراء"},
                "SELL":{"strategy_id":23,"strategy_name":"Sell","combination_id":3,
                        "reason_ar":"مؤشر بيع"}},
            "context":{"buy_quality":75,"sell_quality":55,
                       "buy_trap":False,"sell_trap":False},
            "entry_atr":3,"entry_reference_price":3000}


def qvote(model,side,confidence=.95):
    return {"model":model,"status":"READY","direction":side,
            "confidence":confidence,"reason_ar":"تحليل الشمعة المغلقة"}


class EntrySoftEvidenceTests(unittest.TestCase):
    def test_bullish_candle_close_near_high_adds_buy_pressure(self):
        p=closed_candle_pressure([{"open":100,"high":105,"low":99,"close":104}])
        self.assertGreater(p,0)
        self.assertLessEqual(p,1)

    def test_bearish_candle_close_near_low_adds_sell_pressure(self):
        p=closed_candle_pressure([{"open":104,"high":105,"low":99,"close":100}])
        self.assertLess(p,0)
        self.assertGreaterEqual(p,-1)

    def test_flat_or_bad_candle_neutral_not_blocking(self):
        self.assertEqual(closed_candle_pressure([]),0)
        self.assertEqual(closed_candle_pressure([{"open":100,"high":100,"low":100,"close":100}]),0)

    def test_trap_attenuates_ai_vote_not_zeros_it(self):
        good=model_market_fit({"sell_quality":95,"sell_trap":False},"SELL")
        poor=model_market_fit({"sell_quality":15,"sell_trap":True},"SELL")
        self.assertGreater(good,poor)
        self.assertGreater(poor,0)

    def test_all_models_missing_never_introduces_extra_wait(self):
        d=fuse_live_decision(base(),{"bar_time":1700000100,"votes":{}})
        self.assertEqual(d["decision"],"BUY",d)
        self.assertEqual(d["hybrid"]["model_votes_used"],0,d)
        self.assertEqual(d["hybrid"]["reversal_guard"],"SOFT_SCORE_NO_EXTRA_WAIT")

    def test_trapped_ai_sell_has_less_effect_than_clean_ai_sell(self):
        quant=base()
        votes={"bar_time":1700000100,"votes":{
            "Qwen":qvote("Qwen","SELL"),
            "DeepSeek":qvote("DeepSeek","SELL")}}
        clean=fuse_live_decision(quant,votes)
        trapped=base()
        trapped["context"]["sell_trap"]=True
        trapped["context"]["sell_quality"]=10
        bad=fuse_live_decision(trapped,votes)
        self.assertGreater(clean["hybrid"]["combined_scores"]["SELL"],
                           bad["hybrid"]["combined_scores"]["SELL"])
        self.assertEqual(bad["hybrid"]["model_votes_used"],2)
        self.assertTrue(all(x["used"] for x in bad["hybrid"]["model_contributions"]
                            if x["model"] in ("Qwen","DeepSeek")))

    def test_opposing_ais_may_still_change_close_strategy_rank(self):
        d=fuse_live_decision(base(),{"bar_time":1700000100,"votes":{
            "Qwen":qvote("Qwen","SELL"),
            "DeepSeek":qvote("DeepSeek","SELL")}})
        self.assertEqual(d["decision"],"SELL",d)
        self.assertEqual(d["strategy_id"],23,d)

    def test_wrong_candle_vote_not_used(self):
        d=fuse_live_decision(base(),{"bar_time":1700000040,"votes":{
            "Qwen":qvote("Qwen","SELL")}})
        self.assertEqual(d["decision"],"BUY",d)
        self.assertEqual(d["hybrid"]["model_votes_used"],0)

    def test_malformed_context_not_a_blocker(self):
        d=base();d["context"]="invalid"
        outcome=fuse_live_decision(d,{"bar_time":1700000100,"votes":{}})
        self.assertEqual(outcome["decision"],"BUY",outcome)

    def test_no_strategy_ai_cannot_force_trade(self):
        d=base();d["directional_candidates"]={};d["strategy_id"]=0;d["decision"]="WAIT"
        out=fuse_live_decision(d,{"bar_time":1700000100,"votes":{
            "Qwen":qvote("Qwen","BUY")}})
        self.assertEqual(out["decision"],"WAIT")

    def test_strategy_engine_exposes_candle_pressure_without_new_gate(self):
        rates=[]
        for i in range(90):
            v=3000+i*.18
            rates.append({"time":1700000000+i*60,"open":v,
                          "high":v+1.0,"low":v-.6,"close":v+.6,
                          "tick_volume":100+i%10})
        decision=evaluate_market(rates)
        self.assertIn("last_closed_candle_pressure",decision["context"])
        self.assertGreater(decision["context"]["last_closed_candle_pressure"],0)
        self.assertEqual(set(decision["directional_scores"]),{"BUY","SELL"})
        self.assertEqual(decision["strategy_count"],200)
        self.assertEqual(decision["combination_count"],1000)


if __name__=="__main__":unittest.main()
