"""Read-only agent regression tests; no broker connectivity required."""
import copy
import math
import unittest
from bot_advisors import analyze_advisors, MIN_CANDLES
from bot_signal_bus import build_bot_messages, update_history, market_bar_fresh


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
                         "five_separate_rule_based_analysis_engines_no_execution")
        for item in result["advisors"]:
            self.assertIn(item["signal"], ("BUY","SELL","WAIT"))
            self.assertTrue(0.0 <= item["confidence"] <= .85)
            self.assertTrue(item["reason_ar"])
            self.assertNotIn("execute", item)
            self.assertNotIn("order", item)

    def test_five_distinct_messages_have_per_bot_ids_and_no_orders(self):
        report = analyze_advisors(candles())
        messages = build_bot_messages("account-a", "XAUUSD", "PERIOD_M1", report, "WAIT")
        self.assertEqual(len(messages), 5)
        self.assertEqual(len({event["event_id"] for event in messages}), 5)
        self.assertEqual(len({event["payload"]["bot_id"] for event in messages}), 5)
        self.assertTrue(all(event["event_type"] == "BOT_ANALYSIS_SIGNAL" for event in messages))
        self.assertTrue(all(event["payload"]["source"] == "MT5_CLOSED_CANDLE" for event in messages))
        self.assertTrue(all(event["payload"]["can_execute"] is False for event in messages))
        self.assertTrue(all("evidence" in event["payload"] for event in messages))
        self.assertEqual(len(update_history(messages, messages)), 5)
        newer = analyze_advisors(candles(n=95))
        later = build_bot_messages("account-a", "XAUUSD", "PERIOD_M1", newer, "WAIT")
        self.assertEqual(len(update_history(messages, later)), 10)
        self.assertEqual(build_bot_messages("account-a", "EURUSD", "PERIOD_M1", report), [])

    def test_multi_factor_bot_is_not_derived_from_four_votes(self):
        report=analyze_advisors(candles())
        five=report["advisors"]
        self.assertIn("factor_score", five[-1]["evidence"])
        buys=sum(x["signal"]=="BUY" for x in five)
        sells=sum(x["signal"]=="SELL" for x in five)
        expected="BUY" if buys>=3 and sells==0 else "SELL" if sells>=3 and buys==0 else "WAIT"
        self.assertEqual(report["consensus"]["signal"], expected)

    def test_recent_bar_detection_prevents_stale_live_badges(self):
        now=1800010000
        self.assertTrue(market_bar_fresh(now-80,"PERIOD_M1",now))
        self.assertFalse(market_bar_fresh(now-400,"PERIOD_M1",now))
        self.assertTrue(market_bar_fresh(now-400,"PERIOD_M5",now))
        self.assertFalse(market_bar_fresh(now-1800,"PERIOD_M5",now))
        self.assertFalse(market_bar_fresh(now-3600*24,"PERIOD_M1",now))
        self.assertFalse(market_bar_fresh(now+1000,"PERIOD_M1",now))
        self.assertFalse(market_bar_fresh(now-80,"BAD_TIMEFRAME",now))
        self.assertFalse(market_bar_fresh(None,"PERIOD_M1",now))

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
