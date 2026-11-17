"""Read-only owner readiness checks.

Never grants trading permissions, changes licenses or guesses terminal state.
An API 'live' deployment alone is not proof that MT5 and the two models work.
"""
from datetime import datetime,timezone

def _as_utc(value):
    if isinstance(value,datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value,str):
        try:return datetime.fromisoformat(value.replace("Z","+00:00")).astimezone(timezone.utc)
        except (TypeError,ValueError):return None
    return None

def _fresh(value,now,max_seconds):
    stamp=_as_utc(value)
    if not stamp:return False
    seconds=(now-stamp).total_seconds()
    return -15<=seconds<=max_seconds

def owner_readiness(identity,snapshot,settings,council,latest_candle,now=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:now=now.replace(tzinfo=timezone.utc)
    identity_present=bool(identity and not identity.get("revoked",False))
    connected=bool(identity_present and snapshot and _fresh(snapshot.get("received_at"),now,30))
    market_fresh=_fresh(latest_candle,now,259200) # Allow the weekend market closure.
    decision=(snapshot or {}).get("strategy_decision") or {} if connected else {}
    signal_bar=decision.get("bar_time")
    try: bar_time=datetime.fromtimestamp(float(signal_bar),timezone.utc)
    except (ValueError,TypeError,OverflowError,OSError):bar_time=None
    signal_fresh=connected and _fresh(bar_time,now,600)
    votes=(council or {}).get("votes") or {}
    models={}
    for name in ("Fast AI","Qwen","DeepSeek"):
        vote=votes.get(name) or {}
        models[name]={"ready":bool(signal_fresh and vote.get("status")=="READY"),
            "status":vote.get("status") or "NOT_RECEIVED",
            "direction":vote.get("direction") if signal_fresh else "WAIT"}
    settings=settings or {}
    pm=settings.get("position_management") or {}
    findings=[
        {"id":"owner","label":"هوية المالك","ready":identity_present,
         "detail":"مرتبطة بمعرّف تخزين داخلي دون ترخيص عميل" if identity_present else "معرّف المالك غير موجود"},
        {"id":"mt5","label":"اتصال MT5","ready":connected,
         "detail":"نبضة حديثة أقل من 30 ثانية" if connected else "لم تصل نبضة حديثة من جهاز MT5"},
        {"id":"candles","label":"بيانات الذهب التاريخية","ready":market_fresh,
         "detail":"آخر شمعة خلال 72 ساعة" if market_fresh else "الشموع قديمة أو لم تُحفظ"},
        {"id":"signal","label":"تحليل شمعة حديثة","ready":bool(signal_fresh),
         "detail":"إشارة تم تحليلها خلال 10 دقائق" if signal_fresh else "التحليل قديم أو غير موجود"},
    ]
    for name in ("Fast AI","Qwen","DeepSeek"):
        m=models[name]
        findings.append({"id":name.lower().replace(" ","_"),"label":name,
            "ready":m["ready"],"detail":m["status"]})
    all_checks=all(x["ready"] for x in findings)
    return {
        "checked_at_utc":now.isoformat(),"stage":"DEMO_VALIDATION" if all_checks else "SETUP_REQUIRED",
        "ready_for_demo_validation":bool(all_checks),
        # Readiness is about connectivity/data/model health, not the MT5
        # account's demo/live classification. Neither account type is barred.
        # A live status here is NOT a certification of profitability or safety.
        "ready_for_real_money":False,
        "account_mode_policy":"DEMO_AND_REAL_SUPPORTED",
        "account_type_restriction":False,
        "manual_demo_test_required":True,
        "trading_enabled":settings.get("trading_enabled") is True,
        "central_actions_enabled":pm.get("central_actions_enabled") is True,
        "checks":findings,
        "models":models,
        "latest_saved_candle_utc":_as_utc(latest_candle).isoformat() if _as_utc(latest_candle) else None,
        "note_ar":"يربط المحرك حساب MT5 التجريبي أو الحقيقي بدون تمييز. الجاهزية التقنية لا تعني أن التداول آمن أو مختبر؛ راجع الاختبارات قبل تشغيل أوامر مالية."
    }
