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
    def cycle(self,signal="SELL",deepseek_delay=0,deepseek_error=False,start_offset=61,qwen_delay=0):
        clock=[BAR+start_offset]
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
            if model.startswith("qwen"):
                clock[0]+=qwen_delay
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

    def test_models_run_serially_qwen_then_deepseek(self):
        calls,votes,_=self.cycle()
        self.assertEqual([c[0] for c in calls],["qwen3.5:4b","deepseek-r1:1.5b"])
        self.assertEqual([v["model"] for v in votes],["qwen3.5:4b","deepseek-r1:1.5b"])
        self.assertEqual([v["bar_time"] for v in votes],[BAR,BAR])
        self.assertLessEqual(calls[0][1],25)
        self.assertLessEqual(calls[1][1],28)

    def test_slow_deepseek_never_submits_stale_votes(self):
        calls,votes,log=self.cycle(deepseek_delay=60)
        self.assertEqual([m for m,_ in calls],["qwen3.5:4b","deepseek-r1:1.5b"])
        self.assertEqual([v["model"] for v in votes],["qwen3.5:4b"])
        self.assertIn("STALE_BAR",log)

    def test_deepseek_skips_short_remaining_bar_budget(self):
        calls,votes,log=self.cycle(qwen_delay=32)
        self.assertEqual([m for m,_ in calls],["qwen3.5:4b"])
        self.assertEqual([v["model"] for v in votes],["qwen3.5:4b"])
        self.assertIn("SKIP_BAR_BUDGET",log)

    def test_deepseek_timeout_does_not_block_qwen(self):
        calls,votes,log=self.cycle(deepseek_delay=13,deepseek_error=True)
        self.assertEqual([m for m,_ in calls],["qwen3.5:4b","deepseek-r1:1.5b"])
        self.assertEqual([v["model"] for v in votes],["qwen3.5:4b"])
        self.assertIn("ERROR TimeoutError",log)

    def test_deepseek_never_starts_without_time_to_finish(self):
        calls,votes,log=self.cycle(start_offset=92)
        self.assertEqual(calls,[])
        self.assertEqual(votes,[])
        self.assertIn("SKIP_BAR_BUDGET",log)

    def test_deepseek_has_enough_tokens_for_real_answer(self):
        observed={}
        def fake_request(url,method="GET",payload=None,token=None,timeout=12):
            observed["payload"]=payload
            observed["timeout"]=timeout
            return {"message":{"content":'{"direction":"SELL","confidence":0.7,"reason_ar":"test"}'}}
        with patch.object(relay,"request_json",side_effect=fake_request):
            vote=relay.model_vote("deepseek-r1:1.5b",{"bar_time":BAR,"decision":"BUY"},timeout_seconds=33)
        self.assertEqual(vote["direction"],"SELL")
        self.assertEqual(observed["payload"]["options"]["num_predict"],352)
        self.assertEqual(observed["timeout"],33)

    def test_failed_models_backoff_without_fake_votes(self):
        failures={}
        pauses={}
        output=io.StringIO()
        with patch.object(relay.time,"monotonic",return_value=100):
            with contextlib.redirect_stdout(output):
                relay.record_model_failure("deepseek-r1:1.5b",failures,pauses)
                self.assertNotIn("deepseek-r1:1.5b",pauses)
                relay.record_model_failure("deepseek-r1:1.5b",failures,pauses)
                relay.record_model_failure("qwen3.5:4b",failures,pauses)
                relay.record_model_failure("qwen3.5:4b",failures,pauses)
        self.assertEqual(pauses["deepseek-r1:1.5b"],700)
        self.assertEqual(pauses["qwen3.5:4b"],400)
        self.assertIn("CPU_COOLDOWN",output.getvalue())
        self.assertEqual(failures["deepseek-r1:1.5b"],0)

    def test_central_wait_saves_cpu(self):
        calls,votes,log=self.cycle(signal="WAIT")
        self.assertEqual(calls,[])
        self.assertEqual(votes,[])
        self.assertIn("CENTRAL WAIT",log)

    def test_chat_final_content_is_parsed(self):
        observed={}
        def fake_request(url,method="GET",payload=None,token=None,timeout=12):
            observed["url"]=url
            observed["payload"]=payload
            return {"message":{"content":'{"direction":"SELL","confidence":0.64,"reason_ar":"test"}',
                               "thinking":"a private scratchpad"},"done_reason":"stop"}
        with patch.object(relay,"request_json",side_effect=fake_request):
            vote=relay.model_vote("deepseek-r1:1.5b",{"bar_time":BAR,"decision":"BUY"},timeout_seconds=12)
        self.assertEqual(vote["direction"],"SELL")
        self.assertEqual(observed["url"],relay.OLLAMA_URL+"/api/chat")
        self.assertEqual(observed["payload"]["format"],"json")
        self.assertEqual(observed["payload"]["think"],False)
        self.assertEqual(len(observed["payload"]["messages"]),2)

    def test_reasoning_without_final_content_is_not_a_vote(self):
        def fake_request(url,method="GET",payload=None,token=None,timeout=12):
            return {"message":{"content":"","thinking":'{"direction":"SELL","confidence":0.99}'},
                    "done_reason":"length","eval_count":128}
        output=io.StringIO()
        with patch.object(relay,"request_json",side_effect=fake_request):
            with contextlib.redirect_stdout(output):
                vote=relay.model_vote("deepseek-r1:1.5b",{"bar_time":BAR,"decision":"SELL"})
        self.assertIsNone(vote)
        self.assertIn("RESPONSE_DIAG content_chars=0",output.getvalue())
        self.assertNotIn("0.99",output.getvalue())

    def test_chat_no_thinking_reason_used_as_signal(self):
        vote=relay.validate_response("qwen3.5:4b",
            {"message":{"content":"none","thinking":'{"direction":"BUY","confidence":1.0}'}})
        self.assertIsNone(vote)

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
        self.assertEqual(observed["payload"]["options"]["num_predict"],100)
        self.assertEqual(observed["payload"]["format"]["required"],["direction","confidence","reason_ar"])

if __name__=="__main__":
    unittest.main()
