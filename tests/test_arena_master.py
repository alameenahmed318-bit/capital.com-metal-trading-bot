import unittest
from datetime import datetime,timezone
from unittest.mock import patch
import arena_master as a
class ArenaMasterTest(unittest.TestCase):
    def setUp(self):
        self.now=1800000090; self.bar=1800000000
        self.item={"symbol":"XAUUSD","timeframe":"M1","received_at":datetime.fromtimestamp(self.now,timezone.utc),"strategy_decision":{"symbol":"XAUUSD","bar_time":self.bar},"tick":{"bid":2500,"ask":2500.2,"point_size":.01},"closed_candles":[{"time":self.bar-60*i,"open":2500,"high":2501,"low":2499,"close":2500,"tick_volume":10} for i in range(61)]}
        a.CACHE.clear()
        self.p=patch("arena_master.time.time",return_value=self.now);self.p.start();self.addCleanup(self.p.stop)
    def test_same_bar_only(self):
        body={"symbol":"XAUUSD","bar_time":self.bar,"models":{"LightGBM":.4,"unknown":4},"actions":{"COOLING":{"BUY":.3}}}
        self.assertTrue(a.accept("owner",self.item,body)["accepted"])
        r=a.evidence("owner",self.item["strategy_decision"])
        self.assertEqual(r["models"],{"LightGBM":.4})
        self.assertFalse(r["execution_authorized"])
        self.assertEqual(a.evidence("other",self.item["strategy_decision"])["status"],"NO_FRESH_EVIDENCE")
        self.assertFalse(a.accept("owner",self.item,{**body,"bar_time":self.bar-60})["accepted"])
    def test_no_fresh_data(self):
        self.item["closed_candles"].pop()
        self.assertEqual(a.context("owner",self.item)["status"],"NO_HISTORY")
    def test_merge_non_executable(self):
        a.accept("owner",self.item,{"symbol":"XAUUSD","bar_time":self.bar,"models":{"LightGBM":-.9}})
        base={"decision":"BUY","master":{"scores":{"BUY":1,"SELL":.2}}}
        r=a.fused_shadow("owner",self.item["strategy_decision"],base)
        self.assertFalse(r["execution_authorized"])
        self.assertEqual(base["decision"],"BUY")
        self.assertLessEqual(abs(r["arena_delta"]),.15)
    def test_action_separation(self):
        a.accept("owner",self.item,{"symbol":"XAUUSD","bar_time":self.bar,"actions":{"ADDON":{"BUY":.5},"COOLING":{"BUY":-.3},"RECOVERY":{"BUY":-.8}}})
        d={"entry_atr":1,"master":{"arena_shadow":a.evidence("owner",self.item["strategy_decision"])}}
        self.item["positions"]=[{"symbol":"XAUUSD","type":0,"volume":.01,"open_price":2502,"profit":-2}]
        r=a.management(self.item,d,{"recovery":{"armed":True}})
        self.assertEqual(r["sides"]["BUY"]["relevant_action"],"RECOVERY")
        self.assertEqual(r["sides"]["BUY"]["scores"]["RECOVERY"],-.8)
        self.assertFalse(r["execution_authorized"])
