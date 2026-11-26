"""Independent public XAUUSD M1 walk-forward experiment. No owner login, no trading.
200 scored cases per model; 40 prior warmup cases. Four frozen foundation models
learn through a small rolling calibration layer; CatBoost is actually refitted.
"""
import argparse,json,math,os,statistics,time
from pathlib import Path
os.environ.setdefault("OMP_NUM_THREADS","1")
os.environ.setdefault("MKL_NUM_THREADS","1")
import numpy as np
from arena_live_worker import forecast
from specialist_market import features,samples
NAMES=("TiRex-2","Chronos-2","Chronos-Bolt Tiny","TimesFM-2.5","CatBoost")
def read_data(path):
    data=json.loads(Path(path).read_text())
    sides=[{int(r[0]//1000):r for r in data[k] if len(r)>=5} for k in ("bid","ask")]
    rows=[];b,a=sides
    for t in sorted(b.keys()&a.keys()):
        x,y=b[t],a[t]
        if t%60 or min(x[1:5]+y[1:5])<=0 or y[1]<x[1] or y[4]<x[4]:continue
        rows.append(dict(time=t,open=x[1],high=x[2],low=x[3],close=x[4],tick_volume=x[5] if len(x)>5 else 0,
                         spread_points=y[4]-x[4],point_size=1.,ask_open=y[1],ask_close=y[4]))
    return rows
def calibration(history,z):
    if len(history)<30:return z
    h=np.asarray(history[-100:],dtype=float);x=h[:,0];y=np.clip(h[:,1],-6,6)
    # Ridge shrinkage toward unchanged predictions; fixed before testing.
    a=np.column_stack([np.ones(len(x)),x])
    coef=np.linalg.solve(a.T@a+np.diag([10.,20.]),a.T@y+np.array([0.,20.]))
    return float(coef[0]+np.clip(coef[1],-1,2)*z)
def decision(z,spread):
    return 1 if z>spread+.10 else -1 if z<-(spread+.10) else 0
def metrics(rows,key):
    sides=[r[key] for r in rows];selected=[r for r,s in zip(rows,sides) if s]
    pay=[r[key+"_net"] for r in rows];eq=peak=dd=0.
    for v in pay:eq+=v;peak=max(peak,eq);dd=max(dd,peak-eq)
    return dict(cases=len(rows),signals=len(selected),direction_accuracy=sum(r[key]*r["move"]>0 for r in selected)/len(selected) if selected else None,
                net_atr=round(eq,5),drawdown_atr=round(dd,5),positive=sum(v>0 for v in pay))
def main():
    p=argparse.ArgumentParser();p.add_argument("--data",required=True);p.add_argument("--model",choices=NAMES,required=True);p.add_argument("--output",required=True);a=p.parse_args()
    bars=read_data(a.data);train=samples(bars)
    # Same non-overlapping windows for all five; no random shuffled splits.
    indices=[];last=-1
    for i in range(max(127,int(len(bars)*.55)),len(bars)-3):
        w=bars[i-127:i+4]
        if bars[i]["time"]<=last or any(v["time"]-u["time"]!=60 for u,v in zip(w,w[1:])):continue
        if features(w[67:128]) is None:continue
        indices.append(i);last=bars[i+3]["time"]
    if len(indices)<240:raise RuntimeError("Insufficient continuous public M1 history for 240 disjoint cases")
    indices=[indices[j*(len(indices)-1)//239] for j in range(240)]
    models={};history=[];results=[];model=None
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("w",encoding="utf-8") as f:
        for n,i in enumerate(indices):
            w=bars[i-127:i+1];x,atr=features(w[-61:])
            ent,ext=bars[i+2],bars[i+3];t=time.perf_counter()
            if a.model=="CatBoost":
                if model is None or n%20==0:
                    from catboost import CatBoostRegressor
                    tr=[r for r in train if r["end"]<w[-1]["time"]][-8000:]
                    if len(tr)<500:raise RuntimeError("Insufficient training history")
                    model=CatBoostRegressor(iterations=80,depth=4,thread_count=1,learning_rate=.05,verbose=False,allow_writing_files=False,random_seed=42)
                    model.fit([r["x"] for r in tr],[max(-6,min(6,r["y"])) for r in tr])
                z=float(model.predict([x])[0])
            else:
                z=forecast(a.model,[(b["close"]-w[-1]["close"])/atr for b in w],models)
            calibrated=calibration(history,z);ms=(time.perf_counter()-t)*1000
            if not math.isfinite(z) or not math.isfinite(calibrated):raise ValueError("Nonfinite prediction")
            # Use the second future open to allow one minute for forecast arrival.
            move=(ext["close"]-ent["open"])/atr
            buy=(ext["close"]-ent["ask_open"])/atr-.10
            sell=(ent["open"]-ext["ask_close"])/atr-.10
            raw=decision(z,x[9]);cal=decision(calibrated,x[9])
            def net(s):return buy if s>0 else sell if s<0 else 0.
            row=dict(model=a.model,case=n-39,bar=w[-1]["time"],raw_score=z,calibrated_score=calibrated,move=move,raw=raw,cal=cal,
                     raw_net=net(raw),cal_net=net(cal),elapsed_ms=round(ms,2))
            if n>=40:
                results.append(row);f.write(json.dumps(row,allow_nan=False)+"\n");f.flush()
                print("TEST_COMPLETE",n-39,"/200",json.dumps(row,allow_nan=False),flush=True)
            # Outcomes are available before the next selected non-overlapping origin.
            history.append((z,move))
    summary=dict(model=a.model,market="XAUUSD",source="Dukascopy public bid/ask M1",execution_authorized=False,
                 method="past-only rolling calibration; CatBoost refits; delayed entry price proxy",
                 raw=metrics(results,"raw"),calibrated=metrics(results,"cal"),median_elapsed_ms=statistics.median(r["elapsed_ms"] for r in results))
    out.with_suffix(".summary.json").write_text(json.dumps(summary,indent=2,allow_nan=False),encoding="utf-8")
    print("FINAL",json.dumps(summary,allow_nan=False),flush=True)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"],"a") as f:f.write("### "+a.model+"\n\n```json\n"+json.dumps(summary,indent=2)+"\n```\n")
if __name__=="__main__":main()
