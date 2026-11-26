# AL-ZOOL MASTER NEXT — isolated research V1

No deployment, no broker credentials, no MT5 requests, no change to production files. Separate virtual environment, run manually when live trading is stopped or on a separate machine. PPO/DQN use Stable-Baselines3 (the algorithms used in FinRL's agent layer) with a bespoke discrete Gymnasium environment; **not** an imported FinRL StockTradingEnv or installed FinRL package. PPO and DQN compete on a chronological validation partition; only the selected policy is evaluated alongside static baselines on the final holdout. No model is authorized for live execution. Do not replace yesterday's release on the strength of this one test.

## Windows
From the repository root, on the separate research branch/worktree:
```powershell
py -3.11 -m venv .master-next-venv
.\.master-next-venv\Scripts\python.exe -m pip install -r research\master_next\requirements.txt
.\.master-next-venv\Scripts\python.exe research\master_next\test_core.py
.\.master-next-venv\Scripts\python.exe research\master_next\run.py --bars tests\fixtures\v3_replacement_gold_m1.json --out master_next_gold_run1
.\.master-next-venv\Scripts\python.exe research\master_next\run.py --bars tests\fixtures\v3_replacement_btc_m1.json --out master_next_btc_run1
```
New output directory required on each run. Reports and model weights are local. The historical fixtures have gaps; new exposure is blocked during warmup after gaps and open exposure is closed at next available open, including gap risk.

Optional TimesFM 2.5 (same checkpoint as existing project, not a new live service): install `timesfm[torch]==2.0.2` separately and pass `--timesfm`. This computes offline historical forecasts and may be slow. Optional FinBERT: install transformers, pass `--news path.csv`. Required CSV columns: symbol,published_unix,available_unix,headline. English headlines; Unix seconds; actual point-in-time ingestion timestamp; no data supplied means explicit unavailable mask, not fabricated neutral predictions. Model downloads require connectivity. Never infer gold direction directly from sentiment.

Optional one-call GPT-4o/Gemini review (separate process, opt-in fees): set OPENAI_API_KEY or GEMINI_API_KEY locally, never in git/chat. Run `python research/master_next/review.py --report master_next_gold_run1/report.json --provider openai --allow-paid-api` or use provider gemini with an available `--model MODEL_ID`. Only whitelisted aggregate numeric backtest metrics are sent, no account or order information. No automatic cloud calls, no auto retraining/deploy.

## Before any demo integration
Validate on several untouched chronological periods and seeds; actual broker spread, commissions, swaps, latency, margin, and tick-level execution; benchmark exact deployed policy on same data; model checkpoint/version reproducibility and news timestamps; paper-trade execution and drawdown. The prototype simulates a single net basket up to two units and uses risk reduction as recovery. It does not reproduce production hedging, multi-position recovery, smart-profit runner, or account-based lot sizing. Those require a dedicated replay environment with the real deal/tick schema. Current learning and live server remain untouched.
