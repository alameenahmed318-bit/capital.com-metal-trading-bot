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

    def test_partial_forecasts_preserve_all_management_scores(self):
        body={"symbol":"XAUUSD","bar_time":self.bar}
        actions={"ADDON":{"BUY":.5,"SELL":-.4},"COOLING":{"BUY":-.3,"SELL":.6},"RECOVERY":{"BUY":-.8,"SELL":.2}}
        self.assertTrue(a.accept("owner",self.item,{**body,"models":{"LightGBM":.1,"XGBoost":.2,"CatBoost":.3},"actions":actions})["accepted"])
        for name in ("Chronos-2","TimesFM-2.5"):
            self.assertTrue(a.accept("owner",self.item,{**body,"models":{name:.4},"actions":{}})["accepted"])
        r=a.evidence("owner",self.item["strategy_decision"])
        self.assertEqual(r["actions"],actions)
        self.assertEqual(len(r["models"]),5)
        self.assertFalse(r["execution_authorized"])
        self.assertTrue(a.accept("owner",self.item,{**body,"actions":{"COOLING":{"BUY":.9,"SELL":"invalid"},"RECOVERY":{}}})["accepted"])
        r=a.evidence("owner",self.item["strategy_decision"])
        self.assertEqual(r["actions"],{**actions,"COOLING":{"BUY":.9,"SELL":.6}})
        self.assertFalse(a.accept("owner",self.item,{**body,"actions":{"COOLING":{}}})["accepted"])
    def test_action_scores_do_not_cross_bars(self):
        body={"symbol":"XAUUSD","bar_time":self.bar}
        a.accept("owner",self.item,{**body,"actions":{"ADDON":{"BUY":.5}}})
        with patch("arena_master.time.time",return_value=self.now+60):
            self.item["received_at"]=datetime.fromtimestamp(self.now+60,timezone.utc)
            self.item["strategy_decision"]["bar_time"]=self.bar+60
            self.item["closed_candles"]=[{**v,"time":v["time"]+60} for v in self.item["closed_candles"]]
            a.accept("owner",self.item,{**body,"bar_time":self.bar+60,"models":{"Chronos-2":.4},"actions":{}})
            r=a.evidence("owner",self.item["strategy_decision"])
            self.assertEqual(r["actions"],{})
            self.assertEqual(r["models"],{"Chronos-2":.4})


    def test_seven_local_advisers_and_no_cloud_arena(self):
        from model_arena_api import LIVE_MODELS,CLOUD_CHALLENGERS
        body={"symbol":"XAUUSD","bar_time":self.bar,"models":{n:.5 for n in LIVE_MODELS+CLOUD_CHALLENGERS}}
        self.assertTrue(a.accept("owner",self.item,body)["accepted"])
        self.assertEqual(set(a.evidence("owner",self.item["strategy_decision"])["models"]),set(LIVE_MODELS))
        self.assertEqual(len(LIVE_MODELS),7)

    def test_extra_trees_demo_only_no_legacy_vote(self):
        import master_ai as m
        body={"symbol":"XAUUSD","bar_time":self.bar,"models":{"Extra Trees":.5}}
        self.assertFalse(a.accept("owner",self.item,body)["accepted"])
        demo={**self.item,"client_id":"owner","master_direct_demo_capable":True,"account":{"trade_mode":"DEMO"}}
        with patch.dict("os.environ",{"ALZOOL_HYBRID_DEMO":"1"}):
            self.assertTrue(a.accept("owner",demo,body)["accepted"])
            evidence=a.evidence("owner",self.item["strategy_decision"])
            self.assertEqual(evidence["models"],{"Extra Trees":.5})
            self.assertEqual(m.arena_votes(self.item["strategy_decision"],evidence),{})
            self.assertFalse(a.accept("other",{**demo,"account":{"trade_mode":"REAL"}},body)["accepted"])

    def test_monitor_only_not_legacy_evidence(self):
        import master_ai as m
        body={"symbol":"XAUUSD","bar_time":self.bar,"monitor_models":{"Extra Trees":.9,"CatBoost":-.8}}
        demo={**self.item,"client_id":"owner","master_direct_demo_capable":True,"account":{"trade_mode":"DEMO"}}
        with patch.dict("os.environ",{"ALZOOL_HYBRID_DEMO":"1"}):
            self.assertTrue(a.accept("owner",demo,body)["accepted"])
            r=a.evidence("owner",self.item["strategy_decision"])
            self.assertEqual(r["models"],{})
            self.assertEqual(r["monitor_models"],body["monitor_models"])
            self.assertEqual(m.arena_votes(self.item["strategy_decision"],r),{})
            self.assertFalse(a.accept("other",{**demo,"account":{"trade_mode":"REAL"}},body)["accepted"])
            self.assertTrue(a.accept("owner",demo,{"symbol":"XAUUSD","bar_time":self.bar,"models":{"CatBoost":.4}})["accepted"])
            self.assertEqual(a.evidence("owner",self.item["strategy_decision"])["monitor_models"],{"Extra Trees":.9})
    def test_master_uses_fresh_local_advice_without_permission(self):
        import master_ai as m
        from model_arena_api import LIVE_MODELS
        base={"symbol":"XAUUSD","bar_time":self.bar,"decision":"BUY","strategy_id":1,
              "directional_candidates":{"BUY":{"strategy_id":1},"SELL":{"strategy_id":2}},
              "directional_scores":{"BUY":.51,"SELL":.50}}
        a.accept("owner",self.item,{"symbol":"XAUUSD","bar_time":self.bar,"models":{n:-.9 for n in LIVE_MODELS}})
        result=m.decide(base,{"bar_time":self.bar,"votes":{}},arena_evidence=a.evidence("owner",base))
        self.assertEqual(result["decision"],"SELL")
        self.assertEqual(result["strategy_id"],2)
        self.assertEqual(len(result["master"]["arena_advisory"]["used"]),7)
        self.assertLessEqual(result["master"]["arena_advisory"]["total_points"],.14)
        self.assertFalse(result["master"]["execution_authorized"])
        self.assertEqual(m.decide(base,{},arena_evidence={})["decision"],"BUY")
        stale={**a.evidence("owner",base),"bar_time":self.bar-60}
        self.assertEqual(m.decide(base,{},arena_evidence=stale)["decision"],"BUY")
        no_strategy={**base,"decision":"WAIT","strategy_id":0,"directional_candidates":{}}
        self.assertEqual(m.decide(no_strategy,{},arena_evidence=a.evidence("owner",base))["decision"],"WAIT")

    def test_advice_never_revives_stale_council_votes(self):
        import master_ai as m
        base={"symbol":"XAUUSD","bar_time":self.bar,"decision":"BUY","strategy_id":1,"confidence":.7}
        a.accept("owner",self.item,{"symbol":"XAUUSD","bar_time":self.bar,"models":{"TiRex-2":.8}})
        result=m.decide(base,{"bar_time":self.bar-60,"votes":{"Qwen":{"status":"READY","direction":"BUY","confidence":1.0}}},arena_evidence=a.evidence("owner",base))
        rows={x["model"]:x for x in result["master"]["models"]}
        self.assertTrue(rows["TiRex-2"]["used"])
        self.assertFalse(rows["Qwen"]["used"])
