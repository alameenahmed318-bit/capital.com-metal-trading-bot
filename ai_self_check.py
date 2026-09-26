"""Offline AI architecture self-check. Never calls broker APIs and never trades."""
import importlib
import json

MODULES=[
 "ai_engine","ai_outcomes","ai_dataset_job","ai_regime","ai_calibration",
 "ai_uncertainty","ai_execution_cost","ai_drift_monitor","ai_risk_overlay",
 "ai_meta_label","ai_pipeline","ai_validation_suite"
]def main():
    results={}
    for name in MODULES:
        try:
            importlib.import_module(name)
            results[name]="OK"
        except Exception as exc:
            results[name]=f"FAIL:{type(exc).__name__}:{exc}"
    required=[
      "ai_regime.py","ai_calibration.py","ai_uncertainty.py",
      "ai_execution_cost.py","ai_drift_monitor.py","ai_risk_overlay.py",
      "ai_meta_label.py","ai_pipeline.py","ai_model_registry.json"
    ]
    print(json.dumps({"modules":results,"required_files":required,
                      "all_imports_ok":all(v=="OK" for v in results.values())},indent=2))
    if not all(v=="OK" for v in results.values()):
        raise SystemExit(1)
if __name__=="__main__": main()
