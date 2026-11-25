import sys
import unittest
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
import recovery_basket_memory as m
import master_v3

T=1700000100
SYMBOL="XAUUSD"
def deal(ticket,pid,entry,side,volume,profit=0,stamp=T,comment="",symbol=SYMBOL):
    return {"ticket":ticket,"position_id":pid,"entry":entry,"type":side,
            "volume":volume,"profit":profit,"commission":0,"swap":0,"fee":0,
            "time":stamp,"symbol":symbol,"comment":comment}

def claims():
    return [{"payload":{"symbol":SYMBOL,"action":"RECOVERY_PROPOSAL","side":"BUY","lot_cap":.03,
            "claimed_at":datetime.fromtimestamp(T,timezone.utc).isoformat(),
            "basket_members":[{"position_id":"101","volume":.01,"side":"SELL"}]}}]
def deals():
    return [deal("a","101",0,1,.01,stamp=T-100),deal("b","102",0,0,.03,stamp=T+1,comment="ALZOOL-RECOVERY"),
            deal("c","101",1,0,.01,-7,stamp=T+60),deal("d","102",1,1,.03,12,stamp=T+60)]
class BasketSpeedTests(unittest.TestCase):
    def test_missing_stable_identifier(self):
        self.assertEqual(m.claim_members({"symbol":SYMBOL,"positions":[{"ticket":"1","symbol":SYMBOL,"type":1,"volume":.01}]}),[])
    def test_exact_members(self):
        self.assertEqual(m.claim_members({"symbol":SYMBOL,"positions":[{"position_id":"101","symbol":SYMBOL,"type":1,"volume":.01}]}),[{"position_id":"101","side":"SELL","volume":.01}])
    def test_complete_group_includes_loss_leg(self):
        r=m.summarize(claims(),deals(),SYMBOL)
        self.assertEqual(r["complete_baskets"],1);self.assertEqual(r["net"],5)
        self.assertFalse(r["execution_authorized"])
    def test_missing_entry_cost_is_not_full_net(self):
        r=m.summarize(claims(),deals()[1:],SYMBOL)
        self.assertEqual(r["complete_baskets"],0);self.assertEqual(r["reports"][0]["exit_net"],5)
    def test_ambiguous_recovery_not_linked(self):
        d=deals()+[deal("e","103",0,0,.03,stamp=T+1,comment="ALZOOL-RECOVERY")]
        self.assertEqual(m.summarize(claims(),d,SYMBOL)["reports"][0]["status"],"UNVERIFIED_ENTRY")
    def test_market_isolation(self):
        self.assertEqual(m.summarize(claims(),deals(),"BTCUSD")["complete_baskets"],0)
    def test_trap_penalty_only_research(self):
        p={"decision":"BUY","entry_atr":2,"entry_reference_price":100,
           "context":{"buy_trap":True,"buy_quality":100,"last_closed_candle_pressure":1}}
        r=master_v3.speed_trap_candidate(p,{"tick":{"ask":100.1,"bid":100}},{},T,{"approved":True})
        self.assertEqual(r["direction"],"WAIT");self.assertEqual(r["reason"],"TRAP_RISK")
        self.assertFalse(r["execution_authorized"])
    def test_nontrap_candidate(self):
        p={"decision":"BUY","entry_atr":2,"entry_reference_price":100,
           "context":{"buy_quality":100,"last_closed_candle_pressure":1}}
        r=master_v3.speed_trap_candidate(p,{"tick":{"ask":100.1,"bid":100}},{},T,{"approved":True})
        self.assertEqual(r["direction"],"BUY");self.assertFalse(r["execution_authorized"])
if __name__=="__main__":unittest.main()
