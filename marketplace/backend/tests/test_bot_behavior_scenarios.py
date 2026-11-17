"""Behavioral tests against the five *actual* AL-ZOOL engines, not mocked outputs.

Fixtures are synthetic closed XAUUSD M1 candles. These verify signal direction,
message schemas and safety properties; they are NOT backtests of profitability.
"""
import copy
import json
import time
import unittest
from bot_advisors import analyze_advisors
from bot_signal_bus import build_bot_messages, update_history

BOT_IDS={"adaptive","selective","scalping","breakout","ensemble"}


def regime(steps, n=100, start=2300.0, first_time=1800000000):
    result=[]
    price=start
    for i in range(n):
        change=steps[i % len(steps)]
        close=round(price+change,5)
        result.append({"time":first_time+i*60,"open":round(price,5),
            "high":round(max(price,close)+.08,5),
            "low":round(min(price,close)-.08,5),
            "close":close,"tick_volume":280 if i==n-1 else 250})
        price=close
    return result


class TradingBotBehaviorTests(unittest.TestCase):
    def test_bullish_gold_regime_generates_buy_consensus(self):
        candles=regime([1.0,.5,-.7])
        report=analyze_advisors(candles)
        print("BOT_SCENARIO_BULL="+json.dumps({"close":report["last_close"],"consensus":report["consensus"],"indicators":report["advisors"][0]["evidence"],"bots":[{"id":x["id"],"signal":x["signal"],"confidence":x["confidence"]} for x in report["advisors"]]},ensure_ascii=False),flush=True)
        self.assertEqual(report["status"],"READY")
        self.assertEqual(report["consensus"]["signal"],"BUY")
        self.assertEqual(len(report["advisors"]),5)
        self.assertGreaterEqual(report["consensus"]["buy"],3)
        self.assertEqual(report["consensus"]["sell"],0)
        for bot in report["advisors"]:
            self.assertIn(bot["signal"],("BUY","WAIT"))
            self.assertGreater(len(bot["reason_ar"]),10)

    def test_bearish_gold_regime_generates_sell_consensus(self):
        candles=regime([-1.0,-.5,.7])
        report=analyze_advisors(candles)
        print("BOT_SCENARIO_BEAR="+json.dumps({"close":report["last_close"],"consensus":report["consensus"],"indicators":report["advisors"][0]["evidence"],"bots":[{"id":x["id"],"signal":x["signal"],"confidence":x["confidence"]} for x in report["advisors"]]},ensure_ascii=False),flush=True)
        self.assertEqual(report["status"],"READY")
        self.assertEqual(report["consensus"]["signal"],"SELL")
        self.assertGreaterEqual(report["consensus"]["sell"],3)
        self.assertEqual(report["consensus"]["buy"],0)
        for bot in report["advisors"]:
            self.assertIn(bot["signal"],("SELL","WAIT"))

    def test_flat_market_waits_instead_of_inventing_signal(self):
        report=analyze_advisors(regime([0.0]))
        print("BOT_SCENARIO_FLAT="+json.dumps({"close":report["last_close"],"consensus":report["consensus"],"bots":[{"id":x["id"],"signal":x["signal"]} for x in report["advisors"]]},ensure_ascii=False),flush=True)
        self.assertEqual(report["status"],"READY")
        self.assertEqual(report["consensus"]["signal"],"WAIT")
        self.assertTrue(all(x["signal"]=="WAIT" for x in report["advisors"]))

    def test_changed_closed_bar_produces_new_timestamped_messages(self):
        bars=regime([1.0,.5,-.7],n=100)
        original=copy.deepcopy(bars)
        first=analyze_advisors(bars)
        messages=build_bot_messages("demo-account","XAUUSD","PERIOD_M1",first)
        self.assertEqual(len(messages),5)
        self.assertEqual({m["payload"]["bot_id"] for m in messages},BOT_IDS)
        self.assertEqual(len({m["event_id"] for m in messages}),5)
        self.assertTrue(all(m["payload"]["bar_time"]==bars[-1]["time"] for m in messages))
        self.assertTrue(all(m["payload"]["can_execute"] is False for m in messages))
        self.assertTrue(all(m["payload"]["evidence"] for m in messages))
        self.assertEqual(bars,original)
        self.assertEqual(len(update_history(messages,messages)),5)
        self.assertEqual(len(json.dumps(messages,ensure_ascii=False)),len(json.dumps(messages,ensure_ascii=False)))
        next_bar=regime([1.0,.5,-.7],n=101)
        second=build_bot_messages("demo-account","XAUUSD","PERIOD_M1",analyze_advisors(next_bar))
        self.assertEqual(len(update_history(messages,second)),10)
        self.assertEqual(len({x["event_id"] for x in update_history(messages,second)}),10)
        other=build_bot_messages("another-account","XAUUSD","PERIOD_M1",first)
        self.assertTrue(set(x["event_id"] for x in messages).isdisjoint(x["event_id"] for x in other))

    def test_false_breakout_with_long_upper_wick_is_rejected(self):
        bars=regime([0.0])
        bars[-1]={**bars[-1],"open":2300.0,"high":2305.0,
                  "low":2299.9,"close":2300.01}
        result=analyze_advisors(bars)
        breakout=next(x for x in result["advisors"] if x["id"]=="breakout")
        self.assertEqual(breakout["signal"],"WAIT")
        self.assertEqual(result["consensus"]["signal"],"WAIT")
        print("BOT_SCENARIO_FALSE_BREAKOUT="+json.dumps({
            "last_high":2305.0,"last_close":result["last_close"],
            "prior20_high":breakout["evidence"]["prior20_high"],
            "breakout_signal":breakout["signal"]},ensure_ascii=False),flush=True)

    def test_low_tick_volume_blocks_breakout_and_fast_momentum(self):
        bars=regime([1.0,.5,-.7])
        bars[-1]["tick_volume"]=0
        report=analyze_advisors(bars)
        bot={x["id"]:x["signal"] for x in report["advisors"]}
        self.assertEqual(bot["scalping"],"WAIT")
        self.assertEqual(bot["breakout"],"WAIT")
        self.assertEqual(bot["ensemble"],"WAIT")
        self.assertEqual(report["consensus"]["signal"],"WAIT")
        print("BOT_SCENARIO_NO_VOLUME="+json.dumps({
            "signal":report["consensus"]["signal"],"votes":bot},ensure_ascii=False),flush=True)

    def test_analytical_cpu_time_without_network_or_db(self):
        rows=regime([1.0,.5,-.7],n=120)
        start=time.perf_counter()
        for _ in range(250):
            report=analyze_advisors(rows)
        elapsed_ms=(time.perf_counter()-start)*1000
        self.assertEqual(report["status"],"READY")
        print("BOT_BENCHMARK_CPU_MS_PER_120_CANDLES="+str(round(elapsed_ms/250,4)),flush=True)

    def test_bad_or_insufficient_market_data_does_not_trade(self):
        normal=regime([1.0,.5,-.7])
        cases=[[],normal[:64],list(reversed(normal)),
            [{**b,"time":normal[35]["time"]} if i==36 else b for i,b in enumerate(normal)],
            [{**b,"close":float("nan")} if i==99 else b for i,b in enumerate(normal)],
            [{**b,"high":b["low"]-.01} if i==99 else b for i,b in enumerate(normal)],
            [{**b,"low":-1} if i==99 else b for i,b in enumerate(normal)],
            [{**b,"close":"not a price"} if i==99 else b for i,b in enumerate(normal)]]
        for i,data in enumerate(cases):
            with self.subTest(case=i):
                report=analyze_advisors(data)
                self.assertEqual(report["status"],"WAIT")
                self.assertEqual(report["consensus"]["signal"],"WAIT")
                self.assertEqual(build_bot_messages("demo","XAUUSD","PERIOD_M1",report),[])

if __name__=="__main__":
    unittest.main()
