"""Read-only recovery decision/protocol checks; no MT5 order is sent."""
import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from recovery_logic import recovery_intent
from central_action_protocol import _allowed


class AccountRecoveryTests(unittest.TestCase):
    def snapshot(self, positions=None, net=None):
        p=positions if positions is not None else [
            {"ticket":"1","symbol":"XAUUSD","type":0,"volume":0.01,
             "profit":-18,"swap":0,"open_price":3000},
            {"ticket":"2","symbol":"XAUUSD","type":1,"volume":0.02,
             "profit":-5,"swap":0,"open_price":3000}]
        data={"symbol":"XAUUSD","positions":p,
              "tick":{"bid":2990,"ask":2990.2},"received_at":datetime.now(timezone.utc)}
        if net is not None:data["recovery_group_net"]=net
        return data

    def settings(self, **changes):
        return {"trading_enabled":True,"position_management":{
            "mode":"AI","central_actions_enabled":True,
            "recovery_enabled":True,"recovery_loss_trigger":20,
            "recovery_profit_target":4.0,**changes}}

    def test_exact_net_overrides_legacy_profit_plus_swap(self):
        d=recovery_intent(self.snapshot(net=-21),{"decision":"WAIT"},self.settings())
        self.assertTrue(d["armed"])
        self.assertEqual(d["lot_cap"],0.09)
        self.assertEqual(d["account_net"],-21)
        self.assertEqual(d["profit_target"],4.0)

    def test_no_recovery_below_account_threshold(self):
        d=recovery_intent(self.snapshot(net=-19),{"decision":"WAIT"},self.settings())
        self.assertFalse(d["armed"])

    def test_recovery_claim_ready_even_when_new_entry_side_opposes(self):
        snap=self.snapshot(net=-23)
        now=datetime.now(timezone.utc)
        decision={"decision":"SELL","bar_time":int((now-timedelta(seconds=60)).timestamp()),
                  "confidence":0.1,"entry_atr":3}
        report=_allowed("owner",snap,decision,{},{"approved":False,"votes":{}},
                        {"approved":True},self.settings(),now,council_advisory=True)
        self.assertTrue(report["execution_ready"],report)
        self.assertEqual(report["action"],"RECOVERY_PROPOSAL",report)
        self.assertEqual(report["side"],"BUY",report)
        self.assertEqual(report["lot_cap"],0.09,report)

    def test_existing_recovery_is_not_reopened(self):
        positions=[{"ticket":"1","symbol":"XAUUSD","type":0,"volume":0.01,
                    "profit":-30,"comment":"ALZOOL-RECOVERY"}]
        result=recovery_intent(self.snapshot(positions,net=-30),
                               {"decision":"BUY"},self.settings())
        self.assertTrue(result["armed"])
        self.assertEqual(result["action"],"HOLD")

    def test_nonready_or_disabled_recovery_not_proposed(self):
        a=recovery_intent(self.snapshot(net=-25),{"decision":"BUY"},self.settings(recovery_enabled=False))
        self.assertFalse(a["armed"])

    def test_ea_has_owner_target_and_persistent_group_close_latch(self):
        path=Path(__file__).resolve().parents[1]/"mt5_client"/"ALZOOL_Central_Client.mq5"
        source=path.read_text(encoding="utf-8")
        self.assertIn('JsonDouble(response,"recovery_profit_target",0.0)',source)
        self.assertIn('if(net>=g_recoveryProfitTarget)',source)
        self.assertIn('g_groupClosePending=true;',source)
        self.assertIn('GlobalVariableSet(g_groupCloseKey,1.0);',source)
        self.assertIn('RecoveryCloseAll();',source)


if __name__=="__main__":
    unittest.main()
