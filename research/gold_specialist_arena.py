"""Gold-only challenger arena. Local reports, read-only owner history.
No live votes/orders/settings. Historical results are development evidence.
"""
import argparse,getpass,json,math,os,statistics,time,sys
from pathlib import Path
os.environ["OMP_NUM_THREADS"]="1"
os.environ["MKL_NUM_THREADS"]="1"
import model_arena_worker as arena
from specialist_market import samples,features
NAMES=("ExtraTrees","HistGradientBoosting","Gold MLP","Kronos-small")
EXTRA=.10
CUTS=(.05,.10,.15,.20,.30,.40,.50,.75,1.)
def spaced(rows,limit=300):
    out=[];end=-1
    for r in rows:
        if r["time"]>end:out.append(r);end=r["end"]
    if len(out)>limit:out=[out[i*(len(out)-1)//(limit-1)] for i in range(limit)]
    return out
def data(bars):
    import numpy as np
    bars=sorted({b["time"]:b for b in bars}.values(),key=lambda b:b["time"])
    by={b["time"]:i for i,b in enumerate(bars)};rows=[]
    for r in samples(bars):
        i=by[r["time"]]
        if i<127:continue
        w=bars[i-127:i+1]
        if w[-1]["time"]-w[0]["time"]!=127*60:continue
        f=features(w[-61:])
        if f is None:continue
        _,atr=f;close=np.asarray([b["close"] for b in w]);delta=np.diff(close)/atr
        x=list(r["x"])
        for k in (5,15,30,60,120):
            d=delta[-k:];p=close[-k:]
            x.extend([float(d.mean()),float(d.std()),float((close[-1]-p.min())/atr),float((p.max()-close[-1])/atr),float((d>0).mean())])
        rows.append({**r,"f":np.clip(x,-20,20),"atr":atr,"window":w})
    n=len(rows)
    if n<800:return None
    a,b,c=int(n*.50),int(n*.65),int(n*.80)
    tr=[r for r in rows[:a] if r["end"]<rows[a]["time"]]
    cal=spaced([r for r in rows[a:b] if r["end"]<rows[b]["time"]])
    val=spaced([r for r in rows[b:c] if r["end"]<rows[c]["time"]])
    test=spaced(rows[c:])
    if len(tr)<300 or min(map(len,(cal,val,test)))<40:return None
    return tr,cal,val,test
def matrix(rows):
    import numpy as np
    return np.asarray([r["f"] for r in rows],dtype=float)
def fit(name,tr):
    import numpy as np
    x=matrix(tr)
    # Train on side-specific net outcome rather than generic price direction.
    y=np.clip([[r["buy"]-EXTRA,r["sell"]-EXTRA] for r in tr],-6,6)
    if name=="ExtraTrees":
        from sklearn.ensemble import ExtraTreesRegressor
        m=ExtraTreesRegressor(n_estimators=160,max_depth=7,min_samples_leaf=32,max_features=.7,n_jobs=1,random_state=42).fit(x,y)
        return lambda rows:m.predict(matrix(rows))
    if name=="HistGradientBoosting":
        from sklearn.ensemble import HistGradientBoostingRegressor
        ms=[HistGradientBoostingRegressor(max_iter=100,max_leaf_nodes=7,min_samples_leaf=40,l2_regularization=5,learning_rate=.04,early_stopping=False,random_state=42).fit(x,y[:,i]) for i in range(2)]
        return lambda rows:np.column_stack([m.predict(matrix(rows)) for m in ms])
    if name=="Gold MLP":
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.neural_network import MLPRegressor
        m=make_pipeline(StandardScaler(),MLPRegressor(hidden_layer_sizes=(32,16),alpha=2,max_iter=100,early_stopping=False,random_state=42)).fit(x,y)
        return lambda rows:m.predict(matrix(rows))
    # Official Kronos source is installed separately in a dedicated env.
    import torch,pandas as pd
    torch.set_num_threads(1)
    source=Path(__file__).resolve().parents[1]/".kronos-src"
    if not source.is_dir():raise RuntimeError("Missing .kronos-src")
    sys.path.insert(0,str(source))
    from model import Kronos,KronosTokenizer,KronosPredictor
    tok=KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    mod=Kronos.from_pretrained("NeoQuasar/Kronos-small")
    p=KronosPredictor(mod,tok,device="cpu",max_context=128)
    def predict(rows):
        out=[]
        for r in rows:
            w=r["window"];df=pd.DataFrame(w)[["open","high","low","close"]]
            t=pd.Series(pd.to_datetime([b["time"] for b in w],unit="s"))
            future=pd.Series(pd.to_datetime([w[-1]["time"]+60*k for k in (1,2,3)],unit="s"))
            torch.manual_seed(int(r["time"])%2147483647)
            forecast=p.predict(df=df,x_timestamp=t,y_timestamp=future,pred_len=3,T=1.,top_p=.9,sample_count=3,verbose=False)
            move=(float(forecast["close"].iloc[-1])-float(forecast["open"].iloc[0]))/r["atr"]
            cost=w[-1]["spread_points"]*w[-1]["point_size"]/r["atr"]+EXTRA
            out.append([move-cost,-move-cost])
        return np.asarray(out)
    return predict
def checked(p,rows):
    import numpy as np
    z=np.asarray(p(rows),dtype=float)
    if z.shape!=(len(rows),2) or not np.isfinite(z).all():raise ValueError("Invalid scores")
    return z
def calibrate(rows,z):
    import numpy as np
    y=np.asarray([[r["buy"]-EXTRA,r["sell"]-EXTRA] for r in rows])
    coeff=[]
    for i in range(2):
        x=np.column_stack([np.ones(len(z)),np.clip(z[:,i],-6,6)])
        coeff.append(np.linalg.solve(x.T@x+np.diag([0.,10.]),x.T@np.clip(y[:,i],-6,6)))
    return lambda z:np.column_stack([a[0]+a[1]*np.clip(z[:,i],-6,6) for i,a in enumerate(coeff)])
def metrics(rows,z,cut):
    net=peak=dd=0.;n=positive=0
    for r,(buy,sell) in zip(rows,z):
        if max(buy,sell)<=cut:continue
        v=r["buy" if buy>sell else "sell"]-EXTRA
        net+=v;n+=1;positive+=int(v>0);peak=max(peak,net);dd=max(dd,peak-net)
    return {"signals":n,"positive":positive,"net_atr":round(net,5),"drawdown_atr":round(dd,5)}
def evaluate(name,parts):
    tr,cal,val,test=parts
    start=time.monotonic();p=fit(name,tr)
    print("XAUUSD",name,"FIT_COMPLETE",flush=True)
    c=checked(p,cal);v=checked(p,val)
    transforms=[("raw",lambda z:z),("calibrated",calibrate(cal,c))]
    options=[];diagnostics=[]
    for label,transform in transforms:
        for cut in CUTS:
            m=metrics(val,transform(v),cut);diagnostics.append({"mode":label,"cut":cut,**m})
            if m["signals"]>=20 and m["net_atr"]>0:options.append((m["net_atr"]-.25*m["drawdown_atr"],label,transform,cut,m))
    common={"setup_sec":round(time.monotonic()-start,2),"validation_windows":len(val),"test_windows":len(test),"diagnostics":diagnostics}
    if not options:return {**common,"status":"VALIDATION_REJECTED"}
    _,label,transform,cut,m=max(options,key=lambda r:r[0])
    times=[];z=[]
    for row in test:
        t=time.perf_counter();z.append(transform(checked(p,[row]))[0]);times.append((time.perf_counter()-t)*1000)
    final=metrics(test,z,cut);p95=sorted(times)[math.ceil(.95*len(times))-1]
    passed=final["signals"]>=20 and final["net_atr"]>0 and p95<=500
    return {**common,"status":"PROSPECTIVE_TEST_REQUIRED" if passed else "TEST_REJECTED","mode":label,"threshold":cut,"validation":m,"test":final,"median_ms":round(statistics.median(times),2),"p95_ms":round(p95,2)}
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--models",nargs="+",choices=NAMES,default=list(NAMES[:3]));a=ap.parse_args()
    token=arena.login(input("Owner email: ").strip(),getpass.getpass("Owner password: "))
    _,bars=arena.archive("XAUUSD",token);parts=data(bars)
    report={"market":"XAUUSD","execution_authorized":False,"historical_development_only":True,"candles":len(bars),"models":{}}
    if parts:report["splits"]=[{"start":r[0]["time"],"end":r[-1]["end"],"count":len(r)} for r in parts]
    for name in a.models:
        print("XAUUSD",name,"GOLD_ONLY_TEST",flush=True)
        try:r=evaluate(name,parts) if parts else {"status":"INSUFFICIENT_HISTORY"}
        except Exception as e:r={"status":"ERROR","detail":type(e).__name__+": "+str(e)[:300]}
        report["models"][name]=r;print("XAUUSD",name,json.dumps({k:v for k,v in r.items() if k!="diagnostics"},allow_nan=False),flush=True)
    f=Path(__file__).resolve().parent/("gold_specialists_"+time.strftime("%Y%m%d_%H%M%S")+".json")
    f.write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8");print("REPORT",f,flush=True)
if __name__=="__main__":main()
