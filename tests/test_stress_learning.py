"""Tests are synthetic fixtures only, never broker outcomes."""
import pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from stress_learning import fit_and_test,outcomes,eligible_indices,SCENARIOS

def candles(n):
    return [{"time":1700000040+60*i,"open":100+i,"close":101+i,
             "high":102+i,"low":99+i,"spread_points":1,"point_size":.1,
             "tick_volume":10} for i in range(n)]

class StressLearningTests(unittest.TestCase):
    def test_cost_and_delay_are_hypothetical(self):
        rows=candles(130)
        x=outcomes(rows,119,"BUY",1.0)
        self.assertAlmostEqual(x["observed"],.9)
        self.assertAlmostEqual(x["wide"],.6)
        self.assertAlmostEqual(x["extreme"],-.1)
        self.assertAlmostEqual(x["gap"],-.85)
        self.assertAlmostEqual(x["delayed"],1.6)
        self.assertEqual(set(x),set(SCENARIOS))
        self.assertEqual(outcomes(rows,119,"WAIT",1),dict.fromkeys(SCENARIOS,0))
    def test_time_split_and_learning_weight_proof(self):
        seen=[]
        def evaluate(rows,market_symbol,weights=None,combination_weights=None):
            self.assertEqual(len(rows),120)
            seen.append((rows[-1]["time"],weights))
            return {"entry_atr":1.,"context":{"regime":"اتجاه صاعد"},
                "decision":"BUY","votes":[{"id":1,"signal":"BUY"}],
                "combination_votes":[{"id":1,"signal":"BUY"}]}
        report,models=fit_and_test(candles(500),"BTCUSD",evaluate,0)
        self.assertEqual(report["status"],"RESEARCH_COMPLETE")
        self.assertLess(report["training_last_outcome"],report["validation_first_origin"])
        self.assertGreater(report["changed_strategies"],0)
        self.assertGreater(report["changed_combinations"],0)
        self.assertEqual(models["stress"]["global"]["s"]["1"][0],report["training_windows"])
        self.assertTrue(any(weights for _,weights in seen if weights))
        self.assertEqual(report["stress_observations"],len(SCENARIOS)*report["validation_windows"])
        self.assertFalse(report["actual_trading_proof"])
        self.assertFalse(report["active_weights_changed"])
        self.assertEqual(report["verdict"],"NOT_VALIDATED")
    def test_no_gap_crossing(self):
        rows=candles(150)
        for i in range(70,len(rows)):rows[i]["time"]+=120
        self.assertEqual(eligible_indices(rows),[])
    def test_data_cannot_be_invented(self):
        report,model=fit_and_test(candles(90),"XAUUSD")
        self.assertEqual(report["status"],"INSUFFICIENT_HISTORY")
        self.assertEqual(model,{})

if __name__=="__main__":unittest.main()
