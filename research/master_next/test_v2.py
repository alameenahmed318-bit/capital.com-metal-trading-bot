import unittest
import numpy as np
from core import features,evaluate
from v2_env import make_env

class TestV2(unittest.TestCase):
    def setup_env(self,slip=.1):
        c=np.full(1000,100.);t=1700000000+60*np.arange(1000)
        b=np.column_stack([t,c,c+.3,c-.3,c,np.full(1000,100),np.full(1000,10),np.full(1000,.01)])
        x,atr,valid=features(b)
        return make_env(b,x,atr,valid,(400,600),slippage=slip)
    def test_hold_cash(self):
        self.assertEqual(evaluate(self.setup_env(),lambda o:0)["net_atr_units"],0)
    def test_exit_age(self):
        e=self.setup_env();e.reset()
        e.step(1)
        for _ in range(15):_,_,_,_,r=e.step(0)
        self.assertEqual(e.pos,0)
        self.assertEqual(r["forced_exits"],1)
    def test_cost(self):
        p=lambda o:1 if o[-8]==0 else 0
        self.assertGreater(evaluate(self.setup_env(),p)["net_atr_units"],evaluate(self.setup_env(.3),p)["net_atr_units"])
    def test_symmetry(self):
        a=evaluate(self.setup_env(),lambda o:1 if o[-8]==0 else 0)
        b=evaluate(self.setup_env(),lambda o:2 if o[-8]==0 else 0)
        self.assertAlmostEqual(a["net_atr_units"],b["net_atr_units"])
if __name__=="__main__":unittest.main()
