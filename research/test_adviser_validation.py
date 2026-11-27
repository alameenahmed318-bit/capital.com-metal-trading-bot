import unittest
from adviser_validation import split_rows,nonoverlap,metrics,select_threshold,SideModel,ActionModel

class Fake:
    def __init__(self,value):self.value=value
    def predict(self,x):return [self.value for _ in x]

class ValidationTests(unittest.TestCase):
    def test_temporal_purge(self):
        rows=[{"time":i*60,"end":i*60+180} for i in range(1000)]
        t,v,h=split_rows(rows,lambda r:r["time"],lambda r:r["end"])
        self.assertLess(t[-1]["end"],v[0]["time"])
        self.assertLess(v[-1]["end"],h[0]["time"])
        n=nonoverlap(h,lambda r:r["time"],lambda r:r["end"])
        self.assertTrue(all(a["end"]<b["time"] for a,b in zip(n,n[1:])))
    def test_losing_model_rejected(self):
        self.assertIsNone(select_threshold([.5]*40,[-.1]*40))
        self.assertIsNone(select_threshold([.5]*40,[.1]*40))
    def test_positive_model_passes(self):
        t=select_threshold([.8]*40,[.4]*40)
        self.assertIsNotNone(t)
        self.assertTrue(metrics([.8]*40,[.4]*40,t)["eligible"])
        self.assertFalse(metrics([.8]*10,[.4]*10,t)["eligible"])
    def test_direction_and_abstention(self):
        self.assertEqual(SideModel({"BUY":(Fake(.2),.3)}).predict([[0]]),[0.])
        self.assertEqual(SideModel({"SELL":(Fake(.7),.3)}).predict([[0]]),[-.7])
        self.assertEqual(SideModel({"BUY":(Fake(.8),.3),"SELL":(Fake(.5),.3)}).predict([[0]]),[.8])
        self.assertEqual(ActionModel(Fake(.2),.3).predict([[0]]),[0.])
if __name__=="__main__":unittest.main()
