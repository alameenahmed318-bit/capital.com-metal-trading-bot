"""V3 catalogs: uniqueness, market isolation, and nonactive research gate."""
import pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from strategy_engine import (STRATEGIES,COMBINATIONS,evaluate_market,
                             _REPLACEMENT_CATALOGS_V3)
from strategy_replacements_v3 import VERSION_V3,EXTRA_CANDIDATES
from test_strategy_replacements import rates

class V3CandidateTests(unittest.TestCase):
 def test_exact_library_size_unique_5_member_combos(self):
  for market,cat in _REPLACEMENT_CATALOGS_V3.items():
   self.assertFalse(cat["production_approved"])
   self.assertEqual(cat["version"],VERSION_V3)
   self.assertEqual(len(cat["strategies"]),200)
   self.assertEqual(len(cat["combinations"]),1000)
   self.assertEqual(len(set(tuple(c["members"]) for c in cat["combinations"])),1000)
   self.assertTrue(set(EXTRA_CANDIDATES[market]["strategies"]).issubset(cat["retired_strategy_ids"]))
 def test_new_catalog_never_mutates_v1(self):
  self.assertFalse(any(x.get("version")==VERSION_V3 for x in STRATEGIES))
  self.assertFalse(any(x.get("version")==VERSION_V3 for x in COMBINATIONS))
 def test_default_off_and_on_only_by_explicit_argument(self):
  data=rates()
  for symbol in ("BTCUSDm","XAUUSD"):
   old=evaluate_market(data,market_symbol=symbol)
   nextgen=evaluate_market(data,market_symbol=symbol,replacement_candidates="V3")
   self.assertNotIn("catalog_version",old)
   self.assertEqual(nextgen["catalog_version"],VERSION_V3)
   self.assertEqual(len(nextgen["votes"]),200)
   self.assertEqual(len(nextgen["combination_votes"]),1000)
   self.assertTrue(any(v.get("catalog_version")==VERSION_V3 for v in nextgen["votes"]))

if __name__=="__main__":unittest.main()
