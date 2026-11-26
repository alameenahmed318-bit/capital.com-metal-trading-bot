"""Multiple-seed development trial or chronological fresh private archive trial."""
import argparse,json
from pathlib import Path
import numpy as np
from core import load_bars,features,evaluate
from advisers import lstm_features,anomaly_features
from v2_env import make_env,bounded_momentum

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--bars",required=True);p.add_argument("--out",required=True)
    p.add_argument("--steps",type=int,default=32768)
    p.add_argument("--development",action="store_true",help="Previously inspected fixtures; no independent claims")
    a=p.parse_args()
    if a.steps<4096:raise ValueError("Minimum 4096 steps")
    out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
    import torch
    from stable_baselines3 import PPO,DQN
    from stable_baselines3.common.env_checker import check_env
    torch.set_num_threads(1)
    symbol,b=load_bars(a.bars);n=len(b);x,atr,valid=features(b)
    # Known V1 fixture end timestamps. Reserve fresh candles strictly after that end.
    cut=1795210980 if symbol=="XAUUSD" else 1795422420 if symbol in ("BTCUSD","BTCUSDm") else None
    if cut is None:raise ValueError("Unsupported market")
    if a.development:
        split=int(.80*n)
    else:
        split=int(np.searchsorted(b[:,0],cut,side="right"))
        if split<800 or n-split<300:raise ValueError("Insufficient pre-cutoff training or fresh holdout")
    fit=int(.35*split);train_end=int(.8*split)
    parts={"train":(fit+5,train_end),"validation":(train_end+5,split),"test":(split+5,n)}
    if any(hi-lo<100 for lo,hi in parts.values()):raise ValueError("Insufficient separated partitions")
    lstm,count,_=lstm_features(b,atr,valid,fit,42)
    forest,_=anomaly_features(x,valid,fit,42)
    x=np.column_stack([x,lstm,forest]).astype(np.float32)
    scale=float(np.median(atr[:fit]))
    def env(part,stress=False,random=False,seed=42):
        return make_env(b,x,atr,valid,parts[part],slippage=.30 if stress else .10,
            commission_atr=.04 if stress else .02,random_start=random,seed=seed,scale=scale)
    check_env(env("train"),warn=True)
    report={"version":"MASTER_NEXT_RESEARCH_V2","symbol":symbol,"bars":n,"execution_authorized":False,
        "development_only":a.development,"split_indices":parts,"test_start_unix":int(b[parts["test"][0],0]),
        "known_v1_cutoff_unix":cut,"lstm_predictions":int(count),"steps_per_seed":a.steps,
        "validation":{},"test":{},"limitations":["M1 next-open price simulation, no actual broker executions",
        "Research-only 15-bar/2.5 ATR exit envelope, not the production strategy",
        "No exact live policy comparison, tick replay, account margin, news or TimesFM in this trial"]}
    cash=lambda o:0
    def evaluate_pair(part,policy):
        return {k:evaluate(env(part,stress),policy) for k,stress in (("base",False),("stress",True))}
    report["validation"]["CASH"]=evaluate_pair("validation",cash)
    report["validation"]["MOMENTUM"]=evaluate_pair("validation",bounded_momentum)
    policies={}
    for name,cls in (("PPO",PPO),("DQN",DQN)):
        for seed in (17,42,83):
            key=f"{name}_{seed}"
            opts={"n_steps":256,"batch_size":64,"gamma":.97,"ent_coef":.01} if name=="PPO" else {"buffer_size":50000,"learning_starts":512,"batch_size":64,"gamma":.97,"exploration_fraction":.3}
            m=cls("MlpPolicy",env("train",random=True,seed=seed),seed=seed,device="cpu",verbose=0,**opts)
            m.learn(total_timesteps=a.steps)
            m.save(str(out/key))
            policies[key]=lambda o,m=m:int(m.predict(o,deterministic=True)[0].item())
            report["validation"][key]=evaluate_pair("validation",policies[key])
    def score(k):
        r=report["validation"][k]["stress"]
        return r["net_atr_units"]-.5*r["max_drawdown_atr_units"]
    def qualified(k):
        r=report["validation"][k]["stress"]
        return r["fills"]>=20 and r["net_atr_units"]>0 and min(r["entries"].values())>=3 and score(k)>max(score("CASH"),score("MOMENTUM"))
    # Pick algorithm by median across seeds, then representative median seed; not cherry-pick best seed.
    groups={n:sorted([k for k in policies if k.startswith(n+"_")],key=score) for n in ("PPO","DQN")}
    choice=max(groups,key=lambda k:score(groups[k][1]))
    chosen=groups[choice][1]
    report["validation_choice"]=chosen
    report["validation_pass"]=sum(qualified(k) for k in groups[choice])>=2 and qualified(chosen)
    for k,policy in (("CASH",cash),("MOMENTUM",bounded_momentum),(chosen,policies[chosen])):
        report["test"][k]=evaluate_pair("test",policy)
    r=report["test"][chosen]["stress"]
    report["research_gate_pass"]=bool(not a.development and report["validation_pass"] and r["fills"]>=20 and min(r["entries"].values())>=3 and r["net_atr_units"]>max(0,report["test"]["MOMENTUM"]["stress"]["net_atr_units"]) and report["test"][chosen]["base"]["net_atr_units"]>0)
    (out/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps(report,indent=2,allow_nan=False))
if __name__=="__main__":main()
