"""Replacement catalog safety checks: no market bleed or live auto-activation."""
import pathlib
import sys
import unittest
import math

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from strategy_engine import (STRATEGIES,COMBINATIONS,evaluate_market,_REPLACEMENT_CATALOGS)
from strategy_replacements import VERSION,build_candidates,MARKET_DEFINITIONS

def rates(n=140):
    last=90000.
    out=[]
    for i in range(n):
        price=90000+i*8+math.sin(i/3)*70
        out.append({"time":1700000000+i*60,"open":last,
                    "high":max(last,price)+25,"low":min(last,price)-20,
                    "close":price,"tick_volume":100+(i%9)*11})
        last=price
    return out

class StrategyReplacementSafetyTests(unittest.TestCase):
    def test_candidate_counts_unique_members_and_families(self):
        self.assertEqual(set(_REPLACEMENT_CATALOGS),{"BTCUSD","XAUUSD"})
        for key,cat in _REPLACEMENT_CATALOGS.items():
            self.assertEqual(len(cat["strategies"]),200)
            self.assertEqual(len(cat["combinations"]),1000)
            self.assertEqual(len({c["members"] for c in cat["combinations"]}),1000)
            self.assertFalse(cat["production_approved"])
            self.assertEqual(cat["version"],VERSION)
            self.assertTrue(cat["retired_strategy_ids"])
            self.assertTrue(cat["retired_combo_ids"])
    def test_original_catalog_is_unchanged(self):
        self.assertEqual(len(STRATEGIES),200)
        self.assertEqual(len(COMBINATIONS),1000)
        self.assertFalse(any(s.get("version")==VERSION for s in STRATEGIES))
        self.assertFalse(any(c.get("version")==VERSION for c in COMBINATIONS))
    def test_market_isolation(self):
        btc=_REPLACEMENT_CATALOGS["BTCUSD"]
        gold=_REPLACEMENT_CATALOGS["XAUUSD"]
        self.assertNotEqual(btc["strategies"][11]["name"],gold["strategies"][11]["name"])
        self.assertNotEqual(btc["combinations"][0]["members"],gold["combinations"][0]["members"])
        self.assertEqual(gold["strategies"][67]["name"],STRATEGIES[67]["name"])
        self.assertEqual(btc["strategies"][67]["name"],STRATEGIES[67]["name"])
    def test_live_default_stays_legacy_and_research_is_versioned(self):
        import os
        previous=os.environ.pop("ALZOOL_REPLACEMENTS_V2",None)
        try:
            x=rates()
            base=evaluate_market(x,market_symbol="BTCUSDm")
            candidate=evaluate_market(x,market_symbol="BTCUSDm",replacement_candidates=True)
            candidate_gold=evaluate_market(x,market_symbol="XAUUSD",replacement_candidates=True)
            self.assertEqual(base["catalog_version"],"LEGACY_V1")
            self.assertEqual(candidate["catalog_version"],VERSION)
            self.assertEqual(candidate_gold["catalog_version"],VERSION)
            self.assertEqual(len(candidate["votes"]),200)
            self.assertEqual(len(candidate["combination_votes"]),1000)
            self.assertEqual(base["votes"][11]["catalog_version"],"LEGACY_V1")
            self.assertEqual(candidate["votes"][11]["catalog_version"],VERSION)
            self.assertEqual(candidate_gold["votes"][20]["catalog_version"],VERSION)
            self.assertEqual(candidate["votes"][67]["catalog_version"],"LEGACY_V1")
        finally:
            if previous is not None:os.environ["ALZOOL_REPLACEMENTS_V2"]=previous
    def test_five_candle_patterns_do_not_look_ahead_or_crash(self):
        x=rates()
        for key in ("BTCUSD","XAUUSD"):
            a=evaluate_market(x,market_symbol=key,replacement_candidates=True)
            b=evaluate_market([dict(c) for c in x],market_symbol=key,replacement_candidates=True)
            self.assertEqual([v["signal"] for v in a["votes"]],[v["signal"] for v in b["votes"]])
            self.assertEqual(a["strategy_count"],200)
            self.assertEqual(a["combination_count"],1000)

if __name__=="__main__":unittest.main()
