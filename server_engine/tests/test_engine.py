import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import STRATEGIES, COMBINATIONS, calculate_signal, evaluate_market


def sample_candles(count=100):
    rows=[]
    price=2300.0
    for i in range(count):
        price += 0.18 + ((i % 7)-3)*0.025
        rows.append({"time":1700000000+i*60,"open":price-.08,"high":price+.25,
                     "low":price-.20,"close":price,"tick_volume":100+i})
    return rows


class EngineTests(unittest.TestCase):
    def test_catalog_sizes_and_unique_combinations(self):
        self.assertEqual(len(STRATEGIES),200)
        self.assertEqual(len(COMBINATIONS),1000)
        self.assertEqual(len({x["members"] for x in COMBINATIONS}),1000)

    def test_insufficient_data_waits(self):
        signal, _ = calculate_signal(sample_candles(30), STRATEGIES[0])
        self.assertEqual(signal,"WAIT")

    def test_evaluator_returns_valid_contract(self):
        result=evaluate_market(sample_candles())
        self.assertIn(result["decision"],("BUY","SELL","WAIT"))
        self.assertIn("context",result)
        self.assertIn("strategy_counts",result)
        self.assertIn("combination_counts",result)


if __name__ == "__main__":
    unittest.main()
