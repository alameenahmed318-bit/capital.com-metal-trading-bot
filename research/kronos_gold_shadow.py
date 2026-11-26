"""Prospective XAUUSD-only Kronos-small shadow trial.
No trade/forecast submissions: uses only owner login and GET candle history.
Frozen variants: original 128 bars / 3 samples versus lighter 64 / 1.
A forecast must finish before the next full minute entry, so evaluation
cannot assume fills at the already-passed first future candle open.
"""
import argparse,getpass,json,math,os,statistics,sys,time
from pathlib import Path
from datetime import datetime,timezone
os.environ["OMP_NUM_THREADS"]="1"
os.environ["MKL_NUM_THREADS"]="1"
import model_arena_worker as arena
from specialist_market import features
VARIANTS={"reference":(128,3),"fast":(64,1)}
THRESHOLD=.10
STRESS=.10
POLL_SECONDS=12

def utc(t):
    return datetime.fromtimestamp(t,timezone.utc).isoformat()

def recent(token):
    response=arena.api("XAUUSD","history",token)
    cid=response.get("client");symbol=response.get("symbol")
    if not cid or not isinstance(symbol,str) or not symbol.upper().startswith("XAUUSD"):
        return None,None,[]
    bars={}
    for row in response.get("rows") or []:
        try:
            if row.get("spread_points") is None or row.get("point_size") is None:continue
            b=arena._candle(row)
            if b["time"]+60<=time.time() and b["point_size"]>0 and b["spread_points"]>=0:
                bars[b["time"]]=b
        except (ValueError,TypeError,KeyError):continue
    return cid,symbol,sorted(bars.values(),key=lambda x:x["time"])

def load():
    import torch,pandas as pd
    torch.set_num_threads(1)
    source=Path(__file__).resolve().parents[1]/".kronos-src"
    if not (source/"model").is_dir():raise RuntimeError("Missing official Kronos source")
    sys.path.insert(0,str(source))
    from model import Kronos,KronosTokenizer,KronosPredictor
    tok=KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model=Kronos.from_pretrained("NeoQuasar/Kronos-small")
    model.eval();tok.eval()
    predictor=KronosPredictor(model,tok,device="cpu",max_context=128)
    def predict(w,atr,variant):
        context,count=VARIANTS[variant];w=w[-context:];origin=w[-1]["time"]
        df=pd.DataFrame(w)[["open","high","low","close"]]
        x=pd.Series(pd.to_datetime([b["time"] for b in w],unit="s"))
        y=pd.Series(pd.to_datetime([origin+60*h for h in range(1,6)],unit="s"))
        torch.manual_seed(int(origin)%2147483647)
        start=time.perf_counter()
        with torch.inference_mode():
            pred=predictor.predict(df=df,x_timestamp=x,y_timestamp=y,pred_len=5,T=1.,top_p=.9,sample_count=count,verbose=False)
        ms=(time.perf_counter()-start)*1000
        if len(pred)!=5:raise ValueError("Unexpected forecast size")
        # Entry at second future open, exit at fifth future close.
        z=(float(pred["close"].iloc[4])-float(pred["open"].iloc[1]))/atr
        if not math.isfinite(z):raise ValueError("Nonfinite forecast")
        return z,ms
    return predict

def settle(event,bars,now):
    entry_time=event["entry_time"];exit_time=event["exit_bar"]
    if now<exit_time+60:return None
    needed=list(range(entry_time,exit_time+1,60))
    if not all(t in bars for t in needed):
        if now<exit_time+600:return None
        return {"type":"missing_outcome","variant":event["variant"],"origin":event["origin"]}
    first,last=bars[entry_time],bars[exit_time]
    if any(bars[t]["point_size"]<=0 or bars[t]["spread_points"]<0 for t in needed):
        return {"type":"missing_outcome","variant":event["variant"],"origin":event["origin"]}
    atr=event["atr"];move=(last["close"]-first["open"])/atr
    z=event["score"]
    side="BUY" if z>THRESHOLD else "SELL" if z<-THRESHOLD else "WAIT"
    gross=move if side=="BUY" else -move if side=="SELL" else 0.
    spread=(first["point_size"]*first["spread_points"] if side=="BUY" else last["point_size"]*last["spread_points"])/atr if side!="WAIT" else 0.
    net=gross-spread-STRESS if side!="WAIT" else 0.
    return {"type":"outcome","variant":event["variant"],"origin":event["origin"],"side":side,"gross_atr":gross,
            "spread_net_atr":gross-spread,"stress_net_atr":net,"correct":gross>0 if side!="WAIT" else None,
            "inference_ms":event["inference_ms"]}

def summary(outcomes):
    result={}
    for name in VARIANTS:
        all_rows=[x for x in outcomes if x["variant"]==name and x["type"]=="outcome"]
        signals=[x for x in all_rows if x["side"]!="WAIT"];n=len(signals)
        eq=peak=dd=0.
        for x in signals:
            eq+=x["stress_net_atr"];peak=max(peak,eq);dd=max(dd,peak-eq)
        speeds=sorted(x["inference_ms"] for x in all_rows)
        result[name]={"evaluated":len(all_rows),"signals":n,"direction_accuracy":round(sum(x["correct"] for x in signals)/n,5) if n else None,
                      "stress_net_atr":round(eq,5),"drawdown_atr":round(dd,5),
                      "median_ms":round(statistics.median(speeds),2) if speeds else None,
                      "p95_ms":round(speeds[math.ceil(.95*len(speeds))-1],2) if speeds else None}
    return result

def self_test():
    b={t:{"open":100.,"close":101.,"spread_points":2.,"point_size":.1} for t in (120,180,240,300)}
    e={"origin":0,"entry_time":120,"exit_bar":300,"atr":1.,"score":.5,"variant":"fast","inference_ms":1.}
    assert settle(e,b,359) is None
    r=settle(e,b,360);assert r["side"]=="BUY" and abs(r["stress_net_atr"]-.7)<1e-8
    assert settle({**e,"score":.01},b,360)["side"]=="WAIT"
    assert summary([r])["fast"]["signals"]==1
    print("KRONOS_SHADOW_SELF_TEST_OK",flush=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--self-test",action="store_true");a=ap.parse_args()
    self_test()
    if a.self_test:return
    email=input("Owner email: ").strip();password=getpass.getpass("Owner password: ")
    token=arena.login(email,password);renew=time.monotonic()+1500
    forecast=load()
    stamp=time.strftime("%Y%m%d_%H%M%S")
    journal=Path(__file__).resolve().parent/("kronos_gold_shadow_"+stamp+".jsonl")
    pending={};outcomes=[];seen=set();bound=None
    def write(x):
        with journal.open("a",encoding="utf-8") as f:
            f.write(json.dumps(x,allow_nan=False)+"\n")
    write({"type":"session","market":"XAUUSD","execution_authorized":False,"started_at":utc(time.time()),
           "variants":VARIANTS,"threshold_atr":THRESHOLD,"stress_atr":STRESS,"entry_offset_seconds":120,"exit_bar_offset_seconds":300})
    print("KRONOS GOLD SHADOW READY | NO ORDERS | NO LIVE VOTES",journal,flush=True)
    last_heartbeat=0
    try:
        while True:
            try:
                if time.monotonic()>=renew:
                    token=arena.login(email,password);renew=time.monotonic()+1500
                cid,symbol,b=recent(token)
                if not b:time.sleep(POLL_SECONDS);continue
                if bound is None:bound=(cid,symbol)
                if bound!=(cid,symbol):
                    print("CLIENT_OR_SYMBOL_CHANGED_STOPPED",flush=True);break
                lookup={x["time"]:x for x in b};now=time.time()
                for key,e in list(pending.items()):
                    r=settle(e,lookup,now)
                    if r is not None:
                        write(r);pending.pop(key)
                        if r["type"]=="outcome":outcomes.append(r)
                        print("OUTCOME",json.dumps(r,allow_nan=False),flush=True)
                        print("SUMMARY",json.dumps(summary(outcomes),allow_nan=False),flush=True)
                origin=b[-1]["time"]
                if len(b)>=128 and (origin+60)%300==0 and origin not in seen and origin+60<=now<origin+110:
                    w=b[-128:]
                    f=features(w[-61:]);seen.add(origin)
                    if f is not None and all(v["time"]-u["time"]==60 for u,v in zip(w,w[1:])):
                        atr=f[1]
                        # Alternate execution order to avoid systematically favoring either.
                        order=("fast","reference") if (origin+60)//300%2==0 else ("reference","fast")
                        for name in order:
                            if time.time()>=origin+115:
                                write({"type":"missed_deadline","variant":name,"origin":origin});continue
                            try:z,ms=forecast(w,atr,name)
                            except Exception as exc:
                                write({"type":"forecast_error","variant":name,"origin":origin,"error":type(exc).__name__})
                                print(name,"ERROR",type(exc).__name__,flush=True);continue
                            if time.time()>=origin+115:
                                write({"type":"missed_deadline","variant":name,"origin":origin,"inference_ms":ms});continue
                            e={"type":"forecast","variant":name,"origin":origin,"created_at":utc(time.time()),
                               "entry_time":origin+120,"exit_bar":origin+300,"atr":atr,"score":z,"inference_ms":ms}
                            write(e);pending[(origin,name)]=e
                            print("XAUUSD",name,"SHADOW", "BUY" if z>THRESHOLD else "SELL" if z<-THRESHOLD else "WAIT",
                                  round(z,4),"ATR",round(ms,2),"ms",flush=True)
                if now-last_heartbeat>=60:
                    print("GOLD SHADOW HEARTBEAT | last closed candle",utc(origin),"| pending",len(pending),flush=True)
                    last_heartbeat=now
            except Exception as exc:
                print("SHADOW_RETRY",type(exc).__name__,flush=True)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:pass
    finally:
        write({"type":"end","summary":summary(outcomes),"unresolved":len(pending),"execution_authorized":False})
        print("REPORT",journal,json.dumps(summary(outcomes),allow_nan=False),flush=True)
if __name__=="__main__":main()
