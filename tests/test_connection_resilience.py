import sys,unittest,threading,time,urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from connection_resilience import Lane,Busy,DiagnosticQueue

class ResilienceTests(unittest.TestCase):
    def test_timeout_circuit_recovers(self):
        t=[100.];lane=Lane(2,5,3,lambda:t[0])
        def bad(timeout):
            self.assertEqual(timeout,5);raise TimeoutError()
        with self.assertRaises(TimeoutError):lane.call(bad)
        with self.assertRaises(Busy):lane.call(lambda _:self.fail("must not send during cooldown"))
        t[0]=104
        self.assertEqual(lane.call(lambda _:42),42)
        self.assertEqual(lane.call(lambda _:43),43)
    def test_capacity_is_nonblocking_and_separate(self):
        slow=Lane(1);live=Lane(1);started=threading.Event();release=threading.Event()
        def job(_):started.set();release.wait(2)
        th=threading.Thread(target=lambda:slow.call(job));th.start()
        try:
            self.assertTrue(started.wait(1))
            with self.assertRaises(Busy):slow.call(lambda _:None)
            self.assertEqual(live.call(lambda _:1),1)
        finally:release.set();th.join()
    def test_auth_failure_does_not_trip_transport(self):
        lane=Lane(1)
        def bad(_):raise urllib.error.HTTPError("https://example.test",401,"unauthorized",{},None)
        with self.assertRaises(urllib.error.HTTPError):lane.call(bad)
        self.assertEqual(lane.call(lambda _:True),True)
    def test_batch_retry_and_capacity(self):
        q=DiagnosticQueue(limit=2,batch=2);started=threading.Event();release=threading.Event();saved=[];attempts=[]
        def save(rows):
            attempts.append(len(rows));started.set()
            if len(attempts)==1:release.wait(2);raise TimeoutError()
            saved.extend(row["event_id"] for row in rows)
        self.assertTrue(q.submit({"event_id":"a"},save));self.assertTrue(started.wait(1))
        self.assertFalse(q.submit({"event_id":"a"},save));self.assertTrue(q.submit({"event_id":"b"},save));self.assertFalse(q.submit({"event_id":"c"},save))
        release.set()
        until=time.monotonic()+4
        while len(saved)<2 and time.monotonic()<until:time.sleep(.02)
        self.assertEqual(saved,["a","b"]);self.assertEqual(q.dropped,1)
    def test_worker_transport_is_bounded(self):
        s=(Path(__file__).resolve().parents[1]/"marketplace/backend/app.py").read_text()
        self.assertIn("return database_call(send,path,method)",s)
        self.assertIn("DIAGNOSTIC_QUEUE.submit",s)
        self.assertNotIn("threading.Thread(target=persist_ai_diagnostic_event",s)
if __name__=="__main__":unittest.main()
