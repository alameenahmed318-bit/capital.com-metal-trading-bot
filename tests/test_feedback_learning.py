import pathlib
import sys
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
import feedback_learning as f
import accuracy_v4 as a

def outcome(i,model="qwen2.5:1.5b",symbol="BTCUSDm",kind="LOCAL_AI"):
    bar=1000+i*60
    return {"payload":{"version":a.VERSION,"symbol":symbol,"kind":kind,"model":model,
        "analysis_mode":"UNIFIED","bar_time":bar,"forecast_time_unix":bar+65,
        "next_bar_time":bar+60,"direction":"BUY","model_positive":i%3==0,
        "reported_confidence":.9}}

def deal(ticket,pid,entry,tag="",vol=.01,profit=0,symbol="BTCUSDm"):
    return {"ticket":ticket,"position_id":pid,"symbol":symbol,"entry":entry,"volume":vol,
        "comment":tag,"time":1000+int(ticket),"profit":profit,"commission":-.1,"swap":0,"fee":0}

class FeedbackTests(unittest.TestCase):
    def test_calibration_out_of_sample_and_market_isolation(self):
        rows=[outcome(i) for i in range(90)]
        rows+=rows[:5]+[outcome(i,symbol="XAUUSD") for i in range(100)]
        rows.append({"payload":{**outcome(500)["payload"],"forecast_time_unix":99999999}})
        m=next(x for x in f.model_feedback(rows,"BTCUSDm") if x["model"]=="qwen2.5:1.5b")
        self.assertEqual(m["samples"],90)
        self.assertEqual(m["training"]+m["validation"],90)
        self.assertEqual(m["status"],"CALIBRATION_CANDIDATE")
        self.assertLess(m["candidate_brier"],m["baseline_brier"])
        self.assertFalse(m["live_weight_changed"])
    def test_insufficient_evidence_is_neutral(self):
        m=next(x for x in f.model_feedback([outcome(1)],"BTCUSDm") if x["model"]=="qwen2.5:1.5b")
        self.assertEqual(m["candidate_confidence_factor"],1)
    def test_action_complete_and_partial_positions(self):
        rows=[deal("1","a",0,"ALZOOL-ADD"),deal("2","a",1,profit=2),
              deal("3","b",0,"ALZOOL-COOL",vol=.02),deal("4","b",1,vol=.01,profit=5),
              deal("5","c",1,"ALZOOL-RECOVERY",profit=4),deal("6","d",0,"ALZOOL-ADD",symbol="XAUUSD")]
        result={r["action"]:r for r in f.action_feedback(rows+rows,"BTCUSDm",now=1100)}
        self.assertEqual(result["ADDON"]["complete_positions"],1)
        self.assertAlmostEqual(result["ADDON"]["net"],1.8)
        self.assertEqual(result["COOLING"]["incomplete_positions"],1)
        self.assertEqual(result["RECOVERY"]["incomplete_positions"],1)
        self.assertEqual(result["ADDON"]["timing_candidate_atr_shift"],0)
    def test_cloud_and_fast_have_distinct_forward_records(self):
        a._PENDING.clear()
        for kind,model in (("FAST_AI","Fast AI"),("CLOUD_AI","GPT-OSS 120B")):
            d={"bar_time":1000,"decision":"BUY","context":{"atr":2}}
            event=a.record("c","BTCUSDm","M1",d,{"bid":10,"ask":11},kind,"BUY",.7,1070,model=model,analysis_mode="UNIFIED")
            self.assertIsNotNone(event)
        settled=a.settle("c","BTCUSDm","M1",[{"time":1060,"close":12}])
        self.assertEqual(len(settled),2)
        self.assertTrue(all(x["payload"]["model_positive"] for x in settled))
        self.assertEqual(a.summarize(settled)["sample_count"],2)

if __name__=="__main__":unittest.main()
