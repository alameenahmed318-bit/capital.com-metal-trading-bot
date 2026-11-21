"""BTC-only Meeno server: gold must never share account actions or learning."""
import inspect
import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
import app as app_module
from app import btc_market, btc_database_ready, _fetch_trade_settings, terminal_request_matches_market
from unittest.mock import patch
from strategy_engine import adaptive_bitcoin_parameters, adaptive_gold_parameters
from recovery_logic import recovery_intent
from position_action_engine import plan_account_actions

class MarketIsolationTests(unittest.TestCase):
    def test_btc_symbol_scope_and_gold_denial(self):
        for market in ("BTCUSD","BTCUSDm","BTCUSD.r"):
            self.assertTrue(btc_market(market))
        for market in ("XAUUSD","XAUUSDm","BTCUSDT","ETHUSD",None):
            self.assertFalse(btc_market(market))
        self.assertFalse(terminal_request_matches_market(
            "/v1/terminal-decision/owner?symbol=XAUUSD","XAUUSD"))

    def test_wrong_supabase_project_is_fail_closed(self):
        with patch.object(app_module,"SUPABASE_URL","https://mmevhqgbuqledajrmnav.supabase.co"):
            self.assertFalse(btc_database_ready())
        with patch.object(app_module,"SUPABASE_URL","https://egtofwardrkkzflniikp.supabase.co"):
            self.assertTrue(btc_database_ready())

    def test_market_has_own_settings_table(self):
        source=inspect.getsource(_fetch_trade_settings)
        self.assertIn("alzool_btc_trade_settings",source)
        self.assertNotIn("alzool_engine_trade_settings",source)

    def test_btc_tuning_does_not_use_gold_profile(self):
        context={"atr_ratio":1.9,"regime":"تقلب مرتفع"}
        btc=adaptive_bitcoin_parameters([],context)
        gold=adaptive_gold_parameters([],context)
        self.assertNotEqual((btc["ema_fast"],btc["ema_slow"],btc["rsi_period"]),
                            (gold["ema_fast"],gold["ema_slow"],gold["rsi_period"]))
        self.assertEqual(btc["market"],"BTCUSD")

    def test_mixed_gold_btc_account_cannot_trigger_x3_recovery(self):
        positions=[
            {"symbol":"BTCUSD","type":0,"volume":0.1,"profit":-40,"swap":0},
            {"symbol":"XAUUSD","type":0,"volume":0.4,"profit":-90,"swap":0}]
        snapshot={"symbol":"BTCUSD","positions":positions,"recovery_group_net":-130}
        settings={"position_management":{"recovery_enabled":True,"recovery_loss_trigger":10}}
        result=recovery_intent(snapshot,{"decision":"BUY"},settings)
        self.assertEqual(result["action"],"HOLD")
        self.assertFalse(result["armed"])
        self.assertEqual(result["lot_cap"],0)

    def test_mixed_account_blocks_all_position_actions(self):
        positions=[
            {"symbol":"BTCUSD","type":0,"volume":0.1,"profit":-40},
            {"symbol":"XAUUSD","type":0,"volume":0.4,"profit":-90}]
        snapshot={"symbol":"BTCUSD","positions":positions,"tick":{"bid":85000,"ask":85001}}
        r=plan_account_actions(snapshot,{"decision":"BUY","bar_time":100,
              "entry_atr":100},{},{"approved":True},
            {"trading_enabled":True,"position_management":{"recovery_enabled":True,"recovery_loss_trigger":10}})
        self.assertFalse(r["execution_authorized"])
        self.assertEqual(r["actions"],[])
        self.assertIn("سوق آخر",r["reason_ar"])

if __name__=="__main__":
    unittest.main()
