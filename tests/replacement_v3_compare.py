"""Research-only V1/V2/V3 comparison over recorded MT5 candles.

This uses the exact market data and preselected holdout windows of V2 report.
V3 was designed after inspecting V2, so this is exploratory, not independent
validation. No order permissions, trade-setting writes or profile promotions.
"""
import collections
import json
import pathlib
import sys

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tests"))
sys.path.insert(0,str(ROOT/"marketplace"/"backend"))
from replacement_walkforward_compare import (load, empty, add, rounded, TRAIN,
                                             HOLDOUT,MIN_VALIDATION)
from stress_learning import eligible_indices,pick,outcomes
from strategy_engine import evaluate_market
from strategy_replacements_v3 import EXTRA_CANDIDATES, VERSION_V3
from strategy_replacements import MARKET_DEFINITIONS

def compare_market(symbol):
    candles=load(symbol)
    eligible=eligible_indices(candles)
    cutoff=int(len(candles)*.70)
    train=pick([i for i in eligible if i+3<cutoff],TRAIN)
    holdout=pick([i for i in eligible if i>=cutoff],HOLDOUT)
    assert len(train)>=30 and len(holdout)>=30
    ids={kind:sorted(set(MARKET_DEFINITIONS[symbol][kind])|
                     set(EXTRA_CANDIDATES[symbol][kind]))
         for kind in ("strategies","combinations")}
    track={(kind,id,version):empty() for kind in ids for id in ids[kind]
           for version in ("V1","V2","V3")}
    total={version:empty() for version in ("V1","V2","V3")}
    training=collections.Counter()
    for segment,indices in (("train",train),("validation",holdout)):
        for i in indices:
            window=candles[i-119:i+1]
            baseline=evaluate_market(window,market_symbol=symbol)
            v2=evaluate_market(window,market_symbol=symbol,replacement_candidates=True)
            v3=evaluate_market(window,market_symbol=symbol,replacement_candidates="V3")
            assert v3["catalog_version"]==VERSION_V3
            atr=float(baseline["entry_atr"])
            if atr<=0:continue
            nets={d:outcomes(candles,i,d,atr) for d in ("BUY","SELL")}
            for name,output in (("V1",baseline),("V2",v2),("V3",v3)):
                direction=output["decision"]
                if segment=="validation" and direction in nets:
                    add(total[name],direction,nets[direction])
                for kind,field in (("strategies","votes"),("combinations","combination_votes")):
                    for vote in output[field]:
                        identity=vote["id"];side=vote["signal"]
                        if identity not in ids[kind] or side not in nets:continue
                        if segment=="train":training[(kind,identity,name)]+=1
                        else:add(track[(kind,identity,name)],side,nets[side])
    result={"status":"EXPLORATORY_RESEARCH_ONLY","symbol":symbol,"candles":len(candles),
            "holdout_windows":len(holdout),"train_windows":len(train),
            "train_last_time":candles[train[-1]+3]["time"],
            "holdout_first_time":candles[holdout[0]]["time"],
            "overall":{name:rounded(stats,len(holdout)) for name,stats in total.items()},
            "models":{}}
    for kind in ids:
        items=[]
        for identity in ids[kind]:
            versions={v:rounded(track[(kind,identity,v)],len(holdout)) for v in ("V1","V2","V3")}
            new=versions["V3"];old=versions["V1"]
            enough=new["signals"]>=MIN_VALIDATION and training[(kind,identity,"V3")]>=8
            only_candidate=enough and new["observed"]>0 and new["wide"]>0 and new["extreme"]>0 and new["net_per_100_windows"]>old["net_per_100_windows"]
            items.append({"id":identity,"V1":versions["V1"],"V2":versions["V2"],"V3":versions["V3"],
                          "train_v3":training[(kind,identity,"V3")],
                          "verdict":"RESEARCH_PROMISING_NOT_APPROVED" if only_candidate else "INSUFFICIENT" if not enough else "NOT_BETTER",
                          "promote_to_live":False})
        result["models"][kind]=items
    return result

def main():
    report={"limitations":"Exploratory one-bar ATR-normalized shadow returns, not USD/backtest fills. The same 30% period was previously inspected for V2; this comparison cannot justify live activation.",
            "results":{symbol:compare_market(symbol) for symbol in ("BTCUSD","XAUUSD")}}
    dst=ROOT/"tests"/"reports"/"v3_replacements_comparison.json"
    dst.parent.mkdir(parents=True,exist_ok=True)
    dst.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    print("V3_REPLACEMENTS_COMPARISON_COMPLETE",flush=True)

if __name__=="__main__":main()
