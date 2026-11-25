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

if __name__=="__main__":
    unittest.main()
