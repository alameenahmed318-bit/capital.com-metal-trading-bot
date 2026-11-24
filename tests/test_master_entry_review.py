"""Offline MASTER entry review; no network or trading."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from master_entry_review import review, apply_gate

BAR=1700000100
def model(name,direction="BUY",status="READY",used=True,points=.2):
    return {"model":name,"direction":direction,"status":status,"used":used,"points":points}
def decision(models,confidence=.85,buy=.9,sell=.2,trap=False):
    return {"decision":"BUY","bar_time":BAR,"confidence":confidence,
        "entry_reference_price":100,"entry_atr":2,
        "context":{"atr":2,"buy_quality":80,"buy_trap":trap},
        "master":{"models":models,"scores":{"BUY":buy,"SELL":sell}}}
def item(ask=100.1):
    return {"timeframe":"M1","tick":{"bid":100,"ask":ask}}
def check(d,age,snapshot=None,settings=None):
    return review(d,snapshot or item(),settings or {},BAR+60+age)
def pending():
    return model("DeepSeek","WAIT","AWAITING_LOCAL_VOTE",False,0)
class MasterReviewTests(unittest.TestCase):
    def test_collect_before_deadline(self):
        d=decision([model("Qwen"),pending()])
        self.assertEqual(check(d,5)["state"],"COLLECTING")
    def test_available_models_after_deadline(self):
        d=decision([model("Qwen"),pending()])
        self.assertTrue(check(d,20)["approved"])
    def test_fast_only_never_permitted(self):
        self.assertEqual(check(decision([model("Fast AI")]),25)["state"],"NO_INDEPENDENT_EVIDENCE")
    def test_disagreement_not_unanimity(self):
        d=decision([model("Qwen"),model("DeepSeek","SELL"),model("GPT-OSS 120B")])
        self.assertTrue(check(d,8)["approved"])
    def test_weak_and_trap(self):
        d=decision([model("Qwen","SELL"),model("DeepSeek","SELL"),model("GPT-OSS 120B","SELL")],.48,.51,.49,True)
        self.assertEqual(check(d,9)["state"],"WEAK_EVIDENCE")
    def test_price_guard_respects_owner_setting(self):
        d=decision([model("Qwen"),pending()])
        self.assertEqual(check(d,20,item(102))["state"],"PRICE_MOVED")
        self.assertTrue(check(d,20,item(102),{"position_management":{"entry_guard_enabled":False}})["approved"])
    def test_old_bar_never_permitted(self):
        self.assertEqual(check(decision([model("Qwen")]),60)["state"],"STALE_BAR")
    def test_missing_price(self):
        self.assertEqual(check(decision([model("Qwen")]),20,{"timeframe":"M1","tick":{}})["state"],"PRICE_UNAVAILABLE")
    def test_rejection_preserved(self):
        gate={"approved":False,"reason_ar":"risk"}
        self.assertIs(apply_gate(gate,{"approved":True}),gate)
    def test_revokes_authorization(self):
        gate={"approved":True,"authorization_id":"AZ-1","expires_at_unix":99}
        r=apply_gate(gate,{"approved":False,"reason_ar":"review"})
        self.assertFalse(r["approved"]);self.assertIsNone(r["authorization_id"]);self.assertEqual(r["expires_at_unix"],0)
if __name__=="__main__":unittest.main()
