"""Bounded database transport and diagnostics. No authorization caching.
Independent lanes protect live requests from research/write congestion.
"""
import threading,time,urllib.error,hashlib,copy
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
                if exc.code not in (429,500,502,503,504):
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

# No authorization cache: collapse only overlapping identical read requests.
# Once a response completes the next request is independently authenticated.
class SingleFlight:
    def __init__(self,limit=128):
        self.lock=threading.Lock();self.pending={};self.limit=limit
    def call(self,key,fn):
        with self.lock:
            state=self.pending.get(key)
            leader=state is None
            if leader:
                if len(self.pending)>=self.limit:raise Busy("auth requests busy")
                state={"done":threading.Event(),"result":None,"error":None}
                self.pending[key]=state
        if leader:
            try:
                state["result"]=fn()
            except Exception as exc:
                state["error"]=exc
            finally:
                with self.lock:
                    self.pending.pop(key,None)
                    state["done"].set()
        elif not state["done"].wait(6.):
            raise Busy("auth request still pending")
        if state["error"] is not None:
            # HTTPError carries a mutable response stream. Never consume the
            # same response body twice or share a provider error as a login.
            if leader:raise state["error"]
            raise Busy("shared authentication failed")
        return copy.deepcopy(state["result"])

# Limits per market process. Browser auth is isolated from MT5 identity and
# normal DB reads; a failed login service cannot trip the REST lane.
LIVE=Lane(6,5.,3.)
BROWSER_AUTH=Lane(2,5.,3.)
TERMINAL_AUTH=Lane(2,5.,3.)
RESEARCH=Lane(2,5.,5.)
DIAGNOSTICS=Lane(1,5.,5.)
AUTH_FLIGHTS=SingleFlight()
def database_call(fn,path,method,bearer=None,admin=False):
    name=threading.current_thread().name
    if name=="alzool-diagnostic-batch":lane=DIAGNOSTICS
    elif path.startswith("/auth/v1/"):
        lane=TERMINAL_AUTH if admin else BROWSER_AUTH
    elif name in ("strategy-evidence","specialist-research","alzool-offline-m1-diagnostic","alzool-closed-history") or "/alzool_engine_signal_events" in path or "/alzool_engine_market_candles" in path:
        lane=RESEARCH
    else:lane=LIVE
    if method=="GET" and (path=="/auth/v1/user" and bearer and not admin or admin and path.startswith("/auth/v1/admin/users")):
        identity=hashlib.sha256(str(bearer or "").encode()).hexdigest()
        return AUTH_FLIGHTS.call((path,identity,admin),lambda:lane.call(fn))
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
