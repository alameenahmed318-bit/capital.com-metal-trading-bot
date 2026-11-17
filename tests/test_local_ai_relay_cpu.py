"""Regression checks: lightweight local Qwen can advise M1 without fabricated votes."""
import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
BACKEND=ROOT/"marketplace"/"backend"
sys.path.insert(0,str(BACKEND))
import multi_model_council as council
from decision_guard import build_qwen_vote_event,retain_current_local_ai_advice

spec=importlib.util.spec_from_file_location("alzool_local_ai_relay",ROOT/"mt5_client"/"ALZOOL_Local_Model_Relay.py")
relay=importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)
BAR=1794839940
MODEL="qwen2.5:1.5b"

class FastQwenRelayChecks(unittest.TestCase):
    def cycle(self,signal="SELL",start_offset=61,model_delay=0,model_error=None):
        clock=[BAR+start_offset]
        calls=[]
        posted=[]
        def fake_request(url,method="GET",payload=None,token=None,timeout=12):
            if url.endswith("/api/tags"):
                return {"models":[{"name":MODEL}]}
            if "/v1/local-ai/context/" in url:
                return {"bar_time":BAR,"decision":signal}
            if "/v1/local-ai/advice/" in url:
                posted.append(payload)
                return {"accepted":True,"council_approved":False}
            raise AssertionError(url)
        def fake_vote(model,context,timeout_seconds=50):
            calls.append((model,timeout_seconds))
            clock[0]+=model_delay
            if model_error:
                raise model_error
            return {"model":model,"direction":signal,"confidence":0.62,"reason_ar":"اتجاه الشموع واضح"}
        out=io.StringIO()
        with patch.object(relay,"credentials",return_value=("https://example.com","owner-test","fake-secret-not-printed")):
            with patch.object(relay,"request_json",side_effect=fake_request):
                with patch.object(relay,"model_vote",side_effect=fake_vote):
                    with patch.object(relay.time,"time",side_effect=lambda:clock[0]):
                        with patch.object(sys,"argv",["relay","--once"]):
                            with contextlib.redirect_stdout(out):
                                relay.main()
        return calls,posted,out.getvalue()

    def test_only_lightweight_model_is_scheduled(self):
        self.assertEqual(relay.MODELS,(MODEL,))
        calls,posted,output=self.cycle()
        self.assertEqual([m for m,t in calls],[MODEL])
        self.assertEqual([p["model"] for p in posted],[MODEL])
        self.assertEqual(posted[0]["bar_time"],BAR)
        self.assertLessEqual(calls[0][1],18)
        self.assertIn("accepted=True",output)

    def test_wait_signal_saves_cpu(self):
        calls,posted,output=self.cycle(signal="WAIT")
        self.assertEqual(calls,[])
        self.assertEqual(posted,[])
        self.assertIn("CENTRAL WAIT",output)

    def test_short_m1_budget_skips_inference(self):
        calls,posted,output=self.cycle(start_offset=110)
        self.assertEqual(calls,[])
        self.assertEqual(posted,[])
        self.assertIn("SKIP_BAR_BUDGET",output)

    def test_stale_response_cannot_be_submitted(self):
        calls,posted,output=self.cycle(model_delay=61)
        self.assertEqual([a[0] for a in calls],[MODEL])
        self.assertEqual(posted,[])
        self.assertIn("STALE_BAR",output)

    def test_timeout_does_not_forge_vote(self):
        calls,posted,output=self.cycle(model_error=TimeoutError("slow CPU"))
        self.assertEqual(len(calls),1)
        self.assertEqual(posted,[])
        self.assertIn("ERROR TimeoutError",output)

    def test_reject_non_arabic_reason_in_fast_model(self):
        chinese={"message":{"content":'{"direction":"BUY","confidence":0.9,"reason_ar":"价格上涨"}'}}
        arabic={"message":{"content":'{"direction":"BUY","confidence":0.7,"reason_ar":"الشمعة صاعدة"}'}}
        self.assertIsNone(relay.validate_response(MODEL,chinese))
        self.assertEqual(relay.validate_response(MODEL,arabic)["direction"],"BUY")

    def test_model_request_uses_real_json_and_cpu_budget(self):
        captured={}
        def fake_request(url,method="GET",payload=None,token=None,timeout=12):
            captured.update({"url":url,"payload":payload,"timeout":timeout})
            return {"message":{"content":'{"direction":"WAIT","confidence":0.6,"reason_ar":"بيانات غير كافية"}'}}
        with patch.object(relay,"request_json",side_effect=fake_request):
            vote=relay.model_vote(MODEL,{"bar_time":BAR,"decision":"WAIT"},timeout_seconds=14)
        self.assertEqual(vote["direction"],"WAIT")
        self.assertEqual(captured["url"],relay.OLLAMA_URL+"/api/chat")
        self.assertEqual(captured["timeout"],14)
        self.assertEqual(captured["payload"]["options"]["num_predict"],100)
        self.assertEqual(captured["payload"]["options"]["num_thread"],4)
        self.assertEqual(captured["payload"]["format"]["required"],["direction","confidence","reason_ar"])

    def test_auth_state_accepts_new_qwen_as_real_qwen_vote(self):
        client="test-fast-qwen-only"
        decision={"bar_time":BAR,"decision":"BUY","confidence":0.75}
        advice={"bar_time":BAR,"model":MODEL,"direction":"BUY","confidence":0.67,"reason_ar":"الزخم صاعد"}
        with patch.dict(council._VOTES,{client:{"bar_time":BAR,"fast":{"status":"READY"}}},clear=True):
            self.assertTrue(council.accept_local_model_vote(client,advice,decision))
            self.assertEqual(council._VOTES[client]["local_qwen"]["model_id"],MODEL)
            self.assertEqual(council._VOTES[client]["local_qwen"]["direction"],"BUY")
            self.assertIsNone(council._VOTES[client].get("local_deepseek"))

    def test_diagnostics_preserve_true_model_id(self):
        decision={"bar_time":BAR,"decision":"BUY","confidence":0.75}
        advice={"bar_time":BAR,"model":MODEL,"direction":"BUY","confidence":0.67,
                "reason_ar":"الزخم صاعد","received_at_unix":1000.0}
        event=build_qwen_vote_event("test-client",advice,decision)
        self.assertEqual(event["payload"]["model"],MODEL)
        self.assertIn(MODEL,event["event_id"])
        self.assertEqual(retain_current_local_ai_advice(advice,decision,now_unix=1005)["model"],MODEL)

    def test_legacy_qwen_still_accepted_by_council(self):
        client="test-legacy-qwen"
        decision={"bar_time":BAR}
        advice={"bar_time":BAR,"model":"qwen3.5:4b","direction":"SELL",
                "confidence":0.8,"reason_ar":"زخم هابط"}
        with patch.dict(council._VOTES,{client:{"bar_time":BAR}},clear=True):
            self.assertTrue(council.accept_local_model_vote(client,advice,decision))

if __name__=="__main__":
    unittest.main()
