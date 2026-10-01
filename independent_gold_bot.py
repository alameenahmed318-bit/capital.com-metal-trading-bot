import os
import time
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

import requests

BASE = os.getenv("CAPITAL_BASE_URL", "https://demo-api-capital.backend-capital.com").rstrip("/")
API_KEY = os.environ["CAPITAL_API_KEY"]
IDENTIFIER = os.getenv("CAPITAL_IDENTIFIER") or os.environ["CAPITAL_EMAIL"]
PASSWORD = os.environ["CAPITAL_PASSWORD"]

SIZE = float(os.getenv("GOLD_TRADE_SIZE", os.getenv("TRADE_SIZE", "0.01")))
DRY_RUN = os.getenv("GOLD_DRY_RUN", "true").lower() == "true"
SCAN_SECONDS = int(os.getenv("GOLD_SCAN_SECONDS", "5"))
RUN_SECONDS = int(os.getenv("GOLD_RUN_SECONDS", "720"))
MAX_POSITIONS = int(os.getenv("GOLD_MAX_POSITIONS", "1"))

# Strategy adapted from public GitHub XAUUSD research:
# MTF trend + RSI + ADX + ATR + pullback/structure + trailing protection.
# The source reports a backtest, but those results are not independently verified.
RSI_BUY_MIN = 50.0
RSI_BUY_MAX = 65.0
RSI_SELL_MIN = 35.0
RSI_SELL_MAX = 50.0
ADX_MIN = 30.0
ATR_SL_MULT = 2.0
ATR_TP_MULT = 3.0
TRAIL_ACTIVATE_ATR = 0.5
TRAIL_DISTANCE_ATR = 1.0
EMA_FAST = 9
EMA_SLOW = 21
EMA_PULLBACK = 20
H4_EMA = 50
STRUCTURE_LOOKBACK = 8

# Keep the high-liquidity session used by the researched strategy.
SESSION_FILTER = os.getenv("GOLD_SESSION_FILTER", "true").lower() == "true"
SESSION_START_UTC = 13
SESSION_END_UTC = 17

# Account-currency protection requested for this bot.
MAX_INITIAL_LOSS = float(os.getenv("GOLD_MAX_INITIAL_LOSS_AED", "10"))
PROTECT_TRIGGER = float(os.getenv("GOLD_PROTECT_TRIGGER_AED", "0.25"))
PROFIT_FLOOR = float(os.getenv("GOLD_PROFIT_FLOOR_AED", "0.05"))

STATE_FILE = Path("gold_bot_state.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [GOLD-NEW] %(levelname)s %(message)s",
)
log = logging.getLogger("gold_new")


class Capital:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "X-CAP-API-KEY": API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
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
        r.raise_for_status()
        self.s.headers.update({
            "CST": r.headers.get("CST"),
            "X-SECURITY-TOKEN": r.headers.get("X-SECURITY-TOKEN"),
        })

    def request(self, method, path, **kwargs):
        r = self.s.request(method, BASE + path, timeout=20, **kwargs)
        if r.status_code >= 400:
            raise RuntimeError(
                f"Capital {method} {path} failed ({r.status_code}): {r.text[:500]}"
            )
        return r.json()

    def markets(self):
        return self.request("GET", "/api/v1/markets").get("markets", [])

    def prices(self, epic, resolution="MINUTE_15", n=250):
        return self.request(
            "GET",
            f"/api/v1/prices/{epic}",
            params={"resolution": resolution, "max": n},
        )

    def positions(self):
        return self.request("GET", "/api/v1/positions").get("positions", [])

    def open(self, epic, direction, size):
        return self.request(
            "POST",
            "/api/v1/positions",
            json={
                "epic": epic,
                "direction": direction,
                "size": size,
                "guaranteedStop": False,
            },
        )

    def confirm(self, ref):
        return self.request("GET", f"/api/v1/confirms/{ref}")

    def close(self, deal_id):
        return self.request("DELETE", f"/api/v1/positions/{deal_id}")


def load_state():
    try:
        data = json.loads(STATE_FILE.read_text())
        return data if isinstance(data, dict) else {"owned": {}}
    except Exception:
        return {"owned": {}}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE_FILE)


def parse_candles(raw):
    candles = []
    for row in raw.get("prices", []):
        op = row.get("openPrice", {})
        hi = row.get("highPrice", {})
        lo = row.get("lowPrice", {})
        cl = row.get("closePrice", {})
        try:
            o = (float(op["bid"]) + float(op["ask"])) / 2.0
            h = (float(hi["bid"]) + float(hi["ask"])) / 2.0
            l = (float(lo["bid"]) + float(lo["ask"])) / 2.0
            c = (float(cl["bid"]) + float(cl["ask"])) / 2.0
        except (KeyError, TypeError, ValueError):
            continue
        candles.append({"open": o, "high": h, "low": l, "close": c})
    return candles


def ema(values, period):
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1.0 - k)
    return e


def rsi(values, period=14):
    if len(values) < period + 1:
        return None
    gains = []
    losses = []
    for a, b in zip(values[-(period + 1):-1], values[-period:]):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    prev = candles[-(period + 1)]["close"]
    for c in candles[-period:]:
        trs.append(max(
            c["high"] - c["low"],
            abs(c["high"] - prev),
            abs(c["low"] - prev),
        ))
        prev = c["close"]
    return sum(trs) / period


def adx(candles, period=14):
    if len(candles) < period * 2 + 2:
        return None
    start = len(candles) - (period * 2 + 1)
    segment = candles[start:]
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(segment)):
        cur, prev = segment[i], segment[i - 1]
        trs.append(max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        ))
        up = cur["high"] - prev["high"]
        down = prev["low"] - cur["low"]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)

    def wilder(seq):
        if len(seq) < period:
            return None
        value = sum(seq[:period]) / period
        out = [value]
        for x in seq[period:]:
            value = ((period - 1) * value + x) / period
            out.append(value)
        return out

    tr_s = wilder(trs)
    p_s = wilder(plus_dm)
    m_s = wilder(minus_dm)
    if not tr_s or not p_s or not m_s:
        return None

    dx = []
    for t, p, m in zip(tr_s, p_s, m_s):
        if t <= 0:
            dx.append(0.0)
            continue
        pdi = 100.0 * p / t
        mdi = 100.0 * m / t
        denom = pdi + mdi
        dx.append(0.0 if denom == 0 else 100.0 * abs(pdi - mdi) / denom)

    d = wilder(dx)
    return d[-1] if d else None


def find_gold(markets):
    candidates = []
    for m in markets:
        if str(m.get("marketStatus", "")).upper() != "TRADEABLE":
            continue
        epic = str(m.get("epic", "")).upper()
        name = str(m.get("instrumentName", m.get("name", ""))).upper()
        if (
            epic in {"GOLD", "XAUUSD"}
            or "GOLD" in name
            or "XAU/USD" in name
            or "XAUUSD" in name
        ):
            candidates.append(m)
    if not candidates:
        return None
    candidates.sort(key=lambda x: (
        0 if str(x.get("epic", "")).upper() == "XAUUSD" else 1,
        str(x.get("epic", "")),
    ))
    return candidates[0]


def owned_positions(positions, state):
    out = {}
    live = set()
    for item in positions:
        deal = item.get("position", {}).get("dealId")
        if deal and deal in state["owned"]:
            out[deal] = item
            live.add(deal)

    for deal in list(state["owned"]):
        if deal not in live:
            state["owned"].pop(deal, None)
    return out


def upl(item):
    try:
        return float(item.get("position", {}).get("upl", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def position_level(item):
    pos = item.get("position", {})
    for key in ("level", "openLevel", "openPrice"):
        try:
            if pos.get(key) is not None:
                return float(pos[key])
        except (TypeError, ValueError):
            pass
    return None


def adaptive_percentile(values, q):
    if len(values) < 20:
        return None
    s = sorted(values[-min(len(values), 80):])
    idx = max(0, min(len(s) - 1, int((len(s) - 1) * q)))
    return s[idx]


def signal(candles_m15, candles_h4):
    # Market-adaptive entry: no fixed RSI/ADX entry gates.
    # The bot compares the current market state with its own recent distribution.
    if len(candles_m15) < 80 or len(candles_h4) < 60:
        return None

    closes = [c["close"] for c in candles_m15]
    h4_closes = [c["close"] for c in candles_h4]
    last = candles_m15[-1]
    prev = candles_m15[-2]

    fast = ema(closes, EMA_FAST)
    slow = ema(closes, EMA_SLOW)
    pull = ema(closes, EMA_PULLBACK)
    h4_ema = ema(h4_closes, H4_EMA)
    a = atr(candles_m15, 14)
    adx_v = adx(candles_m15, 14)
    r = rsi(closes, 14)

    if None in (fast, slow, pull, h4_ema, a, adx_v, r) or a <= 0:
        return None

    # Build adaptive market context from recent closed candles.
    atr_samples = []
    adx_samples = []
    rsi_samples = []
    for i in range(30, len(candles_m15) + 1):
        window = candles_m15[:i]
        av = atr(window, 14)
        xv = adx(window, 14)
        rv = rsi([x["close"] for x in window], 14)
        if av is not None:
            atr_samples.append(av)
        if xv is not None:
            adx_samples.append(xv)
        if rv is not None:
            rsi_samples.append(rv)

    atr_high = adaptive_percentile(atr_samples, 0.35)
    adx_mid = adaptive_percentile(adx_samples, 0.50)
    rsi_low = adaptive_percentile(rsi_samples, 0.25)
    rsi_high = adaptive_percentile(rsi_samples, 0.75)

    if None in (atr_high, adx_mid, rsi_low, rsi_high):
        return None

    price = last["close"]
    recent_range = max(c["high"] for c in candles_m15[-6:]) - min(c["low"] for c in candles_m15[-6:])
    move = price - prev["close"]

    bullish = h4_closes[-1] >= h4_ema and fast > slow
    bearish = h4_closes[-1] <= h4_ema and fast < slow

    # Pullback is measured relative to current ATR, not a fixed price distance.
    buy_reclaim = prev["low"] <= pull + 0.35 * a and price > pull and move > 0
    sell_reclaim = prev["high"] >= pull - 0.35 * a and price < pull and move < 0

    # Market-state gates adapt to the current instrument's own history:
    # enough movement relative to recent volatility, and trend strength above
    # its own recent median rather than a hard ADX number.
    active_volatility = a >= atr_high
    directional = adx_v >= adx_mid

    # RSI is contextual only: it must point in the direction of the move,
    # without fixed 50/65/35 thresholds.
    rsi_buy_context = r >= rsi_low and r >= 50.0
    rsi_sell_context = r <= rsi_high and r <= 50.0

    # Avoid entries when the latest move is tiny compared with the current range.
    meaningful_move = abs(move) >= max(recent_range * 0.08, a * 0.05)

    buy = bullish and buy_reclaim and directional and active_volatility and rsi_buy_context and meaningful_move
    sell = bearish and sell_reclaim and directional and active_volatility and rsi_sell_context and meaningful_move

    if buy:
        return {
            "direction": "BUY",
            "atr": a,
            "entry": price,
            "reason": "adaptive H4 trend + M15 pullback + relative volatility/trend context",
        }

    if sell:
        return {
            "direction": "SELL",
            "atr": a,
            "entry": price,
            "reason": "adaptive H4 trend + M15 pullback + relative volatility/trend context",
        }

    return None

def in_session():
    if not SESSION_FILTER:
        return True
    hour = datetime.now(timezone.utc).hour
    return SESSION_START_UTC <= hour < SESSION_END_UTC


def protect(api, owned, state, market_price, candles):
    a = atr(candles, 14)
    if a is None:
        return

    for deal, item in owned.items():
        u = upl(item)
        entry = state["owned"][deal]
        peak_upl = max(float(entry.get("peak_upl", 0.0)), u)
        entry["peak_upl"] = peak_upl

        direction = entry.get("direction")
        open_price = position_level(item) or float(entry.get("entry_price", market_price))

        if direction == "BUY":
            hard_stop = open_price - ATR_SL_MULT * a
            target = open_price + ATR_TP_MULT * a
            trail = (
                entry.get("peak_price", open_price) - TRAIL_DISTANCE_ATR * a
                if entry.get("peak_price") is not None
                else None
            )
            should_close_price = market_price <= hard_stop or market_price >= target
            if trail is not None and market_price >= open_price + TRAIL_ACTIVATE_ATR * a:
                should_close_price = should_close_price or market_price <= trail
        else:
            hard_stop = open_price + ATR_SL_MULT * a
            target = open_price - ATR_TP_MULT * a
            trail = (
                entry.get("peak_price", open_price) + TRAIL_DISTANCE_ATR * a
                if entry.get("peak_price") is not None
                else None
            )
            should_close_price = market_price >= hard_stop or market_price <= target
            if trail is not None and market_price <= open_price - TRAIL_ACTIVATE_ATR * a:
                should_close_price = should_close_price or market_price >= trail

        if direction == "BUY":
            entry["peak_price"] = max(float(entry.get("peak_price", open_price)), market_price)
        else:
            entry["peak_price"] = min(float(entry.get("peak_price", open_price)), market_price)

        # Account-currency hard loss protection.
        if u <= -MAX_INITIAL_LOSS:
            should_close_price = True
            reason = "ACCOUNT_LOSS_LIMIT"
        # Once profit is armed, do not allow the protected trade to cross
        # back through the configured profit floor.
        elif peak_upl >= PROTECT_TRIGGER and u <= PROFIT_FLOOR:
            should_close_price = True
            reason = "PROFIT_FLOOR"
        elif should_close_price:
            reason = "ATR_SL_TP_OR_TRAIL"
        else:
            reason = None

        if reason:
            if DRY_RUN:
                log.info(
                    "DRY RUN | would CLOSE %s | reason=%s | UPL=%.2f | price=%.2f",
                    deal, reason, u, market_price,
                )
            else:
                api.close(deal)
                log.info(
                    "GOLD CLOSE | %s | reason=%s | UPL=%.2f",
                    deal, reason, u,
                )


def open_position(api, market, sig, state):
    epic = market["epic"]
    direction = sig["direction"]
    entry_price = float(sig["entry"])
    a = float(sig["atr"])

    if DRY_RUN:
        log.info(
            "DRY RUN | would OPEN %s %s size=%.4f | ATR=%.2f | SL=%.2f | TP=%.2f",
            direction,
            epic,
            SIZE,
            a,
            entry_price - ATR_SL_MULT * a if direction == "BUY" else entry_price + ATR_SL_MULT * a,
            entry_price + ATR_TP_MULT * a if direction == "BUY" else entry_price - ATR_TP_MULT * a,
        )
        return

    result = api.open(epic, direction, SIZE)
    ref = result.get("dealReference")
    if not ref:
        log.warning("OPEN returned no dealReference: %s", result)
        return

    for _ in range(6):
        try:
            confirmed = api.confirm(ref)
            affected = confirmed.get("affectedDeals", [])
            if affected:
                for d in affected:
                    deal = d.get("dealId")
                    if deal:
                        state["owned"][deal] = {
                            "epic": epic,
                            "direction": direction,
                            "entry_price": entry_price,
                            "peak_price": entry_price,
                            "peak_upl": 0.0,
                            "atr_at_entry": a,
                        }
                        log.info(
                            "OWNED NEW GOLD POSITION | %s | %s | %s",
                            deal, epic, direction,
                        )
                return
        except Exception:
            time.sleep(0.5)

    log.warning("Could not confirm deal reference %s", ref)


def run():
    api = Capital()
    api.session()
    state = load_state()
    started = time.time()

    log.info(
        "INDEPENDENT GOLD BOT | strategy=MTF-ADX-RSI-ATR-PULLBACK | "
        "dry_run=%s | scan=%ss | size=%.4f",
        DRY_RUN, SCAN_SECONDS, SIZE,
    )

    while time.time() - started < RUN_SECONDS:
        try:
            market = find_gold(api.markets())
            if not market:
                log.warning("GOLD MARKET NOT FOUND / NOT TRADEABLE")
                time.sleep(SCAN_SECONDS)
                continue

            epic = market["epic"]

            raw_m15 = api.prices(epic, "MINUTE_15", 250)
            raw_h4 = api.prices(epic, "HOUR_4", 100)

            m15 = parse_candles(raw_m15)
            h4 = parse_candles(raw_h4)

            # Never use the currently forming candle.
            if len(m15) > 1:
                m15 = m15[:-1]
            if len(h4) > 1:
                h4 = h4[:-1]

            live_positions = api.positions()
            owned = owned_positions(live_positions, state)

            current_price = float(market.get("offer") or market.get("bid") or m15[-1]["close"])
            protect(api, owned, state, current_price, m15)

            # This bot can ONLY manage its own deal IDs.
            live_gold = [
                p for p in live_positions
                if str(p.get("market", {}).get("epic", "")).upper() == epic.upper()
            ]

            sig = signal(m15, h4)
            session_ok = in_session()

            log.info(
                "%s | signal=%s | session=%s | owned=%d | all_gold_positions=%d | price=%.2f",
                epic,
                sig["direction"] if sig else "NONE",
                session_ok,
                len(owned),
                len(live_gold),
                current_price,
            )

            if (
                not owned
                and not live_gold
                and sig
                and session_ok
                and len(live_gold) < MAX_POSITIONS
            ):
                open_position(api, market, sig, state)

            save_state(state)
            time.sleep(SCAN_SECONDS)

        except Exception as exc:
            log.exception("GOLD BOT CYCLE FAILED | %s", exc)
            time.sleep(5)
            try:
                api.session()
            except Exception:
                pass

    save_state(state)
    log.info("INDEPENDENT GOLD BOT FINISHED")


if __name__ == "__main__":
    run()
