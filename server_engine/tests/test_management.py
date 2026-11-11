import unittest

from management import build_management_command
from supabase_gateway import EngineGateway


def snapshot(positions, bid=2050.0, ask=2050.2, point=0.01):
    return {"payload": {
        "symbol": "XAUUSD",
        "point": point,
        "tick": {"bid": bid, "ask": ask},
        "positions": positions,
    }}


def position(ticket="1001", *, side=0, volume=0.01, opened=2040.0,
             current=2050.0, sl=0.0, tp=0.0, profit=1.0, swap=0.0,
             magic="26110801", symbol="XAUUSD"):
    return {"ticket": ticket, "type": side, "volume": volume,
            "open_price": opened, "price_current": current, "sl": sl,
            "tp": tp, "profit": profit, "swap": swap,
            "magic": magic, "symbol": symbol}


class CentralManagementPlannerTests(unittest.TestCase):
    def test_gateway_accepts_broker_gold_symbol_suffix(self):
        candles = [{"time": 1000 + i, "open": 2049, "high": 2051,
                    "low": 2048, "close": 2050} for i in range(65)]
        normalized, bid, ask = EngineGateway._validate_market({
            "symbol": "XAUUSDm", "bid": 2050.0, "ask": 2050.2,
            "candles": candles})
        self.assertEqual(len(normalized), 65)
        self.assertEqual(bid, 2050.0)
        self.assertEqual(ask, 2050.2)

    def test_disabled_master_switch_does_not_touch_open_positions(self):
        result = build_management_command(
            {"_trading_enabled": False, "global_loss_enabled": True,
             "global_loss_amount": 1},
            snapshot([position(profit=-50)]), now=1000)
        self.assertEqual(result["action"], "WAIT")

    def test_global_loss_returns_close_all(self):
        result = build_management_command(
            {"_trading_enabled": True, "global_loss_enabled": True,
             "global_loss_amount": 10},
            snapshot([position(profit=-6), position("1002", profit=-5)]),
            now=1000)
        self.assertEqual(result["action"], "CLOSE_ALL")
        self.assertTrue(result["command_id"])

    def test_single_trade_profit_returns_close_all(self):
        result = build_management_command(
            {"_trading_enabled": True, "per_trade_profit_enabled": True,
             "per_trade_profit_target": 2},
            snapshot([position(profit=2.1)]), now=1000)
        self.assertEqual(result["action"], "CLOSE_ALL")

    def test_trailing_stop_is_calculated_centrally(self):
        result = build_management_command(
            {"_trading_enabled": True, "global_loss_enabled": False,
             "per_trade_profit_enabled": False, "global_profit_enabled": False,
             "trailing_enabled": True, "trail_start": 3, "trail_distance": 2},
            snapshot([position(opened=2040, sl=2045)], bid=2050, ask=2050.2),
            now=1000)
        self.assertEqual(result["action"], "MODIFY_SL")
        self.assertEqual(result["ticket"], "1001")
        self.assertAlmostEqual(result["sl"], 2048.0)

    def test_foreign_magic_and_symbol_are_never_managed(self):
        result = build_management_command(
            {"_trading_enabled": True, "global_loss_enabled": True,
             "global_loss_amount": 1},
            snapshot([position(profit=-100, magic="777"),
                      position("1002", profit=-100, symbol="EURUSD")]),
            now=1000)
        self.assertEqual(result["action"], "WAIT")

    def test_no_point_size_means_no_guessed_trailing(self):
        result = build_management_command(
            {"_trading_enabled": True, "global_loss_enabled": False,
             "per_trade_profit_enabled": False, "global_profit_enabled": False,
             "trailing_enabled": True, "trail_start": 3, "trail_distance": 2},
            snapshot([position(opened=2040, sl=2045)], point=0),
            now=1000)
        self.assertEqual(result["action"], "WAIT")



    def test_addon_is_planned_centrally_from_dashboard_settings(self):
        live = snapshot([position(opened=2040, current=2040, profit=1.0)])
        live["payload"].update({"sequence": 12, "volume_step": 0.01,
                                "volume_min": 0.01, "volume_max": 100})
        result = build_management_command(
            {"_trading_enabled": True, "global_loss_enabled": False,
             "per_trade_profit_enabled": False, "global_profit_enabled": False,
             "trailing_enabled": False, "addon_enabled": True, "addon_step": 2,
             "addon_multiplier_enabled": True, "addon_multiplier": 1.2,
             "addon_max": 3, "addon_max_lot": 0.05, "lot": 0.01},
            live, now=1000)
        self.assertEqual(result["action"], "OPEN_MARKET")
        self.assertEqual(result["direction"], "BUY")
        self.assertEqual(result["comment"], "AZS_ADDON")
        self.assertAlmostEqual(result["lots"], 0.02)

    def test_cooling_is_planned_centrally_in_basket_direction(self):
        live = snapshot([position(opened=2050, current=2050, profit=-4.0)], bid=2045, ask=2045.2)
        live["payload"].update({"sequence": 13, "volume_step": 0.01,
                                "volume_min": 0.01, "volume_max": 100})
        result = build_management_command(
            {"_trading_enabled": True, "global_loss_enabled": False,
             "per_trade_profit_enabled": False, "global_profit_enabled": False,
             "trailing_enabled": False, "cooling_enabled": True, "cooling_step": 3,
             "cooling_multiplier_enabled": False, "cooling_max": 2,
             "cooling_max_lot": 0.05, "lot": 0.01},
            live, now=1000)
        self.assertEqual(result["action"], "OPEN_MARKET")
        self.assertEqual(result["direction"], "BUY")
        self.assertEqual(result["comment"], "AZS_COOLING")
        self.assertAlmostEqual(result["lots"], 0.01)

    def test_recovery_uses_total_basket_lots_and_central_market_direction(self):
        positions = [position("1", opened=2050, profit=-7),
                     position("2", opened=2051, profit=-7),
                     position("3", opened=2052, profit=-7)]
        live = snapshot(positions)
        live["payload"].update({"sequence": 14, "volume_step": 0.01,
                                "volume_min": 0.01, "volume_max": 100,
                                "forming_candle": {"open": 2049, "close": 2050}})
        candles = [{"time": 1000 + i, "open": 2049, "high": 2050, "low": 2048, "close": 2049.5} for i in range(15)]
        decision = {"decision": "BUY", "confidence": 0.8, "candles": candles}
        result = build_management_command(
            {"_trading_enabled": True, "global_loss_enabled": False,
             "per_trade_profit_enabled": False, "global_profit_enabled": False,
             "trailing_enabled": False, "recovery_enabled": True,
             "recovery_min_positions": 3, "recovery_trigger_loss": 10,
             "recovery_target_profit": 0, "recovery_mode": "AUTO",
             "recovery_min_confidence": 0.65},
            live, now=1000, market_decision=decision)
        self.assertEqual(result["action"], "OPEN_MARKET")
        self.assertEqual(result["direction"], "BUY")
        self.assertEqual(result["comment"], "AZR_RECOVERY")
        self.assertAlmostEqual(result["lots"], 0.04)

    def test_management_commands_are_stable_for_same_snapshot(self):
        live = snapshot([position(opened=2040, current=2040)])
        live["payload"].update({"sequence": 99, "volume_step": 0.01,
                                "volume_min": 0.01, "volume_max": 100})
        settings = {"_trading_enabled": True, "global_loss_enabled": False,
                    "per_trade_profit_enabled": False, "global_profit_enabled": False,
                    "trailing_enabled": False, "addon_enabled": True, "addon_step": 2,
                    "addon_multiplier_enabled": False, "addon_max": 3,
                    "addon_max_lot": 0.05, "lot": 0.01}
        first = build_management_command(settings, live, now=1000)
        second = build_management_command(settings, live, now=1001)
        self.assertEqual(first["command_id"], second["command_id"])



    def test_primary_buy_and_sell_entries_are_sized_by_central_settings(self):
        live = snapshot([])
        live["payload"].update({"sequence": 20, "volume_step": 0.01,
                                "volume_min": 0.01, "volume_max": 100})
        decision = {"decision": "BUY", "decision_id": "d1", "strategy_id": 17,
                    "reason_ar": "إشارة شراء", "candles": [{"time": 123}]}
        result = build_management_command(
            {"_trading_enabled": True, "allow_buy": True, "allow_sell": True,
             "one_direction_only": True, "lot": 0.01, "fixed_sl_enabled": True,
             "fixed_sl_distance": 3, "global_loss_enabled": False},
            live, now=1000, market_decision=decision)
        self.assertEqual(result["action"], "OPEN_MARKET")
        self.assertEqual(result["direction"], "BUY")
        self.assertAlmostEqual(result["lots"], 0.01)
        self.assertAlmostEqual(result["sl"], 2047.2)

    def test_primary_pending_entry_uses_broker_stop_distance(self):
        live = snapshot([])
        live["payload"].update({"sequence": 21, "volume_step": 0.01,
                                "volume_min": 0.01, "volume_max": 100,
                                "stops_level": 300, "point": 0.01})
        decision = {"decision": "SELL", "decision_id": "d2", "strategy_id": 18,
                    "candles": [{"time": 124}]}
        result = build_management_command(
            {"_trading_enabled": True, "allow_buy": True, "allow_sell": True,
             "one_direction_only": True, "lot": 0.01, "order_mode": "Pending Order",
             "pending_distance": 1, "fixed_sl_enabled": True, "fixed_sl_distance": 2,
             "global_loss_enabled": False},
            live, now=1000, market_decision=decision)
        self.assertEqual(result["action"], "PLACE_PENDING")
        self.assertEqual(result["direction"], "SELL")
        self.assertAlmostEqual(result["price"], 2047.0)
        self.assertAlmostEqual(result["sl"], 2050.0)

    def test_daily_loss_stops_new_entries_and_closes_open_basket(self):
        live = snapshot([])
        live["payload"].update({"day_start": 100, "history": [
            {"time": 101, "net_profit": -55.0}]})
        decision = {"decision": "BUY", "candles": [{"time": 123}]}
        result = build_management_command(
            {"_trading_enabled": True, "loss_limit": 50, "global_loss_enabled": False},
            live, now=1000, market_decision=decision)
        self.assertEqual(result["action"], "WAIT")
        live["payload"]["positions"] = [position(profit=-1)]
        result = build_management_command(
            {"_trading_enabled": True, "loss_limit": 50, "global_loss_enabled": False},
            live, now=1000, market_decision=decision)
        self.assertEqual(result["action"], "CLOSE_ALL")

    def test_ai_loss_guard_closes_only_losing_opposite_position(self):
        live = snapshot([position(profit=-3), position("1002", side=1, profit=2)])
        result = build_management_command(
            {"_trading_enabled": True, "global_loss_enabled": False,
             "per_trade_profit_enabled": False, "global_profit_enabled": False,
             "trailing_enabled": False, "ai_loss_guard_enabled": True},
            live, now=1000, market_decision={"decision": "SELL", "confidence": 0.8})
        self.assertEqual(result["action"], "CLOSE_POSITION")
        self.assertEqual(result["ticket"], "1001")

    def test_reversal_protection_closes_only_after_trigger_plus_continuation(self):
        live = snapshot([position(opened=2050, current=2042, profit=-8)], bid=2042, ask=2042.2)
        result = build_management_command(
            {"_trading_enabled": True, "global_loss_enabled": False,
             "per_trade_profit_enabled": False, "global_profit_enabled": False,
             "trailing_enabled": False, "reverse_protection_enabled": True,
             "reverse_trigger": 5, "reverse_continue": 2},
            live, now=1000, market_decision={"decision": "SELL", "confidence": 0.8})
        self.assertEqual(result["action"], "CLOSE_ALL")

    def test_candle_chase_creates_both_sides_as_pending_orders(self):
        live = snapshot([])
        live["payload"].update({"sequence": 30, "volume_step": 0.01,
                                "volume_min": 0.01, "volume_max": 100,
                                "forming_candle": {"time": 500, "open": 2050,
                                                   "high": 2052, "low": 2048, "close": 2051}})
        settings = {"_trading_enabled": True, "candle_chase_enabled": True,
                    "candle_chase_max_orders": 2, "candle_chase_min_move": 0.1,
                    "pending_distance": 1, "lot": 0.01,
                    "global_loss_enabled": False}
        first = build_management_command(settings, live, now=1000)
        self.assertEqual(first["action"], "PLACE_PENDING")
        self.assertEqual(first["direction"], "BUY")
        live["payload"]["orders"] = [{"ticket": "9001", "symbol": "XAUUSD",
            "type": 4, "magic": "26110801", "comment": "AZS_CANDLE_BUY",
            "time_setup": 500}]
        second = build_management_command(settings, live, now=1001)
        self.assertEqual(second["action"], "PLACE_PENDING")
        self.assertEqual(second["direction"], "SELL")

    def test_stale_candle_pending_order_is_cancelled_centrally(self):
        live = snapshot([])
        live["payload"]["forming_candle"] = {"time": 700, "open": 2050,
            "high": 2051, "low": 2049, "close": 2050.5}
        live["payload"]["orders"] = [{"ticket": "9002", "symbol": "XAUUSD",
            "type": 4, "magic": "26110801", "comment": "AZS_CANDLE_BUY",
            "time_setup": 600}]
        result = build_management_command(
            {"_trading_enabled": True, "global_loss_enabled": False},
            live, now=1000)
        self.assertEqual(result["action"], "CANCEL_PENDING")
        self.assertEqual(result["ticket"], "9002")


if __name__ == "__main__":
    unittest.main()
