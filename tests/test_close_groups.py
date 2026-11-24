import unittest
from closure_groups import closure_groups
def deal(t, ticket, kind=1, profit=2, symbol="BTCUSDm", entry=1, pos=None, commission=0):
    return {"payload":{"time":t,"ticket":ticket,"position_id":pos or ticket,"symbol":symbol,"entry":entry,"type":kind,"profit":profit,"volume":.01,"commission":commission,"reason":3}}
class CloseGroupsTests(unittest.TestCase):
    def test_exit_side_net_and_separate_windows(self):
        rows=[deal(100,"a",profit=5,commission=-.2),deal(102,"b",profit=-2),
              deal(103,"c",kind=0),deal(107,"d"),deal(101,"e",entry=0),deal(102,"f",symbol="XAUUSD")]
        g=closure_groups(rows,lambda s:s.startswith("BTC"))
        self.assertEqual(len(g),3)
        self.assertEqual(g[-1]["side"],"BUY")
        self.assertEqual(g[-1]["count"],2)
        self.assertAlmostEqual(g[-1]["net"],2.8)
        self.assertEqual(g[1]["side"],"SELL")
    def test_duplicate_and_partial_fills(self):
        d=deal(100,"a",pos="P")
        g=closure_groups([d,d,deal(101,"b",pos="P")],lambda s:True)
        self.assertEqual(g[0]["count"],1)
        self.assertEqual(g[0]["deal_count"],2)
        self.assertEqual(g[0]["net"],4)
