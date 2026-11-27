"""Chronological cost-aware advisory selection; no execution permissions."""
import math

THRESHOLDS=(0.,.05,.10,.20,.35,.50,.75,1.,1.5,2.)
STRESS=.15  # extra assumed slippage in ATR, not measured broker slippage

def split_rows(rows, origin, end, minimum=600):
    n=len(rows)
    if n<minimum:return None
    a,b=int(n*.6),int(n*.8)
    tr=[r for r in rows[:a] if end(r)<origin(rows[a])]
    va=[r for r in rows[a:b] if end(r)<origin(rows[b])]
    te=rows[b:]
    return tr,va,te

def nonoverlap(rows,origin,end):
    out=[];last=-1
    for row in rows:
        if origin(row)>last:
            out.append(row);last=end(row)
    return out

def metrics(pred,targets,threshold,minimum=30):
    chosen=[float(y) for p,y in zip(pred,targets) if math.isfinite(float(p)) and p>threshold]
    n=len(chosen);net=sum(chosen);stress=net-STRESS*n
    return {"signals":n,"positive":sum(y>0 for y in chosen),"net_proxy":round(net,5),
            "stress_net_proxy":round(stress,5),"eligible":n>=minimum and net>0 and stress>0}

def select_threshold(pred,targets,minimum=30):
    candidates=[(metrics(pred,targets,t,minimum),t) for t in THRESHOLDS]
    good=[(r,t) for r,t in candidates if r["eligible"]]
    if not good:return None
    return max(good,key=lambda q:(q[0]["stress_net_proxy"],q[1]))[1]

class SideModel:
    def __init__(self,models):
        self.models=models
    def predict(self,features):
        scores={side:[float(v) for v in model.predict(features)] for side,(model,_) in self.models.items()}
        out=[]
        for i in range(len(features)):
            buy=scores["BUY"][i] if "BUY" in scores else -math.inf
            sell=scores["SELL"][i] if "SELL" in scores else -math.inf
            b=buy if "BUY" in self.models and buy>self.models["BUY"][1] else -math.inf
            s=sell if "SELL" in self.models and sell>self.models["SELL"][1] else -math.inf
            # No false strong vote: magnitude is estimated positive net ATR.
            out.append(max(.051,b) if b>s else -max(.051,s) if s>b else 0.)
        return out

class ActionModel:
    def __init__(self,model,threshold):self.model=model;self.threshold=threshold
    def predict(self,features):
        return [float(v) if v>self.threshold else 0. for v in self.model.predict(features)]
