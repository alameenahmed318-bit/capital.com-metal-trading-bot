import unittest

from management import build_management_command


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
        decision = {"decision": "BUY", "confidence": 0.8}
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


if __name__ == "__main__":
    unittest.main()
