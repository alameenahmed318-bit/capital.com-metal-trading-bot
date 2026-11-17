# AL-ZOOL SUPER AI — Central MT5 Gold Engine

Main service: https://al-zool-central-engine.onrender.com
Deployment branch: `work/alzool-foundation-20261114`

**IMPORTANT:** The central Python server DOES NOT directly place broker orders, but
the `mt5_client/ALZOOL_Central_Client.mq5` Expert Advisor **can** open, modify
and close broker positions when explicitly enabled. This is not a read-only
telemetry system. Trade entry requires the server authorization, council, risk
guard and local EA opt-in. Central position actions require a separate
one-time claim and independent opt-in. Never enable these during setup.

## Components
- Backend: `marketplace/backend/app.py`; decisions, account state, licensing,
  owner settings, audit and durable Supabase events.
- Strategy research: 200 composite strategies / 1000 overlapping combinations.
  Historical directional accuracy does NOT establish profitability.
- Local AI: `mt5_client/ALZOOL_Local_Model_Relay.py` for Ollama Qwen and DeepSeek
  owner votes. Their READY startup banner is not proof of successful votes.
- MT5: `mt5_client/ALZOOL_Central_Client.mq5` sends fresh candles and
  account positions; only the MT5 terminal can submit broker orders.
- Storage: Supabase with server-side service-role access; never expose
  `SUPABASE_SERVICE_ROLE_KEY` to clients.

## Operational constraints
- Current market support: XAUUSD family; verify broker suffix and point size.
- Dashboard manages configuration. Local EA still has hard safety settings and
  must be compiled/installed on each Windows terminal; deployment alone cannot
  update a running .ex5.
- The default central trading, position actions and recovery are OFF until
  explicitly enabled. Do not interpret an archived signal as execution approval.
- Dynamic x3 recovery targets the total volume of qualifying same-symbol,
  same-direction LOSING positions; it is rejected if it exceeds configured
  independent recovery maximum, total account lots, per-direction position cap
  or local EA risk caps. It is not a guarantee of profitable recovery.
- Group TP is an account-wide close process. It attempts to close all positions
  and retries leftover tickets; broker closes are not atomic.

## Required validation before real capital
1. Compile EA in MetaEditor, confirm 0 errors and reinstall it.
2. Observe live MT5 telemetry and local model **accepted** votes.
3. Test all entries, market delays, position limits and restart cases on Demo.
4. Verify partial group close, manual/external positions, and one-time action claims.
5. Verify multi-client segregation, license revocation and restart recovery.
6. Validate backtests after transaction costs on out-of-sample candles.
7. Review `mt5_client/ALZOOL_RELEASE_CHECKLIST.md`.

Tests and a Render LIVE deployment **do not** constitute trading certification.
