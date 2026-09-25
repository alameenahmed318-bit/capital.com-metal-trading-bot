"""Capital.com V2 - Quant Hybrid strategy.

V2 keeps the independent strategy/execution plumbing from bot.py, but upgrades
the signal engine into a regime-aware quantitative ensemble inspired by
systematic trend, momentum, breakout, mean-reversion and volatility-scaling
research. It does not copy any proprietary firm's model.
"""

import numpy as np
import pandas as pd

import bot as base

V2_STRATEGY_ID = "CAPITAL_V2_QUANT_HYBRID"
V2_MIN_SCORE = 58.0
V2_RSI_OVERSOLD = 32.0
V2_RSI_OVERBOUGHT = 68.0
V2_BB_LOOKBACK = 20
V2_BB_STD = 2.0
V2_ATR_LOOKBACK = 14


def _atr(df, period=V2_ATR_LOOKBACK):
    h = pd.to_numeric(df["high"], errors="coerce")
    l = pd.to_numeric(df["low"], errors="coerce")
    c = pd.to_numeric(df["close"], errors="coerce")
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _clip(value, low=0.0, high=100.0):
    return float(min(high, max(low, value)))


def _slope(series, lookback=10):
    values = pd.to_numeric(series, errors="coerce").tail(lookback).dropna().values
    if len(values) < 3:
        return 0.0
    x = np.arange(len(values), dtype=float)
    return float(np.polyfit(x, values, 1)[0])


def quant_signal_score_v2(df, htf_df, epic):
    """Regime-aware Quant Hybrid score.

    Components:
      - multi-timeframe trend
      - time-series momentum
      - breakout quality
      - pullback quality
      - Bollinger/RSI mean reversion
      - volatility/regime adaptation
      - candle/structure confirmation

    Components are weighted rather than made into a long chain of hard
    rejection gates. This keeps V2 responsive while still adapting risk and
    conviction to market conditions.
    """
    if df is None or len(df) < 100 or htf_df is None or len(htf_df) < 80:
        return None, None, {"reason": "insufficient_data", "strategy_id": V2_STRATEGY_ID}

    x = df.copy()
    close = pd.to_numeric(x["close"], errors="coerce")
    high = pd.to_numeric(x["high"], errors="coerce")
    low = pd.to_numeric(x["low"], errors="coerce")
    open_ = pd.to_numeric(x["open"], errors="coerce")

    ema9 = close.ewm(span=9, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema100 = close.ewm(span=100, adjust=False).mean()

    mid = close.rolling(V2_BB_LOOKBACK).mean()
    std = close.rolling(V2_BB_LOOKBACK).std(ddof=0)
    upper = mid + V2_BB_STD * std
    lower = mid - V2_BB_STD * std

    atr = _atr(x)
    rsi = _rsi(close)

    htf_close = pd.to_numeric(htf_df["close"], errors="coerce")
    htf20 = htf_close.ewm(span=20, adjust=False).mean()
    htf50 = htf_close.ewm(span=50, adjust=False).mean()
    htf100 = htf_close.ewm(span=100, adjust=False).mean()
    htf200 = htf_close.ewm(span=200, adjust=False).mean()

    price = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    a = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else None
    r = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0
    m = float(mid.iloc[-1]) if pd.notna(mid.iloc[-1]) else price
    sd = float(std.iloc[-1]) if pd.notna(std.iloc[-1]) else 0.0
    u = float(upper.iloc[-1]) if pd.notna(upper.iloc[-1]) else price
    lo = float(lower.iloc[-1]) if pd.notna(lower.iloc[-1]) else price

    h20 = float(htf20.iloc[-1])
    h50 = float(htf50.iloc[-1])
    h100 = float(htf100.iloc[-1])
    h200 = float(htf200.iloc[-1])

    if a is None or a <= 0 or sd <= 0:
        return None, None, {"reason": "invalid_indicators", "strategy_id": V2_STRATEGY_ID}
    if not np.isfinite([price, prev, a, r, m, sd, u, lo, h20, h50, h100, h200]).all():
        return None, None, {"reason": "invalid_indicators", "strategy_id": V2_STRATEGY_ID}

    # --- Quant state / regime ---
    ema_gap = abs(float(ema20.iloc[-1] - ema50.iloc[-1])) / a
    htf_gap = abs(h50 - h200) / a
    atr_fast = float(atr.tail(20).mean()) if atr.tail(20).notna().any() else a
    atr_slow = float(atr.tail(100).mean()) if atr.tail(100).notna().any() else a
    vol_ratio = atr_fast / max(atr_slow, 1e-9)

    if htf_gap >= 1.25 and abs(h50 - h200) / max(h200, 1e-9) > 0.0005:
        regime = "TREND"
    elif vol_ratio >= 1.20:
        regime = "BREAKOUT"
    elif ema_gap <= 0.15 and vol_ratio <= 1.05:
        regime = "RANGE"
    else:
        regime = "TRANSITION"

    htf_up = h20 > h50 and h50 >= h100 and h50 >= h200
    htf_down = h20 < h50 and h50 <= h100 and h50 <= h200

    # --- Time-series momentum ---
    mom5 = (price / max(float(close.iloc[-6]), 1e-9) - 1.0)
    mom10 = (price / max(float(close.iloc[-11]), 1e-9) - 1.0)
    mom20 = (price / max(float(close.iloc[-21]), 1e-9) - 1.0)
    mom_unit = max(a / max(price, 1e-9), 1e-6)
    momentum_buy = _clip(50 + 18 * (mom5 / mom_unit) + 14 * (mom10 / mom_unit) + 10 * (mom20 / mom_unit))
    momentum_sell = _clip(50 - 18 * (mom5 / mom_unit) - 14 * (mom10 / mom_unit) - 10 * (mom20 / mom_unit))

    # --- Trend strength ---
    trend_buy = 0.0
    trend_sell = 0.0
    if price > ema20.iloc[-1]:
        trend_buy += 20
    if ema9.iloc[-1] > ema20.iloc[-1]:
        trend_buy += 20
    if ema20.iloc[-1] > ema50.iloc[-1]:
        trend_buy += 20
    if htf_up:
        trend_buy += 40

    if price < ema20.iloc[-1]:
        trend_sell += 20
    if ema9.iloc[-1] < ema20.iloc[-1]:
        trend_sell += 20
    if ema20.iloc[-1] < ema50.iloc[-1]:
        trend_sell += 20
    if htf_down:
        trend_sell += 40

    # --- Breakout quality ---
    prior_high = float(high.iloc[-21:-1].max())
    prior_low = float(low.iloc[-21:-1].min())
    breakout_buy = 0.0
    breakout_sell = 0.0
    if price > prior_high:
        breakout_buy += 45
        if price - prior_high >= 0.05 * a:
            breakout_buy += 20
    elif price > prior_high - 0.25 * a:
        breakout_buy += 15

    if price < prior_low:
        breakout_sell += 45
        if prior_low - price >= 0.05 * a:
            breakout_sell += 20
    elif price < prior_low + 0.25 * a:
        breakout_sell += 15

    # --- Pullback quality ---
    slope20 = _slope(close, 20)
    pullback_buy = 0.0
    pullback_sell = 0.0
    if htf_up and price <= float(ema20.iloc[-1]) + 0.40 * a:
        pullback_buy += 30
    if htf_up and prev < float(ema20.iloc[-2]) and price > prev:
        pullback_buy += 25
    if htf_down and price >= float(ema20.iloc[-1]) - 0.40 * a:
        pullback_sell += 30
    if htf_down and prev > float(ema20.iloc[-2]) and price < prev:
        pullback_sell += 25
    if slope20 > 0:
        pullback_buy += 10
    if slope20 < 0:
        pullback_sell += 10

    # --- Mean reversion component ---
    z = (price - m) / max(sd, 1e-9)
    mr_buy = 0.0
    mr_sell = 0.0
    if price <= lo:
        mr_buy += 42
    elif z < -1.0:
        mr_buy += 24
    if r <= V2_RSI_OVERSOLD:
        mr_buy += 30
    elif r < 42:
        mr_buy += 14
    if price >= u:
        mr_sell += 42
    elif z > 1.0:
        mr_sell += 24
    if r >= V2_RSI_OVERBOUGHT:
        mr_sell += 30
    elif r > 58:
        mr_sell += 14

    # --- Price-action confirmation ---
    candle_range = max(float(high.iloc[-1] - low.iloc[-1]), 1e-9)
    candle_body = abs(float(close.iloc[-1] - open_.iloc[-1]))
    close_location = (price - float(low.iloc[-1])) / candle_range
    pa_buy = 10 if price > prev else 0
    pa_sell = 10 if price < prev else 0
    if candle_body / candle_range >= 0.55:
        if close_location >= 0.65:
            pa_buy += 10
        if close_location <= 0.35:
            pa_sell += 10

    # Regime-dependent weighting. No component is a mandatory gate.
    weights = {
        "TREND":      {"trend": 0.30, "mom": 0.25, "breakout": 0.18, "pullback": 0.17, "mr": 0.05, "pa": 0.05},
        "BREAKOUT":   {"trend": 0.22, "mom": 0.25, "breakout": 0.28, "pullback": 0.10, "mr": 0.05, "pa": 0.10},
        "RANGE":      {"trend": 0.08, "mom": 0.10, "breakout": 0.05, "pullback": 0.12, "mr": 0.55, "pa": 0.10},
        "TRANSITION": {"trend": 0.18, "mom": 0.20, "breakout": 0.16, "pullback": 0.16, "mr": 0.20, "pa": 0.10},
    }[regime]

    buy = (
        trend_buy * weights["trend"]
        + momentum_buy * weights["mom"]
        + breakout_buy * weights["breakout"]
        + pullback_buy * weights["pullback"]
        + mr_buy * weights["mr"]
        + pa_buy * weights["pa"]
    )
    sell = (
        trend_sell * weights["trend"]
        + momentum_sell * weights["mom"]
        + breakout_sell * weights["breakout"]
        + pullback_sell * weights["pullback"]
        + mr_sell * weights["mr"]
        + pa_sell * weights["pa"]
    )

    # Volatility scaling changes conviction smoothly; it does not reject the setup.
    if vol_ratio >= 1.50:
        buy *= 0.90
        sell *= 0.90
    elif vol_ratio <= 0.75:
        buy *= 0.92
        sell *= 0.92

    # Prevent a single side from winning on a weak, directionless tape.
    directional_edge = abs(buy - sell)
    if directional_edge < 5:
        buy *= 0.94
        sell *= 0.94

    buy = _clip(buy)
    sell = _clip(sell)
    score = max(buy, sell)
    signal = None
    if score >= V2_MIN_SCORE and directional_edge >= 8:
        signal = "BUY" if buy > sell else "SELL"

    diag = {
        "strategy_id": V2_STRATEGY_ID,
        "regime": regime,
        "buy_score": round(buy, 2),
        "sell_score": round(sell, 2),
        "momentum_buy": round(momentum_buy, 2),
        "momentum_sell": round(momentum_sell, 2),
        "trend_buy": round(trend_buy, 2),
        "trend_sell": round(trend_sell, 2),
        "breakout_buy": round(breakout_buy, 2),
        "breakout_sell": round(breakout_sell, 2),
        "pullback_buy": round(pullback_buy, 2),
        "pullback_sell": round(pullback_sell, 2),
        "meanrev_buy": round(mr_buy, 2),
        "meanrev_sell": round(mr_sell, 2),
        "rsi": round(r, 2),
        "bb_z": round(float(z), 3),
        "vol_ratio": round(float(vol_ratio), 3),
        "htf_gap_atr": round(float(htf_gap), 3),
        "htf_up": bool(htf_up),
        "htf_down": bool(htf_down),
    }
    base.log(
        f"{epic}: V2 QUANT HYBRID | regime={regime} | BUY={buy:.1f} SELL={sell:.1f} "
        f"mom={momentum_buy:.0f}/{momentum_sell:.0f} RSI={r:.1f} BBz={z:.2f} "
        f"vol={vol_ratio:.2f} HTF={'UP' if htf_up else 'DOWN' if htf_down else 'MIXED'}"
    )
    return signal, score, diag


# V1's process_epic resolves quant_signal_score from bot.py's global namespace.
# This monkey patch is scoped to the V2 process only; V1 source remains unchanged.
base.quant_signal_score = quant_signal_score_v2
base.STRATEGY_ID = V2_STRATEGY_ID

# V2 has independent operational state but shares the single ownership registry
# so V1 and V2 do not adopt or manage each other's positions.
base.POSITION_OWNERSHIP_FILE = "strategy_positions.json"
base.STATE_FILE = "v2_trades_state.json"
base.OPEN_POSITIONS_FILE = "v2_open_positions.json"
base.SAFETY_STATE_FILE = "v2_bot_safety_state.json"
base.EXECUTION_QUALITY_FILE = "v2_execution_quality.json"
base.ENTRY_REJECTION_FILE = "v2_entry_rejections.json"


def run_cycle():
    base.log(
        f"STARTING {V2_STRATEGY_ID} | DEMO ONLY | "
        "regime-aware trend/momentum/breakout/pullback/mean-reversion ensemble"
    )
    return base.run_cycle()


if __name__ == "__main__":
    run_cycle()
