"""Regression tests for optional advisers and recovery basket accounting."""
import unittest
import master_entry_review as review
import recovery_basket_memory as baskets

class MasterFixTests(unittest.TestCase):
    def test_legacy_missing_does_not_hold_valid_independent_vote(self):
        decision={"decision":"BUY","symbol":"XAUUSD","bar_time":1000,
                  "confidence":.95,"entry_reference_price":100.1,"entry_atr":1,
                  "context":{"buy_quality":90},
                  "master":{"models":[{"model":"GPT-OSS 120B","status":"READY","direction":"BUY","used":True,"points":.8},
                    {"model":"Qwen","status":"AWAITING_LOCAL_VOTE"},{"model":"DeepSeek","status":"AWAITING_LOCAL_VOTE"}],
                    "scores":{"BUY":1,"SELL":0}}}
        snapshot={"symbol":"XAUUSD","timeframe":"M1","tick":{"bid":100,"ask":100.1}}
        self.assertTrue(review.review(decision,snapshot,{},1065,live_arena=True)["approved"])
        self.assertEqual(review.review(decision,snapshot,{},1065)["state"],"COLLECTING")
        decision["master"]["models"]=decision["master"]["models"][1:]
        self.assertFalse(review.review(decision,snapshot,{},1065,live_arena=True)["approved"])
        self.assertEqual(review.review(decision,snapshot,{},1085,live_arena=True)["state"],"NO_INDEPENDENT_EVIDENCE")

    def test_duplicate_recovery_claim_does_not_double_count(self):
        from datetime import datetime, timezone
        t=1700000000
        claim={"payload":{"action":"RECOVERY_PROPOSAL","symbol":"XAUUSD",
            "claimed_at":datetime.fromtimestamp(t,timezone.utc).isoformat(),
            "lot_cap":.02,"side":"BUY","basket_members":[{"position_id":"1","volume":.01}]}}
        def deal(ticket,pid,entry,volume,stamp,profit,comment=""):
            return {"ticket":str(ticket),"position_id":str(pid),"entry":entry,"volume":volume,
                    "time":stamp,"profit":profit,"swap":0,"commission":0,"fee":0,
                    "type":0,"comment":comment,"symbol":"XAUUSD"}
        deals=[deal(1,1,0,.01,t-60,0),deal(2,2,0,.02,t+1,0,"ALZOOL-RECOVERY"),
               deal(3,1,1,.01,t+120,-2),deal(4,2,1,.02,t+120,3)]
        report=baskets.summarize([claim,claim],deals,"XAUUSD")
        self.assertEqual(report["complete_baskets"],1)
        self.assertEqual(report["net"],1)
        self.assertEqual(report["reports"][1]["status"],"OVERLAPPING_BASKET_NOT_COUNTED")
        self.assertEqual(baskets.summarize([claim],deals,"BTCUSD")["complete_baskets"],0)


    def test_cooling_and_closure_feedback_market_side(self):
        from datetime import datetime, timezone
        from smart_profit_advice import closure_feedback
        t=1700000000
        snapshot={"symbol":"XAUUSD","positions":[
            {"symbol":"XAUUSD","position_id":"11","volume":.01,"type":0},
            {"symbol":"XAUUSD","position_id":"12","volume":.01,"type":1},
            {"symbol":"BTCUSD","position_id":"13","volume":.01,"type":0}]}
        members=baskets.claim_members(snapshot,only_side="BUY")
        self.assertEqual([m["position_id"] for m in members],["11"])
        claim={"payload":{"action":"COOLING_PROPOSAL","symbol":"XAUUSD",
               "claimed_at":datetime.fromtimestamp(t,timezone.utc).isoformat(),
               "lot_cap":.02,"side":"BUY","basket_members":members}}
        def deal(ticket,pid,entry,volume,stamp,profit,comment=""):
            return {"ticket":str(ticket),"position_id":str(pid),"entry":entry,"volume":volume,
                    "time":stamp,"profit":profit,"swap":0,"commission":-.1,"fee":0,
                    "type":0 if entry==0 else 1,"comment":comment,"symbol":"XAUUSD"}
        deals=[deal(1,11,0,.01,t-60,0),deal(2,14,0,.02,t+1,0,"ALZOOL-COOL"),
               deal(3,11,1,.01,t+120,-2),deal(4,14,1,.02,t+120,3)]
        report=baskets.summarize([claim],deals,"XAUUSD",action="COOLING_PROPOSAL")
        self.assertEqual(report["complete_baskets"],1)
        self.assertAlmostEqual(report["net"],.6)
        result=closure_feedback(deals,"XAUUSD")
        self.assertEqual(result["sides"]["BUY"]["all"]["positions"],2)
        self.assertAlmostEqual(result["sides"]["BUY"]["all"]["net"],.6)
        self.assertEqual(result["sides"]["SELL"]["all"]["positions"],0)
        self.assertEqual(closure_feedback(deals,"BTCUSD")["sides"]["BUY"]["all"]["positions"],0)


class DirectionReconciliationTests(unittest.TestCase):
    def evaluate(self,symbol="XAUUSD",fresh=True,wrong_market=False,eligible=True,
                 weak=False,trap=False,one_family=False,age=10,bid=100):
        from unittest.mock import patch
        from master_ai import decide
        bar=1700000100;now=bar+60+age
        base={"symbol":symbol,"bar_time":bar,"decision":"BUY","strategy_id":11,
              "combination_id":13,"confidence":.90,
              "directional_scores":{"BUY":.9,"SELL":-.1 if weak else .85},
              "directional_candidates":{"BUY":{"strategy_id":11,"combination_id":13}},
              "entry_reference_price":100,"entry_atr":2,
              "context":{"atr":2,"buy_quality":80,"sell_quality":95,
                         "buy_trap":False,"sell_trap":trap,"last_closed_candle_pressure":-.8}}
        if eligible:base["directional_candidates"]["SELL"]={"strategy_id":42,"combination_id":88}
        models={"LightGBM":-.9,"TiRex-2":-.9} if not one_family else {"LightGBM":-.9,"CatBoost":-.9}
        arena={"status":"SHADOW_READY","symbol":"BTCUSD" if wrong_market else symbol,
               "bar_time":bar if fresh else bar-60,"received_at":now-1,"models":models}
        with patch("master_ai.time.time",return_value=now):
            d=decide(base,{"bar_time":bar,"votes":{}},{},arena_evidence=arena)
        d["master"]["arena_shadow"]=arena
        outcome=review.review(d,{"symbol":symbol,"timeframe":"M1","tick":{"bid":bid,"ask":bid+.1}},
                              {},now,live_arena=True)
        return d,outcome
    def test_both_eligible_reconsider_with_real_strategy_and_price_guard(self):
        for symbol in ("XAUUSD","BTCUSDm"):
            d,r=self.evaluate(symbol=symbol)
            self.assertEqual(d["decision"],"SELL")
            self.assertEqual(d["strategy_id"],42)
            self.assertEqual(d["combination_id"],88)
            self.assertEqual(d["master"]["direction_reconciliation"]["status"],"ALTERNATIVE_SELECTED")
            self.assertTrue(r["approved"])
            self.assertEqual(self.evaluate(symbol=symbol,bid=103)[1]["state"],"PRICE_MOVED")
            self.assertFalse(d["master"]["execution_authorized"])
    def test_no_reversal_without_current_independent_eligible_evidence(self):
        for kwargs in ({"eligible":False},{"weak":True},{"trap":True},
                       {"fresh":False},{"wrong_market":True},{"one_family":True}):
            d,r=self.evaluate(**kwargs)
            self.assertEqual(d["decision"],"BUY",kwargs)
            self.assertFalse(r["approved"],kwargs)

if __name__=="__main__":
    unittest.main()
