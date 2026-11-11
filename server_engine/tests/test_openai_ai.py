import json
import os
import unittest
from unittest.mock import patch

from openai_ai import analyze_market, OpenAIAnalysisError


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def valid_output():
    return {
        "market_regime": "TREND",
        "bias": "BUY",
        "confidence": 0.82,
        "summary_ar": "اتجاه صاعد محتمل وفق البيانات المتاحة.",
        "reasons_ar": ["توافق الاتجاه مع الشموع المغلقة."],
        "risks_ar": ["قد يتغير الاتجاه مع شمعة جديدة."],
    }


class OpenAIResponsesAdapterTests(unittest.TestCase):
    def test_uses_official_responses_endpoint_and_strict_json_schema(self):
        captured = {}

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = request.headers
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse({
                "status": "completed",
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": json.dumps(valid_output(), ensure_ascii=False)}
                ]}],
            })

        result = analyze_market("secret-key", "gpt-6-astra",
                                {"symbol": "XAUUSD", "closed_candle_time": 123},
                                opener=fake_open)
        self.assertEqual(captured["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret-key")
        self.assertEqual(captured["body"]["model"], "gpt-6-astra")
        self.assertEqual(captured["body"]["text"]["format"]["type"], "json_schema")
        self.assertTrue(captured["body"]["text"]["format"]["strict"])
        self.assertEqual(result["bias"], "BUY")
        self.assertAlmostEqual(result["confidence"], 0.82)

    def test_missing_api_key_never_calls_network(self):
        def should_not_open(*args, **kwargs):
            raise AssertionError("network must not be called")

        with self.assertRaises(OpenAIAnalysisError) as raised:
            analyze_market("", "gpt-6-astra", {}, opener=should_not_open)
        self.assertEqual(raised.exception.code, "api_key_not_configured")

    def test_malformed_model_output_is_rejected(self):
        bad = valid_output()
        bad["bias"] = "BUY_NOW"
        with self.assertRaises(OpenAIAnalysisError) as raised:
            analyze_market("secret-key", "gpt-6-astra", {},
                           opener=lambda *args, **kwargs: FakeResponse({
                               "output_text": json.dumps(bad)
                           }))
        self.assertEqual(raised.exception.code, "invalid_bias")

    def test_refusal_is_reported_without_fabricating_analysis(self):
        with self.assertRaises(OpenAIAnalysisError) as raised:
            analyze_market("secret-key", "gpt-6-astra", {},
                           opener=lambda *args, **kwargs: FakeResponse({
                               "output": [{"type": "message", "content": [
                                   {"type": "refusal", "refusal": "refused"}
                               ]}]
                           }))
        self.assertEqual(raised.exception.code, "model_refusal")


if __name__ == "__main__":
    unittest.main()
