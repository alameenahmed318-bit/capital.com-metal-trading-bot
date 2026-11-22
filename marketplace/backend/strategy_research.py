"""Owner-only historical comparison of fixed candidate vs live evaluator.

Read-only market data, no order calls, no settings or active learning changes.
Both policies see identical past 120 closed candles and are scored against the
following minute, after its observed spread. This is NOT a trade backtest.
"""
import json,math,os,tempfile,time,urllib.parse
from pathlib import Path
from datetime import datetime,timezone
from market_symbols import market_family
from strategy_engine import evaluate_market

REPORT=Path(tempfile.gettempdir())/"alzool-strategy-comparison-v2.json"
MARKETS=("XAUUSD","BTCUSD")
MAX_SAMPLES=160

def _num(value):
    n=float(value)
    if not math.isfinite(n):raise ValueError("Invalid number")
    return n

def _candle(row):
    t=int(datetime.fromisoformat(row["candle_time"].replace("Z","+00:00")).timestamp())
    return {"time":t,"open":_num(row["candle_open"]),"high":_num(row["candle_high"]),
            "low":_num(row["candle_low"]),"close":_num(row["candle_close"]),
            "tick_volume":_num(row.get("tick_volume") or 0),
            "spread_points":_num(row.get("spread_points") or 0),
            "point_size":_num(row.get("point_size") or 0)}

def replay(rows,market,evaluator=evaluate_market,pause=0.018):
    bars=sorted(rows,key=lambda x:x["time"])
    eligible=[]
    for i in range(119,len(bars)-1):
        window=bars[i-119:i+2]
        if bars[i+1]["time"]-bars[i-119]["time"]!=120*60:continue
        if any(b["time"]-a["time"]!=60 for a,b in zip(window,window[1:])):continue
        if bars[i+1]["spread_points"]*bars[i+1]["point_size"]<=0:continue
        eligible.append(i)
    if len(eligible)>MAX_SAMPLES:
        eligible=[eligible[round(j*(len(eligible)-1)/(MAX_SAMPLES-1))] for j in range(MAX_SAMPLES)]
    def blank():return {"signals":0,"positive":0,"net":0.0,"wait":0}
    totals={k:blank() for k in ("baseline","candidate")}
    blocks=[{k:blank() for k in totals} for _ in range(2)]
    changed=0
    for j,i in enumerate(eligible):
        past=bars[i-119:i+1]
        nxt=bars[i+1]
        a=evaluator(past,market_symbol=market,research_policy=False)
        b=evaluator(past,market_symbol=market,research_policy=True)
        changed+=a["decision"]!=b["decision"]
        cost=nxt["spread_points"]*nxt["point_size"]
        for name,result in (("baseline",a),("candidate",b)):
            side=result["decision"]
            atr=max(float(result["entry_atr"]),1e-9)
            for d in (totals[name],blocks[int(j>=len(eligible)//2)][name]):
                if side not in ("BUY","SELL"):d["wait"]+=1;continue
                net=((nxt["close"]-nxt["open"])*(1 if side=="BUY" else -1)-cost)/atr
                d["signals"]+=1;d["positive"]+=net>0;d["net"]+=net
        if pause:time.sleep(pause)
    def finish(d,windows):
        return {**d,"net":round(d["net"],4),
                "hit_rate":round(100*d["positive"]/d["signals"],2) if d["signals"] else None,
                "net_per_window":round(d["net"]/max(1,windows),6)}
    n=len(eligible)
    summary={k:finish(v,n) for k,v in totals.items()}
    segments=[{k:finish(v,n//2 if i==0 else n-n//2) for k,v in block.items()} for i,block in enumerate(blocks)]
    return {"status":"REPLAY_COMPLETE" if n else "INSUFFICIENT_HISTORY",
            "candles":len(bars),"windows":n,"changed_decisions":changed,
            "baseline":summary["baseline"],"candidate":summary["candidate"],"segments":segments,
            "first_eval":bars[eligible[0]]["time"] if n else None,
            "last_eval":bars[eligible[-1]+1]["time"] if n else None,
            "policy":"RESEARCH_ONLY","metric":"NEXT_M1_DIRECTION_NET_OF_SPREAD_ATR",
            "actual_trading_proof":False}

def load_history(sb,uid,market):
    # The parent startup process is intentionally NOT market-scoped.
    # Require exact owner identity and lane, never borrow another account.
    clients=sb("GET","/rest/v1/alzool_engine_clients?user_id=eq."+urllib.parse.quote(uid,safe="")+
        "&market=eq."+market+"&revoked=eq.false&select=client_id&limit=1",admin=True)
    if not clients:return []
    base="/rest/v1/alzool_engine_market_candles?client_id=eq."+urllib.parse.quote(str(clients[0]["client_id"]),safe="")+"&timeframe=eq.PERIOD_M1&symbol=ilike."+market+"*"
    latest=sb("GET",base+"&select=symbol&order=candle_time.desc&limit=1",admin=True)
    if not latest or market_family(latest[0]["symbol"])!=market:return []
    rows=sb("GET",base+"&symbol=eq."+urllib.parse.quote(latest[0]["symbol"],safe="")+
        "&select=candle_time,candle_open,candle_high,candle_low,candle_close,tick_volume,spread_points,point_size&order=candle_time.desc&limit=1000",admin=True)
    return [_candle(row) for row in rows]

def _publish(reports):
    fd,path=tempfile.mkstemp(prefix="alzool-replay-",suffix=".json",dir=REPORT.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:json.dump(reports,f,allow_nan=False)
        os.replace(path,REPORT)
    finally:
        if os.path.exists(path):os.unlink(path)

def report(market):
    try:return json.loads(REPORT.read_text(encoding="utf-8")).get(market,{"status":"LOADING","policy":"RESEARCH_ONLY"})
    except (OSError,ValueError):return {"status":"LOADING","policy":"RESEARCH_ONLY"}

def run(sb,owner_email):
    reports={m:{"status":"LOADING","policy":"RESEARCH_ONLY"} for m in MARKETS}
    _publish(reports)
    try:
        users=sb("GET","/auth/v1/admin/users?page=1&per_page=100",admin=True).get("users",[])
        uid=next(u["id"] for u in users if str(u.get("email") or "").lower()==owner_email.lower())
    except Exception as exc:
        for market in MARKETS:reports[market]={"status":"UNAVAILABLE","policy":"RESEARCH_ONLY","error":type(exc).__name__}
        _publish(reports)
        return
    # Research tasks are sequential, bounded, and independent of live entry.
    # The stress profile is deduplicated by archive content, not by test runs.
    from stress_learning import run_market
    def stress_cycle():
        for market in MARKETS:
            try:
                result=run_market(sb,uid,market)
                print("STRESS_LEARNING_RESULT",market,json.dumps({k:result.get(k) for k in
                     ("status","candles","training_windows","validation_windows","changed_strategies",
                      "changed_combinations","verdict","persisted","reused_same_archive")},ensure_ascii=False),flush=True)
            except Exception as exc:
                print("STRESS_LEARNING_ERROR",market,type(exc).__name__,flush=True)
    stress_cycle()
    for market in MARKETS:
        try:
            rows=load_history(sb,uid,market)
            result=replay(rows,market)
        except Exception as exc:
            result={"status":"UNAVAILABLE","policy":"RESEARCH_ONLY","error":type(exc).__name__}
        result["evaluated_at"]=datetime.now(timezone.utc).isoformat()
        reports[market]=result
        _publish(reports)
        print("STRATEGY_REPLAY_V2",market,json.dumps(result,ensure_ascii=False),flush=True)
    while True:
        time.sleep(1800)
        stress_cycle()
