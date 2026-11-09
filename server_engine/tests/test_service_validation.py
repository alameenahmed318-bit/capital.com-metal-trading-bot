import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service import validate_market, signature_message


def valid_payload():
    rows = []
    for i in range(70):
        close = 2300.0 + i * 0.1
        rows.append({
            "time": 1700000000 + i * 60,
            "open": close - 0.05,
            "high": close + 0.10,
            "low": close - 0.10,
            "close": close,
            "tick_volume": 100,
        })
    return {"symbol": "XAUUSD", "bid": 2306.8, "ask": 2307.0, "candles": rows}


class MarketValidationTests(unittest.TestCase):
    def test_valid_market_payload_is_accepted(self):
        self.assertEqual(len(validate_market(valid_payload())), 70)

    def test_nan_price_is_rejected(self):
        payload = valid_payload()
        payload["candles"][20]["close"] = math.nan
        with self.assertRaises(ValueError):
            validate_market(payload)

    def test_infinite_quote_is_rejected(self):
        payload = valid_payload()
        payload["ask"] = math.inf
        with self.assertRaises(ValueError):
            validate_market(payload)

    def test_out_of_order_candles_are_rejected(self):
        payload = valid_payload()
        payload["candles"][30]["time"] = payload["candles"][29]["time"]
        with self.assertRaises(ValueError):
            validate_market(payload)

    def test_inverted_quote_is_rejected(self):
        payload = valid_payload()
        payload["bid"], payload["ask"] = 2308.0, 2307.0
        with self.assertRaises(ValueError):
            validate_market(payload)



class DecisionSignatureTests(unittest.TestCase):
    def test_signature_is_deterministic_for_same_client_token(self):
        message = "client-a|decision-1|BUY|17|0.75000000|1900000000.000000"
        first = signature_message(message, "client-token-secret")
        second = signature_message(message, "client-token-secret")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_signature_changes_when_message_or_client_token_changes(self):
        message = "client-a|decision-1|BUY|17|0.75000000|1900000000.000000"
        signature = signature_message(message, "client-token-secret")
        self.assertNotEqual(signature, signature_message(message + "|tampered", "client-token-secret"))
        self.assertNotEqual(signature, signature_message(message, "different-client-token"))


if __name__ == "__main__":
    unittest.main()
