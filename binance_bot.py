import os
import time
import traceback

import pandas as pd

import config
from binance_futures_api import BinanceFuturesAPI
from bot import add_indicators, generate_signal, safe_float


BINANCE_ENABLED = str(os.getenv("BINANCE_ENABLED", "false")).lower() in {"1", "true", "yes"}
BINANCE_TRADING_ENABLED = str(os.getenv("BINANCE_TRADING_ENABLED", "false")).lower() in {"1", "true", "yes"}
BINANCE_TESTNET = str(os.getenv("BINANCE_TESTNET", "true")).lower() in {"1", "true", "yes"}

BINANCE_SYMBOLS = [
    s.strip().upper()
    for s in os.getenv("BINANCE_SYMBOLS", "BTCUSDT,ETHUSDT").split(",")
    if s.strip()
]

BINANCE_RISK_PER_TRADE = float(os.getenv("BINANCE_RISK_PER_TRADE") or "0.015")
BINANCE_MAX_POSITION_RISK = float(os.getenv("BINANCE_MAX_POSITION_RISK") or "0.04")
BINANCE_LEVERAGE = int(os.getenv("BINANCE_LEVERAGE") or "1")
BINANCE_SL_ATR_MULT = float(os.getenv("BINANCE_SL_ATR_MULT") or str(getattr(config, "SL_ATR_MULT", 1.5)))
BINANCE_TP_ATR_MULT = float(os.getenv("BINANCE_TP_ATR_MULT") or str(getattr(config, "TP_ATR_MULT", 3.0)))


def log(message):
    print(f"[BINANCE] {message}")


def klines_to_dataframe(raw):
    rows = []
    for k in raw:
        if len(k) < 6:
            continue
        rows.append({
            "time": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
        })
    return pd.DataFrame(rows)


def symbol_rules(exchange_info, symbol):
    for item in exchange_info.get("symbols", []):
        if item.get("symbol") != symbol:
            continue
        filters = {f.get("filterType"): f for f in item.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER", {})
        lot_filter = filters.get("LOT_SIZE", {})
        return {
            "tick_size": float(price_filter.get("tickSize", "0.01")),
            "min_qty": float(lot_filter.get("minQty", "0.001")),
            "step_size": float(lot_filter.get("stepSize", "0.001")),
        }
    raise RuntimeError(f"{symbol}: symbol not found in Binance Futures exchangeInfo")


def floor_step(value, step):
    if step <= 0:
        return value
    return (int(value / step + 1e-12)) * step


def round_tick(value, tick, direction):
    if tick <= 0:
        return value
    units = value / tick
    if direction == "down":
        return int(units + 1e-12) * tick
    return int(units + (1 - 1e-12)) * tick


def usdt_balance(api):
    balances = api.balance()
    for item in balances:
        if item.get("asset") == "USDT":
            return float(item.get("availableBalance") or item.get("balance") or 0.0)
    raise RuntimeError("USDT balance not found")


def current_position(api, symbol):
    for p in api.positions(symbol):
        if p.get("symbol") == symbol:
            amount = float(p.get("positionAmt", "0"))
            if abs(amount) > 0:
                return p
    return None


def spread_pct(api, symbol):
    ticker = api.book_ticker(symbol)
    bid = float(ticker.get("bidPrice", "0"))
    ask = float(ticker.get("askPrice", "0"))
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (ask - bid) / ((ask + bid) / 2.0) * 100.0


def process_symbol(api, symbol, exchange_info):
    log("=" * 56)
    log(f"PROCESSING {symbol}")

    if current_position(api, symbol):
        log(f"{symbol}: existing position detected; no new Binance entry in phase 1.")
        return

    spread = spread_pct(api, symbol)
    if spread is None or spread > 0.08:
        log(f"{symbol}: spread unavailable or too wide; entry blocked.")
        return

    df = klines_to_dataframe(api.klines(symbol, "15m", 300))
    htf = klines_to_dataframe(api.klines(symbol, "1h", 250))
    if len(df) < 220 or len(htf) < 205:
        log(f"{symbol}: insufficient candle history.")
        return

    df = add_indicators(df)
    htf = add_indicators(htf)

    signal = generate_signal(df, symbol, htf)
    if signal is None:
        log(f"{symbol}: no signal this cycle.")
        return

    mark = safe_float(api.mark_price(symbol).get("markPrice"))
    closed = df.iloc[-2]
    atr = safe_float(closed.get("atr"))
    if mark is None or atr is None or atr <= 0:
        log(f"{symbol}: invalid mark price/ATR.")
        return

    risk_distance = atr * BINANCE_SL_ATR_MULT
    if signal == "BUY":
        sl = mark - risk_distance
        tp = mark + atr * BINANCE_TP_ATR_MULT
        side = "BUY"
        exit_side = "SELL"
    else:
        sl = mark + risk_distance
        tp = mark - atr * BINANCE_TP_ATR_MULT
        side = "SELL"
        exit_side = "BUY"

    rules = symbol_rules(exchange_info, symbol)
    sl = round_tick(sl, rules["tick_size"], "down" if signal == "BUY" else "up")
    tp = round_tick(tp, rules["tick_size"], "up" if signal == "BUY" else "down")

    balance = usdt_balance(api)
    risk_amount = min(balance * BINANCE_RISK_PER_TRADE, balance * BINANCE_MAX_POSITION_RISK)
    quantity = floor_step(risk_amount / risk_distance, rules["step_size"])

    if quantity < rules["min_qty"]:
        log(f"{symbol}: calculated quantity below Binance minimum; skipped.")
        return

    log(
        f"{symbol}: SIGNAL={signal} | balance={balance:.2f} USDT | "
        f"risk={risk_amount:.2f} | qty={quantity} | mark={mark} | SL={sl} | TP={tp}"
    )

    if not BINANCE_TRADING_ENABLED:
        log(f"{symbol}: trading disabled; TESTNET analysis only.")
        return

    if not BINANCE_TESTNET:
        raise RuntimeError("Live Binance trading is disabled by design. Keep BINANCE_TESTNET=true.")

    api.set_leverage(symbol, BINANCE_LEVERAGE)
    entry = api.market_order(symbol, side, quantity)
    log(f"{symbol}: MARKET ORDER CONFIRMED = {entry}")

    try:
        stop = api.protective_algo_order(symbol, exit_side, "STOP_MARKET", sl)
        log(f"{symbol}: STOP LOSS CONFIRMED = {stop}")

        take = api.protective_algo_order(symbol, exit_side, "TAKE_PROFIT_MARKET", tp)
        log(f"{symbol}: TAKE PROFIT CONFIRMED = {take}")
    except Exception:
        log(f"{symbol}: protective order failed; attempting emergency reduce-only exit.")
        try:
            api.cancel_all_orders(symbol)
            api.market_order(symbol, exit_side, quantity, reduce_only=True)
        finally:
            raise


def run_cycle():
    if not BINANCE_ENABLED:
        log("Binance integration disabled. Set BINANCE_ENABLED=true to activate.")
        return

    api = BinanceFuturesAPI()
    if not api.enabled():
        log("Binance credentials are not configured in GitHub Secrets; skipping safely.")
        return

    log(f"Connected to Binance USD-M Futures | testnet={BINANCE_TESTNET} | trading_enabled={BINANCE_TRADING_ENABLED}")
    api.ping()
    exchange_info = api.exchange_info()

    for symbol in BINANCE_SYMBOLS:
        try:
            process_symbol(api, symbol, exchange_info)
        except Exception as exc:
            log(f"{symbol}: ERROR: {exc}")
            traceback.print_exc()
        time.sleep(1)

    log("Binance cycle completed.")


if __name__ == "__main__":
    run_cycle()
