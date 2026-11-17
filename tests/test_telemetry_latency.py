"""Latency regression tests for safe MT5 historical persistence and hot-path order."""
import sys
import time
import threading
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"marketplace"/"backend"))
from telemetry_persistence import _STATE,_LOCK,queue_closed_deals

class SlowStore:
    def __init__(self,fail_first=False):
        self.calls=0
        self.started=threading.Event()
        self.release=threading.Event()
        self.done=threading.Event()
        self.fail_first=fail_first
    def __call__(self,method,path,data=None,admin=False,prefer=None):
        self.calls+=1
        self.started.set()
        self.release.wait(3)
        if self.fail_first:
            self.fail_first=False
            self.done.set()
            raise RuntimeError("offline")
        self.done.set()
        return []

def sample():
    return [{"ticket":"42","time":1763150000,"symbol":"XAUUSD","profit":3.2,
             "strategy_id":5,"commission":-0.1}]

class LiveTelemetryTests(unittest.TestCase):
    def setUp(self):
        with _LOCK:_STATE.clear()
    def test_db_is_not_on_live_heartbeat_call_stack(self):
        store=SlowStore()
        started=time.monotonic()
        accepted=queue_closed_deals("client",sample(),store)
        elapsed=time.monotonic()-started
        self.assertTrue(accepted)
        self.assertLess(elapsed,.5)
        self.assertTrue(store.started.wait(2))
        self.assertFalse(queue_closed_deals("client",sample(),store))
        store.release.set()
        self.assertTrue(store.done.wait(2))
        # Workers must settle before asserting duplicate suppression.
        for _ in range(100):
            if _STATE["client"]["saved"] is not None and not _STATE["client"]["running"]:break
            time.sleep(.01)
        self.assertFalse(queue_closed_deals("client",sample(),store))
        self.assertEqual(store.calls,1)
    def test_failed_save_is_retried_on_next_heartbeat(self):
        store=SlowStore(fail_first=True)
        self.assertTrue(queue_closed_deals("client2",sample(),store))
        store.release.set()
        self.assertTrue(store.done.wait(2))
        for _ in range(100):
            if not _STATE["client2"]["running"]:break
            time.sleep(.01)
        self.assertTrue(queue_closed_deals("client2",sample(),store))
        for _ in range(100):
            if store.calls==2 and not _STATE["client2"]["running"]:break
            time.sleep(.01)
        self.assertEqual(store.calls,2)
    def test_mt5_snapshot_precedes_decision_and_local_trade_protection_preserved(self):
        s=(ROOT/"mt5_client"/"ALZOOL_Central_Client.mq5").read_text(encoding="utf-8")
        p=s.index("void OnTimer()")
        timer=s[p:s.index("void OnTick()",p)]
        self.assertLess(timer.index("SendLiveSnapshot()"),timer.index("PollTradeSettings()"))
        self.assertLess(timer.index("PollTradeSettings()"),timer.index("PollCentralDecision()"))
        self.assertIn("EvaluateAutoClose()",timer)
        self.assertIn("ManageEntryTrailing()",timer)
        self.assertIn("InpEnableCentralTrading = false",s)
        self.assertIn("InpEnableCentralPositionManagement = false",s)
        self.assertIn("g_closedDealsDirty=true;",s)
        self.assertIn('g_cachedClosedDealsJson=",\\\"closed_deals\\\":[]";',s)
if __name__=="__main__":unittest.main()
