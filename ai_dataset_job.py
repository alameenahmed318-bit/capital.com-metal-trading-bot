"""Build the persisted realized-outcome AI dataset and registry after each run.

Validation is telemetry/governance only. It MUST NOT block BUY/SELL entries.
Hard trading risk controls remain outside this module.
"""
import json, sqlite3
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import ai_outcomes
import ai_calibration
import ai_drift_monitor
import ai_execution_cost
import ai_engine


def _realized_validation(df):
    """Calculate real validation metrics without becoming an entry gate."""
    out = {
        "walk_forward_folds": 0,
        "walk_forward_ok": False,
        "calibration_ok": False,
        "drift_ok": False,
        "execution_cost_ok": False,
        "validation_mode": "advisory_only",
    }
    if df.empty:
        return out

    frame = ai_outcomes.realized_training_frame()
    if frame is not None and not frame.empty:
        try:
            wf = ai_engine._realized_walk_forward(
                frame, ai_engine.PROFILES["CAPITAL_V1"]
            )
            out["walk_forward_folds"] = int(wf.get("folds", 0) or 0)
            out["walk_forward_ok"] = bool(wf.get("ok", False))
            out["walk_forward_accuracy"] = wf.get("accuracy")
            out["walk_forward_balanced_accuracy"] = wf.get("balanced_accuracy")
            out["walk_forward_precision"] = wf.get("directional_precision")
            out["walk_forward_samples"] = int(wf.get("samples", 0) or 0)
        except Exception as exc:
            out["walk_forward_error"] = f"{type(exc).__name__}:{exc}"

        # Calibration: compare the AI's recorded confidence with realized win/loss.
        if {"ai_confidence", "outcome_label"}.issubset(frame.columns):
            p = pd.to_numeric(frame["ai_confidence"], errors="coerce")
            y = (pd.to_numeric(frame["outcome_label"], errors="coerce") > 0).astype(float)
            mask = p.notna() & y.notna()
            if int(mask.sum()) >= 20:
                cal = ai_calibration.summarize(p[mask].to_numpy(), y[mask].to_numpy())
                out["calibration_samples"] = int(cal["samples"])
                out["brier_score"] = cal["brier_score"]
                out["ece"] = cal["ece"]
                # This is a quality report, not an entry filter.
                out["calibration_ok"] = bool(
                    cal["ece"] is not None and cal["ece"] <= 0.15
                )

        # Drift: compare older realized feature distributions with the recent window.
        feature_cols = [
            c for c in ai_engine.FEATURES
            if c in frame.columns
        ]
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
            report = ai_drift_monitor.assess(ref, cur)
            out["max_feature_psi"] = report.get("max_feature_psi", 0.0)
            out["prediction_shift"] = report.get("prediction_shift")
            out["drift_detected"] = bool(report.get("drift", False))
            # Healthy means no material drift; drift is telemetry and never blocks entries.
            out["drift_ok"] = not out["drift_detected"]

        # Execution-cost validation is deliberately data-quality based.
        # We do NOT subtract spread percentages from monetary P/L.
        spread = pd.to_numeric(frame.get("spread_pct"), errors="coerce")
        slip = pd.to_numeric(frame.get("slippage_pct"), errors="coerce")
        valid_cost = spread.notna() & np.isfinite(spread)
        valid_slip = slip.notna() & np.isfinite(slip)
        out["cost_samples"] = int((valid_cost & valid_slip).sum())
        if out["cost_samples"] >= 20:
            recent = pd.concat([spread[valid_cost], slip[valid_slip]], axis=1).dropna()
            out["median_spread_pct"] = float(recent.iloc[:, 0].median()) if not recent.empty else None
            out["median_slippage_pct"] = float(recent.iloc[:, 1].median()) if not recent.empty else None
            out["execution_cost_ok"] = True

    return out


def main():
    report = ai_outcomes.build_training_dataset()
    with sqlite3.connect(ai_outcomes.DB_PATH) as con:
        df = pd.read_sql_query(
            "SELECT * FROM trade_outcomes WHERE status='CLOSED' AND outcome_label IS NOT NULL ORDER BY entry_time",
            con,
        )

    pnl = pd.to_numeric(df.get("pnl", pd.Series(dtype=float)), errors="coerce").dropna()
    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    eq = pnl.cumsum()
    peak = eq.cummax() if len(eq) else pd.Series(dtype=float)
    dd = (eq - peak) if len(eq) else pd.Series(dtype=float)
    validation = _realized_validation(df)

    metrics = {
        "model_version": "realized-outcome-v1",
        "training_period_start": str(df["entry_time"].iloc[0]) if len(df) else None,
        "training_period_end": str(df["entry_time"].iloc[-1]) if len(df) else None,
        "number_of_samples": int(len(df)),
        "buy_sell_wait_distribution": {
            "BUY": int((df.get("direction", pd.Series(dtype=str)) == "BUY").sum()),
            "SELL": int((df.get("direction", pd.Series(dtype=str)) == "SELL").sum()),
            "WAIT": 0,
        },
        "realized_win_rate": round(wins / len(pnl), 4) if len(pnl) else None,
        "expectancy": round(float(pnl.mean()), 6) if len(pnl) else None,
        "max_drawdown": round(float(abs(dd.min())), 6) if len(dd) else 0.0,
        "last_training_at": datetime.now(timezone.utc).isoformat(),
        "label_source": "broker-reported-realized-pnl",
        "dataset_path": report.get("path"),
        **validation,
        "realized_labels_ok": bool(len(df) >= 20),
        "registry_role": "challenger",
        # Governance only. Never an entry gate.
        "trade_entry_gate": "NOT_USED",
    }

    ai_outcomes.update_registry("realized-outcome-v1", metrics)
    print(json.dumps({"dataset": report, "registry": metrics}, indent=2, default=str))


if __name__ == "__main__":
    main()
