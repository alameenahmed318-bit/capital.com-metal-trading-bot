"""Small trade samples are evidence, not permission to amplify live risk."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from app import conservative_outcome_weight,live_learning_profile

class LearningSafetyTests(unittest.TestCase):
    def test_single_win_is_neutral(self):
        self.assertEqual(conservative_outcome_weight(1,0,9),1.0)
        self.assertEqual(conservative_outcome_weight(7,0,70),1.0)
    def test_eight_net_winners_gain_bounded_weight(self):
        value=conservative_outcome_weight(8,0,10)
        self.assertGreater(value,1.0)
        self.assertLessEqual(value,1.5)
    def test_negative_net_never_gets_boost(self):
        self.assertLessEqual(conservative_outcome_weight(12,1,-3),1.0)
    def test_losing_sample_lowers_weight(self):
        self.assertLess(conservative_outcome_weight(0,8,-40),1.0)
    def test_strategy_weights_require_real_attributed_closed_deals(self):
        rows=[{"ticket":str(i),"position_id":str(i),"symbol":"XAUUSD",
               "entry":1,"time":1763200000+i,"strategy_id":7,"profit":4,
               "commission":-1,"swap":0,"fee":0} for i in range(7)]
        first=live_learning_profile(rows)
        self.assertEqual(first["weights"]["7"],1.0)
        rows.append({"ticket":"7","position_id":"7","symbol":"XAUUSD",
               "entry":1,"time":1763200007,"strategy_id":7,"profit":4,
               "commission":-1})
        second=live_learning_profile(rows)
        self.assertGreater(second["weights"]["7"],1.0)
        self.assertEqual(second["weights"]["8"],1.0)
        self.assertEqual(second["updated_strategies"],1)
    def test_unattributed_trade_does_not_change_weights(self):
        result=live_learning_profile([{"ticket":"1","symbol":"XAUUSD","entry":1,
             "time":1763200000,"profit":100}])
        self.assertEqual(result["updated_strategies"],0)
if __name__=="__main__":unittest.main()
