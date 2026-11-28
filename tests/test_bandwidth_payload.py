import gzip,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from bandwidth_payload import negotiate_payload,unchanged_context
from client_access import compact_owner_terminal_decision
class BandwidthTests(unittest.TestCase):
    def test_negotiated_and_native_protocol(self):
        raw=json.dumps({"bars":[{"open":4000,"high":4002,"low":3999,"close":4001} for _ in range(61)]}).encode()
        packed,yes=negotiate_payload(raw,"/market/XAUUSD/api/ai/arena/live-context","gzip, deflate, br","application/json")
        self.assertTrue(yes)
        self.assertEqual(gzip.decompress(packed),raw)
        self.assertLess(len(packed),len(raw)*.25)
        for header in ("","br","gzip;q=0","gzip;q=0.0","gzip;q=wrong"):
            self.assertEqual(negotiate_payload(raw,"/api/context",header,"application/json"),(raw,False))
        self.assertEqual(negotiate_payload(raw,"/market/XAUUSD/v1/terminal-decision/a?compact=1","gzip","application/json"),(raw,False))
    def test_exact_bar_only(self):
        c={"status":"READY","client":"id","symbol":"XAUUSD","bar_time":60,"bars":[1,2]}
        q={"seen_client":["id"],"seen_symbol":["XAUUSD"],"seen_bar":["60"]}
        self.assertEqual(unchanged_context(c,q)["status"],"UNCHANGED")
        for k,v in (("seen_client",["different"]),("seen_symbol",["BTCUSD"]),("seen_bar",["0"])):
            self.assertEqual(unchanged_context(c,{**q,k:v}),c)
        self.assertEqual(unchanged_context({"status":"NO_CURRENT_M1"},q),{"status":"NO_CURRENT_M1"})
    def test_compact_keeps_permit_and_demo_guard(self):
        full={"symbol":"XAUUSD","signal":"BUY","authorization_id":"abc","authorization_expires_at":123,"execution_ready":True,"master_direct_demo":True,"smart_profit_bias_valid":True,"smart_profit_bias":.5,"strategy_id":7,"combination_id":1,"bar_time":60,"entry_reference_price":4000,"entry_atr":1,"confidence":.8,"reason_ar":"ready","master":{"large":"x"*20000}}
        compact=compact_owner_terminal_decision(full)
        self.assertEqual(compact,{k:v for k,v in full.items() if k!="master"})
        self.assertEqual(compact_owner_terminal_decision({"execution_ready":False,"signal":"WAIT"}),{"execution_ready":False,"signal":"WAIT"})
if __name__=="__main__":unittest.main()
