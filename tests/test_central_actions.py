"""Central trading action safeguards: opt-in, risk, freshness and replay."""
import sys
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from central_action_protocol import propose,claim,_PENDING

class OneTimeDB:
    def __init__(self):self.used=set()
    def __call__(self,method,path,payload=None,admin=False,prefer=None):
        if method!="POST":raise AssertionError("Unexpected DB access")
        key=payload["event_id"]
        if key in self.used:return []
        self.used.add(key)
        return [payload]

def sample():
    now=datetime.now(timezone.utc)
    snapshot={"symbol":"XAUUSD","received_at":now,
        "account":{"login":"123","balance":1000,"equity":1000,"free_margin":800},
        "positions":[{"ticket":"1","symbol":"XAUUSD","type":0,
            "volume":.01,"open_price":2450,"comment":"ALZOOL-S1","profit":5}],
        "tick":{"bid":2500,"ask":2500.5}}
    decision={"decision":"BUY","confidence":.7,"entry_atr":5,
        "context":{"atr":5,"atr_ratio":1},"bar_time":int(now.timestamp())-65}
    settings={"trading_enabled":True,"position_management":{
        "central_actions_enabled":True,"mode":"AI","addon_enabled":True,
        "addon_lot":.01,"addon_max":3,"addon_max_lot":.03,
        "max_total_lots":.1}}
    return snapshot,decision,settings,now

class CentralActionTests(unittest.TestCase):
    def setUp(self):_PENDING.clear()
    def test_default_off(self):
        snapshot,decision,settings,now=sample()
        settings["position_management"]["central_actions_enabled"]=False
        result=propose("test",snapshot,decision,{"approved":True},
            {"approved":True},{"approved":True},settings,now)
        self.assertFalse(result["execution_ready"])
    def test_stale_market_rejected(self):
        snapshot,decision,settings,now=sample()
        snapshot["received_at"]=now-timedelta(seconds=35)
        result=propose("test",snapshot,decision,{"approved":True},
            {"approved":True},{"approved":True},settings,now)
        self.assertFalse(result["execution_ready"])
    def test_council_veto_rejected(self):
        snapshot,decision,settings,now=sample()
        result=propose("test",snapshot,decision,{"approved":True},
            {"approved":False},{"approved":True},settings,now)
        self.assertFalse(result["execution_ready"])
    def test_single_claim_and_durable_replay_prevention(self):
        snapshot,decision,settings,now=sample()
        gate,council,risk={"approved":True},{"approved":True},{"approved":True}
        proposal=propose("test",snapshot,decision,gate,council,risk,settings,now)
        self.assertTrue(proposal["execution_ready"])
        db=OneTimeDB()
        result=claim("test",proposal["authorization_id"],snapshot,decision,
            gate,council,risk,settings,db,now)
        self.assertTrue(result["execution_authorized"])
        denied=claim("test",proposal["authorization_id"],snapshot,decision,
            gate,council,risk,settings,db,now)
        self.assertFalse(denied["execution_authorized"])
        second=propose("test",snapshot,decision,gate,council,risk,settings,now)
        rejected=claim("test",second["authorization_id"],snapshot,decision,
            gate,council,risk,settings,db,now)
        self.assertFalse(rejected["execution_authorized"])
    def test_account_snapshot_mismatch_blocks_claim(self):
        snapshot,decision,settings,now=sample()
        gate,council,risk={"approved":True},{"approved":True},{"approved":True}
        proposal=propose("test",snapshot,decision,gate,council,risk,settings,now)
        snapshot["positions"][0]["volume"]=.02
        denied=claim("test",proposal["authorization_id"],snapshot,decision,
            gate,council,risk,settings,OneTimeDB(),now)
        self.assertFalse(denied["execution_authorized"])

if __name__=="__main__":unittest.main()
