"""BTC and legacy gold whitelist tests; no real orders are submitted."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "marketplace" / "backend"))
from market_symbols import market_family, supported_market_symbol, same_market_symbol


class MarketSymbolTests(unittest.TestCase):
    def test_deriv_btc_allowed(self):
        for symbol in ("BTCUSD", "BTCUSDm", "BTCUSD.r", "BTCUSD-m"):
            self.assertTrue(supported_market_symbol(symbol), symbol)
            self.assertEqual(market_family(symbol), "BTCUSD")

    def test_gold_legacy_allowed(self):
        for symbol in ("XAUUSD", "XAUUSDm", "XAUUSD.pro"):
            self.assertTrue(supported_market_symbol(symbol), symbol)

    def test_unknown_pairs_rejected(self):
        for symbol in ("BTCUSDT", "ETHUSD", "EURUSD", "BTCUSDTR", "", "BTCUSD/USDT"):
            self.assertFalse(supported_market_symbol(symbol), symbol)

    def test_exact_symbol_for_trade_attribution(self):
        self.assertTrue(same_market_symbol("btcusd", "BTCUSD"))
        self.assertFalse(same_market_symbol("BTCUSDm", "BTCUSD"))
        self.assertFalse(same_market_symbol("XAUUSD", "BTCUSD"))


if __name__ == "__main__":
    unittest.main()
