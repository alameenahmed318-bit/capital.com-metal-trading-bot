"""Regression tests for as-of forecast feed; synthetic data only."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"research"))
from hybrid_meta_master import features, CURRENT_ADVISERS, PROPOSED_ADVISERS

def row():
    return {"symbol":"XAUUSD","bar_time":1000,"observed_at":1070,"atr":1,
        "bid":10,"ask":10.1,"candidate":{"adaptive":{"forecasts":{"CatBoost":0.0}},
        "adaptive_baseline_review":{"statuses":{"CatBoost":"READY"}}}}
def pair(p,name,advisers=CURRENT_ADVISERS):
    i=advisers.index(name)*2
    return tuple(features(p,advisers)[i:i+2])
class FeedTests(unittest.TestCase):
    def test_asof_and_zero(self):
        p=row()
        p["arena_evidence"]={"status":"SHADOW_READY","symbol":"XAUUSD",
          "bar_time":1000,"received_at":1065,"models":{"TiRex-2":.3,"CatBoost":.4}}
        self.assertEqual(pair(p,"TiRex-2"),(.3,1.))
        self.assertEqual(pair(p,"CatBoost"),(0.,1.))
        self.assertEqual(pair(p,"LightGBM"),(0.,0.))
    def test_reject_wrong_bar_symbol_and_future(self):
        for patch in ({"received_at":1071},{"received_at":1009},{"bar_time":940},{"symbol":"BTCUSDm"}):
            p=row()
            p["arena_evidence"]={"status":"SHADOW_READY","symbol":"XAUUSD","bar_time":1000,
                "received_at":1065,"models":{"TiRex-2":.3},**patch}
            self.assertEqual(pair(p,"TiRex-2"),(0.,0.))
    def test_parallel(self):
        p=row()
        p["parallel_evidence"]={"status":"SHADOW_READY","symbol":"XAUUSD","bar_time":1000,
            "received_at":1068,"forecasts":{"TCN":.2,"Isolation Forest":.9}}
        self.assertEqual(pair(p,"TCN",PROPOSED_ADVISERS),(.2,1.))
        p["parallel_evidence"]["received_at"]=1071
        self.assertEqual(pair(p,"TCN",PROPOSED_ADVISERS),(0.,0.))
if __name__=="__main__":unittest.main()
