import sys,unittest,math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
import specialist_market as s
def candles(n):
    out=[]
    for i in range(n):
        p=100+math.sin(i/13)*3+i*.005
        out.append({"time":1700000100+i*60,"open":p-.02,"close":p,"high":p+.2,"low":p-.2,
            "tick_volume":100+(i%15),"spread_points":1,"point_size":.01})
    return out
class SpecialistTests(unittest.TestCase):
    def test_insufficient(self):
        r,m=s.train(candles(100))
        self.assertIsNone(m);self.assertEqual(r["status"],"INSUFFICIENT_HISTORY")
    def test_temporal_purge(self):
        r,m=s.train(candles(850))
        self.assertEqual(r["status"],"TRAINED_RESEARCH_ONLY")
        self.assertLess(r["train_end"],r["validation_start"])
        self.assertLess(r["validation_start"],r["test_start"])
        self.assertGreater(len(m["trees"]),0)
        self.assertFalse(r["execution_authorized"])
        self.assertEqual(r["test"]["windows"],r["baseline_test"]["windows"])
    def test_gap_rejected(self):
        x=candles(100);x.pop(20)
        self.assertIsNone(s.features(x[:61]))
    def test_cost_and_labels(self):
        x=candles(100);rows=s.samples(x);self.assertTrue(rows)
        self.assertLess(rows[0]["buy"]+rows[0]["sell"],0)
    def test_fit_actual_relationship(self):
        rows=[{"x":[float(i%2)]+[0.]*(len(s.FEATURES)-1),"y":float(i%2)*2-1} for i in range(200)]
        m=s.fit(rows,12)
        self.assertLess(s.predict(m,rows[0]["x"]),0)
        self.assertGreater(s.predict(m,rows[1]["x"]),0)
if __name__=="__main__":unittest.main()
