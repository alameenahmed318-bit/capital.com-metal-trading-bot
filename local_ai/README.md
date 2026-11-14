# AL-ZOOL Local Qwen Relay

## Purpose
This optional local process runs on the same Windows PC as MT5 and Ollama. It reads the current central decision from the licensed AL-ZOOL server, asks the already-installed `qwen2.5:3b` model for an independent BUY/SELL/WAIT vote, and posts that vote back to the server.

It never places, modifies, or closes orders. It does not run inside the MT5 tick/timer path, and MT5 does not wait for the model. The MT5 terminal continues to execute only server-authorized decisions.

## Requirements
- Python 3.10+
- Ollama running at `http://127.0.0.1:11434`
- Existing model `qwen2.5:3b`
- The licensed MT5 Engine ID and Engine Token

No model is downloaded automatically. Check the installed model first:

```powershell
ollama list
```

If `qwen2.5:3b` is absent, stop here and choose a model explicitly; this relay will not pull model weights.

## Run on Windows
From the repository root:

```powershell
python .\local_ai\ai_relay.py
```

Enter the Engine ID and token when prompted. The token is requested as a hidden password and is not written to a config file. Keep the PowerShell window open while the local council vote is required. Press Ctrl+C to stop it.

The relay processes each newly closed-bar decision once, uses a short bounded Ollama request, and prints its vote and whether the server accepted it.

## Server switch — fail-closed behavior
The server setting `LOCAL_AI_REQUIRED` defaults to `false`. When enabled, a new BUY/SELL entry is held at WAIT unless a fresh Qwen vote:
- belongs to the same closed candle as the central decision,
- is no older than 20 seconds,
- agrees with the central BUY/SELL direction,
- has confidence of at least 0.50.

Enable `LOCAL_AI_REQUIRED=true` on the server only after the relay is running and its votes are visible. If the PC, Ollama, or relay stops, new entries are blocked; existing local position protection and closing logic remain available. Do not enable this switch before the relay has been tested on the intended terminal.

## DeepSeek
DeepSeek API is not part of this free local relay. Its hosted API is pay-per-token; do not add an API key or enable cloud reasoning without an explicit budget decision.
