"""Offline AI architecture check. No broker calls and no trading."""
import importlib
import json
import numpy as np
import pandas as pd

MODULES = [
    "ai_engine", "ai_outcomes", "ai_dataset_job", "ai_regime",
    "ai_calibration", "ai_uncertainty", "ai_execution_cost",
    "ai_drift_monitor", "ai_risk_overlay", "ai_meta_label",
    "ai_pipeline", "ai_validation_suite"
]

def main():
    results = {}
    for name in MODULES:
        try:
            importlib.import_module(name)
            results[name] = "OK"
        except Exception as exc:
            results[name] = "FAIL:%s:%s" % (type(exc).__name__, exc)
    functional = {}
    try:
        from ai_conformal import evaluate as conformal_evaluate
        from ai_multihorizon import evaluate as multihorizon_evaluate
        n = 320
        rng = np.random.default_rng(123)
        returns = rng.normal(0.0001, 0.002, n)
        close = 100.0 * np.cumprod(1.0 + returns)
        frame = pd.DataFrame({
            "open": close * (1.0 + rng.normal(0, 0.0005, n)),
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
        })
        mh = multihorizon_evaluate(frame)
        cf = conformal_evaluate(frame, atr_pct=0.002)
        functional["multihorizon_available"] = bool(mh.get("available"))
        functional["conformal_available"] = bool(cf.get("interval", {}).get("available"))
        if not functional["multihorizon_available"] or not functional["conformal_available"]:
            raise RuntimeError("new AI layer functional check did not produce valid diagnostics")
    except Exception as exc:
        functional["error"] = "%s:%s" % (type(exc).__name__, exc)

    payload = {
        "modules": results,
        "functional": functional,
        "all_imports_ok": all(value == "OK" for value in results.values()),
        "functional_checks_ok": not functional.get("error"),
    }
    print(json.dumps(payload, indent=2))
    if not payload["all_imports_ok"] or not payload["functional_checks_ok"]:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
