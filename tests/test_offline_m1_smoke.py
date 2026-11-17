"""Read-only offline M1 test safety and schema checks. No network or orders."""
import contextlib
import io
import json
import math
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
import offline_m1_smoke

def candles(n=175):
    data=[]
    previous=2450.0
    for i in range(n):
        close=2450+0.1*i+2*math.sin(i/5)
        data.append({"candle_time":f"2026-11-13 12:{i%60:02d}:00+00",
                     "candle_open":previous,
                     "candle_high":max(previous,close)+0.3,
                     "candle_low":min(previous,close)-0.3,
                     "candle_close":close,"tick_volume":120+i})
        previous=close
    return data

class OfflineDiagnosticTests(unittest.TestCase):
    def test_no_history_never_calls_groq(self):
        output=io.StringIO()
        with patch.object(offline_m1_smoke.groq_advisor,"_call_groq") as call:
            with contextlib.redirect_stdout(output):
                offline_m1_smoke.run(lambda: [])
            call.assert_not_called()
        self.assertIn("INSUFFICIENT_HISTORY",output.getvalue())
        self.assertIn('"trading_execution": false',output.getvalue())

    def test_full_quant_library_and_one_groq_review_without_authorization(self):
        result={"choices":[{"message":{"content":json.dumps(
            {"direction":"WAIT","confidence":0.7,"reason_ar":"حالة تاريخية"})}}]}
        output=io.StringIO()
        with patch.object(offline_m1_smoke.groq_advisor,"configured",return_value=True):
            with patch.object(offline_m1_smoke.groq_advisor,"_call_groq",return_value=result) as call:
                with contextlib.redirect_stdout(output):
                    offline_m1_smoke.run(lambda:candles())
                self.assertEqual(call.call_count,1)
                prompt=call.call_args.args[0]
                self.assertEqual(prompt["mode"],"OFFLINE_HISTORICAL_M1_READ_ONLY")
                self.assertNotIn("client_id",prompt)
                self.assertNotIn("account",prompt)
        lines=output.getvalue().splitlines()
        self.assertEqual(len(lines),2,output.getvalue())
        metrics=json.loads(lines[0].split(": ",1)[1])
        ai=json.loads(lines[1].split(": ",1)[1])
        self.assertEqual(metrics["status"],"OK")
        self.assertEqual(metrics["strategies"],200)
        self.assertEqual(metrics["combinations"],1000)
        self.assertGreater(metrics["replay_samples"],0)
        self.assertEqual(ai["status"],"READY")
        self.assertFalse(metrics["execution_authorized"])
        self.assertFalse(ai["execution_authorized"])

if __name__=="__main__":
    unittest.main()
