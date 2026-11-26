"""Local research-only durable outcomes and lightweight actual-deal calibration."""
import json,os,hashlib,time,math
from pathlib import Path
import numpy as np
def state_path(owner,client,symbol):
    root=Path.home()/".alzool-parallel-learning";root.mkdir(parents=True,exist_ok=True)
    return root/(hashlib.sha256((owner.lower()+"|"+client+"|"+symbol).encode()).hexdigest()+".json")
def load(path):
    try:
        d=json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d,dict) else {}
    except (OSError,ValueError):return {}
def save(path,d):
    tmp=path.with_suffix(".tmp");tmp.write_text(json.dumps(d,allow_nan=False,separators=(",",":")),encoding="utf-8");os.replace(tmp,path)
def pause_model(samples,now):
    # Non-overlapping mature outcomes, require repeated negative performance.
    eligible=[]
    for row in sorted(samples,key=lambda x:x[0]):
        if not eligible or row[0]-eligible[-1][0]>=180:eligible.append(row)
    eligible=eligible[-120:]
    if len(eligible)<100:return False
    half=len(eligible)//2
    return sum(x[2] for x in eligible[:half])<0 and sum(x[2] for x in eligible[half:])<0 and now-eligible[-1][0]<86400
def trade_calibrator(b,positions,feature):
    """Model predicts whether a historical entry was positive after actual fees.
    No causal model credit. Train only on closes known before validation starts."""
    from sklearn.linear_model import LogisticRegression
    if len(b)<48:return None,{"trade_status":"INSUFFICIENT","trade_samples":0}
    rows=[];ts=b[:,0]
    for p in positions:
        try:
            at=int(p["opened"]);end=int(p["closed"]);side=int(p["side"]);positive=p["positive"]
            if side not in (0,1) or not isinstance(positive,bool) or end<at:continue
            i=int(np.searchsorted(ts,at-60,side="right"))-1
            if i<47 or not 60<=at-ts[i]<120 or ts[i]-ts[i-47]!=47*60:continue
            x,_=feature(b[i-47:i+1]);rows.append((at,end,np.r_[x[-12:].mean(0),x[-1],side],int(positive)))
        except (ValueError,TypeError,KeyError):continue
    rows.sort(key=lambda r:r[0]);n=len(rows);report={"trade_samples":n,"trade_status":"INSUFFICIENT"}
    if n<80:return None,report
    split=int(.7*n);val=rows[split:];tr=[r for r in rows[:split] if r[1]<val[0][0]]
    if len(tr)<50 or len(val)<20 or len({r[3] for r in tr})<2:return None,report
    m=LogisticRegression(max_iter=200,random_state=42).fit([r[2] for r in tr],[r[3] for r in tr])
    pred=m.predict([r[2] for r in val]);y=np.array([r[3] for r in val]);acc=float((pred==y).mean())
    baseline=max(float(y.mean()),1-float(y.mean()))
    report.update(trade_accuracy=acc,trade_status="VALIDATED" if acc>baseline+.02 else "NOT_VALIDATED")
    return (m if report["trade_status"]=="VALIDATED" else None),report
