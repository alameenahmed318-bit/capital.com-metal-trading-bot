"""GOLD HARD V2: strictly chronological, public September 2026 M1.
Research only. 60 warmup origins and 200 non-overlapping scored outcome windows
per model. Compare forced direction and past-only coverage-controlled decisions.
No live connections, no orders, no broker account data.
"""
import argparse,json,math,os,statistics,time
from pathlib import Path
os.environ.setdefault("OMP_NUM_THREADS","1")
os.environ.setdefault("MKL_NUM_THREADS","1")
import numpy as np
from gold_1000_walkforward import read_data,NAMES
from arena_live_worker import forecast
from specialist_market import features

COSTS=("normal","wide","extreme")
POLICIES=("raw_forced","learned_forced","raw_coverage","learned_coverage")
def direction(z):return 1 if z>=0 else -1
def vector(z,x):
    return np.asarray([1.,np.clip(z,-6,6),x[0],x[1],x[2],x[4],x[5],2*x[6]-1,x[7],x[9],x[10],x[11]],dtype=float)
def fit_adapter(history):
    if len(history)<30:return None
    rows=history[-120:]
    X=np.asarray([r[0] for r in rows]);Y=np.clip([r[1] for r in rows],-6,6)
    weights=np.exp(np.linspace(-1.5,0,len(rows)))
    prior=np.zeros(X.shape[1]);prior[1]=1.
    penalty=np.diag([12.,24.]+[40.]*(X.shape[1]-2))
    return np.linalg.solve(X.T@(X*weights[:,None])+penalty,X.T@(weights*Y)+penalty@prior)
def coverage(z,past):
    # Target 60% using only the last 100 recorded scores; realized coverage may vary.
    if len(past)<30:return direction(z)
    return direction(z) if abs(z)>=float(np.quantile(np.abs(past[-100:]),.40)) else 0
def payoff(bars,i,atr,side,scenario):
    if not side:return 0.
    delay,mult,slip={"normal":(2,1.,.10),"wide":(2,2.,.25),"extreme":(3,3.,.50)}[scenario]
    ent,ext=bars[i+delay],bars[i+3]
    cost=(ent["ask_open"]-ent["open"]) if side>0 else (ext["ask_close"]-ext["close"])
    return (side*(ext["close"]-ent["open"])-mult*cost)/atr-slip
def metrics(rows,p,scenario):
    pay=[r["payoffs"][p][scenario] for r in rows];sel=[r for r in rows if r["decisions"][p]]
    eq=peak=dd=0.
    for v in pay:eq+=v;peak=max(eq,peak);dd=max(dd,peak-eq)
    return dict(cases=len(rows),signals=len(sel),coverage=len(sel)/max(1,len(rows)),direction_accuracy=sum(r["decisions"][p]*r["move"]>0 for r in sel)/len(sel) if sel else None,net_atr=round(eq,5),drawdown_atr=round(dd,5),positive=sum(v>0 for v in pay))
def main():
    p=argparse.ArgumentParser();p.add_argument("--data",required=True);p.add_argument("--model",choices=NAMES,required=True);p.add_argument("--output",required=True);a=p.parse_args()
    bars=read_data(a.data);cut=int(len(bars)*.40)
    allrows=[]
    for i in range(127,len(bars)-3):
        w=bars[i-127:i+4]
        if any(v["time"]-u["time"]!=60 for u,v in zip(w,w[1:])):continue
        f=features(w[67:128])
        if f is None:continue
        x,atr=f
        move=(bars[i+3]["close"]-bars[i+2]["open"])/atr
        allrows.append((i,x,atr,move))
    earlier=[r for r in allrows if r[0]+3<cut]
    if len(earlier)<1000:raise RuntimeError("Insufficient initial history")
    volq=float(np.quantile([r[2] for r in earlier],.75))
    spreadq=float(np.quantile([r[1][9] for r in earlier],.75))
    trendq=float(np.quantile([abs(r[1][2]) for r in earlier],.65))
    def regime(x,atr):
        if x[9]>=spreadq:return "wide_spread"
        if atr>=volq:return "high_volatility"
        if abs(x[2])>=trendq:return "trend"
        return "range"
    eligible=[];last=-1
    for r in allrows:
        i,x,atr,move=r
        if i<cut or bars[i]["time"]<=last:continue
        eligible.append(r);last=bars[i+3]["time"]
    if len(eligible)<260:raise RuntimeError("Insufficient continuous chronological cases")
    # Equal chronological sampling across the full held-out period, not selected on outcomes.
    selected=[eligible[j*(len(eligible)-1)//259] for j in range(260)]
    models={};history=[];rawpast=[];learnedpast=[];results=[];model=None;rawmodel=None;fit_times=[]
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("w",encoding="utf-8") as f:
        for n,(i,x,atr,move) in enumerate(selected):
            w=bars[i-127:i+1]
            if a.model=="CatBoost" and (model is None or n%20==0):
                from catboost import CatBoostRegressor
                tr=[r for r in allrows if r[0]+3<i][-8000:]
                if len(tr)<500:raise RuntimeError("Insufficient training history")
                t=time.perf_counter()
                model=CatBoostRegressor(iterations=100,depth=5,thread_count=1,learning_rate=.045,verbose=False,allow_writing_files=False,random_seed=42)
                model.fit([r[1] for r in tr],[max(-6,min(6,r[3])) for r in tr])
                if rawmodel is None:rawmodel=model
                fit_times.append((time.perf_counter()-t)*1000)
            t=time.perf_counter()
            if a.model=="CatBoost":
                raw=float(rawmodel.predict([x])[0]);z=float(model.predict([x])[0])
            else:
                z=forecast(a.model,[(b["close"]-w[-1]["close"])/atr for b in w],models);raw=z
            v=vector(z,x);coef=fit_adapter(history)
            learned=float(v@coef) if coef is not None else z
            ms=(time.perf_counter()-t)*1000
            if not all(math.isfinite(k) for k in (raw,z,learned)):raise ValueError("Nonfinite prediction")
            decisions={"raw_forced":direction(raw),"learned_forced":direction(learned),"raw_coverage":coverage(raw,rawpast),"learned_coverage":coverage(learned,learnedpast)}
            row=dict(model=a.model,case=n-59,bar=w[-1]["time"],regime=regime(x,atr),raw_score=raw,refit_score=z,learned_score=learned,move=move,
                     decisions=decisions,payoffs={k:{s:payoff(bars,i,atr,d,s) for s in COSTS} for k,d in decisions.items()},elapsed_ms=round(ms,3))
            if n>=60:
                results.append(row);f.write(json.dumps(row,allow_nan=False)+"\n");f.flush()
                print("TEST_COMPLETE",n-59,"/200",json.dumps(row,allow_nan=False),flush=True)
            # Only past, resolved outcomes enter subsequent origins.
            history.append((v,move));rawpast.append(raw);learnedpast.append(learned)
    def report(rows):return {k:{s:metrics(rows,k,s) for s in COSTS} for k in POLICIES}
    byregime={g:report([r for r in results if r["regime"]==g]) for g in sorted({r["regime"] for r in results})}
    overall=report(results)
    lf=overall["learned_forced"]["extreme"];rf=overall["raw_forced"]["extreme"];lc=overall["learned_coverage"]["extreme"];rc=overall["raw_coverage"]["extreme"]
    # Research candidate only. Never promote from this one test.
    candidate=lf["net_atr"]>max(0,rf["net_atr"]) and lc["net_atr"]>max(0,rc["net_atr"]) and lc["coverage"]>=.50 and lc["drawdown_atr"]<=rc["drawdown_atr"]
    summary=dict(version="GOLD_HARD_V2",model=a.model,market="XAUUSD",source="September 2026 public bid/ask M1",execution_authorized=False,
        method="past-only ridge adapter; CatBoost fixed reference vs rolling refits; 4 policies x 3 execution scenarios",cases=len(results),
        overall=overall,by_regime=byregime,median_elapsed_ms=statistics.median(r["elapsed_ms"] for r in results),
        p95_elapsed_ms=float(np.quantile([r["elapsed_ms"] for r in results],.95)),catboost_refits=len(fit_times),candidate_for_further_research=bool(candidate))
    out.with_suffix(".summary.json").write_text(json.dumps(summary,indent=2,allow_nan=False),encoding="utf-8")
    print("FINAL",json.dumps(summary,allow_nan=False),flush=True)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"],"a") as f:f.write("### "+a.model+"\n\n```json\n"+json.dumps(summary,indent=2)+"\n```\n")
if __name__=="__main__":main()
