"""Offline safety tests for optional Groq: no external requests or real trades."""
import json
import pathlib
import sys
import time
import unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"marketplace"/"backend"))
import groq_advisor as g
from multi_model_council import _VOTES, _vote, council_result, _groq_worker

def sample(now=None):
    ts = int(time.time() if now is None else now)
    return {
        "decision": "BUY", "confidence": .78, "bar_time": ts-60,
        "entry_reference_price": 2620, "strategy_id": 12,
        "context": {"atr": 1.4}, "family_votes": {"trend": "BUY"},
        "combination_summary": {"BUY": 320, "SELL": 80, "WAIT": 600},
        "advisory_bots": {"status":"READY","bar_time":ts-60,
                          "advisors":[{"id":"adaptive","signal":"BUY"}]}
    }

class GroqAdvisoryTests(unittest.TestCase):
    def setUp(self):
        _VOTES.clear()
        with g._LOCK:g._QUOTA.update({"day": None,"count": 0,"last_call": 0.0})

    def test_only_fresh_strong_closed_bar_candidates(self):
        d=sample()
        self.assertTrue(g.is_candidate(d))
        self.assertFalse(g.is_candidate({**d,"decision":"WAIT"}))
        self.assertFalse(g.is_candidate({**d,"confidence":.55}))
        self.assertFalse(g.is_candidate({**d,"bar_time":int(time.time())-1000}))
        self.assertFalse(g.is_candidate({**d,"bar_time":int(time.time())+300}))
        self.assertFalse(g.is_candidate({**d,"context":{"atr":0}}))

    def test_snapshot_minimizes_personal_info(self):
        d=sample()
        d.update({"client_id":"secret-id","token":"never-send","account":{"balance":12345},
                  "positions":[{"ticket":44}]})
        compact=g._compact(d)
        raw=json.dumps(compact)
        self.assertEqual(compact["signal"],"BUY")
        self.assertEqual(compact["bot_signals"][0]["signal"],"BUY")
        for text in ("secret-id","never-send","balance","positions","ticket"):
            self.assertNotIn(text,raw)

    def test_valid_and_malformed_result(self):
        good={"choices":[{"message":{"content":json.dumps({
            "direction":"BUY","confidence":.81,"reason_ar":"اتجاه متوافق"
        })}}]}
        vote=g._parse_response(good)
        self.assertEqual(vote["direction"],"BUY")
        self.assertFalse(vote["can_execute"])
        self.assertEqual(vote["role"],"advisory_only")
        for response in ({}, {"choices":[]}, {"choices":[{"message":{"content":"not-json"}}]},
                         {"choices":[{"message":{"content":json.dumps({
                             "direction":"BUY","confidence":2})}}]}):
            self.assertEqual(g._parse_response(response)["status"],"INVALID")

    def test_free_tier_call_limits(self):
        ts=time.time()
        a,b=g._reserve(ts,10000)
        self.assertTrue(a)
        a,b=g._reserve(ts+1,10001)
        self.assertFalse(a)
        self.assertEqual(b,"COOLDOWN")
        a,b=g._reserve(ts+181,10181)
        self.assertTrue(a)
        with g._LOCK:g._QUOTA["count"]=g._DAILY_CAP
        self.assertEqual(g._reserve(ts+400,10400),(False,"DAILY_LIMIT"))

    def test_no_key_means_no_request(self):
        with patch.object(g,"KEY",""),patch.object(g,"_call_groq") as call:
            d=g.review(sample())
        self.assertEqual(d["status"],"NOT_CONFIGURED")
        self.assertFalse(d["can_execute"])
        call.assert_not_called()

    def test_groq_review_does_not_authorize_council(self):
        d=sample()
        _VOTES["test"]={
            "bar_time":d["bar_time"],
            "fast":_vote("Fast AI","READY","BUY",.9),
            "qwen":_vote("Qwen","READY","BUY",.8),
            "deepseek":_vote("DeepSeek","READY","SELL",.8)}
        response={"choices":[{"message":{"content":json.dumps({
            "direction":"BUY","confidence":1.0,"reason_ar":"موافق"})}}]}
        with patch.object(g,"KEY","test-key"),patch.object(g,"_call_groq",return_value=response):
            _groq_worker("test",d["bar_time"],d)
        self.assertEqual(council_result("test",d)["groq_advisory"]["direction"],"BUY")
        self.assertFalse(council_result("test",d)["approved"])
        _VOTES["test"]["deepseek"]=_vote("DeepSeek","READY","BUY",.8)
        self.assertTrue(council_result("test",d)["approved"])

    def test_smoke_inference_is_not_an_order(self):
        response={"choices":[{"message":{"content":json.dumps({
            "direction":"WAIT","confidence":0.1,"reason_ar":"اختبار"})}}]}
        with patch.object(g,"KEY","test-key"),patch.object(g,"_call_groq",return_value=response) as called:
            self.assertEqual(g.verify_credentials(),"READY")
            snapshot=called.call_args.args[0]
            self.assertEqual(snapshot["bar_time"],0)
            self.assertNotIn("token",snapshot)

if __name__=="__main__":
    unittest.main()
