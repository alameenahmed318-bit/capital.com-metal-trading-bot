# BTC / Gold isolation — Meeno deployment, 2026-11-21

This deploy branch is **BTCUSD-only**, scoped to the separate `al-zool-meeno-engine` Render service.
Gold stays on `al-zool-central-engine` using `work/alzool-foundation-20261114` and the original Supabase project.
Do not merge this branch into the original gold branch.

## Isolation rules

- BTC trading settings are stored in new Supabase table `public.alzool_btc_trade_settings` in the **new** Supabase project, with default trading actions OFF, profit closes OFF and stop-loss OFF. This table is **not seeded from existing gold settings**.
- The new server rejects all non-BTCUSD terminal-state snapshots and historical uploads before caching, learning, or persisting them.
- A BTC terminal snapshot with any foreign-symbol open position or pending order is rejected.
- Only BTC-labelled closed deals contribute to live strategy/combination learning, BTC dashboard P/L and BTC trade history.
- The 200 strategy rules and 1,000 combinations are reused as algorithms. BTC uses an independent *heuristic starting* EMA/RSI regime profile; this is NOT performance-calibrated or tested on live Deriv BTC results. Existing gold version activations are blocked on the BTC server.
- Recovery/action planner rejects mixed-symbol portfolios. New BTC EA only initializes on a BTCUSD chart and refuses to close a ticket not matching the chart, or to close anything when another symbol is present.
- MT5 state keys for closes/recovery/reentry include chart symbol; gold's MT5 global variables cannot control BTC.

## Preconditions and testing

- Use a dedicated **Deriv MT5 demo account** for BTC. Do not run gold and BTC on one MT5 account or engine ID.
- Configure the new owner pairing to the Meeno URL.
- Compile the updated `.mq5` in MetaEditor and verify 0 compile errors (compilation **not available** in connected cloud tools).
- Run broker-lot-step / margin / history / network checks with `InpEnableCentralTrading=false` and `InpEnableCentralPositionManagement=false`.
- Do not enable live trading until manual demo observations and complete CI are clean.
- No stop-loss is configured by default; large floating loss and margin liquidation remain possible.
