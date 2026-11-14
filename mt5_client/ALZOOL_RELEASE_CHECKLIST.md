# AL-ZOOL | Final installation and acceptance checklist

This is an operational checklist, not evidence that the remote MT5 terminal was
tested. The cloud service can deploy successfully while a Windows EA remains old.

## Safety mode

- The owner account has `trading_enabled=false`.
- Central add-on/cooling/recovery is separately disabled by default.
- The MT5 v2.40 source has `InpEnableCentralTrading=false` and
  `InpEnableCentralPositionManagement=false`.
- Do not turn them ON during installation or debugging.

## On the Windows laptop

1. Update the project checkout to `work/alzool-foundation-20261114`.
2. Run `mt5_client/ALZOOL_Check_and_Start.ps1` in PowerShell, with
   `-CredentialsPath` pointing to the downloaded owner pairing JSON file.
   This check does **not** place trades.
3. In MetaEditor compile `mt5_client/ALZOOL_Central_Client.mq5` (v2.40)
   using **F7**. The compile log must have **0 errors**. Install the
   resulting `.ex5` in the terminal's own `MQL5/Experts` folder.
4. Detach the previous ALZOOL EA from the XAUUSD M1 chart and attach the
   newly compiled EA. Keep both central trading flags disabled.
   Enable WebRequest only for the confirmed Render engine URL.
5. The private owner pairing key has previously been visible in a screenshot.
   Re-pair in the owner dashboard **once**, save the JSON file locally,
   and use the new values. Never paste keys into support chats or logs.
6. Install and run Ollama. Check for `qwen2.5:3b` and
   `deepseek-r1:1.5b`. If absent, install them with
   `ollama pull qwen2.5:3b` and `ollama pull deepseek-r1:1.5b`.
7. Start the read-only local vote relay:
   `python mt5_client/ALZOOL_Local_Model_Relay.py --credentials "FULL_PATH_TO_OWNER_PAIRING.json"`.
   The relay sends only model votes; it cannot place broker orders.
8. Open owner dashboard -> **فحص اكتمال AL-ZOOL**. Confirm MT5 heartbeat,
   market data, fresh analysis, Fast AI, Qwen and DeepSeek are all READY.

## Demo acceptance tests (must be observed, not merely unit tested)

- **Connection loss**: MT5 and dashboard show disconnected; new orders denied.
- **Missing Qwen/DeepSeek**: model council stays WAIT, no new orders.
- **Disagreement**: no entry if any vote disagrees with the others.
- **Valid approved signal**: one permitted entry with expected symbol, lot,
  stop-loss and recorded strategy tag; no duplicate.
- **Risk caps**: equity drawdown, position count and total lots veto excess
  exposure. Test all conditions.
- **Central add-on/cooling/recovery**: no order without explicit admin opt-in,
  separate MT5 opt-in and one-time action claim. Confirm recovery x3 never
  exceeds owner lot and total exposure caps.
- **Closed trade**: deal is persisted and attributed to the strategy; historical
  report agrees with MT5 after expenses.
- **Version rollback**: only changes strategy weights; never enables trading.
- **Customer access**: client must not see another customer's MT5 positions,
  keys, settings, reports or history.
- **Mobile**: owner and trading pages must render and display real values.

Passing GitHub Python tests or Render deploy checks is not a substitute for
the Windows MetaEditor compilation, Ollama integration and demo-account order
tests.

Only after these have been explicitly reviewed should the account owner decide
whether to enable any trading controls. No automated action is taken here.
