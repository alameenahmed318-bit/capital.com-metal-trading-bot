"""Regression checks for historical diagnostics and version rollback."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from strategy_experiments import sanitize_profile, replay_directional
from strategy_versions import activate

class FakeStore:
    def __init__(self):
        self.rows=[
            {"id":1,"client_id":"owned","status":"ACTIVE","profile":{"strategy_weights":{}}},
            {"id":2,"client_id":"owned","status":"CANDIDATE","profile":{"strategy_weights":{"1":1.1}}},
            {"id":3,"client_id":"other","status":"CANDIDATE","profile":{"strategy_weights":{}}}
        ]
    def __call__(self,method,path,payload=None,admin=False,prefer=None):
        from urllib.parse import urlparse,parse_qs
        q=parse_qs(urlparse(path).query)
        rows=self.rows
        for key,v in q.items():
            if key in ("id","client_id","status") and v[0].startswith("eq."):
                target=v[0][3:]
                rows=[r for r in rows if str(r[key])==target]
        if method=="GET":return [dict(x) for x in rows]
        if method=="PATCH":
            for x in rows:x.update(payload)
            return [dict(x) for x in rows]
        raise ValueError("unexpected method")

class ExperimentTests(unittest.TestCase):
    def test_reject_overweight_candidate(self):
        with self.assertRaises(ValueError):
            sanitize_profile({"1":10.0})
    def test_reject_invalid_weight_keys(self):
        with self.assertRaises(ValueError):
            sanitize_profile({"201":1.0})
    def test_empty_history_has_no_backtest_signal(self):
        result=replay_directional([],{"strategy_weights":{}},max_samples=3)
        self.assertEqual(result["samples"],0)
        self.assertEqual(result["signals"],0)
    def test_switch_and_rollback_restores_prior_profile(self):
        fake=FakeStore()
        result=activate("owned",2,fake)
        self.assertEqual(result["previous_id"],1)
        self.assertEqual(next(r for r in fake.rows if r["id"]==2)["status"],"ACTIVE")
        self.assertEqual(next(r for r in fake.rows if r["id"]==1)["status"],"ARCHIVED")
        activate("owned",1,fake)
        self.assertEqual(next(r for r in fake.rows if r["id"]==1)["status"],"ACTIVE")
    def test_owner_cannot_activate_another_account_version(self):
        with self.assertRaises(ValueError):activate("owned",3,FakeStore())

if __name__=="__main__":
    unittest.main()
