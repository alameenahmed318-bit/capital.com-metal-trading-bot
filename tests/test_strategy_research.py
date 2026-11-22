"""Offline regression for the read-only research comparison."""
import pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from strategy_research import replay

def candles(n):
    return [{"time":1700000040+i*60,"open":100+i,"close":101+i,
             "high":102+i,"low":99+i,"spread_points":1,"point_size":.1,
             "tick_volume":10} for i in range(n)]

class ResearchTests(unittest.TestCase):
    def test_scoring_and_exact_same_past(self):
        seen=[]
        def evaluate(rows,market_symbol,research_policy):
            seen.append((market_symbol,research_policy,rows[-1]["time"],len(rows)))
            return {"decision":"BUY" if research_policy else "WAIT","entry_atr":1}
        out=replay(candles(150),"XAUUSD",evaluate,0)
        self.assertEqual(out["windows"],30)
        self.assertEqual(out["baseline"]["wait"],30)
        self.assertEqual(out["candidate"]["positive"],30)
        self.assertAlmostEqual(out["candidate"]["net"],27)
        self.assertFalse(out["actual_trading_proof"])
        self.assertEqual(out["policy"],"RESEARCH_ONLY")
        for a,b in zip(seen[::2],seen[1::2]):
            self.assertEqual(a[2:],b[2:])
            self.assertEqual(a[3],120)
    def test_gaps_and_missing_spread_excluded(self):
        rows=candles(150);rows[100]["time"]+=60
        out=replay(rows,"BTCUSD",lambda *a,**k: self.fail("invalid window"),0)
        self.assertEqual(out["windows"],0)
    def test_sample_limit(self):
        out=replay(candles(500),"BTCUSD",lambda *a,**kw:{"decision":"SELL","entry_atr":1},0)
        self.assertEqual(out["windows"],160)
        self.assertEqual(out["candidate"]["positive"],0)

if __name__=="__main__":unittest.main()
