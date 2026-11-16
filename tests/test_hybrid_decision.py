"""Offline tests for live hybrid AI evidence, never a broker/order test."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from hybrid_decision import fuse_live_decision


def quant(base="BUY",both=True):
    candidates={"BUY":{"strategy_id":11,"strategy_name":"Buy Momentum",
                       "combination_id":13,"reason_ar":"مؤشر الشراء"}}
    if both:
        candidates["SELL"]={"strategy_id":23,"strategy_name":"Sell Momentum",
                            "combination_id":29,"reason_ar":"مؤشر البيع"}
    return {"decision":base,"strategy_id":11 if base=="BUY" else 0,
            "strategy_name":"Buy Momentum","combination_id":13,
            "confidence":0.62,"bar_time":1700000100,
            "directional_scores":{"BUY":0.62,"SELL":0.56},
            "directional_candidates":candidates,
            "entry_atr":3.0,"entry_reference_price":3000}


def vote(model,direction,confidence=0.9,status="READY"):
    return {"model":model,"status":status,"direction":direction,
            "confidence":confidence,"reason_ar":"تحليل اتجاه الشمعة"}


class HybridDecisionTests(unittest.TestCase):
    def test_missing_all_models_keeps_quant_signal_without_waiting(self):
        output=fuse_live_decision(quant(),{"bar_time":1700000100,"votes":{}})
        self.assertEqual(output["decision"],"BUY",output)
        self.assertEqual(output["hybrid"]["model_votes_used"],0)

    def test_agreeing_models_increase_buy_strength(self):
        c={"bar_time":1700000100,"votes":{
            "Qwen":vote("Qwen","BUY",0.9),
            "DeepSeek":vote("DeepSeek","BUY",0.8)}}
        out=fuse_live_decision(quant(),c)
        self.assertEqual(out["decision"],"BUY",out)
        self.assertGreater(out["hybrid"]["combined_scores"]["BUY"],
                           out["hybrid"]["quantitative_scores"]["BUY"])
        self.assertEqual(out["hybrid"]["model_votes_used"],2)

    def test_opposing_models_can_change_ranking_with_real_sell_strategy(self):
        c={"bar_time":1700000100,"votes":{
            "Qwen":vote("Qwen","SELL",0.95),
            "DeepSeek":vote("DeepSeek","SELL",0.95)}}
        out=fuse_live_decision(quant(),c)
        self.assertEqual(out["decision"],"SELL",out)
        self.assertEqual(out["strategy_id"],23,out)
        self.assertEqual(out["combination_id"],29,out)
        self.assertTrue(out["hybrid"]["direction_changed"])

    def test_ai_cannot_create_opposite_trade_without_strategy(self):
        c={"bar_time":1700000100,"votes":{
            "Qwen":vote("Qwen","SELL",1.0),
            "DeepSeek":vote("DeepSeek","SELL",1.0)}}
        out=fuse_live_decision(quant(both=False),c)
        self.assertEqual(out["decision"],"BUY",out)
        self.assertEqual(out["strategy_id"],11)

    def test_stale_previous_bar_votes_are_ignored(self):
        c={"bar_time":1700000040,"votes":{
            "Qwen":vote("Qwen","SELL",1.0),
            "DeepSeek":vote("DeepSeek","SELL",1.0)}}
        out=fuse_live_decision(quant(),c)
        self.assertEqual(out["decision"],"BUY",out)
        self.assertEqual(out["hybrid"]["model_votes_used"],0)

    def test_wait_or_pending_votes_are_not_mandatory_vetoes(self):
        c={"bar_time":1700000100,"votes":{
            "Qwen":vote("Qwen","WAIT",1.0),
            "DeepSeek":vote("DeepSeek","SELL",1.0,"PENDING")}}
        out=fuse_live_decision(quant(),c)
        self.assertEqual(out["decision"],"BUY",out)
        self.assertEqual(out["hybrid"]["model_votes_used"],0)

    def test_no_quant_strategy_never_executes_ai_only_signal(self):
        base=quant(base="WAIT")
        base["directional_scores"]={"BUY":0.0,"SELL":0.0}
        base["directional_candidates"]={}
        c={"bar_time":1700000100,"votes":{
            "Qwen":vote("Qwen","SELL",1.0),
            "DeepSeek":vote("DeepSeek","SELL",1.0)}}
        out=fuse_live_decision(base,c)
        self.assertEqual(out["decision"],"WAIT",out)
        self.assertEqual(out["strategy_id"],0)

    def test_unavailable_model_does_not_delay_ready_other_model(self):
        c={"bar_time":1700000100,"votes":{
            "Qwen":vote("Qwen","SELL",1.0),
            "DeepSeek":vote("DeepSeek","WAIT",0.0,"NOT_CONFIGURED")}}
        out=fuse_live_decision(quant(),c)
        self.assertEqual(out["hybrid"]["model_votes_used"],1)
        self.assertNotEqual(out["hybrid"]["combined_scores"]["SELL"],
                            out["hybrid"]["quantitative_scores"]["SELL"])

    def test_base_input_is_not_mutated(self):
        base=quant()
        out=fuse_live_decision(base,{"bar_time":1700000100,"votes":{
            "Qwen":vote("Qwen","SELL",0.95),
            "DeepSeek":vote("DeepSeek","SELL",0.95)}})
        self.assertEqual(base["decision"],"BUY")
        self.assertEqual(out["decision"],"SELL")


if __name__=="__main__":
    unittest.main()
