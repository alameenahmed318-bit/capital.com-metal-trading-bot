"""Non-trading backtest, walk-forward and Monte Carlo validation."""
import json
from datetime import datetime,timezone
import numpy as np
import pandas as pd
import config
from capital_api import CapitalAPI

EPICS=list(getattr(config,"EPICS",[])); LOOKBACK=300; MC_RUNS=2000

def mid(c):
    p=c.get("closePrice",{}); b,a=p.get("bid"),p.get("ask")
    return (float(b)+float(a))/2 if b is not None and a is not None else float(b if b is not None else a)

def candles(api,epic):
    raw=api.get_candles(epic=epic,resolution="MINUTE_15",max_candles=LOOKBACK)
    rows=[]
    for c in raw.get("prices",[]):
        try: rows.append({"close":mid(c),"high":mid(c.get("highPrice",{})),"low":mid(c.get("lowPrice",{}))})
        except (TypeError,ValueError): pass
    return pd.DataFrame(rows)

def features(df):
    x=df.copy()
    for n in (9,21,50,200): x[f"ema{n}"]=x.close.ewm(span=n,adjust=False).mean()
    tr=pd.concat([x.high-x.low,(x.high-x.close.shift()).abs(),(x.low-x.close.shift()).abs()],axis=1).max(axis=1)
    x["atr"]=tr.ewm(alpha=1/14,adjust=False).mean(); x["roc20"]=x.close.pct_change(20)
    return x

def signal(x,i):
    r=x.iloc[i]
    if any(pd.isna(r[k]) for k in ("ema9","ema21","ema50","ema200","atr","roc20")): return 0
    votes=(1 if r.ema9>r.ema21 else -1)+(1 if r.ema21>r.ema50 else -1)+(1 if r.ema50>r.ema200 else -1)+(1 if r.roc20>0 else -1)
    return 1 if votes>=3 else -1 if votes<=-3 else 0

def backtest(df):
    x=features(df); rs=[]
    for i in range(205,len(x)-1):
        s=signal(x,i)
        if not s: continue
        entry=float(x.close.iloc[i]); atr=float(x.atr.iloc[i]); stop=2*atr; end=min(i+12,len(x)-1); pnl=None
        for j in range(i+1,end+1):
            move=s*(float(x.close.iloc[j])-entry)
            if move<=-stop: pnl=-1; break
            if move>=3*atr: pnl=3; break
        if pnl is None: pnl=float(np.clip(s*(float(x.close.iloc[end])-entry)/stop,-1,3))
        rs.append(pnl)
    return np.asarray(rs,dtype=float)

def metrics(rs):
    if len(rs)==0:return {"trades":0}
    wins=rs[rs>0]; losses=rs[rs<0]; eq=np.cumsum(rs); peak=np.maximum.accumulate(np.r_[0,eq]); dd=eq-peak[1:]
    return {"trades":int(len(rs)),"win_rate_pct":round(float((rs>0).mean()*100),2),"expectancy_R":round(float(rs.mean()),4),"profit_factor":round(float(wins.sum()/abs(losses.sum())),3) if len(losses) else None,"max_drawdown_R":round(float(abs(dd.min())),3) if len(dd) else 0,"net_R":round(float(rs.sum()),3)}

def monte_carlo(rs):
    if len(rs)<10:return {"runs":0}
    rng=np.random.default_rng(42); ends=[]; dds=[]
    for _ in range(MC_RUNS):
        sample=rng.choice(rs,size=len(rs),replace=True); eq=np.cumsum(sample); peak=np.maximum.accumulate(np.r_[0,eq]); dds.append(float(abs((eq-peak[1:]).min()))); ends.append(float(eq[-1]))
    return {"runs":MC_RUNS,"median_end_R":round(float(np.median(ends)),3),"p05_end_R":round(float(np.percentile(ends,5)),3),"p95_max_drawdown_R":round(float(np.percentile(dds,95)),3)}

def validate(api,epic):
    df=candles(api,epic)
    if len(df)<230:return {"epic":epic,"error":"insufficient candles","candles":len(df)}
    rs=backtest(df); split=max(1,len(rs)*2//3)
    return {"epic":epic,"candles":len(df),"full":metrics(rs),"walk_forward_train":metrics(rs[:split]),"walk_forward_test":metrics(rs[split:]),"monte_carlo":monte_carlo(rs)}

def main():
    api=CapitalAPI(); api.login()
    out={"generated_at":datetime.now(timezone.utc).isoformat(),"mode":"NON_TRADING_VALIDATION","markets":[validate(api,e) for e in EPICS]}
    with open("quant_validation.json","w",encoding="utf-8") as f: json.dump(out,f,indent=2)
    print("QUANT VALIDATION COMPLETE")
    for x in out["markets"]: print(x)

if __name__=="__main__": main()
