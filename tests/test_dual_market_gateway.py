"""Offline gateway checks: no network requests or real trade execution."""
import json, pathlib, sys, unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
import dual_market_server as g
from multi_market_switch import MarketSwitch, Terminal

class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.clock=100.0
        self.control=MarketSwitch()
        self.control.observe(Terminal("BTCUSDm","1","Demo",100,ready=True))
        self.control.observe(Terminal("XAUUSDm","2","Demo",100,ready=True))
        self.stack=[patch.object(g,"SWITCH",self.control),
                    patch.object(g,"OWNER_TERMINALS",{"BTCUSD":"btc-id","XAUUSD":"gold-id"}),
                    patch("multi_market_switch.time.time",lambda:self.clock),
                    patch.object(g,"BOOT_TIME",-100)]
        for p in self.stack:p.start()
        self.addCleanup(lambda:[p.stop() for p in reversed(self.stack)])
    def test_same_origin_market_routes(self):
        self.assertEqual(g.route("/api/market/context"),("BTCUSD","/api/market/context"))
        self.assertEqual(g.route("/market/XAUUSD/v1/terminal-decision/gold-id?symbol=XAUUSDm"),("XAUUSD","/v1/terminal-decision/gold-id?symbol=XAUUSDm"))
        for bad in ("//outside","/market/ETHUSD/test","/market/XAUUSD/../api","/market/XAUUSD/%2e%2e/api"):
            with self.assertRaises(ValueError):g.route(bad)
    def test_inactive_market_never_reaches_worker(self):
        self.control.choose("BTCUSD",100)
        with patch.object(g,"request") as worker:
            result=g.permit_request("XAUUSD","/v1/terminal-decision/gold-id","GET",None,{})
            self.assertEqual(result[0],409)
            worker.assert_not_called()
    def test_wrong_identity_never_reaches_worker(self):
        self.control.choose("BTCUSD",100)
        with patch.object(g,"request") as worker:
            result=g.permit_request("BTCUSD","/v1/terminal-decision/other","GET",None,{})
            self.assertEqual(result[0],409)
            worker.assert_not_called()
    def test_forwarded_entry_permit_blocks_handover_until_expiry(self):
        self.control.choose("BTCUSD",100)
        real={"execution_ready":True,"authorization_expires_at":115}
        with patch.object(g,"request",return_value=g.json_response(200,real)):
            result=g.permit_request("BTCUSD","/v1/terminal-decision/btc-id","GET",None,{})
        self.assertEqual(result[0],200)
        self.clock=101
        self.assertTrue(self.control.choose("XAUUSD",101)["switching"])
        self.clock=114
        self.assertFalse(self.control.entry_allowed("XAUUSD"))
        self.clock=115
        self.assertTrue(self.control.entry_allowed("XAUUSD"))
    def test_invalid_or_missing_permit_expiry_fails_closed(self):
        self.control.choose("BTCUSD",100)
        for data in ({"execution_ready":True},{"execution_ready":True,"authorization_expires_at":1000}):
            with patch.object(g,"request",return_value=g.json_response(200,data)):
                self.assertEqual(g.permit_request("BTCUSD","/v1/terminal-decision/btc-id","GET",None,{})[0],409)
    def test_nonowner_heartbeat_cannot_replace_owner(self):
        g.record_heartbeat("BTCUSD","/v1/terminal-state/live/foreign",{},{"accepted":True,"market_owner_terminal":False})
        self.assertEqual(g.OWNER_TERMINALS["BTCUSD"],"btc-id")
    def test_selection_requires_verified_owner(self):
        with patch.object(g,"owner_states",return_value=None):
            self.assertEqual(g.selection_response({}, "XAUUSD")[0],403)
        self.assertIsNone(self.control.active)
    def test_staging_starts_off(self):
        self.assertIsNone(self.control.active)
        self.assertFalse(self.control.entry_allowed("BTCUSD"))
        self.assertFalse(self.control.entry_allowed("XAUUSD"))

if __name__=="__main__":unittest.main()
