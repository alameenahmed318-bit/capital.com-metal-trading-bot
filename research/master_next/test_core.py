import unittest
import numpy as np
from core import features,segments,target_for,make_env,evaluate

class TestResearch(unittest.TestCase):
    def bars(self):
        t=np.arange(1000)*60+1700000000;c=100+np.arange(1000)*.01
        return np.column_stack([t,c,c+.3,c-.3,c,np.ones(1000)*100,np.ones(1000)*10,np.ones(1000)*.01])
    def test_causal(self):
        b=self.bars();x,a,v=features(b)
        b[600:,1:5]+=100
        y,d,w=features(b)
        np.testing.assert_array_equal(x[:600],y[:600])
        np.testing.assert_array_equal(a[:600],d[:600])
    def test_actions(self):
        self.assertEqual(target_for(3,1,1),2)
        self.assertEqual(target_for(4,1,-1),2)
        self.assertEqual(target_for(4,2,-1),2)
        self.assertEqual(target_for(7,-2,-1),0)
        self.assertEqual(target_for(2,1,0),1)
    def test_splits(self):
        parts=list(segments(1000).values())
        for a,b in zip(parts,parts[1:]):self.assertLess(a[1],b[0])
    def test_cost_and_cash(self):
        b=self.bars();x,a,v=features(b);s=(400,600)
        self.assertEqual(evaluate(make_env(b,x,a,v,s),lambda o:0)["net_atr_units"],0)
        normal=evaluate(make_env(b,x,a,v,s,slippage=.1),lambda o:1)
        stress=evaluate(make_env(b,x,a,v,s,slippage=.3),lambda o:1)
        self.assertGreater(normal["net_atr_units"],stress["net_atr_units"])
        self.assertEqual(normal["fills"],2)
    def test_gap_mask(self):
        b=self.bars();b[500:,0]+=120
        _,_,v=features(b)
        self.assertFalse(v[500:532].any())
        self.assertTrue(v[532])
if __name__=="__main__":unittest.main()
