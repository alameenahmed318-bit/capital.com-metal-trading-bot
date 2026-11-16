"""Account-wide one fully closed M1 candle after a filled position exit.

The wait applies only to NEW central entries; add-on, cooling, recovery and
closing remain independent. The strategy/Hybrid decision must be computed from
a candle opened after the exit, not a stale pre-exit strategy vote.
"""
import math
from datetime import datetime,timezone


def _timestamp(value):
    try:
        n=float(value)
        return int(n) if math.isfinite(n) and n>0 else 0
    except (ValueError,TypeError,OverflowError):
        return 0


def last_exit_from_deals(deals, now=None):
    """Best-effort fallback for old EAs; only actual OUT/INOUT closing deals."""
    current=_timestamp(now)
    latest=0
    for row in deals if isinstance(deals,list) else []:
        if not isinstance(row,dict):continue
        try:
            entry=int(row.get("entry",-1))
        except (TypeError,ValueError,OverflowError):
            continue
        if entry not in (1,2,3):continue
        stamp=_timestamp(row.get("time"))
        if current and stamp>current+2:continue
        latest=max(latest,stamp)
    return latest


def post_close_m1_confirmation(last_close, m1_closed_bar, decision_bar, now):
    """Require the first COMPLETE M1 candle opened at/after the exit time.

    If an exit fills at 12:00:25, the 12:00 candle is partial;
    12:01–12:02 is the first full candle. Entry is eligible at 12:02
    using a newly calculated strategy decision from the confirmed bar.
    """
    close=_timestamp(last_close)
    now_ts=_timestamp(now.timestamp() if isinstance(now,datetime) else now)
    latest_bar=_timestamp(m1_closed_bar)
    strategy_bar=_timestamp(decision_bar)
    result={"approved":True,"state":"READY","reason_ar":"لا يوجد إغلاق حديث يتطلب انتظار شمعة M1",
            "last_close_time":close,"confirmation_candle_open":0,
            "eligible_at":0,"m1_closed_candle_open":latest_bar,
            "decision_bar_time":strategy_bar}
    if close<=0:return result
    # Clock inconsistency must not authorize a trade from stale snapshots.
    if not now_ts or close>now_ts+2:
        result.update({"approved":False,"state":"WAIT_CLOCK",
                       "reason_ar":"وقت إغلاق الصفقة أحدث من توقيت المحرك؛ انتظار بيانات متزامنة"})
        return result
    first_full_bar=((close+59)//60)*60
    ready_at=first_full_bar+60
    result.update({"confirmation_candle_open":first_full_bar,"eligible_at":ready_at})
    if now_ts<ready_at or latest_bar<first_full_bar:
        result.update({"approved":False,"state":"WAIT_M1",
                       "reason_ar":f"بعد إغلاق الصفقة: انتظار شمعة M1 كاملة جديدة حتى {datetime.fromtimestamp(ready_at,timezone.utc).strftime('%H:%M:%S')} UTC"})
        return result
    if strategy_bar<first_full_bar:
        result.update({"approved":False,"state":"WAIT_REANALYSIS",
                       "reason_ar":"شمعة M1 المؤكدة أغلقت؛ بانتظار تحديث اتجاه الاستراتيجيات وHybrid AI على شمعة جديدة"})
        return result
    result.update({"approved":True,"state":"CONFIRMED",
                   "reason_ar":"أُغلقت شمعة M1 كاملة بعد آخر صفقة؛ تم تحديث قرار الاستراتيجيات وHybrid AI"})
    return result
