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


def position_management_status(snapshot, settings, now=None):
    """Read-only diagnosis of action-poll prerequisites, never a trade permit."""
    now=now or datetime.now(timezone.utc)
    snapshot=snapshot or {}
    settings=settings or {}
    pm=settings.get("position_management") or {}
    mt5_connected=_fresh(snapshot.get("received_at"),now,30)
    trading_enabled=settings.get("trading_enabled") is True
    central_enabled=pm.get("central_actions_enabled") is True
    local_trading=snapshot.get("mt5_central_trading_enabled")
    local_management=snapshot.get("mt5_position_management_enabled")
    add_enabled=pm.get("addon_enabled") is True
    cool_enabled=pm.get("cooling_enabled") is True
    blockers=[]
    if not mt5_connected:
        blockers.append("MT5 غير متصل حالياً أو بياناته أقدم من 30 ثانية")
    if not trading_enabled:
        blockers.append("زر فتح الصفقات المركزي OFF في السيرفر")
    if not central_enabled:
        blockers.append("إجراءات الإدارة المركزية OFF في إعدادات المالك")
    if local_trading is not True:
        blockers.append("InpEnableCentralTrading داخل MT5 "+("OFF" if local_trading is False else "غير معروف؛ تأكد من نسخة EA"))
    if local_management is not True:
        blockers.append("InpEnableCentralPositionManagement داخل MT5 "+("OFF" if local_management is False else "غير معروف؛ تأكد من نسخة EA"))
    if not (add_enabled or cool_enabled):
        blockers.append("التعزيز والتبريد كلاهما OFF من إعدادات الاستراتيجية")
    polling_ready=not blockers
    return {
        "status":"READY_TO_POLL" if polling_ready else "BLOCKED",
        "polling_ready":polling_ready,
        "execution_authorized":False,
        "mt5_connected":mt5_connected,
        "trading_enabled":trading_enabled,
        "central_actions_enabled":central_enabled,
        "mt5_central_trading_enabled":local_trading if isinstance(local_trading,bool) else None,
        "mt5_position_management_enabled":local_management if isinstance(local_management,bool) else None,
        "addon_enabled":add_enabled,
        "cooling_enabled":cool_enabled,
        "blockers":blockers,
        "reason_ar":("قناة طلبات الإدارة مفعلة؛ التنفيذ يحتاج مركزاً مفتوحاً وإشارة حديثة وتصريحاً مستقلاً"
                     if polling_ready else " | ".join(blockers)),
    }

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
        "position_management_status":position_management_status(snapshot,settings,now),
        "checks":findings,
        "models":models,
        "latest_saved_candle_utc":_as_utc(latest_candle).isoformat() if _as_utc(latest_candle) else None,
        "note_ar":"يربط المحرك حساب MT5 التجريبي أو الحقيقي بدون تمييز. الجاهزية التقنية لا تعني أن التداول آمن أو مختبر؛ راجع الاختبارات قبل تشغيل أوامر مالية."
    }


def entry_execution_status(snapshot, ready=False, now=None):
    """Read-only broker-reported positions and latest opening fill.
    A ready signal is never proof of execution. Never infer a fill from
    strategy confidence or authorization, and never show stale positions as live.
    """
    now=now or datetime.now(timezone.utc)
    item=snapshot or {}
    symbol=item.get("symbol")
    stamp=_as_utc(item.get("received_at"))
    if not symbol or not stamp or not 0<=(now-stamp).total_seconds()<=30 or not isinstance(item.get("positions"),list):
        return {"state":"UNKNOWN","confirmed":False,"open_count":None,"buy_count":None,
                "sell_count":None,"last_entry":None,"reason_ar":"لا توجد بيانات MT5 حديثة للتحقق"}
    positions=[p for p in item["positions"] if isinstance(p,dict) and p.get("symbol")==symbol]
    def side(p):
        value=str(p.get("type",p.get("deal_type",""))).upper()
        return "BUY" if value in ("0","BUY","POSITION_TYPE_BUY","DEAL_TYPE_BUY") else "SELL" if value in ("1","SELL","POSITION_TYPE_SELL","DEAL_TYPE_SELL") else None
    buys=sum(side(p)=="BUY" for p in positions)
    sells=sum(side(p)=="SELL" for p in positions)
    def epoch(p):
        try:
            n=int(float(p.get("time",0)))
            return n if 0<n<4102444800 else 0
        except (ValueError,TypeError,OverflowError):return 0
    entries=[p for p in item.get("action_entry_deals") or [] if isinstance(p,dict) and p.get("symbol")==symbol
             and str(p.get("entry"))=="0" and p.get("position_id") and side(p) and epoch(p)]
    latest=max(entries+ [p for p in positions if p.get("position_id") and side(p) and epoch(p)],key=epoch,default=None)
    last=None
    if latest:
        tag=str(latest.get("comment") or "")[:80]
        pid=str(latest.get("position_id"))
        last={"side":side(latest),"position_id":pid,"time":epoch(latest),
            "comment":tag,"still_open":any(str(p.get("position_id"))==pid for p in positions),
            "source":"MT5_REPORTED","origin":"ALZOOL_ENTRY" if tag.startswith("ALZOOL-S") else
            "ALZOOL_ACTION" if tag in ("ALZOOL-ADD","ALZOOL-COOL","ALZOOL-RECOVERY") else "OTHER_OR_UNKNOWN"}
    state="OPEN" if positions else "READY_NOT_CONFIRMED" if ready else "NO_OPEN_POSITION"
    return {"state":state,"confirmed":bool(positions),"open_count":len(positions),"buy_count":buys,
        "sell_count":sells,"entry_ready":bool(ready),"last_entry":last,"observed_at":stamp.isoformat(),
        "reason_ar":"صفقات مفتوحة مؤكدة من MT5" if positions else
            "إشارة جاهزة؛ لم يرصد MT5 فتح صفقة" if ready else "لا توجد صفقات مفتوحة حالياً"}
