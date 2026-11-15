"""Regression tests: diversity-normalized voting and no-lookahead research."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from strategy_engine import STRATEGIES,COMBINATIONS,evaluate_market
from strategy_audit import audit_strategies

def candles(n):
    return [{"candle_time":f"bar-{i:03d}","candle_open":2500+i*.04,
             "candle_high":2500+i*.04+.3,"candle_low":2500+i*.04-.3,
             "candle_close":2500+i*.04+.06,"tick_volume":100+i%8}
             for i in range(n)]

class StrategyVotingTests(unittest.TestCase):
    def test_real_composites_and_deterministic_groups(self):
        self.assertEqual(len(STRATEGIES),200)
        self.assertEqual(len(COMBINATIONS),1000)
        self.assertEqual(len(set(tuple(x["members"]) for x in COMBINATIONS)),1000)
        self.assertTrue(all(len(set(x["members"]))==5 for x in COMBINATIONS))
    def test_overlapping_groups_do_not_count_as_1000_independent_votes(self):
        original=candles(100)
        rates=[{"time":row["candle_time"],"open":row["candle_open"],
             "high":row["candle_high"],"low":row["candle_low"],
             "close":row["candle_close"],"tick_volume":row["tick_volume"]} for row in original]
        d=evaluate_market(rates)
        self.assertEqual(len(d["votes"]),200)
        self.assertEqual(len(d["combination_votes"]),1000)
        self.assertEqual(d["vote_method"],"FAMILY_20_PLUS_OVERLAP_NORMALIZED_COMBOS")
        self.assertGreater(d["effective_combination_votes"],0)
        self.assertLess(d["effective_combination_votes"],231)
        self.assertTrue(all("reason" in v and v["signal"] in ("BUY","SELL","WAIT") for v in d["votes"]))
        self.assertTrue(all(v["effective_weight"]>0 for v in d["combination_votes"]))
    def test_insufficient_audit_never_claims_a_profit(self):
        result=audit_strategies(candles(70))
        self.assertEqual(result["status"],"INSUFFICIENT_HISTORY")
        self.assertFalse(result["auto_activate"])
    def test_audit_uses_closed_window_never_target_candle(self):
        rows=candles(85)
        seen=[]
        def fake(window):
            seen.append(window[-1]["time"])
            return {"votes":[{"id":1,"signal":"BUY"}],
                    "combination_votes":[{"id":1,"signal":"BUY"}]}
        with patch("strategy_audit.evaluate_market",side_effect=fake):
            result=audit_strategies(rows,max_samples=6)
        self.assertEqual(result["training_points"]+result["validation_points"],6)
        self.assertEqual(result["strategies_total"],200)
        self.assertEqual(result["combinations_total"],1000)
        self.assertEqual(len(seen),6)
        self.assertNotIn(rows[-1]["candle_time"],seen)
        self.assertFalse(result["auto_activate"])
        self.assertEqual(result["weight_changes_applied"],0)
        top=next(x for x in result["strategy_leaders"] if x["id"]==1)
        self.assertFalse(top["trade_profit_proven"])
if __name__=="__main__":unittest.main()
