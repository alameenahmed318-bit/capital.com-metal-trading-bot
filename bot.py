import os
import time
import json
import logging
from collections import deque
from pathlib import Path

import requests
import websocket

BASE = os.getenv("CAPITAL_BASE_URL", "https://demo-api-capital.backend-capital.com")
API_KEY = os.environ["CAPITAL_API_KEY"]
IDENTIFIER = os.environ.get("CAPITAL_IDENTIFIER") or os.environ["CAPITAL_EMAIL"]
PASSWORD = os.environ["CAPITAL_PASSWORD"]

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# GOLD ONLY - REAL-TIME WEBSOCKET MICRO SCALPER
EPIC_ALLOWLIST = {"GOLD", "XAUUSD"}
DESIRED_LOTS = 0.01
RUN_SECONDS = int(os.getenv("RUN_SECONDS", "240"))

# Entry / exit rules
MAX_POSITIONS = 20
ENTRY_COOLDOWN_SECONDS = 1.0
MIN_REENTRY_MOVE = 0.05
PROFIT_TRIGGER_AED = 0.05
PROFIT_FLOOR_AED = 0.05
TRAIL_GIVEBACK_AED = 0.03

# Risk rule: close a losing position when UPL reaches -15 AED.
MAX_LOSS_AED = 15.0

STATE_FILE = Path("bot_state.json")
WS_URL = "wss://api-streaming-capital.backend-capital.com/connect"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gold_realtime_scalper")


class Capital:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "X-CAP-API-KEY": API_KEY,
            "Content-Type": "application/json",
        })

    def session(self):
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

    def ping(self):
        return self.get("/api/v1/ping")


def load_state():
    try:
        data = json.loads(STATE_FILE.read_text())
        return data if isinstance(data, dict) else {"owned": {}}
    except Exception:
        return {"owned": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def market_mid(m):
    bid, ask = m.get("bid"), m.get("offer")
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def gold_market(api):
    for m in api.markets():
        epic = str(m.get("epic", "")).upper()
        status = str(m.get("marketStatus", "")).upper()
        if status == "TRADEABLE" and epic in EPIC_ALLOWLIST:
            return m
    return None


def validate_size(api, epic):
    details = api.market_details(epic)
    rules = details.get("dealingRules", {})
    market = details.get("market", details)

    minimum = float(rules.get("minDealSize", {}).get("value", 0) or 0)
    maximum = float(rules.get("maxDealSize", {}).get("value", 0) or 0)
    increment = float(rules.get("minSizeIncrement", {}).get("value", 0) or 0)
    lot_size = float(
        market.get("lotSize")
        or details.get("lotSize")
        or details.get("contractSize")
        or 100
    )
    size = DESIRED_LOTS * lot_size

    if minimum and size < minimum:
        raise RuntimeError(f"0.01 GOLD lot = {size:.4f} API size, below minimum {minimum}")
    if maximum and size > maximum:
        raise RuntimeError(f"0.01 GOLD lot = {size:.4f} API size, above maximum {maximum}")
    if increment:
        normalized = round(round(size / increment) * increment, 10)
        if abs(normalized - size) > 1e-9:
            raise RuntimeError(f"GOLD API size {size:.4f} incompatible with step {increment}")

    log.info(
        "GOLD SIZE CHECK | lots=%.2f | lot_size=%.4f | api_size=%.4f | min=%.4f | step=%.4f",
        DESIRED_LOTS, lot_size, size, minimum, increment,
    )
    return size


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


def price_at_or_before(samples, age_seconds):
    if not samples:
        return None
    target = samples[-1][0] - age_seconds
    candidates = [x for x in samples if x[0] <= target]
    if candidates:
        return float(candidates[-1][1])
    return float(samples[0][1])


def realtime_signal(samples):
    """Price-action only. Uses actual WebSocket quote timestamps; no indicators."""
    if len(samples) < 10:
        return None, {}

    now = samples[-1][0]
    px = float(samples[-1][1])

    p1 = price_at_or_before(samples, 1.0)
    p2 = price_at_or_before(samples, 2.0)
    p3 = price_at_or_before(samples, 3.0)
    p5 = price_at_or_before(samples, 5.0)

    m1 = px - p1
    m2 = p1 - p2
    m3 = px - p3
    m5 = px - p5

    recent = [float(x[1]) for x in samples if x[0] >= now - 2.0]
    if len(recent) < 4:
        return None, {"move1": m1, "move3": m3, "move5": m5}

    high = max(recent)
    low = min(recent)
    range_px = high - low

    # Gold price must make a real short impulse, not a repeated/stale quote.
    impulse = max(0.03, min(0.20, range_px * 0.35))
    buy = m3 >= impulse and m1 > 0 and m1 >= max(m2, 0.0)
    sell = m3 <= -impulse and m1 < 0 and m1 <= min(m2, 0.0)

    # Avoid chasing a one-sided spike at the extreme.
    buy_confirm = px >= high - max(range_px * 0.45, 0.01)
    sell_confirm = px <= low + max(range_px * 0.45, 0.01)

    strength = max(abs(m1), abs(m3) * 0.6, abs(m5) * 0.35)
    strong = strength >= 0.10

    info = {
        "move1": m1,
        "move3": m3,
        "move5": m5,
        "impulse": impulse,
        "strong": strong,
        "timestamp": samples[-1][0],
    }

    if buy and buy_confirm:
        return "BUY", info
    if sell and sell_confirm:
        return "SELL", info
    return None, info


def manage_position(api, item, entry_state):
    p = item.get("position", {})
    deal_id = p.get("dealId")
    upl = float(p.get("upl", 0) or 0)

    if not deal_id:
        return

    peak = max(float(entry_state.get("peak_upl", 0) or 0), upl)
    entry_state["peak_upl"] = peak

    # Hard maximum loss: close ANY live GOLD position at -15 AED or worse.
    if upl <= -MAX_LOSS_AED:
        api.close(deal_id)
        entry_state["close_requested"] = True
        log.warning("MAX LOSS CLOSE | %s | UPL=%.2f | LIMIT=-%.2f AED", deal_id, upl, MAX_LOSS_AED)
        return

    if upl <= 0:
        return

    strong = bool(entry_state.get("strong_signal", False))

    # First profit target: +0.05 AED.
    if not strong and upl >= PROFIT_TRIGGER_AED:
        api.close(deal_id)
        entry_state["close_requested"] = True
        log.info("FAST PROFIT CLOSE | %s | UPL=+%.2f", deal_id, upl)
        return

    # Strong trades: protect profit, but ONLY while UPL remains positive.
    if strong and peak >= PROFIT_TRIGGER_AED:
        floor = max(PROFIT_FLOOR_AED, peak - TRAIL_GIVEBACK_AED)
        if upl >= PROFIT_FLOOR_AED and upl <= floor:
            api.close(deal_id)
            entry_state["close_requested"] = True
            log.info(
                "PROFIT PROTECTION CLOSE | %s | UPL=+%.2f | PEAK=+%.2f | FLOOR=+%.2f",
                deal_id, upl, peak, floor,
            )


def open_position(api, epic, direction, size, state, strong_signal=False):
    result = api.open(epic, direction, size)
    if DRY_RUN or not result:
        return True

    deal_ref = result.get("dealReference")
    if not deal_ref:
        log.error("OPEN REJECTED | no dealReference | response=%s", result)
        return False

    # Snapshot positions before the order so an asynchronously-confirmed deal
    # cannot become an unmanaged/orphaned GOLD position.
    try:
        before_ids = {
            str(x.get("position", {}).get("dealId"))
            for x in api.positions()
            if x.get("position", {}).get("dealId")
        }
    except Exception:
        before_ids = set()

    last_confirm = None
    for attempt in range(12):
        try:
            confirmed = api.confirm(deal_ref)
            last_confirm = confirmed
            status = str(confirmed.get("dealStatus", "")).upper()
            reason = (
                confirmed.get("reason")
                or confirmed.get("statusReason")
                or confirmed.get("errorCode")
                or confirmed.get("errorMessage")
                or confirmed.get("rejectReason")
                or confirmed.get("description")
            )
            affected = confirmed.get("affectedDeals", [])

            # Do not spam the log with the same ACCEPTED/affected=0 response.
            if attempt == 0 or affected or status in {"REJECTED", "CANCELLED", "ERROR"}:
                log.info(
                    "OPEN CONFIRM | ref=%s | status=%s | reason=%s | affected=%d | attempt=%d/12",
                    deal_ref, status, reason or "NONE", len(affected), attempt + 1,
                )

            if status in {"REJECTED", "CANCELLED", "ERROR"}:
                log.error(
                    "GOLD ORDER REJECTED | direction=%s | size=%.4f | reason=%s",
                    direction, size, reason or "UNKNOWN",
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

        except Exception as e:
            log.warning(
                "OPEN CONFIRM RETRY | ref=%s | attempt=%d/12 | %s",
                deal_ref, attempt + 1, e,
            )

        time.sleep(0.35)

    # Capital can temporarily report ACCEPTED with affectedDeals=[].
    # Before declaring failure, inspect live positions once and adopt a newly
    # opened GOLD position that matches this order.
    try:
        live = api.positions()
        candidates = []
        for item in live:
            p = item.get("position", {})
            m = item.get("market", {})
            deal_id = p.get("dealId")
            live_epic = str(m.get("epic", "")).upper()
            live_direction = str(p.get("direction", "")).upper()
            live_size = float(p.get("size", 0) or 0)
            if (
                deal_id
                and str(deal_id) not in before_ids
                and live_epic == epic
                and live_direction == direction
                and abs(live_size - size) < max(0.0001, size * 0.01)
            ):
                candidates.append(deal_id)

        if candidates:
            for deal_id in candidates:
                state["owned"][deal_id] = {
                    "epic": epic,
                    "direction": direction,
                    "strong_signal": bool(strong_signal),
                    "peak_upl": 0.0,
                    "opened_at": time.time(),
                }
                log.info(
                    "OWNED POSITION RECOVERED | %s | %s | %s | ref=%s",
                    deal_id, epic, direction, deal_ref,
                )
            return True
    except Exception as e:
        log.warning("OPEN POSITION RECOVERY ERROR | ref=%s | %s", deal_ref, e)

    final_status = str((last_confirm or {}).get("dealStatus", "UNKNOWN")).upper()
    log.error(
        "OPEN UNCONFIRMED | ref=%s | direction=%s | size=%.4f | final_status=%s",
        deal_ref, direction, size, final_status,
    )
    return False


def websocket_quote_loop(api, epic, on_quote, stop_at):
    """Stream real GOLD quotes. Reconnects before the 10-minute WS session limit."""
    reconnects = 0

    while time.time() < stop_at:
        ws = None
        connected_at = time.time()
        try:
            ws = websocket.create_connection(WS_URL, timeout=3)
            ws.send(json.dumps({
                "destination": "marketData.subscribe",
                "correlationId": "gold-1",
                "cst": api.s.headers["CST"],
                "securityToken": api.s.headers["X-SECURITY-TOKEN"],
                "payload": {"epics": [epic]},
            }))
            log.info("GOLD WEBSOCKET CONNECTED | EPIC=%s", epic)

            last_ping = time.time()

            while time.time() < stop_at:
                if time.time() - connected_at >= 8 * 60:
                    log.info("GOLD WEBSOCKET REFRESH | reconnecting before 10m limit")
                    break

                if time.time() - last_ping >= 240:
                    ws.send(json.dumps({
                        "destination": "ping",
                        "correlationId": "gold-ping",
                        "cst": api.s.headers["CST"],
                        "securityToken": api.s.headers["X-SECURITY-TOKEN"],
                    }))
                    last_ping = time.time()

                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue

                if not raw:
                    continue

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if msg.get("destination") != "quote":
                    continue

                q = msg.get("payload", {})
                if str(q.get("epic", "")).upper() != epic:
                    continue

                bid = q.get("bid")
                ask = q.get("ofr")
                ts = q.get("timestamp")

                if bid is None or ask is None:
                    continue

                bid = float(bid)
                ask = float(ask)
                if bid <= 0 or ask <= 0 or ask < bid:
                    continue

                mid = (bid + ask) / 2.0
                event_ts = float(ts) / 1000.0 if ts is not None else time.time()
                on_quote(event_ts, mid, bid, ask)

        except Exception as e:
            reconnects += 1
            log.warning("GOLD WEBSOCKET ERROR | reconnect=%d | %s", reconnects, e)
            time.sleep(min(2.0, max(0.5, reconnects * 0.25)))
        finally:
            try:
                if ws:
                    ws.close()
            except Exception:
                pass

        # Tokens are valid for 10 minutes after last use; refresh before reconnect.
        if time.time() < stop_at:
            try:
                api.session()
            except Exception as e:
                log.error("SESSION REFRESH ERROR | %s", e)
                time.sleep(2)


def run():
    api = Capital()
    api.session()

    state = load_state()
    market = gold_market(api)
    if not market:
        raise RuntimeError("No tradeable GOLD/XAUUSD market found")

    epic = str(market["epic"]).upper()
    trade_size = validate_size(api, epic)

    samples = deque(maxlen=200)
    last_entry = 0.0
    last_entry_px = None
    last_entry_direction = None
    last_position_refresh = 0.0
    positions = []
    stop_at = time.time() + RUN_SECONDS

    log.info(
        "GOLD REALTIME SCALPER | DRY_RUN=%s | EPIC=%s | LOTS=%.2f | "
        "SOURCE=WEBSOCKET | PROFIT=+%.2f | MAX_LOSS=-%.2f | MAX_POS=%d",
        DRY_RUN, epic, DESIRED_LOTS, PROFIT_TRIGGER_AED, MAX_LOSS_AED, MAX_POSITIONS,
    )

    def on_quote(event_ts, px, bid, ask):
        nonlocal last_entry, last_entry_px, last_entry_direction
        nonlocal last_position_refresh, positions

        # WebSocket can repeat a timestamp. Do not turn duplicate quotes into fake movement.
        if samples and event_ts <= samples[-1][0]:
            event_ts = samples[-1][0] + 0.0001
        if samples and abs(px - samples[-1][1]) < 1e-12:
            return

        samples.append((event_ts, px))

        now = time.time()
        if now - last_position_refresh < 1.0:
            return

        try:
            positions = api.positions()
            last_position_refresh = now
            owned = owned_positions(positions, state)

            # Manage EVERY live GOLD position, not only positions saved in bot_state.
            # This guarantees the -15 AED hard-loss rule is applied after restarts
            # or when Capital confirms a position asynchronously.
            for item in list(positions):
                item_epic = str(item.get("market", {}).get("epic", "")).upper()
                if item_epic != epic:
                    continue
                p = item.get("position", {})
                deal_id = p.get("dealId")
                if not deal_id:
                    continue
                entry_state = state["owned"].setdefault(
                    deal_id,
                    {
                        "epic": epic,
                        "direction": str(p.get("direction", "")).upper(),
                        "strong_signal": False,
                        "peak_upl": 0.0,
                        "opened_at": time.time(),
                    },
                )
                manage_position(api, item, entry_state)

            # Re-read after any close request.
            positions = api.positions()
            owned = owned_positions(positions, state)
            gold_positions = [
                x for x in positions
                if str(x.get("market", {}).get("epic", "")).upper() == epic
            ]

            strategy_signal, info = realtime_signal(list(samples))
            if not strategy_signal or len(gold_positions) >= MAX_POSITIONS:
                return

            # EXECUTION INVERSION ONLY:
            # strategy BUY -> execute SELL
            # strategy SELL -> execute BUY
            execution_signal = "SELL" if strategy_signal == "BUY" else "BUY"
            log.info(
                "DIRECTION INVERT | GOLD | strategy=%s | execution=%s",
                strategy_signal, execution_signal,
            )

            # Prevent repeated entries on the same impulse/price.
            if now - last_entry < ENTRY_COOLDOWN_SECONDS:
                return

            if last_entry_px is not None:
                same_direction = execution_signal == last_entry_direction
                if same_direction and abs(px - last_entry_px) < MIN_REENTRY_MOVE:
                    return

            opened = open_position(
                api, epic, execution_signal, trade_size, state, bool(info.get("strong", False))
            )
            if opened:
                last_entry = now
                last_entry_px = px
                last_entry_direction = execution_signal
                log.info(
                    "ENTRY | GOLD | strategy=%s | EXECUTION=%s | size=%.4f=0.01lot | px=%.5f | "
                    "move1=%.5f move3=%.5f move5=%.5f | strong=%s | positions=%d/%d",
                    strategy_signal, execution_signal, trade_size, px,
                    info.get("move1", 0), info.get("move3", 0), info.get("move5", 0),
                    info.get("strong", False), len(gold_positions) + 1, MAX_POSITIONS,
                )
            else:
                log.warning(
                    "ENTRY NOT CONFIRMED | GOLD | strategy=%s | execution=%s | size=%.4f",
                    strategy_signal, execution_signal, trade_size,
                )

            save_state(state)

        except Exception as e:
            log.error("QUOTE CYCLE ERROR | %s", e)

    websocket_quote_loop(api, epic, on_quote, stop_at)

    save_state(state)
    log.info("RUN COMPLETE | elapsed=%ds", RUN_SECONDS)


if __name__ == "__main__":
    run()