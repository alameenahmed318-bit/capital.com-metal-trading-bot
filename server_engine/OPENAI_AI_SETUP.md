# AL-ZOOL official OpenAI analysis setup

AL-ZOOL calls the official OpenAI Responses API from the central server only. The browser dashboard and MT5 client never receive the API key.

## Render environment variables

In the Render service that runs the AL-ZOOL web/central engine, add:

- `OPENAI_API_KEY`: create an API key in the OpenAI API platform and save it as a Render secret. Never paste it into chat, GitHub source, browser code, MT5 inputs, or Supabase rows.
- `OPENAI_MODEL`: optional; defaults to `gpt-6-astra`.
- `OPENAI_AI_ENABLED`: optional; defaults to `true`. Set to `false` to pause API analysis without changing the central trading engine.

Save the environment changes and redeploy/restart the Render service.

## How it works

- The central engine sends up to the latest 65 closed candles, the forming candle, current quote, central-engine context, and available open-position/pending-order summaries.
- One request is queued per newly closed candle, not per tick. The request runs asynchronously so it does not block MT5 execution polling.
- Results use the official Responses API with strict JSON Schema output and are stored as `AI_ANALYSIS` events in the existing client-scoped signal-event table.
- The dashboard's AI Council page shows the provider, model, freshness, bias, confidence, Arabic reasons, and risks.
- The AI is initially `MONITOR_ONLY`. It does not open, close, reverse, resize, or manage trades. Existing deterministic central management and user settings remain the only source of broker commands.
- Missing keys, API errors, refusals, malformed outputs, and stale analyses are shown as unavailable/error/stale; no synthetic AI result is substituted.

## Billing and validation

OpenAI API usage is billed separately from a ChatGPT subscription. Review the current API pricing before enabling requests. After setting the secret, sign in to AL-ZOOL, open the AI Council page, and wait for the next newly closed candle. A successful result is labelled `ready` and identifies the configured model.

A valid API key and account access to the selected model are required for live analysis. CI tests mock the API and do not prove that a real API key has been configured or that a live request has succeeded.
