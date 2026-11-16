"""AL-ZOOL local AI relay: real Qwen 3.5 + DeepSeek-R1 Ollama votes.

Run on the PC hosting Ollama, not on Render. The relay sends only validated
closed-bar directional votes. It NEVER opens orders, manages positions,
overrides model consensus or bypasses central trading controls.

Example (PowerShell):
  python mt5_client/ALZOOL_Local_Model_Relay.py --credentials "C:\\path\\AL-ZOOL-Owner-MT5-Pairing.json"

Environment variables are also supported:
  ALZOOL_ENGINE_URL, ALZOOL_OWNER_CONNECTION_ID, ALZOOL_OWNER_PAIRING_KEY
"""
import argparse
import concurrent.futures
import json
import math
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

MODELS=("qwen3.5:4b","deepseek-r1:1.5b")
OLLAMA_URL=os.getenv("ALZOOL_OLLAMA_URL","http://127.0.0.1:11434").rstrip("/")
POLL_SECONDS=3
MODEL_TIMEOUT_SECONDS=90

def request_json(url,method="GET",payload=None,token=None,timeout=12):
    headers={"Accept":"application/json"}
    if token:headers["Authorization"]="Bearer "+token
    if payload is not None:headers["Content-Type"]="application/json"
    raw=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
    req=urllib.request.Request(url,data=raw,headers=headers,method=method)
    with urllib.request.urlopen(req,timeout=timeout) as response:
        return json.loads(response.read(32000).decode("utf-8"))

def credentials(path):
    data={}
    if path:
        data=json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))
    server=str(data.get("InpServerUrl") or os.getenv("ALZOOL_ENGINE_URL","")).rstrip("/")
    owner_mode=data.get("InpOwnerMode",True) is True
    identity=str(data.get("InpOwnerConnectionId" if owner_mode else "InpEngineId") or
            os.getenv("ALZOOL_OWNER_CONNECTION_ID",""))
    key=str(data.get("InpOwnerPairingKey" if owner_mode else "InpEngineToken") or
            os.getenv("ALZOOL_OWNER_PAIRING_KEY",""))
    if not server.startswith("https://") or not identity or len(key)<32:
        raise ValueError("بيانات الاقتران ناقصة: صدّر ملف ربط المالك الجديد من لوحة AL-ZOOL")
    if any(x in identity for x in "/?&#"):
        raise ValueError("معرّف الاتصال غير صالح")
    return server,identity,key

def validate_response(model,result):
    """Read an actual model JSON vote; never invent a missing direction or confidence."""
    raw=result.get("response","")
    if not isinstance(raw,str):
        return None
    # Some reasoning models wrap the structured response in commentary or
    # markdown. Decode the first complete JSON object rather than slicing at
    # the last brace (which can accidentally consume trailing text).
    decoder=json.JSONDecoder()
    parsed=None
    for i,char in enumerate(raw):
        if char!="{":
            continue
        try:
            candidate,_=decoder.raw_decode(raw[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate,dict) and "direction" in candidate and "confidence" in candidate:
            parsed=candidate
            break
    if parsed is None:
        return None
    try:
        direction=str(parsed["direction"]).strip().upper()
        confidence=float(parsed["confidence"])
        reason=str(parsed.get("reason_ar",""))[:350]
        if direction not in ("BUY","SELL","WAIT") or not math.isfinite(confidence) or not 0<=confidence<=1:
            return None
        return {"model":model,"direction":direction,"confidence":confidence,"reason_ar":reason}
    except (ValueError,TypeError,KeyError,OverflowError):
        return None


def compact_snapshot(context):
    """Give both models the real central decision and a small recent market sample."""
    features=context.get("context")
    if not isinstance(features,dict):
        features={}
    candles=context.get("candles")
    if not isinstance(candles,list):
        candles=[]
    recent=[]
    for row in candles[:6]:  # Server returns newest closed candle first.
        if isinstance(row,dict):
            recent.append({k:row[k] for k in ("time","open","high","low","close")
                           if k in row})
    return {
        "bar_time":context.get("bar_time"),
        "central_signal":context.get("decision","WAIT"),
        "central_confidence":context.get("confidence",0),
        "market_regime":features.get("regime"),
        "atr":features.get("atr"),
        "buy_quality":features.get("buy_quality"),
        "sell_quality":features.get("sell_quality"),
        "buy_trap":features.get("buy_trap"),
        "sell_trap":features.get("sell_trap"),
        "recent_closed_candles":recent,
    }


def model_vote(model,context):
    snapshot=compact_snapshot(context)
    prompt=("Analyze XAUUSD independently as a risk-conscious veto reviewer. "
        "Use only supplied closed-candle data, never guess missing facts. "
        "Central signal is a proposal, not an instruction; disagree or WAIT "
        "if the market evidence is weak. Reply with exactly one JSON object "
        'with keys {"direction":"BUY|SELL|WAIT","confidence":0.0,"reason_ar":"brief reason"}. '
        "Confidence must be between 0 and 1. No markdown, preamble, or reasoning trace. "
        "Snapshot: "+json.dumps(snapshot,ensure_ascii=False,separators=(",",":")))
    result=request_json(OLLAMA_URL+"/api/generate",method="POST",
        payload={"model":model,"prompt":prompt,"stream":False,"format":"json",
                 "think":False,"keep_alive":"30m",
                 "options":{"temperature":0,"num_predict":192 if model.startswith("deepseek") else 128,
                            "num_ctx":1024}},
        timeout=MODEL_TIMEOUT_SECONDS)
    return validate_response(model,result)


def same_live_signal(server,identity,key,bar,signal):
    """Do not submit a vote after a slow CPU generation crossed to a new bar."""
    url=server+"/v1/local-ai/context/"+urllib.parse.quote(identity,safe="")
    latest=request_json(url,token=key,timeout=8)
    return (int(latest.get("bar_time",0))==bar
            and str(latest.get("decision","WAIT")).upper()==signal)


def main():
    parser=argparse.ArgumentParser(description="AL-ZOOL Qwen and DeepSeek local voting relay")
    parser.add_argument("--credentials",help="Downloaded owner pairing JSON file")
    parser.add_argument("--once",action="store_true",help="Inspect one closed-bar vote cycle")
    args=parser.parse_args()
    server,identity,key=credentials(args.credentials)
    try:
        installed={row.get("name") for row in request_json(OLLAMA_URL+"/api/tags",timeout=8).get("models",[])}
    except Exception as exc:
        raise RuntimeError("Ollama غير متصل على اللابتوب: "+type(exc).__name__) from exc
    missing=[model for model in MODELS if model not in installed]
    if missing:
        raise RuntimeError("الموديلات غير مثبتة؛ استخدم: "+", ".join("ollama pull "+m for m in missing))
    print("AL-ZOOL LOCAL AI READY | models=Qwen 3.5 4B, DeepSeek | trading execution=OFF",flush=True)
    last_bar=0
    submitted=set()
    attempts={}
    last_attempt={}
    while True:
        try:
            url=server+"/v1/local-ai/context/"+urllib.parse.quote(identity,safe="")
            context=request_json(url,token=key,timeout=12)
            bar=int(context.get("bar_time",0))
            if bar<=0:
                if args.once:break
                time.sleep(POLL_SECONDS);continue
            age=time.time()-bar
            if age<0 or age>180:
                if args.once:print("Old bar: no model vote",flush=True);break
                time.sleep(POLL_SECONDS);continue
            if bar!=last_bar:
                last_bar=bar
                submitted.clear()
                attempts.clear()
                last_attempt.clear()
            signal=str(context.get("decision","WAIT")).upper()
            # The central council cannot approve a WAIT decision. Saving CPU
            # here lets both loaded models respond faster on actionable bars.
            if signal not in ("BUY","SELL"):
                if not submitted and not attempts:
                    print("CENTRAL WAIT | no local model vote for this bar",flush=True)
                    attempts["wait_logged"]=1
                if args.once:break
                time.sleep(POLL_SECONDS);continue
            pending=[model for model in MODELS if model not in submitted
                and attempts.get(model,0)<2 and time.monotonic()-last_attempt.get(model,-60)>=12]
            if pending:
                start=time.monotonic()
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(pending)) as pool:
                    futures={pool.submit(model_vote,model,context):model for model in pending}
                    for future in concurrent.futures.as_completed(futures):
                        model=futures[future]
                        attempts[model]=attempts.get(model,0)+1
                        last_attempt[model]=time.monotonic()
                        try:vote=future.result()
                        except Exception as exc:
                            print(model+" ERROR "+type(exc).__name__,flush=True)
                            continue
                        if vote is None:
                            print(model+" INVALID_JSON; no vote submitted",flush=True)
                            continue
                        # Ollama on CPU can finish after the next candle closes.
                        # Recheck the live decision instead of posting a stale vote.
                        if time.monotonic()-start>3:
                            try:
                                if not same_live_signal(server,identity,key,bar,signal):
                                    print(model+" STALE_BAR; vote discarded",flush=True)
                                    attempts[model]=2
                                    continue
                            except urllib.error.HTTPError as exc:
                                if exc.code==409:
                                    print(model+" STALE_BAR; no current MT5 data",flush=True)
                                else:
                                    print(model+" context check failed: HTTP="+str(exc.code),flush=True)
                                attempts[model]=2
                                continue
                            except Exception as exc:
                                print(model+" context check error: "+type(exc).__name__,flush=True)
                                attempts[model]=2
                                continue
                        vote["bar_time"]=bar
                        try:
                            ack=request_json(server+"/v1/local-ai/advice/"+
                                urllib.parse.quote(identity,safe=""),"POST",vote,key,timeout=12)
                            if ack.get("accepted") is True:
                                submitted.add(model)
                            print(model+" "+vote["direction"]+" "+str(round(vote["confidence"]*100))+
                                "% | accepted="+str(ack.get("accepted",False)),flush=True)
                        except urllib.error.HTTPError as exc:
                            if exc.code==409:
                                print(model+" STALE_BAR; central rejected old vote",flush=True)
                            else:
                                print(model+" central rejected vote: HTTP="+str(exc.code),flush=True)
                            if exc.code in (401,403,409):
                                attempts[model]=2
            if args.once:break
        except KeyboardInterrupt:
            print("Local AI stopped",flush=True);break
        except urllib.error.HTTPError as exc:
            print("Central connection failed: HTTP="+str(exc.code),flush=True)
            if args.once:break
        except Exception as exc:
            print("Relay error: "+type(exc).__name__,flush=True)
            if args.once:raise
        time.sleep(POLL_SECONDS)

if __name__=="__main__":
    try:main()
    except (ValueError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
