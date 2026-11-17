"""CPU-only local AI: vote sequentially, accept only fresh closed-M1 votes."""
import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/"mt5_client"/"ALZOOL_Local_Model_Relay.py"
spec=importlib.util.spec_from_file_location("alzool_local_ai_relay",PATH)
relay=importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)
BAR=1794839940

class CpuLocalRelayChecks(unittest.TestCase):
    def cycle(self,signal="SELL",deepseek_delay=0,deepseek_error=False):
        clock=[BAR+61]
        calls=[]
        votes=[]
        def fake_request(url,method="GET",payload=None,token=None,timeout=12):
            if url.endswith("/api/tags"):
                return {"models":[{"name":"qwen3.5:4b"},{"name":"deepseek-r1:1.5b"}]}
            if "/v1/local-ai/context/" in url:
                return {"bar_time":BAR,"decision":signal}
            if "/v1/local-ai/advice/" in url:
                votes.append(payload)
                return {"accepted":True,"council_approved":False}
            raise AssertionError(url)
        def fake_vote(model,context,timeout_seconds=50):
            calls.append((model,timeout_seconds))
            if model.startswith("deepseek"):
                clock[0]+=deepseek_delay
                if deepseek_error:
                    raise TimeoutError("model budget")
            return {"model":model,"direction":signal,"confidence":.7,"reason_ar":"test"}
        out=io.StringIO()
        with patch.object(relay,"credentials",return_value=("https://example.com","owner-test-connection","test-pairing-token")):
            with patch.object(relay,"request_json",side_effect=fake_request):
                with patch.object(relay,"model_vote",side_effect=fake_vote):
                    with patch.object(relay.time,"time",side_effect=lambda:clock[0]):
                        with patch.object(sys,"argv",["relay","--once"]):
                            with contextlib.redirect_stdout(out):
                                relay.main()
        return calls,votes,out.getvalue()

    def test_models_run_serially_deepseek_then_qwen(self):
        calls,votes,_=self.cycle()
        self.assertEqual([c[0] for c in calls],["deepseek-r1:1.5b","qwen3.5:4b"])
        self.assertEqual([v["model"] for v in votes],["deepseek-r1:1.5b","qwen3.5:4b"])
        self.assertEqual([v["bar_time"] for v in votes],[BAR,BAR])
        self.assertLessEqual(calls[0][1],20)
        self.assertLessEqual(calls[1][1],36)

    def test_slow_deepseek_never_submits_stale_votes(self):
        calls,votes,log=self.cycle(deepseek_delay=60)
        self.assertEqual([m for m,_ in calls],["deepseek-r1:1.5b"])
        self.assertEqual(votes,[])
        self.assertIn("STALE_BAR",log)
        self.assertIn("SKIP_BAR_BUDGET",log)

    def test_deepseek_timeout_does_not_block_qwen(self):
        calls,votes,log=self.cycle(deepseek_delay=13,deepseek_error=True)
        self.assertEqual([m for m,_ in calls],["deepseek-r1:1.5b","qwen3.5:4b"])
        self.assertEqual([v["model"] for v in votes],["qwen3.5:4b"])
        self.assertIn("ERROR TimeoutError",log)

    def test_central_wait_saves_cpu(self):
        calls,votes,log=self.cycle(signal="WAIT")
        self.assertEqual(calls,[])
        self.assertEqual(votes,[])
        self.assertIn("CENTRAL WAIT",log)

    def test_json_schema_and_threads(self):
        observed={}
        def fake_request(url,method="GET",payload=None,token=None,timeout=12):
            observed["payload"]=payload
            observed["timeout"]=timeout
            return {"response":'{"direction":"SELL","confidence":0.7,"reason_ar":"test"}'}
        with patch.object(relay,"request_json",side_effect=fake_request):
            out=relay.model_vote("qwen3.5:4b",{"bar_time":BAR,"decision":"SELL"},timeout_seconds=23)
        self.assertEqual(out["direction"],"SELL")
        self.assertEqual(observed["timeout"],23)
        self.assertEqual(observed["payload"]["options"]["num_thread"],4)
        self.assertEqual(observed["payload"]["format"]["required"],["direction","confidence","reason_ar"])

if __name__=="__main__":
    unittest.main()
