"""Offline checks of real-trade feedback, bounded live weighting and history."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from live_weight_overlay import apply_live_learning
from learning_memory import combine_deals,_refresh,_MEMORY,_LOCK


def learning(count=9,wins=6,losses=3,weight=1.18,combo=.94):
    return {"processed_positions":count,"closed_positions":count+4,
            "unmatched_positions":4,"coverage_percent":69.2,
            "last_closed_at":"2026-11-16T11:29:10+00:00",
            "last_attributed_at":"2026-11-16T11:29:10+00:00",
            "weights":{"11":weight},
            "combination_weights":{"99":combo},
            "strategies":[{"strategy_id":11,"trades":count,"wins":wins,"losses":losses,
                           "net":11.0,"weight":weight,
                           "evidence":"ELIGIBLE" if count>=8 else "INSUFFICIENT_TRADES"}],
            "combinations":[{"combination_id":99,"trades":count,
                             "wins":wins,"losses":losses,"net":-8.0,
                             "weight":combo,
                             "evidence":"ELIGIBLE" if count>=8 else "INSUFFICIENT_TRADES"}]}


class LearningTests(unittest.TestCase):
    def test_real_trades_are_applied_even_with_existing_active_version(self):
        base={"strategy_weights":{"11":1.1},"combination_weights":{"99":1.05},
              "version_label":"EXP_OWNER"}
        out=apply_live_learning(base,learning())
        self.assertEqual(out["diagnostics"]["status"],"APPLIED")
        self.assertGreater(out["strategy_weights"]["11"],1.1)
        self.assertLess(out["combination_weights"]["99"],1.05)
        self.assertTrue(out["diagnostics"]["active_version_preserved"])
        self.assertEqual(base["strategy_weights"]["11"],1.1)
        self.assertEqual(base["combination_weights"]["99"],1.05)
        self.assertEqual(out["diagnostics"]["applied_strategies"],1)
        self.assertEqual(out["diagnostics"]["applied_combinations"],1)

    def test_below_eight_closed_trades_does_not_overfit(self):
        out=apply_live_learning({},learning(count=7,wins=7,losses=0))
        self.assertEqual(out["diagnostics"]["status"],"INSUFFICIENT_EVIDENCE")
        self.assertEqual(out["strategy_weights"],{})
        self.assertEqual(out["combination_weights"],{})

    def test_live_weights_are_clamped_to_reviewed_owner_profile_ranges(self):
        data=learning(weight=99,combo=-42)
        base={"strategy_weights":{"11":1.5},"combination_weights":{"99":1.15}}
        out=apply_live_learning(base,data)
        self.assertGreaterEqual(out["strategy_weights"]["11"],.5)
        self.assertLessEqual(out["strategy_weights"]["11"],1.5)
        self.assertGreaterEqual(out["combination_weights"]["99"],.85)
        self.assertLessEqual(out["combination_weights"]["99"],1.15)

    def test_no_new_trades_does_not_change_existing_version(self):
        active={"strategy_weights":{"4":.9},"combination_weights":{"9":1.07}}
        out=apply_live_learning(active,{})
        self.assertEqual(out["strategy_weights"],active["strategy_weights"])
        self.assertEqual(out["combination_weights"],active["combination_weights"])
        self.assertEqual(out["diagnostics"]["applied_strategies"],0)

    def test_one_loss_does_not_claim_to_train_all_200_strategies(self):
        out=apply_live_learning({},{"weights":{"22":.5},
             "strategies":[{"strategy_id":22,"trades":1,
                            "evidence":"INSUFFICIENT_TRADES"}]})
        self.assertEqual(out["diagnostics"]["applied_strategies"],0)
        self.assertEqual(out["diagnostics"]["eligible_strategies"],0)

    def test_learning_changes_weights_only_not_trade_policy(self):
        active={"strategy_weights":{"11":1.0},"trading_enabled":False,
                "max_positions":2,"position_management":{"recovery_enabled":True}}
        out=apply_live_learning(active,learning())
        self.assertNotIn("trading_enabled",out)
        self.assertNotIn("position_management",out)
        self.assertFalse(out["diagnostics"]["risk_settings_changed"])

    def test_conservatively_merge_strategy_id_from_persisted_record(self):
        persisted=[{"ticket":"a1","strategy_id":12,"strategy_comment":"ALZOOL-S12-C34",
                    "position_id":"pos1","profit":1}]
        live=[{"ticket":"a1","strategy_id":0,"strategy_comment":"",
               "position_id":"pos1","profit":1}]
        out=combine_deals(persisted,live)
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]["strategy_id"],12)
        self.assertEqual(out[0]["strategy_comment"],"ALZOOL-S12-C34")

    def test_closed_deals_loader_paginates_beyond_one_thousand(self):
        ident="offline-test-learning-paged"
        calls=[]
        def fake_request(method,url,admin=False):
            self.assertTrue(admin)
            offset=int(url.split("offset=")[1].split("&")[0])
            limit=int(url.split("limit=")[1].split("&")[0])
            calls.append((offset,limit))
            total=1979
            return [{"payload":{"ticket":str(i),"position_id":str(i),
                                "time":1794828550,"entry":1,"strategy_id":11}}
                    for i in range(offset,min(offset+limit,total))]
        try:
            _refresh(ident,fake_request)
            with _LOCK:
                item=_MEMORY[ident]
            self.assertEqual(item["status"],"READY")
            self.assertEqual(item["count"],1979)
            self.assertEqual(calls,[(0,1000),(1000,1000)])
        finally:
            with _LOCK:_MEMORY.pop(ident,None)


if __name__=="__main__":
    unittest.main()
