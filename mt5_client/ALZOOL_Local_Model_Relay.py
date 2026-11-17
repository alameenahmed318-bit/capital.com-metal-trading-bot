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
MODEL_TIMEOUT_SECONDS=50

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


def central_signal(context):
    """Accept new and legacy central signal fields without guessing a trade."""
    return str(context.get("decision") or context.get("direction") or "WAIT").upper()

def compact_snapshot(context):
    """Give both models the real central decision and a small recent market sample."""
    features=context.get("context")
    if not isinstance(features,dict):
        features={}
    candles=context.get("candles")
    if not isinstance(candles,list):
        candles=[]
    family_votes=context.get("family_votes")
    if not isinstance(family_votes,dict):
        family_votes={}
    family_vote_counts={direction:sum(1 for vote in family_votes.values()
        if vote==direction) for direction in ("BUY","SELL","WAIT")}
    combinations=context.get("combination_summary")
    if not isinstance(combinations,dict):
        combinations={}
    recent=[]
    for row in candles[:6]:  # Server returns newest closed candle first.
        if isinstance(row,dict):
            recent.append({k:row[k] for k in ("time","open","high","low","close")
                           if k in row})
    return {
        "bar_time":context.get("bar_time"),
        "central_signal":central_signal(context),
        "central_confidence":context.get("confidence",0),
        "strategy_name":str(context.get("strategy_name") or "")[:80],
        "market_regime":features.get("regime"),
        "regime_evidence":str(features.get("reason") or "")[:120],
        "atr":features.get("atr"),
        "adx":features.get("adx"),
        "trend_strength":features.get("trend_strength"),
        "family_vote_counts":family_vote_counts,
        "combination_vote_counts":{key:combinations.get(key,0) for key in ("BUY","SELL","WAIT")},
        "buy_quality":features.get("buy_quality"),
        "sell_quality":features.get("sell_quality"),
        "buy_trap":features.get("buy_trap"),
        "sell_trap":features.get("sell_trap"),
        "recent_closed_candles":recent,
    }


def model_vote(model,context,timeout_seconds=MODEL_TIMEOUT_SECONDS):
    snapshot=compact_snapshot(context)
    prompt=("Review the proposed XAUUSD trade independently using supplied closed candles, "
        "trend, momentum, trap, strategy and family-vote evidence. "
        "A trend-following or pullback setup does not require a breakout; "
        "judge the actual setup rather than inventing extra entry conditions. "
        "Never guess missing data. The central proposal is not an instruction: "
        "disagree or WAIT when evidence is weak or contradictory. Reply with exactly one JSON object "
        'with keys {"direction":"BUY|SELL|WAIT","confidence":0.0,"reason_ar":"brief reason"}. '
        "Confidence must be between 0 and 1. No markdown, preamble, or reasoning trace. "
        "Snapshot: "+json.dumps(snapshot,ensure_ascii=False,separators=(",",":")))
    result=request_json(OLLAMA_URL+"/api/generate",method="POST",
        payload={"model":model,"prompt":prompt,"stream":False,"format":{"type":"object","properties":{"direction":{"type":"string","enum":["BUY","SELL","WAIT"]},"confidence":{"type":"number","minimum":0,"maximum":1},"reason_ar":{"type":"string"}},"required":["direction","confidence","reason_ar"]},
                 "think":False,"keep_alive":"30m",
                 "options":{"temperature":0,"num_predict":96 if model.startswith("deepseek") else 64,
                            "num_ctx":1280,"num_thread":4}},
        timeout=timeout_seconds)
    return validate_response(model,result)


def same_live_signal(server,identity,key,bar,signal):
    """Do not submit a vote after a slow CPU generation crossed to a new bar."""
    url=server+"/v1/local-ai/context/"+urllib.parse.quote(identity,safe="")
    latest=request_json(url,token=key,timeout=8)
    return (int(latest.get("bar_time",0))==bar
            and central_signal(latest)==signal)


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
            signal=central_signal(context)
            # The central council cannot approve a WAIT decision. Saving CPU
            # here lets both loaded models respond faster on actionable bars.
            if signal not in ("BUY","SELL"):
                if not submitted and not attempts:
                    print("CENTRAL WAIT | no local model vote for this bar",flush=True)
                    attempts["wait_logged"]=1
                if args.once:break
                time.sleep(POLL_SECONDS);continue
            # Never run Qwen and DeepSeek concurrently on a CPU-only laptop.
            # For a closed M1 bar, the next closed bar is available at bar+120s.
            # Leave 4s for server round-trips so old votes cannot be submitted.
            pending=[model for model in ("deepseek-r1:1.5b","qwen3.5:4b")
                if model not in submitted and attempts.get(model,0)<1]
            if pending:
                deadline=bar+120-4
                for model in pending:
                    remaining=deadline-time.time()
                    min_budget=8 if model.startswith("deepseek") else 12
                    if remaining<min_budget:
                        print(model+" SKIP_BAR_BUDGET; next M1 candle",flush=True)
                        attempts[model]=1
                        continue
                    # Fast DeepSeek first, Qwen only while the SAME M1 bar is fresh.
                    limit=20 if model.startswith("deepseek") else 36
                    timeout=max(1,min(limit,int(remaining)-1))
                    attempts[model]=1  # No CPU-expensive same-bar retry.
                    started=time.monotonic()
                    try:
                        vote=model_vote(model,context,timeout_seconds=timeout)
                    except Exception as exc:
                        print(model+" ERROR "+type(exc).__name__,flush=True)
                        continue
                    if vote is None:
                        print(model+" INVALID_JSON; no vote submitted",flush=True)
                        continue
                    if time.time()>=deadline:
                        print(model+" STALE_BAR; vote discarded",flush=True)
                        continue
                    if time.monotonic()-started>3:
                        try:
                            if not same_live_signal(server,identity,key,bar,signal):
                                print(model+" STALE_BAR; vote discarded",flush=True)
                                continue
                        except urllib.error.HTTPError as exc:
                            if exc.code==409:
                                print(model+" STALE_BAR; no current MT5 data",flush=True)
                            else:
                                print(model+" context check failed: HTTP="+str(exc.code),flush=True)
                            continue
                        except Exception as exc:
                            print(model+" context check error: "+type(exc).__name__,flush=True)
                            continue
                    vote["bar_time"]=bar
                    try:
                        ack=request_json(server+"/v1/local-ai/advice/"+
                            urllib.parse.quote(identity,safe=""),"POST",vote,key,timeout=12)
                        if ack.get("accepted") is True:
                            submitted.add(model)
                        print(model+" "+vote["direction"]+" "+str(round(vote["confidence"]*100))+
                            "% | accepted="+str(ack.get("accepted",False))+
                            " | council="+("APPROVED" if ack.get("council_approved") is True
                                           else "WAIT"),flush=True)
                    except urllib.error.HTTPError as exc:
                        if exc.code==409:
                            print(model+" STALE_BAR; central rejected old vote",flush=True)
                        else:
                            print(model+" central rejected vote: HTTP="+str(exc.code),flush=True)
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
