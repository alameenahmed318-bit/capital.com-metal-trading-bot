"""Safety and timing tests for delayed local model approvals on M1."""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decision_guard import closed_bar_execution_fresh
from multi_model_council import (
    _VOTES, _vote, accept_local_model_vote, council_result,
)

DECISION = {"decision":"SELL", "bar_time":1700000040}


class ClosedBarCouncilSyncTests(unittest.TestCase):
    def setUp(self):
        _VOTES.clear()
        _VOTES["owner"] = {
            "bar_time":DECISION["bar_time"],
            "fast":_vote("Fast AI","READY","SELL",.9),
            "qwen":_vote("Qwen","NOT_CONFIGURED"),
            "deepseek":_vote("DeepSeek","NOT_CONFIGURED"),
        }

    def tearDown(self):
        _VOTES.clear()

    def test_m1_valid_only_in_candle_following_closed_bar(self):
        bar=DECISION["bar_time"]
        self.assertFalse(closed_bar_execution_fresh(bar,bar+59,"PERIOD_M1"))
        self.assertTrue(closed_bar_execution_fresh(bar,bar+60,"PERIOD_M1"))
        self.assertTrue(closed_bar_execution_fresh(bar,bar+119.99,"PERIOD_M1"))
        self.assertFalse(closed_bar_execution_fresh(bar,bar+120,"PERIOD_M1"))
        self.assertFalse(closed_bar_execution_fresh(bar,bar+180,"PERIOD_M1"))

    def test_m5_validity_uses_closed_bar_period(self):
        bar=DECISION["bar_time"]
        self.assertFalse(closed_bar_execution_fresh(bar,bar+299,"PERIOD_M5"))
        self.assertTrue(closed_bar_execution_fresh(bar,bar+301,"PERIOD_M5"))
        self.assertFalse(closed_bar_execution_fresh(bar,bar+600,"PERIOD_M5"))

    def test_unknown_frame_and_invalid_dates_fail_closed(self):
        self.assertFalse(closed_bar_execution_fresh(1700000040,1700000101,"BOGUS"))
        self.assertFalse(closed_bar_execution_fresh(0,1700000101,"M1"))
        self.assertFalse(closed_bar_execution_fresh("bad",1700000101,"M1"))

    def test_approval_appears_immediately_after_both_votes(self):
        self.assertFalse(council_result("owner", DECISION)["approved"])
        q={"model":"qwen3.5:4b","bar_time":DECISION["bar_time"],
           "direction":"SELL","confidence":.95}
        self.assertTrue(accept_local_model_vote("owner",q,DECISION))
        self.assertFalse(council_result("owner",DECISION)["approved"])
        deep={"model":"deepseek-r1:1.5b","bar_time":DECISION["bar_time"],
              "direction":"SELL","confidence":.83}
        self.assertTrue(accept_local_model_vote("owner",deep,DECISION))
        approved=council_result("owner",DECISION)
        self.assertTrue(approved["approved"])
        self.assertEqual(approved["status"],"SELL")

    def test_wrong_bar_low_confidence_and_disagreement_never_approve(self):
        bad={"model":"qwen3.5:4b","bar_time":DECISION["bar_time"]+60,
             "direction":"SELL","confidence":.95}
        self.assertFalse(accept_local_model_vote("owner",bad,DECISION))
        self.assertFalse(council_result("owner",DECISION)["approved"])
        q={**bad,"bar_time":DECISION["bar_time"],"confidence":.49}
        self.assertTrue(accept_local_model_vote("owner",q,DECISION))
        deep={"model":"deepseek-r1:1.5b","bar_time":DECISION["bar_time"],
              "direction":"SELL","confidence":.95}
        self.assertTrue(accept_local_model_vote("owner",deep,DECISION))
        self.assertFalse(council_result("owner",DECISION)["approved"])
        q["confidence"]=.9
        self.assertTrue(accept_local_model_vote("owner",q,DECISION))
        deep["direction"]="BUY"
        self.assertTrue(accept_local_model_vote("owner",deep,DECISION))
        self.assertFalse(council_result("owner",DECISION)["approved"])

    def test_local_relay_and_server_have_matching_signal_contract(self):
        root=Path(__file__).resolve().parents[3]
        server=(root/"marketplace/backend/app.py").read_text(encoding="utf-8")
        relay=(root/"mt5_client/ALZOOL_Local_Model_Relay.py").read_text(encoding="utf-8")
        self.assertIn('"decision":decision.get("decision","WAIT")',server)
        self.assertIn('"direction":decision.get("decision","WAIT")',server)
        self.assertIn("def central_signal(context):",relay)
        self.assertIn("closed_bar_execution_fresh(current_bar",server)
        self.assertIn("AI_COUNCIL_VOTES_READY",server)


if __name__ == "__main__":
    unittest.main()
