"""Independent per-side AL-ZOOL add-on/cooling sizing.

Counts only open positions tagged with the corresponding AL-ZOOL action.
Does not count original/manual positions or the other action type.
Uses owner-configured geometric multipliers in AI mode, fixed lot in manual.
Never rounds an order above its calculated lot to satisfy broker min/step.
"""
import math


def numeric(value, default=0.0):
    try:
        result=float(value)
        return result if math.isfinite(result) else default
    except (TypeError,ValueError,OverflowError):
        return default


TAGS={"ADDON_PROPOSAL":"ALZOOL-ADD", "COOLING_PROPOSAL":"ALZOOL-COOL"}


def count_action(group, action):
    prefix=TAGS[action]
    return sum(1 for p in group if str(p.get("comment") or "").upper().startswith(prefix))


def next_action_lot(group, action, pm, tick):
    """Return diagnostics and broker-step compliant intended amount, 0 if not representable."""
    prefix="addon" if action=="ADDON_PROPOSAL" else "cooling"
    count=count_action(group,action)
    base=numeric(pm.get(prefix+"_lot"),0.0)
    multiplier=numeric(pm.get(prefix+"_multiplier"),1.0)
    mode=str(pm.get("mode","AI")).upper()
    # First addition is base × multiplier**0; independent sequence per side/type.
    if base<=0 or multiplier<1 or not math.isfinite(multiplier):
        return {"lot":0.0,"raw_lot":0.0,"count":count,"index":count+1,
                "base_lot":base,"multiplier":multiplier,
                "reason_ar":"اللوت الأساسي أو المضاعف غير صالح"}
    try:
        raw=base*pow(multiplier,count) if mode=="AI" else base
    except OverflowError:
        raw=float("inf")
    if not math.isfinite(raw) or raw<=0:
        return {"lot":0.0,"raw_lot":0.0,"count":count,"index":count+1,
                "base_lot":base,"multiplier":multiplier,
                "reason_ar":"الحجم المتدرج غير صالح"}
    step=numeric(tick.get("volume_step"))
    minimum=numeric(tick.get("volume_min"))
    maximum=numeric(tick.get("volume_max"))
    # Updated EA sends volume constraints. Older versions normalize at MT5.
    # If unknown, retain precision and let the terminal validate without up-rounding.
    amount=raw
    if step>0:
        amount=math.floor(raw/step+1e-8)*step
    if maximum>0: amount=min(amount,math.floor(maximum/step+1e-8)*step if step>0 else maximum)
    if minimum>0 and amount+1e-9<minimum: amount=0.0
    amount=round(amount,8)
    return {"lot":amount,"raw_lot":round(raw,8),"count":count,"index":count+1,
            "base_lot":base,"multiplier":multiplier,"volume_step":step,
            "reason_ar":("الحجم المتدرج أصغر من أقل لوت يسمح به الوسيط"
                         if amount<=0 else "حساب المضاعف حسب تسلسل الإضافات الفعلي")}
