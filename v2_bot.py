"""Capital bot V2 strategy wrapper.

V2 deliberately uses a different signal model from V1: regime-aware
mean reversion + volatility expansion + price location. It reuses only the
execution/risk plumbing from bot.py. This file is intentionally kept on the
v2-development branch until strategy-isolation testing is complete.
"""
import numpy as np
import pandas as pd

import bot as base

V2_STRATEGY_ID = "CAPITAL_V2_MEANREV"
V2_MIN_SCORE = 58.0
V2_RSI_OVERSOLD = 32.0
V2_RSI_OVERBOUGHT = 68.0
V2_BB_LOOKBACK = 20
V2_BB_STD = 2.0
V2_ATR_LOOKBACK = 14


def _atr(df):
    h = pd.to_numeric(df["high"], errors="coerce")
    l = pd.to_numeric(df["low"], errors="coerce")
    c = pd.to_numeric(df["close"], errors="coerce")
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(V2_ATR_LOOKBACK).mean()


def quant_signal_score_v2(df, htf_df, epic):
    """Return the same tuple shape as V1, but with an independent model.

    V2 looks for stretched price at Bollinger-band extremes, confirms that
    the higher timeframe is not in a strong one-way trend, and adds a
    volatility-expansion/price-action component. It does not call V1's
    trend/momentum score.
    """
    if df is None or len(df) < 80 or htf_df is None or len(htf_df) < 60:
        return None, None, {"reason": "insufficient_data", "strategy_id": V2_STRATEGY_ID}

    x = df.copy()
    close = pd.to_numeric(x["close"], errors="coerce")
    high = pd.to_numeric(x["high"], errors="coerce")
    low = pd.to_numeric(x["low"], errors="coerce")
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    mid = close.rolling(V2_BB_LOOKBACK).mean()
    std = close.rolling(V2_BB_LOOKBACK).std(ddof=0)
    upper = mid + V2_BB_STD * std
    lower = mid - V2_BB_STD * std
    atr = _atr(x)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    htf_close = pd.to_numeric(htf_df["close"], errors="coerce")
    htf50 = htf_close.ewm(span=50, adjust=False).mean().iloc[-1]
    htf200 = htf_close.ewm(span=200, adjust=False).mean().iloc[-1]
    price = float(close.iloc[-1])
    a = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else None
    r = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0
    m = float(mid.iloc[-1]) if pd.notna(mid.iloc[-1]) else price
    u = float(upper.iloc[-1]) if pd.notna(upper.iloc[-1]) else price
    lo = float(lower.iloc[-1]) if pd.notna(lower.iloc[-1]) else price
    if a is None or a <= 0 or not np.isfinite([price, r, m, u, lo, htf50, htf200]).all():
        return None, None, {"reason": "invalid_indicators", "strategy_id": V2_STRATEGY_ID}

    # Strong HTF displacement means mean reversion is less attractive.
    htf_gap_atr_proxy = abs(htf50 - htf200) / a
    strong_trend = htf_gap_atr_proxy > 1.5
    location = (price - m) / max(std.iloc[-1], 1e-9)

    buy = 0.0
    sell = 0.0
    if price <= lo:
        buy += 32
    elif location < -1.0:
        buy += 20
    if r <= V2_RSI_OVERSOLD:
        buy += 28
    elif r < 42:
        buy += 14
    if price < ema20.iloc[-1]:
        buy += 10
    if close.iloc[-1] > close.iloc[-2]:
        buy += 10

    if price >= u:
        sell += 32
    elif location > 1.0:
        sell += 20
    if r >= V2_RSI_OVERBOUGHT:
        sell += 28
    elif r > 58:
        sell += 14
    if price > ema20.iloc[-1]:
        sell += 10
    if close.iloc[-1] < close.iloc[-2]:
        sell += 10

    if strong_trend:
        # Reduce confidence rather than importing V1's trend score.
        buy *= 0.75
        sell *= 0.75
    else:
        # Flat/neutral regime is where V2 is designed to operate.
        if abs(price - ema50.iloc[-1]) <= 0.75 * a:
            buy += 5
            sell += 5

    buy = float(min(100, max(0, buy)))
    sell = float(min(100, max(0, sell)))
    signal = None
    score = max(buy, sell)
    if score >= V2_MIN_SCORE and abs(buy - sell) >= 8:
        signal = "BUY" if buy > sell else "SELL"

    diag = {
        "strategy_id": V2_STRATEGY_ID,
        "buy_score": round(buy, 2),
        "sell_score": round(sell, 2),
        "rsi": round(r, 2),
        "bb_location": round(float(location), 3),
        "strong_htf_trend": bool(strong_trend),
        "htf_gap_atr_proxy": round(float(htf_gap_atr_proxy), 3),
    }
    base.log(f"{epic}: V2 MEAN-REV | BUY={buy:.1f} SELL={sell:.1f} RSI={r:.1f} BBz={location:.2f} strongHTF={strong_trend}")
    return signal, score, diag


# process_epic in V1 resolves quant_signal_score from the bot module's global
# namespace. Monkey-patching is therefore used only inside this V2 process;
# V1's source remains untouched on main.
base.quant_signal_score = quant_signal_score_v2
base.STRATEGY_ID = V2_STRATEGY_ID
base.STATE_FILE = "v2_trades_state.json"
base.OPEN_POSITIONS_FILE = "v2_open_positions.json"
base.SAFETY_STATE_FILE = "v2_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "v2_execution_quality.json"
base.ENTRY_REJECTION_FILE = "v2_entry_rejections.json"


def run_cycle():
    base.log(f"STARTING {V2_STRATEGY_ID} | DEMO ONLY | independent mean-reversion strategy")
    return base.run_cycle()


if __name__ == "__main__":
    run_cycle()
