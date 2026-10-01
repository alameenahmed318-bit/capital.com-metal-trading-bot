import os
import time
import json
import logging
from pathlib import Path

import requests

BASE = os.getenv("CAPITAL_BASE_URL", "https://demo-api-capital.backend-capital.com").rstrip("/")
API_KEY = os.environ["CAPITAL_API_KEY"]
IDENTIFIER = os.getenv("CAPITAL_IDENTIFIER") or os.environ["CAPITAL_EMAIL"]
PASSWORD = os.environ["CAPITAL_PASSWORD"]

SIZE = float(os.getenv("GOLD_TRADE_SIZE", os.getenv("TRADE_SIZE", "0.01")))
DRY_RUN = os.getenv("GOLD_DRY_RUN", "true").lower() == "true"
SCAN_SECONDS = int(os.getenv("GOLD_SCAN_SECONDS", "5"))
RUN_SECONDS = int(os.getenv("GOLD_RUN_SECONDS", "720"))
ADD_ON_MIN_MOVE_ATR = float(os.getenv("GOLD_ADD_ON_MIN_MOVE_ATR", "0.25"))

ATR_SL_MULT = 2.0
ATR_TP_MULT = 3.0
TRAIL_ACTIVATE_ATR = 0.5
TRAIL_DISTANCE_ATR = 1.0
EMA_FAST = 9
EMA_SLOW = 21
EMA_PULLBACK = 20
H4_EMA = 50

SESSION_FILTER = os.getenv("GOLD_SESSION_FILTER", "true").lower() == "true"
SESSION_START_UTC = 13
SESSION_END_UTC = 17

MAX_INITIAL_LOSS = float(os.getenv("GOLD_MAX_INITIAL_LOSS_AED", "10"))
PROTECT_TRIGGER = float(os.getenv("GOLD_PROTECT_TRIGGER_AED", "0.25"))
PROFIT_FLOOR = float(os.getenv("GOLD_PROFIT_FLOOR_AED", "0.05"))

STATE_FILE = Path("gold_bot_state.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [GOLD-NEW] %(levelname)s %(message)s")
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
            json={"identifier": IDENTIFIER, "password": PASSWORD, "encryptedPassword": False},
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
            raise RuntimeError(f"Capital {method} {path} failed ({r.status_code}): {r.text[:500]}")
        return r.json()

    def markets(self):
        return self.request("GET", "/api/v1/markets").get("markets", [])

    def prices(self, epic, resolution="MINUTE_15", n=250):
        return self.request("GET", f"/api/v1/prices/{epic}", params={"resolution": resolution, "max": n})

    def positions(self):
        return self.request("GET", "/api/v1/positions").get("positions", [])

    def open(self, epic, direction, size):
        return self.request(
            "POST", "/api/v1/positions",
            json={"epic": epic, "direction": direction, "size": size, "guaranteedStop": False},
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
        op, hi, lo, cl = row.get("openPrice", {}), row.get("highPrice", {}), row.get("lowPrice", {}), row.get("closePrice", {})
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


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs, prev = [], candles[-(period + 1)]["close"]
    for c in candles[-period:]:
        trs.append(max(c["high"] - c["low"], abs(c["high"] - prev), abs(c["low"] - prev)))
        prev = c["close"]
    return sum(trs) / period


def adx(candles, period=14):
    if len(candles) < period * 2 + 2:
        return None
    seg = candles[-(period * 2 + 1):]
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(seg)):
        cur, prev = seg[i], seg[i - 1]
        trs.append(max(cur["high"] - cur["low"], abs(cur["high"] - prev["close"]), abs(cur["low"] - prev["close"])))
        up, down = cur["high"] - prev["high"], prev["low"] - cur["low"]
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

    tr_s, p_s, m_s = wilder(trs), wilder(plus_dm), wilder(minus_dm)
    if not tr_s or not p_s or not m_s:
        return None
    dx = []
    for t, p, m in zip(tr_s, p_s, m_s):
        if t <= 0:
            dx.append(0.0)
            continue
        pdi, mdi = 100.0 * p / t, 100.0 * m / t
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
        if epic in {"XAUUSD", "GOLD"}:
            candidates.append(m)
    if not candidates:
        return None
    candidates.sort(key=lambda x: 0 if str(x.get("epic", "")).upper() == "XAUUSD" else 1)
    return candidates[0]


def owned_positions(positions, state, gold_epic):
    """
    Keep Gold ownership isolated by exact market epic.
    Also adopt any already-open Gold position that was created before the
    state file recorded it, so profit protection can manage it immediately.
    """
    out, live = {}, set()
    gold_epic = str(gold_epic).upper()

    for item in positions:
        pos = item.get("position", {}) or {}
        market = item.get("market", {}) or {}
        deal = pos.get("dealId")
        market_epic = str(market.get("epic", "")).upper()

        if not deal or market_epic != gold_epic:
            continue

        deal = str(deal)
        live.add(deal)

        if deal not in state["owned"]:
            direction = str(pos.get("direction", "")).upper()
            entry_price = position_level(item)
            if direction not in {"BUY", "SELL"}:
                log.warning("GOLD POSITION %s FOUND BUT DIRECTION IS UNKNOWN; NOT ADOPTING", deal)
                continue

            if entry_price is None:
                entry_price = 0.0

            state["owned"][deal] = {
                "epic": gold_epic,
                "direction": direction,
                "entry_price": entry_price,
                "peak_price": entry_price,
                "peak_upl": max(0.0, upl(item)),
                "atr_at_entry": None,
            }
            log.info(
                "ADOPTED EXISTING GOLD POSITION | %s | %s | %s | entry=%.2f | UPL=%.2f",
                deal, gold_epic, direction, entry_price, upl(item)
            )

        out[deal] = item

    # Remove state entries only after confirming they are no longer live Gold positions.
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


def signal(candles_m15, candles_h4):
    # Adaptive market structure: no fixed RSI/ADX entry gate.
    if len(candles_m15) < 80 or len(candles_h4) < 60:
        return None

    closes = [c["close"] for c in candles_m15]
    h4_closes = [c["close"] for c in candles_h4]
    last, prev = candles_m15[-1], candles_m15[-2]

    fast, slow = ema(closes, EMA_FAST), ema(closes, EMA_SLOW)
    pull, h4e, a = ema(closes, EMA_PULLBACK), ema(h4_closes, H4_EMA), atr(candles_m15)
    if None in (fast, slow, pull, h4e, a) or a <= 0:
        return None

    # Relative volatility threshold from recent ATR values.
    atr_samples = []
    for i in range(30, len(candles_m15) + 1):
        av = atr(candles_m15[:i], 14)
        if av is not None:
            atr_samples.append(av)
    if len(atr_samples) < 20:
        return None
    s = sorted(atr_samples[-80:])
    atr_ref = s[max(0, min(len(s) - 1, int((len(s) - 1) * 0.30)))]

    price = last["close"]
    recent_range = max(c["high"] for c in candles_m15[-6:]) - min(c["low"] for c in candles_m15[-6:])
    move = price - prev["close"]

    bullish = h4_closes[-1] >= h4e and fast > slow
    bearish = h4_closes[-1] <= h4e and fast < slow

    buy_reclaim = prev["low"] <= pull + 0.50 * a and price > pull and move > 0
    sell_reclaim = prev["high"] >= pull - 0.50 * a and price < pull and move < 0

    active_volatility = a >= atr_ref
    meaningful_move = abs(move) >= max(recent_range * 0.05, a * 0.03)

    if bullish and buy_reclaim and active_volatility and meaningful_move:
        return {"direction": "BUY", "atr": a, "entry": price, "reason": "H4 trend + M15 EMA pullback reclaim"}
    if bearish and sell_reclaim and active_volatility and meaningful_move:
        return {"direction": "SELL", "atr": a, "entry": price, "reason": "H4 trend + M15 EMA pullback reclaim"}
    return None


def in_session():
    return True


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
            entry["peak_price"] = max(float(entry.get("peak_price", open_price)), market_price)
            hard_stop = open_price - ATR_SL_MULT * a
            target = open_price + ATR_TP_MULT * a
            trail = entry["peak_price"] - TRAIL_DISTANCE_ATR * a
            should_close = market_price <= hard_stop or market_price >= target
            if market_price >= open_price + TRAIL_ACTIVATE_ATR * a:
                should_close = should_close or market_price <= trail
        else:
            entry["peak_price"] = min(float(entry.get("peak_price", open_price)), market_price)
            hard_stop = open_price + ATR_SL_MULT * a
            target = open_price - ATR_TP_MULT * a
            trail = entry["peak_price"] + TRAIL_DISTANCE_ATR * a
            should_close = market_price >= hard_stop or market_price <= target
            if market_price <= open_price - TRAIL_ACTIVATE_ATR * a:
                should_close = should_close or market_price >= trail

        if u <= -MAX_INITIAL_LOSS:
            should_close, reason = True, "ACCOUNT_LOSS_LIMIT"
        elif peak_upl >= PROTECT_TRIGGER and u <= PROFIT_FLOOR:
            should_close, reason = True, "PROFIT_FLOOR"
        elif should_close:
            reason = "ATR_SL_TP_OR_TRAIL"
        else:
            reason = None

        if reason:
            if DRY_RUN:
                log.info("DRY RUN | would CLOSE %s | reason=%s | UPL=%.2f | price=%.2f", deal, reason, u, market_price)
            else:
                api.close(deal)
                log.info("GOLD CLOSE | %s | reason=%s | UPL=%.2f", deal, reason, u)


def open_position(api, market, sig, state):
    epic, direction = market["epic"], sig["direction"]
    entry_price, a = float(sig["entry"]), float(sig["atr"])

    if DRY_RUN:
        log.info(
            "DRY RUN | would OPEN %s %s size=%.4f | ATR=%.2f | SL=%.2f | TP=%.2f",
            direction, epic, SIZE, a,
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
            for d in confirmed.get("affectedDeals", []):
                deal = d.get("dealId")
                if deal:
                    state["owned"][deal] = {
                        "epic": epic, "direction": direction, "entry_price": entry_price,
                        "peak_price": entry_price, "peak_upl": 0.0, "atr_at_entry": a,
                    }
                    log.info("OWNED NEW GOLD POSITION | %s | %s | %s", deal, epic, direction)
            if confirmed.get("affectedDeals"):
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
        "INDEPENDENT GOLD BOT | strategy=adaptive-MTF-trend-pullback | dry_run=%s | scan=%ss | size=%.4f",
        DRY_RUN, SCAN_SECONDS, SIZE,
    )

    while time.time() - started < RUN_SECONDS:
        try:
            market = find_gold(api.markets())
            if not market:
                log.warning("XAUUSD GOLD MARKET NOT FOUND / NOT TRADEABLE")
                time.sleep(SCAN_SECONDS)
                continue

            epic = market["epic"]
            m15 = parse_candles(api.prices(epic, "MINUTE_15", 250))
            h4 = parse_candles(api.prices(epic, "HOUR_4", 100))

            if len(m15) > 1:
                m15 = m15[:-1]
            if len(h4) > 1:
                h4 = h4[:-1]
            if len(m15) < 80 or len(h4) < 60:
                log.warning("%s | insufficient closed candles | M15=%d H4=%d", epic, len(m15), len(h4))
                time.sleep(SCAN_SECONDS)
                continue

            live_positions = api.positions()
            owned = owned_positions(live_positions, state, epic)

            current_price = float(market.get("offer") or market.get("bid") or m15[-1]["close"])
            protect(api, owned, state, current_price, m15)

            live_gold = [
                p for p in live_positions
                if str(p.get("market", {}).get("epic", "")).upper() == epic.upper()
            ]

            sig = signal(m15, h4)
            session_ok = in_session()

            log.info(
                "%s | signal=%s | session=%s | owned=%d | XAUUSD_positions=%d | price=%.2f",
                epic, sig["direction"] if sig else "NONE", session_ok, len(owned), len(live_gold), current_price,
            )

            # No fixed maximum number of Gold positions. The bot may scale in dynamically
            # when the market keeps producing a valid signal in the same direction and price
            # has moved enough from the most recent owned entry. Opposite-direction stacking
            # is avoided while positions are open.
            can_open = False
            if sig and session_ok:
                directions = {str(v.get("direction", "")).upper() for v in state["owned"].values()}
                if not directions:
                    can_open = not live_gold
                elif sig["direction"] in directions:
                    last_entries = [
                        float(v.get("entry_price")) for v in state["owned"].values()
                        if str(v.get("direction", "")).upper() == sig["direction"] and v.get("entry_price") is not None
                    ]
                    if last_entries:
                        nearest = min(last_entries, key=lambda x: abs(sig["entry"] - x))
                        can_open = abs(sig["entry"] - nearest) >= sig["atr"] * ADD_ON_MIN_MOVE_ATR
                # Never stack a new position in the opposite direction.
                if directions and sig["direction"] not in directions:
                    can_open = False

            if can_open:
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
