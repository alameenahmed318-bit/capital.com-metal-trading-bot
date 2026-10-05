import os
import time
import json
import logging
from collections import deque
from pathlib import Path

import requests

BASE = os.getenv("CAPITAL_BASE_URL", "https://demo-api-capital.backend-capital.com")
API_KEY = os.environ["CAPITAL_API_KEY"]
IDENTIFIER = os.environ.get("CAPITAL_IDENTIFIER") or os.environ["CAPITAL_EMAIL"]
PASSWORD = os.environ["CAPITAL_PASSWORD"]

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# GOLD-ONLY MICRO SCALPER
EPIC_ALLOWLIST = {"GOLD", "XAUUSD"}
DESIRED_LOTS = 0.01  # user-facing GOLD lot size
SCAN_SECONDS = 1.0  # fixed fast scan
RUN_SECONDS = int(os.getenv("RUN_SECONDS", "240"))
MAX_POSITIONS = 20
ENTRY_COOLDOWN_SECONDS = 1.0
MAX_INITIAL_LOSS_AED = 10.0
PROFIT_TRIGGER_AED = 0.05
PROFIT_FLOOR_AED = 0.05
PRICE_RESOLUTION = "MINUTE"
PRICE_HISTORY = 2

STATE_FILE = Path("bot_state.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gold_micro_scalper")


class Capital:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "X-CAP-API-KEY": API_KEY,
            "Content-Type": "application/json",
        })

    def session(self):
        # Capital can temporarily rate-limit session creation (HTTP 429).
        # Back off instead of hammering the login endpoint.
        delay = 5.0
        for attempt in range(6):
            r = self.s.post(
                BASE + "/api/v1/session",
                json={
                    "identifier": IDENTIFIER,
                    "password": PASSWORD,
                    "encryptedPassword": False,
                },
                timeout=20,
            )
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                try:
                    wait = max(delay, float(retry_after))
                except (TypeError, ValueError):
                    wait = delay
                wait = min(wait, 60.0)
                log.warning("CAPITAL 429 SESSION | retrying in %.1fs | attempt=%d/6", wait, attempt + 1)
                time.sleep(wait)
                delay = min(delay * 2.0, 60.0)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Capital session failed ({r.status_code}): {r.text[:500]}")
            self.s.headers.update({
                "CST": r.headers.get("CST"),
                "X-SECURITY-TOKEN": r.headers.get("X-SECURITY-TOKEN"),
            })
            return
        raise RuntimeError("Capital session rate-limited after 6 attempts")

    def get(self, path, **params):
        r = self.s.get(BASE + path, params=params, timeout=15)
        if r.status_code == 429:
            raise RuntimeError(f"Capital GET {path} rate-limited (429)")
        if r.status_code >= 400:
            raise RuntimeError(f"Capital GET {path} failed ({r.status_code}): {r.text[:500]}")
        return r.json()

    def post(self, path, payload):
        r = self.s.post(BASE + path, json=payload, timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"Capital POST {path} failed ({r.status_code}): {r.text[:500]}")
        return r.json()

    def delete(self, path):
        r = self.s.delete(BASE + path, timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"Capital DELETE {path} failed ({r.status_code}): {r.text[:500]}")
        return r.json()

    def positions(self):
        return self.get("/api/v1/positions").get("positions", [])

    def markets(self):
        return self.get("/api/v1/markets").get("markets", [])

    def market_details(self, epic):
        return self.get(f"/api/v1/markets/{epic}")

    def prices(self, epic):
        return self.get(
            f"/api/v1/prices/{epic}",
            resolution=PRICE_RESOLUTION,
            max=PRICE_HISTORY,
        )

    def confirm(self, deal_reference):
        return self.get(f"/api/v1/confirms/{deal_reference}")

    def open(self, epic, direction, size):
        if DRY_RUN:
            log.info("DRY RUN | OPEN %s %s %.4f", direction, epic, size)
            return None
        return self.post("/api/v1/positions", {
            "epic": epic,
            "direction": direction,
            "size": size,
            "guaranteedStop": False,
        })

    def close(self, deal_id):
        if DRY_RUN:
            log.info("DRY RUN | CLOSE %s", deal_id)
            return None
        return self.delete(f"/api/v1/positions/{deal_id}")


def load_state():
    try:
        data = json.loads(STATE_FILE.read_text())
        return data if isinstance(data, dict) else {"owned": {}}
    except Exception:
        return {"owned": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def quote_mid(q):
    cp = q.get("closePrice", {})
    bid, ask = cp.get("bid"), cp.get("ask")
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def market_mid(m):
    bid, ask = m.get("bid"), m.get("offer")
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def latest_price(api, epic):
    raw = api.prices(epic)
    prices = raw.get("prices", [])
    for row in reversed(prices):
        px = quote_mid(row)
        if px is not None:
            return px
    return None


def micro_signal(samples):
    """Fast GOLD price-action strategy; no EMA/RSI/MACD/ATR.

    Requires a real directional impulse plus acceleration and a small
    confirmation pullback/reclaim. This is intentionally selective enough
    to avoid firing repeatedly on a flat/tick-noise market.
    """
    if len(samples) < 8:
        return None, {}

    now_px = float(samples[-1][1])

    def px_ago(seconds):
        target = samples[-1][0] - seconds
        prior = min(samples, key=lambda x: abs(x[0] - target))
        return float(prior[1])

    p1, p2, p3, p5, p7 = (px_ago(x) for x in (1, 2, 3, 5, 7))
    m1 = now_px - p1
    m2 = p1 - p2
    m3 = now_px - p3
    m5 = now_px - p5
    m7 = now_px - p7

    deltas = [abs(float(samples[i][1]) - float(samples[i - 1][1])) for i in range(1, len(samples))]
    typical = sum(deltas[-6:]) / max(len(deltas[-6:]), 1)
    threshold = max(typical * 1.25, 0.01)

    # Require aligned movement over multiple horizons.
    up = m1 > threshold and m3 > threshold * 1.5 and m5 > threshold * 2.0
    down = m1 < -threshold and m3 < -threshold * 1.5 and m5 < -threshold * 2.0

    # Acceleration: the latest 1-second move must not be fading.
    accel_buy = m1 >= max(m2, 0.0)
    accel_sell = m1 <= min(m2, 0.0)

    # Avoid chasing a stretched move; require a small reclaim after a pullback.
    recent = [float(x[1]) for x in samples[-5:]]
    local_high, local_low = max(recent), min(recent)
    range_px = max(local_high - local_low, 0.01)
    pullback_buy = now_px >= local_high - range_px * 0.35
    pullback_sell = now_px <= local_low + range_px * 0.35

    strength = max(abs(m1), abs(m3) / 1.5, abs(m5) / 2.0) / threshold
    strong = strength >= 2.5 and (abs(m7) >= threshold * 2.5)

    moves = {
        "move1": m1, "move3": m3, "move5": m5, "move7": m7,
        "strength": strength, "strong": strong,
        "threshold": threshold,
    }

    if up and accel_buy and pullback_buy:
        return "BUY", moves
    if down and accel_sell and pullback_sell:
        return "SELL", moves
    return None, moves

def gold_market(api):
    for m in api.markets():
        epic = str(m.get("epic", "")).upper()
        status = str(m.get("marketStatus", "")).upper()
        name = str(m.get("instrumentName", m.get("name", ""))).upper()
        if status == "TRADEABLE" and epic in EPIC_ALLOWLIST:
            return m
    return None


def validate_size(api, epic, cached=None):
    """Convert requested GOLD lots to Capital API size; cache broker rules."""
    if cached and cached.get("size"):
        return float(cached["size"])

    details = api.market_details(epic)
    rules = details.get("dealingRules", {})
    market = details.get("market", details)
    minimum = float(rules.get("minDealSize", {}).get("value", 0) or 0)
    maximum = float(rules.get("maxDealSize", {}).get("value", 0) or 0)
    increment = float(rules.get("minSizeIncrement", {}).get("value", 0) or 0)
    lot_size = float(market.get("lotSize") or details.get("lotSize") or details.get("contractSize") or 100)
    size = DESIRED_LOTS * lot_size

    if minimum and size < minimum:
        raise RuntimeError(f"Requested {DESIRED_LOTS:.2f} GOLD lot = {size:.4f}, below minimum {minimum}")
    if maximum and size > maximum:
        raise RuntimeError(f"Requested {DESIRED_LOTS:.2f} GOLD lot = {size:.4f}, above maximum {maximum}")
    if increment:
        normalized = round(round(size / increment) * increment, 10)
        if abs(normalized - size) > 1e-9:
            raise RuntimeError(f"Requested GOLD size {size:.4f} incompatible with increment {increment}")

    result = {"size": size, "lot_size": lot_size, "minimum": minimum, "maximum": maximum, "increment": increment}
    log.info("GOLD SIZE CHECK | lots=%.2f | lot_size=%.4f | api_size=%.4f | min=%.4f | step=%.4f",
             DESIRED_LOTS, lot_size, size, minimum, increment)
    return result

def owned_positions(positions, state):
    out = {}
    live = set()

    for item in positions:
        p = item.get("position", {})
        deal_id = p.get("dealId")
        epic = str(item.get("market", {}).get("epic", "")).upper()
        if deal_id and deal_id in state["owned"] and epic in EPIC_ALLOWLIST:
            out[deal_id] = item
            live.add(deal_id)

    for deal_id in list(state["owned"]):
        if deal_id not in live:
            del state["owned"][deal_id]

    return out


def manage_position(api, item, entry_state, current_px):
    p = item.get("position", {})
    deal_id = p.get("dealId")
    upl = float(p.get("upl", 0) or 0)

    if not deal_id:
        return

    peak = max(float(entry_state.get("peak_upl", 0) or 0), upl)
    entry_state["peak_upl"] = peak

    # Hard per-position loss cap.
    if upl <= -MAX_INITIAL_LOSS_AED:
        api.close(deal_id)
        entry_state["close_requested"] = True
        log.warning("MAX LOSS CLOSE | %s | UPL=%.2f", deal_id, upl)
        return

    # Normal signals: bank the first +0.05 AED quickly.
    # Strong price-action signals: let profit run and protect the peak.
    strong = bool(entry_state.get("strong_signal", False))
    if upl >= PROFIT_TRIGGER_AED and not strong:
        api.close(deal_id)
        entry_state["close_requested"] = True
        log.info(
            "FAST PROFIT CLOSE | %s | UPL=%.2f | TARGET=%.2f | STRONG=False",
            deal_id, upl, PROFIT_TRIGGER_AED,
        )
        return

    if strong and peak >= PROFIT_TRIGGER_AED:
        trail_floor = max(PROFIT_FLOOR_AED, peak - 0.03)
        if upl <= trail_floor:
            api.close(deal_id)
            entry_state["close_requested"] = True
            log.info(
                "STRONG PROFIT PROTECTION CLOSE | %s | UPL=%.2f | PEAK=%.2f | FLOOR=%.2f",
                deal_id, upl, peak, trail_floor,
            )


def open_position(api, epic, direction, size, state, strong_signal=False):
    result = api.open(epic, direction, size)
    if DRY_RUN or not result:
        return True

    deal_ref = result.get("dealReference")
    if not deal_ref:
        log.error("OPEN REJECTED | no dealReference | response=%s", result)
        return False

    for attempt in range(8):
        try:
            confirmed = api.confirm(deal_ref)
            status = str(confirmed.get("dealStatus", "")).upper()

            # Capital can return the rejection reason in different fields.
            reason = (
                confirmed.get("reason")
                or confirmed.get("statusReason")
                or confirmed.get("errorCode")
                or confirmed.get("errorMessage")
                or confirmed.get("rejectReason")
                or confirmed.get("description")
            )
            affected = confirmed.get("affectedDeals", [])

            log.info(
                "OPEN CONFIRM | ref=%s | status=%s | reason=%s | affected=%d",
                deal_ref, status, reason or "NONE", len(affected),
            )

            if status in {"REJECTED", "CANCELLED", "ERROR"}:
                log.error(
                    "GOLD ORDER REJECTED | epic=%s | direction=%s | size=%.4f | "
                    "status=%s | reason=%s | response=%s",
                    epic, direction, size, status, reason or "UNKNOWN", confirmed,
                )
                return False

            if affected:
                for deal in affected:
                    deal_id = deal.get("dealId") or deal.get("dealReference")
                    if deal_id:
                        state["owned"][deal_id] = {
                            "epic": epic,
                            "direction": direction,
                            "strong_signal": bool(strong_signal),
                            "peak_upl": 0.0,
                            "opened_at": time.time(),
                        }
                        log.info("OWNED POSITION | %s | %s | %s", deal_id, epic, direction)
                return True

            if status in {"ACCEPTED", "OPEN"}:
                time.sleep(0.25)
                continue

        except Exception as e:
            log.warning(
                "OPEN CONFIRM RETRY | ref=%s | attempt=%d/8 | %s",
                deal_ref, attempt + 1, e,
            )
        time.sleep(0.25)

    log.error(
        "OPEN UNCONFIRMED | ref=%s | epic=%s | direction=%s | size=%.4f",
        deal_ref, epic, direction, size,
    )
    return False

def run():
    api = Capital()
    api.session()

    state = load_state()
    recent = deque(maxlen=12)
    last_entry = 0.0
    cycle_start = time.time()
    last_position_refresh = 0.0
    positions = []

    market = gold_market(api)
    if not market:
        raise RuntimeError("No tradeable GOLD/XAUUSD market found")

    epic = str(market["epic"]).upper()
    size_info = validate_size(api, epic)
    trade_size = float(size_info["size"])

    log.info(
        "GOLD MICRO SCALPER | DRY_RUN=%s | EPIC=%s | LOTS=%.2f | SCAN=%.1fs | MAX_POS=%d | PROFIT=+%.2f | LOSS=-%.2f",
        DRY_RUN, epic, DESIRED_LOTS, SCAN_SECONDS, MAX_POSITIONS,
        PROFIT_TRIGGER_AED, MAX_INITIAL_LOSS_AED,
    )

    while time.time() - cycle_start < RUN_SECONDS:
        try:
            # Reconnect/session refresh is handled after API failures.
            px = latest_price(api, epic)
            if px is None:
                time.sleep(SCAN_SECONDS)
                continue

            now = time.time()
            recent.append((now, px))

            if now - last_position_refresh >= max(1.0, SCAN_SECONDS):
                positions = api.positions()
                last_position_refresh = now

            owned = owned_positions(positions, state)

            # Manage all bot-owned GOLD positions before looking for new entries.
            for deal_id, item in list(owned.items()):
                manage_position(api, item, state["owned"][deal_id], px)

            # Re-fetch after management so closed positions are no longer counted.
            positions = api.positions()
            owned = owned_positions(positions, state)
            gold_positions = [
                x for x in positions
                if str(x.get("market", {}).get("epic", "")).upper() == epic
            ]

            # Generate the price-action signal immediately after the live refresh.
            signal, moves = micro_signal(list(recent))

            if signal and len(gold_positions) < MAX_POSITIONS:
                if now - last_entry >= ENTRY_COOLDOWN_SECONDS:
                    opened = open_position(api, epic, signal, trade_size, state, bool(moves.get("strong", False)))
                    if opened:
                        last_entry = now
                        # Poll briefly for the live position to appear in /positions.
                        for _ in range(4):
                            positions = api.positions()
                            gold_positions = [
                                x for x in positions
                                if str(x.get("market", {}).get("epic", "")).upper() == epic
                            ]
                            if gold_positions:
                                break
                            time.sleep(0.25)
                        log.info(
                            "ENTRY | %s | %s | size=%.4f | positions=%d/%d | move1=%.5f move3=%.5f move5=%.5f",
                            epic, signal, trade_size, len(gold_positions), MAX_POSITIONS,
                            moves.get("move1", 0), moves.get("move3", 0), moves.get("move5", 0),
                        )
                    else:
                        log.warning("ENTRY NOT CONFIRMED | %s | %s | size=%.4f", epic, signal, trade_size)
            elif not signal:
                log.info(
                    "SIGNAL_NONE | %s | px=%.5f | positions=%d/%d",
                    epic, px, len(gold_positions), MAX_POSITIONS,
                )

            save_state(state)
            time.sleep(SCAN_SECONDS)

        except Exception as e:
            log.error("CYCLE ERROR | %s", e)
            save_state(state)
            time.sleep(3)
            try:
                api.session()
            except Exception:
                pass

    log.info("RUN COMPLETE | elapsed=%ds", int(time.time() - cycle_start))


if __name__ == "__main__":
    run()
