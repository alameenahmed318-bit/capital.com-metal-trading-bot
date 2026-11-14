# AL-ZOOL Foundation Baseline — 2026-11-14

## Safety and source control
- Production branch: `alzool-gold-sniper-web`
- Production commit inspected: `9df5ebc72c4bae18b59918cbf23605447b5900a1`
- Production parent: `15da74ef2e626a8f01f2f9bf8fc62a2996f45324`
- Isolated work branch: `work/alzool-foundation-20261114`
- This baseline is documentation only. No production files, Render settings, Supabase records, customer accounts, licences, or MT5 trading settings were changed.
- Do not merge or deploy this work branch to production until the test gates below pass and the user approves.

## Verified existing foundation
1. Backend service: Python service in `marketplace/backend/app.py`; Render starts it through `render.yaml`. It provides licensing/connection endpoints, central strategy decisions, trade settings, learning/backtest endpoints and recovery event ingestion.
2. Strategy engine: `marketplace/backend/strategy_engine.py` defines 20 families and 10 confirmation gates (200 variants), plus 1,000 deterministic composite combinations. The combinations are generated with a fixed random seed; they are candidates, not 1,000 independently proven profitable strategies.
3. Market adaptation: market-regime classification and adaptive RSI/EMA parameter selection exist. This is partial adaptation, not unconstrained self-optimization.
4. MT5 client: `mt5_client/ALZOOL_Central_Client.mq5` sends account/position/order/deal/candle telemetry and receives central strategy decisions/settings. It contains local execution, trailing protection, account-direction checks and account-wide profit-close logic.
5. Learning: the backend calculates performance profiles from closed-deal history and exposes learning/backtest data. This is statistical weighting, not LLM fine-tuning.
6. Licensing: `LICENSING_SETUP.sql` defines the licensed-client table with revoked/expiry fields. Preserve customer and licence records during future changes.

## Gaps against the approved central-AI design
1. No verified multi-model LLM vote council is wired into the mandatory entry path. The backend's strategy engine produces the central decision; the MT5 client accepts a valid strategy decision with its current confidence/closed-bar checks. A separate LLM advisor is not an entry authorization gate.
2. Recovery in the inspected production MQ5 still selects direction from the next price movement and uses `totalLots * 2.0`. It does not yet implement the agreed x3 plus central-signal, closed-candle and confidence confirmation workflow.
3. The current AI add-on/cooling logic is local EMA/ATR rule logic and scans only the current chart symbol plus AL-ZOOL magic number. It is not a central council decision and is not account-wide.
4. Account-wide telemetry and group profit-close exist, but add-on/cooling and trailing management are scoped to AL-ZOOL positions on the current symbol. A unified account manager for all symbols/sources is still needed, with explicit safeguards against mixing incompatible symbols.
5. Learning weights are heuristic/statistical. Automatic experiment validation, versioned promotion and rollback are not yet demonstrated.
6. Diagnostics endpoints/UI exist for recovery events, but end-to-end event delivery from the currently inspected production MQ5 must be verified and completed.
7. Do not claim that strategy counts imply profitability or that LLM council approval, all-position management, or the agreed recovery design is already live.

## Non-negotiable implementation constraints
- Preserve all current pages, strategy families, combinations, customer/licence flows and working MT5 connectivity.
- Keep BUY and SELL under one shared central brain and one control panel.
- Every new entry must require the approved council's authorization; if council/data is unavailable, stale or contradictory, fail closed (WAIT/no new entry).
- Keep user-owned lot sizes/caps, daily and total loss limits, SL/TP, trailing and protection settings outside autonomous learning authority.
- Learning may adjust strategy selection/weights and indicator parameters only inside explicit safe bounds; retain experiment history and rollback.
- Price/candle data only for decisions unless the user later approves another source. No economic-news dependency.
- Account-wide trade visibility is required. Any account-wide close action must follow the user's already approved group-close behavior and be clearly diagnosed.
- No grid or martingale. Do not silently change configured risk parameters.
- Keep MT5 data ingestion lightweight; do not introduce frequent blocking HTTP calls on the tick path.

## Required test gates before production
1. Static/code review and syntax checks for every changed file.
2. Unit tests for council authorization, WAIT/rejection, stale data, timeouts, model disagreement and risk-bound enforcement.
3. Backtest and paper/demo validation; verify no order is sent without council authorization.
4. Recovery tests for the agreed x3 calculation, broker lot-step/margin checks, closed-bar confirmation, account-wide mixed-symbol safeguards and event logs.
5. Add-on/cooling tests across manual, external-EA and AL-ZOOL positions, symbols and directions.
6. Confirm customer login, one-device licensing, owner revocation and existing licences remain intact.
7. Verify Render health, MT5 telemetry, candle freshness, latency and rollback procedure.
8. Obtain explicit user approval before production merge/deploy or enabling live trading.

## Next dependency
Obtain the laptop CPU, GPU/VRAM, RAM and Windows version before choosing local Ollama models or installing anything. The user has reported 32 GB RAM; CPU/GPU remain unverified.
