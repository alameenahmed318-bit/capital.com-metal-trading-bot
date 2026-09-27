"""Professional AI performance monitor.
Reads the existing realized-trade ledger only; adds no broker/API calls and
never changes trade signals.
"""
from __future__ import annotations
import json, os, sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get("AI_OUTCOMES_DB", "ai_outcomes.db")
REPORT_PATH = os.environ.get("AI_PROFESSIONAL_REPORT", "ai_professional_report.json")

def _now():
    return datetime.now(timezone.utc).isoformat()

def _num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default

def build_report():
    report = {"updated_at": _now(), "available": False, "samples": 0,
              "regimes": {}, "anomalies": []}
    if not os.path.exists(DB_PATH):
        report["reason"] = "outcome_ledger_not_found"
        return report
    try:
        con = sqlite3.connect(DB_PATH, timeout=5)
        rows = con.execute(
            "SELECT epic,direction,pnl,outcome_label,ai_confidence,regime "
            "FROM trade_outcomes WHERE status='CLOSED' AND pnl IS NOT NULL "
            "ORDER BY entry_time DESC LIMIT 5000"
        ).fetchall()
        con.close()
    except Exception as exc:
        report["reason"] = "ledger_read_error"
        report["error"] = str(exc)
        return report
    report["available"] = True
    report["samples"] = len(rows)
    if not rows:
        report["reason"] = "no_realized_samples"
        return report

    pnls = [_num(r[2]) for r in rows]
    wins = sum(p > 0 for p in pnls)
    losses = sum(p < 0 for p in pnls)
    report["overall"] = {
        "win_rate": wins / len(pnls),
        "profit": sum(pnls),
        "avg_pnl": sum(pnls) / len(pnls),
        "wins": wins,
        "losses": losses,
    }

    grouped = {}
    for epic, direction, pnl, label, confidence, regime in rows:
        key = str(regime or "UNKNOWN").upper()
        grouped.setdefault(key, []).append(_num(pnl))
    for key, vals in grouped.items():
        report["regimes"][key] = {
            "samples": len(vals),
            "win_rate": sum(p > 0 for p in vals) / len(vals),
            "avg_pnl": sum(vals) / len(vals),
            "profit": sum(vals),
        }

    recent = pnls[:30]
    if len(recent) >= 20:
        if sum(p > 0 for p in recent) / len(recent) < 0.35:
            report["anomalies"].append("RECENT_WIN_RATE_DETERIORATION")
        if sum(recent) / len(recent) < 0:
            report["anomalies"].append("RECENT_NEGATIVE_EXPECTANCY")

    confidences = [_num(r[4], -1) for r in rows if r[4] is not None]
    if confidences and (min(confidences) < 0 or max(confidences) > 1):
        report["anomalies"].append("INVALID_CONFIDENCE_RANGE")

    report["status"] = "CAUTION" if report["anomalies"] else "NORMAL"
    return report

def run():
    report = build_report()
    try:
        with open(REPORT_PATH, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return report
