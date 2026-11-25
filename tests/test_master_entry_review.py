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
class LiveArenaReviewTests(unittest.TestCase):
    def run_review(self,names=("LightGBM","TiRex-2"),age=10,side="BUY",fresh=True,symbol="XAUUSD",ask=100.1,trap=False):
        d=decision([model(n,side,points=.018) for n in names]+[pending()],confidence=.98,buy=1.2,sell=.05,trap=trap)
        d["symbol"]="XAUUSD"
        d["context"]["last_closed_candle_pressure"]=.8
        now=BAR+60+age
        d["master"]["arena_shadow"]={"status":"SHADOW_READY","symbol":symbol,"bar_time":BAR if fresh else BAR-60,
            "received_at":now-1,"models":{n:.9 if side=="BUY" else -.9 for n in names}}
        d["master"]["arena_advisory"]={"used":list(names)}
        return review(d,{**item(ask),"symbol":"XAUUSD"},{},now,live_arena=True)
    def test_two_families_approve_without_legacy(self):
        r=self.run_review(age=5)
        self.assertTrue(r["approved"])
        self.assertEqual(r["policy"],"MASTER_REVIEW_V2")
        self.assertEqual(r["supporting_families"],2)
        self.assertTrue(r["legacy_pending_optional"])
    def test_no_instant_early_entry(self):
        self.assertEqual(self.run_review(age=2)["state"],"COLLECTING")
    def test_exceptional_three_families_only_at_two_seconds(self):
        d=decision([model("LightGBM",points=.02),model("TiRex-2",points=.02),
                    model("GPT-OSS 120B",points=.3),pending()],confidence=.99,buy=1.5,sell=.01)
        d["symbol"]="XAUUSD";d["context"]["last_closed_candle_pressure"]=.8
        def at(age,ask=100.1):
            now=BAR+60+age
            d["master"]["arena_shadow"]={"status":"SHADOW_READY","symbol":"XAUUSD","bar_time":BAR,
                "received_at":now-1,"models":{"LightGBM":.9,"TiRex-2":.9}}
            d["master"]["arena_advisory"]={"used":["LightGBM","TiRex-2"]}
            return review(d,{**item(ask),"symbol":"XAUUSD"},{},now,live_arena=True)
        self.assertEqual(at(1)["state"],"COLLECTING")
        self.assertTrue(at(2)["approved"])
        self.assertTrue(at(2)["exceptional_early_candidate"])
        self.assertEqual(at(2,103)["state"],"PRICE_MOVED")
        d["context"]["last_closed_candle_pressure"]=.3
        self.assertEqual(at(2)["state"],"COLLECTING")
        d["context"]["buy_trap"]=True
        self.assertEqual(at(20)["state"],"WEAK_EVIDENCE")
    def test_early_legacy_candidate_requires_directional_quality(self):
        d=decision([model("GPT-OSS 120B"),pending()],confidence=.95,buy=1.2,sell=.1)
        self.assertEqual(review(d,item(),{},BAR+62,live_arena=True)["state"],"COLLECTING")
        self.assertTrue(review(d,item(),{},BAR+65,live_arena=True)["approved"])
        d["master"]["models"][0]["direction"]="SELL"
        self.assertEqual(review(d,item(),{},BAR+65,live_arena=True)["state"],"COLLECTING")
    def test_correlated_models_are_still_one_family(self):
        names=("LightGBM","XGBoost","CatBoost")
        self.assertEqual(self.run_review(names=names,age=7)["state"],"COLLECTING")
        r=self.run_review(names=names,age=8)
        self.assertTrue(r["approved"])
        self.assertEqual(r["supporting_families"],1)
        self.assertTrue(r["early_single_candidate"])
        self.assertEqual(self.run_review(names=names,age=8,ask=103)["state"],"PRICE_MOVED")
    def test_ordinary_single_family_retains_fifteen_second_lane(self):
        d=decision([model("LightGBM",points=.018),pending()],confidence=.80,buy=1.2,sell=.05)
        d["symbol"]="XAUUSD";d["context"]["last_closed_candle_pressure"]=.20
        def at(age):
            now=BAR+60+age
            d["master"]["arena_shadow"]={"status":"SHADOW_READY","symbol":"XAUUSD","bar_time":BAR,
                "received_at":now-1,"models":{"LightGBM":.9}}
            d["master"]["arena_advisory"]={"used":["LightGBM"]}
            return review(d,{**item(),"symbol":"XAUUSD"},{},now,live_arena=True)
        self.assertEqual(at(10)["state"],"COLLECTING")
        self.assertFalse(at(10)["early_single_candidate"])
        self.assertTrue(at(15)["approved"])
    def test_opposing_trap_price_and_stale_rejected(self):
        self.assertEqual(self.run_review(side="SELL")["state"],"WEAK_EVIDENCE")
        self.assertEqual(self.run_review(trap=True)["state"],"WEAK_EVIDENCE")
        self.assertEqual(self.run_review(ask=103)["state"],"PRICE_MOVED")
        self.assertEqual(self.run_review(age=60)["state"],"STALE_BAR")
    def test_stale_or_cross_market_not_counted(self):
        self.assertEqual(self.run_review(fresh=False,age=25)["state"],"NO_INDEPENDENT_EVIDENCE")
        self.assertEqual(self.run_review(symbol="BTCUSD",age=25)["state"],"NO_INDEPENDENT_EVIDENCE")
    def test_fast_only_still_blocked(self):
        self.assertEqual(self.run_review(names=("Fast AI",),age=25)["state"],"NO_INDEPENDENT_EVIDENCE")

if __name__=="__main__":unittest.main()
