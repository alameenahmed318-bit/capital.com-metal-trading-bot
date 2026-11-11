import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server_engine.supabase_gateway import EngineGateway


class CaptureExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return None


class LiveTelemetryAITriggerTests(unittest.TestCase):
    def test_live_telemetry_queues_openai_with_closed_candles(self):
        gateway = EngineGateway("https://example.supabase.co", "service-key")
        executor = CaptureExecutor()
        gateway._ai_executor = executor
        gateway._client = lambda client_id, token: {"trading_enabled": False}
        gateway._request = lambda method, path, payload=None, prefer=None: []

        candles = []
        for index in range(1, 66):
            base = 2300.0 + index * 0.1
            candles.append({
                "time": index,
                "open": base,
                "high": base + 0.5,
                "low": base - 0.5,
                "close": base + 0.1,
                "tick_volume": 100,
            })

        payload = {
            "sequence": 1,
            "source_time": 1700000000,
            "symbol": "XAUUSD",
            "timeframe": "PERIOD_M1",
            "account": {"currency": "USD", "balance": 1000, "equity": 1000, "profit": 0},
            "positions": [],
            "orders": [],
            "tick": {"bid": 2306.0, "ask": 2306.2},
            "forming_candle": {"time": 66, "open": 2306.0, "high": 2306.2, "low": 2305.9, "close": 2306.1},
            "closed_candles": candles,
        }

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_AI_ENABLED": "true"}):
            result = gateway.terminal_state_live("client-1", "token", payload)

        self.assertTrue(result["accepted"])
        self.assertEqual(len(executor.calls), 1)
        args, _ = executor.calls[0]
        self.assertEqual(args[1], "client-1")
        self.assertEqual(args[4], "XAUUSD")
        self.assertEqual(args[5], "PERIOD_M1")
        self.assertEqual(len(args[-1]["closed_candles"]), 65)
        self.assertEqual(args[-1]["closed_candles"][0]["time"], 1)
        self.assertEqual(args[-1]["closed_candles"][-1]["time"], 65)

    def test_live_telemetry_without_65_closed_candles_does_not_queue(self):
        gateway = EngineGateway("https://example.supabase.co", "service-key")
        executor = CaptureExecutor()
        gateway._ai_executor = executor
        gateway._client = lambda client_id, token: {"trading_enabled": False}
        gateway._request = lambda method, path, payload=None, prefer=None: []
        payload = {
            "sequence": 1,
            "source_time": 1700000000,
            "symbol": "XAUUSD",
            "timeframe": "PERIOD_M1",
            "account": {},
            "positions": [],
            "orders": [],
            "tick": {"bid": 2300.0, "ask": 2300.2},
            "closed_candles": [],
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_AI_ENABLED": "true"}):
            gateway.terminal_state_live("client-1", "token", payload)
        self.assertEqual(executor.calls, [])


if __name__ == "__main__":
    unittest.main()
