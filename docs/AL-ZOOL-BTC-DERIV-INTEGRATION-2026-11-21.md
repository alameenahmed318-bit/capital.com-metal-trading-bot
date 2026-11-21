# AL-ZOOL BTCUSD / Deriv MT5 integration (2026-11-21)

## Scope of this change
- Keep the existing XAUUSD central engine and trading rules intact.
- Allow the Deriv `BTCUSD` chart (and limited broker suffixes) through strategy, advisor, entry and authorized position-action symbol gates.
- The existing 200 strategies and 1000 combinations evaluate *closed BTC candles* using price-relative technical signals, not prewritten gold prices. The method is **not** a verified profitable BTC strategy.
- The MT5 relay uses the live contract's `SYMBOL_VOLUME_MIN/MAX/STEP`, tick value and contract size. It **rejects** a configured lot below the broker minimum instead of silently increasing exposure.
- Learning statistics only use closed deals of the active chart symbol. A stored gold strategy version is **not** automatically activated for BTC.
- The BTC external TradingView chart is *illustrative spot data* and can differ from Deriv's CFD prices.
- Default central trading in the MT5 client stays **OFF**. Neither live trading nor a new stop-loss policy is enabled by the patch.

## Hard safety limitation — do not run XAUUSD and BTCUSD on the same trading account yet
The current server has **one active chart snapshot per connection ID** and legacy EA group profit/recovery closure is account-wide. If two charts share the same connection ID, their snapshots can overwrite one another. If both symbols hold positions on the same account, the legacy gold EA can close the BTC positions as part of its account basket.

**Use a separate Deriv MT5 demo account and separate connection/engine identity for BTCUSD during evaluation. Do not place the gold and BTC EA on charts in the same account.** The BTC EA refuses automatic closing/new trading when positions from another symbol coexist, but the legacy gold EA is not isolated per symbol.

## Setup
1. Open Deriv MT5 *demo*, find its actual BTCUSD symbol and check Specification (contract size, tick size/value, volume minimum/step, spread, commission, margin, session).
2. Attach `mt5_client/ALZOOL_Central_Client.mq5` to **BTCUSD M1** on the dedicated demo account.
3. Set the approved server URL and the correct owner/client pairing credentials. In MT5, allow WebRequest access to that server. Keep `InpEnableCentralTrading=false` and `InpEnableCentralPositionManagement=false` during diagnostics.
4. Confirm the live endpoint stores **65+ closed BTCUSD candles**, the connection status reports BTCUSD, and strategy decision is updated on the most recent *closed* bar.
5. Inspect entry gate logs, trade costs and contract sizing. In Strategy Tester and Demo evaluate actual fills, slippage, net after costs, maximum drawdown, frozen positions and weekend spread expansions.
6. Only after explicit user approval and broker volume/margin verification consider switching on demo execution. This patch does not turn it on.

## Not yet included / not verified
- No live broker order execution, MT5 MetaEditor compilation or historical backtest was performed as part of this change.
- A single server connection cannot operate gold and BTC charts simultaneously. True multi-symbol parallel operation needs per-client **and per-symbol** snapshots, entry/auth IDs, settings, learning versions, position baskets and closing logic.
- Strategy-version promotion and automatic background research remain **gold-only** to avoid cross-market weight contamination until version storage is partitioned by market.
- Legacy account-wide take-profit and x3 recovery are not safe across mixed-contract positions. The dedicated BTC demo account restriction is mandatory.
- Do not assume that 0.01 lot matches the demo account's available contract and minimum trade size.
