"""Unit tests for local Qwen vote validation; no Ollama/network required."""
import json
import unittest

from ai_relay import normalize_vote


class QwenVoteNormalizationTests(unittest.TestCase):
    def test_matching_buy_vote_is_accepted(self):
        vote = normalize_vote(json.dumps({
            "direction": "BUY", "confidence": 0.82, "reason_ar": "اتجاه متوافق"
        }), "BUY")
        self.assertEqual(vote["direction"], "BUY")
        self.assertEqual(vote["confidence"], 0.82)

    def test_matching_sell_vote_is_accepted(self):
        vote = normalize_vote('{"direction":"SELL","confidence":0.7}', "SELL")
        self.assertEqual(vote["direction"], "SELL")

    def test_opposite_direction_is_reduced_to_wait(self):
        vote = normalize_vote('{"direction":"BUY","confidence":0.99}', "SELL")
        self.assertEqual(vote["direction"], "WAIT")

    def test_wait_is_preserved(self):
        vote = normalize_vote('{"direction":"WAIT","confidence":0.4}', "BUY")
        self.assertEqual(vote["direction"], "WAIT")

    def test_no_central_signal_never_allows_entry_vote(self):
        vote = normalize_vote('{"direction":"BUY","confidence":1}', "WAIT")
        self.assertEqual(vote["direction"], "WAIT")

    def test_invalid_direction_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_vote('{"direction":"HOLD","confidence":0.8}', "BUY")

    def test_out_of_range_confidence_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_vote('{"direction":"BUY","confidence":1.2}', "BUY")

    def test_malformed_json_is_rejected(self):
        with self.assertRaises(json.JSONDecodeError):
            normalize_vote("not-json", "BUY")

    def test_non_object_json_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_vote('["BUY",0.8]', "BUY")


if __name__ == "__main__":
    unittest.main()
