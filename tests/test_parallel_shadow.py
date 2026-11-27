import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace/backend"))
import parallel_shadow as p
class ShadowTests(unittest.TestCase):
    def setUp(self):
        p.CACHE.clear();self.ctx={"status":"READY","client":"c","symbol":"XAUUSD","bar_time":1000}
        self.data={"client":"c","symbol":"XAUUSD","bar_time":1000,"models":{"TCN":{"forecast":.6},"LSTM":{"forecast":.2},"Ensemble":{"forecast":6},"TimesFM-2.5":{"forecast":6}}}
    def test_no_execution_no_double_count(self):
        self.assertTrue(p.accept("a","XAUUSD",self.ctx,self.data,1065)["accepted"])
        r=p.snapshot("a","XAUUSD",1066)
        self.assertFalse(r["execution_authorized"]);self.assertEqual(r["models"]["Ensemble"]["weight"],0)
        self.assertNotIn("TimesFM-2.5",r["models"])
        self.assertLessEqual(sum(m["weight"] for m in r["models"].values()),1.00001)
    def test_monitor_weight_zero(self):
        data={**self.data,"models":{"GRU":{"forecast":.9,"monitor_only":True},"TCN":{"forecast":.3}}}
        self.assertTrue(p.accept("a","XAUUSD",self.ctx,data,1065)["accepted"])
        r=p.snapshot("a","XAUUSD",1066)
        self.assertEqual(r["models"]["GRU"]["forecast"],.9)
        self.assertEqual(r["models"]["GRU"]["status"],"MONITOR_ONLY")
        self.assertEqual(r["models"]["GRU"]["weight"],0)
        self.assertAlmostEqual(r["models"]["Ensemble"]["forecast"],.1,places=5)
    def test_scope_and_stale(self):
        p.accept("a","XAUUSD",self.ctx,self.data,1065)
        for uid,market,now in (("b","XAUUSD",1066),("a","BTCUSD",1066),("a","XAUUSD",1120)):
            self.assertEqual(p.snapshot(uid,market,now)["status"],"NO_FRESH_SHADOW")
        self.assertFalse(p.accept("a","XAUUSD",self.ctx,self.data,1120)["accepted"])
    def test_wrong_symbol_and_bad_number(self):
        self.assertFalse(p.accept("a","XAUUSD",self.ctx,{**self.data,"symbol":"BTCUSD"},1065)["accepted"])
        self.assertFalse(p.accept("a","XAUUSD",self.ctx,{**self.data,"models":{"TCN":{"forecast":float("nan")}}},1065)["accepted"])
if __name__=="__main__":unittest.main()
