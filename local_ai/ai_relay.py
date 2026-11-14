#!/usr/bin/env python3
"""AL-ZOOL local Qwen relay.

Runs beside MT5 on the user's PC. It never sends orders and never blocks MT5:
it polls the central server, asks the already-installed Ollama model for a vote,
and posts that vote back to the authenticated server endpoint.
"""
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request

SERVER = os.getenv("ALZOOL_SERVER_URL", "https://al-zool-gold-sniper.onrender.com").rstrip("/")
OLLAMA = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = "qwen2.5:3b"
POLL_SECONDS = 2.0


def request_json(url, method="GET", payload=None, token=None, timeout=12):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ollama_ready():
    tags = request_json(OLLAMA + "/api/tags", timeout=3)
    names = {str(x.get("name", "")) for x in tags.get("models", [])}
    if MODEL not in names and MODEL + ":latest" not in names:
        raise RuntimeError("Ollama is running, but qwen2.5:3b is not installed. No model will be downloaded automatically.")


def ask_qwen(context):
    prompt = (
        "You are the independent local AI voting seat for a gold trading risk council. "
        "You do not place trades. Judge only the supplied closed-candle data and central signal. "
        "If evidence is unclear, output WAIT. Do not invent indicators or prices. "
        "Return JSON only with keys direction (BUY, SELL, or WAIT), confidence (0 to 1), "
        "reason_ar (short Arabic reason). A BUY/SELL vote must agree with the central signal "
        "and have confidence at least 0.50; otherwise choose WAIT.\nDATA:\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )
    result = request_json(
        OLLAMA + "/api/chat", method="POST",
        payload={
            "model": MODEL, "stream": False, "format": "json",
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 180},
        },
        timeout=18,
    )
    raw = (result.get("message") or {}).get("content", "")
    vote = json.loads(raw)
    direction = str(vote.get("direction", "WAIT")).upper()
    confidence = float(vote.get("confidence", 0))
    reason = str(vote.get("reason_ar", "لم يقدم Qwen سبباً"))[:500]
    if direction not in ("BUY", "SELL", "WAIT") or not 0 <= confidence <= 1:
        raise ValueError("Qwen returned an invalid vote")
    if direction != context["decision"]:
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
    print("AL-ZOOL local Qwen relay is running. MT5 execution remains on the terminal.")
    last_bar = 0
    while True:
        try:
            context = request_json(
                SERVER + "/v1/local-ai/context/" + client_id, token=token, timeout=6
            )
            bar_time = int(context.get("bar_time", 0))
            if bar_time > 0 and bar_time != last_bar:
                vote = ask_qwen(context)
                payload = {
                    "bar_time": bar_time, "direction": vote["direction"],
                    "confidence": vote["confidence"], "reason_ar": vote["reason_ar"],
                    "model": MODEL,
                }
                accepted = request_json(
                    SERVER + "/v1/local-ai/advice/" + client_id,
                    method="POST", payload=payload, token=token, timeout=6,
                )
                last_bar = bar_time
                print(
                    time.strftime("%H:%M:%S"), "bar=", bar_time,
                    "central=", context.get("decision"),
                    "Qwen=", vote["direction"],
                    "confidence=", round(vote["confidence"], 2),
                    "accepted=", accepted.get("accepted"),
                    "|", vote["reason_ar"],
                    flush=True,
                )
        except urllib.error.HTTPError as exc:
            print("HTTP", exc.code, exc.reason, flush=True)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print("Relay/Ollama unavailable:", str(exc)[:180], flush=True)
        except Exception as exc:
            print("Local AI warning:", str(exc)[:180], flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAL-ZOOL local Qwen relay stopped.")
