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
