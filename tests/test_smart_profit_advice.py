import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"marketplace"/"backend"))
from smart_profit_advice import master_bias,profit_settings_for_terminal

class SmartProfitTests(unittest.TestCase):
    def test_master_bias_independent_of_entry_gate(self):
        d={"decision":"WAIT","master":{"mode":"MASTER","scores":{"BUY":.9,"SELL":.1}}}
        self.assertEqual(master_bias(d,True),{"smart_profit_bias":.8,"smart_profit_bias_valid":True})
    def test_invalid_and_stale_bias(self):
        self.assertFalse(master_bias({"master":{"mode":"ADVISORY","scores":{"BUY":1,"SELL":0}}},True)["smart_profit_bias_valid"])
        self.assertFalse(master_bias({"master":{"mode":"MASTER","scores":{"BUY":1,"SELL":0}}},False)["smart_profit_bias_valid"])
        self.assertFalse(master_bias({"master":{"mode":"MASTER","scores":{"BUY":"nan","SELL":0}}},True)["smart_profit_bias_valid"])
    def test_fixed_unchanged(self):
        s={"single_enabled":True,"group_enabled":True}
        self.assertEqual(profit_settings_for_terminal(s,None),s)
    def test_legacy_smart_does_not_switch_to_fixed(self):
        s={"single_enabled":True,"group_enabled":True,"position_management":{"profit_close_mode":"SMART"}}
        self.assertEqual(profit_settings_for_terminal(s,{"smart_profit_capable":True}),s)
        self.assertFalse(profit_settings_for_terminal(s,{})["group_enabled"])
        self.assertTrue(s["group_enabled"])
    def test_source_contract_only_not_mql_compilation(self):
        ea=(ROOT/"mt5_client"/"ALZOOL_Multi_Market_Client_V303.mq5").read_text(encoding="utf-8-sig")
        self.assertIn('#property version "3.03"',ea)
        self.assertIn('if(EvaluateSmartRecovery()) { ResetSmartProfit(); return; }',ea)
        self.assertIn('if(g_smartProfitMode) { EvaluateSmartProfit(); return; }',ea)
        self.assertIn('if(recheck<=0.0 || !MathIsValidNumber(recheck)) return;',ea)
        self.assertIn('EvaluateSmartProfitSide(POSITION_TYPE_BUY,bias);',ea)
        self.assertIn('EvaluateSmartProfitSide(POSITION_TYPE_SELL,bias);',ea)
    def test_ui_opt_in(self):
        page=(ROOT/"trade-management.html").read_text()
        js=(ROOT/"trade-management.js").read_text()
        self.assertIn('<option value="FIXED">',page)
        self.assertIn('<option value="SMART">',page)
        self.assertIn('confirm_smart_profit:profitMode==="SMART"',js)
if __name__=="__main__":unittest.main()
