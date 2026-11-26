# MASTER NEXT V2 — private archive and multi-seed development
Separate branch only. No live trade calls. V2 compares three seeds per algorithm, adds position age, unrealized P&L and peak giveback to the observation, and tests an offline risk envelope of 15 bars / 2.5 ATR. This is not a change to any live SL, profit runner or lot settings. DQN/PPO use Stable-Baselines3; no FinRL package required. No production approval even if a research gate passes.

GitHub development runs reuse old public fixtures; these are no longer independent tests. Fresh evaluation must use owner archive data locally, outside this public repository. Existing authorized read-only arena history API is used; no password or token persisted. Export includes only candles, no identifiers. Archive pagination can limit history; exporter prints actual rows. Train on pre-V1-cutoff data and reserve later data strictly for final test; do not tune against that test. If repeated later, reserve an additional unseen window.

Windows commands from the V2 research worktree:
```powershell
py -3.11 -m venv .master-next-venv
.\.master-next-venv\Scripts\python.exe -m pip install -r research\master_next\requirements.txt
.\.master-next-venv\Scripts\python.exe research\master_next\export_archive.py --out "$HOME\ALZOOL-private-archive-v2"
.\.master-next-venv\Scripts\python.exe research\master_next\run_v2.py --bars "$HOME\ALZOOL-private-archive-v2\XAUUSD.json" --out "$HOME\ALZOOL-v2-gold-report"
.\.master-next-venv\Scripts\python.exe research\master_next\run_v2.py --bars "$HOME\ALZOOL-private-archive-v2\BTCUSD.json" --out "$HOME\ALZOOL-v2-btc-report"
```
Use separate CPU or stop live worker only at an owner-approved safe time. No API keys needed for this experiment. TimesFM, FinBERT and cloud review remain optional V1 adapters, not tested by V2. No synthetic headlines. Actual tick replay and comparison with production policy still required.
