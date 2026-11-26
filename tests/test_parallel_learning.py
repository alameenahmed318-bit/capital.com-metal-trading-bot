import sys,unittest,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/"marketplace/backend"),str(root/"research")]
from parallel_learning import complete_positions,sanitize_report,read_evidence
from parallel_memory import save,load,pause_model
def deal(t,entry,volume,profit=0,side=0,symbol="XAUUSD"):
    return {"payload":{"position_id":"1","ticket":str(t),"time":t,"entry":entry,"volume":volume,"profit":profit,"commission":-.1,"swap":0,"fee":0,"deal_type":side,"symbol":symbol}}
class LearningTests(unittest.TestCase):
    def test_complete_only(self):
        a=deal(100,0,.1);b=deal(200,1,.1,2,1)
        self.assertEqual(complete_positions([a,b,b],"XAUUSD"),[{"opened":100,"closed":200,"side":0,"positive":True}])
        self.assertEqual(complete_positions([a,deal(200,1,.05,2,1)],"XAUUSD"),[])
        self.assertEqual(complete_positions([a,deal(200,1,.1,2,1,"BTCUSDm")],"XAUUSD"),[])
    def test_missing_fee_rejected(self):
        a=deal(100,0,.1);b=deal(200,1,.1,2,1);del b["payload"]["fee"]
        self.assertEqual(complete_positions([a,b],"XAUUSD"),[])
    def test_persistence(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"data.json";d={"outcomes":{"TCN":[[100,1,.2]]},"pending":[]}
            save(p,d);self.assertEqual(load(p),d)
    def test_pause_needs_real_sample(self):
        self.assertFalse(pause_model([[i*60,0,-1] for i in range(100)],6000))
        self.assertTrue(pause_model([[i*180,0,-1] for i in range(100)],18000))
    def test_owner_client_scope_query(self):
        q=[]
        def sb(method,path,admin=False):
            q.append(path);return [{"validation_points":12,"slippage_points":10}] if "learning_runs" in path else [deal(100,0,.1),deal(200,1,.1,2,1)]
        d=read_evidence(sb,"owner-client","XAUUSD")
        self.assertTrue(all("client_id=eq.owner-client" in p and "XAUUSD" in p for p in q))
        self.assertEqual(d["backtests"],1);self.assertEqual(d["slippage_points"],10)
    def test_sanitize(self):
        self.assertEqual(sanitize_report({"trade_status":"VALIDATED","trade_accuracy":.7,"paused":["TCN","unknown"],"execution_authorized":True}),{"trade_status":"VALIDATED","trade_accuracy":.7,"paused":["TCN"]})
if __name__=="__main__":unittest.main()
