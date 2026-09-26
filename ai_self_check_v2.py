"""Offline AI architecture check. No broker calls and no trading."""
import importlib
import json

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
    payload = {
        "modules": results,
        "all_imports_ok": all(value == "OK" for value in results.values()),
    }
    print(json.dumps(payload, indent=2))
    if not payload["all_imports_ok"]:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
