import os, time, json, math, logging
from datetime import datetime, timezone
import requests

BASE = os.getenv("CAPITAL_BASE_URL","https://demo-api-capital.backend-capital.com")
API_KEY = os.environ["CAPITAL_API_KEY"]
IDENTIFIER = os.environ["CAPITAL_IDENTIFIER"]
PASSWORD = os.environ["CAPITAL_PASSWORD"]
SIZE = float(os.getenv("TRADE_SIZE","0.01"))
DRY_RUN = os.getenv("DRY_RUN","true").lower()=="true"
SCAN_SECONDS = int(os.getenv("SCAN_SECONDS","10"))
EPICS = [x.strip() for x in os.getenv("EPICS","EURUSD,GBPUSD,USDJPY,AUDUSD,USDCHF,USDCAD,NZDUSD,EURJPY,GBPJPY").split(",") if x.strip()]

log=logging.getLogger("hybrid")
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")

class Capital:
    def __init__(self):
        self.s=requests.Session()
        self.s.headers.update({"X-CAP-API-KEY":API_KEY,"Content-Type":"application/json"})
        self.cst=None; self.token=None
    def session(self):
        r=self.s.post(BASE+"/api/v1/session",json={"identifier":IDENTIFIER,"password":PASSWORD,"encryptedPassword":False},timeout=20)
        r.raise_for_status()
        self.cst=r.headers.get("CST"); self.token=r.headers.get("X-SECURITY-TOKEN")
        self.s.headers.update({"CST":self.cst,"X-SECURITY-TOKEN":self.token})
    def get(self,path,**params):
        r=self.s.get(BASE+path,params=params,timeout=20); r.raise_for_status(); return r.json()
    def post(self,path,payload):
        r=self.s.post(BASE+path,json=payload,timeout=20); r.raise_for_status(); return r.json()
    def delete(self,path):
        r=self.s.delete(BASE+path,timeout=20); r.raise_for_status(); return r.json() if r.content else {}
    def prices(self,epic,n=100):
        return self.get(f"/api/v1/prices/{epic}",resolution="MINUTE",max=n)
    def positions(self):
        return self.get("/api/v1/positions").get("positions",[])
    def market(self,epic):
        return self.get(f"/api/v1/markets/{epic}")
    def open(self,epic,direction,size):
        if DRY_RUN:
            log.info("DRY RUN | %s %s %.4f",direction,epic,size); return
        return self.post("/api/v1/positions",{"epic":epic,"direction":direction,"size":size,"guaranteedStop":False})

def mid(p):
    b=p.get("closePrice",{}).get("bid"); a=p.get("closePrice",{}).get("ask")
    if b is None or a is None: return None
    return (float(b)+float(a))/2

def candles(raw):
    out=[]
    for x in raw.get("prices",[]):
        m=mid(x)
        if m is not None: out.append(m)
    return out

def ema(xs,n):
    if len(xs)<n:return None
    k=2/(n+1); e=sum(xs[:n])/n
    for v in xs[n:]: e=v*k+e*(1-k)
    return e

def atr_like(xs,n=14):
    if len(xs)<n+1:return None
    return sum(abs(xs[i]-xs[i-1]) for i in range(len(xs)-n,len(xs)))/n

def signal(xs):
    if len(xs)<40:return None
    e9,e21=ema(xs,9),ema(xs,21); prev9=ema(xs[:-1],9); prev21=ema(xs[:-1],21)
    vol=atr_like(xs)
    if not all(v is not None for v in (e9,e21,prev9,prev21,vol)) or vol<=0:return None
    px=xs[-1]; momentum=(xs[-1]-xs[-6])/vol
    cross_up=prev9<=prev21 and e9>e21
    cross_dn=prev9>=prev21 and e9<e21
    trend_up=e9>e21 and momentum>0.25
    trend_dn=e9<e21 and momentum<-0.25
    if cross_up or (trend_up and momentum>0.6): return "BUY"
    if cross_dn or (trend_dn and momentum<-0.6): return "SELL"
    return None

def owned_positions(positions,epic):
    return [p for p in positions if p.get("market",{}).get("epic")==epic]

def run():
    api=Capital(); api.session()
    log.info("HYBRID CAPITAL BOT | DRY_RUN=%s | EPICS=%s",DRY_RUN,",".join(EPICS))
    while True:
        try:
            positions=api.positions()
            for epic in EPICS:
                try:
                    raw=api.prices(epic,100)
                    xs=candles(raw)
                    sig=signal(xs)
                    owned=owned_positions(positions,epic)
                    if sig and not owned:
                        api.open(epic,sig,SIZE)
                        log.info("%s -> %s",epic,sig)
                    else:
                        log.info("%s -> %s | open=%d",epic,sig or "WAIT",len(owned))
                except Exception as e:
                    log.warning("%s scan failed: %s",epic,e)
            time.sleep(SCAN_SECONDS)
        except Exception as e:
            log.error("cycle error: %s",e)
            time.sleep(15)
            try: api.session()
            except Exception: pass

if __name__=="__main__":
    run()
