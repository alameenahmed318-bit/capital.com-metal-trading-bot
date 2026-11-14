#!/usr/bin/env python3
"""Optional AL-ZOOL local Qwen voting relay.

This process is isolated from MT5 execution. Ollama inference is best-effort:
short deadlines and a circuit breaker prevent a slow local model from being
called repeatedly. The deterministic central engine and risk gate remain the
source of entry authorization; this relay never sends orders.
"""
import getpass
import json
import os
import time
import urllib.error
import urllib.request

SERVER = os.getenv("ALZOOL_SERVER_URL", "https://al-zool-gold-sniper.onrender.com").rstrip("/")
OLLAMA = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.getenv("ALZOOL_LOCAL_AI_MODEL", "qwen2.5:3b")
POLL_SECONDS = 2.0
INFERENCE_TIMEOUT = 8
MAX_CONSECUTIVE_FAILURES = 2
COOLDOWN_SECONDS = 300
NUM_CTX = 1024
NUM_PREDICT = 96


def request_json(url, method="GET", payload=None, token=None, timeout=8):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def ollama_ready():
    tags = request_json(OLLAMA + "/api/tags", timeout=3)
    names = {str(x.get("name", "")) for x in tags.get("models", [])}
    if MODEL not in names and MODEL + ":latest" not in names:
        raise RuntimeError(
            "Ollama model is not installed. Local AI stays optional; no model is downloaded automatically."
        )


def ask_qwen(context):
    # Keep the prompt compact: only the central decision, market context and
    # the latest closed candles are sent. Never ask the model to calculate risk.
    compact = {
        "symbol": context.get("symbol"),
        "timeframe": context.get("timeframe"),
        "decision": context.get("decision"),
        "confidence": context.get("confidence"),
        "reason_ar": context.get("reason_ar"),
        "context": context.get("context", {}),
        "candles": (context.get("candles") or [])[:8],
    }
    prompt = (
        "أنت عضو تصويت مساعد فقط في مجلس AL-ZOOL. لا تنفذ صفقات ولا تخترع بيانات. "
        "قيّم الشموع المغلقة والإشارة المركزية فقط. عند الشك أجب WAIT. "
        "أعد JSON فقط بالمفاتيح direction وconfidence وreason_ar. "
        "BUY أو SELL مسموح فقط إذا وافق اتجاه القرار المركزي.\n"
        + json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    )
    result = request_json(
        OLLAMA + "/api/chat",
        method="POST",
        payload={
            "model": MODEL,
            "stream": False,
            "format": "json",
            "keep_alive": "2m",
            "messages": [{"role": "user", "content": prompt}],
            "options": {
                "temperature": 0.1,
                "num_ctx": NUM_CTX,
                "num_predict": NUM_PREDICT,
            },
        },
        timeout=INFERENCE_TIMEOUT,
    )
    raw = (result.get("message") or {}).get("content", "")
    vote = json.loads(raw)
    direction = str(vote.get("direction", "WAIT")).upper()
    confidence = float(vote.get("confidence", 0))
    reason = str(vote.get("reason_ar", "لم يقدم Qwen سبباً"))[:300]
    if direction not in ("BUY", "SELL", "WAIT") or not 0 <= confidence <= 1:
        raise ValueError("Qwen returned an invalid vote")
    if direction not in ("WAIT", context.get("decision")):
        direction = "WAIT"
        reason = "Qwen لم يؤيد اتجاه المحرك المركزي؛ انتظار"
    return {"direction": direction, "confidence": confidence, "reason_ar": reason}


def main():
    client_id = os.getenv("ALZOOL_ENGINE_ID") or input("AL-ZOOL Engine ID: ").strip()
    token = os.getenv("ALZOOL_ENGINE_TOKEN") or getpass.getpass("AL-ZOOL Engine Token: ").strip()
    if len(client_id) < 8 or len(token) < 20:
        raise SystemExit("Engine ID/token is missing or invalid.")

    print("Checking local Ollama and installed model...")
    ollama_ready()
    print(
        "Optional AL-ZOOL Qwen relay started. "
        "Central Quant/Council and MT5 protection do not wait for this process."
    )
    last_bar = 0
    failures = 0
    disabled_until = 0.0

    while True:
        try:
            context = request_json(
                SERVER + "/v1/local-ai/context/" + client_id,
                token=token,
                timeout=6,
            )
            bar_time = int(context.get("bar_time", 0))
            needs_vote = (
                bar_time > 0
                and (bar_time != last_bar or not context.get("local_ai_vote_current", False))
            )
            if needs_vote and time.monotonic() >= disabled_until:
                vote = ask_qwen(context)
                payload = {
                    "bar_time": bar_time,
                    "direction": vote["direction"],
                    "confidence": vote["confidence"],
                    "reason_ar": vote["reason_ar"],
                    "model": MODEL,
                }
                accepted = request_json(
                    SERVER + "/v1/local-ai/advice/" + client_id,
                    method="POST",
                    payload=payload,
                    token=token,
                    timeout=6,
                )
                last_bar = bar_time
                failures = 0
                print(
                    time.strftime("%H:%M:%S"),
                    "bar=", bar_time,
                    "central=", context.get("decision"),
                    "Qwen=", vote["direction"],
                    "confidence=", round(vote["confidence"], 2),
                    "accepted=", accepted.get("accepted"),
                    "|", vote["reason_ar"],
                    flush=True,
                )
        except urllib.error.HTTPError as exc:
            failures += 1
            print("Relay HTTP", exc.code, exc.reason, flush=True)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            failures += 1
            print("Optional local AI unavailable:", str(exc)[:180], flush=True)
        except Exception as exc:
            failures += 1
            print("Local AI warning:", str(exc)[:180], flush=True)

        if failures >= MAX_CONSECUTIVE_FAILURES:
            disabled_until = time.monotonic() + COOLDOWN_SECONDS
            print(
                "Qwen paused for 5 minutes after repeated failures. "
                "Central Quant/Council remain independent; no order is authorized by this relay.",
                flush=True,
            )
            failures = 0
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAL-ZOOL optional Qwen relay stopped.")
