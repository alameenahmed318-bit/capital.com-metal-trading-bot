"""Offline regression, latency and candidate comparison. Never sends orders.

Usage: python tests/strategy_speed_replay.py [--candles-export history.json]
Optional JSON: {"XAUUSD":[{time,open,high,low,close,tick_volume,spread_points,point_size},...],
                "BTCUSD":[...]} in chronological order. Supply consecutive M1 candles.
Synthetic results verify plumbing only; not trading performance.
"""
import argparse,math,random,statistics,subprocess,sys,time,types,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"marketplace"/"backend"))
import strategy_engine as engine
BASE="5ff86e115dd2f4bd93b47d1b1877df954a1e96e3"

def baseline():
    source=subprocess.check_output(["git","show",BASE+":marketplace/backend/strategy_engine.py"],cwd=ROOT)
    m=types.ModuleType("strategy_engine_baseline")
    exec(compile(source,"strategy_engine_baseline.py","exec"),m.__dict__)
    return m

def synthetic(symbol,regime,n=300):
    rng=random.Random(301+regime)
    price=2600.0 if symbol=="XAUUSD" else 95000.0
    unit=.8 if symbol=="XAUUSD" else 22.0
    bars=[]
    for i in range(n):
        op=price
        drift=(.45 if regime==0 else -.45 if regime==1 else .05*math.sin(i/9))
        shock=(3 if regime==3 and i>n//2 else 1)
        price=op+unit*shock*(rng.uniform(-1,1)+drift)
        bars.append({"time":1700000040+60*i,"open":op,"close":price,
                     "high":max(op,price)+rng.random()*unit*shock,
                     "low":min(op,price)-rng.random()*unit*shock,
                     "tick_volume":rng.randrange(5,120),"spread_points":10,
                     "point_size":unit*.01})
    return bars

def evaluate(mod,rows,symbol,weights,combos,research=False):
    kw={"market_symbol":symbol,"weights":weights,"combination_weights":combos,
        "mtf_bias":{"M5":"BUY","M15":"SELL","H1":"WAIT"}}
    if research:kw["research_policy"]=True
    return mod.evaluate_market(rows,**kw)

def directional_metric(rows,symbol,policy):
    count=hits=0;net=0.0;missing=0
    for i in range(120,len(rows)-1,max(1,(len(rows)-121)//60)):
        result=engine.evaluate_market(rows[i-119:i+1],market_symbol=symbol,research_policy=policy)
        side=result["decision"]
        nxt=rows[i+1]
        if nxt["time"]-rows[i]["time"]!=60:missing+=1;continue
        spread=float(nxt.get("spread_points",0))*float(nxt.get("point_size",0))
        if spread<=0:missing+=1;continue
        if side not in ("BUY","SELL"):continue
        count+=1
        move=(nxt["close"]-nxt["open"])*(1 if side=="BUY" else -1)-spread
        hits+=move>0
        net+=move/max(float(result.get("entry_atr",0)),1e-9)
    return {"directional_samples":count,"net_directional_hits":hits,
            "hit_rate":round(hits/count,4) if count else None,
            "normalized_next_bar_net":round(net,5),"excluded_intervals":missing}

def clear(mod):
    for name in ("_ema_cached","_rsi_cached"):
        getattr(mod,name).cache_clear()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--candles-export")
    args=parser.parse_args()
    old=baseline();old_times=[];new_times=[];checks=0
    weights={str(i):.8+(i%11)*.04 for i in range(1,201)}
    combos={str(i):.9+(i%13)*.015 for i in range(1,1001)}
    for symbol in ("XAUUSD","BTCUSD"):
        for regime in range(4):
            bars=synthetic(symbol,regime)
            for end in range(66,len(bars),11):
                window=bars[max(0,end-120):end]
                clear(old);clear(engine)
                t=time.perf_counter();a=evaluate(old,window,symbol,weights,combos);old_times.append(time.perf_counter()-t)
                t=time.perf_counter();b=evaluate(engine,window,symbol,weights,combos);new_times.append(time.perf_counter()-t)
                assert a==b,(symbol,regime,end,"strategy or decision regression", [key for key in a if a[key]!=b.get(key)])
                candidate=evaluate(engine,window,symbol,weights,combos,True)
                assert len(candidate["votes"])==200 and len(candidate["combination_votes"])==1000
                for v in candidate["votes"]:
                    assert v["evidence_strength"] in (0,.35,1)
                    assert v["signal"]=="WAIT" or v["evidence_strength"]>0
                if candidate["decision"]!="WAIT":
                    assert any(v["id"]==candidate["strategy_id"] and v["signal"]==candidate["decision"] for v in candidate["votes"])
                checks+=1
    old_ms=statistics.median(old_times)*1000
    new_ms=statistics.median(new_times)*1000
    print("EXACT_MATCH_WINDOWS",checks)
    print("BASELINE_MEDIAN_MS",round(old_ms,3),"OPTIMIZED_MEDIAN_MS",round(new_ms,3),
          "SPEEDUP",round(old_ms/max(new_ms,1e-9),2))
    print("SYNTHETIC_PROCESS_CHECKS_ONLY; not a prediction of real results")
    if args.candles_export:
        data=json.loads(Path(args.candles_export).read_text(encoding="utf-8"))
        for symbol in ("XAUUSD","BTCUSD"):
            bars=data.get(symbol,[])
            if len(bars)<150:
                print(symbol,"INSUFFICIENT_HISTORY",len(bars));continue
            bars=sorted(bars,key=lambda b:b["time"])
            print(symbol,"REPLAY_BASELINE",json.dumps(directional_metric(bars,symbol,False)))
            print(symbol,"REPLAY_CANDIDATE",json.dumps(directional_metric(bars,symbol,True)))
        print("Next-bar spread-adjusted direction only. No fill simulation, slippage, commission or profit guarantees.")

if __name__=="__main__":main()
