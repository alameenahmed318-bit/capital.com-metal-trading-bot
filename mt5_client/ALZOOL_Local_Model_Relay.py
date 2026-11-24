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
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

MODELS=("qwen2.5:1.5b",)  # Lightweight CPU-only live voting; old models remain installed
OLLAMA_URL=os.getenv("ALZOOL_OLLAMA_URL","http://127.0.0.1:11434").rstrip("/")
POLL_SECONDS=3
INDEPENDENT_PROTOCOL="INDEPENDENT_SHADOW_V4"
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
    if server != "https://al-zool-meeno-engine.onrender.com":
        raise ValueError("استخدم ملف ربط البيتكوين الجديد، وليس ملف الذهب")
    if not server.startswith("https://") or not identity or len(key)<32:
        raise ValueError("بيانات الاقتران ناقصة: صدّر ملف ربط المالك الجديد من لوحة AL-ZOOL")
    if any(x in identity for x in "/?&#"):
        raise ValueError("معرّف الاتصال غير صالح")
    return server,identity,key

def validate_response(model,result):
    """Read an actual model JSON vote; never invent a missing direction or confidence."""
    message=result.get("message") if isinstance(result.get("message"),dict) else {}
    raw=message.get("content",result.get("response",""))
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
        if not reason.strip():
            return None
        vote={"model":model,"direction":direction,"confidence":confidence,"reason_ar":reason}
        # Compact multilingual Qwen sometimes answers in Chinese. A foreign
        # explanation is NOT evidence of an invalid direction/confidence.
        # Keep the actual model rationale separately, and explicitly label the
        # unavailable Arabic translation rather than fabricating a reason.
        if model=="qwen2.5:1.5b" and not re.search(r"[\u0600-\u06FF]",reason):
            vote["reason_original"]=reason
            vote["reason_ar"]="سبب النموذج بلغة أخرى، ولم تتم ترجمته"
        return vote
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
    for row in candles[:3]:  # Server returns newest closed candle first.
        if isinstance(row,dict):
            recent.append({k:row[k] for k in ("time","open","high","low","close")
                           if k in row})
    return {
        "symbol":context.get("symbol"),
        "bar_time":context.get("bar_time"),
        "central_signal":central_signal(context),
        "central_confidence":context.get("confidence",0),
        "strategy_name":str(context.get("strategy_name") or "")[:80],
        "market_regime":features.get("regime"),
        "regime_evidence":str(features.get("reason") or "")[:120],
        "atr":features.get("atr"),
        "rsi":features.get("rsi"),
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


def independent_snapshot(context):
    """Blind market features only. No central side, confidence, votes, or model names."""
    ctx=context.get("context") if isinstance(context.get("context"),dict) else {}
    candles=context.get("candles") if isinstance(context.get("candles"),list) else []
    recent=[]
    for row in candles[:6]:
        if isinstance(row,dict):
            recent.append({key:row[key] for key in ("time","open","high","low","close","tick_volume") if key in row})
    return {"symbol":context.get("symbol"),"bar_time":context.get("bar_time"),
            "atr":ctx.get("atr"),"atr_ratio":ctx.get("atr_ratio"),
            "adx":ctx.get("adx"),"vwap":ctx.get("vwap"),
            "support":ctx.get("support"),"resistance":ctx.get("resistance"),
            "candles_newest_first":recent}

def model_vote(model,context,timeout_seconds=MODEL_TIMEOUT_SECONDS):
    """Use chat content, not generate reasoning; never turn model thoughts into a vote."""
    blind=context.get("_analysis_protocol")==INDEPENDENT_PROTOCOL
    snapshot=independent_snapshot(context) if blind else compact_snapshot(context)
    symbol=str(context.get("symbol") or "BTCUSD").upper()
    market="XAUUSD Gold" if symbol.startswith("XAUUSD") else "BTCUSD Bitcoin"
    instruction=("You are a cautious "+market+" M1 advisory analyst. Use ONLY actual supplied "
        "closed candles, trends and indicators; central_signal is not an order. "
        "RSI near 58 is NOT overbought; normally overbought RSI starts around 70. "
        "Do not infer any missing indicator. WAIT when evidence is insufficient. "
        "Return one JSON object exactly with direction (BUY, SELL or WAIT), "
        "confidence (0..1), reason_ar (short reason written ONLY in Arabic). "
        "The reason_ar MUST contain Arabic script, NOT Chinese or English. "
        "Keep Arabic reason under 10 words, no extra text.")
    if blind:
        instruction=("Independent "+market+" analyst. Predict the direction of the current M1 candle "
            "until it closes, using only the supplied recent CLOSED candles and measured indicators. "
            "No central forecast is supplied. BUY means price likely rises; SELL means likely falls. "
            "Choose WAIT if evidence is unclear. Consider wick rejection and support/resistance. "
            "Do not invent data or claim guaranteed accuracy. Output JSON direction, confidence 0..1, "
            "and short Arabic reason_ar. Confidence is an estimate, not a calibrated probability.")
    schema={"type":"object","properties":{
        "direction":{"type":"string","enum":["BUY","SELL","WAIT"]},
        "confidence":{"type":"number","minimum":0,"maximum":1},
        "reason_ar":{"type":"string"}},
        "required":["direction","confidence","reason_ar"]}
    # /api/chat reports the actual final answer in message.content. Some
    # thinking models put their reasoning in message.thinking; never parse it.
    result=request_json(OLLAMA_URL+"/api/chat",method="POST",
        payload={"model":model,"messages":[
                    {"role":"system","content":instruction},
                    {"role":"user","content":json.dumps(snapshot,ensure_ascii=False,separators=(",",":"))}],
                 "stream":False,"format":"json" if model.startswith("deepseek") else schema,
                 "think":False,"keep_alive":"30m",
                 "options":{"temperature":0,"num_predict":352 if model.startswith("deepseek") else 100,
                            "num_ctx":1024,"num_thread":4}},
        timeout=timeout_seconds)
    vote=validate_response(model,result)
    if vote is None:
        message=result.get("message") if isinstance(result.get("message"),dict) else {}
        content=message.get("content",result.get("response",""))
        thinking=message.get("thinking",result.get("thinking",""))
        done=str(result.get("done_reason") or "unknown")[:32]
        print(model+" RESPONSE_DIAG content_chars="+str(len(content) if isinstance(content,str) else 0)+
            " thinking_chars="+str(len(thinking) if isinstance(thinking,str) else 0)+
            " done="+done+" tokens="+str(result.get("eval_count","?")),flush=True)
    return vote


def same_live_signal(server,identity,key,bar,signal):
    """Do not submit a vote after a slow CPU generation crossed to a new bar."""
    url=server+"/v1/local-ai/context/"+urllib.parse.quote(identity,safe="")
    latest=request_json(url,token=key,timeout=8)
    return (int(latest.get("bar_time",0))==bar
            and central_signal(latest)==signal)


def record_model_failure(model,failures,pause_until):
    """A slow/empty model may not consume every future M1 candle."""
    failures[model]=failures.get(model,0)+1
    if failures[model]>=2:
        seconds=600 if model.startswith("deepseek") else 300
        pause_until[model]=time.monotonic()+seconds
        failures[model]=0
        print(model+" CPU_COOLDOWN "+str(seconds)+"s; Fast AI remains active",flush=True)


def main():
    parser=argparse.ArgumentParser(description="AL-ZOOL Qwen and DeepSeek local voting relay")
    parser.add_argument("--credentials",help="Downloaded owner pairing JSON file")
    parser.add_argument("--once",action="store_true",help="Inspect one closed-bar vote cycle")
    parser.add_argument("--independent-shadow",action="store_true",
        help="V4 independent forward-test; never submits a live council vote")
    args=parser.parse_args()
    server,identity,key=credentials(args.credentials)
    try:
        installed={row.get("name") for row in request_json(OLLAMA_URL+"/api/tags",timeout=8).get("models",[])}
    except Exception as exc:
        raise RuntimeError("Ollama غير متصل على اللابتوب: "+type(exc).__name__) from exc
    missing=[model for model in MODELS if model not in installed]
    if missing:
        raise RuntimeError("الموديلات غير مثبتة؛ استخدم: "+", ".join("ollama pull "+m for m in missing))
    print("AL-ZOOL LOCAL AI READY | model=Qwen 2.5 1.5B FAST | heavy models offline | trading execution=OFF",flush=True)
    if args.independent_shadow:
        print("ACCURACY V4 | INDEPENDENT SHADOW | NO LIVE VOTE OR ORDER",flush=True)
    last_bar=0
    submitted=set()
    attempts={}
    model_failures={}
    model_pause_until={}
    while True:
        try:
            url=server+"/v1/local-ai/context/"+urllib.parse.quote(identity,safe="")
            context=request_json(url,token=key,timeout=12)
            if args.independent_shadow:
                if context.get("accuracy_protocol")!=INDEPENDENT_PROTOCOL:
                    print("V4 protocol unavailable on server; no vote sent",flush=True)
                    break
                context=dict(context,_analysis_protocol=INDEPENDENT_PROTOCOL)
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
            # here avoids wasting CPU on non-actionable bars.
            if signal not in ("BUY","SELL") and not args.independent_shadow:
                if not submitted and not attempts:
                    print("CENTRAL WAIT | no local model vote for this bar",flush=True)
                    attempts["wait_logged"]=1
                if args.once:break
                time.sleep(POLL_SECONDS);continue
            # Fast CPU-friendly Qwen is the only M1 voting model.
            # Large reasoning models are kept installed for offline research.
            # For a closed M1 bar, the next closed bar is available at bar+120s.
            # Leave 4s for server round-trips so old votes cannot be submitted.
            pending=[model for model in MODELS
                if model not in submitted and attempts.get(model,0)<1
                and time.monotonic()>=model_pause_until.get(model,0.0)]
            if pending:
                deadline=bar+120-4
                for model in pending:
                    remaining=deadline-time.time()
                    min_budget=12
                    if remaining<min_budget:
                        print(model+" SKIP_BAR_BUDGET; next M1 candle",flush=True)
                        attempts[model]=1
                        continue
                    # Do not spend longer than one short CPU inference on a single M1 candle.
                    limit=18
                    timeout=max(1,min(limit,int(remaining)-2))
                    attempts[model]=1  # No CPU-expensive same-bar retry.
                    started=time.monotonic()
                    try:
                        vote=model_vote(model,context,timeout_seconds=timeout)
                    except TimeoutError:
                        print(model+" ERROR TimeoutError",flush=True)
                        record_model_failure(model,model_failures,model_pause_until)
                        continue
                    except Exception as exc:
                        print(model+" ERROR "+type(exc).__name__,flush=True)
                        record_model_failure(model,model_failures,model_pause_until)
                        continue
                    if vote is None:
                        print(model+" INVALID_OUTPUT (JSON, direction, confidence or empty reason); no vote submitted",flush=True)
                        record_model_failure(model,model_failures,model_pause_until)
                        continue
                    model_failures[model]=0
                    if vote.get("reason_original"):
                        print(model+" NON_ARABIC_REASON; original preserved, vote validated",flush=True)
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
                    vote["symbol"]=context.get("symbol")
                    if args.independent_shadow:
                        vote["analysis_protocol"]=INDEPENDENT_PROTOCOL
                    try:
                        endpoint="/v1/local-ai/shadow-v4/" if args.independent_shadow else "/v1/local-ai/advice/"
                        ack=request_json(server+endpoint+
                            urllib.parse.quote(identity,safe=""),"POST",vote,key,timeout=12)
                        if args.independent_shadow and ack.get("shadow_only") is not True:
                            raise RuntimeError("Server does not confirm V4 shadow protocol; stop using V4 until server updated")
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
