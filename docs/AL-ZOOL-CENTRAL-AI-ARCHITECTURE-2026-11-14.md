# AL-ZOOL Central AI Architecture — Approved Design
Date: 2026-11-14
Status: Approved design baseline; implementation not yet enabled in production.

## 1. Approved operating profile
- Intelligence level: maximum practical quality, subject to measured laptop hardware and MT5 latency.
- Deployment: hybrid. The MT5 terminal and deterministic execution/risk controls remain local; local models handle fast analysis where hardware permits; deeper analysis may use the central service only when configured and available.
- Market inputs: price, candles and available market-volume/tick data only. No economic-news dependency.
- Trade scope: account-wide visibility and the agreed account-wide group-profit management. Track symbol, direction, ticket, origin (manual/AL-ZOOL/other EA), and ownership separately.
- Direction model: BUY and SELL share one central decision brain, one council and one control panel.
- Entry authorization: mandatory council authorization for every new entry. Missing, stale, timed-out or contradictory council output means WAIT and no new entry.
- Learning: automatic, bounded and auditable. It may tune strategy selection, strategy weights, indicator parameters and timing only inside configured safe ranges. It cannot change lot size/caps, multipliers, SL/TP, trailing, daily/total loss limits or protection settings.
- Prohibited: grid and martingale; hidden risk-setting changes; direct LLM-to-MT5 order execution.

## 2. Intelligence components
1. Fast AI (Qwen family): rapid interpretation of current price/candle context and concise structured proposals.
2. Deep AI (DeepSeek family): second-stage reasoning for high-impact opportunities, disagreement, unusual volatility or ambiguous regimes. It is not called on every tick.
3. Quant Engine: deterministic calculations, regime classification, probability/statistical scoring, spread/volatility/liquidity checks where data is available, and strategy evaluation.
4. AI Vote Council: combines Fast AI, Deep AI when invoked, Quant Engine and strategy-family votes into BUY / SELL / WAIT plus confidence, reasons and dissent. It is the mandatory authorization gate, not an order executor.
5. Risk Guardian: independent deterministic hard limits. It can veto any council proposal. It is not trainable and cannot be overridden by LLM output.
6. Learning & Experiment Manager: records outcomes, evaluates candidate changes on historical/out-of-sample and demo data, versions approved profiles and supports immediate rollback.
7. Account Trade Manager: unified account inventory and per-position management with symbol-aware safeguards. Account-wide close behavior remains explicit and separately logged.
8. Diagnostics: record every decision stage, timestamps/latency, market snapshot identifiers, selected strategy, model/provider/version, votes, confidence, veto/WAIT reason, and resulting order/position identifiers.

## 3. Decision and execution flow
1. MT5 streams compact candle/tick/account/position telemetry to the existing data path; do not add blocking model calls to the tick handler.
2. Quant Engine classifies market state and evaluates the existing 20 strategy families, 200 variants and 1,000 composite candidates. These counts describe candidates, not proven profitable strategies.
3. Fast AI returns a schema-validated proposal. Deep AI is conditionally invoked for disagreement, important setups or abnormal market conditions.
4. Council produces a signed/identifiable decision record: BUY, SELL or WAIT; confidence; strategy; rationale; freshness; expiry; and vote breakdown.
5. Risk Guardian validates account limits, user-configured settings, symbol consistency, market-data freshness and broker constraints. Any failure changes the result to veto/WAIT.
6. Only the local deterministic execution layer may send an MT5 order, and only with a fresh council authorization plus Risk Guardian approval.
7. Every action and non-action is written to diagnostics. If the central service/model is unavailable, existing positions continue to receive local protective management; no new entry is authorized.

## 4. Account-wide management boundaries
- Read and display all open positions/orders/deals, including manual and external-EA activity.
- Preserve origin, symbol, direction, ticket and magic metadata; never merge P/L or recovery sizing across incompatible symbols.
- The user's previously approved group-profit close may close all account positions when its configured group target is met; clearly warn and log the scope.
- Add-on, cooling and recovery decisions must be council-authorized and separately constrained by user-set count, lot and multiplier caps. No automatic widening of risk limits.
- Account-wide visibility does not mean every strategy may modify every external position. Each management action must have an explicit allowed scope and diagnostics.

## 5. Recovery / add-on / cooling
- Recovery target design: x3 calculation, subject to the user's configured caps and broker min/max/step/margin checks.
- Direction must agree with the fresh central signal, closed-candle confirmation and minimum confidence threshold (the previously agreed development threshold was 0.45); council authorization and Risk Guardian approval are additionally required in the final design.
- The council may refuse recovery/add-on/cooling even after a numeric trigger is reached.
- Do not deploy the older production x2/tick-direction behavior as if it were the approved final design.
- Mixed-symbol groups must be blocked or handled as separate symbol-specific groups; never calculate one symbol's recovery lot from an unrelated symbol's exposure.

## 6. Automatic learning policy
- Use closed outcomes and trustworthy historical data; distinguish manual, external-EA and AL-ZOOL trades.
- Candidate updates require repeatable validation, out-of-sample checks and demo/paper evaluation before promotion.
- Store profile version, metrics, training/evaluation window, approval reason and rollback pointer.
- Automatically promote only within pre-approved bounds; immediately revert a profile that breaches defined performance or safety checks.
- No online self-modification of execution code, risk limits or customer/licence controls.

## 7. Performance and resilience
- MT5 tick ingestion and local protective management must not wait for LLM inference.
- Fast analysis is asynchronous and rate-limited; Deep AI is conditional, cached by market snapshot and time-bounded.
- Include request deadlines, stale-data rejection, bounded queues, circuit breakers and explicit WAIT behavior.
- Measure end-to-end latency and missed/stale candles on the actual laptop before selecting model sizes or enabling local inference.
- Hybrid fallback must never turn model unavailability into implicit trade approval.

## 8. User interface
One shared control panel with:
- AI Command Center and service/model health
- Market state and candle freshness
- Council votes, confidence, BUY/SELL/WAIT and dissent
- Strategy families, variants and composite candidates
- Account-wide positions with origin/symbol/direction filters
- Add-on, cooling and recovery controls
- Learning experiments, versions and rollback
- Diagnostics timeline explaining opened / waited / rejected / vetoed / managed actions
- Central settings for all user-owned risk limits and licensing/customer administration

## 9. Compatibility and release rules
- Preserve existing pages, strategy engine, customer login, one-device licensing, owner administration, licence expiry/revocation, Supabase records and Render connectivity.
- Implement on the isolated work branch only. Do not change the production branch, Render service, Supabase schema/data or live MT5 behavior as part of this design document.
- Before release: unit/integration tests, backtests, demo/paper tests, latency tests, licensing regression tests and rollback rehearsal.
- Production merge/deploy and live-trading activation require the user's separate explicit approval.

## 10. Hardware dependency
The laptop has been reported as 32 GB RAM. CPU model, GPU model/VRAM and Windows version are still required. Do not download/install model weights or select final Qwen/DeepSeek quantizations until those values and MT5 latency constraints are verified.
