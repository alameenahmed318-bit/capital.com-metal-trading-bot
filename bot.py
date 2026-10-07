import os, time, json, logging
from pathlib import Path
import requests
import websocket

BASE = os.getenv("CAPITAL_BASE_URL", "https://demo-api-capital.backend-capital.com")
API_KEY = os.environ["CAPITAL_API_KEY"]
IDENTIFIER = os.getenv("CAPITAL_IDENTIFIER") or os.getenv("CAPITAL_EMAIL")
PASSWORD = os.environ["CAPITAL_PASSWORD"]
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# ============================================================
# GOLD 1-minute scalping strategy:
# Bollinger Bands + EMA(9/21) + RSI(7)
# BUY = lower-band pullback + bullish EMA alignment + RSI confirmation
# SELL = upper-band pullback + bearish EMA alignment + RSI confirmation
# ============================================================

EPIC = "GOLD"
SIZE = 0.01
TIMEFRAME_SECONDS = 60
BB_WINDOW = 20
BB_STD = 2.0
FAST_EMA = 9
SLOW_EMA = 21
RSI_PERIOD = 7
RSI_BUY_MAX = 45.0
RSI_SELL_MIN = 55.0
BAND_TOLERANCE = 0.0015

TARGET_PROFIT_PCT = 0.003   # +0.30%
STOP_LOSS_PCT = 0.005       # -0.50%
CHECK_SECONDS = 0.15
RUN_SECONDS = int(os.getenv("RUN_SECONDS", "330"))

STATE_FILE = Path("bot_state.json")
WS_URL = "wss://api-streaming-capital.backend-capital.com/connect"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gold_bollinger_scalper")


class Capital:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "X-CAP-API-KEY": API_KEY,
            "Content-Type": "application/json",
        })

    def session(self):
        r = self.s.post(
            BASE + "/api/v1/session",
            json={
                "identifier": IDENTIFIER,
                "password": PASSWORD,
                "encryptedPassword": False,
            },
            timeout=20,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"session {r.status_code}: {r.text[:300]}")
        self.s.headers["CST"] = r.headers["CST"]
        self.s.headers["X-SECURITY-TOKEN"] = r.headers["X-SECURITY-TOKEN"]

    def get(self, path):
        r = self.s.get(BASE + path, timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"GET {path} {r.status_code}: {r.text[:300]}")
        return r.json()

    def post(self, path, payload):
        r = self.s.post(BASE + path, json=payload, timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"POST {path} {r.status_code}: {r.text[:300]}")
        return r.json()

    def delete(self, path):
        r = self.s.delete(BASE + path, timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"DELETE {path} {r.status_code}: {r.text[:300]}")
        return r.json()

    def positions(self):
        return self.get("/api/v1/positions").get("positions", [])

    def markets(self):
        return self.get("/api/v1/markets").get("markets", [])

    def details(self):
        return self.get(f"/api/v1/markets/{EPIC}")

    def prices(self):
        # Capital.com 1-minute candles, equivalent to the user's 1m OHLCV idea.
        return self.get(
            f"/api/v1/prices/{EPIC}?resolution=MINUTE&max=50"
        )

    def confirm(self, ref):
        return self.get(f"/api/v1/confirms/{ref}")

    def open(self, direction, size):
        if DRY_RUN:
            return None
        return self.post("/api/v1/positions", {
            "epic": EPIC,
            "direction": direction,
            "size": size,
            "guaranteedStop": False,
        })

    def close(self, deal_id):
        if DRY_RUN:
            return {}
        return self.delete(f"/api/v1/positions/{deal_id}")


def gold_positions(api):
    return [
        x for x in api.positions()
        if str(x.get("market", {}).get("epic", "")).upper() == EPIC
    ]


def scalping_signal(api):
    data = api.prices()
    prices = data.get("prices", [])
    if len(prices) < SLOW_EMA + BB_WINDOW + 5:
        return None, None, None, None, None, None, None

    closes = []
    for candle in prices:
        close = candle.get("closePrice", candle.get("close", {}))
        if isinstance(close, dict):
            value = close.get("bid")
            if value is None:
                value = close.get("ask")
        else:
            value = close
        if value is not None:
            closes.append(float(value))

    need = max(BB_WINDOW, SLOW_EMA, RSI_PERIOD) + 2
    if len(closes) < need:
        return None, None, None, None, None, None

    # Use the latest available 1-minute close returned by Capital.com.
    last = closes[-1]

    # Bollinger Bands
    bb_window = closes[-BB_WINDOW:]
    mean = sum(bb_window) / BB_WINDOW
    variance = sum((x - mean) ** 2 for x in bb_window) / BB_WINDOW
    std = variance ** 0.5
    bb_high = mean + BB_STD * std
    bb_low = mean - BB_STD * std

    # EMA
    def ema(values, period):
        k = 2.0 / (period + 1.0)
        value = values[0]
        for x in values[1:]:
            value = x * k + value * (1.0 - k)
        return value

    fast = ema(closes[-(SLOW_EMA + 10):], FAST_EMA)
    slow = ema(closes[-(SLOW_EMA + 10):], SLOW_EMA)

    # RSI
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    recent = deltas[-RSI_PERIOD:]
    gains = sum(max(d, 0.0) for d in recent) / RSI_PERIOD
    losses = sum(max(-d, 0.0) for d in recent) / RSI_PERIOD
    if losses == 0:
        rsi = 100.0 if gains > 0 else 50.0
    else:
        rs = gains / losses
        rsi = 100.0 - (100.0 / (1.0 + rs))

    # Slight tolerance around the bands keeps the strategy from being too
    # restrictive while still requiring price to be near an extreme.
    near_low = last <= bb_low * (1.0 + BAND_TOLERANCE)
    near_high = last >= bb_high * (1.0 - BAND_TOLERANCE)

    buy = near_low and fast >= slow and rsi <= RSI_BUY_MAX
    sell = near_high and fast <= slow and rsi >= RSI_SELL_MIN

    if buy:
        return "BUY", last, bb_low, bb_high, fast, slow, rsi
    if sell:
        return "SELL", last, bb_low, bb_high, fast, slow, rsi

    return None, last, bb_low, bb_high, fast, slow, rsi

def get_live_upl(position):
    return float(position.get("position", {}).get("upl", 0) or 0)


def close_confirmed(api, deal_id, reason):
    try:
        result = api.close(deal_id)
        ref = result.get("dealReference") if isinstance(result, dict) else None

        if ref:
            deadline = time.time() + 5
            while time.time() < deadline:
                c = api.confirm(ref)
                status = str(c.get("dealStatus", "")).upper()
                log.info(
                    "CLOSE CONFIRM | deal=%s | status=%s | reason=%s",
                    deal_id, status, reason
                )
                if status in {"REJECTED", "CANCELLED", "ERROR"}:
                    log.error("CLOSE REJECTED | deal=%s | raw=%s", deal_id, c)
                    return False
                if status in {"ACCEPTED", "FILLED"}:
                    break
                time.sleep(0.25)

        log.info("POSITION CLOSED | GOLD | deal=%s | reason=%s", deal_id, reason)
        return True
    except Exception as e:
        log.error("CLOSE ERROR | GOLD | deal=%s | %s", deal_id, e)
        return False


def open_confirmed(api, direction, size):
    try:
        result = api.open(direction, size)

        if DRY_RUN:
            log.info("DRY RUN ENTRY | GOLD | %s | size=%.4f", direction, size)
            return True

        ref = result.get("dealReference")
        if not ref:
            log.error("ORDER REJECTED | GOLD | %s | no dealReference | %s", direction, result)
            return False

        deadline = time.time() + 5
        while time.time() < deadline:
            c = api.confirm(ref)
            status = str(c.get("dealStatus", "")).upper()
            affected = c.get("affectedDeals") or []
            log.info(
                "OPEN CONFIRM | ref=%s | direction=%s | status=%s | affected=%d",
                ref, direction, status, len(affected)
            )

            if status in {"REJECTED", "CANCELLED", "ERROR"}:
                log.error("%s REJECTED | GOLD | raw=%s", direction, c)
                return False

            if affected:
                return True

            time.sleep(0.25)

        # Last check in case the position appeared just after confirmation.
        if gold_positions(api):
            return True

        log.error("OPEN UNCONFIRMED | GOLD | %s | ref=%s", direction, ref)
        return False

    except Exception as e:
        log.error("OPEN ERROR | GOLD | %s | %s", direction, e)
        return False


def run():
    api = Capital()
    api.session()

    tradeable = [
        m for m in api.markets()
        if str(m.get("epic", "")).upper() == EPIC
        and str(m.get("marketStatus", "")).upper() == "TRADEABLE"
    ]
    if not tradeable:
        raise RuntimeError("GOLD is not tradeable")

    log.info(
        "GOLD BB+EMA+RSI SCALPER | DRY_RUN=%s | "
        "TP=+0.30%% | SL=-0.50%% | CHECK=0.15s | SIZE=0.01",
        DRY_RUN
    )

    stop = time.time() + RUN_SECONDS

    while time.time() < stop:
        try:
            positions = gold_positions(api)

            # The supplied strategy has a single in_position state.
            # Therefore only one GOLD position is allowed at a time.
            if positions:
                position = positions[0]
                p = position.get("position", {})
                deal_id = p.get("dealId")
                direction = str(p.get("direction", "BUY")).upper()
                entry_price = float(
                    p.get("level")
                    or p.get("openLevel")
                    or p.get("openPrice")
                    or 0
                )
                upl = get_live_upl(position)

                if entry_price > 0:
                    live_price = entry_price
                    try:
                        quote = api.prices().get("prices", [])[-1]
                        close = quote.get("closePrice", quote.get("close", {}))
                        if isinstance(close, dict):
                            live_price = float(
                                close.get("bid")
                                or close.get("ask")
                                or entry_price
                            )
                        else:
                            live_price = float(close or entry_price)
                    except Exception:
                        pass

                    if direction == "SELL":
                        take_profit = entry_price * (1 - TARGET_PROFIT_PCT)
                        stop_loss = entry_price * (1 + STOP_LOSS_PCT)
                    else:
                        take_profit = entry_price * (1 + TARGET_PROFIT_PCT)
                        stop_loss = entry_price * (1 - STOP_LOSS_PCT)

                    log.info(
                        "POSITION | GOLD | %s | entry=%.5f | price=%.5f | "
                        "upl=%+.2f AED | TP=%.5f | SL=%.5f",
                        direction, entry_price, live_price, upl,
                        take_profit, stop_loss
                    )

                    hit_tp = (
                        live_price <= take_profit if direction == "SELL"
                        else live_price >= take_profit
                    )
                    hit_sl = (
                        live_price >= stop_loss if direction == "SELL"
                        else live_price <= stop_loss
                    )

                    if hit_tp:
                        close_confirmed(
                            api, deal_id,
                            f"TAKE_PROFIT {direction} +0.30% price={live_price:.5f}"
                        )
                    elif hit_sl:
                        close_confirmed(
                            api, deal_id,
                            f"STOP_LOSS {direction} -0.50% price={live_price:.5f}"
                        )

            else:
                signal, last, bb_low, bb_high, fast, slow, rsi = scalping_signal(api)

                if last is not None:
                    log.info(
                        "SIGNAL | GOLD | price=%.5f | BB_LOW=%.5f | BB_HIGH=%.5f | "
                        "EMA9=%.5f | EMA21=%.5f | RSI7=%.2f | signal=%s",
                        last, bb_low, bb_high, fast,
                        slow, rsi, signal or "NONE"
                    )

                if signal in {"BUY", "SELL"}:
                    log.info(
                        "ENTRY | GOLD | %s | size=%.4f | "
                        "Bollinger + EMA9/21 + RSI7",
                        signal, SIZE
                    )
                    open_confirmed(api, signal, SIZE)
            time.sleep(CHECK_SECONDS)

        except Exception as e:
            log.error("BOT ERROR | GOLD | %s", e)
            time.sleep(20)

            try:
                api.session()
            except Exception as session_error:
                log.error("SESSION REFRESH ERROR | %s", session_error)

    log.info("GOLD BB+EMA+RSI RUN COMPLETE")


if __name__ == "__main__":
    run()
