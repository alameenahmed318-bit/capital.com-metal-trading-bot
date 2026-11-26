"""Gold demo retirement tests. No broker, network, or database operations."""
import unittest
from master_balanced import evaluate, gold_demo_retirement_enabled, GOLD_DEMO_WEIGHTS

class GoldDemoRetirementTests(unittest.TestCase):
    def fixture(self, symbol="XAUUSD", mode="DEMO", capable=True, frame="M1"):
        d={"symbol":symbol,"bar_time":1000,"entry_reference_price":100,
            "entry_atr":2,"context":{"buy_quality":80,"sell_quality":80,
                                      "buy_trap":False,"sell_trap":False},
            "directional_scores":{"BUY":.9,"SELL":.1},
            "directional_candidates":{"BUY":{"strategy_id":1,"combination_id":415}},
            "master":{"arena_shadow":{"status":"SHADOW_READY","symbol":symbol,
                    "bar_time":1000,"received_at":1064,
                    "models":{"Chronos-2":.9,"TimesFM-2.5":.8,
                              "Chronos-Bolt Tiny":-1,"XGBoost":-1,"LightGBM":-1}}}}
        item={"symbol":symbol,"timeframe":frame,
              "master_direct_demo_capable":capable,
              "account":{"trade_mode":mode},"tick":{"bid":100.02,"ask":100.04}}
        return d,item

    def test_gold_demo(self):
        d,item=self.fixture()
        r=evaluate(d,item,{},1065)
        self.assertEqual(r["weights"],GOLD_DEMO_WEIGHTS)
        self.assertEqual(set(r["used_models"]),{"Chronos-2","TimesFM-2.5"})
        self.assertTrue(r["approved"])
        self.assertEqual(r["direction"],"BUY")

    def test_retired_strategy(self):
        d,item=self.fixture()
        d["directional_candidates"]["BUY"]["strategy_id"]=12
        r=evaluate(d,item,{},1065)
        self.assertFalse(r["approved"])
        self.assertIn("gold_retired_strategy",r["scores"]["BUY"]["failed_checks"])

    def test_other_scopes_unchanged(self):
        for symbol,mode,capable,frame in (
                ("BTCUSD","DEMO",True,"M1"),("XAUUSD","REAL",True,"M1"),
                ("XAUUSD","CONTEST",True,"M1"),("XAUUSD","",True,"M1"),
                ("XAUUSD","DEMO",False,"M1"),("XAUUSD","DEMO",True,"M5")):
            with self.subTest(symbol=symbol,mode=mode,capable=capable,frame=frame):
                d,item=self.fixture(symbol,mode,capable,frame)
                self.assertFalse(gold_demo_retirement_enabled(item,symbol))
                r=evaluate(d,item,{},1065)
                self.assertNotIn("gold_demo_pilot",r)
                self.assertIn("Chronos-Bolt Tiny",r["weights"])

    def test_legacy_unchanged(self):
        d,item=self.fixture()
        r=evaluate(d,item,{},1065,legacy=True)
        self.assertNotIn("gold_demo_pilot",r)
        self.assertIn("XGBoost",r["weights"])

    def test_no_missing_evidence_permission(self):
        d,item=self.fixture()
        d["master"]["arena_shadow"]["models"]={"Chronos-Bolt Tiny":.99,"XGBoost":.9}
        r=evaluate(d,item,{},1065)
        self.assertFalse(r["approved"])
        self.assertEqual(r["reason"],"NO_FRESH_AI_EVIDENCE")

    def test_trap_unchanged(self):
        d,item=self.fixture()
        d["context"]["buy_trap"]=True
        r=evaluate(d,item,{},1065)
        self.assertFalse(r["approved"])
        self.assertIn("no_trap",r["scores"]["BUY"]["failed_checks"])

    def test_stale_unchanged(self):
        d,item=self.fixture()
        r=evaluate(d,item,{},1121)
        self.assertFalse(r["approved"])

if __name__=="__main__":
    unittest.main()
