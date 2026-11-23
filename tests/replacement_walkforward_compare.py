"""Read-only, out-of-sample comparison of live V1 and proposed V2 models.

Recorded MT5 closed M1 candles, one-bar proxy outcome, real historical spread.
NOT broker executions, NOT a trading profit backtest, NOT permission to trade.
"""
from __future__ import annotations
import collections
import json
import pathlib
import sys
import statistics

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"marketplace"/"backend"))
from strategy_engine import evaluate_market
from strategy_replacements import MARKET_DEFINITIONS, VERSION
from stress_learning import eligible_indices, outcomes, pick

MIN_VALIDATION=12
TRAIN=48
HOLDOUT=110
SYMBOLS={"BTCUSD":"btc","XAUUSD":"gold"}

def load(symbol):
    filename=ROOT/"tests"/"fixtures"/("v3_replacement_"+SYMBOLS[symbol]+"_m1.json")
    raw=json.loads(filename.read_text(encoding="utf-8"))
    assert raw["symbol"].startswith(symbol)
    bars=[]
    for time,op,hi,lo,cl,vol,spread,point in raw["bars"]:
        bars.append(dict(time=int(time),open=float(op),high=float(hi),low=float(lo),
                         close=float(cl),tick_volume=float(vol),
                         spread_points=float(spread),point_size=float(point)))
    return bars

def empty():
    return {"signals":0,"wins":0,"observed":0.0,"wide":0.0,"extreme":0.0,"gap":0.0,
            "worst":0.0,"peak":0.0,"equity":0.0,"max_dd":0.0}

def add(store,side,nets):
    if side not in ("BUY","SELL"):return
    store["signals"]+=1
    for key in ("observed","wide","extreme","gap"):
        store[key]+=nets[key]
    pnl=nets["observed"]
    store["wins"]+=int(pnl>0)
    store["worst"]=min(store["worst"],pnl)
    store["equity"]+=pnl
    store["peak"]=max(store["peak"],store["equity"])
    store["max_dd"]=max(store["max_dd"],store["peak"]-store["equity"])

def rounded(a,windows):
    return dict(signals=a["signals"],win_rate=round(100*a["wins"]/a["signals"],1) if a["signals"] else 0,
                observed=round(a["observed"],3),wide=round(a["wide"],3),
                extreme=round(a["extreme"],3),gap=round(a["gap"],3),
                mean_per_signal=round(a["observed"]/a["signals"],4) if a["signals"] else 0,
                net_per_100_windows=round(100*a["observed"]/max(1,windows),3),
                max_drawdown=round(a["max_dd"],3),worst=round(a["worst"],3))

def compare_one(symbol):
    bars=load(symbol)
    indices=eligible_indices(bars)
    split=int(len(bars)*.70)
    train=[i for i in indices if i+3<split]
    test=[i for i in indices if i>=split]
    train=pick(train,TRAIN)
    test=pick(test,HOLDOUT)
    result=dict(market=symbol,fixture_bars=len(bars),eligible_origins=len(indices),
                train_origins=len(train),validation_origins=len(test),
                objective="1-minute next-bar ATR-normalized return net of observed spread",
                cost_scenarios=["observed","wide","extreme","gap"],minimum_validation_signals=MIN_VALIDATION,
                source="actual MT5 closed M1 market candles; not broker fills",
                status="INSUFFICIENT_HISTORY")
    if len(test)<30 or len(train)<30:return result
    # Use training only to check activity, never to pick the winning V2.
    target_s=set(MARKET_DEFINITIONS[symbol]["strategies"])
    target_c=set(MARKET_DEFINITIONS[symbol]["combinations"])
    ids={"strategies":sorted(target_s),"combinations":sorted(target_c)}
    scores={}
    for kind,targets in ids.items():
        for model in targets:
            for ver in ("V1","V2"):scores[(kind,model,ver)]=empty()
    all_decisions={"V1":empty(),"V2":empty()}
    train_activity=collections.Counter()
    for segment,selected in (("train",train),("validation",test)):
        for i in selected:
            window=bars[i-119:i+1]
            old=evaluate_market(window,market_symbol=symbol)
            new=evaluate_market(window,market_symbol=symbol,replacement_candidates=True)
            assert old["strategy_count"]==new["strategy_count"]==200
            assert old["combination_count"]==new["combination_count"]==1000
            assert new["catalog_version"]==VERSION
            atr=float(old["entry_atr"])
            if atr<=0:continue
            nets={side:outcomes(bars,i,side,atr) for side in ("BUY","SELL")}
            for ver,ev in (("V1",old),("V2",new)):
                if segment=="validation":add(all_decisions[ver],ev["decision"],nets.get(ev["decision"],{}))
                for kind,field in (("strategies","votes"),("combinations","combination_votes")):
                    current={int(v["id"]):v["signal"] for v in ev[field] if int(v["id"]) in ids[kind]}
                    for model,side in current.items():
                        if segment=="train" and side in ("BUY","SELL"):
                            train_activity[(kind,model,ver)]+=1
                        if segment=="validation" and side in ("BUY","SELL"):
                            add(scores[(kind,model,ver)],side,nets[side])
    result.update(status="RESEARCH_COMPARISON",catalog_version=VERSION,
                  train_last_unix=bars[train[-1]+3]["time"],
                  validation_first_unix=bars[test[0]]["time"],
                  account_cash_profit=False,
                  overall={k:rounded(v,len(test)) for k,v in all_decisions.items()})
    result["old_vs_new"]={}
    for kind,targets in ids.items():
        rows=[]
        for model in targets:
            o=scores[(kind,model,"V1")];n=scores[(kind,model,"V2")]
            old=rounded(o,len(test));new=rounded(n,len(test))
            enough=new["signals"]>=MIN_VALIDATION and train_activity[(kind,model,"V2")]>=8
            robust=enough and new["observed"]>0 and new["wide"]>0 and new["extreme"]>0
            better=enough and new["net_per_100_windows"]>old["net_per_100_windows"]
            rows.append(dict(id=model,baseline=old,candidate=new,
                             train_signals_v2=train_activity[(kind,model,"V2")],
                             statistically_sufficient=False,
                             research_gate="PROVISIONAL_BETTER" if robust and better else
                                           "LOW_SAMPLE" if not enough else "REJECTED_OR_WEAK",
                             requires_forward_testing=True))
        result["old_vs_new"][kind]=rows
    return result

def main():
    results={sym:compare_one(sym) for sym in SYMBOLS}
    report={"method":"One-minute close-to-next-close proxy; non-overlapping sampled origins within chronological 70/30 split. Indicators use only preceding 120 closed M1 candles.",
            "disclaimer":"The profits below are ATR-normalized proxy sums; they are neither USD nor real fills. No strategy weights, orders, or trade settings were touched.",
            "results":results}
    output=ROOT/"tests"/"reports"/"v2_replacements_comparison.json"
    output.parent.mkdir(exist_ok=True,parents=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    assert all(v["status"]=="RESEARCH_COMPARISON" for v in results.values()),"Need more continuous candles"
    assert all(v["train_last_unix"]<v["validation_first_unix"] for v in results.values())
    print("V2_REPLACEMENTS_COMPARISON_COMPLETE",flush=True)

if __name__=="__main__":main()
