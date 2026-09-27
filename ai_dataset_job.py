"""Build and govern the realized-outcome AI learning loop.

This job runs after each bot cycle. It reconciles broker-realized outcomes,
builds the dataset, validates each epic independently, and records a
champion/challenger registry per epic. It never blocks or delays trade entry.
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import ai_outcomes
import ai_calibration
import ai_drift_monitor
import ai_engine


def _profile_for_epic(epic):
    epic_u = str(epic or "").strip().upper()
    if epic_u in {"GOLD", "SILVER", "OIL_CRUDE", "US100", "US500"}:
        return "CAPITAL_METALS_ENERGY_AI"
    return "CAPITAL_FX_AI"


REGISTRY_PREFIX = os.environ.get("AI_MODEL_REGISTRY_PREFIX", "")
LEARNING_REPORT_PATH = os.environ.get("AI_LEARNING_REPORT", "ai_learning_report.json")


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip().upper()) or "UNKNOWN"


def _realized_validation(frame, profile):
    out = {
        "walk_forward_folds": 0,
        "walk_forward_ok": False,
        "calibration_ok": False,
        "drift_ok": False,
        "execution_cost_ok": False,
        "validation_mode": "advisory_only",
        "realized_labels_ok": bool(len(frame) >= 20),
    }
    if frame is None or frame.empty:
        return out

    required = set(ai_engine.REALIZED_FEATURES + ["outcome_label"])
    if not required.issubset(frame.columns):
        out["validation_error"] = "missing_realized_features"
        return out

    frame = frame.dropna(subset=list(required)).copy()
    if len(frame) < ai_engine.REALIZED_MIN_SAMPLES:
        out["reason"] = f"insufficient_realized_samples:{len(frame)}"
        return out

    try:
        wf = ai_engine._realized_walk_forward(frame, profile)
        out["walk_forward_folds"] = int(wf.get("folds", 0) or 0)
        out["walk_forward_ok"] = bool(wf.get("ok", False))
        out["walk_forward_accuracy"] = wf.get("accuracy")
        out["walk_forward_balanced_accuracy"] = wf.get("balanced_accuracy")
        out["walk_forward_precision"] = wf.get("directional_precision")
        out["walk_forward_directional_rate"] = wf.get("directional_rate")
        out["walk_forward_samples"] = int(wf.get("samples", 0) or 0)
        if wf.get("reason"):
            out["walk_forward_reason"] = wf["reason"]
    except Exception as exc:
        out["walk_forward_error"] = f"{type(exc).__name__}:{exc}"

    p = pd.to_numeric(frame["ai_confidence"], errors="coerce")
    y = (pd.to_numeric(frame["outcome_label"], errors="coerce") > 0).astype(float)
    mask = p.notna() & y.notna()
    if int(mask.sum()) >= 20:
        cal = ai_calibration.summarize(p[mask].to_numpy(), y[mask].to_numpy())
        out["calibration_samples"] = int(cal["samples"])
        out["brier_score"] = cal["brier_score"]
        out["ece"] = cal["ece"]
        out["calibration_ok"] = bool(cal["ece"] is not None and cal["ece"] <= 0.15)

    feature_cols = [c for c in ai_engine.FEATURES if c in frame.columns]
    if len(frame) >= 40 and feature_cols:
        split = max(20, len(frame) // 2)
        ref = {
            c: pd.to_numeric(frame.iloc[:split][c], errors="coerce").dropna().to_numpy()
            for c in feature_cols
        }
        cur = {
            c: pd.to_numeric(frame.iloc[split:][c], errors="coerce").dropna().to_numpy()
            for c in feature_cols
        }
        drift = ai_drift_monitor.assess(ref, cur)
        out["max_feature_psi"] = drift.get("max_feature_psi", 0.0)
        out["prediction_shift"] = drift.get("prediction_shift")
        out["drift_detected"] = bool(drift.get("drift", False))
        out["drift_ok"] = not out["drift_detected"]

    spread = pd.to_numeric(frame["spread_pct"], errors="coerce")
    slip = pd.to_numeric(frame["slippage_pct"], errors="coerce")
    valid = spread.notna() & slip.notna() & np.isfinite(spread) & np.isfinite(slip)
    out["cost_samples"] = int(valid.sum())
    if out["cost_samples"] >= 20:
        out["median_spread_pct"] = float(spread[valid].median())
        out["median_slippage_pct"] = float(slip[valid].median())
        out["execution_cost_ok"] = True

    return out


def main():
    dataset_report = ai_outcomes.build_training_dataset()

    with sqlite3.connect(ai_outcomes.DB_PATH) as con:
        df = pd.read_sql_query(
            "SELECT * FROM trade_outcomes "
            "WHERE status='CLOSED' AND outcome_label IS NOT NULL "
            "ORDER BY entry_time",
            con,
        )

    realized_all = ai_outcomes.realized_training_frame()
    if realized_all is None:
        realized_all = pd.DataFrame()

    registry_reports = {}
    epics = sorted(
        set(df.get("epic", pd.Series(dtype=str)).dropna().astype(str).str.upper())
    )

    for epic in epics:
        profile_name = _profile_for_epic(epic)
        profile = ai_engine.PROFILES[profile_name]
        frame = realized_all.copy()
        if not frame.empty and "epic" in frame.columns:
            frame = frame[
                frame["epic"].astype(str).str.strip().str.upper() == epic
            ].copy()

        pnl = pd.to_numeric(
            frame.get("pnl", pd.Series(dtype=float)), errors="coerce"
        ).dropna()
        wins = int((pnl > 0).sum())
        eq = pnl.cumsum()
        peak = eq.cummax() if len(eq) else pd.Series(dtype=float)
        dd = eq - peak if len(eq) else pd.Series(dtype=float)

        validation = _realized_validation(frame, profile)
        metrics = {
            "model_version": f"realized-outcome-v2-{_safe_name(epic)}",
            "epic": epic,
            "strategy_id": profile_name,
            "training_period_start": str(frame["entry_time"].iloc[0]) if len(frame) else None,
            "training_period_end": str(frame["entry_time"].iloc[-1]) if len(frame) else None,
            "number_of_samples": int(len(frame)),
            "buy_sell_wait_distribution": {
                "BUY": int((frame.get("direction", pd.Series(dtype=str)) == "BUY").sum()),
                "SELL": int((frame.get("direction", pd.Series(dtype=str)) == "SELL").sum()),
                "WAIT": 0,
            },
            "realized_win_rate": round(wins / len(pnl), 4) if len(pnl) else None,
            "expectancy": round(float(pnl.mean()), 6) if len(pnl) else None,
            "max_drawdown": round(float(abs(dd.min())), 6) if len(dd) else 0.0,
            "last_training_at": datetime.now(timezone.utc).isoformat(),
            "label_source": "broker-reported-realized-pnl",
            "dataset_path": dataset_report.get("path"),
            **validation,
            "registry_role": "challenger",
            "trade_entry_gate": "NOT_USED",
        }

        registry_path = f"{REGISTRY_PREFIX}ai_model_registry_{_safe_name(epic)}.json"
        registry_reports[epic] = ai_outcomes.update_registry(
            metrics["model_version"],
            metrics,
            registry_path=registry_path,
        )

    summary = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset_report,
        "total_closed_realized_samples": int(len(df)),
        "epics": {
            epic: {
                "active_model": report.get("active_model"),
                "models": list(report.get("models", {}).keys()),
                "last_decision": (
                    report.get("models", {})
                    .get(report.get("active_model"), {})
                    .get("promotion_decision")
                    if report.get("active_model")
                    else None
                ),
            }
            for epic, report in registry_reports.items()
        },
        "learning_policy": (
            "Per-epic realized outcomes only; broker P/L labels; "
            "walk-forward/calibration/drift/cost governance; "
            "AI remains support-only for entry."
        ),
    }
    with open(LEARNING_REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
