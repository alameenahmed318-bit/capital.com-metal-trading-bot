"""V4 is read-only research. Synthetic prices test semantics, not profitability."""
import copy
import importlib.util
import json
import pathlib
import sys
import unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"marketplace"/"backend"))
import accuracy_v4 as a
spec=importlib.util.spec_from_file_location("relay_v4",ROOT/"mt5_client"/"ALZOOL_Local_Model_Relay.py")
relay=importlib.util.module_from_spec(spec);spec.loader.exec_module(relay)
BAR=1795493100
class V4AccuracyTests(unittest.TestCase):
    def setUp(self):
        a._PENDING.clear()
        self.decision={"decision":"BUY","bar_time":BAR,"context":{"atr":10},
          "directional_scores":{"BUY":.51,"SELL":.49},
          "directional_candidates":{"BUY":{"strategy_id":11},"SELL":{"strategy_id":68}}}
        self.tick={"bid":100,"ask":101}
    def test_no_central_information_in_independent_prompt(self):
        ctx={"symbol":"BTCUSDm","bar_time":BAR,"decision":"SELL","confidence":.987654,
             "strategy_name":"SECRET_STRATEGY","family_votes":{"a":"SELL"},
             "combination_summary":{"SELL":900},"context":{"atr":3,"reason":"SECRET_SIGNAL",
                 "buy_quality":99,"sell_trap":True},"candles":[{"time":BAR,"open":100,"high":102,"low":99,"close":101,"signal":"SELL"}],
             "_analysis_protocol":relay.INDEPENDENT_PROTOCOL}
        with patch.object(relay,"request_json",return_value={"message":{"content":'{"direction":"BUY","confidence":0.6,"reason_ar":"دعم واضح"}'}}) as req:
            vote=relay.model_vote("qwen2.5:1.5b",ctx,12)
        payload=req.call_args.kwargs["payload"]
        sent=json.loads(payload["messages"][1]["content"])
        self.assertEqual(vote["direction"],"BUY")
        for k in ("decision","central_signal","central_confidence","confidence","family_votes","family_vote_counts","combination_summary","strategy_name","buy_quality","sell_trap"):
            self.assertNotIn(k,sent)
        self.assertNotIn("signal",sent["candles_newest_first"][0])
        self.assertNotIn("SECRET",json.dumps(sent))
    def test_forecast_before_close_only(self):
        for t in (BAR+59,BAR+120,BAR+121):
            self.assertIsNone(a.record("c","BTCUSDm","M1",self.decision,self.tick,"QUANT","BUY",.8,t))
        self.assertIsNone(a.record("c","BTCUSDm","M5",self.decision,self.tick,"QUANT","BUY",.8,BAR+61))
    def test_exact_next_candle_and_cost_comparison(self):
        event=a.record("c","BTCUSDm","M1",self.decision,self.tick,"LOCAL_AI","SELL",.8,BAR+65,"qwen2.5:1.5b")
        self.assertEqual(event["event_type"],"AI_ACCURACY_V4_FORECAST")
        self.assertFalse(event["payload"]["execution_authorized"])
        self.assertIsNone(a.record("c","BTCUSDm","M1",self.decision,self.tick,"LOCAL_AI","BUY",1,BAR+66,"qwen2.5:1.5b"))
        self.assertEqual(a.settle("other","BTCUSDm","M1",[{"time":BAR+60,"close":97}]),[])
        self.assertEqual(a.settle("c","XAUUSD","M1",[{"time":BAR+60,"close":97}]),[])
        self.assertEqual(a.settle("c","BTCUSDm","M1",[{"time":BAR,"close":97}]),[])
        out=a.settle("c","BTCUSDm","M1",[{"time":BAR+60,"close":97}])
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]["payload"]["model_net_price_proxy"],2)
        self.assertEqual(out[0]["payload"]["baseline_net_price_proxy"],-4)
        self.assertFalse(out[0]["payload"]["actual_trade_proven"])
        self.assertEqual(a.settle("c","BTCUSDm","M1",[{"time":BAR+60,"close":97}]),[])
    def test_gap_is_not_a_fake_win(self):
        a.record("c","XAUUSD","M1",self.decision,self.tick,"QUANT","BUY",None,BAR+65)
        self.assertEqual(a.settle("c","XAUUSD","M1",[{"time":BAR+120,"close":105}]),[])
    def test_rank_never_changes_original_or_invents_unsupported_side(self):
        self.decision["directional_candidates"].pop("SELL")
        saved=copy.deepcopy(self.decision)
        rates=[{"time":BAR-i*60,"open":101-i,"close":100-i,"high":102-i,"low":99-i} for i in reversed(range(8))]
        res=a.shadow_rank(self.decision,rates)
        self.assertEqual(res["direction"],"BUY")
        self.assertFalse(res["execution_authorized"])
        self.assertEqual(self.decision,saved)
        self.assertLessEqual(abs(res["adjustment"]),.08)

    def test_summary_measures_not_assumes_confidence(self):
        a.record("c","BTCUSDm","M1",self.decision,self.tick,"LOCAL_AI","SELL",.8,BAR+65,"qwen2.5:1.5b")
        rows=a.settle("c","BTCUSDm","M1",[{"time":BAR+60,"close":97}])
        report=a.summarize(rows+rows)
        self.assertEqual(report["sample_count"],1)
        group=report["groups"][0]
        self.assertEqual(group["hit_rate"],1)
        self.assertEqual(group["baseline_paired_hit_rate"],0)
        self.assertEqual(group["mean_reported_confidence"],.8)
        self.assertEqual(group["brier_score"],.04)
        self.assertFalse(report["execution_authorized"])

if __name__=="__main__":unittest.main()
