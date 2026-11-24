"""Unified V4 regression tests: mocked inference and handler; no broker actions."""
import contextlib
from datetime import datetime,timezone
import importlib.util
import io
import json
import pathlib
import sys
import types
import unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"marketplace"/"backend"))
import app
import accuracy_v4 as a
spec=importlib.util.spec_from_file_location("unified_relay",ROOT/"mt5_client"/"ALZOOL_Local_Model_Relay.py")
relay=importlib.util.module_from_spec(spec);spec.loader.exec_module(relay)
BAR=1795493100
MODEL="qwen2.5:1.5b"

def context():
    return {"bar_time":BAR,"symbol":"BTCUSDm","decision":"SELL","confidence":.99,
        "unified_ai_protocol":relay.UNIFIED_PROTOCOL,
        "context":{"atr":2,"adx":25,"support":98,"resistance":104,"reason":"SECRET_DIRECTION"},
        "family_votes":{"test":"SELL"},
        "candles":[{"open":100-i,"high":103-i,"low":99-i,"close":102-i} for i in range(5)]}

class UnifiedTests(unittest.TestCase):
    def test_compact_blind_prompt_and_actual_arabic_reason_code(self):
        ctx=dict(context(),_analysis_protocol=relay.UNIFIED_PROTOCOL)
        with patch.object(relay,"request_json",return_value={"message":{"content":
            '{"direction":"BUY","confidence":0.65,"reason_code":"LOW_REJECTION"}'}}) as req:
            vote=relay.model_vote(MODEL,ctx,18)
        self.assertEqual(req.call_count,1)
        self.assertEqual(vote["direction"],"BUY")
        self.assertEqual(vote["reason_ar"],relay.REASON_CODES["LOW_REJECTION"])
        payload=req.call_args.kwargs["payload"]
        data=json.loads(payload["messages"][1]["content"])
        self.assertEqual(len(data["ohlc"]),5)
        self.assertEqual(data["ohlc"][-1][-1],0)
        self.assertNotIn("SELL",json.dumps(data))
        self.assertNotIn("SECRET",json.dumps(data))
        self.assertNotIn("confidence",data)
        self.assertEqual(payload["options"]["num_predict"],80)
        self.assertIsNone(relay.validate_unified_response(MODEL,{"message":{"content":
            '{"direction":"BUY","confidence":0.65,"reason_code":"UNKNOWN"}'}}))

    def test_one_inference_and_one_post_for_both_functions(self):
        posts=[];calls=[]
        def request(url,method="GET",payload=None,token=None,timeout=12):
            if url.endswith("/api/tags"):return {"models":[{"name":MODEL}]}
            if "/v1/local-ai/context/" in url:return context()
            if "/v1/local-ai/unified-v4/" in url:
                posts.append(payload);return {"accepted":True,"unified_v4":True,"accuracy_recorded":True}
            raise AssertionError(url)
        def vote(model,ctx,timeout_seconds):
            calls.append((model,ctx["_analysis_protocol"],timeout_seconds))
            return {"model":model,"direction":"BUY","confidence":.65,"reason_ar":"زخم صاعد"}
        out=io.StringIO()
        with patch.object(relay,"credentials",return_value=("https://example.com","c","test-key")), \
             patch.object(relay,"request_json",side_effect=request), \
             patch.object(relay,"model_vote",side_effect=vote), \
             patch.object(relay.time,"time",return_value=BAR+61), \
             patch.object(sys,"argv",["relay","--unified-v4","--once"]), \
             contextlib.redirect_stdout(out):
            relay.main()
        self.assertEqual(len(calls),1)
        self.assertEqual(len(posts),1)
        self.assertEqual(posts[0]["direction"],"BUY")
        self.assertEqual(posts[0]["analysis_protocol"],relay.UNIFIED_PROTOCOL)
        self.assertIn("accuracy_recorded=True",out.getvalue())
        self.assertLessEqual(calls[0][2],18)

    def test_no_protocol_no_inference(self):
        def request(url,**kwargs):
            if url.endswith("/api/tags"):return {"models":[{"name":MODEL}]}
            return {"bar_time":BAR,"decision":"SELL"}
        with patch.object(relay,"credentials",return_value=("https://example.com","c","test")), \
             patch.object(relay,"request_json",side_effect=request), \
             patch.object(relay,"model_vote") as vote, \
             patch.object(sys,"argv",["relay","--unified-v4","--once"]),contextlib.redirect_stdout(io.StringIO()):
            relay.main()
        vote.assert_not_called()

    def test_handler_uses_same_vote_and_audit_without_authorization(self):
        a._PENDING.clear()
        now=datetime.fromtimestamp(BAR+65,timezone.utc)
        item={"received_at":now,"symbol":"BTCUSDm","timeframe":"M1",
            "strategy_decision":dict(context(),strategy_id=11),"tick":{"bid":100,"ask":101}}
        response=[];events=[]
        data={"model":MODEL,"bar_time":BAR,"symbol":"BTCUSDm","direction":"BUY",
            "confidence":.65,"reason_ar":"زخم صاعد","analysis_protocol":a.UNIFIED_PROTOCOL}
        handler=types.SimpleNamespace(path="/v1/local-ai/unified-v4/c",read_json=lambda:data,
            send_json=lambda code,body:response.append((code,body)))
        with patch.object(app,"token_of",return_value="test"),patch.object(app,"validate_license",return_value=True), \
             patch.object(app,"terminal_is_owner",return_value=True),patch.object(app,"CONNECTIONS",{"c":item}), \
             patch.object(app,"datetime",wraps=datetime) as dt,patch.object(app.time,"time",return_value=BAR+65), \
             patch.object(app,"accept_local_model_vote",return_value=True) as accepted, \
             patch.object(app,"council_result",return_value={"approved":False,"votes":{}}), \
             patch.object(app,"queue_ai_diagnostic_event",side_effect=lambda e:(events.append(e) or True)):
            dt.now.return_value=now
            app.Handler.do_POST(handler)
        self.assertEqual(response[0][0],200,response)
        self.assertTrue(response[0][1]["accuracy_recorded"])
        self.assertTrue(response[0][1]["unified_v4"])
        self.assertFalse(response[0][1]["execution_authorized"])
        self.assertEqual(accepted.call_count,1)
        forecast=[e for e in events if e["event_type"]=="AI_ACCURACY_V4_FORECAST"][0]["payload"]
        self.assertEqual(forecast["direction"],accepted.call_args.args[1]["direction"])
        self.assertEqual(forecast["reported_confidence"],accepted.call_args.args[1]["confidence"])
        self.assertEqual(forecast["analysis_mode"],"UNIFIED")
        out=a.settle("c","BTCUSDm","M1",[{"time":BAR+60,"close":104}])
        self.assertEqual(a.summarize(out)["groups"][0]["analysis_mode"],"UNIFIED")
        a._PENDING.clear()

    def test_dual_models_run_in_sequence_and_submit_own_votes(self):
        deep="deepseek-r1:1.5b"
        calls=[]
        clock=[BAR+61]
        def request(url,method="GET",payload=None,token=None,timeout=12):
            if url.endswith("/api/tags"):return {"models":[{"name":m} for m in (MODEL,deep)]}
            if "/v1/local-ai/context/" in url:
                return dict(context(),unified_ai_models=[MODEL,deep])
            if "/v1/local-ai/unified-v4/" in url:
                calls.append(("post",payload["model"],payload["direction"]))
                return {"accepted":True,"unified_v4":True,"accuracy_recorded":True}
            raise AssertionError(url)
        def vote(model,ctx,timeout_seconds):
            calls.append(("infer",model,timeout_seconds))
            clock[0]+=3
            return {"model":model,"direction":"BUY" if model==MODEL else "SELL",
                    "confidence":.65,"reason_ar":"اختبار"}
        with patch.object(relay,"credentials",return_value=("https://example.com","c","test")), \
             patch.object(relay,"request_json",side_effect=request), \
             patch.object(relay,"model_vote",side_effect=vote), \
             patch.object(relay.time,"time",side_effect=lambda:clock[0]), \
             patch.object(sys,"argv",["relay","--unified-v4","--with-deepseek","--once"]), \
             contextlib.redirect_stdout(io.StringIO()):
            relay.main()
        self.assertEqual([(x[0],x[1]) for x in calls],
            [("infer",MODEL),("post",MODEL),("infer",deep),("post",deep)])
        self.assertEqual(calls[1][2],"BUY")
        self.assertEqual(calls[3][2],"SELL")

    def test_deepseek_uses_own_validated_response(self):
        deep="deepseek-r1:1.5b"
        ctx=dict(context(),_analysis_protocol=relay.UNIFIED_PROTOCOL)
        with patch.object(relay,"request_json",return_value={"done":True,"done_reason":"stop","response":
            '{"direction":"SELL","confidence":0.64,"reason_code":"DOWN_MOMENTUM"}'}) as req:
            vote=relay.model_vote(deep,ctx,24)
        self.assertEqual(vote["model"],deep)
        self.assertEqual(vote["direction"],"SELL")
        self.assertEqual(req.call_args.kwargs["payload"]["model"],deep)
        self.assertEqual(req.call_args.kwargs["timeout"],24)

    def test_deepseek_final_json_only_not_reasoning(self):
        deep="deepseek-r1:1.5b"
        final='{"direction":"SELL","confidence":0.64,"reason_code":"DOWN_MOMENTUM"}'
        hidden='{"direction":"BUY","confidence":0.99,"reason_code":"UP_MOMENTUM"}'
        for raw in (final, "<think>"+hidden+"</think>"+final, "```json\n"+final+"\n```"):
            self.assertEqual(relay.validate_unified_response(deep,{"message":{"content":raw}})["direction"],"SELL")
        self.assertIsNone(relay.validate_unified_response(deep,{"message":{"content":"<think>"+hidden}}))
        self.assertIsNone(relay.validate_unified_response(deep,{"message":{"content":"","thinking":hidden}}))
        self.assertIsNone(relay.validate_unified_response(deep,{"message":{"content":'{"direction":"BUY","confidence":true,"reason_code":"UP_MOMENTUM"}'}}))
        self.assertIsNone(relay.validate_unified_response(deep,{"message":{"content":'{"direction":"BUY","confidence":0.9}'}}))

    def test_deepseek_actual_free_text_reason_preserved(self):
        deep="deepseek-r1:1.5b"
        result={"message":{"content":'{"direction":"SELL","confidence":0.64,"reason":"Upper wick rejection"}'}}
        vote=relay.validate_unified_response(deep,result)
        self.assertEqual(vote["direction"],"SELL")
        self.assertEqual(vote["reason_original"],"Upper wick rejection")

    def test_deepseek_raw_schema_and_token_budget(self):
        deep="deepseek-r1:1.5b"
        ctx=dict(context(),_analysis_protocol=relay.UNIFIED_PROTOCOL)
        with patch.object(relay,"request_json",return_value={"done":True,"done_reason":"stop","response":
            '{"direction":"SELL","confidence":0.64,"reason_code":"HIGH_REJECTION"}'}) as req:
            vote=relay.model_vote(deep,ctx,24)
        self.assertEqual(vote["model"],deep)
        payload=req.call_args.kwargs["payload"]
        self.assertTrue(req.call_args.args[0].endswith("/api/generate"))
        self.assertTrue(payload["raw"])
        self.assertEqual(payload["format"]["required"],["direction","confidence","reason_code"])
        self.assertEqual(payload["options"]["num_predict"],128)
        self.assertFalse(payload["think"])
        self.assertEqual(req.call_args.kwargs["timeout"],24)
        self.assertIn('"ohlc"',payload["prompt"])
        self.assertIn('"trend_efficiency"',payload["prompt"])
        self.assertNotIn("SECRET_DIRECTION",payload["prompt"])
        self.assertTrue(payload["prompt"].endswith("<｜Assistant｜><think></think>"))

    def test_balanced_deepseek_rejects_contradiction_and_truncation(self):
        deep="deepseek-r1:1.5b"
        def vote(direction,code,done_reason="stop",confidence=.65):
            return relay.validate_balanced_deepseek(deep,{"done":True,"done_reason":done_reason,
                "response":json.dumps({"direction":direction,"confidence":confidence,"reason_code":code})})
        self.assertIsNone(vote("SELL","UP_MOMENTUM"))
        self.assertIsNone(vote("BUY","DOWN_MOMENTUM"))
        self.assertIsNone(vote("WAIT","SUPPORT"))
        self.assertIsNone(vote("BUY","SUPPORT","length"))
        self.assertIsNone(vote("BUY","SUPPORT",confidence=1.5))
        self.assertEqual(vote("SELL","HIGH_REJECTION")["direction"],"SELL")
        self.assertEqual(vote("BUY","LOW_REJECTION")["direction"],"BUY")
        self.assertEqual(vote("WAIT","MIXED")["direction"],"WAIT")

    def test_balanced_snapshot_preserves_measured_evidence(self):
        ctx=context()
        ctx["context"]["rsi"]=56
        snap=relay.balanced_deepseek_snapshot(ctx)
        self.assertEqual(snap["ohlc"],relay.unified_snapshot(ctx)["ohlc"])
        self.assertEqual(snap["net_move_atr"],2)
        self.assertEqual(snap["trend_efficiency"],1)
        self.assertEqual(snap["last_body_atr"],1)
        self.assertEqual(snap["last_upper_wick_atr"],.5)
        self.assertEqual(snap["last_lower_wick_atr"],.5)
        self.assertEqual(snap["rsi"],56)
        self.assertNotIn("decision",snap)
        self.assertNotIn("confidence",snap)

    def test_invalid_deepseek_output_shorter_cooldown(self):
        deep="deepseek-r1:1.5b"
        failures={};pause={}
        with patch.object(relay.time,"monotonic",return_value=100),contextlib.redirect_stdout(io.StringIO()):
            relay.record_model_failure(deep,failures,pause,invalid_output=True)
            relay.record_model_failure(deep,failures,pause,invalid_output=True)
        self.assertEqual(pause[deep],160)
        with patch.object(relay.time,"monotonic",return_value=100),contextlib.redirect_stdout(io.StringIO()):
            relay.record_model_failure(deep,failures,pause)
            relay.record_model_failure(deep,failures,pause)
        self.assertEqual(pause[deep],700)

if __name__=="__main__":unittest.main()
