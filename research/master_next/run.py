"""Run offline PPO/DQN challenger. The live project is never imported."""
import argparse,json,time
from pathlib import Path
import numpy as np
from core import load_bars,features,segments,make_env,evaluate,momentum
from advisers import lstm_features,anomaly_features,timesfm_features,news_features

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--bars",required=True);p.add_argument("--out",required=True)
    p.add_argument("--steps",type=int,default=20000);p.add_argument("--seed",type=int,default=42)
    p.add_argument("--timesfm",action="store_true");p.add_argument("--news")
    args=p.parse_args()
    if args.steps<2048:raise ValueError("Minimum 2048 RL steps")
    out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
    import torch
    import joblib
    from stable_baselines3 import PPO,DQN
    from stable_baselines3.common.env_checker import check_env
    torch.set_num_threads(1)
    symbol,b=load_bars(args.bars);x,atr,valid=features(b);splits=segments(len(b))
    fit_end=splits["feature_fit"][1]
    l,count,lstm=lstm_features(b,atr,valid,fit_end,args.seed)
    anomaly,forest=anomaly_features(x,valid,fit_end,args.seed)
    tf,tf_mask=timesfm_features(b,atr,valid,fit_end) if args.timesfm else (np.zeros(len(b)),np.zeros(len(b)))
    news,nmask=news_features(b,args.news,symbol) if args.news else (np.zeros(len(b)),np.zeros(len(b)))
    x=np.column_stack([x,l,anomaly,tf,tf_mask,news,nmask]).astype(np.float32)
    torch.save(lstm.state_dict(),out/"lstm_state.pt");joblib.dump(forest,out/"isolation_forest.joblib")
    # Feature models frozen before RL fit. No tuning on final test.
    def env(part,stress=False):
        return make_env(b,x,atr,valid,splits[part],slippage=.30 if stress else .10,commission_atr=.04 if stress else .02)
    check_env(env("train"),warn=True)
    report={"version":"MASTER_NEXT_RESEARCH_V1","execution_authorized":False,
        "symbol":symbol,"bars":len(b),"split_indices":splits,"seed":args.seed,
        "cost_model":"next-open, half spread per unit per side + 0.10/0.30 ATR slippage + 0.02/0.04 ATR commission proxy",
        "metric":"FIXED_TRAIN_ATR_NORMALIZED_PRICE_PNL_NOT_ACCOUNT_PROFIT",
        "advisers":{"LSTM":{"predictions":int(count)},"IsolationForest":"FITTED",
          "TimesFM":{"enabled":args.timesfm,"predictions":int(tf_mask.sum())},
          "FinBERT":{"enabled":bool(args.news),"bars_with_news":int(nmask.sum())}},
        "validation":{},"test":{}}
    policies={"CASH":lambda o:0,"MOMENTUM":momentum}
    for name,cls in (("PPO",PPO),("DQN",DQN)):
        m=cls("MlpPolicy",env("train"),seed=args.seed,device="cpu",verbose=0,
            **({"n_steps":256,"batch_size":64} if name=="PPO" else {"buffer_size":50000,"learning_starts":512,"batch_size":64}))
        m.learn(total_timesteps=args.steps)
        m.save(str(out/name))
        policies[name]=lambda o,m=m:int(m.predict(o,deterministic=True)[0].item())
    for name,policy in policies.items():
        report["validation"][name]={k:evaluate(env("validation",stress),policy) for k,stress in (("base",False),("stress",True))}
    def rank(name):
        r=report["validation"][name]["stress"];return r["net_atr_units"]-.5*r["max_drawdown_atr_units"]
    chosen=max(("PPO","DQN"),key=rank)
    report["validation_choice"]=chosen
    report["validation_pass"]=bool(rank(chosen)>max(rank("CASH"),rank("MOMENTUM")) and report["validation"][chosen]["base"]["net_atr_units"]>0)
    # Only the frozen selected challenger and fixed baselines are evaluated on final holdout.
    for name in ("CASH","MOMENTUM",chosen):
        report["test"][name]={k:evaluate(env("test",stress),policies[name]) for k,stress in (("base",False),("stress",True))}
    r=report["test"][chosen]["stress"]
    report["pilot_pass"]=bool(report["validation_pass"] and r["fills"]>=20 and r["net_atr_units"]>max(0,report["test"]["MOMENTUM"]["stress"]["net_atr_units"]) and report["test"][chosen]["base"]["net_atr_units"]>0)
    report["limitations"]=["Single net position; recovery is losing-position reduction, not production hedge recovery.",
        "M1 next-open simulation, not ticks or actual MT5 fills. No leverage, margin or swap simulation.",
        "One chronological holdout and one seed: not sufficient to enable production.",
        "No comparison with the existing full production policy yet.",
        "Pretrained checkpoints may contain historical training-period overlap."]
    (out/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps(report,indent=2,allow_nan=False))
if __name__=="__main__":main()
