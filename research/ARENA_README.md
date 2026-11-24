# AL-ZOOL advanced model arena

Research only, no order permissions and no changes to live trading.
Owner opens /model-arena.html after normal login. The separate worker runs on a research computer, not the Render web service. It authenticates as the owner; do not enter secrets in chat. It reads closed M1 archive windows and publishes research-only scores. It uses identical 24 sampled nonoverlapping test windows and validation windows per market per archive snapshot, so this first pass is only a pilot, not proof of profitability.

From the repo on Windows (Python 3.11+ recommended):
```powershell
py -3.11 -m venv .arena-venv
.\.arena-venv\Scripts\python.exe -m pip install lightgbm==4.6.0 xgboost==3.0.2 catboost==1.2.8 "chronos-forecasting>=2,<3"
.\.arena-venv\Scripts\python.exe research\model_arena_worker.py
```
Enter owner email/password locally when prompted. Keep the worker process running; it repeats after each completed round plus one hour. Use --once for one round; --models LightGBM XGBoost CatBoost to test trees only. If a library/checkpoint fails, the UI shows the failure rather than fabricated predictions. The heavier Chronos/TimesFM tests can take significant time on CPU. Pin resolved optional dependency versions before any production use.

TimesFM-2.5 uses Apache-2.0 licensed weights. Install with `python -m pip install "timesfm[torch]==2.0.2"`. The model checkpoint is https://huggingface.co/google/timesfm-2.5-200m-pytorch. Research only until independently validated.

The original specialist, V1/V2/V3, and trade controls are unchanged. Arena results are local research-worker reports, not independent confirmation of broker fills. A model selecting WAIT on all test windows has no accuracy percentage.
