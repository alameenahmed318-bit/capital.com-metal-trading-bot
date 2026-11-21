"""Smart recovery: a loss threshold ARMS analysis, never authorizes a trade.

After the account loses the owner-selected amount, require ONE fully CLOSED M1
candle opened after arming and visibly supporting the recovery side. The
existing strategy/Hybrid direction contributes a small timing bias, not a
three-model consensus veto. All current broker, margin, owner lot caps,
one-use claim and recovery-cycle safeguards remain enforced elsewhere.
"""
import math
import threading
import time
from datetime import datetime,timezone

_ARM_LOCK=threading.RLock()
_ARMED={}  # client/account => first observed loss-threshold hit; reset on recovery


def _n(value,default=0.0):
    try:
        value=float(value)
        return value if math.isfinite(value) else default
    except (ValueError,TypeError,OverflowError):
        return default


def account_recovery_net(snapshot):
    positions=(snapshot or {}).get("positions") or []
    local=sum(_n(p.get("profit"))+_n(p.get("swap"))+_n(p.get("commission"))
              for p in positions if isinstance(p,dict))
    return _n((snapshot or {}).get("recovery_group_net"),local)


def observe_recovery_arm(snapshot,settings,now=None):
    """Track first threshold hit per account from fresh MT5 telemetry only.

    Rerender/restart conservatively requires a NEW confirmation candle, never
    uses a previously cached candle to authorize a recovery on server reboot.
    Old MT5 EAs need no new protocol for this server-side safety gate.
    """
    if not isinstance(snapshot,dict):return 0
    identity=str(snapshot.get("client_id") or "").strip()
    if not identity:return int(_n(snapshot.get("recovery_armed_at")))
    account=snapshot.get("account") or {}
    key=(identity,str(account.get("login") or ""),str(snapshot.get("symbol") or ""))
    pm=(settings or {}).get("position_management") or {}
    enabled=pm.get("recovery_enabled") is True
    trigger=_n(pm.get("recovery_loss_trigger"))
    positions=snapshot.get("positions") or []
    # A foreign-market position must never arm BTC recovery.
    if any(str(p.get("symbol") or "")!=str(snapshot.get("symbol") or "")
           for p in positions if isinstance(p,dict)):
        with _ARM_LOCK:_ARMED.pop(key,None)
        snapshot.pop("recovery_armed_at",None)
        return 0
    current=int(now.timestamp() if isinstance(now,datetime) else
                now if now is not None else time.time())
    received=snapshot.get("received_at")
    if isinstance(received,datetime):
        seen=received.timestamp()
        fresh=0<=current-seen<=15
    else:fresh=False
    net=account_recovery_net(snapshot)
    active=any(str(p.get("comment") or "").upper().startswith("ALZOOL-RECOVERY")
               for p in positions if isinstance(p,dict))
    with _ARM_LOCK:
        if not enabled or trigger<=0 or not positions or net>-trigger or active:
            _ARMED.pop(key,None)
            snapshot.pop("recovery_armed_at",None)
            return 0
        if not fresh or current<=0:
            # No false timestamp on stale data; an existing arm stays intact.
            previous=_ARMED.get(key,0)
            if previous:snapshot["recovery_armed_at"]=previous
            return previous
        if key not in _ARMED:
            _ARMED[key]=current
        snapshot["recovery_armed_at"]=_ARMED[key]
        return _ARMED[key]


def recovery_m1_confirmation(snapshot,decision,eligible_sides):
    """Confirm a candle for a losing gold side without requiring model consensus."""
    sides=set(eligible_sides or ())
    arm=int(_n((snapshot or {}).get("recovery_armed_at")))
    candles=(snapshot or {}).get("closed_candles") or []
    decision=decision if isinstance(decision,dict) else {}
    base={"approved":False,"state":"WAIT_ARM","side":None,"candle_score":0.0,
          "confirmed_candle_time":0,"armed_at":arm,"eligible_at":0,
          "reason_ar":"خسارة التعافي وصلت للحد؛ مراقبة أول شمعة M1 كاملة بعد التفعيل"}
    if not arm:
        return {**base,"reason_ar":"تم بلوغ حد التعافي؛ انتظار بدء دورة تأكيد شمعة M1"}
    first_full=((arm+59)//60)*60
    eligible_at=first_full+60
    base["eligible_at"]=eligible_at
    if str((snapshot or {}).get("timeframe") or "").upper() not in ("M1","PERIOD_M1"):
        return {**base,"state":"WAIT_TIMEFRAME",
                "reason_ar":"التعافي ينتظر شموع M1 المغلقة من MT5؛ اربط EA بشارت الذهب M1"}
    if not candles or not isinstance(candles[0],dict):
        return {**base,"state":"WAIT_M1_DATA",
                "reason_ar":"لا توجد شمعة M1 مغلقة حديثة لتأكيد التعافي"}
    bar=candles[0]  # MT5 sends latest closed candle first.
    bar_time=int(_n(bar.get("time")))
    base["confirmed_candle_time"]=bar_time
    observed=(snapshot or {}).get("received_at")
    now=int(observed.timestamp()) if isinstance(observed,datetime) else int(time.time())
    if now<eligible_at or bar_time<first_full or now<bar_time+60:
        return {**base,"state":"WAIT_FULL_M1",
                "reason_ar":"التعافي مسلح؛ انتظار إغلاق أول شمعة M1 كاملة بعد وصول الخسارة للحد"}
    if int(_n(decision.get("bar_time")))<bar_time:
        return {**base,"state":"WAIT_NEW_ANALYSIS",
                "reason_ar":"شمعة التأكيد أغلقت؛ انتظار إعادة تحليل الاستراتيجيات وHybrid AI لهذه الشمعة"}
    opening=_n(bar.get("open"))
    closing=_n(bar.get("close"))
    high=_n(bar.get("high"))
    low=_n(bar.get("low"))
    span=high-low
    if span<=0 or not low<=min(opening,closing)<=max(opening,closing)<=high:
        return {**base,"state":"WAIT_BAD_CANDLE",
                "reason_ar":"بيانات شمعة M1 غير صالحة لتأكيد دخول التعافي"}
    if closing==opening:
        return {**base,"state":"WAIT_DOJI",
                "reason_ar":"شمعة M1 محايدة (Doji)؛ لا ندخل التعافي بمجرد وصول الخسارة"}
    candle_side="BUY" if closing>opening else "SELL"
    base["candle_side"]=candle_side
    if candle_side not in sides:
        return {**base,"state":"WAIT_DIRECTION",
                "reason_ar":f"آخر شمعة M1 تؤيد {candle_side} لكن لا توجد مجموعة ذهب خاسرة بهذا الاتجاه؛ انتظار شمعة انعكاس مناسبة"}
    body_ratio=abs(closing-opening)/span
    close_strength=(closing-low)/span if candle_side=="BUY" else (high-closing)/span
    fused_side=str(decision.get("decision","WAIT")).upper()
    context=decision.get("context") if isinstance(decision.get("context"),dict) else {}
    trap=bool(context.get("buy_trap" if candle_side=="BUY" else "sell_trap"))
    # Candle is primary. Strategy/AI can favor or caution; no model veto.
    model_bias=.07 if fused_side==candle_side else -.07 if fused_side in ("BUY","SELL") else 0.0
    score=.55*body_ratio+.45*close_strength+model_bias-(.08 if trap else 0)
    base.update({"candle_score":round(score,4),"candle_body_ratio":round(body_ratio,4),
                 "candle_close_strength":round(close_strength,4),
                 "hybrid_vote_direction":fused_side,
                 "false_reversal_penalty":.08 if trap else 0.0})
    # One moderately convincing candle; do not demand two/three confirmations,
    # ATR excursion, Qwen agreement, or a fixed model confidence percentage.
    if body_ratio<.16 or score<.32:
        return {**base,"state":"WAIT_WEAK_CANDLE",
                "reason_ar":f"اتجاه M1 {candle_side} لكن جسم الشمعة/قوة الإغلاق ضعيفة (تقييم {score:.2f})؛ انتظار إشارة أوضح"}
    return {**base,"approved":True,"state":"CONFIRMED","side":candle_side,
            "reason_ar":(f"شمعة M1 مغلقة جديدة تؤكد {candle_side} بجسم {body_ratio:.0%} "
                         f"وقوة إغلاق {close_strength:.0%}؛ تقييم {score:.2f}. "
                         "الاستراتيجيات وHybrid AI أُخذا في الترجيح دون اشتراط إجماع")}
