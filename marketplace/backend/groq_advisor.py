"""Advisory-only Groq GPT-OSS reviewer for AL-ZOOL's quant signals.

It never executes orders, modifies the council's three-vote authorization, or
receives personal/account credentials. It only sees compact market indicators.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request

MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
KEY = os.getenv("GROQ_API_KEY", "").strip()
API_URL = "https://api.groq.com/openai/v1/chat/completions"
_TIMEOUT = min(12.0, max(3.0, float(os.getenv("GROQ_TIMEOUT_SECONDS", "8"))))
_MIN_INTERVAL = max(120.0, float(os.getenv("GROQ_MIN_INTERVAL_SECONDS", "180")))
_DAILY_CAP = min(200, max(1, int(os.getenv("GROQ_DAILY_MAX_REQUESTS", "120"))))
_LOCK = threading.Lock()
_QUOTA = {"day": None, "count": 0, "last_call": 0.0}


def configured():
    return bool(KEY) and MODEL == "openai/gpt-oss-120b"


def is_candidate(decision, now=None):
    """Ask on selected *recent* directional opportunities, never on every tick."""
    if not isinstance(decision, dict):
        return False
    try:
        bar = int(decision.get("bar_time", 0))
        confidence = float(decision.get("confidence", 0))
        age = (time.time() if now is None else now) - bar
        if not math.isfinite(confidence) or not 0 <= age <= 180:
            return False
        if str(decision.get("decision", "")).upper() not in ("BUY", "SELL"):
            return False
        if confidence < 0.65:
            return False
        if float((decision.get("context") or {}).get("atr", 0)) <= 0:
            return False
        families = decision.get("family_votes")
        return isinstance(families, dict) and bool(families)
    except (ValueError, TypeError, OverflowError):
        return False


def _reserve(now_unix=None, now_monotonic=None):
    """Shared per-process quota. Refusals never consume a request."""
    now_unix = time.time() if now_unix is None else float(now_unix)
    now_monotonic = time.monotonic() if now_monotonic is None else float(now_monotonic)
    day = datetime.datetime.fromtimestamp(now_unix, datetime.timezone.utc).date().isoformat()
    with _LOCK:
        if _QUOTA["day"] != day:
            _QUOTA.update({"day": day, "count": 0, "last_call": 0.0})
        if _QUOTA["count"] >= _DAILY_CAP:
            return False, "DAILY_LIMIT"
        if _QUOTA["last_call"] and now_monotonic - _QUOTA["last_call"] < _MIN_INTERVAL:
            return False, "COOLDOWN"
        _QUOTA["count"] += 1
        _QUOTA["last_call"] = now_monotonic
    return True, "OK"


def _finite_number(value, minimum=-1e12, maximum=1e12):
    try:
        x = float(value)
        return round(x, 4) if math.isfinite(x) and minimum <= x <= maximum else None
    except (ValueError, TypeError, OverflowError):
        return None


def _compact(decision):
    # No owner/terminal identity, token, account balance, trades, orders, or URLs.
    context = decision.get("context") if isinstance(decision.get("context"), dict) else {}
    families = decision.get("family_votes") if isinstance(decision.get("family_votes"), dict) else {}
    combos = decision.get("combination_summary") if isinstance(decision.get("combination_summary"), dict) else {}
    bot_data = decision.get("advisory_bots") if isinstance(decision.get("advisory_bots"), dict) else {}
    bots = bot_data.get("advisors") if bot_data.get("status") == "READY" and bot_data.get("bar_time") == decision.get("bar_time") else []
    return {
        "symbol": "XAUUSD",
        "signal": str(decision.get("decision", "WAIT"))[:4],
        "bar_time": int(decision.get("bar_time", 0)),
        "confidence": _finite_number(decision.get("confidence"), 0, 1),
        "strategy_id": int(decision.get("strategy_id", 0)),
        "close": _finite_number(decision.get("entry_reference_price"), 0),
        "atr": _finite_number(context.get("atr"), 0),
        "family_votes": {str(k)[:40]: str(v)[:4] for k, v in list(families.items())[:20]},
        "combination_summary": {k: _finite_number(combos.get(k), 0, 1000) for k in ("BUY", "SELL", "WAIT")},
        "bot_signals": [
            {"bot_id": str(x.get("id", ""))[:30], "signal": str(x.get("signal", "WAIT"))[:4]}
            for x in (bots if isinstance(bots, list) else [])[:5] if isinstance(x, dict)
        ],
    }


def _call_groq(snapshot, *, api_key=None, timeout=None):
    prompt = (
        "You are a READ-ONLY XAUUSD reviewer of a deterministic trading system. "
        "Do not invent market information or imply that an order was placed. "
        "Assess only the supplied closed-bar indicators and votes, including conflicting signals. "
        'Return JSON only: {"direction":"BUY|SELL|WAIT","confidence":0.0,"reason_ar":"short Arabic reason"}. '
        "A missing or conflicted case should be WAIT. "
        "Never treat your response as an authorization to execute an order. "
        "Snapshot: " + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    )
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "reasoning_effort": "low",
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 320,
    }
    key = KEY if api_key is None else api_key
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT if timeout is None else timeout) as response:
        return json.loads(response.read(32768).decode("utf-8"))


def _parse_response(response):
    try:
        raw = response["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        direction = str(parsed["direction"]).upper()
        confidence = float(parsed["confidence"])
        reason = str(parsed.get("reason_ar", ""))[:250]
        if direction not in ("BUY", "SELL", "WAIT") or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("invalid vote")
        return {"model": "GPT-OSS 120B", "status": "READY", "direction": direction,
                "confidence": round(confidence, 3), "reason_ar": reason,
                "can_execute": False, "role": "advisory_only"}
    except (KeyError, IndexError, TypeError, ValueError):
        return {"model": "GPT-OSS 120B", "status": "INVALID", "direction": "WAIT",
                "confidence": 0, "reason_ar": "رد النموذج غير صالح", "can_execute": False,
                "role": "advisory_only"}


def review(decision):
    """Bounded, optional, fail-closed review. NEVER raises or blocks execution."""
    base = {"model": "GPT-OSS 120B", "direction": "WAIT", "confidence": 0,
            "can_execute": False, "role": "advisory_only"}
    if not configured():
        return {**base, "status": "NOT_CONFIGURED", "reason_ar": "لم يتم ضبط Groq"}
    if not is_candidate(decision):
        return {**base, "status": "SKIPPED", "reason_ar": "لا توجد فرصة حديثة مؤهلة للمراجعة"}
    ok, reason = _reserve()
    if not ok:
        return {**base, "status": reason, "reason_ar": "حماية حدود الاستخدام المجاني"}
    try:
        return _parse_response(_call_groq(_compact(decision)))
    except urllib.error.HTTPError as e:
        # Deliberately don't log response bodies, key, or authorization headers.
        status = "RATE_LIMITED" if e.code == 429 else "UNAVAILABLE"
        return {**base, "status": status, "reason_ar": "تعذر استدعاء Groq"}
    except (ValueError, TypeError, TimeoutError, OSError, urllib.error.URLError):
        return {**base, "status": "UNAVAILABLE", "reason_ar": "Groq غير متاح حالياً"}


def _safe_http_status(exc):
    """Report only well-known Groq error codes, never response text or secrets."""
    if exc.code == 403:
        try:
            data = json.loads(exc.read(2048).decode("utf-8"))
            error = data.get("error") if isinstance(data, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            if code == "model_permission_blocked_org":
                return "MODEL_BLOCKED_ORG_403"
            if code == "model_permission_blocked_project":
                return "MODEL_BLOCKED_PROJECT_403"
        except (ValueError, TypeError, UnicodeDecodeError, OSError, AttributeError):
            pass
        return "FORBIDDEN_403_OTHER"
    if exc.code == 401:
        return "UNAUTHORIZED_401"
    if exc.code == 429:
        return "RATE_LIMITED_429"
    return f"HTTP_{exc.code}"


def verify_credentials():
    """One small smoke inference on deployment; never uses account/market data."""
    if not configured():
        return "NOT_CONFIGURED"
    try:
        result = _call_groq({
            "signal": "WAIT", "bar_time": 0, "confidence": 0,
            "note": "TEST DATA ONLY. Return WAIT."
        }, timeout=12)
        vote = _parse_response(result)
        return "READY" if vote["status"] == "READY" else "INVALID_RESPONSE"
    except urllib.error.HTTPError as exc:
        return _safe_http_status(exc)
    except (ValueError, TypeError, TimeoutError, OSError, urllib.error.URLError):
        return "UNAVAILABLE"
