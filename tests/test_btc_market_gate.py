"""BTCUSD/Deriv entry and advisory gates: pure unit tests, no broker orders."""
import pathlib
import sys
import unittest
from datetime import datetime, timezone
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "marketplace" / "backend"))

from decision_guard import evaluate_entry_gate
from bot_signal_bus import build_bot_messages, BOT_IDS
from app import live_learning_profile, terminal_request_matches_market

class BTCMarketGateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.bar = int(self.now.timestamp()) - 60
        self.decision = {"decision":"BUY","confidence":.65,"strategy_id":7,
                         "bar_time":self.bar,"entry_reference_price":90000.0,
                         "entry_atr":250.0}
        self.account = {"balance":1000,"equity":999,"free_margin":900}

    def test_btc_entry_gate_accepts_valid_market(self):
        result=evaluate_entry_gate(self.decision,self.account,"BTCUSD",
            self.now,self.now,True,strategy_first=True)
        self.assertTrue(result["approved"], result["reason_ar"])

    def test_unlisted_pair_is_rejected(self):
        result=evaluate_entry_gate(self.decision,self.account,"BTCUSDT",
            self.now,self.now,True,strategy_first=True)
        self.assertFalse(result["approved"])
        self.assertEqual(result["checks"][-1]["check"],"symbol")

    def test_btc_advisor_event_ids_are_symbol_scoped(self):
        report={"status":"READY","bar_time":self.bar,"advisors":[
            {"id":bot,"signal":"WAIT","confidence":0,"evidence":{}} for bot in BOT_IDS]}
        btc=build_bot_messages("engine1","BTCUSD","M1",report)
        gold=build_bot_messages("engine1","XAUUSD","M1",report)
        self.assertEqual(len(btc),5)
        self.assertEqual(len(gold),5)
        self.assertNotEqual(btc[0]["event_id"],gold[0]["event_id"])

    def test_btc_requires_explicit_symbol_on_terminal_poll(self):
        self.assertFalse(terminal_request_matches_market(
            "/v1/terminal-decision/engine-1", "BTCUSD"))
        self.assertTrue(terminal_request_matches_market(
            "/v1/terminal-decision/engine-1?symbol=BTCUSD", "BTCUSD"))
        self.assertFalse(terminal_request_matches_market(
            "/v1/terminal-decision/engine-1?symbol=XAUUSD", "BTCUSD"))
        self.assertFalse(terminal_request_matches_market(
            "/v1/terminal-decision/engine-1?symbol=BTCUSDm", "BTCUSD"))

    def test_legacy_gold_poll_remains_compatible(self):
        self.assertTrue(terminal_request_matches_market(
            "/v1/terminal-decision/engine-1", "XAUUSD"))
        self.assertFalse(terminal_request_matches_market(
            "/v1/terminal-decision/engine-1?symbol=BTCUSD", "XAUUSD"))

    def test_learning_keeps_gold_and_btc_separate(self):
        now = int(self.now.timestamp())
        btc=[{"symbol":"BTCUSD","position_id":"btc","entry":1,"time":now,
              "profit":15,"strategy_id":7}]
        gold=[{"symbol":"XAUUSD","position_id":"gold","entry":1,"time":now,
               "profit":-30,"strategy_id":7}]
        btc_only=live_learning_profile(btc+gold,symbol="BTCUSD")
        gold_only=live_learning_profile(btc+gold,symbol="XAUUSD")
        self.assertEqual(btc_only["strategies"][0]["net"],15.0)
        self.assertEqual(gold_only["strategies"][0]["net"],-30.0)

if __name__=="__main__":
    unittest.main()
