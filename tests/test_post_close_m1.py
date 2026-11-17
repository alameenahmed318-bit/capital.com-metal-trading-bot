"""Exact after-close M1 reentry boundary tests; offline, no orders."""
import sys
import unittest
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from post_close_confirmation import post_close_m1_confirmation,last_exit_from_deals

# 12:00:00 UTC; last close at 12:00:25 => 12:01 candle,
# first eligible after that candle closes at 12:02.
M0=1794830400

class PostCloseM1Tests(unittest.TestCase):
    def check(self,close,m1,bar,now):
        return post_close_m1_confirmation(close,m1,bar,now)

    def test_exit_within_m1_waits_for_full_next_candle(self):
        r=self.check(M0+25,M0,M0,M0+61)
        self.assertFalse(r["approved"])
        self.assertEqual(r["state"],"WAIT_M1")
        self.assertEqual(r["eligible_at"],M0+120)

    def test_nearly_closed_previous_candle_is_not_enough(self):
        r=self.check(M0+59,M0,M0,M0+119)
        self.assertFalse(r["approved"])
        self.assertEqual(r["confirmation_candle_open"],M0+60)

    def test_full_new_candle_plus_recalculated_strategy_allows_entry(self):
        r=self.check(M0+25,M0+60,M0+60,M0+120)
        self.assertTrue(r["approved"],r)
        self.assertEqual(r["state"],"CONFIRMED")

    def test_timing_without_actual_m1_candle_is_not_a_confirmation(self):
        r=self.check(M0+25,M0,M0+60,M0+200)
        self.assertFalse(r["approved"])
        self.assertEqual(r["state"],"WAIT_M1")

    def test_stale_strategy_after_m1_close_must_reanalyze(self):
        r=self.check(M0+25,M0+60,M0,M0+120)
        self.assertEqual(r["state"],"WAIT_REANALYSIS")
        self.assertFalse(r["approved"])

    def test_no_exit_preserves_immediate_normal_strategy_entry(self):
        r=self.check(0,M0,M0,M0+60)
        self.assertTrue(r["approved"])
        self.assertEqual(r["state"],"READY")

    def test_exact_minute_exit_allows_next_full_m1(self):
        r=self.check(M0+60,M0+60,M0+60,M0+120)
        self.assertTrue(r["approved"])
        self.assertEqual(r["eligible_at"],M0+120)

    def test_one_candle_once_not_every_entry(self):
        r=self.check(M0+25,M0+120,M0+120,M0+180)
        self.assertTrue(r["approved"])

    def test_second_exit_resets_only_its_own_confirmation(self):
        first=self.check(M0+25,M0+60,M0+60,M0+120)
        second=self.check(M0+126,M0+60,M0+60,M0+140)
        self.assertTrue(first["approved"])
        self.assertFalse(second["approved"])
        self.assertEqual(second["eligible_at"],M0+240)

    def test_future_exit_clock_skew_must_not_be_accepted(self):
        r=self.check(M0+100,M0,M0,M0+60)
        self.assertFalse(r["approved"])
        self.assertEqual(r["state"],"WAIT_CLOCK")

    def test_latest_exit_ignores_entry_deals_and_old_timestamps(self):
        records=[{"entry":0,"time":M0+56},{"entry":1,"time":M0+20},
                 {"entry":3,"time":M0+26},{"entry":2,"time":M0+33}]
        self.assertEqual(last_exit_from_deals(records,M0+40),M0+33)

    def test_history_fallback_rejects_future_close(self):
        records=[{"entry":1,"time":M0+99},{"entry":1,"time":M0+1}]
        self.assertEqual(last_exit_from_deals(records,M0+10),M0+1)

    def test_datetime_timestamp_supported(self):
        d=datetime.fromtimestamp(M0+120,timezone.utc)
        r=self.check(M0+25,M0+60,M0+60,d)
        self.assertTrue(r["approved"])

    def test_mt5_persists_trade_closure_and_enforces_local_reentry_check(self):
        ea=(Path(__file__).resolve().parents[1]/"mt5_client"/
            "ALZOOL_Central_Client.mq5").read_text(encoding="utf-8")
        for marker in ("OnTradeTransaction(","TRADE_TRANSACTION_DEAL_ADD",
                       "DEAL_ENTRY_OUT_BY","GlobalVariableSet(g_lastClosedDealKey",
                       "FreshM1ConfirmedAfterClose(decisionBar)",
                       "iTime(_Symbol,PERIOD_M1,1)",
                       '"last_closed_deal_time','"last_m1_closed_open'):
            # Server sends JSON keys; EA has quote-escaped sources.
            if marker.startswith('"'):
                self.assertIn(marker[1:],ea)
            else:self.assertIn(marker,ea)

    def test_server_and_monitor_both_use_same_post_close_check(self):
        app=(Path(__file__).resolve().parents[1]/"marketplace"/"backend"/
             "app.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(app.count("post_close_m1_confirmation("),2)
        self.assertIn('"post_close_m1_confirmation":confirmation',app)
        self.assertIn('confirmation["approved"]',app)


if __name__=="__main__":
    unittest.main()
