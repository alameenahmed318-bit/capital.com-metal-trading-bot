"""XAUUSD-only HistGradientBoosting development, no live submissions.
V3 historical results informed this design; V4 historical folds are not fresh proof.
All rule/model selection occurs on validation before the test block.
"""
import argparse,getpass,json,math,statistics,time
from pathlib import Path
import gold_specialist_arena as base
import model_arena_worker as arena

EXTRA=.10
# Fixed before evaluation; no test-dependent regime definitions.
REGIMES=("all","trend","range")
CUTS=(0.,.025,.05,.10,.20,.35)
PROBS=(0.,.55,.60)
def regime(r):
    # Momentum over 15 minutes in ATR units, normalized by recent volatility.
    return "trend" if abs(r["x"][2])>=1.5*max(.7,r["x"][7]) else "range"

def matrix(rows):
    import numpy as np
    x=[]
    for r in rows:
        w=r["window"];atr=r["atr"];p=w[-1]["close"]
        a=list(r["f"])
        for k in (5,15,30,60):
            s=w[-k:]
            bodies=[(b["close"]-b["open"])/atr for b in s]
            upper=[(b["high"]-max(b["open"],b["close"]))/atr for b in s]
            lower=[(min(b["open"],b["close"])-b["low"])/atr for b in s]
            hi=max(b["high"] for b in s);lo=min(b["low"] for b in s)
            a.extend([sum(bodies)/k,sum(upper)/k,sum(lower)/k,(hi-lo)/atr,
                      (p-lo)/(hi-lo) if hi>lo else .5,
                      abs(sum(bodies))/max(.01,sum(abs(b) for b in bodies))])
        x.append(a)
    return np.clip(np.asarray(x,dtype=float),-20,20)

def fit(rows):
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingRegressor,HistGradientBoostingClassifier
    x=matrix(rows);y=np.asarray([[r["buy"]-EXTRA,r["sell"]-EXTRA] for r in rows])
    weight=np.linspace(.6,1.4,len(rows))
    common=dict(max_iter=120,max_leaf_nodes=7,min_samples_leaf=45,l2_regularization=8,
                learning_rate=.035,early_stopping=False,random_state=42)
    heads=[]
    for j in range(2):
        m=HistGradientBoostingRegressor(loss="squared_error",**common).fit(x,np.clip(y[:,j],-6,6),sample_weight=weight)
        target=(y[:,j]>0).astype(int)
        c=HistGradientBoostingClassifier(**common).fit(x,target,sample_weight=weight) if len(np.unique(target))==2 else None
        heads.append((m,c,float(target.mean())))
    return heads

def predict(heads,rows):
    import numpy as np
    x=matrix(rows)
    z=np.column_stack([m.predict(x) for m,c,f in heads])
    p=np.column_stack([c.predict_proba(x)[:,list(c.classes_).index(1)] if c is not None else np.full(len(x),f) for m,c,f in heads])
    if not np.isfinite(z).all() or not np.isfinite(p).all():raise ValueError("Nonfinite prediction")
    return z,p

def calibrate(rows,z,p):
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    transform=base.calibrate(rows,z)
    models=[]
    for j,side in enumerate(("buy","sell")):
        target=np.array([r[side]-EXTRA>0 for r in rows],dtype=int)
        m=LogisticRegression(C=.1,random_state=42).fit(p[:,j:j+1],target) if len(np.unique(target))==2 else None
        models.append((m,float(target.mean())))
    def apply(z,p):
        q=np.column_stack([m.predict_proba(p[:,j:j+1])[:,1] if m is not None else np.full(len(p),f) for j,(m,f) in enumerate(models)])
        return transform(z),q
    return apply

def choose(rows,z,p,rule):
    mode,side,cut,prob=rule;chosen=[]
    for r,v,q in zip(rows,z,p):
        if mode!="all" and regime(r)!=mode:continue
        j=0 if v[0]>v[1] else 1
        if v[0]==v[1] or (side!="both" and j!=(0 if side=="buy" else 1)):continue
        if v[j]<=cut or q[j]<prob:continue
        chosen.append((r,v))
    return chosen

def report(pairs,windows):
    result=base.diagnostics([r for r,v in pairs],[v for r,v in pairs],force=True)
    result["windows"]=windows
    result["coverage"]=round(len(pairs)/max(1,windows),5)
    return result

def select(rows,z,p):
    # Select at most one policy per fold. Minimum sample support preserved.
    options=[]
    for mode in REGIMES:
        for side in ("both","buy","sell"):
            for cut in CUTS:
                for prob in PROBS:
                    rule=(mode,side,cut,prob)
                    m=report(choose(rows,z,p,rule),len(rows))
                    if m["signals"]>=20 and m["stress_net_atr"]>0:
                        # Penalize drawdown and sparse validation support.
                        quality=m["stress_net_atr"]-.35*m["drawdown_atr"]-5/math.sqrt(m["signals"])
                        options.append((quality,rule,m))
    return max(options,key=lambda x:x[0]) if options else None

def run(parts):
    import numpy as np
    folds=[];all_pairs=[];raw_pairs=[];old_pairs=[];timings=[]
    for i,(tr,cal,val,test) in enumerate(parts,1):
        print("XAUUSD HISTBOOST_V4 FOLD",i,"TRAIN",len(tr),flush=True)
        heads=fit(tr);c,cp=predict(heads,cal);apply=calibrate(cal,c,cp)
        v,vp=predict(heads,val);v,vp=apply(v,vp)
        selection=select(val,v,vp)
        # Old V3 baseline is trained on identical data and chosen on validation.
        old=base.fit("HistGradientBoosting",tr);ov=base.checked(old,val);oc=base.checked(old,cal)
        options=[]
        for label,fn in (("raw",lambda z:z),("calibrated",base.calibrate(cal,oc))):
            for cut in (0.,.025)+base.CUTS:
                m=base.diagnostics(val,fn(ov),cut)
                if m["signals"]>=20 and m["stress_net_atr"]>0:options.append((m["stress_net_atr"]-.25*m["drawdown_atr"],fn,cut,label))
        zz=[];pp=[]
        for r in test:
            t=time.perf_counter();a,b=predict(heads,[r]);a,b=apply(a,b);timings.append((time.perf_counter()-t)*1000)
            zz.append(a[0]);pp.append(b[0])
        z=np.asarray(zz);p=np.asarray(pp)
        raw_pairs.extend(zip(test,z))
        selected=choose(test,z,p,selection[1]) if selection else []
        all_pairs.extend(selected)
        if options:
            _,fn,cut,label=max(options,key=lambda x:x[0]);oz=fn(base.checked(old,test))
            old_selected=[(r,a) for r,a in zip(test,oz) if max(a)>cut and a[0]!=a[1]]
        else:old_selected=[]
        old_pairs.extend(old_selected)
        m=report(selected,len(test));o=report(old_selected,len(test))
        record={"fold":i,"test_start":test[0]["time"],"test_end":test[-1]["end"],"rule":selection[1] if selection else None,
                "validation":selection[2] if selection else None,"test_v4":m,"test_v3":o,
                "direction_by_regime":{k:report([(r,a) for r,a in zip(test,z) if regime(r)==k],sum(regime(r)==k for r in test)) for k in ("trend","range")}}
        folds.append(record);print("XAUUSD HISTBOOST_V4 FOLD_RESULT",json.dumps(record,allow_nan=False),flush=True)
    n=sum(len(t) for _,_,_,t in parts)
    final=report(all_pairs,n);old=report(old_pairs,n)
    positive=sum(r["test_v4"]["stress_net_atr"]>0 for r in folds)
    p95=sorted(timings)[math.ceil(.95*len(timings))-1]
    passed=len(folds)==3 and final["signals"]>=60 and positive>=2 and final["stress_net_atr"]>max(0,old["stress_net_atr"]) and p95<=500
    return {"status":"FRESH_DATA_TEST_REQUIRED" if passed else "RESEARCH_ONLY","folds":len(folds),"positive_folds":positive,
            "v4":final,"v3_same_windows":old,"direction_only":report(raw_pairs,n),
            "median_ms":round(statistics.median(timings),2),"p95_ms":round(p95,2),"details":folds}

def self_test():
    import numpy as np
    rows=[{"y":1.,"buy":.8,"sell":-1.2,"x":[0.,0.,2.,0.,0.,0.,0.,1.]+[0.]*4} for _ in range(24)]
    z=np.tile([.4,-.4],(24,1));p=np.tile([.7,.3],(24,1))
    assert regime(rows[0])=="trend"
    assert len(choose(rows,z,p,("trend","buy",.1,.6)))==24
    assert not choose(rows,z,p,("range","both",0.,0.))
    assert not choose(rows,z,p,("all","sell",0.,0.))
    assert select(rows,z,p) is not None
    assert report(choose(rows,z,p,("all","buy",0.,0.)),24)["stress_net_atr"]==16.8
    print("HISTBOOST_V4_SELF_TEST_OK",flush=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--self-test",action="store_true");a=ap.parse_args()
    self_test()
    if a.self_test:return
    token=arena.login(input("Owner email: ").strip(),getpass.getpass("Owner password: "))
    _,bars=arena.archive("XAUUSD",token);rows=base.data(bars,all_rows=True);parts=base.folds(rows)
    result=run(parts) if parts else {"status":"INSUFFICIENT_HISTORY"}
    out={"version":"GOLD_HISTBOOST_V4","market":"XAUUSD","historical_development_only":True,"execution_authorized":False,"candles":len(bars),"extra_cost_atr":EXTRA,"result":result}
    print("XAUUSD HISTBOOST_V4 FINAL",json.dumps({k:v for k,v in result.items() if k!="details"},allow_nan=False),flush=True)
    f=Path(__file__).resolve().parent/("gold_histboost_v4_"+time.strftime("%Y%m%d_%H%M%S")+".json")
    f.write_text(json.dumps(out,indent=2,allow_nan=False),encoding="utf-8");print("REPORT",f,flush=True)

if __name__=="__main__":main()
