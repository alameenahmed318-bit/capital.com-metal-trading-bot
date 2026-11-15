"""Five read-only technical advisors for AL-ZOOL closed MT5 gold candles.

These are ORIGINAL rule-based implementations of common public trading ideas,
NOT the code, learned weights, or proprietary signals of commercial MQL5 EAs.
No trading authorizations or order calls occur in this module.
"""
from __future__ import annotations
import math

MIN_CANDLES = 65
_IDS = ("adaptive", "selective", "scalping", "breakout", "ensemble")
_LABELS = {
    "adaptive": "مساعد التحليل التكيفي",
    "selective": "مساعد الدخول الانتقائي",
    "scalping": "مساعد الحركة السريعة",
    "breakout": "مساعد الاختراقات",
    "ensemble": "مساعد الاستراتيجيات المتعددة",
}


def _ema(values, n):
    k = 2.0 / (n + 1.0)
    value = values[0]
    for x in values[1:]:
        value += (x - value) * k
    return value


def _rsi(values, n=14):
    diffs = [values[i] - values[i - 1] for i in range(len(values)-n, len(values))]
    gain = sum(max(0.0, x) for x in diffs) / n
    loss = sum(max(0.0, -x) for x in diffs) / n
    if gain == 0 and loss == 0:
        return 50.0
    return 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss)


def _atr(rows, n=14):
    out = []
    for i in range(max(1, len(rows)-n), len(rows)):
        prev, cur = rows[i-1], rows[i]
        out.append(max(cur["high"]-cur["low"], abs(cur["high"]-prev["close"]), abs(cur["low"]-prev["close"])))
    return sum(out) / len(out) if out else 0.0


def _result(key, direction, confidence, reason, evidence, risk=None):
    signal = direction if direction in ("BUY", "SELL") else "WAIT"
    return {
        "id": key, "name": _LABELS[key], "signal": signal,
        "confidence": round(min(0.85, max(0.0, float(confidence))), 3) if signal != "WAIT" else 0.0,
        "reason_ar": reason,
        "risk_note_ar": risk or "الرأي تحليلي فقط، وليس تصريحاً بتنفيذ صفقة.",
        "evidence": evidence,
    }


def _validate(rows):
    if not isinstance(rows, list) or len(rows) < MIN_CANDLES:
        return None
    clean = []
    previous_time = -1
    try:
        for row in rows[-120:]:
            o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
            t = int(row["time"])
            v = max(0.0, float(row.get("tick_volume", 0)))
            if not all(math.isfinite(x) for x in (o,h,l,c,v)) or l <= 0 or l > min(o,c) or h < max(o,c) or t <= previous_time:
                return None
            clean.append({"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v})
            previous_time = t
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return clean if len(clean) >= MIN_CANDLES else None


def analyze_advisors(closed_candles):
    """Pure, deterministic advisors; expects oldest-first CLOSED candles only.

    A malformed/insufficient series never creates a directional signal.
    The ensemble is a fifth *derived viewpoint*, not independent corroboration.
    """
    rows = _validate(closed_candles)
    if rows is None:
        items = [_result(k, "WAIT", 0, "بيانات الشموع المغلقة غير كافية أو غير صالحة؛ نحتاج 65 شمعة مرتبة.", {}) for k in _IDS]
        return {"status": "WAIT", "reason_ar": "لا توجد بيانات كافية للتحليل", "advisors": items,
                "consensus": {"signal": "WAIT", "agreeing": 0, "active": 0, "method": "advisory_only_no_execution"}}

    closes = [r["close"] for r in rows]
    last, prev = rows[-1], rows[-2]
    e8, e13, e21, e55 = (_ema(closes, p) for p in (8,13,21,55))
    rsi = _rsi(closes)
    atr = _atr(rows)
    historical_atr = _atr(rows[:-1])
    atr_ratio = atr / max(historical_atr, 1e-9)
    width = max(last["high"]-last["low"],1e-9)
    body = abs(last["close"]-last["open"]) / width
    upper_wick = (last["high"]-max(last["open"],last["close"])) / width
    lower_wick = (min(last["open"],last["close"])-last["low"]) / width
    volume_avg = sum(x["volume"] for x in rows[-21:-1])/20
    volume_ratio = last["volume"]/max(volume_avg,1) if volume_avg > 0 else 0
    bull = last["close"] > last["open"]
    bear = last["close"] < last["open"]
    up = e8 > e21 > e55
    down = e8 < e21 < e55
    evidence = {
        "close": round(last["close"], 5), "ema8": round(e8, 5),
        "ema21": round(e21,5), "ema55": round(e55,5),
        "rsi14": round(rsi,2), "atr14": round(atr,5),
        "atr_ratio": round(atr_ratio,3), "volume_ratio": round(volume_ratio,2),
        "candle_body_ratio": round(body,2),
    }

    # 1. Adaptive market-regime assistant.
    if atr <= 0 or atr_ratio > 2.2:
        adaptive = _result("adaptive","WAIT",0,"تذبذب غير معتاد؛ يحتاج السوق تأكيداً إضافياً.",evidence)
    elif up and rsi >= 52 and rsi <= 76 and last["close"] > e8:
        adaptive = _result("adaptive","BUY",0.59 + min(.17,(rsi-52)/100),"اتجاه المتوسطات صاعد والزخم متوافق مع الحركة.",evidence)
    elif down and 24 <= rsi <= 48 and last["close"] < e8:
        adaptive = _result("adaptive","SELL",0.59 + min(.17,(48-rsi)/100),"اتجاه المتوسطات هابط والزخم متوافق مع الحركة.",evidence)
    else:
        adaptive = _result("adaptive","WAIT",0,"لا يوجد تزامن كافٍ بين الاتجاه والزخم.",evidence)

    # 2. Conservative entry assistant: a close in trend direction, sensible RSI and wick.
    if atr <= 0 or atr_ratio > 2.0:
        selective = _result("selective","WAIT",0,"التحرك غير مستقر قياساً إلى نطاقه المعتاد.",evidence)
    elif up and bull and 52 < rsi < 70 and upper_wick < .35 and body >= .35:
        selective = _result("selective","BUY",.68,"شمعة مغلقة صاعدة مع اتجاه وزخم وذيل علوي محدود.",evidence)
    elif down and bear and 30 < rsi < 48 and lower_wick < .35 and body >= .35:
        selective = _result("selective","SELL",.68,"شمعة مغلقة هابطة مع اتجاه وزخم وذيل سفلي محدود.",evidence)
    else:
        selective = _result("selective","WAIT",0,"شمعة التأكيد أو موضع الزخم لا يحقق شروط الدخول الانتقائي.",evidence)

    # 3. Fast momentum assistant, no intrabar peeking; not a live tick scalper.
    if atr <= 0 or atr_ratio > 2.0:
        scalp = _result("scalping","WAIT",0,"تقلب مفاجئ غير مناسب لتحليل الزخم السريع.",evidence)
    elif bull and e8 > e13 and closes[-1] > max(x["high"] for x in rows[-5:-1]) and body >= .55 and volume_ratio >= 1:
        scalp = _result("scalping","BUY",.64,"إغلاق شمعة سريع فوق قمم الأربع شموع السابقة مع زخم وحجم داعم.",evidence)
    elif bear and e8 < e13 and closes[-1] < min(x["low"] for x in rows[-5:-1]) and body >= .55 and volume_ratio >= 1:
        scalp = _result("scalping","SELL",.64,"إغلاق شمعة سريع أسفل قيعان الأربع شموع السابقة مع زخم وحجم داعم.",evidence)
    else:
        scalp = _result("scalping","WAIT",0,"لا توجد دفعة سعرية قصيرة مكتملة على شمعة مغلقة.",evidence)

    # 4. 20-bar Donchian breakout; exclude the signal bar from its own range.
    high20 = max(x["high"] for x in rows[-21:-1])
    low20 = min(x["low"] for x in rows[-21:-1])
    breakout_ev = {**evidence, "prior20_high":round(high20,5),"prior20_low":round(low20,5)}
    if atr <= 0 or atr_ratio > 2.2:
        breakout = _result("breakout","WAIT",0,"تذبذب مرتفع؛ الاختراق قد يكون حركة استثنائية.",breakout_ev)
    elif last["close"] > high20 and bull and upper_wick < .35 and body >= .4 and volume_ratio >= 1:
        breakout = _result("breakout","BUY",.70,"إغلاق فوق أعلى 20 شمعة سابقة مع جسم وحجم مؤيدين.",breakout_ev)
    elif last["close"] < low20 and bear and lower_wick < .35 and body >= .4 and volume_ratio >= 1:
        breakout = _result("breakout","SELL",.70,"إغلاق تحت أدنى 20 شمعة سابقة مع جسم وحجم مؤيدين.",breakout_ev)
    else:
        breakout = _result("breakout","WAIT",0,"لا يوجد اختراق مؤكد بالإغلاق خارج نطاق 20 شمعة سابقة.",breakout_ev)

    # 5. Multi-factor view, deliberately derived from the four preceding voices.
    first = [adaptive, selective, scalp, breakout]
    buys = sum(x["signal"] == "BUY" for x in first)
    sells = sum(x["signal"] == "SELL" for x in first)
    if buys >= 2 and sells == 0:
        ensemble = _result("ensemble","BUY",.57 + .04*buys,
                           f"{buys} من 4 أساليب أصلية تميل للشراء دون معارضة اتجاهية.",{**evidence,"buy_votes":buys,"sell_votes":sells})
    elif sells >= 2 and buys == 0:
        ensemble = _result("ensemble","SELL",.57 + .04*sells,
                           f"{sells} من 4 أساليب أصلية تميل للبيع دون معارضة اتجاهية.",{**evidence,"buy_votes":buys,"sell_votes":sells})
    else:
        ensemble = _result("ensemble","WAIT",0,
                           "الأساليب غير متفقة أو التأييد غير كافٍ؛ لا ترجّح صفقة.",
                           {**evidence,"buy_votes":buys,"sell_votes":sells})
    advisors = [*first, ensemble]
    return {"status":"READY","symbol_hint":"XAUUSD","bar_time":last["time"],"last_close":last["close"],
            "candle_count":len(rows),"advisors":advisors,
            "consensus":{"signal":ensemble["signal"],"agreeing":max(buys,sells),
                         "active":buys+sells,"method":"four_independent_rule_sets_fifth_derived_advisory_only_no_execution"},
            "reason_ar":"هذه آراء من شموع MT5 المغلقة؛ لا تُصدر تصريح تداول ولا تتصل ببوتات MQL5 التجارية."}
