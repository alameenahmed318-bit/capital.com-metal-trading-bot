"""Bounded database transport and diagnostics. No authorization caching.
Independent lanes protect live requests from research/write congestion.
"""
import threading,time,urllib.error
from collections import OrderedDict

class Busy(RuntimeError):pass

class Lane:
    def __init__(self,limit,timeout=5.,cooldown=3.,clock=time.monotonic):
        self.slots=threading.BoundedSemaphore(limit)
        self.timeout=timeout;self.cooldown=cooldown;self.clock=clock
        self.lock=threading.Lock();self.until=0.;self.probe=False;self.generation=0
    def call(self,fn):
        if not self.slots.acquire(blocking=False):raise Busy("database lane busy")
        probe=False;started=False
        try:
            with self.lock:
                now=self.clock()
                if self.until:
                    if now<self.until or self.probe:raise Busy("database reconnect cooldown")
                    self.probe=True;probe=True
                gen=self.generation;started=True
            try:
                result=fn(self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code not in (429,502,503,504):
                    if probe:self._success(gen,probe)
                    raise
                self._failure(gen);raise
            except (TimeoutError,ConnectionError,urllib.error.URLError,OSError):
                self._failure(gen);raise
            self._success(gen,probe)
            return result
        finally:
            if started and probe:
                with self.lock:
                    if self.generation==gen:self.probe=False
            self.slots.release()
    def _failure(self,gen):
        with self.lock:
            if self.generation==gen:
                self.generation+=1;self.until=self.clock()+self.cooldown;self.probe=False
    def _success(self,gen,probe):
        with self.lock:
            if self.generation==gen and probe:self.until=0.;self.probe=False

# Limits are per market process. Never reuse auth or stale order permits.
LIVE=Lane(6,5.,3.)
RESEARCH=Lane(2,5.,5.)
DIAGNOSTICS=Lane(1,5.,5.)
def database_call(fn,path,method):
    name=threading.current_thread().name
    if name=="alzool-diagnostic-batch":lane=DIAGNOSTICS
    elif name in ("strategy-evidence","specialist-research","alzool-offline-m1-diagnostic","alzool-closed-history") or "/alzool_engine_signal_events" in path or "/alzool_engine_market_candles" in path:
        lane=RESEARCH
    else:lane=LIVE
    return lane.call(fn)

class DiagnosticQueue:
    """Bounded in-memory, idempotent batches; retry failed rows with backoff.
    On saturation reject newest events explicitly; no order or trade settings.
    """
    def __init__(self,limit=2048,batch=32):
        self.limit=limit;self.batch=batch;self.cv=threading.Condition()
        self.pending=OrderedDict();self.running=False;self.dropped=0
    def submit(self,event,save):
        key=event["event_id"]
        with self.cv:
            if key in self.pending:return False
            if len(self.pending)>=self.limit:
                self.dropped+=1
                if self.dropped==1 or self.dropped%100==0:print("AI_DIAGNOSTIC_QUEUE_FULL:",self.dropped,flush=True)
                return False
            self.pending[key]=event
            if not self.running:
                self.running=True
                threading.Thread(target=self._run,args=(save,),name="alzool-diagnostic-batch",daemon=True).start()
            self.cv.notify()
            return True
    def _run(self,save):
        delay=1.
        while True:
            with self.cv:
                while not self.pending:self.cv.wait()
                rows=list(self.pending.values())[:self.batch]
            try:save(rows)
            except Exception as exc:
                print("AI_DIAGNOSTIC_BATCH_RETRY:",type(exc).__name__,flush=True)
                time.sleep(delay);delay=min(delay*2,30.)
                continue
            with self.cv:
                for row in rows:self.pending.pop(row["event_id"],None)
            delay=1.
