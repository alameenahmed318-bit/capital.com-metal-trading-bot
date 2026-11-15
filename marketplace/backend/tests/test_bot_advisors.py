"""Read-only agent regression tests; no broker connectivity required."""
import copy
import math
import unittest
from bot_advisors import analyze_advisors, MIN_CANDLES


def candles(n=90, drift=0.14):
    rows = []
    last = 2300.0
    for i in range(n):
        close = last + drift + 0.18 * math.sin(i / 3.0)
        rows.append({
            "time": 1800000000 + i * 60, "open": last,
            "high": max(last, close) + .18,
            "low": min(last, close) - .18,
            "close": close, "tick_volume": 200 + (i % 4) * 5
        })
        last = close
    return rows


class BotAdvisorsTests(unittest.TestCase):
    def test_all_five_advisors_are_real_deterministic_functions(self):
        data = candles()
        before = copy.deepcopy(data)
        result = analyze_advisors(data)
        self.assertEqual(result["status"], "READY")
        self.assertEqual(len(result["advisors"]), 5)
        self.assertEqual([x["id"] for x in result["advisors"]],
                         ["adaptive", "selective", "scalping", "breakout", "ensemble"])
        self.assertEqual(result, analyze_advisors(data))
        self.assertEqual(data, before)
        self.assertEqual(result["bar_time"], data[-1]["time"])
        self.assertEqual(result["consensus"]["method"],
                         "four_independent_rule_sets_fifth_derived_advisory_only_no_execution")
        for item in result["advisors"]:
            self.assertIn(item["signal"], ("BUY","SELL","WAIT"))
            self.assertTrue(0.0 <= item["confidence"] <= .85)
            self.assertTrue(item["reason_ar"])
            self.assertNotIn("execute", item)
            self.assertNotIn("order", item)

    def test_insufficient_or_corrupted_bars_fail_closed(self):
        for rows in ([], candles(MIN_CANDLES-1), list(reversed(candles())),
                     [{**x, "high": x["low"]-1} if i == 65 else x
                      for i,x in enumerate(candles())]):
            report = analyze_advisors(rows)
            self.assertEqual(report["status"], "WAIT")
            self.assertEqual(report["consensus"]["signal"], "WAIT")
            self.assertTrue(all(x["signal"] == "WAIT" for x in report["advisors"]))

    def test_no_forming_bar_input_contract(self):
        report = analyze_advisors(candles())
        self.assertEqual(report["bar_time"], candles()[-1]["time"])
        self.assertNotIn("positions", report)
        self.assertNotIn("trading_enabled", report)

    def test_non_finite_prices_fail_closed(self):
        bad = candles()
        bad[-1]["close"] = float("nan")
        self.assertEqual(analyze_advisors(bad)["status"], "WAIT")
        self.assertTrue(all(x["signal"] == "WAIT" for x in analyze_advisors(bad)["advisors"]))


if __name__ == "__main__":
    unittest.main()
