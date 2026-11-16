"""Offline regression tests for AL-ZOOL's owner-only local AI relay."""
import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

RELAY_PATH = Path(__file__).resolve().parents[3] / "mt5_client" / "ALZOOL_Local_Model_Relay.py"
spec = importlib.util.spec_from_file_location("alzool_mt5_local_relay", RELAY_PATH)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class LocalModelRelayTests(unittest.TestCase):
    def test_parses_model_json_with_markdown_and_trailing_text(self):
        result = relay.validate_response("deepseek-r1:1.5b", {
            "response": 'analysis note\n```json\n{"direction":"BUY","confidence":0.85,"reason_ar":"اتجاه"}\n```\nDone'
        })
        self.assertEqual(result["direction"], "BUY")
        self.assertAlmostEqual(result["confidence"], .85)

    def test_invalid_or_truncated_vote_never_becomes_an_approval(self):
        for response in ('{"direction":"BUY","confidence":',
                         '{"direction":"BUY"}', '{"direction":"BUY","confidence":1.2}',
                         '{"direction":"BUY","confidence":-0.1}',
                         '{"direction":"MAYBE","confidence":0.9}',
                         '{"direction":"BUY","confidence":"NaN"}',
                         "BUY 86%"):
            with self.subTest(response=response):
                self.assertIsNone(relay.validate_response("qwen3.5:4b", {"response":response}))

    def test_low_confidence_is_preserved_not_upgraded(self):
        result = relay.validate_response("deepseek-r1:1.5b",
                                         {"response":'{"direction":"SELL","confidence":0}'})
        self.assertEqual(result["confidence"], 0.0)

    def test_snapshot_includes_actual_central_signal_and_recent_candles(self):
        snapshot = relay.compact_snapshot({
            "bar_time": 1700000000, "decision": "SELL", "confidence": .78,
            "context": {"regime": "TREND", "atr": 2.5, "sell_quality": 81},
            "candles": [{"time":1,"open":2000,"high":2005,"low":1995,"close":1997,
                         "other_private_field":"do-not-send"}]*12,
        })
        self.assertEqual(snapshot["central_signal"], "SELL")
        self.assertEqual(snapshot["market_regime"], "TREND")
        self.assertEqual(len(snapshot["recent_closed_candles"]), 6)
        self.assertNotIn("other_private_field", snapshot["recent_closed_candles"][0])

    def test_live_signal_guard_discards_changed_bar_or_signal(self):
        with mock.patch.object(relay, "request_json", return_value={
            "bar_time": 61, "decision": "BUY"
        }):
            self.assertTrue(relay.same_live_signal("https://example.org", "id", "token", 61, "BUY"))
            self.assertFalse(relay.same_live_signal("https://example.org", "id", "token", 60, "BUY"))
            self.assertFalse(relay.same_live_signal("https://example.org", "id", "token", 61, "SELL"))

    def test_model_prompt_uses_factual_signal_without_forcing_agreement(self):
        seen = {}
        def stub(url, **kwargs):
            seen.update(kwargs["payload"])
            return {"response": json.dumps({
                "direction":"WAIT","confidence":.65,"reason_ar":"لا تأكيد"
            })}
        with mock.patch.object(relay, "request_json", side_effect=stub):
            vote = relay.model_vote("qwen3.5:4b", {
                "bar_time": 61, "decision":"BUY", "confidence":.91,
                "context": {"regime": "RANGE"}, "candles":[]
            })
        self.assertEqual(vote["direction"], "WAIT")
        self.assertIn('"central_signal":"BUY"', seen["prompt"])
        self.assertIn("disagree or WAIT", seen["prompt"])
        self.assertEqual(seen["format"], "json")
        self.assertFalse(seen["think"])


if __name__ == "__main__":
    unittest.main()
