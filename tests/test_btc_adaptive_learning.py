"""Pure BTC adaptive learning; no broker, credentials or network needed."""
import pathlib,sys,unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from btc_adaptive_learning import (evidence_weight, historical_profile, effective_weights,
    key_for, snapshot, _candle, REGIMES, VERSION)
from strategy_engine import evaluate_market
from app import live_learning_profile
from datetime import datetime,timezone

def bars(n=110):
    result=[]
    for i in range(n):
        p=90000+i*9
        result.append({"time":1700000000+i*60,"open":p,"high":p+11,"low":p-3,"close":p+8,
                       "tick_volume":100,"point_size":.01,"spread_points":50})
    return result

def evaluator(candles,market_symbol):
    assert market_symbol=="BTCUSD"
    return {"context":{"atr":10,"regime":"اتجاه صاعد"},
        "votes":[{"id":1,"signal":"BUY"},{"id":2,"signal":"SELL"}],
        "combination_votes":[{"id":1,"signal":"BUY"},{"id":2,"signal":"SELL"}]}

class BTCAdaptiveLearningTests(unittest.TestCase):
    def test_market_isolation(self):
        self.assertIsNone(key_for("a","XAUUSD","M1"))
        self.assertIsNone(key_for("a","BTCUSDm","M5"))
        self.assertNotEqual(key_for("a","BTCUSDm","M1"),key_for("b","BTCUSDm","M1"))
        self.assertNotEqual(key_for("a","BTCUSDm","M1"),key_for("a","BTCUSD","M1"))
    def test_first_trade_soft_and_bounded(self):
        self.assertGreater(evidence_weight(1,1,1,.3),1)
        self.assertLess(evidence_weight(1,-1,1,.3),1)
        self.assertLess(evidence_weight(1,1,1,.3),1.02)
        self.assertEqual(evidence_weight(0,0,0,.3),1)
    def test_history_same_library_uses_only_previous_window(self):
        seen=[]
        def spy(window,market_symbol):
            seen.append(window[-1]["time"])
            return evaluator(window,market_symbol)
        history=historical_profile(bars(),spy)
        self.assertEqual(history["status"],"READY")
        self.assertEqual(len(seen),len(set(seen)))
        self.assertLess(seen[-1],bars()[-1]["time"])
        self.assertEqual(history["samples"],len(seen))
        self.assertEqual(historical_profile(bars(),spy),history)
        weights=effective_weights(history,{},"اتجاه صاعد")
        self.assertGreater(weights["strategy_weights"]["1"],1)
        self.assertLess(weights["strategy_weights"]["2"],1)
        self.assertEqual(weights["diagnostics"]["status"],"APPLIED")
        self.assertEqual(weights["diagnostics"]["regime_samples"],history["samples"])
    def test_regime_weights_can_differ(self):
        h={"status":"READY","samples":80,"global":{"s":{"1":[80,0,80]},"c":{}},
           "regimes":{"اتجاه صاعد":{"s":{"1":[40,40,40]},"samples":40},
                      "اتجاه هابط":{"s":{"1":[40,-40,40]},"samples":40}}}
        up=effective_weights(h,{},"اتجاه صاعد")
        down=effective_weights(h,{},"اتجاه هابط")
        self.assertGreater(up["strategy_weights"]["1"],1)
        self.assertLess(down["strategy_weights"]["1"],1)
    def test_attributed_real_trade_enters_decision_without_eight(self):
        stamp=int(datetime.now(timezone.utc).timestamp())
        deals=[{"symbol":"BTCUSDm","entry":1,"position_id":"123","ticket":"456","time":stamp,
                "strategy_id":11,"strategy_comment":"ALZOOL-S11-C12","profit":-2},
               {"symbol":"XAUUSD","entry":1,"position_id":"999","ticket":"999","time":stamp,
                "strategy_id":11,"profit":999},
               {"symbol":"BTCUSDm","entry":1,"position_id":"321","ticket":"654","time":stamp,"profit":999}]
        live=live_learning_profile(deals,symbol="BTCUSDm")
        self.assertEqual(live["processed_positions"],1)
        result=effective_weights({},live,"تذبذب جانبي")
        self.assertLess(result["strategy_weights"]["11"],1)
        self.assertLess(result["combination_weights"]["12"],1)
        self.assertEqual(result["diagnostics"]["live_strategies"],1)
        self.assertEqual(result["diagnostics"]["historical_samples"],0)
        self.assertFalse(result["diagnostics"]["risk_settings_changed"])
    def test_real_engine_output_uses_weights(self):
        rates=bars(100)
        # Actual production evaluator; no broker action.
        profile=historical_profile(rates)
        self.assertEqual(profile["status"],"READY")
        out=effective_weights(profile,{},"اتجاه صاعد")
        self.assertTrue(out["strategy_weights"])
        decision=evaluate_market(rates,weights=out["strategy_weights"],
            combination_weights=out["combination_weights"],market_symbol="BTCUSDm")
        for vote in decision["votes"]:
            self.assertAlmostEqual(vote["weight"],out["strategy_weights"].get(str(vote["id"]),1))
    def test_missing_spread_does_not_create_evidence(self):
        data=bars()
        for c in data:c["spread_points"]=0
        self.assertEqual(historical_profile(data,evaluator)["samples"],0)

if __name__=="__main__":unittest.main()
