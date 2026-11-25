"""Bounded, owner/market scoped research candidates. Never grants execution."""
import math
from market_symbols import same_market_symbol

VERSION = "ADAPTIVE_MEMORY_V1"
MODELS = {"Fast AI": "FAST_AI", "qwen2.5:1.5b": "LOCAL_AI",
          "deepseek-r1:1.5b": "LOCAL_AI", "GPT-OSS 120B": "CLOUD_AI"}
LABELS = {"Fast AI": "Fast AI", "Qwen": "qwen2.5:1.5b",
          "DeepSeek": "deepseek-r1:1.5b", "GPT-OSS 120B": "GPT-OSS 120B"}

def number(value):
    try:
        if isinstance(value, bool):
            return None
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError, OverflowError):
        return None

def performance_factors(rows, symbol):
    """Use only settled unique M1 proxy outcomes; chronological holdout."""
    groups = {}
    for row in rows or []:
        p = row.get("payload") if isinstance(row, dict) else None
        if not isinstance(p, dict) or not same_market_symbol(p.get("symbol"), symbol):
            continue
        model = p.get("model")
        if (model not in MODELS or p.get("kind") != MODELS[model] or
            p.get("version") != "AZ_ACCURACY_V4" or p.get("analysis_mode") != "UNIFIED" or
            p.get("metric") != "NEXT_CLOSE_QUOTE_PROXY_NOT_BROKER_FILL" or
            p.get("direction") not in ("BUY", "SELL")):
            continue
        bar, at, end, net = (number(p.get(k)) for k in
            ("bar_time", "forecast_time_unix", "next_bar_time", "model_net_atr"))
        if None in (bar, at, end, net) or bar <= 0 or end != bar+60 or not bar+60 <= at < bar+120:
            continue
        groups.setdefault(model, {}).setdefault(bar, max(-3.0, min(3.0, net)))
    output = {}
    for model in MODELS:
        samples = [v for _, v in sorted(groups.get(model, {}).items())][-300:]
        cut = int(len(samples)*.7)
        train, test = samples[:cut], samples[cut:]
        record = {"samples": len(samples), "training": len(train), "validation": len(test),
                  "factor": 1.0, "status": "INSUFFICIENT_EVIDENCE"}
        if len(train) >= 80 and len(test) >= 40:
            a, b = sum(train)/len(train), sum(test)/len(test)
            record.update(train_net_atr=round(a, 5), validation_net_atr=round(b, 5),
                          status="NEUTRAL")
            # A stable sign in both periods is evidence for a bounded candidate,
            # not proof that reweighting the full ensemble improves its P&L.
            if a > .02 and b > .02:
                record.update(factor=1.2, status="POSITIVE_BOTH_PERIODS")
            elif a < -.02 and b < -.02:
                record.update(factor=.8, status="NEGATIVE_BOTH_PERIODS")
        output[model] = record
    return output

def candidate_weights(base, performance, votes):
    """Do not transfer model identity or weights to a replacement local model."""
    result = dict(base)
    for label, model in LABELS.items():
        if label not in result:
            continue
        if label in ("Qwen", "DeepSeek") and (votes.get(label) or {}).get("model_id") != model:
            continue
        factor = number((performance.get(model) or {}).get("factor"))
        if factor is not None and .8 <= factor <= 1.2:
            result[label] *= factor
    # Keep overall nominal contribution budget unchanged.
    total = sum(result.values())
    return {k: v*sum(base.values())/total for k, v in result.items()} if total > 0 else dict(base)

def action_side_memory(deals, symbol, action_feedback):
    """Use existing exact full-position P&L checks; unknown side stays unknown."""
    positions = {}
    for d in deals or []:
        if not isinstance(d, dict) or not same_market_symbol(d.get("symbol"), symbol):
            continue
        pid = str(d.get("position_id") or "")
        if pid:
            positions.setdefault(pid, []).append(d)
    groups = {"BUY": [], "SELL": [], "UNKNOWN": []}
    for ds in positions.values():
        entries = [d for d in ds if number(d.get("entry")) == 0]
        sides = {number(d.get("type", d.get("deal_type"))) for d in entries}
        side = "BUY" if sides == {0.0} else "SELL" if sides == {1.0} else "UNKNOWN"
        groups[side].extend(ds)
    result = []
    for side, ds in groups.items():
        for row in action_feedback(ds, symbol):
            result.append({**row, "side": side, "execution_authorized": False})
    return result
