"""ALZOOL model rehabilitation V2. Research only. Never submits live forecasts.
Historical holdout has been examined by V1: V2 results are development evidence,
not independent proof. Freeze the selected policy before a new prospective test.
"""
import argparse,getpass,json,math,time,statistics,gc
from pathlib import Path
import fast_challenger_worker as base
import model_arena_worker as arena

TREES=("CatBoost","LightGBM","XGBoost")
NAMES=TREES+base.NAMES
CUTS=(.05,.10,.15,.20,.30,.40,.50,.75,1.0)
EXTRA=.10

def nonoverlap(rows,limit=384):
    out=[];end=-1
    for r in rows:
        if r["time"]>end:out.append(r);end=r["end"]
    if len(out)>limit:out=[out[i*(len(out)-1)//(limit-1)] for i in range(limit)]
    return out

def split(bars):
    import numpy as np
    bars=sorted({b["time"]:b for b in bars}.values(),key=lambda b:b["time"])
    by={b["time"]:i for i,b in enumerate(bars)}
    rows=[]
    for r in base.samples(bars):
        i=by[r["time"]]
        if i<511:continue
        w=bars[i-511:i+1]
        if w[-1]["time"]-w[0]["time"]!=511*60:continue
        f=base.features(w[-61:])
        if f is None:continue
        x=np.asarray([(b["close"]-w[-1]["close"])/f[1] for b in w],dtype=np.float32)
        if np.isfinite(x).all():rows.append({**r,"series":x})
    n=len(rows)
    if n<800:return None
    a,b,c=int(n*.50),int(n*.65),int(n*.80)
    tr=[r for r in rows[:a] if r["end"]<rows[a]["time"]]
    cal=nonoverlap([r for r in rows[a:b] if r["end"]<rows[b]["time"]])
    val=nonoverlap([r for r in rows[b:c] if r["end"]<rows[c]["time"]])
    test=nonoverlap(rows[c:])
    if len(tr)<300 or min(map(len,(cal,val,test)))<40:return None
    return tr,cal,val,test

def tree(name,train,depth):
    import numpy as np
    if name=="CatBoost":
        from catboost import CatBoostRegressor
        m=CatBoostRegressor(iterations=120,depth=depth,learning_rate=.03,thread_count=1,verbose=False,allow_writing_files=False,random_seed=42)
    elif name=="LightGBM":
        from lightgbm import LGBMRegressor
        m=LGBMRegressor(n_estimators=120,max_depth=depth,num_leaves=2**depth-1,min_child_samples=40,reg_lambda=3,learning_rate=.03,n_jobs=1,verbosity=-1,random_state=42)
    else:
        from xgboost import XGBRegressor
        m=XGBRegressor(n_estimators=120,max_depth=depth,min_child_weight=20,reg_lambda=3,learning_rate=.03,n_jobs=1,random_state=42,tree_method="hist")
    m.fit(np.array([r["x"] for r in train]),np.clip([r["y"] for r in train],-6,6))
    return lambda rows:m.predict(np.asarray([r["x"] for r in rows]))

def neural(name,train):
    import torch,numpy as np
    from torch import nn
    torch.manual_seed(42)
    def inputs(rows):
        series=np.stack([r["series"][-97:] for r in rows])
        change=np.clip(np.diff(series,axis=1),-6,6)
        level=np.clip(series[:,1:],-20,20)/5
        return torch.tensor(np.stack([change,level],axis=-1),dtype=torch.float32),torch.tensor(np.asarray([r["x"] for r in rows]),dtype=torch.float32)
    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            if name=="GRU":self.seq=nn.GRU(2,24,batch_first=True)
            else:
                layers=[]
                for i,d in enumerate((1,2,4,8,16)):
                    layers.extend([nn.ConstantPad1d((2*d,0),0),nn.Conv1d(2 if i==0 else 24,24,3,dilation=d),nn.GELU()])
                self.seq=nn.Sequential(*layers)
            self.head=nn.Sequential(nn.Linear(24+12,24),nn.GELU(),nn.Dropout(.1),nn.Linear(24,1))
        def forward(self,x,f):
            z=self.seq(x)[0][:,-1,:] if name=="GRU" else self.seq(x.transpose(1,2))[:,:,-1]
            return self.head(torch.cat([z,f],dim=1)).squeeze(-1)
    m=Net();opt=torch.optim.AdamW(m.parameters(),lr=.0005,weight_decay=.01)
    x,f=inputs(train);y=torch.tensor(np.clip([r["y"] for r in train],-6,6),dtype=torch.float32)
    for _ in range(10):
        for ix in torch.randperm(len(x)).split(128):
            opt.zero_grad();loss=nn.functional.smooth_l1_loss(m(x[ix],f[ix]),y[ix]);loss.backward()
            torch.nn.utils.clip_grad_norm_(m.parameters(),1.);opt.step()
    m.eval()
    def predict(rows):
        with torch.inference_mode():return m(*inputs(rows)).numpy()
    return predict

def raw_predictor(name,train):
    if name in TREES:
        return [("shallow",tree(name,train,2)),("regularized",tree(name,train,4))]
    if name in ("TCN","GRU"):
        original=base.neural(name,train)
        return [("original",lambda rows:original([r["series"] for r in rows])),("returns+market_features",neural(name,train))]
    p=base.pretrained(name)
    return [("pretrained",lambda rows:p([r["series"] for r in rows]))]

def predict_checked(p,rows):
    import numpy as np
    values=[]
    for i in range(0,len(rows),16):values.extend(np.asarray(p(rows[i:i+16]),dtype=float).reshape(-1))
    v=np.asarray(values)
    if len(v)!=len(rows) or not np.isfinite(v).all():raise ValueError("invalid predictions")
    return v

def calibrator(kind,rows,p):
    import numpy as np
    def design(rows,p):
        p=np.clip(np.asarray(p),-6,6)
        if kind=="affine":return np.column_stack([np.ones(len(p)),p])
        # Interactions condition the forecast on momentum and volatility;
        # no future data or labels are used to calculate inputs.
        f=np.array([r["x"] for r in rows])
        return np.column_stack([np.ones(len(p)),p,p*f[:,1],p*f[:,3],p*f[:,7],p*f[:,9]])
    if kind=="raw":return lambda rows,p:np.asarray(p)
    x=design(rows,p);y=np.clip([r["y"] for r in rows],-6,6)
    reg=np.eye(x.shape[1])*10;reg[0,0]=0
    beta=np.linalg.solve(x.T@x+reg,x.T@y)
    return lambda rows,p:np.clip(design(rows,p)@beta,-6,6)

def metrics(rows,p,cut):
    total=peak=dd=0.;count=positive=0
    for r,v in zip(rows,p):
        if not math.isfinite(float(v)) or abs(v)<=cut:continue
        cost=r["buy" if v>0 else "sell"]-EXTRA
        count+=1;positive+=int(cost>0);total+=cost;peak=max(peak,total);dd=max(dd,peak-total)
    return {"signals":count,"positive":positive,"net_atr":round(total,5),"drawdown_atr":round(dd,5)}

def evaluate(name,parts):
    tr,cal,val,test=parts
    start=time.monotonic();choices=[];diagnostics=[]
    for variant,p in raw_predictor(name,tr):
        pc=predict_checked(p,cal);pv=predict_checked(p,val)
        for kind in ("raw","affine","conditional"):
            convert=calibrator(kind,cal,pc);v=convert(val,pv)
            grid=[(cut,metrics(val,v,cut)) for cut in CUTS]
            diagnostics.append({"variant":variant,"calibration":kind,"validation":grid})
            for cut,m in grid:
                if m["signals"]>=20 and m["net_atr"]>0:
                    choices.append((m["net_atr"]-.25*m["drawdown_atr"],variant,kind,cut,m,p,convert))
    result={"setup_sec":round(time.monotonic()-start,2),"calibration_windows":len(cal),"validation_windows":len(val),"test_windows":len(test),"extra_cost_atr":EXTRA,"diagnostics":diagnostics}
    if not choices:return {**result,"status":"VALIDATION_REJECTED"}
    _,variant,kind,cut,m,p,convert=max(choices,key=lambda r:r[0])
    # Test ONLY the single validation-selected variant. No retry on test.
    timings=[];scores=[]
    for row in test:
        t=time.perf_counter();scores.append(float(convert([row],predict_checked(p,[row]))[0]))
        timings.append((time.perf_counter()-t)*1000)
    final=metrics(test,scores,cut)
    p95=sorted(timings)[math.ceil(.95*len(timings))-1]
    baseline=metrics(test,[r["x"][1] for r in test],cut)
    passed=final["signals"]>=20 and final["net_atr"]>0 and p95<=500 and final["net_atr"]>max(0,baseline["net_atr"])
    return {**result,"status":"PROSPECTIVE_TEST_REQUIRED" if passed else "TEST_REJECTED","variant":variant,"calibration":kind,"threshold":cut,"validation":m,"test":final,"momentum_baseline_same_cut":baseline,"median_ms":round(statistics.median(timings),2),"p95_ms":round(p95,2)}

def self_test():
    import numpy as np
    rows=[{"time":i*60,"end":i*60+180,"x":[0.]*12,"y":.5,"buy":.4,"sell":-.6} for i in range(20)]
    selected=nonoverlap(rows)
    assert len(selected)==5 and all(b["time"]>a["end"] for a,b in zip(selected,selected[1:]))
    assert metrics(rows,[.5]*20,.1)["signals"]==20
    assert abs(metrics(rows,[.5]*20,.1)["net_atr"]-6)<1e-6
    for k in ("raw","affine","conditional"):
        c=calibrator(k,rows,np.linspace(-1,1,len(rows)))
        assert np.isfinite(c(rows,np.ones(len(rows)))).all()
    print("SELF_TEST_OK",flush=True)

def main():
    import torch
    torch.set_num_threads(1)
    ap=argparse.ArgumentParser();ap.add_argument("--models",nargs="+",choices=NAMES,default=list(TREES)+["TCN","GRU"])
    ap.add_argument("--market",choices=("XAUUSD","BTCUSD","both"),default="both");ap.add_argument("--self-test",action="store_true")
    a=ap.parse_args();self_test()
    if a.self_test:return
    token=arena.login(input("Owner email: ").strip(),getpass.getpass("Owner password: "))
    report={"version":"REHABILITATION_V2","execution_authorized":False,"historical_development_only":True,"markets":{}}
    for market in ("XAUUSD","BTCUSD"):
        if a.market not in (market,"both"):continue
        try:
            _,bars=arena.archive(market,token);parts=split(bars);out={}
            report["markets"][market]={"candles":len(bars),"models":out}
            if parts:report["markets"][market]["split_ranges"]=[{"start":x[0]["time"],"end":x[-1]["end"],"count":len(x)} for x in parts]
            for name in a.models:
                print(market,name,"REHABILITATION_TEST",flush=True)
                try:r=evaluate(name,parts) if parts else {"status":"INSUFFICIENT_HISTORY"}
                except Exception as e:r={"status":"ERROR","detail":type(e).__name__+": "+str(e)[:250]}
                out[name]=r
                print(market,name,json.dumps({k:v for k,v in r.items() if k!="diagnostics"},allow_nan=False),flush=True);gc.collect()
        except Exception as e:report["markets"][market]={"status":"ERROR","detail":type(e).__name__+": "+str(e)[:250]}
    p=Path(__file__).resolve().parent/("model_repair_v2_"+time.strftime("%Y%m%d_%H%M%S")+".json")
    p.write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8");print("REPORT",p,flush=True)

if __name__=="__main__":main()
