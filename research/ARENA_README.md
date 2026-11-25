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

## Challenger comparison (research only)
The same immutable archive, training/validation/test windows, threshold selection and ATR spread proxy are used across models in one round. The additional stress result deducts 0.10 ATR per selected signal, not a simulated broker fill. The 24-window test remains a small pilot.

On the existing Windows research environment install `chronos-forecasting>=2,<3` and try `tirex-2` separately. TiRex-2's official pip install has been tested on Linux/macOS only; on Windows prefer WSL2 or the official Docker setup if native install fails. Never install these packages on Render. Run a single local comparison with `python research/model_arena_worker.py --once --models LightGBM XGBoost CatBoost "TiRex-2" "Chronos-Bolt Tiny"`. Optional older models may be added to the same run. New models are accepted for historical reports only, not live advice. Existing workers remain compatible.

Cloud comparison is optional, not enabled automatically: `python research/model_arena_worker.py --once --cloud --models "Qwen 3.8 27B" "GPT-OSS 120B (Arena)"`. Requires a local Groq API key and explicit acceptance of provider costs. Maximum 48 requests per model per market (192 across both); only normalized historical price contexts are sent, no owner account data. No automatic retries, no quota bypass. Results are historical proxies, not live trading performance. Never paste the key or password into chat.
