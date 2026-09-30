"""Shared fast market strategy for the two Capital.com AI bots.

Design:
- Fast signal discovery from current completed candle + live quote.
- M1 for FX, M5 for Metals; H1 confirms broader direction.
- Uses trend, momentum, ATR and candle/structure quality without a hard score gate.
- Rejects only clearly dangerous opposing wicks / sharp reversal candles.
- AI remains advisory; strategy signal is the entry authority.
- No fixed take-profit. Broker SL is the base loss barrier; profit protection is
  handled by the fast position-management loop in bot.py.
"""

import numpy as np
import pandas as pd
import bot as base


def _num(v, default=None):
    x = base.safe_float(v)
    return default if x is None else float(x)


def _completed(df):
    if df is None or len(df) < 30:
        return None
    return df.iloc[-2]


def _atr(df):
    d = base.add_indicators(df.copy())
    if d is None or len(d) < 30:
        return d, None
    return d, _num(d.iloc[-2].get("atr"))


def _htf_alignment(htf_df, direction):
    if htf_df is None or len(htf_df) < 30:
        return False
    h = htf_df.copy()
    close = pd.to_numeric(h["close"], errors="coerce")
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-2]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-2]
    last = _num(h.iloc[-2].get("close"))
    if last is None:
        return False
    return (last > ema20 and ema20 > ema50) if direction == "BUY" else (last < ema20 and ema20 < ema50)


def _wick_reversal(candle, direction):
    o = _num(candle.get("open"))
    h = _num(candle.get("high"))
    l = _num(candle.get("low"))
    c = _num(candle.get("close"))
    if None in (o, h, l, c) or h <= l:
        return True
    rng = h - l
    upper = h - max(o, c)
    lower = min(o, c) - l
    body = abs(c - o)

    # Only reject an unusually dominant wick opposing the intended direction.
    # Normal pullbacks remain tradable.
    if direction == "BUY":
        return upper / rng >= 0.68 and body / rng <= 0.35
    return lower / rng >= 0.68 and body / rng <= 0.35


def _momentum(df, direction, atr):
    if len(df) < 25 or not atr or atr <= 0:
        return False
    cur = df.iloc[-2]
    prev = df.iloc[-3]
    close = _num(cur.get("close"))
    prev_close = _num(prev.get("close"))
    old = _num(df.iloc[-7].get("close"))
    if None in (close, prev_close, old):
        return False
    move = close - old
    one_bar = close - prev_close
    return (move > 0 and one_bar >= -0.20 * atr) if direction == "BUY" else (move < 0 and one_bar <= 0.20 * atr)


def _structure(df, direction):
    if len(df) < 22:
        return False
    cur = df.iloc[-2]
    prior = df.iloc[-22:-2]
    close = _num(cur.get("close"))
    if close is None:
        return False
    high = _num(prior["high"].max())
    low = _num(prior["low"].min())
    return close > high if direction == "BUY" else close < low


def fast_signal(df, epic, htf_df=None, live_quote=None):
    if df is None or df.empty or len(df) < 30:
        return None

    d, atr = _atr(df)
    if d is None or atr is None or atr <= 0:
        return None

    cur = d.iloc[-2]
    close = _num(cur.get("close"))
    ema9 = _num(d["close"].ewm(span=9, adjust=False).mean().iloc[-2])
    ema21 = _num(d["close"].ewm(span=21, adjust=False).mean().iloc[-2])
    rsi = _num(cur.get("rsi"), 50.0)
    if None in (close, ema9, ema21):
        return None

    # Live quote is deliberately used as an execution/freshness input.
    # The completed candle supplies the stable signal context.
    live = _num((live_quote or {}).get("bid")) if isinstance(live_quote, dict) else None
    if live is None:
        live = close

    directions = []
    if ema9 > ema21:
        directions.append("BUY")
    elif ema9 < ema21:
        directions.append("SELL")

    if not directions:
        return None

    direction = directions[0]
    candle_green = _num(cur.get("close")) > _num(cur.get("open"))
    candle_red = _num(cur.get("close")) < _num(cur.get("open"))
    candle_agrees = candle_green if direction == "BUY" else candle_red
    htf_ok = _htf_alignment(htf_df, direction)
    mom_ok = _momentum(d, direction, atr)
    structure_ok = _structure(d, direction)
    wick_bad = _wick_reversal(cur, direction)

    # Minimal confirmation:
    # trend + (momentum OR structure) + (candle OR HTF).
    # HTF disagreement is not an automatic veto when local momentum/structure
    # is strong; this keeps the strategy responsive.
    quality_votes = int(mom_ok) + int(structure_ok) + int(candle_agrees) + int(htf_ok)
    valid = quality_votes >= 2 and (mom_ok or structure_ok) and not wick_bad

    # Avoid entering after an extreme one-bar extension.
    prev_close = _num(d.iloc[-3].get("close"))
    stretched = prev_close is not None and abs(close - prev_close) > 1.8 * atr
    if stretched and quality_votes < 3:
        valid = False

    if not valid:
        return None

    base.log(
        f"{epic}: FAST MARKET SIGNAL | {direction} | "
        f"EMA9={ema9:.6f} EMA21={ema21:.6f} ATR={atr:.6f} RSI={rsi:.1f} "
        f"momentum={mom_ok} structure={structure_ok} candle={candle_agrees} "
        f"HTF={htf_ok} votes={quality_votes} live={live}"
    )
    return direction


def calculate_trade(df, direction, entry_price=None, strength=1.0, epic=None, ai_decision=None):
    """Volatility SL only; TP is intentionally absent."""
    if df is None or len(df) < 3 or direction not in {"BUY", "SELL"}:
        return None
    d = base.add_indicators(df.copy())
    current = d.iloc[-2]
    atr = _num(current.get("atr"))
    price = _num(entry_price if entry_price is not None else current.get("close"))
    if price is None or atr is None or atr <= 0:
        return None

    sl_distance = 1.50 * atr
    stop_level = price - sl_distance if direction == "BUY" else price + sl_distance
    return {
        "entry": price,
        "stop_level": stop_level,
        "profit_level": None,
        "risk_distance": sl_distance,
        "atr": atr,
        "signal_strength": strength,
    }
