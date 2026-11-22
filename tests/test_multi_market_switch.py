"""Offline handover tests. Never connect to a broker or send orders."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from multi_market_switch import MarketSwitch,Terminal,market_scope

def terminal(symbol,login="1",seen=100,positions=0,ready=True,foreign_positions=0,foreign_orders=0):
    return Terminal(symbol,login,"Demo",seen,positions,ready,foreign_positions,foreign_orders)

class MultiMarketSwitchTests(unittest.TestCase):
    def setUp(self):
        self.control=MarketSwitch("BTCUSD")
        self.control.observe(terminal("BTCUSDm",login="1",positions=3))
        self.control.observe(terminal("XAUUSDm",login="2",positions=2))
    def test_select_ready_market(self):
        result=self.control.choose("XAUUSD",100)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["active"],"XAUUSD")
        self.assertFalse(self.control.entry_allowed("BTCUSD",100))
        self.assertTrue(self.control.entry_allowed("XAUUSD",100))
        self.assertTrue(self.control.management_allowed("BTCUSD",100))
        self.assertEqual(result["markets"]["BTCUSD"]["open_positions"],3)
    def test_outstanding_entry_authorization_delays_handover(self):
        self.assertTrue(self.control.issue_entry("BTCUSD",100,115))
        result=self.control.choose("XAUUSD",101)
        self.assertTrue(result["switching"])
        self.assertIsNone(result["active"])
        self.assertFalse(self.control.issue_entry("BTCUSD",102))
        self.assertFalse(self.control.issue_entry("XAUUSD",114))
        self.assertTrue(self.control.issue_entry("XAUUSD",115))
    def test_unready_target_does_not_change_selection(self):
        self.control.observe(terminal("XAUUSDm",login="2",seen=50))
        self.assertFalse(self.control.choose("XAUUSD",100)["accepted"])
        self.assertTrue(self.control.entry_allowed("BTCUSD",100))
    def test_same_account_disallowed(self):
        self.control.observe(terminal("XAUUSDm",login="1"))
        self.assertFalse(self.control.choose("XAUUSD",100)["accepted"])
        self.assertFalse(self.control.entry_allowed("BTCUSD",100))
        self.assertFalse(self.control.management_allowed("XAUUSD",100))
    def test_foreign_positions_or_orders_disallowed(self):
        for detail in ({"foreign_positions":1},{"foreign_orders":1}):
            self.control.observe(terminal("XAUUSDm",login="2",**detail))
            self.assertFalse(self.control.choose("XAUUSD",100)["accepted"])
    def test_repeated_switch_cannot_bypass_old_permit(self):
        self.assertTrue(self.control.issue_entry("BTCUSD",100,115))
        self.control.choose("XAUUSD",101)
        self.control.choose("BTCUSD",102)
        self.control.choose("XAUUSD",103)
        self.assertFalse(self.control.entry_allowed("XAUUSD",114))
        self.assertTrue(self.control.entry_allowed("XAUUSD",115))
    def test_market_and_account_keys_are_isolated(self):
        self.assertNotEqual(market_scope("owner","BTCUSD"),market_scope("owner","XAUUSD"))
        self.assertNotEqual(market_scope("owner","BTCUSD"),market_scope("client","BTCUSD"))
        with self.assertRaises(ValueError):market_scope("owner","ETHUSD")
    def test_missing_terminal_or_expired_permit_fails_closed(self):
        control=MarketSwitch()
        self.assertFalse(control.choose("BTCUSD",100)["accepted"])
        self.assertFalse(self.control.issue_entry("BTCUSD",100,116))
        self.assertFalse(self.control.issue_entry("BTCUSD",100,100))
        self.assertFalse(self.control.entry_allowed("BTCUSD",131))
    def test_no_activation_after_target_disconnects(self):
        self.control.issue_entry("BTCUSD",100,115)
        self.control.choose("XAUUSD",101)
        self.control.observe(terminal("XAUUSDm",login="2",ready=False,seen=110))
        self.assertFalse(self.control.entry_allowed("XAUUSD",116))
        self.assertIsNone(self.control.status(116)["active"])

if __name__=="__main__":unittest.main()
