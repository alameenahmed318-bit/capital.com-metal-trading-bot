"""Gold M1 financial foundation model benchmark. Read-only history; no live votes.
Evaluate 1/3/5 minute outcomes from a single five-step forecast.
Previous historical archive may have been inspected: development evidence only.
"""
import argparse,getpass,json,math,os,statistics,sys,time
from pathlib import Path
os.environ["OMP_NUM_THREADS"]="1"
os.environ["MKL_NUM_THREADS"]="1"
import model_arena_worker as arena
from specialist_market import features
HORIZONS=(1,3,5)
MODELS=("Kronos-small","Kronos-base")
EXTRA=.10
CUTS=(.05,.10,.15,.20,.30,.50,.75,1.)
def dataset(bars,limit):
    bars=sorted({b["time"]:b for b in bars if b["time"]+60<=time.time()}.values(),key=lambda b:b["time"])
    rows=[]
    for i in range(127,len(bars)-5):
        w=bars[i-127:i+1];future=bars[i+1:i+6]
        if future[-1]["time"]-w[0]["time"]!=132*60:continue
        if any(b["time"]-a["time"]!=60 for a,b in zip(w+future,(w+future)[1:])):continue
        f=features(w[-61:])
        if f is None:continue
        _,atr=f;entry=future[0]
        if any(b["point_size"]<=0 or b["spread_points"]<0 for b in future):continue
        labels={}
        for h in HORIZONS:
            exit=future[h-1];move=(exit["close"]-entry["open"])/atr
            labels[h]=(move,move-entry["spread_points"]*entry["point_size"]/atr,-move-exit["spread_points"]*exit["point_size"]/atr)
        rows.append({"time":w[-1]["time"],"end":future[-1]["time"],"window":w,"atr":atr,"labels":labels})
    n=len(rows)
    if n<800:return None
    def subset(r):
        out=[];end=-1
        for x in r:
            if x["time"]>end:out.append(x);end=x["end"]
        if len(out)>limit:out=[out[i*(len(out)-1)//(limit-1)] for i in range(limit)]
        return out
    a,b=int(n*.6),int(n*.8)
    val=subset([r for r in rows[a:b] if r["end"]<rows[b]["time"]])
    test=subset(rows[b:])
    return (val,test) if min(len(val),len(test))>=40 else None
def load(name):
    import torch,pandas as pd,numpy as np
    torch.set_num_threads(1)
    source=Path(__file__).resolve().parents[1]/".kronos-src"
    if not (source/"model").is_dir():raise RuntimeError("Install official Kronos source in .kronos-src")
    sys.path.insert(0,str(source))
    from model import Kronos,KronosTokenizer,KronosPredictor
    tok=KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model=Kronos.from_pretrained("NeoQuasar/"+name)
    p=KronosPredictor(model,tok,device="cpu",max_context=128)
    def forecast(r):
        w=r["window"]
        # MT5 tick volume is not exchange volume; OHLC only, no invented values.
        df=pd.DataFrame(w)[["open","high","low","close"]]
        x=pd.Series(pd.to_datetime([b["time"] for b in w],unit="s"))
        y=pd.Series(pd.to_datetime([r["time"]+60*h for h in range(1,6)],unit="s"))
        torch.manual_seed(int(r["time"])%2147483647)
        with torch.inference_mode():
            pred=p.predict(df=df,x_timestamp=x,y_timestamp=y,pred_len=5,T=1.,top_p=.9,sample_count=3,verbose=False)
        if len(pred)!=5:raise ValueError("Unexpected forecast length")
        op=float(pred["open"].iloc[0])
        z={h:(float(pred["close"].iloc[h-1])-op)/r["atr"] for h in HORIZONS}
        if not all(math.isfinite(v) for v in z.values()):raise ValueError("Nonfinite forecast")
        return z
    return forecast
def score(rows,pred,h,cut,forced=False):
    n=correct=positive=0;gross=net=stress=peak=dd=0.
    for r,z in zip(rows,pred):
        v=z[h]
        if not math.isfinite(v) or v==0 or (not forced and abs(v)<=cut):continue
        move,buy,sell=r["labels"][h]
        raw=move if v>0 else -move;pay=buy if v>0 else sell
        n+=1;correct+=int(raw>0);positive+=int(pay-EXTRA>0);gross+=raw;net+=pay;stress+=pay-EXTRA
        peak=max(peak,stress);dd=max(dd,peak-stress)
    return {"windows":len(rows),"signals":n,"direction_accuracy":round(correct/n,5) if n else None,
            "positive_after_cost":positive,"gross_atr":round(gross,5),"spread_net_atr":round(net,5),
            "stress_net_atr":round(stress,5),"drawdown_atr":round(dd,5)}
def evaluate(name,parts):
    val,test=parts
    start=time.monotonic();p=load(name);setup=time.monotonic()-start
    p(val[0])
    result=[];times=[]
    for stage,rows in (("VALIDATION",val),("TEST",test)):
        z=[]
        for i,r in enumerate(rows,1):
            t=time.perf_counter();z.append(p(r));times.append((time.perf_counter()-t)*1000)
            if i%20==0 or i==len(rows):print(name,stage,i,"/",len(rows),flush=True)
        result.append(z)
    pv,pt=result
    options=[];reports={}
    for h in HORIZONS:
        grid=[]
        for cut in CUTS:
            m=score(val,pv,h,cut);grid.append({"cut":cut,**m})
            if m["signals"]>=20 and m["stress_net_atr"]>0:options.append((m["stress_net_atr"]-.25*m["drawdown_atr"],h,cut,m))
        baseline=[{j:(r["window"][-1]["close"]-r["window"][-6]["close"])/r["atr"] for j in HORIZONS} for r in test]
        reports[str(h)]={"validation_grid":grid,"direction_only":score(test,pt,h,0,True),"momentum_baseline":score(test,baseline,h,0,True)}
    p95=sorted(times)[math.ceil(.95*len(times))-1]
    selected=None;status="VALIDATION_REJECTED"
    if options:
        _,h,cut,m=max(options,key=lambda q:q[0]);final=score(test,pt,h,cut)
        selected={"horizon":h,"threshold":cut,"validation":m,"test":final}
        status="FRESH_DATA_TEST_REQUIRED" if final["signals"]>=20 and final["stress_net_atr"]>0 and p95<=500 else "TEST_OR_SPEED_REJECTED"
    return {"status":status,"setup_sec":round(setup,2),"median_ms":round(statistics.median(times),2),
            "p95_ms":round(p95,2),"horizons":reports,"selected_policy":selected}
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--models",nargs="+",choices=MODELS,default=list(MODELS));ap.add_argument("--windows",type=int,default=100);a=ap.parse_args()
    if not 40<=a.windows<=300:ap.error("--windows must be 40..300")
    token=arena.login(input("Owner email: ").strip(),getpass.getpass("Owner password: "))
    _,bars=arena.archive("XAUUSD",token);parts=dataset(bars,a.windows)
    out={"market":"XAUUSD","timeframe":"M1","historical_development_only":True,"execution_authorized":False,
         "candles":len(bars),"extra_cost_atr":EXTRA,"models":{}}
    if parts:out["split_ranges"]=[{"start":r[0]["time"],"end":r[-1]["end"],"count":len(r)} for r in parts]
    for name in a.models:
        print("XAUUSD",name,"FINANCIAL_MODEL_TEST",flush=True)
        try:r=evaluate(name,parts) if parts else {"status":"INSUFFICIENT_HISTORY"}
        except Exception as e:r={"status":"ERROR","detail":type(e).__name__+": "+str(e)[:400]}
        out["models"][name]=r
        compact={k:v for k,v in r.items() if k!="horizons"}
        if "horizons" in r:compact["horizons"]={k:{j:v for j,v in h.items() if j!="validation_grid"} for k,h in r["horizons"].items()}
        print("XAUUSD",name,json.dumps(compact,allow_nan=False),flush=True)
    f=Path(__file__).resolve().parent/("gold_financial_"+time.strftime("%Y%m%d_%H%M%S")+".json")
    f.write_text(json.dumps(out,indent=2,allow_nan=False),encoding="utf-8");print("REPORT",f,flush=True)
if __name__=="__main__":main()
