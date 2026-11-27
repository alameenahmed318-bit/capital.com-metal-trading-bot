import os,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
import hybrid_demo_pilot as m
class PilotTest(unittest.TestCase):
    def item(self,mode="DEMO"):
        return {"account":{"trade_mode":mode},"master_direct_demo_capable":True,"timeframe":"M1","client_id":"test","symbol":"XAUUSD","tick":{"bid":100,"ask":100.1}}
    def test_demo_only(self):
        with patch.dict(os.environ,{"ALZOOL_HYBRID_DEMO":"1"}):
            self.assertTrue(m.allowed(self.item()))
            for mode in ("REAL","CONTEST","UNKNOWN",None):self.assertFalse(m.allowed(self.item(mode)))
            self.assertFalse(m.allowed({**self.item(),"master_direct_demo_capable":False}))
        with patch.dict(os.environ,{"ALZOOL_HYBRID_DEMO":"0"}):self.assertFalse(m.allowed(self.item()))
    def test_seven_exact_and_stale(self):
        with patch.dict(os.environ,{"ALZOOL_HYBRID_DEMO":"1"}):
            d={"symbol":"XAUUSD","bar_time":1000,"entry_atr":1,"master":{"arena_shadow":{"status":"SHADOW_READY","symbol":"XAUUSD","bar_time":1000,"received_at":1065,"models":{"CatBoost":.2,"Extra Trees":.4,"XGBoost":.7,"LightGBM":.8,"TimesFM-2.5":.9,"Chronos-2":.7}}}}
            p={"status":"SHADOW_READY","client_id":"test","symbol":"XAUUSD","bar_time":1000,"received_at":1066,"models":{"TCN":{"forecast":.3},"GRU":{"forecast":.6},"LSTM Multi":{"forecast":.9},"Isolation Forest":{"forecast":.5}}}
            self.assertEqual(m.collect(d,self.item(),p,1070)["raw"],{"CatBoost":.2,"Extra Trees":.4,"TCN":.3,"GRU":.6})
            self.assertEqual(m.collect(d,self.item(),{**p,"client_id":"other"},1070)["raw"],{"CatBoost":.2,"Extra Trees":.4})
            self.assertIsNone(m.collect(d,self.item("REAL"),p,1070))
    def test_master_evaluates_all_local_analyses(self):
        with patch.dict(os.environ,{"ALZOOL_HYBRID_DEMO":"1"}):
            d={"symbol":"XAUUSD","bar_time":1000,"entry_atr":1,"master":{"arena_shadow":{"status":"SHADOW_READY","symbol":"XAUUSD","bar_time":1000,"received_at":1065,"models":{"TiRex-2":.2},"monitor_models":{"Extra Trees":.8,"CatBoost":-.7}}}}
            p={"status":"SHADOW_READY","client_id":"test","symbol":"XAUUSD","bar_time":1000,"received_at":1066,"models":{"GRU":{"forecast":.9,"status":"MONITOR_ONLY"},"TCN":{"forecast":.3}}}
            row=m.collect(d,self.item(),p,1070)
            self.assertEqual(row["raw"],{"TiRex-2":.2,"TCN":.3})
            self.assertEqual(row["monitor"],{"Extra Trees":.8,"CatBoost":-.7,"GRU":.9})
            vec=m.vector(row)
            for n in row["monitor"]:
                i=m.NAMES.index(n)*2
                self.assertEqual(vec[i:i+2],[row["monitor"][n],1.])
    def test_master_adaptive_weights_never_hard_zero(self):
        rows=[{"raw":{"TCN":.2},"monitor":{"GRU":-.4},"y":[1.,-2.]} for _ in range(120)]
        p=m.performance_profile(rows)
        self.assertEqual(set(p["weights"]),set(m.NAMES))
        self.assertAlmostEqual(sum(p["weights"].values()),1.)
        self.assertTrue(all(w>0 for w in p["weights"].values()))
        self.assertGreater(p["weights"]["TCN"],p["weights"]["GRU"])
        self.assertEqual(p["counts"]["GRU"],120)
    def test_strength_not_unanimity_or_probability(self):
        self.assertLess(m.calibrated_strength(.03,.03),.2)
        self.assertLess(m.calibrated_strength(.2,.2),.8)
        self.assertGreater(m.calibrated_strength(.7,.7),.8)
        self.assertLess(m.calibrated_strength(1.,1.),1.)
        self.assertEqual(m.calibrated_strength(0.,.9),0.)
        self.assertAlmostEqual(m.calibrated_strength(-.2,.2),-m.calibrated_strength(.2,.2))
    def test_durable_scope_and_idempotent_outcome(self):
        key=("test","XAUUSD")
        row={"key":list(key),"bar":1000,"raw":{"GRU":.2},"context":[0]*6,"y":[.1,-.2]}
        other={**row,"key":["test","BTCUSD"]}
        calls=[]
        def db(method,path,payload=None,**kw):
            calls.append((method,path,payload,kw))
            return [{"payload":{"symbol":"XAUUSD","sample":row}},{"payload":{"symbol":"BTCUSD","sample":other}}] if method=="GET" else []
        with patch.object(m,"MEMORY_DB",db):
            self.assertEqual(m.durable_load(key),[row])
            m.durable_save(key,row)
            m.durable_save(key,row)
        self.assertEqual(calls[1][2]["event_id"],calls[2][2]["event_id"])
        self.assertNotEqual(m.memory_id(key,1000),m.memory_id(("test","BTCUSD"),1000))
        self.assertEqual(calls[1][2]["client_id"],"test")
        self.assertIn("ignore-duplicates",calls[1][3]["prefer"])
    def test_m1_samples_isolated_and_richer_snapshot_causal(self):
        key=("test","XAUUSD")
        old={"key":list(key),"bar":900,"raw":{"GRU":.2},"context":[0]*6,"y":[.1,-.2],"horizons":{"GRU":3},"at":960,"end":1020}
        first={"key":key,"bar":1000,"at":1061,"raw":{"GRU":.2},"monitor":{},"horizons":{"GRU":1},"context":[0]*6,"atr":1.,"bid":100.,"ask":100.1}
        rich={**first,"at":1067,"bid":101.,"ask":101.1,"raw":{"GRU":.2,"TCN":.3},"horizons":{"GRU":1,"TCN":1}}
        later={**rich,"bar":1060,"at":1127,"bid":102.,"ask":102.1}
        with patch.object(m,"STATE",{}),patch.object(m,"PUBLISHED",{}),patch.object(m,"MEMORY_HEALTH",{}),patch.object(m,"load",return_value=[old]),patch.object(m,"durable_load",return_value=[]),patch.object(m,"save"),patch.object(m,"queue_memory") as persist:
            m.work(first)
            self.assertEqual(m.PUBLISHED[key]["samples"],0)
            m.work(rich)
            m.work(later)
            rows=m.STATE[key]["rows"]
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[-1]["at"],1067)
            self.assertAlmostEqual(rows[-1]["y"][0],.9)
            self.assertEqual(m.PUBLISHED[key]["samples"],1)
            self.assertEqual(m.PUBLISHED[key]["stored_samples"],2)
            self.assertEqual(persist.call_args.args[1]["bar"],1000)
    def test_resource_guard(self):
        with patch.dict(os.environ,{"ALZOOL_META_RESOURCE_GUARD":"1"}):
            with patch.object(m,"memory_headroom",return_value=200*1024*1024):self.assertFalse(m.training_resources_ready())
            with patch.object(m,"memory_headroom",return_value=None):self.assertFalse(m.training_resources_ready())
            with patch.object(m,"memory_headroom",return_value=450*1024*1024):self.assertTrue(m.training_resources_ready())
    def test_retirement(self):
        rows=[{"raw":{"TCN":.2,"CatBoost":.4},"y":[-.1,.1]} for _ in range(60)]
        self.assertEqual(set(m.retired(rows)),{"TCN","CatBoost"})
        self.assertEqual(m.retired(rows[:59]),())
        row={"raw":{"TCN":.2},"context":[0]*6}
        self.assertEqual(m.vector(row,("TCN",))[8:10],[0.,0.])
if __name__=="__main__":unittest.main()
