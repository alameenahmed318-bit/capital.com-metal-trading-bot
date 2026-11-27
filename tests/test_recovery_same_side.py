import sys,unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace/backend"))
import recovery_logic as r
import central_action_protocol as c

class SameSideTests(unittest.TestCase):
    def setup_case(self,typ):
        now=datetime.now(timezone.utc)
        snap={"symbol":"XAUUSD","positions":[{"symbol":"XAUUSD","type":typ,"volume":.01,"profit":-20}],"recovery_bidirectional_capable":True,"received_at":now}
        d={"master":{"mode":"MASTER"},"bar_time":int(now.timestamp())-60}
        settings={"trading_enabled":True,"position_management":{"mode":"AI","recovery_enabled":True,"recovery_loss_trigger":10,"central_actions_enabled":True}}
        return snap,d,settings,now
    def test_confirmation_only_receives_losing_side(self):
        for typ,side,opposite in ((0,"BUY","SELL"),(1,"SELL","BUY")):
            snap,d,s,_=self.setup_case(typ)
            with patch.object(r,"recovery_m1_confirmation",return_value={"approved":True,"side":opposite,"reason_ar":"test"}) as confirm:
                out=r.recovery_intent(snap,d,s)
                self.assertEqual(confirm.call_args.args[2],[side])
                self.assertEqual(out["action"],"HOLD")
            with patch.object(r,"recovery_m1_confirmation",return_value={"approved":True,"side":side,"reason_ar":"test"}):
                out=r.recovery_intent(snap,d,s)
                self.assertEqual(out["direction_mode"],"SAME_SIDE")
                self.assertEqual(out["action"],"RECOVERY_PROPOSAL")
                self.assertEqual(out["side"],side)
    def test_claim_guard_rejects_opposite_even_if_planner_wrong(self):
        for typ,side,opposite in ((0,"BUY","SELL"),(1,"SELL","BUY")):
            snap,d,s,now=self.setup_case(typ)
            with patch.object(c,"plan_account_actions",return_value={"actions":[{"action":"RECOVERY_PROPOSAL","side":opposite,"lot_cap":.03}]}):
                self.assertFalse(c._allowed("test",snap,d,{}, {},{"approved":True},s,now)["execution_ready"])
            with patch.object(c,"plan_account_actions",return_value={"actions":[{"action":"RECOVERY_PROPOSAL","side":side,"lot_cap":.03}]}):
                self.assertTrue(c._allowed("test",snap,d,{}, {},{"approved":True},s,now)["execution_ready"])
if __name__=="__main__":unittest.main()
