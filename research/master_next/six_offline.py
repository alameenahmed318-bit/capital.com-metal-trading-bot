"""Discrete offline RL pilot: IQL-style expectile/advantage-weighted policy,
conservative Q learning, and compact return-conditioned Decision Transformer.
Train on simulated behavior trajectories only, not actual owner trading records.
"""
import argparse,json
from pathlib import Path
import numpy as np
import torch
from torch import nn
from core import load_bars,features,evaluate
from v2_env import make_env,bounded_momentum
A=8
def net(d,o):return nn.Sequential(nn.Linear(d,64),nn.ReLU(),nn.Linear(64,64),nn.ReLU(),nn.Linear(64,o))
class DT(nn.Module):
    def __init__(self,d):
        super().__init__();self.embed=nn.Linear(d+A+1,48);self.pos=nn.Parameter(torch.zeros(1,16,48))
        self.enc=nn.TransformerEncoder(nn.TransformerEncoderLayer(48,4,96,.1,batch_first=True),2)
        self.head=nn.Linear(48,A)
    def forward(self,x):
        z=self.embed(x)+self.pos[:,:x.shape[1]]
        mask=torch.triu(torch.ones(z.shape[1],z.shape[1],dtype=torch.bool,device=z.device),diagonal=1)
        return self.head(self.enc(z,mask=mask)[:,-1])
def main():
    p=argparse.ArgumentParser();p.add_argument("--bars",required=True);p.add_argument("--out",required=True)
    p.add_argument("--group",choices=["iql_cql","dt"],required=True);p.add_argument("--development",required=True,action="store_true")
    a=p.parse_args();torch.set_num_threads(1);torch.manual_seed(42);rng=np.random.default_rng(42)
    sym,b=load_bars(a.bars);x,atr,valid=features(b);n=len(b);fit=int(.25*n);boundary=int(.8*n);v=int(.65*n)
    scale=float(np.median(atr[:fit]));parts={"train":(fit+5,v),"validation":(v+5,boundary),"test":(boundary+5,n)}
    def env(part,stress=False,random=False,seed=42):return make_env(b,x,atr,valid,parts[part],
        slippage=.30 if stress else .10,commission_atr=.04 if stress else .02,random_start=random,seed=seed,scale=scale)
    transitions=[];episodes=[]
    # Diverse fixed behavior policies, no hindsight-optimal trades. Random exploration only in offline generator.
    for episode in range(80):
        e=env("train",stress=bool(episode%2),random=True,seed=episode+100)
        o,_=e.reset(seed=episode+100);done=False;traj=[]
        while not done:
            z=int(rng.integers(A)) if rng.random()<(.15,.4,.75)[episode%3] else bounded_momentum(o)
            new,r,done,_,info=e.step(z)
            transitions.append((o,z,r,new,done));traj.append((o,z,r));o=new
        episodes.append(traj)
    s=torch.tensor(np.stack([t[0] for t in transitions]));act=torch.tensor([t[1] for t in transitions],dtype=torch.long)
    r=torch.tensor([t[2] for t in transitions],dtype=torch.float32);ns=torch.tensor(np.stack([t[3] for t in transitions]))
    done=torch.tensor([t[4] for t in transitions],dtype=torch.float32);d=s.shape[1]
    policies={}
    if a.group=="iql_cql":
        for kind in ("IQL_DISCRETE","CQL_DISCRETE"):
            torch.manual_seed(42);q=net(d,A);target=net(d,A);target.load_state_dict(q.state_dict())
            opt=torch.optim.Adam(q.parameters(),lr=.0005)
            if kind.startswith("IQL"):
                value=net(d,1);actor=net(d,A);vo=torch.optim.Adam(value.parameters(),lr=.0005);ao=torch.optim.Adam(actor.parameters(),lr=.0005)
            for step in range(2000):
                ix=torch.randint(len(s),(128,));obs,ac,rew,nxt,terminal=s[ix],act[ix],r[ix],ns[ix],done[ix]
                qa=q(obs).gather(1,ac[:,None]).squeeze(1)
                with torch.no_grad():
                    boot=value(nxt).squeeze(1) if kind.startswith("IQL") else target(nxt).max(1).values
                    y=rew+.97*(1-terminal)*boot
                loss=nn.functional.smooth_l1_loss(qa,y)
                if kind.startswith("CQL"):loss=loss+.5*(torch.logsumexp(q(obs),dim=1)-qa).mean()
                opt.zero_grad();loss.backward();opt.step()
                if kind.startswith("IQL"):
                    with torch.no_grad():qd=target(obs).gather(1,ac[:,None]).squeeze(1)
                    err=qd-value(obs).squeeze(1);vl=(torch.where(err>0,.7,.3)*err.square()).mean()
                    vo.zero_grad();vl.backward();vo.step()
                    with torch.no_grad():weight=torch.exp((qd-value(obs).squeeze(1))*2).clamp(max=20)
                    al=(nn.functional.cross_entropy(actor(obs),ac,reduction="none")*weight).mean()
                    ao.zero_grad();al.backward();ao.step()
                if step%50==0:target.load_state_dict(q.state_dict())
            m=actor if kind.startswith("IQL") else q;m.eval()
            policies[kind]=lambda o,m=m:int(m(torch.from_numpy(o)[None]).argmax(1).item())
    else:
        # Input at each time: state, previous action, return-to-go. No current action leakage.
        windows=[];labels=[];returns=[]
        for ep in episodes:
            rtg=np.cumsum([t[2] for t in ep][::-1])[::-1];returns.append(float(rtg[0]))
            seq=[]
            for i,(o,z,rr) in enumerate(ep):
                prev=np.zeros(A,np.float32)
                if i:prev[ep[i-1][1]]=1
                seq.append(np.r_[o,prev,np.clip(rtg[i]/20,-5,5)])
                w=seq[-16:];windows.append(np.asarray([np.zeros(d+A+1)]*(16-len(w))+w,dtype=np.float32));labels.append(z)
        data=torch.from_numpy(np.stack(windows));labels=torch.tensor(labels,dtype=torch.long)
        m=DT(d);opt=torch.optim.AdamW(m.parameters(),lr=.0005)
        for step in range(1500):
            ix=torch.randint(len(data),(64,));loss=nn.functional.cross_entropy(m(data[ix]),labels[ix]);opt.zero_grad();loss.backward();opt.step()
        m.eval();goal=float(np.quantile(returns,.75))
        def dt_eval(part,stress):
            e=env(part,stress);o,_=e.reset();seq=[];last=None;rtg=goal;done=False
            while not done:
                prev=np.zeros(A,np.float32)
                if last is not None:prev[last]=1
                seq.append(np.r_[o,prev,np.clip(rtg/20,-5,5)])
                w=seq[-16:];z=torch.tensor(np.asarray([np.zeros(d+A+1)]*(16-len(w))+w,dtype=np.float32))[None]
                with torch.no_grad():last=int(m(z).argmax(1).item())
                o,reward,done,_,info=e.step(last);rtg-=reward
            return info
    report={"symbol":sym,"group":a.group,"development_only":True,"execution_authorized":False,
        "behavior_transitions":len(transitions),"behavior_episodes":len(episodes),"models":{},
        "limitations":["Discrete adapted IQL/CQL and compact DT implementations, not official pretrained models",
            "Simulator experience, not live trades; M1 next-open proxy and offline V2 exit envelope",
            "Reused development data; no independent proof"]}
    def pair(part,fn):return {k:fn(part,s) for k,s in (("base",False),("stress",True))}
    policies["MOMENTUM"]=bounded_momentum;policies["CASH"]=lambda o:0
    for name,policy in policies.items():
        fn=lambda part,stress,policy=policy:evaluate(env(part,stress),policy)
        report["models"][name]={"validation":pair("validation",fn),"test":pair("test",fn)}
    if a.group=="dt":report["models"]["DECISION_TRANSFORMER"]={"validation":pair("validation",dt_eval),"test":pair("test",dt_eval),"goal_train_quantile":goal}
    Path(a.out).write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps(report,indent=2,allow_nan=False))
if __name__=="__main__":main()
