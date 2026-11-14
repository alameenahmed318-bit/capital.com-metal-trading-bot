"""Safety regression tests for AL-ZOOL multi-model council."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from multi_model_council import fast_ai, risk_guardian, council_result, update_council, _VOTES, _vote
from learning_memory import combine_deals

GOOD_ACCOUNT={"balance":1000,"equity":980,"free_margin":900}
DECISION={"decision":"BUY","confidence":.7,"strategy_id":1,"bar_time":1700000000,
          "entry_reference_price":2500,"context":{"atr":5},
          "family_votes":{"trend":"BUY","range":"WAIT"},
          "combination_summary":{"BUY":10,"SELL":4}}

class SafetyTests(unittest.TestCase):
    def setUp(self):
        _VOTES.clear()

    def test_fast_quant_vote(self):
        v=fast_ai(DECISION,GOOD_ACCOUNT)
        self.assertEqual(v["direction"],"BUY")

    def test_fast_ai_wait_on_bad_price(self):
        d={**DECISION,"entry_reference_price":0}
        self.assertEqual(fast_ai(d,GOOD_ACCOUNT)["direction"],"WAIT")

    def test_risk_guardian_blocks_drawdown(self):
        self.assertFalse(risk_guardian({"balance":1000,"equity":700,"free_margin":500})["approved"])

    def test_risk_guardian_blocks_position_limit(self):
        self.assertFalse(risk_guardian(GOOD_ACCOUNT,[{}]*10)["approved"])

    def test_unavailable_models_never_authorize(self):
        result=council_result("test",DECISION)
        self.assertFalse(result["approved"])

    def test_three_models_must_agree(self):
        _VOTES["test"]={"bar_time":1700000000,"fast":_vote("Fast AI","READY","BUY",.7),
                         "qwen":_vote("Qwen","READY","BUY",.8),
                         "deepseek":_vote("DeepSeek","READY","SELL",.8)}
        self.assertFalse(council_result("test",DECISION)["approved"])
        _VOTES["test"]["deepseek"]=_vote("DeepSeek","READY","BUY",.8)
        self.assertTrue(council_result("test",DECISION)["approved"])

    def test_low_confidence_council_veto(self):
        _VOTES["test"]={"bar_time":1700000000,"fast":_vote("Fast AI","READY","BUY",.7),
                         "qwen":_vote("Qwen","READY","BUY",.4),
                         "deepseek":_vote("DeepSeek","READY","BUY",.8)}
        self.assertFalse(council_result("test",DECISION)["approved"])

    def test_memory_deduplicates_current_deal(self):
        saved=[{"ticket":"1","profit":1},{"ticket":"2","profit":3}]
        live=[{"ticket":"1","profit":2}]
        result=combine_deals(saved,live)
        self.assertEqual(len(result),2)
        self.assertEqual(next(x for x in result if x["ticket"]=="1")["profit"],2)

if __name__=="__main__":
    unittest.main()
