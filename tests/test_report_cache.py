import sys,unittest,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace/backend"))
from report_cache import ReportCache,archive_read,cutoff
class CacheTests(unittest.TestCase):
    def test_read_reuses_and_expires(self):
        t=[0];c=ReportCache(clock=lambda:t[0]);calls=[]
        def read():calls.append(1);return [{"value":len(calls)}]
        a=c.read("one",read,60);a[0]["value"]=99
        for _ in range(20):self.assertEqual(c.read("one",read,60),[{"value":1}])
        self.assertEqual(len(calls),1)
        t[0]=61;self.assertEqual(c.read("one",read,60),[{"value":2}])
    def test_isolation_and_bound(self):
        c=ReportCache(max_bytes=100,max_entries=2)
        for k in ("gold-a","btc-a","gold-b"):self.assertEqual(c.read(k,lambda:[k]),[k])
        self.assertEqual(len(c.items),2);self.assertLessEqual(c.bytes,100)
    def test_reject_auth_and_settings(self):
        for q in ("/auth/v1/user","/rest/v1/alzool_engine_clients","/rest/v1/alzool_engine_trade_settings"):
            with self.assertRaises(ValueError):archive_read(lambda *a,**k:self.fail("must not fetch"),q)
    def test_concurrent_reads(self):
        c=ReportCache();calls=[];start=threading.Event();release=threading.Event();results=[]
        def load():calls.append(1);start.set();release.wait(2);return [42]
        a=threading.Thread(target=lambda:results.append(c.read("same",load)))
        b=threading.Thread(target=lambda:results.append(c.read("same",load)))
        a.start();self.assertTrue(start.wait(1));b.start();time.sleep(.05);release.set();a.join();b.join()
        self.assertEqual(calls,[1]);self.assertEqual(results,[[42],[42]])
    def test_failure_does_not_cache_empty_or_return_stale(self):
        t=[0];c=ReportCache(clock=lambda:t[0]);c.read("x",lambda:[1],1);t[0]=2
        def fail():raise TimeoutError()
        with self.assertRaises(TimeoutError):c.read("x",fail)
        with self.assertRaises(RuntimeError):c.read("x",lambda:self.fail("retry too soon"))
        t[0]=18;self.assertEqual(c.read("x",lambda:[2]),[2])
    def test_only_reports_are_modified(self):
        s=(Path(__file__).resolve().parents[1]/"marketplace/backend/app.py").read_text()
        self.assertIn("stored=archive_read(sb,",s);self.assertIn("page=archive_read(sb,",s)
        self.assertIn("validate_license(client_id,token)",s)
        self.assertIn("return database_call(send,path,method,bearer=bearer,admin=admin)",s)
if __name__=="__main__":unittest.main()
