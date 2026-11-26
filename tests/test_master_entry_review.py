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

class LocalContinuationTests(unittest.TestCase):
    def check_local(self,side="BUY",symbol="XAUUSD",age=7,forecast=-.10,
                    pressure=.8,quality=90,move=.15,trap=False,cap=.5,eligible=True,
                    family_count=6,language_opposes=False):
        sign=1 if side=="BUY" else -1
        other="SELL" if side=="BUY" else "BUY"
        names=("LightGBM","CatBoost","Chronos-2")
        d=decision([model(n,other,points=.002) for n in names]+[pending()],confidence=.98)
        d.update(decision=side,symbol=symbol,strategy_id=12,combination_id=21,
            directional_candidates={side:{"strategy_id":12,"combination_id":21}} if eligible else {},
            directional_scores={side:1.,other:.02},
            family_votes={str(i):side for i in range(family_count)})
        d["context"].update({side.lower()+"_quality":quality,side.lower()+"_trap":trap,
            "last_closed_candle_pressure":sign*pressure})
        now=BAR+60+age
        for row in d["master"]["models"]:
            if row["model"] in names:row["confidence"]=abs(forecast)
        if language_opposes:
            row=model("GPT-OSS 120B",other,points=.3);row["confidence"]=.9
            d["master"]["models"].append(row)
        d["master"]["scores"]={side:1.2,other:.02}
        d["master"]["arena_shadow"]={"status":"SHADOW_READY","symbol":symbol,"bar_time":BAR,
            "received_at":now-1,"models":{n:-sign*abs(forecast) for n in names}}
        d["master"]["arena_advisory"]={"used":list(names)}
        mid=100+sign*move*2
        snap={"symbol":symbol,"timeframe":"M1","tick":{"bid":mid-.05,"ask":mid+.05}}
        return review(d,snap,{"position_management":{"entry_max_deviation_atr":cap}},now,live_arena=True)
    def test_strong_local_m1_route_both_directions_and_markets(self):
        for side in ("BUY","SELL"):
            for symbol in ("XAUUSD","BTCUSDm"):
                r=self.check_local(side=side,symbol=symbol)
                self.assertTrue(r["approved"])
                self.assertEqual(r["route"],"LOCAL_PRICE_CONFIRMED")
                self.assertEqual(r["independent_support"] if "independent_support" in r else None,None)
    def test_weak_conflicting_and_stale_cases(self):
        for kw in ({"age":4},{"age":60},{"forecast":-.6},{"pressure":.1},{"quality":60},
                   {"move":-.1},{"trap":True},{"eligible":False},{"family_count":2},
                   {"language_opposes":True}):
            self.assertFalse(self.check_local(**kw)["approved"],kw)
    def test_local_price_cap_preserved(self):
        self.assertEqual(self.check_local(cap=.05)["state"],"PRICE_MOVED")

class UnifiedScoreTests(unittest.TestCase):
    def test_weak_forecasts_are_soft_and_strong_opposition_reduces_score(self):
        from master_entry_review import unified_score
        for side in ("BUY","SELL"):
            other="SELL" if side=="BUY" else "BUY"
            for sym in ("XAUUSD","BTCUSDm"):
                d=decision([],confidence=.99)
                d.update(decision=side,symbol=sym,strategy_id=12,combination_id=21,
                    directional_candidates={side:{"strategy_id":12,"combination_id":21}})
                d["context"].update({side.lower()+"_quality":65,side.lower()+"_trap":False})
                d["master"]["scores"]={side:1.0,other:0.}
                item={"symbol":sym,"timeframe":"M1","tick":{"bid":100.05,"ask":100.1}}
                votes=[{"model":n,"direction":other,"confidence":.10} for n in ("LightGBM","Chronos-2")]
                r=unified_score(d,item,side,10,votes,{})
                strong=unified_score(d,item,side,10,[{**v,"confidence":.95} for v in votes],{})
                self.assertGreater(r["score"],strong["score"])
                self.assertEqual(r["direction"],side)
                self.assertFalse(r["execution_authorized"])
                self.assertEqual(r["mode"],"SHADOW_ONLY")
                self.assertEqual(unified_score(d,item,side,10,[],{})["direction"],"WAIT")
                self.assertEqual(unified_score(d,item,side,60,votes,{})["direction"],"WAIT")
                self.assertEqual(unified_score(d,{**item,"symbol":"OTHER"},side,10,votes,{})["direction"],"WAIT")
                self.assertEqual(unified_score(d,item,side,10,votes,{"position_management":{"entry_max_deviation_atr":.01}})["direction"],"WAIT")
                d["context"][side.lower()+"_trap"]=True
                self.assertEqual(unified_score(d,item,side,10,votes,{})["direction"],"WAIT")

class EntryExecutionStatusTests(unittest.TestCase):
    def test_reports_broker_positions_not_signal(self):
        from datetime import datetime,timezone,timedelta
        from system_readiness import entry_execution_status
        now=datetime.now(timezone.utc)
        p={"symbol":"XAUUSD","type":0,"position_id":"123","time":1700000100,"comment":"ALZOOL-S3-C526"}
        item={"symbol":"XAUUSD","received_at":now,"positions":[],"action_entry_deals":[{**p,"entry":0}]}
        x=entry_execution_status(item,True,now)
        self.assertEqual(x["state"],"READY_NOT_CONFIRMED")
        self.assertFalse(x["confirmed"])
        self.assertFalse(x["last_entry"]["still_open"])
        x=entry_execution_status({**item,"positions":[p,{**p,"symbol":"BTCUSDm","type":1}]},False,now)
        self.assertEqual(x["state"],"OPEN")
        self.assertEqual((x["buy_count"],x["sell_count"],x["open_count"]),(1,0,1))
        self.assertTrue(x["last_entry"]["still_open"])
        self.assertEqual(x["last_entry"]["origin"],"ALZOOL_ENTRY")
        self.assertEqual(entry_execution_status(item,False,now)["state"],"NO_OPEN_POSITION")
        self.assertEqual(entry_execution_status({**item,"received_at":now-timedelta(seconds=31)},True,now)["state"],"UNKNOWN")
        self.assertIsNone(entry_execution_status(None,True,now)["open_count"])

class DirectDemoReviewTests(unittest.TestCase):
    def test_direct_demo_without_models_both_markets_and_sides(self):
        from master_entry_review import DIRECT_DEMO_VERSION
        for sym in ("XAUUSD","BTCUSDm"):
            for side in ("BUY","SELL"):
                other="SELL" if side=="BUY" else "BUY"
                d=decision([pending()],confidence=.99)
                d.update(symbol=sym,decision=side,strategy_id=12,combination_id=21,
                    directional_candidates={side:{"strategy_id":12,"combination_id":21}},
                    directional_scores={side:.98,other:.02})
                d["context"].update({side.lower()+"_quality":80,side.lower()+"_trap":False})
                snap={"timeframe":"M1","symbol":sym,"account":{"trade_mode":"DEMO"},
                    "master_direct_demo_capable":True,"tick":{"bid":99.95,"ask":100.05}}
                r=review(d,snap,{},BAR+61,live_arena=True)
                self.assertTrue(r["approved"])
                self.assertEqual(r["policy"],DIRECT_DEMO_VERSION)
                self.assertEqual(r["collection_limit_sec"],0)
                for mode in ("REAL","CONTEST","UNKNOWN"):
                    old=review(d,{**snap,"account":{"trade_mode":mode}},{},BAR+61,live_arena=True)
                    self.assertFalse(old["approved"])
                    self.assertNotEqual(old["policy"],DIRECT_DEMO_VERSION)
                self.assertFalse(review(d,{**snap,"master_direct_demo_capable":False},{},BAR+61,live_arena=True)["approved"])
                self.assertFalse(review(d,{**snap,"symbol":"OTHER"},{},BAR+61,live_arena=True)["approved"])
                self.assertFalse(review(d,snap,{},BAR+120,live_arena=True)["approved"])
                self.assertFalse(review(d,snap,{"position_management":{"entry_max_deviation_atr":.001}},BAR+61,live_arena=True)["approved"])
                weak={**d,"directional_scores":{side:.2,other:.19}}
                self.assertFalse(review(weak,snap,{},BAR+61,live_arena=True)["approved"])
                blocked={**d,"context":{**d["context"],side.lower()+"_trap":True}}
                self.assertFalse(review(blocked,snap,{},BAR+61,live_arena=True)["approved"])
                self.assertFalse(apply_gate({"approved":False,"reason_ar":"owner control"},r)["approved"])
    def test_native_demo_guard_in_client(self):
        path=Path(__file__).resolve().parents[1]/"mt5_client"/"ALZOOL_Multi_Market_Client_V305.mq5"
        source=path.read_text(encoding="utf-8")
        self.assertIn('AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO',source)
        self.assertIn('JsonBool(response,"master_direct_demo")',source)
        self.assertIn('master_direct_demo_capable',source)

class BalancedDemoTests(unittest.TestCase):
    def fixture(self,symbol="XAUUSD",forecasts=None):
        d=decision([],confidence=.99)
        d.update(symbol=symbol,strategy_id=12,combination_id=21,
            directional_candidates={"BUY":{"strategy_id":12,"combination_id":21},"SELL":{"strategy_id":13,"combination_id":22}},
            directional_scores={"BUY":.98,"SELL":.02})
        d["context"].update(buy_quality=80,sell_quality=80,buy_trap=False,sell_trap=False)
        d["master"]["arena_shadow"]={"status":"SHADOW_READY","symbol":symbol,"bar_time":BAR,
            "received_at":BAR+61,"models":forecasts or {}}
        snap={"symbol":symbol,"timeframe":"M1","account":{"trade_mode":"DEMO"},
            "master_direct_demo_capable":True,"tick":{"bid":100.3,"ask":100.4}}
        return d,snap
    def test_seven_models_have_60_percent_budget_and_missing_block(self):
        from master_balanced import evaluate,MODELS,select_demo
        d,snap=self.fixture(forecasts={n:1. for n in MODELS})
        t=evaluate(d,snap,{},BAR+62)
        self.assertAlmostEqual(sum(t["weights"].values()),1.)
        self.assertEqual(t["allocation"],{"strategy":.25,"seven_models":.60,"market":.15})
        self.assertTrue(t["approved"])
        self.assertEqual(t["direction"],"BUY")
        selected=select_demo(d,snap,{},BAR+62)
        self.assertTrue(review(selected,snap,{},BAR+62,live_arena=True)["approved"])
        d["master"]["arena_shadow"]["models"]={n:-1. for n in MODELS}
        against=evaluate(d,snap,{},BAR+62)
        self.assertAlmostEqual(t["scores"]["BUY"]["score"]-against["scores"]["BUY"]["score"],.60,places=7)
        self.assertFalse(against["scores"]["BUY"]["eligible"])
        self.assertEqual(against["model_direction"],"SELL")
        d["master"]["arena_shadow"]["models"]={}
        neutral=evaluate(d,snap,{},BAR+62)
        self.assertEqual(neutral["scores"]["BUY"]["components"]["models"],0.)
        self.assertFalse(neutral["approved"])
        self.assertEqual(neutral["reason"],"NO_FRESH_AI_EVIDENCE")
        self.assertFalse(review(select_demo(d,snap,{},BAR+62),snap,{},BAR+62,live_arena=True)["approved"])
        self.assertEqual(evaluate(d,snap,{},BAR+62,legacy=True)["scores"]["BUY"]["components"]["models"],.5)
        d["master"]["arena_shadow"]["models"]={n:1. for n in MODELS}
        d["master"]["arena_shadow"]["bar_time"]=BAR-60
        self.assertEqual(evaluate(d,snap,{},BAR+62)["used_models"],[])
    def test_one_material_model_no_wait_both_markets(self):
        from master_balanced import evaluate,select_demo
        for sym in ("XAUUSD","BTCUSDm"):
            for sign in (1.,-1.):
                d,snap=self.fixture(sym,{"LightGBM":sign})
                side="BUY" if sign>0 else "SELL"
                other="SELL" if sign>0 else "BUY"
                d["directional_scores"]={side:.98,other:.02}
                snap["tick"]={"bid":100.3,"ask":100.4} if sign>0 else {"bid":99.6,"ask":99.7}
                t=evaluate(d,snap,{},BAR+62)
                self.assertEqual(t["model_direction"],side)
                self.assertEqual(t["used_models"],["LightGBM"])
                self.assertTrue(t["approved"])
                self.assertTrue(review(select_demo(d,snap,{},BAR+62),snap,{},BAR+62,live_arena=True)["approved"])
                d["master"]["arena_shadow"]["models"]={"LightGBM":.03}
                self.assertFalse(evaluate(d,snap,{},BAR+62)["approved"])
                d["master"]["arena_shadow"]["models"]={"LightGBM":.8,"XGBoost":-.8}
                self.assertEqual(evaluate(d,snap,{},BAR+62)["reason"],"AI_DIRECTION_UNCLEAR")
                d["master"]["arena_shadow"]["models"]={"LightGBM":sign}
                del d["directional_candidates"][side]
                self.assertEqual(evaluate(d,snap,{},BAR+62)["reason"],"AI_SIDE_NOT_ELIGIBLE")
    def test_direct_timing_is_shadow_only(self):
        from master_balanced import evaluate,select_demo,direct_timing_shadow,MODELS
        for sym in ("XAUUSD","BTCUSDm"):
            for sign in (1.,-1.):
                side="BUY" if sign>0 else "SELL"
                d,snap=self.fixture(sym,{n:sign for n in MODELS})
                snap["tick"]={"bid":100.3,"ask":100.4} if sign>0 else {"bid":99.6,"ask":99.7}
                del d["directional_candidates"][side]
                t=evaluate(d,snap,{},BAR+62)
                x=direct_timing_shadow(t)
                self.assertEqual(x["direction"],side)
                self.assertTrue(x["additional_signal"])
                self.assertFalse(x["execution_authorized"])
                self.assertNotIn("strategy_id",x)
                actual=select_demo(d,snap,{},BAR+62)
                self.assertEqual(actual["decision"],"WAIT")
                rv=review(actual,snap,{},BAR+62,live_arena=True)
                self.assertFalse(rv["approved"])
                self.assertEqual(rv["direct_timing_shadow"]["direction"],side)
                self.assertEqual(direct_timing_shadow(evaluate(d,snap,{},BAR+120))["direction"],"WAIT")
                self.assertEqual(direct_timing_shadow(evaluate(d,snap,{"position_management":{"entry_max_deviation_atr":.01}},BAR+62))["direction"],"WAIT")
                d["context"][side.lower()+"_trap"]=True
                self.assertEqual(direct_timing_shadow(evaluate(d,snap,{},BAR+62))["direction"],"WAIT")
                d["context"][side.lower()+"_trap"]=False
                d["master"]["arena_shadow"]["models"]={}
                self.assertEqual(direct_timing_shadow(evaluate(d,snap,{},BAR+62))["direction"],"WAIT")
    def test_rank_both_eligible_and_never_model_only(self):
        from master_balanced import evaluate,MODELS
        d,snap=self.fixture(forecasts={n:-1. for n in MODELS})
        d["directional_scores"]={"BUY":.8,"SELL":.8}
        snap["tick"]={"bid":99.4,"ask":99.5}
        t=evaluate(d,snap,{},BAR+62)
        self.assertEqual(t["selected"],"SELL")
        self.assertGreater(t["scores"]["SELL"]["score"],t["scores"]["BUY"]["score"])
        del d["directional_candidates"]["SELL"]
        self.assertNotEqual(evaluate(d,snap,{},BAR+62)["direction"],"SELL")
    def test_weight_learning_requires_market_scope_and_chronological_samples(self):
        from master_balanced import weight_profile
        now=BAR+62
        profile={"symbol":"XAUUSD","master_adaptive":{"updated_at_unix":now,
            "model_profiles":{"LightGBM":{"train":80,"validation":40,"train_mean":.1,"validation_mean":.1,"status":"POSITIVE_BOTH"},
                "XGBoost":{"train":79,"validation":40,"train_mean":.1,"validation_mean":.1,"status":"POSITIVE_BOTH"}}}}
        good=weight_profile(profile,"XAUUSD",now)
        self.assertEqual(good["factors"]["LightGBM"],1.2)
        self.assertEqual(good["factors"]["XGBoost"],1.)
        self.assertEqual(weight_profile(profile,"BTCUSDm",now)["factors"]["LightGBM"],1.)
        self.assertEqual(weight_profile(profile,"XAUUSD",now+1201)["factors"]["LightGBM"],1.)
    def test_gold_early_demo_68_is_explicit_scoped_and_rechecked(self):
        from master_balanced import evaluate, MODELS, select_demo
        d,snap=self.fixture(forecasts={n:1. for n in MODELS})
        d["directional_scores"]={"BUY":.4,"SELL":.4}
        d["context"]["buy_quality"]=20
        snap["tick"]={"bid":99.99,"ask":100.01}
        now=BAR+65
        base=evaluate(d,snap,{},now)
        self.assertGreaterEqual(base["scores"]["BUY"]["score"],.68)
        self.assertLess(base["scores"]["BUY"]["score"],.72)
        self.assertFalse(base["approved"])
        pilot=evaluate(d,snap,{},now,early_demo=True)
        self.assertTrue(pilot["approved"])
        self.assertEqual(pilot["threshold"],.68)
        selected=select_demo(d,snap,{},now)
        r=review(selected,snap,{},now,live_arena=True)
        self.assertTrue(r["approved"])
        self.assertEqual(r["quality_threshold"],.68)
        self.assertFalse(review(selected,snap,{},BAR+71,live_arena=True)["approved"])
        self.assertFalse(apply_gate({"approved":False,"reason_ar":"risk"},r)["approved"])
        for mode in ("REAL","CONTEST","UNKNOWN"):
            x=evaluate(d,{**snap,"account":{"trade_mode":mode}},{},now,early_demo=True)
            self.assertEqual(x["threshold"],.72)
        for patch in ({"master_direct_demo_capable":False},{"timeframe":"M5"}):
            self.assertEqual(evaluate(d,{**snap,**patch},{},now,early_demo=True)["threshold"],.72)
        btc,bs=self.fixture("BTCUSDm",{n:1. for n in MODELS})
        self.assertEqual(evaluate(btc,bs,{},now,early_demo=True)["threshold"],.72)
        self.assertEqual(evaluate(d,snap,{},now,legacy=True,early_demo=True)["threshold"],.72)
        d["context"]["buy_trap"]=True
        self.assertFalse(evaluate(d,snap,{},now,early_demo=True)["approved"])
        d["context"]["buy_trap"]=False
        d["master"]["arena_shadow"]["models"]={}
        self.assertFalse(evaluate(d,snap,{},now,early_demo=True)["approved"])

    def test_real_or_old_client_and_existing_gate_preserved(self):
        from master_balanced import select_demo,MODELS
        d,snap=self.fixture(forecasts={n:1. for n in MODELS})
        for mode in ("REAL","UNKNOWN","CONTEST"):
            self.assertIs(select_demo(d,{**snap,"account":{"trade_mode":mode}},{},BAR+62),d)
        self.assertIs(select_demo(d,{**snap,"master_direct_demo_capable":False},{},BAR+62),d)
        trial=select_demo(d,snap,{},BAR+62)
        self.assertFalse(apply_gate({"approved":False,"reason_ar":"owner"},review(trial,snap,{},BAR+62,live_arena=True))["approved"])
        self.assertFalse(review(trial,snap,{},BAR+120,live_arena=True)["approved"])

if __name__=="__main__":unittest.main()
