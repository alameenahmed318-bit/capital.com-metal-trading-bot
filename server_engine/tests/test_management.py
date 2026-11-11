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


if __name__ == "__main__":
    unittest.main()
