"""AL-ZOOL research-only two-model meta-master. No broker, API or live imports.
Input: owner-local JSON array/JSONL of MASTER_V3_OUTCOME payloads (or objects
with payload). Select one earliest observation per market/bar; never use
post-forecast data as features. All scores are quote proxies, not fills.
"""
import argparse
import json
import math
import time
from pathlib import Path
import numpy as np
from catboost import CatBoostRegressor
from xgboost import XGBRegressor

ADVISERS = ("XGBoost", "TiRex-2", "CatBoost", "TCN", "LSTM Multi",
            "Patch Transformer", "Chronos-Bolt Tiny", "LightGBM",
            "TimesFM-2.5", "Chronos-2", "PatchTST", "LSTM")
SIDES = ("BUY", "SELL")
HORIZON = 60
STRESS_ATR = 0.30
MIN_SIGNALS = 30
VERSION = "HYBRID_META_MASTER_V1"

def number(v, default=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError, OverflowError):
        return default

def features(p):
    c=p.get("candidate") or {}
    a=c.get("adaptive") or {}
    review=c.get("adaptive_baseline_review") or {}
    forecasts=a.get("forecasts") or {}
    statuses=review.get("statuses") or {}
    # Missing forecasts and genuine zero signals are different.
    # Optional timestamped parallel evidence must match the market and bar,
    # and be available at the observation time, not joined retrospectively.
    parallel=p.get("parallel_evidence") or {}
    if (parallel.get("symbol")==p.get("symbol") and
        parallel.get("bar_time")==p.get("bar_time") and
        0 < number(parallel.get("received_at"), -1) <= number(p.get("observed_at"), -1)):
        extra=parallel.get("forecasts") or {}
        for k,v in extra.items():
            if k in ("TCN","LSTM Multi","Patch Transformer","PatchTST","LSTM") and isinstance(v,(int,float)) and math.isfinite(v):
                forecasts={**forecasts,k:v}
                statuses={**statuses,k:"READY"}
    x=[]
    for name in ADVISERS:
        v=forecasts.get(name)
        present=statuses.get(name)=="READY" and isinstance(v,(int,float)) and math.isfinite(v)
        x.extend([max(-6.,min(6.,float(v))) if present else 0.,float(present)])
    s=a.get("scores") or {}
    x.extend([number(s.get("BUY")),number(s.get("SELL")),
              number(review.get("market_quality")),number(a.get("price_deviation_atr")),
              number(p.get("delay_sec")),number(p.get("ask"))-number(p.get("bid")) if number(p.get("atr"))<=0 else (number(p.get("ask"))-number(p.get("bid")))/number(p.get("atr"))])
    return np.array(x,dtype=float)

def rows(payloads, symbol):
    selected={}
    for item in payloads:
        p=item.get("payload",item)
        if not isinstance(p,dict) or p.get("symbol")!=symbol: continue
        t=number(p.get("bar_time"),-1)
        obs=number(p.get("observed_at"),-1)
        outcome=(p.get("outcomes") or {}).get(str(HORIZON)) or {}
        payoff=outcome.get("payoffs") or {}
        y=[number(payoff.get(k),float("nan")) for k in SIDES]
        if (t<=0 or not 60<=obs-t<=120 or
            not all(math.isfinite(v) for v in y) or
            number(outcome.get("quote_time"),-1)<=obs):continue
        # Audit baseline is the recorded demo comparison, NOT a broker fill.
        base=((p.get("candidate") or {}).get("balanced_demo_comparison") or {}).get("direction","WAIT")
        r=(t,obs,number(outcome["quote_time"]),features(p),y,base)
        if t not in selected or obs<selected[t][1]:selected[t]=r
    return sorted(selected.values(),key=lambda r:r[0])

def split(r):
    if len(r)<300:raise ValueError("Need >=300 distinct matured bars per market")
    a,b=int(.60*len(r)),int(.80*len(r))
    tr=[x for x in r[:a] if x[2]<r[a][1]]
    va=[x for x in r[a:b] if x[2]<r[b][1]]
    te=r[b:]
    if min(len(va),len(te))<50 or len(tr)<150:
        raise ValueError("Insufficient purged chronological data")
    return tr,va,te

def arrays(r):
    return np.stack([x[3] for x in r]),np.asarray([x[4] for x in r])

def fit(model,r):
    x,y=arrays(r)
    side=np.tile([0.,1.],len(x))
    xx=np.column_stack([np.repeat(x,2,axis=0),side])
    model.fit(xx,np.clip(y.reshape(-1),-6.,6.))
    return model

def predict(model,x):
    return model.predict(np.column_stack([np.repeat(x,2,axis=0),np.tile([0.,1.],len(x))])).reshape(-1,2)

def directions(p,threshold):
    d=np.argmax(p,axis=1)
    return np.where(np.max(p,axis=1)>threshold,d,-1)

def metrics(d,y,stress=0.):
    trade=d>=0
    result=y[np.arange(len(y)),np.maximum(d,0)]-stress
    pay=np.where(trade,result,0.)
    n=int(trade.sum())
    eq=np.r_[0.,np.cumsum(pay)]
    return {"signals":n,"positive_rate":float((pay>0).sum()/n) if n else None,
            "net_atr":float(pay.sum()),"max_drawdown_atr":float((np.maximum.accumulate(eq)-eq).max())}

def score(d,y):
    ordinary=metrics(d,y)
    stressed=metrics(d,y,STRESS_ATR)
    return (stressed["net_atr"],ordinary["net_atr"]) if ordinary["signals"]>=MIN_SIGNALS and ordinary["net_atr"]>0 and stressed["net_atr"]>0 else None

def run(r):
    tr,va,te=split(r)
    models={"CatBoost":CatBoostRegressor(iterations=150,depth=4,learning_rate=.04,
                   random_seed=42,thread_count=1,verbose=False,allow_writing_files=False),
            "XGBoost":XGBRegressor(n_estimators=150,max_depth=3,learning_rate=.04,
                   n_jobs=1,random_state=42,objective="reg:squarederror")}
    xv,yv=arrays(va);xt,yt=arrays(te)
    v={};t={};lat={}
    for k,m in models.items():
        fit(m,tr);v[k]=predict(m,xv);t[k]=predict(m,xt)
        start=time.perf_counter()
        for _ in range(30):predict(m,xt[:1])
        lat[k]=1000*(time.perf_counter()-start)/30
    report={"version":VERSION,"mode":"SHADOW_ONLY","execution_authorized":False,
            "train":len(tr),"validation":len(va),"test":len(te),
            "stress_extra_atr":STRESS_ATR,"horizon_seconds":HORIZON,
            "single_prediction_ms":lat,"models":{}}
    all_candidates={}
    for name,weights in (("CatBoost",[1.]),("XGBoost",[0.]),("Hybrid",[.25,.5,.75])):
        options=[]
        for w in weights:
            pv=w*v["CatBoost"]+(1-w)*v["XGBoost"]
            for cutoff in (0.,.05,.10,.20,.30,.50,.75):
                d=directions(pv,cutoff);s=score(d,yv)
                if s is not None:options.append((s,w,cutoff))
        if not options:
            report["models"][name]={"status":"VALIDATION_REJECTED"}
            continue
        s,w,cutoff=max(options)
        dv=directions(w*v["CatBoost"]+(1-w)*v["XGBoost"],cutoff)
        dt=directions(w*t["CatBoost"]+(1-w)*t["XGBoost"],cutoff)
        report["models"][name]={"catboost_weight":w,"xgboost_weight":1-w,"threshold":cutoff,
            "validation":metrics(dv,yv),"validation_stress":metrics(dv,yv,STRESS_ATR),
            "test":metrics(dt,yt),"test_stress":metrics(dt,yt,STRESS_ATR),
            "status":"TEST_POSITIVE" if score(dt,yt) is not None else "TEST_REJECTED"}
        all_candidates[name]=(s,dt)
    base=np.asarray([SIDES.index(x[5]) if x[5] in SIDES else -1 for x in te])
    report["recorded_demo_comparison"]={"test":metrics(base,yt),"test_stress":metrics(base,yt,STRESS_ATR)}
    if all_candidates:
        # Winner chosen on validation, not test. Holdout is for veto only.
        winner=max(all_candidates,key=lambda k:all_candidates[k][0])
        chosen=report["models"][winner]
        base_stress=report["recorded_demo_comparison"]["test_stress"]["net_atr"]
        report["validation_winner"]=winner
        report["verdict"]="RESEARCH_CANDIDATE" if (chosen["status"]=="TEST_POSITIVE" and chosen["test_stress"]["net_atr"]>base_stress) else "NOT_VALIDATED"
    else:report["verdict"]="NOT_VALIDATED"
    return report

def smoke():
    rng=np.random.default_rng(42)
    samples=[]
    for i in range(700):
        x=rng.normal(size=30);y=[float(x[0]-.15),float(-x[0]-.15)]
        samples.append((i*60,i*60+60,i*60+120,x,y,"WAIT"))
    result=run(samples)
    assert result["execution_authorized"] is False
    assert result["test"]>=50
    assert result["verdict"] in ("RESEARCH_CANDIDATE","NOT_VALIDATED")
    result["synthetic_only"]=True
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input",help="Owner-local export; do not commit private data")
    p.add_argument("--symbol",choices=("XAUUSD","BTCUSD"),default="XAUUSD")
    p.add_argument("--out",required=True,help="Local report JSON")
    p.add_argument("--self-test",action="store_true")
    a=p.parse_args()
    if a.self_test:report=smoke()
    else:
        if not a.input:p.error("--input or --self-test required")
        raw=Path(a.input).read_text(encoding="utf-8-sig")
        try:data=json.loads(raw)
        except json.JSONDecodeError:data=[json.loads(line) for line in raw.splitlines() if line.strip()]
        if not isinstance(data,list):raise ValueError("Expected array or JSONL")
        report=run(rows(data,a.symbol))
        report["symbol"]=a.symbol
        report["synthetic_only"]=False
    Path(a.out).write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps(report,indent=2,allow_nan=False))
if __name__=="__main__":main()
