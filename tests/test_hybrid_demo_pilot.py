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
            d={"symbol":"XAUUSD","bar_time":1000,"entry_atr":1,"master":{"arena_shadow":{"status":"SHADOW_READY","symbol":"XAUUSD","bar_time":1000,"received_at":1065,"models":{"CatBoost":.2,"LightGBM":.8,"TimesFM-2.5":.9,"Chronos-2":.7}}}}
            p={"status":"SHADOW_READY","client_id":"test","symbol":"XAUUSD","bar_time":1000,"received_at":1066,"models":{"TCN":{"forecast":.3},"Isolation Forest":{"forecast":.5}}}
            self.assertEqual(m.collect(d,self.item(),p,1070)["raw"],{"CatBoost":.2,"TCN":.3})
            self.assertEqual(m.collect(d,self.item(),{**p,"client_id":"other"},1070)["raw"],{"CatBoost":.2})
            self.assertIsNone(m.collect(d,self.item("REAL"),p,1070))
    def test_retirement(self):
        rows=[{"raw":{"TCN":.2,"CatBoost":.4},"y":[-.1,.1]} for _ in range(60)]
        self.assertEqual(set(m.retired(rows)),{"TCN","CatBoost"})
        self.assertEqual(m.retired(rows[:59]),())
        row={"raw":{"TCN":.2},"context":[0]*6}
        self.assertEqual(m.vector(row,("TCN",))[8:10],[0.,0.])
if __name__=="__main__":unittest.main()
