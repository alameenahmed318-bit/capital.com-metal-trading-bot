# AL-ZOOL GOLD SNIPER — MT5 connection, licensing and research

This service manages customer access/licensing and receives read-only MT5 telemetry. It does not place, modify, or close trades.

## Backtest Lab historical data
- The MT5 client sends a live snapshot (up to 120 closed candles) for AI analysis.
- The updated MT5 client also uploads up to 5,000 historical closed candles from its current chart timeframe every 15 minutes.
- Historical candles are stored durably in Supabase in `public.alzool_engine_market_candles`, keyed by client, symbol, timeframe and candle time.
- The Backtest Lab reads the stored archive and evaluates the 200 registered strategies and 1,000 combinations over the selected date range and timeframe.
- Simulation enters at the next candle open and exits at that candle close. It deducts historical MT5 spread, user-entered round-turn commission per lot and user-entered slippage.
- P/L is an estimate using MT5 tick value/tick size where available (or contract size as a fallback). It is not a broker-execution replay and does not include variable stop/target rules, swaps, requotes, latency or partial fills.

## MT5 client update
Compile `mt5_client/ALZOOL_Central_Client.mq5` in MetaEditor and attach it to the XAUUSD chart/timeframe you want archived. Preserve the existing server URL, client ID and license token. New inputs:
- `InpHistoricalCandlesToSend`: 5,000 closed candles by default (maximum 10,000).
- `InpHistoryUploadMinutes`: upload interval, 15 minutes by default.

Allow the Render HTTPS host in MT5 WebRequest settings. Each chart timeframe is archived independently; attach the client to each timeframe you want to test. Do not remove the existing client or licensing settings.

## Supabase
The table schema is documented in `BACKTEST_MARKET_CANDLES_SETUP.sql` and has already been applied to the configured project. RLS is enabled; only the backend service role has table access. Never expose the service-role key to the browser.

## Render
Render runs `python marketplace/backend/app.py`. Required environment variables: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, and `OWNER_EMAIL`.
