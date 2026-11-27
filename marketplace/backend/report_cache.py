"""Read-only archive/report cache. Never used for authentication, settings,
execution, live forecasts or training weight decisions. Bounded per process.
No stale fallback on error; current history can be re-read after expiry.
"""
import json,threading,time
from collections import OrderedDict
from datetime import datetime,timezone,timedelta

def cutoff(days=0,seconds=120):
    now=int(time.time())
    return (datetime.fromtimestamp(now-now%seconds,timezone.utc)-timedelta(days=days)).isoformat()

class ReportCache:
    def __init__(self,max_bytes=8*1024*1024,max_entries=64,clock=time.monotonic):
        self.max_bytes=max_bytes;self.max_entries=max_entries;self.clock=clock
        self.items=OrderedDict();self.bytes=0;self.lock=threading.Lock()
        self.pending={};self.failure={};self.hits=0;self.misses=0
    def read(self,key,load,ttl=120):
        with self.lock:
            now=self.clock();old=self.items.get(key)
            if old and now<old[0]:
                self.items.move_to_end(key);self.hits+=1
                return json.loads(old[1])
            if self.failure.get(key,0)>now:raise RuntimeError("archive temporarily unavailable")
            state=self.pending.get(key);leader=state is None
            if leader:
                if len(self.pending)>=4:raise RuntimeError("archive reads busy")
                state={"done":threading.Event(),"raw":None,"error":False}
                self.pending[key]=state;self.misses+=1
        if not leader:
            if not state["done"].wait(5.5) or state["error"]:raise RuntimeError("archive read unavailable")
            return json.loads(state["raw"])
        try:
            raw=json.dumps(load(),ensure_ascii=False,separators=(",",":")).encode()
            state["raw"]=raw
            with self.lock:
                prior=self.items.pop(key,None)
                if prior:self.bytes-=len(prior[1])
                if len(raw)<=self.max_bytes:
                    while self.items and (self.bytes+len(raw)>self.max_bytes or len(self.items)>=self.max_entries):
                        _,v=self.items.popitem(last=False);self.bytes-=len(v[1])
                    self.items[key]=(self.clock()+ttl,raw);self.bytes+=len(raw)
                self.failure.pop(key,None)
            return json.loads(raw)
        except Exception:
            state["error"]=True
            with self.lock:
                if len(self.failure)>=128:self.failure.clear()
                self.failure[key]=self.clock()+15
            raise
        finally:
            with self.lock:
                self.pending.pop(key,None);state["done"].set()

REPORTS=ReportCache()
def archive_read(sb,query,ttl=120):
    # Explicit allowlist, never accept a user-provided URL or auth/settings table.
    table=query.split("?",1)[0]
    if table not in ("/rest/v1/alzool_engine_closed_deals","/rest/v1/alzool_engine_signal_events","/rest/v1/alzool_engine_market_candles"):
        raise ValueError("not an archive query")
    return REPORTS.read(query,lambda:sb("GET",query,admin=True),ttl)
