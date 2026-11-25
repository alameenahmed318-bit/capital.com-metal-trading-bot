import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"research"),str(ROOT/"marketplace"/"backend")]
import challenger_forecasts as c
import model_arena_worker as w
import model_arena_api as api

class ChallengerChecks(unittest.TestCase):
    def test_new_models_only_report_allowlist(self):
        self.assertTrue(set(c.LOCAL).issubset(api.REPORT_MODELS))
        self.assertTrue(set(c.CLOUD).issubset(api.REPORT_MODELS))
        self.assertFalse(set(c.LOCAL).intersection(api.MODELS))
        self.assertFalse(set(c.CLOUD).intersection(api.MODELS))
    def test_invalid_forecast_rejected(self):
        for x in ([float("nan")],[1.,2.]):
            with self.assertRaises(ValueError):c.finite_predictions(x,1)
    def test_cloud_mock_uses_only_context_and_explicit_model(self):
        calls=[]
        def fake(body):
            calls.append(body)
            return {"choices":[{"message":{"content":'{"forecast": 0.25}'}}]}
        values,model=c.cloud_forecast("Qwen 3.8 27B",[[1.,0.]],"test",request=fake)
        self.assertEqual(values,[.25])
        self.assertEqual(model,"qwen/qwen3.8-27b")
        self.assertEqual(calls[0]["messages"][1]["content"],"[1.0, 0.0]")
    def test_official_sdk_transport(self):
        sdk=Mock()
        client=sdk.Groq.return_value
        client.chat.completions.create.return_value.model_dump.return_value={"choices":[{"message":{"content":'{"forecast": 0.25}'}}]}
        with patch.dict(sys.modules,{"groq":sdk}):
            values,model=c.cloud_forecast("GPT-OSS 120B (Arena)",[[0.]],"test")
        self.assertEqual(values,[.25])
        sdk.Groq.assert_called_once_with(api_key="test",max_retries=0,timeout=30.0)
        self.assertEqual(client.chat.completions.create.call_args.kwargs["model"],"openai/gpt-oss-120b")
    def test_strict_cloud_response(self):
        def fake(body):
            self.assertTrue(body["response_format"]["json_schema"]["strict"])
            self.assertEqual(body["reasoning_effort"],"low")
            return {"choices":[{"finish_reason":"stop","message":{"content":'{"forecast": 0.2}'}}]}
        self.assertEqual(c.cloud_forecast("GPT-OSS 120B (Arena)",[[0.]],"test",request=fake)[0],[.2])
        for response in ({"choices":[{"finish_reason":"length","message":{"content":'{"forecast": 0.2}'}}]},
                         {"choices":[{"finish_reason":"stop","message":{"content":"[]"}}]}):
            with self.assertRaises(ValueError):c.cloud_forecast("GPT-OSS 120B (Arena)",[[0.]],"test",request=lambda body:response)
    def test_quota_stops_remaining_markets_and_models(self):
        class Limited(Exception):
            status_code=429
            response=Mock(headers={"retry-after":"60"})
        with patch.object(w,"login",return_value="test"),patch.object(w,"archive",return_value=("id",[])),patch.object(w,"dataset",return_value=([],[],[])),patch.object(w,"api") as api_call,patch.object(w,"evaluate",side_effect=Limited()) as evaluate:
            w.run("owner","password",["Qwen 3.8 27B","GPT-OSS 120B (Arena)"],False,"test")
        self.assertEqual(evaluate.call_count,1)
        self.assertIn("retry after 60s",api_call.call_args.args[3]["detail"])
    def test_cloud_without_key_or_over_budget(self):
        for key,contexts in (("",[[0.]]),("test",[[0.]]*49)):
            with self.assertRaises(ValueError):c.cloud_forecast("Qwen 3.8 27B",contexts,key,request=lambda x:self.fail("unexpected call"))
    def test_stress_only_charges_signals(self):
        d=[{"buy":.4,"sell":-.5},{"buy":.1,"sell":-.2},{"buy":-.5,"sell":.4}]
        r=w.stress(d,[1.,0.,1.],.1)
        self.assertEqual(r["signals"],2)
        self.assertAlmostEqual(r["net_atr"],-.3)
        self.assertAlmostEqual(r["max_drawdown_atr"],.6)
    def test_new_forecast_uses_shared_split(self):
        rows=[{"time":1,"x":[0.,.2],"buy":.4,"sell":-.5},{"time":4,"x":[0.,.2],"buy":.3,"sell":-.4}]
        with patch.object(w,"pretrained",return_value=([.5,.5],"mock")):
            r=w.evaluate("Chronos-Bolt Tiny",[],([],rows[:1],rows[1:]),False)
        self.assertEqual(r["test_windows"],1)
        self.assertIn("stress_test",r)
        self.assertEqual(r["status"],"TESTED")
if __name__=="__main__":unittest.main()
